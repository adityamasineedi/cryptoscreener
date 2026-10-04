"""Research feature cache — reuses production/signal engines, stores once.

Does not invent new trading logic. Currently caches ATR-like rolling stats
computed from OHLCV already in ResearchDataset (no second signal engine fork).
BOS/swing/event features live in ``event_cache`` wrapping existing builders.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from app.research.data_cache.cache_key import make_feature_cache_key
from app.research.data_cache.config import ResearchCacheConfig, load_research_cache_config
from app.research.data_cache.dataset import ResearchDataset
from app.research.data_cache.metrics import ResearchCacheMetrics


def _atr_wilder(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """Wilder ATR from OHLC — research helper only (not live SignalConfig)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pl.max_horizontal(
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    )
    # Approximate Wilder via EWM alpha=1/period
    atr = tr.ewm_mean(alpha=1.0 / period, adjust=False)
    return df.with_columns(
        [
            tr.alias("tr"),
            atr.alias(f"atr_{period}"),
            (df["volume"] / df["volume"].rolling_mean(window_size=20)).alias("rvol_20"),
            df["close"].ewm_mean(span=20, adjust=False).alias("ema_20"),
            df["high"].rolling_max(window_size=20).alias("roll_high_20"),
            df["low"].rolling_min(window_size=20).alias("roll_low_20"),
        ]
    )


class FeatureCache:
    """Parquet feature frames keyed by symbol×TF×dataset×feature version."""

    def __init__(
        self,
        config: ResearchCacheConfig | None = None,
        metrics: ResearchCacheMetrics | None = None,
    ) -> None:
        self.config = config or load_research_cache_config()
        self.metrics = metrics or ResearchCacheMetrics()

    def _paths(self, key: str) -> tuple[Path, Path]:
        root = self.config.feature_dir()
        return root / f"{key}.parquet", root / f"{key}.manifest.json"

    def ensure_features(self, dataset: ResearchDataset, *, force: bool = False) -> dict[str, Any]:
        key = make_feature_cache_key(
            symbol=dataset.symbol,
            timeframe=dataset.timeframe,
            start_time=dataset.start_time,
            end_time=dataset.end_time,
            dataset_version=dataset.dataset_version,
            feature_version=self.config.feature_version,
        )
        parquet_path, manifest_path = self._paths(key)
        if not force and parquet_path.is_file() and manifest_path.is_file():
            self.metrics.feature_cache_hits += 1
            frame = pl.read_parquet(parquet_path)
            return {
                "status": "CACHE_HIT",
                "cache_key": key,
                "parquet_path": str(parquet_path),
                "row_count": frame.height,
                "columns": frame.columns,
            }

        self.metrics.feature_cache_misses += 1
        t0 = time.monotonic()
        feat = _atr_wilder(dataset.frame)
        self.metrics.feature_seconds += time.monotonic() - t0
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        tw = time.monotonic()
        feat.write_parquet(parquet_path)
        self.metrics.write_seconds += time.monotonic() - tw
        man = {
            "feature_version": self.config.feature_version,
            "dataset_version": dataset.dataset_version,
            "symbol": dataset.symbol,
            "timeframe": dataset.timeframe,
            "start_time": dataset.start_time,
            "end_time": dataset.end_time,
            "row_count": feat.height,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "ohlcv_dataset_version": dataset.dataset_version,
                "ohlcv_cache_key": dataset.cache_key,
                "features": ["atr_14", "rvol_20", "ema_20", "roll_high_20", "roll_low_20"],
            },
            "note": (
                "Research-only derived columns from cached OHLCV. "
                "Does not replace production SignalEngine features. "
                "BOS/swing/CHOCH events use event_cache wrapping existing engines."
            ),
        }
        manifest_path.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
        return {
            "status": "CACHE_WRITTEN",
            "cache_key": key,
            "parquet_path": str(parquet_path),
            "row_count": feat.height,
            "columns": feat.columns,
            "manifest": man,
        }

    def load_feature_frame(self, dataset: ResearchDataset) -> pl.DataFrame:
        meta = self.ensure_features(dataset)
        return pl.read_parquet(meta["parquet_path"])
