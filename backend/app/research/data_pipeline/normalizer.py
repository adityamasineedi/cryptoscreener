"""Normalize Binance REST klines into research candle dicts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from app.ingestion.klines import normalize_rest_kline, normalize_timeframe
from app.models.ohlcv import Candle


def normalize_kline_row(
    symbol: str,
    timeframe: str,
    row: Sequence[Any],
    *,
    source: str = "binance_research_pipeline",
) -> dict[str, Any] | None:
    """Return a closed-candle dict or None if incomplete / still open."""
    candle = normalize_rest_kline(symbol, normalize_timeframe(timeframe), row)
    if candle is None or not candle.is_closed:
        return None
    return candle_to_row(candle, source=source)


def candle_to_row(candle: Candle, *, source: str | None = None) -> dict[str, Any]:
    src = source or candle.source or "binance_research_pipeline"
    return {
        "time": candle.open_time,
        "symbol": candle.symbol.upper(),
        "timeframe": normalize_timeframe(candle.timeframe),
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
        "close": float(candle.close),
        "volume": float(candle.volume),
        "quote_volume": candle.quote_volume,
        "trade_count": candle.trade_count,
        "taker_buy_base": candle.taker_buy_volume,
        "taker_buy_quote": candle.taker_buy_quote_volume,
        "source": src,
        "open_time_ms": int(candle.open_time.timestamp() * 1000),
    }


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
