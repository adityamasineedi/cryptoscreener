"""Cache hit/miss and timing metrics for the research data layer."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResearchCacheMetrics:
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    cache_hits: int = 0
    cache_misses: int = 0
    postgres_queries: int = 0
    rows_loaded: int = 0
    parquet_rows_loaded: int = 0
    feature_cache_hits: int = 0
    feature_cache_misses: int = 0
    event_cache_hits: int = 0
    event_cache_misses: int = 0
    db_seconds: float = 0.0
    cache_load_seconds: float = 0.0
    feature_seconds: float = 0.0
    event_seconds: float = 0.0
    strategy_seconds: float = 0.0
    write_seconds: float = 0.0
    peak_memory_mb: float | None = None

    def mark_done(self) -> None:
        self.finished_at = time.monotonic()

    @property
    def processing_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return round(end - self.started_at, 3)

    @property
    def cache_hit_ratio(self) -> float | None:
        total = self.cache_hits + self.cache_misses
        if total <= 0:
            return None
        return round(self.cache_hits / total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processing_seconds": self.processing_seconds,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_ratio": self.cache_hit_ratio,
            "postgres_queries": self.postgres_queries,
            "rows_loaded": self.rows_loaded,
            "parquet_rows_loaded": self.parquet_rows_loaded,
            "feature_cache_hits": self.feature_cache_hits,
            "feature_cache_misses": self.feature_cache_misses,
            "event_cache_hits": self.event_cache_hits,
            "event_cache_misses": self.event_cache_misses,
            "db_seconds": round(self.db_seconds, 3),
            "cache_load_seconds": round(self.cache_load_seconds, 3),
            "feature_seconds": round(self.feature_seconds, 3),
            "event_seconds": round(self.event_seconds, 3),
            "strategy_seconds": round(self.strategy_seconds, 3),
            "write_seconds": round(self.write_seconds, 3),
            "peak_memory_mb": self.peak_memory_mb,
        }


def sample_rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except Exception:  # noqa: BLE001
        return None
