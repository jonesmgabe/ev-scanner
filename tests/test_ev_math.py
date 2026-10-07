"""Unit tests for the EV / devig / Kelly / DFS math on hand-checked examples."""
import math
import unittest
from datetime import datetime, timezone

from evtool import devig as dv
from evtool import dfs
from evtool.scanner import scan_events


class TestConversions(unittest.TestCase):
    def test_american_decimal_roundtrip(self):
        self.assertAlmostEqual(dv.american_to_decimal(-110), 1.909090909, places=6)
        self.assertAlmostEqual(dv.american_to_decimal(150), 2.5)
        self.assertAlmostEqual(dv.decimal_to_american(2.5), 150)
        self.assertAlmostEqual(dv.decimal_to_american(1.5), -200)
        self.assertEqual(dv.fmt_american(dv.prob_to_american(0.5)), "+100")


class TestDevig(unittest.TestCase):
    def test_symmetric_market(self):
        d = dv.american_to_decimal(-110)
        for m in ("multiplicative", "power"):
            p = dv.devig([d, d], m)
            self.assertAlmostEqual(p[0], 0.5, places=9)
            self.assertAlmostEqual(sum(p), 1.0, places=9)

    def test_multiplicative_known(self):
        # -150 / +130: implied 0.6 and 0.434783 (sum 1.034783)
        p = dv.devig_multiplicative([dv.american_to_decimal(-150), dv.american_to_decimal(130)])
        self.assertAlmostEqual(p[0], 0.6 / 1.0347826, places=6)    # 0.579832
        self.assertAlmostEqual(p[1], 0.4347826 / 1.0347826, places=6)

    def test_power_known(self):
        imp = [0.6, 1 / 2.3]
        p = dv.devig_power([1 / x for x in imp])
        # sum(p_i^k) = 1 and favourite keeps more probability than multiplicative
        k = math.log(p[0]) / math.log(imp[0])
        self.assertAlmostEqual(imp[0] ** k + imp[1] ** k, 1.0, places=9)
        self.assertAlmostEqual(math.log(p[1]) / math.log(imp[1]), k, places=6)
        self.assertGreater(p[0], dv.devig_multiplicative([1 / x for x in imp])[0])
        # hand-solved: k ~= 1.0530 -> 0.6**k ~= 0.5840
        self.assertAlmostEqual(p[0], 0.5840, places=3)

    def test_three_way(self):
        p = dv.devig_power([2.1, 3.4, 3.6])
        self.assertAlmostEqual(sum(p), 1.0, places=9)


class TestEVKelly(unittest.TestCase):
    def test_known_example(self):
        # Sharp line -150/+130 -> fair underdog prob (multiplicative) 0.420168.
        # Target book offers +140 (decimal 2.40):
        #   EV = 0.420168 * 2.40 - 1 = +0.84%
        #   full Kelly = (1.4*0.420168 - 0.579832)/1.4 = 0.006002 ; quarter = 0.15%
        p = dv.devig_multiplicative([dv.american_to_decimal(-150), dv.american_to_decimal(130)])[1]
        self.assertAlmostEqual(dv.ev_pct(p, 2.40), 0.8403, places=3)
        self.assertAlmostEqual(dv.kelly_fraction(p, 2.40) * 100, 0.6002, places=3)
        self.assertAlmostEqual(dv.kelly_fraction(p, 2.40, 0.25) * 100, 0.1501, places=3)
        self.assertEqual(dv.fmt_american(dv.prob_to_american(p)), "+138")

    def test_coin_flip(self):
        self.assertAlmostEqual(dv.ev_pct(0.5, 2.10), 5.0)
        self.assertAlmostEqual(dv.kelly_fraction(0.5, 2.10), (1.1 * 0.5 - 0.5) / 1.1)
        self.assertEqual(dv.kelly_fraction(0.4, 2.0), 0.0)   # -EV -> no bet

    def test_kalshi_fee(self):
        # 50c contract: fee 0.07*0.5*0.5 = 1.75c -> effective decimal 1/0.5175
        self.assertAlmostEqual(dv.kalshi_effective_decimal(2.0), 1 / 0.5175, places=9)


