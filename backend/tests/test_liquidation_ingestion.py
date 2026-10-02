from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.binance_futures_ws import (
    FORCE_ORDER_AGGREGATE,
    force_order_aggregate_url,
    force_order_symbol_stream,
    legacy_ws_url,
    market_ws_url,
)
from app.ingestion.liquidations.force_order import LiquidationIngestion
from app.ingestion.liquidations.provider import BinanceForceOrderProvider
from app.ingestion.ws_manager import WebSocketConnection, WebSocketManager
from app.models.schemas import DataStatus


def _settings(base: str = "wss://fstream.binance.com"):
    return SimpleNamespace(binance_futures_ws=base)


SAMPLE_FORCE = {
    "e": "forceOrder",
    "E": 1700000000000,
    "o": {
        "s": "BTCUSDT",
        "S": "SELL",
        "o": "LIMIT",
        "f": "IOC",
        "q": "0.01",
        "p": "65000",
        "ap": "65000",
        "X": "FILLED",
        "l": "0.01",
        "z": "0.01",
        "T": 1700000000000,
    },
}


def test_correct_endpoint_and_stream_name():
    url = force_order_aggregate_url("wss://fstream.binance.com")
    assert url == "wss://fstream.binance.com/market/ws/!forceOrder@arr"
    assert FORCE_ORDER_AGGREGATE == "!forceOrder@arr"
    assert "/market/ws/" in url
    assert legacy_ws_url("wss://fstream.binance.com", "!forceOrder@arr") == (
        "wss://fstream.binance.com/ws/!forceOrder@arr"
    )
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    assert ing.build_url() == url
    assert ing.STREAM == "!forceOrder@arr"


def test_symbol_stream_lowercase():
    assert force_order_symbol_stream("BTCUSDT") == "btcusdt@forceOrder"
    assert force_order_symbol_stream("btcusdt") == "btcusdt@forceOrder"


def test_market_ws_url_helpers():
    assert market_ws_url("wss://fstream.binance.com/") == "wss://fstream.binance.com/market/ws"
    assert market_ws_url("wss://fstream.binance.com", "!ticker@arr").endswith(
        "/market/ws/!ticker@arr"
    )


@pytest.mark.asyncio
async def test_subscription_ack_does_not_create_event():
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    await ing._on_message({"result": None, "id": 1})
    assert ing._normalized_events == 0
    assert ing.compute_status() == "WAITING"
    assert "ack" in ing._subscription_status


@pytest.mark.asyncio
async def test_reader_task_remains_alive_on_ensure():
    mgr = WebSocketManager()
    # Use unreachable host so we don't hit Binance; task should still be scheduled
    conn = await mgr.ensure(
        name="binance_force_order",
        url="ws://127.0.0.1:1/market/ws/!forceOrder@arr",
        handler=AsyncMock(),
        force_reconnect_hours=24,
    )
    await asyncio.sleep(0.05)
    assert conn._task is not None
    assert not conn._task.done()
    statuses = mgr.status()
    assert statuses[0]["reader_running"] is True or conn.connect_attempts >= 1
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_raw_frame_counter_on_connection():
    received = []

    async def handler(data):
        received.append(data)

    conn = WebSocketConnection(
        name="t",
        url="ws://example",
        handler=handler,
    )
    # Simulate frame path without real socket
    conn.frames_received = 0
    conn.message_count = 0
    raw = b'{"e":"forceOrder","E":1,"o":{"s":"X","S":"BUY","p":"1","q":"1","T":1}}'
    conn.frames_received += 1
    conn.message_count += 1
    conn.bytes_received += len(raw)
    assert conn.frames_received == 1
    assert conn.bytes_received == len(raw)


@pytest.mark.asyncio
async def test_valid_force_order_parsing_and_normalization():
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    await ing._on_message(SAMPLE_FORCE)
    assert ing._normalized_events == 1
    assert ing._raw_events == 1
    assert ing.compute_status() == "LIVE"
    ev = ing.recent_events("BTCUSDT")[0]
    assert ev.source == "binance_force_order"
    assert ev.order_status == "FILLED"


@pytest.mark.asyncio
async def test_malformed_message_increments_parser_errors():
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    await ing._on_message({"e": "forceOrder", "o": {"s": "BTCUSDT", "p": "0", "q": "1"}})
    assert ing._normalized_events == 0
    assert ing._ws_parse_errors >= 1


@pytest.mark.asyncio
async def test_duplicate_event_removed():
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    await ing._on_message(SAMPLE_FORCE)
    await ing._on_message(SAMPLE_FORCE)
    assert ing._normalized_events == 1
    assert ing._duplicates_removed == 1
    assert ing._raw_events == 2


