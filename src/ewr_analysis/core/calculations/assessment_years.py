from typing import Any, cast


def _window_days(wy_label: int, sm: int, em: int) -> int:
    """Calendar days of the timing window (months sm..em, may wrap the year end) inside water year `wy_label` (1 Jul - 30 Jun)."""
    import pandas as pd

    m = pd.date_range(f"{wy_label - 1}-07-01", f"{wy_label}-06-30").month.to_numpy()
    return int(((m >= sm) & (m <= em) if sm <= em else (m >= sm) | (m <= em)).sum())


def _water_year(d: Any) -> int:
    """py_ewr's water year label: the calendar year it starts in (1 Jul Y - 30 Jun Y+1)."""
    return cast(int, d.year if d.month >= 7 else d.year - 1)


def record_days(s: Any) -> Any:
    """A gauge's series from its first to its last observed day on every calendar day (missing days NaN), as ewr_eval sees it; None when the
    index is not dates (ten-thousand-year runs)."""
    import pandas as pd

    if s is None or not isinstance(s.index, pd.DatetimeIndex):
        return None
    s = s.loc[s.first_valid_index() : s.last_valid_index()] if s.notna().any() else s.iloc[:0]
    return s if s.empty else s.reindex(pd.date_range(s.index[0], s.index[-1]))


def _feb29(w: int) -> bool:
    """True when water year w (1 Jul w - 30 Jun w+1) holds a 29 February."""
    import calendar

    return calendar.isleap(w + 1)


def _water_year_days(w: int) -> int:
    """Calendar days in water year w (1 Jul w - 30 Jun w+1)."""
    return 366 if _feb29(w) else 365
