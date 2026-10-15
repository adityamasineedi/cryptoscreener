"""Next-bar execution. OHLCV cannot describe queue position or market impact."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.research.intraday_bos_sd.candles import Series
from app.research.intraday_bos_sd.setups import SetupSignal
from app.research.intraday_bos_sd.spec import MAX_HOLD_HOURS, TAKER_FEE_RATE, TARGET_R
from app.research.intraday_bos_sd.zones import Zone, obstructing_zones


@dataclass
class Fill:
    signal: SetupSignal
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    stop: float
    target: float
    quantity: float
    gross_pnl: float
    fees: float
    funding_pnl: float
    slippage_cost: float
    net_pnl: float
    r_multiple: float
    notional: float


def _slip(price: float, rate: float, adverse_up: bool) -> float:
    return price * (1.0 + rate) if adverse_up else price * (1.0 - rate)


def plan_entry(
    signal: SetupSignal,
    setup: Series,
    zones: list[Zone],
    *,
    slippage: float,
) -> tuple[float, float, float] | str:
    """Return (entry, stop, target) or a rejection reason. Uses the next open only."""
    if signal.entry_index >= len(setup):
        return "no_next_bar"
    raw_open = setup.opens[signal.entry_index]
    entry = _slip(raw_open, slippage, adverse_up=signal.side == "LONG")
    stop = signal.stop
    if signal.side == "LONG" and entry <= stop:
        return "entry_beyond_stop"
    if signal.side == "SHORT" and entry >= stop:
        return "entry_beyond_stop"
    risk = (entry - stop) if signal.side == "LONG" else (stop - entry)
    if risk <= 0:
        return "non_positive_risk"
    target = entry + TARGET_R * risk if signal.side == "LONG" else entry - TARGET_R * risk
    when = setup.bars[signal.entry_index].open_time
    opposing = "SUPPLY" if signal.side == "LONG" else "DEMAND"
    if obstructing_zones(zones, side=opposing, when=when, entry=entry, target=target):
        return "opposing_zone_obstructs_target"
    return entry, stop, target


def _walk_bars(execution: Series | None, setup: Series, entry_index: int):
    if execution is not None and execution.bars:
        start = setup.bars[entry_index].open_time
        end = setup.close_time(min(entry_index, len(setup) - 1))
        # Include execution bars from the entry open through the hold window.
        return execution, start
    return setup, setup.bars[entry_index].open_time


def simulate_trade(
    signal: SetupSignal,
    setup: Series,
    zones: list[Zone],
    *,
    quantity: float,
    slippage: float,
    execution: Series | None = None,
    funding: list[tuple[datetime, float]] | None = None,
) -> Fill | str:
    planned = plan_entry(signal, setup, zones, slippage=slippage)
    if isinstance(planned, str):
        return planned
    entry, stop, target = planned
    entry_time = setup.bars[signal.entry_index].open_time
    deadline = entry_time + timedelta(hours=MAX_HOLD_HOURS)
    path, _ = _walk_bars(execution, setup, signal.entry_index)
    bars = [
        b
        for b in path.bars
        if b.open_time >= entry_time and b.open_time < deadline + path.delta
    ]
    if path is setup:
        bars = setup.bars[signal.entry_index :]
    if not bars:
        return "no_path"
    exit_price = None
    exit_time = None
    reason = None
    last = bars[0]
    for bar in bars:
        last = bar
        bar_close = bar.open_time + path.delta
        if bar.open_time >= deadline:
            reason = "max_hold"
            exit_price = _slip(bar.open, slippage, adverse_up=signal.side == "SHORT")
            exit_time = bar.open_time
            break
        hit_stop, hit_target, stop_fill = _bar_exits(signal.side, bar.open, bar.high, bar.low, stop, target, slippage)
        if hit_stop:
            reason = "stop"
            exit_price = stop_fill
            exit_time = bar_close
            break
        if hit_target:
            reason = "target"
            exit_price = target
            exit_time = bar_close
            break
        if bar_close >= deadline:
            reason = "max_hold"
            exit_price = _slip(bar.close, slippage, adverse_up=signal.side == "SHORT")
            exit_time = bar_close
            break
    if exit_price is None or exit_time is None:
        reason = "max_hold"
        exit_price = _slip(last.close, slippage, adverse_up=signal.side == "SHORT")
        exit_time = last.open_time + path.delta
    risk_per_unit = abs(entry - stop)
    if signal.side == "LONG":
        gross = (exit_price - entry) * quantity
    else:
        gross = (entry - exit_price) * quantity
    entry_fee = entry * quantity * TAKER_FEE_RATE
    exit_fee = exit_price * quantity * TAKER_FEE_RATE
    fees = -(entry_fee + exit_fee)
    slip_cost = abs(entry - setup.opens[signal.entry_index]) * quantity
    slip_cost += abs(exit_price - (target if reason == "target" else exit_price)) * 0.0
    funding_pnl = _funding(signal.side, quantity, entry, entry_time, exit_time, funding)
    net = gross + fees + funding_pnl
    r_mult = net / (risk_per_unit * quantity) if risk_per_unit > 0 and quantity > 0 else 0.0
    return Fill(
        signal=signal,
        entry_time=entry_time,
        entry_price=entry,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=reason or "max_hold",
        stop=stop,
        target=target,
        quantity=quantity,
        gross_pnl=gross,
        fees=fees,
        funding_pnl=funding_pnl,
        slippage_cost=slip_cost,
        net_pnl=net,
        r_multiple=r_mult,
        notional=entry * quantity,
    )


def _bar_exits(side, open_, high, low, stop, target, slippage):
    if side == "LONG":
        gap_stop = open_ <= stop
        gap_target = open_ >= target
        if gap_stop:
            return True, False, _slip(open_, slippage, adverse_up=False)
        if gap_target:
            return False, True, target
        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop:
            return True, False, _slip(stop, slippage, adverse_up=False)
        if hit_target:
            return False, True, target
        return False, False, None
    gap_stop = open_ >= stop
    gap_target = open_ <= target
    if gap_stop:
        return True, False, _slip(open_, slippage, adverse_up=True)
    if gap_target:
        return False, True, target
    hit_stop = high >= stop
    hit_target = low <= target
    if hit_stop:
        return True, False, _slip(stop, slippage, adverse_up=True)
    if hit_target:
        return False, True, target
    return False, False, None


def _funding(side, quantity, entry_price, entry_time, exit_time, funding):
    if not funding:
        return 0.0
    total = 0.0
    notional = entry_price * quantity
    for stamp, rate in funding:
        if entry_time < stamp <= exit_time:
            sign = 1.0 if side == "LONG" else -1.0
            total -= sign * rate * notional
    return total
