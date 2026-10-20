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
    """Resolve a catalog drought interval to inclusive assessment-year dates.

    Equation / rule: EQ-DROUGHT-DATES-01. Start at date(w0, ystart, 1);
    end one day before date(w1 + 1, ystart, 1).

    Parameters
    ----------
    key : str
        Drought identifier [catalog key] in the module-level DROUGHTS constant.
    ystart : int
        Assessment-year start month [calendar month], default 7 (July).

    Returns
    -------
    tuple of datetime.date
        Inclusive first and last calendar dates [dates], shape (2,). No index
        or timezone applies; dates contain no time-of-day component.

    Assumptions
    -----------
    DROUGHTS is stable and read only. Both catalog year labels are included;
    the assessment year starts on the first of ystart. Invalid keys/months retain
    native KeyError/ValueError behavior. NaN values are not normalized. No input
    or catalog entry is mutated. Catalog values are unchanged in this slice and
    will migrate to configuration later.

    References
    ----------
    pre-refactor:src/drought_windows.py and its declared catalog labels.
    Historical window provenance: TBD, needs scientist review."""
    _, w0, w1 = DROUGHTS[key]
    return dt.date(w0, ystart, 1), dt.date(w1 + 1, ystart, 1) - dt.timedelta(days=1)


def meta() -> list[dict[str, object]]:
    """Project the drought catalog into the dashboard's metadata rows.

    Equation / rule: EQ-DROUGHT-META-01; one k/name/w0/w1 mapping per catalog entry.

    Parameters
    ----------
    None
        The module-level DROUGHTS constant is the sole data input.

    Returns
    -------
    list of dict
        One row per catalog entry, in insertion order: k [catalog key], name
        [label], w0 and w1 [starting calendar-year labels]. No date index or
        timezone applies. Rows contain the unchanged inclusive endpoint labels.

    Assumptions
    -----------
    Catalog entries have the existing key/name/integer-year structure; there is
    no validation or NaN normalization. Each result list and row dict is newly
    constructed. The catalog is not mutated. The authorized constant-table
    input will migrate to configuration later, without conversion in this slice.

    References
    ----------
    pre-refactor:src/drought_windows.py::meta and the existing dashboard metadata
    contract. Historical catalog provenance: TBD, needs scientist review."""
    return [{"k": k, "name": n, "w0": w0, "w1": w1} for k, (n, w0, w1) in DROUGHTS.items()]
