"""Full-length indicator series. Index i uses bars 0..i only; None during warmup."""

from __future__ import annotations

import bisect
import math
from collections import deque
from typing import Sequence

Series = list[float | None]


def sma(values: Sequence[float], n: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def rolling_std(values: Sequence[float], n: int) -> Series:
    """Population standard deviation over the last n values."""
    out: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        mean = sum(window) / n
        out[i] = math.sqrt(sum((x - mean) ** 2 for x in window) / n)
    return out


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(len(highs)):
        if i == 0:
            out.append(highs[0] - lows[0])
        else:
            pc = closes[i - 1]
            out.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return out


def wilder(values: Sequence[float], n: int, start: int = 0) -> Series:
    """Wilder smoothing seeded with the mean of values[start:start+n]."""
    out: Series = [None] * len(values)
    seed_end = start + n
    if len(values) < seed_end:
        return out
    avg = sum(values[start:seed_end]) / n
    out[seed_end - 1] = avg
    for i in range(seed_end, len(values)):
        avg = (avg * (n - 1) + values[i]) / n
        out[i] = avg
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> Series:
    return wilder(true_range(highs, lows, closes), n)


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int = 14) -> Series:
    size = len(highs)
    out: Series = [None] * size
    if size < 2 * n + 1:
        return out
    tr = true_range(highs, lows, closes)
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for i in range(1, size):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    # Smoothed sums start from bar 1, the first bar with a directional move.
    s_tr = wilder(tr, n, start=1)
    s_p = wilder(plus_dm, n, start=1)
    s_m = wilder(minus_dm, n, start=1)
    dx = [0.0] * size
    first_dx = n
    for i in range(first_dx, size):
        t, p, m = s_tr[i], s_p[i], s_m[i]
        if not t:
            continue
        pdi = 100.0 * p / t
        mdi = 100.0 * m / t
        denom = pdi + mdi
        dx[i] = 100.0 * abs(pdi - mdi) / denom if denom else 0.0
    smoothed = wilder(dx, n, start=first_dx)
    for i in range(size):
        out[i] = smoothed[i]
    return out


def rsi(closes: Sequence[float], n: int) -> Series:
    size = len(closes)
    out: Series = [None] * size
    if size <= n:
        return out
    gains = [0.0] * size
    losses = [0.0] * size
    for i in range(1, size):
        d = closes[i] - closes[i - 1]
        gains[i] = d if d > 0 else 0.0
        losses[i] = -d if d < 0 else 0.0
    ag = wilder(gains, n, start=1)
    al = wilder(losses, n, start=1)
    for i in range(n, size):
        g, l = ag[i], al[i]
        if g is None or l is None:
            continue
        if l == 0:
            out[i] = 100.0 if g > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + g / l)
    return out


def prior_high(highs: Sequence[float], n: int) -> Series:
    """Highest high of the n bars before i (bar i excluded)."""
    out: Series = [None] * len(highs)
    dq: deque[int] = deque()
    for i in range(len(highs)):
        if i >= n:
            out[i] = highs[dq[0]]
        # Window for i+1 is [i-n+1, i].
        while dq and highs[dq[-1]] <= highs[i]:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
    return out


def prior_low(lows: Sequence[float], n: int) -> Series:
    """Lowest low of the n bars before i (bar i excluded)."""
    out: Series = [None] * len(lows)
    dq: deque[int] = deque()
    for i in range(len(lows)):
        if i >= n:
            out[i] = lows[dq[0]]
        while dq and lows[dq[-1]] >= lows[i]:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
    return out


def percentile_rank(values: Series, window: int) -> Series:
    """Share of the trailing `window` values (current included) that are <= the current one."""
    out: Series = [None] * len(values)
    ordered: list[float] = []
    fifo: deque[float] = deque()
    for i, v in enumerate(values):
        if v is None:
            continue
        fifo.append(v)
        bisect.insort(ordered, v)
        if len(fifo) > window:
            old = fifo.popleft()
            del ordered[bisect.bisect_left(ordered, old)]
        if len(fifo) == window:
            out[i] = bisect.bisect_right(ordered, v) / window
    return out


def ema(values: Sequence[float], n: int) -> Series:
    """EMA seeded with the SMA of the first n values."""
    out: Series = [None] * len(values)
    if len(values) < n:
        return out
    k = 2.0 / (n + 1)
    avg = sum(values[:n]) / n
    out[n - 1] = avg
    for i in range(n, len(values)):
        avg = values[i] * k + avg * (1 - k)
        out[i] = avg
    return out


def efficiency_ratio(closes: Sequence[float], n: int) -> Series:
    """Kaufman ER: net move over the sum of bar-to-bar moves, 0 (noise) to 1 (straight line)."""
    out: Series = [None] * len(closes)
    path = 0.0
    steps = [0.0] + [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    for i in range(len(closes)):
        path += steps[i]
        if i > n:
            path -= steps[i - n]
        if i >= n:
            out[i] = abs(closes[i] - closes[i - n]) / path if path > 0 else 0.0
    return out


def rolling_corr(xs: Sequence[float | None], ys: Sequence[float | None], n: int) -> Series:
    """Pearson correlation over the last n index positions where both values exist."""
    out: Series = [None] * len(xs)
    window: deque[tuple[float, float]] = deque()
    sx = sy = sxx = syy = sxy = 0.0
    for i, (x, y) in enumerate(zip(xs, ys)):
        if x is not None and y is not None:
            window.append((x, y))
            sx += x
            sy += y
            sxx += x * x
            syy += y * y
            sxy += x * y
            if len(window) > n:
                ox, oy = window.popleft()
                sx -= ox
                sy -= oy
                sxx -= ox * ox
                syy -= oy * oy
                sxy -= ox * oy
        if len(window) == n:
            vx = sxx - sx * sx / n
            vy = syy - sy * sy / n
            if vx > 1e-18 and vy > 1e-18:
                out[i] = max(-1.0, min(1.0, (sxy - sx * sy / n) / math.sqrt(vx * vy)))
    return out
