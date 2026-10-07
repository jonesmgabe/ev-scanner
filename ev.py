#!/usr/bin/env python3
"""+EV sports betting scanner (CLI).

Examples:
  python ev.py                               # all in-season sports, main markets
  python ev.py --sports nfl,nba --min-ev 1
  python ev.py --sports nfl --props --max-events 3
  python ev.py --sports nfl --props --prop-markets player_pass_yds,player_receptions
  python ev.py --offline                     # re-use cached responses only (0 credits)
  python ev.py --dry-run --props             # print planned calls + credit estimate
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from evtool.api import OddsAPI, OddsAPIError
from evtool.report import write_report
from evtool.scanner import compare_references, ref_config, reference_settings, scan_events

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")


def load_config(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def bookmaker_list(cfg: dict, props: bool) -> tuple[list[str], list[str]]:
    """References (priority order) + covered targets (+DFS for props) + consensus
    fillers, capped so the call bills as a single region (10 bookmakers).
    Returns (books, dropped) where `dropped` lists refs/targets cut by the cap."""
    cap = cfg["api"].get("max_bookmakers_per_call", 10)
    skip_paid = cfg["api"].get("skip_paid_only_books", True)
    skip = set(cfg.get("props", {}).get("skip_books", [])) if props else set()
    refs, _, never = reference_settings(cfg, props)   # per call type; unavailable books left out
    wanted = list(refs)
    for b in cfg["target_books"]:
        k = b.get("key")
        if not b.get("covered") or not k or k in skip or k in never or k in wanted:
            continue
        if b.get("paid_only") and skip_paid:
            continue
        if b.get("props_only") and not props:
            continue
        wanted.append(k)
    books, dropped = wanted[:cap], wanted[cap:]
    fillers = cfg.get("props", {}).get("consensus_books", []) if props else []
    fillers = fillers + cfg.get("consensus", {}).get("books", [])
    for k in fillers:
        if len(books) >= cap:
            break
        if k not in books and k not in never:
            books.append(k)
    return books, dropped


def reference_chain(cfg: dict, props: bool) -> str:
    """Human summary, e.g. 'blend pinnacle 50 / bookmaker 30 (n/a) / circasports 20 (n/a), min 1 → novig → consensus'."""
    rc = ref_config(cfg, props)
    parts = [f"{r} {w * 100:.0f}" + (" (n/a)" if r in rc["unavailable"] else "")
             for r, w in zip(rc["refs"], rc["weights"])]
    head = ("blend " if rc["mode"] == "blend" else "first of ") + " / ".join(parts) + f", min {rc['min_refs']}"
    tail = "".join(f" → {r}" for r in rc["fallback"]) + (" → consensus" if rc["fallback_mode"] == "consensus" else "")
    return head + tail


def report_footer(cfg: dict) -> str:
    """Explanatory footer for the HTML report (shared by ev.py and site_run.py)."""
    from evtool import dfs as _dfs
    import html as _html
    be_parts = []
    for b in cfg["target_books"]:
        if b.get("props_only") and b.get("key") in cfg.get("dfs", {}):
            app = cfg["dfs"][b["key"]]
            label, be = _dfs.best_entry(app)
            be_parts.append(f"<b>{_html.escape(b['name'])}</b>: {label} → {be:.2%} "
                            f"<span title=\"{_html.escape(app.get('source', ''))}\">[source]</span>")
    return ("EV% = fair_prob × decimal_odds − 1 using de-vigged Pinnacle (or leave-one-out consensus) prices on the exact same line. "
            "¼ Kelly is a fraction of bankroll. DFS pick'em rows compare the fair probability with the per-leg break-even (BE) "
            "of the app's best standard entry and show per-leg edge (fair/BE − 1); Kelly is n/a. "
            "Reference = which fair-odds source was used: one book, blend(a+b) = priority-weighted average of each book's de-vigged line "
            "(main: Pinnacle, Bookmaker, Circa; props: FanDuel, DraftKings, Circa; Novig = last-resort reference, never a bet), or consensus(n). "
            "The book being priced is always left out of its own fair price. "
            "Kalshi and Polymarket prices include taker fees. Fliff and Courtside are sweepstakes/social books (EV is in sweeps coins). "
            "Tap a row to compare every book's price. Odds move fast - always re-check the price in the app before betting."
            + ("<br>Break-evens used (verify payouts in each app; hover [source]): " + " · ".join(be_parts) if be_parts else ""))


def fmt_table(rows: list[dict]) -> str:
    cols = [("event", "Event", 34), ("commence_et", "Start (ET)", 22), ("market", "Market", 12),
            ("selection", "Selection", 34), ("book", "Book", 16), ("book_american", "Book", 9),
            ("fair_american", "Fair", 7), ("ev_pct", "EV%", 7), ("kelly_pct", "¼Kelly%", 8),
            ("fair_source", "Reference", 13)]
    def cell(r, k, w):
        v = r[k]
        if k == "ev_pct":
            v = f"{v:.2f}"
        elif k == "kelly_pct":
            v = "—" if v is None else f"{v:.2f}"
        v = str(v)
        return (v[: w - 1] + "…") if len(v) > w else v
    out = ["  ".join(h.ljust(w) if k not in ("ev_pct", "kelly_pct", "book_american", "fair_american")
                     else h.rjust(w) for k, h, w in cols)]
    out.append("-" * len(out[0]))
    for r in rows:
        out.append("  ".join(cell(r, k, w).rjust(w) if k in ("ev_pct", "kelly_pct", "book_american", "fair_american")
                             else cell(r, k, w).ljust(w) for k, h, w in cols))
    return "\n".join(out)


def _age_now(api, offline: bool, now: datetime) -> datetime:
    """Offline re-scans judge sharp-price staleness as of when the data was
    fetched, so re-running later doesn't silently drop every reference."""
    if offline and api.last_fetched_at:
        return datetime.fromtimestamp(api.last_fetched_at, timezone.utc)
    return now


