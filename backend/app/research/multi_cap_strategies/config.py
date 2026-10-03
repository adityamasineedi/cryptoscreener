"""Research-only configuration for multi-cap strategies.

Isolated from live SignalConfig. Changing these values does NOT alter
production signals, Trade Plan, screener, or WebSocket behavior.
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

RESEARCH_ENGINE_VERSION = "multi_cap_strategies_v1"
SIGNAL_LOGIC_LABEL = "SIGNAL LOGIC"
TRADE_EVALUATION_LABEL = "TRADE EVALUATION LOGIC"

DISCLAIMER = (
    "Research comparison — historical analysis only. "
    "Not a ranking, not a profitability claim, not a trade recommendation. "
    "NO LIVE TRADING LOGIC WAS CHANGED."
)

# ---------------------------------------------------------------------------
# Explicit research rules (documented; not silently invented / not tuned)
# ---------------------------------------------------------------------------

FVG_MITIGATION_RULE = (
    "Intersection (touch / entry overlap): low <= upper AND high >= lower. "
    "Full mitigation (bullish FVG): low <= lower (price traded through the "
    "entire gap to/through the FVG floor; a candle entirely below the zone "
    "also counts). "
    "Full mitigation (bearish FVG): high >= upper (price traded through the "
    "entire gap to/through the FVG ceiling; a candle entirely above also counts). "
    "An FVG starts unmitigated at Candle-3 close. Mitigation is evaluated on "
    "subsequent candles only (formation candle excluded). Once fully mitigated, "
    "the FVG cannot generate later entries."
)

SMALL_CAP_DEEP_RETRACE_THRESHOLD = 0.618
"""Fraction of the breakout impulse retraced that invalidates a small-cap BOS
signal. Documented research default — not optimized against OOS."""

SMALL_CAP_DEEP_RETRACE_WINDOW = 5
"""Bars after BOS+volume to monitor for deep retrace before entry confirmation."""

DEEP_RETRACE_RULE = (
    f"After SMALL_CAP volume-BOS at bar bos_i, monitor bars bos_i+1 .. "
    f"bos_i+{SMALL_CAP_DEEP_RETRACE_WINDOW}. "
    f"impulse_high = high[bos_i]; bos_level = prior 10-bar rolling max high "
    f"(excluding bos_i). "
    f"retrace_depth = (impulse_high - subsequent_low) / (impulse_high - bos_level) "
    f"when impulse_high > bos_level. "
    f"Invalidate (no trade) if retrace_depth >= SMALL_CAP_DEEP_RETRACE_THRESHOLD "
    f"(default {SMALL_CAP_DEEP_RETRACE_THRESHOLD}). "
    f"If the window completes without invalidation, enter at the close of "
    f"bos_i+{SMALL_CAP_DEEP_RETRACE_WINDOW}. Chronological; no lookahead."
)

TRADE_EVALUATION_MECHANICS = (
    "SIGNAL LOGIC emits entry candidates only. "
    "TRADE EVALUATION LOGIC reuses combination_backtest.simulate_research_trade / "
    "evaluate_candidate_trades with ResearchConfig ambiguous_handling "
    "(default CONSERVATIVE same-bar SL-first), ATR-based research stop "
    "(atr_multiplier * ATR below entry for LONG, or structural_invalidation "
    "when farther), and TP1 at min_rr * risk. Fees/slippage via trade_fees + "
    "configured taker/maker/slippage_rate. Period split 60/20/20 chronological."
)


@dataclass
class MultiCapResearchConfig:
    """Controlled multi-cap research parameters — not auto-optimized."""

    # Reuse ResearchConfig period / quality / RR defaults
    min_rr: float = 2.0
    atr_period: int = 14
    atr_multiplier: float = 1.5
    ambiguous_handling: str = AMBIGUOUS_CONSERVATIVE
    min_bars: int = 60
    min_coverage_ratio: float = 0.85
    max_gap_bars: int = 3
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    oos_fraction: float = 0.20

    # Cap thresholds — identical semantics to ResearchConfig / classify_asset_group
    large_cap_min: float = 10_000_000_000.0
    mid_cap_min: float = 1_000_000_000.0
    mid_cap_max: float = 10_000_000_000.0
    small_cap_min: float = 50_000_000.0
    small_cap_max: float = 1_000_000_000.0

    # Fees / slippage — match bos_strategy_comparison defaults
    taker_fee: float = DEFAULT_TAKER_FEE
    maker_fee: float = DEFAULT_MAKER_FEE
    slippage_rate: float = 0.0002
    risk_usd: float = 100.0

    # Strategy-specific (documented defaults; do not tune)
    large_cap_htf_swing_lookback: int = 48
    mid_cap_range_window: int = 24
    small_cap_volume_sma: int = 50
    small_cap_bos_lookback: int = 10
    small_cap_volume_ratio: float = 3.0
    small_cap_deep_retrace_threshold: float = SMALL_CAP_DEEP_RETRACE_THRESHOLD
    small_cap_deep_retrace_window: int = SMALL_CAP_DEEP_RETRACE_WINDOW

    # Swing confirmation for CHOCH (reuse production swing defaults)
    swing_left_bars: int = 3
    swing_right_bars: int = 3
    minimum_swing_distance_atr: float = 0.0

    primary_timeframes: tuple[str, ...] = ("5m", "15m", "1h")
    one_open_at_a_time: bool = True

    fvg_mitigation_rule: str = FVG_MITIGATION_RULE
    deep_retrace_rule: str = DEEP_RETRACE_RULE
    trade_evaluation_mechanics: str = TRADE_EVALUATION_MECHANICS

    extra: dict[str, Any] = field(default_factory=dict)

    def to_research_config(self) -> ResearchConfig:
        return ResearchConfig(
            min_rr=self.min_rr,
            atr_multiplier=self.atr_multiplier,
            ambiguous_handling=self.ambiguous_handling,
            min_bars=self.min_bars,
            min_coverage_ratio=self.min_coverage_ratio,
            max_gap_bars=self.max_gap_bars,
            train_fraction=self.train_fraction,
            validation_fraction=self.validation_fraction,
            oos_fraction=self.oos_fraction,
            large_cap_min=self.large_cap_min,
            mid_cap_min=self.mid_cap_min,
            mid_cap_max=self.mid_cap_max,
            small_cap_min=self.small_cap_min,
            small_cap_max=self.small_cap_max,
        )

    def configuration_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["research_engine_version"] = RESEARCH_ENGINE_VERSION
        d["signal_vs_trade_evaluation"] = {
            "signal_logic": SIGNAL_LOGIC_LABEL,
            "trade_evaluation_logic": TRADE_EVALUATION_LABEL,
            "mechanics": self.trade_evaluation_mechanics,
            "fvg_mitigation_rule": self.fvg_mitigation_rule,
            "deep_retrace_rule": self.deep_retrace_rule,
            "small_cap_deep_retrace_threshold": self.small_cap_deep_retrace_threshold,
        }
        return d

    def configuration_hash(self) -> str:
        raw = json.dumps(self.configuration_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> MultiCapResearchConfig:
        cfg = cls()
        if not raw:
            return cfg
        for key, value in raw.items():
            if hasattr(cfg, key) and key != "extra":
                setattr(cfg, key, value)
        return cfg
