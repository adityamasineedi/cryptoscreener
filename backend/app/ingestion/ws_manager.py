from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger

logger = get_logger("ws_manager")

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class WebSocketConnection:
    """Single multiplexed Binance WS connection with reconnect + 24h refresh."""

    def __init__(
        self,
        *,
        name: str,
        url: str,
        handler: MessageHandler,
        force_reconnect_hours: float = 23.0,
        reconnect_base: float = 1.0,
        reconnect_max: float = 60.0,
        ping_interval: float = 20.0,
    ) -> None:
        self.name = name
        self.url = url
        self.handler = handler
        self.force_reconnect_hours = force_reconnect_hours
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.ping_interval = ping_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.last_message_at: datetime | None = None
        self.connect_count = 0
        self.message_count = 0
        self.bytes_received = 0
        self.reconnect_count = 0
        self.connected_at: datetime | None = None
        self.last_frame_bytes: int | None = None
        self.last_handshake_ok: bool | None = None
        self.last_error: str | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"ws:{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            started = datetime.now(timezone.utc)
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=20,
                    max_queue=1024,
                    open_timeout=20,
                ) as ws:
                    self.connected = True
                    self.connect_count += 1
                    self.connected_at = datetime.now(timezone.utc)
                    self.last_handshake_ok = True
                    self.last_error = None
                    attempt = 0
                    logger.info("ws_connected", name=self.name, url=self.url)
                    while not self._stop.is_set():
                        # Force reconnect before Binance 24h limit
                        age_h = (
                            datetime.now(timezone.utc) - started
                        ).total_seconds() / 3600
                        if age_h >= self.force_reconnect_hours:
                            logger.info("ws_force_reconnect_24h", name=self.name)
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            # Idle timeout — connection may still be healthy via ping
                            continue
                        nbytes = len(raw) if isinstance(raw, (bytes, str)) else 0
                        self.bytes_received += nbytes
                        self.last_frame_bytes = nbytes
                        self.last_message_at = datetime.now(timezone.utc)
                        self.message_count += 1
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("ws_bad_json", name=self.name)
                            continue
                        await self.handler(data)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                self.last_error = f"closed:{exc.code}:{exc.reason}"
                logger.warning(
                    "ws_closed", name=self.name, code=exc.code, reason=str(exc.reason)
                )
            except Exception as exc:  # noqa: BLE001
                self.last_handshake_ok = False
                self.last_error = str(exc)
                logger.warning("ws_error", name=self.name, error=str(exc))
            finally:
                self.connected = False
                self.connected_at = None

            if self._stop.is_set():
                break
            delay = min(
                self.reconnect_max,
                self.reconnect_base * (2**attempt) + random.uniform(0, 0.5),
            )
            attempt += 1
            self.reconnect_count += 1
            logger.info("ws_reconnect_scheduled", name=self.name, delay=round(delay, 2))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


class WebSocketManager:
    """Manage multiple multiplexed WS connections; prevent duplicate URLs."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocketConnection] = {}

    def has(self, name: str) -> bool:
        return name in self._connections

    async def ensure(
        self,
        *,
        name: str,
        url: str,
        handler: MessageHandler,
        force_reconnect_hours: float = 23.0,
    ) -> WebSocketConnection:
        existing = self._connections.get(name)
        if existing is not None:
            if existing.url == url:
                return existing
            await existing.stop()
        conn = WebSocketConnection(
            name=name,
            url=url,
            handler=handler,
            force_reconnect_hours=force_reconnect_hours,
        )
        self._connections[name] = conn
        await conn.start()
        return conn

    async def stop_all(self) -> None:
        for conn in list(self._connections.values()):
            await conn.stop()
        self._connections.clear()

    def status(self) -> list[dict[str, Any]]:
        out = []
        now = datetime.now(timezone.utc)
        for name, conn in self._connections.items():
            age_s = None
            if conn.connected_at is not None:
                age_s = round((now - conn.connected_at).total_seconds(), 1)
            out.append(
                {
                    "name": name,
                    "connected": conn.connected,
                    "connect_count": conn.connect_count,
                    "reconnect_count": conn.reconnect_count,
                    "message_count": conn.message_count,
                    "bytes_received": conn.bytes_received,
                    "last_frame_bytes": conn.last_frame_bytes,
                    "connection_age_seconds": age_s,
                    "last_handshake_ok": conn.last_handshake_ok,
                    "last_error": conn.last_error,
                    "last_message_at": conn.last_message_at.isoformat()
                    if conn.last_message_at
                    else None,
                    "url": conn.url,
                }
            )
        return out
