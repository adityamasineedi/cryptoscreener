"""Pipeline runtime metrics (API requests, DB writes, memory, timing)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineMetrics:
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    binance_requests: int = 0
    binance_empty_pages: int = 0
    binance_retries: int = 0
    db_insert_attempts: int = 0
    db_rows_written: int = 0
    db_queries: int = 0
    candles_validated: int = 0
    chunks_completed: int = 0
    series_completed: int = 0
    series_failed: int = 0
    events_written: int = 0
    feature_cache_hits: int = 0
    feature_cache_misses: int = 0
    peak_workers: int = 0
    series_skipped: int = 0
    data_conflicts: int = 0
    lock_conflicts: int = 0
    estimated_full_redownload_candles: int = 0
    actual_download_candles: int = 0
    http_429: int = 0
    http_418: int = 0
    http_5xx: int = 0
    timeouts: int = 0
    notes: list[str] = field(default_factory=list)

    def mark_done(self) -> None:
        self.finished_at = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return round(end - self.started_at, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "binance_requests": self.binance_requests,
            "binance_empty_pages": self.binance_empty_pages,
            "binance_retries": self.binance_retries,
            "db_insert_attempts": self.db_insert_attempts,
            "db_rows_written": self.db_rows_written,
            "db_queries": self.db_queries,
            "candles_validated": self.candles_validated,
            "chunks_completed": self.chunks_completed,
            "series_completed": self.series_completed,
            "series_failed": self.series_failed,
            "events_written": self.events_written,
            "feature_cache_hits": self.feature_cache_hits,
            "feature_cache_misses": self.feature_cache_misses,
            "peak_workers": self.peak_workers,
            "series_skipped": self.series_skipped,
            "data_conflicts": self.data_conflicts,
            "lock_conflicts": self.lock_conflicts,
            "estimated_full_redownload_candles": self.estimated_full_redownload_candles,
            "actual_download_candles": self.actual_download_candles,
            "download_reduction_percent": (
                round(
                    100.0
                    * (
                        1.0
                        - (
                            self.actual_download_candles
                            / self.estimated_full_redownload_candles
                        )
                    ),
                    2,
                )
                if self.estimated_full_redownload_candles > 0
                else None
            ),
            "http_429": self.http_429,
            "http_418": self.http_418,
            "http_5xx": self.http_5xx,
            "timeouts": self.timeouts,
            "notes": self.notes,
        }


def estimate_naive_runtime_hours(
    *,
    symbols: int,
    timeframes: int,
    years: float = 6.0,
    seconds_per_series_year: float = 45.0,
) -> float:
    """Rough pre-pipeline estimate for naive sequential download+scan."""
    return round(symbols * timeframes * years * seconds_per_series_year / 3600.0, 2)
