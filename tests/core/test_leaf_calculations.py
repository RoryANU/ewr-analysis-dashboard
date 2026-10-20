"""Independent known answers for the first scientific leaf extraction."""

from collections import Counter
from datetime import date
from fractions import Fraction
from itertools import product

import numpy as np
import pandas as pd
import pytest

from ewr_analysis.core.calculations.assessment_years import _feb29, _water_year, _water_year_days, _window_days, record_days
from ewr_analysis.core.calculations.axis_limits import nice_ceiling
from ewr_analysis.core.calculations.drought_windows import DROUGHTS, meta, window_dates
from ewr_analysis.core.calculations.event_gaps import _mrip_days
from ewr_analysis.core.calculations.flow_duration_curves import exceed_pct
from ewr_analysis.core.calculations.flow_regimes import flow_mappings, regimes_for
from ewr_analysis.core.calculations.paired_comparison import mcnemar_p


@pytest.mark.parametrize(("gained", "lost", "expected"), [(0, 0, None), (0, 5, 0.0625), (5, 0, 0.0625), (1, 6, 0.125), (2, 2, 1.0), (20, 0, 0.0)])
def test_mcnemar_hand_calculated(gained, lost, expected):
    # Five identical discordances: 2 extreme sequences / 2**5 = 1/16.
    # One versus six: 2*(1+7)/2**7 = 1/8; equal counts give probability 1.
    # Twenty identical: 2/2**20 < 0.00005, so four-decimal rounding gives 0.
    # Zero discordances has no test statistic under the preserved API contract.
    assert mcnemar_p(gained, lost) == expected


@pytest.mark.parametrize(("gained", "lost"), [(0, 1), (1, 4), (2, 5), (4, 4), (8, 2), (0, 6)])
def test_mcnemar_against_exact_enumerated_binomial(gained, lost):
    # Each fair-coin sequence has mass 1/2**n. A two-sided exact binomial
    # p-value adds the masses of outcomes no more likely than the observation.
    # Enumerating sequences avoids reusing the implementation's comb/tail sum.
    n = gained + lost
    masses = Counter(sum(draw) for draw in product((0, 1), repeat=n))
    tail_count = sum(count for count in masses.values() if count <= masses[gained])
    expected = round(float(Fraction(tail_count, 2**n)), 4)
    assert mcnemar_p(gained, lost) == expected


@pytest.mark.parametrize(("threshold", "expected"), [(-3, 100.0), (0, 80.0), (2, 40.0), (5, 20.0), (6, 0.0)])
def test_exceed_pct_includes_equal_values(threshold, expected):
    # Five observations: at least 0 -> four; at least 2 -> two; at least 5 -> one.
    values = np.array([-3.0, 0.0, 0.0, 2.0, 5.0])
    assert exceed_pct(values, threshold) == expected


def test_exceed_pct_vector_and_empty_record():
    # Equality includes both duplicated 10s: counts at 0,10,11 are 3,2,0 of 3.
    np.testing.assert_array_equal(exceed_pct(np.array([0.0, 10.0, 10.0]), np.array([0.0, 10.0, 11.0])), [100.0, 200.0 / 3, 0.0])
    # An empty denominator has no percentage; preserve the existing NaN/warning.
    with pytest.warns(RuntimeWarning, match="invalid value"):
        assert np.isnan(exceed_pct(np.array([], dtype=float), 0.0))


@pytest.mark.parametrize(("value", "expected"), [(-1, 100), (0, 100), (0.2, 1), (1.2, 2), (12.01, 13), (105, 110), (1001, 1100)])
def test_nice_ceiling_by_decade(value, expected):
    # Positive values below 100 round up to a whole unit. At 105 the grid is
    # tens, so 110 is next; at 1001 it is hundreds, so 1100 is next.
    # Nonpositive values use the documented historical sentinel, 100.
    assert nice_ceiling(value) == expected


@pytest.mark.parametrize(("years", "expected"), [(-1, 0), (0, 0), (float("nan"), 0), (0.1, 36), (0.3, 110), (1, 365), (2.5, 912)])
def test_mrip_days_365_day_year_and_even_rounding(years, expected):
    # The contract uses 365, not calendar-year length: 2.5*365 = 912.5.
    # Python rounds exact halves to even: 36.5 -> 36, 109.5 -> 110, 912.5 -> 912.
    assert _mrip_days(years) == expected


@pytest.mark.parametrize("blank", [None, ""])
def test_mrip_blank_oddity_is_preserved(blank):
    # The old comparison with zero rejects these before float conversion.
    with pytest.raises(TypeError):
        _mrip_days(blank)


def test_record_days_trims_and_fills_without_mutating():
    series = pd.Series([np.nan, 2.0, 4.0, np.nan], index=pd.to_datetime(["2020-02-27", "2020-02-28", "2020-03-01", "2020-03-02"]), name="flow")
    before = series.copy(deep=True)
    # Only Feb 28 through Mar 1 are observed bounds. Feb 29 exists in 2020;
    # its absent reading becomes NaN, rather than an interpolated value.
    expected = pd.Series([2.0, np.nan, 4.0], index=pd.date_range("2020-02-28", "2020-03-01"), name="flow")
    pd.testing.assert_series_equal(record_days(series), expected)
    pd.testing.assert_series_equal(series, before)


