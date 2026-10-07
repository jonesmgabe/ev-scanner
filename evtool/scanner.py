"""Core +EV scan: build fair probabilities from the sharp book (or consensus)
and compare every target-book price on the exact same line."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from statistics import mean
from zoneinfo import ZoneInfo

from . import devig as dv

ET = ZoneInfo("America/New_York")


@dataclass
class EVBet:
    sport: str
    event: str
    event_id: str
    commence_utc: str
    commence_et: str
    market: str
    selection: str
    book_key: str
    book: str
    book_decimal: float          # raw price from the API
    effective_decimal: float     # after exchange fees (== raw for sportsbooks)
    book_american: str
    fair_prob: float
    fair_american: str
    ev_pct: float
    kelly_pct: float | None      # None for DFS pick'em legs (Kelly n/a per leg)
    fair_source: str             # "pinnacle" or "consensus(n)"
    sharp_prices: str            # the de-vigged prices, for transparency
    book_last_update: str
    is_prop: bool = False
    is_dfs: bool = False
    # book-comparison data for this exact market/selection/line (see build_quotes)
    quotes: list = field(default_factory=list)
    other_lines: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


MARKET_LABELS = {
    "h2h": "Moneyline", "spreads": "Spread", "totals": "Total",
    # props
    "player_pass_yds": "Pass Yds", "player_rush_yds": "Rush Yds", "player_reception_yds": "Rec Yds",
    "player_receptions": "Receptions", "player_pass_tds": "Pass TDs", "player_anytime_td": "Anytime TD",
    "player_points": "Points", "player_rebounds": "Rebounds", "player_assists": "Assists",
    "player_threes": "Threes", "player_points_rebounds_assists": "PRA",
    "player_shots_on_goal": "Shots on Goal", "player_goals": "Goals",
    "batter_hits": "Hits", "batter_total_bases": "Total Bases", "pitcher_strikeouts": "Pitcher Ks",
    "batter_home_runs": "Home Runs",
}


# --------------------------------------------------------------- line keys
def line_key(market_key: str, outcome: dict, event: dict):
    """Identify the 'line' an outcome belongs to so that all sides of one
    proposition share a key, and lines only match on the exact same point.

    h2h            -> None
    spreads        -> the home team's handicap (away side is the negation)
    totals         -> the total
    props (later)  -> (player description, point)
    """
    if market_key.startswith("h2h"):
        return None
    if market_key.startswith("spreads") or market_key.startswith("alternate_spreads"):
        pt = float(outcome["point"])
        return pt if outcome["name"] == event["home_team"] else -pt
    if "description" in outcome:  # player props: exact player + exact line
        player = outcome.get("_player_key", outcome["description"])
        return (player, float(outcome.get("point", 0) or 0))
    return float(outcome.get("point", 0) or 0)  # totals & team totals


def expected_sides(market_key: str, lines_outcomes: dict) -> int:
    if market_key.startswith("h2h"):
        return 3 if any(n.lower() == "draw" for n in lines_outcomes) else 2
    return 2


def selection_label(market_key: str, outcome: dict) -> str:
    name = outcome["name"]
    if market_key.startswith("h2h"):
        return f"{name} ML"
    if market_key.startswith("spreads"):
        pt = float(outcome["point"])
        return f"{name} {pt:+g}"
    if "description" in outcome:
        pt = outcome.get("point")
        return f'{outcome["description"]} {name}' + (f" {pt:g}" if pt is not None else "")
    return f'{name} {outcome["point"]:g}' if outcome.get("point") is not None else name


def index_event(event: dict, normalize_player=None) -> dict:
    """{book_key: {market_key: {"last_update": str, "lines": {lk: {name: outcome}}}}}"""
    idx: dict = {}
    for b in event.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"].endswith("_lay"):
                continue
            lines: dict = {}
            for o in m.get("outcomes", []):
                if normalize_player and "description" in o:
                    o = {**o, "_player_key": normalize_player(o["description"])}
                line = lines.setdefault(line_key(m["key"], o, event), {})
                # exchanges (e.g. ProphetX) can list several price levels for the
                # same selection - keep the best available price
                if o["name"] not in line or float(o["price"]) > float(line[o["name"]]["price"]):
                    line[o["name"]] = o
            idx.setdefault(b["key"], {"title": b["title"], "markets": {}})["markets"][m["key"]] = {
                "last_update": m.get("last_update") or b.get("last_update"),
                "lines": lines,
            }
    return idx


IMPLIED_SUM_RANGE = (0.97, 1.20)  # sanity band for a usable reference line


def _complete(market_key: str, line: dict) -> bool:
    if not (len(line) >= 2 and len(line) == expected_sides(market_key, line)):
        return False
    if any(float(o["price"]) <= 1.01 for o in line.values()):
        return False  # settled/suspended side (e.g. a 1.00 exchange quote)
    try:
        s = sum(1 / float(o["price"]) for o in line.values())
    except ZeroDivisionError:
        return False
    # rejects junk such as an illiquid exchange quoting 1.01 on both sides
    return IMPLIED_SUM_RANGE[0] <= s <= IMPLIED_SUM_RANGE[1]


def _age_min(ts: str | None, now: datetime) -> float:
    if not ts:
        return 0.0  # no timestamp supplied -> treat as fresh
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (now - t).total_seconds() / 60


REF_MAX_IMPLIED_SUM: dict[str, float] = {}  # per-reference overround cap, from config


def reference_line(idx: dict, book: str, market_key: str, lk, *, method: str,
                   max_age_min: float, now: datetime):
    """De-vigged probabilities from one reference book's complete, fresh line,
    or None. Exchanges like Novig carry little vig, but devigging still removes
    any residual overround (or underround) so probabilities sum to 1."""
    m = idx.get(book, {}).get("markets", {}).get(market_key)
    if not m or lk not in m["lines"] or not _complete(market_key, m["lines"][lk]):
        return None
    if _age_min(m["last_update"], now) > max_age_min:
        return None
    line = m["lines"][lk]
    max_sum = REF_MAX_IMPLIED_SUM.get(book)
    if max_sum is not None and sum(1 / float(o["price"]) for o in line.values()) > max_sum:
        return None  # wide/illiquid two-sided quote: not a usable fair price
    names = sorted(line)
    probs = dict(zip(names, dv.devig([line[n]["price"] for n in names], method)))
    detail = idx[book]["title"] + " " + " / ".join(
        f"{n.split()[-1] if market_key.startswith(('h2h', 'spreads')) else n} "
        f"{dv.fmt_american(dv.decimal_to_american(line[n]['price']))}" for n in names)
    return probs, detail


def _weighted(found: list) -> tuple[dict, dict]:
    """Weighted average of de-vigged probabilities; returns (probs, {book: weight})."""
    tot_w = sum(w for _, w, _ in found)
    names = set(found[0][2][0])
    probs = {n: sum(w * res[0][n] for _, w, res in found) / tot_w for n in names}
    t = sum(probs.values())
    return {n: v / t for n, v in probs.items()}, {r: w / tot_w for r, w, _ in found}


def fair_probs(idx: dict, market_key: str, lk, *, rc: dict, method: str, exclude: set[str],
               consensus_min: int, max_age_min: float, now: datetime):
    """Fair probabilities for one exact market/line.

    Returns (probs{name: p}, source, detail, weights{book: share}) or None.

    rc (from ref_config): listed references in priority order with weights
    (e.g. 50/30/20), Novig-style fallback references, mode and min_refs.
    * Each reference book is de-vigged on its own first.
    * Books in `exclude` (the target being priced, DFS apps) are never used:
      a FanDuel price is judged against the OTHER references only.
    * If fewer than min_refs listed references have a usable line, fallback
      references are added (in order) until min_refs is met.
    * mode "blend": weighted average of the found references, weights
      renormalised over the books that actually have the line.
      mode "first": the highest-priority found reference sets the price
      (min_refs is still required as confirmation).
    * Still short -> leave-one-out consensus of non-target books (if enabled).
    """
    kw = dict(method=method, max_age_min=max_age_min, now=now)
    found = []
    for r, w in zip(rc["refs"], rc["weights"]):
        if r in exclude or r in rc["unavailable"]:
            continue
        res = reference_line(idx, r, market_key, lk, **kw)
        if res:
            found.append((r, w, res))
    if len(found) < rc["min_refs"]:
        for r in rc["fallback"]:
            if r in exclude or len(found) >= rc["min_refs"]:
                continue
            res = reference_line(idx, r, market_key, lk, **kw)
            if res:
                found.append((r, rc["fallback_weight"], res))
    if found:
        names = set(found[0][2][0])
        found = [f for f in found if set(f[2][0]) == names]
    if found and len(found) >= rc["min_refs"]:
        if rc["mode"] == "first" or len(found) == 1:
            r, _, (probs, detail) = found[0]
            return probs, r, detail, {r: 1.0}
        probs, weights = _weighted(found)
        detail = " | ".join(f"{res[1]} ({weights[r]:.0%})" for r, _, res in found)
        return probs, "blend(" + "+".join(r for r, _, _ in found) + ")", detail, weights
    if rc.get("fallback_mode", "consensus") != "consensus":
        return None
    # consensus fallback: average of each other usable book's de-vigged line
    per_book: list[dict] = []
    used: list[str] = []
    for bk, bdata in idx.items():
        if bk in exclude:
            continue
        m = bdata["markets"].get(market_key)
        if not m or lk not in m["lines"] or not _complete(market_key, m["lines"][lk]):
            continue
        if _age_min(m["last_update"], now) > max_age_min:
            continue
        line = m["lines"][lk]
        cap = REF_MAX_IMPLIED_SUM.get(bk)
        if cap is not None and sum(1 / float(o["price"]) for o in line.values()) > cap:
            continue
        names = sorted(line)
        per_book.append(dict(zip(names, dv.devig([line[n]["price"] for n in names], method))))
        used.append((bk, bdata["title"]))
    if len(per_book) < max(consensus_min, 1):
        return None
    names = set(per_book[0])
    if any(set(p) != names for p in per_book):
        return None
    probs = {n: mean(p[n] for p in per_book) for n in names}
    s = sum(probs.values())
    probs = {n: v / s for n, v in probs.items()}
    w = 1 / len(used)
    return (probs, f"consensus({len(per_book)})", "consensus of " + ", ".join(t for _, t in used),
            {k: w for k, _ in used})


def ref_config(cfg: dict, props: bool) -> dict:
    """Reference settings for one call type ([sharp.main] or [sharp.props])."""
    sh = cfg["sharp"]
    sub = sh.get("props" if props else "main") or {}
    refs = list(sub.get("references") or sh.get("references") or [sh.get("book", "pinnacle")])
    weights = list(sub.get("weights") or sh.get("weights") or [])
    if len(weights) < len(refs):              # default: 50/30/20/... style decay
        base = [0.5, 0.3, 0.2]
        weights += [base[i] if i < len(base) else 0.1 for i in range(len(weights), len(refs))]
    mode = sub.get("mode") or sh.get("mode", "blend")
    mode = {"priority": "first", "first": "first", "blend": "blend"}.get(mode, "blend")
    return {"refs": refs, "weights": [float(w) for w in weights[:len(refs)]],
            "fallback": [r for r in sh.get("fallback_references", []) if r not in refs],
            "fallback_weight": float(sh.get("fallback_weight", 0.1)),
            "unavailable": set(sh.get("unavailable_books", [])),
            "mode": mode, "min_refs": int(sub.get("min_refs", 2 if props else 1)),
            "fallback_mode": sh.get("fallback", "consensus")}


def reference_settings(cfg: dict, props: bool = False) -> tuple[list[str], str, set[str]]:
    """(usable reference books incl. fallbacks, in priority order; mode; never-target books).
    Also loads the per-reference overround caps."""
    sh = cfg["sharp"]
    rc = ref_config(cfg, props)
    never = set(sh.get("never_target", []))
    REF_MAX_IMPLIED_SUM.clear()
    REF_MAX_IMPLIED_SUM.update(sh.get("max_implied_sum_by_ref", {}))
    usable = [r for r in rc["refs"] if r not in rc["unavailable"]] + rc["fallback"]
    return usable, rc["mode"], never


def compare_references(events: list[dict], cfg: dict, a: str, b: str, markets: list[str],
                       now: datetime | None = None) -> list[dict]:
    """For every exact line where both reference books have a usable price,
    record the difference in de-vigged fair probability (one row per line;
    for 2-way lines both sides differ by the same amount)."""
    now = now or datetime.now(timezone.utc)
    reference_settings(cfg)
    sc = cfg["scan"]
    method = sc.get("devig_method", "power")
    age = sc.get("max_fair_odds_age_min", 30)
    rows = []
    for ev in events:
        if datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) <= now:
            continue
        idx = index_event(ev, normalize_player=_norm_player)
        if a not in idx or b not in idx:
            continue
        for mk in markets:
            ma = idx[a]["markets"].get(mk)
            if not ma:
                continue
            for lk in ma["lines"]:
                ra = reference_line(idx, a, mk, lk, method=method, max_age_min=age, now=now)
                rb = reference_line(idx, b, mk, lk, method=method, max_age_min=age, now=now)
                if not ra or not rb or set(ra[0]) != set(rb[0]):
                    continue
                n = sorted(ra[0])[0]
                rows.append({"event": f'{ev["away_team"]} @ {ev["home_team"]}', "market": mk,
                             "line": str(lk), "selection": n,
                             a: round(ra[0][n], 4), b: round(rb[0][n], 4),
                             "abs_diff": round(abs(ra[0][n] - rb[0][n]), 4)})
    return rows


def _norm_player(name: str) -> str:
    return " ".join(name.replace(".", "").split()).casefold()


def _point_of(market_key: str, outcome: dict):
    if market_key.startswith("h2h"):
        return None
    return outcome.get("point")


def _line_label(market_key: str, outcome: dict) -> str:
    pt = _point_of(market_key, outcome)
    if pt is None:
        return outcome["name"]
    if market_key.startswith("spreads"):
        return f"{float(pt):+g}"
    return f"{outcome['name']} {float(pt):g}"


def build_quotes(idx: dict, market_key: str, lk, name: str, fair_p: float, *, refs: list[str],
                 never_target: set[str], targets: set[str], dfs_be: dict, fees: dict,
                 max_other: int = 3, weights: dict | None = None,
                 priced_book: str | None = None) -> tuple[list[dict], list[dict]]:
    """Every book's quote for this exact market/selection/line, plus books that
    only list the market at a different line ('other lines').

    role: reference (not bettable) | reference+target (a reference you can also
    bet, e.g. FanDuel for props) | target | dfs | other (fetched for consensus).
    fair_weight: this book's share of THIS bet's fair price (None = not used);
    self_excluded: the book being priced, left out of its own fair price.
    `best` marks the best price among bettable books (after exchange fees)."""
    weights = weights or {}
    quotes, others = [], []
    for bk, bdata in idx.items():
        m = bdata["markets"].get(market_key)
        if not m:
            continue
        line = m["lines"].get(lk)
        if line and name in line:
            o = line[name]
            price = float(o["price"])
            if bk in refs and bk in targets and bk not in never_target:
                role = "reference+target"
            elif bk in refs:
                role = "reference"
            elif bk in never_target:
                role = "not bettable"
            elif bk in dfs_be:
                role = "dfs"
            elif bk in targets:
                role = "target"
            else:
                role = "other"
            opp = [(n2, o2) for n2, o2 in line.items() if n2 != name]
            q = {"book_key": bk, "book": bdata["title"], "role": role,
                 "decimal": price, "american": dv.fmt_american(dv.decimal_to_american(price)) if price > 1.0 else "—",
                 "opposite": ", ".join(
                     (f"{n2.split()[-1]} " if len(opp) > 1 else "") +
                     (dv.fmt_american(dv.decimal_to_american(float(o2["price"]))) if float(o2["price"]) > 1.0 else "—")
                     for n2, o2 in opp),
                 "last_update": m.get("last_update") or "", "best": False,
                 "fair_weight": round(weights[bk], 3) if bk in weights else None,
                 "self_excluded": bk == priced_book and bk in refs}
            if role == "dfs":
                entry, be = dfs_be[bk]
                q.update({"american": f"BE {dv.fmt_american(dv.prob_to_american(be))}" if entry else "—",
                          "opposite": "pick'em", "implied": round(be, 4), "entry": entry,
                          "eff_decimal": round(1 / be, 4),
                          "ev_pct": round((fair_p / be - 1) * 100, 2) if entry else None})
            else:
                eff = dv.apply_fee(bk, price, fees) if price > 1.0 else price
                q.update({"implied": round(1 / price, 4), "eff_decimal": round(eff, 4),
                          "fee_adjusted": abs(eff - price) > 1e-9,
                          "ev_pct": round(dv.ev_pct(fair_p, eff), 2) if price > 1.01 else None})
            quotes.append(q)
        elif not market_key.startswith("h2h"):
            alts = []
            for olk, oline in m["lines"].items():
                if olk == lk or name not in oline:
                    continue
                if isinstance(lk, tuple) and (not isinstance(olk, tuple) or olk[0] != lk[0]):
                    continue  # props: same player only
                o = oline[name]
                if float(o["price"]) <= 1.0:
                    continue
                if isinstance(lk, tuple):
                    dist = abs(float(olk[1]) - float(lk[1]))
                else:
                    dist = abs(float(olk or 0) - float(lk or 0))   # same key space (home handicap / total)
                opp = [o2 for n2, o2 in oline.items() if n2 != name]
                alts.append((dist, {"book_key": bk, "book": bdata["title"],
                                    "role": ("reference+target" if bk in refs and bk in targets else
                                             "reference" if bk in refs else ("dfs" if bk in dfs_be else
                                             ("target" if bk in targets else "other"))),
                                    "line": _line_label(market_key, o),
                                    "american": dv.fmt_american(dv.decimal_to_american(float(o["price"]))),
                                    "opposite": ", ".join(dv.fmt_american(dv.decimal_to_american(float(o2["price"])))
                                                          for o2 in opp if float(o2["price"]) > 1.0)}))
            alts.sort(key=lambda t: t[0])
            others += [a for _, a in alts[:max_other]]
    bettable = [q for q in quotes if q["role"] in ("target", "reference+target") and q.get("ev_pct") is not None]
    if bettable:
        top = max(q["eff_decimal"] for q in bettable)
        for q in bettable:
            q["best"] = abs(q["eff_decimal"] - top) < 1e-9
    order = {"reference": 0, "reference+target": 0, "target": 1, "dfs": 2, "other": 3, "not bettable": 4}
    quotes.sort(key=lambda q: (order.get(q["role"], 9), -(q.get("eff_decimal") or 0)))
    others.sort(key=lambda q: (order.get(q["role"], 9), q["book"]))
    return quotes, others


def scan_events(events: list[dict], sport_label: str, cfg: dict, now: datetime | None = None,
                markets: list[str] | None = None, props: bool = False,
                age_now: datetime | None = None) -> list[EVBet]:
    """Scan already-fetched events. `markets` limits which market keys are
    evaluated; `props=True` switches to prop rules (consensus minimum and
    DFS pick'em break-even comparison)."""
    from . import dfs

    now = now or datetime.now(timezone.utc)
    age_now = age_now or now   # offline re-scans judge staleness at fetch time, not wall-clock
    sc, sh = cfg["scan"], cfg["sharp"]
    pc = cfg.get("props", {})
    refs, ref_mode, never_target = reference_settings(cfg, props)
    rc = ref_config(cfg, props)
    method = sc.get("devig_method", "power")
    markets = set(markets or sc["markets"])
    consensus_min = pc.get("consensus_min_books", 2) if props else sh.get("consensus_min_books", 3)
    min_ev = sc.get("min_ev_pct", 1.0)
    dfs_min_edge = pc.get("dfs_min_edge_pct", min_ev)
    global IMPLIED_SUM_RANGE
    IMPLIED_SUM_RANGE = (sh.get("min_implied_sum", 0.97), sh.get("max_implied_sum", 1.20))
    max_ev = sc.get("max_ev_pct", 25.0)
    # reference-only books (e.g. novig) can never be targets, whatever the target list says
    targets = {b["key"]: b for b in cfg["target_books"]
               if b.get("covered") and b.get("key") and b["key"] not in never_target
               and (props or not b.get("props_only"))}
    dfs_books = {b["key"]: b for b in cfg["target_books"] if b.get("props_only") and b.get("key")}
    dfs_be = {k: dfs.best_entry(cfg.get("dfs", {}).get(k)) for k in dfs_books}
    fees = cfg.get("fees", {})
    out: list[EVBet] = []
    for ev in events:
        start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if sc.get("skip_live", True) and start <= now:
            continue
        idx = index_event(ev, normalize_player=_norm_player)
        ev_name = f'{ev["away_team"]} @ {ev["home_team"]}'
        for tkey, tbook in targets.items():
            tb = idx.get(tkey)
            if not tb:
                continue
            is_dfs = tkey in dfs_books
            for mkey, m in tb["markets"].items():
                if mkey not in markets:
                    continue
                for lk, line in m["lines"].items():
                    # the book being priced is never part of its own fair price
                    fp = fair_probs(idx, mkey, lk, rc=rc, method=method,
                                    exclude={tkey} | set(dfs_books) | (never_target - set(refs)),
                                    consensus_min=consensus_min,
                                    max_age_min=sc.get("max_fair_odds_age_min", 30), now=age_now)
                    if not fp:
                        continue
                    probs, source, detail, fweights = fp
                    for name, o in line.items():
                        if name not in probs:
                            continue
                        p = probs[name]
                        raw = float(o["price"])
                        if raw <= 1.01:  # settled/suspended exchange quote (e.g. 99c+)
                            continue
                        if is_dfs:
                            entry, be = dfs_be[tkey]
                            if not entry:
                                continue
                            # per-leg edge vs the best standard entry's break-even
                            evp = (p / be - 1) * 100
                            eff = 1 / be
                            book_odds = f"BE {dv.fmt_american(dv.prob_to_american(be))}"
                            kelly = None
                            detail_row = f"{detail}; vs {tbook['name']} {entry} break-even {be:.2%}"
                        else:
                            eff = dv.apply_fee(tkey, raw, fees)
                            evp = dv.ev_pct(p, eff)
                            book_odds = dv.fmt_american(dv.decimal_to_american(raw))
                            kelly = round(min(dv.kelly_fraction(p, eff, sc.get("kelly_multiplier", 0.25)) * 100,
                                              sc.get("max_kelly_pct", 100.0)), 2)
                            detail_row = detail
                        if evp <= (dfs_min_edge if is_dfs else min_ev) or evp > max_ev:
                            continue
                        quotes, other_lines = build_quotes(
                            idx, mkey, lk, name, p, refs=refs, never_target=never_target,
                            targets=set(targets) - set(dfs_books), dfs_be=dfs_be, fees=fees,
                            weights=fweights, priced_book=tkey)
                        out.append(EVBet(
                            sport=sport_label, event=ev_name, event_id=ev["id"],
                            commence_utc=ev["commence_time"],
                            commence_et=start.astimezone(ET).strftime("%a %b %d %I:%M %p ET"),
                            market=MARKET_LABELS.get(mkey, mkey),
                            selection=selection_label(mkey, o),
                            book_key=tkey, book=tbook["name"],
                            book_decimal=raw, effective_decimal=round(eff, 4),
                            book_american=book_odds,
                            fair_prob=round(p, 4),
                            fair_american=dv.fmt_american(dv.prob_to_american(p)),
                            ev_pct=round(evp, 2), kelly_pct=kelly,
                            fair_source=source, sharp_prices=detail_row,
                            book_last_update=m["last_update"] or "",
                            is_prop=props, is_dfs=is_dfs,
                            quotes=quotes, other_lines=other_lines,
                        ))
    out = [b for b in out if b.book_key not in never_target]  # belt and braces
    out.sort(key=lambda b: b.ev_pct, reverse=True)
    return out
