from __future__ import annotations

from io import BytesIO
from typing import Dict, Any, List, Tuple

import pandas as pd
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet

from .models import Assumptions, Output


def _money(ccy: str, x: float) -> str:
    return f"{ccy} {x:,.0f}"


def _pct(x: float) -> str:
    return f"{x*100:.1f}%"


def _cashflow_chart_png(df_cf: pd.DataFrame) -> bytes:
    """
    Create a simple chart (no custom colors) and return PNG bytes.
    """
    fig = plt.figure(figsize=(7.2, 3.2), dpi=150)
    ax = fig.add_subplot(111)
    ax.plot(df_cf["Month"], df_cf["Debt balance"], label="Debt balance")
    ax.plot(df_cf["Month"], df_cf["Cash balance"], label="Cash balance")
    ax.set_title("Monthly cash and debt (end of month)")
    ax.set_xlabel("Month")
    ax.set_ylabel("ZAR")
    ax.legend()
    ax.grid(True, alpha=0.3)

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def build_pdf_report(
    project_name: str,
    version_name: str,
    assumptions_dict: Dict[str, Any],
    out: Output,
    sensitivity_table: Tuple[List[str], List[str], List[List[float]]],
) -> bytes:
    """
    Return PDF bytes.
    sensitivity_table: (row_labels, col_labels, matrix_as_lists)
    """
    styles = getSampleStyleSheet()
    s_title = styles["Title"]
    s_h = styles["Heading2"]
    s_p = styles["BodyText"]

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Development Appraisal Report",
        author="Dev Appraisal (Streamlit MVP)",
    )

    a = Assumptions.from_dict(assumptions_dict)
    ccy = out.currency

    story: List[Any] = []
    story.append(Paragraph(f"Development Appraisal Report", s_title))
    story.append(Paragraph(f"<b>Project:</b> {project_name}<br/><b>Version:</b> {version_name}<br/><b>Currency:</b> {ccy}", s_p))
    story.append(Spacer(1, 10))

    # KPI Summary
    story.append(Paragraph("Summary KPIs", s_h))
    kpi = [
        ["GDV", _money(ccy, out.gdv)],
        ["Sellable sqm", f"{out.sellable_sqm:,.0f} sqm"],
        ["TDC (ex land, ex finance)", _money(ccy, out.tdc_ex_land_ex_fin)],
        ["Finance costs", _money(ccy, out.finance_costs)],
        ["Residual / Land value", _money(ccy, out.land_value)],
        ["Total cost (incl land)", _money(ccy, out.total_cost_incl_land)],
        ["Profit", _money(ccy, out.profit)],
        ["Profit % GDV", _pct(out.profit_rate_on_gdv)],
        ["Profit % Cost", _pct(out.profit_rate_on_cost)],
        ["Peak debt", _money(ccy, out.peak_debt)],
        ["Equity IRR p.a.", "-" if out.irr_equity_pa is None else _pct(out.irr_equity_pa)],
    ]
    t = Table(kpi, colWidths=[70*mm, 90*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(t)
    story.append(Spacer(1, 10))

    # Product mix
    story.append(Paragraph("Product Mix (Revenue Lines)", s_h))
    pm_rows = [["Name", "Measure", "Quantity", "Unit size", "Price", "Start", "Duration", "Curve", "GDV"]]
    for p in a.products:
        qty_disp = f"{p.qty:,.0f}" + (" units" if p.measure == "units" else " sqm")
        unit_disp = "-" if p.measure == "sqm" else f"{p.unit_size_sqm:,.1f} sqm"
        price_disp = _money(ccy, p.price_per_unit) if p.price_per_unit is not None else _money(ccy, p.price_per_sqm) + (" /sqm" if p.price_per_unit is None else "")
        pm_rows.append([
            p.name,
            p.measure,
            qty_disp,
            unit_disp,
            price_disp,
            str(p.start_month),
            str(p.duration_months),
            p.curve,
            _money(ccy, p.gross_value()),
        ])
    pm = Table(pm_rows, repeatRows=1, colWidths=[34*mm, 16*mm, 20*mm, 18*mm, 26*mm, 14*mm, 16*mm, 14*mm, 28*mm])
    pm.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(pm)
    story.append(Spacer(1, 10))

    # Cashflow chart + excerpt table
    story.append(Paragraph("Cashflow & Finance (Monthly)", s_h))
    df_cf = pd.DataFrame(out.cashflow_rows)
    df_cf_disp = df_cf.copy()
    df_cf_disp["Month"] = df_cf_disp["Month"] + 1

    png = _cashflow_chart_png(df_cf_disp)
    img = Image(BytesIO(png), width=170*mm, height=75*mm)
    story.append(img)
    story.append(Spacer(1, 6))

    cols_keep = ["Month", "Revenue", "Costs (ex land, ex fin)", "Land", "Interest", "Fees", "Debt draw", "Debt repay", "Debt balance"]
    excerpt = df_cf_disp[cols_keep].head(18)  # keep it readable; full table can be exported separately if needed
    tbl = [cols_keep] + excerpt.round(0).astype(int).values.tolist()
    tbl = [[c if isinstance(c, str) else str(c) for c in row] for row in tbl]

    cf_table = Table(tbl, repeatRows=1)
    cf_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(cf_table)
    story.append(Spacer(1, 10))

    # Sensitivity
    story.append(PageBreak())
    story.append(Paragraph("Sensitivity (Price vs Build Cost -> Residual Land)", s_h))
    rlabels, clabels, mat = sensitivity_table

    sens_rows = [["Price\\Cost"] + clabels]
    for i, rl in enumerate(rlabels):
        row = [rl] + [_money(ccy, float(mat[i][j])) for j in range(len(clabels))]
        sens_rows.append(row)

    sens = Table(sens_rows, repeatRows=1)
    sens.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f9f9f9")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(sens)
    story.append(Spacer(1, 10))

    # Audit
    story.append(Paragraph("Audit Trail", s_h))
    aud = pd.DataFrame(out.audit)
    # Keep it light; show key lines
    aud_rows = [["Section", "Key", "Value"]]
    for _, r in aud.iterrows():
        unit = r.get("unit", "")
        v = r.get("value", "")
        if unit == "ratio":
            vdisp = _pct(float(v))
        elif unit == out.currency:
            vdisp = _money(out.currency, float(v))
        else:
            vdisp = str(v)
        aud_rows.append([str(r.get("section", "")), str(r.get("key", "")), vdisp])

    aud_tbl = Table(aud_rows, repeatRows=1, colWidths=[35*mm, 75*mm, 70*mm])
    aud_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(aud_tbl)

    doc.build(story)
    return buf.getvalue()
