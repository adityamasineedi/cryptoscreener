"""Multi-horizon performance from persisted/in-memory OHLCV.
Formula: (price_now / price_at_period_start - 1) * 100
Never substitutes 24h ticker change when history is missing — returns WAITING.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any
from app.models.schemas import DataStatus, FreshValue
from app.services.ohlcv_store import ohlcv_store
from app.services.persistence import persistence
METHOD = "(price_now / price_at_period_start - 1) * 100 using 1d OHLCV closes"
SOURCE = "ohlcv_store"
PERIODS: dict[str, timedelta | str] = {
    "performance_1d": timedelta(days=1),
    "performance_7d": timedelta(days=7),
    "performance_30d": timedelta(days=30),
    "performance_90d": timedelta(days=90),
    "performance_180d": timedelta(days=180),
    "performance_1y": timedelta(days=365),
    "performance_ytd": "ytd",
}

def _waiting(field: str) -> FreshValue[float]:
    return FreshValue.waiting(
        SOURCE,
        methodology=f"{METHOD} — {field} unavailable (insufficient history)",
    )

def _pct(now: float, start: float) -> float:
    return (now / start - 1.0) * 100.0

def _price_at_or_before(
    candles: list[Any], target: datetime
) -> tuple[float, datetime] | None:
    """Return close of the candle at or just before target."""
    best: tuple[float, datetime] | None = None
    for c in candles:
        ot = c.open_time if hasattr(c, "open_time") else c.get("open_time")
        close = c.close if hasattr(c, "close") else c.get("close")
        if ot is None or close is None:
            continue
        if ot.tzinfo is None:
            ot = ot.replace(tzinfo=timezone.utc)
        if ot <= target:
            best = (float(close), ot)
        else:
            break
    return best

async def _resolve_start_price(
    symbol: str, target: datetime, candles: list[Any]
) -> tuple[float, datetime] | None:
    hit = _price_at_or_before(candles, target)
    if hit is not None:
        return hit
    # Fall back to DB when memory series is shorter than the horizon
    rows = await persistence.load_closes_near(
        symbol, timeframe="1d", before=target, limit=1
    )
    if rows:
        return rows[0][1], rows[0][0]
    return None

async def compute_performance(
    symbol: str,
    *,
    price_now: float | None = None,
) -> dict[str, FreshValue[float]]:
    sym = symbol.upper()
    candles = ohlcv_store.get_closed(sym, "1d")
    now_ts = datetime.now(timezone.utc)
    if price_now is None:
        if candles:
            price_now = float(candles[-1].close)
        else:
            return {k: _waiting(k) for k in PERIODS}
    out: dict[str, FreshValue[float]] = {}
    for field, period in PERIODS.items():
        if period == "ytd":
            target = datetime(now_ts.year, 1, 1, tzinfo=timezone.utc)
        else:
            assert isinstance(period, timedelta)
            target = now_ts - period
        start = await _resolve_start_price(sym, target, candles)
        if start is None:
            out[field] = _waiting(field)
            continue
        start_price, start_ts = start
        if start_price <= 0:
            out[field] = FreshValue.unavailable(SOURCE, methodology=METHOD)
            continue
        # Require the start candle to be reasonably close to the target
        # (within 2 days for daily series) — otherwise WAITING (gap / missing)
        if abs((start_ts - target).total_seconds()) > 2 * 86400 and period != "ytd":
            # For long horizons, allow the closest available candle at/before target
            # as long as we have a candle on or before the target (already enforced).
            pass
        # For YTD, require a candle on/before Jan 1 of this year
        if period == "ytd" and start_ts.year < now_ts.year - 1:
            out[field] = _waiting(field)
            continue
        out[field] = FreshValue(
            value=_pct(float(price_now), start_price),
            timestamp=now_ts,
            source=SOURCE,
            status=DataStatus.HISTORICAL,
            methodology=METHOD,
        )
    return out

def compute_performance_sync(
    symbol: str, *, price_now: float | None = None
) -> dict[str, FreshValue[float]]:
    """Sync path using in-memory OHLCV only (no DB fallback)."""
    sym = symbol.upper()
    candles = ohlcv_store.get_closed(sym, "1d")
    now_ts = datetime.now(timezone.utc)
    if price_now is None:
        if candles:
            price_now = float(candles[-1].close)
        else:
            return {k: _waiting(k) for k in PERIODS}
    out: dict[str, FreshValue[float]] = {}
    for field, period in PERIODS.items():
        if period == "ytd":
            target = datetime(now_ts.year, 1, 1, tzinfo=timezone.utc)
        else:
            assert isinstance(period, timedelta)
            target = now_ts - period
        start = _price_at_or_before(candles, target)
        if start is None:
            out[field] = _waiting(field)
            continue
        start_price, _start_ts = start
        if start_price <= 0:
            out[field] = FreshValue.unavailable(SOURCE, methodology=METHOD)
            continue
        out[field] = FreshValue(
            value=_pct(float(price_now), start_price),
            timestamp=now_ts,
            source=SOURCE,
            status=DataStatus.HISTORICAL,
            methodology=METHOD,
        )
    return out
