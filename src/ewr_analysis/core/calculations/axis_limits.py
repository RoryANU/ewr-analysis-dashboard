import math


def nice_ceiling(x: float) -> int:
    """Choose an integer upper axis limit on a two-significant-figure grid.

    Equation / rule: EQ-AXIS-01. For positive x, use grid spacing
    10**max(floor(log10(x)) - 1, 0), and round x upward on that grid.

    Parameters
    ----------
    x : float
        Scalar axis value [caller units]. No index or timezone applies.

    Returns
    -------
    int
        Upper limit [same units], at least one for positive finite inputs.
        Returns the legacy sentinel 100 for falsy or nonpositive inputs,
        including zero, negative infinity and None despite the float annotation.

    Assumptions
    -----------
    Positive inputs are finite. NaN and positive infinity retain their existing
    ValueError/OverflowError behavior; no validation is added. Grid boundaries
    are inclusive: a value already on the grid is returned unchanged.
    No input is mutated. The sentinel is a display choice, not a physical bound.

    References
    ----------
    pre-refactor:src/server.py::nice_ceiling. Origin of the display grid/sentinel:
    TBD, needs scientist review."""
    if not x or x <= 0:
        return 100
    k = 10 ** max(int(math.floor(math.log10(x))) - 1, 0)
    return int(math.ceil(x / k) * k)
