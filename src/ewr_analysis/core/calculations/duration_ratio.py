from typing import Any

from ewr_analysis.core.calculations.assessment_years import _timing_mask


def dr_years(yr: Any, code: str, dur: Any, low: bool, per_year: Any, events: dict[Any, list[Any]] | None = None) -> list[float | None]:
    """Duration ratio of each assessment year (py_ewr's yearly results `yr`, columns [<code>_]maxEventDays / totalEventDays): the longest
    event in the year ÷ the required duration, capped at 2. Low-flow (any-days) EWRs use the days at or above the threshold in the year;
    an EWR needing k > 1 events a year uses its k-th longest event (from `events`, py_ewr's events by year; 0 when fewer than k)."""
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
    """Volume ratio of each assessment year for a volume EWR: the largest sum of daily flow over AccumulationPeriod consecutive days within
    the timing window ÷ VolumeThreshold, capped at 2."""
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
    """The review results have no yearly event lengths, so the duration ratio is approximated per scenario as the mean event length ÷ the
    required duration, capped at 2 (py_ewr's AverageEventLength averages every event, successful or not). Flow EWRs only."""
    dur = prm.get("dur")
    if prm.get("kind", "F") != "F" or not dur:
        return [None] * len(ael)
    return [None if a is None else round(min(a / dur, 2.0), 3) for a in ael]
