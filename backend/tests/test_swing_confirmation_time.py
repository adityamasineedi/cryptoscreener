"""Swing confirmation timing: pivot timestamp vs confirmed_at after right bars close.

Uses the existing detector as-is. Does not change swing or BOS rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.signals.swing_detector import detect_swings

LEFT = 2
RIGHT = 3
PIVOT = 4


def _ts(i: int) -> datetime:
    return datetime(2024, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i)


def _candles() -> list[dict]:
    """One swing high at PIVOT. Neighbor highs stay below it; lows do not pivot."""
    out: list[dict] = []
    for i in range(PIVOT + RIGHT + 2):
        high = 20.0 if i == PIVOT else 10.0 + (i % 3)
        low = 8.0 + i * 0.05
        out.append(
            {
                "time": _ts(i),
                "open": 9.0,
                "high": high,
                "low": low,
                "close": 9.5,
                "volume": 1.0,
            }
        )
    return out


def _at_pivot(swings: list) -> list:
    return [s for s in swings if s.bar_index == PIVOT and s.swing_type == "HIGH"]


def test_swing_absent_until_right_bars_close() -> None:
    candles = _candles()
    before_close = PIVOT + RIGHT - 1
    early = detect_swings(candles, left=LEFT, right=RIGHT, as_of_index=before_close)
    assert _at_pivot(early) == []

    after_close = PIVOT + RIGHT
    later = detect_swings(candles, left=LEFT, right=RIGHT, as_of_index=after_close)
    found = _at_pivot(later)
    assert len(found) == 1
    swing = found[0]
    assert swing.timestamp == _ts(PIVOT)
    assert swing.confirmed_at == _ts(PIVOT + RIGHT)
    assert swing.confirmed_at > swing.timestamp
