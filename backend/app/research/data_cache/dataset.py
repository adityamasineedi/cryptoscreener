"""In-memory ResearchDataset views (Polars + optional NumPy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import polars as pl

from app.research.data_cache.parquet_store import candles_to_frame, frame_to_candles


@dataclass
class ResearchDataset:
    """Reusable OHLCV bundle for research backtests (load once, run many)."""

    symbol: str
    timeframe: str
    start_time: str | None
    end_time: str | None
    dataset_version: str
    frame: pl.DataFrame
    source: str = "parquet_cache"
    cache_key: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_candles(
        cls,
        *,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        start_time: str | None,
        end_time: str | None,
        dataset_version: str,
        source: str = "memory",
        cache_key: str | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> ResearchDataset:
        return cls(
            symbol=symbol.upper(),
            timeframe=timeframe.lower(),
            start_time=start_time,
            end_time=end_time,
            dataset_version=dataset_version,
            frame=candles_to_frame(candles),
            source=source,
            cache_key=cache_key,
            manifest=dict(manifest or {}),
        )

    @property
    def row_count(self) -> int:
        return int(self.frame.height)

    @property
    def timestamp(self) -> pl.Series:
        return self.frame["timestamp"]

    @property
    def open(self) -> pl.Series:
        return self.frame["open"]

    @property
    def high(self) -> pl.Series:
        return self.frame["high"]

    @property
    def low(self) -> pl.Series:
        return self.frame["low"]

    @property
    def close(self) -> pl.Series:
        return self.frame["close"]

    @property
    def volume(self) -> pl.Series:
        return self.frame["volume"]

    def as_candles(self) -> list[dict[str, Any]]:
        """Engine-compatible candle dicts (``time`` + OHLCV)."""
        return frame_to_candles(self.frame)

    def numpy_ohlcv(self) -> dict[str, Any] | None:
        """Optional NumPy views when numpy is importable."""
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            return None
        return {
            "open": self.open.to_numpy(),
            "high": self.high.to_numpy(),
            "low": self.low.to_numpy(),
            "close": self.close.to_numpy(),
            "volume": self.volume.to_numpy(),
            "timestamp_ns": self.timestamp.dt.epoch(time_unit="ns").to_numpy(),
        }

    def estimate_memory_mb(self) -> float:
        # Rough: 6 float64 cols + datetime ≈ 56 bytes/row
        return round(self.row_count * 56 / (1024 * 1024), 3)

    def slice_as_of(self, as_of: datetime) -> ResearchDataset:
        """No-lookahead helper: drop all bars with timestamp > as_of."""
        cutoff = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
        cutoff_naive = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        clipped = self.frame.filter(pl.col("timestamp") <= cutoff_naive)
        return ResearchDataset(
            symbol=self.symbol,
            timeframe=self.timeframe,
            start_time=self.start_time,
            end_time=as_of.date().isoformat(),
            dataset_version=self.dataset_version,
            frame=clipped,
            source=self.source,
            cache_key=self.cache_key,
            manifest={**self.manifest, "as_of": cutoff.isoformat()},
        )


@dataclass
class PreparedResearchBundle:
    """Multi symbol×TF prepared datasets for reuse across strategies."""

    datasets: dict[tuple[str, str], ResearchDataset]
    start_time: str | None
    end_time: str | None
    dataset_version: str
    metrics: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None

    def get(self, symbol: str, timeframe: str) -> ResearchDataset | None:
        return self.datasets.get((symbol.upper(), timeframe.lower()))

    def keys(self) -> list[tuple[str, str]]:
        return list(self.datasets.keys())
