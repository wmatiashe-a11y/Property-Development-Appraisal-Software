from __future__ import annotations

from typing import List, Optional


def _npv(rate: float, cashflows: List[float]) -> float:
    return sum(cf / ((1 + rate) ** i) for i, cf in enumerate(cashflows))


def irr_monthly(cashflows: List[float], guess: float = 0.01) -> Optional[float]:
    """
    Newton-Raphson IRR (monthly). Returns None if not solvable.
    """
    if not cashflows:
        return None
    if not (any(x < 0 for x in cashflows) and any(x > 0 for x in cashflows)):
        return None

    r = guess
    for _ in range(120):
        f = _npv(r, cashflows)
        df = 0.0
        for i, cf in enumerate(cashflows):
            if i == 0:
                continue
            df -= i * cf / ((1 + r) ** (i + 1))
        if abs(df) < 1e-12:
            break
        step = f / df
        r -= step
        if r <= -0.999:
            r = -0.9
        if r > 10:
            r = 1.0
        if abs(step) < 1e-8:
            return r
    return None


def annualize_monthly_rate(r_m: Optional[float]) -> Optional[float]:
    if r_m is None:
        return None
    return (1 + r_m) ** 12 - 1


def monthly_rate_from_pa(r_pa: float) -> float:
    return (1 + r_pa) ** (1 / 12) - 1
