from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from core.models import Assumptions, ProductLine
from core.engine import run_appraisal, sensitivity_grid
from core.report import build_pdf_report
from data.db import init_db, list_projects, create_project, list_appraisals, save_appraisal, load_appraisal, get_project


# ----------------------------
# Formatting helpers
# ----------------------------
def money(ccy: str, x: float) -> str:
    try:
        return f"{ccy} {x:,.0f}"
    except Exception:
        return f"{ccy} {x}"


def pct(x: float) -> str:
    return f"{x*100:.1f}%"


def kpi_cards(out):
    ccy = out.currency
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Residual / Land", money(ccy, out.land_value))
    c2.metric("Profit", money(ccy, out.profit), pct(out.profit_rate_on_gdv))
    c3.metric("GDV", money(ccy, out.gdv))
    c4.metric("TDC (ex land, ex fin)", money(ccy, out.tdc_ex_land_ex_fin))


# ----------------------------
# App
# ----------------------------
st.set_page_config(page_title="Dev Appraisal (ZAR) — MVP+", layout="wide")
init_db()

st.title("Development Appraisal — Streamlit MVP+")
st.caption("Product mix • Monthly finance • Scenario compare • PDF report • ZAR • UK-style residual")


# Sidebar: Project selection + load appraisal
with st.sidebar:
    st.header("Projects")
    projects = list_projects()

    with st.expander("➕ New project", expanded=False):
        new_name = st.text_input("Project name", value="", key="new_proj_name")
        new_loc = st.text_input("Location", value="", key="new_proj_loc")
        if st.button("Create project", use_container_width=True) and new_name.strip():
            pid = create_project(new_name.strip(), new_loc.strip())
            st.success(f"Created project #{pid}")
            st.rerun()

    if not projects:
        st.info("Create a project to begin.")
        st.stop()

    project_labels = [f"#{p['id']} — {p['name']}" for p in projects]
    pidx = st.selectbox("Select project", list(range(len(project_labels))), format_func=lambda i: project_labels[i])
    project_id = int(projects[pidx]["id"])

    st.divider()
    st.subheader("Saved appraisals")
    appraisals = list_appraisals(project_id)
    appraisal_id = None
    if appraisals:
        labels = [f"#{a['id']} — {a['version_name']} ({a['created_at']})" for a in appraisals]
        pick = st.selectbox("Load a saved version", ["(none)"] + labels)
        if pick != "(none)":
            appraisal_id = int(pick.split("—")[0].strip().replace("#", ""))


# Session state assumptions
if "assumptions_dict" not in st.session_state:
    st.session_state.assumptions_dict = Assumptions(currency="ZAR").to_dict()

loaded_meta = None
if appraisal_id:
    loaded = load_appraisal(appraisal_id)
    if loaded:
        st.session_state.assumptions_dict = loaded[0]
        loaded_meta = loaded[2]

a = Assumptions.from_dict(st.session_state.assumptions_dict)

# Layout
left, right = st.columns([0.56, 0.44], gap="large")

