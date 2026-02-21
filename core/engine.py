from __future__ import annotations

from typing import Dict, Any, List, Tuple
import numpy as np

from .models import Assumptions, Output, ProductLine
from .finance import irr_monthly, annualize_monthly_rate, monthly_rate_from_pa


def _weights(n: int, curve: str) -> np.ndarray:
    if n <= 0:
        return np.array([])
    x = np.linspace(0, 1, n)
    if curve == "front":
        w = 1 - x**1.8
    elif curve == "back":
        w = x**1.8 + 0.01
    else:
        w = np.ones(n)
    w = np.maximum(w, 1e-9)
    return w / w.sum()


def _build_weights(n: int) -> np.ndarray:
    """Simple S-curve-ish weights for construction spend."""
    if n <= 0:
        return np.array([])
    x = np.linspace(-2, 2, n)
    s = 1 / (1 + np.exp(-x))
    w = np.diff(np.concatenate([[0], s]))
    w = np.maximum(w, 1e-9)
    return w / w.sum()


def _allocate_product_revenue(products: List[ProductLine], months: int) -> Tuple[np.ndarray, float, float]:
    """Return (monthly_revenue, total_gdv, total_sellable_sqm)."""
    rev = np.zeros(months, dtype=float)
    gdv = 0.0
    sellable = 0.0

    for p in products:
        pv = float(p.gross_value())
        gdv += pv
        sellable += float(p.sellable_sqm())

        start = int(max(0, p.start_month))
        dur = int(max(1, p.duration_months))
        end = min(months, start + dur)
        dur_eff = max(0, end - start)
        if dur_eff == 0:
            continue

        w = _weights(dur_eff, p.curve)
        rev[start:end] += pv * w

    return rev, gdv, sellable


def _allocate_costs(a: Assumptions, months: int, sellable_sqm: float, gdv: float) -> Tuple[np.ndarray, float, List[Dict[str, Any]]]:
    """
    Build the monthly cost stream (ex land, ex finance) and return:
      - monthly_costs
      - tdc_ex_land_ex_fin (total)
      - audit_items (cost breakdown lines)
    """
    audit: List[Dict[str, Any]] = []
    costs = np.zeros(months, dtype=float)

    # Build base + uplifts
    build_base = sellable_sqm * float(a.build_cost_per_sqm)
    heritage_uplift = build_base * float(a.heritage_cost_uplift_rate)
    build_base2 = build_base + heritage_uplift

    # Escalation (approx half-period on build)
    build_years = max(int(a.build_months), 1) / 12.0
    escalation = build_base2 * float(a.escalation_rate_pa) * (build_years / 2.0)

    contingency = (build_base2 + escalation) * float(a.contingency_rate)
    build_total = build_base2 + escalation + contingency

    prof_fees = build_total * float(a.professional_fees_rate)
    marketing = gdv * float(a.marketing_rate)
    overhead = float(a.overhead_per_month) * months
    statutory = float(a.statutory_costs)
    incentive = float(a.incentive_grant)

    # Timing: build costs over build window
    bstart = int(max(0, a.build_start_month))
    bmonths = int(max(1, a.build_months))
    bend = min(months, bstart + bmonths)
    b_eff = max(0, bend - bstart)

    if b_eff > 0:
        bw = _build_weights(b_eff)
        costs[bstart:bend] += build_total * bw
        costs[bstart:bend] += (prof_fees / b_eff)
        costs[bstart:bend] += (statutory / b_eff)
    else:
        costs[0] += build_total + prof_fees + statutory

    # Marketing: spread over months where revenue occurs (fallback: last 12 months)
    if months > 0:
        # Find months with revenue later in UI; for engine we approximate with last 12 months
        m_start = max(0, months - 12)
        mw = _weights(months - m_start, "linear")
        costs[m_start:months] += marketing * mw

    # Overhead: evenly across horizon
    costs += overhead / months

    # Incentive as negative cost at month 0
    costs[0] -= incentive

    tdc = float(costs.sum())

    audit.extend([
        {"section": "Costs", "key": "Build base", "value": build_base, "unit": a.currency},
        {"section": "Costs", "key": "Heritage uplift", "value": heritage_uplift, "unit": a.currency},
        {"section": "Costs", "key": "Escalation", "value": escalation, "unit": a.currency},
        {"section": "Costs", "key": "Contingency", "value": contingency, "unit": a.currency},
        {"section": "Costs", "key": "Build total", "value": build_total, "unit": a.currency},
        {"section": "Costs", "key": "Professional fees", "value": prof_fees, "unit": a.currency},
        {"section": "Costs", "key": "Statutory", "value": statutory, "unit": a.currency},
        {"section": "Costs", "key": "Marketing", "value": marketing, "unit": a.currency},
        {"section": "Costs", "key": "Overhead", "value": overhead, "unit": a.currency},
        {"section": "Costs", "key": "Incentive (reduces cost)", "value": incentive, "unit": a.currency},
        {"section": "Costs", "key": "TDC (ex land, ex finance)", "value": tdc, "unit": a.currency},
    ])

    return costs, tdc, audit


