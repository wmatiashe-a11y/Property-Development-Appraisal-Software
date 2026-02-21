from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from core.models import Assumptions
from core.engine import run_appraisal, sensitivity_grid
from data.db import init_db, list_projects, create_project, list_appraisals, save_appraisal, load_appraisal


# ----------------------------
# Helpers
# ----------------------------
def money(x: float, ccy: str) -> str:
    try:
        return f"{ccy} {x:,.0f}"
    except Exception:
        return f"{ccy} {x}"

def pct(x: float) -> str:
    return f"{x*100:.1f}%"

def kpi_row(out):
    ccy = out.currency
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Residual / Land", money(out.land_value, ccy))
    c2.metric("Profit", money(out.profit, ccy), pct(out.profit_rate_on_gdv))
    c3.metric("GDV", money(out.gdv, ccy))
    c4.metric("TDC (ex land)", money(out.tdc_ex_land, ccy))


# ----------------------------
# App
# ----------------------------
st.set_page_config(page_title="Dev Appraisal (MVP)", layout="wide")
init_db()

st.title("Development Appraisal — Streamlit MVP")
st.caption("Fast feasibility: inputs → residual land value → cashflow → sensitivity → saved versions")

# Sidebar: Project / Appraisal selection
with st.sidebar:
    st.header("Projects")
    projects = list_projects()

    with st.expander("➕ New project", expanded=False):
        new_name = st.text_input("Project name", value="")
        new_loc = st.text_input("Location", value="")
        if st.button("Create project", use_container_width=True) and new_name.strip():
            pid = create_project(new_name.strip(), new_loc.strip())
            st.success(f"Created project #{pid}")
            st.rerun()

    if not projects:
        st.info("Create a project to begin.")
        st.stop()

    project_labels = [f"#{p['id']} — {p['name']}" for p in projects]
    idx = st.selectbox("Select project", list(range(len(project_labels))), format_func=lambda i: project_labels[i])
    project_id = int(projects[idx]["id"])

    st.divider()
    st.subheader("Appraisals")
    appraisals = list_appraisals(project_id)

    appraisal_id = None
    if appraisals:
        labels = [f"#{a['id']} — {a['version_name']} ({a['created_at']})" for a in appraisals]
        pick = st.selectbox("Load saved version", ["(none)"] + labels)
        if pick != "(none)":
            appraisal_id = int(pick.split("—")[0].strip().replace("#", ""))

# Load assumptions if an appraisal picked
if "assumptions_dict" not in st.session_state:
    st.session_state.assumptions_dict = Assumptions().to_dict()

if appraisal_id:
    loaded = load_appraisal(appraisal_id)
    if loaded:
        st.session_state.assumptions_dict = loaded[0]

a = Assumptions.from_dict(st.session_state.assumptions_dict)

# Main layout
left, right = st.columns([0.52, 0.48], gap="large")

