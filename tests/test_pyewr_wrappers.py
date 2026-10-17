"""Tests for the functions in src/server.py that wrap py_ewr: ewr_list, load_frames, run_scenario, worker.

Tests that run py_ewr over several years of daily data are marked `slow` and are left out of the pre-commit hook (`pytest -m "not slow"`).
The py_ewr parameter table is the source of truth for thresholds, so expectations are read from it directly rather than copied from the
wrapper's own output.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from py_ewr import data_inputs, evaluate_EWRs
from py_ewr.scenario_handling import ScenarioHandler
from snapshots import assert_snapshot

import server

ROOT = Path(__file__).resolve().parents[1]
GOULBURN_A = ROOT / "data/example_scenarios/dummy_goulburn_scenario_A.csv"
GOULBURN_GAUGES = ["405201", "405202", "405203", "405232"]


@pytest.fixture(scope="module")
def ewr_table() -> pd.DataFrame:
    return data_inputs.get_EWR_table(None)


# ---------------------------------------------------------------------------------------------------------------------
# ewr_list
# ---------------------------------------------------------------------------------------------------------------------
class TestEwrList:
    GAUGES = ["416001", "410001", "404216", "405201"]

    def test_unknown_gauge_has_no_ewrs(self):
        assert server.ewr_list("not-a-gauge") == []

    def test_known_values_match_the_parameter_table(self, ewr_table):
        # BF1_a at 416001 (Macintyre River and Boomi River floodplain): >= 160 ML/d for 220 days, Jul-Jun, target 50%, max inter-event 0.3561644 yr
        row = ewr_table[
            (ewr_table.Gauge.astype(str) == "416001") & (ewr_table.Code == "BF1_a") & (ewr_table.PlanningUnitName.str.startswith("Macintyre"))
        ].iloc[0]
        assert (float(row.FlowThresholdMin), int(row.Duration), int(row.StartMonth), int(row.EndMonth), float(row.TargetFrequency)) == (160.0, 220, 7, 6, 50.0)
        entry = next(e for e in server.ewr_list("416001") if e["code"] == "BF1_a" and e["pu"].startswith("Macintyre"))
        assert (entry["thr"], entry["dur"], entry["sm"], entry["em"], entry["target"]) == (160.0, 220, 7, 6, 50.0)
        assert entry["handler"] == "lowflow_handle" and entry["consecutive"] is False  # low-flow EWRs count non-consecutive days
        assert entry["maxint"] == pytest.approx(0.3561644, abs=1e-9)

    @pytest.mark.parametrize("gauge", GAUGES)
    def test_every_entry_is_consistent_with_the_table(self, gauge, ewr_table):
        t = ewr_table[ewr_table.Gauge.astype(str) == gauge]
        entries = server.ewr_list(gauge)
        assert entries, f"no EWRs listed at {gauge}"
        seen: set[tuple[str, str]] = set()
        for e in entries:
            assert e["handler"] in ("flow_handle", "lowflow_handle") and e["consecutive"] is (e["handler"] == "flow_handle")
            assert 1 <= e["sm"] <= 12 and 1 <= e["em"] <= 12 and e["dur"] >= 1 and e["thr"] >= 0 and e["maxint"] >= 0
            # a blank target is None in a fresh process but 0.0 once py_ewr has run in the same process (it fills blanks in its shared table: see PR)
            assert e["target"] is None or 0 <= e["target"] <= 100
            assert e["pu"] in e["pus"] and e["pus"] == sorted(set(e["pus"]), key=e["pus"].index)  # no repeats
            for pu in e["pus"]:
                rows = t[(t.Code == e["code"]) & (t.PlanningUnitName == pu)]
                assert not rows.empty
                assert float(rows.FlowThresholdMin.iloc[0]) == e["thr"] and int(rows.Duration.iloc[0]) == e["dur"]
                assert (e["code"], pu) not in seen  # each EWR x planning unit is listed exactly once overall
                seen.add((e["code"], pu))

    @pytest.mark.parametrize("gauge", GAUGES)
    def test_multigauge_ewrs_are_not_offered(self, gauge, ewr_table):
        t = ewr_table[ewr_table.Gauge.astype(str) == gauge]
        multi = {(r.Code, r.PlanningUnitName) for r in t.itertuples() if isinstance(r.Multigauge, str) and r.Multigauge.strip()}
        listed = {(e["code"], pu) for e in server.ewr_list(gauge) for pu in e["pus"]}
        assert not (multi & listed)

    def test_entries_are_unique_by_their_settings(self):
        entries = server.ewr_list("416001")
        keys = [(e["code"], e["thr"], e["dur"], e["sm"], e["em"], e["target"], e["maxint"], e["handler"]) for e in entries]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------------------------------------------------
# load_frames
# ---------------------------------------------------------------------------------------------------------------------
class TestLoadFrames:
    def test_standard_time_series_gives_one_flow_column_per_gauge_and_no_levels(self):
        df_f, df_l = server.load_frames("Standard time-series", str(GOULBURN_A))
        assert sorted(map(str, df_f.columns)) == GOULBURN_GAUGES
        assert len(df_f) == 9132 and df_f.index[0] == pd.Timestamp("1995-07-01") and df_f.index[-1] == pd.Timestamp("2020-06-30")
        assert df_l.shape[1] == 0
        raw = pd.read_csv(GOULBURN_A, index_col="Date", parse_dates=True)
        for g in GOULBURN_GAUGES:  # flows pass through unchanged
            assert df_f[g].tolist() == raw[f"{g}_flow"].tolist()

    def test_unknown_format(self):
        with pytest.raises(ValueError, match="unknown data format"):
            server.load_frames("Excel macro", str(GOULBURN_A))

    def test_the_example_files_are_whole_water_years_of_daily_data(self):
        for f in (ROOT / "data/example_scenarios").glob("*.csv"):
            idx = pd.to_datetime(pd.read_csv(f, usecols=["Date"]).Date)
            assert idx.is_monotonic_increasing and idx.is_unique
            assert idx.iloc[0] == pd.Timestamp("1995-07-01") and idx.iloc[-1] == pd.Timestamp("2020-06-30")
            assert len(idx) == (idx.iloc[-1] - idx.iloc[0]).days + 1  # no skipped days
            flows = pd.read_csv(f).drop(columns="Date")
            assert not flows.isna().any().any() and (flows >= 0).all().all()


# ---------------------------------------------------------------------------------------------------------------------
# run_scenario
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def goulburn_run():
    return server.run_scenario(GOULBURN_A.read_bytes(), GOULBURN_GAUGES, None, "Standard time-series", GOULBURN_A.name)


@pytest.mark.slow
class TestRunScenario:
    def test_row_structure_and_value_ranges(self, goulburn_run):
        rows, stats = goulburn_run
        assert len(rows) == stats["ewr_rows"] == 35
        assert all(set(r) == {"pu", "gauge", "ewr", "fr", "et"} for r in rows)
        assert {r["gauge"] for r in rows} <= set(GOULBURN_GAUGES)
        assert len({(r["pu"], r["gauge"], r["ewr"]) for r in rows}) == len(rows)  # one row per planning unit x gauge x EWR
        for r in rows:
            for k in ("fr", "et"):
                assert r[k] is None or (math.isfinite(r[k]) and r[k] >= 0)
                assert r[k] is None or round(r[k], 4) == r[k]  # rounded to 4 places
            assert r["et"] is None or r["et"] <= 1.0  # a share of the record's days

    def test_stats_reconcile_with_the_rows(self, goulburn_run):
        rows, stats = goulburn_run
        assert stats == {
            "gauges_in_file": 4,
            "gauges_requested": 4,
            "ewr_rows": 35,
            "missing_gauges": [],
            "skipped": {},
            "flow_gauges": [],
            "format": "Standard time-series",
        }
        assert {r["gauge"] for r in rows} == set(GOULBURN_GAUGES)  # every requested gauge produced results

    def test_matches_the_baseline_snapshot(self, goulburn_run):
        rows, _ = goulburn_run
        assert_snapshot("run_scenario_goulburn_A", rows)

    def test_fr_and_etp_agree_with_an_independent_recomputation_from_py_ewr(self, goulburn_run):
        """Re-derive FR = Frequency / TargetFrequency and ETP = sum(max(0, gap - MRIP days)) / TotalDays with plain Python from py_ewr's own tables."""
        rows, _ = goulburn_run
        df_f, df_l = server.load_frames("Standard time-series", str(GOULBURN_A))
        table = data_inputs.get_EWR_table(None)
        cfg = data_inputs.get_ewr_calc_config(None)
        results, events = {}, {}
        for gauge in sorted(set(df_f.columns)):
            results[gauge], events[gauge] = evaluate_EWRs.calc_sorter(df_f, df_l, gauge, table, cfg)
        sh = ScenarioHandler("scenario.csv", "Standard time-series")
        sh.pu_ewr_statistics, sh.yearly_events, sh.flow_data, sh.level_data = {"scenario": results}, {"scenario": events}, df_f, df_l
        res = sh.get_ewr_results()
        inter = sh.get_all_successful_interEvents()
        gaps: dict[tuple[str, str, str], list[float]] = {}
        for r in inter.itertuples():
            gaps.setdefault((str(r.Gauge), r.PlanningUnit, r.Code), []).append(float(r.interEventLength))
        expected = {}
        for r in res.itertuples():
            target, freq = float(r.TargetFrequency), float(r.Frequency)
            fr = freq / target if target > 0 else None
            mrip, days = float(r.MaxInterEventYears) * 365, float(r.TotalDays)
            if mrip > 0 and days > 0:
                excess = sum(max(0.0, g - mrip) for g in gaps.get((str(r.Gauge), r.PlanningUnit, r.Code), []))
                et = excess / days
            else:
                et = None
            expected[(r.PlanningUnit, str(r.Gauge), r.Code)] = (fr, et)
        assert len(expected) == len(rows)
        for r in rows:
            fr, et = expected[(r["pu"], r["gauge"], r["ewr"])]
            assert (r["fr"] is None) == (fr is None) and (r["et"] is None) == (et is None)
            if fr is not None:
                assert r["fr"] == pytest.approx(fr, abs=5e-5)  # rounded to 4 places: half a unit in the last place
            if et is not None:
                assert r["et"] == pytest.approx(et, abs=5e-5)

    def test_a_scenario_that_is_better_everywhere_has_no_lower_fr(self):
        """Doubling every flow cannot reduce how often a minimum-flow style EWR is met (BF1 needs flow >= a threshold)."""
        raw = pd.read_csv(GOULBURN_A)
        wetter = raw.copy()
        wetter.loc[:, wetter.columns != "Date"] = wetter.loc[:, wetter.columns != "Date"] * 2
        base, _ = server.run_scenario(raw.to_csv(index=False).encode(), GOULBURN_GAUGES, None, "Standard time-series", "a.csv")
        more, _ = server.run_scenario(wetter.to_csv(index=False).encode(), GOULBURN_GAUGES, None, "Standard time-series", "b.csv")
        bmap = {(r["pu"], r["gauge"], r["ewr"]): r["fr"] for r in base}
        mmap = {(r["pu"], r["gauge"], r["ewr"]): r["fr"] for r in more}
        assert bmap.keys() == mmap.keys()
        low_flow = [k for k in bmap if k[2].startswith("BF")]
        assert low_flow
        assert all(mmap[k] >= bmap[k] for k in low_flow if bmap[k] is not None and mmap[k] is not None)

    def test_a_completely_dry_file_is_evaluated_not_a_crash(self):
        """Regression: py_ewr's inter-event summary raised KeyError('Gauge') when no EWR had any event."""
        idx = pd.date_range("2000-07-01", "2010-06-30")
        dry = pd.DataFrame({"Date": idx.strftime("%Y-%m-%d"), "405201_flow": 0.0})
        rows, stats = server.run_scenario(dry.to_csv(index=False).encode(), ["405201"], None, "Standard time-series", "dry.csv")
        assert stats["ewr_rows"] == len(rows) == 6
        assert [(r["ewr"], r["fr"]) for r in rows] == [(e, 0.0) for e in ("BF1", "BK1", "F1", "F2", "F3", "OB1")]

    def test_only_events_shorter_than_min_spell_are_evaluated_not_a_crash(self):
        """409003: every EWR needs a spell of at least 3 days, so a single one-day spike gives events but no successful event."""
        idx = pd.date_range("2000-07-01", "2010-06-30")
        f = np.zeros(len(idx))
        f[400] = 1e7
        df = pd.DataFrame({"Date": idx.strftime("%Y-%m-%d"), "409003_flow": f})
        rows, stats = server.run_scenario(df.to_csv(index=False).encode(), ["409003"], None, "Standard time-series", "spike.csv")
        assert stats["ewr_rows"] == len(rows) == 2 and stats["skipped"] == {}
        assert all(r["fr"] == 0.0 for r in rows)

    def test_empty_successful_event_summary_is_tolerated(self, monkeypatch):
        """If py_ewr's successful-inter-event summary fails on an empty ledger, run_scenario carries on with no gaps; other errors still surface."""
        from py_ewr.scenario_handling import ScenarioHandler as SH

        def empty(self):
            raise KeyError("Gauge")

        monkeypatch.setattr(SH, "get_all_successful_interEvents", empty)
        rows, _ = server.run_scenario(GOULBURN_A.read_bytes(), ["405201"], None, "Standard time-series", "a.csv")
        assert len(rows) == 6 and all(r["et"] == 0.0 for r in rows)  # no gaps recorded

        def other(self):
            raise KeyError("Something else")

        monkeypatch.setattr(SH, "get_all_successful_interEvents", other)
        with pytest.raises(KeyError, match="Something else"):
            server.run_scenario(GOULBURN_A.read_bytes(), ["405201"], None, "Standard time-series", "a.csv")

    @pytest.mark.xfail(
        strict=True,
        reason="known discrepancy (see PR): the headline ETP counts no gap for an EWR with no successful event (0.0), whereas the flow-chart "
        "ETP (ewr_gaps) treats the whole record as one gap (0.9001 here). Remove this marker when the two are reconciled.",
    )
    def test_never_achieved_ewr_has_the_same_etp_in_the_table_and_the_flow_chart(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "STORE", tmp_path)
        server.FLOWS.clear()
        server._EVAL_CACHE.clear()
        idx = pd.date_range("2000-07-01", "2010-06-30")
        dry = pd.DataFrame({"Date": idx.strftime("%Y-%m-%d"), "405201_flow": 0.0})
        rows, _ = server.run_scenario(dry.to_csv(index=False).encode(), ["405201"], "dry", "Standard time-series", "dry.csv")
        chart = server.ewr_years({"base": "dry", "gauge": "405201", "ewr": "F1"})["scenarios"]["base"]["etp"]
        table = next(r["et"] for r in rows if r["ewr"] == "F1")
        server.FLOWS.clear()
        server._EVAL_CACHE.clear()
        assert table == pytest.approx(chart, abs=1e-4)

    def test_flows_are_stored_for_the_charts_and_merged_by_gauge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "STORE", tmp_path / "store")
        server.FLOWS.clear()
        raw = pd.read_csv(GOULBURN_A, index_col="Date", parse_dates=True)
        first, stats = server.run_scenario(GOULBURN_A.read_bytes(), ["405201", "405202"], "s1", "Standard time-series", "a.csv")
        assert stats["flow_gauges"] == ["405201", "405202"]
        stored = pd.read_csv(tmp_path / "store/s1.csv", index_col=0, parse_dates=True)
        assert list(stored.columns) == ["405201", "405202"]
        assert stored["405201"].tolist() == raw["405201_flow"].tolist()  # flows are stored unchanged
        # a second upload for other gauges, merged into the same scenario, keeps the first two
        _, stats2 = server.run_scenario(GOULBURN_A.read_bytes(), ["405203"], "s1", "Standard time-series", "a.csv", merge=True)
        merged = pd.read_csv(tmp_path / "store/s1.csv", index_col=0, parse_dates=True)
        assert list(merged.columns) == ["405201", "405202", "405203"] and stats2["flow_gauges"] == ["405203"]
        assert merged["405203"].tolist() == raw["405203_flow"].tolist() and len(merged) == len(raw)
        # without merge the scenario is replaced
        server.run_scenario(GOULBURN_A.read_bytes(), ["405232"], "s1", "Standard time-series", "a.csv")
        assert list(pd.read_csv(tmp_path / "store/s1.csv", index_col=0).columns) == ["405232"]
        server.FLOWS.clear()
        assert first  # rows were produced

    def test_gauges_not_in_the_file_are_reported_missing(self):
        rows, stats = server.run_scenario(GOULBURN_A.read_bytes(), [*GOULBURN_GAUGES, "999999"], None, "Standard time-series", "a.csv")
        assert stats["missing_gauges"] == ["999999"] and stats["gauges_in_file"] == 4 and stats["gauges_requested"] == 5
        assert len(rows) == 35


