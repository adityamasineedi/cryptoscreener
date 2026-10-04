"""Parquet read/write for research OHLCV (Polars). No synthetic fills."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import polars as pl

_SCHEMA_COLS = ("timestamp", "open", "high", "low", "close", "volume")


def candles_to_frame(candles: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for c in candles:
        raw = c.get("time") or c.get("timestamp") or c.get("open_time")
        if isinstance(raw, datetime):
            ts = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        elif isinstance(raw, (int, float)):
            v = float(raw)
            if v > 1e12:
                v /= 1000.0
            ts = datetime.fromtimestamp(v, tz=timezone.utc)
        elif isinstance(raw, str):
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            continue
        rows.append(
            {
                "timestamp": ts.astimezone(timezone.utc).replace(tzinfo=None),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume") or 0.0),
            }
        )
    if not rows:
        return pl.DataFrame(
            schema={
                "timestamp": pl.Datetime("us"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )
    return pl.DataFrame(rows).sort("timestamp")


def frame_to_candles(df: pl.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in df.select(list(_SCHEMA_COLS)).iter_rows(named=True):
        ts = row["timestamp"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
        out.append(
            {
                "time": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"] or 0.0),
            }
        )
    return out


def write_ohlcv_parquet(path: Path, candles: Sequence[Mapping[str, Any]]) -> str:
    """Write Parquet; return sha256 of file bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = candles_to_frame(candles)
    df.write_parquet(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def read_ohlcv_parquet(path: Path) -> pl.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    df = pl.read_parquet(path)
    missing = [c for c in _SCHEMA_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"parquet_schema_missing:{missing}")
    return df.select(list(_SCHEMA_COLS)).sort("timestamp")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
