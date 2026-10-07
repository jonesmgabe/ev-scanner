# +EV Scanner (v1)

A personal positive-expected-value bet finder, similar in spirit to OddsJam's +EV tool. It pulls odds
from [The Odds API v4](https://the-odds-api.com/liveapi/guides/v4/) and works out fair (no-vig)
probabilities from per-market-type reference books: Pinnacle → Bookmaker → Circa for main lines and
FanDuel → DraftKings → Circa for player props, then Novig as a last resort, then a market consensus.
Bookmaker and Circa aren't on The Odds API today, so they are skipped until the API adds them. It then
flags prices at your books that beat fair value. Novig is used as a reference only and never appears
as a bet. It covers main markets for NFL, NCAAF, NBA, NHL, MLB and
WNBA, plus player props. Pick'em apps (PrizePicks, Pick6, Underdog, Dabble) are compared against each
app's own break-even rate, and the sweepstakes sportsbooks Fliff and Courtside are handled like any
other book. See `COVERAGE.md` for what The Odds API covers.

## Quick start

```bash
export ODDS_API_KEY=...            # never stored in any file; read at runtime only
cd /workspace/ev-tool
python ev.py --dry-run --props --props-sports nfl     # show planned calls + credit estimate (free)
python ev.py --sports nfl,nba --min-ev 1              # main markets only (3 credits per sport)
python ev.py --sports nfl --props --max-events 3      # + props for the next 3 NFL games
python ev.py --offline                                # re-scan cached responses, 0 credits
python -m unittest tests/test_ev_math.py -v           # math tests
```

Requires Python 3.11+ and `requests`. The HTML report has no other dependencies.

Outputs:
- the terminal table (ranked by EV%)
- `report.html`, a static dark-themed report you can sort by any column and filter by sport, book,
  market, type (Main/Prop/DFS), minimum EV% and free-text search. Hover cells for decimal odds, fair
  probability and the reference prices used.
- `output/ev_bets.csv`, `output/ev_bets.json` and `output/run_summary.json`

### CLI flags

| flag | meaning |
|---|---|
| `--sports nfl,ncaaf,...` | sports aliases from `[sport_keys]` in the config (any raw Odds API sport key also works) |
| `--min-ev 1` | EV% threshold (default from config: 1%) |
| `--devig power\|multiplicative` | how vig is removed (default: power) |
| `--ref-mode blend\|first` | blend = priority-weighted average of the references with the line (default); first = highest-priority reference sets the price |
| `--books draftkings,fanduel` | show only these target books |
| `--props` | also scan player props (per-event calls) |
| `--props-sports nfl` | which sports to pull props for (default: same as `--sports`) |
| `--max-events N` | props: only the N soonest upcoming events per sport |
| `--prop-markets k1,k2` | props: override the market keys |
| `--no-main` | props only |
| `--max-credits N` | hard per-run cap. Calls that would go over it are refused. |
| `--offline` / `--cache-ttl M` | use only cached data / reuse cache younger than M minutes |
| `--dry-run` | print bookmakers, calls and estimated credits, then exit |
| `--show-books` | print the book-comparison table under each bet (★ = best bettable price, » = flagged book) |

## Book comparison (per-bet dropdown)

Every bet row in `report.html` expands. Tap the row or its ▸ chevron and a detail panel opens
directly below it. The panel covers that exact market, selection and line:

- **Header:** fair odds (American and %), the reference that set it (one book, `blend(a+b)` or
  `consensus(n)`), and each reference's raw two-way prices with its share of the blend.
- **One row per book that priced it:**
  - American odds for this side, plus the opposite side when the book lists it.
  - Implied probability.
  - EV% against the fair price.
- **Labels:**
  - Reference books you can't bet (Novig) are labelled **"reference, not bettable"** and never
    appear as a bet row or the best price.
  - References that are also target books (FanDuel and DraftKings for props) are labelled
    **"reference & target"**. They can be the best price.
  - Each reference row shows "N% of fair", its weight in this bet's fair price. When the bet is
    at that same book, the row says **"left out of its own fair price"**.
  - The best price among bettable target books is highlighted (green, BEST).
  - The flagged book is marked "← flagged".
  - Kalshi and Polymarket EV is after fees, and the row says "after fee".
  - Pick'em apps show **"BE −xxx"**: the per-leg break-even of the app's best entry type (e.g.
    "6-pick flex"), its implied %, and the edge against break-even.
- **Other lines:** books that list the market at a different line (e.g. Under 14.5 instead of
  15.5), the nearest 3 by line, with both sides' prices.

Rows can be expanded several at a time. Expanded state is tied to the bet itself, so each panel
stays under its parent row when you sort or filter.

- **Linking:** `report.html?open=0,3` (or `#open=0`) opens rows by their index in the data.
- **Mobile:** below 700px the report drops the Sport, Event, Start, Market, Kelly and Reference
  columns. The event, market and kick-off appear as a sub-line under the selection. The panel
  becomes compact and shows the opposite price under each book's price.

The quotes come from the odds already pulled for the scan. Each bet in `output/ev_bets.json`
carries `quotes` and `other_lines`, and they are embedded in the HTML. The CSV keeps flat columns
only. Showing the panel or `--show-books` costs **0 credits**. Rebuild from cache with `--offline`:

```
python3 ev.py --sports nfl,ncaaf --props --props-sports nfl --max-events 1 --show-books --offline
```

The offline run must use the same sports, prop and book settings as the cached run. The cache key
includes the bookmaker list.

## How it works

1. **Fetch** (`evtool/api.py`). Main markets come from one `/v4/sports/{sport}/odds` call per sport
   with `markets=h2h,spreads,totals`. Instead of regions it passes an explicit `bookmakers=` list of
   at most 10 books: that call type's references (minus `unavailable_books`), Novig, targets, then
   consensus fillers. The API bills 10 bookmakers as one region, so each sport costs **3 credits**,
   even though the books span the us, us2, us_ex and eu regions.
   - **Main lines:** Pinnacle, Novig, DraftKings, FanDuel, theScore, Kalshi, Polymarket,
     ProphetX, Fliff and BetOnline (consensus filler).
   - **Props** (`/v4/sports/{sport}/events/{id}/odds`): FanDuel, DraftKings, Novig, theScore,
     ProphetX, Pick6, PrizePicks, Underdog, Fliff and Pinnacle. Pinnacle is a consensus filler
     here, not a props reference.
   - **Left out:** Caesars and Courtside (paid plans only); Pick6, PrizePicks and Underdog from
     main-line calls (props only); BetMGM, BetRivers, LowVig and Bovada (consensus fillers, no slot
     left); and on prop calls Kalshi (alternate props only), Polymarket (anytime TD only) and
     Dabble (no NFL props listed).

   If the cap ever cuts a reference or target, the CLI prints a warning naming it. Every response is cached to `cache/` and every live call's `x-requests-*` headers are
   appended to `cache/usage_log.jsonl`. The key is added only to the outgoing request and never
   appears in cache files, logs or error messages.
2. **Line matching** (`evtool/scanner.py`). Outcomes are grouped into "lines":
   - h2h: one line per game
   - spreads: keyed by the home team's handicap, so Home −3.5 and Away +3.5 are one line
   - totals: keyed by the total
   - props: keyed by (normalised player name, point)

   A target price is only compared against a reference on exactly the same line. Exchanges such as
   ProphetX sometimes list several price levels for one selection; the best one is kept.
3. **Fair probabilities.** References are set per call type in `[sharp.main]` and `[sharp.props]`:

   | | references (priority order, weights) | min refs |
   |---|---|---|
   | main lines | Pinnacle 50, Bookmaker 30 (n/a), Circa 20 (n/a) | 1 |
   | player props | FanDuel 50, DraftKings 30, Circa 20 (n/a) | 2 |
   | last resort (both) | Novig (weight 10), then consensus | |

   - **Usable lines.** A reference line counts only if it is complete (both or all sides), was
     updated within 30 min, has no side at 1.01 or lower, and has an implied-probability sum
     within 0.97–1.20. Novig also needs an implied sum of 1.05 or less, because many of its prop
     lines are wide bid/ask quotes (median 1.069).
   - **Each book is de-vigged on its own** (power by default) before anything is combined.
   - **`mode = "blend"` (default).** The fair probability is a weighted average of the listed
     references that have the exact line. The weights follow your priority order (50/30/20) and
     are renormalised over the books present. FanDuel + DraftKings becomes 62.5/37.5; Pinnacle
     alone becomes 100%.
     - Why blend rather than pure priority: for props, FanDuel and DraftKings are both soft-ish and
       heavily vigged, so averaging two independent de-vigged prices is less noisy than trusting
       one.
     - For main lines only Pinnacle is available today, so blend and first give the same result.
   - **`mode = "first"` (`--ref-mode first`).** The highest-priority reference with the line sets
     the price. `min_refs` still has to be met as confirmation.
   - **Minimum references.** Main lines need ≥1 reference and props ≥2 (`min_refs`), because
     FD/DK props carry heavier vig. If the listed books fall short, the `fallback_references`
     (Novig) are added at `fallback_weight` until the minimum is met.
     - Why Novig goes last: it is a low-vig exchange, but you didn't list it. Its prop quotes are
       often too wide to use, so it is a sensible tie-breaker rather than a primary.
   - **No self-comparison.** The book being priced is never part of its own fair price. A FanDuel
     prop is judged against DraftKings (+Circa, then Novig) only, and a DraftKings prop against
     FanDuel (+Circa, then Novig). Any reference that is also a target is handled the same way.
   - **Consensus fallback.** If the minimum still isn't met: a leave-one-out consensus of the
     other books (excluding the target and the DFS apps). Main lines need at least 3 books and
     props at least 2.
   - **Unavailable references.** `[sharp] unavailable_books = ["bookmaker", "circasports"]`: listed
     by you but not on The Odds API. They are never requested and slot in at their weight if the
     API adds them (confirm the key name then).
   - **Reference column.** The CLI and HTML show `pinnacle`, `fanduel`, `blend(fanduel+draftkings)`,
     `consensus(n)` and so on. Hovering shows the exact prices and weights used.
   - **Reference comparisons.** Each run compares the first two usable references per type
     (Pinnacle vs Novig for main lines, FanDuel vs DraftKings for props) and writes the result to
     `output/ref_compare.json`.
   - **Reference-only books:** `[sharp] never_target = ["novig"]`. Novig is kept out of the target
     list, and the scanner drops any row for it even if someone adds it as a target.
   - De-vig methods:
     - *Multiplicative:* p_i = (1/d_i) / Σ(1/d_j)
     - *Power (default):* find k with Σ(1/d_j)^k = 1, then p_i = (1/d_i)^k. This takes more vig off
       longshots, in line with the favourite–longshot bias.
4. **EV and stake.** EV% = fair_prob × decimal_odds − 1. Kalshi and Polymarket US prices are first
   converted to fee-inclusive odds (taker fee θ·p·(1−p), θ = 0.07 / 0.0695). The stake column is
   quarter Kelly: f = ¼ · (b·p − q)/b, capped at 5% of bankroll. The fair price is shown in American
   odds.
5. **DFS pick'em** (`evtool/dfs.py` + `[dfs.<key>]` in `config.toml`). Pick'em apps pay fixed
   multipliers, so their quoted "odds" are ignored. Each app's payout table (power/all-in and
   flex/insured, up to `max_legs`, default 6) is stored in the config with a cited source and a
   verify-in-app note. The scanner computes the per-leg break-even p* for every entry and uses the
   lowest:

   | App | Best entry | Break-even |
   |---|---|---|
   | PrizePicks | 6-pick flex | 54.21% |
   | Pick6 | 5-pick | 56.10% (Base Payout only) |
   | Underdog | 5-pick insured | 53.74% |
   | Dabble | 6-pick All-In | 54.07% |

   `Book odds` shows that break-even as "BE −116". EV% is the per-leg edge fair/p* − 1, which is
   exact for power plays and an approximation for flex. Kelly shows "—". DFS legs use
   `props.dfs_min_edge_pct` (default 0.5%). To add an app, give it a `[[target_books]]` entry with
   `props_only = true` and a `[dfs.<key>]` block.
