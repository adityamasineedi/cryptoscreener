"""BOS strategy comparison research — historical analysis only.

Does NOT modify live trading strategy, signal thresholds, or entry logic.
Live engine behavior remains unchanged.
"""

from __future__ import annotations

from app.research.bos_strategy_comparison.strategies import (
    STRATEGIES,
    get_strategy,
    list_strategies,
)

__all__ = [
    "STRATEGIES",
    "get_strategy",
    "list_strategies",
]
