"""Own `!forceOrder@arr` socket. Uses the engine parser and does not start the live app."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.ingestion.binance_futures_ws import force_order_aggregate_url
from app.research.liq_flush.costs import WS_BASE
from app.research.liq_flush.recorder import Recorder, ingest_message

UTC = timezone.utc


def stream_url(ws_base: str = WS_BASE) -> str:
    return force_order_aggregate_url(ws_base)


async def stream_force_orders(recorder: Recorder, stop: asyncio.Event, *, ws_base: str = WS_BASE) -> None:
    import websockets

    url = stream_url(ws_base)
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=2048) as socket:
                async for message in socket:
                    if stop.is_set():
                        break
                    _take(recorder, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            recorder.note_disconnect(datetime.now(UTC))
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                continue


def _take(recorder: Recorder, message: Any) -> None:
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return
    ingest_message(recorder, data, received_at=datetime.now(UTC))