6. **Filters.** Games that have already started are skipped. Rows above `max_ev_pct` (25%) are
   dropped as likely stale or bad data. Rows are ranked by EV%.

## Live site (GitHub Actions + Pages)

The repo deploys itself to GitHub Pages: **https://jonesmgabe.github.io/ev-scanner/**. The site is
public but marked `noindex`. `.github/workflows/scan.yml` runs on a schedule and on demand (Actions →
"Scan and publish" → Run workflow). Each run:

1. **Gate.** The cron table lists each ET time twice (EDT and EST). The gate step runs only the
   line that matches today's DST state, so 10:20 ET stays 10:20 ET all year.
2. **Budget guard (0 credits).** The free `/sports` call returns `x-requests-used/remaining`.
   This run's cap is the smallest of:
   - `[budget].max_credits_per_run`
   - what is left of `max_credits_per_day` today (earlier runs today, manual ones included,
     come from the run log the site publishes)
   - `(remaining − reserve_credits) / scheduled runs left until the quota resets`

   The run shrinks if the fair share is smaller than the cap. It is **skipped** if the cap drops
   below `min_run_credits`, and a skipped run still republishes the last good report with a banner
   explaining why.
3. **Plan (0 credits).**
   - Uses `/events` to find sports with a game in the next `window_hours` (36).
   - Ranks them by hours since their last refresh × `sport_weight`, so each run refreshes the
     stalest sport first.
   - Buys main markets (3 credits per sport).
   - Holds back `props_reserve_credits` for props on the soonest game(s), skipping games whose
     props were pulled in the last 6 h.
   - Spends spare credits on more sports. On a tiny cap it drops to moneyline only.
