"""Hand-derived contracts for Phase 1 slice 2; legacy oddities are preserved."""

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ewr_analysis.core.calculations.assessment_years import (
    _iso_date,
    _timing_mask,
    achievement_frequencies,
    assessment_start,
    complete_years,
    window_metrics,
    year_summary,
)
from ewr_analysis.core.calculations.duration_ratio import dr_approx, dr_years, vr_years
from ewr_analysis.core.calculations.event_gaps import _dateless_gap_stats, _has_any_event, _runs_by_year, ewr_gaps, gap_severity
from ewr_analysis.core.calculations.paired_comparison import _paired_achievement
from scripts.compare_implementations import load_old


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2000, 2, 29), "2000-02-29"),
        ("2000-02-29", "2000-02-29"),
        (pd.Timestamp("2024-07-01T00:30:00+10:00"), "2024-07-01"),
        (0, "1970-01-01"),
        (86_400_000_000_000, "1970-01-02"),
        (-1, "1969-12-31"),
    ],
)
def test_iso_date_explicit_calendar_and_nanoseconds(value, expected):
    # POSIX origin is Jan 1 1970; 86400*10**9 ns is one day, -1 ns the
    # preceding date. Local July 1 remains July 1 even though UTC is June 30.
    assert _iso_date(value) == expected


@pytest.mark.parametrize("value", [None, pd.NaT, ""])
def test_iso_date_missing_inputs_raise(value):
    # These all denote NaT, which has no calendar representation to format.
    with pytest.raises(ValueError, match="NaTType does not support strftime"):
        _iso_date(value)


@pytest.mark.parametrize("keyword", ["now", "today"])
def test_iso_date_relative_strings_old_and_new_accept_only(keyword):
    # Relative dates depend on wall time: no expected date or equality assertion.
    old = load_old("src/server.py", {"_iso_date"})
    old._iso_date(keyword)
    _iso_date(keyword)


@pytest.mark.parametrize(("sm", "em", "expected"), [(1, 12, 1), (6, 7, 6), (6, 6, 7), (7, 12, 7), (12, 2, 7)])
def test_assessment_start_calendar_convention(sm, em, expected):
    # A Jan-Dec or Jun-Jul window crosses July and starts its own assessment
    # year; same-half or Dec-Feb windows retain the July convention.
    assert assessment_start(sm, em) == expected


def test_timing_mask_first_last_day_and_leap_day():
    index = pd.date_range("2000-02-27", "2000-03-02")
    # Feb 28, Feb 29 and Mar 1 are the three included dates; both ends count.
    np.testing.assert_array_equal(_timing_mask(index, 2, 3, 28, 1), [False, True, True, True, False])
    # Same-month reversed day bounds form an empty intersection, not a wrap.
    np.testing.assert_array_equal(_timing_mask(index, 2, 2, 29, 28), [False] * 5)


def test_timing_mask_wrapped_months_and_zero_day_sentinel():
    index = pd.to_datetime(["1999-12-30", "1999-12-31", "2000-02-29", "2000-03-01"])
    # Dec 31-Feb 29 wraps New Year, includes both boundary dates.
    np.testing.assert_array_equal(_timing_mask(index, 12, 2, 31, 29), [False, True, True, False])
    # Legacy zero day acts as unspecified, selecting the whole February month.
    np.testing.assert_array_equal(_timing_mask(index, 2, 2, 0, 0), [False, False, True, False])


def test_complete_years_leap_water_year_and_no_complete_record():
    flow = pd.Series(1.0, index=pd.date_range("1999-07-01", "2000-06-30"))
    # Jul-Dec 184 + Jan-Jun 182 = 366 days; removing leap day leaves 365,
    # and the absent calendar day must make the full timing window incomplete.
    assert len(flow) == 366
    assert complete_years(flow, [1999, 2000], 7, 7, 6) == [1, 0]
    assert complete_years(flow.drop(pd.Timestamp("2000-02-29")), [1999], 7, 7, 6) == [0]
    assert complete_years(flow.iloc[:0], [1999], 7, 7, 6) == [0]
    assert complete_years(flow, [], 7, 7, 6) == []


def test_complete_years_day_bounds_ignore_missing_outside_window():
    flow = pd.Series([2.0, 2.0, 2.0], index=pd.date_range("2000-02-28", "2000-03-01"))
    # Only these three dates are required, including leap day; the other 363
    # days of the July-start assessment year do not affect this timing window.
    assert complete_years(flow, [1999], 7, 2, 3, 28, 1) == [1]
    assert complete_years(flow.iloc[1:], [1999], 7, 2, 3, 28, 1) == [0]
    assert complete_years(flow.iloc[:-1], [1999], 7, 2, 3, 28, 1) == [0]


