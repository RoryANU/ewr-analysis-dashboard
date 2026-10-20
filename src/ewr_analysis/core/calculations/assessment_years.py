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
    """Calculate threshold-duration achievement rates over shared complete years.

    Equation / rule: nonconsecutive annual threshold is the d-th largest selected
    flow; consecutive annual threshold is the maximum rolling minimum of width d.
    Report 100 * successful shared years / shared complete years.

    Parameters
    ----------
    flows : ndarray
        Shape (days, scenarios), values [same units as thresholds], with NaN missing.
    index : DatetimeIndex
        Sorted daily dates aligned to rows; local month/year fields are used without
        timezone conversion. Water-year labels use the ENDING calendar year (Jul-Jun).
    thresholds, durations : one-dimensional arrays
        Thresholds [flow-series units] and positive ascending integer durations [days].
    sm, em : int
        Inclusive endpoint months [calendar month], whole months only.
    consecutive : bool
        Require adjacent selected rows to meet duration when true.

    Returns
    -------
    (ndarray, int)
        Rates [%], float32 shape (scenarios, thresholds, durations), and shared-year
        count [years]. Zero shared complete years gives NaN rates; empty index raises.

    Assumptions
    -----------
    Equality meets the threshold. Partial/missing years are excluded in ALL scenarios
    if incomplete in any one. Completeness counts rows; duplicate dates and missing
    calendar dates are assumed resolved upstream. Consecutive mode grows widths in
    provided order and assumes increasing durations; it does not shrink for an
    unsorted duration. Zero/negative durations are not validated. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::achievement_frequencies; its numba-reference comment
    has no bibliographic citation. Scientific provenance: TBD, needs scientist review."""
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
    """Choose the first month of an assessment year.

    Equation / rule: return sm if sm <= 6 and em >= 7, otherwise 7.

    Parameters
    ----------
    sm, em : int
        First and last included timing-window months [calendar month], scalars.
        No index or timezone applies; values are assumed to be in 1..12.

    Returns
    -------
    int
        Assessment-year starting month [calendar month]; the year is labeled by
        its starting calendar year, unlike achievement_frequencies' ending label.

    Assumptions
    -----------
    Month endpoints are inclusive. NaN/invalid months are not validated; Python
    comparison semantics apply. No input is mutated. The July-crossing condition
    is retained exactly, including January-December windows starting in January.

    References
    ----------
    pre-refactor:src/server.py::assessment_start, whose comment attributes this
    convention to py_ewr wateryear_daily. Scientific suitability: TBD, needs scientist review."""
    return sm if sm <= 6 and em >= 7 else 7


def _timing_mask(index: Any, sm: int, em: int, sd: int | None = None, ed: int | None = None) -> Any:
    """Select calendar dates inside an inclusive timing window.

    Equation / rule: intersect the lower/upper month-day bounds when sm <= em;
    otherwise union them across New Year. Day zero uses the unspecified default.

    Parameters
    ----------
    index : DatetimeIndex
        One-dimensional dates [calendar days], in their existing timezone.
    sm, em : int
        Inclusive endpoint months [calendar month].
    sd, ed : int or None
        Inclusive endpoint days [day of month]; falsy values use 1 and 31.

    Returns
    -------
    array-like of bool
        One flag [dimensionless] per index entry. No date is inserted or converted.

    Assumptions
    -----------
    Uses local month/day fields. NaT comparisons are false. Reversed day bounds
    inside the same month produce an empty intersection, not a wrap. Invalid
    bounds are not validated. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_timing_mask. Scientific timing-window policy:
    TBD, needs scientist review."""
    month, day = index.month, index.day
    after = (month > sm) | ((month == sm) & (day >= (sd or 1)))
    before = (month < em) | ((month == em) & (day <= (ed or 31)))
    return after & before if sm <= em else after | before


def complete_years(flow: Any, wys: list[int], ys: int, sm: int, em: int, sd: int | None = None, ed: int | None = None) -> list[int]:
    """Flag assessment years whose timing-window dates are all observed.

    Equation / rule: reindex to the full calendar span, select timing-window dates,
    and require every selected value in each assessment year to be nonmissing.

    Parameters
    ----------
    flow : Series
        One daily observation [caller-supplied unit] per DatetimeIndex entry.
    wys : list[int]
        Requested starting-year labels [calendar year], output order retained.
    ys, sm, em : int
        Assessment start and inclusive timing endpoint months [calendar month].
    sd, ed : int or None
        Inclusive timing endpoint days [day of month].

    Returns
    -------
    list[int]
        One completeness flag [dimensionless] per requested year; empty wys gives
        []. Missing calendar dates or NaN in the window yield 0; outside NaN is ignored.

    Assumptions
    -----------
    The constructed daily calendar is timezone-naive; no timezone conversion is
    performed, so timezone-aware inputs can fail alignment. A year with no selected
    dates returns 0. Reindexing assumes unique dates. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::complete_years. Completeness policy and timezone
    contract: TBD, needs scientist review."""
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
    """Summarize duration ratios over complete assessment years.

    Equation / rule: median retained DR, rounded to 3 decimals; near misses count
    0.5 <= DR < 1, with all DR < 1 as their denominator count.

    Parameters
    ----------
    dr : list[float or None]
        Duration ratios [dimensionless], one per assessment year.
    complete : list[int]
        Aligned completeness flags [dimensionless], same length as dr.

    Returns
    -------
    dict
        dr: median [dimensionless]; nm: [near misses, failures] [year counts].
        Both are None if no complete non-None ratio remains. No index/timezone applies.

    Assumptions
    -----------
    None is excluded; NaN is retained in the median but compares false to failure
    bounds. Exactly 0.5 is near; exactly 1 is successful. Mismatched lengths raise
    through strict zip. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::year_summary. Rationale for near-miss bounds and
    median summary: TBD, needs scientist review."""
    import numpy as np

    v = [x for x, c in zip(dr, complete, strict=True) if c and x is not None]
    fail = [x for x in v if x < 1]
    return {"dr": round(float(np.median(v)), 3) if v else None, "nm": [sum(1 for x in fail if x >= 0.5), len(fail)] if v else None}