4. **Scan and merge.**
   - New bets are merged with still-valid bets from the previous published run.
   - Carried-over bets are other sports, games not yet started, no more than 24 h old. They are
     labelled "earlier" and show their odds age.
   - Started games are hidden by default.
5. **Publish.**
   - `site/index.html` is the report. At the top: **Last updated (ET)**, which sports were pulled
     this run, what was carried over and how old it is, and a live "page built X ago" line that
     turns amber after 6 h and red after 12 h.
   - `site/site_data.json` holds the bets plus rotation state and the run log; the next run reads
     it back from the live site.
   - `tools/check_secrets.py site` must pass before upload, then the official
     `configure-pages` / `upload-pages-artifact` / `deploy-pages` actions publish it.

If a run fails, nothing is deployed and the previous report stays up.

**Key handling.**
- The key lives only in the Actions secret `ODDS_API_KEY`. It is passed as an env var to the
  scan and secret-check steps, and GitHub masks it in logs.
- The client never prints it, never puts the request URL into errors (network exceptions are
  scrubbed), and never writes it to the cache.
- Before pushing, run `python3 tools/check_secrets.py --git --history`. It is also installed
  locally as a pre-commit/pre-push hook.

**Free plan defaults (500 credits/month).**

| | value |
|---|---|
| runs | 10:20, 12:20 and 18:10 ET (late morning, before the 1 PM slate, before the 7 PM slate) |
| per run | main markets for 1 sport (3) + props, 2 markets, for the soonest game (2) = **5 credits max** |
| per day | ≤ **15** (3 × 5, enforced by `max_credits_per_day`) |
| per month | ≤ ~450–465 (fewer when no games are in the window); 25 credits always held in reserve |

