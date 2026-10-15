"""BTC daily trend -> which passed sides may open new trades.

BULLISH allows the LONG rows of PASSED_SIDES, BEARISH the SHORT rows, and
NEUTRAL (or anything unrecognised) allows none. It only filters the book.
It does not change entry, stop, target, size, leverage, or fee rules.
"""

from __future__ import annotations

from app.research.passed_cap_book import PASSED_SIDES

TRENDS: tuple[str, ...] = ("BULLISH", "BEARISH", "NEUTRAL")

# trend -> the direction that may open
_TREND_DIRECTION = {"BULLISH": "LONG", "BEARISH": "SHORT"}


def normalize_trend(trend: object) -> str:
    """Anything that is not BULLISH or BEARISH is NEUTRAL."""
    if not isinstance(trend, str):
        return "NEUTRAL"
    value = trend.strip().upper()
    return value if value in _TREND_DIRECTION else "NEUTRAL"


def allowed_passed_sides(trend: object) -> tuple[tuple[str, str, str], ...]:
    """Rows of PASSED_SIDES that may open under this trend, in book order."""
    direction = _TREND_DIRECTION.get(normalize_trend(trend))
    if direction is None:
        return ()
    return tuple(side for side in PASSED_SIDES if side[2] == direction)


def skipped_passed_sides(trend: object) -> tuple[tuple[str, str, str], ...]:
    """Rows of PASSED_SIDES that may not open under this trend, in book order."""
    allowed = set(allowed_passed_sides(trend))
    return tuple(side for side in PASSED_SIDES if side not in allowed)


def is_side_allowed(
    trend: object, combination_id: str, timeframe: str, direction: str
) -> bool:
    key = (
        str(combination_id or ""),
        str(timeframe or "").lower(),
        str(direction or "").upper(),
    )
    return key in allowed_passed_sides(trend)


def _rows(sides: tuple[tuple[str, str, str], ...]) -> list[dict[str, str]]:
    return [
        {"strategy_id": combination_id, "timeframe": timeframe, "direction": direction}
        for combination_id, timeframe, direction in sides
    ]


def route_passed_book(trend: object) -> dict:
    """JSON-ready view of the routed book for the admin status."""
    return {
        "trend": normalize_trend(trend),
        "allowed": _rows(allowed_passed_sides(trend)),
        "skipped": _rows(skipped_passed_sides(trend)),
    }
