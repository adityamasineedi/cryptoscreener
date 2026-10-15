"""Causal swings, structure, BOS, and CHoCH for one timeframe.

Uses the production Wilder ATR series. Swing rules are this strategy version's
rules (two bars each side, strict inequality) and do not modify SignalEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.research.intraday_bos_sd.candles import Series
from app.research.intraday_bos_sd.spec import BREAK_BUFFER_ATR, SWING_LEFT, SWING_RIGHT


@dataclass
class Swing:
    kind: str
    price: float
    occur_index: int
    confirm_index: int
    occur_time: datetime
    available_time: datetime
    broken: bool = False


@dataclass
class StructureEvent:
    kind: str  # BOS or CHOCH
    direction: str  # BULLISH or BEARISH
    index: int
    time: datetime
    level: float
    close: float
    buffer: float
    swing: Swing


@dataclass
class StructureState:
    label: str = "NEUTRAL"  # BULLISH BEARISH NEUTRAL
    swings: list[Swing] = field(default_factory=list)
    events: list[StructureEvent] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


def _pair_direction(older: float, newer: float) -> str:
    if newer > older:
        return "UP"
    if newer < older:
        return "DOWN"
    return "FLAT"


def structure_label(swings: list[Swing]) -> str:
    highs = [s for s in swings if s.kind == "HIGH"]
    lows = [s for s in swings if s.kind == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"
    high_dir = _pair_direction(highs[-2].price, highs[-1].price)
    low_dir = _pair_direction(lows[-2].price, lows[-1].price)
    if high_dir == "UP" and low_dir == "UP":
        return "BULLISH"
    if high_dir == "DOWN" and low_dir == "DOWN":
        return "BEARISH"
    return "NEUTRAL"


def build_structure(series: Series) -> StructureState:
    state = StructureState()
    n = len(series)
    left = SWING_LEFT
    right = SWING_RIGHT
    for i in range(n):
        pivot = i - right
        if pivot >= left:
            h = series.highs[pivot]
            l = series.lows[pivot]
            left_h = series.highs[pivot - left : pivot]
            right_h = series.highs[pivot + 1 : pivot + right + 1]
            left_l = series.lows[pivot - left : pivot]
            right_l = series.lows[pivot + 1 : pivot + right + 1]
            available = series.close_time(i)
            if h > max(left_h) and h > max(right_h):
                state.swings.append(
                    Swing(
                        "HIGH",
                        h,
                        pivot,
                        i,
                        series.bars[pivot].open_time,
                        available,
                    )
                )
            if l < min(left_l) and l < min(right_l):
                state.swings.append(
                    Swing(
                        "LOW",
                        l,
                        pivot,
                        i,
                        series.bars[pivot].open_time,
                        available,
                    )
                )
        state.label = structure_label(state.swings)
        state.labels.append(state.label)
        atr = series.preceding_atr(i)
        if atr is None or atr <= 0 or state.label == "NEUTRAL":
            continue
        buffer = BREAK_BUFFER_ATR * atr
        close = series.closes[i]
        if state.label == "BULLISH":
            highs = [s for s in state.swings if s.kind == "HIGH" and not s.broken]
            lows = [s for s in state.swings if s.kind == "LOW" and not s.broken]
            if highs and close > highs[-1].price + buffer:
                swing = highs[-1]
                swing.broken = True
                state.events.append(
                    StructureEvent("BOS", "BULLISH", i, series.close_time(i), swing.price, close, buffer, swing)
                )
            if lows and close < lows[-1].price - buffer:
                swing = lows[-1]
                swing.broken = True
                state.events.append(
                    StructureEvent("CHOCH", "BEARISH", i, series.close_time(i), swing.price, close, buffer, swing)
                )
        elif state.label == "BEARISH":
            lows = [s for s in state.swings if s.kind == "LOW" and not s.broken]
            highs = [s for s in state.swings if s.kind == "HIGH" and not s.broken]
            if lows and close < lows[-1].price - buffer:
                swing = lows[-1]
                swing.broken = True
                state.events.append(
                    StructureEvent("BOS", "BEARISH", i, series.close_time(i), swing.price, close, buffer, swing)
                )
            if highs and close > highs[-1].price + buffer:
                swing = highs[-1]
                swing.broken = True
                state.events.append(
                    StructureEvent("CHOCH", "BULLISH", i, series.close_time(i), swing.price, close, buffer, swing)
                )
    return state


def label_as_of(series: Series, state: StructureState, close_time: datetime) -> str:
    """Structure label using swings available at close_time.

    ``build_structure`` mutates broken flags through the end of the series, so
    as-of labels are recomputed from swing availability only.
    """
    available = [s for s in state.swings if s.available_time <= close_time]
    # Preserve confirmation order, which is append order.
    return structure_label(available)
