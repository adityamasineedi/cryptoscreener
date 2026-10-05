"""Point-in-time safe indicator series for market-structure analytics.

All series are causal (no centered windows). Index i uses only bars <= i.
"""

from __future__ import annotations

import math
from typing import Sequence


def _f(values: Sequence[float]) -> list[float]:
    return [float(v) for v in values]


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be >= 1")
    series = _f(values)
    n = len(series)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(series[:period]) / period
    ema_val = seed
    out[period - 1] = ema_val
    for i in range(period, n):
        ema_val = series[i] * k + ema_val * (1.0 - k)
        out[i] = ema_val
    return out


def rolling_slope(values: Sequence[float], lookback: int) -> list[float | None]:
    """OLS slope of close over trailing lookback (inclusive of current)."""
    series = _f(values)
    n = len(series)
    out: list[float | None] = [None] * n
    if lookback < 2:
        return out
    # Precompute x stats for fixed window length.
    x_mean = (lookback - 1) / 2.0
    x_var = sum((i - x_mean) ** 2 for i in range(lookback))
    if x_var <= 0:
        return out
    for i in range(lookback - 1, n):
        window = series[i - lookback + 1 : i + 1]
        y_mean = sum(window) / lookback
        cov = sum((j - x_mean) * (window[j] - y_mean) for j in range(lookback))
        out[i] = cov / x_var
    return out


def true_range_series(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float]:
    h = _f(highs)
    l = _f(lows)
    c = _f(closes)
    n = len(c)
    if not (len(h) == len(l) == n) or n == 0:
        return []
    trs = [h[0] - l[0]]
    for i in range(1, n):
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return trs


def wilder_smooth(values: Sequence[float], period: int) -> list[float | None]:
    series = _f(values)
    n = len(series)
    out: list[float | None] = [None] * n
    if n < period or period < 1:
        return out
    avg = sum(series[:period]) / period
    out[period - 1] = avg
    for i in range(period, n):
        avg = (avg * (period - 1) + series[i]) / period
        out[i] = avg
    return out