with left:
    st.subheader("Inputs")

    colA, colB = st.columns(2)
    with colA:
        a.currency = st.selectbox("Currency", ["GBP", "EUR", "USD", "ZAR"], index=["GBP","EUR","USD","ZAR"].index(a.currency) if a.currency in ["GBP","EUR","USD","ZAR"] else 0)
        a.sellable_sqm = st.number_input("Sellable area (m²)", min_value=0.0, value=float(a.sellable_sqm), step=50.0)
        a.sale_price_per_sqm = st.number_input("Sales price (per m²)", min_value=0.0, value=float(a.sale_price_per_sqm), step=50.0)
        a.sales_curve = st.selectbox("Sales curve", ["linear", "front", "back"], index=["linear","front","back"].index(a.sales_curve) if a.sales_curve in ["linear","front","back"] else 0)

    with colB:
        a.build_cost_per_sqm = st.number_input("Build cost (per m²)", min_value=0.0, value=float(a.build_cost_per_sqm), step=50.0)
        a.contingency_rate = st.slider("Contingency (%)", 0.0, 0.20, float(a.contingency_rate), 0.01)
        a.escalation_rate_pa = st.slider("Escalation (p.a.)", 0.0, 0.15, float(a.escalation_rate_pa), 0.005)
        a.professional_fees_rate = st.slider("Professional fees (%)", 0.0, 0.20, float(a.professional_fees_rate), 0.01)

    st.markdown("### Programme")
    p1, p2, p3 = st.columns(3)
    a.build_months = int(p1.number_input("Build months", 1, 60, int(a.build_months)))
    a.sales_months = int(p2.number_input("Sales months", 1, 60, int(a.sales_months)))
    a.overhead_per_month = float(p3.number_input("Overhead / month", min_value=0.0, value=float(a.overhead_per_month), step=1000.0))

    st.markdown("### Sales & stat costs")
    s1, s2, s3 = st.columns(3)
    a.marketing_rate = s1.slider("Marketing (% of GDV)", 0.0, 0.10, float(a.marketing_rate), 0.005)
    a.statutory_costs = float(s2.number_input("Statutory costs (lump sum)", min_value=0.0, value=float(a.statutory_costs), step=10000.0))
    a.incentive_grant = float(s3.number_input("Incentive / grant (reduces cost)", value=float(a.incentive_grant), step=10000.0))

    st.markdown("### Profit target")
    t1, t2 = st.columns(2)
    a.target_profit_basis = t1.selectbox("Target basis", ["gdv", "cost"], index=["gdv","cost"].index(a.target_profit_basis) if a.target_profit_basis in ["gdv","cost"] else 0)
    a.target_profit_rate = t2.slider("Target profit (%)", 0.0, 0.35, float(a.target_profit_rate), 0.01)

    st.markdown("### Finance (MVP)")
    f1, f2, f3, f4 = st.columns(4)
    a.use_debt = f1.toggle("Use debt", value=bool(a.use_debt))
    a.debt_interest_rate_pa = f2.slider("Interest p.a.", 0.0, 0.25, float(a.debt_interest_rate_pa), 0.005)
    a.debt_arrangement_fee_rate = f3.slider("Arrangement fee", 0.0, 0.05, float(a.debt_arrangement_fee_rate), 0.001)
    a.debt_exit_fee_rate = f4.slider("Exit fee", 0.0, 0.05, float(a.debt_exit_fee_rate), 0.001)

    st.markdown("### Policy / overlays (MVP toggles)")
    o1, o2, o3 = st.columns(3)
    a.inclusionary_rate = o1.slider("Inclusionary (%)", 0.0, 0.30, float(a.inclusionary_rate), 0.01)
    a.inclusionary_price_per_sqm = float(o2.number_input("Affordable price / m²", min_value=0.0, value=float(a.inclusionary_price_per_sqm), step=50.0))
    a.heritage_cost_uplift_rate = o3.slider("Heritage cost uplift", 0.0, 0.20, float(a.heritage_cost_uplift_rate), 0.01)

    st.markdown("### Land input (optional)")
    a.land_price_input = st.number_input("If you want profit on a fixed land price, enter it here (leave 0 for residual solve).",
                                         value=float(a.land_price_input or 0.0), step=50000.0)
    if a.land_price_input == 0.0:
        a.land_price_input = None

    # persist assumptions
    st.session_state.assumptions_dict = a.to_dict()

with right:
    st.subheader("Outputs")

    out = run_appraisal(a)
    kpi_row(out)

    ccy = out.currency
    k1, k2, k3 = st.columns(3)
    k1.metric("Finance costs", money(out.finance_costs, ccy))
    k2.metric("Peak debt", money(out.peak_debt, ccy))
    k3.metric("Equity IRR p.a.", "-" if out.irr_equity_pa is None else pct(out.irr_equity_pa))

    st.markdown("### Cashflow (Net ex land, by month)")
    df_cf = pd.DataFrame(out.cashflow_table)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df_cf["Month"], y=df_cf["Net (ex land)"], name="Net (ex land)"))
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

    tabs = st.tabs(["Sensitivity", "Audit", "Save"])
    with tabs[0]:
        st.caption("2D sensitivity: price vs build cost → residual land value")
        rows, cols, mat = sensitivity_grid(a)
        heat = go.Figure(data=go.Heatmap(
            z=mat,
            x=cols,
            y=rows,
            hovertemplate="Price: %{y}<br>Cost: %{x}<br>Land: %{z:,.0f}<extra></extra>"
        ))
        heat.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(heat, use_container_width=True)
        st.dataframe(pd.DataFrame(mat, index=rows, columns=cols).applymap(lambda v: money(float(v), ccy)), use_container_width=True)

    with tabs[1]:
        df_audit = pd.DataFrame(out.audit)
        # nicer formatting without pandas Styler to avoid Arrow/matplotlib issues
        def fmt_row(r):
            if r.get("unit") == "ratio":
                return pct(float(r["value"]))
            if r.get("unit") == ccy:
                return money(float(r["value"]), ccy)
            return r["value"]
        df_audit["Display"] = df_audit.apply(fmt_row, axis=1)
        st.dataframe(df_audit[["section", "key", "Display"]], use_container_width=True)

    with tabs[2]:
        st.caption("Save this as a version under the selected project.")
        version = st.text_input("Version name", value="Base Case")
        if st.button("💾 Save appraisal version", use_container_width=True):
            app_id = save_appraisal(project_id, version.strip() or "Base Case", a.to_dict(), out.headline())
            st.success(f"Saved appraisal #{app_id}")
            st.rerun()
