"""Formal research-only multi-cap strategy adapter.

PostgreSQL OHLCV
        ↓
multi_cap_strategy_adapter
        ↓
strategy candidate generation (SIGNAL LOGIC)
        ↓
existing combination_backtest evaluator (TRADE EVALUATION LOGIC)
        ↓
fees / slippage / period split / metrics
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.combination_backtest import evaluate_candidate_trades
from app.research.multi_cap_strategies.cap_filter import (
    filter_symbols_for_strategy,
    symbol_eligible_for_strategy,
)
from app.research.multi_cap_strategies.common import candidate_to_research_trade
from app.research.multi_cap_strategies.config import (
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    MultiCapResearchConfig,
)
from app.research.multi_cap_strategies.large_cap_sweep_choch import (
    DEFINITION as LARGE_DEF,
)
from app.research.multi_cap_strategies.large_cap_sweep_choch import (
    LargeCapSweepChochStrategy,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (
    DEFINITION as MID_DEF,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (
    MidCapFvgDiscountStrategy,
)
from app.research.multi_cap_strategies.schemas import (
    CapEligibility,
    StrategyCandidate,
    StrategyDefinition,
)
from app.research.multi_cap_strategies.small_cap_volume_bos import (
    DEFINITION as SMALL_DEF,
)
from app.research.multi_cap_strategies.small_cap_volume_bos import (
    SmallCapVolumeBosStrategy,
)
from app.research.schemas import ResearchTrade

STRATEGIES: dict[str, StrategyDefinition] = {
    LARGE_DEF.strategy_id: LARGE_DEF,
    MID_DEF.strategy_id: MID_DEF,
    SMALL_DEF.strategy_id: SMALL_DEF,
}

_GENERATORS = {
    LARGE_DEF.strategy_id: LargeCapSweepChochStrategy,
    MID_DEF.strategy_id: MidCapFvgDiscountStrategy,
    SMALL_DEF.strategy_id: SmallCapVolumeBosStrategy,
}


def list_strategy_definitions() -> list[dict[str, Any]]:
    return [s.to_dict() for s in STRATEGIES.values()]


def get_strategy(strategy_id: str) -> StrategyDefinition | None:
    return STRATEGIES.get((strategy_id or "").upper().strip())


class MultiCapStrategyAdapter:
    """Common interface for the three formal multi-cap research strategies."""

    def __init__(self, config: MultiCapResearchConfig | None = None) -> None:
        self.config = config or MultiCapResearchConfig()

    @property
    def research_engine_version(self) -> str:
        return RESEARCH_ENGINE_VERSION

    def list_strategies(self) -> list[dict[str, Any]]:
        return list_strategy_definitions()

    def get_definition(self, strategy_id: str) -> StrategyDefinition | None:
        return get_strategy(strategy_id)

    def filter_universe(
        self,
        strategy_id: str,
        symbols: Sequence[str],
        market_caps: Mapping[str, float | None],
    ) -> CapEligibility:
        definition = get_strategy(strategy_id)
        if definition is None:
            return CapEligibility(
                excluded_symbols=[
                    {"symbol": s, "reason": "UNKNOWN_STRATEGY"} for s in symbols
                ]
            )
        return filter_symbols_for_strategy(
            symbols,
            market_caps,
            definition.asset_group,
            config=self.config,
        )

    def generate_candidates(
        self,
        strategy_id: str,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        *,
        market_cap: float | None,
        index_start: int | None = None,
        index_end: int | None = None,
    ) -> tuple[list[StrategyCandidate], dict[str, Any]]:
        definition = get_strategy(strategy_id)
        if definition is None:
            return [], {"status": "ERROR", "reason": f"Unknown strategy {strategy_id}"}

        ok, label, reason = symbol_eligible_for_strategy(
            symbol, market_cap, definition.asset_group, config=self.config
        )
        meta = {
            "status": "OK" if ok else "EXCLUDED",
            "strategy_id": definition.strategy_id,
            "symbol": symbol.upper(),
            "resolved_group": label,
            "required_group": definition.asset_group,
            "reason": reason,
            "signal_logic": definition.strategy_id,
            "trade_evaluation_logic": "combination_backtest.evaluate_candidate_trades",
        }
        if not ok:
            return [], meta

        gen = _GENERATORS[definition.strategy_id]
        cands = gen.generate_candidates(
            symbol,
            timeframe,
            candles,
            config=self.config,
            index_start=index_start,
            index_end=index_end,
        )
        meta["signal_count"] = len(cands)
        return cands, meta

    def evaluate_entry(
        self,
        strategy_id: str,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        as_of_index: int,
        *,
        market_cap: float | None,
    ) -> StrategyCandidate | None:
        definition = get_strategy(strategy_id)
        if definition is None:
            return None
        ok, _, _ = symbol_eligible_for_strategy(
            symbol, market_cap, definition.asset_group, config=self.config
        )
        if not ok:
            return None
        gen = _GENERATORS[definition.strategy_id]
        return gen.evaluate_entry(
            symbol, timeframe, candles, as_of_index, config=self.config
        )

    def candidates_to_trades(
        self,
        candidates: Sequence[StrategyCandidate],
        *,
        asset_group: str,
        period_label: str = "FULL",
    ) -> list[ResearchTrade]:
        trades: list[ResearchTrade] = []
        for c in candidates:
            atr = c.metadata.get("atr")
            rt = candidate_to_research_trade(
                strategy_id=c.strategy_id,
                symbol=c.symbol,
                timeframe=c.timeframe,
                direction=c.direction,
                entry_index=c.entry_index,
                signal_time=c.signal_time,
                entry_price=c.entry_price,
                atr=float(atr) if atr is not None else None,
                structural_invalidation=c.structural_invalidation,
                asset_group=asset_group,
                condition_snapshot=dict(c.metadata),
                config=self.config,
                period_label=period_label,
            )
            if rt is not None:
                trades.append(rt)
        return trades

    def evaluate_trades(
        self,
        candles: Sequence[Mapping[str, Any]],
        candidates: Sequence[StrategyCandidate],
        *,
        asset_group: str,
        period_label: str = "FULL",
    ) -> tuple[list[ResearchTrade], int, int]:
        """SIGNAL → TRADE EVALUATION via existing combination_backtest."""
        signal_count = len(candidates)
        open_trades = self.candidates_to_trades(
            candidates, asset_group=asset_group, period_label=period_label
        )
        closed = evaluate_candidate_trades(
            candles,
            open_trades,
            handling=self.config.ambiguous_handling,
            one_open_at_a_time=self.config.one_open_at_a_time,
        )
        return closed, signal_count, len(closed)

    def disclaimer(self) -> str:
        return DISCLAIMER