def test_record_days_empty_and_all_missing():
    empty = pd.Series([], index=pd.DatetimeIndex([]), dtype=float)
    pd.testing.assert_series_equal(record_days(empty), empty)
    missing = pd.Series([np.nan, np.nan], index=pd.date_range("2024-01-01", periods=2))
    # With no valid endpoint, the retained span contains zero observations.
    pd.testing.assert_series_equal(record_days(missing), missing.iloc[:0])


def test_record_days_none_and_non_dates():
    # A positional series does not define calendar days, so no daily record exists.
    assert record_days(None) is None
    assert record_days(pd.Series([10.0, 20.0])) is None


def test_record_days_keeps_timezone():
    series = pd.Series([1.0, 3.0], index=pd.to_datetime(["2024-02-28", "2024-03-01"], utc=True))
    # No timezone conversion is requested; the leap day is inserted in UTC.
    expected = pd.Series([1.0, np.nan, 3.0], index=pd.date_range("2024-02-28", "2024-03-01", tz="UTC"))
    pd.testing.assert_series_equal(record_days(series), expected)


@pytest.mark.parametrize(("start_year", "expected"), [(1899, False), (1999, True), (2000, False), (2003, True), (2099, False), (2399, True)])
def test_feb29_gregorian_century_rules(start_year, expected):
    # The February belongs to start_year+1: centuries 1900/2100 are common;
    # 2000/2400 divide by 400 and are leap years.
    assert _feb29(start_year) is expected


@pytest.mark.parametrize(("start_year", "days"), [(1899, 365), (1999, 366), (2000, 365), (2003, 366), (2099, 365), (2399, 366)])
def test_water_year_days_from_calendar_length(start_year, days):
    # Twelve months total 365 days, with one additional day for leap February.
    assert _water_year_days(start_year) == days


@pytest.mark.parametrize(("day", "label"), [(date(2024, 6, 30), 2023), (date(2024, 7, 1), 2024), (date(2024, 2, 29), 2023)])
def test_water_year_start_label_and_july_boundary(day, label):
    # June 30 is the last included day of the preceding July-start year;
    # July 1 starts a new year. The label is its first calendar year.
    assert _water_year(day) == label


@pytest.mark.parametrize(("end_year", "sm", "em", "expected"), [(1900, 2, 2, 28), (2000, 2, 2, 29), (2024, 12, 2, 91), (2021, 4, 8, 153), (2024, 1, 12, 366)])
def test_window_days_uses_ending_label_and_inclusive_months(end_year, sm, em, expected):
    # Gregorian February has 28/29 days; Dec-Feb in 2023/24 is 31+31+29.
    # Apr-Aug within Jul 2020-Jun 2021 counts Apr-Jun 2021 and Jul-Aug 2020:
    # 30+31+30+31+31 = 153. All months of a leap water year total 366.
    assert _window_days(end_year, sm, em) == expected


@pytest.mark.parametrize(
    ("key", "month", "first", "last"),
    [
        ("fed", 7, date(1895, 7, 1), date(1903, 6, 30)),
        ("ww2", 7, date(1937, 7, 1), date(1946, 6, 30)),
        ("mil", 1, date(1996, 1, 1), date(2009, 12, 31)),
        ("tin", 3, date(2017, 3, 1), date(2020, 2, 29)),
    ],
)
def test_drought_window_dates_from_inclusive_labels(key, month, first, last):
    # Each catalog interval includes its last labeled assessment year: its
    # final day is immediately before month 1 of last_label+1, including Feb 29.
    assert window_dates(key, month) == (first, last)


def test_drought_constants_default_window_and_metadata():
    # Catalog dates are declared scenario choices, not fitted equations.
    # Three labels 2017,2018,2019 mean Jul 2017 through Jun 2020 by default.
    assert DROUGHTS["tin"] == ("Tinderbox", 2017, 2019)
    assert window_dates("tin") == (date(2017, 7, 1), date(2020, 6, 30))
    assert meta() == [
        {"k": "fed", "name": "Federation", "w0": 1895, "w1": 1902},
        {"k": "ww2", "name": "WWII", "w0": 1937, "w1": 1945},
        {"k": "mil", "name": "Millennium", "w0": 1996, "w1": 2009},
        {"k": "tin", "name": "Tinderbox", "w0": 2017, "w1": 2019},
    ]


@pytest.mark.parametrize(
    ("code", "state", "expected"),
    [
        ("BF1", "VIC", ["Baseflow"]),
        ("BF-FD1", "QLD", ["Baseflow"]),
        ("FD1", "QLD", ["Baseflow", "Cease to flow"]),
        ("OB-WL", "NSW", ["Overbank"]),
        ("", "VIC", []),
        ("BF1", "WA", []),
    ],
)
def test_flow_regime_lookup_specificity_and_ties(code, state, expected):
    # BF beats F; BF-FD1 beats FD1. FD1's two regimes retain catalog order.
    # OB and WL tie in length; OB is declared first, so Overbank wins.
    assert regimes_for(code, state) == expected


def test_flow_regime_return_is_a_copy():
    # Appending to the result must not add a third category to the catalog.
    answer = regimes_for("FD1", "QLD")
    answer.append("example")
    assert flow_mappings["QLD"]["FD1"] == ["Baseflow", "Cease to flow"]
