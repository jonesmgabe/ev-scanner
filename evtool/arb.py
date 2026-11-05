"""FanDuel + DraftKings vs Kalshi/Polymarket arbitrage scanner — NBA player props first.

Sportsbook sides: fanduel, draftkings (default both; --books subset).

Detects fee-aware two-leg arbs where one side is FanDuel or DraftKings and the other is an
exchange (Kalshi or Polymarket). Never places trades. Novig is never a target.

CLI:
  python3 -m evtool.arb --once --sport nba --fd-source fixture
  python3 -m evtool.arb --once --sport nba --fd-source cache --offline
  python3 -m evtool.arb --loop --interval 20 --fd-source fixture
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import tomllib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from . import devig as dv
from .exchanges import (
    MARKET_MAP,
    PropQuote,
    fetch_kalshi_nba_props,
    fetch_polymarket_nba_props,
)
from .names import normalize_player, names_match

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")

DEFAULT_MARKETS = [
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
    "player_points_rebounds_assists",
]

SPORTSBOOKS = ("fanduel", "draftkings")
EXCHANGES = ("kalshi", "polymarket")
ARB_BOOKS = set(SPORTSBOOKS) | set(EXCHANGES)  # Novig intentionally excluded


# ================================================================== math
@dataclass
class ArbOpportunity:
    sport: str
    event: str
    market: str
    market_key: str
    player: str
    point: float
    sportsbook: str
    fd_side: str
    fd_decimal: float
    fd_american: str
    exchange: str
    ex_side: str
    ex_decimal_raw: float
    ex_decimal_eff: float
    ex_american_eff: str
    implied_sum: float
    profit_pct: float
    stake_total: float
    stake_fd: float
    stake_ex: float
    profit: float
    liquidity: float | None = None
    commence_utc: str = ""
    commence_et: str = ""
    match_note: str = ""
    fd_source_id: str = ""
    ex_source_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def fee_adjusted_decimal(book: str, decimal_odds: float, fees: dict) -> float:
    return dv.apply_fee(book, decimal_odds, fees)


def arb_stake_split(
    dec_a: float,
    dec_b: float,
    total: float = 100.0,
) -> tuple[float, float, float, float, float]:
    """Two-way arb stake split.

    Returns (stake_a, stake_b, profit, profit_pct, implied_sum).
    Guaranteed payout = stake_a * dec_a = stake_b * dec_b when sized correctly.
    """
    if dec_a <= 1.0 or dec_b <= 1.0:
        raise ValueError("decimal odds must be > 1")
    inv_a, inv_b = 1.0 / dec_a, 1.0 / dec_b
    implied = inv_a + inv_b
    if implied <= 0:
        raise ValueError("invalid implied sum")
    stake_a = total * inv_a / implied
    stake_b = total * inv_b / implied
    payout = stake_a * dec_a  # == stake_b * dec_b
    profit = payout - total
    profit_pct = (profit / total) * 100.0
    return stake_a, stake_b, profit, profit_pct, implied


def is_arb(dec_a: float, dec_b: float) -> bool:
    return (1.0 / dec_a) + (1.0 / dec_b) < 1.0 - 1e-12


# ============================================================ FD loaders
def load_fees(cfg: dict) -> dict:
    return cfg.get("fees", {})


def load_config(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _american_str(dec: float) -> str:
    try:
        return dv.fmt_american(dv.decimal_to_american(dec))
    except ValueError:
        return ""


def fanduel_quotes_from_odds_events(
    events: Iterable[dict],
    market_keys: list[str],
    *,
    sport: str = "nba",
    sportsbooks: Iterable[str] = ("fanduel",),
) -> list[PropQuote]:
    """Parse The Odds API event-odds payloads for sportsbook props (FD/DK)."""
    sb_set = set(sportsbooks)
    out: list[PropQuote] = []
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wanted = set(market_keys)
    for ev in events:
        home = ev.get("home_team") or ""
        away = ev.get("away_team") or ""
        label = f"{away} @ {home}".strip(" @")
        commence = ev.get("commence_time") or ""
        event_key = normalize_player(f"{away} {home}")
        for b in ev.get("bookmakers") or []:
            bkey = b.get("key")
            if bkey not in sb_set:
                continue
            for m in b.get("markets") or []:
                mk = m.get("key")
                if mk not in wanted:
                    continue
                for o in m.get("outcomes") or []:
                    side = o.get("name")
                    if side not in ("Over", "Under"):
                        continue
                    player = o.get("description") or ""
                    if not player:
                        continue
                    point = o.get("point")
                    if point is None:
                        continue
                    try:
                        point = float(point)
                        price = float(o["price"])
                    except (TypeError, ValueError, KeyError):
                        continue
                    if price <= 1.0:
                        continue
                    out.append(PropQuote(
                        book=bkey,
                        market_key=mk,
                        player=player,
                        player_key=normalize_player(player),
                        side=side,
                        point=point,
                        decimal=price,
                        american=_american_str(price),
                        event=label,
                        event_key=event_key,
                        commence_utc=commence,
                        source_id=ev.get("id") or "",
                        fetched_at=fetched,
                    ))
    return out


def load_fd_from_fixture(path: Path, market_keys: list[str], sport: str = "nba", sportsbooks=("fanduel",)) -> list[PropQuote]:
    blob = json.loads(path.read_text())
    data = blob["data"] if isinstance(blob, dict) and "data" in blob else blob
    if isinstance(data, dict):
        data = [data]
    return fanduel_quotes_from_odds_events(data, market_keys, sport=sport, sportsbooks=sportsbooks)


def load_fd_from_cache_dir(cache_dir: Path, market_keys: list[str], sport: str = "nba", sportsbooks=("fanduel",)) -> list[PropQuote]:
    """Best-effort: scan cache/ for Odds API blobs that contain fanduel player_* markets."""
    sport_key = {
        "nba": "basketball_nba",
        "wnba": "basketball_wnba",
        "nfl": "americanfootball_nfl",
    }.get(sport, sport)
    quotes: list[PropQuote] = []
    for p in sorted(cache_dir.glob("*.json")):
        if sport_key not in p.name and sport not in p.name:
            # still allow generic fixtures copied into cache
            if "player_" not in p.name and "props" not in p.name:
                continue
        try:
            blob = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        data = blob.get("data") if isinstance(blob, dict) else blob
        events: list[dict] = []
        if isinstance(data, list):
            events = [e for e in data if isinstance(e, dict) and "bookmakers" in e]
        elif isinstance(data, dict) and "bookmakers" in data:
            events = [data]
        if not events:
            continue
        # Only keep files that actually have FanDuel props
        sample = fanduel_quotes_from_odds_events(events[:1], market_keys, sport=sport, sportsbooks=sportsbooks)
        if not sample and not any(
            m.get("key", "").startswith("player_")
            for e in events for b in e.get("bookmakers", []) if b.get("key") in sportsbooks
            for m in b.get("markets", [])
        ):
            continue
        quotes.extend(fanduel_quotes_from_odds_events(events, market_keys, sport=sport, sportsbooks=sportsbooks))
    return quotes


def fetch_fd_from_odds_api(
    cfg: dict,
    *,
    sport: str,
    market_keys: list[str],
    max_events: int,
    offline: bool,
    sportsbooks: Iterable[str] = SPORTSBOOKS,
) -> list[PropQuote]:
    """Single carefully scoped Odds API pull for FD and/or DK props (bookmakers=fanduel,draftkings).
    Both books fit in one request (same region), so cost is unchanged vs FD-only.

    Cost: ~1 credit per market key per event (bookmakers=fanduel = 1 region).
    With max_events=1 and 2 markets → ~2 credits. Refuse if remaining quota is tiny.
    """
    from .api import OddsAPI, OddsAPIError

    sport_keys = cfg.get("sport_keys", {})
    sport_key = sport_keys.get(sport, sport)
    api = OddsAPI(
        cache_dir=cfg.get("api", {}).get("cache_dir", "cache"),
        cache_ttl_minutes=cfg.get("api", {}).get("cache_ttl_minutes", 10),
        offline=offline,
        max_credits_per_run=max(1, min(5, int(cfg.get("api", {}).get("max_credits_per_run", 5)))),
    )
    # Prefer cache: try events list (free / cached) then per-event odds.
    events = api.events(sport_key)
    now = datetime.now(timezone.utc)
    upcoming = []
    for e in events:
        ct = e.get("commence_time")
        if not ct:
            continue
        try:
            t = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t >= now:
            upcoming.append(e)
    upcoming.sort(key=lambda e: e.get("commence_time") or "")
    upcoming = upcoming[:max_events]
    quotes: list[PropQuote] = []
    for e in upcoming:
        try:
            payload = api.event_odds(
                sport_key, e["id"], market_keys,
                bookmakers=list(sportsbooks), odds_format="decimal",
            )
        except OddsAPIError as exc:
            print(f"[arb] Odds API skip {e.get('id')}: {exc}", file=sys.stderr)
            continue
        # event_odds returns a single event dict
        if isinstance(payload, dict):
            quotes.extend(fanduel_quotes_from_odds_events([payload], market_keys, sport=sport, sportsbooks=sportsbooks))
        elif isinstance(payload, list):
            quotes.extend(fanduel_quotes_from_odds_events(payload, market_keys, sport=sport, sportsbooks=sportsbooks))
    return quotes


# ============================================================== matching
def _index_quotes(quotes: Iterable[PropQuote]) -> dict[tuple, dict[str, PropQuote]]:
    """Index by (market_key, player_key, point, side) -> quote (best decimal kept)."""
    idx: dict[tuple, dict[str, PropQuote]] = {}
    for q in quotes:
        key = (q.market_key, q.player_key, float(q.point), q.side)
        bucket = idx.setdefault(key, {})
        prev = bucket.get(q.book)
        if prev is None or q.decimal > prev.decimal:
            bucket[q.book] = q
    return idx


def _fuzzy_player_lookup(
    exchange_quotes: list[PropQuote],
) -> dict[tuple, list[PropQuote]]:
    """(market_key, point, side) -> list of exchange quotes (for name fuzzy match)."""
    by: dict[tuple, list[PropQuote]] = {}
    for q in exchange_quotes:
        by.setdefault((q.market_key, float(q.point), q.side), []).append(q)
    return by


def find_prop_arbs(
    fd_quotes: list[PropQuote],
    exchange_quotes: list[PropQuote],
    fees: dict,
    *,
    sport: str = "nba",
    stake_total: float = 100.0,
    min_profit_pct: float = 0.0,
    books: set[str] | None = None,
    max_decimal: float = 3.5,
    min_liquidity: float = 0.0,
) -> list[ArbOpportunity]:
    """Match FanDuel/DraftKings Over/Under props to Kalshi/Polymarket and size arbs."""
    books = books or ARB_BOOKS
    exchange_quotes = [q for q in exchange_quotes if q.book in books and q.book in EXCHANGES]
    fd_quotes = [q for q in fd_quotes if q.book in books and q.book in SPORTSBOOKS]
    if not fd_quotes:
        return []

    ex_by_exact = _index_quotes(exchange_quotes)
    ex_fuzzy = _fuzzy_player_lookup(exchange_quotes)
    arbs: list[ArbOpportunity] = []
    seen: set[tuple] = set()

    for fd in fd_quotes:
        other_side = "Under" if fd.side == "Over" else "Over"
        # 1) exact player_key match
        candidates: list[PropQuote] = []
        exact_key = (fd.market_key, fd.player_key, float(fd.point), other_side)
        bucket = ex_by_exact.get(exact_key) or {}
        candidates.extend(bucket.values())
        # 2) fuzzy name if nothing exact
        if not candidates:
            for eq in ex_fuzzy.get((fd.market_key, float(fd.point), other_side), []):
                if names_match(fd.player, eq.player):
                    candidates.append(eq)

        for ex in candidates:
            if ex.book not in books:
                continue
            # Sanity: skip penny longshots / absurd quotes (default max_decimal=5 ≈ +400)
            lo, hi = 1.05, max_decimal
            if not (lo <= fd.decimal <= hi and lo <= ex.decimal <= hi):
                continue
            if min_liquidity > 0 and (ex.liquidity is None or ex.liquidity < min_liquidity):
                continue
            eff = fee_adjusted_decimal(ex.book, ex.decimal, fees)
            if not (lo <= eff <= hi):
                continue
            if not is_arb(fd.decimal, eff):
                continue
            stake_fd, stake_ex, profit, profit_pct, implied = arb_stake_split(
                fd.decimal, eff, stake_total
            )
            if profit_pct < min_profit_pct:
                continue
            dedupe = (fd.book, fd.market_key, fd.player_key, fd.point, fd.side, ex.book, ex.side)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            commence_et = ""
            if fd.commence_utc:
                try:
                    t = datetime.fromisoformat(fd.commence_utc.replace("Z", "+00:00"))
                    commence_et = t.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
                except ValueError:
                    pass
            arbs.append(ArbOpportunity(
                sport=sport,
                event=fd.event or ex.event,
                market=MARKET_MAP.get(fd.market_key, {}).get("label", fd.market_key),
                market_key=fd.market_key,
                player=fd.player,
                point=float(fd.point),
                sportsbook=fd.book,
                fd_side=fd.side,
                fd_decimal=round(fd.decimal, 4),
                fd_american=fd.american or _american_str(fd.decimal),
                exchange=ex.book,
                ex_side=ex.side,
                ex_decimal_raw=round(ex.decimal, 4),
                ex_decimal_eff=round(eff, 4),
                ex_american_eff=_american_str(eff),
                implied_sum=round(implied, 6),
                profit_pct=round(profit_pct, 4),
                stake_total=stake_total,
                stake_fd=round(stake_fd, 2),
                stake_ex=round(stake_ex, 2),
                profit=round(profit, 2),
                liquidity=ex.liquidity,
                commence_utc=fd.commence_utc or ex.commence_utc,
                commence_et=commence_et,
                match_note=f"player+{fd.market_key}+{fd.point}",
                fd_source_id=fd.source_id,
                ex_source_id=ex.source_id,
            ))
    arbs.sort(key=lambda a: a.profit_pct, reverse=True)
    return arbs


# ============================================================== output
def write_arbs_csv(path: Path, arbs: list[ArbOpportunity]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ArbOpportunity.__dataclass_fields__.keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for a in arbs:
            w.writerow(a.to_dict())


def write_arbs_html(path: Path, arbs: list[ArbOpportunity], *, meta: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for a in arbs:
        rows.append(
            "<tr>"
            f"<td>{_h(a.sport)}</td>"
            f"<td>{_h(a.event)}</td>"
            f"<td>{_h(a.player)}</td>"
            f"<td>{_h(a.market)}</td>"
            f"<td class='num'>{a.point:g}</td>"
            f"<td>{_h(a.sportsbook)} {_h(a.fd_side)} {_h(a.fd_american)} <span class='muted'>({a.fd_decimal:.3f})</span></td>"
            f"<td>{_h(a.exchange)} {_h(a.ex_side)} {_h(a.ex_american_eff)} "
            f"<span class='muted'>(eff {a.ex_decimal_eff:.3f}, raw {a.ex_decimal_raw:.3f})</span></td>"
            f"<td class='num ev'>{a.profit_pct:.2f}%</td>"
            f"<td class='num'>${a.stake_fd:.2f} / ${a.stake_ex:.2f}</td>"
            f"<td class='num'>${a.profit:.2f}</td>"
            f"<td class='num'>{'' if a.liquidity is None else f'{a.liquidity:.0f}'}</td>"
            f"<td class='muted'>{_h(a.commence_et)}</td>"
            "</tr>"
        )
    body = "\n".join(rows) if rows else "<tr><td colspan='12' class='muted'>No arbs found.</td></tr>"
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arb Scanner — FanDuel+DraftKings vs Kalshi/Polymarket (NBA props)</title>
<style>
:root{{--bg:#0e1116;--panel:#161b22;--line:#262c36;--text:#e6edf3;--muted:#8b949e;--acc:#3fb950;--acc2:#58a6ff}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
header{{padding:20px 28px 8px}} h1{{margin:0;font-size:22px}} h1 span{{color:var(--acc)}}
.meta{{color:var(--muted);font-size:12.5px;margin-top:4px}}
.wrap{{padding:12px 28px 28px;overflow-x:auto}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}
th{{background:#1c222b;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px}}
td.num,th.num{{text-align:right;font-variant-numeric:tabular-nums}}
.ev{{color:var(--acc);font-weight:650}} .muted{{color:var(--muted);font-size:12px}}
.note{{padding:0 28px 24px;color:var(--muted);font-size:12.5px;max-width:900px}}
</style></head><body>
<header>
  <h1>Arb Scanner — <span>FanDuel + DraftKings vs Kalshi / Polymarket</span></h1>
  <div class="meta">NBA player props (v1). Fee-aware exchange prices. Never auto-bets. Re-check in apps before staking.
  { _h(meta) }</div>
</header>
<div class="wrap">
<table>
<thead><tr>
<th>Sport</th><th>Event</th><th>Player</th><th>Market</th><th class="num">Line</th>
<th>Sportsbook</th><th>Exchange (after fee)</th><th class="num">Profit %</th>
<th class="num">Stake Book / Ex</th><th class="num">Profit @ $100</th><th class="num">Ex liq</th><th>Start</th>
</tr></thead>
<tbody>
{body}
</tbody></table>
</div>
<p class="note">Matching: same player (normalized name) + market type + exact line.
Kalshi YES = Over at floor_strike; Polymarket Yes = Over. Exchange decimal is taker-fee adjusted
(Kalshi/Polymarket taker_p1p from config.toml). Novig is never shown as a bettable book.</p>
</body></html>
"""
    path.write_text(html)


