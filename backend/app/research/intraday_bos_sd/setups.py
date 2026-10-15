"""Shared setup engine. Backtests and the screener both call this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.research.intraday_bos_sd.candles import Series
from app.research.intraday_bos_sd.spec import (
    CONFIRM_BARS_AFTER_TOUCH,
    STOP_BUFFER_ATR,
    VOLUME_LOOKBACK,
    VOLUME_RATIO_MIN,
)
from app.research.intraday_bos_sd.structure import StructureState, build_structure, label_as_of
from app.research.intraday_bos_sd.zones import Zone, create_zones, select_zone


@dataclass
class SetupSignal:
    symbol: str
    variant: str
    side: str
    tier: str
    setup_time: datetime
    confirmation_index: int
    entry_index: int
    stop: float
    atr: float
    volume_ratio: float | None
    zone_low: float | None
    zone_high: float | None
    zone_active_from: datetime | None
    bos_level: float | None
    choch_level: float | None
    evidence: dict
    idempotency_key: str


@dataclass
class Rejection:
    symbol: str
    variant: str
    time: datetime
    reason: str
    detail: dict = field(default_factory=dict)


@dataclass
class SymbolBook:
    symbol: str
    tier: str
    setup: Series
    direction: Series
    zones_tf: Series
    execution: Series | None
    direction_state: StructureState
    setup_state: StructureState
    zone_state: StructureState
    zones: list[Zone]


def prepare_book(
    symbol: str,
    tier: str,
    setup: Series,
    direction: Series,
    zones_tf: Series,
    execution: Series | None = None,
) -> SymbolBook:
    zone_state = build_structure(zones_tf)
    return SymbolBook(
        symbol=symbol,
        tier=tier,
        setup=setup,
        direction=direction,
        zones_tf=zones_tf,
        execution=execution,
        direction_state=build_structure(direction),
        setup_state=build_structure(setup),
        zone_state=zone_state,
        zones=create_zones(zones_tf, zone_state),
    )


def _hour_label(book: SymbolBook, when: datetime) -> str:
    return label_as_of(book.direction, book.direction_state, when)


def _volume_ratio(series: Series, index: int) -> float | None:
    if index < VOLUME_LOOKBACK:
        return None
    window = series.volumes[index - VOLUME_LOOKBACK : index]
    base = sum(window) / VOLUME_LOOKBACK
    if base <= 0:
        return None
    return series.volumes[index] / base


def _key(symbol: str, variant: str, side: str, when: datetime) -> str:
    return f"{symbol}|{variant}|{side}|{when.isoformat()}"


def _stop_long(extreme: float, atr: float) -> float:
    return extreme - STOP_BUFFER_ATR * atr


def _stop_short(extreme: float, atr: float) -> float:
    return extreme + STOP_BUFFER_ATR * atr


def generate_signals(book: SymbolBook, variant: str) -> tuple[list[SetupSignal], list[Rejection]]:
    variant = variant.upper()
    if variant == "A":
        return _variant_a(book)
    if variant in {"B", "C", "D"}:
        return _zone_variants(book, variant)
    raise ValueError(f"unknown variant {variant}")


def _variant_a(book: SymbolBook) -> tuple[list[SetupSignal], list[Rejection]]:
    signals: list[SetupSignal] = []
    rejected: list[Rejection] = []
    series = book.setup
    for event in book.setup_state.events:
        if event.kind != "BOS":
            continue
        when = series.close_time(event.index)
        hour = _hour_label(book, when)
        side = "LONG" if event.direction == "BULLISH" else "SHORT"
        if (side == "LONG" and hour != "BULLISH") or (side == "SHORT" and hour != "BEARISH"):
            rejected.append(Rejection(book.symbol, "A", when, "hour_structure"))
            continue
        if event.index + 1 >= len(series):
            rejected.append(Rejection(book.symbol, "A", when, "no_next_bar"))
            continue
        atr = series.atr[event.index]
        if atr is None or atr <= 0:
            rejected.append(Rejection(book.symbol, "A", when, "atr_unavailable"))
            continue
        start = event.swing.occur_index
        if side == "LONG":
            extreme = min(series.lows[start : event.index + 1])
            stop = _stop_long(extreme, atr)
        else:
            extreme = max(series.highs[start : event.index + 1])
            stop = _stop_short(extreme, atr)
        signals.append(
            _make(
                book,
                "A",
                side,
                event.index,
                stop,
                atr,
                None,
                None,
                event.level,
                None,
                {"swing_occur_index": start, "extreme": extreme},
            )
        )
    return signals, rejected


def _make(
    book: SymbolBook,
    variant: str,
    side: str,
    confirmation_index: int,
    stop: float,
    atr: float,
    zone: Zone | None,
    volume_ratio: float | None,
    bos_level: float | None,
    choch_level: float | None,
    evidence: dict,
) -> SetupSignal:
    when = book.setup.close_time(confirmation_index)
    return SetupSignal(
        symbol=book.symbol,
        variant=variant,
        side=side,
        tier=book.tier,
        setup_time=when,
        confirmation_index=confirmation_index,
        entry_index=confirmation_index + 1,
        stop=stop,
        atr=atr,
        volume_ratio=volume_ratio,
        zone_low=None if zone is None else zone.low,
        zone_high=None if zone is None else zone.high,
        zone_active_from=None if zone is None else zone.active_from,
        bos_level=bos_level,
        choch_level=choch_level,
        evidence=evidence,
        idempotency_key=_key(book.symbol, variant, side, when),
    )


def _zone_variants(book: SymbolBook, variant: str) -> tuple[list[SetupSignal], list[Rejection]]:
    signals: list[SetupSignal] = []
    rejected: list[Rejection] = []
    series = book.setup
    pending: dict | None = None
    consumed_ids: set[int] = set()
    bos_at = {
        (event.index, event.direction): event
        for event in book.setup_state.events
        if event.kind == "BOS"
    }
    choch_at = {
        (event.index, event.direction): event
        for event in book.setup_state.events
        if event.kind == "CHOCH"
    }

    def fail(when: datetime, reason: str, zone: Zone | None = None) -> None:
        if zone is not None:
            zone.touched = True
        rejected.append(Rejection(book.symbol, variant, when, reason))

    for i in range(len(series)):
        when = series.close_time(i)
        if pending is not None:
            zone = pending["zone"]
            side = pending["side"]
            if side == "LONG":
                pending["extreme"] = min(pending["extreme"], series.lows[i])
            else:
                pending["extreme"] = max(pending["extreme"], series.highs[i])
            if i > pending["touch"] + CONFIRM_BARS_AFTER_TOUCH:
                fail(when, "confirmation_window", zone)
                pending = None
                continue
            if zone.invalidated(when) or zone.expired(when):
                fail(when, "zone_dead", zone)
                pending = None
                continue
            done = _try_confirm(
                book, variant, pending, i, when, signals, rejected, bos_at, choch_at
            )
            if done:
                pending = None
            continue

        hour = _hour_label(book, when)
        for side, zone_side, hour_needed, setup_needed in (
            ("LONG", "DEMAND", "BULLISH", "BEARISH"),
            ("SHORT", "SUPPLY", "BEARISH", "BULLISH"),
        ):
            if hour != hour_needed:
                continue
            zone = select_zone(
                book.zones,
                side=zone_side,
                when=when,
                price_low=series.lows[i],
                price_high=series.highs[i],
            )
            if zone is None or id(zone) in consumed_ids:
                continue
            consumed_ids.add(id(zone))
            zone.touched = True
            if variant in {"C", "D"} and book.setup_state.labels[i] != setup_needed:
                fail(when, "entry_structure", zone)
                continue
            pending = {
                "zone": zone,
                "side": side,
                "touch": i,
                "extreme": series.lows[i] if side == "LONG" else series.highs[i],
                "choch_index": None,
                "choch_level": None,
            }
            if _try_confirm(book, variant, pending, i, when, signals, rejected, bos_at, choch_at):
                pending = None
            break
    return signals, rejected


def _try_confirm(book, variant, pending, i, when, signals, rejected, bos_at, choch_at) -> bool:
    """Return True when the pending setup is finished (signal or terminal reject)."""
    series = book.setup
    side = pending["side"]
    zone = pending["zone"]
    if variant == "B":
        event = bos_at.get((i, "BULLISH" if side == "LONG" else "BEARISH"))
        if event is None:
            return False
        return _emit_zone_signal(book, variant, pending, i, when, signals, rejected, event.level, None)
    # C and D: CHoCH then post-CHoCH swing break.
    if pending["choch_index"] is None:
        choch = choch_at.get((i, "BULLISH" if side == "LONG" else "BEARISH"))
        if choch is not None and choch.index >= pending["touch"]:
            pending["choch_index"] = choch.index
            pending["choch_level"] = choch.level
        return False
    swing = _post_swing(book, pending["choch_index"], i, "HIGH" if side == "LONG" else "LOW")
    if swing is None or i <= swing.confirm_index:
        return False
    atr_prev = series.preceding_atr(i)
    if atr_prev is None:
        return False
    buffer = 0.1 * atr_prev
    close = series.closes[i]
    if side == "LONG" and close <= swing.price + buffer:
        return False
    if side == "SHORT" and close >= swing.price - buffer:
        return False
    ratio = _volume_ratio(series, i)
    if variant == "D" and (ratio is None or ratio < VOLUME_RATIO_MIN):
        rejected.append(Rejection(book.symbol, variant, when, "relative_volume"))
        zone.touched = True
        return True
    return _emit_zone_signal(book, variant, pending, i, when, signals, rejected, swing.price, pending["choch_level"], ratio)


def _emit_zone_signal(book, variant, pending, i, when, signals, rejected, bos_level, choch_level, ratio=None) -> bool:
    series = book.setup
    if i + 1 >= len(series):
        rejected.append(Rejection(book.symbol, variant, when, "no_next_bar"))
        return True
    atr = series.atr[i]
    if atr is None or atr <= 0:
        rejected.append(Rejection(book.symbol, variant, when, "atr_unavailable"))
        return True
    zone: Zone = pending["zone"]
    if pending["side"] == "LONG":
        extreme = min(zone.low, pending["extreme"])
        stop = _stop_long(extreme, atr)
    else:
        extreme = max(zone.high, pending["extreme"])
        stop = _stop_short(extreme, atr)
    signals.append(
        _make(
            book,
            variant,
            pending["side"],
            i,
            stop,
            atr,
            zone,
            ratio,
            bos_level,
            choch_level,
            {"touch_index": pending["touch"], "extreme": extreme},
        )
    )
    return True


def _post_swing(book: SymbolBook, choch_index: int, index: int, kind: str):
    found = [
        s
        for s in book.setup_state.swings
        if s.kind == kind and s.occur_index > choch_index and s.confirm_index <= index
    ]
    if not found:
        return None
    return found[-1]
