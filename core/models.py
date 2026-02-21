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
class ProductLine:
    """
    Revenue line item.

    measure:
      - "units": qty * unit_size_sqm * price_per_sqm  (or use price_per_unit if set)
      - "sqm":   qty_sqm * price_per_sqm

    sale_timing:
      - start_month: relative to month 0
      - duration_months: over how many months revenue is recognised
      - curve: "linear" | "front" | "back"
    """
    name: str = "Residential (Market)"
    measure: str = "units"              # "units" or "sqm"
    qty: float = 50.0                   # units if measure="units", sqm if measure="sqm"
    unit_size_sqm: float = 55.0         # only if measure="units"
    price_per_sqm: float = 45000.0      # ZAR / sqm (sales price)
    price_per_unit: Optional[float] = None  # if set, overrides sqm calc for units

    start_month: int = 12
    duration_months: int = 12
    curve: str = "linear"               # "linear" | "front" | "back"

    # Optional inclusionary/affordable within this product line (share sold at capped price)
    affordable_share: float = 0.0
    affordable_price_per_sqm: float = 25000.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ProductLine":
        base = ProductLine()
        merged = deep_merge(base.to_dict(), d or {})
        return ProductLine(**merged)

    def gross_value(self) -> float:
        """Total GDV contributed by this product line."""
        if self.measure == "sqm":
            total_sqm = float(self.qty)
            market_sqm = total_sqm * (1.0 - float(self.affordable_share))
            aff_sqm = total_sqm - market_sqm
            return market_sqm * float(self.price_per_sqm) + aff_sqm * float(self.affordable_price_per_sqm)

        # measure == "units"
        units = float(self.qty)
        if self.price_per_unit is not None:
            total_val = units * float(self.price_per_unit)
            # If affordable share is used with price_per_unit, we approximate using sqm pricing for split.
            # Prefer setting price_per_sqm + unit_size_sqm if you need accurate split.
            if self.affordable_share > 0:
                total_sqm = units * float(self.unit_size_sqm)
                market_sqm = total_sqm * (1.0 - float(self.affordable_share))
                aff_sqm = total_sqm - market_sqm
                total_val = market_sqm * float(self.price_per_sqm) + aff_sqm * float(self.affordable_price_per_sqm)
            return total_val

        total_sqm = units * float(self.unit_size_sqm)
        market_sqm = total_sqm * (1.0 - float(self.affordable_share))
        aff_sqm = total_sqm - market_sqm
        return market_sqm * float(self.price_per_sqm) + aff_sqm * float(self.affordable_price_per_sqm)

    def sellable_sqm(self) -> float:
        if self.measure == "sqm":
            return float(self.qty)
        return float(self.qty) * float(self.unit_size_sqm)


@dataclass
class Assumptions:
    # --- GLOBAL ---
    currency: str = "ZAR"

    # --- PROGRAMME ---
    months_total: int = 30  # cashflow horizon (month 0..months_total-1)

    # --- PRODUCT MIX ---
    products: List[ProductLine] = field(default_factory=lambda: [
        ProductLine(
            name="Residential (Market)",
            measure="units",
            qty=60,
            unit_size_sqm=55,
            price_per_sqm=45000,
            start_month=12,
            duration_months=12,
            curve="linear",
        ),
        ProductLine(
            name="Retail / Commercial",
            measure="sqm",
            qty=800,
            price_per_sqm=60000,
            start_month=16,
            duration_months=10,
            curve="back",
        ),
    ])

    # --- COSTS (construction on GBA proxy; we use sellable sqm as proxy in MVP) ---
    build_cost_per_sqm: float = 22000.0
    contingency_rate: float = 0.05
    escalation_rate_pa: float = 0.08  # ZA-style escalation, but can set as you like
    heritage_cost_uplift_rate: float = 0.0

    professional_fees_rate: float = 0.08     # % of build (incl contingency/escalation/uplift)
    statutory_costs: float = 500000.0        # lump sum
    marketing_rate: float = 0.02             # % of GDV
    overhead_per_month: float = 45000.0      # monthly overhead across horizon
    incentive_grant: float = 0.0             # reduces cost (month 0 as negative cost)

    # --- COST TIMING ---
    build_start_month: int = 0
    build_months: int = 18

    # --- PROFIT TARGET (UK-style residual: developer return basis switch) ---
    target_profit_basis: str = "gdv"         # "gdv" or "cost"
    target_profit_rate: float = 0.18

    # --- LAND ---
    land_price_input: Optional[float] = None  # if set, compute profit; else solve residual land
    land_paid_month: int = 0                  # MVP: land paid at month 0

    # --- FINANCE (monthly, explicit) ---
    use_debt: bool = True
    debt_interest_rate_pa: float = 0.115
    debt_arrangement_fee_rate: float = 0.01
    debt_exit_fee_rate: float = 0.005
    fees_paid_month: int = 0                  # arrangement fee month (typically first draw / month 0)

    equity_injection_month0: float = 0.0      # optional

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["products"] = [p.to_dict() for p in self.products]
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Assumptions":
        base = Assumptions()
        merged = deep_merge(base.to_dict(), d or {})

        # rebuild products properly
        plist = []
        for p in (merged.get("products") or []):
            plist.append(ProductLine.from_dict(p))
        merged["products"] = plist

        return Assumptions(**merged)


@dataclass
class Output:
    currency: str
    gdv: float
    sellable_sqm: float

    tdc_ex_land_ex_fin: float
    finance_costs: float
    land_value: float

    total_cost_incl_land: float
    profit: float
    profit_rate_on_gdv: float
    profit_rate_on_cost: float

    peak_debt: float
    irr_equity_pa: Optional[float]

    cashflow_rows: List[Dict[str, Any]] = field(default_factory=list)
    audit: List[Dict[str, Any]] = field(default_factory=list)

    def headline(self) -> Dict[str, Any]:
        return {
            "GDV": self.gdv,
            "Sellable sqm": self.sellable_sqm,
            "TDC (ex land, ex fin)": self.tdc_ex_land_ex_fin,
            "Finance costs": self.finance_costs,
            "Residual / Land": self.land_value,
            "Total cost (incl land)": self.total_cost_incl_land,
            "Profit": self.profit,
            "Profit % GDV": self.profit_rate_on_gdv,
            "Profit % Cost": self.profit_rate_on_cost,
            "Peak debt": self.peak_debt,
            "Equity IRR p.a.": self.irr_equity_pa,
        }
