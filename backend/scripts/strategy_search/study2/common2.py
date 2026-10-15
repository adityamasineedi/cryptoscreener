"""Study 2 (2026-10-14 brief): shared constants, windows, read-only data access.

New research work only. Reuses the read-only loader scripts/strategy_rank/multicap/_db.py and the
classifier calls used by scripts/strategy_search/regimes_ext.py. Nothing under backend/app or
scripts/sleeve_study is edited; the simulator is a copy (sim2.py, changes listed in SIM_CHANGES.md).
"""
from __future__ import annotations

import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
for p in (str(BACKEND), str(BACKEND / "scripts" / "strategy_rank" / "multicap"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT = HERE / "out"
CACHE = OUT / "cache"
UTC = timezone.utc
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
TFS = ["15m", "1h", "4h", "1d"]

# Fixed V1_SYMBOLS universe (current-universe retrospective). The 11 coins with bars only from
# 2024-10-09 cannot be evaluated in research folds F1-F3 and are excluded (DATA_MANIFEST.json).
UNIVERSE = ["ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ETHUSDT",
            "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT", "XRPUSDT"]
LATE = ["AAVEUSDT", "ATOMUSDT", "BCHUSDT", "ETCUSDT", "FILUSDT", "ICPUSDT", "INJUSDT", "RENDERUSDT", "TONUSDT",
        "TRXUSDT", "UNIUSDT"]
# 15m setups only where stored 15m covers the whole common window (others start 2023-09 .. 2024-10)
COINS_15M = ["APTUSDT", "AVAXUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT", "ETHUSDT", "SOLUSDT"]

D = lambda y, m, d: datetime(y, m, d, tzinfo=UTC)  # noqa: E731
COMMON = (D(2023, 6, 1), D(2026, 10, 13))          # 1230 days; bars before 2023-06-01 = warm-up only
RESEARCH = (D(2023, 6, 1), D(2025, 9, 1))          # 823 d (66.9%)
VALIDATION = (D(2025, 9, 1), D(2026, 3, 24))       # 204 d (16.6%)
TEST = (D(2026, 3, 24), D(2026, 10, 13))           # 203 d (16.5%) - previously inspected, see EXPOSURE_LEDGER.json
FOLDS = [("F1", D(2023, 6, 1), D(2024, 1, 1)), ("F2", D(2024, 1, 1), D(2024, 8, 1)),
         ("F3", D(2024, 8, 1), D(2025, 2, 1)), ("F4", D(2025, 2, 1), D(2025, 9, 1))]
WINDOWS = {"research": RESEARCH, "validation": VALIDATION, "test": TEST}
DATA_FROM = D(2022, 6, 1)  # earliest bar loaded for warm-up (stored history starts 2022-10 / 2023-01 anyway)


def candles(coin: str, tf: str, start: datetime = DATA_FROM, end: datetime = COMMON[1]) -> list[dict]:
    """Read-only OHLCV (asyncpg session with default_transaction_read_only=on), pickle-cached."""
    from _db import load_candles
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{coin}_{tf}_{start:%Y%m%d}_{end:%Y%m%d}.pkl"
    if f.exists():
        return pickle.loads(f.read_bytes())
    c = load_candles(coin, tf, start, end)
    f.write_bytes(pickle.dumps(c))
    return c


def hourly(coin: str, end: datetime = COMMON[1]) -> list[dict]:
    """Stored 1h bars; before the first stored 1h bar, 1h bars built from complete groups of four stored
    15m bars (AVAXUSDT 1h starts 2024-04-04 while its 15m starts 2023-01-01). Incomplete hours are dropped."""
    h = candles(coin, "1h", DATA_FROM, end)
    first = h[0]["time"] if h else end
    if first <= COMMON[0] - timedelta(days=30):
        return h
    agg: dict = {}
    for x in candles(coin, "15m", DATA_FROM, end):
        if x["time"] >= first:
            break
        k = x["time"].replace(minute=0)
        a = agg.setdefault(k, dict(time=k, open=x["open"], high=x["high"], low=x["low"], close=x["close"],
                                   volume=0.0, n=0, _last=-1))
        a["high"] = max(a["high"], x["high"]); a["low"] = min(a["low"], x["low"])
        a["close"] = x["close"]; a["volume"] += x["volume"]; a["n"] += 1
    built = [{k: v for k, v in a.items() if k not in ("n", "_last")} for _, a in sorted(agg.items()) if a["n"] == 4]
    return built + h


def ts(dt: datetime) -> int:
    return int(dt.timestamp())