# ----------------------------
# Inputs
# ----------------------------
with left:
    st.subheader("Inputs")

    # Global / Programme
    g1, g2, g3 = st.columns(3)
    a.currency = g1.selectbox("Currency", ["ZAR", "GBP", "EUR", "USD"], index=0 if a.currency == "ZAR" else ["ZAR","GBP","EUR","USD"].index(a.currency))
    a.months_total = int(g2.number_input("Cashflow horizon (months)", 12, 84, int(a.months_total)))
    a.overhead_per_month = float(g3.number_input("Overhead per month", min_value=0.0, value=float(a.overhead_per_month), step=5000.0))

    st.markdown("### Product mix (multi-line revenue)")

    # Editable products table-like UI
    if "products_ui" not in st.session_state:
        st.session_state.products_ui = [p.to_dict() for p in a.products]

    # Add/remove product lines
    b1, b2 = st.columns([0.6, 0.4])
    with b1:
        if st.button("➕ Add product line", use_container_width=True):
            st.session_state.products_ui.append(ProductLine(name=f"New line {len(st.session_state.products_ui)+1}").to_dict())
            st.rerun()
    with b2:
        if st.session_state.products_ui and st.button("🗑️ Remove last line", use_container_width=True):
            st.session_state.products_ui.pop()
            st.rerun()

    # Render each product line editor
    for i, pdict in enumerate(st.session_state.products_ui):
        with st.expander(f"{i+1}) {pdict.get('name','Product')}", expanded=(i == 0)):
            c1, c2, c3 = st.columns(3)
            pdict["name"] = c1.text_input("Name", value=pdict.get("name", ""), key=f"pname_{i}")
            pdict["measure"] = c2.selectbox("Measure", ["units", "sqm"], index=0 if pdict.get("measure","units") == "units" else 1, key=f"pmeas_{i}")
            pdict["curve"] = c3.selectbox("Sales curve", ["linear", "front", "back"], index=["linear","front","back"].index(pdict.get("curve","linear")), key=f"pcurve_{i}")

            c4, c5, c6 = st.columns(3)
            if pdict["measure"] == "units":
                pdict["qty"] = float(c4.number_input("Units", min_value=0.0, value=float(pdict.get("qty", 0.0)), step=1.0, key=f"pqty_{i}"))
                pdict["unit_size_sqm"] = float(c5.number_input("Avg unit size (sqm)", min_value=0.0, value=float(pdict.get("unit_size_sqm", 0.0)), step=1.0, key=f"psize_{i}"))
                pdict["price_per_unit"] = None
                pdict["price_per_sqm"] = float(c6.number_input("Price per sqm", min_value=0.0, value=float(pdict.get("price_per_sqm", 0.0)), step=500.0, key=f"ppsqm_{i}"))
            else:
                pdict["qty"] = float(c4.number_input("Area (sqm)", min_value=0.0, value=float(pdict.get("qty", 0.0)), step=10.0, key=f"parea_{i}"))
                pdict["unit_size_sqm"] = float(pdict.get("unit_size_sqm", 0.0) or 0.0)
                pdict["price_per_unit"] = None
                pdict["price_per_sqm"] = float(c6.number_input("Price per sqm", min_value=0.0, value=float(pdict.get("price_per_sqm", 0.0)), step=500.0, key=f"ppsqm2_{i}"))

            t1, t2, t3 = st.columns(3)
            pdict["start_month"] = int(t1.number_input("Start month", 0, a.months_total-1, int(pdict.get("start_month", 0)), key=f"pstart_{i}"))
            pdict["duration_months"] = int(t2.number_input("Duration (months)", 1, a.months_total, int(pdict.get("duration_months", 12)), key=f"pdur_{i}"))
            pdict["affordable_share"] = float(t3.slider("Affordable share", 0.0, 0.5, float(pdict.get("affordable_share", 0.0)), 0.01, key=f"paff_{i}"))
            if pdict["affordable_share"] > 0:
                pdict["affordable_price_per_sqm"] = float(st.number_input("Affordable price per sqm", min_value=0.0, value=float(pdict.get("affordable_price_per_sqm", 0.0)), step=500.0, key=f"paffp_{i}"))

    # Update assumptions products from UI
    a.products = [ProductLine.from_dict(p) for p in st.session_state.products_ui]

    st.markdown("### Costs")
    c1, c2, c3 = st.columns(3)
    a.build_cost_per_sqm = float(c1.number_input("Build cost per sqm (proxy)", min_value=0.0, value=float(a.build_cost_per_sqm), step=500.0))
    a.contingency_rate = float(c2.slider("Contingency", 0.0, 0.25, float(a.contingency_rate), 0.01))
    a.escalation_rate_pa = float(c3.slider("Escalation p.a.", 0.0, 0.25, float(a.escalation_rate_pa), 0.005))

    c4, c5, c6 = st.columns(3)
    a.professional_fees_rate = float(c4.slider("Professional fees", 0.0, 0.25, float(a.professional_fees_rate), 0.01))
    a.marketing_rate = float(c5.slider("Marketing (% GDV)", 0.0, 0.10, float(a.marketing_rate), 0.005))
    a.heritage_cost_uplift_rate = float(c6.slider("Heritage cost uplift", 0.0, 0.25, float(a.heritage_cost_uplift_rate), 0.01))

    c7, c8, c9 = st.columns(3)
    a.statutory_costs = float(c7.number_input("Statutory costs (lump sum)", min_value=0.0, value=float(a.statutory_costs), step=50000.0))
    a.incentive_grant = float(c8.number_input("Incentive / grant (reduces cost)", value=float(a.incentive_grant), step=50000.0))
    a.build_months = int(c9.number_input("Build months", 1, 60, int(a.build_months)))

    a.build_start_month = int(st.number_input("Build start month", 0, a.months_total-1, int(a.build_start_month)))

    st.markdown("### Profit target (UK-style residual)")
    p1, p2 = st.columns(2)
    a.target_profit_basis = p1.selectbox("Target basis", ["gdv", "cost"], index=0 if a.target_profit_basis == "gdv" else 1)
    a.target_profit_rate = float(p2.slider("Target developer return", 0.0, 0.35, float(a.target_profit_rate), 0.01))

    st.markdown("### Finance (monthly)")
    f1, f2, f3, f4 = st.columns(4)
    a.use_debt = f1.toggle("Use debt", value=bool(a.use_debt))
    a.debt_interest_rate_pa = float(f2.slider("Interest p.a.", 0.0, 0.35, float(a.debt_interest_rate_pa), 0.005))
    a.debt_arrangement_fee_rate = float(f3.slider("Arrangement fee", 0.0, 0.05, float(a.debt_arrangement_fee_rate), 0.001))
    a.debt_exit_fee_rate = float(f4.slider("Exit fee", 0.0, 0.05, float(a.debt_exit_fee_rate), 0.001))
    a.equity_injection_month0 = float(st.number_input("Equity injection month 0 (optional)", value=float(a.equity_injection_month0), step=100000.0))

    st.markdown("### Land")
    a.land_paid_month = int(st.number_input("Land paid month", 0, a.months_total-1, int(a.land_paid_month)))
    land_in = float(st.number_input("If you want profit on fixed land price: enter land price here (0 = residual solve)",
                                    value=float(a.land_price_input or 0.0),
                                    step=250000.0))
    a.land_price_input = None if land_in == 0.0 else land_in

    # persist assumptions
    st.session_state.assumptions_dict = a.to_dict()


