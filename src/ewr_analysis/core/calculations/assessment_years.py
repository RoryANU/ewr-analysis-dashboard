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


def achievement_frequencies(flows: Any, index: Any, thresholds: Any, durations: Any, sm: int, em: int, consecutive: bool = False) -> tuple[Any, int]:
    """% of water years in which each threshold is exceeded for at least `duration` days within the timing window.
    flows: (days, scenarios). Same result as the numba reference (non-consecutive days): the d-th largest in-window flow of a year
    is the highest threshold that is exceeded on d days of that year.  consecutive=True requires the d days to be consecutive (the
    reference's calc_annual_max_threshold_consecutive: the largest, over windows of d days, of the window minimum).
    returns (scenarios, thresholds, durations)."""
    import numpy as np

    months, calendar_years = index.month.to_numpy(), index.year.to_numpy()
    wy = np.where(months >= 7, calendar_years + 1, calendar_years)  # labelled by the year the water year ends in
    year_lookup = np.unique(wy, return_inverse=True)[1]
    n_years = year_lookup.max() + 1
    mask = (months >= sm) & (months <= em) if sm <= em else (months >= sm) | (months <= em)
    window_flows = np.where(mask[:, None] & ~np.isnan(flows), flows, -np.inf)
    bounds = np.searchsorted(year_lookup, np.arange(n_years + 1))  # each year's first row (the index is in date order)
    # Denominator: only water years whose timing window is fully observed count. A water year is complete for a scenario when
    # every calendar day of the window (Jul-Jun water year, as above) is in the index with a non-NaN flow. Partial first/last
    # years and years with no (or incomplete) data are excluded rather than scored as failures; a complete year that simply
    # stays below the threshold is still an unsuccessful year.
    # All scenarios use the SAME years: the intersection of every scenario's complete years, so base/comparison rates (and
    # their difference) are paired over one year set. That shared count is returned as the number of years. No shared complete
    # year -> NaN. (No separate cap on the duration is needed: days outside the window are -inf, so a duration longer than
    # the window's days in a year can never be met, in either mode.)
    expected = np.array([_window_days(wl, sm, em) for wl in np.unique(wy)])
    complete = np.stack(
        [(np.bincount(year_lookup, weights=mask & ~np.isnan(flows[:, s]), minlength=n_years) == expected) & (expected > 0) for s in range(flows.shape[1])]
    )
    counted = complete.all(axis=0)
    n_counted = int(counted.sum())
    out = np.zeros((flows.shape[1], len(thresholds), len(durations)), dtype=np.float32)
    for s in range(flows.shape[1]):
        # annual[k, y]: the highest threshold that year y's flows exceed for durations[k] days
        annual = np.full((len(durations), n_years), -np.inf)
        for y in np.flatnonzero(counted):
            year_flows = window_flows[bounds[y] : bounds[y + 1], s]
            if consecutive:
                # sliding-window minimum grown one day at a time: run_min[i] = min(year_flows[i : i + width])
                run_min, width = year_flows.copy(), 1
                for k, d in enumerate(durations):
                    while width < d and len(run_min) > 1:
                        run_min, width = np.minimum(run_min[:-1], year_flows[width:]), width + 1
                    if d <= len(year_flows) and width == d and len(run_min):
                        annual[k, y] = run_min.max()
            else:
                descending = np.sort(year_flows)[::-1]
                for k, d in enumerate(durations):
                    if d <= len(descending):
                        annual[k, y] = descending[d - 1]
        for k in range(len(durations)):
            if not n_counted:
                out[s, :, k] = np.nan
                continue
            n_unsuccessful = np.searchsorted(np.sort(annual[k][counted]), thresholds, side="left")
            out[s, :, k] = (n_counted - n_unsuccessful) * 100.0 / n_counted
    return out, n_counted


def assessment_start(sm: int, em: int) -> int:
    """py_ewr's assessment year starts on 1 July, except when the timing window runs from the first half of the year into the second
    (StartMonth <= 6 and EndMonth >= 7, e.g. Jun-Nov): then on the 1st of StartMonth (evaluate_EWRs.wateryear_daily)."""
    return sm if sm <= 6 and em >= 7 else 7


def _timing_mask(index: Any, sm: int, em: int, sd: int | None = None, ed: int | None = None) -> Any:
    """Calendar window mask; optional day boundaries are used by user EWRs, while stock summaries retain whole months."""
    month, day = index.month, index.day
    after = (month > sm) | ((month == sm) & (day >= (sd or 1)))
    before = (month < em) | ((month == em) & (day <= (ed or 31)))
    return after & before if sm <= em else after | before


