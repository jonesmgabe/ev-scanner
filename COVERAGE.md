# Book and app coverage in The Odds API (checked Oct 7 2026)

How this was checked:
- the official bookmaker list (https://the-odds-api.com/sports-odds-data/bookmaker-apis.html, free)
- live probes:
  - `coverage_probe.py` (5 credits): NFL `h2h` across `us,us2,us_ex,eu`, plus `/events/{id}/markets` for `us_dfs`. Output: `output/coverage_probe.json`.
  - DFS/Courtside probe (3 credits): NFL `h2h` with `bookmakers=courtside,fliff,underdog,dabble_us_dfs,pick6,prizepicks,betr_au`, plus `/events/{id}/markets` for the next 2 NFL games. Output: `output/coverage_probe_dfs.json`.

## Sportsbooks, exchanges and sweepstakes books

| Book | Covered? | Key | Region | Seen live on this (free) key | Notes / alternative public data |
|---|---|---|---|---|---|
| DraftKings | Yes | `draftkings` | us | Yes | |
| FanDuel | Yes | `fanduel` | us | Yes | |
| Caesars | Paid plans only | `williamhill_us` | us | No | No public API. Set `skip_paid_only_books = false` after upgrading. |
| ESPN BET → theScore Bet | Yes | `espnbet` | us2 | Yes | Rebranded to theScore Bet; the key is unchanged |
| Kalshi | Yes | `kalshi` | us_ex | Yes (main markets) | Has only *alternate*/milestone props, so it's skipped on prop calls. Public no-auth market-data API at `api.elections.kalshi.com/trade-api/v2`. |
| Polymarket | Yes | `polymarket` | us_ex | Yes (main markets) | Props are anytime-TD only, often junk 1.01 quotes, so it's skipped on prop calls. Public Gamma/CLOB APIs. |
| ProphetX | Yes | `prophetx` | us_ex | Yes | Partner API needs a key |
| Fliff | Yes | `fliff` | us2 | Yes (15/29 NFL games, main + core props) | Social/sweepstakes sportsbook with normal odds. EV is computed like a sportsbook (value is in sweeps coins). |
| **Courtside** | Paid plans only | `courtside` | us2 | **No odds returned.** The free `/markets` metadata does list it (h2h, spreads, totals, alt lines, anytime TD, alternate props). | Courtside Games Inc.'s free-to-play social/sweepstakes "Social Sportsbook" (courtside.app, 42 states): picks with Courtside Coins (no cash value) and redeemable Courtside Cash. Its rules say it is "not a sportsbook". No public API found. Added to config as `paid_only`. |
| Sportzino | No | – | – | – | Sweepstakes social sportsbook. No public API. Paid feeds only (OpticOdds, Betstamp). |

## DFS / pick'em apps

The break-even is per leg, for the app's best standard entry with up to 6 legs, computed from the
payout tables in `config.toml` `[dfs.*]`. Payouts change, so verify them in each app.

| App | Covered? | Key | Region | Seen live (next 2 NFL games) | Break-even used | Payout source |
|---|---|---|---|---|---|---|
| PrizePicks | Yes | `prizepicks` | us_dfs | Yes | **54.21%** (6-pick flex 25x/2x/0.4x) | oddsreference.com platform comparison (verified there Jul 19 2026); 6-flex from oddsassist.com |
| DraftKings Pick6 | Yes | `pick6` | us_dfs | Yes (TB@DAL only) | **56.10%** (5-pick 18x; Base Payout only, so conservative) | oddsreference.com (Jul 19 2026) |
| Underdog | Yes | `underdog` | us_dfs | Yes | **53.74%** (5-pick insured 10x/2x/0.5x) | oddsreference.com (Jul 19 2026) |
| Dabble | Yes | `dabble_us_dfs` | us_dfs | **No NFL listings** at check time. In `props.skip_books` to keep prop calls at 10 books. | **54.07%** (6-pick All-In 40x) | Dabble Help Center pay scale (updated Sep 3 2026) |
| Betr Picks | No | – (only `betr_au`, an Australian sportsbook) | – | – | – | No documented public API found |
| Sleeper Picks | No | – | – | – | – | Sleeper's public read-only API (docs.sleeper.com) covers fantasy leagues, players and stats, not pick'em lines |
| ParlayPlay | No | – | – | – | – | No documented public API found |
| Chalkboard | No | – | – | – | – | No documented public API found |
| Boom Fantasy | No | – | – | – | – | No documented public API found |
| Vivid Picks | No | – | – | – | – | No documented public API found |

Sites that need a login were not scraped. Paid odds aggregators advertise DFS pick'em feeds, but
coverage of each uncovered app was not verified.

## Fair-odds references

Configured in `[sharp.main]` / `[sharp.props]`. Checked Oct 7 2026: one `/events/{id}/markets` call across
`us,us2,us_ex,eu,uk,au` (1 credit, 73 bookmakers for TB@DAL) plus one direct `bookmakers=` probe with 8
candidate keys (1 credit). Output: `output/coverage_probe_circa_bookmaker.json`.

| Reference | Used for | Key | Region | On The Odds API? | Props |
|---|---|---|---|---|---|
| Pinnacle | main #1 (50) | `pinnacle` | eu | Yes, free key. NFL 15/29, NCAAF 56/66, NBA 12/46, NHL 3/14, MLB 4/4, WNBA 2/2 events | 10 NFL prop markets for TB@DAL. Used only as a props consensus filler now |
| **Bookmaker.eu** | main #2 (30) | none | none | **No.** Absent from all 6 regions. `bookmaker`, `bookmakereu`, `bookmaker_eu`, `bookmakerag`, `bookmaker_ag` returned nothing | n/a |
| **Circa Sports** | main #3 / props #3 (20) | none | none | **No.** Absent from all 6 regions. `circasports`, `circa`, `circa_sports` returned nothing | n/a |
| FanDuel | props #1 (50), also a target | `fanduel` | us | Yes, free key | Yes: 25 prop markets for TB@DAL |
| DraftKings | props #2 (30), also a target | `draftkings` | us | Yes, free key | Yes: 46 prop markets for TB@DAL |
| **Novig** (no-vig exchange) | last resort, both (10) | `novig` | us_ex | Yes, free key. h2h/spreads/totals: NFL 29/29, NCAAF 57/66, NBA 13/46, NHL 12/14, MLB 4/4, WNBA 2/2 | Yes (NFL yds/receptions/TDs, WNBA, NHL). Prop quotes are often wide (median implied sum 1.069), so lines above 1.05 are not used |

Bookmaker and Circa stay in the config lists with `[sharp] unavailable_books = ["bookmaker", "circasports"]`, so
they are never requested and would slot in at their priority if the API adds them. Check the key name then.
Some other odds aggregators (e.g. OddsPapi) carry Circa as `circasports`; The Odds API does not.

Novig is reference-only via `never_target = ["novig"]`: it is never a target book or a bet row
(`output/coverage_probe_novig.json`). FanDuel and DraftKings are both references (props) and targets. A
FanDuel price is always priced from the other references only, never against itself.
