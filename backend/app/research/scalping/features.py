from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr_series
from app.research.intraday_bos_sd.candles import parse_time, tf_delta
from app.signals.schemas import SwingRecord
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import infer_trend


TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15}


def utc_time(candle: Mapping[str, Any]) -> datetime:
    value = candle.get("time") or candle.get("open_time") or candle.get("timestamp")
    if value is None:
        raise ValueError("candle is missing a datetime open timestamp")
    return parse_time(value)


def close_time(candle: Mapping[str, Any], timeframe: str) -> datetime:
    return utc_time(candle) + tf_delta(timeframe)


@dataclass(frozen=True)
class FeatureSeries:
    timeframe: str
    candles: tuple[Mapping[str, Any], ...]
    times: tuple[datetime, ...]
    closes_at: tuple[datetime, ...]
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...]
    atr: tuple[float | None, ...]
    rvol: tuple[float | None, ...]
    vwap: tuple[float | None, ...]
    segment: tuple[int, ...]
    swings: tuple[SwingRecord, ...]

    def closed_index_at(self, when: datetime) -> int | None:
        idx = bisect_right(self.closes_at, when) - 1
        return idx if idx >= 0 else None

    def confirmed_swings(self, index: int, side: str | None = None) -> list[SwingRecord]:
        if index < 0:
            return []
        seg = self.segment[index]
        return [
            s
            for s in self.swings
            if s.bar_index + 2 <= index
            and self.segment[s.bar_index] == seg
            and (side is None or s.swing_type == side)
        ]

    def trend(self, index: int) -> str:
        swings = self.confirmed_swings(index)
        return str(infer_trend(swings).get("trend") or "NEUTRAL")


def build_features(
    candles: Sequence[Mapping[str, Any]], timeframe: str
) -> FeatureSeries:
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    rows = tuple(candles)
    for candle in rows:
        if "is_closed" in candle and not bool(candle.get("is_closed")):
            raise ValueError("open candles are not eligible for strategy evaluation")
        if "closed" in candle and not bool(candle.get("closed")):
            raise ValueError("open candles are not eligible for strategy evaluation")
    times = tuple(utc_time(c) for c in rows)
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("candles must have unique, strictly increasing timestamps")
    opens = tuple(float(c["open"]) for c in rows)
    highs = tuple(float(c["high"]) for c in rows)
    lows = tuple(float(c["low"]) for c in rows)
    closes = tuple(float(c["close"]) for c in rows)
    volumes = tuple(float(c.get("volume") or 0.0) for c in rows)
    if any(h < max(o, c) or l > min(o, c) or h < l for o, h, l, c in zip(opens, highs, lows, closes)):
        raise ValueError("invalid OHLC candle")

    step = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    segment: list[int] = []
    seg = 0
    for i, ts in enumerate(times):
        if i and ts - times[i - 1] != step:
            seg += 1
        segment.append(seg)

    # ATR/RVOL initialize independently after every gap.  A missing interval is
    # never treated as if adjacent observations were consecutive candles.
    atr_values_list: list[float | None] = [None] * len(rows)
    rvol: list[float | None] = [None] * len(rows)
    starts = [0]
    for i in range(1, len(rows)):
        if segment[i] != segment[i - 1]:
            starts.append(i)
    starts.append(len(rows))
    for start, end in zip(starts, starts[1:]):
        local_atr = atr_series(highs[start:end], lows[start:end], closes[start:end], 14)
        atr_values_list[start:end] = local_atr
        for i in range(start + 20, end):
            reference = volumes[i - 20 : i]
            avg = sum(reference) / 20.0
            rvol[i] = volumes[i] / avg if avg > 0 else None
    atr_values = tuple(atr_values_list)

    # UTC session VWAP.  Missing/zero volume leaves VWAP unavailable rather than
    # substituting price or carrying a prior day's value.
    vwaps: list[float | None] = []
    current_day = None
    cum_pv = 0.0
    cum_v = 0.0
    for i, ts in enumerate(times):
        if ts.date() != current_day:
            current_day = ts.date()
            cum_pv = 0.0
            cum_v = 0.0
        vol = volumes[i]
        if vol > 0:
            typical = (highs[i] + lows[i] + closes[i]) / 3.0
            cum_pv += typical * vol
            cum_v += vol
        vwaps.append(cum_pv / cum_v if cum_v > 0 else None)

    swings: list[SwingRecord] = []
    # Never allow a swing window to bridge a missing-bar boundary.
    for start, end in zip(starts, starts[1:]):
        local = detect_swings(rows[start:end], left=2, right=2, timeframe=timeframe)
        for swing in local:
            swing.bar_index += start
            swings.append(swing)

    return FeatureSeries(
        timeframe=timeframe,
        candles=rows,
        times=times,
        closes_at=tuple(t + step for t in times),
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
        atr=atr_values,
        rvol=tuple(rvol),
        vwap=tuple(vwaps),
        segment=tuple(segment),
        swings=tuple(swings),
    )
