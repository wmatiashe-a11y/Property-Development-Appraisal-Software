from __future__ import annotations

from typing import Dict, Any, List, Tuple
import numpy as np

from .models import Assumptions, Output
from .finance import irr_monthly, annualize_monthly_rate, monthly_rate_from_pa


def _sales_weights(n: int, curve: str) -> np.ndarray:
    if n <= 0:
        return np.array([])
    x = np.linspace(0, 1, n)
    if curve == "front":
        w = 1 - x**1.8
    elif curve == "back":
        w = x**1.8 + 0.01
    else:  # linear
        w = np.ones(n)
    w = np.maximum(w, 1e-9)
    return w / w.sum()


def _build_weights(n: int) -> np.ndarray:
    """Simple S-curve-ish weights (MVP)."""
    if n <= 0:
        return np.array([])
    x = np.linspace(-2, 2, n)
    w = 1 / (1 + np.exp(-x))  # sigmoid
    w = np.diff(np.concatenate([[0], w]))
    w = np.maximum(w, 1e-9)
    return w / w.sum()


def run_appraisal(a: Assumptions, max_iter: int = 20) -> Output:
    """
    Deterministic appraisal:
    - Computes GDV with optional inclusionary split
    - Computes costs ex land
    - If land_price_input is None: solves residual land to hit target profit
    - Builds monthly cashflow, applies debt interest on running balance
    - Iterates land/finance linkage lightly for stability
    """
    audit: List[Dict[str, Any]] = []

    months = int(max(1, a.build_months + a.sales_months))
    build_w = _build_weights(a.build_months)
    sales_w = _sales_weights(a.sales_months, a.sales_curve)

    # --- Revenue (GDV) ---
    affordable_sqm = a.sellable_sqm * float(np.clip(a.inclusionary_rate, 0.0, 1.0))
    market_sqm = a.sellable_sqm - affordable_sqm
    gdv = market_sqm * a.sale_price_per_sqm + affordable_sqm * a.inclusionary_price_per_sqm

    audit.append({"section": "Revenue", "key": "Sellable sqm", "value": a.sellable_sqm, "unit": "sqm"})
    audit.append({"section": "Revenue", "key": "Market sqm", "value": market_sqm, "unit": "sqm"})
    audit.append({"section": "Revenue", "key": "Affordable sqm", "value": affordable_sqm, "unit": "sqm"})
    audit.append({"section": "Revenue", "key": "GDV", "value": gdv, "unit": a.currency})

    # --- Build cost base ---
    build_base = a.sellable_sqm * a.build_cost_per_sqm
    heritage_uplift = build_base * a.heritage_cost_uplift_rate
    build_base2 = build_base + heritage_uplift

    # Escalation approx across build duration (MVP: average half-period escalation)
    build_years = max(a.build_months, 1) / 12.0
    escalation = build_base2 * a.escalation_rate_pa * (build_years / 2.0)
    contingency = (build_base2 + escalation) * a.contingency_rate
    build_total = build_base2 + escalation + contingency

    prof_fees = build_total * a.professional_fees_rate
    marketing = gdv * a.marketing_rate
    overhead = a.overhead_per_month * months

    # Incentive grant reduces costs (MVP: treat as cost reduction)
    incentive = float(a.incentive_grant)

    tdc_ex_land_pre_finance = build_total + prof_fees + a.statutory_costs + marketing + overhead - incentive

    audit.extend([
        {"section": "Costs", "key": "Build base", "value": build_base, "unit": a.currency},
        {"section": "Costs", "key": "Heritage uplift", "value": heritage_uplift, "unit": a.currency},
        {"section": "Costs", "key": "Escalation", "value": escalation, "unit": a.currency},
        {"section": "Costs", "key": "Contingency", "value": contingency, "unit": a.currency},
        {"section": "Costs", "key": "Build total", "value": build_total, "unit": a.currency},
        {"section": "Costs", "key": "Professional fees", "value": prof_fees, "unit": a.currency},
        {"section": "Costs", "key": "Statutory", "value": a.statutory_costs, "unit": a.currency},
        {"section": "Costs", "key": "Marketing", "value": marketing, "unit": a.currency},
        {"section": "Costs", "key": "Overhead", "value": overhead, "unit": a.currency},
        {"section": "Costs", "key": "Incentive grant (reduces cost)", "value": incentive, "unit": a.currency},
    ])

    # Target profit (pre-land logic)
    def target_profit(total_cost_incl_land: float) -> float:
        if a.target_profit_basis == "cost":
            return total_cost_incl_land * a.target_profit_rate
        return gdv * a.target_profit_rate

    # Prepare monthly base cashflows (ex land and ex finance)
    monthly_costs = np.zeros(months)
    # Spread build-related components over build months
    if a.build_months > 0:
        build_stream = build_total * build_w
        monthly_costs[:a.build_months] += build_stream
        # professional fees / statutory roughly during build
        monthly_costs[:a.build_months] += (prof_fees / a.build_months)
        monthly_costs[:a.build_months] += (a.statutory_costs / a.build_months)
    # marketing: during sales
    if a.sales_months > 0:
        monthly_costs[a.build_months:] += (marketing * sales_w)
    # overhead: evenly across all months
    monthly_costs += overhead / months
    # incentive: apply month 0 as negative cost
    monthly_costs[0] -= incentive

    monthly_revenue = np.zeros(months)
    if a.sales_months > 0:
        monthly_revenue[a.build_months:] = gdv * sales_w

    r_m = monthly_rate_from_pa(a.debt_interest_rate_pa)

    land = a.land_price_input if a.land_price_input is not None else 0.0
    finance_costs = 0.0
    peak_debt = 0.0

    # Iterate to stabilize land <-> finance (land paid month 0 for MVP)
    for _ in range(max_iter):
        cashflow = monthly_revenue - monthly_costs

        # land treated as month 0 outflow
        cashflow0 = cashflow.copy()
        cashflow0[0] -= land

        if a.use_debt:
            # Debt finances negative running balance; interest charged on drawn balance
            debt_balance = 0.0
            interest_paid = 0.0
            debt_track = np.zeros(months)
            interest_track = np.zeros(months)

            for m in range(months):
                # Apply cashflow first
                debt_balance -= cashflow0[m]  # if cashflow is -ve, debt increases
                if debt_balance < 0:
                    # surplus pays down debt, but debt can't go negative
                    debt_balance = 0.0
                # Interest on end-of-month balance
                i = debt_balance * r_m
                interest_paid += i
                debt_balance += i
                debt_track[m] = debt_balance
                interest_track[m] = i

            peak = float(debt_track.max(initial=0.0))
            arr_fee = peak * a.debt_arrangement_fee_rate
            exit_fee = peak * a.debt_exit_fee_rate
            finance_new = float(interest_paid + arr_fee + exit_fee)

            # Add finance into month-end of build (MVP: allocate to last month)
            # More realistic later: spread fees/interest, but OK for MVP.
            cashflow_fin = cashflow0.copy()
            cashflow_fin[-1] -= (arr_fee + exit_fee)
            cashflow_fin += 0.0  # interest already embedded in debt path, not in cashflow here

            finance_costs = finance_new
            peak_debt = peak
        else:
            finance_costs = 0.0
            peak_debt = 0.0

        # Solve land if residual mode
        if a.land_price_input is None:
            # total cost incl land and finance
            # finance_costs are "extra" costs, add to total
            # profit = GDV - (tdc_ex_land_pre_finance + finance_costs + land)
            # Need profit to match target basis.
            # If basis is cost: target uses total_cost_incl_land, which includes land and finance.
            # Solve with simple rearrangement; if cost basis, do fixed-point iteration.
            if a.target_profit_basis == "gdv":
                # profit = gdv*(1 - target_rate)
                desired_profit = gdv * a.target_profit_rate
                land_new = gdv - (tdc_ex_land_pre_finance + finance_costs) - desired_profit
            else:
                # profit = (cost_incl_land)*target_rate
                # profit = gdv - (cost_ex_land + finance + land)
                # gdv - (c + land) = (c + land)*p
                # gdv = (c + land)*(1 + p)
                # land = gdv/(1+p) - c
                c = (tdc_ex_land_pre_finance + finance_costs)
                land_new = (gdv / (1.0 + a.target_profit_rate)) - c

            # prevent absurd negatives if user inputs are extreme
            land_new = float(land_new)
            if abs(land_new - land) < 1.0:
                land = land_new
                break
            land = land_new
        else:
            break

    # Final totals
    tdc_ex_land = tdc_ex_land_pre_finance
    total_cost_incl_land = tdc_ex_land + finance_costs + land
    profit = gdv - total_cost_incl_land
    profit_rate_on_gdv = (profit / gdv) if gdv else 0.0
    profit_rate_on_cost = (profit / total_cost_incl_land) if total_cost_incl_land else 0.0

    # Equity cashflows (very MVP): assume equity funds any shortfall not covered by debt
    # For now: approximate equity as (total_cost - peak_debt) at month0 outflow + revenues later.
    irr_pa = None
    try:
        # crude equity series: month0 equity out, last month equity back = profit + equity
        equity0 = max(0.0, total_cost_incl_land - peak_debt) + float(a.equity_injection_month0)
        eq_cf = [-equity0] + [0.0] * (months - 2) + [equity0 + profit]
        irr_pa = annualize_monthly_rate(irr_monthly(eq_cf))
    except Exception:
        irr_pa = None

    # Build a cashflow table for UI (show revenue/cost net, plus headline)
    table: List[Dict[str, Any]] = []
    for m in range(months):
        table.append({
            "Month": m + 1,
            "Revenue": float(monthly_revenue[m]),
            "Cost (ex land)": float(monthly_costs[m]),
            "Net (ex land)": float(monthly_revenue[m] - monthly_costs[m]),
        })

    audit.extend([
        {"section": "Profit", "key": "Target basis", "value": a.target_profit_basis, "unit": ""},
        {"section": "Profit", "key": "Target profit rate", "value": a.target_profit_rate, "unit": "ratio"},
        {"section": "Land", "key": "Land value (residual or input)", "value": land, "unit": a.currency},
        {"section": "Finance", "key": "Finance costs", "value": finance_costs, "unit": a.currency},
        {"section": "Finance", "key": "Peak debt", "value": peak_debt, "unit": a.currency},
        {"section": "Profit", "key": "Profit", "value": profit, "unit": a.currency},
        {"section": "Profit", "key": "Profit % GDV", "value": profit_rate_on_gdv, "unit": "ratio"},
        {"section": "Profit", "key": "Profit % Cost", "value": profit_rate_on_cost, "unit": "ratio"},
    ])

    return Output(
        currency=a.currency,
        gdv=float(gdv),
        tdc_ex_land=float(tdc_ex_land),
        land_value=float(land),
        finance_costs=float(finance_costs),
        profit=float(profit),
        profit_rate_on_gdv=float(profit_rate_on_gdv),
        profit_rate_on_cost=float(profit_rate_on_cost),
        irr_equity_pa=None if irr_pa is None else float(irr_pa),
        peak_debt=float(peak_debt),
        months=int(months),
        cashflow_table=table,
        audit=audit,
    )


def sensitivity_grid(a: Assumptions, price_steps=(-0.1, -0.05, 0, 0.05, 0.1), cost_steps=(-0.1, -0.05, 0, 0.05, 0.1)) -> Tuple[List[str], List[str], np.ndarray]:
    """
    Returns (row_labels, col_labels, matrix) for a 2D heatmap:
    rows = price changes, cols = cost changes, values = residual land.
    """
    rows = [f"{int(p*100)}%" for p in price_steps]
    cols = [f"{int(c*100)}%" for c in cost_steps]
    mat = np.zeros((len(rows), len(cols)))

    for i, dp in enumerate(price_steps):
        for j, dc in enumerate(cost_steps):
            aa = Assumptions.from_dict(a.to_dict())
            aa.sale_price_per_sqm = a.sale_price_per_sqm * (1 + dp)
            aa.build_cost_per_sqm = a.build_cost_per_sqm * (1 + dc)
            out = run_appraisal(aa)
            mat[i, j] = out.land_value
    return rows, cols, mat