On the free plan a single run can't refresh every sport, because 3–4 sports in season would cost
9–12 credits. Instead the rotation refreshes each sport with games in the window about once a
day, and the page merges the latest scan of each.

## When you upgrade the API

All of the cadence and caps are in **two places**: `config.toml` (`[site]` + `[budget]`) and the
cron lines in `.github/workflows/scan.yml`. Steps:

1. **`[budget]`:**
   - `max_credits_per_run` and `max_credits_per_day`: aim for runs × cap per day ≈ 90% of
     quota / 30.
   - `reserve_credits` and `monthly_quota` (informational).
   - Check `quota_reset_day` against your account page.
2. **`[site]`:**
   - `slots_et`: add run times.
   - `max_main_sports_per_run`, `props_reserve_credits`, `max_prop_events_per_run`.
   - `[site.prop_markets]`: e.g. the full lists from `[props.markets]`.
   - `window_hours`.
3. **Cron:** run `python3 site_run.py crons` and paste the output over the `schedule:` lines in
   `scan.yml`. A unit test fails if the two drift apart.
4. **Check:** `python3 site_run.py plan` shows the budget decision and the plan without spending
   anything. Commit and push.

Example for a 20,000-credit plan:

```toml
[site]
slots_et = ["08:00", "10:20", "12:20", "14:30", "16:30", "18:10", "19:45", "21:30"]
max_prop_events_per_run = 4
props_reserve_credits = 24
[site.prop_markets]        # e.g. copy the 6-market lists from [props.markets]
[budget]
max_credits_per_run = 75   # every in-window sport's main markets + props for ~4 games
max_credits_per_day = 600  # ~18,000/month
reserve_credits = 500
```

