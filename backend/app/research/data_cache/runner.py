"""Run existing combination backtest against a prepared ResearchDataset.

Does not change strategy parameters — calls ``run_combination_backtest`` as-is.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.config import ResearchConfig
from app.research.data_cache.checkpoint import CheckpointStore, RunCheckpoint
from app.research.data_cache.config import ResearchCacheConfig, load_research_cache_config
from app.research.data_cache.dataset import PreparedResearchBundle, ResearchDataset
from app.research.data_cache.metrics import ResearchCacheMetrics
from app.signals.config import SignalConfig


STRATEGY_VERSION = "combo_backtest_runner_v1"


def run_strategy_on_dataset(
    dataset: ResearchDataset,
    *,
    combination_id: str = "COMBO_02",
    direction: str = "LONG",
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
    metrics: ResearchCacheMetrics | None = None,
    preloaded_candles: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute frozen combination engine on cached candles (no PG re-read)."""
    combo = get_combination(combination_id)
    if combo is None:
        return {"status": "NOT_FOUND", "combination_id": combination_id}
    candles = list(preloaded_candles) if preloaded_candles is not None else dataset.as_candles()
    t0 = time.monotonic()
    out = run_combination_backtest(
        dataset.symbol,
        dataset.timeframe,
        candles,
        combo,
        signal_config=signal_config or SignalConfig(),
        research_config=research_config or ResearchConfig(),
        direction_filter=direction.upper(),
        candles_1h=candles_1h,
        candles_4h=candles_4h,
    )
    elapsed = time.monotonic() - t0
    if metrics is not None:
        metrics.strategy_seconds += elapsed
    out = dict(out)
    out["research_cache"] = {
        "symbol": dataset.symbol,
        "timeframe": dataset.timeframe,
        "source": dataset.source,
        "cache_key": dataset.cache_key,
        "row_count": dataset.row_count,
        "strategy_seconds": round(elapsed, 3),
        "strategy_version": STRATEGY_VERSION,
    }
    return out


def run_strategies_on_bundle(
    bundle: PreparedResearchBundle,
    *,
    combination_ids: Sequence[str] = ("COMBO_02",),
    direction: str = "LONG",
    config: ResearchCacheConfig | None = None,
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    """Reuse the same prepared OHLCV for one or more strategies (no PG reload)."""
    cfg = config or load_research_cache_config()
    metrics = ResearchCacheMetrics()
    store = CheckpointStore(cfg)
    strategy_version = STRATEGY_VERSION + "+" + "+".join(combination_ids)
    if resume_run_id:
        cp = store.load(resume_run_id)
        if cp is None:
            raise FileNotFoundError(f"checkpoint_not_found:{resume_run_id}")
    else:
        cp = store.create(
            symbols=sorted({s for s, _ in bundle.keys()}),
            timeframes=sorted({t for _, t in bundle.keys()}),
            start_time=bundle.start_time,
            end_time=bundle.end_time,
            dataset_version=bundle.dataset_version,
            strategy_version=strategy_version,
            run_id=bundle.run_id,
        )

    results: list[dict[str, Any]] = []
    # Convert each series to candle dicts once — reused across strategies.
    candle_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (sym, tf), ds in bundle.datasets.items():
        candle_cache[(sym, tf)] = ds.as_candles()

    for (sym, tf), ds in bundle.datasets.items():
        # HTF series from the same prepared bundle when present
        c1h_list = candle_cache.get((sym, "1h"))
        c4h_list = candle_cache.get((sym, "4h"))
        setup_candles = candle_cache[(sym, tf)]
        for combo_id in combination_ids:
            cell_key = RunCheckpoint.cell_key(sym, tf) + f"|{combo_id}"
            # Resume: skip completed combo cells recorded in result_summary
            existing = cp.cells.get(RunCheckpoint.cell_key(sym, tf))
            if (
                existing
                and existing.status == "COMPLETED"
                and (existing.result_summary or {}).get("combination_id") == combo_id
            ):
                results.append(existing.result_summary)
                continue

            store.mark(cp, sym, tf, "BACKTESTING")
            try:
                out = run_strategy_on_dataset(
                    ds,
                    combination_id=combo_id,
                    direction=direction,
                    candles_1h=c1h_list,
                    candles_4h=c4h_list,
                    metrics=metrics,
                    preloaded_candles=setup_candles,
                )
                summary = {
                    "combination_id": combo_id,
                    "symbol": sym,
                    "timeframe": tf,
                    "status": out.get("status"),
                    "sample_size": out.get("sample_size"),
                    "average_R": (out.get("result") or {}).get("average_R"),
                    "cell_key": cell_key,
                }
                store.mark(
                    cp,
                    sym,
                    tf,
                    "COMPLETED",
                    result_summary=summary,
                )
                results.append({**summary, "raw": out})
            except Exception as exc:  # noqa: BLE001
                store.mark(cp, sym, tf, "FAILED", error=str(exc))
                results.append(
                    {
                        "combination_id": combo_id,
                        "symbol": sym,
                        "timeframe": tf,
                        "status": "FAILED",
                        "error": str(exc),
                    }
                )

    metrics.mark_done()
    cp.metrics = metrics.to_dict()
    store.save(cp)
    return {
        "status": "OK",
        "run_id": cp.run_id,
        "results": results,
        "metrics": metrics.to_dict(),
        "prepare_metrics": bundle.metrics,
        "note": (
            "Strategies reused prepared Parquet-backed datasets. "
            "No live trading logic changed."
        ),
    }
