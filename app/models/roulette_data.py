"""Static domain data and pure classification helpers for a European (single-zero) roulette.

No I/O, no dependencies beyond the stdlib — kept trivially unit-testable and reusable by any
future frontend (this display, a future web dashboard, a future REST API).
"""
from __future__ import annotations

# Standard European roulette red-number set (all others 1-36 are black; 0 is green).
RED_NUMBERS = frozenset({
    1, 3, 5, 7, 9, 12, 14, 16, 18,
    19, 21, 23, 25, 27, 30, 32, 34, 36,
})

VALID_NUMBERS = frozenset(range(0, 37))

COLOR_RED = "red"
COLOR_BLACK = "black"
COLOR_GREEN = "green"

PARITY_EVEN = "even"
PARITY_ODD = "odd"

RANGE_LOW = "low"   # 1-18
RANGE_HIGH = "high"  # 19-36


def is_valid_number(number: int) -> bool:
    return isinstance(number, int) and number in VALID_NUMBERS


def color_of(number: int) -> str:
    if number == 0:
        return COLOR_GREEN
    return COLOR_RED if number in RED_NUMBERS else COLOR_BLACK


def parity_of(number: int) -> str | None:
    """Zero has no parity for betting purposes."""
    if number == 0:
        return None
    return PARITY_EVEN if number % 2 == 0 else PARITY_ODD


def range_of(number: int) -> str | None:
    """Low (1-18) / High (19-36). Zero has none."""
    if number == 0:
        return None
    return RANGE_LOW if 1 <= number <= 18 else RANGE_HIGH


def dozen_of(number: int) -> int | None:
    """1st/2nd/3rd dozen. Zero has none."""
    if number == 0:
        return None
    return ((number - 1) // 12) + 1


def column_of(number: int) -> int | None:
    """1st/2nd/3rd table column. Zero has none."""
    if number == 0:
        return None
    return ((number - 1) % 3) + 1
