"""Exact-value tests for the numerical helpers and chart-data builders in src/server.py that do not need py_ewr.

Every expected number is worked out by hand in the comment next to it. Tolerances (pytest.approx) are only used where the value passes
through float32 (achievement_frequencies) and are stated explicitly.
"""

import http.client
import json
import math
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import server

G = "400001"


# ---------------------------------------------------------------------------------------------------------------------
# a scratch store of scenario flows
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirect the scenario store to a temporary folder and clear the caches before and after."""
    monkeypatch.setattr(server, "STORE", tmp_path)
    server.FLOWS.clear()
    server._EVAL_CACHE.clear()
    yield tmp_path
    server.FLOWS.clear()
    server._EVAL_CACHE.clear()


def put(store_dir: Path, sid: str, idx, flow, gauge: str = G) -> None:
    pd.DataFrame({gauge: np.asarray(flow, dtype=float)}, index=pd.DatetimeIndex(idx, name="Date")).to_csv(store_dir / f"{sid}.csv")


# ---------------------------------------------------------------------------------------------------------------------
# scalar helpers
# ---------------------------------------------------------------------------------------------------------------------
class TestScalarHelpers:
    @pytest.mark.parametrize(
        ("x", "expected"),
        [
            (0, 100),  # nothing to scale: default axis
            (-5, 100),
            (None, 100),
            (7, 7),  # below 100 the step is 1
            (0.5, 1),
            (99.5, 100),
            (123, 130),  # step 10 once x has 3 digits: ceil(12.3) * 10
            (1000, 1000),  # already on a step
            (1001, 1100),  # step 100
            (12345, 13000),  # step 1000
        ],
    )
    def test_nice_ceiling(self, x, expected):
        assert server.nice_ceiling(x) == expected

    def test_nice_ceiling_never_rounds_down(self):
        for x in [1.0, 9.99, 10.01, 55.5, 100.0, 101.0, 999.0, 5432.1, 98765.4, 1.234e6]:
            assert server.nice_ceiling(x) >= x

    @pytest.mark.parametrize(
        ("vals", "x", "expected"),
        [
            ([1, 2, 3, 4], 3, 50.0),  # 3 and 4
            ([1, 2, 3, 4], 2, 75.0),  # equal counts as exceeded
            ([1, 2, 3, 4], 2.5, 50.0),
            ([1, 2, 3, 4], 0, 100.0),
            ([1, 2, 3, 4], 5, 0.0),
            ([1, 1, 1, 1], 1, 100.0),
            ([0, 0, 5, 5], 0, 100.0),  # zero flows count at threshold 0
            ([0, 0, 5, 5], 0.001, 50.0),
        ],
    )
    def test_exceed_pct(self, vals, x, expected):
        assert server.exceed_pct(np.array(vals, dtype=float), x) == expected

    def test_exceed_pct_vectorised_matches_scalar(self):
        v = np.sort(np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0]))
        xs = np.array([0.0, 1.0, 2.5, 5.0, 9.0, 10.0])
        assert server.exceed_pct(v, xs).tolist() == [server.exceed_pct(v, float(x)) for x in xs]
        assert server.exceed_pct(v, xs).tolist() == [100.0, 100.0, 62.5, 37.5, 12.5, 0.0]

    @pytest.mark.parametrize(("date", "wy"), [("2000-06-30", 1999), ("2000-07-01", 2000), ("2000-12-31", 2000), ("2001-01-01", 2000), ("2001-06-30", 2000)])
    def test_water_year_label_is_the_starting_calendar_year(self, date, wy):
        assert server._water_year(pd.Timestamp(date)) == wy

    @pytest.mark.parametrize(("wy", "leap"), [(1999, True), (2000, False), (2003, True), (2004, False), (1899, False), (1599, True)])
    def test_feb29_belongs_to_the_water_year_that_ends_in_a_leap_year(self, wy, leap):
        assert server._feb29(wy) is leap

    @pytest.mark.parametrize(
        ("wy_label", "sm", "em", "days"),
        [
            (2001, 7, 6, 365),  # whole Jul 2000 - Jun 2001
            (2000, 7, 6, 366),  # includes 29 Feb 2000
            (2001, 1, 12, 365),
            (2001, 7, 9, 92),  # Jul 31 + Aug 31 + Sep 30
            (2000, 12, 2, 91),  # wraps the year end: Dec 31 + Jan 31 + Feb 29
            (2001, 12, 2, 90),  # Dec 31 + Jan 31 + Feb 28
            (2000, 2, 2, 29),
            (2001, 2, 2, 28),
            (2001, 6, 6, 30),
        ],
    )
    def test_window_days(self, wy_label, sm, em, days):
        assert server._window_days(wy_label, sm, em) == days

    def test_json_safe_replaces_non_finite_floats_recursively(self):
        out = server._json_safe({"a": float("nan"), "b": [1.5, float("inf"), -float("inf")], "c": ({"d": np.float64("nan")},), "e": 3, "f": None, "g": True})
        assert out == {"a": None, "b": [1.5, None, None], "c": [{"d": None}], "e": 3, "f": None, "g": True}
        json.dumps(out, allow_nan=False)  # strict JSON now

    def test_json_safe_keeps_zero_and_negative_values(self):
        assert server._json_safe([0.0, -0.0, -3.25]) == [0.0, -0.0, -3.25]


class TestRequestOriginChecks:
    ADDR = ("127.0.0.1", 8792)

    @pytest.mark.parametrize(
        ("origin", "host", "allowed"),
        [
            (None, "127.0.0.1:8792", True),  # same-origin fetches from non-browser clients send no Origin
            ("http://127.0.0.1:8792", "127.0.0.1:8792", True),
            ("http://localhost:8792", "localhost:8792", True),  # loopback aliases
            ("http://[::1]:8792", "[::1]:8792", True),
            ("http://evil.example", "127.0.0.1:8792", False),
            ("http://127.0.0.1:9999", "127.0.0.1:8792", False),  # another local port
            (None, "evil.example:8792", False),  # DNS-rebinding Host
            ("null", "127.0.0.1:8792", False),
            ("http://127.0.0.1:8792/path", "127.0.0.1:8792", False),
            ("ftp://127.0.0.1:8792", "127.0.0.1:8792", False),
        ],
    )
    def test_loopback_bind(self, origin, host, allowed):
        assert server._state_change_allowed(origin, host, self.ADDR) is allowed

    def test_authority_parsing(self):
        assert server._authority("localhost:8792") == ("localhost", 8792)
        assert server._authority("EXAMPLE.com.", 80) == ("example.com", 80)
        assert server._authority("[::1]:80") == ("::1", 80)
        assert server._authority("") is None and server._authority(None) is None
        assert server._authority("user@host") is None
        assert server._authority("host/path") is None

    def test_loopback_host(self):
        assert server._loopback_host("localhost") and server._loopback_host("127.0.0.2") and server._loopback_host("::1")
        assert not server._loopback_host("example.com") and not server._loopback_host("192.168.0.1")


# ---------------------------------------------------------------------------------------------------------------------
# achievement_frequencies (the heat map engine)
# ---------------------------------------------------------------------------------------------------------------------
IDX4 = pd.date_range("2000-07-01", "2004-06-30")  # WY2001, 2002, 2003 (365 days each) and WY2004 (366 days, 29 Feb 2004)


def four_year_flows() -> np.ndarray:
    f = np.zeros(len(IDX4))
    f[[10, 50, 90]] = 100  # WY2001: three separate days at 100
    f[365 + 5 : 365 + 8] = 100  # WY2002 (starts at offset 365): three consecutive days at 100
    f[730 + 20] = 500  # WY2003 (offset 730): one day at 500
    return f  # WY2004 stays 0


THR = np.array([50.0, 100.0, 101.0, 400.0, 0.0])
DUR = np.array([1, 3])


class TestAchievementFrequencies:
    def test_year_offsets_used_by_the_fixture(self):
        wy = np.where(IDX4.month >= 7, IDX4.year + 1, IDX4.year)
        assert [int((wy == w).sum()) for w in (2001, 2002, 2003, 2004)] == [365, 365, 365, 366]
        assert IDX4[365].isoformat()[:10] == "2001-07-01" and IDX4[730].isoformat()[:10] == "2002-07-01"

    def test_non_consecutive_days_hand_computed(self):
        # d-th largest flow of each year. d=1: [100, 100, 500, 0]; d=3: [100, 100, 0, 0]. A year succeeds when that value >= threshold.
        out, n = server.achievement_frequencies(four_year_flows().reshape(-1, 1), IDX4, THR, DUR, 7, 6)
        assert n == 4 and out.shape == (1, 5, 2)
        assert out[0, :, 0].tolist() == [75.0, 75.0, 25.0, 25.0, 100.0]  # duration 1 at thresholds 50, 100, 101, 400, 0
        assert out[0, :, 1].tolist() == [50.0, 50.0, 0.0, 0.0, 100.0]  # duration 3

    def test_consecutive_days_hand_computed(self):
        # WY2001's three 100-ML/d days are not adjacent, so for d=3 only WY2002 has 3 consecutive days >= 50 / >= 100.
        out, n = server.achievement_frequencies(four_year_flows().reshape(-1, 1), IDX4, THR, DUR, 7, 6, consecutive=True)
        assert n == 4
        assert out[0, :, 0].tolist() == [75.0, 75.0, 25.0, 25.0, 100.0]  # d=1 identical to the non-consecutive rule
        assert out[0, :, 1].tolist() == [25.0, 25.0, 0.0, 0.0, 100.0]

    def test_threshold_zero_is_always_met_even_by_zero_flow(self):
        out, n = server.achievement_frequencies(np.zeros((len(IDX4), 1)), IDX4, np.array([0.0, 1e-9]), np.array([1, 365]), 7, 6)
        assert n == 4
        assert out[0, 0, :].tolist() == [100.0, 100.0]  # >= 0 holds on every day
        assert out[0, 1, :].tolist() == [0.0, 0.0]

    def test_duration_longer_than_the_window_is_never_met(self):
        flows = np.full((len(IDX4), 1), 1000.0)
        out, _ = server.achievement_frequencies(flows, IDX4, np.array([1.0]), np.array([365, 366, 400]), 7, 9)  # window Jul-Sep is only 92 days
        assert out[0, 0, :].tolist() == [0.0, 0.0, 0.0]
        out, _ = server.achievement_frequencies(flows, IDX4, np.array([1.0]), np.array([92]), 7, 9)
        assert out[0, 0, 0] == 100.0  # exactly 92 days available: met

    def test_wrapping_window_ignores_days_outside_it(self):
        f = np.zeros(len(IDX4))
        f[IDX4.month == 7] = 900  # July only: outside a Dec-Feb window
        out, n = server.achievement_frequencies(f.reshape(-1, 1), IDX4, np.array([500.0]), np.array([1]), 12, 2)
        assert n == 4 and out[0, 0, 0] == 0.0
        out, _ = server.achievement_frequencies(f.reshape(-1, 1), IDX4, np.array([500.0]), np.array([1]), 7, 7)
        assert out[0, 0, 0] == 100.0

    def test_a_missing_day_inside_the_window_removes_that_year_from_the_denominator(self):
        f = four_year_flows()
        f[730 + 100] = np.nan  # WY2003 incomplete -> excluded
        out, n = server.achievement_frequencies(f.reshape(-1, 1), IDX4, THR, np.array([1]), 7, 6)
        assert n == 3
        # d=1 over WY2001, 2002, 2004 = [100, 100, 0]: thr 50 -> 2/3; thr 101 -> 0; thr 0 -> 100
        assert out[0, 0, 0] == pytest.approx(200 / 3, abs=1e-4)  # float32 output
        assert out[0, 2, 0] == 0.0 and out[0, 4, 0] == 100.0

    def test_a_missing_day_outside_the_window_does_not_matter(self):
        f = four_year_flows()
        f[IDX4 == "2001-12-25"] = np.nan  # WY2002; the window below is Jul-Sep
        _, n = server.achievement_frequencies(f.reshape(-1, 1), IDX4, np.array([50.0]), np.array([1]), 7, 9)
        assert n == 4

    def test_partial_first_and_last_years_are_not_counted(self):
        idx = pd.date_range("2000-10-01", "2003-03-31")  # WY2001 starts before the record, WY2003 ends after it: only WY2002 is whole
        out, n = server.achievement_frequencies(np.ones((len(idx), 1)), idx, np.array([0.5, 2.0]), np.array([1]), 7, 6)
        assert n == 1
        assert out[0, :, 0].tolist() == [100.0, 0.0]

    def test_no_complete_year_gives_nan_and_zero_years(self):
        idx = pd.date_range("2000-10-01", "2001-03-31")
        out, n = server.achievement_frequencies(np.ones((len(idx), 1)), idx, np.array([0.5, 5.0]), np.array([1, 2]), 7, 6)
        assert n == 0 and np.isnan(out).all() and out.shape == (1, 2, 2)

    def test_all_missing_flows_give_nan(self):
        out, n = server.achievement_frequencies(np.full((len(IDX4), 1), np.nan), IDX4, np.array([1.0]), np.array([1]), 7, 6)
        assert n == 0 and np.isnan(out).all()

    def test_empty_input_is_an_error_not_a_silent_zero(self):
        with pytest.raises(ValueError):
            server.achievement_frequencies(np.empty((0, 1)), pd.DatetimeIndex([]), np.array([1.0]), np.array([1]), 7, 6)

    def test_scenarios_share_one_set_of_years(self):
        a = four_year_flows()
        b = a.copy()
        b[:365] = np.nan  # scenario b has no WY2001
        out, n = server.achievement_frequencies(np.column_stack([a, b]), IDX4, np.array([50.0]), np.array([1]), 7, 6)
        assert n == 3  # WY2002-2004 for both
        # a over those years: d=1 = [100, 500, 0] -> 2/3; b identical there
        assert out[0, 0, 0] == pytest.approx(200 / 3, abs=1e-4) and out[1, 0, 0] == pytest.approx(200 / 3, abs=1e-4)

    @pytest.mark.parametrize("consecutive", [False, True])
    def test_properties_on_random_flows(self, consecutive):
        rng = np.random.default_rng(20240607)
        idx = pd.date_range("1990-07-01", "2002-06-30")  # 12 whole water years
        flows = rng.lognormal(mean=3.0, sigma=1.5, size=(len(idx), 2))
        thr = np.array([0.0, 5.0, 20.0, 80.0, 300.0, 5000.0, 1e9])
        dur = np.array([1, 2, 5, 10, 40, 90])
        out, n = server.achievement_frequencies(flows, idx, thr, dur, 7, 6, consecutive)
        assert n == 12
        assert np.all((out >= 0) & (out <= 100))
        assert np.all(np.diff(out, axis=1) <= 0)  # a higher threshold is never easier
        assert np.all(np.diff(out, axis=2) <= 0)  # a longer duration is never easier
        assert np.all(out[:, 0, :] == 100.0) and np.all(out[:, -1, :] == 0.0)
        years = out * n / 100  # a share of whole years only
        assert np.allclose(years, np.round(years), atol=1e-4)

    def test_consecutive_never_exceeds_non_consecutive(self):
        rng = np.random.default_rng(7)
        idx = pd.date_range("1990-07-01", "1999-06-30")
        flows = rng.lognormal(3.0, 1.2, size=(len(idx), 1))
        thr, dur = np.linspace(0, 400, 21), np.array([1, 3, 7, 30])
        loose, _ = server.achievement_frequencies(flows, idx, thr, dur, 7, 6, False)
        tight, _ = server.achievement_frequencies(flows, idx, thr, dur, 7, 6, True)
        assert np.all(tight <= loose + 1e-4)


# ---------------------------------------------------------------------------------------------------------------------
# gaps between successful events (ewr_gaps) with a stand-in for the py_ewr output
# ---------------------------------------------------------------------------------------------------------------------
def gap_ev(flow: pd.Series, inter_rows: list[tuple[str, str]], mrip: int, successes: int = 1) -> dict[str, Any]:
    inter = pd.DataFrame(inter_rows, columns=["startDate", "endDate"]).astype({"startDate": "datetime64[ns]", "endDate": "datetime64[ns]"})
    succ = pd.DataFrame({"startDate": pd.date_range("2000-01-01", periods=successes)})
    return {"flow": flow, "mrip_days": mrip, "inter": inter, "succ": succ}


class TestDroughtWindows:
    def test_window_metrics_counts_only_in_window_breach_days_of_a_gap_that_began_before(self):
        # record 1990-07-01 .. 2000-06-30; one interior gap 1993-01-01 .. 1997-12-31 (1,826 days), MRIP 365: beyond it from 1994-01-01
        # window = water years 1995-96 .. 1996-97 (1995-07-01 .. 1997-06-30, 731 days), wholly inside the breach: et = 1
        gaps = [{"start": "1993-01-01", "end": "1997-12-31", "days": 1826, "censored": False, "boundary": False, "excess": 1461}]
        years = {"wy": list(range(1990, 2000)), "achieved": [1, 1, 0, 0, 0, 0, 0, 0, 1, 1], "complete": [1] * 10, "dr": [1.5] * 3 + [0.2] * 7, "ystart": 7}
        out = server.window_metrics(years, gaps, 365, 50, "1995-07-01", "1997-06-30", record=("1990-07-01", "2000-06-30"))
        assert out["n"] == 2 and out["fr"] == 0.0 and out["et"] == 1.0 and out["dr"] == 0.2
        assert out["sev"] == round(1826 / 365, 2)  # the gap from its true start, not cut at the window
        # a window that starts before the breach: 1993-07-01 .. 1994-06-30 (365 days); beyond MRIP from 1994-01-01 = 181 days
        out = server.window_metrics(years, gaps, 365, 50, "1993-07-01", "1994-06-30", record=("1990-07-01", "2000-06-30"))
        assert out["et"] == round(181 / 365, 4) and out["n"] == 1 and out["fr"] == 0.0
        # a window after the gap with achieved years: FR = (2 / 2) / 0.5
        out = server.window_metrics(years, gaps, 365, 50, "1998-07-01", "2000-06-30", record=("1990-07-01", "2000-06-30"))
        assert (out["n"], out["fr"], out["et"], out["sev"]) == (2, 2.0, 0.0, None)

    def test_window_outside_the_record_and_censored_days(self):
        gaps = [{"start": "1990-07-01", "end": "1991-06-30", "days": 365, "censored": True, "boundary": True, "excess": None}]
        years = {"wy": [1990], "achieved": [0], "complete": [0], "dr": [None], "ystart": 7}
        out = server.window_metrics(years, gaps, 100, 50, "1895-07-01", "1903-06-30", record=("1990-07-01", "1991-06-30"))
        assert out == {"n": 0, "fr": None, "et": None, "sev": None, "dr": None}
        out = server.window_metrics(years, gaps, 100, 50, "1990-07-01", "1991-06-30", record=("1990-07-01", "1991-06-30"))
        assert out["et"] is None and out["sev"] is None  # every day of the window is inside a censored gap

    def test_window_dates_follow_the_assessment_year(self):
        from drought_windows import DROUGHTS, window_dates

        assert DROUGHTS["mil"] == ("Millennium", 1996, 2009)
        assert [str(d) for d in window_dates("mil")] == ["1996-07-01", "2010-06-30"]
        assert [str(d) for d in window_dates("tin", 6)] == ["2017-06-01", "2020-05-31"]  # a Jun-Nov EWR's year starts in June
        assert server.assessment_start(6, 11) == 6 and server.assessment_start(7, 6) == 7 and server.assessment_start(9, 3) == 7


class TestDurationRatio:
    YR = pd.DataFrame({"X_maxEventDays": [10, 30, 0, 50], "X_totalEventDays": [40, 30, 5, 200]}, index=[2000, 2001, 2002, 2003])

    def test_longest_event_over_the_required_duration_capped_at_2(self):
        assert server.dr_years(self.YR, "X", 20, False, 1) == [0.5, 1.5, 0.0, 2.0]  # 50 / 20 = 2.5 -> 2

    def test_low_flow_ewrs_use_the_days_at_or_above_the_threshold(self):
        assert server.dr_years(self.YR, "X", 100, True, 1) == [0.4, 0.3, 0.05, 2.0]  # totalEventDays / 100; 200 / 100 -> 2

    def test_k_events_a_year_use_the_kth_longest(self):
        events = {2000: [[1] * 12, [1] * 4, [1] * 9], 2001: [[1] * 30], 2002: [], 2003: [[1] * 50, [1] * 50]}
        assert server.dr_years(self.YR, "X", 10, False, 2, events) == [0.9, 0.0, 0.0, 2.0]  # second longest: 9, none, none, 50

    def test_no_duration_and_the_summary(self):
        assert server.dr_years(self.YR, "X", None, False, 1) == [None] * 4
        # median over complete years; near misses = 0.5 <= DR < 1 among the years with DR < 1
        assert server.year_summary([0.5, 1.5, 0.0, 2.0, 0.7], [1, 1, 1, 1, 0]) == {"dr": 1.0, "nm": [1, 2]}
        assert server.year_summary([None], [1]) == {"dr": None, "nm": None}

    def test_volume_ratio_is_the_best_accumulated_volume_in_the_window(self):
        idx = pd.date_range("2000-07-01", "2002-06-30")
        flow = pd.Series(10.0, index=idx)
        flow[(idx >= "2000-09-01") & (idx <= "2000-09-03")] = 100.0  # 3-day sum 300 inside a Aug-Oct window in water year 2000
        assert server.vr_years(flow, [2000, 2001], 7, 8, 10, 3, 150) == [2.0, 0.2]  # 300 / 150 -> 2; 30 / 150
        assert server.vr_years(flow, [2000], 7, 8, 10, None, 150) == [None]

    @pytest.mark.parametrize("ystart", [1, 7])
    def test_volume_accumulation_stays_within_each_assessment_year(self, ystart):
        boundary = pd.Timestamp(2001, ystart, 1)
        idx = pd.date_range(boundary - pd.Timedelta(days=2), periods=5)
        flow = pd.Series([100.0, 100.0, 1.0, 1.0, 1.0], index=idx)
        # The preceding year has fewer than 3 days; the new year's largest 3-day sum is 3, not 201 across the boundary.
        assert server.vr_years(flow, [2000, 2001], ystart, ystart, 12 if ystart == 1 else 6, 3, 100) == [None, 0.03]

    def test_complete_years_need_every_day_of_the_timing_window(self):
        idx = pd.date_range("2000-07-01", "2003-06-30")
        flow = pd.Series(1.0, index=idx)
        flow[pd.Timestamp("2001-12-25")] = np.nan  # outside a Jul-Sep window, inside a Nov-Jan one
        assert server.complete_years(flow, [2000, 2001, 2002, 2003], 7, 7, 9) == [1, 1, 1, 0]  # 2003 is beyond the record
        assert server.complete_years(flow, [2000, 2001, 2002], 7, 11, 1) == [1, 0, 1]


class TestMcNemar:
    def test_exact_two_sided_p_by_hand(self):
        # 8 gained, 2 lost: P(X <= 2), X ~ Bin(10, 1/2) = (1 + 10 + 45) / 1024 = 56 / 1024; two-sided: 112 / 1024 = 0.109375
        assert server.mcnemar_p(8, 2) == 0.1094
        assert server.mcnemar_p(2, 8) == 0.1094  # symmetric
        assert server.mcnemar_p(6, 0) == round(2 / 64, 4)  # 0.03125
        assert server.mcnemar_p(3, 3) == 1.0  # capped at 1
        assert server.mcnemar_p(0, 0) is None  # no year differs: nothing to test


class TestEwrGaps:
    IDX = pd.date_range("2000-01-01", "2000-12-31")  # 366 days

    def flow(self) -> pd.Series:
        return pd.Series(1.0, index=self.IDX)

    def test_excess_days_and_etp_by_hand(self):
        # gap 1: 1 Jan - 10 Mar 2000 = 31 + 29 + 10 = 70 days -> below the 100-day limit, excess 0
        # gap 2: 1 Apr - 31 Dec = 30 + 31 + 30 + 31 + 31 + 30 + 31 + 30 + 31 = 275 days -> excess 175
        # ETP = 175 / 366 days of record = 0.478142 -> 0.4781
        ev = gap_ev(self.flow(), [("2000-01-01", "2000-03-10"), ("2000-04-01", "2000-12-31")], mrip=100)
        gaps, etp = server.ewr_gaps(ev)
        assert [(g["days"], g["excess"], g["censored"], g["boundary"]) for g in gaps] == [(70, 0, False, True), (275, 175, False, True)]
        assert etp == 0.4781

    def test_interior_gap_is_not_a_boundary_gap(self):
        ev = gap_ev(self.flow(), [("2000-03-01", "2000-06-30")], mrip=30)
        gaps, etp = server.ewr_gaps(ev)
        assert gaps[0]["boundary"] is False and gaps[0]["days"] == 122  # Mar 31 + Apr 30 + May 31 + Jun 30
        assert gaps[0]["excess"] == 92 and etp == round(92 / 366, 4)

    def test_a_gap_with_a_missing_day_is_censored_and_leaves_both_numerator_and_denominator(self):
        f = self.flow()
        f[pd.Timestamp("2000-02-01")] = np.nan
        ev = gap_ev(f, [("2000-01-01", "2000-03-10"), ("2000-04-01", "2000-12-31")], mrip=100)
        gaps, etp = server.ewr_gaps(ev)
        assert gaps[0]["censored"] and gaps[0]["excess"] is None
        # excess 175 over the 366 - 70 = 296 days that are not inside the censored gap: 0.591216 -> 0.5912
        assert etp == 0.5912

    def test_no_successful_event_is_one_gap_over_the_whole_record(self):
        ev = gap_ev(self.flow(), [], mrip=100, successes=0)
        gaps, etp = server.ewr_gaps(ev)
        assert len(gaps) == 1
        assert (gaps[0]["start"], gaps[0]["end"], gaps[0]["days"], gaps[0]["excess"], gaps[0]["no_success"]) == ("2000-01-01", "2000-12-31", 366, 266, True)
        assert etp == round(266 / 366, 4)  # 0.7268

    def test_no_maximum_inter_event_period_means_no_etp_and_no_excess(self):
        ev = gap_ev(self.flow(), [("2000-01-01", "2000-12-31")], mrip=0)
        gaps, etp = server.ewr_gaps(ev)
        assert gaps[0]["excess"] == 0 and etp is None

    def test_whole_record_censored_leaves_nothing_to_count(self):
        f = self.flow()
        f.iloc[100] = np.nan
        ev = gap_ev(f, [], mrip=100, successes=0)
        gaps, etp = server.ewr_gaps(ev)
        assert gaps[0]["censored"] and etp is None

    def test_a_gap_equal_to_the_limit_has_no_excess(self):
        ev = gap_ev(self.flow(), [("2000-01-01", "2000-04-09")], mrip=100)  # 31 + 29 + 31 + 9 = 100 days
        gaps, etp = server.ewr_gaps(ev)
        assert gaps[0]["days"] == 100 and gaps[0]["excess"] == 0 and etp == 0.0

    def test_severity_by_hand(self):
        # gaps of 400 (from the record start), 1,200 (interior) and 300 days (to the record end), MRIP 365:
        # severity = longest interior gap / MRIP = 1200 / 365 = 3.288 -> 3.29; episodes = gaps beyond 365 days = 2
        idx = pd.date_range("2000-01-01", periods=1950)  # days 0-399 gap, 400-425 event, 426-1625 gap, 1626-1649 event, 1650-1949 gap
        ev = gap_ev(pd.Series(1.0, index=idx), [(idx[0], idx[399]), (idx[426], idx[1625]), (idx[1650], idx[-1])], mrip=365)
        gaps, _ = server.ewr_gaps(ev)
        assert [(g["days"], g["boundary"]) for g in gaps] == [(400, True), (1200, False), (300, True)]
        assert server.gap_severity(gaps, 365) == {"sev": 3.29, "sev_b": 3.29, "eps": 2}
        # boundary gaps are reported separately: a longer gap from the start of the record raises sev_b only
        long_start = [{**gaps[0], "days": 1500, "excess": 1135}, *gaps[1:]]
        assert server.gap_severity(long_start, 365) == {"sev": 3.29, "sev_b": round(1500 / 365, 2), "eps": 2}
        # only boundary gaps: severity falls back to them; censored gaps and a missing maximum give nothing
        assert server.gap_severity([gaps[0]], 365)["sev"] == round(400 / 365, 2)
        assert server.gap_severity([{**gaps[1], "censored": True, "excess": None}], 365) == {"sev": None, "sev_b": None, "eps": None}
        assert server.gap_severity(gaps, 0) == {"sev": None, "sev_b": None, "eps": None}

    def test_interevents_lists_only_gaps_beyond_the_limit(self, monkeypatch):
        ev = gap_ev(self.flow(), [("2000-01-01", "2000-03-10"), ("2000-04-01", "2000-12-31")], mrip=100)
        monkeypatch.setattr(server, "ewr_eval", lambda *a, **k: ev)
        out = server.interevents({"scn": "a", "gauge": G, "ewr": "X"})
        assert out["mrip_days"] == 100 and out["n_gaps"] == 2 and out["n_censored"] == 0
        assert out["exceed"] == [{"start": "2000-04-01", "end": "2000-12-31", "days": 275}]
        assert out["maxint_years"] == pytest.approx(100 / 365, abs=1e-12)

    def test_interevents_without_a_limit(self, monkeypatch):
        monkeypatch.setattr(server, "ewr_eval", lambda *a, **k: gap_ev(self.flow(), [], mrip=0))
        assert server.interevents({"scn": "a", "gauge": G, "ewr": "X"}) == {"mrip_days": 0, "exceed": [], "n_gaps": 0}


# ---------------------------------------------------------------------------------------------------------------------
# runs of consecutive days
# ---------------------------------------------------------------------------------------------------------------------
class TestRunsByYear:
    IDX = pd.date_range("2000-07-01", "2001-06-30")  # one whole water year, 365 days

    def test_runs_counts_and_censoring(self):
        hit = np.zeros(len(self.IDX), dtype=bool)
        hit[0:3] = True  # starts on the first day of the record: censored
        hit[10:12] = True
        hit[364] = True  # last day of the record: censored
        years, runs = server._runs_by_year(pd.Series(1.0, index=self.IDX), hit)
        assert [(r["start"], r["end"], r["days"], r["censored"]) for r in runs] == [
            ("2000-07-01", "2000-07-03", 3, True),
            ("2000-07-11", "2000-07-12", 2, False),
            ("2001-06-30", "2001-06-30", 1, True),
        ]
        assert years == {"wy": [2000], "complete": [1], "days": [6], "longest": [3], "n": [3]}

    def test_a_missing_day_breaks_a_run_and_voids_the_year(self):
        s = pd.Series(1.0, index=self.IDX)
        s.iloc[20] = np.nan
        hit = np.zeros(len(self.IDX), dtype=bool)
        hit[19] = hit[21] = True
        hit[20] = True  # a "hit" on a missing day must be ignored
        years, runs = server._runs_by_year(s, hit)
        assert [(r["days"], r["censored"]) for r in runs] == [(1, True), (1, True)]
        assert years["complete"] == [0] and years["days"] == [None] and years["longest"] == [None] and years["n"] == [None]

    def test_no_hits(self):
        years, runs = server._runs_by_year(pd.Series(1.0, index=self.IDX), np.zeros(len(self.IDX), dtype=bool))
        assert runs == [] and years == {"wy": [2000], "complete": [1], "days": [0], "longest": [0], "n": [0]}

    def test_a_run_over_the_year_boundary_counts_once_but_is_split_per_year(self):
        idx = pd.date_range("2000-07-01", "2002-06-30")
        hit = (idx >= "2001-06-28") & (idx <= "2001-07-02")  # 5 days: 28, 29, 30 Jun (WY2000) and 1, 2 Jul (WY2001)
        years, runs = server._runs_by_year(pd.Series(1.0, index=idx), np.asarray(hit))
        assert len(runs) == 1 and runs[0]["days"] == 5 and runs[0]["wy"] == 2000 and not runs[0]["censored"]
        assert years["days"] == [3, 2] and years["longest"] == [3, 2] and years["n"] == [1, 0]
        assert sum(years["days"]) == runs[0]["days"]  # days reconcile with the run length

    def test_short_record_has_no_complete_year(self):
        idx = pd.date_range("2000-07-01", "2000-12-31")
        years, _ = server._runs_by_year(pd.Series(1.0, index=idx), np.ones(len(idx), dtype=bool))
        assert years["complete"] == [0] and years["days"] == [None]


# ---------------------------------------------------------------------------------------------------------------------
# stored flows
# ---------------------------------------------------------------------------------------------------------------------
class TestStoredFlows:
    def test_scenario_ids_cannot_escape_the_store(self, store):
        (store.parent / "secret.csv").write_text("Date,400001\n2000-01-01,1\n")
        with pytest.raises(ValueError, match="not on the server"):
            server.load_flows("../secret")  # sanitised to "secret": not in the store

    def test_unknown_scenario_and_gauge(self, store):
        with pytest.raises(ValueError, match="not on the server"):
            server.load_flows("nope")
        put(store, "a", pd.date_range("2000-01-01", periods=3), [1, 2, 3])
        with pytest.raises(ValueError, match="no flow series"):
            server.gauge_series("a", "999999")

    def test_daily_series_is_trimmed_to_observed_days_and_reindexed_without_filling(self, store):
        idx = pd.DatetimeIndex(["2000-01-02", "2000-01-04", "2000-01-05", "2000-01-06"])  # 1 Jan absent, 3 Jan absent
        put(store, "a", idx, [np.nan, 5.0, 6.0, np.nan])  # NaN at both ends
        s = server.daily_series("a", G)
        assert list(s.index.strftime("%Y-%m-%d")) == ["2000-01-04", "2000-01-05"]
        put(store, "b", pd.DatetimeIndex(["2000-01-01", "2000-01-04"]), [1.0, 4.0])
        server.FLOWS.clear()
        s = server.daily_series("b", G)
        assert len(s) == 4 and s.isna().tolist() == [False, True, True, False]  # days 2 and 3 stay missing

    def test_daily_series_all_missing_is_an_error(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=3), [np.nan] * 3)
        with pytest.raises(ValueError, match="no flows"):
            server.daily_series("a", G)

    def test_zero_flows_are_kept(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=3), [0, 0, 0])
        assert server.daily_series("a", G).tolist() == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------------------------------------------------
# chart payloads: curves, heat map
# ---------------------------------------------------------------------------------------------------------------------
class TestCurves:
    def test_flow_duration_curve_values_and_marks(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=10), range(1, 11))  # flows 1..10
        out = server.curves({"base": "a", "gauge": G, "kind": "fdc", "thr": "5,10,11"})
        assert out["kind"] == "fdc" and len(out["p"]) == len(out["base"]) == 401
        assert out["p"][0] == 0.0 and out["p"][-1] == 100.0 and out["p"][200] == 50.0
        assert out["base"][0] == 10.0  # flow exceeded 0% of the time = the maximum
        assert out["base"][-1] == 1.0  # exceeded 100% of the time = the minimum
        assert out["base"][200] == 5.5  # median of 1..10
        assert all(a >= b for a, b in zip(out["base"], out["base"][1:], strict=False))  # non-increasing with exceedance
        # marks: % of days at or above the flow: >= 5 -> 6 of 10 days; >= 10 -> 1 of 10; >= 11 -> 0
        assert out["marks"] == [{"thr": 5.0, "base": 60.0}, {"thr": 10.0, "base": 10.0}, {"thr": 11.0, "base": 0.0}]
        assert "comp" not in out and "diff" not in out

    def test_fdc_with_a_comparison_has_a_consistent_difference(self, store):
        idx = pd.date_range("2000-01-01", periods=10)
        put(store, "a", idx, range(1, 11))
        put(store, "b", idx, range(2, 12))  # always 1 ML/d higher
        out = server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "fdc", "thr": "5"})
        assert out["marks"] == [{"thr": 5.0, "base": 60.0, "comp": 70.0}]
        # 1 ML/d everywhere, give or take the 2-decimal rounding of each of the two series before they are subtracted (<= 0.01)
        assert out["diff"] == [pytest.approx(1.0, abs=0.011)] * 401
        assert out["diff"][0] == 1.0 and out["diff"][-1] == 1.0  # the extremes are exact

    def test_timing_window_restricts_the_days_used(self, store):
        idx = pd.date_range("2000-01-01", "2000-12-31")
        put(store, "a", idx, np.where(idx.month == 3, 100.0, 1.0))
        out = server.curves({"base": "a", "gauge": G, "kind": "fdc", "thr": "50", "sm": "3", "em": "3"})
        assert out["marks"] == [{"thr": 50.0, "base": 100.0}]  # March only
        out = server.curves({"base": "a", "gauge": G, "kind": "fdc", "thr": "50"})
        assert out["marks"][0]["base"] == round(31 / 366 * 100, 3)  # 31 high days of 366 = 8.469

    def test_window_with_no_data_is_an_error(self, store):
        idx = pd.date_range("2000-01-01", "2000-02-29")
        put(store, "a", idx, np.ones(len(idx)))
        with pytest.raises(ValueError, match="no flow data inside that timing window"):
            server.curves({"base": "a", "gauge": G, "kind": "fdc", "sm": "6", "em": "8"})

    def test_differential_flow_duration_curve(self, store):
        idx = pd.date_range("2000-01-01", periods=10)
        put(store, "a", idx, range(1, 11))
        put(store, "b", idx, range(2, 12))
        out = server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "dfdc", "thr": "5"})
        assert len(out["x"]) == len(out["base"]) == len(out["comp"]) == len(out["diff"]) == 500
        assert out["x"][0] == 1.0 and out["x"][-1] == 11.0  # smallest positive flow to the largest flow of either scenario
        assert all(a < b for a, b in zip(out["x"], out["x"][1:], strict=False))  # strictly increasing log axis
        assert out["marks"] == [{"thr": 5.0, "base": 60.0, "comp": 70.0}]
        assert out["base"][0] == 100.0 and out["comp"][0] == 100.0 and out["comp"][-1] == 100 / 10  # at 11: only the comparison's maximum (11) reaches it
        assert out["base"][-1] == 0.0
        assert all(
            abs(d - (c - b)) < 1.5e-3 for d, c, b in zip(out["diff"], out["comp"], out["base"], strict=False)
        )  # rounding of the three series to 3 places
        assert all(-100.0 <= v <= 100.0 for v in out["diff"])

    def test_dfdc_needs_two_scenarios_and_a_positive_flow(self, store):
        idx = pd.date_range("2000-01-01", periods=5)
        put(store, "a", idx, [0, 0, 0, 0, 0])
        put(store, "b", idx, [0, 0, 0, 0, 0])
        with pytest.raises(ValueError, match="two scenarios"):
            server.curves({"base": "a", "gauge": G, "kind": "dfdc"})
        with pytest.raises(ValueError, match="no positive flows"):
            server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "dfdc"})

    def test_unknown_kind(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=5), [1, 2, 3, 4, 5])
        with pytest.raises(ValueError, match="unknown curve kind"):
            server.curves({"base": "a", "gauge": G, "kind": "violin"})

    def test_hydrograph_dates_values_and_volumes(self, store):
        idx = pd.date_range("2000-07-01", "2002-06-30")  # WY2000 and WY2001, both 365 days
        flow = np.full(len(idx), 2.0)
        flow[5] = np.nan
        put(store, "a", idx, flow)
        out = server.curves({"base": "a", "gauge": G, "kind": "hydro"})
        assert len(out["dates"]) == len(out["base"]) == len(out["flow"]) == 730
        assert out["dates"][0] == "2000-07-01" and out["dates"][-1] == "2002-06-30"
        assert out["dates"] == sorted(out["dates"]) and len(set(out["dates"])) == 730  # ascending, one per day
        assert out["base"][5] is None and out["base"][4] == 2.0
        assert out["missing_days"] == 1
        assert out["years"] == [{"wy": 2001, "base": 730.0}]  # WY2000 has a missing day; WY2001: 365 days x 2 ML/d

    def test_hydrograph_volume_of_a_leap_water_year(self, store):
        idx = pd.date_range("2003-07-01", "2004-06-30")  # 366 days
        put(store, "a", idx, np.full(len(idx), 1.5))
        out = server.curves({"base": "a", "gauge": G, "kind": "hydro"})
        assert out["years"] == [{"wy": 2003, "base": 549.0}]  # 366 x 1.5

    def test_hydrograph_flows_are_rounded_to_two_places(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=3), [1.005, 2.344, 0.0])
        out = server.curves({"base": "a", "gauge": G, "kind": "hydro"})
        assert out["base"][1:] == [2.34, 0.0]  # zero stays zero, not null

    def test_monthly_differential_flow_duration(self, store):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        put(store, "a", idx, np.full(len(idx), 10.0))
        put(store, "b", idx, np.full(len(idx), 20.0))
        out = server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "mdfdc"})
        assert out["months"] == [7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]  # water-year order
        assert out["ymax"] == 20 and len(out["flows"]) == 200 and out["flows"][0] == 0.1 and out["flows"][-1] == 20.0
        assert all(len(row) == 12 for row in out["diff"])
        i5, i15 = out["flows"].index(5.0), out["flows"].index(15.0)
        assert out["diff"][i5] == [0.0] * 12  # both scenarios reach 5 ML/d every day
        assert out["diff"][i15] == [100.0] * 12  # only the comparison reaches 15
        assert out["base"][i15] == [0.0] * 12 and out["comp"][i15] == [100.0] * 12
        # difference = comparison - base at every cell
        assert all(
            d == pytest.approx(c - b, abs=1e-9)
            for dr, cr, br in zip(out["diff"], out["comp"], out["base"], strict=False)
            for d, c, b in zip(dr, cr, br, strict=False)
        )

    def test_mdfdc_needs_two_scenarios_and_a_positive_range(self, store):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        put(store, "a", idx, np.zeros(len(idx)))
        put(store, "b", idx, np.zeros(len(idx)))
        with pytest.raises(ValueError, match="two scenarios"):
            server.curves({"base": "a", "gauge": G, "kind": "mdfdc"})
        with pytest.raises(ValueError, match="no positive flows"):
            server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "mdfdc"})

    def test_mdfdc_zoom_range_must_not_be_empty(self, store):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        put(store, "a", idx, np.full(len(idx), 5.0))
        put(store, "b", idx, np.full(len(idx), 6.0))
        with pytest.raises(ValueError, match="empty"):
            server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "mdfdc", "ymin": "50", "ymax": "40"})

    def test_scenarios_with_no_shared_dates(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=3), [1, 2, 3])
        put(store, "b", pd.date_range("2001-01-01", periods=3), [1, 2, 3])
        with pytest.raises(ValueError, match="share no"):
            server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "fdc"})
        with pytest.raises(ValueError, match="share no"):
            server.curves({"base": "a", "comp": "b", "gauge": G, "kind": "hydro"})


class TestHeatmap:
    def three_years(self, store, a_level: float, b_level: float | None = None):
        idx = pd.date_range("2000-07-01", "2003-06-30")  # WY2001-2003
        put(store, "a", idx, np.full(len(idx), a_level))
        if b_level is not None:
            put(store, "b", idx, np.full(len(idx), b_level))

    def test_a_zoomed_window_is_gridded_finer_over_just_that_window(self, store):
        self.three_years(store, 100.0)
        full = server.heatmap({"base": "a", "gauge": G, "xlim": "2000", "dmax": "5"})
        zoom = server.heatmap({"base": "a", "gauge": G, "xlim": "1200", "xmin": "1000", "dmax": "5", "dmin": "2"})
        assert full["spacing"] > 1 and zoom["spacing"] == 1
        assert zoom["thresholds"][0] == 1000 and zoom["thresholds"][-1] == 1200 and zoom["durations"] == [2, 3, 4, 5]
        assert len(zoom["base"]) == 4 and all(len(row) == 201 for row in zoom["base"])

    def test_structure_and_values_for_a_constant_flow(self, store):
        self.three_years(store, 100.0)
        out = server.heatmap({"base": "a", "gauge": G, "xlim": "200", "dmax": "5"})
        assert out["xlim"] == 200 and out["spacing"] == 1 and out["years"] == 3
        assert out["thresholds"] == list(range(201)) and out["durations"] == [1, 2, 3, 4, 5]
        assert out["dates"] == ["2000-07-01", "2003-06-30"]
        z = out["base"]
        assert len(z) == 5 and all(len(row) == 201 for row in z)  # durations x thresholds
        for row in z:
            assert row[:101] == [100.0] * 101  # thresholds 0..100 are met (>=) in every year
            assert row[101:] == [0.0] * 100
        assert out["consecutive"] is False and "comp" not in out and "diff" not in out

    def test_comparison_and_difference(self, store):
        self.three_years(store, 100.0, 50.0)
        out = server.heatmap({"base": "a", "comp": "b", "gauge": G, "xlim": "200", "dmax": "2"})
        assert out["comp"][0][50] == 100.0 and out["comp"][0][51] == 0.0
        assert out["diff"][0][40] == 0.0  # both met
        assert out["diff"][0][60] == -100.0  # base met, comparison not
        assert out["diff"][0][150] == 0.0  # neither met
        assert all(-100.0 <= v <= 100.0 for row in out["diff"] for v in row)

    def test_mark_is_the_exact_percentage_at_one_threshold_and_duration(self, store):
        self.three_years(store, 100.0, 50.0)
        out = server.heatmap({"base": "a", "comp": "b", "gauge": G, "xlim": "200", "dmax": "5", "mthr": "75", "mdur": "3"})
        assert out["mark"] == [100.0, 0.0]

    def test_multiple_marks_keep_threshold_duration_pairs_and_input_order(self, store):
        idx = pd.date_range("2000-07-01", "2002-06-30")
        flow = np.zeros(len(idx))
        flow[:10] = 100
        second_year = idx.get_loc("2001-07-01")
        flow[second_year : second_year + 4] = 100
        put(store, "a", idx, flow)
        out = server.heatmap({"base": "a", "gauge": G, "xlim": "200", "dmax": "5", "mthr": "75,25,75", "mdur": "5,15,3"})
        assert out["marks"] == [
            {"thr": 75.0, "dur": 5, "values": [50.0]},
            {"thr": 25.0, "dur": 15, "values": [0.0]},
            {"thr": 75.0, "dur": 3, "values": [100.0]},
        ]
        assert "mark" not in out

    @pytest.mark.parametrize(("thresholds", "durations"), [("75,25", "3"), ("nan", "3"), ("75", "2.5"), ("75", "0")])
    def test_invalid_mark_pairs_are_rejected(self, store, thresholds, durations):
        self.three_years(store, 100.0)
        with pytest.raises(ValueError):
            server.heatmap({"base": "a", "gauge": G, "xlim": "200", "dmax": "5", "mthr": thresholds, "mdur": durations})

    def test_mark_is_dropped_when_there_is_no_shared_complete_year(self, store):
        idx = pd.date_range("2000-10-01", "2001-03-31")
        put(store, "a", idx, np.full(len(idx), 100.0))
        out = server.heatmap({"base": "a", "gauge": G, "xlim": "200", "dmax": "5", "mthr": "75", "mdur": "3"})
        assert out["years"] == 0 and "mark" not in out
        assert all(math.isnan(v) for row in out["base"] for v in row)  # NaN (-> null in the HTTP layer), never 0
        assert all(v is None for row in server._json_safe(out["base"]) for v in row)

    def test_default_axis_limit_is_a_nice_ceiling_of_the_99_5th_percentile(self, store):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        put(store, "a", idx, np.linspace(0, 1000, len(idx)))
        out = server.heatmap({"base": "a", "gauge": G, "dmax": "2"})
        # 99.5th percentile of 365 evenly spaced values 0..1000 is 1000 * (0.995 * 364) / 364 = 995.0 -> step 10 -> 1000
        assert out["xlim"] == 1000
        assert out["spacing"] == int(np.ceil((out["xlim"] + 1) / 400)) and out["thresholds"][0] == 0

    def test_limits_are_clamped(self, store):
        self.three_years(store, 100.0)
        small = server.heatmap({"base": "a", "gauge": G, "xlim": "3", "dmax": "1"})
        assert small["xlim"] == 10 and small["durations"] == [1, 2]  # at least 10 ML/d and 2 durations
        big = server.heatmap({"base": "a", "gauge": G, "xlim": "999999", "dmax": "9999"})
        assert big["xlim"] == 200000 and big["durations"][-1] == 366
        # ceil(200001 / 400) = 501 ML/d per column, so at most 400 columns: 0, 501, ..., 399 * 501
        assert big["spacing"] == 501 and big["thresholds"][-1] == 399 * 501 == 199899 and len(big["thresholds"]) == 400

    def test_no_shared_dates(self, store):
        put(store, "a", pd.date_range("2000-01-01", periods=3), [1, 2, 3])
        put(store, "b", pd.date_range("2001-01-01", periods=3), [1, 2, 3])
        with pytest.raises(ValueError, match="share no dates"):
            server.heatmap({"base": "a", "comp": "b", "gauge": G})


# ---------------------------------------------------------------------------------------------------------------------
# paired water years in the spells chart
# ---------------------------------------------------------------------------------------------------------------------
class TestSpellsPayload:
    def test_year_lists_are_aligned_and_sorted(self, store):
        idx = pd.date_range("2000-07-01", "2003-06-30")
        f = np.full(len(idx), 10.0)
        f[(idx >= "2001-07-10") & (idx <= "2001-07-14")] = 0.5  # 5 days below 1 in WY2001
        put(store, "a", idx, f)
        out = server.spells({"base": "a", "gauge": G, "thr": "1"})["scenarios"]["base"]
        y = out["years"]
        assert y["wy"] == [2000, 2001, 2002]
        assert all(len(v) == 3 for v in y.values())
        assert y["below"] == [0, 5, 0] and y["longest"] == [0, 5, 0] and y["n"] == [0, 1, 0]
        assert out["n_runs"] == 1 and out["missing_days"] == 0
        assert [r["days"] for r in out["longest_runs"]] == [5]

    def test_threshold_is_strictly_below(self, store):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        put(store, "a", idx, np.full(len(idx), 1.0))
        y = server.spells({"base": "a", "gauge": G, "thr": "1"})["scenarios"]["base"]["years"]
        assert y["below"] == [0]  # flow == threshold is not "below"
        y = server.spells({"base": "a", "gauge": G, "thr": "1.0001"})["scenarios"]["base"]["years"]
        assert y["below"] == [365]

    def test_longest_runs_are_capped_at_ten_and_sorted(self, store):
        idx = pd.date_range("2000-07-01", "2001-06-30")
        f = np.full(len(idx), 10.0)
        for k in range(12):  # 12 runs of length k + 1
            start = 10 + k * 20
            f[start : start + k + 1] = 0
        put(store, "a", idx, f)
        out = server.spells({"base": "a", "gauge": G, "thr": "1"})["scenarios"]["base"]
        assert out["n_runs"] == 12
        assert [r["days"] for r in out["longest_runs"]] == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3]


# ---------------------------------------------------------------------------------------------------------------------
# the HTTP layer: strict JSON and error codes
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def live_server(store):
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def get(port: int, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


class TestHttpPayloads:
    def test_ping_lists_the_supported_formats(self, live_server):
        status, body = get(live_server, "/api/ping")
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True and data["formats"] == server.FORMATS and "Standard time-series" in data["formats"]

    def test_curves_payload_is_strict_json_with_nulls_for_missing_days(self, live_server, store):
        put(store, "a", pd.date_range("2000-01-01", periods=5), [1, np.nan, 3, 4, 5])
        status, body = get(live_server, f"/api/curves?base=a&gauge={G}&kind=hydro")
        assert status == 200
        assert b"NaN" not in body and b"Infinity" not in body
        data = json.loads(body)
        assert data["base"] == [1.0, None, 3.0, 4.0, 5.0] and data["missing_days"] == 1

    def test_errors_are_400_with_a_message(self, live_server, store):
        status, body = get(live_server, f"/api/curves?base=missing&gauge={G}&kind=hydro")
        assert status == 400 and "not on the server" in json.loads(body)["error"]
        status, body = get(live_server, "/api/heatmap?base=missing&gauge=1")
        assert status == 400 and "error" in json.loads(body)

    def test_unknown_job_is_404(self, live_server):
        status, body = get(live_server, "/api/job?id=nope")
        assert status == 404 and json.loads(body)["status"] == "error"

    def test_forget_by_get_is_not_allowed(self, live_server):
        status, _ = get(live_server, "/api/forget?id=a")
        assert status == 405