def adx_di_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (adx, di_plus, di_minus) Wilder ADX — causal."""
    h = _f(highs)
    l = _f(lows)
    c = _f(closes)
    n = len(c)
    adx: list[float | None] = [None] * n
    di_p: list[float | None] = [None] * n
    di_m: list[float | None] = [None] * n
    if n < period + 1 or not (len(h) == len(l) == n):
        return adx, di_p, di_m

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = true_range_series(h, l, c)
    for i in range(1, n):
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0

    atr = wilder_smooth(tr, period)
    sm_plus = wilder_smooth(plus_dm, period)
    sm_minus = wilder_smooth(minus_dm, period)
    dx: list[float | None] = [None] * n
    for i in range(n):
        a = atr[i]
        p = sm_plus[i]
        m = sm_minus[i]
        if a is None or a <= 0 or p is None or m is None:
            continue
        di_plus = 100.0 * p / a
        di_minus = 100.0 * m / a
        di_p[i] = di_plus
        di_m[i] = di_minus
        denom = di_plus + di_minus
        dx[i] = 0.0 if denom <= 0 else 100.0 * abs(di_plus - di_minus) / denom

    # ADX = Wilder smooth of DX; first ADX at index 2*period-1 roughly.
    dx_vals = [0.0 if v is None else v for v in dx]
    # Seed once we have `period` non-None DX values after ATR seed.
    start = period * 2 - 1
    if start >= n:
        return adx, di_p, di_m
    seed_slice = [dx_vals[i] for i in range(period, start + 1)]
    if len(seed_slice) < period:
        return adx, di_p, di_m
    adx_val = sum(seed_slice[-period:]) / period
    adx[start] = adx_val
    for i in range(start + 1, n):
        if dx[i] is None:
            continue
        adx_val = (adx_val * (period - 1) + dx_vals[i]) / period
        adx[i] = adx_val
    return adx, di_p, di_m


def efficiency_ratio_series(
    closes: Sequence[float], lookback: int
) -> list[float | None]:
    """Kaufman Efficiency Ratio: |net change| / sum(|bar changes|)."""
    series = _f(closes)
    n = len(series)
    out: list[float | None] = [None] * n
    if lookback < 1:
        return out
    for i in range(lookback, n):
        net = abs(series[i] - series[i - lookback])
        path = 0.0
        for j in range(i - lookback + 1, i + 1):
            path += abs(series[j] - series[j - 1])
        out[i] = 0.0 if path <= 0 else net / path
    return out


def choppiness_index_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    lookback: int = 14,
) -> list[float | None]:
    """Choppiness Index = 100 * log10(sum(TR)/range) / log10(n)."""
    h = _f(highs)
    l = _f(lows)
    c = _f(closes)
    n = len(c)
    out: list[float | None] = [None] * n
    if lookback < 2 or not (len(h) == len(l) == n):
        return out
    tr = true_range_series(h, l, c)
    log_n = math.log10(lookback)
    if log_n <= 0:
        return out
    for i in range(lookback - 1, n):
        tr_sum = sum(tr[i - lookback + 1 : i + 1])
        hh = max(h[i - lookback + 1 : i + 1])
        ll = min(l[i - lookback + 1 : i + 1])
        rng = hh - ll
        if rng <= 0 or tr_sum <= 0:
            out[i] = 100.0
            continue
        out[i] = 100.0 * math.log10(tr_sum / rng) / log_n
    return out


def atr_percentile_series(
    atr_values: Sequence[float | None], lookback: int
) -> list[float | None]:
    """Trailing percentile rank of current ATR in prior lookback window (excl. future)."""
    n = len(atr_values)
    out: list[float | None] = [None] * n
    if lookback < 2:
        return out
    for i in range(n):
        cur = atr_values[i]
        if cur is None:
            continue
        start = max(0, i - lookback + 1)
        window = [float(v) for v in atr_values[start : i + 1] if v is not None]
        if len(window) < max(5, lookback // 5):
            continue
        below = sum(1 for v in window if v <= float(cur))
        out[i] = 100.0 * below / len(window)
    return out


def atr_relative_series(
    atr_values: Sequence[float | None], lookback: int
) -> list[float | None]:
    n = len(atr_values)
    out: list[float | None] = [None] * n
    for i in range(n):
        cur = atr_values[i]
        if cur is None:
            continue
        start = max(0, i - lookback + 1)
        window = [float(v) for v in atr_values[start : i + 1] if v is not None]
        if len(window) < 5:
            continue
        avg = sum(window) / len(window)
        out[i] = None if avg <= 0 else float(cur) / avg
    return out


def realized_vol_series(closes: Sequence[float], lookback: int) -> list[float | None]:
    series = _f(closes)
    n = len(series)
    out: list[float | None] = [None] * n
    if lookback < 2:
        return out
    for i in range(lookback, n):
        rets: list[float] = []
        for j in range(i - lookback + 1, i + 1):
            prev = series[j - 1]
            if prev == 0:
                continue
            rets.append((series[j] - prev) / prev)
        if len(rets) < 2:
            continue
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        out[i] = math.sqrt(max(var, 0.0))
    return out


def roc_series(closes: Sequence[float], lookback: int) -> list[float | None]:
    series = _f(closes)
    n = len(series)
    out: list[float | None] = [None] * n
    for i in range(lookback, n):
        prev = series[i - lookback]
        if prev == 0:
            continue
        out[i] = (series[i] - prev) / prev
    return out


def rsi_series(closes: Sequence[float], period: int) -> list[float | None]:
    series = _f(closes)
    n = len(series)
    out: list[float | None] = [None] * n
    if n < period + 1 or period < 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = series[i] - series[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_g = sum(gains[1 : period + 1]) / period
    avg_l = sum(losses[1 : period + 1]) / period
    if avg_l == 0:
        out[period] = 100.0 if avg_g > 0 else 0.0
    else:
        rs = avg_g / avg_l
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            out[i] = 100.0 if avg_g > 0 else 0.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def directional_alternation_count(
    closes: Sequence[float], lookback: int
) -> list[int | None]:
    series = _f(closes)
    n = len(series)
    out: list[int | None] = [None] * n
    for i in range(lookback, n):
        changes = 0
        prev_sign = 0
        for j in range(i - lookback + 1, i + 1):
            d = series[j] - series[j - 1]
            sign = 1 if d > 0 else (-1 if d < 0 else 0)
            if sign != 0 and prev_sign != 0 and sign != prev_sign:
                changes += 1
            if sign != 0:
                prev_sign = sign
        out[i] = changes
    return out


def rolling_high_low(
    highs: Sequence[float], lows: Sequence[float], lookback: int
) -> tuple[list[float | None], list[float | None]]:
    h = _f(highs)
    l = _f(lows)
    n = len(h)
    hh: list[float | None] = [None] * n
    ll: list[float | None] = [None] * n
    if lookback < 1 or len(l) != n:
        return hh, ll
    for i in range(lookback - 1, n):
        hh[i] = max(h[i - lookback + 1 : i + 1])
        ll[i] = min(l[i - lookback + 1 : i + 1])
    return hh, ll
