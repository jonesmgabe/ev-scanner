#!/usr/bin/env python3
"""Scheduled scan + static site build (GitHub Actions -> GitHub Pages).

  python3 site_run.py gate --event schedule --schedule "20 14 * * *"   # is this cron a real ET slot?
  python3 site_run.py build --out site --prev-url https://<user>.github.io/<repo>/
  python3 site_run.py plan                                             # budget + plan only (0 credits)
  python3 site_run.py crons                                            # cron lines matching [site].slots_et

Each build:
 1. reads the quota from the free /sports call (x-requests-used / -remaining),
 2. computes this run's credit cap (budget guard, see [budget] in config.toml),
 3. plans for free with /events: sports with games in the next window_hours,
    stalest first, main markets + props for the soonest game(s) within the cap,
 4. scans, merges with still-valid bets from the previous published run
    (sports not refreshed now; labelled with their age) and writes site/.
If the guard says skip (or the API is unreachable) it republishes the previous
report with a banner explaining why. The API key is only read from the
environment by evtool.api and never printed.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import ev
from evtool.api import OddsAPI, OddsAPIError
from evtool.report import write_report
from evtool.scanner import reference_settings, scan_events

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_et(dt: datetime, with_day: bool = True) -> str:
    d = dt.astimezone(ET)
    return d.strftime("%a %b %-d, %-I:%M %p ET" if with_day else "%-I:%M %p ET")


def fmt_age(td: timedelta) -> str:
    m = max(0, int(td.total_seconds() // 60))
    return f"{m} min" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def slot_times(cfg) -> list[time]:
    return [time(*map(int, s.split(":"))) for s in cfg["site"]["slots_et"]]


# --------------------------------------------------------------------------- gate / crons
def cron_lines(cfg) -> list[str]:
    """UTC cron lines for every ET slot under both EDT (UTC-4) and EST (UTC-5)."""
    out = []
    for t in slot_times(cfg):
        for off in (4, 5):
            h = (t.hour + off) % 24
            out.append(f'- cron: "{t.minute} {h} * * *"   # {t.strftime("%H:%M")} ET when UTC-{off}')
    return out


def gate(cfg, event: str, schedule: str, now: datetime | None = None) -> tuple[bool, str]:
    if event != "schedule":
        return True, f"{event}: always runs"
    now = now or datetime.now(UTC)
    mm, hh = schedule.split()[:2]
    trig = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if trig > now:                       # scheduled runs can start late, even past midnight UTC
        trig -= timedelta(days=1)
    et = trig.astimezone(ET).strftime("%H:%M")
    ok = et in cfg["site"]["slots_et"]
    return ok, f"cron '{schedule}' = {et} ET -> " + ("scheduled slot, running" if ok else "other DST variant, skipping")


# --------------------------------------------------------------------------- budget
def next_reset(cfg, now: datetime) -> datetime:
    day = int(cfg["budget"].get("quota_reset_day", 1))
    n = now.astimezone(UTC)
    cand = datetime(n.year, n.month, min(day, 28), tzinfo=UTC)
    if cand <= n:
        y, m = (n.year + 1, 1) if n.month == 12 else (n.year, n.month + 1)
        cand = datetime(y, m, min(day, 28), tzinfo=UTC)
    return cand


def scheduled_runs_left(cfg, now: datetime, include_current: bool) -> int:
    """Scheduled slots from now until the quota resets (current slot counts if within 45 min)."""
    reset = next_reset(cfg, now)
    d: date = now.astimezone(ET).date()
    n = 0
    grace = timedelta(minutes=45) if include_current else timedelta(0)
    while datetime.combine(d, time(0), ET) < reset:
        for t in slot_times(cfg):
            dt = datetime.combine(d, t, ET)
            if now - grace <= dt < reset:
                n += 1
        d += timedelta(days=1)
    return n


def budget_cap(cfg, used, remaining, now, runs_log, manual_cap=None) -> dict:
    b = cfg["budget"]
    out = {"used": used, "remaining": remaining, "notes": [], "skip": False}
    if remaining is None:
        out.update(cap=0, skip=True, reason="could not read the remaining credits from the API")
        return out
    is_manual = manual_cap is not None
    runs_left = scheduled_runs_left(cfg, now, include_current=not is_manual) + (1 if is_manual else 0)
    usable = remaining - b["reserve_credits"]
    fair = usable / max(runs_left, 1)
    today = now.astimezone(ET).date()
    spent_today = sum(r.get("credits", 0) for r in runs_log
                      if parse_iso(r["ts"]).astimezone(ET).date() == today)
    day_left = b["max_credits_per_day"] - spent_today
    if is_manual:
        cap = max(0, min(manual_cap, usable))
        if cap < manual_cap:
            out["notes"].append(f"manual cap {manual_cap} cut to {cap} to keep the {b['reserve_credits']}-credit reserve")
    else:
        cap = max(0, min(b["max_credits_per_run"], math.floor(fair), day_left))
        if math.floor(fair) < b["max_credits_per_run"]:
            out["notes"].append(f"budget guard: shrunk to {cap} credits (fair share {fair:.1f}/run for "
                                f"{runs_left} runs left before the reset)")
        if day_left < b["max_credits_per_run"]:
            out["notes"].append(f"daily cap: {spent_today} of {b['max_credits_per_day']} credits already used today")
    total = (used or 0) + remaining
    projected = (used or 0) + cap + b["max_credits_per_run"] * max(runs_left - 1, 0)
    out.update(cap=cap, runs_left=runs_left, fair_share=round(fair, 2), spent_today=spent_today,
               quota_total=total, projected_month_end=min(projected, total),
               projected_if_full=projected, reset=next_reset(cfg, now).isoformat())
    if cap < b["min_run_credits"]:
        out.update(skip=True, reason=("monthly budget nearly used" if fair < b["min_run_credits"]
                                      else "daily credit cap reached"))
    return out


# --------------------------------------------------------------------------- plan
def apply_manual_scope(cfg, sports: str | None, props_sports: str | None, max_prop_events: int | None,
                        prop_markets: str | None = None) -> None:
    """A manual run can pin the sports and the props window. Blank = config defaults."""
    site = cfg["site"]
    if sports:
        site["sports"] = [x.strip() for x in sports.split(",") if x.strip()]
    if props_sports is not None and props_sports.strip() != "":
        site["props_sports"] = [] if props_sports.strip().lower() == "none" else [
            x.strip() for x in props_sports.split(",") if x.strip()]
    if max_prop_events is not None:
        site["max_prop_events_per_run"] = max_prop_events
    if prop_markets:
        mk = [x.strip() for x in prop_markets.split(",") if x.strip()]
        for sport in site.get("props_sports") or []:
            site.setdefault("prop_markets", {})[sport] = mk


def make_plan(cfg, api: OddsAPI, active: set[str], cap: int, state: dict, now: datetime) -> dict:
    site = cfg["site"]
    keymap = cfg["sport_keys"]
    last = state.get("last_scanned", {})
    end = now + timedelta(hours=site["window_hours"])
    cands, notes = [], []
    for prio, s in enumerate(site["sports"]):
        sk = keymap.get(s, s)
        if sk not in active:
            continue
        try:
            evs = api.events(sk)  # free
        except OddsAPIError as e:
            notes.append(f"{s} events: {e}")
            continue
        inwin = sorted((e for e in evs if now < parse_iso(e["commence_time"]) <= end),
                       key=lambda e: e["commence_time"])
        if not inwin:
            continue
        ls = last.get(f"{s}:main")
        hrs = (now - parse_iso(ls)).total_seconds() / 3600 if ls else 1000.0
        cands.append({"sport": s, "key": sk, "events": inwin, "hours_since": round(hrs, 1),
                      "score": hrs * site.get("sport_weight", {}).get(s, 0.5), "prio": prio})
    cands.sort(key=lambda c: (-c["score"], c["prio"]))

    prop_cands = []
    for c in cands:
        mk = site.get("prop_markets", {}).get(c["sport"], [])
        if c["sport"] not in site.get("props_sports", []) or not mk:
            continue
        for e in c["events"]:
            lp = last.get(f"{c['sport']}:props:{e['id']}")
            recent = bool(lp) and (now - parse_iso(lp)) < timedelta(hours=site.get("prop_refresh_hours", 6))
            prop_cands.append({"sport": c["sport"], "key": c["key"], "event": e, "markets": mk, "recent": recent})
    prop_cands.sort(key=lambda p: (p["recent"], p["event"]["commence_time"]))

    mk_main = list(site["main_markets"])
    cost_main = len(mk_main)                    # x1 region: <=10 bookmakers per call
    pres = site.get("props_reserve_credits", 0)
    reserve = pres if prop_cands and cap >= cost_main + pres else 0   # main markets first on small caps
    left, mains, props = cap, [], []
    for c in cands:
        if len(mains) < site.get("max_main_sports_per_run", 6) and cost_main <= left - reserve:
            mains.append({**c, "markets": mk_main}); left -= cost_main
    for p in prop_cands:
        if len(props) >= site.get("max_prop_events_per_run", 1):
            break
        if len(p["markets"]) <= left:
            props.append(p); left -= len(p["markets"])
    for c in cands:                              # spare credits -> more sports
        if c["sport"] not in {m["sport"] for m in mains} and cost_main <= left \
                and len(mains) < site.get("max_main_sports_per_run", 6):
            mains.append({**c, "markets": mk_main}); left -= cost_main
    if not mains and not props and cands and left >= 1:   # shrink: fewer markets (h2h first)
        mains.append({**cands[0], "markets": mk_main[:left]})
        notes.append(f"shrunk {cands[0]['sport']} to {','.join(mk_main[:left])} to fit {left} credit(s)")
        left -= len(mk_main[:left])
    return {"candidates": [{k: c[k] for k in ("sport", "hours_since", "score")} | {"games_in_window": len(c["events"])}
                           for c in cands],
            "mains": mains, "props": props, "estimated": cap - left, "notes": notes}


# --------------------------------------------------------------------------- build
def load_prev(prev_url: str | None, prev_file: str | None) -> dict:
    try:
        if prev_file:
            return json.loads(Path(prev_file).read_text())
        if prev_url:
            r = requests.get(prev_url.rstrip("/") + "/site_data.json", timeout=20)
            if r.status_code == 200:
                return r.json()
    except Exception as e:  # first run / site not up yet
        print(f"[info] no previous site data ({type(e).__name__})")
    return {}


def next_slot(cfg, now: datetime) -> datetime:
    d = now.astimezone(ET).date()
    for i in range(3):
        for t in slot_times(cfg):
            dt = datetime.combine(d + timedelta(days=i), t, ET)
            if dt > now:
                return dt
    return now


def logic_fingerprint(cfg) -> str:
    """Changes whenever the pricing logic's settings change (references, devig, caps)."""
    import hashlib
    blob = json.dumps({"sharp": cfg["sharp"], "devig": cfg["scan"].get("devig_method"),
                       "min_ev": cfg["scan"].get("min_ev_pct")}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def build(a, cfg) -> int:
    now = datetime.now(UTC)
    site = cfg["site"]
    apply_manual_scope(cfg, getattr(a, "sports", None), getattr(a, "props_sports", None),
                       getattr(a, "max_prop_events", None), getattr(a, "prop_markets", None))
    cfg["scan"]["min_ev_pct"] = site.get("min_ev_pct", cfg["scan"]["min_ev_pct"])
    prev = load_prev(a.prev_url, a.prev_file)
    logic = logic_fingerprint(cfg)
    logic_changed = bool(prev) and prev.get("logic") != logic
    state = prev.get("state", {"last_scanned": {}})
    if logic_changed:   # bets priced under old settings must not be carried; rescan stalest-first from scratch
        state = {"last_scanned": {}}
    runs_log = prev.get("runs", [])
    api = OddsAPI(ROOT / cfg["api"].get("cache_dir", "cache"), cache_ttl_minutes=10, offline=a.offline,
                  max_credits_per_run=0)   # raised below once the budget guard has decided
    banner, notes, errors = [], [], []

    # 1. quota (free)
    sports_list, used, remaining = [], None, None
    try:
        sports_list = api.sports(fresh=not a.offline)
        used, remaining = api.usage.used, api.usage.remaining
        if remaining is None:
            q = api.last_known_quota() or {}
            used, remaining = q.get("used"), q.get("remaining")
    except OddsAPIError as e:
        if not a.offline:
            errors.append(str(e))
    if a.offline and not sports_list:      # testing from cache: assume configured sports, last known quota
        sports_list = [{"key": cfg["sport_keys"].get(s, s)} for s in site["sports"]]
        q = api.last_known_quota() or {}
        used, remaining = q.get("used"), q.get("remaining")
    active = {s["key"] for s in sports_list if s.get("active", True)}

    # 2. budget guard
    bud = budget_cap(cfg, used, remaining, now, runs_log, manual_cap=a.max_credits)
    notes += bud["notes"]
    plan = {"mains": [], "props": [], "candidates": [], "estimated": 0, "notes": []}
    if not bud["skip"]:
        plan = make_plan(cfg, api, active, bud["cap"], state, now)
        notes += plan["notes"]
        if not plan["mains"] and not plan["props"]:
            bud.update(skip=True, reason=f"no games in the next {site['window_hours']} hours" if not plan["candidates"]
                       else f"nothing fits in {bud['cap']} credits")
    print(json.dumps({"budget": {k: v for k, v in bud.items()},
                      "plan": {"mains": [(m["sport"], m["markets"]) for m in plan["mains"]],
                               "props": [(p["sport"], p["event"]["away_team"] + " @ " + p["event"]["home_team"], p["markets"])
                                         for p in plan["props"]],
                               "estimated_credits": plan["estimated"], "candidates": plan["candidates"]}}, indent=1))
    if a.plan_only:
        return 0

    # 3. scan
    new_rows, refreshed, done = [], set(), []
    if not bud["skip"]:
        api.max_credits_per_run = bud["cap"]
        main_books, _ = ev.bookmaker_list(cfg, props=False)
        prop_books, _ = ev.bookmaker_list(cfg, props=True)
        fmt = cfg["api"].get("odds_format", "decimal")
        for m in plan["mains"]:
            try:
                data = api.odds(m["key"], m["markets"], bookmakers=main_books, odds_format=fmt)
            except OddsAPIError as e:
                errors.append(f"{m['sport']} main: {e}"); continue
            ts = datetime.fromtimestamp(api.last_fetched_at, UTC)
            scope = f"{m['sport']}:main"
            for b in scan_events(data, m["sport"].upper(), cfg, now=now, markets=m["markets"],
                                 age_now=ts if a.offline else None):
                new_rows.append(b.to_dict() | {"scope": scope, "scanned_at": ts.isoformat(), "carried": False})
            refreshed.add(scope); done.append(f"{m['sport'].upper()} main markets ({len(data)} games)")
        for p in plan["props"]:
            e = p["event"]
            try:
                d = api.event_odds(p["key"], e["id"], p["markets"], bookmakers=prop_books, odds_format=fmt)
            except OddsAPIError as ex:
                errors.append(f"{p['sport']} props: {ex}"); continue
            ts = datetime.fromtimestamp(api.last_fetched_at, UTC)
            scope = f"{p['sport']}:props:{e['id']}"
            for b in scan_events([d], p["sport"].upper(), cfg, now=now, markets=p["markets"], props=True,
                                 age_now=ts if a.offline else None):
                new_rows.append(b.to_dict() | {"scope": scope, "scanned_at": ts.isoformat(), "carried": False})
            refreshed.add(scope)
            done.append(f"{p['sport'].upper()} props: {e['away_team']} @ {e['home_team']} ({', '.join(p['markets'])})")
    spent = api.usage.spent_this_run
    if api.usage.remaining is not None and api.usage.calls_live > 1:
        used, remaining = api.usage.used, api.usage.remaining
    if bud["skip"]:
        banner.append(f"No new odds this run: {bud.get('reason', 'skipped')}. Showing the last good scan.")
    elif not refreshed:
        banner.append("This run could not pull any odds (API error). Showing the last good scan.")
    if logic_changed:
        notes.append("Fair-odds settings changed since the last run: earlier bets were dropped, not carried over.")
    if errors:
        notes.append(f"{len(errors)} API warning(s): " + "; ".join(e[:140] for e in errors[:3]))

    # 4. merge with the previous run
    carry_h = site.get("carry_over_hours", 24)
    carried = []
    for r in ([] if logic_changed else prev.get("bets", [])):
        try:
            if r.get("scope") in refreshed or parse_iso(r["commence_utc"]) <= now:
                continue
            if now - parse_iso(r["scanned_at"]) > timedelta(hours=carry_h):
                continue
        except (KeyError, ValueError):
            continue
        carried.append(r | {"carried": True})
    rows = sorted(new_rows + carried, key=lambda r: r["ev_pct"], reverse=True)

    for s in refreshed:
        state.setdefault("last_scanned", {})[s] = now.isoformat()
    # forget props scopes for games that have started
    state["last_scanned"] = {k: v for k, v in state.get("last_scanned", {}).items()
                             if ":props:" not in k or now - parse_iso(v) < timedelta(days=3)}
    run_entry = {"ts": now.isoformat(), "event": a.event, "credits": spent, "cap": bud.get("cap"),
                 "skipped": bool(bud["skip"]), "scopes": sorted(refreshed), "new_bets": len(new_rows),
                 "carried_bets": len(carried), "used": used, "remaining": remaining}
    runs_log = (runs_log + [run_entry])[-90:]

    # 5. status + page
    good_ts = [parse_iso(r["scanned_at"]) for r in rows]
    last_good = prev.get("last_good_utc")
    if refreshed:
        last_good = now.isoformat()
    upd = f'<span class="upd">Last updated: {html.escape(fmt_et(now))}</span>'
    lines = []
    if done:
        lines.append("Odds pulled this run (" + fmt_et(now, False) + "): " + html.escape("; ".join(done)) + ".")
    if carried:
        by_scope = {}
        for r in carried:
            k = r["sport"] + (" props" if r.get("is_prop") else " main")
            by_scope[k] = min(by_scope.get(k, now), parse_iso(r["scanned_at"]))
        lines.append("Carried over from earlier runs (each row shows its odds age): " + html.escape(", ".join(
            f"{k} ({fmt_et(t, False) if t.date() == now.date() else fmt_et(t)}, {fmt_age(now - t)} old)"
            for k, t in sorted(by_scope.items()))) + ".")
    if good_ts:
        lines.append(f"Oldest odds on this page are {fmt_age(now - min(good_ts))} old; odds move quickly, so re-check "
                     "the price in the app before betting.")
    elif last_good:
        lines.append(f"Last successful scan: {html.escape(fmt_et(parse_iso(last_good)))}.")
    q = (f"Credits: {spent} this run (cap {bud.get('cap', 0)})"
         + (f" · {remaining} left until the quota resets" if remaining is not None else "")
         + f" · budget ≤{cfg['budget']['max_credits_per_day']}/day · next scheduled update {fmt_et(next_slot(cfg, now))}")
    status_html = upd + "".join(f'<div class="note">{l}</div>' for l in lines)
    if banner:
        status_html += "".join(f'<div class="banner">{html.escape(b)}</div>' for b in banner)
    details = [q] + notes
    status_html += ('<details class="note"><summary>Run details</summary>'
                    + "".join(f"<div>{html.escape(d)}</div>" for d in details) + "</details>")
    meta = (f"Fair-odds reference · main: {ev.reference_chain(cfg, False)} · props: {ev.reference_chain(cfg, True)} · devig: {cfg['scan']['devig_method']} · "
            f"min EV {cfg['scan']['min_ev_pct']}% · {len(rows)} bets ({len(new_rows)} new, {len(carried)} carried over) · "
            f"schedule: {', '.join(site['slots_et'])} ET")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    write_report(out / "index.html", rows, meta, ev.report_footer(cfg), cfg["scan"]["min_ev_pct"],
                 status_html=status_html,
                 status={"generated_utc": now.isoformat(), "next_update_et": fmt_et(next_slot(cfg, now))})
    doc = (out / "index.html").read_text().replace(
        '<meta charset="utf-8">', '<meta charset="utf-8">\n<meta name="robots" content="noindex,nofollow">', 1)
    (out / "index.html").write_text(doc)
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    (out / "site_data.json").write_text(json.dumps({
        "generated_utc": now.isoformat(), "last_good_utc": last_good, "bets": rows, "logic": logic,
        "state": state, "runs": runs_log}, separators=(",", ":")))
    summary = {"generated_et": fmt_et(now), "credits_this_run": spent, "cap": bud.get("cap"),
               "quota_used": used, "quota_remaining": remaining, "skipped": bool(bud["skip"]),
               "reason": bud.get("reason"), "refreshed": done, "new_bets": len(new_rows),
               "carried_bets": len(carried), "total_bets": len(rows), "notes": notes, "errors": errors,
               "budget": bud}
    (out / "run_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ("generated_et", "credits_this_run", "cap", "quota_used",
                                               "quota_remaining", "skipped", "reason", "refreshed",
                                               "new_bets", "carried_bets", "total_bets", "errors")}, indent=1))
    gh_sum = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_sum:
        with open(gh_sum, "a") as fh:
            fh.write(f"### +EV scan {fmt_et(now)}\n\n"
                     f"- credits this run: **{spent}** (cap {bud.get('cap')}), remaining: **{remaining}**\n"
                     f"- refreshed: {'; '.join(done) or 'nothing'}\n"
                     f"- bets: {len(rows)} ({len(new_rows)} new, {len(carried)} carried over)\n"
                     + (f"- skipped: {bud.get('reason')}\n" if bud["skip"] else "")
                     + "".join(f"- note: {n}\n" for n in notes))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["gate", "build", "plan", "crons"])
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--event", default="workflow_dispatch", help="github.event_name")
    ap.add_argument("--schedule", default="", help="github.event.schedule (cron string)")
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--prev-url", help="published site base URL (to carry over bets and state)")
    ap.add_argument("--prev-file", help="local site_data.json instead of --prev-url")
    ap.add_argument("--max-credits", type=int, help="manual run: override the per-run cap (reserve still kept)")
    ap.add_argument("--sports", default="", help="manual: only these sports (comma list)")
    ap.add_argument("--props-sports", default="", help="manual: props sports, or 'none'")
    ap.add_argument("--max-prop-events", default="", help="manual: soonest games to pull props for")
    ap.add_argument("--prop-markets", default="", help="manual: override prop market keys")
    ap.add_argument("--offline", action="store_true", help="cached data only (testing)")
    a = ap.parse_args(argv)
    if a.max_credits is not None and a.max_credits <= 0:
        a.max_credits = None
    a.sports = (a.sports or "").strip()
    a.props_sports = (a.props_sports or "").strip()
    a.max_prop_events = int(a.max_prop_events) if str(a.max_prop_events or "").strip() else None
    a.prop_markets = (a.prop_markets or "").strip()
    cfg = ev.load_config(Path(a.config))
    if a.cmd == "crons":
        print("\n".join(cron_lines(cfg)))
        return 0
    if a.cmd == "gate":
        ok, why = gate(cfg, a.event, a.schedule)
        print(why)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"run={'true' if ok else 'false'}\n")
        return 0
    a.plan_only = a.cmd == "plan"
    return build(a, cfg)


if __name__ == "__main__":
    sys.exit(main())
