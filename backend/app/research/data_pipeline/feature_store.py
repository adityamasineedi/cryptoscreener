"""Precompute research features by wrapping production signal engines.

Does NOT reimplement BOS / swing / impulse / pullback / retest.
Caches by (symbol, timeframe, feature_version, data_fingerprint).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.data_pipeline.config import FEATURE_VERSION, PipelineConfig
from app.research.data_pipeline.metrics import PipelineMetrics
from app.research.data_pipeline import repository as repo
from app.research.postgres_ohlcv import load_ohlcv_series_range

logger = get_logger("research_data_pipeline.feature_store")


def series_data_fingerprint(
    symbol: str,
    timeframe: str,
    first: datetime | None,
    last: datetime | None,
    bar_count: int,
    *,
    gap_count: int = 0,
    duplicate_count: int = 0,
) -> str:
    """Deterministic fingerprint from durable DB stats (not process-local state).

    Changes when candles are inserted/repaired or conflict markers change counts.
    Does NOT change merely because a sync job ran with no data changes.
    """
    raw = (
        f"{symbol.upper()}|{timeframe}|{first}|{last}|{bar_count}|"
        f"gaps={gap_count}|dup={duplicate_count}|{FEATURE_VERSION}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


async def ensure_features_for_symbol(
    *,
    symbol: str,
    config: PipelineConfig,
    dataset_version: str,
    metrics: PipelineMetrics,
    research_cfg: StrategyResearchConfig | None = None,
) -> dict[str, Any]:
    """Load MTF candles, build BOS events via production engines, cache metadata."""
    from app.research.data_pipeline.event_store import build_bos_events_for_symbol

    sym = symbol.upper()
    setup_tf = config.setup_timeframe
    first, last, bars = await repo.series_bounds(sym, setup_tf)
    metrics.db_queries += 1
    if first is None or bars < 50:
        return {
            "symbol": sym,
            "status": "INSUFFICIENT_DATA",
            "events": 0,
            "bars": bars,
        }

    fp = series_data_fingerprint(sym, setup_tf, first, last, bars)
    if await repo.feature_cache_hit(sym, setup_tf, FEATURE_VERSION, fp):
        metrics.feature_cache_hits += 1
        metrics.db_queries += 1
        return {
            "symbol": sym,
            "status": "CACHE_HIT",
            "feature_version": FEATURE_VERSION,
            "data_fingerprint": fp,
            "events": -1,
            "bars": bars,
        }
    metrics.feature_cache_misses += 1
    metrics.db_queries += 1

    period_start = datetime.fromisoformat(config.period_start).replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    rcfg = research_cfg or StrategyResearchConfig()

    candles_by_tf: dict[str, list[dict[str, Any]]] = {}
    needed = {setup_tf, config.entry_timeframe, *config.htf_timeframes}
    for tf in needed:
        candles_by_tf[tf] = await load_ohlcv_series_range(
            sym,
            tf,
            start=period_start,
            end_exclusive=end,
            warmup_bars=200,
        )
        metrics.db_queries += 1

    events = build_bos_events_for_symbol(
        symbol=sym,
        candles_by_tf=candles_by_tf,
        config=config,
        dataset_version=dataset_version,
        research_cfg=rcfg,
    )
    n = await repo.insert_bos_events(events)
    metrics.events_written += n
    metrics.db_queries += 1

    await repo.upsert_feature_cache(
        {
            "symbol": sym,
            "timeframe": setup_tf,
            "feature_version": FEATURE_VERSION,
            "data_fingerprint": fp,
            "first_time": first,
            "last_time": last,
            "bar_count": bars,
            "status": "COMPLETE",
            "payload": {
                "dataset_version": dataset_version,
                "events_written": n,
                "events_detected": len(events),
                "timeframes": sorted(needed),
            },
        }
    )
    metrics.db_queries += 1
    return {
        "symbol": sym,
        "status": "COMPUTED",
        "feature_version": FEATURE_VERSION,
        "data_fingerprint": fp,
        "events": n,
        "events_detected": len(events),
        "bars": bars,
    }
