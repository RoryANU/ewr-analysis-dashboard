from typing import Any, cast


def _window_days(wy_label: int, sm: int, em: int) -> int:
    """Count inclusive timing-window months within a July-start water year.

    Equation / rule: EQ-WINDOW-01. Count daily calendar dates whose months lie
    in sm..em, using an OR across the year boundary when sm > em.

    Parameters
    ----------
    wy_label : int
        Ending calendar year [year label]; dates run from 1 July of wy_label-1
        through 30 June of wy_label, both included. This is an ending-year label.
    sm, em : int
        First and last included months [calendar month], assumed in 1..12.

    Returns
    -------
    int
        Number of selected dates [days]. Equal month bounds include that whole
        month. Gregorian February length comes from the generated calendar.

    Assumptions
    -----------
    The internally generated daily index is timezone-naive. Leap days count as
    one day. No observation coverage is considered and no NaN normalization is
    performed; callers supply valid integer year/month values. Month bounds and
    both dates of the water-year range are inclusive. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_window_days; pandas date_range calendar.
    Scientific timing-window conventions: TBD, needs scientist review."""
    import pandas as pd

    m = pd.date_range(f"{wy_label - 1}-07-01", f"{wy_label}-06-30").month.to_numpy()
    return int(((m >= sm) & (m <= em) if sm <= em else (m >= sm) | (m <= em)).sum())


def _water_year(d: Any) -> int:
    """Label a July-start water year by its starting calendar year.

    Equation / rule: EQ-WY-START-01. Use d.year when d.month >= 7;
    otherwise use d.year - 1.

    Parameters
    ----------
    d : date-like scalar
        Date with year/month attributes [calendar date]. For timezone-aware
        values, the existing local fields are read without timezone conversion.

    Returns
    -------
    int
        Starting calendar year [year label], including July 1 in the new year
        and June 30 in the preceding July-start year.

    Assumptions
    -----------
    A valid date-like scalar is supplied. There is no index, NaN/NaT screening or
    missing-value normalization. The typing cast preserves the actual value for
    unsupported date-like inputs. No input is mutated. This starting-year label
    is distinct from the ending-year label accepted by _window_days.

    References
    ----------
    pre-refactor:src/server.py::_water_year, whose docstring attributes this label
    to py_ewr. Formal convention/source verification: TBD, needs scientist review."""
    return cast(int, d.year if d.month >= 7 else d.year - 1)


def record_days(s: Any) -> Any:
    """Trim a daily gauge series to observed endpoints and insert absent dates.

    Equation / rule: EQ-RECORD-DAYS-01; retain first..last nonmissing observations,
    then reindex at daily intervals without interpolating values.

    Parameters
    ----------
    s : pandas.Series or None
        One-dimensional observations [caller units], assumed to have an ascending,
        unique DatetimeIndex at a consistent daily observation time. The existing
        timezone is carried into the new daily index, without conversion.

    Returns
    -------
    pandas.Series or None
        Daily observations [same units] from first through last valid observation,
        inclusive. Missing dates contain NaN. All-missing and empty dated inputs
        yield an empty series. None or a non-DatetimeIndex input returns None.

    Assumptions
    -----------
    The input represents one gauge and one measurement. No aggregation or
    interpolation is performed. Leading/trailing missing values are excluded;
    interior missing values remain missing. Duplicate/nonordered dates are not
    validated or repaired. The input is not mutated; no copy is guaranteed for
    an already empty input. Index names/types follow pandas slicing/reindexing.

    References
    ----------
    pre-refactor:src/server.py::record_days; pandas Series slicing/reindexing.
    Scientific observation-time and missing-data policy: TBD, needs scientist review."""
    import pandas as pd

    if s is None or not isinstance(s.index, pd.DatetimeIndex):
        return None
    s = s.loc[s.first_valid_index() : s.last_valid_index()] if s.notna().any() else s.iloc[:0]
    return s if s.empty else s.reindex(pd.date_range(s.index[0], s.index[-1]))


def _feb29(w: int) -> bool:
    """Test whether a July-start water year contains a Gregorian leap day.

    Equation / rule: EQ-LEAP-01; calendar.isleap(w + 1).

    Parameters
    ----------
    w : int
        Starting calendar year [year label], for 1 July w through 30 June w+1.
        Scalar integer; no index, timezone or NaN handling applies.

    Returns
    -------
    bool
        Whether 29 February occurs in that inclusive water-year range
        [dimensionless]. Its February belongs to calendar year w+1.

    Assumptions
    -----------
    The proleptic Gregorian leap-year rule is intended: divisibility by four,
    except centuries not divisible by 400. No validation or input mutation is
    performed. The leap day is included in the water year that contains it.

    References
    ----------
    pre-refactor:src/server.py::_feb29; Python calendar.isleap.
    Application of this calendar to source data: TBD, needs scientist review."""
    import calendar

    return calendar.isleap(w + 1)


def _water_year_days(w: int) -> int:
    """Return the calendar length of a July-start water year.

    Equation / rule: EQ-WY-DAYS-01; 366 if _feb29(w), otherwise 365.

    Parameters
    ----------
    w : int
        Starting calendar year [year label], for 1 July w through 30 June w+1.
        Scalar integer; no index, timezone or NaN handling applies.

    Returns
    -------
    int
        Calendar length [days], including both endpoints and any February 29.

    Assumptions
    -----------
    The proleptic Gregorian calendar and July-start year are intended. The pure
    _feb29 helper supplies the leap-day decision. No observation coverage is
    considered and no validation or input mutation is performed. This calendar
    length is separate from the fixed 365-day MRIP conversion.

    References
    ----------
    pre-refactor:src/server.py::_water_year_days and _feb29.
    Application of this calendar convention: TBD, needs scientist review."""
    return 366 if _feb29(w) else 365
