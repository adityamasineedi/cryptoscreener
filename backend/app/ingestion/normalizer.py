from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.schemas import DataStatus, MarkPriceUpdate, TickerUpdate


def ms_to_dt(ms: int | float | None) -> datetime:
    if ms is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)


def normalize_futures_ticker_event(data: dict[str, Any]) -> TickerUpdate | None:
    """Normalize Binance USDT-M futures 24hr ticker payload."""
    # Combined stream wraps as {"stream": "...", "data": {...}}
    payload = data.get("data", data)
    if not isinstance(payload, dict):
        return None
    # All-market array stream
    if isinstance(payload, list):
        return None
    symbol = payload.get("s")
    if not symbol:
        return None
    try:
        price = float(payload["c"])
    except (KeyError, TypeError, ValueError):
        return None
    def _f(key: str) -> float | None:
        raw = payload.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _i(key: str) -> int | None:
        raw = payload.get(key)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return TickerUpdate(
        symbol=symbol,
        market_type="futures_perp",
        price=price,
        price_change_pct_24h=_f("P"),
        high_24h=_f("h"),
        low_24h=_f("l"),
        volume_24h=_f("v"),
        quote_volume_24h=_f("q"),
        trade_count=_i("n"),
        timestamp=ms_to_dt(payload.get("E")),
        source="binance_ws",
        status=DataStatus.LIVE,
    )


def normalize_futures_ticker_array(data: Any) -> list[TickerUpdate]:
    payload = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(payload, list):
        single = normalize_futures_ticker_event(data if isinstance(data, dict) else {})
        return [single] if single else []
    out: list[TickerUpdate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        tick = normalize_futures_ticker_event(item)
        if tick:
            out.append(tick)
    return out


def normalize_mark_price_event(data: dict[str, Any]) -> MarkPriceUpdate | None:
    payload = data.get("data", data)
    if not isinstance(payload, dict):
        return None
    symbol = payload.get("s")
    if not symbol:
        return None
    try:
        mark = float(payload["p"])
    except (KeyError, TypeError, ValueError):
        return None

    def _f(key: str) -> float | None:
        raw = payload.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    next_funding = None
    t = payload.get("T")
    if t:
        next_funding = ms_to_dt(t)

    return MarkPriceUpdate(
        symbol=symbol,
        mark_price=mark,
        index_price=_f("i"),
        funding_rate=_f("r"),
        next_funding_time=next_funding,
        timestamp=ms_to_dt(payload.get("E")),
        source="binance_ws",
        status=DataStatus.LIVE,
    )


def normalize_mark_price_array(data: Any) -> list[MarkPriceUpdate]:
    payload = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(payload, list):
        single = normalize_mark_price_event(data if isinstance(data, dict) else {})
        return [single] if single else []
    out: list[MarkPriceUpdate] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        mark = normalize_mark_price_event(item)
        if mark:
            out.append(mark)
    return out


def normalize_rest_ticker_24hr(item: dict[str, Any]) -> TickerUpdate | None:
    """Normalize Binance futures REST /fapi/v1/ticker/24hr row."""
    symbol = item.get("symbol")
    if not symbol:
        return None
    try:
        price = float(item["lastPrice"])
    except (KeyError, TypeError, ValueError):
        return None

    def _f(key: str) -> float | None:
        raw = item.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _i(key: str) -> int | None:
        raw = item.get(key)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    # Freshness must reflect when WE received the snapshot. Using exchange
    # closeTime with WS-oriented 15s/60s windows falsely marks REST as UNAVAILABLE.
    return TickerUpdate(
        symbol=symbol,
        market_type="futures_perp",
        price=price,
        price_change_pct_24h=_f("priceChangePercent"),
        high_24h=_f("highPrice"),
        low_24h=_f("lowPrice"),
        volume_24h=_f("volume"),
        quote_volume_24h=_f("quoteVolume"),
        trade_count=_i("count"),
        timestamp=datetime.now(timezone.utc),
        source="binance_rest",
        status=DataStatus.LIVE,
    )


def normalize_rest_premium_index(item: dict[str, Any]) -> MarkPriceUpdate | None:
    symbol = item.get("symbol")
    if not symbol:
        return None
    try:
        mark = float(item["markPrice"])
    except (KeyError, TypeError, ValueError):
        return None

    def _f(key: str) -> float | None:
        raw = item.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    return MarkPriceUpdate(
        symbol=symbol,
        mark_price=mark,
        index_price=_f("indexPrice"),
        funding_rate=_f("lastFundingRate"),
        next_funding_time=ms_to_dt(item.get("nextFundingTime"))
        if item.get("nextFundingTime")
        else None,
        timestamp=datetime.now(timezone.utc),
        source="binance_rest",
        status=DataStatus.LIVE,
    )