# ----------------------------
# Outputs + Compare + PDF
# ----------------------------
with right:
    st.subheader("Outputs")
    out = run_appraisal(a)
    kpi_cards(out)

    ccy = out.currency
    k1, k2, k3 = st.columns(3)
    k1.metric("Finance costs", money(ccy, out.finance_costs))
    k2.metric("Peak debt", money(ccy, out.peak_debt))
    k3.metric("Equity IRR p.a.", "-" if out.irr_equity_pa is None else pct(out.irr_equity_pa))

    tabs = st.tabs(["Cashflow", "Sensitivity", "Compare", "Report (PDF)", "Audit", "Save"])

    with tabs[0]:
        st.caption("Monthly cashflow includes debt draw/repay, interest, and fees.")
        df_cf = pd.DataFrame(out.cashflow_rows).copy()
        df_cf["Month"] = df_cf["Month"] + 1

        fig = go.Figure()
        fig.add_trace(go.Bar(x=df_cf["Month"], y=(df_cf["Revenue"] - df_cf["Costs (ex land, ex fin)"] - df_cf["Land"]), name="Net before finance"))
        fig.add_trace(go.Scatter(x=df_cf["Month"], y=df_cf["Debt balance"], name="Debt balance", mode="lines"))
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        show_cols = ["Month", "Revenue", "Costs (ex land, ex fin)", "Land", "Interest", "Fees", "Debt draw", "Debt repay", "Debt balance", "Cash balance"]
        st.dataframe(df_cf[show_cols], use_container_width=True)

    with tabs[1]:
        st.caption("2D sensitivity: product prices vs build cost -> residual land value")
        rows, cols, mat = sensitivity_grid(a)
        heat = go.Figure(data=go.Heatmap(
            z=mat,
            x=cols,
            y=rows,
            hovertemplate="Price: %{y}<br>Cost: %{x}<br>Land: %{z:,.0f}<extra></extra>"
        ))
        heat.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(heat, use_container_width=True)

        df_s = pd.DataFrame(mat, index=rows, columns=cols)
        st.dataframe(df_s.applymap(lambda v: money(ccy, float(v))), use_container_width=True)

    with tabs[2]:
        st.caption("Compare saved versions side-by-side (Base vs Offer vs Bank).")
        appraisals = list_appraisals(project_id)

        if not appraisals:
            st.info("No saved appraisals yet. Save a few versions, then compare here.")
        else:
            labels = [f"#{a_['id']} — {a_['version_name']} ({a_['created_at']})" for a_ in appraisals]
            picked = st.multiselect("Pick up to 3 versions", labels, default=labels[: min(3, len(labels))])

            picked = picked[:3]
            cols_ui = st.columns(max(1, len(picked)))
            outs = []

            for i, sel in enumerate(picked):
                aid = int(sel.split("—")[0].strip().replace("#", ""))
                loaded = load_appraisal(aid)
                if not loaded:
                    continue
                aa = Assumptions.from_dict(loaded[0])
                oo = run_appraisal(aa)
                outs.append((sel, oo))

                with cols_ui[i]:
                    st.markdown(f"**{sel}**")
                    st.metric("Land", money(oo.currency, oo.land_value))
                    st.metric("Profit", money(oo.currency, oo.profit), pct(oo.profit_rate_on_gdv))
                    st.metric("GDV", money(oo.currency, oo.gdv))
                    st.metric("TDC ex", money(oo.currency, oo.tdc_ex_land_ex_fin))
                    st.metric("Peak debt", money(oo.currency, oo.peak_debt))

            if len(outs) >= 2:
                # Table compare
                rows_cmp = []
                keys = [
                    ("GDV", "gdv"),
                    ("TDC (ex land, ex fin)", "tdc_ex_land_ex_fin"),
                    ("Finance costs", "finance_costs"),
                    ("Land", "land_value"),
                    ("Total cost (incl land)", "total_cost_incl_land"),
                    ("Profit", "profit"),
                    ("Profit % GDV", "profit_rate_on_gdv"),
                    ("Peak debt", "peak_debt"),
                ]
                base_name, base_out = outs[0]
                for label, attr in keys:
                    r = {"Metric": label}
                    for name, oo in outs:
                        v = getattr(oo, attr)
                        if "rate" in attr:
                            r[name] = pct(float(v))
                        else:
                            r[name] = money(oo.currency, float(v))
                    rows_cmp.append(r)
                st.dataframe(pd.DataFrame(rows_cmp), use_container_width=True)

    with tabs[3]:
        st.caption("Generate a PDF report with KPIs, product mix, cashflow chart, sensitivity table, and audit trail.")
        project = get_project(project_id) or {"name": f"Project #{project_id}"}
        default_version = loaded_meta["version_name"] if loaded_meta else "Current run"

        vname = st.text_input("Report version label", value=default_version)
        # compute sensitivity for report
        rlabels, clabels, mat = sensitivity_grid(a)
        mat_list = mat.tolist()

        pdf_bytes = build_pdf_report(
            project_name=project.get("name", ""),
            version_name=vname,
            assumptions_dict=a.to_dict(),
            out=out,
            sensitivity_table=(rlabels, clabels, mat_list),
        )
        st.download_button(
            "⬇️ Download PDF report",
            data=pdf_bytes,
            file_name=f"{project.get('name','project').replace(' ','_')}_{vname.replace(' ','_')}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

    with tabs[4]:
        st.caption("Audit trail (numbers formatted without pandas Styler to avoid Arrow/matplotlib issues).")
        df_a = pd.DataFrame(out.audit)

        def fmt_value(row):
            unit = row.get("unit", "")
            v = row.get("value", "")
            if unit == "ratio":
                return pct(float(v))
            if unit == out.currency:
                return money(out.currency, float(v))
            return v

        df_a["Display"] = df_a.apply(fmt_value, axis=1)
        st.dataframe(df_a[["section", "key", "Display"]], use_container_width=True)

    with tabs[5]:
        st.caption("Save this run as a version under the selected project.")
        version = st.text_input("Version name", value=(loaded_meta["version_name"] if loaded_meta else "Base Case"))
        if st.button("💾 Save appraisal version", use_container_width=True):
            app_id = save_appraisal(project_id, version.strip() or "Base Case", a.to_dict(), out.headline())
            st.success(f"Saved appraisal #{app_id}")
            st.rerun()
