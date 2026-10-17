"""Deterministic synthetic 1h candles for the VectorBT parity check.

Shared by the engine-family and combo-family parity runners. The series is a
seeded random walk that alternates trend-up, range, trend-down and range
regimes, with occasional long wicks (liquidity sweeps), gaps between candle
bodies (fair-value gaps) and volume spikes, so structure engines (BOS, CHoCH,
sweeps, FVG) actually fire. Everything comes from ``numpy.random.default_rng``
seeded with ``seed``; nothing else is random, and the output for a given
(n, seed) never changes.

These candles are not market data and say nothing about any strategy's edge;
they only exercise the order paths.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

FIXTURE_SYMBOL = "BTCUSDT"
FIXTURE_TIMEFRAME = "1h"
FIXTURE_START = datetime(2023, 1, 1, tzinfo=timezone.utc)
FIXTURE_SEED = 20261017
FIXTURE_N = 3000
FIXTURE_WARMUP = 1000  # use as index_start

# (drift per bar, volatility per bar) per regime, cycled in this order.
_REGIMES: tuple[tuple[float, float], ...] = (
    (0.0008, 0.0060),  # trend up
    (0.0, 0.0045),  # range
    (-0.0008, 0.0065),  # trend down
    (0.0, 0.0050),  # range
)
_BASE_PRICE = 30000.0
# Weak pull toward the base price so the series stays in one price band and
# stops/targets set early stay reachable.
_LONG_RUN_PULL = 0.003


def fixture_candles(n: int = FIXTURE_N, seed: int = FIXTURE_SEED) -> list[dict]:
    """``n`` 1h bars from 2023-01-01T00:00Z: time, open, high, low, close, volume."""
    if n <= 0:
        return []
    rng = np.random.default_rng(seed)
    candles: list[dict] = []
    price = _BASE_PRICE
    regime = 0
    left = int(rng.integers(120, 260))
    anchor = price  # range regimes mean-revert toward the price they started at
    for i in range(n):
        if left <= 0:
            regime = (regime + 1) % len(_REGIMES)
            left = int(rng.integers(120, 260))
            anchor = price
        left -= 1
        drift, vol = _REGIMES[regime]
        if drift == 0.0:
            drift = -0.02 * math.log(price / anchor)
        drift -= _LONG_RUN_PULL * math.log(price / _BASE_PRICE)
        # Mostly continuous, sometimes a gap open (FVG material).
        gap = 0.0
        if rng.random() < 0.04:
            gap = float(rng.normal(0.0, 2.0 * vol))
        open_ = price * math.exp(gap)
        ret = drift + vol * float(rng.standard_t(5)) / math.sqrt(5.0 / 3.0)
        # Impulse bars: larger, directional bodies on heavy volume (breakouts).
        impulse = rng.random() < 0.05
        if impulse:
            ret += math.copysign(3.0 * vol, drift if drift != 0.0 else float(rng.normal()))
        close = open_ * math.exp(ret)
        body_hi = max(open_, close)
        body_lo = min(open_, close)
        up_wick = abs(float(rng.normal(0.0, 0.5 * vol)))
        dn_wick = abs(float(rng.normal(0.0, 0.5 * vol)))
        # Liquidity sweeps: one long wick that closes back inside.
        if rng.random() < 0.03:
            if rng.random() < 0.5:
                up_wick += 4.0 * vol
            else:
                dn_wick += 4.0 * vol
        high = body_hi * (1.0 + up_wick)
        low = body_lo * (1.0 - dn_wick)
        volume = float(rng.lognormal(mean=math.log(800.0), sigma=0.35))
        if impulse:
            volume *= float(rng.uniform(3.5, 6.0))
        elif abs(ret) > 2.5 * vol:
            volume *= 2.0
        if rng.random() < 0.04:
            volume *= float(rng.uniform(3.0, 6.0))
        o = round(open_, 2)
        c = round(close, 2)
        h = max(round(high, 2), o, c)
        lo = min(round(low, 2), o, c)
        candles.append(
            {
                "time": FIXTURE_START + timedelta(hours=i),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": round(volume, 4),
            }
        )
        price = c
    return candles


# ---------------------------------------------------------------------------
# Multi-timeframe fixture (additional; ``fixture_candles`` above is unchanged).
#
# One seeded synthetic 5m series, aggregated exactly to 15m / 1h / 4h / 1d on
# UTC bar boundaries (open = first open, high = max, low = min, close = last
# close, volume = sum; only complete buckets). Used for strategies whose screen
# path also loads lower/higher timeframe strategy candles (COMBO_02_V2 family:
# 15m + 4h; COMBO_SD_*: 15m + 5m + 4h + 1d). Same regime idea as above, scaled
# to 5m bars. Not market data.
# ---------------------------------------------------------------------------

MTF_FIXTURE_DAYS = 180
# index_start on the 1h series: 1000 1h bars (~41.7 days) precede the first
# evaluated bar, which covers the slowest series' warmup (strategy_matrix asks
# for max(warmup // 4, 40) daily bars and max(warmup // 4, 50) 4h bars).
MTF_FIXTURE_WARMUP_1H = 1000
_TF_MIN = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_MTF_CACHE: dict[tuple[int, int], list[dict]] = {}


def _fixture_5m(days: int, seed: int) -> list[dict]:
    key = (int(days), int(seed))
    if key in _MTF_CACHE:
        return _MTF_CACHE[key]
    n = int(days) * 288
    per_hour = 12
    rng = np.random.default_rng(seed)
    out: list[dict] = []
    price = 30000.0
    regime = 0
    left = int(rng.integers(120, 260)) * per_hour
    anchor = price
    for i in range(n):
        if left <= 0:
            regime = (regime + 1) % len(_REGIMES)
            left = int(rng.integers(120, 260)) * per_hour
            anchor = price
        left -= 1
        drift_h, vol_h = _REGIMES[regime]
        if drift_h == 0.0:
            drift_h = -0.02 * math.log(price / anchor)
        drift = drift_h / per_hour
        vol = vol_h / math.sqrt(per_hour)
        gap = 0.0
        if rng.random() < 0.004:
            gap = float(rng.normal(0.0, 2.0 * vol_h))
        open_ = price * math.exp(gap)
        ret = drift + vol * float(rng.standard_t(5)) / math.sqrt(5.0 / 3.0)
        if rng.random() < 0.005:
            ret += math.copysign(3.0 * vol_h, drift if drift != 0.0 else float(rng.normal()))
        close = open_ * math.exp(ret)
        up_wick = abs(float(rng.normal(0.0, 0.5 * vol)))
        dn_wick = abs(float(rng.normal(0.0, 0.5 * vol)))
        if rng.random() < 0.003:
            if rng.random() < 0.5:
                up_wick += 4.0 * vol_h
            else:
                dn_wick += 4.0 * vol_h
        high = max(open_, close) * (1.0 + up_wick)
        low = min(open_, close) * (1.0 - dn_wick)
        volume = float(rng.lognormal(mean=math.log(800.0 / per_hour), sigma=0.35))
        if abs(ret) > 2.5 * vol:
            volume *= 2.0
        if rng.random() < 0.004:
            volume *= float(rng.uniform(3.0, 6.0))
        o = round(open_, 2)
        c = round(close, 2)
        out.append(
            {
                "time": FIXTURE_START + timedelta(minutes=5 * i),
                "open": o,
                "high": max(round(high, 2), o, c),
                "low": min(round(low, 2), o, c),
                "close": c,
                "volume": round(volume, 4),
            }
        )
        price = c
    _MTF_CACHE[key] = out
    return out


def aggregate_candles(candles: list[dict], minutes: int) -> list[dict]:
    """Exact OHLCV aggregation on UTC boundaries; incomplete buckets dropped."""
    step = timedelta(minutes=minutes)
    out: list[dict] = []
    bucket: list[dict] = []
    bucket_start = None
    base = 5  # source bar minutes
    need = minutes // base
    for c in candles:
        t = c["time"]
        epoch_min = int(t.timestamp()) // 60
        start = t - timedelta(minutes=epoch_min % minutes)
        if bucket_start is None or start != bucket_start:
            bucket, bucket_start = [], start
        bucket.append(c)
        if len(bucket) == need and bucket[-1]["time"] == bucket_start + step - timedelta(minutes=base):
            out.append(
                {
                    "time": bucket_start,
                    "open": bucket[0]["open"],
                    "high": max(b["high"] for b in bucket),
                    "low": min(b["low"] for b in bucket),
                    "close": bucket[-1]["close"],
                    "volume": sum(b["volume"] for b in bucket),
                }
            )
            bucket, bucket_start = [], None
    return out


def fixture_candles_tf(
    timeframe: str, *, seed: int = FIXTURE_SEED, days: int = MTF_FIXTURE_DAYS
) -> list[dict]:
    """Multi-TF fixture: ``timeframe`` in 5m/15m/1h/4h/1d, from one 5m walk."""
    tf = str(timeframe).lower()
    if tf not in _TF_MIN:
        raise ValueError(f"unsupported fixture timeframe {timeframe!r}")
    base = _fixture_5m(days, seed)
    if tf == "5m":
        return [dict(c) for c in base]
    return aggregate_candles(base, _TF_MIN[tf])


__all__ = [
    "FIXTURE_N",
    "FIXTURE_SEED",
    "FIXTURE_START",
    "FIXTURE_SYMBOL",
    "FIXTURE_TIMEFRAME",
    "FIXTURE_WARMUP",
    "MTF_FIXTURE_DAYS",
    "MTF_FIXTURE_WARMUP_1H",
    "aggregate_candles",
    "fixture_candles",
    "fixture_candles_tf",
]
