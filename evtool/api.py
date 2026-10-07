"""Thin client for The Odds API v4 with disk caching and credit tracking.

The API key is read from the ODDS_API_KEY environment variable at request time
and is never printed, logged, cached or written to disk. Cache file names and
the usage log are derived only from the path and the non-secret parameters.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.the-odds-api.com"


class OddsAPIError(RuntimeError):
    pass


def _scrub(text: str) -> str:
    """Remove the key from any text we might surface (defence in depth)."""
    key = (os.environ.get("ODDS_API_KEY") or "").strip()
    return text.replace(key, "***") if key else text


@dataclass
class Usage:
    used: int | None = None
    remaining: int | None = None
    spent_this_run: int = 0
    calls_live: int = 0
    calls_cached: int = 0
    oldest_data: float | None = None   # epoch of the oldest response used this run
    log: list[dict] = field(default_factory=list)


class OddsAPI:
    def __init__(self, cache_dir: str | Path, cache_ttl_minutes: float = 10.0,
                 offline: bool = False, max_credits_per_run: int | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl_minutes * 60
        self.offline = offline
        self.max_credits_per_run = max_credits_per_run
        self.usage = Usage()
        self.last_fetched_at: float | None = None   # fetch time of the last response returned

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _key() -> str:
        key = (os.environ.get("ODDS_API_KEY") or "").strip()
        if not key:
            raise OddsAPIError("ODDS_API_KEY is not set in the environment")
        return key

    def _cache_path(self, path: str, params: dict) -> Path:
        ident = json.dumps({"path": path, "params": params}, sort_keys=True)
        h = hashlib.sha1(ident.encode()).hexdigest()[:16]
        slug = path.strip("/").replace("/", "_")
        return self.cache_dir / f"{slug}__{h}.json"

    def estimate_cost(self, path: str, params: dict) -> int:
        """Rough credit estimate for odds endpoints (0 for free endpoints)."""
        if not path.rstrip("/").endswith("/odds"):
            return 0 if path.rstrip("/").endswith(("/sports", "/events")) else 1
        n_markets = len([m for m in params.get("markets", "h2h").split(",") if m])
        if params.get("bookmakers"):
            n_books = len([b for b in params["bookmakers"].split(",") if b])
            n_regions = -(-n_books // 10)
        else:
            n_regions = len([r for r in params.get("regions", "us").split(",") if r])
        return n_markets * n_regions

    # ------------------------------------------------------------------- core
    def get(self, path: str, params: dict | None = None, *, use_cache: bool = True) -> Any:
        params = dict(params or {})
        cpath = self._cache_path(path, params)
        if use_cache and cpath.exists():
            age = time.time() - cpath.stat().st_mtime
            if self.offline or age <= self.cache_ttl:
                self.usage.calls_cached += 1
                blob = json.loads(cpath.read_text())
                self.last_fetched_at = blob.get("fetched_at", cpath.stat().st_mtime)
                if path.rstrip("/").endswith("/odds"):
                    self._seen(blob.get("fetched_at", cpath.stat().st_mtime))
                return blob["data"]
        if self.offline:
            raise OddsAPIError(f"offline mode and no cached response for {path} {params}")

        est = self.estimate_cost(path, params)
        if (self.max_credits_per_run is not None
                and self.usage.spent_this_run + est > self.max_credits_per_run):
            raise OddsAPIError(
                f"refusing call to {path}: would exceed per-run credit cap "
                f"({self.usage.spent_this_run}+{est} > {self.max_credits_per_run})")

        try:
            resp = requests.get(BASE_URL + path, params={**params, "apiKey": self._key()}, timeout=30)
        except requests.RequestException as e:
            # requests' messages can embed the full URL (which carries the key) - never surface them
            raise OddsAPIError(f"{path}: network error ({type(e).__name__})") from None
        hdr = resp.headers
        last = int(hdr.get("x-requests-last", 0) or 0)
        if hdr.get("x-requests-used") is not None:
            self.usage.used = int(float(hdr["x-requests-used"]))
        if hdr.get("x-requests-remaining") is not None:
            self.usage.remaining = int(float(hdr["x-requests-remaining"]))
        self.usage.spent_this_run += last
        self.usage.calls_live += 1
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": path, "params": params,
                 "status": resp.status_code, "cost": last,
                 "used": self.usage.used, "remaining": self.usage.remaining}
        self.usage.log.append(entry)
        with open(self.cache_dir / "usage_log.jsonl", "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        if resp.status_code != 200:
            # never include the request URL (it contains the key)
            raise OddsAPIError(f"{path} -> HTTP {resp.status_code}: {_scrub(resp.text[:300])}")
        data = resp.json()
        self.last_fetched_at = time.time()
        if path.rstrip("/").endswith("/odds"):
            self._seen(time.time())
        cpath.write_text(json.dumps({"fetched_at": time.time(), "path": path,
                                     "params": params, "data": data}))
        return data

    def _seen(self, ts: float) -> None:
        if self.usage.oldest_data is None or ts < self.usage.oldest_data:
            self.usage.oldest_data = ts

    def last_known_quota(self) -> dict | None:
        """Most recent x-requests-* values seen (from the usage log)."""
        log = self.cache_dir / "usage_log.jsonl"
        if not log.exists():
            return None
        lines = [l for l in log.read_text().splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None

    # ------------------------------------------------------------ endpoints
    def sports(self, fresh: bool = False) -> list[dict]:
        """Free call. fresh=True bypasses the cache so the quota headers are current."""
        return self.get("/v4/sports/", use_cache=not fresh)

    def events(self, sport_key: str) -> list[dict]:
        return self.get(f"/v4/sports/{sport_key}/events")

    def odds(self, sport_key: str, markets: list[str], *, bookmakers: list[str] | None = None,
             regions: list[str] | None = None, odds_format: str = "decimal") -> list[dict]:
        params = {"markets": ",".join(markets), "oddsFormat": odds_format, "dateFormat": "iso"}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        else:
            params["regions"] = ",".join(regions or ["us"])
        return self.get(f"/v4/sports/{sport_key}/odds", params)

    def event_odds(self, sport_key: str, event_id: str, markets: list[str], *,
                   bookmakers: list[str] | None = None, regions: list[str] | None = None,
                   odds_format: str = "decimal") -> dict:
        """Per-event odds; this is the hook for player props in a later version."""
        params = {"markets": ",".join(markets), "oddsFormat": odds_format, "dateFormat": "iso"}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        else:
            params["regions"] = ",".join(regions or ["us"])
        return self.get(f"/v4/sports/{sport_key}/events/{event_id}/odds", params)

    def event_markets(self, sport_key: str, event_id: str, regions: list[str]) -> dict:
        return self.get(f"/v4/sports/{sport_key}/events/{event_id}/markets",
                        {"regions": ",".join(regions)})
