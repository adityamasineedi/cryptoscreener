"""Chunked Binance USDⓈ-M Futures historical kline downloader.

Uses the shared BinanceRestClient rate limiter. Writes via ON CONFLICT DO NOTHING.
Never fabricates candles. Never mutates the live in-memory OHLCV store.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from app.core.logging import get_logger
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe
from app.ingestion.symbol_discovery import SymbolDiscovery
from app.research.data_pipeline.checkpoint import SeriesCheckpoint
from app.research.data_pipeline.config import PipelineConfig
from app.research.data_pipeline.deduplicator import dedupe_rows
from app.research.data_pipeline.metrics import PipelineMetrics
from app.research.data_pipeline.normalizer import normalize_kline_row
from app.research.data_pipeline.paginator import (
    TimeChunk,
    page_windows_within_chunk,
)
from app.research.data_pipeline import repository as repo
from app.research.data_pipeline.validator import filter_valid_rows, validate_candles
from app.services.market_store import MarketDataStore

# Prefer expanding the research bottleneck first.
_TF_PRIORITY = {"5m": 0, "15m": 1, "1h": 2, "4h": 3, "1d": 4, "1m": 5}

logger = get_logger("research_data_pipeline.downloader")


async def discover_perpetual_symbols(
    rest: BinanceRestClient,
    *,
    quote: str = "USDT",
) -> list[dict[str, Any]]:
    """Dynamic Binance USDⓈ-M perpetual discovery (no hardcoded list)."""
    store = MarketDataStore()
    discovery = SymbolDiscovery(rest, store, quote=quote)
    symbols = await discovery.refresh()
    out: list[dict[str, Any]] = []
    for s in symbols:
        out.append(
            {
                "symbol": s.symbol,
                "status": s.status,
                "contract_type": s.contract_type,
                "quote_asset": s.quote_asset,
                "base_asset": s.base_asset,
                "market_type": s.market_type,
                "listed_at": getattr(s, "listed_at", None),
            }
        )
    return out


async def download_series(
    rest: BinanceRestClient,
    *,
    symbol: str,
    timeframe: str,
    config: PipelineConfig,
    metrics: PipelineMetrics,
    should_cancel: Callable[[], bool] | None = None,
) -> SeriesCheckpoint:
    """Download one symbol×timeframe using DB-first missing-range planning.

    Prefer ``--mode sync`` for the full incremental engine (locks, dry-run, summary).
    This path still audits Postgres before any download and never trusts manifest alone.
    """
    from app.research.data_pipeline.coverage import audit_series_coverage
    from app.research.data_pipeline.planner import ACTION_SKIP, ACTION_UNAVAILABLE, plan_from_coverage
    from app.research.data_pipeline.sync import _download_range

    sym = symbol.upper()
    tf = normalize_timeframe(timeframe)
    if tf not in TIMEFRAME_MS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    period_end = config.resolved_period_end()
    existing = await repo.load_manifest(sym, tf)
    metrics.db_queries += 1

    # DB is source of truth — plan missing ranges even if manifest says COMPLETE
    cov = await audit_series_coverage(
        sym,
        tf,
        start_day=config.period_start,
        end_day=period_end,
        detect_internal_gaps=True,
        manifest_status=existing.status if existing else None,
    )
    metrics.db_queries += 1
    plan = plan_from_coverage(cov, kline_limit=config.kline_limit)

    cp = existing or SeriesCheckpoint(
        symbol=sym,
        timeframe=tf,
        requested_start=config.period_start,
        requested_end=period_end,
    )
    cp.requested_start = config.period_start
    cp.requested_end = period_end

    if plan.action in (ACTION_SKIP, ACTION_UNAVAILABLE):
        if cov.existing_min_ms is not None:
            cp.actual_first = datetime.fromtimestamp(
                cov.existing_min_ms / 1000, tz=timezone.utc
            ).date().isoformat()
            cp.actual_last = (
                datetime.fromtimestamp(
                    cov.existing_max_ms / 1000, tz=timezone.utc
                ).date().isoformat()
                if cov.existing_max_ms
                else None
            )
            cp.candles_downloaded = cov.row_count
            cp.gap_count = len(cov.gaps)
            cp.duplicate_count = cov.duplicate_count
            cp.invalid_rows = cov.invalid_ohlc_count
            if plan.action == ACTION_SKIP:
                cp.mark_complete()
            else:
                cp.mark_no_history()
        else:
            cp.mark_no_history()
        await repo.upsert_manifest(cp)
        metrics.db_queries += 1
        metrics.series_completed += 1
        metrics.series_skipped += 1
        return cp

    cp.mark_downloading()
    await repo.upsert_manifest(cp)
    metrics.db_queries += 1

    written_total = 0
    pages = 0
    run_id = f"legacy-download-{sym}-{tf}"

    try:
        for rng in plan.ranges:
            if should_cancel and should_cancel():
                cp.mark_partial("cancelled")
                await repo.upsert_manifest(cp)
                return cp
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
            written_total += stats["rows_inserted"]
            pages += stats["requests"]
            cp.mark_chunk_done(0, rng.end_ms, stats["rows_inserted"])
            await repo.upsert_manifest(cp)
            metrics.db_queries += 1
            metrics.chunks_completed += 1
            if pages >= config.max_pages_per_series:
                cp.mark_partial("max_pages_reached")
                break

        first, last, bars = await repo.series_bounds(sym, tf)
        metrics.db_queries += 1
        if first is None:
            cp.mark_no_history()
        else:
            cp.actual_first = first.date().isoformat()
            cp.actual_last = last.date().isoformat() if last else None
            cp.candles_downloaded = bars
            from app.research.postgres_ohlcv import load_ohlcv_series_range

            sample = await load_ohlcv_series_range(
                sym,
                tf,
                start=datetime.fromisoformat(config.period_start).replace(
                    tzinfo=timezone.utc
                ),
                end_exclusive=datetime.now(timezone.utc),
            )
            metrics.db_queries += 1
            sample_rows = sample[-50_000:] if len(sample) > 50_000 else sample
            report = validate_candles(sample_rows, symbol=sym, timeframe=tf)
            metrics.candles_validated += report.candle_count
            cp.quality = report.quality
            cp.gap_count = len(report.gaps)
            cp.duplicate_count = report.duplicate_count
            cp.invalid_rows = report.invalid_ohlc
            if report.gaps:
                await repo.save_gap_reports(
                    sym, tf, [g.to_dict() for g in report.gaps[:200]]
                )
                metrics.db_queries += 1
            if cp.status != "PARTIAL":
                cp.mark_complete()
        await repo.upsert_manifest(cp)
        metrics.db_queries += 1
        metrics.series_completed += 1
        return cp
    except Exception as exc:  # noqa: BLE001
        logger.warning("series_download_failed", symbol=sym, timeframe=tf, error=str(exc))
        cp.mark_failed(str(exc))
        await repo.upsert_manifest(cp)
        metrics.db_queries += 1
        metrics.series_failed += 1
        return cp


async def _download_chunk(
    rest: BinanceRestClient,
    chunk: TimeChunk,
    *,
    config: PipelineConfig,
    metrics: PipelineMetrics,
    pages_so_far: int,
    should_cancel: Callable[[], bool] | None,
) -> tuple[int, int]:
    written = 0
    pages = pages_so_far
    for start_ms, end_ms in page_windows_within_chunk(
        chunk, kline_limit=config.kline_limit
    ):
        if should_cancel and should_cancel():
            break
        if pages >= config.max_pages_per_series:
            break
        raw = await rest.futures_klines(
            chunk.symbol,
            chunk.timeframe,
            limit=config.kline_limit,
            start_time=start_ms,
            end_time=end_ms - 1,
        )
        metrics.binance_requests += 1
        pages += 1
        if not raw:
            metrics.binance_empty_pages += 1
            await asyncio.sleep(config.page_pause_seconds)
            continue
        rows: list[dict[str, Any]] = []
        for row in raw:
            norm = normalize_kline_row(
                chunk.symbol,
                chunk.timeframe,
                row,
                source=config.write_source_tag,
            )
            if norm is not None:
                rows.append(norm)
        rows = dedupe_rows(rows)
        good, _reasons = filter_valid_rows(rows)
        report = validate_candles(
            good, symbol=chunk.symbol, timeframe=chunk.timeframe
        )
        metrics.candles_validated += report.candle_count
        # Persist only valid rows; gaps recorded at series level — never fabricate
        if good:
            metrics.db_insert_attempts += 1
            n = await repo.insert_ohlcv_do_nothing(
                good, batch_size=config.batch_insert_size
            )
            metrics.db_rows_written += n
            metrics.db_queries += 1
            written += n
        await asyncio.sleep(config.page_pause_seconds)
    return written, pages


async def download_universe(
    rest: BinanceRestClient,
    *,
    symbols: Sequence[str],
    config: PipelineConfig,
    metrics: PipelineMetrics,
    should_cancel: Callable[[], bool] | None = None,
) -> list[SeriesCheckpoint]:
    """Parallelize at symbol×timeframe with a bounded worker pool.

    Timeframes are ordered so 5m (research bottleneck) is scheduled first.
    """
    ordered_tfs = sorted(
        config.timeframes,
        key=lambda t: _TF_PRIORITY.get(str(t).lower(), 99),
    )
    cells = [(s.upper(), tf) for tf in ordered_tfs for s in symbols]
    sem = asyncio.Semaphore(max(1, config.max_workers))
    metrics.peak_workers = max(metrics.peak_workers, config.max_workers)
    results: list[SeriesCheckpoint] = []
    lock = asyncio.Lock()

    async def _one(sym: str, tf: str) -> None:
        async with sem:
            if should_cancel and should_cancel():
                return
            cp = await download_series(
                rest,
                symbol=sym,
                timeframe=tf,
                config=config,
                metrics=metrics,
                should_cancel=should_cancel,
            )
            async with lock:
                results.append(cp)

    await asyncio.gather(*[_one(s, t) for s, t in cells])
    return results
