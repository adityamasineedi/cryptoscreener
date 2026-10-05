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
from app.ingestion.trade_tip_ws import TradeTipWebSocket
from app.ingestion.klines import normalize_timeframe
from app.ingestion.liquidations import BinanceForceOrderProvider, LiquidationIngestion
from app.ingestion.oi_scheduler import OIScheduler
from app.ingestion.providers.router import FundamentalProviderRouter
from app.ingestion.providers.sentiment import get_sentiment_provider
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
        ws_cfg = market_cfg.get("websocket") or {}
        # Live WS TF set from market.yaml kline_live_timeframes (include 1h/4h for v1).
        live_tfs = ws_cfg.get("kline_live_timeframes") or ["1m", "5m", "15m", "1h", "4h"]
        max_streams = int(
            ws_cfg.get("max_streams_per_connection")
            or settings.max_ws_streams_per_connection
            or 900
        )
        force_h = float(ws_cfg.get("force_reconnect_hours", 23))
        self._kline_live_max = int(ws_cfg.get("kline_live_max_symbols") or 120)
        self._kline_focus: set[str] = set()
        self._kline_live_sticky: set[str] = set()
        self._universe_symbols: list[str] = []

        self.kline_ws = KlineWebSocketManager(
            base_ws=settings.binance_futures_ws,
            max_streams_per_connection=max_streams,
            timeframes=[normalize_timeframe(t) for t in live_tfs],
            force_reconnect_hours=force_h,
            on_candle=self.on_candle,
        )
        self.trade_tips = TradeTipWebSocket(
            base_ws=settings.binance_futures_ws,
            ohlcv=self.ohlcv,
            timeframes=[normalize_timeframe(t) for t in live_tfs],
            on_closed=self.on_candle,
            max_symbols=min(self._kline_live_max, 60),
            max_streams_per_connection=20,
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
        self.sentiment = get_sentiment_provider(settings)
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
        self._universe_symbols = [s.upper() for s in symbols]
        # Paper watch always stays on backfill/OI priority lists.
        try:
            from app.research.v1_production import V1_SYMBOLS

            paper_syms = sorted(V1_SYMBOLS)
            self.backfill.set_watchlist(paper_syms)
            self.oi.set_watchlist(paper_syms)
        except Exception:  # noqa: BLE001
            self.backfill.set_watchlist(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            self.oi.set_watchlist(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        active = self.backfill.select_active_universe(self._universe_symbols)
        await self.fundamentals.start()
        try:
            await self.sentiment.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sentiment_provider_start_failed", error=str(exc))
        live_syms = self._select_kline_live_symbols(self._universe_symbols)
        await self.kline_ws.start(live_syms)
        await self.trade_tips.sync_symbols(live_syms)
        await self.liquidations.start()
        self.oi.set_symbols(symbols)
        await self.oi.start()
        await self.backfill.start()
        # Init paper book once with settings (singleton), then hydrate from DB
        from app.services.paper_risk import policy_from_settings
        from app.services.paper_trade import get_paper_trade_engine

        paper = get_paper_trade_engine(
            starting_equity=float(self.settings.paper_starting_equity),
            risk_percent=float(self.settings.paper_risk_percent),
            enabled=bool(self.settings.paper_trade_enabled),
            entry_mode=str(getattr(self.settings, "paper_entry_mode", "path_a") or "path_a"),
            risk_policy=policy_from_settings(self.settings),
            v1_profile_enabled=bool(
                getattr(self.settings, "paper_v1_profile_enabled", True)
            ),
            v1_universe_only=bool(
                getattr(self.settings, "paper_v1_universe_only", True)
            ),
            v1_secondary_enabled=bool(
                getattr(self.settings, "paper_v1_secondary_enabled", True)
            ),
        )
        # Exclusive ownership is optional — default parallel streams keep sources separate.
        paper.v1_watcher_owns_entries = bool(
            getattr(self.settings, "paper_v1_watcher_owns_entries", False)
        )
        paper.legacy_auto_entry_enabled = bool(
            getattr(self.settings, "paper_legacy_auto_entry_enabled", False)
        )
        v1_watcher = None
        if bool(getattr(self.settings, "paper_v1_watcher_enabled", True)):
            from app.services.v1_paper_watcher import get_v1_paper_watcher

            v1_watcher = get_v1_paper_watcher(
                enabled=True,
                timeframe=str(
                    getattr(self.settings, "paper_v1_timeframe", "1h") or "1h"
                ),
                secondary_enabled=bool(
                    getattr(self.settings, "paper_v1_secondary_enabled", True)
                ),
                replay_mode=bool(
                    getattr(self.settings, "paper_v1_replay_mode", False)
                ),
                emit_alerts=True,
                paper_engine=paper,
            )
            # Keep v1 books on live kline WS + tip REST forever.
            try:
                self.set_kline_focus([b.symbol for b in v1_watcher.books])
            except Exception:  # noqa: BLE001
                self.set_kline_focus(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
            try:
                seeded = v1_watcher.seed_from_store()
                logger.info(
                    "v1_paper_watcher_seeded",
                    books=len(v1_watcher.books),
                    seeded=seeded,
                    replay=v1_watcher.replay_mode,
                    owns_entries=paper.v1_watcher_owns_entries,
                    legacy_auto=paper.legacy_auto_entry_enabled,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("v1_paper_watcher_seed_failed", error=str(exc))
        try:
            rows = await persistence.load_paper_trades(closed_limit=paper.max_closed)
            paper.hydrate_from_rows(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("paper_hydrate_failed", error=str(exc))
        # Alerts: Redis first, else Postgres
        try:
            from app.services.alerts import get_alert_feed
            from app.services.redis_state import redis_state

            feed = get_alert_feed()
            snap = await redis_state.get_alerts_state()
            if not isinstance(snap, dict) or not snap.get("rows"):
                db_rows = await persistence.load_alerts(limit=feed.maxlen)
                if db_rows:
                    snap = {
                        "rows": db_rows,
                        "latest_seq": max(
                            (int(a.get("seq") or 0) for a in db_rows), default=0
                        ),
                    }
            feed.hydrate_from_snapshot(snap if isinstance(snap, dict) else None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("alerts_hydrate_failed", error=str(exc))
        # Funding marks from DB until live WS overwrites
        try:
            fund_rows = await persistence.load_latest_funding_rates()
            self.market.hydrate_funding_from_rows(fund_rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("funding_hydrate_failed", error=str(exc))
        # Open any legacy Path A/B setups already in cache after boot.
        # Runs alongside the v1 watcher unless exclusive ownership is enabled.
        if paper.legacy_auto_entry_enabled and not paper.v1_watcher_owns_entries:
            try:
                paper.scan_cached_setups()
            except Exception:  # noqa: BLE001
                pass
        # Background hydrate/enqueue so FastAPI lifespan can yield and bind :8000
        # immediately. Blocking here previously left the API unreachable for minutes
        # while ~symbols×TFs sequential DB loads ran (ERR_CONNECTION_REFUSED / timeouts).
        # Only hydrate/enqueue the active paper+volume set — not all ~500 discovered perps.
        self._tasks.append(
            asyncio.create_task(
                self._hydrate_and_enqueue(active),
                name="hydrate_and_enqueue",
            )
        )
        self._tasks.append(asyncio.create_task(self._db_flush_loop(), name="ohlcv_db_flush"))
        self._tasks.append(asyncio.create_task(self._fundamentals_loop(), name="fundamentals"))
        self._tasks.append(asyncio.create_task(self._sentiment_loop(), name="sentiment"))
        self._tasks.append(asyncio.create_task(self._retention_loop(), name="retention"))
        self._tasks.append(
            asyncio.create_task(self._setup_ensure_loop(), name="setup_ensure_loop")
        )
        self._tasks.append(
            asyncio.create_task(self._paper_trade_loop(), name="paper_trade_loop")
        )
        if bool(getattr(self.settings, "paper_v1_watcher_enabled", True)):
            self._tasks.append(
                asyncio.create_task(
                    self._v1_paper_watcher_loop(), name="v1_paper_watcher_loop"
                )
            )
        self._tasks.append(
            asyncio.create_task(self._kline_live_rebalance_loop(), name="kline_live_rebalance")
        )
        self._tasks.append(
            asyncio.create_task(self._visible_tip_loop(), name="visible_tip_loop")
        )
        logger.info(
            "orchestrator_started",
            symbols=len(symbols),
            active_universe=len(active),
            active_cap=self.backfill.active_universe_count,
            kline_live_symbols=len(live_syms),
            kline_plan=self.kline_ws.plan_for_symbols(live_syms),
            oi=self.oi.status(),
            backfill=self.backfill.status(),
            v1_watcher=bool(
                getattr(self.settings, "paper_v1_watcher_enabled", True)
            ),
        )

    def _select_kline_live_symbols(self, universe: list[str] | None = None) -> list[str]:
        """Sticky top-volume + focus/visible — avoids WS reconnect thrash."""
        uni = [s.upper() for s in (universe or self._universe_symbols or list(self.market.tickers.keys()))]
        ranked = sorted(
            uni,
            key=lambda s: float(
                getattr(self.market.tickers.get(s), "quote_volume_24h", 0) or 0
            ),
            reverse=True,
        )
        focus = {s.upper() for s in self._kline_focus}
        visible = set(getattr(self.backfill, "_visible", set()) or set())
        # Frozen COMBO_02 v1 books must always stay on live kline WS (1h/4h tips).
        try:
            from app.research.v1_production import V1_SYMBOLS

            v1_must = {s for s in V1_SYMBOLS if s in set(uni) or True}
        except Exception:  # noqa: BLE001
            v1_must = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
        # Always keep focus/visible/v1; fill remainder from sticky then volume rank
        must = focus | visible | v1_must
        sticky = set(self._kline_live_sticky)
        ranked_fill = [s for s in ranked if s not in must]
        sticky_keep = [s for s in ranked if s in sticky and s not in must]
        combined = list(dict.fromkeys([*sorted(must), *sticky_keep, *ranked_fill]))
        live = combined[: max(20, self._kline_live_max)]
        self._kline_live_sticky = set(live)
        return sorted(live)

    def set_kline_focus(self, symbols: list[str]) -> None:
        """Chart / screener focus symbols always stay on the live kline WS."""
        added = {s.upper() for s in symbols if s} - self._kline_focus
        self._kline_focus |= {s.upper() for s in symbols if s}
        if len(self._kline_focus) > 40:
            keep = list(self._kline_focus)[-40:]
            self._kline_focus = set(keep)
        if added:
            self._kline_live_sticky |= added

    async def refresh_kline_live_subscriptions(self, *, force: bool = False) -> None:
        live = self._select_kline_live_symbols()
        if not live:
            return
        desired = set(live)
        current = set(getattr(self.kline_ws, "_symbols", []) or [])
        if force or desired != current:
            await self.kline_ws.sync_symbols(live)
        # Trade tips are the live path when kline WS is silent
        await self.trade_tips.sync_symbols(live)

    async def _kline_live_rebalance_loop(self) -> None:
        """Periodically re-pick top-volume live set so WS stays healthy."""
        while self._running:
            await asyncio.sleep(60.0)
            try:
                await self.refresh_kline_live_subscriptions()
            except Exception as exc:  # noqa: BLE001
                logger.warning("kline_live_rebalance_failed", error=str(exc))

    async def _visible_tip_loop(self) -> None:
        """Keep chart/screener tips fresh via direct REST when kline WS is silent."""
        from app.ingestion.klines import BINANCE_INTERVAL, normalize_rest_kline

        while self._running:
            # Faster when trade tips also quiet
            trade_ok = bool(
                self.trade_tips.connected and self.trade_tips.message_count > 0
            )
            await asyncio.sleep(3.0 if not trade_ok else 8.0)
            try:
                kline_msgs = sum(
                    int(c.get("message_count") or 0)
                    for c in (self.kline_ws.status().get("connections") or [])
                )
                kline_silent = kline_msgs == 0
                # When WS quiet, starve non-visible backfill so tip REST gets slots
                if kline_silent and hasattr(self.backfill, "adaptive"):
                    try:
                        self.backfill.adaptive.concurrency = 1
                    except Exception:  # noqa: BLE001
                        pass

                visible = list(getattr(self.backfill, "_visible", set()) or set())
                focus = list(self._kline_focus)
                targets = list(dict.fromkeys([*focus, *visible]))[:6]
                if not targets:
                    # Still refresh a few top-volume tips so paper/signals move
                    targets = self._select_kline_live_symbols()[:4]
                tfs = list(self.kline_ws.timeframes) or ["15m", "5m", "1m", "1h"]
                # Always include v1 setup/HTF TFs even if WS list was truncated historically.
                for extra in ("1h", "4h"):
                    if extra not in tfs:
                        tfs.append(extra)
                # Prefer setup TFs for tip freshness when budget is tight
                prefer = [t for t in ("1h", "15m", "4h", "5m", "1m") if t in tfs]
                tfs = list(dict.fromkeys([*prefer, *tfs]))[:5]
                rest = getattr(self.backfill, "rest", None)
                if rest is None:
                    continue
                for sym in targets:
                    for tf in tfs:
                        tip_key = (sym, normalize_timeframe(tf))
                        last = self.backfill._last_tip_fetch.get(tip_key, 0.0)  # noqa: SLF001
                        import time as _time

                        min_gap = 3.0 if tip_key[0] in set(focus) else 6.0
                        if _time.monotonic() - float(last or 0.0) < min_gap:
                            continue
                        try:
                            interval = BINANCE_INTERVAL.get(normalize_timeframe(tf), tf)
                            raw = await asyncio.wait_for(
                                rest.futures_klines(sym, interval, limit=5),
                                timeout=4.0,
                            )
                            self.backfill._last_tip_fetch[tip_key] = _time.monotonic()  # noqa: SLF001
                            batch = []
                            open_new = None
                            for row in raw or []:
                                c = normalize_rest_kline(sym, tf, row)
                                if c is None:
                                    continue
                                if not c.is_closed:
                                    open_new = c
                                else:
                                    batch.append(c)
                            if batch:
                                await self.ohlcv.ingest_history(batch)
                            if open_new is not None:
                                await self.ohlcv.upsert_candle(open_new)
                            # Prefer live ticker on tip if present
                            tick = self.market.get_ticker(sym)
                            if tick and tick.price:
                                await self.ohlcv.apply_live_price(sym, float(tick.price))
                        except Exception:  # noqa: BLE001
                            continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("visible_tip_loop_failed", error=str(exc))

    async def _hydrate_and_enqueue(self, symbols: list[str]) -> None:
        """Load OHLCV from DB, restore setups/OI, warm engines, then enqueue gaps."""
        try:
            try:
                from app.services.performance import performance_monitor

                performance_monitor.hydrate_active = True
            except Exception:  # noqa: BLE001
                pass
            await self.backfill.hydrate_from_db(symbols)
            try:
                from app.services.performance import performance_monitor

                performance_monitor.hydrate_active = False
            except Exception:  # noqa: BLE001
                pass
            await self._hydrate_setup_signals()
            # OI history for active/visible tier so % changes aren't blank after restart
            try:
                prefer = list(
                    dict.fromkeys(
                        [
                            *self.oi._prioritized()[:60],  # noqa: SLF001
                            *(list(symbols)[:40]),
                        ]
                    )
                )[:80]
                oi_hist = await persistence.load_open_interest_history(
                    prefer, limit_per_symbol=200, max_symbols=80
                )
                await self.oi.hydrate_from_db(oi_hist)
            except Exception as exc:  # noqa: BLE001
                logger.warning("oi_hydrate_failed", error=str(exc))
            if not self._running:
                return
            self._tasks.append(
                asyncio.create_task(
                    self._warm_engines_from_store(symbols),
                    name="engine_warm_from_hydrate",
                )
            )
            # Start the startup ramp clock after hydrate so enqueue/REST burst
            # controls measure from ingestion readiness (scheduling only).
            self.backfill.mark_ingestion_ready()
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
        await self.trade_tips.stop()
        await self.liquidations.stop()
        await self._liq_ws.stop_all()
        await self.oi.stop()
        await self.fundamentals.close()
        try:
            await self.sentiment.close()
        except Exception:  # noqa: BLE001
            pass

    async def on_symbols_changed(self, symbols: list[str]) -> None:
        self._universe_symbols = [s.upper() for s in symbols]
        await self.refresh_kline_live_subscriptions()
        self.oi.set_symbols(symbols)
        active = self.backfill.select_active_universe(symbols)
        await self.backfill.enqueue_universe(active)

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
        # COMBO_02 v1 1h watcher — independent of 15m screener Path A
        await self._maybe_run_v1_watcher(symbol, timeframe)
        # Dynamic v2 experimental paper — disabled by default; never joins v1
        await self._maybe_run_v2_candidate_watcher(symbol, timeframe)

    async def _maybe_run_v1_watcher(self, symbol: str, timeframe: str) -> None:
        if not bool(getattr(self.settings, "paper_v1_watcher_enabled", True)):
            return
        want = str(getattr(self.settings, "paper_v1_timeframe", "1h") or "1h").lower()
        if normalize_timeframe(timeframe).lower() != want:
            return
        try:
            from app.services.paper_trade import flush_paper_trade_persists, get_paper_trade_engine
            from app.services.v1_paper_watcher import get_v1_paper_watcher

            watcher = get_v1_paper_watcher()
            pos = watcher.on_closed_1h(symbol)
            if pos is not None:
                await flush_paper_trade_persists(get_paper_trade_engine())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "v1_paper_watcher_failed",
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )

    async def _maybe_run_v2_candidate_watcher(self, symbol: str, timeframe: str) -> None:
        if not bool(getattr(self.settings, "dynamic_v2_paper_watcher_enabled", False)):
            return
        if normalize_timeframe(timeframe).lower() != "1h":
            return
        try:
            from app.services.paper_trade import flush_paper_trade_persists, get_paper_trade_engine
            from app.services.v2_candidate_paper_watcher import get_v2_candidate_paper_watcher

            watcher = get_v2_candidate_paper_watcher(
                enabled=True,
                max_open_positions=int(
                    getattr(self.settings, "dynamic_v2_max_open_positions", 1)
                ),
                max_total_risk_percent=float(
                    getattr(self.settings, "dynamic_v2_max_total_risk_percent", 0.005)
                ),
                max_v1_book_risk_percent=float(
                    getattr(self.settings, "dynamic_v2_max_v1_book_risk_percent", 0.05)
                ),
                paper_engine=get_paper_trade_engine(),
                emit_alerts=False,
            )
            pos = await watcher.on_closed_1h(symbol)
            if pos is not None:
                await flush_paper_trade_persists(get_paper_trade_engine())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "v2_candidate_watcher_failed",
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )

    async def _v1_paper_watcher_loop(self) -> None:
        """Periodic tip check for v1 books (covers missed 1h close events)."""
        from app.services.paper_trade import flush_paper_trade_persists, get_paper_trade_engine
        from app.services.v1_paper_watcher import get_v1_paper_watcher

        while self._running:
            await asyncio.sleep(30.0)
            if not bool(getattr(self.settings, "paper_v1_watcher_enabled", True)):
                continue
            try:
                watcher = get_v1_paper_watcher()
                paper = get_paper_trade_engine()
                # Preferential REST tip catch-up for v1 books. Deep-fill thin
                # series once; thereafter only tip-refresh.
                if getattr(self, "backfill", None) is not None:
                    from app.services.ohlcv_store import ohlcv_store

                    if not hasattr(self, "_v1_deep_hydrated"):
                        self._v1_deep_hydrated = set()
                    for book in watcher.books:
                        try:
                            bars_1h = len(
                                ohlcv_store.get_candles_for_engine(
                                    book.symbol, "1h", include_open=False
                                )
                                or []
                            )
                            bars_4h = len(
                                ohlcv_store.get_candles_for_engine(
                                    book.symbol, "4h", include_open=False
                                )
                                or []
                            )
                            key = book.symbol.upper()
                            need_deep = key not in self._v1_deep_hydrated and (
                                bars_1h < 80 or bars_4h < 80
                            )
                            if need_deep:
                                if bars_1h < 80:
                                    await self.backfill._fetch_rest_tail(
                                        book.symbol, "1h", limit=200
                                    )
                                if bars_4h < 80:
                                    await self.backfill._fetch_rest_tail(
                                        book.symbol, "4h", limit=200
                                    )
                                bars_1h = len(
                                    ohlcv_store.get_candles_for_engine(
                                        book.symbol, "1h", include_open=False
                                    )
                                    or []
                                )
                                bars_4h = len(
                                    ohlcv_store.get_candles_for_engine(
                                        book.symbol, "4h", include_open=False
                                    )
                                    or []
                                )
                                if bars_1h >= 80 and bars_4h >= 80:
                                    self._v1_deep_hydrated.add(key)
                            else:
                                await self.backfill.ensure_series_fresh(
                                    book.symbol, "1h", limit=80, force_tip=True
                                )
                                await self.backfill.ensure_series_fresh(
                                    book.symbol, "4h", limit=80, force_tip=True
                                )
                                if bars_1h >= 80 and bars_4h >= 80:
                                    self._v1_deep_hydrated.add(key)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "v1_watcher_tip_refresh_failed",
                                symbol=book.symbol,
                                error=str(exc),
                            )
                for book in watcher.books:
                    watcher.on_closed_1h(book.symbol)
                await flush_paper_trade_persists(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("v1_paper_watcher_loop_failed", error=str(exc))

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
            from app.services.paper_trade import get_paper_trade_engine

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
                from app.services.paper_trade import flush_paper_trade_persists

                paper_eng = get_paper_trade_engine()
                paper_eng.on_setup_signal(symbol, payload)
                await flush_paper_trade_persists(paper_eng)
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
        """Drain visible/stale setup queue — refresh OHLCV first when WS is silent."""
        from app.services.setup_signals import get_setup_signal_service
        from app.services.paper_trade import get_paper_trade_engine

        while self._running:
            await asyncio.sleep(0.75)
            svc = get_setup_signal_service()
            paper = get_paper_trade_engine()
            try:
                batch = list(getattr(svc, "_pending_ensure", set()))[:12]
                cfg = svc.config
                mtf = [
                    cfg.mtf_major,
                    cfg.mtf_primary,
                    cfg.mtf_setup,
                    cfg.mtf_entry,
                ]
                for sym in batch:
                    try:
                        await self.backfill.ensure_setup_mtf_fresh(
                            sym,
                            mtf,
                            force_setup_tip=True,
                            setup_tf=cfg.mtf_setup,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "setup_ohlcv_refresh_failed",
                            symbol=sym,
                            error=str(exc),
                        )
                updated = svc.drain_ensure_queue(limit=12)
                from app.services.paper_trade import flush_paper_trade_persists

                for sym in updated:
                    payload = self.engines.get_setup_signal(sym)
                    if payload and payload.get("signal_status") != "WAITING":
                        await persistence.persist_setup_analysis(sym, payload)
                    if payload:
                        paper.on_setup_signal(sym, payload)
                await flush_paper_trade_persists(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("setup_ensure_loop_failed", error=str(exc))

    async def _paper_trade_loop(self) -> None:
        """Poll marks for open paper positions; persist fills."""
        from app.services.market_store import market_store
        from app.services.paper_trade import (
            flush_paper_trade_persists,
            get_paper_trade_engine,
        )

        paper = get_paper_trade_engine()
        while self._running:
            try:
                # Flush opens/closes first so a restart mid-sleep cannot drop them
                await flush_paper_trade_persists(paper)
                prices: dict[str, float] = {}
                for pos in list(paper._open.values()):  # noqa: SLF001
                    sym = getattr(pos, "symbol", None)
                    if not sym:
                        continue
                    mark = market_store.mark_prices.get(sym)
                    if mark is not None and getattr(mark, "mark_price", None):
                        prices[sym] = float(mark.mark_price)
                        continue
                    tick = market_store.get_ticker(sym)
                    if tick is not None and tick.price:
                        prices[sym] = float(tick.price)
                if prices:
                    paper.tick(prices)
                await flush_paper_trade_persists(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("paper_trade_loop_failed", error=str(exc))
            await asyncio.sleep(2.0)

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

    async def _sentiment_loop(self) -> None:
        """Refresh LunarCrush social metrics on a slow cadence (not candle-tied)."""
        await asyncio.sleep(20)
        while self._running:
            refresh = 300.0
            try:
                client = getattr(self.sentiment, "client", None)
                if client is not None:
                    refresh = float(getattr(client, "refresh_seconds", 300) or 300)
                await self._refresh_sentiment()
            except Exception as exc:  # noqa: BLE001
                logger.warning("sentiment_refresh_failed", error=str(exc))
            await asyncio.sleep(max(60.0, refresh))

    async def _refresh_sentiment(self) -> None:
        if not self.sentiment.configured:
            return
        symbols = self.backfill.select_active_universe()
        if not symbols:
            return
        data = await self.sentiment.get_sentiment(symbols)
        client = getattr(self.sentiment, "client", None)
        if client is not None:
            client.coverage_for_universe(symbols)
        for sym, fields in data.items():
            self.engines.set_sentiment(sym, fields)
            # Persist a compact snapshot for sentiment_change history (optional DB)
            sd = fields.get("sentiment")
            if sd is not None and sd.value is not None and client is not None:
                mapping = client.map_symbol(sym)
                asset = (
                    client.raw_asset(mapping.provider_symbol)
                    if mapping.provider_symbol
                    else None
                )
                await persistence.persist_sentiment_snapshot(
                    symbol=sym,
                    provider=str(getattr(self.sentiment, "provider_name", None) or "free_social"),
                    provider_symbol=mapping.provider_symbol,
                    observed_at=sd.timestamp,
                    provider_generated_at=getattr(client.stats, "provider_generated_at", None),
                    social_dominance=_fv_num(fields.get("social_dominance")),
                    social_volume=_fv_num(fields.get("social_volume")),
                    mentions=_fv_num(fields.get("mentions")),
                    engagement=_fv_num(fields.get("engagement")),
                    sentiment=_fv_num(fields.get("sentiment")),
                    sentiment_change=_fv_num(fields.get("sentiment_change")),
                    raw_payload_hash=client.payload_hash(asset) if asset else None,
                )

    async def _refresh_fundamentals(self) -> None:
        # Cap CoinGecko/DefiLlama refresh to active paper+volume set.
        symbols = self.backfill.select_active_universe()
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
            "trade_tips": self.trade_tips.status(),
            "ohlcv": self.ohlcv.stats(),
            "oi": self.oi.status(),
            "liquidations": self.liquidations.status(),
            "backfill": self.backfill.status(),
            "fundamentals": self.fundamentals.status(),
            "sentiment": self.sentiment.status(),
            "provider_health": await provider_health.snapshot_all(),
            "rest_last_minute": audit,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _fv_num(fv: FreshValue | None) -> float | None:
    if fv is None or fv.value is None:
        return None
    try:
        return float(fv.value)
    except (TypeError, ValueError):
        return None


orchestrator: CalculationOrchestrator | None = None


def get_orchestrator() -> CalculationOrchestrator | None:
    return orchestrator
