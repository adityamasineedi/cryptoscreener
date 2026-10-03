from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.engines import orchestrator as orch_mod
from app.engines.orchestrator import CalculationOrchestrator
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.normalizer import (
    normalize_futures_ticker_array,
    normalize_mark_price_array,
    normalize_rest_premium_index,
    normalize_rest_ticker_24hr,
)
from app.ingestion.symbol_discovery import SymbolDiscovery
from app.ingestion.ws_manager import WebSocketManager
from app.models.schemas import SymbolInfo
from app.services.market_store import MarketDataStore
from app.services.ohlcv_store import ohlcv_store
from app.services.redis_manager import redis_manager

logger = get_logger("ingestion")


class MarketDataIngestionService:
    """
    Exchange → WebSocket Manager → Normalizer → Market Store (Redis)

    Uses all-market array streams where possible (NOT one WS per coin).
    If WS is silent (common on filtered networks), falls back to batched REST
    snapshots: /ticker/24hr and /premiumIndex — still one request for all symbols.
    """

    def __init__(self, settings: Settings, store: MarketDataStore) -> None:
        self.settings = settings
        self.store = store
        self.rest = BinanceRestClient(settings)
        self.discovery = SymbolDiscovery(
            self.rest, store, quote=settings.subscribe_quote
        )
        self.ws = WebSocketManager()
        self.orchestrator: CalculationOrchestrator | None = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._known_symbols: dict[str, SymbolInfo] = {}
        self._ws_message_count = 0
        self._feed_mode = "starting"

    async def start(self) -> None:
        if not self.settings.use_real_data:
            self.store.ingestion_status = "blocked:USE_REAL_DATA=false"
            logger.error("refusing_to_start_without_real_data_mode")
            return
        await self.rest.start()
        self._running = True
        self.store.started_at = datetime.now(timezone.utc)
        self.store.ingestion_status = "discovering_symbols"
        try:
            symbols = await self.discovery.refresh()
            self._known_symbols = {s.symbol: s for s in symbols}
        except Exception as exc:  # noqa: BLE001
            self.store.ingestion_status = f"symbol_discovery_failed:{exc}"
            logger.error("symbol_discovery_failed", error=str(exc))
            self._tasks.append(asyncio.create_task(self._symbol_refresh_loop()))
            self._tasks.append(asyncio.create_task(self._rest_snapshot_loop()))
            return

        await self.store.start_flusher()
        if redis_manager.client is not None:
            await ohlcv_store.connect_redis(redis_manager.client)
        await self._subscribe_all_market_streams()
        # Immediate REST snapshot so UI has real prices even before WS frames arrive
        await self._poll_rest_snapshots()
        self.orchestrator = CalculationOrchestrator(
            self.settings, self.store, self.rest
        )
        orch_mod.orchestrator = self.orchestrator
        await self.orchestrator.start(list(self._known_symbols.keys()))
        self.store.ingestion_status = "live"
        self._tasks.append(asyncio.create_task(self._symbol_refresh_loop()))
        self._tasks.append(asyncio.create_task(self._rest_snapshot_loop()))
        logger.info("ingestion_started", symbols=len(self._known_symbols))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        if self.orchestrator is not None:
            await self.orchestrator.stop()
            self.orchestrator = None
            orch_mod.orchestrator = None
        await self.store.stop_flusher()
        await self.ws.stop_all()
        await self.rest.close()
        self.store.ingestion_status = "stopped"

    async def _subscribe_all_market_streams(self) -> None:
        base = self.settings.binance_futures_ws.rstrip("/")
        force_h = float(
            self.settings.market_config.get("websocket", {}).get(
                "force_reconnect_hours", 23
            )
        )

        # USD-M market streams require /market/ws (legacy /ws accepts sockets
        # but delivers zero frames after Binance's 2026-04-23 path split).
        from app.ingestion.binance_futures_ws import market_ws_url

        await self.ws.ensure(
            name="futures_ticker_arr",
            url=market_ws_url(base, "!ticker@arr"),
            handler=self._on_ticker_message,
            force_reconnect_hours=force_h,
            stream_type="ticker",
            expected_streams=1,
        )
        await self.ws.ensure(
            name="futures_mark_price_arr",
            url=market_ws_url(base, "!markPrice@arr@1s"),
            handler=self._on_mark_message,
            force_reconnect_hours=force_h,
            stream_type="mark_price",
            expected_streams=1,
        )
        logger.info("all_market_streams_subscribed")

    async def _on_ticker_message(self, data: Any) -> None:
        from app.ingestion.handler_latency import handler_latency

        with handler_latency.time("ticker_message"):
            self._ws_message_count += 1
            self._feed_mode = "websocket"
            allowed = self._known_symbols
            ticks = normalize_futures_ticker_array(data)
            filtered = []
            for tick in ticks:
                if tick.symbol not in allowed:
                    continue
                tick.market_type = allowed[tick.symbol].market_type
                filtered.append(tick)
            if filtered:
                await self.store.update_tickers_batch(filtered)
                # Keep forming candles glued to live last price even when kline WS is silent
                with handler_latency.time("ticker_apply_live_price"):
                    for tick in filtered:
                        if tick.price:
                            try:
                                await ohlcv_store.apply_live_price(
                                    tick.symbol, float(tick.price)
                                )
                            except Exception:  # noqa: BLE001
                                pass

    async def _on_mark_message(self, data: Any) -> None:
        from app.ingestion.handler_latency import handler_latency

        with handler_latency.time("mark_message"):
            self._ws_message_count += 1
            self._feed_mode = "websocket"
            allowed = self._known_symbols
            marks = normalize_mark_price_array(data)
            filtered = [m for m in marks if m.symbol in allowed]
            if filtered:
                await self.store.update_mark_prices_batch(filtered)

    async def _poll_rest_snapshots(self) -> None:
        """Batched REST backfill / fallback — never one-request-per-coin."""
        allowed = self._known_symbols
        try:
            raw_tickers = await self.rest.futures_ticker_24hr_all()
            ticks = []
            for item in raw_tickers:
                tick = normalize_rest_ticker_24hr(item)
                if tick is None or tick.symbol not in allowed:
                    continue
                tick.market_type = allowed[tick.symbol].market_type
                ticks.append(tick)
            if ticks:
                await self.store.update_tickers_batch(ticks)
                await self.store.flush_dirty()
                for tick in ticks:
                    if tick.price:
                        try:
                            await ohlcv_store.apply_live_price(tick.symbol, float(tick.price))
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("rest_ticker_snapshot_failed", error=str(exc))

        try:
            raw_marks = await self.rest.futures_premium_index()
            marks = []
            for item in raw_marks:
                mark = normalize_rest_premium_index(item)
                if mark is None or mark.symbol not in allowed:
                    continue
                marks.append(mark)
            if marks:
                await self.store.update_mark_prices_batch(marks)
                await self.store.flush_dirty()
        except Exception as exc:  # noqa: BLE001
            logger.warning("rest_premium_snapshot_failed", error=str(exc))

        if self._ws_message_count == 0:
            self._feed_mode = "rest_batch_fallback"
            logger.info(
                "using_rest_batch_fallback",
                reason="websocket_silent",
                tickers=self.store.live_ticker_count(),
            )

    async def _rest_snapshot_loop(self) -> None:
        """
        Poll batched REST when WS is silent or as sparse refresh.
        Weight budget: ~40 + 10 = 50 per cycle. At 2s interval ≈ 1500/min worst case —
        we slow down when WS is healthy.
        """
        while self._running:
            ws_healthy = self._ws_message_count > 0 and any(
                c.get("connected") for c in self.ws.status()
            )
            # If WS delivering, only refresh REST every 30s as safety net.
            # If WS silent, poll every 2s so the screener stays live.
            await asyncio.sleep(45.0 if ws_healthy else 5.0)
            if not self._running:
                break
            prev = self._ws_message_count
            await self._poll_rest_snapshots()
            if self._ws_message_count == prev and not ws_healthy:
                self._feed_mode = "rest_batch_fallback"

    async def _symbol_refresh_loop(self) -> None:
        interval = self.settings.symbol_refresh_seconds
        while self._running:
            await asyncio.sleep(interval)
            try:
                previous = dict(self._known_symbols)
                current = await self.discovery.refresh()
                listed, delisted = await self.discovery.detect_changes(
                    previous, current
                )
                self._known_symbols = {s.symbol: s for s in current}
                if listed:
                    logger.info(
                        "new_symbols_listed",
                        symbols=[s.symbol for s in listed[:20]],
                        count=len(listed),
                    )
                if delisted:
                    for sym in delisted:
                        await self.store.remove_symbol(sym)
                        ohlcv_store.remove_symbol(sym)
                    logger.info(
                        "symbols_delisted",
                        symbols=delisted[:20],
                        count=len(delisted),
                    )
                if listed or delisted:
                    if self.orchestrator is not None:
                        await self.orchestrator.on_symbols_changed(
                            list(self._known_symbols.keys())
                        )
                if self.store.ingestion_status.startswith("symbol_discovery_failed"):
                    await self._subscribe_all_market_streams()
                    if self.orchestrator is None:
                        self.orchestrator = CalculationOrchestrator(
                            self.settings, self.store, self.rest
                        )
                        orch_mod.orchestrator = self.orchestrator
                        await self.orchestrator.start(list(self._known_symbols.keys()))
                    self.store.ingestion_status = "live"
            except Exception as exc:  # noqa: BLE001
                logger.warning("symbol_refresh_failed", error=str(exc))

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.store.ingestion_status,
            "feed_mode": self._feed_mode,
            "ws_messages": self._ws_message_count,
            "symbols": len(self._known_symbols),
            "tickers": self.store.live_ticker_count(),
            "mark_prices": len(self.store.mark_prices),
            "websockets": self.ws.status(),
            "kline": self.orchestrator.kline_ws.status()
            if self.orchestrator
            else None,
            "ohlcv": ohlcv_store.stats(),
            "started_at": self.store.started_at.isoformat()
            if self.store.started_at
            else None,
        }
        return out


ingestion_service: MarketDataIngestionService | None = None


def get_ingestion() -> MarketDataIngestionService | None:
    return ingestion_service
