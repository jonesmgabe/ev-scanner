# Arbitrage scanner (NBA player props v1)

FanDuel vs **Kalshi** and FanDuel vs **Polymarket** only. Novig is never a bettable
arb target. The scanner never places trades — it only ranks fee-aware opportunities.

## Prop-market overlap (checked 2026-10-26)

| FanDuel (Odds API key) | Kalshi series | Polymarket `sportsMarketType` |
|---|---|---|
| `player_points` | `KXNBAPTS` | `points` |
| `player_rebounds` | `KXNBAREB` | `rebounds` |
| `player_assists` | `KXNBAAST` | `assists` |
| `player_threes` | `KXNBA3PT` | `threes` |
| `player_points_rebounds_assists` | `KXNBAPRA` | **none** (Poly has no PRA type on game events) |

Also on exchanges but **not** matched in v1 (no FanDuel Odds API key in our config):
Kalshi `KXNBABLK` / `KXNBASTL` / `KXNBASTOCK`, Polymarket `blocks` / `steals`.

### Matching heuristics

1. Same market type (table above).
2. Same numeric line (`point` / Kalshi `floor_strike` / Poly `line`).
   - Kalshi milestone “30+ points” → `floor_strike` 29.5 → treat YES as **Over 29.5**, NO as **Under 29.5**.
   - Polymarket “Donovan Mitchell: Points O/U 25.5” → Yes=Over, No=Under at 25.5.
3. Same player after normalization (`evtool/names.py`): lowercase, strip accents,
   drop Jr/Sr/II, collapse punctuation. Fuzzy fallback: same last name + matching
   first initial. Unmatched quotes are skipped.
4. Kickoff / event labels are informational only for props v1 (player+line is enough).

## Fees

Uses `config.toml` `[fees.kalshi]` / `[fees.polymarket]` (`taker_p1p`) via
`evtool.devig.apply_fee` / `kalshi_effective_decimal`. Arb only if
`1/fd_decimal + 1/exchange_decimal_after_fee < 1`. Stake split for a total bankroll
unit (default $100) equalizes payout on both legs.

## How to run

```bash
cd /workspace/ev-tool

# Demo / offline (0 Odds API credits): FD fixture + live Kalshi/Poly (or exchange fixture)
python3 -m evtool.arb --once --sport nba --fd-source fixture --exchanges live
python3 -m evtool.arb --once --sport nba --fd-source fixture --exchanges fixture

# FD from local Odds API cache only (0 credits) if you have prop cache files
python3 -m evtool.arb --once --fd-source cache --offline

# Live FD pull (SPENDS credits): 1 credit × markets × events with bookmakers=fanduel
# Example: --max-events 1 --markets player_points,player_rebounds → ~2 credits
python3 -m evtool.arb --once --fd-source api --max-events 1 \
  --markets player_points,player_rebounds

# Poll loop: exchanges every --interval seconds; keep FD on fixture/cache to save credits
python3 -m evtool.arb --loop --interval 20 --fd-source fixture --exchanges live

python3 -m unittest tests/test_arb_math.py -v
```

Useful filters: `--min-profit 0.5`, `--max-decimal 3.5` (drop penny longshots),
`--min-liquidity 10` (when the exchange feed reports size).


Outputs (under `--out`, default `output/`):

- `arbs.csv` / `arbs.json` — ranked opportunities  
- `arbs.html` — simple dark table (event, player, market, line, FD price, exchange
  after fee, profit %, stake split, liquidity)

## Credit math (FanDuel / Odds API)

| Call | Credits |
|---|---|
| Kalshi public REST | 0 |
| Polymarket Gamma | 0 |
| Odds API `/events` | 0 (usually cached) |
| Odds API `/events/{id}/odds?markets=player_points,player_rebounds&bookmakers=fanduel` | **≈ number of markets** (1 region) |

Free-plan note: if you are near the monthly cap, keep `--fd-source fixture|cache`.
Do **not** run `--loop` with `--fd-source api`. Prefer refreshing FanDuel every
many exchange polls (`--fd-refresh-every`).

## Limitations

- Odds API FanDuel props lag exchanges; arbs can vanish before you click.
- Exchange asks can be thin — check `liquidity` and re-quote in the Kalshi /
  Polymarket apps (and FanDuel) before staking.
- No auto-betting, no order placement, no Novig as a target.
- Main-line h2h/spreads/totals arbs are out of scope for this props-first v1.
- PRA arbs are FanDuel↔Kalshi only (Polymarket has no PRA game markets).

## Files

| Path | Role |
|---|---|
| `evtool/arb.py` | Math, matching, CLI |
| `evtool/exchanges.py` | Kalshi + Polymarket clients + market map |
| `evtool/names.py` | Player-name normalization |
| `fixtures/arb/fd_nba_props.json` | Synthetic FD props overlapping live lines |
| `fixtures/arb/exchange_nba_props.json` | Snapshot of exchange quotes for offline demos |
| `tests/test_arb_math.py` | Fee-aware arb + name tests |

## FanDuel + DraftKings (dual sportsbook)

Sportsbook side is now **FanDuel and/or DraftKings**; exchange side is Kalshi/Polymarket
(fee-aware). Pairs: FD↔Kalshi, FD↔Polymarket, DK↔Kalshi, DK↔Polymarket. Matching is unchanged
(player + market + exact line). Novig never appears.

- `--books` defaults to `fanduel,draftkings`. Subset: `--books draftkings` or
  `--books fanduel,kalshi` (adding exchanges restricts the exchange side too).
- Fixtures: `--fd-fixture fixtures/arb/fd_nba_props.json`, `--dk-fixture fixtures/arb/dk_nba_props.json`
  (DK fixture is synthetic, derived from FD fixture; forced overs at 2.12).
- `--fd-source cache` reads Odds API blobs for both books, falling back per-book to fixtures.
- `--fd-source api` requests `bookmakers=fanduel,draftkings` in ONE call per event (same US
  region → same credit cost as FD-only).
- Output: new `sportsbook` column in CSV/JSON; HTML "Sportsbook" column shows the book name.
  `fd_side/fd_decimal/fd_american/stake_fd` fields now mean "sportsbook leg" (names kept for compatibility).

```bash
python3 -m evtool.arb --once --fd-source fixture --exchanges fixture            # both books, 0 credits
python3 -m evtool.arb --once --fd-source api --max-events 1 --exchanges live    # live, both books
```

## Scheduled runs

`.github/workflows/scan.yml` runs `python3 -m evtool.arb --fd-source api --exchanges live --max-events 1`
in the 6:10 PM ET slot (pre-tip for NBA) and on manual dispatch. Output is published under
`site/arb/` (live at https://jonesmgabe.github.io/ev-scanner/arb/). The step is
`continue-on-error`, uses the `ODDS_API_KEY` secret (never printed), costs only the props
credits for one game, and runs before the site secret check. Never places trades; Novig is never a target.
