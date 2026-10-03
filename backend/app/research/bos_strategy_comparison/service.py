"""API facade for BOS strategy comparison research.

Read-only vs live signal state. Never modifies production strategy files.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Sequence

from app.core.logging import get_logger
from app.research.bos_strategy_comparison.config import (
    DATASET_LABEL,
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    SIGNAL_ENGINE_VERSION,
    StrategyResearchConfig,
)
from app.research.bos_strategy_comparison.data_quality import (
    audit_ohlcv_coverage,
    build_universe_eligibility,
)
from app.research.bos_strategy_comparison.repository import (
    load_latest_strategy_run,
    save_strategy_run,
)
from app.research.bos_strategy_comparison.runner import (
    aggregate_strategy_results,
    condition_contribution,
    run_multi_strategy_backtest,
    run_walk_forward_strategy,
)
from app.research.bos_strategy_comparison.strategies import (
    STRATEGIES,
    get_strategy,
    list_strategies,
)
from app.research.postgres_ohlcv import (
    load_ohlcv_series_range,
    load_ohlcv_series_tail,
)
from app.research.query_utils import (
    normalize_research_symbol,
    normalize_research_timeframe,
    resolve_date_bounds,
    slice_candles_for_research,
)
from app.signals.config import SignalConfig

logger = get_logger("bos_strategy_comparison_service")


class BosStrategyComparisonService:
    def list_strategies(self) -> dict[str, Any]:
        return {
            "status": "OK",
            "dataset": DATASET_LABEL,
            "strategies": list_strategies(),
            "disclaimer": DISCLAIMER,
            "label": "Historical Result",
            "note": "Research only — live engine unchanged. No winner ranking.",
        }

    async def data_coverage(self) -> dict[str, Any]:
        cfg = StrategyResearchConfig()
        universe = await build_universe_eligibility(config=cfg)
        audit = await audit_ohlcv_coverage(
            timeframes=("4h", "1h", "15m", "5m")
        )
        return {
            "status": "OK",
            "dataset": DATASET_LABEL,
            "universe": universe,
            "audit": audit,
            "disclaimer": DISCLAIMER,
        }

    async def _load_mtf(
        self,
        symbol: str,
        *,
        setup_tf: str,
        start_date: str | None,
        end_date: str | None,
        limit: int | None,
        warmup_bars: int = 100,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, dict[str, Any]]:
        sym = normalize_research_symbol(symbol)
        setup_tf = normalize_research_timeframe(setup_tf)

        bounds = resolve_date_bounds(start_date, end_date)
        meta: dict[str, Any] = {"candle_source": "postgresql", "date_filter": bounds}

        async def _load(tf: str) -> list[dict[str, Any]]:
            if bounds.get("start") or bounds.get("end_exclusive"):
                return await load_ohlcv_series_range(
                    sym,
                    tf,
                    start=bounds.get("start"),
                    end_exclusive=bounds.get("end_exclusive"),
                    warmup_bars=warmup_bars if tf == setup_tf else max(50, warmup_bars // 4),
                )
            if limit and limit > 0:
                # Scale HTF tails roughly to cover the same calendar window
                tf_limit = limit
                if tf == "1h":
                    tf_limit = max(100, limit // 4)
                elif tf == "4h":
                    tf_limit = max(50, limit // 16)
                elif tf == "5m":
                    tf_limit = max(100, limit * 3)
                return await load_ohlcv_series_tail(sym, tf, limit=tf_limit)
            return await load_ohlcv_series_tail(sym, tf, limit=2000)

        setup = await _load(setup_tf)
        h4 = await _load("4h")
        h1 = await _load("1h")
        m5 = await _load("5m")

        eval_start = 0
        if bounds.get("start") or bounds.get("end_exclusive"):
            # Range loader already applied warmup; eval starts after warmup bars
            setup, eval_start, slice_meta = slice_candles_for_research(
                setup,
                start=bounds.get("start"),
                end_exclusive=bounds.get("end_exclusive"),
                warmup_bars=0,
            )
            # Warmup already in series from SQL; find first eval index
            if bounds.get("start") is not None:
                from app.research.query_utils import candle_timestamp

                start_bound = bounds["start"]
                eval_start = next(
                    (
                        i
                        for i, c in enumerate(setup)
                        if (ts := candle_timestamp(c)) is not None and ts >= start_bound
                    ),
                    0,
                )
            meta["eval_start"] = eval_start
            meta["slice"] = slice_meta
        elif limit and limit > 0:
            eval_start = 0
            meta["eval_start"] = 0
            meta["slice"] = {"candles_loaded": len(setup), "eval_bars": len(setup)}
        return setup, h4, h1, m5, eval_start, meta

    async def run_symbol(
        self,
        symbol: str,
        *,
        strategy_ids: Sequence[str] | None = None,
        timeframe: str = "15m",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
        direction: str | None = None,
        research_config: StrategyResearchConfig | None = None,
    ) -> dict[str, Any]:
        rcfg = research_config or StrategyResearchConfig()
        scfg = SignalConfig()
        ids = list(strategy_ids) if strategy_ids else list(STRATEGIES.keys())
        setup, h4, h1, m5, eval_start, meta = await self._load_mtf(
            symbol,
            setup_tf=timeframe,
            start_date=start_date or rcfg.period_start,
            end_date=end_date,
            limit=limit,
        )
        multi = run_multi_strategy_backtest(
            symbol,
            timeframe,
            setup,
            ids,
            candles_4h=h4,
            candles_1h=h1,
            candles_5m=m5,
            signal_config=scfg,
            research_config=rcfg,
            index_start=eval_start,
            direction_filter=direction,
        )
        return {
            "symbol": normalize_research_symbol(symbol),
            "timeframe": timeframe,
            "meta": meta,
            "strategies": multi.get("strategies") or {},
            "elapsed_seconds": multi.get("elapsed_seconds"),
            "disclaimer": DISCLAIMER,
        }

    async def compare(
        self,
        *,
        symbols: Sequence[str] | None = None,
        timeframe: str = "15m",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
        direction: str | None = None,
        persist: bool = True,
        include_walk_forward: bool = False,
        max_symbols: int | None = None,
    ) -> dict[str, Any]:
        """Run all strategies across eligible symbols (chunked per symbol)."""
        t0 = time.perf_counter()
        rcfg = StrategyResearchConfig()
        if symbols:
            syms = [normalize_research_symbol(s) for s in symbols]
            # Lightweight coverage for explicit symbol lists (avoid full-universe scan)
            universe = {
                "status": "OK",
                "eligible_symbols": syms,
                "eligible_count": len(syms),
                "excluded_symbols": [],
                "excluded_count": 0,
                "full_universe": syms,
                "full_universe_count": len(syms),
                "note": "Explicit symbol list — full universe audit skipped",
            }
        else:
            universe = await build_universe_eligibility(
                setup_timeframe=timeframe,
                config=rcfg,
                period_start=start_date or rcfg.period_start,
            )
            syms = list(universe.get("eligible_symbols") or [])
        if max_symbols is not None:
            syms = syms[: max(0, int(max_symbols))]

        trades_by_strategy: dict[str, list[dict[str, Any]]] = {
            sid: [] for sid in STRATEGIES
        }
        per_symbol: dict[str, Any] = {}
        candles_processed = 0
        setups_processed = 0
        excluded_runtime: list[dict[str, Any]] = []

        for idx, sym in enumerate(syms):
            logger.info(
                "strategy_research_symbol_start",
                symbol=sym,
                index=idx + 1,
                total=len(syms),
            )
            try:
                run = await self.run_symbol(
                    sym,
                    timeframe=timeframe,
                    start_date=start_date or rcfg.period_start,
                    end_date=end_date,
                    limit=limit,
                    direction=direction,
                    research_config=rcfg,
                )
                logger.info(
                    "strategy_research_symbol_done",
                    symbol=sym,
                    elapsed=run.get("elapsed_seconds"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("strategy_symbol_failed", symbol=sym, error=str(exc))
                excluded_runtime.append(
                    {"symbol": sym, "status": "Excluded", "exclusion_reason": str(exc)}
                )
                continue

            per_symbol[sym] = {
                sid: {
                    "sample_size": (payload.get("sample_size") or 0),
                    "status": payload.get("status"),
                    "data_quality": payload.get("data_quality"),
                }
                for sid, payload in (run.get("strategies") or {}).items()
            }
            for sid, payload in (run.get("strategies") or {}).items():
                if payload.get("status") == "INSUFFICIENT_DATA":
                    continue
                candles_processed += int(payload.get("candles_processed") or 0)
                setups_processed += int(payload.get("setups_processed") or 0)
                for t in payload.get("trades") or []:
                    if t.get("exit_reason") == "OPEN":
                        continue
                    trades_by_strategy.setdefault(sid, []).append(t)

        results_list: list[dict[str, Any]] = []
        results_map = {}
        for sid, strat in STRATEGIES.items():
            agg = aggregate_strategy_results(
                strat,
                trades_by_strategy.get(sid) or [],
                research_config=rcfg,
                timeframe=timeframe,
            )
            results_map[sid] = agg
            results_list.append(agg.to_dict())

        wf: dict[str, Any] = {}
        if include_walk_forward and syms:
            # WF on first eligible symbol only (expensive); observational
            demo_sym = syms[0]
            setup, h4, h1, m5, _, _ = await self._load_mtf(
                demo_sym,
                setup_tf=timeframe,
                start_date=start_date or rcfg.period_start,
                end_date=end_date,
                limit=limit,
            )
            for sid in STRATEGIES:
                wf[sid] = run_walk_forward_strategy(
                    demo_sym,
                    timeframe,
                    setup,
                    sid,
                    candles_4h=h4,
                    candles_1h=h1,
                    candles_5m=m5,
                    research_config=rcfg,
                )

        elapsed = time.perf_counter() - t0
        all_trades = []
        for sid, ts in trades_by_strategy.items():
            all_trades.extend(ts)

        summary = {
            "run_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset": DATASET_LABEL,
            "configuration_hash": rcfg.configuration_hash(),
            "signal_engine_version": SIGNAL_ENGINE_VERSION,
            "research_engine_version": RESEARCH_ENGINE_VERSION,
            "configuration": rcfg.configuration_dict(),
            "data_period": {
                "requested_start": start_date or rcfg.period_start,
                "requested_end": end_date or "current",
                "note": "Per-symbol actual start = first available candle",
            },
            "symbols": syms,
            "timeframes": ["4h", "1h", timeframe, "5m"],
            "strategies_tested": len(STRATEGIES),
            "elapsed_seconds": elapsed,
            "candles_processed": candles_processed,
            "setups_processed": setups_processed,
            "data_coverage": universe,
            "universe": universe,
            "excluded_runtime": excluded_runtime,
            "per_symbol": per_symbol,
            "results": results_list,
            "condition_contribution": condition_contribution(results_map),
            "walk_forward": wf,
            "trades": all_trades,
            "fee_assumptions": {
                "taker_fee": rcfg.taker_fee,
                "maker_fee": rcfg.maker_fee,
                "slippage_rate": rcfg.slippage_rate,
                "same_bar_rule": rcfg.same_bar_rule,
                "cost_multipliers": list(rcfg.cost_multipliers),
                "leverage": "NOT_APPLIED_IN_R_METRICS",
            },
            "total_trades": len(all_trades),
            "label": "Historical Result",
            "disclaimer": DISCLAIMER,
            "note": (
                "Do not treat any strategy as BEST/WINNER/RECOMMENDED. "
                "OOS is evaluation-only. Live engine unchanged."
            ),
        }

        if persist:
            try:
                run_id = await save_strategy_run(summary)
                summary["run_id"] = run_id
            except Exception as exc:  # noqa: BLE001
                logger.warning("strategy_run_persist_failed", error=str(exc))
                summary["persist_error"] = str(exc)

        # Slim API response: drop raw trade blotter by default (huge)
        api = {k: v for k, v in summary.items() if k != "trades"}
        api["trades_persisted"] = bool(summary.get("run_id"))
        api["trade_count"] = len(all_trades)
        return api

    async def latest(self) -> dict[str, Any]:
        row = await load_latest_strategy_run()
        if row is None:
            return {
                "status": "EMPTY",
                "dataset": DATASET_LABEL,
                "results": [],
                "disclaimer": DISCLAIMER,
                "note": "No persisted research run yet. POST/GET compare to execute.",
            }
        payload = row.get("payload") or {}
        return {
            "status": "OK",
            "dataset": DATASET_LABEL,
            **row,
            "disclaimer": payload.get("disclaimer") or DISCLAIMER,
            "label": "Historical Result",
        }


_SERVICE: BosStrategyComparisonService | None = None


def get_bos_strategy_comparison_service() -> BosStrategyComparisonService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = BosStrategyComparisonService()
    return _SERVICE
