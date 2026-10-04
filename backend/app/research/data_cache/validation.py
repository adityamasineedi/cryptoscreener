"""Validate research OHLCV before cache use. Never silently repairs data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.data_quality import verify_ohlcv


CACHE_INVALID = "CACHE_INVALID"
CACHE_PASS = "PASS"


def _ts(c: Mapping[str, Any]) -> datetime | None:
    raw = c.get("time") or c.get("timestamp") or c.get("open_time")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def validate_ohlcv_candles(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """Schema + ordering + OHLC validity. Does not fill gaps."""
    problems: list[str] = []
    if not candles:
        return {
            "status": CACHE_INVALID,
            "validation_status": CACHE_INVALID,
            "reason": "empty_series",
            "problems": ["empty_series"],
            "row_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "fabricated": False,
            "interpolated": False,
        }

    required = ("open", "high", "low", "close")
    for i, c in enumerate(candles[:5]):
        for k in required:
            if k not in c:
                problems.append(f"missing_field:{k}@row{i}")
        ts = _ts(c)
        if ts is None:
            problems.append(f"bad_timestamp@row{i}")

    # Strict ascending unique timestamps
    times: list[datetime] = []
    for i, c in enumerate(candles):
        ts = _ts(c)
        if ts is None:
            problems.append(f"bad_timestamp@row{i}")
            continue
        if times and ts < times[-1]:
            problems.append("unordered_timestamps")
            break
        if times and ts == times[-1]:
            problems.append("duplicate_timestamps")
            break
        times.append(ts)
        try:
            o = float(c["open"])
            h = float(c["high"])
            lo = float(c["low"])
            cl = float(c["close"])
            vol = float(c.get("volume") or 0.0)
        except (TypeError, ValueError, KeyError):
            problems.append(f"non_numeric_ohlcv@row{i}")
            continue
        if not (lo <= min(o, cl) and h >= max(o, cl) and lo <= h):
            problems.append(f"invalid_ohlc@row{i}")
        if vol < 0:
            problems.append(f"negative_volume@row{i}")

    dq = verify_ohlcv(candles, timeframe)
    if dq.get("duplicate_count") or dq.get("duplicate_candles"):
        problems.append("duplicates_reported_by_verify_ohlcv")

    first = times[0].isoformat() if times else None
    last = times[-1].isoformat() if times else None

    status = CACHE_PASS if not problems else CACHE_INVALID
    return {
        "status": status,
        "validation_status": status,
        "problems": problems,
        "row_count": len(candles),
        "first_timestamp": first,
        "last_timestamp": last,
        "data_quality": dq,
        "fabricated": False,
        "interpolated": False,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
    }


def manifests_compatible(
    *,
    manifest: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    start_time: str | None,
    end_time: str | None,
    dataset_version: str,
) -> tuple[bool, str]:
    if str(manifest.get("symbol") or "").upper() != symbol.upper():
        return False, "symbol_mismatch"
    if str(manifest.get("timeframe") or "").lower() != timeframe.lower():
        return False, "timeframe_mismatch"
    if str(manifest.get("dataset_version") or "") != dataset_version:
        return False, "dataset_version_mismatch"
    if str(manifest.get("validation_status") or "") != CACHE_PASS:
        return False, "validation_not_pass"
    # Exact range match required for hit (partial handled separately).
    m_start = (manifest.get("start_time") or "")[:10] or None
    m_end = (manifest.get("end_time") or "")[:10] or None
    req_start = (start_time or "")[:10] or None
    req_end = (end_time or "")[:10] or None
    if m_start != req_start or m_end != req_end:
        return False, "range_mismatch"
    if bool(manifest.get("fabricated")) or bool(manifest.get("interpolated")):
        return False, "synthetic_data_flag"
    return True, "ok"
