"""Candle-close / no-lookahead validation for parity research."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.research.live_backtest_parity.models import (
    CandleCloseValidation,
    ensure_utc,
)
from app.signals._candle_utils import candle_time

_TF_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "1d": 1440,
}


def timeframe_minutes(tf: str) -> int | None:
    return _TF_MINUTES.get(str(tf or "").lower())


def candle_close_time(
    candle: Mapping[str, Any] | None,
    timeframe: str,
) -> datetime | None:
    """Close time = open + timeframe duration (closed candle convention)."""
    if not candle:
        return None
    # Prefer explicit close_time if present.
    raw_close = candle.get("close_time") or candle.get("closeTime")
    if raw_close is not None:
        if isinstance(raw_close, datetime):
            return ensure_utc(raw_close)
        if isinstance(raw_close, (int, float)):
            ts = float(raw_close)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    open_t = candle_time(candle)
    mins = timeframe_minutes(timeframe)
    if open_t is None or mins is None:
        return open_t
    return ensure_utc(open_t) + timedelta(minutes=mins)  # type: ignore[operator]


def slice_as_of(
    candles: Sequence[Mapping[str, Any]],
    as_of: datetime,
) -> list[dict[str, Any]]:
    """Keep only bars with open time <= as_of (no future candles)."""
    cutoff = ensure_utc(as_of)
    assert cutoff is not None
    out: list[dict[str, Any]] = []
    for c in candles:
        t = candle_time(c)
        if t is None:
            continue
        if ensure_utc(t) <= cutoff:
            out.append(dict(c))
    return out


def validate_candle_close(
    *,
    as_of: datetime,
    signal_detected_at: datetime | None,
    setup_candles: Sequence[Mapping[str, Any]],
    setup_timeframe: str,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_15m: Sequence[Mapping[str, Any]] | None = None,
) -> CandleCloseValidation:
    """Verify signal time >= candle close and no future OHLCV used."""
    as_of_utc = ensure_utc(as_of)
    assert as_of_utc is not None
    details: dict[str, Any] = {}
    future = False

    def _check_series(
        name: str,
        series: Sequence[Mapping[str, Any]] | None,
        tf: str,
    ) -> None:
        nonlocal future
        if not series:
            details[name] = {"present": False}
            return
        tip = series[-1]
        open_t = candle_time(tip)
        close_t = candle_close_time(tip, tf)
        open_utc = ensure_utc(open_t)
        # Any bar open after as_of is future data.
        for c in series:
            t = ensure_utc(candle_time(c))
            if t is not None and t > as_of_utc:
                future = True
                break
        details[name] = {
            "present": True,
            "bars": len(series),
            "latest_open": open_utc.isoformat() if open_utc else None,
            "latest_close": close_t.isoformat() if close_t else None,
            "timeframe": tf,
        }

    setup_tf = str(setup_timeframe or "15m").lower()
    _check_series("setup", setup_candles, setup_tf)
    if candles_15m is not None or setup_tf == "15m":
        _check_series("15m", candles_15m if candles_15m is not None else setup_candles, "15m")
    _check_series("1h", candles_1h, "1h")
    _check_series("4h", candles_4h, "4h")

    latest_close = None
    if setup_candles:
        latest_close = candle_close_time(setup_candles[-1], setup_tf)

    signal_after = None
    if signal_detected_at is not None and latest_close is not None:
        sig = ensure_utc(signal_detected_at)
        signal_after = sig is not None and sig >= latest_close

    tip_open = candle_time(setup_candles[-1]) if setup_candles else None
    return CandleCloseValidation(
        as_of_timestamp=as_of_utc,
        latest_candle_used=tip_open.isoformat() if tip_open else None,
        latest_candle_close=latest_close,
        future_data_detected=future,
        signal_after_close=signal_after,
        details=details,
    )


class TrackingCandles(list):
    """Sequence that records max index accessed via __getitem__/__len__ scans."""

    def __init__(self, data: Sequence[Mapping[str, Any]]):
        super().__init__(list(data))
        self.max_accessed = -1

    def __getitem__(self, key):  # type: ignore[no-untyped-def]
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if stop > 0:
                self.max_accessed = max(self.max_accessed, stop - 1)
            return list.__getitem__(self, key)
        idx = key if key >= 0 else len(self) + key
        self.max_accessed = max(self.max_accessed, idx)
        return list.__getitem__(self, key)
