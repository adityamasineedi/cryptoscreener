"""Historical market-cap classification for research (time-aware).

Separates CURRENT_CAP (live CoinGecko / engine_store) from HISTORICAL_CAP
(as-of observations persisted for research). Never fabricates values.
Never uses future observations for earlier timestamps.

Research only — does not alter live signals, Trade Plan, or thresholds.
"""

from app.research.historical_market_cap.classifier import (
    CLASSIFICATION_RULE_VERSION,
    cap_group_at,
    classify_strategy_cap_group,
    filter_candidates_by_historical_cap,
)
from app.research.historical_market_cap.schemas import CapObservation, CapLookupResult

__all__ = [
    "CLASSIFICATION_RULE_VERSION",
    "CapObservation",
    "CapLookupResult",
    "cap_group_at",
    "classify_strategy_cap_group",
    "filter_candidates_by_historical_cap",
]
