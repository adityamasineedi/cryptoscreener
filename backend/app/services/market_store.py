from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger
from app.models.schemas import DataStatus, MarkPriceUpdate, SymbolInfo, TickerUpdate

logger = get_logger("market_store")

Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class MarketDataStore:
    """In-process market cache with optional Redis mirroring."""

    def __init__(self) -> None:
        self.symbols: dict[str, SymbolInfo] = {}
        self.tickers: dict[str, TickerUpdate] = {}
        self.mark_prices: dict[str, MarkPriceUpdate] = {}
        self._subscribers: list[Subscriber] = []
        self._lock = asyncio.Lock()
        self.redis = None
        self.ingestion_status: str = "starting"
        self.started_at: datetime | None = None
        self._dirty_tickers: set[str] = set()
        self._dirty_marks: set[str] = set()
        self._flush_task: asyncio.Task | None = None
        self._flush_interval = 0.25  # batch browser updates

    async def connect_redis(self, redis_client) -> None:
        self.redis = redis_client
        logger.info("redis_attached")

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        self._subscribers = [c for c in self._subscribers if c is not callback]

    async def start_flusher(self) -> None:
        if self._flush_task and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._flush_loop(), name="market_flush")

    async def stop_flusher(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self.flush_dirty()

    async def flush_dirty(self) -> None:
        async with self._lock:
            tickers = list(self._dirty_tickers)
            marks = list(self._dirty_marks)
            self._dirty_tickers.clear()
            self._dirty_marks.clear()

        if not tickers and not marks:
            return

        batch: list[dict[str, Any]] = []
        for sym in tickers:
            t = self.tickers.get(sym)
            if t:
                payload = t.model_dump(mode="json")
                batch.append(
                    {
                        "type": "ticker",
                        "symbol": sym,
                        "payload": payload,
                        "timestamp": t.timestamp.isoformat(),
                    }
                )
                if self.redis is not None:
                    try:
                        await self.redis.set(
                            f"ticker:{sym}", json.dumps(payload, default=str), ex=120
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("redis_set_failed", error=str(exc))

        for sym in marks:
            m = self.mark_prices.get(sym)
            if m:
                payload = m.model_dump(mode="json")
                batch.append(
                    {
                        "type": "mark_price",
                        "symbol": sym,
                        "payload": payload,
                        "timestamp": m.timestamp.isoformat(),
                    }
                )
                if self.redis is not None:
                    try:
                        await self.redis.set(
                            f"mark:{sym}", json.dumps(payload, default=str), ex=120
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("redis_set_failed", error=str(exc))
                if m.funding_rate is not None:
                    try:
                        from app.services.persistence import persistence

                        await persistence.persist_funding(
                            sym,
                            float(m.funding_rate),
                            mark_price=m.mark_price,
                            source=m.source,
                            ts=m.timestamp,
                        )
                    except Exception:  # noqa: BLE001
                        pass

        if batch:
            event = {
                "type": "market_batch",
                "updates": batch,
                "count": len(batch),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if self.redis is not None:
                try:
                    await self.redis.publish(
                        "market:events", json.dumps(event, default=str)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("redis_publish_failed", error=str(exc))
            await self._notify(event)

    async def _notify(self, event: dict[str, Any]) -> None:
        dead: list[Subscriber] = []
        for sub in list(self._subscribers):
            try:
                await sub(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("subscriber_failed", error=str(exc))
                dead.append(sub)
        for d in dead:
            self.unsubscribe(d)

    async def set_symbols(self, symbols: list[SymbolInfo]) -> None:
        async with self._lock:
            self.symbols = {s.symbol: s for s in symbols}
        await self._notify(
            {
                "type": "symbols_updated",
                "count": len(symbols),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def upsert_symbol(self, symbol: SymbolInfo) -> None:
        async with self._lock:
            self.symbols[symbol.symbol] = symbol

    async def remove_symbol(self, symbol: str) -> None:
        async with self._lock:
            self.symbols.pop(symbol, None)
            self.tickers.pop(symbol, None)
            self.mark_prices.pop(symbol, None)

    async def update_ticker(self, ticker: TickerUpdate) -> None:
        async with self._lock:
            self.tickers[ticker.symbol] = ticker
            self._dirty_tickers.add(ticker.symbol)

    async def update_tickers_batch(self, tickers: list[TickerUpdate]) -> None:
        async with self._lock:
            for ticker in tickers:
                self.tickers[ticker.symbol] = ticker
                self._dirty_tickers.add(ticker.symbol)

    async def update_mark_price(self, mark: MarkPriceUpdate) -> None:
        async with self._lock:
            self.mark_prices[mark.symbol] = mark
            self._dirty_marks.add(mark.symbol)

    async def update_mark_prices_batch(self, marks: list[MarkPriceUpdate]) -> None:
        async with self._lock:
            for mark in marks:
                self.mark_prices[mark.symbol] = mark
                self._dirty_marks.add(mark.symbol)

    def list_symbols(self, market_type: str | None = None) -> list[SymbolInfo]:
        symbols = list(self.symbols.values())
        if market_type:
            symbols = [s for s in symbols if s.market_type == market_type]
        return sorted(symbols, key=lambda s: s.symbol)

    def get_ticker(self, symbol: str) -> TickerUpdate | None:
        return self.tickers.get(symbol.upper())

    def live_ticker_count(self) -> int:
        return len(self.tickers)

    def hydrate_funding_from_rows(self, rows: list[dict[str, Any]]) -> int:
        """Seed mark/funding from DB when WS has not filled the symbol yet."""
        if not rows:
            return 0
        n = 0
        for r in rows:
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or "").upper()
            if not sym or sym in self.mark_prices:
                continue
            fr = r.get("funding_rate")
            mp = r.get("mark_price")
            if fr is None and mp is None:
                continue
            ts = r.get("time") or datetime.now(timezone.utc)
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    ts = datetime.now(timezone.utc)
            if isinstance(ts, datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            try:
                self.mark_prices[sym] = MarkPriceUpdate(
                    symbol=sym,
                    mark_price=float(mp) if mp is not None else 0.0,
                    funding_rate=float(fr) if fr is not None else None,
                    timestamp=ts,
                    source=str(r.get("source") or "postgresql"),
                    status=DataStatus.CACHED,
                )
                n += 1
            except Exception:  # noqa: BLE001
                continue
        if n:
            logger.info("funding_hydrated_from_db", symbols=n)
        return n


market_store = MarketDataStore()
