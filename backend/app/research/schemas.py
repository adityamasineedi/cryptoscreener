"""Schemas for BOS combination research results.

Historical analysis only — not trade commands or profitability claims.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ResearchTrade:
    symbol: str
    timeframe: str
    combination_id: str
    entry_index: int
    signal_time: str | None
    direction: str
    entry_price: float
    stop_price: float
    tp1: float | None
    tp2: float | None
    tp3: float | None
    rr: float | None
    exit_index: int | None = None
    exit_time: str | None = None
    exit_price: float | None = None
    outcome: str | None = None  # TP1|TP2|TP3|SL|AMBIGUOUS_INTRABAR|OPEN
    r_multiple: float | None = None
    holding_bars: int | None = None
    time_to_tp1: int | None = None
    time_to_tp2: int | None = None
    time_to_tp3: int | None = None
    time_to_sl: int | None = None
    mae: float | None = None
    mfe: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    ambiguous: bool = False
    period_label: str | None = None  # TRAINING|VALIDATION|OUT_OF_SAMPLE|FULL
    asset_group: str = "UNKNOWN"
    condition_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CombinationResult:
    combination_id: str
    description: str
    period_start: str | None
    period_end: str | None
    symbol: str | None
    timeframe: str | None
    direction: str | None  # ALL|LONG|SHORT
    sample_size: int
    long_setups: int = 0
    short_setups: int = 0
    tp1_hits: int = 0
    tp2_hits: int = 0
    tp3_hits: int = 0
    sl_hits: int = 0
    ambiguous_trades: int = 0
    ambiguous_count: int = 0
    tp1_hit_rate: float | None = None
    tp2_hit_rate: float | None = None
    tp3_hit_rate: float | None = None
    sl_rate: float | None = None
    average_R: float | None = None
    median_R: float | None = None
    expectancy_R: float | None = None
    profit_factor: float | None = None
    gross_profit_R: float = 0.0
    gross_loss_R: float = 0.0
    max_drawdown_R: float | None = None
    average_MAE_R: float | None = None
    average_MFE_R: float | None = None
    average_holding_time: float | None = None
    median_holding_time: float | None = None
    signals_per_day: float | None = None
    signals_per_week: float | None = None
    data_coverage: float | None = None
    data_quality: str = "UNKNOWN"
    regime: str = "REGIME_NOT_AVAILABLE"
    period_label: str = "FULL"
    r_values: list[float] = field(default_factory=list)
    equity_curve_r: list[float] = field(default_factory=list)
    drawdown_curve_r: list[float] = field(default_factory=list)
    setup_frequency: list[dict[str, Any]] = field(default_factory=list)
    by_timeframe: dict[str, Any] = field(default_factory=dict)
    by_direction: dict[str, Any] = field(default_factory=dict)
    by_asset_group: dict[str, Any] = field(default_factory=dict)
    by_regime: dict[str, Any] = field(default_factory=dict)
    condition_definition: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = (
        "Research comparison — historical analysis only. "
        "Not a ranking, not a profitability claim, not a trade recommendation."
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["label"] = "RESEARCH_COMPARISON"
        return d


@dataclass
class ResearchRunSummary:
    run_id: str
    created_at: str
    configuration_hash: str
    signal_engine_version: str
    research_engine_version: str
    data_period: dict[str, Any]
    symbols: list[str]
    timeframes: list[str]
    combinations_tested: int
    parameters_tested: int
    symbols_tested: int
    timeframes_tested: int
    historical_period: dict[str, Any]
    multiple_testing_risk: bool
    elapsed_seconds: float
    candles_processed: int
    setups_processed: int
    data_coverage: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    out_of_sample: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = (
        "Research comparison using real OHLCV. "
        "Do not treat any combination as best. Out-of-sample is evaluation-only."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
