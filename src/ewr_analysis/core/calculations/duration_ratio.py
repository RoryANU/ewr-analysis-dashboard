from typing import Any

from ewr_analysis.core.calculations.assessment_years import _timing_mask


def dr_years(yr: Any, code: str, dur: Any, low: bool, per_year: Any, events: dict[Any, list[Any]] | None = None) -> list[float | None]:
    """Calculate annual duration ratios from event lengths or total hit days.

    Equation / rule: round(min(days/duration, 2), 3); low flow uses total days,
    otherwise longest event, or k-th longest nonempty event when k > 1 and supplied.

    Parameters
    ----------
    yr : DataFrame
        Rows are assessment-year labels, columns [<code>_]maxEventDays/totalEventDays
        [days]. No datetime index or timezone is required for these year aggregates.
    code : str
        Optional column prefix [label].
    dur, per_year : scalar
        Required duration [days] and events per year [count]; coercion follows pandas
        and int. Invalid/nonpositive duration gives None per row.
    low : bool
        Use annual total days instead of event length when true.
    events : dict or None
        Year -> list of event sequences, lengths [days]; empty events are ignored.

    Returns
    -------
    list[float or None]
        Ratios [dimensionless] in row order, capped at 2 and rounded to 3 places.
        Missing length is None; fewer than k events gives 0. No completeness filter.

    Assumptions
    -----------
    Positive fractional per_year is truncated; a numeric string such as '2.0' can
    pass numeric coercion but raise during int conversion. Negative lengths are not
    clipped to zero. No calendar boundaries are evaluated and no input is mutated.

    References
    ----------
    pre-refactor:src/server.py::dr_years. Duration/low-flow/k-event conventions:
    TBD, needs scientist review."""
    import pandas as pd

    dur = pd.to_numeric(dur, errors="coerce")
    if pd.isna(dur) or dur <= 0:
        return [None] * len(yr)
    k = int(per_year) if not pd.isna(pd.to_numeric(per_year, errors="coerce")) and int(per_year) > 1 else 1
    col = (f"{code}_" if code else "") + ("totalEventDays" if low else "maxEventDays")  # ewr_eval's years drop the code prefix
    out: list[float | None] = []
    for w in yr.index:
        days: Any
        if k > 1 and not low and events is not None:
            lens = sorted((len(e) for e in events.get(w, []) if e), reverse=True)
            days = lens[k - 1] if len(lens) >= k else 0
        else:
            days = pd.to_numeric(yr.at[w, col], errors="coerce") if col in yr else float("nan")
        out.append(None if pd.isna(days) else round(min(float(days) / float(dur), 2.0), 3))
    return out


def vr_years(flow: Any, wys: list[int], ys: int, sm: int, em: int, acc: Any, vol: Any, sd: int | None = None, ed: int | None = None) -> list[float | None]:
    """Calculate annual volume ratios from maximum rolling sums within timing bounds.

    Equation / rule: round(min(best rolling sum / volume threshold, 2), 3), with
    integer accumulation width and all rows required nonmissing.

    Parameters
    ----------
    flow : Series
        Daily flow [ML/d] on an ordered DatetimeIndex, one row per calendar day.
    wys : list[int]
        Requested starting-year labels [calendar year], returned in this order.
    ys, sm, em : int
        Assessment-year start and inclusive timing months [calendar month].
    acc, vol : scalar
        Accumulation width [days] and volume threshold [ML]; numeric coercion applies.
    sd, ed : int or None
        Inclusive timing endpoint days [day of month].

    Returns
    -------
    list[float or None]
        Best volume/threshold [dimensionless], capped at 2, rounded to 3 places.
        None for invalid acc/vol or absent complete rolling windows.

    Assumptions
    -----------
    Uses local index fields; no timezone conversion or daily reindexing. Outside
    window observations become NaN; NaN breaks a rolling sum, which cannot cross
    assessment years. Fractional acc >= 1 truncates via int. Rows are assumed daily;
    missing calendar dates are not inserted. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::vr_years. Daily-to-volume interpretation and rolling
    policy: TBD, needs scientist review."""
    import pandas as pd

    acc, vol = pd.to_numeric(acc, errors="coerce"), pd.to_numeric(vol, errors="coerce")
    if pd.isna(acc) or pd.isna(vol) or acc < 1 or vol <= 0:
        return [None] * len(wys)
    m = flow.index.month
    win = flow.where(_timing_mask(flow.index, sm, em, sd, ed))
    total = win.groupby(flow.index.year - (m < ys)).rolling(int(acc), min_periods=int(acc)).sum()
    best = total.groupby(level=0).max()
    return [None if pd.isna(best.get(w)) else round(min(float(best[w]) / float(vol), 2.0), 3) for w in wys]


def dr_approx(ael: list[float | None], prm: dict[str, Any]) -> list[float | None]:
    """Approximate duration ratio from mean event length for review scenarios.

    Equation / rule: round(min(mean event length / required duration, 2), 3).

    Parameters
    ----------
    ael : list[float or None]
        Scenario mean event lengths [days], including successful and unsuccessful
        events. No calendar index or timezone applies.
    prm : dict
        dur [days] and kind [label], defaulting kind to F (flow).

    Returns
    -------
    list[float or None]
        Ratios [dimensionless] aligned to scenarios; None lengths stay None. Nonflow
        kind or falsy duration gives all None. Values are capped at 2, not at zero.

    Assumptions
    -----------
    NaN is not filtered. Unlike dr_years, a truthy negative duration is accepted,
    while a numeric string can raise in division. No annual completeness or date
    boundary evaluation is performed. No input is mutated.

    References
    ----------
    pre-refactor:src/build_dashboard.py::dr_approx, whose comment identifies
    py_ewr AverageEventLength. Approximation suitability: TBD, needs scientist review."""
    dur = prm.get("dur")
    if prm.get("kind", "F") != "F" or not dur:
        return [None] * len(ael)
    return [None if a is None else round(min(a / dur, 2.0), 3) for a in ael]
