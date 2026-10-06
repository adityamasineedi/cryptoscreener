"""PIT-safe cached structure/regime context for COMBO_02 v2 backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.htf import (
    build_htf_as_of_index_map_fully_closed,
    decision_timestamp_for_setup_bar,
)
from app.research.market_structure.structure import (
    StructureSnapshot,
    TimeframeFeatureTable,
    build_timeframe_feature_table,
    snapshot_at_index,
)


@dataclass
class Combo02V2Context:
    """Precomputed 1h feature table + optional 15m as-of maps."""

    symbol: str
    setup_timeframe: str
    table_1h: TimeframeFeatureTable
    candles_15m: list[Mapping[str, Any]] = field(default_factory=list)
    idx_15m_map: list[int | None] = field(default_factory=list)
    table_15m: TimeframeFeatureTable | None = None
    # Sticky event control: last accepted (direction, event_key)
    last_event_key: str | None = None

    @classmethod
    def build(
        cls,
        *,
        symbol: str,
        setup_timeframe: str,
        setup_candles: Sequence[Mapping[str, Any]],
        candles_15m: Sequence[Mapping[str, Any]] | None = None,
    ) -> "Combo02V2Context":
        table_1h = build_timeframe_feature_table(
            list(setup_candles),
            setup_timeframe or "1h",
            symbol=symbol,
        )
        c15 = list(candles_15m or [])
        table_15m = None
        idx_map: list[int | None] = [None] * len(setup_candles)
        if c15:
            table_15m = build_timeframe_feature_table(
                c15, "15m", symbol=symbol
            )
            idx_map = build_htf_as_of_index_map_fully_closed(
                setup_candles,
                c15,
                setup_timeframe=setup_timeframe or "1h",
                htf_timeframe="15m",
            )
        return cls(
            symbol=symbol.upper(),
            setup_timeframe=setup_timeframe or "1h",
            table_1h=table_1h,
            candles_15m=c15,
            idx_15m_map=idx_map,
            table_15m=table_15m,
        )

    def snapshot_1h(self, as_of_index: int) -> StructureSnapshot:
        decision = decision_timestamp_for_setup_bar(
            self.table_1h.candles,
            as_of_index,
            setup_timeframe=self.setup_timeframe,
        )
        return snapshot_at_index(
            self.table_1h, as_of_index, decision_time=decision
        )

    def snapshot_15m(self, as_of_index: int) -> StructureSnapshot | None:
        if self.table_15m is None or not self.idx_15m_map:
            return None
        if as_of_index < 0 or as_of_index >= len(self.idx_15m_map):
            return None
        j = self.idx_15m_map[as_of_index]
        if j is None:
            return None
        decision = decision_timestamp_for_setup_bar(
            self.table_1h.candles,
            as_of_index,
            setup_timeframe=self.setup_timeframe,
        )
        return snapshot_at_index(self.table_15m, int(j), decision_time=decision)
