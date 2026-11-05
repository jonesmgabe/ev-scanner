"""Unit tests for fee-aware arb math and player-name matching."""
import math
import unittest

from evtool import arb, devig as dv
from evtool.exchanges import PropQuote, MARKET_MAP
from evtool.names import normalize_player, names_match


class TestArbMath(unittest.TestCase):
    def test_classic_two_way_arb(self):
        # +150 / +150 → implied 0.4+0.4=0.8 → 25% profit on $100
        a, b, profit, pct, implied = arb.arb_stake_split(2.5, 2.5, 100.0)
        self.assertAlmostEqual(implied, 0.8)
        # stake_a = 100 * 0.4 / 0.8 = 50; payout = 125; profit = 25
        self.assertAlmostEqual(a, 50.0)
        self.assertAlmostEqual(b, 50.0)
        self.assertAlmostEqual(profit, 25.0)
        self.assertAlmostEqual(pct, 25.0)

    def test_no_arb_vigged_market(self):
        # -110 / -110
        d = dv.american_to_decimal(-110)
        self.assertFalse(arb.is_arb(d, d))
        _, _, _, pct, implied = arb.arb_stake_split(d, d, 100.0)
        self.assertGreater(implied, 1.0)
        self.assertLess(pct, 0.0)

    def test_fee_aware_kalshi_destroys_thin_arb(self):
        # Raw exchange Under at 2.05 vs FD Over 2.05 would be a tiny arb,
        # but Kalshi taker fee can erase it.
        fees = {"kalshi": {"type": "taker_p1p", "rate": 0.07}}
        raw = 2.05
        eff = arb.fee_adjusted_decimal("kalshi", raw, fees)
        self.assertLess(eff, raw)
        # Symmetric 2.05/2.05 is an arb before fees
        self.assertTrue(arb.is_arb(2.05, 2.05))
        # After fee on one leg, may or may not remain — check known number:
        # p=1/2.05≈0.4878; fee=0.07*p*(1-p)≈0.0175; cost≈0.5053; eff≈1.979
        self.assertAlmostEqual(eff, dv.kalshi_effective_decimal(2.05, 0.07), places=9)
        # 2.05 vs 1.979: 1/2.05 + 1/1.979 ≈ 0.4878 + 0.5053 = 0.9931 → still arb
        self.assertTrue(arb.is_arb(2.05, eff))
        # Thinner: 2.01 vs 2.01 after fee should not arb
        eff_thin = arb.fee_adjusted_decimal("kalshi", 2.01, fees)
        self.assertFalse(arb.is_arb(2.01, eff_thin))

    def test_polymarket_fee_same_helper(self):
        fees = {"polymarket": {"type": "taker_p1p", "rate": 0.0695}}
        eff = arb.fee_adjusted_decimal("polymarket", 2.0, fees)
        self.assertAlmostEqual(eff, dv.kalshi_effective_decimal(2.0, 0.0695), places=9)

    def test_find_prop_arbs_detects_known(self):
        fees = {
            "kalshi": {"type": "taker_p1p", "rate": 0.07},
            "polymarket": {"type": "taker_p1p", "rate": 0.0695},
        }
        fd = [PropQuote(
            book="fanduel", market_key="player_points",
            player="Donovan Mitchell", player_key=normalize_player("Donovan Mitchell"),
            side="Over", point=25.5, decimal=2.40, event="MIN @ CLE",
        )]
        # Under at ~2.10 raw on Kalshi → after fee still arbs vs 2.40
        ex = [PropQuote(
            book="kalshi", market_key="player_points",
            player="Donovan Mitchell", player_key=normalize_player("Donovan Mitchell"),
            side="Under", point=25.5, decimal=2.10, event="KXNBAPTS-...",
            liquidity=50.0,
        )]
        arbs = arb.find_prop_arbs(fd, ex, fees, stake_total=100.0)
        self.assertEqual(len(arbs), 1)
        self.assertGreater(arbs[0].profit_pct, 0.0)
        self.assertEqual(arbs[0].fd_side, "Over")
        self.assertEqual(arbs[0].ex_side, "Under")
        self.assertEqual(arbs[0].exchange, "kalshi")
        # stakes sum to total
        self.assertAlmostEqual(arbs[0].stake_fd + arbs[0].stake_ex, 100.0, places=2)

    def test_novig_never_matched(self):
        fees = {}
        fd = [PropQuote(
            book="fanduel", market_key="player_points",
            player="X", player_key="x", side="Over", point=10.5, decimal=2.5,
        )]
        ex = [PropQuote(
            book="novig", market_key="player_points",
            player="X", player_key="x", side="Under", point=10.5, decimal=2.5,
        )]
        self.assertEqual(arb.find_prop_arbs(fd, ex, fees), [])

    def test_junk_price_filtered(self):
        fees = {"polymarket": {"type": "taker_p1p", "rate": 0.0695}}
        fd = [PropQuote(
            book="fanduel", market_key="player_points",
            player="A", player_key="a", side="Over", point=10.5, decimal=2.2,
        )]
        ex = [PropQuote(
            book="polymarket", market_key="player_points",
            player="A", player_key="a", side="Under", point=10.5, decimal=2000.0,
        )]
        self.assertEqual(arb.find_prop_arbs(fd, ex, fees), [])