class TestRunScenarioErrors:
    def test_unknown_format(self):
        with pytest.raises(ValueError, match="unknown data format"):
            server.run_scenario(b"x", ["1"], fmt="Excel macro")

    def test_file_that_is_not_in_the_chosen_format(self):
        with pytest.raises(ValueError, match="Could not read this file as 'Bigmod - MDBA'"):
            server.run_scenario(GOULBURN_A.read_bytes(), ["405201"], None, "Bigmod - MDBA", "x.csv")

    def test_garbage_bytes(self):
        with pytest.raises(ValueError, match="Could not read this file"):
            server.run_scenario(b"\x00\x01not,a,csv\n", ["405201"], None, "Standard time-series", "x.csv")

    def test_empty_file(self):
        with pytest.raises(ValueError, match="Could not read this file"):
            server.run_scenario(b"", ["405201"], None, "Standard time-series", "x.csv")

    def test_no_matching_gauge_columns(self):
        with pytest.raises(ValueError, match="No columns in this 'Standard time-series' file match gauges"):
            server.run_scenario(GOULBURN_A.read_bytes(), ["999999"], None, "Standard time-series", "x.csv")

    def test_northern_connectivity_scenario_must_exist(self, monkeypatch):
        import northern_connectivity as nc

        monkeypatch.setattr(nc, "unpack_northern_connectivity", lambda path: {"CP1A": pd.DataFrame()})
        with pytest.raises(ValueError, match="scenario 'ZZ' not found in the workbook; available: CP1A"):
            server.load_frames("Northern connectivity", "book.xlsx", sub="ZZ")


