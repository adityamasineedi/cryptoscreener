"""Schemas for BOS strategy comparison research results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StrategyTrade:
    strategy_id: str
    symbol: str
    direction: str
    timeframe: str
    entry_index: int
    entry_time: str | None
    entry_price: float
    sl: float
    tp1: float | None
    tp2: float | None
    tp3: float | None
    exit_time: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # TP1|TP2|TP3|SL|AMBIGUOUS_INTRABAR|OPEN
    exit_index: int | None = None
    gross_R: float | None = None
    net_R: float | None = None
    MFE: float | None = None
    MAE: float | None = None
    MFE_R: float | None = None
    MAE_R: float | None = None
    holding_period: int | None = None
    trend_4h: str | None = None
    trend_1h: str | None = None
    trend_15m: str | None = None
    trend_5m: str | None = None
    bos_direction: str | None = None
    bos_timestamp: str | None = None
    impulse_state: str | None = None
    pullback_state: str | None = None
    retest_state: str | None = None
    sd_state: str | None = None
    htf_alignment: str | None = None  # HTF_ALIGNED|HTF_CONFLICT|HTF_NEUTRAL_UNAVAILABLE
    regime: str = "REGIME_UNAVAILABLE"
    stop_reason: str | None = None
    risk_per_unit: float | None = None
    structural_invalidation: str | None = None
    entry_type: str | None = None
    period_label: str | None = None
    ambiguous: bool = False
    condition_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DirectionMetrics:
    direction: str
    sample_size: int = 0
    win_rate: float | None = None
    loss_rate: float | None = None
    breakeven_rate: float | None = None
    average_R: float | None = None
    median_R: float | None = None
    expectancy_R: float | None = None
    gross_expectancy: float | None = None
    net_expectancy: float | None = None
    profit_factor: float | None = None
    total_R: float | None = None
    max_drawdown_R: float | None = None
    average_win: float | None = None
    average_loss: float | None = None
    largest_win: float | None = None
    largest_loss: float | None = None
    average_MFE_R: float | None = None
    average_MAE_R: float | None = None
    average_holding_period: float | None = None
    tp1_hit_rate: float | None = None
    tp2_hit_rate: float | None = None
    tp3_hit_rate: float | None = None
    sl_rate: float | None = None
    expectancy_ci_low: float | None = None
    expectancy_ci_high: float | None = None
    sample_status: str = "OK"  # OK | INSUFFICIENT_SAMPLE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyResult:
    strategy_id: str
    strategy_name: str
    direction: str  # ALL|LONG|SHORT
    sample_size: int
    win_rate: float | None = None
    expectancy_R: float | None = None
    profit_factor: float | None = None
    max_drawdown_R: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    long: dict[str, Any] = field(default_factory=dict)
    short: dict[str, Any] = field(default_factory=dict)
    by_year: dict[str, Any] = field(default_factory=dict)
    by_symbol: dict[str, Any] = field(default_factory=dict)
    by_timeframe: dict[str, Any] = field(default_factory=dict)
    by_htf_alignment: dict[str, Any] = field(default_factory=dict)
    train: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    oos: dict[str, Any] = field(default_factory=dict)
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    cost_sensitivity: dict[str, Any] = field(default_factory=dict)
    condition_definition: dict[str, Any] = field(default_factory=dict)
    label: str = "Historical Result"
    disclaimer: str = "Research only — live engine unchanged."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
