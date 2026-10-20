from typing import Any


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