def test_year_summary_thresholds_and_missing_values():
    # Complete ratios sorted: 0.499,0.5,1,2 -> median (0.5+1)/2=.75.
    # Two failures (<1), exactly one near miss (>=.5); incomplete 0 is ignored.
    assert year_summary([0.499, 0.5, 1, 2, None, 0], [1, 1, 1, 1, 1, 0]) == {"dr": 0.75, "nm": [1, 2]}
    assert year_summary([], []) == {"dr": None, "nm": None}
    assert year_summary([None, 1], [1, 0]) == {"dr": None, "nm": None}
    # Unlike None, NaN remains in the median and compares false to <1.
    result = year_summary([np.nan, 0.5], [1, 1])
    assert np.isnan(result["dr"])
    assert result["nm"] == [1, 1]


def test_window_metrics_antecedent_gap_and_inclusive_edges():
    years: dict[str, Any] = {"wy": [], "complete": [], "achieved": [], "dr": []}
    gaps = [{"start": "2000-06-28", "end": "2000-07-03", "days": 6, "censored": False}]
    # Gap exceeds MRIP=3 on Jul 1-3: 3 of the window's 5 days. Severity uses
    # all 6 gap days, even though the gap began outside the Jul 1-5 window.
    assert window_metrics(years, gaps, 3, 50, "2000-07-01", "2000-07-05", ("2000-06-28", "2000-07-05")) == {
        "n": 0,
        "fr": None,
        "et": 0.6,
        "sev": 2.0,
        "dr": None,
    }
    # The very first and last day each count in a singleton window: 1/1.
    for day in ("2000-07-01", "2000-07-03"):
        assert window_metrics(years, gaps, 3, 50, day, day)["et"] == 1.0


def test_window_metrics_full_year_and_censored_denominator():
    years = {"wy": [1999, 2000], "complete": [1, 0], "achieved": [1, 1], "dr": [0.5, 2]}
    gaps = [
        {"start": "1999-07-01", "end": "1999-07-05", "days": 5, "censored": False},
        {"start": "2000-06-26", "end": "2000-06-30", "days": 5, "censored": False},
        {"start": "2000-02-29", "end": "2000-02-29", "days": 1, "censored": True},
    ]
    # 1/1 achieved divided by target .5 = 2. Two 5-day gaps each have 2
    # excess days: 4/(366-1)=.0109589 -> .0110; longest/MRIP=5/3 ->1.67.
    assert window_metrics(years, gaps, 3, 50, "1999-07-01", "2000-06-30") == {"n": 1, "fr": 2.0, "et": 0.011, "sev": 1.67, "dr": 0.5}
    # Shortening either full-year boundary excludes that assessment year.
    assert window_metrics(years, [], 3, 50, "1999-07-02", "2000-06-30")["n"] == 0
    assert window_metrics(years, [], 3, 50, "1999-07-01", "2000-06-29")["n"] == 0
    assert window_metrics(years, [], 3, 50, "1999-07-01", "2000-06-30")["et"] is None


@pytest.mark.parametrize("consecutive", [False, True])
def test_achievement_frequencies_threshold_equality_and_leap_month(consecutive):
    index = pd.date_range("2000-02-01", "2000-02-29")
    flows = np.full((29, 2), 10.0)
    # Both scenarios observe all 29 leap-February days. Exactly 10 succeeds
    # for 1 and 29 days, fails for 30; 11 never succeeds. One counted year.
    result, n = achievement_frequencies(flows, index, np.array([10.0, 11.0]), np.array([1, 29, 30]), 2, 2, consecutive)
    assert n == 1
    assert result.dtype == np.float32
    np.testing.assert_array_equal(result, [[[100, 100, 0], [0, 0, 0]], [[100, 100, 0], [0, 0, 0]]])
    # One missing day in the comparison removes the only year for BOTH.
    flows[0, 1] = np.nan
    result, n = achievement_frequencies(flows, index, np.array([10.0]), np.array([1]), 2, 2, consecutive)
    assert n == 0
    assert np.isnan(result).all()


def test_achievement_empty_series_preserves_error():
    # No dates means no water-year maximum; the legacy API raises, not NaN.
    with pytest.raises(ValueError, match="zero-size array"):
        achievement_frequencies(np.empty((0, 1)), pd.DatetimeIndex([]), np.array([10.0]), np.array([1]), 7, 6)


