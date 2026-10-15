"""Shared constants and data access for the structure-strategy study (docs/research_instructions.md).

Locked 2026-10-15 (Phase 1 approval): split, universe tiers, costs. Bars are read-only from Postgres `ohlcv`
and cached under research/out/cache. Any read reaching the OOS segment raises unless RESEARCH_ALLOW_OOS=1
(set only in Phase 6).
"""
from __future__ import annotations

import os
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "scripts" / "strategy_rank" / "multicap"))

OUT = ROOT / "research" / "out"
CACHE = OUT / "cache"
UTC = timezone.utc

# ---- locked split (inclusive dates; a segment ends at 00:00 UTC of the day after its last date)
DEV = (datetime(2022, 10, 16, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC))
VAL = (datetime(2025, 4, 8, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
OOS = (datetime(2026, 1, 8, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC))
SEGMENTS = {"dev": DEV, "val": VAL, "oos": OOS}

# ---- locked universe
CORE = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIER2 = ["BNBUSDT", "XRPUSDT", "ADAUSDT", "APTUSDT", "AVAXUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT",
         "NEARUSDT", "OPUSDT"]

# ---- locked costs
TAKER = 0.0005
SLIP = 0.0003
FUNDING_8H = 0.0001  # charged to both sides

TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
WARMUP = timedelta(days=200)  # history loaded before a window for indicator warm-up (4h ATR100, daily z)


def _guard(end: datetime) -> None:
    if end > OOS[0] and os.environ.get("RESEARCH_ALLOW_OOS") != "1":
        raise RuntimeError(f"read up to {end:%Y-%m-%d} reaches the OOS segment (starts {OOS[0]:%Y-%m-%d}); "
                           "only Phase 6 may set RESEARCH_ALLOW_OOS=1")


def _stored(sym: str, tf: str, start: datetime, end: datetime) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{sym}_{tf}_{start:%Y%m%d}_{end:%Y%m%d}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    from _db import load_candles  # read-only asyncpg session
    df = pd.DataFrame(load_candles(sym, tf, start, end))
    if df.empty:
        df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
    f.write_bytes(pickle.dumps(df))
    return df


def _aggregate(lo: pd.DataFrame, hi_sec: int, lo_sec: int) -> pd.DataFrame:
    """Complete higher-TF buckets (open-time convention) built from a lower-TF series."""
    if lo.empty:
        return lo.copy()
    b = lo["time"].dt.floor(f"{hi_sec}s")
    g = lo.groupby(b)
    agg = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "n": g.size()})
    agg = agg[agg["n"] == hi_sec // lo_sec].drop(columns="n")
    return agg.rename_axis("time").reset_index()


def _stitch(built: pd.DataFrame, stored: pd.DataFrame) -> pd.DataFrame:
    """Prefer bars built from the base series; fall back to stored bars where no complete bucket exists."""
    if built.empty:
        return stored.reset_index(drop=True)
    extra = stored[~stored["time"].isin(built["time"])]
    return pd.concat([built, extra]).sort_values("time").reset_index(drop=True)


def bars(sym: str, start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    """15m (stored only), 1h, 4h, 1d for [start - WARMUP, end). 1h is built from 15m where 15m exists,
    4h from 1h, 1d from 4h; stored bars fill buckets the base series cannot complete."""
    _guard(end)
    s0 = start - WARMUP
    m15 = _stored(sym, "15m", s0, end)
    h1 = _stitch(_aggregate(m15, 3600, 900), _stored(sym, "1h", s0, end))
    h4 = _stitch(_aggregate(h1, 14400, 3600), _stored(sym, "4h", s0, end))
    d1 = _aggregate(h4, 86400, 14400)
    return {"15m": m15, "1h": h1, "4h": h4, "1d": d1}


def epoch_s(s: pd.Series) -> np.ndarray:
    return ((s - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy()
