from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
import copy


def deep_merge(a: dict, b: dict) -> dict:
    """Merge b into a (recursively) without mutating inputs."""
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class Assumptions:
    # --- SITE / PROGRAMME ---
    currency: str = "GBP"
    start_month_index: int = 0  # month 0 is "start"
    build_months: int = 18
    sales_months: int = 12

    # --- AREAS / MIX (sellable m²) ---
    sellable_sqm: float = 3000.0

    # --- REVENUE (simple: single blended sales price per m²) ---
    sale_price_per_sqm: float = 4500.0  # currency / m²
    sales_curve: str = "linear"  # "linear" or "front" or "back"

    # --- COSTS (simple: blended build cost per m² sellable, plus add-ons) ---
    build_cost_per_sqm: float = 2200.0
    contingency_rate: float = 0.05  # on build cost
    escalation_rate_pa: float = 0.04  # on build cost over build period

    professional_fees_rate: float = 0.08  # % of (build + contingency + escalation)
    statutory_costs: float = 150000.0     # lump sum
    marketing_rate: float = 0.02          # % of GDV
    overhead_per_month: float = 15000.0   # monthly overhead during build+sales

    # --- PROFIT TARGET ---
    target_profit_basis: str = "gdv"      # "gdv" or "cost"
    target_profit_rate: float = 0.18

    # --- FINANCE (very MVP) ---
    use_debt: bool = True
    debt_interest_rate_pa: float = 0.095
    debt_arrangement_fee_rate: float = 0.01  # % of peak debt (approx)
    debt_exit_fee_rate: float = 0.005
    equity_injection_month0: float = 0.0     # optional

    # --- LAND ---
    land_price_input: Optional[float] = None  # if set, compute profit; else solve residual land

    # --- POLICY / OVERLAYS (simple toggles) ---
    inclusionary_rate: float = 0.0           # share of sellable sold at capped price
    inclusionary_price_per_sqm: float = 2500.0
    incentive_grant: float = 0.0             # reduces cost (or add to cashflow as inflow)
    heritage_cost_uplift_rate: float = 0.0   # adds to build costs

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Assumptions":
        base = Assumptions()
        merged = deep_merge(base.to_dict(), d or {})
        return Assumptions(**merged)


@dataclass
class Output:
    currency: str
    gdv: float
    tdc_ex_land: float
    land_value: float
    finance_costs: float
    profit: float
    profit_rate_on_gdv: float
    profit_rate_on_cost: float
    irr_equity_pa: Optional[float]
    peak_debt: float
    months: int
    cashflow_table: List[Dict[str, Any]] = field(default_factory=list)
    audit: List[Dict[str, Any]] = field(default_factory=list)

    def headline(self) -> Dict[str, Any]:
        return {
            "GDV": self.gdv,
            "TDC (ex land)": self.tdc_ex_land,
            "Residual / Land": self.land_value,
            "Finance costs": self.finance_costs,
            "Profit": self.profit,
            "Profit % GDV": self.profit_rate_on_gdv,
            "Profit % Cost": self.profit_rate_on_cost,
            "Peak debt": self.peak_debt,
            "Equity IRR p.a.": self.irr_equity_pa,
        }
