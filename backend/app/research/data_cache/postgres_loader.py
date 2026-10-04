"""Bulk PostgreSQL → candle list for research cache (one query per series)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.research.data_cache.metrics import ResearchCacheMetrics
from app.research.postgres_ohlcv import load_ohlcv_series_range
from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe


async def bulk_load_ohlcv_from_postgres(
    *,
    symbol: str,
    timeframe: str,
    start_time: datetime | None,
    end_time: datetime | None,
    metrics: ResearchCacheMetrics | None = None,
) -> list[dict[str, Any]]:
    """ONE bulk range query via existing ``load_ohlcv_series_range``.

    No candle-by-candle SELECT loops.
    """
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    start = start_time
    end = end_time
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    t0 = time.monotonic()
    candles = await load_ohlcv_series_range(
        sym,
        tf,
        start=start,
        end_exclusive=end,
        warmup_bars=0,
    )
    elapsed = time.monotonic() - t0
    if metrics is not None:
        metrics.postgres_queries += 1
        metrics.db_seconds += elapsed
        metrics.rows_loaded += len(candles)
    return candles
