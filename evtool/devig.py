"""Odds conversion, vig removal, EV and Kelly math (pure functions, unit tested)."""
from __future__ import annotations

import math
from typing import Sequence


# ------------------------------------------------------------------ conversions
def american_to_decimal(american: float) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    return 1 + (american / 100 if american > 0 else 100 / -american)


def decimal_to_american(decimal: float) -> float:
    if decimal <= 1:
        raise ValueError("decimal odds must be > 1")
    return (decimal - 1) * 100 if decimal >= 2 else -100 / (decimal - 1)


def prob_to_american(p: float) -> float:
    return decimal_to_american(1 / p)


def fmt_american(american: float) -> str:
    a = round(american)
    return f"+{a}" if a > 0 else str(a)


def implied_probs(decimals: Sequence[float]) -> list[float]:
    return [1 / d for d in decimals]


def overround(decimals: Sequence[float]) -> float:
    """Book margin, e.g. 0.0476 for a -110/-110 market."""
    return sum(implied_probs(decimals)) - 1


# ------------------------------------------------------------------ devig
def devig_multiplicative(decimals: Sequence[float]) -> list[float]:
    """Normalise implied probabilities so they sum to 1 (proportional method)."""
    imp = implied_probs(decimals)
    s = sum(imp)
    return [p / s for p in imp]


def devig_power(decimals: Sequence[float], tol: float = 1e-12, max_iter: int = 200) -> list[float]:
    """Power method: find k with sum(p_i ** k) == 1 and return p_i ** k.

    Takes more vig off longshots than favourites, which matches the
    favourite-longshot bias better than the multiplicative method.
    """
    imp = implied_probs(decimals)
    s = sum(imp)
    if abs(s - 1) < 1e-15:
        return list(imp)
    # sum(p^k) is strictly decreasing in k for 0<p<1. Bracket the root.
    lo, hi = (1.0, 2.0) if s > 1 else (0.5, 1.0)
    f = lambda k: sum(p ** k for p in imp) - 1
    while f(hi) > 0:
        hi *= 2
    while f(lo) < 0:
        lo /= 2
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        v = f(mid)
        if abs(v) < tol:
            break
        if v > 0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    probs = [p ** k for p in imp]
    t = sum(probs)
    return [p / t for p in probs]  # tiny renormalisation for float noise


DEVIG_METHODS = {
    "multiplicative": devig_multiplicative,
    "power": devig_power,
}


def devig(decimals: Sequence[float], method: str = "power") -> list[float]:
    try:
        return DEVIG_METHODS[method](decimals)
    except KeyError:
        raise ValueError(f"unknown devig method {method!r}; choose from {list(DEVIG_METHODS)}")


# ------------------------------------------------------------------ EV / Kelly
def ev_pct(fair_prob: float, decimal_odds: float) -> float:
    """Expected value per unit staked, as a percentage: (p * d - 1) * 100."""
    return (fair_prob * decimal_odds - 1) * 100


def kelly_fraction(fair_prob: float, decimal_odds: float, multiplier: float = 1.0) -> float:
    """Kelly stake as a fraction of bankroll (0 if the bet is -EV)."""
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    f = (b * fair_prob - (1 - fair_prob)) / b
    return max(0.0, f) * multiplier


# ------------------------------------------------------------------ exchange fees
def kalshi_effective_decimal(decimal_odds: float, fee_rate: float = 0.07) -> float:
    """Convert a Kalshi price (expressed as decimal odds) to fee-inclusive odds.

    Kalshi taker fee per contract ~= fee_rate * P * (1 - P) where P is the
    contract price in dollars (rounded up to the cent per order in reality).
    A $1-payout contract therefore effectively costs P + fee.
    """
    p = 1 / decimal_odds
    cost = p + fee_rate * p * (1 - p)
    return 1 / cost


def apply_fee(book_key: str, decimal_odds: float, fees: dict) -> float:
    spec = fees.get(book_key)
    if not spec:
        return decimal_odds
    if isinstance(spec, dict) and spec.get("type") in ("kalshi_taker", "taker_p1p"):
        # Kalshi and Polymarket US both use fee = theta * C * p * (1 - p)
        return kalshi_effective_decimal(decimal_odds, spec.get("rate", 0.07))
    if isinstance(spec, dict) and spec.get("type") == "winnings_commission":
        # commission charged on net winnings, e.g. 0.02 = 2%
        c = spec.get("rate", 0.0)
        return 1 + (decimal_odds - 1) * (1 - c)
    return decimal_odds
