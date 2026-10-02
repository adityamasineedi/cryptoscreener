"""Schemas and enums for the setup signal engine.

States are structural labels — never profitability claims.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SignalStatus(str, Enum):
    ENTRY_CANDIDATE = "ENTRY_CANDIDATE"
    LONG_ENTRY_CANDIDATE = "LONG_ENTRY_CANDIDATE"
    SHORT_ENTRY_CANDIDATE = "SHORT_ENTRY_CANDIDATE"
    NO_SETUP = "NO_SETUP"
    WAITING = "WAITING"
    INVALIDATED = "INVALIDATED"
    CONFLICT = "CONFLICT"


class TrendState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    WAITING = "WAITING"


class EventState(str, Enum):
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    WAITING = "WAITING"
    NONE = "NONE"


class PullbackState(str, Enum):
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"
    NONE = "NONE"


class ImpulseQuality(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    INVALID = "INVALID"


class MTFAlignment(str, Enum):
    STRONG_LONG = "STRONG_LONG"
    STRONG_SHORT = "STRONG_SHORT"
    MIXED = "MIXED"
    CONFLICT = "CONFLICT"
    INSUFFICIENT = "INSUFFICIENT"
    WAITING = "WAITING"


class ConditionVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "N/A"
    WAITING = "WAITING"


class EntryType(str, Enum):
    MARKET = "MARKET"
    LIMIT_RETEST = "LIMIT_RETEST"
    BREAKOUT_CONFIRMATION = "BREAKOUT_CONFIRMATION"
    WAITING = "WAITING"


@dataclass
class SwingRecord:
    symbol: str
    timeframe: str
    swing_type: str  # HIGH | LOW
    price: float
    timestamp: datetime | None
    bar_index: int
    strength: float
    confirmed_at: datetime | None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("timestamp", "confirmed_at"):
            if d[k] is not None and hasattr(d[k], "isoformat"):
                d[k] = d[k].isoformat()
        return d


@dataclass
class ConditionCheck:
    id: str
    label: str
    verdict: str  # PASS | FAIL | N/A | WAITING
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TargetLevel:
    name: str
    target_price: float
    target_type: str
    distance: float
    r_multiple: float
    structural_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SetupAnalysis:
    """Full explainable setup snapshot for one symbol (optionally one TF focus)."""

    symbol: str
    status: str
    direction: str | None = None
    timeframe: str = "15m"
    trend: dict[str, Any] = field(default_factory=dict)
    swings: list[dict[str, Any]] = field(default_factory=list)
    bos: dict[str, Any] | None = None
    choch: dict[str, Any] | None = None
    impulse: dict[str, Any] | None = None
    pullback: dict[str, Any] | None = None
    retest: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None
    targets: list[dict[str, Any]] = field(default_factory=list)
    risk_reward: dict[str, Any] = field(default_factory=dict)
    risk_management: dict[str, Any] = field(default_factory=dict)
    mtf: dict[str, Any] = field(default_factory=dict)
    score: dict[str, Any] = field(default_factory=dict)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    invalidation_reason: str | None = None
    data_dependencies: dict[str, str] = field(default_factory=dict)
    annotations: list[dict[str, Any]] = field(default_factory=list)
    # Freshness / provenance (not trading logic)
    source_candle_timestamps: dict[str, str | None] = field(default_factory=dict)
    calculated_at: str | None = None
    signal_status: str = "WAITING"  # LIVE | STALE | WAITING
    # Separate from setup status (ENTRY_CANDIDATE etc.)
    market_signal: str | None = None
    market_signal_reason: str | None = None
    confirmation_strength: int | None = None
    confirmation_denominator: int | None = None
    market_signal_payload: dict[str, Any] = field(default_factory=dict)
    classification_version: str | None = None
    disclaimer: str = (
        "Structural setup identification only. Not a profitability prediction. "
        "Parameters are configuration defaults, not claimed optima."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
