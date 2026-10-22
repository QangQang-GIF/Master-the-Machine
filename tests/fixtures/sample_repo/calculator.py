"""Simple calculator module for fixture testing."""


def calculate_discount(price: float, threshold: float = 100.0, rate: float = 0.1) -> float:
    """Apply discount when price meets or exceeds threshold.

    Bug: uses > instead of >= so exact-threshold prices don't get the discount.
    """
    if price > threshold:  # BUG: should be >=
        return round(price * (1 - rate), 2)
    return price
