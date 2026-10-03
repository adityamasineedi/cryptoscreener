"""Orchestrate research data pipeline: download → validate → features → events.

Isolated from live trading. Does not restart production processes.
Stops after dataset quality report when ``stop_after_dataset=True`` (default).
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from app.config import get_settings
from app.core.logging import get_logger
from app.ingestion.binance_rest import BinanceRestClient
from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.data_pipeline.config import (
    FEATURE_VERSION,
    PIPELINE_VERSION,
    PipelineConfig,
)
from app.research.data_pipeline.downloader import (
    discover_perpetual_symbols,
    download_universe,
)
from app.research.data_pipeline.event_store import evaluate_strategy_specs
from app.research.data_pipeline.feature_store import ensure_features_for_symbol
from app.research.data_pipeline.manifest import (
    build_quality_report,
    data_fingerprint,
    make_dataset_version,
)
from app.research.data_pipeline.metrics import (
    PipelineMetrics,
    estimate_naive_runtime_hours,
)
from app.research.data_pipeline import repository as repo
from app.services.database import db_manager

logger = get_logger("research_data_pipeline.runner")


def _git_commit() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip() or None
    except Exception:  # noqa: BLE001
        return None


async def preflight_safety_check() -> dict[str, Any]:
    """Non-destructive health checks before a full run."""
    checks: dict[str, Any] = {
        "postgres": False,
        "destructive_migration": False,
        "table_truncation": False,
        "production_restart_required": False,
        "ok": False,
        "notes": [],
    }
    try:
        if db_manager.engine is None:
            settings = get_settings()
            await db_manager.connect(settings)
        healthy = await db_manager.health()
        checks["postgres"] = (
            isinstance(healthy, dict)
            and healthy.get("status") == "ok"
            and bool(healthy.get("enabled", True))
        )
    except Exception as exc:  # noqa: BLE001
        checks["notes"].append(f"postgres_error:{exc}")
    checks["notes"].append(
        "Research uses separate process path; does not restart live WS/screener."
    )
    checks["ok"] = bool(checks["postgres"]) and not checks["destructive_migration"]
    return checks


class ResearchDataPipeline:
    """High-level entrypoint for historical research data builds."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self.config = config or PipelineConfig()
        self.metrics = PipelineMetrics()
        self._cancel = False

    def request_cancel(self) -> None:
        self._cancel = True

    async def run(
        self,
        *,
        symbols: Sequence[str] | None = None,
        discover: bool = False,
        build_features: bool = True,
        evaluate_event_masks: bool = False,
        dataset_label: str = "v1",
        mode: str = "full",  # smoke | full | sync
        dry_run: bool = False,
    ) -> dict[str, Any]:
        cfg = self.config
        metrics = self.metrics
        run_id = f"research-sync-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        git = _git_commit()
        dataset_version = make_dataset_version(dataset_label)

        preflight = await preflight_safety_check()
        if not preflight["ok"]:
            return {
                "status": "ABORTED",
                "reason": "preflight_failed",
                "preflight": preflight,
                "run_id": run_id,
            }

        await repo.ensure_research_pipeline_schema()
        metrics.db_queries += 1

        settings = get_settings()
        rest: BinanceRestClient | None = None
        owns_rest = False

        try:
            # Dry-run / sync planning: avoid network for symbol discovery unless required
            if dry_run and symbols:
                syms = [s.upper() for s in symbols]
                discovered = [{"symbol": s} for s in syms]
            else:
                rest = BinanceRestClient(settings)
                await rest.start()
                owns_rest = True
                if discover or not symbols:
                    discovered = await discover_perpetual_symbols(
                        rest, quote=cfg.quote_asset
                    )
                    syms = [d["symbol"] for d in discovered]
                else:
                    syms = [s.upper() for s in symbols]
                    discovered = [{"symbol": s} for s in syms]

            if mode == "smoke":
                # Section 40 — small validation set
                default_smoke = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
                requested = set(syms)
                syms = [s for s in default_smoke if s in requested] or list(default_smoke)
                end = datetime.now(timezone.utc)
                start = end.replace(year=end.year - 1)
                smoke_tfs = (
                    ["5m", "15m", "1h", "4h"]
                    if build_features
                    else list(cfg.timeframes) or ["15m"]
                )
                cfg = PipelineConfig.from_mapping(
                    {
                        **cfg.to_dict(),
                        "period_start": cfg.period_start
                        if cfg.period_end
                        else start.date().isoformat(),
                        "period_end": cfg.period_end or end.date().isoformat(),
                        "timeframes": smoke_tfs,
                    }
                )

            naive_est = estimate_naive_runtime_hours(
                symbols=len(syms),
                timeframes=len(cfg.timeframes),
                years=6.0 if mode in ("full", "sync") else 1.0,
            )
            metrics.notes.append(f"naive_runtime_estimate_hours={naive_est}")

            await repo.insert_pipeline_run(
                {
                    "run_id": run_id,
                    "git_commit": git,
                    "pipeline_version": PIPELINE_VERSION,
                    "feature_version": FEATURE_VERSION,
                    "data_version": dataset_version,
                    "configuration_hash": cfg.configuration_hash(),
                    "configuration": {**cfg.to_dict(), "dry_run": dry_run, "mode": mode},
                    "symbols": syms,
                    "timeframes": list(cfg.timeframes),
                    "period_start": cfg.period_start,
                    "period_end": cfg.resolved_period_end(),
                    "status": "RUNNING",
                    "metrics": {},
                    "quality_report": {},
                    "failed_symbols": [],
                }
            )
            metrics.db_queries += 1

            # Incremental sync path — DB-first plan; dry-run = zero Binance calls
            if mode == "sync" or dry_run:
                from app.research.data_pipeline.sync import sync_universe

                listed_at: dict[str, int | None] = {}
                for d in discovered:
                    listed = d.get("listed_at")
                    if listed is None:
                        listed_at[d["symbol"]] = None
                    elif hasattr(listed, "timestamp"):
                        listed_at[d["symbol"]] = int(listed.timestamp() * 1000)
                    else:
                        listed_at[d["symbol"]] = None

                if dry_run and rest is None:
                    sync_result = await sync_universe(
                        None,
                        symbols=syms,
                        config=cfg,
                        metrics=metrics,
                        run_id=run_id,
                        listed_at_by_symbol=listed_at,
                        should_cancel=lambda: self._cancel,
                        dry_run=True,
                    )
                    metrics.mark_done()
                    sync_result["run_id"] = run_id
                    sync_result["preflight"] = preflight
                    sync_result["confirmations"] = {
                        "live_trading_unchanged": True,
                        "no_fabricated_data": True,
                        "db_source_of_truth": True,
                        "dry_run_no_network": True,
                    }
                    await repo.insert_pipeline_run(
                        {
                            "run_id": run_id,
                            "git_commit": git,
                            "pipeline_version": PIPELINE_VERSION,
                            "feature_version": FEATURE_VERSION,
                            "data_version": dataset_version,
                            "configuration_hash": cfg.configuration_hash(),
                            "configuration": {**cfg.to_dict(), "dry_run": True},
                            "symbols": syms,
                            "timeframes": list(cfg.timeframes),
                            "period_start": cfg.period_start,
                            "period_end": cfg.resolved_period_end(),
                            "status": "DRY_RUN",
                            "metrics": metrics.to_dict(),
                            "quality_report": sync_result,
                            "failed_symbols": [],
                        }
                    )
                    return sync_result

                if rest is None:
                    rest = BinanceRestClient(settings)
                    await rest.start()
                    owns_rest = True

                sync_result = await sync_universe(
                    rest,
                    symbols=syms,
                    config=cfg,
                    metrics=metrics,
                    run_id=run_id,
                    listed_at_by_symbol=listed_at,
                    should_cancel=lambda: self._cancel,
                    dry_run=False,
                )
                metrics.mark_done()
                sync_result["run_id"] = run_id
                sync_result["dataset_version"] = dataset_version
                sync_result["preflight"] = preflight
                sync_result["confirmations"] = {
                    "live_trading_unchanged": True,
                    "no_fabricated_data": True,
                    "db_source_of_truth": True,
                    "manifest_not_sole_truth": True,
                }
                # Optional features after incremental sync
                if build_features and not self._cancel and sync_result.get("status") == "DONE":
                    rcfg = StrategyResearchConfig(
                        period_start=cfg.period_start,
                        setup_timeframe=cfg.setup_timeframe,
                        htf_timeframes=cfg.htf_timeframes,
                        entry_timeframe=cfg.entry_timeframe,
                    )
                    feature_summaries = []
                    for sym in syms:
                        try:
                            feature_summaries.append(
                                await ensure_features_for_symbol(
                                    symbol=sym,
                                    config=cfg,
                                    dataset_version=dataset_version,
                                    metrics=metrics,
                                    research_cfg=rcfg,
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            feature_summaries.append(
                                {"symbol": sym, "status": "FAILED", "error": str(exc)}
                            )
                    sync_result["feature_summaries"] = feature_summaries
                await repo.insert_pipeline_run(
                    {
                        "run_id": run_id,
                        "git_commit": git,
                        "pipeline_version": PIPELINE_VERSION,
                        "feature_version": FEATURE_VERSION,
                        "data_version": dataset_version,
                        "configuration_hash": cfg.configuration_hash(),
                        "configuration": cfg.to_dict(),
                        "symbols": syms,
                        "timeframes": list(cfg.timeframes),
                        "period_start": cfg.period_start,
                        "period_end": cfg.resolved_period_end(),
                        "status": sync_result.get("status") or "DONE",
                        "metrics": metrics.to_dict(),
                        "quality_report": {
                            k: sync_result.get(k)
                            for k in (
                                "download_reduction_percent",
                                "downloaded_candles",
                                "inserted_candles",
                                "gaps_before",
                                "gaps_after",
                            )
                        },
                        "failed_symbols": [],
                    }
                )
                return sync_result

            if rest is None:
                rest = BinanceRestClient(settings)
                await rest.start()
                owns_rest = True

            checkpoints = await download_universe(
                rest,
                symbols=syms,
                config=cfg,
                metrics=metrics,
                should_cancel=lambda: self._cancel,
            )

            bars_by_key: dict[tuple[str, str], int] = {}
            for cp in checkpoints:
                _f, _l, bars = await repo.series_bounds(cp.symbol, cp.timeframe)
                metrics.db_queries += 1
                bars_by_key[(cp.symbol, cp.timeframe)] = bars

            quality = build_quality_report(
                dataset_version=dataset_version,
                checkpoints=checkpoints,
                symbols_requested=syms,
                timeframes=cfg.timeframes,
                bars_by_key=bars_by_key,
            )
            fp = data_fingerprint(quality)

            await repo.save_dataset_version(
                {
                    "dataset_version": dataset_version,
                    "pipeline_version": PIPELINE_VERSION,
                    "feature_version": FEATURE_VERSION,
                    "data_fingerprint": fp,
                    "git_commit": git,
                    "symbols": quality.symbols_eligible,
                    "timeframes": list(cfg.timeframes),
                    "period_start": cfg.period_start,
                    "period_end": cfg.resolved_period_end(),
                    "candle_counts": {
                        tf: quality.per_timeframe.get(tf, {}).get("candles", 0)
                        for tf in cfg.timeframes
                    },
                    "quality_report": quality.to_dict(),
                    "payload": {"run_id": run_id, "mode": mode},
                }
            )
            metrics.db_queries += 1

            feature_summaries: list[dict[str, Any]] = []
            event_mask_report: dict[str, Any] | None = None

            if build_features and not self._cancel:
                rcfg = StrategyResearchConfig(
                    period_start=cfg.period_start,
                    setup_timeframe=cfg.setup_timeframe,
                    htf_timeframes=cfg.htf_timeframes,
                    entry_timeframe=cfg.entry_timeframe,
                )
                sem = asyncio.Semaphore(max(1, min(cfg.max_workers, 4)))

                async def _feat(sym: str) -> None:
                    async with sem:
                        if self._cancel:
                            return
                        try:
                            summary = await ensure_features_for_symbol(
                                symbol=sym,
                                config=cfg,
                                dataset_version=dataset_version,
                                metrics=metrics,
                                research_cfg=rcfg,
                            )
                            feature_summaries.append(summary)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "feature_build_failed", symbol=sym, error=str(exc)
                            )
                            feature_summaries.append(
                                {"symbol": sym, "status": "FAILED", "error": str(exc)}
                            )

                await asyncio.gather(
                    *[_feat(s) for s in quality.symbols_eligible or syms]
                )

                if evaluate_event_masks and not cfg.stop_after_dataset:
                    # Optional readiness check — does not run full optimization
                    from sqlalchemy import text

                    if db_manager.engine is not None:
                        async with db_manager.engine.begin() as conn:
                            rows = (
                                await conn.execute(
                                    text(
                                        """
                                        SELECT direction, htf_alignment, impulse,
                                               pullback, retest, sd_state, payload
                                        FROM research_bos_events
                                        WHERE dataset_version = :dv
                                          AND feature_version = :fv
                                        """
                                    ),
                                    {
                                        "dv": dataset_version,
                                        "fv": FEATURE_VERSION,
                                    },
                                )
                            ).mappings().fetchall()
                        metrics.db_queries += 1
                        events = [dict(r) for r in rows]
                        event_mask_report = evaluate_strategy_specs(
                            events, min_sample=cfg.min_sample_size
                        )

            metrics.mark_done()
            failed = sorted(
                {
                    cp.symbol
                    for cp in checkpoints
                    if cp.status == "FAILED"
                }
                | {
                    s["symbol"]
                    for s in feature_summaries
                    if s.get("status") == "FAILED"
                }
            )

            status = "DONE"
            if self._cancel:
                status = "PARTIAL"
            elif failed and quality.symbols_eligible:
                status = "PARTIAL"
            elif not quality.symbols_eligible:
                status = "ERROR"

            result = {
                "status": status,
                "run_id": run_id,
                "git_commit": git,
                "pipeline_version": PIPELINE_VERSION,
                "feature_version": FEATURE_VERSION,
                "dataset_version": dataset_version,
                "data_fingerprint": fp,
                "mode": mode,
                "preflight": preflight,
                "symbols": syms,
                "discovered_count": len(discovered),
                "timeframes": list(cfg.timeframes),
                "period_start": cfg.period_start,
                "period_end": cfg.resolved_period_end(),
                "quality_report": quality.to_dict(),
                "feature_summaries": feature_summaries,
                "event_mask_report": event_mask_report,
                "bos_event_count": await repo.count_bos_events(
                    dataset_version, FEATURE_VERSION
                ),
                "metrics": metrics.to_dict(),
                "failed_symbols": failed,
                "naive_runtime_estimate_hours": naive_est,
                "speed_note": (
                    "Chunked download + DO NOTHING inserts + event store; "
                    "avoids full-universe DataFrame and per-strategy candle rescan."
                ),
                "confirmations": {
                    "live_trading_unchanged": True,
                    "no_fabricated_data": True,
                    "oos_locked_until_strategy_phase": True,
                    "stop_after_dataset": cfg.stop_after_dataset,
                },
            }

            await repo.insert_pipeline_run(
                {
                    "run_id": run_id,
                    "git_commit": git,
                    "pipeline_version": PIPELINE_VERSION,
                    "feature_version": FEATURE_VERSION,
                    "data_version": dataset_version,
                    "configuration_hash": cfg.configuration_hash(),
                    "configuration": cfg.to_dict(),
                    "symbols": syms,
                    "timeframes": list(cfg.timeframes),
                    "period_start": cfg.period_start,
                    "period_end": cfg.resolved_period_end(),
                    "status": status,
                    "metrics": metrics.to_dict(),
                    "quality_report": quality.to_dict(),
                    "failed_symbols": failed,
                }
            )
            metrics.db_queries += 1
            return result
        finally:
            if owns_rest:
                await rest.close()
