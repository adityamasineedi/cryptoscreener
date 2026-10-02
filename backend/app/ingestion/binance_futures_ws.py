"""Binance USD-M Futures WebSocket URL helpers.

Binance retired legacy ``/ws`` market-data delivery for USD-M futures
(2026-04-23). Market streams (ticker, kline, aggTrade, forceOrder, …)
must use ``/market/ws`` or ``/market/stream``. High-frequency book streams
use ``/public/ws``. Legacy ``/ws`` still accepts handshakes but pushes no
market frames — a silent connected socket is the failure mode.
"""

from __future__ import annotations

MARKET_TYPE = "USD-M Futures"
MARKET_WS_PATH = "/market/ws"
MARKET_STREAM_PATH = "/market/stream"
PUBLIC_WS_PATH = "/public/ws"
LEGACY_WS_PATH = "/ws"  # retired for market data; keep for diagnostics only

FORCE_ORDER_AGGREGATE = "!forceOrder@arr"


def normalize_base(base_ws: str) -> str:
    return (base_ws or "").rstrip("/")


def market_ws_url(base_ws: str, stream: str = "") -> str:
    """Raw market stream URL: ``{base}/market/ws[/{stream}]``."""
    base = normalize_base(base_ws)
    stream = (stream or "").lstrip("/")
    if stream:
        return f"{base}{MARKET_WS_PATH}/{stream}"
    return f"{base}{MARKET_WS_PATH}"


def market_combined_url(base_ws: str, streams: list[str]) -> str:
    """Combined market streams URL."""
    base = normalize_base(base_ws)
    joined = "/".join(streams)
    return f"{base}{MARKET_STREAM_PATH}?streams={joined}"


def force_order_aggregate_url(base_ws: str) -> str:
    return market_ws_url(base_ws, FORCE_ORDER_AGGREGATE)


def force_order_symbol_stream(symbol: str) -> str:
    """Per-symbol forceOrder stream name (lowercase required by Binance)."""
    return f"{symbol.lower()}@forceOrder"


def legacy_ws_url(base_ws: str, stream: str = "") -> str:
    """Legacy path — connects but does not deliver market data after retirement."""
    base = normalize_base(base_ws)
    stream = (stream or "").lstrip("/")
    if stream:
        return f"{base}{LEGACY_WS_PATH}/{stream}"
    return f"{base}{LEGACY_WS_PATH}"
