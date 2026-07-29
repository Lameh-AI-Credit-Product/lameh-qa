"""
Lameh Intelligence - shared numeric comparison metrics
=======================================================
Exact/tolerance/MAPE comparison between an agent-stated value and a
ground-truth value. Lives here (not duplicated in correctness.py) since
helpfulness/report code may eventually want the same comparison primitives.

Swap this out for the metric logic from the other extraction QA framework
if/when that's shared - the call signatures here are intentionally small so
that's a drop-in replacement.
"""

DEFAULT_TOLERANCE = 0.01  # 1% relative tolerance


def exact_match(actual, expected, eps=1e-9):
    if actual is None or expected is None:
        return False
    return abs(actual - expected) < eps


def tolerance_match(actual, expected, tolerance=DEFAULT_TOLERANCE):
    """True if `actual` is within `tolerance` (relative) of `expected`.
    Falls back to exact equality when `expected` is 0, since relative
    tolerance is undefined there."""
    if actual is None or expected is None:
        return False
    if expected == 0:
        return actual == 0
    return abs(actual - expected) <= tolerance * abs(expected)


def mape(actual, expected):
    """Mean absolute percentage error for a single pair, as a fraction (0.01 ==
    1%). Returns None when undefined (expected == 0)."""
    if actual is None or expected is None or expected == 0:
        return None
    return abs((actual - expected) / expected)


def compare(actual, expected, tolerance=DEFAULT_TOLERANCE, display_epsilon=None):
    """One-shot comparison bundle used by correctness.py per extracted tuple.

    `display_epsilon` (see extraction.display_precision) is the largest error
    attributable to the value having been *printed* rounded - "1.4%" for a
    true 1.4267% is off by 1.9% relatively while being correct to every digit
    shown. A value within that band passes regardless of `tolerance`, and is
    marked "rounded_match" so it stays visible as the weaker kind of pass:
    it means the stated digits are right, not that the underlying figure was
    checked to full precision.

    Without this, tightening `tolerance` to catch real errors just fails
    every value the agent displayed to one or two decimals - which was every
    reported failure in this suite's first full run."""
    within = tolerance_match(actual, expected, tolerance)
    rounded = False
    if not within and display_epsilon is not None and actual is not None and expected is not None:
        rounded = abs(actual - expected) <= display_epsilon
        within = rounded
    return {
        "actual": actual,
        "expected": expected,
        "exact_match": exact_match(actual, expected),
        "within_tolerance": within,
        "rounded_match": rounded,
        "mape": mape(actual, expected),
    }
