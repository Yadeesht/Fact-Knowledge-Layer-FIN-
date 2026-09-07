import math

def approximately_equal(
    a: float,
    b: float,
    relative_tol: float = 0.001,  # 0.1% tolerance
    abs_tol: float = 1e-9,
) -> bool:
    """
    Checks if two numerical values are approximately equal within a financial materiality threshold.
    Prevents false contradictions caused by reporting rounding discrepancies (e.g. ₹8,142 Cr vs ₹81,424 Mn).
    """
    if a is None or b is None:
        return False

    abs_diff = abs(a - b)
    if abs_diff <= abs_tol:
        return True

    denominator = max(abs(a), abs(b), 1.0)
    relative_diff = abs_diff / denominator

    return relative_diff <= relative_tol


def calculate_relative_difference(a: float, b: float) -> float:
    if a is None or b is None:
        return 0.0
    denominator = max(abs(a), abs(b), 1.0)
    return abs(a - b) / denominator
