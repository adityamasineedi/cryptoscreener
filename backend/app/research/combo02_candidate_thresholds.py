"""Centralized thresholds for COMBO_02 candidate research (research labels only).

Does not alter v1_production, paper watcher universe, or Telegram eligibility.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CandidateSelectorConfig:
    """Reproducible screener-universe candidate selection rules."""

    quote_asset: str = "USDT"
    market_type: str = "perpetual"
    top_n: int = 30
    min_history_days: float = 540.0  # 18 months preferred
    min_ohlcv_completeness: float = 0.99
    # Match paper-risk liquidity floor (PAPER_MIN_QUOTE_VOLUME_24H default).
    min_avg_24h_quote_volume_usd: float = 5_000_000.0
    exclude_stablecoins: bool = True
    exclude_leveraged_tokens: bool = True
    exclude_inactive_or_delisted: bool = True
    exclude_nonstandard_symbols: bool = True
    exclude_v1_universe: bool = True
    setup_timeframe: str = "1h"
    selector_version: str = "v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EligibilityThresholds:
    """Conservative initial research tier thresholds."""

    promising_min_trades: int = 20
    promising_min_net_avg_r: float = 0.25
    promising_min_profit_factor: float = 1.25
    promising_max_dd_r: float = 6.0
    promising_max_losing_streak: int = 6
    promising_max_fee_pct_of_gross: float = 0.70

    watchlist_min_trades: int = 10

    reject_max_dd_r: float = 10.0
    reject_max_losing_streak: int = 10
    reject_fee_pct_of_gross: float = 0.70

    insufficient_max_trades: int = 9

    # OOS gates for V2_PAPER_CANDIDATE (research label only).
    oos_min_trades: int = 10
    oos_min_net_avg_r: float = 0.0  # strict >
    oos_min_profit_factor: float = 1.0  # strict >
    oos_severe_max_dd_r: float = 10.0
    oos_severe_max_losing_streak: int = 10

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchWindowConfig:
    """Fixed frozen-COMBO_02 research windows (UTC calendar dates)."""

    base_start: str = "2025-01-01"
    base_end: str = "2026-01-31"
    # Dev partition starts at earliest usable candle; floor for reporting:
    oos_dev_end: str = "2025-06-30"
    oos_val_start: str = "2025-07-01"
    oos_val_end: str = "2026-01-31"
    risk_usd: float = 20.0
    principal_usd: float = 1000.0
    combination_id: str = "COMBO_02"
    direction: str = "LONG"
    setup_timeframe: str = "1h"
    require_htf_alignment: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_SELECTOR = CandidateSelectorConfig()
DEFAULT_THRESHOLDS = EligibilityThresholds()
DEFAULT_WINDOW = ResearchWindowConfig()

# Portfolio overlap / concurrency flags (research text only).
PORTFOLIO_HIGH_ENTRY_OVERLAP_PCT = 0.50
PORTFOLIO_HIGH_CORR = 0.75
PORTFOLIO_SUGGESTED_MAX_CONCURRENT = 2

DISCLAIMER = (
    "Research only. No symbol from this report was added to the frozen "
    "COMBO_02 v1 paper/live universe or Telegram eligibility list."
)

STRATEGY_FINGERPRINT_TEXT = (
    "COMBO_02 LONG, 1h setup, 1h+4h HTF_ALIGNED, Path A-equivalent, "
    "same fee/risk/SL/TP/swing/BOS logic as v1."
)
