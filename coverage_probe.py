"""One-off probe: which bookmakers does The Odds API return per region?

Costs ~5 credits: NFL h2h across us,us2,us_ex,eu (4) + one NFL event's market
list for us_dfs (1). Results are cached under cache/ so re-runs are free.
"""
import json
from evtool.api import OddsAPI

api = OddsAPI("cache", cache_ttl_minutes=24 * 60, max_credits_per_run=8)
out = {}
# one combined h2h call; bookmaker->region attribution uses the official docs table
data = api.odds("americanfootball_nfl", ["h2h"], regions=["us", "us2", "us_ex", "eu"])
books = {}
for ev in data:
    for b in ev["bookmakers"]:
        books.setdefault(b["key"], {"title": b["title"], "events": 0})
        books[b["key"]]["events"] += 1
out["nfl_h2h_books"] = books
out["nfl_events"] = len(data)
# DFS: market list for the first upcoming NFL event
evs = api.events("americanfootball_nfl")
ev = sorted(evs, key=lambda e: e["commence_time"])[0]
mk = api.event_markets("americanfootball_nfl", ev["id"], ["us_dfs"])
out["dfs_event"] = f'{ev["away_team"]} @ {ev["home_team"]}'
out["dfs_markets"] = {b["key"]: sorted(m["key"] for m in b["markets"]) for b in mk.get("bookmakers", [])}
json.dump(out, open("output/coverage_probe.json", "w"), indent=1)
print(json.dumps(out, indent=1))
print("credits spent:", api.usage.spent_this_run, "used:", api.usage.used, "remaining:", api.usage.remaining)
