"""Multi-horizon volume change from OHLCV volume history.

Uses consecutive non-overlapping windows of equal length:

  volume_change = (volume_current_window / volume_previous_window - 1) * 100

Does NOT use the same 24h ticker volume as both numerator and denominator.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.schemas import DataStatus, FreshValue
from app.services.ohlcv_store import ohlcv_store

SOURCE = "ohlcv_store"
METHOD = (
    "(sum(volume_current_window) / sum(volume_previous_window) - 1) * 100 "
    "from consecutive non-overlapping OHLCV windows"
)

# field -> (candle timeframe, window length)
WINDOWS: dict[str, tuple[str, timedelta]] = {
    "volume_change_1h": ("1m", timedelta(hours=1)),
    "volume_change_4h": ("5m", timedelta(hours=4)),
    "volume_change_24h": ("1h", timedelta(hours=24)),
    "volume_change_7d": ("1d", timedelta(days=7)),
}


def _waiting(field: str) -> FreshValue[float]:
    return FreshValue.waiting(
        SOURCE,
        methodology=f"{METHOD} — {field} needs two full windows of volume history",
    )


def _sum_volume(candles: list[Any], start: datetime, end: datetime) -> float | None:
    total = 0.0
    n = 0
    for c in candles:
        ot = c.open_time if hasattr(c, "open_time") else c.get("open_time")
        vol = c.volume if hasattr(c, "volume") else c.get("volume")
        if ot is None or vol is None:
            continue
        if ot.tzinfo is None:
            ot = ot.replace(tzinfo=timezone.utc)
        if start <= ot < end:
            total += float(vol)
            n += 1
    if n == 0:
        return None
    return total


def compute_volume_changes(
    symbol: str, *, now: datetime | None = None
) -> dict[str, FreshValue[float]]:
    sym = symbol.upper()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out: dict[str, FreshValue[float]] = {}

    for field, (tf, window) in WINDOWS.items():
        candles = ohlcv_store.get_closed(sym, tf)
        if not candles:
            out[field] = _waiting(field)
            continue

        cur_end = now
        cur_start = now - window
        prev_end = cur_start
        prev_start = cur_start - window

        cur = _sum_volume(candles, cur_start, cur_end)
        prev = _sum_volume(candles, prev_start, prev_end)

        if cur is None or prev is None or prev == 0:
            out[field] = _waiting(field)
            continue

        # Guard: never allow identical window reuse masquerading as change
        if cur_start == prev_start:
            out[field] = _waiting(field)
            continue

        out[field] = FreshValue(
            value=(cur / prev - 1.0) * 100.0,
            timestamp=now,
            source=SOURCE,
            status=DataStatus.LIVE,
            methodology=METHOD,
        )
    return out
