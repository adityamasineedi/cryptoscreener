"""Central configuration for the structure-based setup signal engine.

Thresholds are screening parameters, not profitability claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class TimeframeSwingConfig:
    swing_left_bars: int = 3
    swing_right_bars: int = 3
    minimum_swing_distance_atr: float = 0.0


@dataclass
class SignalConfig:
    """Editable thresholds for Trend→BOS→Impulse→Pullback→Entry→Risk."""

    # Swing defaults (overridden per timeframe when present)
    swing_left_bars: int = 3
    swing_right_bars: int = 3
    minimum_swing_distance_atr: float = 0.0
    swing_by_timeframe: dict[str, TimeframeSwingConfig] = field(default_factory=dict)

    # Impulse
    atr_multiplier: float = 1.5
    min_body_ratio: float = 0.60
    min_rvol: float = 1.5
    atr_period: int = 14

    # Pullback
    pullback_min_retracement: float = 0.20
    pullback_max_retracement: float = 0.80
    pullback_invalidation_retracement: float = 1.05

    # Retest
    retest_atr_tolerance: float = 0.25
    retest_pct_tolerance: float | None = None

    # Stop / targets / R:R
    sl_buffer_atr: float = 0.2
    min_rr: float = 2.0
    allow_entry_below_min_rr: bool = False
    tp_r_multiples: tuple[float, float, float] = (2.0, 3.0, 4.0)

    # Risk calculator defaults (user-overridable at request time)
    default_account_equity: float = 10_000.0
    default_risk_percent: float = 0.01
    max_leverage: float = 20.0
    default_leverage: float = 5.0
    fee_rate: float = 0.0004
    slippage_rate: float = 0.0002
    contract_quantity_step: float = 0.001
    minimum_quantity: float = 0.001

    # Futures risk warnings
    stop_too_close_pct: float = 0.0015
    liquidation_too_close_pct: float = 0.01
    max_leverage_warning: float = 25.0

    # MTF roles
    mtf_major: str = "4h"
    mtf_primary: str = "1h"
    mtf_setup: str = "15m"
    mtf_entry: str = "5m"
    mtf_refine: str = "1m"
    require_mtf_alignment: bool = True
    oi_mandatory: bool = False
    liquidation_mandatory: bool = False

    # Research gates — default OFF. Opt-in only; do not enable in production
    # until larger-sample SHORT/HTF studies are reviewed.
    research_gate_enabled: bool = False
    research_gate_block_shorts: bool = False
    research_gate_block_htf_conflict: bool = False

    # Stale invalidation
    stale_data_seconds: float = 900.0

    # Market signal classification thresholds (screening defaults — not optima)
    market_signal_strong_min_confirmations: int = 7
    market_signal_buy_min_confirmations: int = 4

    # Scoring weights (explainable; not sole entry decision)
    score_weights: dict[str, float] = field(
        default_factory=lambda: {
            "trend": 20.0,
            "bos": 20.0,
            "impulse": 15.0,
            "pullback": 15.0,
            "volume": 8.0,
            "supply_demand": 4.0,
            "oi": 0.0,
            "liquidation": 0.0,
        }
    )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> SignalConfig:
        cfg = cls()
        if not raw:
            cfg._apply_default_tf_swings()
            return cfg
        block = dict(raw.get("setup_signals") or raw.get("structure_signals") or raw)
        for key in (
            "swing_left_bars",
            "swing_right_bars",
            "minimum_swing_distance_atr",
            "atr_multiplier",
            "min_body_ratio",
            "min_rvol",
            "atr_period",
            "pullback_min_retracement",
            "pullback_max_retracement",
            "pullback_invalidation_retracement",
            "retest_atr_tolerance",
            "sl_buffer_atr",
            "min_rr",
            "allow_entry_below_min_rr",
            "default_account_equity",
            "default_risk_percent",
            "max_leverage",
            "default_leverage",
            "fee_rate",
            "slippage_rate",
            "contract_quantity_step",
            "minimum_quantity",
            "stop_too_close_pct",
            "liquidation_too_close_pct",
            "max_leverage_warning",
            "mtf_major",
            "mtf_primary",
            "mtf_setup",
            "mtf_entry",
            "mtf_refine",
            "require_mtf_alignment",
            "oi_mandatory",
            "liquidation_mandatory",
            "research_gate_enabled",
            "research_gate_block_shorts",
            "research_gate_block_htf_conflict",
            "stale_data_seconds",
            "market_signal_strong_min_confirmations",
            "market_signal_buy_min_confirmations",
        ):
            if key in block and block[key] is not None:
                setattr(cfg, key, type(getattr(cfg, key))(block[key]))
        if block.get("retest_pct_tolerance") is not None:
            cfg.retest_pct_tolerance = float(block["retest_pct_tolerance"])
        if block.get("tp_r_multiples"):
            m = list(block["tp_r_multiples"])
            while len(m) < 3:
                m.append(m[-1] + 1.0)
            cfg.tp_r_multiples = (float(m[0]), float(m[1]), float(m[2]))
        if isinstance(block.get("score_weights"), dict):
            cfg.score_weights.update({str(k): float(v) for k, v in block["score_weights"].items()})
        tf_swings = block.get("swing_by_timeframe") or {}
        if isinstance(tf_swings, dict):
            for tf, vals in tf_swings.items():
                if not isinstance(vals, dict):
                    continue
                cfg.swing_by_timeframe[str(tf).lower()] = TimeframeSwingConfig(
                    swing_left_bars=int(vals.get("swing_left_bars", vals.get("left", cfg.swing_left_bars))),
                    swing_right_bars=int(vals.get("swing_right_bars", vals.get("right", cfg.swing_right_bars))),
                    minimum_swing_distance_atr=float(
                        vals.get("minimum_swing_distance_atr", cfg.minimum_swing_distance_atr)
                    ),
                )
        if not cfg.swing_by_timeframe:
            cfg._apply_default_tf_swings()
        return cfg

    def _apply_default_tf_swings(self) -> None:
        self.swing_by_timeframe = {
            "4h": TimeframeSwingConfig(3, 3),
            "1h": TimeframeSwingConfig(3, 3),
            "15m": TimeframeSwingConfig(2, 2),
            "5m": TimeframeSwingConfig(2, 2),
            "1d": TimeframeSwingConfig(3, 3),
            "1m": TimeframeSwingConfig(2, 2),
        }

    def swing_for(self, timeframe: str) -> TimeframeSwingConfig:
        tf = timeframe.lower()
        if tf in self.swing_by_timeframe:
            return self.swing_by_timeframe[tf]
        return TimeframeSwingConfig(
            self.swing_left_bars,
            self.swing_right_bars,
            self.minimum_swing_distance_atr,
        )
