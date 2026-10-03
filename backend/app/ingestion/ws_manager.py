from __future__ import annotations

import asyncio
import json
import random
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger
from app.ingestion.ws_forensics import ws_forensics

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
        stream_type: str = "market",
        expected_streams: int = 1,
        open_timeout: float = 20.0,
    ) -> None:
        self.name = name
        self.url = url
        self.handler = handler
        self.force_reconnect_hours = force_reconnect_hours
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.ping_interval = ping_interval
        self.stream_type = stream_type
        self.expected_streams = expected_streams
        self.open_timeout = open_timeout
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.reader_running = False
        self.last_message_at: datetime | None = None
        self.connect_count = 0
        self.connect_attempts = 0
        self.disconnect_count = 0
        self.message_count = 0
        self.frames_received = 0
        self.bytes_received = 0
        self.reconnect_count = 0
        self.connected_at: datetime | None = None
        self.last_frame_bytes: int | None = None
        self.last_frame_preview: str | None = None
        self.last_handshake_ok: bool | None = None
        self.last_error: str | None = None
        self.lifecycle: deque[str] = deque(maxlen=50)
        self.forensics = ws_forensics.get_or_create(
            name=name,
            endpoint=url,
            stream_type=stream_type,
            shard_id=name,
            expected_streams=expected_streams,
        )
        self.connection_id = self.forensics.connection_id

    def _life(self, phase: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.lifecycle.append(f"{stamp} {phase}")
        logger.info(
            "ws_lifecycle",
            name=self.name,
            connection_id=self.connection_id,
            phase=phase,
            url=self.url,
        )

    async def start(self) -> None:
        if self._task and not self._task.done():
            self.forensics.note_duplicate_reconnect_blocked()
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"ws:{self.name}")
        self.forensics.note_task_created(f"ws:{self.name}")
        self._life("READER_STARTED")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            self._life("READER_CANCELLED")
        self.connected = False
        self.reader_running = False

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            started = datetime.now(timezone.utc)
            self.connect_attempts += 1
            forced_24h = False
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=20,
                    max_queue=1024,
                    open_timeout=self.open_timeout,
                ) as ws:
                    self.connected = True
                    self.reader_running = True
                    self.connect_count += 1
                    self.connected_at = datetime.now(timezone.utc)
                    self.last_handshake_ok = True
                    self.last_error = None
                    attempt = 0
                    self.forensics.note_connected(endpoint=self.url)
                    self.forensics.note_reconnect_owner_idle()
                    # URL-subscribed streams: treat expected as requested+active
                    self.forensics.note_subscribe_sent(
                        self.expected_streams,
                        streams=[self.name] if self.expected_streams else [],
                    )
                    self.forensics.note_subscribe_ack(ack_count=self.expected_streams)
                    self._life("CONNECTED")
                    logger.info(
                        "ws_connected",
                        name=self.name,
                        connection_id=self.connection_id,
                        url=self.url,
                    )
                    while not self._stop.is_set():
                        # Force reconnect before Binance 24h limit
                        age_h = (
                            datetime.now(timezone.utc) - started
                        ).total_seconds() / 3600
                        if age_h >= self.force_reconnect_hours:
                            logger.info(
                                "ws_force_reconnect_24h",
                                name=self.name,
                                connection_id=self.connection_id,
                            )
                            forced_24h = True
                            self.forensics.note_force_24h()
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
                        self.frames_received += 1
                        if isinstance(raw, bytes):
                            preview = raw.decode("utf-8", errors="replace")[:200]
                        else:
                            preview = str(raw)[:200]
                        self.last_frame_preview = preview
                        if self.frames_received == 1:
                            self._life("FIRST_FRAME")
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning(
                                "ws_bad_json",
                                name=self.name,
                                connection_id=self.connection_id,
                            )
                            self.forensics.note_first_or_message(
                                nbytes=nbytes, parse_error=True, parsed=False
                            )
                            continue
                        msg_type = None
                        if isinstance(data, dict):
                            msg_type = str(data.get("e") or data.get("stream") or "json")
                        self.forensics.note_first_or_message(
                            nbytes=nbytes,
                            message_type=msg_type,
                            stream=self.name,
                            parsed=True,
                        )
                        try:
                            await self.handler(data)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "ws_handler_failed",
                                name=self.name,
                                connection_id=self.connection_id,
                                error=str(exc),
                            )
                            # Handler errors must not kill the reader; record only.
                            self.forensics.parse_errors += 1
            except asyncio.CancelledError:
                self.reader_running = False
                self.forensics.note_disconnect(cancelled=True)
                if self.forensics.task:
                    self.forensics.task.last_cancelled = True
                raise
            except ConnectionClosed as exc:
                self.last_error = f"closed:{exc.code}:{exc.reason}"
                logger.warning(
                    "ws_closed",
                    name=self.name,
                    connection_id=self.connection_id,
                    code=exc.code,
                    reason=str(exc.reason),
                )
                ping_to = "ping" in str(exc.reason).lower() and "timeout" in str(
                    exc.reason
                ).lower()
                self.forensics.note_disconnect(
                    close_code=int(exc.code) if exc.code is not None else None,
                    close_reason=str(exc.reason) if exc.reason is not None else None,
                    exception=exc,
                    forced_24h=forced_24h,
                    ping_timeout=ping_to,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_handshake_ok = False
                self.last_error = str(exc)
                logger.warning(
                    "ws_error",
                    name=self.name,
                    connection_id=self.connection_id,
                    error=str(exc),
                )
                lower = str(exc).lower()
                self.forensics.note_disconnect(
                    exception=exc,
                    forced_24h=forced_24h,
                    ping_timeout="ping" in lower and "timeout" in lower,
                    pong_timeout="pong" in lower and "timeout" in lower,
                )
            finally:
                was_connected = self.connected
                if forced_24h and was_connected:
                    # Clean client-side break — classify if not already recorded
                    if self.forensics.disconnect_reason is None or (
                        self.forensics.closed_at
                        and (datetime.now(timezone.utc) - self.forensics.closed_at).total_seconds()
                        > 1
                    ):
                        self.forensics.note_disconnect(forced_24h=True)
                self.connected = False
                self.reader_running = False
                self.connected_at = None
                if was_connected:
                    self.disconnect_count += 1
                    self._life("DISCONNECTED")
                # Detect silent task death pattern for diagnostics
                if self.forensics.task and self._task and self._task.done():
                    self.forensics.task.observe(self._task, expecting_alive=not self._stop.is_set())

            if self._stop.is_set():
                break
            delay = min(
                self.reconnect_max,
                self.reconnect_base * (2**attempt) + random.uniform(0, 0.5),
            )
            attempt += 1
            self.reconnect_count += 1
            self.forensics.note_reconnect_scheduled(delay, attempt)
            self._life("RECONNECTING")
            logger.info(
                "ws_reconnect_scheduled",
                name=self.name,
                connection_id=self.connection_id,
                delay=round(delay, 2),
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

        # Loop exited — if stop not set, unexpected reader death
        if not self._stop.is_set() and self.forensics.task:
            self.forensics.task.unexpected_exit_count += 1
            logger.error(
                "WS_TASK_DIED",
                connection_id=self.connection_id,
                task_name=f"ws:{self.name}",
                last_message_age=(
                    (
                        datetime.now(timezone.utc) - self.last_message_at
                    ).total_seconds()
                    if self.last_message_at
                    else None
                ),
                stream_count=self.forensics.stream_count,
            )


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
        stream_type: str = "market",
        expected_streams: int = 1,
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
            stream_type=stream_type,
            expected_streams=expected_streams,
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
            msg_age_s = None
            if conn.last_message_at is not None:
                msg_age_s = round((now - conn.last_message_at).total_seconds(), 1)
            task = conn._task
            reader_running = bool(
                conn.reader_running and task is not None and not task.done()
            )
            # Sync forensics reader flag with task liveness
            conn.forensics.reader_running = reader_running
            if conn.connected and task is not None and task.done():
                # CONNECTED but reader DEAD
                if conn.forensics.task:
                    conn.forensics.task.observe(task, expecting_alive=True)
            out.append(
                {
                    "name": name,
                    "connection_id": conn.connection_id,
                    "connected": conn.connected,
                    "reader_running": reader_running,
                    "connect_count": conn.connect_count,
                    "connect_attempts": conn.connect_attempts,
                    "disconnect_count": conn.disconnect_count,
                    "reconnect_count": conn.reconnect_count,
                    "message_count": conn.message_count,
                    "frames_received": conn.frames_received,
                    "bytes_received": conn.bytes_received,
                    "last_frame_bytes": conn.last_frame_bytes,
                    "last_frame_preview": conn.last_frame_preview,
                    "connection_age_seconds": age_s,
                    "seconds_since_last_message": msg_age_s,
                    "last_handshake_ok": conn.last_handshake_ok,
                    "last_error": conn.last_error,
                    "last_message_at": conn.last_message_at.isoformat()
                    if conn.last_message_at
                    else None,
                    "lifecycle": list(conn.lifecycle),
                    "url": conn.url,
                    "disconnect_reason": conn.forensics.disconnect_reason.value
                    if conn.forensics.disconnect_reason
                    else None,
                    "disconnect_evidence": conn.forensics.disconnect_evidence,
                    "close_code": conn.forensics.close_code,
                    "forensics": conn.forensics.to_summary(),
                }
            )
        return out
