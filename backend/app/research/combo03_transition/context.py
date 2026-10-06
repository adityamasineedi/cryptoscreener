"""PIT-safe context for COMBO_03_TRANSITION.

Uses the existing research fast regime precompute (same ``classify_market_regime``)
instead of a full market_structure feature table — same classifier, lower memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.htf import (
    build_htf_as_of_index_map_fully_closed,
)
from app.research.combo03_transition.state_machine import SetupState
from app.research.grid_range_research.fast_structure import (
    precompute_regimes_and_bounds_fast,
)


@dataclass
class Combo03Context:
    """1h regimes (existing classifier) + fully-closed 15m as-of map + setup state."""

    symbol: str
    setup_timeframe: str
    setup_candles: list[Mapping[str, Any]]
    regimes_1h: list[str]
    candles_15m: list[Mapping[str, Any]] = field(default_factory=list)
    idx_15m_map: list[int | None] = field(default_factory=list)
    setup: SetupState = field(default_factory=SetupState)
    last_entered_event_key: str | None = None

    @classmethod
    def build(
        cls,
        *,
        symbol: str,
        setup_timeframe: str,
        setup_candles: Sequence[Mapping[str, Any]],
        candles_15m: Sequence[Mapping[str, Any]] | None = None,
    ) -> "Combo03Context":
        series = list(setup_candles)
        regimes, _bounds = precompute_regimes_and_bounds_fast(
            series,
            symbol=symbol,
            index_start=0,
        )
        c15 = list(candles_15m or [])
        idx_map: list[int | None] = [None] * len(series)
        if c15:
            idx_map = build_htf_as_of_index_map_fully_closed(
                series,
                c15,
                setup_timeframe=setup_timeframe or "1h",
                htf_timeframe="15m",
            )
        return cls(
            symbol=symbol.upper(),
            setup_timeframe=setup_timeframe or "1h",
            setup_candles=series,
            regimes_1h=list(regimes),
            candles_15m=c15,
            idx_15m_map=idx_map,
        )

    def regime_at(self, as_of_index: int) -> str:
        if as_of_index < 0 or as_of_index >= len(self.regimes_1h):
            return "UNKNOWN"
        return str(self.regimes_1h[as_of_index] or "UNKNOWN")

    def closed_15m_index(self, as_of_index: int) -> int | None:
        if as_of_index < 0 or as_of_index >= len(self.idx_15m_map):
            return None
        j = self.idx_15m_map[as_of_index]
        return int(j) if j is not None else None

    def snapshot_15m(self, as_of_index: int) -> None:
        """15m structure snapshots unused — confirmation uses analyze_timeframe."""
        return None
