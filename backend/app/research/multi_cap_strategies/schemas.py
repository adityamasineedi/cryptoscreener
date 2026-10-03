"""Schemas for multi-cap research strategies (research only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    name: str
    description: str
    asset_group: str  # LARGE_CAP | MID_CAP | SMALL_CAP
    timeframes: tuple[str, ...]
    required_features: tuple[str, ...]
    direction: str = "LONG"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "asset_group": self.asset_group,
            "timeframes": list(self.timeframes),
            "required_features": list(self.required_features),
            "direction": self.direction,
            "metadata": dict(self.metadata),
            "label": "Historical Result",
            "disclaimer": "Research only — live engine unchanged.",
            "signal_logic": "SIGNAL LOGIC",
            "trade_evaluation_logic": "TRADE EVALUATION LOGIC",
        }


@dataclass
class StrategyEvent:
    strategy_id: str
    symbol: str
    timeframe: str
    event_time: str | None
    event_type: str
    direction: str
    price: float | None
    bar_index: int
    reference_level: float | None = None
    atr: float | None = None
    volume_ratio: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    data_version: str = "multi_cap_strategies_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyCandidate:
    """SIGNAL LOGIC output — entry intent before TRADE EVALUATION."""

    strategy_id: str
    symbol: str
    timeframe: str
    direction: str
    entry_index: int
    signal_time: str | None
    entry_price: float
    structural_invalidation: float | None
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[StrategyEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entry_index": self.entry_index,
            "signal_time": self.signal_time,
            "entry_price": self.entry_price,
            "structural_invalidation": self.structural_invalidation,
            "metadata": dict(self.metadata),
            "events": [e.to_dict() for e in self.events],
        }


@dataclass
class CapEligibility:
    eligible_symbols: list[str] = field(default_factory=list)
    excluded_symbols: list[dict[str, Any]] = field(default_factory=list)
    unknown_symbols: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StrategyGenerator(Protocol):
    definition: StrategyDefinition

    def generate_candidates(
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        *,
        config: Any,
        index_start: int | None = None,
        index_end: int | None = None,
    ) -> list[StrategyCandidate]: ...

    def evaluate_entry(
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        as_of_index: int,
        *,
        config: Any,
    ) -> StrategyCandidate | None: ...


CandidateFn = Callable[..., list[StrategyCandidate]]