class TestDFS(unittest.TestCase):
    def test_power_breakeven(self):
        self.assertAlmostEqual(dfs.breakeven_prob(2, {2: 3.0}), 3 ** -0.5, places=6)   # 57.74%
        self.assertAlmostEqual(dfs.breakeven_prob(5, {5: 20.0}), 20 ** -0.2, places=6)  # 54.93%

    def test_best_entry_from_config(self):
        app = {"max_legs": 6, "power": {"2": 3.0, "3": 6.0, "7": 50.0},
               "flex": {"5": {"5": 10.0, "4": 2.0, "3": 0.5}}}
        label, be = dfs.best_entry(app)
        # 7-pick is beyond max_legs; 5-pick flex (53.7%) beats 3-pick 6x power (55.0%)
        self.assertEqual(label, "5-pick flex")
        self.assertAlmostEqual(dfs.expected_payout(be, 5, {5: 10.0, 4: 2.0, 3: 0.5}), 1.0, places=6)
        self.assertAlmostEqual(dfs.breakeven_prob(3, {3: 6.0}), 6 ** (-1 / 3), places=6)  # 55.03%

    def test_flex6_breakeven(self):
        be = dfs.breakeven_prob(6, {6: 25.0, 5: 2.0, 4: 0.4})
        self.assertAlmostEqual(dfs.expected_payout(be, 6, {6: 25.0, 5: 2.0, 4: 0.4}), 1.0, places=6)
        self.assertAlmostEqual(be, 0.5421, places=3)