def window_metrics(
    years: dict[str, Any], gaps: list[dict[str, Any]], mrip: int, target: Any, w0: Any, w1: Any, record: tuple[Any, Any] | None = None
) -> dict[str, Any]:
    """Compute achievement and gap metrics within an inclusive date window.

    Equation / rule: FR = (achieved/n)/(target/100); ET = in-window days beyond
    MRIP / observed uncensored window days; severity = longest overlapping full
    gap/MRIP. DR is the median over included complete years. FR/ET round to 4
    places, severity to 2, DR to 3.

    Parameters
    ----------
    years : dict
        Parallel wy, complete, achieved and dr lists [year labels, flags, flags,
        dimensionless ratios]; ystart is the starting month (default 7).
    gaps : list[dict]
        start/end date scalars, inclusive days [days], and censored flags.
    mrip : int
        Maximum inter-event period [days].
    target : scalar
        Target achievement frequency [%]; numeric coercion is retained.
    w0, w1 : date-like scalars
        Inclusive window endpoints [calendar dates], timezone-compatible with gaps.
    record : pair of date-like scalars or None
        Inclusive record bounds [calendar dates]; otherwise use the gap span.

    Returns
    -------
    dict
        n [years], fr/et/sev/dr [dimensionless or None]. Only whole assessment years
        within the window enter n/FR/DR. Absent gaps leave ET/severity None.

    Assumptions
    -----------
    No timezone normalization is performed. None DR is excluded, NaN is retained.
    Gap lists are nonoverlapping and day counts consistent; overlaps can double
    count and ET is not capped. A gap contributes excess from start+MRIP through
    end, inclusive. Severity uses its entire length and original window overlap,
    including when explicit record bounds clip the ET denominator. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::window_metrics. Scientific aggregation and clipping
    policy: TBD, needs scientist review."""
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
    """Format one pandas-compatible timestamp as a local calendar date.

    Equation / rule: pd.Timestamp(value).strftime("%Y-%m-%d").

    Parameters
    ----------
    value : scalar
        Date-like value [calendar date], or numeric timestamp [ns since Unix epoch].
        No array/index is accepted by this scalar contract. A timezone-aware input
        keeps its local date; no timezone conversion or normalization is requested.

    Returns
    -------
    str
        YYYY-MM-DD [calendar date]. Time of day is discarded. None, NaT and blank
        strings raise ValueError during formatting; invalid date strings also raise.

    Assumptions
    -----------
    accepts pandas relative strings; output depends on wall time for 'now'/'today'
    Explicit dates/numeric timestamps are deterministic. No input is mutated.
    Boundaries are the input timezone's calendar date, not UTC day boundaries.

    References
    ----------
    pre-refactor:src/server.py::_iso_date. Input-validation policy and suitability
    of relative strings: TBD, needs scientist review."""
    import pandas as pd

    return cast(str, pd.Timestamp(value).strftime("%Y-%m-%d"))


def record_coverage(flow: Any, complete: list[int]) -> dict[str, Any]:
    """Count missing calendar dates and report the supplied completeness summary.

    Equation / rule: Reindex daily between observed index min/max and count NaN; separately sum
    complete flags and report their length.

    Parameters
    ----------
    flow : Series
        Observations [caller units] on a DatetimeIndex. Original timezone is kept;
        absent dates and existing NaN both count missing after daily reindexing.
    complete : list[int]
        Caller-supplied assessment-year flags [dimensionless], independently trusted.

    Returns
    -------
    dict
        start/end [inclusive ISO local dates or None], missing_days [days],
        complete_years/years [counts]. Empty flow has zero missing days and None
        endpoints while still summing/length-counting supplied flags.

    Assumptions
    -----------
    No input is mutated or timezone converted. Duplicate/non-date indexes are not
    repaired. Completeness is not re-derived from missingness; no numerical
    threshold is applied. Nonempty all-NaT indexes can still raise.

    References
    ----------
    pre-refactor:src/server.py::record_coverage. Scientific provenance or
    suitability of this retained convention: TBD, needs scientist review."""
    import pandas as pd

    if flow.empty:
        return {"start": None, "end": None, "missing_days": 0, "complete_years": sum(complete), "years": len(complete)}
    start, end = flow.index.min(), flow.index.max()
    daily = flow.reindex(pd.date_range(start, end))
    return {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "missing_days": int(daily.isna().sum()),
        "complete_years": sum(complete),
        "years": len(complete),
    }
