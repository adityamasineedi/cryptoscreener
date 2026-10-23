"""E34 liquidation-flush signal.

Bar t is the liquidation bar. A long needs L(t) above the prior-30-day p99,
a lower wick of at least half the range, and bar t+1 closing above the
midpoint of bar t. The short is the mirror. No signal until 30 days after
the first complete liquidation hour, and not unless at least 648 of those
720 hours are present. Entry is the open of bar t+2; this module only
decides the side.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from app.research.liq_flush.costs import P99_HOURS, P99_MIN_PRESENT, WICK_MIN


def percentile_linear(values: Sequence[float], q: float) -> float:
    """Linear percentile, Hyndman-Fan type 7 (NumPy's default method='linear')."""
    if not values:
        raise ValueError("empty")
    if not 0 <= q <= 100:
        raise ValueError("q")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (q / 100.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def p99_threshold(
    present: Sequence[float],
    *,
    first_complete: datetime | None,
    bar_open: datetime,
) -> float | None:
    """p99 of the values that are present in the prior 720 clock hours.

    ``present`` must already be only those hours. The caller counts them.
    Returns None when the 30-day wait or the 648-hour rule fails.
    """
    if first_complete is None:
        return None
    if bar_open < first_complete + timedelta(hours=P99_HOURS):
        return None
    if len(present) < P99_MIN_PRESENT:
        return None
    return percentile_linear(present, 99)


def window_values(by_hour: Mapping[datetime, float], bar_open: datetime) -> list[float]:
    """Values for the 720 hours [bar_open - 720h, bar_open). Missing hours are omitted."""
    out: list[float] = []
    for k in range(P99_HOURS, 0, -1):
        hour = bar_open - timedelta(hours=k)
        if hour in by_hour:
            out.append(float(by_hour[hour]))
    return out


def first_complete_hour(by_hour: Mapping[datetime, float]) -> datetime | None:
    if not by_hour:
        return None
    return min(by_hour)


def lower_wick_ok(open_: float, high: float, low: float, close: float) -> bool:
    span = high - low
    if span <= 0:
        return False
    return (min(open_, close) - low) >= WICK_MIN * span


def upper_wick_ok(open_: float, high: float, low: float, close: float) -> bool:
    span = high - low
    if span <= 0:
        return False
    return (high - max(open_, close)) >= WICK_MIN * span


def midpoint(high: float, low: float) -> float:
    return (high + low) / 2.0


def side_at(
    *,
    long_now: float | None,
    short_now: float | None,
    long_prior: Sequence[float],
    short_prior: Sequence[float],
    first_long: datetime | None,
    first_short: datetime | None,
    bar_open: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    next_close: float,
) -> str | None:
    """Return LONG, SHORT, or None.

    Both sides on the same bar is ambiguous (a doji can print a 50/50 wick)
    and is not a trade. The caller records ``both_sides``.
    """
    long_p = p99_threshold(long_prior, first_complete=first_long, bar_open=bar_open)
    short_p = p99_threshold(short_prior, first_complete=first_short, bar_open=bar_open)
    mid = midpoint(high, low)
    long_ok = (
        long_now is not None
        and long_p is not None
        and long_now > long_p
        and lower_wick_ok(open_, high, low, close)
        and next_close > mid
    )
    short_ok = (
        short_now is not None
        and short_p is not None
        and short_now > short_p
        and upper_wick_ok(open_, high, low, close)
        and next_close < mid
    )
    if long_ok and short_ok:
        return "BOTH"
    if long_ok:
        return "LONG"
    if short_ok:
        return "SHORT"
    return None
