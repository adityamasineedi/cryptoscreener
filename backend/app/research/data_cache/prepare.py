"""Prepare research datasets once for reuse across strategies."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Sequence

from app.research.data_cache.cache_manager import ResearchCacheManager, get_research_cache
from app.research.data_cache.config import ResearchCacheConfig, load_research_cache_config
from app.research.data_cache.dataset import PreparedResearchBundle, ResearchDataset
from app.research.data_cache.feature_cache import FeatureCache
from app.research.data_cache.metrics import sample_rss_mb
from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe


async def prepare_research_dataset(
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    start_time: str | datetime | None = None,
    end_time: str | datetime | None = None,
    config: ResearchCacheConfig | None = None,
    force_refresh: bool = False,
    build_features: bool = True,
    max_concurrency: int | None = None,
) -> PreparedResearchBundle:
    """LOAD ONCE: bulk PG (on miss) → Parquet → memory for each symbol×TF."""
    cfg = config or load_research_cache_config()
    cache = get_research_cache(cfg)
    cache.reset_metrics()
    sem = asyncio.Semaphore(max_concurrency or cfg.db_max_concurrency)
    datasets: dict[tuple[str, str], ResearchDataset] = {}
    errors: list[str] = []

    async def _one(sym: str, tf: str) -> None:
        async with sem:
            try:
                ds = await cache.load(
                    symbol=sym,
                    timeframe=tf,
                    start=start_time,
                    end=end_time,
                    force_refresh=force_refresh,
                )
                if build_features:
                    FeatureCache(cfg, cache.metrics).ensure_features(ds)
                datasets[(ds.symbol, ds.timeframe)] = ds
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sym}/{tf}:{exc}")

    tasks = [
        _one(normalize_research_symbol(s), normalize_research_timeframe(t))
        for s in symbols
        for t in timeframes
    ]
    await asyncio.gather(*tasks)
    cache.metrics.mark_done()
    peak = sample_rss_mb()
    if peak is not None:
        cache.metrics.peak_memory_mb = max(cache.metrics.peak_memory_mb or 0.0, peak)

    start_s = None if start_time is None else str(start_time)[:10]
    end_s = None if end_time is None else str(end_time)[:10]
    return PreparedResearchBundle(
        datasets=datasets,
        start_time=start_s,
        end_time=end_s,
        dataset_version=cfg.dataset_version,
        metrics={**cache.metrics.to_dict(), "errors": errors},
        run_id=uuid.uuid4().hex[:12],
    )


async def load_ohlcv_to_cache(
    symbol: str,
    timeframe: str,
    start_time: str | datetime | None,
    end_time: str | datetime | None,
    *,
    force_refresh: bool = False,
    config: ResearchCacheConfig | None = None,
) -> dict[str, Any]:
    """Public helper matching the task API name."""
    return await get_research_cache(config).load_ohlcv_to_cache(
        symbol, timeframe, start_time, end_time, force_refresh=force_refresh
    )
