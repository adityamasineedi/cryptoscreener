from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.core.provider_health import provider_health
from app.core.request_audit import request_audit
from app.engines.filters.engine import apply_filters
from app.engines.mtf.engine import MTFEngine
from app.engines.structure.engine import StructureEngine
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.engines.volume.engine import VolumeEngine
from app.ingestion.backfill import ProgressiveBackfillService
from app.ingestion.binance_rest import BinanceRestClient
from app.ingestion.kline_ws_manager import KlineWebSocketManager
from app.ingestion.klines import normalize_timeframe
from app.ingestion.liquidations import BinanceForceOrderProvider, LiquidationIngestion
from app.ingestion.oi_scheduler import OIScheduler
from app.ingestion.providers.router import FundamentalProviderRouter
from app.ingestion.ws_manager import WebSocketManager
from app.models.ohlcv import Candle
from app.models.schemas import FreshValue
from app.services.engine_store import EngineStore, engine_store
from app.services.market_store import MarketDataStore
from app.services.ohlcv_store import OHLCVStore, ohlcv_store
from app.services.performance import performance_monitor
from app.services.persistence import persistence
from app.services.redis_state import redis_state
from app.services.retention import apply_retention

logger = get_logger("orchestrator")


class CalculationOrchestrator:
    """Wires OHLCV ingestion → engines → engine_store. No fake values."""

    def __init__(
        self,
        settings: Settings,
        market: MarketDataStore,
        rest: BinanceRestClient,
        *,
        ohlcv: OHLCVStore | None = None,
        engines: EngineStore | None = None,
    ) -> None:
        self.settings = settings
        self.market = market
        self.rest = rest
        self.ohlcv = ohlcv or ohlcv_store
        self.engines = engines or engine_store
        cfg = settings.indicators_config
        market_cfg = settings.market_config
        tfs = market_cfg.get("timeframes") or ["1m", "5m", "15m", "1h", "4h", "1d"]
        max_streams = int(
            market_cfg.get("websocket", {}).get("max_streams_per_connection")
            or settings.max_ws_streams_per_connection
            or 900
        )
        force_h = float(market_cfg.get("websocket", {}).get("force_reconnect_hours", 23))

        self.kline_ws = KlineWebSocketManager(
            base_ws=settings.binance_futures_ws,
            max_streams_per_connection=max_streams,
            timeframes=[normalize_timeframe(t) for t in tfs],
            force_reconnect_hours=force_h,
            on_candle=self.on_candle,
        )
        self.mtf = MTFEngine(cfg)
        self.structure = StructureEngine(cfg)
        self.supply_demand = SupplyDemandEngine(cfg)
        self.volume = VolumeEngine(cfg)

        self._liq_ws = WebSocketManager()
        self._liq_ingestion = LiquidationIngestion(
            settings,
            self._liq_ws,
            volume_lookup=lambda s: (
                self.market.tickers[s].quote_volume_24h
                if s in self.market.tickers
                else None
            ),
        )
        self.liquidations = BinanceForceOrderProvider(self._liq_ingestion)

        self.oi = OIScheduler(
            settings,
            rest,
            price_lookup=lambda s: (
                self.market.tickers[s].price if s in self.market.tickers else None
            ),
            volume_lookup=lambda s: float(
                self.market.tickers[s].quote_volume_24h
                if s in self.market.tickers
                and self.market.tickers[s].quote_volume_24h is not None
                else 0.0
            ),
            mcap_lookup=lambda s: float(
                (engine_store.get_fundamental(s, "market_cap").value or 0)
            ),
        )
        self.fundamentals = FundamentalProviderRouter(settings)
        self.backfill = ProgressiveBackfillService(
            settings,
            rest,
            market,
            self.ohlcv,
            on_series_ready=self._on_backfill_ready,
        )

        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._candle_events = 0
        self._calc_events = 0

    async def start(self, symbols: list[str]) -> None:
        if not self.settings.use_real_data:
            logger.error("orchestrator_refusing_mock_mode")
            return
        self._running = True
        await self.fundamentals.start()
        await self.kline_ws.start(symbols)
        await self.liquidations.start()
        self.oi.set_symbols(symbols)
        await self.oi.start()
        await self.backfill.start()
        # Background hydrate/enqueue so FastAPI lifespan can yield and bind :8000
        # immediately. Blocking here previously left the API unreachable for minutes
        # while ~symbols×TFs sequential DB loads ran (ERR_CONNECTION_REFUSED / timeouts).
        self._tasks.append(
            asyncio.create_task(
                self._hydrate_and_enqueue(symbols),
                name="hydrate_and_enqueue",
            )
        )
        self._tasks.append(asyncio.create_task(self._db_flush_loop(), name="ohlcv_db_flush"))
        self._tasks.append(asyncio.create_task(self._fundamentals_loop(), name="fundamentals"))
        self._tasks.append(asyncio.create_task(self._retention_loop(), name="retention"))
        self._tasks.append(
            asyncio.create_task(self._setup_ensure_loop(), name="setup_ensure_loop")
        )
        logger.info(
            "orchestrator_started",
            symbols=len(symbols),
            kline_plan=self.kline_ws.plan_for_symbols(symbols),
            oi=self.oi.status(),
            backfill=self.backfill.status(),
        )

    async def _hydrate_and_enqueue(self, symbols: list[str]) -> None:
        """Load OHLCV from DB, restore setups, warm engines, then enqueue gaps."""
        try:
            await self.backfill.hydrate_from_db(symbols)
            await self._hydrate_setup_signals()
            if not self._running:
                return
            self._tasks.append(
                asyncio.create_task(
                    self._warm_engines_from_store(symbols),
                    name="engine_warm_from_hydrate",
                )
            )
            await self.backfill.enqueue_universe(symbols)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("hydrate_and_enqueue_failed", error=str(exc))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        await self.backfill.stop()
        await self.kline_ws.stop()
        await self.liquidations.stop()
        await self._liq_ws.stop_all()
        await self.oi.stop()
        await self.fundamentals.close()

    async def on_symbols_changed(self, symbols: list[str]) -> None:
        await self.kline_ws.sync_symbols(symbols)
        self.oi.set_symbols(symbols)
        await self.backfill.enqueue_universe(symbols)

    async def on_candle(self, candle: Candle) -> None:
        self._candle_events += 1
        performance_monitor.record_ws_message()
        closed, _ = await self.ohlcv.upsert_candle(candle)
        if not closed:
            return
        await self._recalculate(candle.symbol, candle.timeframe)

    async def _on_backfill_ready(self, symbol: str, timeframe: str) -> None:
        await self._recalculate(symbol, timeframe)

    async def _recalculate(self, symbol: str, timeframe: str) -> None:
        self._calc_events += 1
        candles = self.ohlcv.get_candles_for_engine(symbol, timeframe, include_open=False)
        if len(candles) < 5:
            return

        inds = self.mtf.on_candle_close(symbol, timeframe, candles)
        self.engines.set_indicators(symbol, timeframe, inds)

        vol = self.volume.compute(symbol, timeframe, candles)
        self.engines.set_volume(symbol, timeframe, vol)

        if self.structure.supports_timeframe(timeframe):
            struct = self.structure.analyze(symbol, timeframe, candles)
            self.engines.set_structure(symbol, timeframe, struct)
            events = list(struct.get("events") or [])
            if events:
                await persistence.persist_structure_events(symbol, timeframe, events)
            zones = self.supply_demand.detect_zones(symbol, timeframe, candles)
            existing = [
                z
                for z in (self.engines.zones.get(symbol.upper()) or [])
                if z.get("timeframe") != timeframe
            ]
            updated = []
            for z in zones:
                z2 = self.supply_demand.update_zone_status(z, candles)
                updated.append(z2.to_dict() if hasattr(z2, "to_dict") else z2)
            self.engines.set_zones(symbol, existing + updated)
            if updated:
                await persistence.persist_supply_demand_zones(symbol, existing + updated)

        # Structure setup signal engine — candle-close / series-ready only (not tick)
        await self._recompute_setup(symbol, timeframe)

    async def _hydrate_setup_signals(self) -> None:
        """Load persisted setups into cache; mark stale vs current OHLCV for recompute."""
        try:
            from app.services.setup_signals import get_setup_signal_service

            loaded = await persistence.load_latest_setup_analyses()
            svc = get_setup_signal_service()
            stale_n = 0
            for sym, payload in loaded.items():
                self.engines.set_setup_signal(sym, payload)
                if svc.is_stale(payload, sym):
                    svc.mark_stale_if_source_advanced(sym)
                    svc.enqueue_ensure([sym])
                    stale_n += 1
            logger.info(
                "setup_signals_hydrated",
                loaded=len(loaded),
                queued_stale=stale_n,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("setup_signals_hydrate_failed", error=str(exc))

    async def _recompute_setup(self, symbol: str, timeframe: str) -> None:
        """Dependency-aware setup recompute for one symbol after a TF update."""
        try:
            from app.services.setup_signals import get_setup_signal_service

            svc = get_setup_signal_service()
            tf_l = normalize_timeframe(timeframe).lower()
            relevant = {
                svc.config.mtf_major,
                svc.config.mtf_primary,
                svc.config.mtf_setup,
                svc.config.mtf_entry,
            }
            if tf_l not in relevant:
                return
            # Mark prior result stale until recompute finishes (if source advanced)
            svc.mark_stale_if_source_advanced(symbol)
            if svc.setup_ohlcv_ready(symbol):
                payload = svc.analyze_symbol(symbol, triggered_timeframe=tf_l)
                await persistence.persist_setup_analysis(symbol, payload)
            else:
                # Honest WAITING placeholder; queue until setup TF arrives
                svc.ensure_computed(symbol, triggered_timeframe=tf_l)
                svc.enqueue_ensure([symbol])
        except Exception as exc:  # noqa: BLE001
            logger.warning("setup_signal_recalc_failed", symbol=symbol, error=str(exc))

    def request_setup_for_visible(self, symbols: list[str]) -> int:
        """Enqueue visible symbols missing/stale setups — not the full universe."""
        from app.services.setup_signals import get_setup_signal_service

        return get_setup_signal_service().enqueue_ensure(symbols)

    async def _setup_ensure_loop(self) -> None:
        """Drain visible/stale setup queue without blocking candle path."""
        from app.services.setup_signals import get_setup_signal_service

        while self._running:
            await asyncio.sleep(0.5)
            svc = get_setup_signal_service()
            try:
                updated = svc.drain_ensure_queue(limit=25)
                for sym in updated:
                    payload = self.engines.get_setup_signal(sym)
                    if payload and payload.get("signal_status") != "WAITING":
                        await persistence.persist_setup_analysis(sym, payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("setup_ensure_loop_failed", error=str(exc))

    async def _warm_engines_from_store(self, symbols: list[str]) -> None:
        """
        After DB hydrate, compute derived metrics for series that already have candles.

        Does NOT wait for universe 95% — each symbol/TF computes as soon as local
        OHLCV exists. Prioritizes visible + higher TFs lightly via ordering.
        """
        tfs = [
            normalize_timeframe(t)
            for t in (getattr(self.backfill, "tf_priority", None) or ["1d", "4h", "1h", "15m", "5m"])
        ]
        # Prefer screener-critical 15m early in warm pass after higher TF seeds
        ordered_tfs: list[str] = []
        for tf in tfs:
            if tf not in ordered_tfs:
                ordered_tfs.append(tf)
        if "15m" in ordered_tfs:
            ordered_tfs.remove("15m")
            # After 1h if present, else near front
            if "1h" in ordered_tfs:
                idx = ordered_tfs.index("1h") + 1
                ordered_tfs.insert(idx, "15m")
            else:
                ordered_tfs.insert(0, "15m")

        visible = set(getattr(self.backfill, "_visible", set()) or set())
        ranked = sorted(
            symbols,
            key=lambda s: (0 if s.upper() in visible else 1, -(self.backfill._volume(s) if hasattr(self.backfill, "_volume") else 0)),
        )
        warmed = 0
        for sym in ranked:
            if not self._running:
                break
            for tf in ordered_tfs:
                candles = self.ohlcv.get_closed(sym, tf)
                if len(candles) < 5:
                    continue
                try:
                    await self._recalculate(sym, tf)
                    warmed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "engine_warm_failed",
                        symbol=sym,
                        timeframe=tf,
                        error=str(exc),
                    )
                # Yield so WS / backfill workers stay responsive
                if warmed % 20 == 0:
                    await asyncio.sleep(0)
        logger.info("engine_warm_from_hydrate_done", recalcs=warmed, symbols=len(symbols))

    async def _db_flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(2.0)
            await self.ohlcv.flush_db()
            await persistence.flush_pending_signals()

    async def _retention_loop(self) -> None:
        while self._running:
            try:
                await apply_retention(self.settings)
            except Exception as exc:  # noqa: BLE001
                logger.warning("retention_loop_failed", error=str(exc))
            await asyncio.sleep(3600)

    async def _fundamentals_loop(self) -> None:
        # Initial delay so ticker/OI get headroom first
        await asyncio.sleep(15)
        while self._running:
            try:
                await self._refresh_fundamentals()
            except Exception as exc:  # noqa: BLE001
                logger.warning("fundamentals_refresh_failed", error=str(exc))
            await asyncio.sleep(self.fundamentals.refresh_seconds)

    async def _refresh_fundamentals(self) -> None:
        symbols = list(self.market.symbols.keys())
        if not symbols:
            return
        data = await self.fundamentals.refresh(symbols)
        for sym, fields in data.items():
            self.engines.set_fundamentals(sym, fields)
            await redis_state.store_fundamentals(
                sym,
                {k: v.model_dump(mode="json") for k, v in fields.items()},
            )
        # Persist symbol metadata + redis mirror (optional)
        await persistence.upsert_symbols(list(self.market.symbols.values()))
        await redis_state.store_symbol_metadata(
            [s.model_dump(mode="json") for s in self.market.symbols.values()]
        )
        await redis_state.store_market_state(
            {
                "symbols": len(self.market.symbols),
                "tickers_live": self.market.live_ticker_count(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def apply_row_filters(
        self, rows: list[dict[str, Any]], filters: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return apply_filters(rows, filters)

    async def stats(self) -> dict[str, Any]:
        kstat = self.kline_ws.status()
        audit = await request_audit.stats_last_minute()
        return {
            "candle_events": self._candle_events,
            "calc_events": self._calc_events,
            "kline": kstat,
            "ohlcv": self.ohlcv.stats(),
            "oi": self.oi.status(),
            "liquidations": self.liquidations.status(),
            "backfill": self.backfill.status(),
            "fundamentals": self.fundamentals.status(),
            "provider_health": await provider_health.snapshot_all(),
            "rest_last_minute": audit,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


orchestrator: CalculationOrchestrator | None = None


def get_orchestrator() -> CalculationOrchestrator | None:
    return orchestrator
