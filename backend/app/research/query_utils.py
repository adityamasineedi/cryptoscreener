"""Deterministic query helpers for BOS Combination Research.

Calendar date filters are interpreted as UTC days.
Convention: start <= timestamp < end_exclusive.

This module is for the BOS Combination Research dataset only.
It must not be used to pull Candle-1/Candle-2 V2 research result files.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

# Canonical research timeframes (lowercase). Aliases map into these.
_TF_ALIASES: dict[str, str] = {
    "1m": "1m",
    "1min": "1m",
    "1-minute": "1m",
    "5m": "5m",
    "5min": "5m",
    "5-minute": "5m",
    "15m": "15m",
    "15min": "15m",
    "15-minute": "15m",
    "30m": "30m",
    "30min": "30m",
    "1h": "1h",
    "60m": "1h",
    "1hour": "1h",
    "1-hour": "1h",
    "4h": "4h",
    "4hour": "4h",
    "4-hour": "4h",
    "1d": "1d",
    "1day": "1d",
    "1-day": "1d",
}

DATASET_ID = "bos_combination_research"
DATASET_LABEL = "BOS Combination Research"
# Explicit non-identity — do not mix with Candle-1/Candle-2 V2 outputs.
OTHER_DATASET_ID = "candle12_v2"
OTHER_DATASET_LABEL = "Candle-1/Candle-2 V2"


def normalize_research_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def normalize_research_timeframe(timeframe: str) -> str:
    """Map UI/API timeframe variants onto one canonical key (e.g. 15M → 15m)."""
    raw = (timeframe or "").strip()
    if not raw:
        return raw
    key = raw.lower().replace("_", "-").replace(" ", "")
    if key in _TF_ALIASES:
        return _TF_ALIASES[key]
    # Bare numbers like "15" are ambiguous — reject by returning lowercased token.
    return key


def parse_utc_calendar_day(value: str | None) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    # Accept YYYY-MM-DD or full ISO; calendar day is always taken in UTC.
    if "T" in text:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    return date.fromisoformat(text[:10])


def utc_day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def utc_day_end_exclusive(day: date) -> datetime:
    """Inclusive UI end-date → exclusive UTC bound (next midnight)."""
    return utc_day_start(day) + timedelta(days=1)


def resolve_date_bounds(
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    """Resolve UI date strings into UTC half-open bounds.

    UI calendar dates are interpreted as UTC dates (not the browser local zone).
    end_date is inclusive for the user; query uses end_exclusive = end_date + 1 day.
    """
    start_day = parse_utc_calendar_day(start_date)
    end_day = parse_utc_calendar_day(end_date)
    start_utc = utc_day_start(start_day) if start_day else None
    end_exclusive = utc_day_end_exclusive(end_day) if end_day else None
    if start_utc and end_exclusive and end_exclusive <= start_utc:
        # Degenerate / inverted range → empty window (start == end_exclusive after clamp)
        end_exclusive = start_utc
    return {
        "start_date": start_day.isoformat() if start_day else None,
        "end_date_inclusive": end_day.isoformat() if end_day else None,
        "start_utc": start_utc.isoformat() if start_utc else None,
        "end_exclusive_utc": end_exclusive.isoformat() if end_exclusive else None,
        "convention": "start <= timestamp < end_exclusive",
        "timezone": "UTC",
        "ui_dates_interpreted_as": "UTC calendar days",
        "start": start_utc,
        "end_exclusive": end_exclusive,
    }


def candle_timestamp(candle: Mapping[str, Any]) -> datetime | None:
    raw = candle.get("time") or candle.get("timestamp") or candle.get("open_time")
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
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def slice_candles_for_research(
    candles: Sequence[Mapping[str, Any]],
    *,
    start: datetime | None = None,
    end_exclusive: datetime | None = None,
    limit: int | None = None,
    warmup_bars: int = 0,
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Return (windowed_candles, eval_index_start, meta).

    eval_index_start is the first index inside the returned window that is
    inside the requested period (warmup bars precede it when available).
    """
    series = [dict(c) for c in candles]
    if not series:
        return [], 0, {
            "candles_loaded": 0,
            "warmup_bars_applied": 0,
            "eval_bars": 0,
        }

    if start is None and end_exclusive is None:
        if limit is not None and limit > 0:
            series = series[-limit:]
        return series, 0, {
            "candles_loaded": len(series),
            "warmup_bars_applied": 0,
            "eval_bars": len(series),
            "period_start": (
                candle_timestamp(series[0]).isoformat() if series else None
            ),
            "period_end": (
                candle_timestamp(series[-1]).isoformat() if series else None
            ),
        }

    first_eval = 0
    if start is not None:
        first_eval = next(
            (
                i
                for i, c in enumerate(series)
                if (ts := candle_timestamp(c)) is not None and ts >= start
            ),
            len(series),
        )
    last_excl = len(series)
    if end_exclusive is not None:
        last_excl = next(
            (
                i
                for i, c in enumerate(series)
                if (ts := candle_timestamp(c)) is not None and ts >= end_exclusive
            ),
            len(series),
        )

    warm = max(0, int(warmup_bars))
    win_start = max(0, first_eval - warm)
    window = series[win_start:last_excl]
    eval_index_start = first_eval - win_start
    return window, eval_index_start, {
        "candles_loaded": len(window),
        "warmup_bars_applied": eval_index_start,
        "eval_bars": max(0, len(window) - eval_index_start),
        "period_start": (
            candle_timestamp(window[eval_index_start]).isoformat()
            if window and eval_index_start < len(window)
            else None
        ),
        "period_end": (
            candle_timestamp(window[-1]).isoformat() if window else None
        ),
    }
