"""Read-only market structure and regime analytics for backtests.

Observability / research only. Does not alter COMBO_02 strategy behavior.
"""

from __future__ import annotations

from app.research.market_structure.attach import (
    attach_market_structure_to_row,
    slim_backtest_row_for_api,
    slim_market_structure_for_api,
)
from app.research.market_structure.config import (
    ANALYTICS_VERSION,
    ENABLE_MARKET_STRUCTURE_ANALYTICS,
    ENABLE_REGIME_FILTERING,
    MarketStructureFeatureConfig,
    assert_regime_filtering_safe,
    default_feature_config,
    resolve_analytics_flags,
)
from app.research.market_structure.engine import compute_market_structure_analytics
from app.research.market_structure.regime import RegimeClassification, classify_market_regime

__all__ = [
    "ANALYTICS_VERSION",
    "ENABLE_MARKET_STRUCTURE_ANALYTICS",
    "ENABLE_REGIME_FILTERING",
    "MarketStructureFeatureConfig",
    "RegimeClassification",
    "assert_regime_filtering_safe",
    "attach_market_structure_to_row",
    "classify_market_regime",
    "compute_market_structure_analytics",
    "default_feature_config",
    "resolve_analytics_flags",
    "slim_backtest_row_for_api",
    "slim_market_structure_for_api",
]
