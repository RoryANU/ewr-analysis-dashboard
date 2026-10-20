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
    """True when any EWR at any gauge has at least one threshold event (events: gauge -> pu -> code -> year -> event list)."""
    return any(len(lst) > 0 for by_pu in events.values() for by_code in by_pu.values() for by_year in by_code.values() for lst in by_year.values())


def _dateless_gap_stats(key: tuple[str, str, str], record_days_total: Any, mrip: int, inter_by_ewr: dict, succ_by_ewr: dict) -> dict[str, Any]:
    """ETP and gap severity for a run with no dates (ten-thousand-year runs): py_ewr's gap lengths, or the whole record as one
    gap when nothing succeeded."""
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
    """How far the gaps between successful events overshoot the maximum inter-event period, from the uncensored gaps:
    sev = longest interior gap / MRIP (all gaps when none is interior), sev_b = longest gap including those from the start or to the end of
    the record / MRIP, eps = gaps longer than MRIP (episodes). All None without a maximum or an uncensored gap."""
    known = [g for g in gaps if not g["censored"]]
    if not mrip or not known:
        return {"sev": None, "sev_b": None, "eps": None}
    inner = [g["days"] for g in known if not g["boundary"]] or [g["days"] for g in known]
    return {"sev": round(max(inner) / mrip, 2), "sev_b": round(max(g["days"] for g in known) / mrip, 2), "eps": sum(1 for g in known if g["excess"])}


def ewr_gaps(ev: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    """py_ewr's gaps between successful events (including the gaps from the start of the record to the first event and from the
    last event to the end, as py_ewr counts them), each with the days beyond the maximum inter-event period. Two corrections to
    what py_ewr returns:
    - an EWR with no successful event in the whole record gets one gap covering the record (py_ewr returns none, which would read
      as "never longer than the maximum");
    - a gap containing a missing day has an unknown length: it is kept but marked `censored` and counts nowhere.
    Returns (gaps, etp): etp = days beyond the maximum in the uncensored gaps / days of record outside the censored gaps (the same
    spans in both), or None when the EWR has no maximum or nothing is left to count."""
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
    """Runs of consecutive days where `hit` is true in the daily series `s` (missing days are never hits and break a run; a run
    beside a missing day or the record's ends is `censored`: its true length is unknown). Per water year (1 Jul - 30 Jun,
    labelled by its first calendar year as in py_ewr): the days that are hits, the longest run inside that year (a run crossing
    30 June is split at the year boundary for this) and the number of runs starting in it; numbers only for complete water years
    (every day observed). Returns (years, runs)."""
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
