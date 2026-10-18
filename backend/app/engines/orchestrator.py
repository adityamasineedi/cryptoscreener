from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

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
from app.models.schemas import DataStatus, FreshValue
from app.services.engine_store import EngineStore, engine_store
from app.services.market_store import MarketDataStore
from app.services.ohlcv_store import OHLCVStore, ohlcv_store
from app.services.performance import performance_monitor
from app.services.persistence import persistence
from app.services.redis_state import redis_state
from app.services.retention import apply_retention

logger = get_logger("orchestrator")

# Session-level Postgres advisory lock: one backend process writes paper trades.
# Fixed arbitrary bigint; changing it lets two builds write side by side.
PAPER_WRITER_LOCK_KEY = 7_302_026_001
# Startup tries, 1 s apart: a reload can start before the old process lets go.
PAPER_WRITER_LOCK_ATTEMPTS = 5
# Seconds between ledger-load retries while the paper book is not ready.
PAPER_LEDGER_RETRY_SECONDS = 30.0
# A mark older than this (or not LIVE) is not used to exit a paper position.
PAPER_MARK_MAX_AGE_SECONDS = 30.0


async def acquire_paper_writer_lock(
    db: Any, *, attempts: int = 1, interval: float = 1.0
) -> tuple[bool | None, Any]:
    """Try to become the single paper writer.

    Returns ``(held, connection)``. ``held`` is None when no lock is possible
    (database off), True when this process holds it, False when another process
    does or the attempt failed. The connection must stay open: closing it
    releases the lock. ``attempts`` covers a reload, where the previous process
    may hold the lock for a moment after this one starts.
    """
    if not getattr(db, "enabled", False) or getattr(db, "engine", None) is None:
        logger.info("paper_writer_lock_not_needed", reason="database_disabled")
        return None, None
    conn = None
    got = False
    try:
        conn = await db.engine.connect()
        for attempt in range(max(1, int(attempts))):
            if attempt:
                await asyncio.sleep(interval)
            got = (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": PAPER_WRITER_LOCK_KEY},
                )
            ).scalar()
            # End the implicit transaction; the session lock outlives it.
            await conn.commit()
            if got:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("paper_writer_lock_failed", error=str(exc))
        got = False
    if got:
        logger.info("paper_writer_lock_acquired", key=PAPER_WRITER_LOCK_KEY)
        return True, conn
    logger.warning("paper_writer_lock_not_held", key=PAPER_WRITER_LOCK_KEY)
    if conn is not None:
        try:
            await conn.close()
        except Exception:  # noqa: BLE001
            pass
    return False, None


async def retry_paper_writer_lock(
    paper: Any,
    db: Any,
    *,
    on_acquired: Callable[[Any], None] | None = None,
    interval: float = 15.0,
    keep_running: Callable[[], bool] = lambda: True,
) -> None:
    """Keep trying the paper writer lock after a reload left the old session up.

    Startup only waits a few seconds. A force-stopped process can hold the
    Postgres advisory lock until the server notices the socket is gone, and
    this process would otherwise refuse every paper open until the next boot.
    """
    while keep_running() and not getattr(paper, "writer_lock_held", False):
        await asyncio.sleep(interval)
        if not keep_running() or getattr(paper, "writer_lock_held", False):
            return
        held, conn = await acquire_paper_writer_lock(db, attempts=1)
        if held:
            paper.writer_lock_held = True
            if on_acquired is not None:
                on_acquired(conn)
            logger.info("paper_writer_lock_acquired_on_retry", key=PAPER_WRITER_LOCK_KEY)
            return