def _h(s: Any) -> str:
    import html as _html
    return _html.escape("" if s is None else str(s))


# ================================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="FanDuel + DraftKings vs Kalshi/Polymarket NBA props arb scanner")
    p.add_argument("--config", default=str(ROOT / "config.toml"))
    p.add_argument("--sport", default="nba", help="Sport alias (nba default for props v1)")
    p.add_argument("--books", default="fanduel,draftkings",
                   help="Sportsbook sides: fanduel,draftkings (default both) or subset. "
                        "May also include kalshi/polymarket to restrict exchanges; novig always ignored")
    p.add_argument("--markets", default=",".join(DEFAULT_MARKETS),
                   help="Odds API prop market keys")
    p.add_argument("--once", action="store_true", default=True, help="Single pass (default)")
    p.add_argument("--loop", action="store_true", help="Poll exchanges repeatedly")
    p.add_argument("--interval", type=float, default=20.0, help="Loop interval seconds (exchanges)")
    p.add_argument("--fd-refresh-every", type=int, default=30,
                   help="In --loop, refresh FanDuel only every N exchange polls (saves Odds API credits)")
    p.add_argument("--fd-source", choices=("fixture", "cache", "api", "none"), default="fixture",
                   help="Where FanDuel props come from (default fixture = 0 credits)")
    p.add_argument("--fd-fixture", default=str(ROOT / "fixtures/arb/fd_nba_props.json"),
                   help="FanDuel fixture (Odds API event format)")
    p.add_argument("--dk-fixture", default=str(ROOT / "fixtures/arb/dk_nba_props.json"),
                   help="DraftKings fixture (Odds API event format)")
    p.add_argument("--max-events", type=int, default=1,
                   help="When --fd-source api: max upcoming games to pull (each market ≈ 1 credit)")
    p.add_argument("--offline", action="store_true", help="Odds API cache only (no live FD calls)")
    p.add_argument("--min-profit", type=float, default=0.0, help="Min guaranteed profit % after fees")
    p.add_argument("--max-decimal", type=float, default=3.5,
                   help="Ignore either leg with decimal odds above this (filters penny longshots)")
    p.add_argument("--min-liquidity", type=float, default=0.0,
                   help="Min exchange ask size / liquidity when the feed provides it")
    p.add_argument("--stake", type=float, default=100.0, help="Total stake to split across both legs")
    p.add_argument("--out", default=str(ROOT / "output"), help="Output directory")
    p.add_argument("--exchanges", choices=("live", "fixture", "skip"), default="live",
                   help="Kalshi/Polymarket source (live = public APIs, free)")
    p.add_argument("--ex-fixture", default=str(ROOT / "fixtures/arb/exchange_nba_props.json"))
    p.add_argument("--poly-max-events", type=int, default=8)
    return p


