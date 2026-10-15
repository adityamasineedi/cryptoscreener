"""Bounded research-only orchestration over existing backtest primitives."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.combination_backtest import (
    run_combination_backtest,
    run_walk_forward,
)
from app.research.config import RESEARCH_ENGINE_VERSION, SIGNAL_ENGINE_VERSION
from app.research.market_structure_discovery_v1.config import (
    DIRECTIONS,
    MARKETS,
    DiscoveryConfig,
)
from app.research.repository import save_research_run


DISCLAIMER = (
    "Research evidence only. A passing research gate is not an approval, "
    "profitability claim, trade recommendation, or permission for paper, live, "
    "or Telegram use."
)


@dataclass(frozen=True)
class MarketData:
    """Read-only candle inputs for one market."""

    candles: Sequence[Mapping[str, Any]]
    candles_1h: Sequence[Mapping[str, Any]] | None = None
    candles_4h: Sequence[Mapping[str, Any]] | None = None
    candles_15m: Sequence[Mapping[str, Any]] | None = None


def _coerce_market_data(value: MarketData | Mapping[str, Any]) -> MarketData:
    if isinstance(value, MarketData):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("market data must be MarketData or a mapping")
    return MarketData(
        candles=value.get("candles") or (),
        candles_1h=value.get("candles_1h"),
        candles_4h=value.get("candles_4h"),
        candles_15m=value.get("candles_15m"),
    )


def _validate_inputs(
    datasets: Mapping[str, MarketData | Mapping[str, Any]],
    config: DiscoveryConfig,
) -> tuple[tuple[str, ...], dict[str, MarketData]]:
    combo_ids = config.validated_combination_ids()
    normalized = {str(key).upper(): _coerce_market_data(value) for key, value in datasets.items()}
    if set(normalized) != set(MARKETS):
        raise ValueError(f"datasets must contain exactly: {', '.join(MARKETS)}")
    for symbol in MARKETS:
        count = len(normalized[symbol].candles)
        if count > config.max_candles_per_market:
            raise ValueError(
                f"{symbol} has {count} candles; discovery limit is "
                f"{config.max_candles_per_market}"
            )
        train = int(config.research_config.walk_forward_train_bars)
        test = int(config.research_config.walk_forward_test_bars)
        windows = 0
        if train > 0 and test > 0 and count >= train + test:
            windows = 1 + (count - train - test) // test
        if windows > config.max_walk_forward_windows:
            raise ValueError(
                f"{symbol} would create {windows} walk-forward windows; limit is "
                f"{config.max_walk_forward_windows}"
            )
    return combo_ids, normalized


def _research_assessment(
    backtest: Mapping[str, Any],
    walk_forward: Mapping[str, Any],
    config: DiscoveryConfig,
) -> dict[str, Any]:
    """Apply a data-sufficiency gate; never confer deployment approval."""
    sample_size = int(backtest.get("sample_size") or 0)
    windows = list(walk_forward.get("windows") or [])
    window_statuses_ok = all(row.get("status") == "OK" for row in windows)
    passed = (
        backtest.get("status") == "OK"
        and sample_size >= config.minimum_closed_trades
        and len(windows) >= config.minimum_walk_forward_windows
        and window_statuses_ok
    )
    return {
        "research_passed": bool(passed),
        "criterion": "data_sufficiency_and_successful_walk_forward_execution",
        "sample_size": sample_size,
        "walk_forward_windows": len(windows),
        "operator_approved": False,
        "production_approved": False,
    }


def _execute_search(
    datasets: Mapping[str, MarketData],
    config: DiscoveryConfig,
    combo_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, float]:
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    candles_processed = 0
    setups_processed = 0

    for symbol in MARKETS:
        data = datasets[symbol]
        for combination_id in combo_ids:
            for direction in DIRECTIONS:
                backtest = run_combination_backtest(
                    symbol,
                    config.timeframe,
                    data.candles,
                    combination_id,
                    research_config=config.research_config,
                    direction_filter=direction,
                    candles_1h=data.candles_1h,
                    candles_4h=data.candles_4h,
                    candles_15m=data.candles_15m,
                )
                # Deliberately call the existing runner; discovery does not
                # reproduce its rolling-window or signal logic.
                walk_forward = run_walk_forward(
                    symbol,
                    config.timeframe,
                    data.candles,
                    combination_id,
                    research_config=config.research_config,
                    direction_filter=direction,
                    candles_1h=data.candles_1h,
                    candles_4h=data.candles_4h,
                )
                assessment = _research_assessment(backtest, walk_forward, config)
                result_payload = dict(backtest.get("result") or {})
                row = {
                    **result_payload,
                    "combination_id": combination_id,
                    "symbol": symbol,
                    "timeframe": config.timeframe,
                    "direction": direction,
                    "period_label": "FULL",
                    "status": backtest.get("status"),
                    "walk_forward": walk_forward,
                    "assessment": assessment,
                    "research_passed": assessment["research_passed"],
                    "operator_approved": False,
                    "production_approved": False,
                    "disclaimer": DISCLAIMER,
                }
                results.append(row)
                for trade in backtest.get("trades") or []:
                    trades.append(
                        {
                            **dict(trade),
                            "combination_id": combination_id,
                            "symbol": symbol,
                            "timeframe": config.timeframe,
                            "direction": str(trade.get("direction") or direction).upper(),
                            "operator_approved": False,
                            "production_approved": False,
                            "research_only": True,
                        }
                    )
                candles_processed += int(backtest.get("candles_processed") or 0)
                setups_processed += int(backtest.get("setups_processed") or 0)
    return results, trades, candles_processed, setups_processed, time.perf_counter() - started


async def run_discovery(
    datasets: Mapping[str, MarketData | Mapping[str, Any]],
    *,
    config: DiscoveryConfig | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run and optionally persist the fixed-universe discovery matrix.

    Persistence uses the existing research repository only. No paper/live or
    notification service is imported or called.
    """
    cfg = config or DiscoveryConfig()
    combo_ids, normalized = _validate_inputs(datasets, cfg)
    results, trades, candles_processed, setups_processed, elapsed = await asyncio.to_thread(
        _execute_search, normalized, cfg, combo_ids
    )
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid.uuid4())
    summary = {
        "run_id": run_id,
        "created_at": created_at,
        "strategy_id": "MARKET_STRUCTURE_DISCOVERY_V1",
        "research_only": True,
        "operator_approved": False,
        "production_approved": False,
        "paper_eligible": False,
        "live_eligible": False,
        "telegram_eligible": False,
        "configuration_hash": cfg.research_config.configuration_hash(),
        "signal_engine_version": SIGNAL_ENGINE_VERSION,
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "data_period": {},
        "configuration": cfg.to_dict(),
        "symbols": list(MARKETS),
        "timeframes": [cfg.timeframe],
        "combinations_tested": len(combo_ids),
        "parameters_tested": 1,
        "multiple_testing_risk": len(combo_ids) > 1,
        "elapsed_seconds": elapsed,
        "candles_processed": candles_processed,
        "setups_processed": setups_processed,
        "data_coverage": {},
        "results": results,
        "trades": trades,
        "disclaimer": DISCLAIMER,
    }
    if persist:
        persisted_id = await save_research_run(summary)
        if persisted_id != run_id:
            raise RuntimeError("research repository returned an unexpected run id")
    return summary


# Descriptive alias for callers that prefer the full package name.
run_market_structure_discovery = run_discovery
