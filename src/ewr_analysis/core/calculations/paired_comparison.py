import math
from typing import cast


def mcnemar_p(gained: int, lost: int) -> float | None:
    """Exact two-sided McNemar p-value for paired yes/no years: under no change the discordant years (achieved in one scenario only) split
    50:50, so p = min(1, 2 * P(X <= min(gained, lost))) with X ~ Binomial(gained + lost, 1/2). None when no year differs."""
    n = gained + lost
    if n == 0:
        return None
    return cast(float, round(min(1.0, 2 * sum(math.comb(n, k) for k in range(min(gained, lost) + 1)) / 2**n), 4))
