"""Live forming-candle tips from Binance @trade streams.

Kline WS is often silent on filtered networks; @trade streams still deliver.
Each trade updates open OHLC for configured timeframes and closes buckets
when the interval rolls.

Streams are sharded across connections — a single combined URL with 80+
streams gets killed (1006) due to URL length limits.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger
from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe
from app.ingestion.ws_forensics import ws_forensics
from app.models.ohlcv import Candle
from app.models.schemas import DataStatus
from app.services.ohlcv_store import OHLCVStore

logger = get_logger("trade_tip_ws")

MAX_STREAMS_PER_CONN = 40


def _bucket_open(ts: datetime, timeframe: str) -> datetime:
    tf = normalize_timeframe(timeframe)
    step = TIMEFRAME_MS.get(tf, 60_000)
    ms = int(ts.timestamp() * 1000)
    open_ms = ms - (ms % step)
    return datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)


class _TradeShard:
    def __init__(
        self,
        *,
        name: str,
        base_ws: str,
        symbols: list[str],
        apply_trade: Callable[[str, float, float, datetime], Awaitable[None]],
    ) -> None:
        self.name = name
        self.base_ws = base_ws.rstrip("/")
        self.symbols = list(symbols)
        self.apply_trade = apply_trade
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected = False
        self.message_count = 0
        self.trade_count = 0
        self.last_message_at: datetime | None = None
        self.connect_count = 0
        self.forensics = ws_forensics.get_or_create(
            name=name,
            endpoint=self._url(),
            stream_type="trade_tip",
            shard_id=name,
            expected_streams=len(symbols),
        )
        self.connection_id = self.forensics.connection_id

    def status(self) -> dict[str, Any]:
        task = self._task
        reader_running = bool(self.connected and task is not None and not task.done())
        self.forensics.reader_running = reader_running
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
            "symbols": len(self.symbols),
            "message_count": self.message_count,
            "trade_count": self.trade_count,
            "connect_count": self.connect_count,
            "disconnect_count": self.forensics.disconnect_count,
            "reconnect_count": self.forensics.reconnect_count,
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

    async def start(self) -> None:
        if self._task and not self._task.done():
            self.forensics.note_duplicate_reconnect_blocked()
            return
        if not self.symbols:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"trade_tip:{self.name}")
        self.forensics.note_task_created(f"trade_tip:{self.name}")

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

    def _url(self) -> str:
        from app.ingestion.binance_futures_ws import market_combined_url

        return market_combined_url(
            self.base_ws, [f"{s.lower()}@trade" for s in self.symbols]
        )

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if not self.symbols:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
                continue
            url = self._url()
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=4096,
                    open_timeout=30,
                    max_size=2**23,
                ) as ws:
                    self.connected = True
                    self.connect_count += 1
                    attempt = 0
                    streams = [f"{s.lower()}@trade" for s in self.symbols]
                    self.forensics.note_connected(endpoint=url)
                    self.forensics.note_reconnect_owner_idle()
                    self.forensics.note_subscribe_sent(len(streams), streams=streams)
                    # Combined URL streams are active at connect; no SUBSCRIBE ack
                    self.forensics.note_subscribe_ack(ack_count=len(streams))
                    logger.info(
                        "trade_tip_ws_connected",
                        name=self.name,
                        connection_id=self.connection_id,
                        symbols=len(self.symbols),
                    )
                    while not self._stop.is_set():
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
                            self.forensics.note_first_or_message(
                                nbytes=nbytes, parse_error=True, parsed=False
                            )
                            continue
                        payload = data.get("data", data) if isinstance(data, dict) else None
                        if not isinstance(payload, dict):
                            self.forensics.note_first_or_message(
                                nbytes=nbytes, parsed=False
                            )
                            continue
                        if payload.get("e") not in ("trade", "aggTrade"):
                            self.forensics.note_first_or_message(
                                nbytes=nbytes,
                                message_type=str(payload.get("e")),
                                parsed=False,
                            )
                            continue
                        sym = str(payload.get("s") or "").upper()
                        if not sym:
                            continue
                        try:
                            price = float(payload["p"])
                            qty = float(payload.get("q") or 0.0)
                            ts_ms = int(payload.get("T") or payload.get("E") or 0)
                        except (KeyError, TypeError, ValueError):
                            self.forensics.note_first_or_message(
                                nbytes=nbytes, parse_error=True, parsed=False
                            )
                            continue
                        if price <= 0 or ts_ms <= 0:
                            continue
                        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                        self.trade_count += 1
                        self.forensics.note_first_or_message(
                            nbytes=nbytes,
                            message_type=str(payload.get("e")),
                            stream=f"{sym.lower()}@trade",
                            parsed=True,
                        )
                        await self.apply_trade(sym, price, qty, ts)
            except asyncio.CancelledError:
                self.forensics.note_disconnect(cancelled=True)
                raise
            except ConnectionClosed as exc:
                logger.warning(
                    "trade_tip_ws_closed",
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
                    ping_timeout="ping" in lower and "timeout" in lower,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "trade_tip_ws_error",
                    name=self.name,
                    connection_id=self.connection_id,
                    error=str(exc),
                )
                lower = str(exc).lower()
                self.forensics.note_disconnect(
                    exception=exc,
                    ping_timeout="ping" in lower and "timeout" in lower,
                    pong_timeout="pong" in lower and "timeout" in lower,
                )
            finally:
                self.connected = False

            if self._stop.is_set():
                break
            delay = min(60.0, (2**attempt) + random.uniform(0, 0.5))
            attempt += 1
            self.forensics.note_reconnect_scheduled(delay, attempt)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

        if not self._stop.is_set() and self.forensics.task:
            self.forensics.task.unexpected_exit_count += 1
            logger.error(
                "WS_TASK_DIED",
                connection_id=self.connection_id,
                task_name=f"trade_tip:{self.name}",
                stream_count=len(self.symbols),
            )


class TradeTipWebSocket:
    """Sharded @trade feed that keeps forming candles live."""

    def __init__(
        self,
        *,
        base_ws: str,
        ohlcv: OHLCVStore,
        timeframes: list[str] | None = None,
        on_closed: Callable[[Candle], Awaitable[None]] | None = None,
        max_symbols: int = 80,
        max_streams_per_connection: int = MAX_STREAMS_PER_CONN,
    ) -> None:
        self.base_ws = base_ws.rstrip("/")
        self.ohlcv = ohlcv
        self.timeframes = [
            normalize_timeframe(t) for t in (timeframes or ["1m", "5m", "15m"])
        ]
        self.on_closed = on_closed
        self.max_symbols = max(10, int(max_symbols))
        self.max_streams_per_connection = max(10, int(max_streams_per_connection))
        self._symbols: list[str] = []
        self._shards: list[_TradeShard] = []

    @property
    def connected(self) -> bool:
        return any(s.connected for s in self._shards)

    @property
    def message_count(self) -> int:
        return sum(s.message_count for s in self._shards)

    @property
    def trade_count(self) -> int:
        return sum(s.trade_count for s in self._shards)

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "symbols": len(self._symbols),
            "timeframes": list(self.timeframes),
            "message_count": self.message_count,
            "trade_count": self.trade_count,
            "shards": [s.status() for s in self._shards],
            "connected_count": sum(1 for s in self._shards if s.connected),
        }

    async def sync_symbols(self, symbols: Iterable[str]) -> None:
        syms = sorted({s.upper() for s in symbols})[: self.max_symbols]
        if syms == self._symbols and self._shards:
            return
        await self.stop()
        self._symbols = syms
        if self._symbols:
            await self.start()

    async def start(self) -> None:
        if self._shards:
            return
        if not self._symbols:
            return
        n = max(1, math.ceil(len(self._symbols) / self.max_streams_per_connection))
        self._shards = []
        for i in range(n):
            start = i * self.max_streams_per_connection
            end = start + self.max_streams_per_connection
            shard = _TradeShard(
                name=f"trade_shard_{i}",
                base_ws=self.base_ws,
                symbols=self._symbols[start:end],
                apply_trade=self._apply_trade,
            )
            self._shards.append(shard)
            await shard.start()
        logger.info(
            "trade_tip_sharded",
            symbols=len(self._symbols),
            shards=len(self._shards),
        )

    async def stop(self) -> None:
        for shard in self._shards:
            await shard.stop()
        self._shards = []

    async def _apply_trade(self, symbol: str, price: float, qty: float, ts: datetime) -> None:
        from app.ingestion.handler_latency import handler_latency

        with handler_latency.time("trade_tip_upsert_candle"):
            await self._apply_trade_inner(symbol, price, qty, ts)

    async def _apply_trade_inner(
        self, symbol: str, price: float, qty: float, ts: datetime
    ) -> None:
        for tf in self.timeframes:
            step_ms = TIMEFRAME_MS.get(tf, 60_000)
            bucket = _bucket_open(ts, tf)
            close_time = bucket + timedelta(milliseconds=step_ms - 1)
            open_c = self.ohlcv.get_open(symbol, tf)

            if open_c is not None and open_c.open_time < bucket:
                closed = open_c.model_copy(
                    update={
                        "is_closed": True,
                        "close_time": open_c.open_time
                        + timedelta(milliseconds=step_ms - 1),
                        "timestamp": ts,
                        "source": "binance_trade_roll",
                        "status": DataStatus.LIVE,
                    }
                )
                await self.ohlcv.upsert_candle(closed)
                if self.on_closed is not None:
                    try:
                        await self.on_closed(closed)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("trade_tip_closed_handler_failed", error=str(exc))
                open_c = None

            if open_c is None or open_c.open_time != bucket:
                candle = Candle(
                    symbol=symbol,
                    timeframe=tf,
                    open_time=bucket,
                    close_time=close_time,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=max(qty, 0.0),
                    is_closed=False,
                    timestamp=ts,
                    source="binance_trade",
                    status=DataStatus.LIVE,
                )
                await self.ohlcv.upsert_candle(candle)
                continue

            hi = max(float(open_c.high), price)
            lo = min(float(open_c.low), price)
            vol = float(open_c.volume or 0.0) + max(qty, 0.0)
            updated = open_c.model_copy(
                update={
                    "high": hi,
                    "low": lo,
                    "close": price,
                    "volume": vol,
                    "timestamp": ts,
                    "source": "binance_trade",
                    "status": DataStatus.LIVE,
                }
            )
            await self.ohlcv.upsert_candle(updated)
