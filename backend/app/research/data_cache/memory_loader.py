"""Memory loader helpers — re-exports ResearchDataset construction."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.data_cache.dataset import ResearchDataset
from app.research.data_cache.parquet_store import read_ohlcv_parquet, frame_to_candles
from pathlib import Path


def load_dataset_from_parquet(
    *,
    parquet_path: Path | str,
    symbol: str,
    timeframe: str,
    start_time: str | None,
    end_time: str | None,
    dataset_version: str,
    cache_key: str | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> ResearchDataset:
    df = read_ohlcv_parquet(Path(parquet_path))
    return ResearchDataset(
        symbol=symbol.upper(),
        timeframe=timeframe.lower(),
        start_time=start_time,
        end_time=end_time,
        dataset_version=dataset_version,
        frame=df,
        source="parquet_file",
        cache_key=cache_key,
        manifest=dict(manifest or {}),
    )


def candles_from_parquet(parquet_path: Path | str) -> list[dict[str, Any]]:
    return frame_to_candles(read_ohlcv_parquet(Path(parquet_path)))


def dataset_from_candles(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    start_time: str | None,
    end_time: str | None,
    dataset_version: str,
) -> ResearchDataset:
    return ResearchDataset.from_candles(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        start_time=start_time,
        end_time=end_time,
        dataset_version=dataset_version,
    )
