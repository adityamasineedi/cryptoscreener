"""Dataclasses for live ↔ backtest parity events (research-only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.research.live_backtest_parity.constants import (
    BACKTEST_ENTRY,
    ENTRY_PRICE_NOT_CHECKED,
    LIVE_SIGNAL_PRICE,
    PAPER_ENTRY,
    PRICE_SOURCE_UNAVAILABLE,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def iso_utc(ts: datetime | None) -> str | None:
    t = ensure_utc(ts)
    return t.isoformat() if t else None


def ms_between(start: datetime | None, end: datetime | None) -> int | None:
    """Latency in ms from recorded timestamps only — never inferred."""
    a = ensure_utc(start)
    b = ensure_utc(end)
    if a is None or b is None:
        return None
    return int((b - a).total_seconds() * 1000.0)


@dataclass
class EntryTriad:
    """Three distinct entry values — never overwrite one with another."""

    backtest_entry: float | None = None
    live_signal_price: float | None = None
    paper_entry: float | None = None
    price_source: str = PRICE_SOURCE_UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            BACKTEST_ENTRY: self.backtest_entry,
            LIVE_SIGNAL_PRICE: self.live_signal_price,
            PAPER_ENTRY: self.paper_entry,
            "price_source": self.price_source,
        }


@dataclass
class EventTimeline:
    """UTC timestamps for the live event chain. Missing = not recorded."""

    candle_open_time: datetime | None = None
    candle_close_time: datetime | None = None
    live_frame_received_at: datetime | None = None
    signal_detected_at: datetime | None = None
    trade_plan_created_at: datetime | None = None
    alert_generated_at: datetime | None = None
    telegram_sent_at: datetime | None = None
    paper_entry_at: datetime | None = None

    def latencies_ms(self) -> dict[str, int | None]:
        close = self.candle_close_time
        signal = self.signal_detected_at
        plan = self.trade_plan_created_at
        alert = self.alert_generated_at
        paper = self.paper_entry_at
        telegram = self.telegram_sent_at
        return {
            "signal_latency_ms": ms_between(close, signal),
            "alert_generation_latency_ms": ms_between(plan, alert),
            "telegram_latency_ms": ms_between(alert, telegram),
            "paper_entry_latency_ms": ms_between(signal, paper),
            "total_signal_to_entry_ms": ms_between(signal, paper),
            "candle_close_to_signal_ms": ms_between(close, signal),
            "signal_to_trade_plan_ms": ms_between(signal, plan),
            "trade_plan_to_alert_ms": ms_between(plan, alert),
            "alert_to_paper_entry_ms": ms_between(alert, paper),
            "candle_close_to_paper_entry_ms": ms_between(close, paper),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "candle_open_time": iso_utc(self.candle_open_time),
            "candle_close_time": iso_utc(self.candle_close_time),
            "live_frame_received_at": iso_utc(self.live_frame_received_at),
            "signal_detected_at": iso_utc(self.signal_detected_at),
            "trade_plan_created_at": iso_utc(self.trade_plan_created_at),
            "alert_generated_at": iso_utc(self.alert_generated_at),
            "telegram_sent_at": iso_utc(self.telegram_sent_at),
            "paper_entry_at": iso_utc(self.paper_entry_at),
            **self.latencies_ms(),
        }


@dataclass
class CandleCloseValidation:
    as_of_timestamp: datetime | None = None
    latest_candle_used: str | None = None
    latest_candle_close: datetime | None = None
    future_data_detected: bool = False
    signal_after_close: bool | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_timestamp": iso_utc(self.as_of_timestamp),
            "latest_candle_used": self.latest_candle_used,
            "latest_candle_close": iso_utc(self.latest_candle_close),
            "future_data_detected": self.future_data_detected,
            "signal_after_close": self.signal_after_close,
            "details": dict(self.details),
        }


@dataclass
class ParityEvent:
    """One live candidate with AS-OF shadow comparison + entry triad."""

    event_id: str
    symbol: str
    timeframe: str
    direction: str | None = None
    setup: str | None = None
    bos: str | None = None
    choch: str | None = None
    parity_result: str | None = None
    mismatch_reason: str | None = None

    live_status: str | None = None
    backtest_status: str | None = None
    live_direction: str | None = None
    backtest_direction: str | None = None
    live_entry: float | None = None
    backtest_entry: float | None = None
    live_sl: float | None = None
    backtest_sl: float | None = None
    live_tp: float | None = None
    backtest_tp: float | None = None
    live_rr: float | None = None
    backtest_rr: float | None = None

    entry_triad: EntryTriad = field(default_factory=EntryTriad)
    entry_deviation_pct: float | None = None
    live_vs_backtest_bps: float | None = None
    absolute_difference: float | None = None
    entry_price_status: str = ENTRY_PRICE_NOT_CHECKED
    entry_price_label: str | None = None

    timeline: EventTimeline = field(default_factory=EventTimeline)
    candle_validation: CandleCloseValidation = field(
        default_factory=CandleCloseValidation
    )

    production_approved: bool = False
    telegram_eligible: bool = False
    strategy_id: str | None = None
    source: str | None = None

    telegram_status: str | None = None  # generated|sent|blocked|failed|skipped
    telegram_message_id: str | None = None
    paper_opened: bool = False

    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_triad"] = self.entry_triad.to_dict()
        d["timeline"] = self.timeline.to_dict()
        d["candle_validation"] = self.candle_validation.to_dict()
        d.update(self.timeline.latencies_ms())
        return d

    def alert_payload(self) -> dict[str, Any]:
        """Structured alert payload — never hides planned vs paper entry."""
        return {
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "source": self.source,
            "timeframe": self.timeframe,
            "direction": self.direction or self.live_direction,
            "backtest_entry": self.entry_triad.backtest_entry,
            "live_signal_price": self.entry_triad.live_signal_price,
            "paper_entry": self.entry_triad.paper_entry,
            "entry_deviation_pct": self.entry_deviation_pct,
            "SL": self.live_sl,
            "TP": self.live_tp,
            "RR": self.live_rr,
            "signal_timestamp": iso_utc(self.timeline.signal_detected_at),
            "paper_timestamp": iso_utc(self.timeline.paper_entry_at),
            "status": self.parity_result,
            "entry_price_status": self.entry_price_status,
            "production_approved": False,
            "telegram_eligible": False,
            "price_source": self.entry_triad.price_source,
        }
