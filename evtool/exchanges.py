"""Direct public clients for Kalshi and Polymarket NBA player props (no auth).

Kalshi series used (game-night milestone O/U):
  KXNBAPTS, KXNBAREB, KXNBAAST, KXNBA3PT, KXNBAPRA
Polymarket sportsMarketType on game events:
  points, rebounds, assists, threes
  (no PRA market type observed on 2026-10-26)

Odds-API FanDuel keys they map to:
  player_points, player_rebounds, player_assists, player_threes,
  player_points_rebounds_assists
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .names import normalize_player

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
# Legacy host still works; prefer external-api.
KALSHI_FALLBACK = "https://api.elections.kalshi.com/trade-api/v2"
POLY_GAMMA = "https://gamma-api.polymarket.com"

# Odds API market key <-> exchange identifiers
MARKET_MAP = {
    "player_points": {"kalshi": "KXNBAPTS", "poly": "points", "label": "Points"},
    "player_rebounds": {"kalshi": "KXNBAREB", "poly": "rebounds", "label": "Rebounds"},
    "player_assists": {"kalshi": "KXNBAAST", "poly": "assists", "label": "Assists"},
    "player_threes": {"kalshi": "KXNBA3PT", "poly": "threes", "label": "Threes"},
    "player_points_rebounds_assists": {
        "kalshi": "KXNBAPRA",
        "poly": None,  # not listed on Polymarket sports game events (checked 2026-10-26)
        "label": "PRA",
    },
}

POLY_NBA_TAG = 745  # primaryTagId for sport=nba


@dataclass
class PropQuote:
    """One side of a player prop at one book/exchange."""
    book: str                 # fanduel | kalshi | polymarket
    market_key: str           # Odds API style key
    player: str
    player_key: str           # normalized
    side: str                 # Over | Under
    point: float
    decimal: float            # raw (pre-fee for exchanges)
    american: str | None = None
    event: str = ""           # human game label if known
    event_key: str = ""       # normalized matchup hint
    commence_utc: str = ""
    liquidity: float | None = None
    source_id: str = ""       # ticker / condition id
    fetched_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _get_json(url: str, timeout: float = 45.0) -> Any:
    req = Request(url, headers={"User-Agent": "ev-tool-arb/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dollars_to_decimal(p: float) -> float | None:
    """Contract price in dollars (0,1) -> decimal odds. None if untradeable."""
    if p is None:
        return None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if p <= 0.0 or p >= 1.0:
        return None
    return 1.0 / p


# ------------------------------------------------------------------ Kalshi
def _kalshi_get(path: str, params: dict | None = None) -> Any:
    q = f"?{urlencode(params)}" if params else ""
    try:
        return _get_json(f"{KALSHI_BASE}{path}{q}")
    except Exception:
        return _get_json(f"{KALSHI_FALLBACK}{path}{q}")


def _parse_kalshi_player(title: str, yes_sub: str) -> str:
    # "Ajay Mitchell: 30+ points" or yes_sub "Ajay Mitchell: 30+"
    src = title or yes_sub or ""
    m = re.match(r"^([^:]+):", src)
    return (m.group(1) if m else src).strip()


def _event_key_from_kalshi_ticker(event_ticker: str) -> str:
    # KXNBAPTS-26OCT26PHXOKC -> phxokc
    parts = (event_ticker or "").split("-")
    return parts[-1].lower() if parts else ""


def fetch_kalshi_nba_props(
    market_keys: list[str] | None = None,
    *,
    limit_per_series: int = 200,
) -> list[PropQuote]:
    """Pull open Kalshi NBA player-prop markets as Over/Under quotes."""
    keys = market_keys or list(MARKET_MAP.keys())
    series_wanted = {
        MARKET_MAP[k]["kalshi"]: k for k in keys if MARKET_MAP.get(k, {}).get("kalshi")
    }
    out: list[PropQuote] = []
    fetched = _now_iso()
    for series, market_key in series_wanted.items():
        cursor = None
        got = 0
        while got < limit_per_series:
            params: dict[str, Any] = {
                "series_ticker": series,
                "status": "open",
                "limit": min(200, limit_per_series - got),
            }
            if cursor:
                params["cursor"] = cursor
            data = _kalshi_get("/markets", params)
            markets = data.get("markets") or []
            if not markets:
                break
            for m in markets:
                point = m.get("floor_strike")
                if point is None:
                    continue
                try:
                    point = float(point)
                except (TypeError, ValueError):
                    continue
                player = _parse_kalshi_player(m.get("title") or "", m.get("yes_sub_title") or "")
                if not player:
                    continue
                yes_ask = m.get("yes_ask_dollars")
                no_ask = m.get("no_ask_dollars")
                yes_sz = _float_or_none(m.get("yes_ask_size_fp"))
                no_sz = _float_or_none(m.get("no_ask_size_fp"))
                event_ticker = m.get("event_ticker") or ""
                event_key = _event_key_from_kalshi_ticker(event_ticker)
                for side, ask, liq in (
                    ("Over", yes_ask, yes_sz),
                    ("Under", no_ask, no_sz),
                ):
                    dec = dollars_to_decimal(ask)
                    if dec is None:
                        continue
                    out.append(PropQuote(
                        book="kalshi",
                        market_key=market_key,
                        player=player,
                        player_key=normalize_player(player),
                        side=side,
                        point=point,
                        decimal=dec,
                        event=event_ticker,
                        event_key=event_key,
                        commence_utc=m.get("close_time") or "",
                        liquidity=liq,
                        source_id=m.get("ticker") or "",
                        fetched_at=fetched,
                    ))
            got += len(markets)
            cursor = data.get("cursor")
            if not cursor:
                break
    return out


def _float_or_none(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# -------------------------------------------------------------- Polymarket
_PLAYER_LINE_RE = re.compile(
    r"^(.+?):\s*(Points|Rebounds|Assists|Threes|3-Pointers|Three.?Pointers)"
    r".*?O/?U\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)


def _poly_market_key(sports_type: str) -> str | None:
    for k, spec in MARKET_MAP.items():
        if spec.get("poly") == sports_type:
            return k
    return None


def fetch_polymarket_nba_events(limit: int = 30) -> list[dict]:
    """Upcoming/open NBA game events (with nested markets)."""
    params = urlencode({
        "tag_id": POLY_NBA_TAG,
        "closed": "false",
        "limit": limit,
        "order": "startTime",
        "ascending": "true",
    })
    data = _get_json(f"{POLY_GAMMA}/events?{params}")
    # Prefer real game events (hundreds of markets), skip exact-margin satellites.
    games = []
    for e in data or []:
        slug = e.get("slug") or ""
        if "exact-margin" in slug or "1h-" in slug:
            continue
        mkts = e.get("markets") or []
        if len(mkts) < 20:
            continue
        games.append(e)
    return games


def fetch_polymarket_nba_props(
    market_keys: list[str] | None = None,
    *,
    max_events: int = 12,
) -> list[PropQuote]:
    keys = set(market_keys or [k for k, v in MARKET_MAP.items() if v.get("poly")])
    poly_types = {MARKET_MAP[k]["poly"] for k in keys if MARKET_MAP.get(k, {}).get("poly")}
    out: list[PropQuote] = []
    fetched = _now_iso()
    for e in fetch_polymarket_nba_events(limit=max(30, max_events * 2))[:max_events]:
        title = e.get("title") or e.get("slug") or ""
        commence = e.get("startTime") or e.get("startDate") or ""
        event_key = (e.get("slug") or "").lower()
        for m in e.get("markets") or []:
            smt = m.get("sportsMarketType")
            if smt not in poly_types:
                continue
            market_key = _poly_market_key(smt)
            if not market_key or market_key not in keys:
                continue
            question = m.get("question") or m.get("groupItemTitle") or ""
            point = m.get("line")
            player = None
            if point is None:
                mm = _PLAYER_LINE_RE.match(question)
                if mm:
                    player = mm.group(1).strip()
                    point = float(mm.group(3))
            else:
                try:
                    point = float(point)
                except (TypeError, ValueError):
                    continue
                mm = _PLAYER_LINE_RE.match(question)
                player = mm.group(1).strip() if mm else question.split(":")[0].strip()
            if player is None or point is None:
                continue
            # Taker quotes: Gamma bestAsk/bestBid are for the Yes (Over) token.
            # outcomePrices are mids and invent phantom arbs when the book is wide.
            yes_ask = _float_or_none(m.get("bestAsk"))
            yes_bid = _float_or_none(m.get("bestBid"))
            if (
                yes_ask is None
                or yes_bid is None
                or not (0.0 < yes_bid < yes_ask < 1.0)
            ):
                continue
            # Buy Over at Yes ask; buy Under at No ask ≈ 1 - Yes bid on a binary book.
            side_prices = {"Over": yes_ask, "Under": 1.0 - yes_bid}
            liq = _float_or_none(m.get("liquidityNum") or m.get("liquidity"))
            for side, price in side_prices.items():
                dec = dollars_to_decimal(price)
                if dec is None:
                    continue
                out.append(PropQuote(
                    book="polymarket",
                    market_key=market_key,
                    player=player,
                    player_key=normalize_player(player),
                    side=side,
                    point=point,
                    decimal=dec,
                    event=title,
                    event_key=event_key,
                    commence_utc=str(commence),
                    liquidity=liq,
                    source_id=str(m.get("conditionId") or m.get("id") or ""),
                    fetched_at=fetched,
                ))
    return out
