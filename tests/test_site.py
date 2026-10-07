import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ev  # noqa: E402
import site_run as sr  # noqa: E402

CFG = ev.load_config(ROOT / "config.toml")
UTC = timezone.utc


class TestSchedule(unittest.TestCase):
    def test_workflow_crons_match_config_slots(self):
        wf = (ROOT / ".github/workflows/scan.yml").read_text()
        in_wf = set(re.findall(r'cron: "([^"]+)"', wf))
        want = {re.search(r'cron: "([^"]+)"', l).group(1) for l in sr.cron_lines(CFG)}
        self.assertEqual(in_wf, want, "run `python3 site_run.py crons` and paste into scan.yml")

    def test_gate_is_dst_aware(self):
        summer = datetime(2026, 10, 7, 14, 25, tzinfo=UTC)      # EDT
        self.assertTrue(sr.gate(CFG, "schedule", "20 14 * * *", summer)[0])
        self.assertFalse(sr.gate(CFG, "schedule", "20 15 * * *", summer.replace(hour=15))[0])
        winter = datetime(2026, 12, 7, 15, 25, tzinfo=UTC)      # EST
        self.assertTrue(sr.gate(CFG, "schedule", "20 15 * * *", winter)[0])
        self.assertFalse(sr.gate(CFG, "schedule", "20 14 * * *", winter.replace(hour=14))[0])
        late = datetime(2026, 10, 8, 0, 5, tzinfo=UTC)           # 22:10 UTC cron started 2h late
        self.assertTrue(sr.gate(CFG, "schedule", "10 22 * * *", late)[0])
        self.assertTrue(sr.gate(CFG, "workflow_dispatch", "")[0])


class TestBudget(unittest.TestCase):
    now = datetime(2026, 10, 7, 22, 10, tzinfo=UTC)              # 18:10 ET slot, 24 full days left

    def test_normal_run_gets_full_cap(self):
        b = sr.budget_cap(CFG, 86, 414, self.now, [])
        self.assertEqual(b["runs_left"], 73)
        self.assertEqual(b["cap"], 5)
        self.assertFalse(b["skip"])
        self.assertLessEqual(b["projected_if_full"], b["quota_total"])

    def test_shrinks_then_skips_when_budget_short(self):
        b = sr.budget_cap(CFG, 300, 200, self.now, [])          # (200-25)/73 = 2.4 per run
        self.assertEqual(b["cap"], 2)
        self.assertFalse(b["skip"])
        b = sr.budget_cap(CFG, 470, 30, self.now, [])           # (30-25)/73 < 1
        self.assertTrue(b["skip"])

    def test_daily_cap_counts_earlier_runs(self):
        log = [{"ts": (self.now - timedelta(hours=2)).isoformat(), "credits": 12}]
        b = sr.budget_cap(CFG, 86, 414, self.now, log)
        self.assertEqual(b["cap"], 3)

    def test_manual_cap_keeps_reserve(self):
        b = sr.budget_cap(CFG, 470, 35, self.now, [], manual_cap=15)
        self.assertEqual(b["cap"], 10)


class FakeAPI:
    def __init__(self, events):
        self._ev = events

    def events(self, sk):
        return self._ev.get(sk, [])


class TestPlan(unittest.TestCase):
    now = datetime(2026, 10, 7, 22, 10, tzinfo=UTC)

    def _ev(self, i, hours):
        t = (self.now + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
        return {"id": f"e{i}", "commence_time": t, "home_team": f"H{i}", "away_team": f"A{i}"}

    def test_rotation_and_props_within_cap(self):
        evs = {"americanfootball_nfl": [self._ev(1, 26)], "icehockey_nhl": [self._ev(2, 1), self._ev(3, 2)],
               "baseball_mlb": [self._ev(4, 60)]}                  # MLB outside 36h window
        active = set(evs)
        p = sr.make_plan(CFG, FakeAPI(evs), active, 5, {"last_scanned": {}}, self.now)
        self.assertEqual([m["sport"] for m in p["mains"]], ["nfl"])
        self.assertEqual([x["event"]["id"] for x in p["props"]], ["e2"])   # soonest game
        self.assertLessEqual(p["estimated"], 5)
        # NFL refreshed an hour ago -> NHL is now stalest
        st = {"last_scanned": {"nfl:main": (self.now - timedelta(hours=1)).isoformat(),
                               "nhl:props:e2": (self.now - timedelta(hours=1)).isoformat()}}
        p = sr.make_plan(CFG, FakeAPI(evs), active, 5, st, self.now)
        self.assertEqual([m["sport"] for m in p["mains"]], ["nhl"])
        self.assertEqual([x["event"]["id"] for x in p["props"]], ["e3"])   # e2 props recent -> next game

    def test_shrinks_markets_when_cap_tiny(self):
        evs = {"americanfootball_nfl": [self._ev(1, 5)]}
        cfg = {**CFG, "site": {**CFG["site"], "props_sports": []}}
        p = sr.make_plan(cfg, FakeAPI(evs), set(evs), 1, {"last_scanned": {}}, self.now)
        self.assertEqual(p["mains"][0]["markets"], ["h2h"])
        self.assertEqual(p["estimated"], 1)


if __name__ == "__main__":
    unittest.main()