GitHub's scheduled workflows can start 5–30 min late at busy times. The slots sit at :10/:20
past the hour, and the gate accepts late starts.

## Configuration (`config.toml`)

- `[scan]`: threshold, devig method, Kelly multiplier and cap, markets, sports, live-game skip,
  sharp staleness limit
- `[sharp]`: blend/first mode, Novig fallback and weight, unavailable books, never-target books,
  overround caps, consensus minimum; `[sharp.main]` / `[sharp.props]`: references, weights, min_refs
- `[api]`: cache TTL, per-run credit cap, 10-books-per-call limit, whether to skip paid-only books
- `[[target_books]]`: each target with its Odds API key, region and coverage flags (`paid_only`,
  `props_only`, `dfs_entry_types`)
- `[consensus]`: filler books for the main-market consensus (betonlineag, betmgm, …)
- `[props]`: on/off, `max_events`, consensus minimum, filler books, DFS edge threshold, and per-sport
  market keys:
  - NFL/NCAAF: `player_pass_yds, player_rush_yds, player_reception_yds, player_receptions, player_pass_tds, player_anytime_td`
  - NBA/WNBA: `player_points, player_rebounds, player_assists, player_threes, player_points_rebounds_assists`
  - NHL: `player_points, player_shots_on_goal, player_goals`
  - MLB: `batter_hits, batter_total_bases, pitcher_strikeouts, batter_home_runs`
- `[fees.*]`: exchange fee models

## Credit costs (free tier = 500 credits/month)

| Scan | Cost |
|---|---|
| `/sports`, `/events` | free |
| Main markets, 1 sport (h2h+spreads+totals, ≤10 books) | 3 |
| Main markets, all 6 sports | 18 |
| Props, 1 event | number of prop markets *returned* × 1 (NFL 6, NBA/WNBA 5, MLB 4, NHL 3). Adding more DFS apps doesn't change this while the list stays at ≤10 books. |
| Props, full NFL Sunday slate (~13–14 games) | ~80–85 |
| Props, a full NFL week (~15–16 games) | ~90–96 |
| Props, typical NBA night (10 games) | ~50 |
| Props, typical NHL night (10 games) | ~30 |
| Props, typical MLB day (15 games) | ~60 |

So 500 credits buys roughly one of:
- ~27 full main-market scans of all 6 sports, or
- ~5 full-week NFL prop scans

