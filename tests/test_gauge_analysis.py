"""Regression tests for the per-gauge analyses in server.py: low-flow spells, date pairing of the duration curves and hydrograph
volumes, duplicate dates in stored flows, and year-by-year pairing of EWR achievement."""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import server  # noqa: E402

G = "400001"
BK1 = dict(gauge="416001", ewr="BK1", pu="Macintyre River and Boomi River floodplain")  # >= 7,900 ML/d, MinSpell 5, Jul-Jun, max inter-event 4 yr


class StoredFlows(unittest.TestCase):
    """Each test writes scenarios into a temporary store; caches are cleared around it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = server.STORE
        self.addCleanup(setattr, server, "STORE", old)
        server.STORE = Path(self.tmp.name)
        server.FLOWS.clear()
        server._EVAL_CACHE.clear()
        self.addCleanup(server.FLOWS.clear)
        self.addCleanup(server._EVAL_CACHE.clear)

    def store(self, sid, idx, flow, gauge=G):
        pd.DataFrame({gauge: np.asarray(flow, dtype=float)}, index=pd.DatetimeIndex(idx, name="Date")).to_csv(server.STORE / f"{sid}.csv")


class SpellTest(StoredFlows):
    def test_two_runs_and_missing_day_never_merges(self):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        f = np.full(len(idx), 10.0)
        f[10:12] = 0  # run of 2
        f[13] = 0  # run of 1 (day 12 is 5 ML/d: not below 1)
        f[12] = 5
        f[20] = 0
        f[21] = np.nan  # missing day between two low days: two runs of 1, not one of 3
        f[22] = 0
        self.store("a", idx, f)
        out = server.spells({"base": "a", "gauge": G, "thr": "1"})["scenarios"]["base"]
        self.assertEqual(out["n_runs"], 4)
        self.assertEqual(sorted(r["days"] for r in out["longest_runs"]), [1, 1, 1, 2])
        self.assertEqual(sum(r["censored"] for r in out["longest_runs"]), 2)  # the two runs beside the missing day
        y = out["years"]
        self.assertEqual(y["complete"], [0])  # the missing day makes the year incomplete: no numbers for it
        self.assertIsNone(y["longest"][0])

    def test_complete_year_counts_and_year_boundary_split(self):
        idx = pd.date_range("2000-07-01", "2002-06-30")
        f = np.full(len(idx), 10.0)
        f[(idx >= "2001-06-26") & (idx <= "2001-07-03")] = 0  # 8 low days across 30 June: 5 in WY2000, 3 in WY2001
        self.store("a", idx, f)
        out = server.spells({"base": "a", "gauge": G, "thr": "1"})["scenarios"]["base"]
        y = out["years"]
        self.assertEqual(y["wy"], [2000, 2001])
        self.assertEqual(y["below"], [5, 3])
        self.assertEqual(y["longest"], [5, 3])
        self.assertEqual(y["n"], [1, 0])  # the run starts in WY2000
        self.assertEqual(out["longest_runs"][0]["days"], 8)

    def test_paired_years_are_complete_in_both(self):
        idx = pd.date_range("2000-07-01", "2003-06-30")
        f = np.full(len(idx), 10.0)
        g = f.copy()
        g[(idx >= "2001-08-01") & (idx <= "2001-08-03")] = np.nan  # WY2001 incomplete in the comparison only
        self.store("a", idx, f)
        self.store("b", idx, g)
        out = server.spells({"base": "a", "comp": "b", "gauge": G, "thr": "1"})
        self.assertEqual(out["paired"]["wy"], [2000, 2002])


class PairingTest(StoredFlows):
    def test_duration_curve_uses_shared_dates_only(self):
        idx = pd.date_range("2000-01-01", periods=4)
        self.store("a", idx, [0, 1, 2, 3])
        self.store("b", idx, [0, 1, 2, np.nan])  # the comparison is missing the highest-flow date
        one = server.curves({"base": "a", "gauge": G, "kind": "fdc", "thr": "3"})
        self.assertEqual(one["marks"][0]["base"], 25.0)  # 1 of 4 days >= 3
        two = server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "dfdc", "thr": "2"})
        self.assertEqual(two["marks"][0]["base"], two["marks"][0]["comp"])  # the date is dropped from both: 1 of 3 days >= 2 in each
        self.assertAlmostEqual(two["marks"][0]["base"], 100 / 3, places=3)

    def test_hydrograph_volumes_only_for_years_complete_in_both(self):
        idx = pd.date_range("2000-07-01", "2002-06-30")
        a = np.full(len(idx), 1.0)
        b = np.full(len(idx), 2.0)
        b[400] = np.nan  # one missing day in WY2001
        self.store("a", idx, a)
        self.store("b", idx, b)
        h = server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "hydro"})
        self.assertEqual([r["wy"] for r in h["years"]], [2000])
        self.assertEqual(h["years"][0]["base"], 365.0)
        self.assertEqual(h["years"][0]["comp"], 730.0)
        self.assertEqual(h["missing_days"], 1)
        self.assertIsNone(h["comp"][400])  # the missing day stays missing (a break in the line), not 0

    def test_repeated_dates(self):
        idx = pd.DatetimeIndex(["2000-01-01", "2000-01-02", "2000-01-02", "2000-01-03"])
        self.store("same", idx, [1, 2, 2, 3])
        self.assertEqual(len(server.load_flows("same")), 3)  # an exact repeat is dropped
        self.store("clash", idx, [1, 2, 5, 3])
        with self.assertRaises(ValueError):
            server.load_flows("clash")  # the same date with two flows is an error


class SpellLeapYearTest(StoredFlows):
    def test_leap_water_year_is_complete_with_366_days(self):
        idx = pd.date_range("2003-07-01", "2004-06-30")  # holds 29 Feb 2004
        self.store("a", idx, np.zeros(len(idx)))
        y = server.spells({"base": "a", "gauge": G, "thr": "1"})["scenarios"]["base"]["years"]
        self.assertEqual((y["complete"], y["below"], y["longest"]), ([1], [366], [366]))


class MonthlyPairingTest(StoredFlows):
    def test_monthly_curve_uses_shared_dates_only(self):
        idx = pd.date_range("2000-07-01", "2000-07-04")
        self.store("a", idx, [10, 10, 10, 50])
        self.store("b", idx, [10, 10, 10, np.nan])
        m = server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "mdfdc", "ymax": "40"})
        jul = m["months"].index(7)
        self.assertEqual(m["diff"][-1][jul], 0.0)  # the 50 ML/d day is not in the comparison, so it leaves the base too


class EwrWithPyEwrTest(StoredFlows):
    """ewr_years / ewr_gaps on synthetic flows run through py_ewr itself (not a stand-in)."""

    def test_ewr_list_does_not_depend_on_what_ran_before(self):
        # py_ewr edits its own cached parameter sheet while it runs (blank TargetFrequency becomes 0)
        g = BK1["gauge"]
        before = server.ewr_list(g)
        idx = pd.date_range("2000-07-01", "2004-06-30")
        self.store("a", idx, np.full(len(idx), 9000.0), gauge=g)
        for e in before:
            server.ewr_years({"base": "a", "gauge": g, "ewr": e["code"], "pu": e["pu"]})
        self.assertEqual(server.ewr_list(g), before)
        # BF-FD2 has no target frequency: None, not 0
        self.assertIsNone(next(e for e in before if e["code"] == "BF-FD2")["target"])

    def run_bk1(self, flow, idx):
        self.store("a", idx, flow, gauge=BK1["gauge"])
        return server.ewr_years({"base": "a", **BK1})

    def test_no_event_at_all_does_not_fail_and_is_one_long_gap(self):
        idx = pd.date_range("2000-07-01", "2010-06-30")
        out = self.run_bk1(np.full(len(idx), 100.0), idx)
        sc = out["scenarios"]["base"]
        self.assertEqual(sc["events"], [])
        self.assertEqual(sum(sc["years"]["achieved"]), 0)
        self.assertEqual(len(sc["gaps"]), 1)
        g = sc["gaps"][0]
        self.assertTrue(g["no_success"] and g["boundary"])
        self.assertEqual(g["days"], len(idx))
        self.assertEqual(g["excess"], len(idx) - 1460)
        self.assertAlmostEqual(sc["etp"], (len(idx) - 1460) / len(idx), places=4)  # not 0: the maximum is exceeded for most of the record

    def test_only_unsuccessful_events_is_still_one_long_gap(self):
        idx = pd.date_range("2000-07-01", "2010-06-30")
        f = np.full(len(idx), 100.0)
        f[400:402] = 9000  # a 2-day event: shorter than MinSpell 5
        sc = self.run_bk1(f, idx)["scenarios"]["base"]
        self.assertEqual([e["ok"] for e in sc["events"]], [False])
        self.assertEqual([g["no_success"] for g in sc["gaps"]], [True])

    def test_gap_with_missing_days_is_censored_and_not_counted(self):
        idx = pd.date_range("2000-07-01", "2010-06-30")
        f = np.full(len(idx), 100.0)
        f[(idx >= "2000-09-01") & (idx <= "2000-09-07")] = 9000  # successful events near both ends
        f[(idx >= "2009-09-01") & (idx <= "2009-09-07")] = 9000
        f[(idx >= "2002-07-01") & (idx <= "2007-06-30")] = np.nan  # five unobserved years in the gap between them
        sc = self.run_bk1(f, idx)["scenarios"]["base"]
        mid = [g for g in sc["gaps"] if g["start"] == "2000-09-08"][0]
        self.assertTrue(mid["censored"])
        self.assertIsNone(mid["excess"])
        known = [g for g in sc["gaps"] if not g["censored"]]
        days = len(idx) - mid["days"]
        self.assertAlmostEqual(sc["etp"], sum(g["excess"] for g in known) / days, places=4)
        self.assertLessEqual(sc["etp"], 1.0)  # a share of the observed time
        ie = server.interevents({"scn": "a", **{k: v for k, v in BK1.items()}})
        self.assertNotIn("2000-09-08", [g["start"] for g in ie["exceed"]])
        self.assertEqual(ie["n_censored"], 1)

    def test_nonstandard_assessment_year_and_window_completeness(self):
        # F1_b at 404216: Jun-Nov window, so py_ewr's year runs 1 Jun - 31 May; a missing day outside the window does not matter
        idx = pd.date_range("2000-06-01", "2003-05-31")
        f = np.full(len(idx), 100.0)
        f[idx == "2001-01-15"] = np.nan  # outside Jun-Nov
        f[idx == "2002-07-15"] = np.nan  # inside the window of the year starting June 2002
        self.store("a", idx, f, gauge="404216")
        out = server.ewr_years({"base": "a", "gauge": "404216", "ewr": "F1_b"})
        self.assertEqual(out["ystart"], 6)
        y = out["scenarios"]["base"]["years"]
        self.assertEqual(dict(zip(y["wy"], y["complete"], strict=False)), {2000: 1, 2001: 1, 2002: 0})

    def test_event_length_and_volume_cover_bridged_days(self):
        # W-LF7 at 410001: >= 28,000 ML/d, MinSpell 3, dips of up to 90 days are bridged within an event
        idx = pd.date_range("2000-07-01", "2001-06-30")
        f = np.full(len(idx), 100.0)
        pulse = (idx >= "2000-08-01") & (idx <= "2000-08-03") | (idx >= "2000-08-06") & (idx <= "2000-08-08")
        f[pulse] = 30000
        self.store("a", idx, f, gauge="410001")
        ev = server.ewr_years({"base": "a", "gauge": "410001", "ewr": "W-LF7"})["scenarios"]["base"]["events"]
        e = [x for x in ev if x["start"] == "2000-08-01"][0]
        self.assertEqual((e["end"], e["days"], e["qual"]), ("2000-08-08", 8, 6))
        self.assertEqual(e["vol"], 6 * 30000 + 2 * 100)  # every day from start to end, the dip days included


class RegimeTest(StoredFlows):
    def test_thresholds_come_from_the_ewr_table_and_runs_are_counted(self):
        # 416001: the lowest EWR threshold per regime, e.g. Small fresh 540 ML/d (SF1_P, SF1_S, SF2), Very low flow 45 ML/d
        idx = pd.date_range("2000-07-01", "2001-06-30")
        f = np.full(len(idx), 50.0)
        f[10:15] = 600  # two 5-day runs at or above 540
        f[30:35] = 600
        self.store("a", idx, f, gauge="416001")
        out = server.regimes({"base": "a", "gauge": "416001"})
        lowest: dict[str, float] = {}
        from flow_regime_mappings import regimes_for

        for e in server.ewr_list("416001"):
            for rg in regimes_for(e["code"], e["state"]):
                lowest[rg] = min(lowest.get(rg, np.inf), e["thr"])
        self.assertEqual({r["regime"]: r["thr"] for r in out["regimes"]}, {k: v for k, v in lowest.items() if k != "excluded"})
        by = {r["regime"]: y for r, y in zip(out["regimes"], out["scenarios"]["base"]["regimes"], strict=False)}
        self.assertEqual((by["Small fresh"]["days"], by["Small fresh"]["n"], by["Small fresh"]["longest"]), ([10], [2], [5]))
        self.assertEqual(by["Very low flow"]["days"], [365])  # 45 ML/d: every day


class EwrYearPairingTest(unittest.TestCase):
    """ewr_years compares achievement year by year over the water years complete in both scenarios."""

    def setUp(self):
        old = server.ewr_eval
        self.addCleanup(setattr, server, "ewr_eval", old)

    def fake(self, achieved, missing_years=()):
        """Stand-in for ewr_eval: py_ewr's yearly verdicts for water years 2000-2009, and flows with no data in `missing_years`."""
        idx = pd.date_range("2000-07-01", "2010-06-30")
        flow = pd.Series(1.0, index=idx)
        for w in missing_years:
            flow[(idx >= f"{w}-07-01") & (idx <= f"{w + 1}-06-30")] = np.nan
        years = pd.DataFrame(
            {"eventYears": achieved, "numEvents": achieved, "numEventsAll": achieved, "totalEventDays": achieved, "maxEventDays": achieved, "missingDays": 0},
            index=range(2000, 2010),
        )
        empty = pd.DataFrame(columns=["startDate", "endDate", "interEventLength"])
        return {
            "pu": "PU",
            "years": years,
            "events": {},
            "flow": flow,
            "succ": empty,
            "inter": empty,
            "mrip_days": 0,
            "thr": 1.0,
            "thr_max": None,
            "dur": 1,
            "min_spell": 1,
            "per_year": 1,
            "gap_tol": 0,
            "sm": 7,
            "em": 6,
            "ystart": 7,
            "target": 50.0,
        }

    def test_equal_totals_still_show_changed_years(self):
        base = self.fake([1] * 6 + [0] * 4)  # years 1-6
        comp = self.fake([0] * 2 + [1] * 6 + [0] * 2)  # years 3-8
        server.ewr_eval = lambda sid, *a: base if sid == "a" else comp
        p = server.ewr_years({"base": "a", "comp": "b", "gauge": G, "ewr": "X"})["paired"]
        self.assertEqual((p["n"], p["base"], p["comp"], p["gained"], p["lost"]), (10, 6, 6, 2, 2))

    def test_incomplete_years_leave_the_denominator(self):
        base = self.fake([1] * 6 + [0] * 4, missing_years=(2008, 2009))  # 6 achieved of 8 complete years
        comp = self.fake([1] * 6 + [0] * 4)
        server.ewr_eval = lambda sid, *a: base if sid == "a" else comp
        out = server.ewr_years({"base": "a", "comp": "b", "gauge": G, "ewr": "X"})
        self.assertEqual(out["scenarios"]["base"]["years"]["complete"], [1] * 8 + [0, 0])
        self.assertEqual(out["paired"]["n"], 8)
        self.assertEqual(out["paired"]["pct_base"], 75.0)  # 6 / 8, not 6 / 10


if __name__ == "__main__":
    unittest.main()
