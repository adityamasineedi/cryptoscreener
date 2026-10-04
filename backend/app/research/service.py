"""Research orchestration service — read-only vs live signal state.

BOS Combination Research (this service) is distinct from Candle-1/Candle-2 V2.
Compare/detail endpoints run the BOS combination backtest engine on PostgreSQL
OHLCV. They do NOT read candle12_v2_research_result*.json.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from app.core.logging import get_logger
from app.research.bos_combinations import COMBINATIONS, get_combination, list_combinations
from app.research.combination_backtest import (
    compare_combinations,
    run_combination_backtest,
    run_oos_split_backtest,
    run_walk_forward,
)
from app.research.config import (
    RESEARCH_ENGINE_VERSION,
    SIGNAL_ENGINE_VERSION,
    ResearchConfig,
    count_parameters_tested,
)
from app.research.metrics import summarize_breakdown
from app.research.query_utils import (
    DATASET_ID,
    DATASET_LABEL,
    OTHER_DATASET_ID,
    OTHER_DATASET_LABEL,
    normalize_research_symbol,
    normalize_research_timeframe,
    resolve_date_bounds,
    slice_candles_for_research,
)
from app.research.repository import save_research_run
from app.research.schemas import ResearchTrade
from app.research.trade_fees import (
    DEFAULT_MAKER_FEE,
    DEFAULT_TAKER_FEE,
    enrich_trades,
)
from app.services.engine_store import engine_store
from app.services.ohlcv_store import ohlcv_store
from app.signals.config import SignalConfig

logger = get_logger("research_service")

# Candle cache for a single research batch (symbols × tf × combination)
_CANDLE_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}


def clear_candle_cache() -> None:
    _CANDLE_CACHE.clear()


def _load_candles_memory(symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
    """Fallback: in-memory ohlcv_store (max ~500). Prefer PostgreSQL for research."""
    cache_key = (f"{symbol}:{limit}", timeframe)
    cached = _CANDLE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    candles = ohlcv_store.get_candles_for_engine(
        symbol, timeframe, include_open=False
    )
    candles = list(candles[-limit:]) if candles else []
    _CANDLE_CACHE[cache_key] = candles
    return candles


def _load_candles(symbol: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
    """Sync helper for non-compare paths; memory fallback only."""
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    return _load_candles_memory(sym, tf, limit)


async def _load_research_candles(
    symbol: str,
    timeframe: str,
    *,
    limit: int = 500,
    start_date: str | None = None,
    end_date: str | None = None,
    warmup_bars: int = 100,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Load OHLCV for BOS combination research from PostgreSQL when available.

    Date bounds use UTC calendar days: start <= ts < end_exclusive.
    Warmup bars precede start so structure/gates can form before evaluation.

    When ``RESEARCH_CACHE_ENABLED=true`` and date bounds are set, prefers the
    research Parquet cache (bulk PG on miss). Live trading paths do not use this.
    """
    from app.services.database import db_manager
    from app.research.postgres_ohlcv import (
        load_ohlcv_series_range,
        load_ohlcv_series_tail,
    )

    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    bounds = resolve_date_bounds(start_date, end_date)
    source = "postgresql_ohlcv"
    raw: list[dict[str, Any]] = []

    # Research-only cache path (optional). Never used by live signal engines.
    try:
        from app.research.data_cache.config import load_research_cache_config

        cache_cfg = load_research_cache_config()
        if (
            cache_cfg.enabled
            and (bounds["start"] is not None or bounds["end_exclusive"] is not None)
            and db_manager.enabled
            and db_manager.engine is not None
        ):
            from app.research.data_cache.cache_manager import get_research_cache

            start_s = start_date[:10] if start_date else None
            end_s = end_date[:10] if end_date else None
            ds = await get_research_cache(cache_cfg).load(
                symbol=sym,
                timeframe=tf,
                start=start_s,
                end=end_s,
            )
            raw = ds.as_candles()
            source = f"research_parquet_cache:{ds.source}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("research_cache_load_failed", error=str(exc))
        raw = []

    try:
        if not raw and db_manager.enabled and db_manager.engine is not None:
            if bounds["start"] is not None or bounds["end_exclusive"] is not None:
                # Bounded SQL load (+ warmup) — never pull the entire multi-year series.
                raw = await load_ohlcv_series_range(
                    sym,
                    tf,
                    start=bounds["start"],
                    end_exclusive=bounds["end_exclusive"],
                    warmup_bars=warmup_bars,
                )
            else:
                raw = await load_ohlcv_series_tail(sym, tf, limit=max(limit, 1))
        elif not raw:
            source = "ohlcv_store_memory"
            raw = _load_candles_memory(sym, tf, limit if not bounds["start"] else 50_000)
            if not raw:
                # Last resort: empty
                raw = []
    except Exception as exc:  # noqa: BLE001
        logger.warning("research_postgres_ohlcv_failed", error=str(exc))
        source = "ohlcv_store_memory_fallback"
        raw = _load_candles_memory(sym, tf, limit)

    window, eval_start, slice_meta = slice_candles_for_research(
        raw,
        start=bounds["start"],
        end_exclusive=bounds["end_exclusive"],
        limit=None if (bounds["start"] or bounds["end_exclusive"]) else limit,
        warmup_bars=warmup_bars if (bounds["start"] or bounds["end_exclusive"]) else 0,
    )
    meta = {
        **{k: v for k, v in bounds.items() if k not in ("start", "end_exclusive")},
        **slice_meta,
        "candle_source": source,
        "symbol": sym,
        "timeframe": tf,
        "limit": limit,
        "warmup_bars_requested": warmup_bars
        if (bounds["start"] or bounds["end_exclusive"])
        else 0,
    }
    logger.info(
        "bos_research_candles_loaded",
        symbol=sym,
        timeframe=tf,
        source=source,
        candles=len(window),
        eval_start=eval_start,
        start=meta.get("start_utc"),
        end_exclusive=meta.get("end_exclusive_utc"),
    )
    return window, eval_start, meta