def _simulate_monthly_finance(
    a: Assumptions,
    months: int,
    monthly_revenue: np.ndarray,
    monthly_costs_ex_land_ex_fin: np.ndarray,
    land_value: float,
    peak_guess: float,
) -> Tuple[List[Dict[str, Any]], float, float, float]:
    """
    Explicit monthly cashflow with debt:
      - Start with equity injection at month 0 (cash)
      - Each month: cash += revenue - costs - fees - interest
      - If cash < 0 => draw debt to bring cash to 0
      - If cash > 0 and debt > 0 => repay debt (using cash)
      - Interest charged monthly on opening debt balance; treated as a cash outflow
      - Fees:
          arrangement fee = peak_debt * rate (paid at a.fees_paid_month)
          exit fee        = peak_debt * rate (paid in final month)
    We iterate fees externally by passing a peak_guess; return the actual peak.
    """
    r_m = monthly_rate_from_pa(float(a.debt_interest_rate_pa))

    cash = 0.0
    debt = 0.0
    peak = 0.0
    finance_costs = 0.0

    rows: List[Dict[str, Any]] = []

    # Fee schedule based on peak_guess (outer iteration refines)
    arr_fee = float(peak_guess) * float(a.debt_arrangement_fee_rate) if a.use_debt else 0.0
    exit_fee = float(peak_guess) * float(a.debt_exit_fee_rate) if a.use_debt else 0.0

    for m in range(months):
        rev = float(monthly_revenue[m])
        cost = float(monthly_costs_ex_land_ex_fin[m])

        land = 0.0
        if a.land_paid_month == m:
            land = float(land_value)

        equity_in = 0.0
        if m == 0:
            equity_in = float(a.equity_injection_month0)

        fee = 0.0
        if a.use_debt and m == int(a.fees_paid_month):
            fee += arr_fee
        if a.use_debt and m == months - 1:
            fee += exit_fee

        # Interest on opening balance
        interest = float(debt) * float(r_m) if a.use_debt else 0.0

        # Cash movement before debt draw/repay
        cash += equity_in + rev - cost - land - fee - interest

        debt_draw = 0.0
        debt_repay = 0.0

        if a.use_debt:
            if cash < 0:
                debt_draw = -cash
                debt += debt_draw
                cash = 0.0
            elif cash > 0 and debt > 0:
                debt_repay = min(cash, debt)
                debt -= debt_repay
                cash -= debt_repay

        peak = max(peak, debt)
        finance_costs += interest + fee

        rows.append({
            "Month": m,
            "Equity in": equity_in,
            "Revenue": rev,
            "Costs (ex land, ex fin)": cost,
            "Land": land,
            "Interest": interest,
            "Fees": fee,
            "Net pre debt": equity_in + rev - cost - land - interest - fee,
            "Debt draw": debt_draw,
            "Debt repay": debt_repay,
            "Debt balance": debt,
            "Cash balance": cash,
        })

    return rows, float(finance_costs), float(peak), float(arr_fee + exit_fee)