ROLE_LABEL = {"reference": "reference, not bettable", "reference+target": "reference & target",
              "target": "target", "dfs": "pick'em", "other": "not a target", "not bettable": "not bettable"}


def fmt_books(r: dict, indent: str = "      ") -> str:
    """Book-comparison block printed under a bet with --show-books."""
    lines = [f"{indent}fair {r['fair_american']} ({r['fair_prob']:.1%}) via {r['fair_source']} · {r['sharp_prices']}"]
    lines.append(f"{indent}{'':2}{'Book':<19}{'This':>9}{'Opp':>14}{'Impl%':>8}{'EV%':>8}  Role")
    for q in r.get("quotes", []):
        ev = "—" if q.get("ev_pct") is None else f"{q['ev_pct']:.2f}"
        star = "★ " if q.get("best") else ("» " if q["book_key"] == r["book_key"] else "  ")
        lines.append(f"{indent}{star}{q['book'][:18]:<19}{q['american']:>9}{(q.get('opposite') or '—')[:13]:>14}"
                     f"{q['implied'] * 100:>7.1f}%{ev:>8}  {ROLE_LABEL.get(q['role'], q['role'])}"
                     + (f" ({q['entry']})" if q.get("entry") else "") + (" [after fee]" if q.get("fee_adjusted") else "")
                     + (f" · {q['fair_weight']:.0%} of fair" if q.get("fair_weight") else "")
                     + (" · excluded (book being priced)" if q.get("self_excluded") else ""))
    if r.get("other_lines"):
        lines.append(f"{indent}  other lines: " + "; ".join(
            f"{o['book']} {o['line']} {o['american']}" + (f"/{o['opposite']}" if o.get('opposite') else "")
            for o in r["other_lines"]))
    return "\n".join(lines)