def test_ewr_gaps_single_event_threshold_equality_and_censoring():
    flow = pd.Series(1.0, index=pd.date_range("2000-07-01", periods=7))
    inter = pd.DataFrame({"startDate": ["2000-07-01", "2000-07-05"], "endDate": ["2000-07-03", "2000-07-07"]})
    ev = {"flow": flow, "mrip_days": 3, "inter": inter, "succ": pd.DataFrame({"startDate": ["2000-07-04"]})}
    # One event on Jul 4 leaves two 3-day boundary gaps: equality to MRIP
    # gives zero excess, ETP=0/7. First and last record days are included.
    gaps, etp = ewr_gaps(ev)
    assert etp == 0
    assert gaps == [
        {"start": "2000-07-01", "end": "2000-07-03", "days": 3, "censored": False, "boundary": True, "excess": 0, "no_success": False},
        {"start": "2000-07-05", "end": "2000-07-07", "days": 3, "censored": False, "boundary": True, "excess": 0, "no_success": False},
    ]
    flow.iloc[0] = np.nan
    # The entire first gap is now unknown and removed from the denominator.
    gaps, etp = ewr_gaps(ev)
    assert gaps[0]["censored"] is True
    assert gaps[0]["excess"] is None
    assert etp == 0


def test_ewr_gaps_no_event_and_empty_series():
    flow = pd.Series(1.0, index=pd.date_range("2000-07-01", periods=5))
    ev = {"flow": flow, "mrip_days": 3, "inter": pd.DataFrame(), "succ": pd.DataFrame()}
    # No successful event gives one five-day gap: (5-3)/5=.4.
    gaps, etp = ewr_gaps(ev)
    assert (gaps[0]["days"], gaps[0]["excess"], gaps[0]["no_success"], etp) == (5, 2, True, 0.4)
    # Empty records have no first/last date in the legacy API.
    ev["flow"] = flow.iloc[:0]
    with pytest.raises(IndexError):
        ewr_gaps(ev)


def test_gap_severity_interior_preference_and_truthy_excess():
    gaps: list[dict[str, Any]] = [
        {"days": 9, "boundary": True, "censored": False, "excess": 6},
        {"days": 3, "boundary": False, "censored": False, "excess": 0},
        {"days": 99, "boundary": False, "censored": True, "excess": None},
    ]
    # Interior severity 3/3=1, boundary-inclusive 9/3=3, one excess episode.
    assert gap_severity(gaps, 3) == {"sev": 1.0, "sev_b": 3.0, "eps": 1}
    assert gap_severity(gaps[:1], 3) == {"sev": 3.0, "sev_b": 3.0, "eps": 1}
    assert gap_severity([], 3) == {"sev": None, "sev_b": None, "eps": None}
    # Episode count trusts the supplied excess, rather than recomputing it.
    gaps[1]["excess"] = 1
    assert gap_severity(gaps, 3)["eps"] == 2


def test_dateless_gap_stats_and_has_any_event():
    key = ("g", "p", "e")
    # Ten days with no success: 7 excess days/10=.7 and severity 10/3=3.33.
    assert _dateless_gap_stats(key, 10, 3, {}, {}) == {"et": 0.7, "sev": 3.33, "sev_b": 3.33, "eps": 1}
    # Successful run with lengths 3 and 7: only 4 excess days/10=.4, but the
    # legacy dateless API supplies no severity when any success is present.
    inter = {key: pd.DataFrame({"interEventLength": [3, 7, np.nan, "bad"]})}
    assert _dateless_gap_stats(key, 10, 3, inter, {key: pd.DataFrame()}) == {"et": 0.4, "sev": None, "sev_b": None, "eps": None}
    assert _dateless_gap_stats(key, 0, 3, {}, {}) == {"et": None, "sev": None, "sev_b": None, "eps": None}
    assert _has_any_event({}) is False
    assert _has_any_event({"g": {"p": {"e": {2000: []}}}}) is False
    # A list containing even an empty event is nonempty under this predicate.
    assert _has_any_event({"g": {"p": {"e": {2000: [[]]}}}}) is True


def test_runs_nan_break_single_event_and_empty_series():
    s = pd.Series([10, 10, np.nan, 10, 10], index=pd.date_range("2000-07-01", periods=5))
    # Two 2-day runs, both censored by the record edge and adjacent NaN.
    years, runs = _runs_by_year(s, np.ones(5, dtype=bool))
    assert years == {"wy": [2000], "days": [None], "longest": [None], "n": [None], "complete": [0]}
    assert runs == [
        {"start": "2000-07-01", "end": "2000-07-02", "days": 2, "censored": True, "wy": 2000},
        {"start": "2000-07-04", "end": "2000-07-05", "days": 2, "censored": True, "wy": 2000},
    ]
    years, runs = _runs_by_year(s.iloc[:1], np.array([True]))
    assert runs == [{"start": "2000-07-01", "end": "2000-07-01", "days": 1, "censored": True, "wy": 2000}]
    assert _runs_by_year(s.iloc[:0], np.array([], dtype=bool)) == ({"wy": [], "days": [], "longest": [], "n": [], "complete": []}, [])


