"""Strategy search (2026-10-14 brief): shared constants, guarded data access, regime labels.

Reuses the sleeve-study simulator (scripts/sleeve_study/sim.py) and read-only loader
(scripts/strategy_rank/multicap/_db.py). Holdout coins cannot be loaded until a FREEZE_*.json exists.
"""
from __future__ import annotations

import glob
import json
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
SLEEVE = BACKEND / "scripts" / "sleeve_study"
for p in (str(BACKEND), str(BACKEND / "scripts" / "strategy_rank" / "multicap"), str(SLEEVE), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT = HERE / "out"
CACHE = OUT / "cache"
UTC = timezone.utc

DISCOVERY = ["ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ETHUSDT",
             "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT", "XRPUSDT"]
HOLDOUT = ["AAVEUSDT", "ATOMUSDT", "BCHUSDT", "ETCUSDT", "FILUSDT", "ICPUSDT", "INJUSDT", "RENDERUSDT", "TONUSDT",
           "TRXUSDT", "UNIUSDT"]
D_WIN = (datetime(2023, 6, 1, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC))   # through 2026-10-08
H_WIN = (datetime(2024, 10, 9, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC))  # through 2026-10-08
FOLDS = [("F1", datetime(2023, 6, 1, tzinfo=UTC), datetime(2024, 4, 1, tzinfo=UTC)),
         ("F2", datetime(2024, 4, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)),
         ("F3", datetime(2025, 2, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC)),
         ("F4", datetime(2025, 12, 1, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC))]
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
WARMUP_DAYS = {"15m": 30, "1h": 90, "4h": 250, "1d": 400}


def frozen() -> bool:
    return bool(glob.glob(str(HERE / "FREEZE_*.json")))


def _guard(coin: str) -> None:
    if coin in HOLDOUT and not frozen():
        raise RuntimeError(f"holdout coin {coin} requested before a FREEZE_*.json exists")


def candles(coin: str, tf: str, start: datetime, end: datetime) -> list[dict]:
    """Read-only OHLCV with a pickle cache keyed by coin/tf/start/end."""
    _guard(coin)
    from _db import load_candles
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{coin}_{tf}_{start:%Y%m%d}_{end:%Y%m%d}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    c = load_candles(coin, tf, start, end)
    f.write_bytes(pickle.dumps(c))
    return c


def universe(name: str) -> tuple[list[str], tuple[datetime, datetime]]:
    if name == "discovery":
        return DISCOVERY, D_WIN
    if name == "holdout":
        if not frozen():
            raise RuntimeError("holdout requested before freeze")
        return HOLDOUT, H_WIN
    raise ValueError(name)
