"""Shared constants for the sleeve study (current-universe retrospective). Research only."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
for p in (str(BACKEND), str(BACKEND / "scripts" / "strategy_rank" / "multicap"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

UTC = timezone.utc
# Universe: the 16 of the 27 V1_SYMBOLS whose 1d/4h/1h history covers the study window
# (user decision 2026-10-14). The other 11 start 2024-10-09 and are excluded.
STUDY_SYMBOLS = ["ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT", "DOTUSDT",
                 "ETHUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT", "XRPUSDT"]
EXCLUDED_SYMBOLS = ["AAVEUSDT", "ATOMUSDT", "BCHUSDT", "ETCUSDT", "FILUSDT", "ICPUSDT", "INJUSDT", "RENDERUSDT",
                    "TONUSDT", "TRXUSDT", "UNIUSDT"]
# Coins lacking the window on a timeframe (dropped from strategies that need it).
TF_GAPS = {
    "1h": {"AVAXUSDT": "1h starts 2024-04-04"},
    "15m": {"ADAUSDT": "15m starts 2023-11-15", "ARBUSDT": "15m starts 2023-09-30", "BNBUSDT": "15m starts 2024-08-08",
            "LINKUSDT": "15m starts 2024-04-05", "LTCUSDT": "15m starts 2024-06-06", "NEARUSDT": "15m starts 2024-05-21",
            "OPUSDT": "15m starts 2023-11-15", "SUIUSDT": "15m starts 2023-12-17", "XRPUSDT": "15m starts 2024-10-09",
            "AVAXUSDT": "needs 1h HTF; 1h starts 2024-04-04"},
}

STUDY_START = datetime(2023, 6, 1, tzinfo=UTC)
RESEARCH = (datetime(2023, 6, 1, tzinfo=UTC), datetime(2025, 4, 1, tzinfo=UTC))
VALIDATION = (datetime(2025, 4, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC))
FINAL = (datetime(2025, 9, 1, tzinfo=UTC), datetime(2026, 2, 14, tzinfo=UTC))
STUDY_END = FINAL[1]  # exclusive; last day 2026-02-13 (published rank window starts 2026-02-14)
DATA_FROM = datetime(2021, 1, 1, tzinfo=UTC)  # warmup: everything stored before STUDY_START

TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def coins_for_tf(tf: str) -> list[str]:
    gaps = TF_GAPS.get(tf, {})
    return [s for s in STUDY_SYMBOLS if s not in gaps]


UPTREND = {"BULL_TREND", "HIGH_VOLATILITY_TREND"}
DOWNTREND = {"BEAR_TREND"}
RANGE = {"RANGE", "HIGH_VOLATILITY_RANGE", "LOW_VOLATILITY_COMPRESSION"}


def bucket(regime: str) -> str:
    if regime in UPTREND:
        return "UPTREND"
    if regime in DOWNTREND:
        return "DOWNTREND"
    if regime in RANGE:
        return "RANGE"
    return "NO_TRADE"