A daily all-sport main scan alone (18 × 30 = 540) would already be over the free quota. A workable
free-tier routine is 1–2 main scans a day for only the sports that have games, plus props for a few
chosen games (`--max-events`). Anything more needs a paid plan.

Asking for more than 10 books in one call doubles the cost (20 books = 2 "regions"). The empty
response for an off-season sport costs nothing.

## Files

```
ev.py               CLI
config.toml         settings (books, sharp, threshold, devig, props, fees)
evtool/api.py       Odds API client + cache + credit log
evtool/devig.py     conversions, multiplicative/power devig, EV, Kelly, exchange fees
evtool/dfs.py       pick'em payout tables and break-even maths
evtool/scanner.py   line matching, fair probabilities, EV rows
evtool/report.py    HTML report (sortable, filterable, expandable book comparison, mobile layout)
site_run.py         scheduled scan + site build (budget guard, planner, carry-over)
.github/workflows/scan.yml  schedule, Pages deploy
tools/check_secrets.py      fails if the API key / a token is in files, the git index or history
coverage_probe.py   one-off bookmaker coverage probe (≈5 credits)
COVERAGE.md         coverage of the 10 requested books
report.html         latest report; report.png / report_expanded.png / report_mobile.png screenshots
tests/              unit tests (python3 -m unittest discover -s tests)
```

## Known limitations

- Pinnacle odds come from its public website through The Odds API, which can lag. Prices also move
  quickly. Re-check each price in the app before betting, and treat exchange prices as
  liquidity-dependent (no bet-limit data is used yet).
- Caesars (`williamhill_us`) and Courtside (`courtside`) are only on paid Odds API plans.
- Sportzino, Betr Picks, Sleeper, ParlayPlay, Chalkboard, Boom and Vivid Picks aren't covered at all.
- Dabble is covered but had no NFL props listed at check time. It's currently skipped on prop calls
  to keep the call at 10 books.
- Circa and Bookmaker.eu are not carried by The Odds API (checked Oct 7 2026), so main lines rely on
  Pinnacle (then Novig) and props on FanDuel + DraftKings (then Novig).
- Because the book being priced is left out of its own fair price, FanDuel and DraftKings prop
  prices need the other one plus Novig (or a consensus) to be evaluated, so few FD/DK prop rows appear.
- Novig's prop markets are often wide (illiquid). Those lines are skipped as a reference by the
  1.05 implied-sum cap.
- The 10-book cap per call is full for props. Adding another app means dropping one or paying for a
  second "region".
- One-sided markets (e.g. a book's anytime-TD "Yes" with no "No") can only be priced when the
  reference book offers both sides.
- ProphetX commission is assumed to be 0, and Pick6 "Extra Winnings" aren't modelled, which makes
  the Pick6 break-even conservative.
- DFS payout tables change and can vary by state, promo or pick tier, so verify them in-app. Fliff
  and Courtside are sweepstakes products: EV is in sweeps coins and redemption rules apply.
- Team names and player names rely on The Odds API's normalisation. Only trivial clean-up is applied
  (case, periods, whitespace).
- No correlation handling for DFS entries; legs are treated as independent.

## Next steps

- **Props scheduling:** pull props only for games starting in the next N hours, and use the free
  `/events` endpoint to decide when.
- **Auto-refresh:** a cron/systemd timer, e.g. main markets every 30–60 minutes (18 credits per run)
  and props shortly before kick-off. Diff results between runs.
- **Alerts:** push new bets above X% EV to Slack, Telegram or email. Use `includeLinks=true` to add
  bet-slip deep links.
- Use `includeBetLimits=true` for exchange liquidity. Add alternate lines, period markets and more
  prop markets.
- Track closing-line value by storing each flagged bet and comparing it with Pinnacle's closing price.
- Add a paid plan to unlock Caesars, or add direct Kalshi/Polymarket public API feeds for faster
  exchange prices.
