from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger
from app.ingestion.klines import (
    generate_kline_streams,
    normalize_timeframe,
    normalize_ws_kline,
    required_connections,
    shard_streams,
    stream_name,
)
from app.ingestion.ws_forensics import ws_forensics
from app.models.ohlcv import Candle, StreamShard

logger = get_logger("kline_ws")

CandleHandler = Callable[[Candle], Awaitable[None]]


class KlineShardConnection:
    """One multiplexed kline WS connection with SUBSCRIBE batching + reconnect."""

    def __init__(
        self,
        *,
        name: str,
        base_ws: str,
        streams: list[str],
        handler: CandleHandler,
        force_reconnect_hours: float = 23.0,
        reconnect_base: float = 1.0,
        reconnect_max: float = 60.0,
        ping_interval: float = 20.0,
        subscribe_batch_size: int = 100,
    ) -> None:
        self.name = name
        self.base_ws = base_ws.rstrip("/")
        self.streams = list(streams)
        self.handler = handler
        self.force_reconnect_hours = force_reconnect_hours
        self.reconnect_base = reconnect_base
        self.reconnect_max = reconnect_max
        self.ping_interval = ping_interval
        self.subscribe_batch_size = subscribe_batch_size
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.last_message_at: datetime | None = None
        self.connect_count = 0
        self.message_count = 0
        self.candle_count = 0
        self._subscribed: set[str] = set()
        from app.ingestion.binance_futures_ws import market_ws_url

        self.forensics = ws_forensics.get_or_create(
            name=name,
            endpoint=market_ws_url(self.base_ws),
            stream_type="kline",
            shard_id=name,
            expected_streams=len(streams),
        )
        self.connection_id = self.forensics.connection_id

    async def start(self) -> None:
        if self._task and not self._task.done():
            self.forensics.note_duplicate_reconnect_blocked()
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"kline:{self.name}")
        self.forensics.note_task_created(f"kline:{self.name}")

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
        self._subscribed.clear()

    async def update_streams(self, streams: list[str]) -> None:
        """Replace stream set; reconnect only when membership actually changes."""
        new = list(streams)
        if set(new) == set(self.streams):
            # Keep existing order — avoid bounce on reshuffles
            return
        self.streams = new
        self.forensics.expected_stream_count = len(new)
        # Bounce connection so SUBSCRIBE set is rebuilt cleanly
        if self._task and not self._task.done():
            self._stop.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"kline:{self.name}")
            self.forensics.note_task_created(f"kline:{self.name}")

    async def _subscribe_all(self, ws: Any) -> None:
        self._subscribed.clear()
        unique = list(dict.fromkeys(self.streams))
        self.forensics.note_subscribe_sent(len(unique), streams=list(self.streams))
        for i in range(0, len(unique), self.subscribe_batch_size):
            batch = unique[i : i + self.subscribe_batch_size]
            msg = {
                "method": "SUBSCRIBE",
                "params": batch,
                "id": i // self.subscribe_batch_size + 1,
            }
            await ws.send(json.dumps(msg))
            self._subscribed.update(batch)
            await asyncio.sleep(0.05)
        logger.info(
            "kline_subscribed",
            name=self.name,
            connection_id=self.connection_id,
            streams=len(self._subscribed),
        )

    async def _run(self) -> None:
        attempt = 0
        # Market streams (kline) must use /market/ws after Binance USD-M split.
        from app.ingestion.binance_futures_ws import market_ws_url

        url = market_ws_url(self.base_ws)
        while not self._stop.is_set():
            if not self.streams:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                continue
            started = datetime.now(timezone.utc)
            forced_24h = False
            try:
                async with websockets.connect(
                    url,
                    ping_interval=self.ping_interval,
                    ping_timeout=20,
                    max_queue=2048,
                    open_timeout=30,
                ) as ws:
                    self.connected = True
                    self.connect_count += 1
                    attempt = 0
                    self.forensics.note_connected(endpoint=url)
                    self.forensics.note_reconnect_owner_idle()
                    logger.info(
                        "kline_ws_connected",
                        name=self.name,
                        connection_id=self.connection_id,
                        streams=len(self.streams),
                    )
                    await self._subscribe_all(ws)
                    while not self._stop.is_set():
                        age_h = (
                            datetime.now(timezone.utc) - started
                        ).total_seconds() / 3600
                        if age_h >= self.force_reconnect_hours:
                            logger.info(
                                "kline_force_reconnect_24h",
                                name=self.name,
                                connection_id=self.connection_id,
                            )
                            forced_24h = True
                            self.forensics.note_force_24h()
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        self.last_message_at = datetime.now(timezone.utc)
                        self.message_count += 1
                        nbytes = len(raw) if isinstance(raw, (bytes, str)) else 0
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning(
                                "kline_bad_json",
                                name=self.name,
                                connection_id=self.connection_id,
                            )
                            self.forensics.note_first_or_message(
                                nbytes=nbytes, parse_error=True, parsed=False
                            )
                            continue
                        # Subscribe acks
                        if isinstance(data, dict) and "result" in data:
                            self.forensics.note_subscribe_ack()
                            continue
                        if isinstance(data, dict) and data.get("e") == "listenKeyExpired":
                            continue
                        candle = normalize_ws_kline(data if isinstance(data, dict) else {})
                        if candle is None:
                            self.forensics.note_first_or_message(
                                nbytes=nbytes,
                                message_type=str(
                                    data.get("e") if isinstance(data, dict) else "unknown"
                                ),
                                parsed=False,
                            )
                            continue
                        self.candle_count += 1
                        self.forensics.note_first_or_message(
                            nbytes=nbytes,
                            message_type="kline",
                            stream=f"{candle.symbol}@{candle.timeframe}",
                            parsed=True,
                        )
                        try:
                            await self.handler(candle)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "kline_handler_failed",
                                name=self.name,
                                connection_id=self.connection_id,
                                error=str(exc),
                            )
            except asyncio.CancelledError:
                self.forensics.note_disconnect(cancelled=True)
                raise
            except ConnectionClosed as exc:
                logger.warning(
                    "kline_ws_closed",
                    name=self.name,
                    connection_id=self.connection_id,
                    code=exc.code,
                    reason=str(exc.reason),
                )
                lower = str(exc.reason).lower()
                self.forensics.note_disconnect(
                    close_code=int(exc.code) if exc.code is not None else None,
                    close_reason=str(exc.reason) if exc.reason is not None else None,
                    exception=exc,
                    forced_24h=forced_24h,
                    ping_timeout="ping" in lower and "timeout" in lower,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "kline_ws_error",
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
                if forced_24h and self.connected:
                    self.forensics.note_disconnect(forced_24h=True)
                self.connected = False
                self._subscribed.clear()

            if self._stop.is_set():
                break
            delay = min(
                self.reconnect_max,
                self.reconnect_base * (2**attempt) + random.uniform(0, 0.5),
            )
            attempt += 1
            self.forensics.note_reconnect_scheduled(delay, attempt)
            logger.info(
                "kline_reconnect_scheduled",
                name=self.name,
                connection_id=self.connection_id,
                delay=round(delay, 2),
            )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

        if not self._stop.is_set() and self.forensics.task:
            self.forensics.task.unexpected_exit_count += 1
            logger.error(
                "WS_TASK_DIED",
                connection_id=self.connection_id,
                task_name=f"kline:{self.name}",
                stream_count=len(self.streams),
            )

    def status(self) -> dict[str, Any]:
        task = self._task
        reader_running = bool(self.connected and task is not None and not task.done())
        self.forensics.reader_running = reader_running
        if self.connected and task is not None and task.done() and self.forensics.task:
            self.forensics.task.observe(task, expecting_alive=True)
        msg_age_s = None
        if self.last_message_at is not None:
            msg_age_s = round(
                (datetime.now(timezone.utc) - self.last_message_at).total_seconds(), 1
            )
        return {
            "name": self.name,
            "connection_id": self.connection_id,
            "connected": self.connected,
            "reader_running": reader_running,
            "streams": len(self.streams),
            "subscribed": len(self._subscribed),
            "connect_count": self.connect_count,
            "message_count": self.message_count,
            "candle_count": self.candle_count,
            "disconnect_count": self.forensics.disconnect_count,
            "reconnect_count": self.forensics.reconnect_count,
            "bytes_received": self.forensics.bytes_received,
            "parse_errors": self.forensics.parse_errors,
            "disconnect_reason": self.forensics.disconnect_reason.value
            if self.forensics.disconnect_reason
            else None,
            "disconnect_evidence": self.forensics.disconnect_evidence,
            "close_code": self.forensics.close_code,
            "last_message_at": self.last_message_at.isoformat()
            if self.last_message_at
            else None,
            "seconds_since_last_message": msg_age_s,
            "forensics": self.forensics.to_summary(),
        }


class KlineWebSocketManager:
    """
    Shard kline subscriptions across multiple WS connections.

    connections = ceil(n_streams / MAX_STREAMS_PER_CONNECTION)
    """

    def __init__(
        self,
        *,
        base_ws: str,
        max_streams_per_connection: int = 900,
        timeframes: list[str] | None = None,
        force_reconnect_hours: float = 23.0,
        on_candle: CandleHandler | None = None,
    ) -> None:
        self.base_ws = base_ws
        self.max_streams_per_connection = max_streams_per_connection
        self.timeframes = [
            normalize_timeframe(tf) for tf in (timeframes or ["1m", "5m", "15m", "1h", "4h", "1d"])
        ]
        self.force_reconnect_hours = force_reconnect_hours
        self.on_candle = on_candle
        self._connections: dict[str, KlineShardConnection] = {}
        self._symbols: list[str] = []
        self._streams: list[str] = []
        self._shards: list[StreamShard] = []
        self._lock = asyncio.Lock()

    def set_handler(self, handler: CandleHandler) -> None:
        self.on_candle = handler

    @property
    def active_streams(self) -> int:
        return len(self._streams)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def plan_for_symbols(self, symbols: Iterable[str]) -> dict[str, Any]:
        streams = generate_kline_streams(symbols, self.timeframes)
        shards = shard_streams(
            streams, max_streams_per_connection=self.max_streams_per_connection
        )
        return {
            "symbols": len(list(symbols)) if not isinstance(symbols, list) else len(symbols),
            "timeframes": list(self.timeframes),
            "active_streams": len(streams),
            "max_streams_per_connection": self.max_streams_per_connection,
            "websocket_connections": required_connections(
                len(streams), self.max_streams_per_connection
            ),
            "shards": [s.stream_count for s in shards],
        }

    async def sync_symbols(self, symbols: Iterable[str]) -> None:
        """Discover/rebalance streams when the symbol universe changes."""
        async with self._lock:
            syms = sorted({s.upper() for s in symbols})
            streams = generate_kline_streams(syms, self.timeframes)
            shards = shard_streams(
                streams, max_streams_per_connection=self.max_streams_per_connection
            )
            self._symbols = syms
            self._streams = streams
            self._shards = shards

            desired_names = {f"kline_shard_{s.connection_index}" for s in shards}
            # Remove obsolete shards
            for name in list(self._connections.keys()):
                if name not in desired_names:
                    await self._connections[name].stop()
                    del self._connections[name]

            handler = self._handle_candle
            for shard in shards:
                name = f"kline_shard_{shard.connection_index}"
                existing = self._connections.get(name)
                if existing is None:
                    conn = KlineShardConnection(
                        name=name,
                        base_ws=self.base_ws,
                        streams=shard.streams,
                        handler=handler,
                        force_reconnect_hours=self.force_reconnect_hours,
                    )
                    self._connections[name] = conn
                    await conn.start()
                else:
                    await existing.update_streams(shard.streams)

            logger.info(
                "kline_rebalanced",
                symbols=len(syms),
                streams=len(streams),
                connections=len(self._connections),
                max_per_conn=self.max_streams_per_connection,
            )

    async def start(self, symbols: Iterable[str]) -> None:
        await self.sync_symbols(symbols)

    async def stop(self) -> None:
        async with self._lock:
            for conn in list(self._connections.values()):
                await conn.stop()
            self._connections.clear()

    async def _handle_candle(self, candle: Candle) -> None:
        if self.on_candle is None:
            return
        await self.on_candle(candle)

    def has_stream(self, symbol: str, timeframe: str) -> bool:
        return stream_name(symbol, timeframe) in set(self._streams)

    def status(self) -> dict[str, Any]:
        return {
            "symbols": len(self._symbols),
            "timeframes": list(self.timeframes),
            "active_streams": len(self._streams),
            "websocket_connections": len(self._connections),
            "max_streams_per_connection": self.max_streams_per_connection,
            "connections": [c.status() for c in self._connections.values()],
            "connected_count": sum(1 for c in self._connections.values() if c.connected),
        }