async def _load_htf_candles_for_combo(
    symbol: str,
    *,
    require_htf: bool,
    setup_timeframe: str,
    limit: int,
    start_date: str | None,
    end_date: str | None,
    warmup_bars: int,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Load 1h/4h series for combo HTF gate. Returns (None, None) when not required."""
    if not require_htf:
        return None, None
    tf = normalize_research_timeframe(setup_timeframe)
    # Cover the same calendar window as setup; size HTF tails for swing history.
    lim_1h = max(int(limit), 500) if tf == "1h" else max(int(limit) // 4 + 200, 500)
    lim_4h = max(int(limit), 200) if tf == "4h" else max(int(limit) // 16 + 100, 200)
    c1h: list[dict[str, Any]] | None = None
    c4h: list[dict[str, Any]] | None = None
    if tf != "1h":
        c1h, _, _ = await _load_research_candles(
            symbol,
            "1h",
            limit=lim_1h,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup_bars,
        )
    if tf != "4h":
        c4h, _, _ = await _load_research_candles(
            symbol,
            "4h",
            limit=lim_4h,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=max(warmup_bars // 4, 50),
        )
    return c1h, c4h


def _load_htf_candles_sync(
    symbol: str,
    *,
    require_htf: bool,
    setup_timeframe: str,
    limit: int,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """Memory-store HTF load for sync research helpers."""
    if not require_htf:
        return None, None
    tf = normalize_research_timeframe(setup_timeframe)
    lim_1h = max(int(limit), 500) if tf == "1h" else max(int(limit) // 4 + 200, 500)
    lim_4h = max(int(limit), 200) if tf == "4h" else max(int(limit) // 16 + 100, 200)
    c1h = None if tf == "1h" else _load_candles(symbol, "1h", lim_1h)
    c4h = None if tf == "4h" else _load_candles(symbol, "4h", lim_4h)
    return c1h, c4h


def _market_cap(symbol: str) -> float | None:
    try:
        fv = engine_store.get_fundamental(symbol.upper(), "market_cap")
        if fv and fv.value is not None:
            return float(fv.value)
    except Exception:  # noqa: BLE001
        return None
    return None


def _signal_config() -> SignalConfig:
    try:
        from app.services.setup_signals import get_setup_signal_service

        return get_setup_signal_service().config
    except Exception:  # noqa: BLE001
        return SignalConfig()


class BosResearchService:
    """Read-only research API facade."""

    def list_combinations(self) -> dict[str, Any]:
        return {
            "label": "RESEARCH_COMPARISON",
            "dataset": DATASET_LABEL,
            "dataset_id": DATASET_ID,
            "not_dataset": OTHER_DATASET_ID,
            "combinations": list_combinations(),
            "count": len(COMBINATIONS),
            "regime": "REGIME_NOT_AVAILABLE",
            "disclaimer": (
                "Research definitions only. No combination is ranked or declared best. "
                f"Dataset={DATASET_LABEL}. Not {OTHER_DATASET_LABEL}."
            ),
        }

    def get_combination(self, combination_id: str) -> dict[str, Any] | None:
        combo = get_combination(combination_id)
        if not combo:
            return None
        return {
            "label": "RESEARCH_COMPARISON",
            "combination": combo.to_dict(),
            "regime": "REGIME_NOT_AVAILABLE",
        }

    def run_backtest(
        self,
        combination_id: str,
        *,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 500,
        direction: str | None = None,
        research_config: ResearchConfig | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        clear_candle_cache()
        candles = _load_candles(symbol, timeframe, limit)
        rcfg = research_config or ResearchConfig()
        combo = get_combination(combination_id)
        c1h, c4h = _load_htf_candles_sync(
            symbol,
            require_htf=bool(combo and combo.require_htf_alignment),
            setup_timeframe=timeframe,
            limit=limit,
        )
        run = run_combination_backtest(
            symbol,
            timeframe,
            candles,
            combination_id,
            signal_config=_signal_config(),
            research_config=rcfg,
            market_cap=_market_cap(symbol),
            direction_filter=direction,
            candles_1h=c1h,
            candles_4h=c4h,
        )
        summary = {
            "run_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "configuration_hash": rcfg.configuration_hash(),
            "signal_engine_version": SIGNAL_ENGINE_VERSION,
            "research_engine_version": RESEARCH_ENGINE_VERSION,
            "data_period": {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "limit": limit,
            },
            "configuration": rcfg.configuration_dict(),
            "symbols": [symbol.upper()],
            "timeframes": [timeframe],
            "combinations_tested": 1,
            "parameters_tested": count_parameters_tested(rcfg.parameter_grid),
            "symbols_tested": 1,
            "timeframes_tested": 1,
            "historical_period": run.get("result")
            and {
                "start": (run.get("result") or {}).get("period_start"),
                "end": (run.get("result") or {}).get("period_end"),
            },
            "multiple_testing_risk": False,
            "elapsed_seconds": run.get("elapsed_seconds"),
            "candles_processed": run.get("candles_processed"),
            "setups_processed": run.get("setups_processed"),
            "data_coverage": run.get("data_quality") or {},
            "results": [run.get("result")] if run.get("result") else [],
            "trades": run.get("trades") or [],
        }
        if persist:
            # Fire-and-forget sync wrapper for async repo from sync path
            try:
                import asyncio

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Store memory path via create_task not always available; use memory
                    from app.research import repository as repo

                    repo._MEMORY_RUNS[summary["run_id"]] = summary
                    for row in summary["results"]:
                        repo._MEMORY_RESULTS.append({**row, "run_id": summary["run_id"]})
                else:
                    loop.run_until_complete(save_research_run(summary))
            except Exception:  # noqa: BLE001
                from app.research import repository as repo

                repo._MEMORY_RUNS[summary["run_id"]] = summary
        return {**run, "run_id": summary["run_id"]}

    async def compare(
        self,
        *,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 500,
        direction: str | None = None,
        combination_ids: Sequence[str] | None = None,
        minimum_sample_size: int = 0,
        research_config: ResearchConfig | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        clear_candle_cache()
        t0 = time.perf_counter()
        rcfg = research_config or ResearchConfig()
        warmup = max(int(rcfg.min_bars), 100)
        candles, eval_start, load_meta = await _load_research_candles(
            symbol,
            timeframe,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup,
        )
        # Load HTF once for any combo that requires the hard gate (e.g. COMBO_02).
        ids = list(combination_ids) if combination_ids else list(COMBINATIONS.keys())
        needs_htf = any(
            (get_combination(cid) and get_combination(cid).require_htf_alignment)
            for cid in ids
        )
        c1h, c4h = await _load_htf_candles_for_combo(
            symbol,
            require_htf=needs_htf,
            setup_timeframe=timeframe,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup,
        )
        sym = load_meta["symbol"]
        tf = load_meta["timeframe"]
        # CPU-heavy sync path — must not block the asyncio event loop (health /
        # other API routes hang for minutes if compare runs inline).
        scfg = _signal_config()
        mcap = _market_cap(sym)
        idx0 = eval_start if eval_start > 0 else None

        def _run_cmp() -> dict[str, Any]:
            return compare_combinations(
                sym,
                tf,
                candles,
                combination_ids,
                signal_config=scfg,
                research_config=rcfg,
                market_cap=mcap,
                direction_filter=direction,
                index_start=idx0,
                candles_1h=c1h,
                candles_4h=c4h,
            )

        cmp = await asyncio.to_thread(_run_cmp)
        tested_rows = list(cmp.get("rows") or [])
        combinations_tested = int(cmp.get("combinations_tested") or len(tested_rows))
        # parameters_tested = size of explicit parameter_grid product (1 if none).
        # Distinct from combinations_tested (COMBO_01..N).
        parameter_variants_tested = count_parameters_tested(rcfg.parameter_grid)
        rows = [
            r
            for r in tested_rows
            if int(r.get("sample_size") or 0) >= minimum_sample_size
        ]
        total_closed = sum(int(r.get("sample_size") or 0) for r in rows)
        configs_explored = combinations_tested * parameter_variants_tested
        flag = configs_explored >= rcfg.multiple_testing_flag_threshold
        empty = total_closed == 0
        return {
            **cmp,
            "rows": rows,
            "status": "SUCCESS_EMPTY" if empty else "SUCCESS_WITH_DATA",
            "empty_reason": (
                "NO QUALIFYING DATA FOR SELECTED FILTERS" if empty else None
            ),
            "combinations_tested": combinations_tested,
            "parameters_tested": parameter_variants_tested,
            "parameter_variants_tested": parameter_variants_tested,
            "parameters_tested_meaning": (
                "Product of ResearchConfig.parameter_grid sizes; "
                "1 means no parameter grid was swept (default single configuration)."
            ),
            "symbols_tested": 1,
            "timeframes_tested": 1,
            "closed_trades_sample": total_closed,
            "historical_period": {
                "symbol": sym,
                "timeframe": tf,
                "limit": limit,
                "start_date": load_meta.get("start_date"),
                "end_date_inclusive": load_meta.get("end_date_inclusive"),
                "start_utc": load_meta.get("start_utc"),
                "end_exclusive_utc": load_meta.get("end_exclusive_utc"),
                "period_start": load_meta.get("period_start"),
                "period_end": load_meta.get("period_end"),
                "convention": load_meta.get("convention"),
                "timezone": "UTC",
            },
            "date_filter": {
                "start_date": load_meta.get("start_date"),
                "end_date_inclusive": load_meta.get("end_date_inclusive"),
                "start_utc": load_meta.get("start_utc"),
                "end_exclusive_utc": load_meta.get("end_exclusive_utc"),
                "convention": load_meta.get("convention"),
                "timezone": "UTC",
                "ui_dates_interpreted_as": load_meta.get("ui_dates_interpreted_as"),
            },
            "candle_source": load_meta.get("candle_source"),
            "candles_loaded": load_meta.get("candles_loaded"),
            "warmup_bars_applied": load_meta.get("warmup_bars_applied"),
            "dataset": DATASET_LABEL,
            "dataset_id": DATASET_ID,
            "not_dataset": OTHER_DATASET_ID,
            "not_dataset_label": OTHER_DATASET_LABEL,
            "multiple_testing_risk": flag,
            "multiple_testing_flag": "MULTIPLE_TESTING_RISK" if flag else None,
            "multiple_testing_detail": {
                "combinations_tested": combinations_tested,
                "parameter_variants_tested": parameter_variants_tested,
                "configurations_explored": configs_explored,
                "threshold": rcfg.multiple_testing_flag_threshold,
            },
            "elapsed_seconds": time.perf_counter() - t0,
            "regime": "REGIME_NOT_AVAILABLE",
            "timezone": "UTC",
        }

    def by_timeframe(
        self,
        combination_id: str,
        *,
        symbol: str,
        timeframes: Sequence[str] | None = None,
        limit: int = 500,
        direction: str | None = None,
    ) -> dict[str, Any]:
        tfs = list(timeframes) if timeframes else ["5m", "15m", "1h", "4h"]
        clear_candle_cache()
        out = {}
        combo = get_combination(combination_id)
        for tf in tfs:
            candles = _load_candles(symbol, tf, limit)
            c1h, c4h = _load_htf_candles_sync(
                symbol,
                require_htf=bool(combo and combo.require_htf_alignment),
                setup_timeframe=tf,
                limit=limit,
            )
            run = run_combination_backtest(
                symbol,
                tf,
                candles,
                combination_id,
                signal_config=_signal_config(),
                market_cap=_market_cap(symbol),
                direction_filter=direction,
                candles_1h=c1h,
                candles_4h=c4h,
            )
            res = run.get("result") or {}
            out[tf] = {
                "sample_size": res.get("sample_size", 0),
                "tp1_hit_rate": res.get("tp1_hit_rate"),
                "sl_rate": res.get("sl_rate"),
                "average_R": res.get("average_R"),
                "expectancy_R": res.get("expectancy_R"),
                "profit_factor": res.get("profit_factor"),
                "max_drawdown_R": res.get("max_drawdown_R"),
                "status": run.get("status"),
            }
        return {
            "label": "RESEARCH_COMPARISON",
            "combination_id": combination_id,
            "symbol": symbol.upper(),
            "by_timeframe": out,
            "regime": "REGIME_NOT_AVAILABLE",
        }

    def by_symbol(
        self,
        combination_id: str,
        *,
        symbols: Sequence[str],
        timeframe: str = "15m",
        limit: int = 500,
        direction: str | None = None,
    ) -> dict[str, Any]:
        clear_candle_cache()
        out = {}
        combo = get_combination(combination_id)
        for sym in symbols:
            candles = _load_candles(sym, timeframe, limit)
            c1h, c4h = _load_htf_candles_sync(
                sym,
                require_htf=bool(combo and combo.require_htf_alignment),
                setup_timeframe=timeframe,
                limit=limit,
            )
            run = run_combination_backtest(
                sym,
                timeframe,
                candles,
                combination_id,
                signal_config=_signal_config(),
                market_cap=_market_cap(sym),
                direction_filter=direction,
                candles_1h=c1h,
                candles_4h=c4h,
            )
            res = run.get("result") or {}
            out[sym.upper()] = {
                "sample_size": res.get("sample_size", 0),
                "asset_group": classify_helper(sym),
                "tp1_hit_rate": res.get("tp1_hit_rate"),
                "sl_rate": res.get("sl_rate"),
                "average_R": res.get("average_R"),
                "expectancy_R": res.get("expectancy_R"),
                "profit_factor": res.get("profit_factor"),
                "status": run.get("status"),
            }
        return {
            "label": "RESEARCH_COMPARISON",
            "combination_id": combination_id,
            "timeframe": timeframe,
            "by_symbol": out,
            "regime": "REGIME_NOT_AVAILABLE",
        }

    def walk_forward(
        self,
        combination_id: str,
        *,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 2000,
        direction: str | None = None,
        research_config: ResearchConfig | None = None,
    ) -> dict[str, Any]:
        """Walk-forward research surface — loads HTF when the combo requires it.

        COMBO_02 (require_htf_alignment) receives 1h/4h series the same way as
        ``out_of_sample`` / ``detail`` so HTF-gated LONGs are not silently
        fail-closed by missing candles on this endpoint.
        """
        clear_candle_cache()
        candles = _load_candles(symbol, timeframe, limit)
        combo = get_combination(combination_id)
        c1h, c4h = _load_htf_candles_sync(
            symbol,
            require_htf=bool(combo and combo.require_htf_alignment),
            setup_timeframe=timeframe,
            limit=limit,
        )
        # Scale walk-forward bars to available history if defaults too large
        rcfg = research_config or ResearchConfig()
        n = len(candles)
        if n < rcfg.walk_forward_train_bars + rcfg.walk_forward_test_bars:
            rcfg = rcfg.with_overrides(
                walk_forward_train_bars=max(80, n // 3),
                walk_forward_test_bars=max(40, n // 6),
            )
        wf = run_walk_forward(
            symbol,
            timeframe,
            candles,
            combination_id,
            signal_config=_signal_config(),
            research_config=rcfg,
            market_cap=_market_cap(symbol),
            direction_filter=direction,
            candles_1h=c1h,
            candles_4h=c4h,
        )
        return {
            **wf,
            "combination_id": combination_id,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "require_htf_alignment": bool(combo and combo.require_htf_alignment),
            "regime": "REGIME_NOT_AVAILABLE",
        }

    def out_of_sample(
        self,
        combination_id: str,
        *,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 500,
        direction: str | None = None,
        research_config: ResearchConfig | None = None,
    ) -> dict[str, Any]:
        clear_candle_cache()
        candles = _load_candles(symbol, timeframe, limit)
        combo = get_combination(combination_id)
        c1h, c4h = _load_htf_candles_sync(
            symbol,
            require_htf=bool(combo and combo.require_htf_alignment),
            setup_timeframe=timeframe,
            limit=limit,
        )
        return {
            **run_oos_split_backtest(
                symbol,
                timeframe,
                candles,
                combination_id,
                signal_config=_signal_config(),
                research_config=research_config or ResearchConfig(),
                market_cap=_market_cap(symbol),
                direction_filter=direction,
                candles_1h=c1h,
                candles_4h=c4h,
            ),
            "combination_id": combination_id,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "label": "RESEARCH_COMPARISON",
            "regime": "REGIME_NOT_AVAILABLE",
        }

    async def detail(
        self,
        combination_id: str,
        *,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 500,
        direction: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        rcfg = ResearchConfig()
        warmup = max(int(rcfg.min_bars), 100)
        candles, eval_start, load_meta = await _load_research_candles(
            symbol,
            timeframe,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup,
        )
        combo = get_combination(combination_id)
        c1h, c4h = await _load_htf_candles_for_combo(
            symbol,
            require_htf=bool(combo and combo.require_htf_alignment),
            setup_timeframe=timeframe,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            warmup_bars=warmup,
        )
        sym = load_meta["symbol"]
        tf = load_meta["timeframe"]
        scfg = _signal_config()
        mcap = _market_cap(sym)
        idx0 = eval_start if eval_start > 0 else None
        n = len(candles)
        wf_cfg = rcfg
        if n < rcfg.walk_forward_train_bars + rcfg.walk_forward_test_bars:
            wf_cfg = rcfg.with_overrides(
                walk_forward_train_bars=max(80, n // 3),
                walk_forward_test_bars=max(40, n // 6),
            )

        def _run_detail() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            run_local = run_combination_backtest(
                sym,
                tf,
                candles,
                combination_id,
                signal_config=scfg,
                research_config=rcfg,
                market_cap=mcap,
                direction_filter=direction,
                index_start=idx0,
                candles_1h=c1h,
                candles_4h=c4h,
            )
            oos_local = run_oos_split_backtest(
                sym,
                tf,
                candles,
                combination_id,
                signal_config=scfg,
                research_config=rcfg,
                market_cap=mcap,
                direction_filter=direction,
                candles_1h=c1h,
                candles_4h=c4h,
            )
            wf_local = run_walk_forward(
                sym,
                tf,
                candles,
                combination_id,
                signal_config=scfg,
                research_config=wf_cfg,
                market_cap=mcap,
                direction_filter=direction,
                candles_1h=c1h,
                candles_4h=c4h,
            )
            return run_local, oos_local, wf_local

        run, oos, wf = await asyncio.to_thread(_run_detail)
        trades_raw = run.get("trades") or []
        trade_objs = []
        for t in trades_raw:
            try:
                trade_objs.append(
                    ResearchTrade(
                        **{k: v for k, v in t.items() if k in ResearchTrade.__dataclass_fields__}
                    )
                )
            except TypeError:
                continue
        by_dir = summarize_breakdown(
            trade_objs,
            key_fn=lambda t: t.direction,
            combination_id=combination_id,
            description=(run.get("combination") or {}).get("description") or "",
        )
        combo = get_combination(combination_id)
        sample = int((run.get("result") or {}).get("sample_size") or 0)
        return {
            "label": "RESEARCH_COMPARISON",
            "dataset": DATASET_LABEL,
            "dataset_id": DATASET_ID,
            "not_dataset": OTHER_DATASET_ID,
            "definition": combo.to_dict() if combo else {},
            "gates": (combo.to_dict() if combo else {}).get("gates"),
            "historical_sample": run.get("result"),
            "outcomes": {
                "tp1_hits": (run.get("result") or {}).get("tp1_hits") if sample else None,
                "tp2_hits": (run.get("result") or {}).get("tp2_hits") if sample else None,
                "tp3_hits": (run.get("result") or {}).get("tp3_hits") if sample else None,
                "sl_hits": (run.get("result") or {}).get("sl_hits") if sample else None,
                "ambiguous_count": (run.get("result") or {}).get("ambiguous_count")
                if sample
                else None,
                "sample_size": sample,
            },
            "r_distribution": (run.get("result") or {}).get("r_values") or [],
            "mae_mfe": {
                "average_MAE_R": (run.get("result") or {}).get("average_MAE_R")
                if sample
                else None,
                "average_MFE_R": (run.get("result") or {}).get("average_MFE_R")
                if sample
                else None,
                "sample_size": sample,
            },
            "equity_curve_r": (run.get("result") or {}).get("equity_curve_r") or [],
            "drawdown_curve_r": (run.get("result") or {}).get("drawdown_curve_r") or [],
            "setup_frequency": (run.get("result") or {}).get("setup_frequency") or [],
            "by_direction": by_dir,
            "out_of_sample": oos,
            "walk_forward": wf,
            "sample_size": sample,
            "date_filter": {
                "start_date": load_meta.get("start_date"),
                "end_date_inclusive": load_meta.get("end_date_inclusive"),
                "start_utc": load_meta.get("start_utc"),
                "end_exclusive_utc": load_meta.get("end_exclusive_utc"),
                "convention": load_meta.get("convention"),
                "timezone": "UTC",
            },
            "candle_source": load_meta.get("candle_source"),
            "timezone": "UTC",
            "regime": "REGIME_NOT_AVAILABLE",
            "disclaimer": (
                "Research comparison — historical evidence only. "
                "No winner ranking. No profitability claim. "
                f"Dataset={DATASET_LABEL}."
            ),
        }

    async def strategy_matrix(
        self,
        *,
        combination_id: str = "COMBO_02",
        symbols: Sequence[str],
        timeframes: Sequence[str],
        direction: str = "LONG",
        limit: int = 1200,
        risk_usd: float = 20.0,
        start_date: str | None = None,
        end_date: str | None = None,
        taker_fee: float = DEFAULT_TAKER_FEE,
        maker_fee: float = DEFAULT_MAKER_FEE,
        leverage: float = 2.0,
        include_trades: bool = True,
        should_cancel: Any | None = None,
    ) -> dict[str, Any]:
        """Lean multi-symbol/TF backtest for the UI Backtest tab (no OOS/WF)."""
        from app.research.trade_fees import DEFAULT_LEVERAGE

        lev = max(1.0, float(leverage or DEFAULT_LEVERAGE))
        clear_candle_cache()
        t0 = time.perf_counter()
        combo = get_combination(combination_id)
        if combo is None:
            return {
                "status": "NOT_FOUND",
                "combination_id": combination_id,
                "rows": [],
            }
        rcfg = ResearchConfig()
        warmup = max(int(rcfg.min_bars), 100)
        direction_u = direction.upper().strip() if direction else "LONG"
        if direction_u not in {"LONG", "SHORT"}:
            direction_u = "LONG"
        rows: list[dict[str, Any]] = []
        for tf in timeframes:
            for sym in symbols:
                if should_cancel is not None and should_cancel():
                    return {
                        "status": "CANCELLED",
                        "combination_id": combination_id,
                        "rows": rows,
                        "elapsed_seconds": round(time.perf_counter() - t0, 3),
                    }
                candles, eval_start, load_meta = await _load_research_candles(
                    sym,
                    tf,
                    limit=limit,
                    start_date=start_date,
                    end_date=end_date,
                    warmup_bars=warmup,
                )
                c1h, c4h = await _load_htf_candles_for_combo(
                    sym,
                    require_htf=bool(combo.require_htf_alignment),
                    setup_timeframe=tf,
                    limit=limit,
                    start_date=start_date,
                    end_date=end_date,
                    warmup_bars=warmup,
                )
                # CPU-heavy sync path — run in a thread so the API event loop
                # (health, progress cell requests) is not blocked for minutes.
                scfg = _signal_config()
                mcap = _market_cap(load_meta["symbol"])
                sym_u = load_meta["symbol"]
                tf_u = load_meta["timeframe"]
                idx0 = eval_start if eval_start > 0 else None

                def _run_bt() -> dict[str, Any]:
                    return run_combination_backtest(
                        sym_u,
                        tf_u,
                        candles,
                        combo,
                        signal_config=scfg,
                        research_config=rcfg,
                        market_cap=mcap,
                        direction_filter=direction_u,
                        index_start=idx0,
                        should_cancel=should_cancel,
                        candles_1h=c1h,
                        candles_4h=c4h,
                    )

                out = await asyncio.to_thread(_run_bt)
                if out.get("status") == "CANCELLED":
                    return {
                        "status": "CANCELLED",
                        "combination_id": combination_id,
                        "rows": rows,
                        "elapsed_seconds": round(time.perf_counter() - t0, 3),
                    }
                r = out.get("result") or {}
                n = int(out.get("sample_size") or r.get("sample_size") or 0)
                trades = enrich_trades(
                    out.get("trades") or [],
                    risk_usd=risk_usd,
                    taker_fee=taker_fee,
                    maker_fee=maker_fee,
                    leverage=lev,
                    closed_only=True,
                ) if include_trades else []
                net_pnls = [
                    float(t["net_pnl_usd"])
                    for t in trades
                    if t.get("net_pnl_usd") is not None
                ]
                pnl_gross = (
                    float(r["average_R"]) * n * float(risk_usd)
                    if r.get("average_R") is not None and n
                    else None
                )
                pnl_net = sum(net_pnls) if net_pnls else None
                avg_r_net = (
                    (sum(float(t["r_net"]) for t in trades if t.get("r_net") is not None) / n)
                    if n and trades
                    else None
                )
                fee_total = sum(float(t.get("fee_total_usd") or 0) for t in trades)
                structure = (
                    "HL (bullish HH+HL)"
                    if direction_u == "LONG"
                    else "LH (bearish LH+LL)"
                )
                rows.append(
                    {
                        "combination_id": combination_id,
                        "name": combo.name,
                        "symbol": load_meta["symbol"],
                        "timeframe": load_meta["timeframe"],
                        "direction": direction_u,
                        "structure": structure,
                        "sample_size": n,
                        "average_R": r.get("average_R"),
                        "average_R_net": avg_r_net,
                        "expectancy_R": r.get("expectancy_R"),
                        "tp1_hit_rate": r.get("tp1_hit_rate"),
                        "sl_rate": r.get("sl_rate"),
                        "profit_factor": r.get("profit_factor"),
                        "max_drawdown_R": r.get("max_drawdown_R"),
                        "tp1_hits": r.get("tp1_hits"),
                        "sl_hits": r.get("sl_hits"),
                        "period_start": r.get("period_start"),
                        "period_end": r.get("period_end"),
                        "r_values": r.get("r_values") or [],
                        "equity_curve_r": r.get("equity_curve_r") or [],
                        "pnl_usd": pnl_gross,
                        "pnl_usd_net": pnl_net,
                        "fees_usd": fee_total,
                        "risk_usd": risk_usd,
                        "leverage": lev,
                        "bars_loaded": len(candles),
                        "candle_source": load_meta.get("candle_source"),
                        "trades": trades,
                        "status": out.get("status") or ("OK" if n else "SUCCESS_EMPTY"),
                    }
                )
        return {
            "status": "OK",
            "label": "LONG_STRATEGY_BACKTEST"
            if direction_u == "LONG"
            else "STRATEGY_MATRIX_BACKTEST",
            "dataset": DATASET_LABEL,
            "dataset_id": DATASET_ID,
            "not_dataset": OTHER_DATASET_ID,
            "combination_id": combination_id,
            "combination_name": combo.name,
            "playbook": (
                "HL Long Path A (Trend + BOS + HTF 4h/1h)"
                if direction_u == "LONG" and combo.require_htf_alignment
                else "HL Long Path A local (Trend + BOS, no HTF)"
                if direction_u == "LONG"
                else "LH Short (Trend + BOS + HTF)"
                if combo.require_htf_alignment
                else "LH Short (Trend + BOS, no HTF)"
            ),
            "direction": direction_u,
            "require_htf_alignment": bool(combo.require_htf_alignment),
            "limit": limit,
            "risk_usd": risk_usd,
            "leverage": lev,
            "fee_model": {
                "taker_fee": taker_fee,
                "maker_fee": maker_fee,
                "exit_fee": taker_fee,
                "leverage": lev,
                "note": (
                    "Entry uses maker fee for LIMIT_RETEST, else taker. "
                    "Exit uses taker. Binance USDT-M VIP0 defaults unless overridden. "
                    "Leverage sets margin = notional/leverage only; qty still from risk $/stop."
                ),
            },
            "symbols": [normalize_research_symbol(s) for s in symbols],
            "timeframes": [normalize_research_timeframe(t) for t in timeframes],
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
            "timezone": "UTC",
            "disclaimer": (
                "Historical research only — not a profitability claim. "
                f"Path matches docs/LONG_STRATEGY.md Path A when direction=LONG "
                f"(COMBO_02 requires 4h+1h HTF alignment; COMBO_02_LOCAL is setup-TF only). "
                f"Dataset={DATASET_LABEL}. Fees modeled; not live exchange fills."
            ),
        }


def classify_helper(symbol: str) -> str:
    from app.research.config import classify_asset_group

    return classify_asset_group(symbol, _market_cap(symbol))


_service: BosResearchService | None = None


def get_bos_research_service() -> BosResearchService:
    global _service
    if _service is None:
        _service = BosResearchService()
    return _service
