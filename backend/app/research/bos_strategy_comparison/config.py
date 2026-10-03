"""Research-only configuration for BOS strategy comparison.

Changing these values does NOT alter live SignalConfig / live thresholds.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from app.research.config import (
    AMBIGUOUS_CONSERVATIVE,
    ResearchConfig,
)
from app.research.trade_fees import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE

RESEARCH_ENGINE_VERSION = "bos_strategy_comparison_v1"
SIGNAL_ENGINE_VERSION = "setup_signal_v1"
DATASET_LABEL = "BOS Strategy Comparison Research"
DISCLAIMER = (
    "Research only — live engine unchanged. "
    "Historical Result labels only; not a ranking, winner, or trade recommendation."
)

# Default research window (inclusive start). Per-symbol starts at first available candle.
DEFAULT_PERIOD_START = "2020-10-01"
DEFAULT_SETUP_TIMEFRAME = "15m"
DEFAULT_HTF_TIMEFRAMES = ("4h", "1h")
DEFAULT_ENTRY_TIMEFRAME = "5m"
MIN_SYMBOL_SAMPLE = 30
MIN_BARS_15M = 200  # ~2 days of 15m — below this, exclude symbol


@dataclass
class StrategyResearchConfig:
    """Isolated from live SignalConfig. Fee/slippage match existing research defaults."""

    min_rr: float = 2.0
    min_rvol: float = 1.5
    atr_multiplier: float = 1.5
    ambiguous_handling: str = AMBIGUOUS_CONSERVATIVE
    min_bars: int = 50
    min_coverage_ratio: float = 0.85
    max_gap_bars: int = 3
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    oos_fraction: float = 0.20
    walk_forward_train_bars: int = 90 * 96
    walk_forward_test_bars: int = 30 * 96
    period_start: str = DEFAULT_PERIOD_START
    setup_timeframe: str = DEFAULT_SETUP_TIMEFRAME
    htf_timeframes: tuple[str, ...] = DEFAULT_HTF_TIMEFRAMES
    entry_timeframe: str = DEFAULT_ENTRY_TIMEFRAME
    min_symbol_sample: int = MIN_SYMBOL_SAMPLE
    min_bars_setup: int = MIN_BARS_15M
    # Fees / slippage — existing research blotter defaults (not silently changed)
    taker_fee: float = DEFAULT_TAKER_FEE
    maker_fee: float = DEFAULT_MAKER_FEE
    slippage_rate: float = 0.0002  # matches SignalConfig.slippage_rate
    cost_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)
    bootstrap_samples: int = 500
    bootstrap_ci: float = 0.95
    same_bar_rule: str = (
        "CONSERVATIVE: if SL and TP both touched in the same candle, assume SL first"
    )
    regime_mode: str = "REGIME_UNAVAILABLE"  # do not invent a regime detector
    # Research execution bound for multi-bar pullback/retest lifecycle.
    # NOT a trading rule — production pullback engine has no expiry.
    research_max_lifecycle_bars: int = 40

    def to_research_config(self) -> ResearchConfig:
        """Bridge to shared ResearchConfig used by combination_backtest helpers."""
        return ResearchConfig(
            min_rr=self.min_rr,
            min_rvol=self.min_rvol,
            atr_multiplier=self.atr_multiplier,
            ambiguous_handling=self.ambiguous_handling,
            min_bars=self.min_bars,
            min_coverage_ratio=self.min_coverage_ratio,
            max_gap_bars=self.max_gap_bars,
            train_fraction=self.train_fraction,
            validation_fraction=self.validation_fraction,
            oos_fraction=self.oos_fraction,
            walk_forward_train_bars=self.walk_forward_train_bars,
            walk_forward_test_bars=self.walk_forward_test_bars,
        )

    def configuration_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["htf_timeframes"] = list(self.htf_timeframes)
        d["cost_multipliers"] = list(self.cost_multipliers)
        return d

    def configuration_hash(self) -> str:
        raw = json.dumps(self.configuration_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> StrategyResearchConfig:
        cfg = cls()
        if not raw:
            return cfg
        for key, value in raw.items():
            if not hasattr(cfg, key):
                continue
            if key in ("htf_timeframes", "cost_multipliers") and isinstance(value, list):
                setattr(cfg, key, tuple(value))
            else:
                setattr(cfg, key, value)
        return cfg


@dataclass
class FeeAssumptions:
    taker_fee: float = DEFAULT_TAKER_FEE
    maker_fee: float = DEFAULT_MAKER_FEE
    slippage_rate: float = 0.0002
    leverage: str = "NOT_APPLIED_IN_R_METRICS"
    note: str = (
        "Gross R from price vs stop/TP; net R deducts round-trip fees "
        "(and optional slippage) via existing trade_fees helpers."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
