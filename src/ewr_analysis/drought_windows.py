"""Drought windows, defined once for the build (META.droughts, the page's period selector and hydrograph filters) and the server
(window_metrics on uploaded scenarios).

Each window is a run of water years (1 July - 30 June) labelled by the year they start in, as py_ewr labels them: (1895, 1902) is
1895-96 to 1902-03. An EWR whose assessment year starts in another month (py_ewr's StartMonth <= 6 and EndMonth >= 7 rule) uses
the same labels from its own year start (window_dates).
"""

import datetime as dt

DROUGHTS: dict[str, tuple[str, int, int]] = {  # key -> (name, first water year, last water year)
    "fed": ("Federation", 1895, 1902),
    "ww2": ("WWII", 1937, 1945),
    "mil": ("Millennium", 1996, 2009),
    "tin": ("Tinderbox", 2017, 2019),
}


def window_dates(key: str, ystart: int = 7) -> tuple[dt.date, dt.date]:
    """First and last day of a drought window for an assessment year starting on the 1st of month `ystart` (7 = July)."""
    _, w0, w1 = DROUGHTS[key]
    return dt.date(w0, ystart, 1), dt.date(w1 + 1, ystart, 1) - dt.timedelta(days=1)


def meta() -> list[dict[str, object]]:
    """The windows as the page reads them (META.droughts)."""
    return [{"k": k, "name": n, "w0": w0, "w1": w1} for k, (n, w0, w1) in DROUGHTS.items()]
