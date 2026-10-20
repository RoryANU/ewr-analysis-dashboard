import math


def nice_ceiling(x: float) -> int:
    """Round x up to two significant figures, and at least to a whole number (1234 -> 1300, 57.2 -> 58), for a tidy axis limit; 100 when x is not positive."""
    if not x or x <= 0:
        return 100
    k = 10 ** max(int(math.floor(math.log10(x))) - 1, 0)
    return int(math.ceil(x / k) * k)
