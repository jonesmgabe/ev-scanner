"""DFS pick'em break-even helpers (PrizePicks, Pick6, Underdog, Dabble, ...).

Pick'em apps pay fixed multipliers on multi-leg entries instead of quoting odds,
so a leg is only +EV relative to an entry type. For an n-leg entry the per-leg
break-even probability p* solves  E[payout](p*) == 1  assuming independent legs
with equal win probability. The scanner compares the de-vigged fair probability
of each prop leg against the lowest p* of the app's standard entries.

Payout tables live in config.toml under [dfs.<bookmaker_key>] together with a
cited source. They change - verify in the app.
"""
from __future__ import annotations

from math import comb


def expected_payout(p: float, legs: int, table: dict[int, float]) -> float:
    return sum(mult * comb(legs, k) * p ** k * (1 - p) ** (legs - k) for k, mult in table.items())


def breakeven_prob(legs: int, table: dict[int, float], tol: float = 1e-10) -> float:
    lo, hi = 0.0, 1.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if expected_payout(mid, legs, table) < 1:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def entries_from_config(app_cfg: dict) -> dict[str, dict[int, dict[int, float]]]:
    """Normalise a [dfs.<key>] config block to {family: {legs: {hits: mult}}}."""
    out: dict[str, dict[int, dict[int, float]]] = {}
    max_legs = int(app_cfg.get("max_legs", 99))
    for family in ("power", "flex"):
        for legs_s, val in (app_cfg.get(family) or {}).items():
            legs = int(legs_s)
            if legs > max_legs:
                continue
            table = {legs: float(val)} if isinstance(val, (int, float)) else {int(h): float(m) for h, m in val.items()}
            out.setdefault(family, {})[legs] = table
    return out


def breakeven_table(app_cfg: dict) -> list[dict]:
    rows = []
    for family, by_legs in entries_from_config(app_cfg).items():
        for legs, table in sorted(by_legs.items()):
            rows.append({"entry": f"{legs}-pick {family}", "legs": legs, "family": family,
                         "breakeven_leg_prob": breakeven_prob(legs, table)})
    return rows


def best_entry(app_cfg: dict | None) -> tuple[str | None, float]:
    """Lowest per-leg break-even among the app's entries -> (label, p*)."""
    rows = breakeven_table(app_cfg or {})
    if not rows:
        return None, 1.0
    r = min(rows, key=lambda r: r["breakeven_leg_prob"])
    return r["entry"], r["breakeven_leg_prob"]


def leg_edge(fair_prob: float, legs: int, table: dict[int, float]) -> float:
    """Entry EV% if every leg had this fair probability."""
    return (expected_payout(fair_prob, legs, table) - 1) * 100
