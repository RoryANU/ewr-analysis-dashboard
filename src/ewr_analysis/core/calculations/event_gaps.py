from typing import Any

from ewr_analysis.core.calculations.assessment_years import _iso_date, _water_year, _water_year_days


def _mrip_days(max_inter_event_years: Any) -> int:
    """Convert a maximum inter-event period to the legacy integer day count.

    Equation / rule: EQ-MRIP-01. For a positive value, int(round(float(years)
    * 365)); otherwise zero. This is not a calendar-year conversion.

    Parameters
    ----------
    max_inter_event_years : scalar
        Maximum inter-event period [years]. No date index or timezone applies.
        Comparison with zero occurs before float conversion.

    Returns
    -------
    int
        Maximum inter-event period [days]. Nonpositive values and floating NaN
        return zero. Python's round-to-even rule applies at exact half days.

    Assumptions
    -----------
    Positive inputs are finite and numeric. None and an empty string are not
    accepted: the legacy comparison raises TypeError. Positive infinity also
    retains its conversion error. No missing-value normalization or input mutation
    is performed. The positivity boundary is strict; zero returns zero.

    References
    ----------
    pre-refactor:src/server.py::_mrip_days. Scientific basis for the 365-day
    convention and rounding choice: TBD, needs scientist review."""
    return int(round(float(max_inter_event_years) * 365)) if max_inter_event_years > 0 else 0


def _has_any_event(events: dict) -> bool:
    """Test whether a nested event inventory contains a nonempty yearly list.

    Equation / rule: any(len(year_event_list) > 0) across gauge/PU/EWR/year entries.

    Parameters
    ----------
    events : dict
        Gauge -> planning unit -> code -> year -> list of events [labels/counts].
        No calendar index or timezone is consulted; event contents are not inspected.

    Returns
    -------
    bool
        Presence flag [dimensionless]. Empty mappings/lists return False, but a list
        containing an empty event is still nonempty and returns True.

    Assumptions
    -----------
    The nested mapping/list shape is valid. No NaN filtering, date-boundary test,
    success test or threshold test is performed. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_has_any_event. Empty-event interpretation:
    TBD, needs scientist review."""
    return any(len(lst) > 0 for by_pu in events.values() for by_code in by_pu.values() for by_year in by_code.values() for lst in by_year.values())


def _dateless_gap_stats(key: tuple[str, str, str], record_days_total: Any, mrip: int, inter_by_ewr: dict, succ_by_ewr: dict) -> dict[str, Any]:
    """Summarize excess gap days for a record without calendar dates.

    Equation / rule: ET = min(1, sum(max(0, length-MRIP))/record_days_total), rounded
    to 4 places. When no event succeeded, the whole record is one boundary gap.

    Parameters
    ----------
    key : tuple[str, str, str]
        Gauge, planning unit and EWR identifiers [labels].
    record_days_total, mrip : scalar
        Record length and maximum inter-event period [days].
    inter_by_ewr, succ_by_ewr : dict
        Keyed in-memory summaries; interEventLength [days] is coerced to numeric and
        NaN dropped. Success is key membership, even for an empty DataFrame value.

    Returns
    -------
    dict
        et/sev/sev_b [dimensionless or None], eps [episodes or None]. No date index
        or timezone applies. ET needs positive record length and truthy MRIP.

    Assumptions
    -----------
    Successful records supply no dated gap objects here, so severity/episodes remain
    None even if ET is positive. No-success record supplies severity through
    gap_severity. Threshold equality has zero excess. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_dateless_gap_stats. Dateless record and missing
    severity policy: TBD, needs scientist review."""
    import pandas as pd

    succeeded = key in succ_by_ewr
    if not succeeded:
        gap_lengths = [record_days_total]
    elif key in inter_by_ewr:
        gap_lengths = pd.to_numeric(inter_by_ewr[key].interEventLength, errors="coerce").dropna().tolist()
    else:
        gap_lengths = []

    countable = mrip and record_days_total > 0
    etp = round(min(1.0, sum(max(0.0, x - mrip) for x in gap_lengths) / record_days_total), 4) if countable else None
    gaps = [{"days": record_days_total, "boundary": True, "censored": False, "excess": max(0, record_days_total - mrip)}] if not succeeded and countable else []
    return {"et": etp, **gap_severity(gaps, mrip)}


def gap_severity(gaps: list[dict[str, Any]], mrip: int) -> dict[str, Any]:
    """Scale uncensored gap lengths by MRIP and count supplied excess episodes.

    Equation / rule: sev = max interior days/MRIP (fall back to all known gaps);
    sev_b = max all known days/MRIP; round both to 2 places. eps counts truthy excess.

    Parameters
    ----------
    gaps : list[dict]
        days/excess [days], boundary/censored [flags]; dates and timezone are unused.
    mrip : int
        Maximum inter-event period [days], assumed positive when nonzero.

    Returns
    -------
    dict
        sev/sev_b [dimensionless], eps [episodes]; all None when MRIP is falsy or
        every gap is censored/absent.

    Assumptions
    -----------
    Censored gaps are excluded. Equality to MRIP only gives no episode when the
    supplied excess is zero; excess is not recomputed. No NaN filtering is performed
    on supplied lengths/excess. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::gap_severity. Severity and episode interpretation:
    TBD, needs scientist review."""
    known = [g for g in gaps if not g["censored"]]
    if not mrip or not known:
        return {"sev": None, "sev_b": None, "eps": None}
    inner = [g["days"] for g in known if not g["boundary"]] or [g["days"] for g in known]
    return {"sev": round(max(inner) / mrip, 2), "sev_b": round(max(g["days"] for g in known) / mrip, 2), "eps": sum(1 for g in known if g["excess"])}