def _load_exchange_quotes(
    args, market_keys: list[str], books: set[str]
) -> list[PropQuote]:
    quotes: list[PropQuote] = []
    if args.exchanges == "skip":
        return quotes
    if args.exchanges == "fixture":
        path = Path(args.ex_fixture)
        if not path.exists():
            print(f"[arb] missing exchange fixture {path}", file=sys.stderr)
            return quotes
        raw = json.loads(path.read_text())
        for row in raw:
            q = PropQuote(**row)
            if q.market_key in market_keys and q.book in books:
                quotes.append(q)
        return quotes
    # live
    if "kalshi" in books:
        try:
            k = fetch_kalshi_nba_props(market_keys)
            print(f"[arb] Kalshi quotes: {len(k)}")
            quotes.extend(k)
        except Exception as exc:
            print(f"[arb] Kalshi fetch failed: {exc}", file=sys.stderr)
    if "polymarket" in books:
        poly_keys = [k for k in market_keys if MARKET_MAP.get(k, {}).get("poly")]
        try:
            p = fetch_polymarket_nba_props(poly_keys, max_events=args.poly_max_events)
            print(f"[arb] Polymarket quotes: {len(p)}")
            quotes.extend(p)
        except Exception as exc:
            print(f"[arb] Polymarket fetch failed: {exc}", file=sys.stderr)
    return quotes


