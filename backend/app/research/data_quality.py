"""OHLCV data-quality checks for research runs.

Never silently fill missing candles. Never substitute another timeframe.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.config import ResearchConfig


_TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}


def _candle_ts(c: Mapping[str, Any]) -> float | None:
    raw = c.get("time") or c.get("timestamp") or c.get("open_time")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return ts
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def verify_ohlcv(
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    *,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """Return coverage report. status INSUFFICIENT_DATA when not usable."""
    cfg = config or ResearchConfig()
    n = len(candles)
    if n < cfg.min_bars:
        return {
            "status": "INSUFFICIENT_DATA",
            "data_quality": "INSUFFICIENT_DATA",
            "reason": f"Need at least {cfg.min_bars} candles, got {n}",
            "candle_count": n,
            "coverage_ratio": 0.0,
            "missing_candles": 0,
            "gap_count": 0,
            "duplicate_count": 0,
            "duplicate_candles": 0,
            "timestamp_consistent": False,
            "timeframe_consistent": False,
            "calendar_start": None,
            "calendar_end": None,
            "fabricated": False,
            "interpolated": False,
        }

    step = _TF_SECONDS.get(timeframe.lower())
    timestamps: list[float] = []
    duplicates = 0
    seen: set[float] = set()
    bad_ohlc = 0
    for c in candles:
        ts = _candle_ts(c)
        if ts is None:
            continue
        if ts in seen:
            duplicates += 1
        seen.add(ts)
        timestamps.append(ts)
        try:
            o = float(c.get("open") or c.get("o") or 0)
            h = float(c.get("high") or c.get("h") or 0)
            l = float(c.get("low") or c.get("l") or 0)
            cl = float(c.get("close") or c.get("c") or 0)
            if h < max(o, cl) or l > min(o, cl) or h < l:
                bad_ohlc += 1
        except (TypeError, ValueError):
            bad_ohlc += 1

    timestamps.sort()
    mono = all(timestamps[i] < timestamps[i + 1] for i in range(len(timestamps) - 1))
    missing = 0
    max_gap = 0
    if step and len(timestamps) >= 2:
        for i in range(len(timestamps) - 1):
            gap = timestamps[i + 1] - timestamps[i]
            bars = int(round(gap / step)) - 1
            if bars > 0:
                missing += bars
                max_gap = max(max_gap, bars)
        span_bars = int(round((timestamps[-1] - timestamps[0]) / step)) + 1
        coverage = (len(timestamps) / span_bars) if span_bars > 0 else 0.0
    else:
        coverage = 1.0 if timestamps else 0.0
        span_bars = len(timestamps)

    tf_ok = step is not None
    # Canonical research labels: DATA_OK | DATA_GAPS | DUPLICATES | INSUFFICIENT_DATA
    quality = "DATA_OK"
    status = "DATA_OK"
    reasons: list[str] = []
    if not mono:
        quality = "DATA_GAPS"
        reasons.append("timestamps not strictly ascending")
    if duplicates:
        reasons.append(f"duplicate_candles={duplicates}")
        quality = "DUPLICATES"
    if bad_ohlc:
        reasons.append(f"bad_ohlc={bad_ohlc}")
        quality = "DATA_GAPS"
    if missing > 0 or max_gap > cfg.max_gap_bars:
        reasons.append(f"gap_count={missing}; max_gap_bars={max_gap}")
        if quality == "DATA_OK":
            quality = "DATA_GAPS"
    if coverage < cfg.min_coverage_ratio or n < cfg.min_bars:
        status = "INSUFFICIENT_DATA"
        quality = "INSUFFICIENT_DATA"
        reasons.append(
            f"coverage_ratio={coverage:.3f} < min {cfg.min_coverage_ratio}"
            if coverage < cfg.min_coverage_ratio
            else f"candle_count={n} < min_bars={cfg.min_bars}"
        )
    elif quality == "DUPLICATES":
        status = "DUPLICATES"
    elif quality == "DATA_GAPS":
        status = "DATA_GAPS"
    else:
        status = "DATA_OK"
        quality = "DATA_OK"

    calendar_start = None
    calendar_end = None
    if timestamps:
        calendar_start = datetime.fromtimestamp(timestamps[0], tz=timezone.utc).isoformat()
        calendar_end = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).isoformat()

    return {
        "status": status,
        "data_quality": quality,
        "reason": "; ".join(reasons) if reasons else "ok",
        "candle_count": n,
        "unique_timestamps": len(timestamps),
        "coverage_ratio": coverage,
        "missing_candles": missing,
        "gap_count": missing,
        "duplicate_count": duplicates,
        "duplicate_candles": duplicates,
        "max_gap_bars": max_gap,
        "bad_ohlc": bad_ohlc,
        "timestamp_consistent": mono,
        "timeframe_consistent": tf_ok,
        "expected_step_seconds": step,
        "span_bars": span_bars,
        "calendar_start": calendar_start,
        "calendar_end": calendar_end,
        # Never fabricate / interpolate / substitute another timeframe
        "fabricated": False,
        "interpolated": False,
    }
