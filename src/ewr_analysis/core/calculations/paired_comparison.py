import math
from typing import cast


def mcnemar_p(gained: int, lost: int) -> float | None:
    """Two-sided exact McNemar p-value for paired binary achievement years.

    Equation / rule: EQ-PAIR-01. For n = gained + lost, return
    round(min(1, 2 * sum(comb(n, k), k=0..min(gained, lost)) / 2**n), 4).

    Parameters
    ----------
    gained, lost : int
        Discordant paired-year counts [years]; scalar nonnegative integers.
        No index or timezone applies. Counts are not validated or NaN-filtered.

    Returns
    -------
    float or None
        Two-sided probability [dimensionless], rounded to four decimal places;
        None when n == 0. Equal counts are included in the lower tail.

    Assumptions
    -----------
    Pairs are independent and exchangeable under a fair split of discordances.
    The probability is capped at one; small probabilities may round to zero.
    No input is mutated. Agreement with the legacy calculation does not establish
    that the independence assumption holds for a particular river record.

    References
    ----------
    pre-refactor:src/server.py::mcnemar_p and the independent exact enumeration
    in tests/core/test_leaf_calculations.py. Scientific provenance and suitability:
    TBD, needs scientist review."""
    n = gained + lost
    if n == 0:
        return None
    return cast(float, round(min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gained, lost) + 1)) / 2**n), 4))
