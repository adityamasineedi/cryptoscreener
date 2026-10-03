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
    series_quality_report,
)
from app.research.bos_strategy_comparison.diagnostics import (
    run_stage_funnel_diagnostic,
)
from app.research.bos_strategy_comparison.htf_variants import list_htf_variants
from app.research.bos_strategy_comparison.lifecycle import run_lifecycles_for_series
from app.research.bos_strategy_comparison.pullback_diagnostics import (
    run_pullback_forensic_diagnostic,
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
from app.research.bos_strategy_comparison.s3_diagnostics import (
    run_s3_forensic_diagnostic,
)
from app.research.bos_strategy_comparison.s3_htf_sensitivity import (
    aggregate_symbol_runs,
    reconcile_sep2024,
    run_s3_htf_sensitivity_symbol,
    run_walk_forward_sensitivity,
)
from app.research.bos_strategy_comparison.strategies import (
    STRATEGIES,
    list_strategies,
)
from app.research.data_pipeline.history_coverage import (
    audit_symbol_mtf_coverage,
    research_data_readiness_gate,
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
        universe = await build_universe_eligibility(
            config=cfg,
            required_timeframes=("4h", "1h", "15m", "5m"),
        )
        audit = await audit_ohlcv_coverage(
            timeframes=("4h", "1h", "15m", "5m")
        )
        history = await audit_symbol_mtf_coverage(
            timeframes=("5m", "15m", "1h", "4h"),
            period_start=cfg.period_start,
        )
        gate = await research_data_readiness_gate(
            timeframes=("5m", "15m", "1h", "4h"),
            period_start=cfg.period_start,
        )
        return {
            "status": "OK",
            "dataset": DATASET_LABEL,
            "universe": universe,
            "audit": audit,
            "history_coverage": history,
            "data_readiness": gate,
            "disclaimer": DISCLAIMER,
            "confirmations": {
                "live_signal_engines_unchanged": True,
                "no_fabricated_candles": True,
                "full_optimization_not_executed": True,
            },
        }

    async def diagnostics(
        self,
        *,
        symbol: str,
        timeframe: str = "15m",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
        strategy_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Stage funnel + retest rejection diagnostics (no strategy optimization)."""
        rcfg = StrategyResearchConfig()
        scfg = SignalConfig()
        setup, h4, h1, m5, eval_start, meta = await self._load_mtf(
            symbol,
            setup_tf=timeframe,
            start_date=start_date or rcfg.period_start,
            end_date=end_date,
            limit=limit,
        )
        if len(setup) < rcfg.min_bars:
            return {
                "status": "INSUFFICIENT_DATA",
                "symbol": normalize_research_symbol(symbol),
                "timeframe": timeframe,
                "bos_count": 0,
                "htf_aligned_count": 0,
                "impulse_count": 0,
                "pullback_count": 0,
                "retest_count": 0,
                "entry_count": 0,
                "meta": meta,
                "disclaimer": DISCLAIMER,
                "reason": f"Need at least {rcfg.min_bars} setup candles, got {len(setup)}",
            }
        report = run_stage_funnel_diagnostic(
            symbol=normalize_research_symbol(symbol),
            timeframe=normalize_research_timeframe(timeframe),
            candles=setup,
            candles_4h=h4,
            candles_1h=h1,
            candles_5m=m5,
            index_start=eval_start,
            signal_config=scfg,
            research_config=rcfg,
            strategy_ids=strategy_ids,
        )
        return {
            **report,
            "meta": meta,
            "dataset": DATASET_LABEL,
            "start_date": start_date or rcfg.period_start,
            "end_date": end_date,
        }

    async def pullback_diagnostics(
        self,
        *,
        symbol: str,
        timeframe: str = "15m",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
        max_traces: int = 8,
        lifecycle: bool = False,
    ) -> dict[str, Any]:
        """Forensic pullback diagnostic (research-only, no engine changes).

        lifecycle=True runs multi-bar frozen-impulse re-evaluation via production
        detect_pullback/detect_retest (research adapter only).
        """
        rcfg = StrategyResearchConfig()
        scfg = SignalConfig()
        setup, h4, h1, m5, eval_start, meta = await self._load_mtf(
            symbol,
            setup_tf=timeframe,
            start_date=start_date or rcfg.period_start,
            end_date=end_date,
            limit=limit,
        )
        if len(setup) < rcfg.min_bars:
            return {
                "status": "INSUFFICIENT_DATA",
                "symbol": normalize_research_symbol(symbol),
                "timeframe": timeframe,
                "bos_candidates": 0,
                "impulse_candidates": 0,
                "pullback_evaluations": 0,
                "pullback_pass": 0,
                "meta": meta,
                "disclaimer": DISCLAIMER,
                "reason": f"Need at least {rcfg.min_bars} setup candles, got {len(setup)}",
                "live_engines_unchanged": True,
                "pullback_logic_unchanged": True,
            }
        sym = normalize_research_symbol(symbol)
        tf = normalize_research_timeframe(timeframe)
        if lifecycle:
            report = run_lifecycles_for_series(
                symbol=sym,
                timeframe=tf,
                candles=setup,
                index_start=eval_start,
                signal_config=scfg,
                research_config=rcfg,
                max_trace_lifecycles=max_traces if max_traces else 20,
            )
            report["same_bar_baseline"] = {
                "pullback_pass": 0,
                "note": (
                    "Same-bar analyze_timeframe path cannot reach pullback PASS when "
                    "impulse.bar_index == as_of_index (WAITING for bars after impulse). "
                    "Use lifecycle=false forensic endpoint for full same-bar reason tables."
                ),
            }
            report["window"] = {
                "start": start_date,
                "end": end_date,
                "index_start": eval_start,
                "candles_loaded": len(setup),
            }
        else:
            report = run_pullback_forensic_diagnostic(
                symbol=sym,
                timeframe=tf,
                candles=setup,
                candles_4h=h4,
                candles_1h=h1,
                candles_5m=m5,
                index_start=eval_start,
                signal_config=scfg,
                research_config=rcfg,
                max_traces=max_traces,
                window_start_iso=start_date,
                window_end_iso=end_date,
            )
        return {
            **report,
            "meta": meta,
            "dataset": DATASET_LABEL,
            "start_date": start_date or rcfg.period_start,
            "end_date": end_date,
        }

    async def s3_diagnostics(
        self,
        *,
        symbol: str,
        timeframe: str = "15m",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int | None = None,
        direction: str | None = None,
        trace: bool = False,
        htf_trace: bool = False,
    ) -> dict[str, Any]:
        """Forensic S3 S/D (+ optional HTF) diagnostic (research-only, no engine changes)."""
        rcfg = StrategyResearchConfig()
        scfg = SignalConfig()
        setup, h4, h1, m5, eval_start, meta = await self._load_mtf(
            symbol,
            setup_tf=timeframe,
            start_date=start_date or rcfg.period_start,
            end_date=end_date,
            limit=None,
        )
        if len(setup) < rcfg.min_bars:
            return {
                "status": "INSUFFICIENT_DATA",
                "symbol": normalize_research_symbol(symbol),
                "timeframe": timeframe,
                "funnel": {
                    "bos_lifecycles": 0,
                    "pullback_pass": 0,
                    "sd_evaluated": 0,
                    "sd_available": 0,
                    "sd_confluence_pass": 0,
                    "retest_pass": 0,
                    "htf_pass": 0,
                    "entry_ready": 0,
                    "rr_pass": 0,
                    "final_s3_trades": 0,
                },
                "meta": meta,
                "disclaimer": DISCLAIMER,
                "reason": f"Need at least {rcfg.min_bars} setup candles, got {len(setup)}",
                "live_engines_unchanged": True,
                "sd_logic_unchanged": True,
                "htf_logic_unchanged": True,
            }
        report = run_s3_forensic_diagnostic(
            symbol=normalize_research_symbol(symbol),
            timeframe=normalize_research_timeframe(timeframe),
            candles=setup,
            candles_4h=h4,
            candles_1h=h1,
            candles_5m=m5,
            index_start=eval_start,
            signal_config=scfg,
            research_config=rcfg,
            direction_filter=direction,
            trace=trace,
            htf_trace=htf_trace,
            limit=int(limit) if limit is not None else 100,
        )
        return {
            **report,
            "meta": meta,
            "dataset": DATASET_LABEL,
            "start_date": start_date,
            "end_date": end_date,
        }

    async def s3_htf_sensitivity(
        self,
        *,
        symbols: Sequence[str] | None = None,
        timeframe: str = "15m",
        start_date: str | None = None,
        end_date: str | None = None,
        include_walk_forward: bool = False,
        reconcile_sep2024_window: bool = True,
    ) -> dict[str, Any]:
        """Research-only S3 HTF gate sensitivity (variants share pre-HTF path)."""
        t0 = time.perf_counter()
        rcfg = StrategyResearchConfig()
        scfg = SignalConfig()
        syms = [
            normalize_research_symbol(s)
            for s in (symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        ]
        tf = normalize_research_timeframe(timeframe)
        start = start_date or rcfg.period_start
        end = end_date

        coverage_all = await audit_symbol_mtf_coverage(
            timeframes=("15m", "1h", "4h"),
            period_start=start,
        )
        # Filter coverage rows to requested symbols when present
        cov_symbols = [
            row
            for row in (coverage_all.get("symbols") or [])
            if str(row.get("symbol") or "").upper() in set(syms)
        ]
        coverage = {
            **coverage_all,
            "symbols": cov_symbols or coverage_all.get("symbols"),
            "requested_symbols": syms,
        }
        warnings: list[str] = []
        if coverage_all.get("status") == "DATABASE_UNAVAILABLE":
            return {
                "status": "INSUFFICIENT_DATA",
                "coverage": coverage,
                "warnings": ["Coverage audit blocked sensitivity run"],
                "disclaimer": DISCLAIMER,
            }

        # --- Sep 2024 baseline reconciliation (mandatory gate) ---
        reconciliation = {"match": True, "skipped": not reconcile_sep2024_window}
        sep_runs: dict[str, Any] = {}
        if reconcile_sep2024_window:
            for sym in syms:
                logger.info("s3_htf_sensitivity_sep2024_start", symbol=sym)
                setup, h4, h1, m5, eval_start, meta = await self._load_mtf(
                    sym,
                    setup_tf=tf,
                    start_date="2024-09-01",
                    end_date="2024-10-01",
                    limit=None,
                )
                q = series_quality_report(setup, tf, config=rcfg)
                if q.get("status") == "INSUFFICIENT_DATA" or len(setup) < rcfg.min_bars:
                    warnings.append(f"{sym}: insufficient Sep 2024 data")
                    continue
                sep_runs[sym] = run_s3_htf_sensitivity_symbol(
                    symbol=sym,
                    timeframe=tf,
                    candles=setup,
                    candles_4h=h4,
                    candles_1h=h1,
                    candles_5m=m5,
                    index_start=eval_start,
                    signal_config=scfg,
                    research_config=rcfg,
                    period_label="SEP2024",
                )
                logger.info(
                    "s3_htf_sensitivity_sep2024_done",
                    symbol=sym,
                    sd=sep_runs[sym].get("shared_funnel", {}).get("sd_pass"),
                    htf=(
                        (sep_runs[sym].get("variants") or {})
                        .get("S3_BASELINE", {})
                        .get("funnel", {})
                        .get("htf_pass")
                    ),
                )
            reconciliation = reconcile_sep2024(sep_runs)
            if not reconciliation.get("match"):
                return {
                    "status": "RECONCILIATION_FAILED",
                    "reconciliation": reconciliation,
                    "coverage": coverage,
                    "warnings": warnings
                    + ["Baseline Sep 2024 mismatch — sensitivity stopped"],
                    "disclaimer": DISCLAIMER,
                    "live_engines_unchanged": True,
                }

        # --- Full-period sensitivity ---
        # If request window is exactly Sep 2024, reuse reconciliation runs.
        reuse_sep = (
            start == "2024-09-01"
            and (end in ("2024-10-01", None) or str(end).startswith("2024-10-01"))
            and bool(sep_runs)
        )
        symbol_runs: list[dict[str, Any]] = []
        dataset_rows: list[dict[str, Any]] = []
        walk_forward: dict[str, Any] = {}
        if reuse_sep:
            warnings.append("Full-period window equals Sep 2024 — reused reconcile runs")
            for sym, run in sep_runs.items():
                symbol_runs.append(run)
                dataset_rows.append(
                    {
                        "symbol": sym,
                        "candles_15m": None,
                        "period_start": "2024-09-01",
                        "period_end": "2024-10-01",
                        "reused_sep2024_run": True,
                    }
                )
        else:
            for sym in syms:
                logger.info("s3_htf_sensitivity_full_start", symbol=sym, start=start)
                setup, h4, h1, m5, eval_start, meta = await self._load_mtf(
                    sym,
                    setup_tf=tf,
                    start_date=start,
                    end_date=end,
                    limit=None,
                )
                q = series_quality_report(setup, tf, config=rcfg)
                dataset_rows.append(
                    {
                        "symbol": sym,
                        "candles_15m": len(setup),
                        "candles_1h": len(h1),
                        "candles_4h": len(h4),
                        "eval_start": eval_start,
                        "quality": q,
                        "meta": meta,
                        "period_start": start,
                        "period_end": end,
                    }
                )
                if q.get("status") == "INSUFFICIENT_DATA" or len(setup) < rcfg.min_bars:
                    warnings.append(f"{sym}: insufficient data for full period")
                    continue
                run = run_s3_htf_sensitivity_symbol(
                    symbol=sym,
                    timeframe=tf,
                    candles=setup,
                    candles_4h=h4,
                    candles_1h=h1,
                    candles_5m=m5,
                    index_start=eval_start,
                    signal_config=scfg,
                    research_config=rcfg,
                    period_label="FULL",
                )
                symbol_runs.append(run)
                logger.info(
                    "s3_htf_sensitivity_full_done",
                    symbol=sym,
                    trades={
                        vid: p.get("trade_count")
                        for vid, p in (run.get("variants") or {}).items()
                    },
                )
                if include_walk_forward and len(setup) > rcfg.walk_forward_train_bars:
                    walk_forward[sym] = run_walk_forward_sensitivity(
                        symbol=sym,
                        timeframe=tf,
                        candles=setup,
                        candles_4h=h4,
                        candles_1h=h1,
                        research_config=rcfg,
                        index_start=eval_start,
                    )

        if not symbol_runs:
            return {
                "status": "INSUFFICIENT_DATA",
                "coverage": coverage,
                "dataset": {"symbols": dataset_rows},
                "reconciliation": reconciliation,
                "warnings": warnings or ["No symbols produced runs"],
                "disclaimer": DISCLAIMER,
            }

        agg = aggregate_symbol_runs(symbol_runs, research_config=rcfg)
        return {
            "status": "OK",
            "dataset": {
                "label": DATASET_LABEL,
                "symbols": dataset_rows,
                "timeframe": tf,
                "start": start,
                "end": end,
                "variant_catalog": list_htf_variants(),
            },
            "coverage": coverage,
            "reconciliation": reconciliation,
            "per_symbol": {r["symbol"]: r for r in symbol_runs},
            "variants": agg.get("variants"),
            "pairwise_vs_baseline": agg.get("pairwise_vs_baseline"),
            "baseline_htf_rejection_distribution": agg.get(
                "baseline_htf_rejection_distribution"
            ),
            "robustness": agg.get("robustness"),
            "walk_forward": walk_forward,
            "warnings": warnings,
            "no_winner_declared": True,
            "disclaimer": DISCLAIMER,
            "live_engines_unchanged": True,
            "htf_production_unchanged": True,
            "elapsed_seconds": time.perf_counter() - t0,
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
        bypass_data_gate: bool = False,
    ) -> dict[str, Any]:
        """Run all strategies across eligible symbols (chunked per symbol)."""
        t0 = time.perf_counter()
        rcfg = StrategyResearchConfig()

        # Full-universe comparison (no explicit symbol list / no limit) requires
        # the research data readiness gate. Small validations may bypass.
        is_full_run = (
            not symbols
            and limit is None
            and max_symbols is None
            and not bypass_data_gate
        )
        if is_full_run:
            gate = await research_data_readiness_gate(
                timeframes=("5m", "15m", "1h", "4h"),
                period_start=start_date or rcfg.period_start,
            )
            if not gate.get("allow_full_strategy_comparison"):
                return {
                    "status": "RESEARCH_BLOCKED_DATA_INSUFFICIENT",
                    "dataset": DATASET_LABEL,
                    "reasons": gate.get("reasons") or [],
                    "data_readiness": gate,
                    "disclaimer": DISCLAIMER,
                    "note": (
                        "Full strategy comparison blocked until MTF history "
                        "and data-quality gate pass. Live engines unchanged."
                    ),
                    "confirmations": {
                        "live_signal_engines_unchanged": True,
                        "existing_bos_impulse_pullback_retest_entry_sl_tp_unchanged": True,
                        "no_fabricated_candles": True,
                        "full_optimization_not_executed": True,
                    },
                }

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
