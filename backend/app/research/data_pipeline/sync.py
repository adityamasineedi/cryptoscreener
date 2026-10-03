"""Incremental OHLCV sync: DB audit → plan → lock → recheck → download missing only.

Dry-run makes zero network calls. Second sync on unchanged data downloads ~0.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from app.core.logging import get_logger
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import normalize_timeframe
from app.research.data_pipeline.checkpoint import SeriesCheckpoint
from app.research.data_pipeline.config import (
    PipelineConfig,
    STATUS_COMPLETE,
    STATUS_PARTIAL,
)
from app.research.data_pipeline.coverage import (
    audit_series_coverage,
    recheck_range_covered,
)
from app.research.data_pipeline.deduplicator import dedupe_rows
from app.research.data_pipeline.locks import (
    OP_DATA_SYNC,
    SYNC_ALREADY_RUNNING,
    count_active_data_sync_runs,
    heartbeat_lock,
    range_lock,
    warn_global_concurrency,
)
from app.research.data_pipeline.metrics import PipelineMetrics
from app.research.data_pipeline.normalizer import normalize_kline_row
from app.research.data_pipeline.paginator import page_windows_within_chunk, TimeChunk
from app.research.data_pipeline.planner import (
    ACTION_SKIP,
    ACTION_UNAVAILABLE,
    DownloadPlan,
    format_console_coverage,
    plan_from_coverage,
    plan_universe,
)
from app.research.data_pipeline import repository as repo
from app.research.data_pipeline.validator import filter_valid_rows, validate_candles

logger = get_logger("research_data_pipeline.sync")

_TF_PRIORITY = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "1d": 4, "1m": 5}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _console(msg: str) -> None:
    print(f"[{_ts()}] {msg}", flush=True)


async def build_sync_plans(
    *,
    symbols: Sequence[str],
    config: PipelineConfig,
    listed_at_by_symbol: dict[str, int | None] | None = None,
    detect_gaps: bool = True,
) -> list[DownloadPlan]:
    """DB-first coverage + download plans. No network."""
    ordered_tfs = sorted(
        config.timeframes,
        key=lambda t: _TF_PRIORITY.get(str(t).lower(), 99),
    )
    listed = listed_at_by_symbol or {}
    plans: list[DownloadPlan] = []
    end_day = config.resolved_period_end()
    for tf in ordered_tfs:
        for sym in symbols:
            manifest = await repo.load_manifest(sym, tf)
            cov = await audit_series_coverage(
                sym,
                tf,
                start_day=config.period_start,
                end_day=end_day,
                listed_at_ms=listed.get(sym.upper()),
                detect_internal_gaps=detect_gaps,
                manifest_status=manifest.status if manifest else None,
            )
            plan = plan_from_coverage(cov, kline_limit=config.kline_limit)
            plans.append(plan)
    return plans


async def dry_run_sync(
    *,
    symbols: Sequence[str],
    config: PipelineConfig,
    listed_at_by_symbol: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    """Inspect DB and emit plan report. Zero Binance requests."""
    plans = await build_sync_plans(
        symbols=symbols,
        config=config,
        listed_at_by_symbol=listed_at_by_symbol,
        detect_gaps=True,
    )
    agg = plan_universe(plans, kline_limit=config.kline_limit)
    for p in plans:
        _console(format_console_coverage(p))
        _console("")
    _console("DRY-RUN SUMMARY (no network)")
    _console(f"symbols: {agg['symbols']}")
    for tf, bucket in agg["by_timeframe"].items():
        _console(
            f"{tf}: already covered={bucket['already_covered']} "
            f"missing_ranges={bucket['missing_ranges']} "
            f"estimated_candles={bucket['estimated_candles']:,}"
        )
    _console(f"total_existing_candles: {agg['total_existing_candles']:,}")
    _console(f"total_missing_candles: {agg['total_missing_candles']:,}")
    _console(f"estimated_binance_requests: {agg['estimated_binance_requests']:,}")
    return {
        "status": "DRY_RUN",
        "mode": "sync",
        "dry_run": True,
        "binance_requests": 0,
        **agg,
    }


async def _download_range(
    rest: BinanceRestClient,
    *,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    config: PipelineConfig,
    metrics: PipelineMetrics,
    run_id: str,
    should_cancel: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Download one missing range with recheck + conflict detection."""
    tf = normalize_timeframe(timeframe)
    stats = {
        "rows_received": 0,
        "rows_inserted": 0,
        "rows_duplicate": 0,
        "rows_invalid": 0,
        "rows_already_present": 0,
        "conflicts": 0,
        "requests": 0,
        "skipped_recheck": False,
        "elapsed_ms": 0,
    }
    t0 = time.monotonic()

    # Recheck — another process may have filled this range
    if await recheck_range_covered(symbol, tf, start_ms, end_ms):
        stats["skipped_recheck"] = True
        stats["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        logger.info(
            "chunk_skip_already_covered",
            run_id=run_id,
            symbol=symbol,
            timeframe=tf,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        return stats

    chunk = TimeChunk(
        symbol=symbol.upper(),
        timeframe=tf,
        start_ms=start_ms,
        end_ms=end_ms,
        chunk_index=0,
    )
    for page_start, page_end in page_windows_within_chunk(
        chunk, kline_limit=config.kline_limit
    ):
        if should_cancel and should_cancel():
            break
        raw = await rest.futures_klines(
            symbol.upper(),
            tf,
            limit=config.kline_limit,
            start_time=page_start,
            end_time=page_end - 1,
        )
        metrics.binance_requests += 1
        stats["requests"] += 1
        if not raw:
            metrics.binance_empty_pages += 1
            await asyncio.sleep(config.page_pause_seconds)
            continue

        rows: list[dict[str, Any]] = []
        for row in raw:
            norm = normalize_kline_row(
                symbol.upper(),
                tf,
                row,
                source=config.write_source_tag,
            )
            if norm is not None:
                rows.append(norm)
        stats["rows_received"] += len(rows)
        rows = dedupe_rows(rows)
        good, reasons = filter_valid_rows(rows)
        stats["rows_invalid"] += len(reasons)
        metrics.candles_validated += len(good)

        if good:
            conflicts = await repo.detect_ohlcv_conflicts(good, run_id=run_id)
            stats["conflicts"] += len(conflicts)
            metrics.data_conflicts += len(conflicts)
            # Insert only non-conflicting rows (conflicts already exist with different OHLC)
            conflict_times = {c["time"] for c in conflicts}
            insertable = [r for r in good if r.get("time") not in conflict_times]
            before = len(insertable)
            metrics.db_insert_attempts += 1
            n = await repo.insert_ohlcv_do_nothing(
                insertable, batch_size=config.batch_insert_size
            )
            metrics.db_rows_written += n
            stats["rows_inserted"] += n
            already = max(0, before - n)
            stats["rows_already_present"] += already
            stats["rows_duplicate"] += already
            metrics.db_queries += 2

        await asyncio.sleep(config.page_pause_seconds)

    stats["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return stats


async def sync_series(
    rest: BinanceRestClient,
    *,
    symbol: str,
    timeframe: str,
    config: PipelineConfig,
    metrics: PipelineMetrics,
    run_id: str,
    listed_at_ms: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Incremental sync for one symbol×timeframe."""
    sym = symbol.upper()
    tf = normalize_timeframe(timeframe)
    end_day = config.resolved_period_end()

    manifest = await repo.load_manifest(sym, tf)
    cov = await audit_series_coverage(
        sym,
        tf,
        start_day=config.period_start,
        end_day=end_day,
        listed_at_ms=listed_at_ms,
        detect_internal_gaps=True,
        manifest_status=manifest.status if manifest else None,
    )
    metrics.db_queries += 2
    plan = plan_from_coverage(cov, kline_limit=config.kline_limit)
    _console(format_console_coverage(plan))

    result: dict[str, Any] = {
        "symbol": sym,
        "timeframe": tf,
        "plan": plan.to_dict(),
        "action": plan.action,
        "downloaded": 0,
        "inserted": 0,
        "requests": 0,
        "conflicts": 0,
        "gaps_before": len(cov.gaps),
        "gaps_after": None,
        "status": plan.action,
    }

    if dry_run or plan.action in (ACTION_SKIP, ACTION_UNAVAILABLE):
        metrics.series_skipped += 1
        if plan.action == ACTION_SKIP:
            metrics.series_completed += 1
            # Refresh manifest from DB truth without redownload
            cp = manifest or SeriesCheckpoint(
                symbol=sym,
                timeframe=tf,
                requested_start=config.period_start,
                requested_end=end_day,
            )
            if cov.existing_min_ms is not None:
                cp.actual_first = datetime.fromtimestamp(
                    cov.existing_min_ms / 1000, tz=timezone.utc
                ).date().isoformat()
                cp.actual_last = datetime.fromtimestamp(
                    cov.existing_max_ms / 1000, tz=timezone.utc
                ).date().isoformat() if cov.existing_max_ms else None
                cp.candles_downloaded = cov.row_count
                cp.gap_count = len(cov.gaps)
                cp.duplicate_count = cov.duplicate_count
                cp.invalid_rows = cov.invalid_ohlc_count
                if not cov.gaps and cov.covers_requested:
                    cp.mark_complete()
                await repo.upsert_manifest(cp)
                metrics.db_queries += 1
        result["status"] = "SKIP" if plan.action == ACTION_SKIP else plan.action
        return result

    # Estimated full redownload for reduction metric
    metrics.estimated_full_redownload_candles += max(cov.expected_count, 0)

    cp = manifest or SeriesCheckpoint(
        symbol=sym,
        timeframe=tf,
        requested_start=config.period_start,
        requested_end=end_day,
    )
    cp.requested_start = config.period_start
    cp.requested_end = end_day
    cp.mark_downloading()
    await repo.upsert_manifest(cp)
    metrics.db_queries += 1

    total_inserted = 0
    total_recv = 0
    total_req = 0
    total_conflicts = 0

    for rng in plan.ranges:
        if should_cancel and should_cancel():
            cp.mark_partial("cancelled")
            break
        async with range_lock(
            operation=OP_DATA_SYNC,
            symbol=sym,
            timeframe=tf,
            start_ms=rng.start_ms,
            end_ms=rng.end_ms,
            run_id=run_id,
        ) as lock:
            if not lock.acquired:
                result["lock_status"] = lock.reason or SYNC_ALREADY_RUNNING
                _console(f"{sym} {tf} {SYNC_ALREADY_RUNNING}")
                metrics.lock_conflicts += 1
                continue
            await heartbeat_lock(lock.lock_key)
            stats = await _download_range(
                rest,
                symbol=sym,
                timeframe=tf,
                start_ms=rng.start_ms,
                end_ms=rng.end_ms,
                config=config,
                metrics=metrics,
                run_id=run_id,
                should_cancel=should_cancel,
            )
            total_inserted += stats["rows_inserted"]
            total_recv += stats["rows_received"]
            total_req += stats["requests"]
            total_conflicts += stats["conflicts"]
            metrics.actual_download_candles += stats["rows_received"]
            cp.mark_chunk_done(0, rng.end_ms, stats["rows_inserted"])
            await repo.upsert_manifest(cp)
            metrics.db_queries += 1
            metrics.chunks_completed += 1
            _console(
                f"{sym} {tf} DOWNLOAD requests={stats['requests']} "
                f"received={stats['rows_received']:,} inserted={stats['rows_inserted']:,} "
                f"duplicate={stats['rows_duplicate']:,} invalid={stats['rows_invalid']:,} "
                f"elapsed={stats['elapsed_ms']/1000:.1f}s"
            )

    # Post-download gap repair pass (targeted)
    repair_cov = await audit_series_coverage(
        sym,
        tf,
        start_day=config.period_start,
        end_day=end_day,
        listed_at_ms=listed_at_ms,
        detect_internal_gaps=True,
        manifest_status=None,
    )
    metrics.db_queries += 1
    repair_plan = plan_from_coverage(repair_cov, kline_limit=config.kline_limit)
    if repair_plan.ranges and not (should_cancel and should_cancel()):
        for rng in repair_plan.ranges:
            if rng.reason != "INTERNAL_GAP":
                # edges already attempted; only repair remaining internal gaps
                if rng.reason not in ("INTERNAL_GAP",):
                    pass
            async with range_lock(
                operation=OP_DATA_SYNC,
                symbol=sym,
                timeframe=tf,
                start_ms=rng.start_ms,
                end_ms=rng.end_ms,
                run_id=run_id,
            ) as lock:
                if not lock.acquired:
                    continue
                stats = await _download_range(
                    rest,
                    symbol=sym,
                    timeframe=tf,
                    start_ms=rng.start_ms,
                    end_ms=rng.end_ms,
                    config=config,
                    metrics=metrics,
                    run_id=run_id,
                    should_cancel=should_cancel,
                )
                total_inserted += stats["rows_inserted"]
                total_recv += stats["rows_received"]
                total_req += stats["requests"]

    final_cov = await audit_series_coverage(
        sym,
        tf,
        start_day=config.period_start,
        end_day=end_day,
        listed_at_ms=listed_at_ms,
        detect_internal_gaps=True,
    )
    metrics.db_queries += 1
    result["gaps_after"] = len(final_cov.gaps)
    result["downloaded"] = total_recv
    result["inserted"] = total_inserted
    result["requests"] = total_req
    result["conflicts"] = total_conflicts

    if final_cov.existing_min_ms is not None:
        cp.actual_first = datetime.fromtimestamp(
            final_cov.existing_min_ms / 1000, tz=timezone.utc
        ).date().isoformat()
        cp.actual_last = datetime.fromtimestamp(
            final_cov.existing_max_ms / 1000, tz=timezone.utc
        ).date().isoformat() if final_cov.existing_max_ms else None
        cp.candles_downloaded = final_cov.row_count
        cp.gap_count = len(final_cov.gaps)
        cp.duplicate_count = final_cov.duplicate_count
        cp.invalid_rows = final_cov.invalid_ohlc_count
        if final_cov.covers_requested and final_cov.invalid_ohlc_count == 0:
            cp.quality = "PASS"
            cp.mark_complete()
            result["status"] = "COMPLETE"
        elif final_cov.invalid_ohlc_count > 0:
            cp.quality = "FAIL"
            cp.mark_partial("DATA_QUALITY_FAILED")
            result["status"] = "DATA_QUALITY_FAILED"
        else:
            cp.mark_partial("gaps_remain")
            result["status"] = "PARTIAL"
    else:
        cp.mark_no_history()
        result["status"] = "NO_HISTORY"

    await repo.upsert_manifest(cp)
    metrics.db_queries += 1
    metrics.series_completed += 1
    cov_pct = 100.0
    if final_cov.expected_count > 0:
        present = max(0, final_cov.expected_count - final_cov.missing_count)
        cov_pct = round(100.0 * present / final_cov.expected_count, 2)
    _console(
        f"{sym} {tf} VERIFY coverage={cov_pct}% gaps={len(final_cov.gaps)} "
        f"status={result['status']}"
    )
    return result


async def sync_universe(
    rest: BinanceRestClient | None,
    *,
    symbols: Sequence[str],
    config: PipelineConfig,
    metrics: PipelineMetrics,
    run_id: str,
    listed_at_by_symbol: dict[str, int | None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bounded-worker incremental sync across symbols×timeframes."""
    if dry_run:
        return await dry_run_sync(
            symbols=symbols,
            config=config,
            listed_at_by_symbol=listed_at_by_symbol,
        )

    if rest is None:
        raise ValueError("BinanceRestClient required when dry_run=False")

    active = await count_active_data_sync_runs()
    concurrency_note = warn_global_concurrency(
        active, int(os.getenv("MAX_GLOBAL_RESEARCH_WORKERS", "8"))
    )
    if concurrency_note.get("warning"):
        _console(concurrency_note["warning"])
        metrics.notes.append(concurrency_note["warning"])

    ordered_tfs = sorted(
        config.timeframes,
        key=lambda t: _TF_PRIORITY.get(str(t).lower(), 99),
    )
    cells = [(s.upper(), tf) for tf in ordered_tfs for s in symbols]
    workers = max(1, min(int(config.max_workers), 8))
    # Env: RESEARCH_DOWNLOAD_WORKERS
    if os.getenv("RESEARCH_DOWNLOAD_WORKERS"):
        workers = max(1, min(8, int(os.environ["RESEARCH_DOWNLOAD_WORKERS"])))
    sem = asyncio.Semaphore(workers)
    metrics.peak_workers = max(metrics.peak_workers, workers)
    listed = listed_at_by_symbol or {}
    results: list[dict[str, Any]] = []
    lock = asyncio.Lock()

    async def _one(sym: str, tf: str) -> None:
        async with sem:
            if should_cancel and should_cancel():
                return
            r = await sync_series(
                rest,
                symbol=sym,
                timeframe=tf,
                config=config,
                metrics=metrics,
                run_id=run_id,
                listed_at_ms=listed.get(sym),
                should_cancel=should_cancel,
                dry_run=False,
            )
            async with lock:
                results.append(r)

    await asyncio.gather(*[_one(s, t) for s, t in cells])

    skipped = sum(1 for r in results if r.get("action") == ACTION_SKIP)
    changed = sum(1 for r in results if int(r.get("inserted") or 0) > 0)
    summary = build_sync_summary(results, metrics, symbols=symbols, timeframes=ordered_tfs)
    summary["concurrency"] = concurrency_note
    summary["series_results"] = results
    summary["symbols_skipped"] = skipped
    summary["symbols_changed"] = changed
    return summary


def build_sync_summary(
    results: Sequence[dict[str, Any]],
    metrics: PipelineMetrics,
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
) -> dict[str, Any]:
    existing = metrics.estimated_full_redownload_candles
    downloaded = metrics.actual_download_candles
    reduction = 0.0
    if existing > 0:
        reduction = round(100.0 * (1.0 - (downloaded / existing)), 2)
    inserted = sum(int(r.get("inserted") or 0) for r in results)
    gaps_before = sum(int(r.get("gaps_before") or 0) for r in results)
    gaps_after = sum(int(r.get("gaps_after") or 0) for r in results if r.get("gaps_after") is not None)
    elapsed = metrics.elapsed_seconds or 0.001
    rows_per_sec = round(inserted / elapsed, 2) if elapsed else 0.0

    print("", flush=True)
    _console("RESEARCH SYNC SUMMARY")
    _console(f"symbols_checked: {len(set(symbols))}")
    _console(f"timeframes_checked: {len(list(timeframes))}")
    _console(f"existing_candles(est full request): {existing:,}")
    _console(f"downloaded_candles: {downloaded:,}")
    _console(f"inserted_candles: {inserted:,}")
    _console(f"gaps_before: {gaps_before} gaps_after: {gaps_after}")
    _console(f"binance_requests: {metrics.binance_requests}")
    _console(f"download_reduction_percent: {reduction}%")
    _console(f"elapsed_seconds: {metrics.elapsed_seconds}")
    _console(f"average_rows_per_second: {rows_per_sec}")

    return {
        "status": "DONE",
        "mode": "sync",
        "dry_run": False,
        "symbols_checked": len(set(s.upper() for s in symbols)),
        "timeframes_checked": len(list(timeframes)),
        "existing_candles": existing,
        "downloaded_candles": downloaded,
        "inserted_candles": inserted,
        "conflicting_candles": metrics.data_conflicts,
        "gaps_before": gaps_before,
        "gaps_after": gaps_after,
        "binance_requests": metrics.binance_requests,
        "cache_hits": metrics.feature_cache_hits,
        "cache_misses": metrics.feature_cache_misses,
        "elapsed_seconds": metrics.elapsed_seconds,
        "average_rows_per_second": rows_per_sec,
        "estimated_full_redownload": existing,
        "actual_download": downloaded,
        "download_reduction_percent": reduction,
        "metrics": metrics.to_dict(),
    }