class TestNameMatch(unittest.TestCase):
    def test_normalize_suffix(self):
        self.assertEqual(normalize_player("Scotty Pippen Jr."), normalize_player("Scotty Pippen"))
        self.assertEqual(normalize_player("Kenyon Martin Jr"), normalize_player("Kenyon Martin"))

    def test_accents(self):
        self.assertEqual(normalize_player("Luka Dončić"), "luka doncic")
        self.assertTrue(names_match("Luka Doncic", "Luka Dončić"))

    def test_market_map_overlap(self):
        # Documented overlap: PRA only on Kalshi, not Polymarket
        self.assertEqual(MARKET_MAP["player_points"]["kalshi"], "KXNBAPTS")
        self.assertEqual(MARKET_MAP["player_points"]["poly"], "points")
        self.assertIsNone(MARKET_MAP["player_points_rebounds_assists"]["poly"])
        self.assertEqual(MARKET_MAP["player_points_rebounds_assists"]["kalshi"], "KXNBAPRA")



class TestDualBook(unittest.TestCase):
    def _q(self, book, side, dec):
        return PropQuote(book=book, market_key="player_points", player="Tyrese Haliburton",
                         player_key="tyrese haliburton", side=side, point=19.5, decimal=dec,
                         american="", event="IND @ X", event_key="ind x", commence_utc="",
                         source_id=book, fetched_at="")

    def test_all_four_pairs(self):
        sb = [self._q("fanduel", "Over", 2.2), self._q("draftkings", "Over", 2.15)]
        ex = [self._q("kalshi", "Under", 2.0), self._q("polymarket", "Under", 2.0),
              self._q("novig", "Under", 3.0)]
        arbs = arb.find_prop_arbs(sb, ex, {}, books=arb.ARB_BOOKS)
        pairs = {(a.sportsbook, a.exchange) for a in arbs}
        self.assertEqual(pairs, {("fanduel", "kalshi"), ("fanduel", "polymarket"),
                                 ("draftkings", "kalshi"), ("draftkings", "polymarket")})

    def test_subset_books(self):
        sb = [self._q("fanduel", "Over", 2.2), self._q("draftkings", "Over", 2.15)]
        ex = [self._q("kalshi", "Under", 2.0)]
        arbs = arb.find_prop_arbs(sb, ex, {}, books={"draftkings", "kalshi"})
        self.assertEqual({a.sportsbook for a in arbs}, {"draftkings"})

    def test_parser_reads_dk(self):
        ev = {"home_team": "A", "away_team": "B", "id": "e", "bookmakers": [
            {"key": k, "markets": [{"key": "player_points", "outcomes": [
                {"name": "Over", "description": "P Q", "point": 10.5, "price": 1.9}]}]}
            for k in ("fanduel", "draftkings", "betmgm")]}
        q = arb.sportsbook_quotes_from_odds_events([ev], ["player_points"])
        self.assertEqual(sorted(x.book for x in q), ["draftkings", "fanduel"])

if __name__ == "__main__":
    unittest.main()