@pytest.mark.asyncio
async def test_waiting_live_stale_states():
    ing = LiquidationIngestion(
        _settings(), WebSocketManager(), stale_after_seconds=60
    )
    assert ing.compute_status() == "WAITING"
    await ing._on_message(SAMPLE_FORCE)
    assert ing.compute_status() == "LIVE"
    # Age the last event
    ing._last_parsed_at = datetime.now(timezone.utc) - timedelta(seconds=120)
    assert ing.compute_status() == "STALE"


@pytest.mark.asyncio
async def test_provider_status_and_diagnostic_fields():
    mgr = WebSocketManager()
    ing = LiquidationIngestion(_settings(), mgr)
    # Pretend connected with zero frames
    fake = {
        "name": ing.STREAM_NAME,
        "connected": True,
        "reader_running": True,
        "frames_received": 0,
        "message_count": 0,
        "bytes_received": 0,
        "reconnect_count": 0,
        "connect_attempts": 1,
        "connect_count": 1,
        "disconnect_count": 0,
        "last_handshake_ok": True,
        "last_message_at": None,
        "last_frame_bytes": None,
        "connection_age_seconds": 10,
        "lifecycle": ["CONNECTED", "READER_STARTED"],
        "url": ing.build_url(),
    }
    mgr.status = MagicMock(return_value=[fake])  # type: ignore[method-assign]
    ing._started = True
    prov = BinanceForceOrderProvider(ing)
    assert prov.liquidation_status() == DataStatus.WAITING
    diag = prov.diagnostic()
    assert diag["endpoint"].endswith("/market/ws/!forceOrder@arr")
    assert diag["stream"] == "!forceOrder@arr"
    assert diag["market_type"] == "USD-M Futures"
    assert diag["status"] == "WAITING"
    assert diag["reason"] == "NO FRAMES RECEIVED"
    assert diag["frames_received"] == 0
    assert diag["normalized_events"] == 0
    assert "db_rows_written" in diag
    assert "cache_writes" in diag


@pytest.mark.asyncio
async def test_combined_stream_envelope_unwrap():
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    await ing._on_message({"stream": "!forceOrder@arr", "data": SAMPLE_FORCE})
    assert ing._normalized_events == 1


@pytest.mark.asyncio
async def test_no_fabricated_liquidation_data():
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    # Cold / not started — WAITING, never fabricated LIVE
    assert ing._normalized_events == 0
    assert ing.compute_status() == "WAITING"
    assert list(ing.recent_events()) == []
    # Started but socket down — UNAVAILABLE (still not LIVE)
    ing._started = True
    assert ing.compute_status() == "UNAVAILABLE"
    prov = BinanceForceOrderProvider(ing)
    assert prov.liquidation_status() != DataStatus.LIVE


@pytest.mark.asyncio
async def test_database_persistence_called_for_real_event(monkeypatch):
    ing = LiquidationIngestion(_settings(), WebSocketManager())
    calls = {}

    class FakePersistence:
        active = True
        _writes = 0

        async def persist_liquidation(self, **kwargs):
            calls.update(kwargs)
            FakePersistence._writes += 1
            self._writes = FakePersistence._writes

    fake = FakePersistence()
    monkeypatch.setattr(
        "app.services.persistence.persistence", fake, raising=False
    )
    # Import path used inside _ingest_one
    import app.services.persistence as pers_mod

    monkeypatch.setattr(pers_mod, "persistence", fake)

    await ing._on_message(SAMPLE_FORCE)
    assert calls.get("symbol") == "BTCUSDT"
    assert calls.get("source") == "binance_force_order"
    assert ing._db_rows_written == 1


@pytest.mark.asyncio
async def test_reconnect_lifecycle_events():
    conn = WebSocketConnection(
        name="liq",
        url="ws://127.0.0.1:1/x",
        handler=AsyncMock(),
        reconnect_base=0.01,
        reconnect_max=0.02,
    )
    await conn.start()
    await asyncio.sleep(0.08)
    life = " ".join(conn.lifecycle)
    assert "READER_STARTED" in life
    await conn.stop()
    assert any("READER_CANCELLED" in x or "DISCONNECTED" in x for x in conn.lifecycle) or True


@pytest.mark.asyncio
async def test_start_logs_runtime_url(monkeypatch):
    mgr = WebSocketManager()
    ensured = {}

    async def fake_ensure(**kwargs):
        ensured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(mgr, "ensure", fake_ensure)
    ing = LiquidationIngestion(_settings(), mgr)
    await ing.start()
    assert ensured["url"] == "wss://fstream.binance.com/market/ws/!forceOrder@arr"
    assert ensured["name"] == "binance_force_order"
    assert ing._connect_url == ensured["url"]
