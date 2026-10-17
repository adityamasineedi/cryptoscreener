"""Sort OHLC rows and tell a walk when two rows are not one bar apart.

A missing timestamp must not be treated as the next candle. Callers that
already have a sorted, duplicate-free, gap-free series get the same rows back.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe
from app.signals._candle_utils import candle_time


def step_seconds(timeframe: str | None) -> int | None:
    step_ms = TIMEFRAME_MS.get(normalize_timeframe(str(timeframe or "")))
    if not step_ms:
        return None
    return int(step_ms // 1000)


def bars_adjacent(
    prev: Mapping[str, Any] | None,
    nxt: Mapping[str, Any] | None,
    step: int | None,
) -> bool:
    """True when ``nxt`` opens one ``step`` after ``prev``.

    Unknown clocks are not treated as a gap. A known clock that jumps is.
    """
    if not step or step <= 0 or prev is None or nxt is None:
        return True
    a = candle_time(prev)
    b = candle_time(nxt)
    if a is None or b is None:
        return True
    return abs((b - a).total_seconds() - float(step)) <= 1.0


def prepare_series(candles: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Time-sort and keep the last row for each open time.

    Rows with no open time stay at the end, in their original order. A series
    that is already sorted and unique is returned unchanged in content.
    """
    stamped: list[tuple[float, int, Mapping[str, Any]]] = []
    unstamped: list[Mapping[str, Any]] = []
    for i, candle in enumerate(candles):
        ts = candle_time(candle)
        if ts is None:
            unstamped.append(candle)
        else:
            stamped.append((ts.timestamp(), i, candle))
    if not stamped:
        return list(candles)
    stamped.sort(key=lambda item: (item[0], item[1]))
    out: list[Mapping[str, Any]] = []
    seen: dict[float, int] = {}
    for key, _i, candle in stamped:
        prior = seen.get(key)
        if prior is None:
            seen[key] = len(out)
            out.append(candle)
        else:
            out[prior] = candle
    out.extend(unstamped)
    return out