def run_appraisal(a: Assumptions, max_iter: int = 30) -> Output:
    """
    Full appraisal:
      - Product mix revenue schedule and GDV
      - Cost schedule ex land/ex finance
      - Monthly debt cashflow (interest + fees)
      - Residual land solve if land not input, to hit target developer return (gdv or cost)
    """
    months = int(max(1, a.months_total))

    audit: List[Dict[str, Any]] = []
    monthly_revenue, gdv, sellable_sqm = _allocate_product_revenue(a.products, months)

    audit.append({"section": "Revenue", "key": "GDV", "value": gdv, "unit": a.currency})
    audit.append({"section": "Areas", "key": "Sellable sqm (sum of products)", "value": sellable_sqm, "unit": "sqm"})

    monthly_costs, tdc_ex_land_ex_fin, cost_audit = _allocate_costs(a, months, sellable_sqm, gdv)
    audit.extend(cost_audit)

    def target_profit(total_cost_incl_land: float) -> float:
        if a.target_profit_basis == "cost":
            return total_cost_incl_land * float(a.target_profit_rate)
        return gdv * float(a.target_profit_rate)

    # Residual solve loop (land affects finance via month cash needs)
    land = float(a.land_price_input) if a.land_price_input is not None else 0.0
    finance_costs = 0.0
    peak_debt = 0.0
    cashflow_rows: List[Dict[str, Any]] = []

    # Outer fixed-point iterations
    for _ in range(max_iter):
        # Fee/peak refinement: small inner loop
        peak_guess = max(peak_debt, 0.0)
        for __ in range(4):
            rows, fin_cost, peak, _fees = _simulate_monthly_finance(
                a=a,
                months=months,
                monthly_revenue=monthly_revenue,
                monthly_costs_ex_land_ex_fin=monthly_costs,
                land_value=land,
                peak_guess=max(peak_guess, peak_debt, 1.0),
            )
            # Use actual peak as next guess
            if abs(peak - peak_guess) < 1.0:
                peak_guess = peak
                finance_costs = fin_cost
                peak_debt = peak
                cashflow_rows = rows
                break
            peak_guess = peak
            finance_costs = fin_cost
            peak_debt = peak
            cashflow_rows = rows

        if a.land_price_input is not None:
            break

        # Solve land to hit target return
        # Profit = GDV - (TDC_ex + finance + land)
        # If basis = GDV: target profit is GDV * rate => land = GDV - TDC_ex - finance - target_profit
        # If basis = Cost: target profit = (TDC_ex + finance + land) * rate
        #   => GDV - (C + land) = (C + land)*p
        #   => GDV = (C + land)*(1+p) => land = GDV/(1+p) - C
        C = float(tdc_ex_land_ex_fin + finance_costs)
        if a.target_profit_basis == "gdv":
            desired_profit = gdv * float(a.target_profit_rate)
            land_new = gdv - C - desired_profit
        else:
            land_new = (gdv / (1.0 + float(a.target_profit_rate))) - C

        if abs(land_new - land) < 5.0:
            land = float(land_new)
            break
        land = float(land_new)

    total_cost_incl_land = float(tdc_ex_land_ex_fin + finance_costs + land)
    profit = float(gdv - total_cost_incl_land)
    profit_rate_on_gdv = float(profit / gdv) if gdv else 0.0
    profit_rate_on_cost = float(profit / total_cost_incl_land) if total_cost_incl_land else 0.0

    # Equity IRR approximation using net equity cashflows (derived from model cashflow rows)
    irr_pa = None
    try:
        # equity CF: use "Net pre debt" minus debt draw/repay mechanics implies equity is what's not funded by debt
        # For a simple, stable IRR: take actual net cash to equity each month as:
        #   equity_cf = revenue - costs - land - interest - fees - (change in debt)
        eq_cf = []
        prev_debt = 0.0
        for r in cashflow_rows:
            debt_bal = float(r["Debt balance"])
            d_debt = debt_bal - prev_debt
            prev_debt = debt_bal
            eq_cf.append(float(r["Revenue"]) - float(r["Costs (ex land, ex fin)"]) - float(r["Land"]) - float(r["Interest"]) - float(r["Fees"]) - d_debt + float(r["Equity in"]))
        irr_pa = annualize_monthly_rate(irr_monthly(eq_cf))
    except Exception:
        irr_pa = None

    audit.extend([
        {"section": "Profit", "key": "Target basis", "value": a.target_profit_basis, "unit": ""},
        {"section": "Profit", "key": "Target profit rate", "value": float(a.target_profit_rate), "unit": "ratio"},
        {"section": "Finance", "key": "Finance costs (interest+fees)", "value": finance_costs, "unit": a.currency},
        {"section": "Finance", "key": "Peak debt", "value": peak_debt, "unit": a.currency},
        {"section": "Land", "key": "Land value (residual or input)", "value": land, "unit": a.currency},
        {"section": "Profit", "key": "Total cost (incl land)", "value": total_cost_incl_land, "unit": a.currency},
        {"section": "Profit", "key": "Profit", "value": profit, "unit": a.currency},
        {"section": "Profit", "key": "Profit % GDV", "value": profit_rate_on_gdv, "unit": "ratio"},
        {"section": "Profit", "key": "Profit % Cost", "value": profit_rate_on_cost, "unit": "ratio"},
    ])

    return Output(
        currency=a.currency,
        gdv=float(gdv),
        sellable_sqm=float(sellable_sqm),
        tdc_ex_land_ex_fin=float(tdc_ex_land_ex_fin),
        finance_costs=float(finance_costs),
        land_value=float(land),
        total_cost_incl_land=float(total_cost_incl_land),
        profit=float(profit),
        profit_rate_on_gdv=float(profit_rate_on_gdv),
        profit_rate_on_cost=float(profit_rate_on_cost),
        peak_debt=float(peak_debt),
        irr_equity_pa=None if irr_pa is None else float(irr_pa),
        cashflow_rows=cashflow_rows,
        audit=audit,
    )


