"""Causal swing breaks and variant fills. Live detectors are only read."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.research.ohlcv_structure_experiment.config import (
    CLOSE_BACK_BARS,
    FEE_RATE_PER_SIDE,
    HOLD_BARS,
    RETEST_WINDOW,
    RIGHT_BARS_BASELINE,
    RIGHT_BARS_FASTER,
    SLIPPAGE_RATE,
    TARGET_R,
)
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import infer_trend


def _num(candle: Mapping[str, Any], *names: str) -> float:
    for name in names:
        if candle.get(name) is not None:
            return float(candle[name])
    return 0.0


def _stamp(candle: Mapping[str, Any], index: int) -> str:
    raw = candle.get("time") or candle.get("timestamp")
    if isinstance(raw, datetime):
        return raw.isoformat()
    if isinstance(raw, str):
        return raw
    return f"bar:{index}"


@dataclass
class BreakEvent:
    event_id: str
    symbol: str
    direction: str
    call_type: str
    pivot_index: int
    pivot_at: str
    confirmed_index: int
    confirmed_at: str
    break_index: int
    break_at: str
    level: float
    stop: float
    atr: float | None
    confirmation_delay_bars: int
    bars_from_confirmation_to_break: int


@dataclass
class SetupRecord:
    event: BreakEvent
    variant: str
    status: str
    qualified_index: int | None = None
    qualified_at: str | None = None
    entry_index: int | None = None
    entry_at: str | None = None
    invalidated_at: str | None = None
    expired_at: str | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    exit_index: int | None = None
    exit_price: float | None = None
    outcome: str | None = None
    ambiguous: bool = False
    net_r: float | None = None
    fee_r: float | None = None
    funding_r: float | None = None
    holding_bars: int | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    close_back_10: bool | None = None
    post_entry_close_back_10: bool | None = None
    stop_distance_atr: float | None = None
    target_distance_atr: float | None = None
    lifecycle_end: int = 0
    notes: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        event = self.event
        return {
            "event_id": event.event_id,
            "symbol": event.symbol,
            "direction": event.direction,
            "call_type": event.call_type,
            "variant": self.variant,
            "status": self.status,
            "provisional_swing": "not_tradable",
            "pivot_at": event.pivot_at,
            "confirmed_at": event.confirmed_at,
            "break_at": event.break_at,
            "qualified_at": self.qualified_at,
            "entry_at": self.entry_at,
            "invalidated_at": self.invalidated_at,
            "expired_at": self.expired_at,
            "broken_level": event.level,
            "stop_price": self.stop_price if self.stop_price is not None else event.stop,
            "target_price": self.target_price,
            "entry_price": self.entry_price,
            "outcome": self.outcome,
            "ambiguous": self.ambiguous,
            "net_r": self.net_r,
            "confirmation_delay_bars": event.confirmation_delay_bars,
            "bars_from_confirmation_to_break": event.bars_from_confirmation_to_break,
            "detector": {
                "left_bars": 3,
                "right_bars": event.confirmation_delay_bars,
            },
        }


def scan_breaks(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    right: int,
    left: int = 3,
) -> list[BreakEvent]:
    """First close through each confirmed swing, using only swings known at that bar."""
    if right < 1 or len(candles) < left + right + 2:
        return []
    swings = detect_swings(
        candles,
        left=left,
        right=right,
        symbol=symbol,
        timeframe="1h",
        as_of_index=len(candles) - 1,
    )
    highs = [swing for swing in swings if swing.swing_type == "HIGH"]
    lows = [swing for swing in swings if swing.swing_type == "LOW"]
    closes = [_num(candle, "close", "c") for candle in candles]
    highs_px = [_num(candle, "high", "h") for candle in candles]
    lows_px = [_num(candle, "low", "l") for candle in candles]
    events: list[BreakEvent] = []
    used: set[tuple[str, int]] = set()
    hi = 0
    lo = 0
    for index in range(1, len(candles)):
        while hi < len(highs) and highs[hi].bar_index + right <= index:
            hi += 1
        while lo < len(lows) and lows[lo].bar_index + right <= index:
            lo += 1
        if hi < 1 or lo < 1:
            continue
        last_high = highs[hi - 1]
        last_low = lows[lo - 1]
        previous = closes[index - 1]
        close = closes[index]
        upside = previous <= last_high.price < close and last_high.bar_index < index
        downside = previous >= last_low.price > close and last_low.bar_index < index
        if not upside and not downside:
            continue
        if upside:
            key = ("LONG", last_high.bar_index)
            pivot = last_high
            other = last_low
            direction = "LONG"
        else:
            key = ("SHORT", last_low.bar_index)
            pivot = last_low
            other = last_high
            direction = "SHORT"
        if key in used:
            continue
        if other.bar_index >= index or other.bar_index + right > index:
            continue
        used.add(key)
        visible = [swing for swing in swings if swing.bar_index + right <= index]
        trend = str(infer_trend(visible).get("trend") or "")
        if direction == "LONG":
            call_type = (
                "bos_with_trend"
                if trend == "BULLISH"
                else "choch_against_trend"
                if trend in {"BEARISH", "NEUTRAL"} and last_high.label == "LH"
                else "other_close_through"
            )
            level = last_high.price
            stop = last_low.price
        else:
            call_type = (
                "bos_with_trend"
                if trend == "BEARISH"
                else "choch_against_trend"
                if trend in {"BULLISH", "NEUTRAL"} and last_low.label == "HL"
                else "other_close_through"
            )
            level = last_low.price
            stop = last_high.price
        atr = calc_atr(highs_px[: index + 1], lows_px[: index + 1], closes[: index + 1], 14)
        confirmed_index = pivot.bar_index + right
        events.append(
            BreakEvent(
                event_id=f"{symbol}:{direction}:{pivot.bar_index}:{level}",
                symbol=symbol,
                direction=direction,
                call_type=call_type,
                pivot_index=pivot.bar_index,
                pivot_at=_stamp(candles[pivot.bar_index], pivot.bar_index),
                confirmed_index=confirmed_index,
                confirmed_at=_stamp(candles[confirmed_index], confirmed_index),
                break_index=index,
                break_at=_stamp(candles[index], index),
                level=level,
                stop=stop,
                atr=float(atr) if atr else None,
                confirmation_delay_bars=right,
                bars_from_confirmation_to_break=index - confirmed_index,
            )
        )
    return events


def _beyond(close: float, level: float, direction: str) -> bool:
    return close > level if direction == "LONG" else close < level


def _through_wrong(close: float, level: float, direction: str) -> bool:
    return close < level if direction == "LONG" else close > level


def _slip_entry(open_price: float, direction: str) -> float:
    if direction == "LONG":
        return open_price * (1.0 + SLIPPAGE_RATE)
    return open_price * (1.0 - SLIPPAGE_RATE)


def _slip_exit(price: float, direction: str) -> float:
    if direction == "LONG":
        return price * (1.0 - SLIPPAGE_RATE)
    return price * (1.0 + SLIPPAGE_RATE)


def qualify(event: BreakEvent, candles: Sequence[Mapping[str, Any]], variant: str) -> SetupRecord:
    """Turn a completed break into a qualified, expired, or invalid setup. No fill yet."""
    record = SetupRecord(event=event, variant=variant, status="break_observed")
    break_index = event.break_index
    direction = event.direction
    level = event.level
    if variant in {"A_corrected_baseline", "D_faster_confirmation"}:
        record.qualified_index = break_index
        record.qualified_at = event.break_at
        record.entry_index = break_index + 1
        record.status = "entry_qualified"
        return record
    if variant == "B_acceptance":
        follow = break_index + 1
        if follow >= len(candles):
            record.status = "expired"
            record.expired_at = event.break_at
            record.lifecycle_end = break_index
            return record
        close = _num(candles[follow], "close", "c")
        if _beyond(close, level, direction):
            record.qualified_index = follow
            record.qualified_at = _stamp(candles[follow], follow)
            record.entry_index = follow + 1
            record.status = "entry_qualified"
            return record
        record.status = "expired"
        record.expired_at = _stamp(candles[follow], follow)
        record.lifecycle_end = follow
        return record
    follow_end = min(len(candles) - 1, break_index + RETEST_WINDOW)
    for index in range(break_index + 1, follow_end + 1):
        _open, high, low, close = (
            _num(candles[index], "open", "o"),
            _num(candles[index], "high", "h"),
            _num(candles[index], "low", "l"),
            _num(candles[index], "close", "c"),
        )
        if _through_wrong(close, level, direction):
            record.status = "invalidated"
            record.invalidated_at = _stamp(candles[index], index)
            record.lifecycle_end = index
            return record
        touched = low <= level if direction == "LONG" else high >= level
        if touched and _beyond(close, level, direction):
            record.qualified_index = index
            record.qualified_at = _stamp(candles[index], index)
            record.entry_index = index + 1
            record.status = "entry_qualified"
            return record
    record.status = "expired"
    record.expired_at = _stamp(candles[follow_end], follow_end)
    record.lifecycle_end = follow_end
    return record


def _funding_r(
    funding: Sequence[tuple[datetime, float]] | None,
    *,
    entry_at: datetime | None,
    exit_at: datetime | None,
    entry_price: float,
    risk: float,
) -> float:
    if not funding or entry_at is None or exit_at is None or risk <= 0:
        return 0.0
    stamps = [stamp for stamp, _rate in funding]
    start = bisect_right(stamps, entry_at)
    end = bisect_right(stamps, exit_at)
    total = 0.0
    for _stamp, rate in funding[start:end]:
        total += rate * entry_price / risk
    return total


def _parse_time(value: str | None) -> datetime | None:
    if not value or value.startswith("bar:"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def simulate_fill(
    record: SetupRecord,
    candles: Sequence[Mapping[str, Any]],
    *,
    funding: Sequence[tuple[datetime, float]] | None = None,
) -> SetupRecord:
    """Fill a qualified setup. Incomplete windows stay unfilled and are not given a zero return."""
    if record.status != "entry_qualified" or record.entry_index is None:
        return record
    entry_index = record.entry_index
    if entry_index >= len(candles):
        record.status = "expired"
        record.expired_at = record.event.break_at
        record.lifecycle_end = record.event.break_index
        record.notes.append("no_bar_after_qualification")
        return record
    direction = record.event.direction
    stop = record.event.stop
    raw_open = _num(candles[entry_index], "open", "o")
    entry = _slip_entry(raw_open, direction)
    invalid = entry <= stop if direction == "LONG" else entry >= stop
    record.entry_at = _stamp(candles[entry_index], entry_index)
    record.entry_price = entry
    record.stop_price = stop
    if invalid:
        record.status = "skipped_invalid_entry"
        record.lifecycle_end = entry_index
        record.notes.append("entry_gap_through_frozen_stop")
        return record
    risk = abs(entry - stop)
    target = entry + TARGET_R * risk if direction == "LONG" else entry - TARGET_R * risk
    record.target_price = target
    if record.event.atr and record.event.atr > 0:
        record.stop_distance_atr = risk / record.event.atr
        record.target_distance_atr = abs(target - entry) / record.event.atr
    last_index = entry_index + HOLD_BARS - 1
    if last_index >= len(candles):
        record.status = "incomplete_window"
        record.lifecycle_end = len(candles) - 1
        record.notes.append("outcome_window_exceeds_sample")
        return record
    exit_index = None
    exit_price = None
    outcome = None
    ambiguous = False
    mae = 0.0
    mfe = 0.0
    for index in range(entry_index, last_index + 1):
        bar_open = _num(candles[index], "open", "o")
        high = _num(candles[index], "high", "h")
        low = _num(candles[index], "low", "l")
        close = _num(candles[index], "close", "c")
        if direction == "LONG":
            mae = max(mae, entry - low)
            mfe = max(mfe, high - entry)
        else:
            mae = max(mae, high - entry)
            mfe = max(mfe, entry - low)
        if index > entry_index:
            gapped = bar_open <= stop if direction == "LONG" else bar_open >= stop
            if gapped:
                exit_index = index
                exit_price = bar_open
                outcome = "stop"
                break
        hit_stop = low <= stop if direction == "LONG" else high >= stop
        hit_target = high >= target if direction == "LONG" else low <= target
        if hit_stop and hit_target:
            exit_index = index
            exit_price = stop
            outcome = "stop"
            ambiguous = True
            break
        if hit_stop:
            exit_index = index
            exit_price = stop
            outcome = "stop"
            break
        if hit_target:
            exit_index = index
            exit_price = target
            outcome = "target"
            break
        if index == last_index:
            exit_index = index
            exit_price = _slip_exit(close, direction)
            outcome = "timeout"
            break
    assert exit_index is not None and exit_price is not None and outcome is not None
    gross = (exit_price - entry) / risk if direction == "LONG" else (entry - exit_price) / risk
    fee_r = FEE_RATE_PER_SIDE * (entry + exit_price) / risk
    entry_time = _parse_time(record.entry_at)
    exit_time = _parse_time(_stamp(candles[exit_index], exit_index))
    funding_r = _funding_r(
        funding,
        entry_at=entry_time,
        exit_at=exit_time,
        entry_price=entry,
        risk=risk,
    )
    record.exit_index = exit_index
    record.exit_price = exit_price
    record.outcome = outcome
    record.ambiguous = ambiguous
    record.fee_r = fee_r
    record.funding_r = funding_r
    record.net_r = gross - fee_r - funding_r
    record.holding_bars = exit_index - entry_index + 1
    record.mae_r = mae / risk
    record.mfe_r = mfe / risk
    record.lifecycle_end = exit_index
    record.status = "filled"
    record.close_back_10 = _close_back(candles, record.event)
    record.post_entry_close_back_10 = _close_back_from(candles, entry_index, record.event)
    return record


def _close_back(candles: Sequence[Mapping[str, Any]], event: BreakEvent) -> bool | None:
    end = event.break_index + CLOSE_BACK_BARS
    if end >= len(candles):
        return None
    for index in range(event.break_index + 1, end + 1):
        close = _num(candles[index], "close", "c")
        if _through_wrong(close, event.level, event.direction) or close == event.level:
            if _through_wrong(close, event.level, event.direction):
                return True
    return False


def _close_back_from(
    candles: Sequence[Mapping[str, Any]],
    start: int,
    event: BreakEvent,
) -> bool | None:
    end = start + CLOSE_BACK_BARS - 1
    if end >= len(candles):
        return None
    for index in range(start, end + 1):
        close = _num(candles[index], "close", "c")
        if _through_wrong(close, event.level, event.direction):
            return True
    return False


def run_variant(
    candles: Sequence[Mapping[str, Any]],
    events: Sequence[BreakEvent],
    variant: str,
    *,
    funding: Sequence[tuple[datetime, float]] | None = None,
) -> list[SetupRecord]:
    """One position at a time. A pivot is consumed by scan_breaks before this runs."""
    records: list[SetupRecord] = []
    busy_until = -1
    for event in events:
        record = qualify(event, candles, variant)
        record.close_back_10 = _close_back(candles, event)
        if record.status == "entry_qualified":
            assert record.entry_index is not None
            if record.entry_index <= busy_until:
                record.status = "skipped_overlap"
                record.lifecycle_end = record.entry_index
                record.notes.append("position_already_open")
            else:
                simulate_fill(record, candles, funding=funding)
                if record.status == "filled" and record.exit_index is not None:
                    busy_until = record.exit_index
        records.append(record)
    return records


def breaks_for_variant(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    variant: str,
) -> list[BreakEvent]:
    right = RIGHT_BARS_FASTER if variant == "D_faster_confirmation" else RIGHT_BARS_BASELINE
    return scan_breaks(candles, symbol=symbol, right=right)
