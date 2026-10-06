"""Explicit COMBO_03_TRANSITION setup state machine (PIT-safe)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.research.combo03_transition.params import (
    MAX_BARS_STRUCTURE_TO_15M,
    MAX_BARS_SWEEP_TO_STRUCTURE,
)

STATE_IDLE = "IDLE"
STATE_SWEEP_DETECTED = "SWEEP_DETECTED"
STATE_REJECTION_CONFIRMED = "REJECTION_CONFIRMED"
STATE_STRUCTURE_SHIFT_CONFIRMED = "STRUCTURE_SHIFT_CONFIRMED"
STATE_WAITING_FOR_15M_CONFIRMATION = "WAITING_FOR_15M_CONFIRMATION"
STATE_ENTRY_ELIGIBLE = "ENTRY_ELIGIBLE"
STATE_ENTERED = "ENTERED"
STATE_INVALIDATED = "INVALIDATED"
STATE_EXPIRED = "EXPIRED"


@dataclass
class SetupState:
    """Active setup tracked across 1h bars. Only past/current bar data."""

    state: str = STATE_IDLE
    direction: str | None = None
    regime_at_setup: str | None = None
    sweep_timestamp: str | None = None
    sweep_bar_index: int | None = None
    sweep_direction: str | None = None
    sweep_level: float | None = None
    sweep_high: float | None = None
    sweep_low: float | None = None
    sweep_close: float | None = None
    sweep_source: str | None = None
    sweep_event: str | None = None
    rejection_timestamp: str | None = None
    rejection_bar_index: int | None = None
    structure_shift: str | None = None
    bos_or_choch: str | None = None
    structure_shift_timestamp: str | None = None
    structure_shift_bar_index: int | None = None
    # Frozen at structure-shift time for existing risk finalize (entry bar may be later).
    structure_bos: dict[str, Any] | None = None
    structure_choch: dict[str, Any] | None = None
    structure_impulse: dict[str, Any] | None = None
    confirmation_15m: bool = False
    confirmation_type: str | None = None
    confirmation_timestamp: str | None = None
    confirmation_candle_close: float | None = None
    displacement: dict[str, Any] | None = None
    event_key: str | None = None
    labels: list[str] = field(default_factory=list)
    invalidation_reason: str | None = None

    def reset(self, *, reason: str | None = None) -> None:
        self.state = STATE_IDLE
        self.direction = None
        self.regime_at_setup = None
        self.sweep_timestamp = None
        self.sweep_bar_index = None
        self.sweep_direction = None
        self.sweep_level = None
        self.sweep_high = None
        self.sweep_low = None
        self.sweep_close = None
        self.sweep_source = None
        self.sweep_event = None
        self.rejection_timestamp = None
        self.rejection_bar_index = None
        self.structure_shift = None
        self.bos_or_choch = None
        self.structure_shift_timestamp = None
        self.structure_shift_bar_index = None
        self.structure_bos = None
        self.structure_choch = None
        self.structure_impulse = None
        self.confirmation_15m = False
        self.confirmation_type = None
        self.confirmation_timestamp = None
        self.confirmation_candle_close = None
        self.displacement = None
        self.event_key = None
        self.labels = []
        self.invalidation_reason = reason

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "direction": self.direction,
            "regime": self.regime_at_setup,
            "sweep_timestamp": self.sweep_timestamp,
            "sweep_bar_index": self.sweep_bar_index,
            "sweep_direction": self.sweep_direction,
            "sweep_level": self.sweep_level,
            "sweep_high": self.sweep_high,
            "sweep_low": self.sweep_low,
            "sweep_close": self.sweep_close,
            "sweep_source": self.sweep_source,
            "sweep_event": self.sweep_event,
            "rejection_timestamp": self.rejection_timestamp,
            "rejection_bar_index": self.rejection_bar_index,
            "structure_shift": self.structure_shift,
            "BOS_or_CHoCH": self.bos_or_choch,
            "structure_shift_timestamp": self.structure_shift_timestamp,
            "structure_shift_bar_index": self.structure_shift_bar_index,
            "confirmation_15m": self.confirmation_15m,
            "confirmation_type": self.confirmation_type,
            "confirmation_timestamp": self.confirmation_timestamp,
            "confirmation_candle_close": self.confirmation_candle_close,
            "displacement": self.displacement,
            "labels": list(self.labels),
            "event_key": self.event_key,
            "invalidation_reason": self.invalidation_reason,
            "max_bars_sweep_to_structure": MAX_BARS_SWEEP_TO_STRUCTURE,
            "max_bars_structure_to_15m": MAX_BARS_STRUCTURE_TO_15M,
        }