def sensitivity_grid(
    a: Assumptions,
    price_steps=(-0.1, -0.05, 0, 0.05, 0.1),
    cost_steps=(-0.1, -0.05, 0, 0.05, 0.1),
) -> Tuple[List[str], List[str], np.ndarray]:
    """
    2D sensitivity: (blended) sales prices vs build cost -> residual land.
    Applies price factor to ALL product price_per_sqm (and price_per_unit if set).
    """
    rows = [f"{int(p*100)}%" for p in price_steps]
    cols = [f"{int(c*100)}%" for c in cost_steps]
    mat = np.zeros((len(rows), len(cols)), dtype=float)

    base = a.to_dict()

    for i, dp in enumerate(price_steps):
        for j, dc in enumerate(cost_steps):
            dd = dict(base)
            dd["build_cost_per_sqm"] = float(base["build_cost_per_sqm"]) * (1 + dc)

            # adjust product prices
            plist = []
            for p in base["products"]:
                pp = dict(p)
                pp["price_per_sqm"] = float(pp["price_per_sqm"]) * (1 + dp)
                if pp.get("price_per_unit") is not None:
                    pp["price_per_unit"] = float(pp["price_per_unit"]) * (1 + dp)
                plist.append(pp)
            dd["products"] = plist

            aa = Assumptions.from_dict(dd)
            out = run_appraisal(aa)
            mat[i, j] = out.land_value

    return rows, cols, mat
