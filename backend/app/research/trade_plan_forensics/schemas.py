"""Normalized trade + forensic record schemas (research-only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


UNKNOWN = "UNKNOWN"
UNAVAILABLE = "UNAVAILABLE"


@dataclass
class NormalizedTrade:
    """Canonical trade fields. Missing inputs become UNKNOWN/UNAVAILABLE."""

    trade_id: str
    symbol: str
    direction: str
    entry_time: str | None
    exit_time: str | None
    entry_price: float | None
    exit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    exit_reason: str | None
    bars_held: int | None
    duration: str | None
    entry_type: str | None
    fees: float | None
    gross_pnl: float | None
    net_pnl: float | None
    R: float | None
    balance: str | None
    timeframe: str | None
    strategy: str | None
    setup: str | None
    market_signal: str | None
    trade_plan_state: str | None
    source: str
    mae: float | None = None
    mfe: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    entry_index: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sample_size_status(n: int) -> str:
    from app.research.trade_plan_forensics.thresholds import (
        SAMPLE_INSUFFICIENT_MAX,
        SAMPLE_LIMITED_MAX,
    )

    if n < 30 or n <= SAMPLE_INSUFFICIENT_MAX:
        return "INSUFFICIENT_SAMPLE"
    if n <= SAMPLE_LIMITED_MAX:
        return "LIMITED_SAMPLE"
    return "MORE_RELIABLE_DESCRIPTIVE_SAMPLE"
