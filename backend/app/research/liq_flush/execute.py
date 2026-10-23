"""E34 execution, the scalar form of the E31 / E31c walk.

Entry is the open of bar t+2, slipped by D144. The stop is the liquidation
bar's wick ± 0.2 × ATR(14) as of that bar. The target is 2R from the fill.
The entry bar is bar 1; the time stop is the close of bar e+47. A bar that
touches both the stop and the target is the stop. A gap through the stop
fills at the open. One position per coin: the liquidation bar must be
strictly after the previous exit bar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from app.research.liq_flush.atr import atr_at
from app.research.liq_flush.costs import (
    ATR_BUFFER,
    HOUR_MS,
    RISK_USD,
    TAKER,
    TARGET_R,
    TIME_BARS,
    size_qty,
    slippage,
)

UTC = timezone.utc


@dataclass(frozen=True)
class Bar:
    open_time: datetime
    o: float
    h: float
    l: float
    c: float

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("bar open_time must be timezone-aware")


@dataclass(frozen=True)
class Position:
    symbol: str
    side: str
    signal_index: int
    entry_index: int
    signal_hour: datetime
    entry_hour: datetime
    fill: float
    stop: float
    target: float
    qty: float
    capped: bool
    slip: float
    atr: float


@dataclass(frozen=True)
class FillResult:
    exit_index: int
    exit_hour: datetime
    raw: float
    exit_px: float
    reason: str
    fees: float
    funding: float
    gross: float
    net: float
    r: float


def _dd(side: str) -> float:
    if side == "LONG":
        return 1.0
    if side == "SHORT":
        return -1.0
    raise ValueError(side)


def plan_entry(
    bars: Sequence[Bar],
    signal_index: int,
    side: str,
    symbol: str,
) -> Position | None:
    """Build the position filled at the open of t+2, or None if the stop is invalid."""
    entry_index = signal_index + 2
    if signal_index < 0 or entry_index >= len(bars):
        return None
    atr = atr_at([b.h for b in bars], [b.l for b in bars], [b.c for b in bars], signal_index)
    if atr is None:
        return None
    bar = bars[signal_index]
    if side == "LONG":
        stop = bar.l - ATR_BUFFER * atr
    elif side == "SHORT":
        stop = bar.h + ATR_BUFFER * atr
    else:
        raise ValueError(side)
    slip = slippage(symbol)
    dd = _dd(side)
    fill = bars[entry_index].o * (1.0 + dd * slip)
    if not (dd * (fill - stop) > 0) or fill <= 0:
        return None
    dist = abs(fill - stop)
    target = fill + dd * TARGET_R * dist
    qty, capped = size_qty(fill, stop)
    if qty <= 0 or not math.isfinite(qty):
        return None
    return Position(
        symbol=symbol,
        side=side,
        signal_index=signal_index,
        entry_index=entry_index,
        signal_hour=bars[signal_index].open_time,
        entry_hour=bars[entry_index].open_time,
        fill=fill,
        stop=stop,
        target=target,
        qty=qty,
        capped=capped,
        slip=slip,
        atr=atr,
    )


def bar_hit(pos: Position, bar: Bar) -> tuple[str, float] | None:
    """First exit on this bar, or None if the bar does not reach a stop or target.

    Order: gap through the stop, gap through the target, wick stop, wick target.
    """
    dd = _dd(pos.side)
    opened = dd * bar.o
    extreme_far = bar.h if dd > 0 else -bar.l
    extreme_near = bar.l if dd > 0 else -bar.h
    stop_px = dd * pos.stop
    target_px = dd * pos.target
    if opened <= stop_px:
        return "stop_gap", bar.o
    if opened >= target_px:
        return "target_gap", bar.o
    if extreme_near <= stop_px:
        return "stop", pos.stop
    if extreme_far >= target_px:
        return "target", pos.target
    return None


def funding_pnl(
    pos: Position,
    bars: Sequence[Bar],
    exit_index: int,
    reason: str,
    funding: Sequence[tuple[int, float]],
) -> float:
    """Funding settlements in (entry open, exit time], mark = the 1h close at or before the settlement.

    A stop or target exits at the bar's open, so its exit time is that open.
    A time stop exits at the bar's close, so its exit time is one hour later.
    Longs pay positive funding.
    """
    dd = _dd(pos.side)
    t_in = int(bars[pos.entry_index].open_time.timestamp() * 1000)
    t_out = int(bars[exit_index].open_time.timestamp() * 1000)
    if reason == "time":
        t_out += HOUR_MS
    total = 0.0
    for ts, rate in funding:
        if ts <= t_in or ts > t_out:
            continue
        mark = 0.0
        for bar in bars:
            close_ms = int(bar.open_time.timestamp() * 1000) + HOUR_MS
            if close_ms <= ts:
                mark = bar.c
            else:
                break
        total += float(rate) * mark
    return -dd * pos.qty * total


def settle(
    pos: Position,
    bars: Sequence[Bar],
    exit_index: int,
    raw: float,
    reason: str,
    funding: Sequence[tuple[int, float]],
) -> FillResult:
    dd = _dd(pos.side)
    exit_px = raw * (1.0 - dd * pos.slip)
    fees = TAKER * pos.qty * (pos.fill + exit_px)
    fund = funding_pnl(pos, bars, exit_index, reason, funding)
    gross = dd * (exit_px - pos.fill) * pos.qty
    net = gross - fees + fund
    return FillResult(
        exit_index=exit_index,
        exit_hour=bars[exit_index].open_time,
        raw=raw,
        exit_px=exit_px,
        reason=reason,
        fees=fees,
        funding=fund,
        gross=gross,
        net=net,
        r=net / RISK_USD,
    )


def try_exit(
    pos: Position,
    bars: Sequence[Bar],
    index: int,
    funding: Sequence[tuple[int, float]],
) -> FillResult | None:
    """Walk one bar of an open position. The time stop fires on bar e+47."""
    if index < pos.entry_index or index >= len(bars):
        return None
    last_hold = pos.entry_index + TIME_BARS - 1
    if index > last_hold:
        return None
    hit = bar_hit(pos, bars[index])
    if hit is None:
        if index < last_hold:
            return None
        return settle(pos, bars, index, bars[index].c, "time", funding)
    reason, raw = hit
    return settle(pos, bars, index, raw, reason, funding)


def floor_hour(when: datetime) -> datetime:
    when = when.astimezone(UTC)
    return when.replace(minute=0, second=0, microsecond=0)


def hour_after(when: datetime, hours: int) -> datetime:
    return floor_hour(when) + timedelta(hours=hours)
