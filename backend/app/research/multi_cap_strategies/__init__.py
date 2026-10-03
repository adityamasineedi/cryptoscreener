"""Multi-cap formal research strategies (research only).

Strategies:
  - LARGE_CAP_SWEEP_CHOCH
  - MID_CAP_FVG_DISCOUNT
  - SMALL_CAP_VOLUME_BOS

Does NOT modify production signals, Trade Plan, screener, WebSocket,
or existing S1/S2/S3/C1-C4 behavior.
"""

from app.research.multi_cap_strategies.adapter import (
    MultiCapStrategyAdapter,
    get_strategy,
    list_strategy_definitions,
)
from app.research.multi_cap_strategies.config import (
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    MultiCapResearchConfig,
)

__all__ = [
    "DISCLAIMER",
    "RESEARCH_ENGINE_VERSION",
    "MultiCapResearchConfig",
    "MultiCapStrategyAdapter",
    "get_strategy",
    "list_strategy_definitions",
]