async def load_paper_ledger(paper: Any, store: Any) -> bool:
    """One ledger load. Marks the paper book ready only when every read succeeded."""
    try:
        rows = await store.load_paper_trades(
            closed_limit=paper.max_closed, raise_on_error=True
        )
        closed_pnl_total = await store.sum_closed_paper_pnl(raise_on_error=True)
        dedupe_keys = await store.load_paper_dedupe_keys(raise_on_error=True)
        paper.hydrate_from_rows(rows, closed_pnl_total=closed_pnl_total)
        paper.remember_dedupe_keys(dedupe_keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("paper_hydrate_failed", error=str(exc))
        return False
    paper.ledger_ready = True
    return True


async def retry_paper_ledger_load(
    paper: Any,
    store: Any,
    *,
    interval: float = PAPER_LEDGER_RETRY_SECONDS,
    keep_running: Callable[[], bool] = lambda: True,
) -> None:
    """Reload the ledger every ``interval`` seconds until the book is ready."""
    while keep_running() and not paper.ledger_ready:
        await asyncio.sleep(interval)
        if not keep_running():
            return
        await load_paper_ledger(paper, store)


def paper_tick_price(
    mark: Any,
    ticker: Any,
    last_candle_close: Callable[[], float | None] | None = None,
    now: datetime | None = None,
) -> float | None:
    """Price the paper exit loop uses for one symbol: fresh LIVE mark, ticker, candle.

    ``last_candle_close`` is called only when neither mark nor ticker is usable.
    """
    px = getattr(mark, "mark_price", None) if mark is not None else None
    ts = getattr(mark, "timestamp", None) if mark is not None else None
    if px and isinstance(ts, datetime) and getattr(mark, "status", None) == DataStatus.LIVE:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        wall = now if now is not None else datetime.now(timezone.utc)
        if (wall - ts).total_seconds() <= PAPER_MARK_MAX_AGE_SECONDS:
            return float(px)
    if ticker is not None and ticker.price:
        return float(ticker.price)
    if last_candle_close is not None:
        last = float(last_candle_close() or 0)
        if last > 0:
            return last
    return None


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
        self._paper_replay_seeded: set[tuple[str, str]] = set()
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
        # Dedicated connection holding the paper writer advisory lock.
        self._paper_writer_lock_conn: Any | None = None

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
        # One paper writer per database. Without the lock this process only reads.
        from app.services.database import db_manager

        lock_held, self._paper_writer_lock_conn = await acquire_paper_writer_lock(
            db_manager, attempts=PAPER_WRITER_LOCK_ATTEMPTS
        )
        paper.writer_lock_held = lock_held is not False
        if lock_held is False:
            self._tasks.append(
                asyncio.create_task(
                    retry_paper_writer_lock(
                        paper,
                        db_manager,
                        on_acquired=lambda conn: setattr(
                            self, "_paper_writer_lock_conn", conn
                        ),
                        keep_running=lambda: self._running,
                    ),
                    name="paper_writer_lock_retry",
                )
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
        if bool(getattr(self.settings, "paper_v21a_watcher_enabled", False)):
            try:
                from app.research.combo02_v21a_paper_constants import DEFAULT_SYMBOLS
                from app.services.v21a_paper_watcher import get_v21a_paper_watcher

                raw_syms = str(
                    getattr(self.settings, "paper_v21a_symbols", "") or ""
                ).strip()
                symbols = (
                    [s.strip().upper() for s in raw_syms.split(",") if s.strip()]
                    if raw_syms
                    else list(DEFAULT_SYMBOLS)
                )
                v21a = get_v21a_paper_watcher(
                    enabled=True,
                    symbols=symbols,
                    risk_percent=float(self.settings.paper_risk_percent),
                    replay_mode=bool(
                        getattr(self.settings, "paper_v21a_replay_mode", False)
                    ),
                    emit_alerts=True,
                    paper_engine=paper,
                )
                try:
                    self.set_kline_focus(
                        list(
                            dict.fromkeys(
                                [
                                    *(
                                        [b.symbol for b in v1_watcher.books]
                                        if v1_watcher is not None
                                        else []
                                    ),
                                    *symbols,
                                ]
                            )
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
                seeded_v21a = v21a.seed_from_store()
                logger.info(
                    "v21a_paper_watcher_started",
                    strategy_id="COMBO_02_V2_1_A",
                    mode="PAPER",
                    symbols=len(symbols),
                    seeded=seeded_v21a,
                    live_execution=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("v21a_paper_watcher_start_failed", error=str(exc))
        async def _hydrate_ledgers() -> None:
            if not await load_paper_ledger(paper, persistence):
                # Opens stay refused until a later load succeeds.
                self._tasks.append(
                    asyncio.create_task(
                        retry_paper_ledger_load(
                            paper, persistence, keep_running=lambda: self._running
                        ),
                        name="paper_ledger_retry",
                    )
                )
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
            try:
                fund_rows = await persistence.load_latest_funding_rates()
                self.market.hydrate_funding_from_rows(fund_rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("funding_hydrate_failed", error=str(exc))

        # No paper open before the ledger has loaded (database on only).
        if persistence.active:
            paper.ledger_ready = False
        # Do not block the port on these reads. A locked or large paper/alert
        # query was leaving the API unbound while the rest of startup finished.
        self._tasks.append(
            asyncio.create_task(_hydrate_ledgers(), name="hydrate_ledgers")
        )
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
        # Paper loops run only in the process that holds the writer lock.
        if paper.writer_lock_held:
            self._tasks.append(
                asyncio.create_task(self._paper_trade_loop(), name="paper_trade_loop")
            )
        if paper.writer_lock_held and bool(
            getattr(self.settings, "paper_v1_watcher_enabled", True)
        ):
            self._tasks.append(
                asyncio.create_task(
                    self._v1_paper_watcher_loop(), name="v1_paper_watcher_loop"
                )
            )
        if paper.writer_lock_held and bool(
            getattr(self.settings, "paper_v21a_watcher_enabled", False)
        ):
            self._tasks.append(
                asyncio.create_task(
                    self._v21a_paper_watcher_loop(), name="v21a_paper_watcher_loop"
                )
            )
        if paper.writer_lock_held and bool(
            getattr(self.settings, "paper_passed_watcher_enabled", False)
        ):
            self._tasks.append(
                asyncio.create_task(
                    self._passed_paper_watcher_loop(), name="passed_paper_watcher_loop"
                )
            )
        if paper.writer_lock_held and bool(
            getattr(self.settings, "paper_candle12_v2_btc_long_enabled", False)
        ):
            self._tasks.append(
                asyncio.create_task(
                    self._candle12_v2_paper_loop(), name="candle12_v2_paper_loop"
                )
            )
        if paper.writer_lock_held and bool(getattr(self.settings, "paper_book16_4h_enabled", False)):
            self._tasks.append(
                asyncio.create_task(self._book16_paper_loop(), name="book16_paper_loop")
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
        from app.core.shutdown import cancel_tasks, finish_within

        self._running = False
        await cancel_tasks(self._tasks, label="orchestrator")
        self._tasks.clear()
        # Stop the loops that keep issuing REST before waiting on sockets.
        await finish_within(self.oi.stop(), label="oi")
        await finish_within(self.backfill.stop(), label="backfill")
        await finish_within(self.kline_ws.stop(), label="kline_ws")
        await finish_within(self.trade_tips.stop(), label="trade_tips")
        await finish_within(self.liquidations.stop(), label="liquidations")
        await finish_within(self._liq_ws.stop_all(), label="liq_ws")
        await self.fundamentals.close()
        try:
            await self.sentiment.close()
        except Exception:  # noqa: BLE001
            pass
        # Closing the dedicated connection releases the paper writer lock.
        lock_conn = getattr(self, "_paper_writer_lock_conn", None)
        if lock_conn is not None:
            self._paper_writer_lock_conn = None
            try:
                await lock_conn.close()
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
        await self._maybe_run_scalping_watcher(symbol, timeframe)
        # COMBO_02 v1 1h watcher — independent of 15m screener Path A
        await self._maybe_run_v1_watcher(symbol, timeframe)
        # Dynamic v2 experimental paper — disabled by default; never joins v1
        await self._maybe_run_v2_candidate_watcher(symbol, timeframe)
        # COMBO_02 V2.1-A paper — disabled by default; PAPER ONLY (never live)
        await self._maybe_run_v21a_watcher(symbol, timeframe)

    async def _maybe_run_scalping_watcher(self, symbol: str, timeframe: str) -> None:
        """Update read-only scalp setup cache on each fully closed 1m bar."""
        if not bool(getattr(self.settings, "scalping_watcher_enabled", True)):
            return
        if normalize_timeframe(timeframe).lower() != "1m":
            return
        try:
            from app.services.scalping_watcher import get_scalping_watcher

            await asyncio.to_thread(get_scalping_watcher().on_closed_1m, symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scalping_watcher_failed",
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )

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

    async def _maybe_run_v21a_watcher(self, symbol: str, timeframe: str) -> None:
        if not bool(getattr(self.settings, "paper_v21a_watcher_enabled", False)):
            return
        if normalize_timeframe(timeframe).lower() != "1h":
            return
        # LIVE mode must never auto-enable V2.1-A real execution.
        mode = str(getattr(self.settings, "trading_mode", "PAPER") or "PAPER").upper()
        if mode == "LIVE":
            logger.warning(
                "v21a_live_mode_blocked",
                symbol=symbol,
                trading_mode=mode,
            )
            return
        try:
            from app.services.paper_trade import flush_paper_trade_persists, get_paper_trade_engine
            from app.services.v21a_paper_watcher import get_v21a_paper_watcher

            watcher = get_v21a_paper_watcher()
            if not watcher.enabled:
                return
            pos = await asyncio.to_thread(watcher.on_closed_1h, symbol)
            if pos is not None:
                await flush_paper_trade_persists(get_paper_trade_engine())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "v21a_paper_watcher_failed",
                symbol=symbol,
                timeframe=timeframe,
                error=str(exc),
            )

    async def _paper_history_catchup(self, symbols: list[str]) -> None:
        """REST-fill the windows every paper strategy reads, then keep the tip live.

        1h 250 covers the 200-bar EMA used by regime routing. 4h 80 covers the
        HTF swing gate. 15m 100 covers v2 / COMBO_03 confirmation. BTC 1d 50
        covers the passed-book market-direction swing.
        """
        if getattr(self, "backfill", None) is None:
            return
        from app.services.ohlcv_store import ohlcv_store

        targets = (("1h", 250), ("4h", 80), ("15m", 100))
        if not hasattr(self, "_paper_history_ready"):
            self._paper_history_ready = set()
        for raw in symbols:
            sym = str(raw or "").upper()
            if not sym:
                continue
            try:
                counts = {
                    tf: len(
                        ohlcv_store.get_candles_for_engine(sym, tf, include_open=False) or []
                    )
                    for tf, _need in targets
                }
                if sym == "BTCUSDT":
                    counts["1d"] = len(
                        ohlcv_store.get_candles_for_engine(sym, "1d", include_open=False) or []
                    )
                ready = all(counts[tf] >= need for tf, need in targets)
                if sym == "BTCUSDT":
                    ready = ready and counts.get("1d", 0) >= 50
                if sym in self._paper_history_ready and ready:
                    for tf, _need in targets:
                        await self.backfill.ensure_series_fresh(
                            sym, tf, limit=80, force_tip=True
                        )
                    continue
                for tf, need in targets:
                    if counts[tf] < need:
                        await self.backfill._fetch_rest_tail(sym, tf, limit=need)
                if sym == "BTCUSDT" and counts.get("1d", 0) < 50:
                    await self.backfill._fetch_rest_tail(sym, "1d", limit=50)
                counts = {
                    tf: len(
                        ohlcv_store.get_candles_for_engine(sym, tf, include_open=False) or []
                    )
                    for tf, _need in targets
                }
                ready = all(counts[tf] >= need for tf, need in targets)
                if sym == "BTCUSDT":
                    d1 = len(
                        ohlcv_store.get_candles_for_engine(sym, "1d", include_open=False) or []
                    )
                    ready = ready and d1 >= 50
                if ready:
                    self._paper_history_ready.add(sym)
            except Exception as exc:  # noqa: BLE001
                logger.warning("paper_history_catchup_failed", symbol=sym, error=str(exc))

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
                await self._paper_history_catchup([b.symbol for b in watcher.books])
                for book in watcher.books:
                    try:
                        from app.services.market_store import market_store

                        mark = market_store.mark_prices.get(book.symbol)
                        px = None
                        if mark is not None and getattr(mark, "mark_price", None):
                            px = float(mark.mark_price)
                        else:
                            tick = market_store.get_ticker(book.symbol)
                            if tick is not None and tick.price:
                                px = float(tick.price)
                        if px and px > 0:
                            await self.ohlcv.apply_live_price(book.symbol, px)
                    except Exception:  # noqa: BLE001
                        pass
                    watcher.on_closed_1h(book.symbol)
                await flush_paper_trade_persists(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("v1_paper_watcher_loop_failed", error=str(exc))

    async def _passed_paper_watcher_loop(self) -> None:
        """Closed-1h scan for every strategy side that passed the cap book.

        Each tick evaluates every closed bar since the symbol's watermark,
        oldest first, so a restart or a delayed tick does not skip a bar.
        """
        from app.research.passed_cap_book import cap_symbols
        from app.services.paper_trade import flush_paper_trade_persists, get_paper_trade_engine
        from app.services.passed_paper_watcher import (
            DEFAULT_STATE_PATH,
            get_passed_paper_watcher,
        )

        watcher = get_passed_paper_watcher(
            enabled=True,
            paper_engine=get_paper_trade_engine(),
            state_path=DEFAULT_STATE_PATH,
        )
        try:
            self.set_kline_focus(list(cap_symbols()))
        except Exception:  # noqa: BLE001
            pass
        while self._running:
            await asyncio.sleep(60.0)
            if not bool(getattr(self.settings, "paper_passed_watcher_enabled", False)):
                continue
            try:
                paper = get_paper_trade_engine()
                await self._paper_history_catchup(list(watcher.symbols))
                # One BTC daily read per tick, so every symbol gets the same trend.
                daily = await asyncio.to_thread(watcher.read_daily_snapshot)
                for sym in watcher.symbols:
                    try:
                        await asyncio.to_thread(watcher.catch_up, sym, daily=daily)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "passed_paper_watcher_symbol_failed",
                            symbol=sym,
                            error=str(exc),
                        )
                await flush_paper_trade_persists(paper)
            except Exception as exc:  # noqa: BLE001
                logger.warning("passed_paper_watcher_loop_failed", error=str(exc))

    async def _candle12_v2_paper_loop(self) -> None:
        """Virtual CANDLE12_V2 BTCUSDT 1h LONG. One pass per new closed bar."""
        from app.services.candle12_v2_paper import sync_candles
        from app.services.ohlcv_store import ohlcv_store

        last_tip = ""
        while self._running:
            await asyncio.sleep(60.0)
            if not bool(getattr(self.settings, "paper_candle12_v2_btc_long_enabled", False)):
                continue
            try:
                candles = ohlcv_store.get_candles_for_engine(
                    "BTCUSDT", "1h", include_open=False
                )
                if not candles:
                    continue
                tip = str(candles[-1].get("time"))
                if tip == last_tip:
                    continue
                result = await asyncio.to_thread(sync_candles, candles)
                last_tip = tip
                if bool(getattr(self.settings, "paper_candle12_v2_telegram_enabled", False)):
                    from app.services.candle12_v2_paper import send_signals

                    await send_signals((result or {}).get("alerts") or [])
            except Exception as exc:  # noqa: BLE001
                logger.warning("candle12_v2_paper_loop_failed", error=str(exc))

    async def _book16_paper_loop(self) -> None:
        """Virtual 4h book 16 (BTC/ETH LONG). One pass when a new 4h bar has closed, retried every 2 min."""
        from datetime import timedelta

        from app.services import book16_paper

        done_for = None
        while self._running:
            await asyncio.sleep(120.0)
            if not bool(getattr(self.settings, "paper_book16_4h_enabled", False)):
                continue
            now = datetime.now(timezone.utc)
            # Last closed 4h bar's close; a pass is due once per close (its bar must be in Postgres).
            close = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=now.hour % 4)
            if done_for == close:
                continue
            try:
                result = await book16_paper.sync(now)
                if result.get("status") in ("COLLECTING", "WAITING_FOR_FIRST_FORWARD_BAR"):
                    done_for = close
            except Exception as exc:  # noqa: BLE001
                logger.warning("book16_paper_loop_failed", error=str(exc))

    async def _v21a_paper_watcher_loop(self) -> None:
        """Periodic tip check for V2.1-A paper symbols + optional daily summary."""
        from app.services.paper_trade import flush_paper_trade_persists, get_paper_trade_engine
        from app.services.v21a_paper_watcher import get_v21a_paper_watcher

        while self._running:
            await asyncio.sleep(45.0)
            if not bool(getattr(self.settings, "paper_v21a_watcher_enabled", False)):
                continue
            try:
                watcher = get_v21a_paper_watcher()
                if not watcher.enabled:
                    continue
                paper = get_paper_trade_engine()
                await self._paper_history_catchup(list(watcher.symbols))
                for sym in watcher.symbols:
                    await asyncio.to_thread(watcher.on_closed_1h, sym)
                await flush_paper_trade_persists(paper)
                if bool(
                    getattr(self.settings, "paper_v21a_daily_summary_enabled", False)
                ):
                    await asyncio.to_thread(watcher.maybe_daily_summary)
            except Exception as exc:  # noqa: BLE001
                logger.warning("v21a_paper_watcher_loop_failed", error=str(exc))

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
            is_causal_passed,
        )

        paper = get_paper_trade_engine()
        while self._running:
            try:
                # Flush opens/closes first so a restart mid-sleep cannot drop them
                await flush_paper_trade_persists(paper)
                prices: dict[str, float] = {}
                ranges: dict[tuple[str, str], list[tuple[Any, float, float]]] = {}
                fresh_opens: dict[tuple[str, str], list[str]] = {}
                # Closed bars for the causal passed book (backtest exit rules).
                causal_bars: dict[tuple[str, str], list[dict[str, Any]]] = {}
                causal_keys = {
                    paper.replay_key(p.symbol, getattr(p, "timeframe", None) or "1h")
                    for p in list(paper._open.values())  # noqa: SLF001
                    if is_causal_passed(p)
                }
                visited: set[tuple[str, str]] = set()
                for pos in list(paper._open.values()):  # noqa: SLF001
                    sym = getattr(pos, "symbol", None)
                    tf = getattr(pos, "timeframe", None) or "1h"
                    key = paper.replay_key(sym or "", tf)
                    if not sym or key in visited:
                        continue
                    visited.add(key)
                    closed_bars = self.ohlcv.get_closed(sym, tf)
                    if key in causal_keys:
                        causal_bars[key] = [candle.as_mapping() for candle in closed_bars]
                    seen = paper._replayed_bar_opens.setdefault(key, set())
                    if key not in self._paper_replay_seeded:
                        for candle in closed_bars:
                            seen.add(candle.open_time.isoformat())
                        self._paper_replay_seeded.add(key)
                    else:
                        fresh = [
                            candle
                            for candle in closed_bars
                            if candle.open_time.isoformat() not in seen
                        ]
                        fresh.sort(key=lambda candle: candle.open_time)
                        if fresh:
                            ranges[key] = [
                                (candle.open_time, float(candle.high), float(candle.low))
                                for candle in fresh
                            ]
                            fresh_opens[key] = [
                                candle.open_time.isoformat() for candle in fresh
                            ]
                    forming = self.ohlcv.get_open(sym, tf)
                    if forming is not None:
                        ranges.setdefault(key, []).append(
                            (forming.open_time, float(forming.high), float(forming.low))
                        )

                    def _last_close(sym: str = sym, pos: Any = pos) -> float | None:
                        candles = self.ohlcv.get_candles_for_engine(
                            sym, getattr(pos, "timeframe", None) or "1h", include_open=True
                        )
                        return candles[-1].get("close") if candles else None

                    px = paper_tick_price(
                        market_store.mark_prices.get(sym),
                        market_store.get_ticker(sym),
                        _last_close,
                    )
                    if px is not None and sym not in prices:
                        prices[sym] = px
                if causal_bars:
                    paper.resolve_passed_exits(causal_bars)
                if ranges:
                    paper.replay_bar_ranges(ranges)
                    for key, opens in fresh_opens.items():
                        paper._replayed_bar_opens.setdefault(key, set()).update(opens)
                if prices:
                    paper.tick(prices)
                    for sym, px in prices.items():
                        try:
                            await self.ohlcv.apply_live_price(sym, px)
                        except Exception:  # noqa: BLE001
                            pass
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
