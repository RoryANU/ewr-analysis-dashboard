from typing import Any


def exceed_pct(sorted_vals: Any, x: Any) -> Any:
    """% of days the flow is equal to or above x (sorted_vals ascending)."""
    import numpy as np

    n = len(sorted_vals)
    return 100.0 * (n - np.searchsorted(sorted_vals, x, side="left")) / n