def ewr_gaps(ev: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    """Describe inclusive gaps between successful events and their excess fraction.

    Equation / rule: excess = max(0, gap_days-MRIP); ETP = sum uncensored excess /
    (record row count - full censored gap days), capped at 1 and rounded to 4 places.

    Parameters
    ----------
    ev : dict
        flow: nonempty daily Series [caller-supplied observation unit], DatetimeIndex;
        mrip_days [days]; inter: DataFrame with startDate/endDate; succ: DataFrame
        whose emptiness indicates no successful event. Dates must be timezone-compatible.

    Returns
    -------
    (list[dict], float or None)
        Gaps contain ISO start/end [local calendar dates], inclusive days/excess
        [days], censored/boundary/no_success flags. ETP [dimensionless] is None for
        falsy MRIP or no countable denominator. With no success, use one whole-record gap.

    Assumptions
    -----------
    NaN anywhere in a gap censors its whole span; absent dates are not inserted.
    Boundary includes equality to first/last date. Spans are expected nonoverlapping;
    row count must represent calendar days. Empty flow raises IndexError. _iso_date
    retains its authorized relative-string clock dependency; normal inputs are dates.
    No input is mutated or timezone converted.

    References
    ----------
    pre-refactor:src/server.py::ewr_gaps. Scientific censoring, boundary and ETP
    conventions: TBD, needs scientist review."""
    import pandas as pd

    flow, mrip, inter = ev["flow"], ev["mrip_days"], ev["inter"]
    first, last = flow.index[0], flow.index[-1]
    no_success = not len(ev["succ"])
    if no_success:
        spans = [(first, last)]
    else:
        spans = [(pd.Timestamp(a), pd.Timestamp(b)) for a, b in zip(inter.startDate, inter.endDate, strict=True)] if len(inter) else []

    gaps = []
    for start, end in spans:
        days = (end - start).days + 1
        censored = bool(flow.loc[start:end].isna().any())
        gaps.append(
            {
                "start": _iso_date(start),
                "end": _iso_date(end),
                "days": days,
                "censored": censored,
                "boundary": bool(start <= first or end >= last),
                "excess": None if censored else (max(0, days - mrip) if mrip else 0),
                "no_success": no_success,
            }
        )

    counted_days = len(flow) - sum(g["days"] for g in gaps if g["censored"])
    etp = round(min(1.0, sum(g["excess"] for g in gaps if not g["censored"]) / counted_days), 4) if mrip and counted_days > 0 else None
    return gaps, etp


def _runs_by_year(s: Any, hit: Any) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Find hit runs and summarize complete July-start water years.

    Equation / rule: contiguous true rows form runs; NaN breaks them. A run's
    starting year receives its count; hit days/longest runs split at July 1.

    Parameters
    ----------
    s : Series
        Daily observations [caller-supplied unit], ordered DatetimeIndex; missing
        observations are NaN. Local calendar dates are retained without conversion.
    hit : boolean ndarray
        Shape (len(s),), threshold predicate [dimensionless]; NaN entries are cleared.

    Returns
    -------
    (dict, list[dict])
        Parallel wy/complete/days/longest/n lists [starting-year labels, flags, days,
        days, runs]. Incomplete years have None metrics. Runs contain inclusive ISO
        start/end [dates], days [days], censored [flag], wy [starting-year label].

    Assumptions
    -----------
    Caller supplies one row per calendar day. Adjacency and completeness check row
    counts, not uniqueness or calendar continuity. Runs touching a record edge or
    neighboring NaN are censored. Empty input returns empty lists. _iso_date retains
    its authorized relative-string clock dependency; normal inputs are dates.
    No input is mutated; leap water years require 366 observed rows.

    References
    ----------
    pre-refactor:src/server.py::_runs_by_year. Run/censoring and annual assignment
    policy: TBD, needs scientist review."""
    import numpy as np

    values = s.to_numpy(dtype=float)
    missing = np.isnan(values)
    hit = hit & ~missing
    wy = np.array([_water_year(d) for d in s.index])
    n = len(values)

    runs: list[dict[str, Any]] = []
    i = 0
    while i < n:
        if not hit[i]:
            i += 1
            continue
        j = i  # extend to the run's last day
        while j + 1 < n and hit[j + 1]:
            j += 1
        touches_gap_or_edge = i == 0 or j == n - 1 or missing[i - 1] or (j + 1 < n and missing[j + 1])
        runs.append({"start": _iso_date(s.index[i]), "end": _iso_date(s.index[j]), "days": j - i + 1, "censored": bool(touches_gap_or_edge), "wy": int(wy[i])})
        i = j + 1

    starts: dict[int, int] = {}
    for r in runs:
        starts[r["wy"]] = starts.get(r["wy"], 0) + 1

    years: dict[str, list[Any]] = {"wy": [], "days": [], "longest": [], "n": [], "complete": []}
    for w in np.unique(wy):
        in_year = wy == w
        complete = bool(in_year.sum() == _water_year_days(int(w)) and not missing[in_year].any())
        year_hits, longest, current = hit[in_year], 0, 0
        for is_hit in year_hits:
            current = current + 1 if is_hit else 0
            longest = max(longest, current)
        years["wy"].append(int(w))
        years["complete"].append(int(complete))
        years["days"].append(int(year_hits.sum()) if complete else None)
        years["longest"].append(longest if complete else None)
        years["n"].append(starts.get(int(w), 0) if complete else None)
    return years, runs