def summarize_ref_compare(rows: list[dict], refs: list[str]) -> dict | None:
    if not rows or len(refs) < 2:
        return None
    from statistics import median
    d = sorted(r["abs_diff"] for r in rows)
    n = len(d)
    by_mkt = {}
    for r in rows:
        by_mkt.setdefault(r["market"], []).append(r["abs_diff"])
    summ = {"refs": refs[:2], "lines_compared": n, "median_abs_diff_pp": round(median(d) * 100, 2),
            "mean_abs_diff_pp": round(sum(d) / n * 100, 2), "p90_abs_diff_pp": round(d[int(0.9 * (n - 1))] * 100, 2),
            "max_abs_diff_pp": round(d[-1] * 100, 2),
            "share_within_1pp": round(sum(x <= 0.01 for x in d) / n, 3),
            "share_within_2pp": round(sum(x <= 0.02 for x in d) / n, 3),
            "median_by_market_pp": {k: round(median(v) * 100, 2) for k, v in sorted(by_mkt.items())}}
    summ["text"] = (f"{refs[0]} vs {refs[1]} fair prob: {n} shared lines, median |diff| "
                    f"{summ['median_abs_diff_pp']}pp, p90 {summ['p90_abs_diff_pp']}pp, "
                    f"{summ['share_within_1pp']:.0%} within 1pp")
    return summ


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="+EV sports betting scanner (The Odds API)")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--sports", help="comma list, e.g. nfl,nba (default: config scan.sports)")
    ap.add_argument("--min-ev", type=float, help="minimum EV%% (default from config)")
    ap.add_argument("--devig", choices=["power", "multiplicative"], help="devig method")
    ap.add_argument("--ref-mode", choices=["blend", "first", "priority"],
                    help="blend = priority-weighted average of the references with the line (default); "
                         "first = highest-priority reference sets the price (priority = alias)")
    ap.add_argument("--books", help="only show these target book keys, e.g. draftkings,fanduel")
    ap.add_argument("--no-main", action="store_true", help="skip main markets (props only)")
    ap.add_argument("--props", action="store_true", help="also scan player props (per-event, costs more)")
    ap.add_argument("--props-sports", help="sports to scan props for (default: same as --sports)")
    ap.add_argument("--max-events", type=int, help="props: max events per sport (soonest first)")
    ap.add_argument("--prop-markets", help="props: override market keys (comma list)")
    ap.add_argument("--max-credits", type=int, help="hard cap on credits this run")
    ap.add_argument("--offline", action="store_true", help="use cached responses only (0 credits)")
    ap.add_argument("--cache-ttl", type=float, help="minutes to reuse cached responses")
    ap.add_argument("--dry-run", action="store_true", help="show planned calls and credit estimate only")
    ap.add_argument("--out", default=str(ROOT / "output"), help="directory for csv/json output")
    ap.add_argument("--html", default=str(ROOT / "report.html"))
    ap.add_argument("--limit", type=int, default=0, help="rows to print (0 = all)")
    ap.add_argument("--show-books", action="store_true",
                    help="print every book's price for the same market/line under each bet "
                         "(★ = best bettable price, » = the flagged book)")
    a = ap.parse_args(argv)

    cfg = load_config(Path(a.config))
    sc = cfg["scan"]
    if a.min_ev is not None:
        sc["min_ev_pct"] = a.min_ev
    if a.devig:
        sc["devig_method"] = a.devig
    if a.ref_mode:
        for sect in (cfg["sharp"], cfg["sharp"].setdefault("main", {}), cfg["sharp"].setdefault("props", {})):
            sect["mode"] = a.ref_mode
    sports = [s.strip() for s in (a.sports or ",".join(sc["sports"])).split(",") if s.strip()]
    pcfg = cfg.setdefault("props", {})
    do_props = a.props or pcfg.get("enabled", False)
    props_sports = [s.strip() for s in (a.props_sports or ",".join(sports)).split(",") if s.strip()]
    max_events = a.max_events if a.max_events is not None else pcfg.get("max_events", 3)
    api_cfg = cfg["api"]
    api = OddsAPI(ROOT / api_cfg.get("cache_dir", "cache"),
                  cache_ttl_minutes=a.cache_ttl if a.cache_ttl is not None else api_cfg.get("cache_ttl_minutes", 10),
                  offline=a.offline,
                  max_credits_per_run=a.max_credits if a.max_credits is not None else api_cfg.get("max_credits_per_run"))
    keymap = cfg["sport_keys"]
    now = datetime.now(timezone.utc)
    all_bets = []
    plan = []
    errors = []
    events_scanned = {"main": 0, "props": 0}

    main_books, main_dropped = bookmaker_list(cfg, props=False)
    prop_books, prop_dropped = bookmaker_list(cfg, props=True)
    for label, dropped in (("main", main_dropped), ("props", prop_dropped if do_props else [])):
        if dropped:
            print(f"[warn] {label}: 10-bookmaker cap dropped {','.join(dropped)} "
                  f"(add them to skip_books or raise max_bookmakers_per_call)", file=sys.stderr)
    refs, ref_mode, never_target = reference_settings(cfg, False)
    prop_refs, _, _ = reference_settings(cfg, True)
    ref_rows: list[dict] = []
    prop_ref_rows: list[dict] = []

    # ---------------------------------------------------------------- main markets
    if not a.no_main:
        for s in sports:
            sk = keymap.get(s, s)
            est = len(sc["markets"]) * -(-len(main_books) // 10)
            plan.append(f"main  {s:<6} {sk:<26} markets={','.join(sc['markets'])}  ~{est} credits")
            if a.dry_run:
                continue
            try:
                data = api.odds(sk, sc["markets"], bookmakers=main_books, odds_format=api_cfg.get("odds_format", "decimal"))
            except OddsAPIError as e:
                errors.append(str(e)); print(f"[warn] {s}: {e}", file=sys.stderr); continue
            events_scanned["main"] += sum(1 for e in data if e["commence_time"] > now.isoformat().replace("+00:00", "Z"))
            all_bets += scan_events(data, s.upper(), cfg, now=now, age_now=_age_now(api, a.offline, now))
            if len(refs) >= 2:
                ref_rows += [{"sport": s.upper(), **r} for r in
                             compare_references(data, cfg, refs[0], refs[1], sc["markets"], now=now)]

    # ---------------------------------------------------------------- player props
    if do_props:
        for s in props_sports:
            sk = keymap.get(s, s)
            mk = [m.strip() for m in a.prop_markets.split(",")] if a.prop_markets else pcfg.get("markets", {}).get(s, [])
            if not mk:
                print(f"[info] no prop markets configured for {s}", file=sys.stderr); continue
            try:
                evs = api.events(sk)  # free
            except OddsAPIError as e:
                errors.append(str(e)); print(f"[warn] {s} events: {e}", file=sys.stderr); continue
            upcoming = sorted((e for e in evs if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) > now),
                              key=lambda e: e["commence_time"])[:max_events]
            per_event = len(mk) * -(-len(prop_books) // 10)
            plan.append(f"props {s:<6} {len(upcoming)} events x {len(mk)} markets  ~{per_event}/event, "
                        f"~{per_event * len(upcoming)} max")
            if a.dry_run:
                continue
            for e in upcoming:
                try:
                    d = api.event_odds(sk, e["id"], mk, bookmakers=prop_books,
                                       odds_format=api_cfg.get("odds_format", "decimal"))
                except OddsAPIError as ex:
                    errors.append(str(ex)); print(f"[warn] props {s} {e['id']}: {ex}", file=sys.stderr); continue
                events_scanned["props"] += 1
                all_bets += scan_events([d], s.upper(), cfg, now=now, markets=mk, props=True,
                                        age_now=_age_now(api, a.offline, now))
                if len(prop_refs) >= 2:
                    prop_ref_rows += [{"sport": s.upper(), **r} for r in
                                      compare_references([d], cfg, prop_refs[0], prop_refs[1], mk, now=now)]

    if a.dry_run:
        print("Main-market bookmakers:", ",".join(main_books))
        if do_props:
            print("Prop bookmakers:      ", ",".join(prop_books))
        print("\n".join(plan))
        return 0

    if a.books:
        keep = {k.strip() for k in a.books.split(",")}
        all_bets = [b for b in all_bets if b.book_key in keep]
    all_bets.sort(key=lambda b: b.ev_pct, reverse=True)
    rows = [b.to_dict() for b in all_bets]
    scanned_at = datetime.fromtimestamp(api.usage.oldest_data or now.timestamp(), timezone.utc).isoformat()
    for r in rows:
        r["scanned_at"] = scanned_at

    # ---------------------------------------------------------------- output
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET")
    with open(out / "ev_bets.json", "w") as fh:
        json.dump({"generated": stamp, "bets": rows}, fh, indent=1)
    # (summary is written below once credit info is known)
    if rows:
        with open(out / "ev_bets.csv", "w", newline="") as fh:
            flat = [k for k in rows[0] if k not in ("quotes", "other_lines")]
            w = csv.DictWriter(fh, fieldnames=flat, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    u = api.usage
    used, remaining = u.used, u.remaining
    if remaining is None:
        q = api.last_known_quota()
        if q:
            used, remaining = q.get("used"), q.get("remaining")
    credit_line = (f"credits this run: {u.spent_this_run} (live calls {u.calls_live}, cached {u.calls_cached})"
                   + (f" | quota used {used}, remaining {remaining}" if remaining is not None else ""))
    data_ts = (datetime.fromtimestamp(u.oldest_data, ET).strftime("%Y-%m-%d %I:%M %p ET")
               if u.oldest_data else stamp)
    ref_chain = "main: " + reference_chain(cfg, False) + " · props: " + reference_chain(cfg, True)
    meta = (f"Generated {stamp} · odds as of {data_ts} · fair-odds reference: {ref_chain} · devig: {sc['devig_method']} · "
            f"min EV {sc['min_ev_pct']}% · sports: {', '.join(s.upper() for s in sports)}"
            + (f" · props: {', '.join(s.upper() for s in props_sports)} (next {max_events} events)" if do_props else "")
            + f" · {len(rows)} bets")
    footer = report_footer(cfg)
    ref_summary = summarize_ref_compare(ref_rows, refs)
    prop_ref_summary = summarize_ref_compare(prop_ref_rows, prop_refs)
    for rs, lab in ((ref_summary, "main"), (prop_ref_summary, "props")):
        if rs:
            rs["text"] = f"{lab}: " + rs["text"]
            meta += " · " + rs["text"]
    with open(out / "ref_compare.json", "w") as fh:
        json.dump({"main": {"summary": ref_summary, "lines": ref_rows},
                   "props": {"summary": prop_ref_summary, "lines": prop_ref_rows}}, fh, indent=1)
    write_report(a.html, rows, meta, footer, sc["min_ev_pct"])
    with open(out / "run_summary.json", "w") as fh:
        json.dump({"generated": stamp, "odds_as_of": data_ts, "sports": sports,
                   "props_sports": props_sports if do_props else [], "max_events": max_events,
                   "events_scanned": events_scanned, "bets": len(rows),
                   "credits_this_run": u.spent_this_run, "quota_used": used, "quota_remaining": remaining,
                   "main_bookmakers": main_books, "prop_bookmakers": prop_books if do_props else []},
                  fh, indent=1)

    print(f"+EV scan · {stamp} (odds as of {data_ts}) · devig={sc['devig_method']} · min EV {sc['min_ev_pct']}%")
    print(f"events scanned: main {events_scanned['main']}, props {events_scanned['props']} · bets found: {len(rows)}")
    shown = rows[: a.limit] if a.limit else rows
    if not rows:
        print("No +EV bets above threshold.")
    elif a.show_books:
        table = fmt_table(shown).split("\n")
        print("\n".join(table[:2]))
        for line, r in zip(table[2:], shown):
            print(line)
            print(fmt_books(r))
            print()
    else:
        print(fmt_table(shown))
    print(credit_line)
    for rs in (ref_summary, prop_ref_summary):
        if rs:
            print(rs["text"])
    print("reference used per row: " + ", ".join(f"{k} {v}" for k, v in
          sorted(__import__("collections").Counter(r["fair_source"].split("(")[0] for r in rows).items())))
    print(f"HTML report: {a.html}\nCSV/JSON:    {out}/ev_bets.csv, ev_bets.json")
    if errors:
        print(f"{len(errors)} warnings (see above)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
