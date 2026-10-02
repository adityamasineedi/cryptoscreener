from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.ingestion.ws_manager import WebSocketConnection


@pytest.mark.asyncio
async def test_ws_connection_reconnects_on_failure():
    calls = {"n": 0}

    async def handler(_data):
        return None

    conn = WebSocketConnection(
        name="test",
        url="ws://127.0.0.1:1/invalid",  # connection refused
        handler=handler,
        reconnect_base=0.05,
        reconnect_max=0.1,
        force_reconnect_hours=24,
    )
    await conn.start()
    await asyncio.sleep(0.35)
    assert conn.connect_count == 0  # never connected
    assert conn.connected is False
    await conn.stop()
