from typing import Any


def exceed_pct(sorted_vals: Any, x: Any) -> Any:
    """Percentage of observations equal to or greater than each threshold.

    Equation / rule: EQ-FDC-01; 100 * (n - searchsorted(values, x, left)) / n.

    Parameters
    ----------
    sorted_vals : array-like
        One-dimensional ascending observations [caller units], length n. No
        date index or timezone is used. Sorting is the caller's responsibility.
    x : scalar or array-like
        Thresholds [same units], with any shape supported by NumPy searchsorted.

    Returns
    -------
    scalar or ndarray
        Exceedance percentage [%], with the shape of x. Equality is included by
        searching the left edge of duplicate observations. No rounding is applied.

    Assumptions
    -----------
    All observations have equal weight. No NaN filtering or missing-value
    normalization is performed; NumPy's ordering is preserved. An empty record
    and nonempty query retain NaN results and the invalid-divide warning.
    Inputs are not mutated; an empty query remains empty.

    References
    ----------
    pre-refactor:src/server.py::exceed_pct; NumPy searchsorted with side='left'.
    Scientific provenance and missing-data policy: TBD, needs scientist review."""
    import numpy as np

    n = len(sorted_vals)
    return 100.0 * (n - np.searchsorted(sorted_vals, x, side="left")) / n