def complete_years(flow: Any, wys: list[int], ys: int, sm: int, em: int, sd: int | None = None, ed: int | None = None) -> list[int]:
    """1 for each assessment year (label w, starting on the 1st of month ys) in which every day of the timing window sm-em has a value;
    the window's days come from the calendar, so a window cut off by the record's ends is incomplete."""
    import pandas as pd

    if not wys:
        return []
    full = pd.date_range(pd.Timestamp(min(wys), ys, 1), pd.Timestamp(max(wys) + 1, ys, 1) - pd.Timedelta(days=1))
    m = full.month
    win = _timing_mask(full, sm, em, sd, ed)
    ok = pd.Series(flow.reindex(full).notna().to_numpy()[win], index=(full.year - (m < ys))[win])
    done = ok.groupby(level=0).all()
    return [int(bool(done.get(w, False))) for w in wys]


def year_summary(dr: list[float | None], complete: list[int]) -> dict[str, Any]:
    """Median duration ratio over the complete years, and the near misses: years with 0.5 <= DR < 1 and the years with DR < 1."""
    import numpy as np

    v = [x for x, c in zip(dr, complete, strict=True) if c and x is not None]
    fail = [x for x in v if x < 1]
    return {"dr": round(float(np.median(v)), 3) if v else None, "nm": [sum(1 for x in fail if x >= 0.5), len(fail)] if v else None}


def window_metrics(
    years: dict[str, Any], gaps: list[dict[str, Any]], mrip: int, target: Any, w0: Any, w1: Any, record: tuple[Any, Any] | None = None
) -> dict[str, Any]:
    """An EWR's metrics inside one drought window, the days w0..w1 (dates, inclusive; drought_windows.window_dates), from its yearly results
    (`years`: wy labels, achieved, complete, dr, ystart) and its gaps (ewr_gaps):
      n   complete assessment years inside the window;  fr = (achieved / n) / (target / 100)
      et  days of the window beyond MRIP in an uncensored gap (a gap is beyond MRIP from its start + MRIP days, so a gap that began before
          the window counts: antecedent dryness) ÷ the window's days within the record, less days inside censored gaps
      sev longest uncensored gap overlapping the window, from its true start to its end, ÷ MRIP
      dr  median duration ratio over the window's complete years.
    `record`: the first and last day of the record (default: the span of the gaps)."""
    import numpy as np
    import pandas as pd

    w0, w1, ys = pd.Timestamp(w0), pd.Timestamp(w1), years.get("ystart", 7)
    inside = [i for i, w in enumerate(years["wy"]) if pd.Timestamp(w, ys, 1) >= w0 and pd.Timestamp(w + 1, ys, 1) - pd.Timedelta(days=1) <= w1]
    done = [i for i in inside if years["complete"][i]]
    n, ach, tgt = len(done), sum(years["achieved"][i] for i in done), pd.to_numeric(target, errors="coerce")
    drs = [years["dr"][i] for i in done if years.get("dr") and years["dr"][i] is not None]
    out: dict[str, Any] = {
        "n": n,
        "fr": round(ach / n / (tgt / 100), 4) if n and tgt > 0 else None,
        "et": None,
        "sev": None,
        "dr": round(float(np.median(drs)), 3) if drs else None,
    }
    span = [(pd.Timestamp(g["start"]), pd.Timestamp(g["end"]), g) for g in gaps]
    if mrip and span:
        first, last = (pd.Timestamp(record[0]), pd.Timestamp(record[1])) if record else (min(a for a, _, _ in span), max(b for _, b, _ in span))
        lo, hi = max(w0, first), min(w1, last)  # the window within the record
        overlap = lambda a, b: max(0, (min(b, hi) - max(a, lo)).days + 1)  # noqa: E731  (days of a..b inside lo..hi)
        days = overlap(lo, hi) - sum(overlap(a, b) for a, b, g in span if g["censored"])
        if days > 0:
            beyond = sum(overlap(a + pd.Timedelta(days=mrip), b) for a, b, g in span if not g["censored"] and g["days"] > mrip)
            out["et"] = round(beyond / days, 4)
        hit = [g["days"] for a, b, g in span if not g["censored"] and a <= w1 and b >= w0]
        out["sev"] = round(max(hit) / mrip, 2) if hit else None
    return out


def _iso_date(value: Any) -> str:
    import pandas as pd

    return cast(str, pd.Timestamp(value).strftime("%Y-%m-%d"))
