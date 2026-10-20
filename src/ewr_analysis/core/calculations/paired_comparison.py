import math
from typing import Any, cast


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


def _paired_achievement(base: dict[str, Any], comp: dict[str, Any]) -> dict[str, Any]:
    """Compare achievement over years complete in both scenarios.

    Equation / rule: intersect complete labels, count gains/losses, calculate
    100 * sum(achievement)/n (3 decimals), and apply the existing exact McNemar test.

    Parameters
    ----------
    base, comp : dict
        Parallel wy/complete/achieved lists [year labels, flags, achievement values].
        Labels must use the same assessment convention; no date index/timezone applies.

    Returns
    -------
    dict
        Sorted wy [labels], n/base/comp/gained/lost [years], pct_base/pct_comp [% or
        None], p_mcnemar [dimensionless or None]. No common year gives zero counts
        and None percentages/p-value. P-value rounding follows mcnemar_p (4 places).

    Assumptions
    -----------
    Achievement is expected binary, but sums retain actual values while gain/loss
    counts use truthiness. Duplicate labels retain the last complete row's value.
    No NaN filtering or shape validation is performed. No input is mutated.

    References
    ----------
    pre-refactor:src/server.py::_paired_achievement and core mcnemar_p.
    Scientific pairing and independence assumptions: TBD, needs scientist review."""
    base_index = {w: i for i, w in enumerate(base["wy"]) if base["complete"][i]}
    comp_index = {w: i for i, w in enumerate(comp["wy"]) if comp["complete"][i]}
    common = sorted(set(base_index) & set(comp_index))
    base_achieved = [base["achieved"][base_index[w]] for w in common]
    comp_achieved = [comp["achieved"][comp_index[w]] for w in common]
    n = len(common)
    gained = sum(1 for b, c in zip(base_achieved, comp_achieved, strict=True) if not b and c)
    lost = sum(1 for b, c in zip(base_achieved, comp_achieved, strict=True) if b and not c)
    return {
        "wy": common,
        "n": n,
        "base": int(sum(base_achieved)),
        "comp": int(sum(comp_achieved)),
        "gained": gained,
        "lost": lost,
        "pct_base": round(100 * sum(base_achieved) / n, 3) if n else None,
        "pct_comp": round(100 * sum(comp_achieved) / n, 3) if n else None,
        "p_mcnemar": mcnemar_p(gained, lost),
    }
