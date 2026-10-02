"""Pure multi-timeframe indicator calculations (no I/O, deterministic)."""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Sequence


def _as_floats(values: Sequence[float]) -> list[float]:
    return [float(v) for v in values]


def sma(values: Sequence[float], period: int) -> float | None:
    if period < 1:
        raise ValueError("period must be >= 1")
    series = _as_floats(values)
    if len(series) < period:
        return None
    return mean(series[-period:])


def ema(values: Sequence[float], period: int) -> float | None:
    if period < 1:
        raise ValueError("period must be >= 1")
    series = _as_floats(values)
    if len(series) < period:
        return None
    k = 2.0 / (period + 1)
    seed = mean(series[:period])
    ema_val = seed
    for price in series[period:]:
        ema_val = price * k + ema_val * (1.0 - k)
    return ema_val


def _true_ranges(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    h = _as_floats(highs)
    l = _as_floats(lows)
    c = _as_floats(closes)
    n = len(c)
    if not (len(h) == len(l) == n) or n == 0:
        return []
    trs: list[float] = [h[0] - l[0]]
    for i in range(1, n):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
    return trs


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> float | None:
    if period < 1:
        raise ValueError("period must be >= 1")
    trs = _true_ranges(highs, lows, closes)
    if len(trs) < period:
        return None
    if len(trs) == period:
        return mean(trs)
    atr_val = mean(trs[:period])
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val


def rsi(closes: Sequence[float], period: int) -> float | None:
    if period < 1:
        raise ValueError("period must be >= 1")
    series = _as_floats(closes)
    if len(series) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(series)):
        delta = series[i] - series[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def vwap(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
) -> float | None:
    h = _as_floats(highs)
    l = _as_floats(lows)
    c = _as_floats(closes)
    v = _as_floats(volumes)
    n = len(c)
    if not (len(h) == len(l) == len(v) == n) or n == 0:
        return None
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(n):
        if v[i] <= 0:
            continue
        tp = (h[i] + l[i] + c[i]) / 3.0
        cum_pv += tp * v[i]
        cum_v += v[i]
    if cum_v == 0:
        return None
    return cum_pv / cum_v


def volume_sma(volumes: Sequence[float], period: int) -> float | None:
    return sma(volumes, period)


def relative_volume(current_volume: float, baseline_volume: float | None) -> float | None:
    if baseline_volume is None or baseline_volume <= 0:
        return None
    return float(current_volume) / float(baseline_volume)


def volatility(closes: Sequence[float], period: int) -> float | None:
    """Sample stdev of simple returns over the last `period` intervals."""
    if period < 2:
        raise ValueError("period must be >= 2")
    series = _as_floats(closes)
    if len(series) < period + 1:
        return None
    window = series[-(period + 1) :]
    returns: list[float] = []
    for i in range(1, len(window)):
        prev = window[i - 1]
        if prev == 0:
            continue
        returns.append((window[i] - prev) / prev)
    if len(returns) < 2:
        return None
    return pstdev(returns)


def candle_body_ratio(open_: float, high: float, low: float, close: float) -> float:
    rng = high - low
    if rng <= 0:
        return 0.0
    return abs(close - open_) / rng


def williams_r(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int,
) -> float | None:
    """Williams %R = (HH_n - C) / (HH_n - LL_n) * -100."""
    if period < 1:
        raise ValueError("period must be >= 1")
    h = _as_floats(highs)
    l = _as_floats(lows)
    c = _as_floats(closes)
    n = len(c)
    if not (len(h) == len(l) == n) or n < period:
        return None
    hh = max(h[-period:])
    ll = min(l[-period:])
    denom = hh - ll
    if denom == 0:
        return 0.0
    return ((hh - c[-1]) / denom) * -100.0
