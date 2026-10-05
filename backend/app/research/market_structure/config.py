"""Market-structure analytics configuration (research / observability only).

These thresholds never enter strategy fingerprints or trade gates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

ANALYTICS_VERSION = "market_structure_v1_1"

# Module-level defaults matching the user brief. Settings may override via env.
ENABLE_MARKET_STRUCTURE_ANALYTICS = True
ENABLE_REGIME_FILTERING = False


@dataclass(frozen=True)
class MarketStructureFeatureConfig:
    """Transparent, serializable feature thresholds for regime diagnostics."""

    analytics_version: str = ANALYTICS_VERSION
    adx_period: int = 14
    adx_trending_threshold: float = 25.0
    adx_ranging_threshold: float = 20.0
    atr_period: int = 14
    atr_percentile_lookback: int = 100
    atr_high_percentile: float = 80.0
    atr_low_percentile: float = 20.0
    efficiency_lookback: int = 20
    efficiency_trending_threshold: float = 0.50
    efficiency_choppy_threshold: float = 0.30
    choppiness_lookback: int = 14
    choppiness_high_threshold: float = 61.8
    choppiness_low_threshold: float = 38.2
    ema_fast: int = 20
    ema_mid: int = 50
    ema_slow: int = 200
    slope_lookback: int = 20
    momentum_lookback: int = 14
    rsi_period: int = 14
    range_lookback: int = 40
    direction_change_lookback: int = 20
    trend_vote_required: int = 2
    swing_left: int = 2
    swing_right: int = 2
    bos_lookback_bars: int = 30
    pullback_atr_shallow: float = 1.0
    pullback_atr_deep: float = 2.5
    min_bars_basic: int = 30
    min_bars_adx: int = 30
    min_bars_ema_slow: int = 200

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def default_feature_config() -> MarketStructureFeatureConfig:
    return MarketStructureFeatureConfig()


def assert_regime_filtering_safe(
    *,
    enable_regime_filtering: bool,
    research_only: bool,
) -> None:
    """Hard safety guard — regime filtering cannot run in production mode."""
    if enable_regime_filtering and not research_only:
        raise ValueError(
            "Regime filtering is research-only and cannot run in production mode"
        )


def resolve_analytics_flags(
    settings: Any | None = None,
) -> tuple[bool, bool]:
    """Return (enable_analytics, enable_regime_filtering)."""
    analytics = ENABLE_MARKET_STRUCTURE_ANALYTICS
    filtering = ENABLE_REGIME_FILTERING
    if settings is not None:
        analytics = bool(
            getattr(settings, "enable_market_structure_analytics", analytics)
        )
        filtering = bool(getattr(settings, "enable_regime_filtering", filtering))
    return analytics, filtering