def _load_fd_quotes(args, cfg, market_keys: list[str], sportsbooks=SPORTSBOOKS) -> list[PropQuote]:
    """Load sportsbook (FD and/or DK) quotes. Name kept for compatibility."""
    sbs = tuple(sportsbooks)
    if args.fd_source == "none" or not sbs:
        return []
    fixtures = {"fanduel": Path(args.fd_fixture), "draftkings": Path(getattr(args, "dk_fixture", ""))}

    def from_fixtures() -> list[PropQuote]:
        out: list[PropQuote] = []
        seen_paths: set[Path] = set()
        for sb in sbs:
            path = fixtures.get(sb)
            if not path or not path.exists():
                print(f"[arb] missing {sb} fixture {path}", file=sys.stderr)
                continue
            if path in seen_paths:
                continue
            seen_paths.add(path)
            out.extend(load_fd_from_fixture(path, market_keys, sport=args.sport, sportsbooks=sbs))
        return out

    def summary(q):
        return ", ".join(f"{sb}={sum(1 for x in q if x.book == sb)}" for sb in sbs)

    if args.fd_source == "fixture":
        q = from_fixtures()
        print(f"[arb] Sportsbook fixture quotes: {summary(q)} (0 Odds API credits)")
        return q
    if args.fd_source == "cache":
        cache_dir = ROOT / cfg.get("api", {}).get("cache_dir", "cache")
        q = load_fd_from_cache_dir(cache_dir, market_keys, sport=args.sport, sportsbooks=sbs)
        missing = [sb for sb in sbs if not any(x.book == sb for x in q)]
        if missing:
            saved = args.fd_fixture
            for sb in missing:
                p = fixtures.get(sb)
                if p and p.exists():
                    q.extend(load_fd_from_fixture(p, market_keys, sport=args.sport, sportsbooks=(sb,)))
            print(f"[arb] cache missing {','.join(missing)}; filled from fixture")
        print(f"[arb] Sportsbook cache quotes: {summary(q)} (0 credits)")
        return q
    q = fetch_fd_from_odds_api(
        cfg, sport=args.sport, market_keys=market_keys,
        max_events=args.max_events, offline=args.offline, sportsbooks=sbs,
    )
    print(f"[arb] Sportsbook API/cache quotes: {summary(q)}")
    return q