class TestScannerExactLine(unittest.TestCase):
    CFG = {
        "scan": {"min_ev_pct": 1.0, "devig_method": "multiplicative", "kelly_multiplier": 0.25,
                 "max_kelly_pct": 5, "max_ev_pct": 25, "markets": ["h2h", "spreads", "totals"],
                 "skip_live": True, "max_fair_odds_age_min": 1e9},
        "sharp": {"book": "pinnacle", "consensus_min_books": 3},
        "props": {"consensus_min_books": 2},
        "target_books": [{"name": "DraftKings", "key": "draftkings", "covered": True},
                         {"name": "PrizePicks", "key": "prizepicks", "covered": True, "props_only": True}],
        "dfs": {"prizepicks": {"max_legs": 6,
                               "power": {"2": 3.0, "5": 20.0},
                               "flex": {"6": {"6": 25.0, "5": 2.0, "4": 0.4}}}},
        "fees": {},
    }

    def _ev(self, books):
        return {"id": "e1", "commence_time": "2099-01-01T00:00:00Z", "home_team": "Home", "away_team": "Away",
                "bookmakers": books}

    def test_spread_only_matches_same_point(self):
        pin = {"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Home", "price": 1.91, "point": -3.5}, {"name": "Away", "price": 1.91, "point": 3.5}]}]}
        dk_same = {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Home", "price": 2.10, "point": -3.5}, {"name": "Away", "price": 1.75, "point": 3.5}]}]}
        bets = scan_events([self._ev([pin, dk_same])], "NFL", self.CFG)
        self.assertEqual(len(bets), 1)
        self.assertEqual(bets[0].selection, "Home -3.5")
        self.assertAlmostEqual(bets[0].ev_pct, 5.0, places=2)   # 0.5 * 2.10 - 1
        dk_diff = {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Home", "price": 2.10, "point": -4.5}, {"name": "Away", "price": 1.75, "point": 4.5}]}]}
        self.assertEqual(scan_events([self._ev([pin, dk_diff])], "NFL", self.CFG), [])

    def test_prop_consensus_and_dfs(self):
        def ou(key, title, over, under, point=24.5, player="Jane Doe"):
            return {"key": key, "title": title, "markets": [{"key": "player_points", "outcomes": [
                {"name": "Over", "description": player, "price": over, "point": point},
                {"name": "Under", "description": player, "price": under, "point": point}]}]}
        books = [ou("fanduel", "FanDuel", 1.70, 2.20), ou("betmgm", "BetMGM", 1.72, 2.15),
                 ou("prizepicks", "PrizePicks", 1.84, 1.84),
                 ou("draftkings", "DraftKings", 1.70, 2.20, point=25.5)]  # different line: no match
        bets = scan_events([self._ev(books)], "WNBA", self.CFG, markets=["player_points"], props=True)
        self.assertEqual([b.book_key for b in bets], ["prizepicks"])
        b = bets[0]
        self.assertTrue(b.is_dfs and b.kelly_pct is None)
        self.assertTrue(b.fair_source.startswith("consensus(2)"))
        self.assertGreater(b.fair_prob, 0.542)


class TestReferences(unittest.TestCase):
    def _cfg(self, mode):
        cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in TestScannerExactLine.CFG.items()}
        cfg["sharp"] = {"references": ["pinnacle", "novig"], "mode": mode, "never_target": ["novig"],
                        "consensus_min_books": 3}
        # even if someone lists novig as a target it must never produce a row
        cfg["target_books"] = cfg["target_books"] + [{"name": "Novig", "key": "novig", "covered": True}]
        return cfg

    @staticmethod
    def _ml(key, home, away):
        return {"key": key, "title": key.title(), "markets": [{"key": "h2h", "outcomes": [
            {"name": "Home", "price": home}, {"name": "Away", "price": away}]}]}

    def _ev(self, books):
        return {"id": "e1", "commence_time": "2099-01-01T00:00:00Z", "home_team": "Home",
                "away_team": "Away", "bookmakers": books}

    def test_novig_fallback_and_never_target(self):
        # no pinnacle -> novig (2.0/2.0 => 50%) is the reference; DK 2.10 => +5%
        books = [self._ml("novig", 2.0, 2.0), self._ml("draftkings", 2.10, 1.80)]
        bets = scan_events([self._ev(books)], "NFL", self._cfg("priority"))
        self.assertEqual([(b.book_key, b.fair_source) for b in bets], [("draftkings", "novig")])
        self.assertAlmostEqual(bets[0].ev_pct, 5.0, places=2)
        # novig offering a juicy price is still never a bet row
        books = [self._ml("pinnacle", 2.0, 2.0), self._ml("novig", 2.5, 1.7)]
        self.assertEqual(scan_events([self._ev(books)], "NFL", self._cfg("priority")), [])

    def test_priority_vs_blend(self):
        # pinnacle 50/50, novig ~(1/1.8)/(1/1.8+1/2.25)=55.6% home
        books = [self._ml("pinnacle", 2.0, 2.0), self._ml("novig", 1.8, 2.25),
                 self._ml("draftkings", 2.10, 1.80)]
        pri = scan_events([self._ev(books)], "NFL", self._cfg("priority"))
        self.assertEqual(pri[0].fair_source, "pinnacle")
        self.assertAlmostEqual(pri[0].fair_prob, 0.5, places=4)
        bl = scan_events([self._ev(books)], "NFL", self._cfg("blend"))
        self.assertEqual(bl[0].fair_source, "blend")
        nov_home = (1 / 1.8) / (1 / 1.8 + 1 / 2.25)   # exactly 0.5556; no overround so devig = identity
        self.assertAlmostEqual(bl[0].fair_prob, round((0.5 + nov_home) / 2, 4), places=4)

    def test_book_comparison_quotes(self):
        cfg = self._cfg("priority")
        cfg["target_books"] = cfg["target_books"] + [{"name": "FanDuel", "key": "fanduel", "covered": True}]
        books = [self._ml("pinnacle", 2.0, 2.0), self._ml("novig", 2.5, 1.7),
                 self._ml("draftkings", 2.10, 1.80), self._ml("fanduel", 2.05, 1.85)]
        tot = lambda key, pt: {"key": key, "title": key.title(), "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "price": 2.0, "point": pt}, {"name": "Under", "price": 2.0, "point": pt}]}]}
        books[0]["markets"] += tot("pinnacle", 44.5)["markets"]
        books[2]["markets"] += [{"key": "totals", "outcomes": [{"name": "Over", "price": 2.12, "point": 44.5},
                                                                {"name": "Under", "price": 1.78, "point": 44.5}]}]
        books[3]["markets"] += tot("fanduel", 45.5)["markets"]
        bets = scan_events([self._ev(books)], "NFL", cfg)
        ml = next(b for b in bets if b.market == "Moneyline")
        q = {x["book_key"]: x for x in ml.quotes}
        self.assertEqual(set(q), {"pinnacle", "novig", "draftkings", "fanduel"})
        self.assertEqual(q["novig"]["role"], "reference")       # shown, but labelled not bettable
        self.assertFalse(q["novig"]["best"])                      # 2.5 is the top price, yet never "best"
        self.assertTrue(q["draftkings"]["best"])
        self.assertFalse(q["fanduel"]["best"])
        self.assertEqual(q["draftkings"]["opposite"], "-125")
        self.assertAlmostEqual(q["draftkings"]["ev_pct"], ml.ev_pct, places=2)
        self.assertEqual([x["role"] for x in ml.quotes][:2], ["reference", "reference"])
        over = next(b for b in bets if "Over" in b.selection)
        self.assertEqual([(o["book_key"], o["line"]) for o in over.other_lines], [("fanduel", "Over 45.5")])


if __name__ == "__main__":
    unittest.main()
