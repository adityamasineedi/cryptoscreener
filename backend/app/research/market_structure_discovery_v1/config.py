"""Fixed, bounded configuration for market-structure discovery v1.

This module is research orchestration only.  It does not expose execution or
approval switches and it does not define strategy rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.research.bos_combinations import COMBINATIONS
from app.research.config import ResearchConfig


MARKETS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DIRECTIONS: tuple[str, ...] = ("LONG", "SHORT")

# Hard limits make the search space explicit and prevent this research wrapper
# from becoming an unbounded optimizer.
MAX_COMBINATIONS = 24
MAX_CANDLES_PER_MARKET = 50_000
MAX_WALK_FORWARD_WINDOWS = 24
MAX_BACKTEST_CALLS = 144  # 3 markets * 2 directions * 24 combinations


@dataclass(frozen=True)
class DiscoveryConfig:
    """One bounded discovery request over the fixed market/direction universe."""

    combination_ids: tuple[str, ...] = ("COMBO_01",)
    timeframe: str = "1h"
    research_config: ResearchConfig = field(default_factory=ResearchConfig)
    minimum_closed_trades: int = 1
    minimum_walk_forward_windows: int = 1
    max_candles_per_market: int = MAX_CANDLES_PER_MARKET
    max_walk_forward_windows: int = MAX_WALK_FORWARD_WINDOWS

    def validated_combination_ids(self) -> tuple[str, ...]:
        ids = tuple(str(value).strip().upper() for value in self.combination_ids)
        if not ids:
            raise ValueError("at least one existing COMBO id is required")
        if len(ids) != len(set(ids)):
            raise ValueError("combination_ids must not contain duplicates")
        if len(ids) > MAX_COMBINATIONS:
            raise ValueError(f"combination search exceeds limit {MAX_COMBINATIONS}")
        unknown = [value for value in ids if value not in COMBINATIONS]
        if unknown:
            raise ValueError(f"unknown COMBO ids: {', '.join(unknown)}")
        calls = len(MARKETS) * len(DIRECTIONS) * len(ids)
        if calls > MAX_BACKTEST_CALLS:
            raise ValueError(f"backtest search exceeds limit {MAX_BACKTEST_CALLS}")
        if self.research_config.parameter_grid:
            raise ValueError("parameter grids are not supported by discovery v1")
        if self.minimum_closed_trades < 0:
            raise ValueError("minimum_closed_trades must be non-negative")
        if self.minimum_walk_forward_windows < 0:
            raise ValueError("minimum_walk_forward_windows must be non-negative")
        if not 1 <= self.max_candles_per_market <= MAX_CANDLES_PER_MARKET:
            raise ValueError(
                f"max_candles_per_market must be between 1 and {MAX_CANDLES_PER_MARKET}"
            )
        if not 1 <= self.max_walk_forward_windows <= MAX_WALK_FORWARD_WINDOWS:
            raise ValueError(
                f"max_walk_forward_windows must be between 1 and {MAX_WALK_FORWARD_WINDOWS}"
            )
        return ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "market_structure_discovery_v1",
            "markets": list(MARKETS),
            "directions": list(DIRECTIONS),
            "combination_ids": list(self.validated_combination_ids()),
            "timeframe": self.timeframe,
            "minimum_closed_trades": self.minimum_closed_trades,
            "minimum_walk_forward_windows": self.minimum_walk_forward_windows,
            "max_candles_per_market": self.max_candles_per_market,
            "max_walk_forward_windows": self.max_walk_forward_windows,
            "research_config": self.research_config.configuration_dict(),
        }