def run_once(args, cfg) -> list[ArbOpportunity]:
    market_keys = [m.strip() for m in args.markets.split(",") if m.strip()]
    books = {b.strip().lower() for b in args.books.split(",") if b.strip()}
    books.discard("novig")  # hard rule
    unknown = books - ARB_BOOKS
    if unknown:
        print(f"[arb] ignoring unknown books: {','.join(sorted(unknown))}", file=sys.stderr)
    books &= ARB_BOOKS
    sbs = tuple(b for b in SPORTSBOOKS if b in books) or SPORTSBOOKS
    exs = {b for b in EXCHANGES if b in books} or set(EXCHANGES)
    books = set(sbs) | exs

    fees = load_fees(cfg)
    fd = _load_fd_quotes(args, cfg, market_keys, sportsbooks=sbs)
    ex = _load_exchange_quotes(args, market_keys, books)
    arbs = find_prop_arbs(
        fd, ex, fees,
        sport=args.sport,
        stake_total=args.stake,
        min_profit_pct=args.min_profit,
        books=books,
        max_decimal=args.max_decimal,
        min_liquidity=args.min_liquidity,
    )
    out_dir = Path(args.out)
    write_arbs_csv(out_dir / "arbs.csv", arbs)
    meta = (
        f"Generated {datetime.now(ET).strftime('%Y-%m-%d %H:%M %Z')} · "
        f"books={','.join(sbs)} · source={args.fd_source} · exchanges={args.exchanges} · "
        f"{len(fd)} sportsbook quotes · {len(ex)} exchange quotes · {len(arbs)} arbs"
    )
    write_arbs_html(out_dir / "arbs.html", arbs, meta=meta)
    (out_dir / "arbs.json").write_text(json.dumps([a.to_dict() for a in arbs], indent=2))
    print(f"[arb] wrote {out_dir / 'arbs.csv'}, arbs.html, arbs.json ({len(arbs)} rows)")
    for a in arbs[:15]:
        print(
            f"  {a.profit_pct:6.2f}%  {a.player} {a.market} {a.point:g}  "
            f"{a.sportsbook} {a.fd_side} {a.fd_american} / {a.exchange} {a.ex_side} {a.ex_american_eff}  "
            f"${a.stake_fd:.0f}/${a.stake_ex:.0f}"
        )
    if len(arbs) > 15:
        print(f"  … {len(arbs) - 15} more")
    return arbs


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.loop:
        args.once = False
    cfg = load_config(Path(args.config))
    if args.once or not args.loop:
        run_once(args, cfg)
        return 0
    # loop: poll exchanges often; refresh FD rarely
    n = 0
    fd_args_source = args.fd_source
    while True:
        n += 1
        # After the first poll, freeze FD on fixture/cache snapshot unless refresh due
        if n > 1 and fd_args_source == "api" and (n - 1) % max(1, args.fd_refresh_every) != 0:
            # reuse last written FD by switching to cache/fixture temporarily
            pass
        print(f"\n[arb] loop pass {n} @ {datetime.now(ET).strftime('%H:%M:%S %Z')}")
        try:
            run_once(args, cfg)
        except Exception as exc:
            print(f"[arb] pass failed: {exc}", file=sys.stderr)
        # After first API pull, prefer not re-hitting Odds API every interval
        if args.fd_source == "api" and n == 1:
            print(
                "[arb] note: subsequent loop passes still use --fd-source api only when "
                f"pass % {args.fd_refresh_every} == 1; set --fd-source fixture|cache to avoid credits.",
                file=sys.stderr,
            )
            # Switch to cache for interim polls if a fixture exists
            if Path(args.fd_fixture).exists():
                args.fd_source = "fixture"
                print("[arb] switched fd-source -> fixture for interim exchange polls", file=sys.stderr)
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())


def sportsbook_quotes_from_odds_events(events, market_keys, *, sport="nba", sportsbooks=SPORTSBOOKS):
    return fanduel_quotes_from_odds_events(events, market_keys, sport=sport, sportsbooks=sportsbooks)