def test_runs_complete_leap_year_event_crosses_year_boundary():
    s = pd.Series(0.0, index=pd.date_range("1999-07-01", "2001-06-30"))
    s.loc["2000-06-30":"2000-07-01"] = 10
    # One 2-day run starts in 1999's 366-day year. Each year gets 1 hit day
    # and longest=1, but only the starting year gets a run count. No censoring.
    years, runs = _runs_by_year(s, s.to_numpy() >= 10)
    assert years == {"wy": [1999, 2000], "days": [1, 1], "longest": [1, 1], "n": [1, 0], "complete": [1, 1]}
    assert runs == [{"start": "2000-06-30", "end": "2000-07-01", "days": 2, "censored": False, "wy": 1999}]


def test_duration_ratios_longest_kth_low_flow_and_approximation():
    yr = pd.DataFrame({"maxEventDays": [3, 9, np.nan], "totalEventDays": [6, 12, 0]}, index=[1999, 2000, 2001])
    # Required 3 days: longest gives 1, capped 3->2, missing->None. Low flow
    # uses all days: 2, capped 4->2, 0. Second longest 3 of [6,3] gives 1;
    # only one event the next year means 0, as does a year without events.
    assert dr_years(yr, "", 3, False, 1) == [1.0, 2.0, None]
    assert dr_years(yr, "", 3, True, 2) == [2.0, 2.0, 0.0]
    assert dr_years(yr, "", 3, False, 2, {1999: [[1] * 6, [1] * 3], 2000: [[1] * 9]}) == [1.0, 0.0, 0.0]
    assert dr_years(yr, "", 0, False, 1) == [None, None, None]
    assert dr_years(yr.iloc[:0], "", 3, False, 1) == []
    # Mean event lengths 1,3,9 divided by 3 -> .333,1,capped2.
    assert dr_approx([1, 3, 9, None], {"dur": 3}) == [0.333, 1.0, 2.0, None]
    assert dr_approx([3], {"dur": 3, "kind": "V"}) == [None]
    assert dr_approx([3], {"dur": -3}) == [-1.0]  # Legacy approximation accepts negative duration.
    assert dr_approx([], {"dur": 3}) == []


def test_volume_ratio_nan_break_and_timing_boundaries():
    flow = pd.Series([999, 2, 3, 4, 999], index=pd.date_range("2000-02-27", periods=5))
    # Feb28-Mar1 includes 2,3,4. Two-day sums 5 and7; best7/threshold7=1.
    assert vr_years(flow, [1999, 2000], 7, 2, 3, 2, 7, 28, 1) == [1.0, None]
    # NaN on Feb29 breaks both candidate windows; neither is a full 2 days.
    flow.loc["2000-02-29"] = np.nan
    assert vr_years(flow, [1999], 7, 2, 3, 2, 7, 28, 1) == [None]
    assert vr_years(flow.iloc[:0], [1999], 7, 2, 3, 2, 7, 28, 1) == [None]
    assert vr_years(flow, [1999], 7, 2, 3, 2, 0, 28, 1) == [None]


def test_paired_achievement_hand_count_and_no_common_year():
    base = {"wy": [1999, 2000, 2001, 2002], "complete": [1, 1, 1, 0], "achieved": [0, 1, 1, 1]}
    comp = {"wy": [2001, 2000, 1999, 2002], "complete": [1, 1, 1, 1], "achieved": [1, 0, 1, 1]}
    # Common complete years 1999-2001: base 0,1,1; comp 1,0,1. One gain and
    # one loss; both 2/3=66.667%. With two fair discordances, p=1.
    assert _paired_achievement(base, comp) == {
        "wy": [1999, 2000, 2001],
        "n": 3,
        "base": 2,
        "comp": 2,
        "gained": 1,
        "lost": 1,
        "pct_base": 66.667,
        "pct_comp": 66.667,
        "p_mcnemar": 1.0,
    }
    empty: dict[str, Any] = {"wy": [], "complete": [], "achieved": []}
    assert _paired_achievement(base, empty) == {
        "wy": [],
        "n": 0,
        "base": 0,
        "comp": 0,
        "gained": 0,
        "lost": 0,
        "pct_base": None,
        "pct_comp": None,
        "p_mcnemar": None,
    }