class TestWorker:
    def test_failed_job_records_the_error_and_elapsed_time(self):
        server.JOBS["t1"] = {"status": "running", "t0": 0.0}
        try:
            server.worker("t1", b"", ["1"], None, "Excel macro")
            job = server.JOBS["t1"]
            assert job["status"] == "error" and job["message"].startswith("ValueError: unknown data format")
            assert job["elapsed"] > 0
        finally:
            server.JOBS.pop("t1", None)

    @pytest.mark.slow
    def test_successful_job_stores_rows_and_stats(self):
        server.JOBS["t2"] = {"status": "running", "t0": 0.0}
        try:
            server.worker("t2", GOULBURN_A.read_bytes(), ["405201"], None, "Standard time-series", "a.csv")
            job = server.JOBS["t2"]
            assert job["status"] == "done" and len(job["rows"]) == 6 and job["stats"]["gauges_in_file"] == 1
        finally:
            server.JOBS.pop("t2", None)


# ---------------------------------------------------------------------------------------------------------------------
# py_ewr behaviour the wrappers rely on
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.slow
class TestEwrEvalAgainstPyEwr:
    G = "416001"

    @pytest.fixture(autouse=True)
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "STORE", tmp_path)
        server.FLOWS.clear()
        server._EVAL_CACHE.clear()
        yield
        server.FLOWS.clear()
        server._EVAL_CACHE.clear()

    def put(self, flow, idx):
        pd.DataFrame({self.G: np.asarray(flow, dtype=float)}, index=pd.DatetimeIndex(idx, name="Date")).to_csv(server.STORE / "a.csv")

    def test_unknown_ewr_and_planning_unit(self):
        self.put(np.ones(400), pd.date_range("2000-07-01", periods=400))
        with pytest.raises(ValueError, match="not defined at gauge"):
            server.ewr_eval("a", self.G, "NOPE")
        with pytest.raises(ValueError, match="not defined at gauge"):
            server.ewr_eval("a", self.G, "BK1", "No such planning unit")

    def test_parameters_come_from_the_table_and_results_are_cached(self):
        idx = pd.date_range("2000-07-01", "2010-06-30")
        self.put(np.full(len(idx), 100.0), idx)
        ev = server.ewr_eval("a", self.G, "BK1")
        assert (ev["thr"], ev["dur"], ev["min_spell"], ev["sm"], ev["em"], ev["ystart"]) == (7900.0, 5, 5, 7, 6, 7)
        assert ev["mrip_days"] == 1460  # 4 years x 365
        assert server.ewr_eval("a", self.G, "BK1") is ev  # second call served from the cache
        assert list(ev["years"].index) == list(range(2000, 2010))
        server.forget_cached("a")
        assert server.ewr_eval("a", self.G, "BK1") is not ev

    def test_one_clear_event_is_counted_exactly(self):
        # BK1: >= 7,900 ML/d for at least 5 days. One 6-day event in WY2003 (Jul 2002 - Jun 2003).
        idx = pd.date_range("2000-07-01", "2010-06-30")
        f = np.full(len(idx), 100.0)
        f[(idx >= "2002-09-10") & (idx <= "2002-09-15")] = 10000.0
        self.put(f, idx)
        out = server.ewr_years({"base": "a", "gauge": self.G, "ewr": "BK1"})
        sc = out["scenarios"]["base"]
        assert [e for e in sc["events"]] == [
            {"wy": 2002, "start": "2002-09-10", "end": "2002-09-15", "days": 6, "qual": 6, "vol": 60000.0, "peak": 10000.0, "ok": True}
        ]
        y = dict(zip(sc["years"]["wy"], sc["years"]["achieved"], strict=False))
        assert sum(y.values()) == 1 and y[2002] == 1
        assert sc["years"]["complete"] == [1] * 10
        # gaps: record start to the event (2000-07-01 .. 2002-09-09), then event end to record end
        first, second = sc["gaps"][0], sc["gaps"][-1]
        assert math.isfinite(sc["etp"]) and 0 <= sc["etp"] <= 1
        assert first["start"] == "2000-07-01" and first["boundary"] and second["end"] == "2010-06-30" and second["boundary"]
        assert sum(g["days"] for g in sc["gaps"]) == len(idx) - 6  # the gaps plus the 6-day event tile the record
        assert out["pu"] and out["thr"] == 7900.0 and out["mrip_days"] == 1460

    def test_event_volume_sums_flows_over_the_event(self):
        idx = pd.date_range("2000-07-01", "2002-06-30")
        f = np.full(len(idx), 100.0)
        f[(idx >= "2000-10-01") & (idx <= "2000-10-05")] = [8000, 9000, 10000, 9000, 8000]
        self.put(f, idx)
        ev = server.ewr_years({"base": "a", "gauge": self.G, "ewr": "BK1"})["scenarios"]["base"]["events"]
        assert len(ev) == 1 and ev[0]["vol"] == 44000.0 and ev[0]["peak"] == 10000.0 and ev[0]["days"] == 5
