"""Deterministic cache keys for research OHLCV / feature / event datasets."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _iso_day(value: str | datetime | None, *, empty: str = "full") -> str:
    if value is None:
        return empty
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return empty
    return s[:10]


def make_ohlcv_cache_key(
    *,
    symbol: str,
    timeframe: str,
    start_time: str | datetime | None,
    end_time: str | datetime | None,
    dataset_version: str,
) -> str:
    """Unique id: SYMBOL_TF_start_end_version (never ambiguous across ranges)."""
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    start = _iso_day(start_time, empty="full")
    end = _iso_day(end_time, empty="now")
    ver = _SAFE.sub("_", str(dataset_version or "ohlcv_v1"))
    return f"{sym}_{tf}_{start}_{end}_{ver}"


def make_feature_cache_key(
    *,
    symbol: str,
    timeframe: str,
    start_time: str | datetime | None,
    end_time: str | datetime | None,
    dataset_version: str,
    feature_version: str,
) -> str:
    base = make_ohlcv_cache_key(
        symbol=symbol,
        timeframe=timeframe,
        start_time=start_time,
        end_time=end_time,
        dataset_version=dataset_version,
    )
    fv = _SAFE.sub("_", str(feature_version or "features_v1"))
    return f"{base}__{fv}"


def content_fingerprint(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
