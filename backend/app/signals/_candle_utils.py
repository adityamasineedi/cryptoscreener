"""Shared candle helpers for the setup signal engine (no I/O)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def candle_time(candle: Mapping[str, Any]) -> datetime | None:
    raw = candle.get("time") or candle.get("timestamp") or candle.get("open_time")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def ohlc(candles: Sequence[Mapping[str, Any]], i: int) -> tuple[float, float, float, float]:
    c = candles[i]
    o = float(c.get("open") or c.get("o") or 0)
    h = float(c.get("high") or c.get("h") or 0)
    l = float(c.get("low") or c.get("l") or 0)
    cl = float(c.get("close") or c.get("c") or 0)
    return o, h, l, cl


def volume_at(candles: Sequence[Mapping[str, Any]], i: int) -> float:
    c = candles[i]
    return float(c.get("volume") or c.get("v") or 0)


def series_ohlcv(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    for i in range(len(candles)):
        o, h, l, c = ohlc(candles, i)
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)
        vols.append(volume_at(candles, i))
    return opens, highs, lows, closes, vols
