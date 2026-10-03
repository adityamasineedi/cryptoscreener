from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Query, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.core.provider_health import provider_health
from app.core.rate_limiter import rate_limiters
from app.core.request_audit import request_audit
from app.engines.orchestrator import get_orchestrator
from app.ingestion.service import get_ingestion
from app.models.schemas import HealthResponse
from app.services.database import db_manager
from app.services.market_store import market_store
from app.services.ohlcv_store import ohlcv_store
from app.services.redis_manager import redis_manager
from app.services.coverage import build_data_coverage, build_ohlcv_coverage
from app.services.performance import performance_monitor
from app.services.screener import ScreenerService
from app.services.system_stats import build_system_stats

router = APIRouter()
ws_router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    redis_h = await redis_manager.health()
    db_h = await db_manager.health()
    return HealthResponse(
        status="ok",
        use_real_data=settings.use_real_data,
        redis=redis_h.get("status", "unknown"),
        database=db_h.get("status", "unknown"),
        ingestion=market_store.ingestion_status,
        symbols_loaded=len(market_store.symbols),
        tickers_live=market_store.live_ticker_count(),
        rate_limiters=rate_limiters.all_snapshots(),
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/system/stats")
async def system_stats() -> dict[str, Any]:
    return await build_system_stats()


@router.get("/system/performance")
async def system_performance() -> dict[str, Any]:
    return await performance_monitor.snapshot()


@router.get("/data/coverage")
async def data_coverage() -> dict[str, Any]:
    return await build_data_coverage(get_settings())


@router.get("/data/ohlcv-coverage")
async def ohlcv_coverage() -> dict[str, Any]:
    return await build_ohlcv_coverage(get_settings())


@router.get("/data/backfill")
async def data_backfill() -> dict[str, Any]:
    orch = get_orchestrator()
    symbols = [s.symbol for s in market_store.list_symbols(market_type="futures_perp")]
    if orch is None or getattr(orch, "backfill", None) is None:
        return {
            "timeframes": {},
            "queue_size": 0,
            "note": "backfill not started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return orch.backfill.backfill_report(symbols)


@router.get("/data/oi-coverage")
async def data_oi_coverage() -> dict[str, Any]:
    orch = get_orchestrator()
    if orch is None:
        return {"coverage": {}, "note": "orchestrator not started"}
    return orch.oi.oi_coverage_report()


@router.get("/data/liquidations/diagnostic")
async def liquidations_diagnostic() -> dict[str, Any]:
    orch = get_orchestrator()
    if orch is None:
        return {
            "provider": "binance_force_order",
            "connection_status": "STOPPED",
            "connected": False,
            "reader_running": False,
            "frames_received": 0,
            "normalized_events": 0,
            "events_seen": 0,
            "status": "UNAVAILABLE",
            "liquidation_status": "UNAVAILABLE",
            "reason": "orchestrator not started",
            "note": "orchestrator not started",
        }
    if hasattr(orch.liquidations, "diagnostic"):
        return orch.liquidations.diagnostic()
    return orch.liquidations.status()


@router.get("/health/providers")
async def health_providers() -> dict[str, Any]:
    audit = await request_audit.stats_last_minute()
    orch = get_orchestrator()
    fund = orch.fundamentals.status() if orch else {}
    liq = orch.liquidations.status() if orch else {"liquidation_status": "WAITING"}
    settings = get_settings()
    from app.ingestion.providers.onchain import OnChainProvider
    from app.ingestion.providers.sentiment import get_sentiment_provider
    from app.services.persistence import persistence
    from app.services.redis_state import redis_state

    onchain = OnChainProvider(settings)
    sentiment = (
        orch.sentiment
        if orch is not None and getattr(orch, "sentiment", None) is not None
        else get_sentiment_provider(settings)
    )
    if not onchain.configured:
        await provider_health.mark_disabled("onchain")
    if not sentiment.configured:
        await provider_health.mark_disabled("sentiment")
        await provider_health.mark_disabled("free_social")
        await provider_health.mark_disabled("lunarcrush")

    providers = await provider_health.snapshot_all()
    # Ensure disabled adapters appear even with zero traffic
    names = {p.get("provider") or p.get("name") for p in providers}
    # Enrich with provider cooldown when available
    cooldown_map: dict[str, Any] = {}
    if orch:
        try:
            cooldown_map["coingecko"] = {
                "cooldown": orch.fundamentals.coingecko.in_cooldown(),
                "remaining": orch.fundamentals.coingecko.cooldown_remaining(),
            }
            cooldown_map["defillama"] = {
                "cooldown": orch.fundamentals.defillama.in_cooldown(),
                "remaining": orch.fundamentals.defillama.cooldown_remaining(),
            }
        except Exception:  # noqa: BLE001
            pass
        try:
            lc = getattr(sentiment, "client", None)
            if lc is not None:
                cooldown_map["lunarcrush"] = {
                    "cooldown": lc.in_cooldown(),
                    "remaining": lc.cooldown_remaining(),
                }
        except Exception:  # noqa: BLE001
            pass
    for p in providers:
        pname = p.get("provider") or p.get("name")
        cd = cooldown_map.get(pname or "")
        if cd and cd.get("cooldown"):
            p["cooldown_until"] = f"in_{round(cd.get('remaining') or 0, 1)}s"
        if pname == "onchain":
            p["enabled"] = bool(onchain.configured)
            p["healthy"] = bool(onchain.configured)
        if pname in ("sentiment", "lunarcrush", "free_social", "socialtickers", "xoomar"):
            st = sentiment.status()
            p["enabled"] = bool(st.get("configured"))
            p["healthy"] = bool(st.get("connected")) or (
                st.get("status") in ("LIVE", "HEALTHY", "CACHED")
            )
            p["status"] = st.get("status") or p.get("status")
            p["vendor"] = st.get("provider")
            p["assets_received"] = st.get("assets_received")
            p["assets_mapped"] = st.get("assets_mapped")
            p["assets_unmapped"] = st.get("assets_unmapped")
            p["note"] = st.get("note")

    for name, status in (
        ("onchain", onchain.status()),
        ("sentiment", sentiment.status()),
        ("free_social", sentiment.diagnostic()),
    ):
        if name not in names:
            providers.append(
                {
                    "provider": name,
                    "name": name,
                    "enabled": bool(status.get("configured")),
                    "healthy": bool(status.get("connected"))
                    if name in ("sentiment", "lunarcrush")
                    else False,
                    "status": status.get("status")
                    or ("DISABLED" if not status.get("configured") else "HEALTHY"),
                    "requests": status.get("requests_total") or 0,
                    "successful": status.get("requests_success") or 0,
                    "failed": status.get("requests_failed") or 0,
                    "429_count": status.get("rate_limit_hits") or 0,
                    "cache_hits": (status.get("cache_stats") or {}).get("hits", 0),
                    "cache_misses": (status.get("cache_stats") or {}).get("misses", 0),
                    "cache_hit_rate": (status.get("cache_stats") or {}).get(
                        "cache_hit_rate", 0.0
                    ),
                    "last_success": status.get("last_success_at"),
                    "last_error": status.get("last_error"),
                    "cooldown_until": None,
                    "note": status.get("note"),
                    "assets_received": status.get("assets_received"),
                    "assets_mapped": status.get("assets_mapped"),
                    "assets_unmapped": status.get("assets_unmapped"),
                }
            )

    return {
        "providers": providers,
        "rate_limiters": rate_limiters.all_snapshots(),
        "rest_last_minute": audit,
        "fundamentals": fund,
        "liquidations": liq,
        "onchain": onchain.status(),
        "sentiment": sentiment.status(),
        "free_social": sentiment.diagnostic(
            universe_size=len(market_store.list_symbols(market_type="futures_perp"))
        ),
        "lunarcrush": (
            sentiment.diagnostic(
                universe_size=len(market_store.list_symbols(market_type="futures_perp"))
            )
            if str(sentiment.status().get("provider") or "") == "lunarcrush"
            else {"provider": "lunarcrush", "enabled": False, "status": "DISABLED"}
        ),
        "persistence": persistence.stats(),
        "redis": await redis_state.health_detail(),
        "database": await db_manager.health(),
        "retention_policies": await db_manager.retention_policies(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/data/sentiment/diagnostic")
async def sentiment_diagnostic() -> dict[str, Any]:
    """LunarCrush / sentiment provider diagnostic — never exposes API keys."""
    orch = get_orchestrator()
    from app.ingestion.providers.sentiment import get_sentiment_provider

    sentiment = (
        orch.sentiment
        if orch is not None and getattr(orch, "sentiment", None) is not None
        else get_sentiment_provider(get_settings())
    )
    universe = [s.symbol for s in market_store.list_symbols(market_type="futures_perp")]
    client = getattr(sentiment, "client", None)
    if client is not None and sentiment.configured:
        try:
            await client.refresh_universe()
            client.coverage_for_universe(universe)
        except Exception:  # noqa: BLE001
            pass
    diag = sentiment.diagnostic(universe_size=len(universe))
    # Never leak secrets
    diag.pop("api_key", None)
    diag["api_key_present"] = bool(diag.get("api_key_present"))
    return diag


@router.get("/symbols")
async def list_symbols(
    market_type: str | None = Query(default="futures_perp"),
) -> dict[str, Any]:
    symbols = market_store.list_symbols(market_type=market_type)
    return {
        "count": len(symbols),
        "symbols": [s.model_dump() for s in symbols],
        "source": "binance_exchange_info",
        "status": "LIVE" if symbols else "WAITING",
    }


@router.get("/screener/presets")
async def screener_presets() -> dict[str, Any]:
    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    return {
        "presets": svc.list_presets(),
        "note": "Presets are filter configurations — not hardcoded coin lists",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _screener_response(
    *,
    rows: list[Any],
    total: int,
    meta: dict[str, Any],
    settings: Any,
    preset: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    payload_rows = [r.model_dump(mode="json") for r in rows]
    return {
        "total": total,
        "offset": offset,
        "limit": meta.get("limit", limit),
        "preset": preset,
        "rows": payload_rows,
        "use_real_data": settings.use_real_data,
        "ingestion": market_store.ingestion_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        # Screen-universe metadata (presentation selection — not strategy scores)
        "total_universe": meta.get("total_universe"),
        "eligible_count": meta.get("eligible_count"),
        "returned_count": meta.get("returned_count", len(payload_rows)),
        "selection_updated_at": meta.get("selection_updated_at"),
        "excluded": meta.get("excluded") or {},
        "search_mode": bool(meta.get("search_mode")),
        "screen_filter": meta.get("screen_filter"),
        "max_screen_symbols": meta.get("max_screen_symbols", 100),
        "screen_label": "SCREEN TOP 100",
    }


@router.get("/screener")
@router.get("/screener/futures")
async def screener_futures(
    search: str | None = None,
    min_change: float | None = None,
    max_change: float | None = None,
    min_volume: float | None = None,
    min_funding: float | None = None,
    max_funding: float | None = None,
    preset: str | None = None,
    sort_by: str = "buy_opportunity",
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    screen_filter: str | None = None,
    signal: str | None = None,
    setup: str | None = None,
) -> dict[str, Any]:
    """Main screener — returns ≤100 dynamically selected symbols.

    Full backend universe remains unchanged for ingestion / Data Health.
    Search can return symbols outside the current top-100 without permanently
    adding them to the screen universe.
    """
    from app.services.screen_universe import MAX_SCREEN_SYMBOLS, clamp_screen_limit

    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    screen_limit = clamp_screen_limit(limit)
    t0 = asyncio.get_event_loop().time()
    rows, total, meta = svc.futures_screener(
        search=search,
        min_change=min_change,
        max_change=max_change,
        min_volume=min_volume,
        min_funding=min_funding,
        max_funding=max_funding,
        preset=preset,
        sort_by=sort_by,
        limit=screen_limit,
        offset=offset,
        screen_filter=screen_filter,
        signal=signal,
        setup=setup,
    )
    performance_monitor.record_screener_latency(
        (asyncio.get_event_loop().time() - t0) * 1000
    )
    orch = get_orchestrator()
    if orch is not None:
        visible = [r.symbol for r in rows]
        orch.oi.set_visible_symbols(visible)
        if getattr(orch, "backfill", None) is not None:
            orch.backfill.set_visible_symbols(visible)
        # Eagerly fill setup for THIS page only (cap sync work; rest via ensure queue)
        from app.services.engine_store import engine_store as es
        from app.services.setup_signals import get_setup_signal_service

        setup_svc = get_setup_signal_service()
        sync_budget = 12
        cfg = setup_svc.config
        mtf = [cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry]
        for i, row in enumerate(rows):
            cached = es.get_setup_signal(row.symbol)
            needs = setup_svc.setup_ohlcv_ready(row.symbol) and (
                not cached or setup_svc.is_stale(cached, row.symbol)
            )
            # Also refresh when OHLCV tip itself is behind (WS silent)
            from app.ingestion.klines import is_trailing_stale

            tip = ohlcv_store.get_closed(row.symbol, cfg.mtf_setup)
            tip_stale = bool(tip) and is_trailing_stale(tip, cfg.mtf_setup)
            if not needs and not tip_stale:
                continue
            if sync_budget > 0:
                try:
                    await orch.backfill.ensure_setup_mtf_fresh(
                        row.symbol,
                        mtf,
                        force_setup_tip=True,
                        setup_tf=cfg.mtf_setup,
                    )
                except Exception:  # noqa: BLE001
                    pass
                setup_svc.ensure_computed(row.symbol, force=tip_stale)
                try:
                    from app.services.paper_trade import get_paper_trade_engine
                    from app.services.engine_store import engine_store as es2

                    get_paper_trade_engine().on_setup_signal(
                        row.symbol, es2.get_setup_signal(row.symbol)
                    )
                except Exception:  # noqa: BLE001
                    pass
                info = market_store.symbols.get(row.symbol)
                if info is not None:
                    rows[i] = svc.build_row(info, rank=row.rank)
                    # Preserve presentation fields from selection
                    rows[i].screen_priority_score = row.screen_priority_score
                    rows[i].screen_priority_reason = row.screen_priority_reason
                sync_budget -= 1
            else:
                setup_svc.enqueue_ensure([row.symbol])
    payload = _screener_response(
        rows=rows,
        total=total,
        meta=meta,
        settings=settings,
        preset=preset,
        limit=screen_limit,
        offset=offset,
    )
    # Mirror latest page into Redis for reconnect (no-op if Redis disabled)
    try:
        from app.services.redis_state import redis_state

        if offset == 0:
            await redis_state.store_screener_state(
                {
                    "total": total,
                    "rows": payload["rows"][:MAX_SCREEN_SYMBOLS],
                    "symbols": [r["symbol"] for r in payload["rows"][:MAX_SCREEN_SYMBOLS]],
                    "ingestion": market_store.ingestion_status,
                    "total_universe": meta.get("total_universe"),
                    "eligible_count": meta.get("eligible_count"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            performance_monitor.record_redis_op()
    except Exception:  # noqa: BLE001
        pass
    return payload


@router.post("/screener/filter")
async def screener_filter(
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    from app.services.screen_universe import clamp_screen_limit

    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    filters = body.get("filters") or []
    sort_by = body.get("sort_by") or "buy_opportunity"
    limit = clamp_screen_limit(int(body.get("limit") or 100))
    offset = int(body.get("offset") or 0)
    search = body.get("search")
    preset = body.get("preset")
    screen_filter = body.get("screen_filter")
    rows, total, meta = svc.futures_screener(
        search=search,
        filters=filters,
        preset=preset,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
        screen_filter=screen_filter,
        signal=body.get("signal"),
        setup=body.get("setup"),
    )
    return {
        **_screener_response(
            rows=rows,
            total=total,
            meta=meta,
            settings=settings,
            preset=preset,
            limit=limit,
            offset=offset,
        ),
        "filters": filters,
    }


@router.get("/screener/fundamentals")
async def screener_fundamentals(
    preset: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    from app.services.screen_universe import clamp_screen_limit

    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    screen_limit = clamp_screen_limit(limit)
    rows, total, meta = svc.futures_screener(
        search=search,
        preset=preset,
        sort_by="market_cap",
        limit=screen_limit,
        offset=offset,
    )
    return {
        **_screener_response(
            rows=rows,
            total=total,
            meta=meta,
            settings=settings,
            preset=preset,
            limit=screen_limit,
            offset=offset,
        ),
    }


@router.get("/coin/{symbol}")
async def coin_detail(symbol: str) -> dict[str, Any]:
    settings = get_settings()
    sym = symbol.upper()
    info = market_store.symbols.get(sym)
    if info is None:
        return {
            "symbol": sym,
            "status": "UNAVAILABLE",
            "message": "Data unavailable",
        }
    svc = ScreenerService(settings, market_store)
    tabs_payload = await svc.coin_detail_tabs(sym)
    return {
        "symbol": sym,
        "info": info.model_dump(),
        "row": tabs_payload.get("tabs", {}).get("overview", {}).get("row"),
        "tabs": tabs_payload.get("tabs"),
        "structure": (tabs_payload.get("tabs") or {}).get("technicals", {}).get("structure"),
        "zones": tabs_payload.get("zones"),
        "indicators": (tabs_payload.get("tabs") or {}).get("technicals", {}).get("indicators"),
        "signals": {
            "entry_exit": (tabs_payload.get("tabs") or {})
            .get("overview", {})
            .get("entry_exit"),
            "tech_rating": (tabs_payload.get("tabs") or {})
            .get("overview", {})
            .get("tech_rating"),
            "setup": (tabs_payload.get("tabs") or {}).get("setup"),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/coin/{symbol}/tabs")
async def coin_tabs(symbol: str) -> dict[str, Any]:
    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    return await svc.coin_detail_tabs(symbol)


@router.get("/signals")
async def list_setup_signals(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """List cached setup analyses across the universe (no fake data)."""
    from app.services.engine_store import engine_store
    from app.services.setup_signals import get_setup_signal_service

    rows: list[dict[str, Any]] = []
    for sym, payload in engine_store.setup_signals.items():
        st = payload.get("status")
        if status and st != status:
            continue
        rows.append(
            {
                "symbol": sym,
                "status": st,
                "setup_status": st,
                "signal_status": payload.get("signal_status"),
                "market_signal": payload.get("market_signal"),
                "market_signal_reason": payload.get("market_signal_reason"),
                "confirmation_strength": payload.get("confirmation_strength"),
                "confirmation_denominator": payload.get("confirmation_denominator"),
                "bullish_conditions": (payload.get("market_signal_payload") or {}).get(
                    "bullish_conditions"
                ),
                "bearish_conditions": (payload.get("market_signal_payload") or {}).get(
                    "bearish_conditions"
                ),
                "direction": payload.get("direction"),
                "trend": payload.get("trend"),
                "risk_reward": payload.get("risk_reward"),
                "entry": payload.get("entry"),
                "calculated_at": payload.get("calculated_at"),
                "source_candle_timestamps": payload.get("source_candle_timestamps"),
                "classification_version": payload.get("classification_version"),
            }
        )
    rows.sort(key=lambda r: r["symbol"])
    return {
        "total": len(rows),
        "rows": rows[:limit],
        "metrics": get_setup_signal_service().metrics(),
        "disclaimer": (
            "Structured setup identification only — not profitability predictions."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/{symbol}/risk")
async def signal_risk(
    symbol: str,
    account_equity: float | None = None,
    risk_percent: float | None = None,
    leverage: float | None = None,
) -> dict[str, Any]:
    from app.services.setup_signals import get_setup_signal_service

    payload = get_setup_signal_service().analyze_symbol(
        symbol,
        account_equity=account_equity,
        risk_percent=risk_percent,
        leverage=leverage,
    )
    return {
        "symbol": symbol.upper(),
        "status": payload.get("status"),
        "risk_management": payload.get("risk_management"),
        "risk_reward": payload.get("risk_reward"),
        "stop": payload.get("stop"),
        "disclaimer": "Risk calculator only — does not place trades.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/{symbol}/targets")
async def signal_targets(symbol: str) -> dict[str, Any]:
    from app.services.setup_signals import get_setup_signal_service

    payload = get_setup_signal_service().analyze_symbol(symbol)
    return {
        "symbol": symbol.upper(),
        "status": payload.get("status"),
        "targets": payload.get("targets") or [],
        "risk_reward": payload.get("risk_reward"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/{symbol}/explanation")
async def signal_explanation(symbol: str) -> dict[str, Any]:
    from app.services.setup_signals import get_setup_signal_service

    payload = get_setup_signal_service().analyze_symbol(symbol)
    return {
        "symbol": symbol.upper(),
        "status": payload.get("status"),
        "direction": payload.get("direction"),
        "explanation": payload.get("explanation") or [],
        "conditions": payload.get("conditions") or [],
        "data_dependencies": payload.get("data_dependencies") or {},
        "disclaimer": payload.get("disclaimer"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/{symbol}/annotations")
async def signal_annotations(symbol: str) -> dict[str, Any]:
    """Chart annotation payload for Lightweight Charts toggles."""
    from app.services.setup_signals import get_setup_signal_service
    from app.services.engine_store import engine_store as es

    payload = es.get_setup_signal(symbol) or get_setup_signal_service().analyze_symbol(
        symbol
    )
    return {
        "symbol": symbol.upper(),
        "annotations": payload.get("annotations") or [],
        "status": payload.get("status"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/signals/{symbol}/backtest")
async def signal_backtest(
    symbol: str,
    timeframe: str = Query(default="15m"),
    limit: int = Query(default=300, ge=50, le=1000),
) -> dict[str, Any]:
    """Historical analysis using the same signal engine (no look-ahead)."""
    from app.signals.backtest import run_backtest
    from app.services.setup_signals import get_setup_signal_service

    candles = ohlcv_store.get_candles_for_engine(
        symbol.upper(), timeframe, include_open=False
    )
    candles = candles[-limit:]
    if len(candles) < 50:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "WAITING",
            "reason": "WAITING FOR OHLCV — insufficient history for backtest",
            "label": "HISTORICAL_ANALYSIS",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    report = run_backtest(
        symbol.upper(),
        timeframe,
        candles,
        config=get_setup_signal_service().config,
    )
    return {
        **report.to_dict(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# BOS combination RESEARCH (read-only; does not mutate live signals)
# ---------------------------------------------------------------------------


@router.get("/research/ohlcv-range")
async def research_ohlcv_range(
    symbols: str = Query(default="BTCUSDT,ETHUSDT,SOLUSDT"),
    timeframes: str = Query(default="15m,1h"),
) -> dict[str, Any]:
    """Postgres OHLCV min/max times — so Backtest tab can warn about missing years."""
    from sqlalchemy import text

    from app.research.query_utils import (
        normalize_research_symbol,
        normalize_research_timeframe,
    )
    from app.services.database import db_manager

    syms = [
        normalize_research_symbol(s)
        for s in symbols.split(",")
        if s.strip()
    ]
    tfs = [
        normalize_research_timeframe(t)
        for t in timeframes.split(",")
        if t.strip()
    ]
    if db_manager.engine is None:
        return {
            "status": "UNAVAILABLE",
            "rows": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    rows: list[dict[str, Any]] = []
    async with db_manager.engine.begin() as conn:
        for sym in syms:
            for tf in tfs:
                r = await conn.execute(
                    text(
                        """
                        SELECT COUNT(*), MIN(time), MAX(time)
                        FROM ohlcv
                        WHERE symbol = :s AND timeframe = :tf
                        """
                    ),
                    {"s": sym, "tf": tf},
                )
                n, t0, t1 = r.fetchone()
                rows.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "bars": int(n or 0),
                        "start": t0.isoformat() if t0 else None,
                        "end": t1.isoformat() if t1 else None,
                    }
                )
    return {
        "status": "OK",
        "rows": rows,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/ohlcv-expand")
async def research_ohlcv_expand_status() -> dict[str, Any]:
    """Status of the latest OHLCV history expand job."""
    from app.research.ohlcv_expand import ohlcv_expand_service

    out = ohlcv_expand_service.status()
    out["timestamp"] = datetime.now(timezone.utc).isoformat()
    return out


@router.post("/research/ohlcv-expand")
async def research_ohlcv_expand_start(
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Start fetching Binance Futures OHLCV into Postgres for selected symbols/TFs.

    Body:
      symbols: list[str] | comma-string
      timeframes: list[str] | comma-string
      until: YYYY-MM-DD (walk history back to this UTC day)
      refresh_tip: bool (also fill forward to now; default true)
      max_pages: int (safety cap per series; default 200)
    """
    from app.research.ohlcv_expand import ohlcv_expand_service

    def _as_list(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v]
        return [s for s in str(v).split(",") if s.strip()]

    symbols = _as_list(body.get("symbols") or "BTCUSDT,ETHUSDT,SOLUSDT")
    timeframes = _as_list(body.get("timeframes") or "15m,1h")
    until = body.get("until")
    until_s = str(until).strip()[:10] if until else None
    refresh_tip = bool(body.get("refresh_tip", True))
    max_pages = int(body.get("max_pages") or 200)
    try:
        job = await ohlcv_expand_service.start(
            symbols=symbols,
            timeframes=timeframes,
            until=until_s,
            refresh_tip=refresh_tip,
            max_pages=max_pages,
        )
    except ValueError as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except RuntimeError as exc:
        return {
            "status": "BUSY",
            "error": str(exc),
            "job": ohlcv_expand_service.status(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "status": "STARTED",
        "job": job,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/research/ohlcv-expand/cancel")
async def research_ohlcv_expand_cancel() -> dict[str, Any]:
    """Request cancel of the running expand job (stops between pages/cells)."""
    from app.research.ohlcv_expand import ohlcv_expand_service

    job = await ohlcv_expand_service.cancel()
    return {
        "status": job.get("status", "idle"),
        "job": job,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/long-strategy/backtest/job")
async def research_long_strategy_backtest_job_status() -> dict[str, Any]:
    """Status of the latest background long-strategy backtest job."""
    from app.research.backtest_job import backtest_job_service

    out = backtest_job_service.status()
    out["timestamp"] = datetime.now(timezone.utc).isoformat()
    return out


@router.post("/research/long-strategy/backtest/start")
async def research_long_strategy_backtest_start(
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Start a background HL/LH Trend+BOS matrix backtest (survives UI navigation)."""
    from app.research.backtest_job import backtest_job_service

    def _as_list(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v]
        return [s for s in str(v).split(",") if s.strip()]

    symbols = _as_list(body.get("symbols") or "BTCUSDT,ETHUSDT,SOLUSDT")
    timeframes = _as_list(body.get("timeframes") or "15m,1h")
    try:
        job = await backtest_job_service.start(
            symbols=symbols,
            timeframes=timeframes,
            direction=str(body.get("direction") or "LONG"),
            combination_id=str(body.get("combination_id") or "COMBO_02"),
            limit=int(body.get("limit") or 1200),
            risk_usd=float(body.get("risk_usd") or 20.0),
            taker_fee_pct=float(body.get("taker_fee_pct") or 0.04),
            maker_fee_pct=float(body.get("maker_fee_pct") or 0.02),
            include_trades=bool(body.get("include_trades", True)),
            start_date=(
                str(body.get("start_date")).strip()[:10]
                if body.get("start_date")
                else None
            ),
            end_date=(
                str(body.get("end_date")).strip()[:10]
                if body.get("end_date")
                else None
            ),
        )
    except ValueError as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except RuntimeError as exc:
        return {
            "status": "BUSY",
            "error": str(exc),
            "job": backtest_job_service.status(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "status": "STARTED",
        "job": job,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/research/long-strategy/backtest/cancel")
async def research_long_strategy_backtest_cancel() -> dict[str, Any]:
    """Request cancel of the running backtest job (stops between cells)."""
    from app.research.backtest_job import backtest_job_service

    job = await backtest_job_service.cancel()
    return {
        "status": job.get("status", "idle"),
        "job": job,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/long-strategy/backtest")
async def research_long_strategy_backtest(
    symbols: str = Query(
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        description="Comma-separated symbols",
    ),
    timeframes: str = Query(
        default="15m,1h",
        description="Comma-separated timeframes",
    ),
    direction: str = Query(default="LONG", description="LONG or SHORT"),
    combination_id: str = Query(default="COMBO_02"),
    limit: int = Query(
        default=1200,
        ge=50,
        le=20000,
        description="OHLCV bars from DB tail (~96 bars/day on 15m)",
    ),
    risk_usd: float = Query(default=20.0, ge=1.0, le=10_000.0),
    taker_fee_pct: float = Query(
        default=0.04,
        ge=0.0,
        le=1.0,
        description="Taker fee percent of notional (0.04 = Binance USDT-M VIP0)",
    ),
    maker_fee_pct: float = Query(
        default=0.02,
        ge=0.0,
        le=1.0,
        description="Maker fee percent of notional (used for LIMIT_RETEST entries)",
    ),
    include_trades: bool = Query(default=True),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
) -> dict[str, Any]:
    """UI Backtest tab — lean HL/LH Trend+BOS matrix (same engine as research scripts)."""
    from app.research.service import get_bos_research_service

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in timeframes.split(",") if t.strip()]
    if not syms or not tfs:
        return {
            "status": "ERROR",
            "reason": "symbols and timeframes required",
            "rows": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    payload = await get_bos_research_service().strategy_matrix(
        combination_id=combination_id.upper(),
        symbols=syms,
        timeframes=tfs,
        direction=direction,
        limit=limit,
        risk_usd=risk_usd,
        start_date=start_date,
        end_date=end_date,
        taker_fee=taker_fee_pct / 100.0,
        maker_fee=maker_fee_pct / 100.0,
        include_trades=include_trades,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-combinations")
async def research_bos_combinations() -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    return {
        **get_bos_research_service().list_combinations(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/bos-combinations/compare")
async def research_bos_combinations_compare(
    symbol: str = Query(...),
    timeframe: str = Query(default="15m"),
    direction: str | None = Query(default=None),
    start_date: str | None = Query(
        default=None,
        description="UTC calendar day YYYY-MM-DD (inclusive start)",
    ),
    end_date: str | None = Query(
        default=None,
        description="UTC calendar day YYYY-MM-DD (inclusive); queried as < end+1day",
    ),
    minimum_sample_size: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=50, le=20000),
) -> dict[str, Any]:
    """BOS Combination Research comparison — never ranks a winner.

    Uses PostgreSQL OHLCV + BOS combination engine. Not Candle-1/Candle-2 V2.
    """
    from app.research.service import get_bos_research_service

    payload = await get_bos_research_service().compare(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        direction=direction,
        minimum_sample_size=minimum_sample_size,
        start_date=start_date,
        end_date=end_date,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-combinations/{combination_id}")
async def research_bos_combination_detail(
    combination_id: str,
    symbol: str = Query(default="BTCUSDT"),
    timeframe: str = Query(default="15m"),
    direction: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=500, ge=50, le=20000),
) -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    svc = get_bos_research_service()
    meta = svc.get_combination(combination_id)
    if meta is None:
        return {
            "status": "NOT_FOUND",
            "combination_id": combination_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    detail = await svc.detail(
        combination_id,
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        direction=direction,
        start_date=start_date,
        end_date=end_date,
    )
    return {**meta, **detail, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-combinations/{combination_id}/backtest")
async def research_bos_combination_backtest(
    combination_id: str,
    symbol: str = Query(...),
    timeframe: str = Query(default="15m"),
    direction: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    minimum_sample_size: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=50, le=20000),
) -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    # Date-bounded backtest via detail's postgres loader + single combo engine.
    detail = await get_bos_research_service().detail(
        combination_id,
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        direction=direction,
        start_date=start_date,
        end_date=end_date,
    )
    sample = int(detail.get("sample_size") or 0)
    run = {
        "status": "OK",
        "combination_id": combination_id,
        "result": detail.get("historical_sample"),
        "sample_size": sample,
        "dataset": detail.get("dataset"),
        "date_filter": detail.get("date_filter"),
        "candle_source": detail.get("candle_source"),
        "label": "RESEARCH_COMPARISON",
    }
    if sample < minimum_sample_size:
        return {
            **run,
            "status": "BELOW_MINIMUM_SAMPLE",
            "minimum_sample_size": minimum_sample_size,
            "sample_size": sample,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {**run, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-combinations/{combination_id}/by-timeframe")
async def research_bos_combination_by_timeframe(
    combination_id: str,
    symbol: str = Query(...),
    direction: str | None = Query(default=None),
    limit: int = Query(default=500, ge=50, le=20000),
) -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    return {
        **get_bos_research_service().by_timeframe(
            combination_id, symbol=symbol, direction=direction, limit=limit
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/bos-combinations/{combination_id}/by-symbol")
async def research_bos_combination_by_symbol(
    combination_id: str,
    symbols: str = Query(
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        description="Comma-separated symbols",
    ),
    timeframe: str = Query(default="15m"),
    direction: str | None = Query(default=None),
    limit: int = Query(default=500, ge=50, le=20000),
) -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return {
        **get_bos_research_service().by_symbol(
            combination_id,
            symbols=syms,
            timeframe=timeframe,
            direction=direction,
            limit=limit,
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/bos-combinations/{combination_id}/walk-forward")
async def research_bos_combination_walk_forward(
    combination_id: str,
    symbol: str = Query(...),
    timeframe: str = Query(default="15m"),
    direction: str | None = Query(default=None),
    limit: int = Query(default=2000, ge=100, le=10000),
) -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    return {
        **get_bos_research_service().walk_forward(
            combination_id,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            limit=limit,
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/bos-strategies")
async def research_bos_strategies(
    symbols: str | None = Query(
        default=None,
        description="Comma-separated symbols; default = all eligible from Postgres",
    ),
    timeframe: str = Query(default="15m"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=50, le=200000),
    max_symbols: int | None = Query(default=None, ge=1, le=2000),
    persist: bool = Query(default=True),
    include_walk_forward: bool = Query(default=False),
    latest_only: bool = Query(
        default=False,
        description="If true, return latest persisted run without re-running",
    ),
) -> dict[str, Any]:
    """BOS Strategy Comparison Research — historical only; never ranks a winner.

    Research only — live engine unchanged.
    """
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    svc = get_bos_strategy_comparison_service()
    if latest_only:
        payload = await svc.latest()
        return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}

    syms = None
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    payload = await svc.compare(
        symbols=syms,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        direction=direction,
        persist=persist,
        include_walk_forward=include_walk_forward,
        max_symbols=max_symbols,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-strategies/catalog")
async def research_bos_strategies_catalog() -> dict[str, Any]:
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    return {
        **get_bos_strategy_comparison_service().list_strategies(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/bos-strategies/coverage")
async def research_bos_strategies_coverage() -> dict[str, Any]:
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    payload = await get_bos_strategy_comparison_service().data_coverage()
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-strategies/diagnostics")
async def research_bos_strategies_diagnostics(
    symbol: str = Query(..., description="Symbol, e.g. BTCUSDT"),
    timeframe: str = Query(default="15m"),
    start: str | None = Query(default=None, description="Start date YYYY-MM-DD"),
    end: str | None = Query(default=None, description="End date YYYY-MM-DD"),
    limit: int | None = Query(default=None, ge=50, le=200000),
    strategies: str | None = Query(
        default=None,
        description="Comma-separated strategy ids (default: S1–S3, CONTROL_C/D)",
    ),
) -> dict[str, Any]:
    """Stage-funnel + retest rejection diagnostic (production engines only).

    Research only — does not change retest logic or run full optimization.
    """
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    ids = None
    if strategies:
        ids = [s.strip().upper() for s in strategies.split(",") if s.strip()]
    payload = await get_bos_strategy_comparison_service().diagnostics(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        limit=limit,
        strategy_ids=ids,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-strategies/pullback-diagnostics")
async def research_bos_strategies_pullback_diagnostics(
    symbol: str = Query(..., description="Symbol, e.g. BTCUSDT"),
    timeframe: str = Query(default="15m"),
    start: str | None = Query(default=None, description="Start date YYYY-MM-DD"),
    end: str | None = Query(default=None, description="End date YYYY-MM-DD"),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=200000,
        description="OHLCV load limit, or with lifecycle=true: max lifecycle traces",
    ),
    lifecycle: bool = Query(
        default=False,
        description="If true, run multi-bar frozen-impulse pullback/retest lifecycle",
    ),
) -> dict[str, Any]:
    """Forensic pullback lifecycle diagnostic (production engines only).

    Research only — does not change pullback rules or run optimization.
    With lifecycle=true, freezes BOS+impulse and re-evaluates on later candles.
    """
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    # When lifecycle=true, `limit` means max traces (OHLCV still date-bounded).
    ohlcv_limit = None if lifecycle else limit
    max_traces = int(limit) if lifecycle and limit is not None else (20 if lifecycle else 8)
    payload = await get_bos_strategy_comparison_service().pullback_diagnostics(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        limit=ohlcv_limit,
        max_traces=max_traces,
        lifecycle=lifecycle,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-strategies/s3-htf-sensitivity")
async def research_bos_strategies_s3_htf_sensitivity(
    symbols: str | None = Query(
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        description="Comma-separated symbols",
    ),
    timeframe: str = Query(default="15m"),
    start: str | None = Query(default=None, description="Start date YYYY-MM-DD"),
    end: str | None = Query(default=None, description="End date YYYY-MM-DD"),
    include_walk_forward: bool = Query(default=False),
    reconcile_sep2024: bool = Query(
        default=True,
        description="Require Sep 2024 baseline SD/HTF reconciliation before full run",
    ),
) -> dict[str, Any]:
    """Research-only S3 HTF gate sensitivity (variants share pre-HTF path).

    Does not modify production S3 or HTF engines. Does not declare a winner.
    """
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    syms = None
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    payload = await get_bos_strategy_comparison_service().s3_htf_sensitivity(
        symbols=syms,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        include_walk_forward=include_walk_forward,
        reconcile_sep2024_window=reconcile_sep2024,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/bos-strategies/s3-diagnostics")
async def research_bos_strategies_s3_diagnostics(
    symbol: str = Query(..., description="Symbol, e.g. BTCUSDT"),
    timeframe: str = Query(default="15m"),
    start: str | None = Query(default=None, description="Start date YYYY-MM-DD"),
    end: str | None = Query(default=None, description="End date YYYY-MM-DD"),
    direction: str = Query(default="ALL", description="ALL | LONG | SHORT"),
    limit: int | None = Query(
        default=100,
        ge=1,
        le=500,
        description="Max lifecycle traces when trace=true",
    ),
    trace: bool = Query(
        default=False,
        description="If true, include per-lifecycle S/D/HTF/entry traces",
    ),
    htf_trace: bool = Query(
        default=False,
        description="If true, decompose HTF failure for every S/D PASS survivor",
    ),
) -> dict[str, Any]:
    """Forensic S3 S/D (+ optional HTF) diagnostic (research only).

    Does not change S/D or HTF logic, loosen thresholds, or run optimization.
    """
    from app.research.bos_strategy_comparison.service import (
        get_bos_strategy_comparison_service,
    )

    payload = await get_bos_strategy_comparison_service().s3_diagnostics(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        limit=limit,
        direction=direction,
        trace=trace,
        htf_trace=htf_trace,
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/signals/{symbol}")
async def signal_for_symbol(
    symbol: str,
    account_equity: float | None = None,
    risk_percent: float | None = None,
    leverage: float | None = None,
) -> dict[str, Any]:
    from app.services.setup_signals import get_setup_signal_service
    from app.services.paper_trade import get_paper_trade_engine

    svc = get_setup_signal_service()
    orch = get_orchestrator()
    # Strategy signals require live closed candles — refresh MTF tips when WS silent
    if orch is not None and getattr(orch, "backfill", None) is not None:
        cfg = svc.config
        try:
            await orch.backfill.ensure_setup_mtf_fresh(
                symbol.upper(),
                [cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry],
                force_setup_tip=True,
                setup_tf=cfg.mtf_setup,
            )
        except Exception:  # noqa: BLE001
            pass
    payload = svc.analyze_symbol(
        symbol,
        account_equity=account_equity,
        risk_percent=risk_percent,
        leverage=leverage,
    )
    # Auto paper: open as soon as this request produces an entry candidate
    try:
        get_paper_trade_engine().on_setup_signal(symbol, payload)
    except Exception:  # noqa: BLE001
        pass
    return {
        **payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/paper/status")
async def paper_status() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    return get_paper_trade_engine().status()


@router.get("/paper/positions")
async def paper_positions(closed_limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    return get_paper_trade_engine().positions(closed_limit=closed_limit)


@router.get("/paper/opportunities")
async def paper_opportunities(
    limit: int = Query(default=100, ge=1, le=300),
    warm: int = Query(default=25, ge=0, le=80),
) -> dict[str, Any]:
    """List setups across many coins — READY/NEAR plus WAITING/WATCH so the book is visible."""
    from app.services.paper_trade import get_paper_trade_engine, list_trade_opportunities
    from app.services.setup_signals import get_setup_signal_service
    from app.services.market_store import market_store

    eng = get_paper_trade_engine()
    orch = get_orchestrator()
    setup_svc = get_setup_signal_service()
    cfg = setup_svc.config
    mtf = [cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry]

    # Warm top-volume / visible symbols missing a computed setup (bounded)
    warmed = 0
    if warm > 0 and orch is not None and getattr(orch, "backfill", None) is not None:
        from app.services.engine_store import engine_store as es

        ranked = sorted(
            market_store.tickers.keys(),
            key=lambda s: float(getattr(market_store.tickers.get(s), "quote_volume_24h", 0) or 0),
            reverse=True,
        )
        visible = set(getattr(orch.backfill, "_visible", set()) or set())
        prefer = list(dict.fromkeys([*sorted(visible), *ranked]))
        for sym in prefer:
            if warmed >= warm:
                break
            cached = es.get_setup_signal(sym)
            # Skip symbols that already have a non-waiting setup
            if cached and str(cached.get("status") or "") not in ("", "WAITING"):
                continue
            try:
                # Setup TF tip only — keep warm cheap for the opportunities page
                await orch.backfill.ensure_series_fresh(
                    sym, cfg.mtf_setup, limit=80, force_tip=True
                )
                for tf in (cfg.mtf_primary, cfg.mtf_major, cfg.mtf_entry):
                    await orch.backfill.ensure_series_fresh(sym, tf, limit=80, force_tip=False)
                setup_svc.ensure_computed(sym, force=True)
                warmed += 1
            except Exception:  # noqa: BLE001
                continue

    open_syms = set(eng._open.keys())  # noqa: SLF001
    rows = list_trade_opportunities(limit=limit, open_symbols=open_syms, include_waiting=True)
    by_tier: dict[str, int] = {}
    for r in rows:
        t = str(r.get("tier") or "?")
        by_tier[t] = by_tier.get(t, 0) + 1
    return {
        "rows": rows,
        "count": len(rows),
        "ready": by_tier.get("READY", 0),
        "near": by_tier.get("NEAR", 0),
        "forming": by_tier.get("FORMING", 0),
        "waiting": by_tier.get("WAITING", 0),
        "watch": by_tier.get("WATCH", 0),
        "blocked": by_tier.get("BLOCKED", 0),
        "by_tier": by_tier,
        "warmed": warmed,
        "auto_enabled": eng.enabled,
        "note": (
            f"Auto mode={eng.entry_mode}. READY = Path A Trend+BOS opens when Auto ON"
            if eng.entry_mode == "path_a"
            else "READY = LONG_ENTRY_CANDIDATE. WAITING = OHLCV/setup not ready."
        ),
        "entry_mode": eng.entry_mode,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/paper/enable")
async def paper_enable() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    eng = get_paper_trade_engine()
    eng.enable()
    return eng.status()


@router.post("/paper/disable")
async def paper_disable() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    eng = get_paper_trade_engine()
    eng.disable()
    return eng.status()


@router.post("/paper/reset")
async def paper_reset() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine
    from app.services.persistence import persistence

    eng = get_paper_trade_engine()
    eng.reset()
    # Keep DB in sync so a restart does not resurrect the wiped book
    try:
        await persistence.clear_paper_trades()
    except Exception:  # noqa: BLE001
        pass
    return eng.status()


@router.get("/alerts")
async def list_alerts(
    limit: int = Query(default=100, ge=1, le=500),
    types: str | None = Query(
        default=None,
        description="Comma-separated: BOS,SETUP_STATUS,MARKET_SIGNAL,PAPER_ENTRY,PAPER_EXIT,LIQ_SPIKE",
    ),
    symbol: str | None = Query(default=None),
    since_seq: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Live alert feed — setup / paper / liquidation transitions (in-memory)."""
    from app.services.alerts import get_alert_feed

    type_list = [t.strip() for t in (types or "").split(",") if t.strip()] or None
    return get_alert_feed().list(
        limit=limit,
        types=type_list,
        symbol=symbol,
        since_seq=since_seq,
    )


@router.get("/signals/{symbol}/{timeframe}")
async def signal_for_symbol_tf(symbol: str, timeframe: str) -> dict[str, Any]:
    from app.services.setup_signals import get_setup_signal_service
    from app.services.engine_store import engine_store as es
    from app.signals.signal_engine import SignalEngine

    # Guard reserved subpaths if router order changes
    if timeframe.lower() in {
        "risk",
        "targets",
        "explanation",
        "annotations",
        "backtest",
    }:
        return {"status": "UNAVAILABLE", "message": f"Use /signals/{{symbol}}/{timeframe}"}

    svc = get_setup_signal_service()
    candles = ohlcv_store.get_candles_for_engine(
        symbol.upper(), timeframe, include_open=False
    )
    if not candles:
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "status": "WAITING",
            "reason": f"{timeframe.upper()}: WAITING FOR OHLCV",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    engine = SignalEngine(svc.config)
    rvol = es.get_rvol(symbol.upper(), timeframe)
    tf_analysis = engine.analyze_timeframe(
        symbol.upper(),
        timeframe,
        candles,
        rvol=float(rvol.value) if rvol.value is not None else None,
    )
    tf_analysis.pop("_swings_objs", None)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        **tf_analysis,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/charts/{symbol}/ohlcv")
async def chart_ohlcv(
    symbol: str,
    timeframe: str = Query(default="15m"),
    limit: int = Query(default=200, ge=10, le=1000),
) -> dict[str, Any]:
    from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe

    sym = symbol.upper()
    orch = get_orchestrator()
    # Serve in-memory tip immediately. REST catch-up runs in the background so
    # chart polls never queue behind the universe backfill (can take 60s+).
    if orch is not None and getattr(orch, "backfill", None) is not None:
        try:
            visible = set(getattr(orch.backfill, "_visible", set()) or set())
            visible.add(sym)
            orch.backfill.set_visible_symbols(list(visible))
            orch.set_kline_focus([sym])
            # Only resubscribe when this chart symbol is not already on the live WS
            current = set(getattr(orch.kline_ws, "_symbols", []) or [])
            if sym not in current:
                try:
                    asyncio.create_task(
                        orch.refresh_kline_live_subscriptions(),
                        name=f"kline_focus_{sym}",
                    )
                except Exception:  # noqa: BLE001
                    pass
            tip_key = (sym, normalize_timeframe(timeframe))
            last = getattr(orch.backfill, "_last_tip_fetch", {}).get(tip_key, 0.0)
            import time as _time

            min_s = float(getattr(orch.backfill, "tip_refresh_min_seconds", 10.0))
            if _time.monotonic() - float(last or 0.0) >= min_s:
                asyncio.create_task(
                    orch.backfill.ensure_series_fresh(
                        sym, timeframe, limit=limit, force_tip=True
                    ),
                    name=f"chart_tip_{sym}_{timeframe}",
                )
        except Exception:  # noqa: BLE001
            pass

    candles = ohlcv_store.get_closed(sym, timeframe, limit=limit)
    open_c = ohlcv_store.get_open(sym, timeframe)

    # Cold or stale tip: short direct kline pull (not the backfill worker queue)
    from app.ingestion.klines import BINANCE_INTERVAL, is_trailing_stale, normalize_rest_kline

    tip_view = list(candles)
    if open_c is not None:
        tip_view.append(open_c)
    need_direct = (not tip_view) or is_trailing_stale(tip_view, timeframe)
    if need_direct and orch is not None:
        rest = getattr(getattr(orch, "backfill", None), "rest", None)
        if rest is not None:
            try:
                interval = BINANCE_INTERVAL.get(normalize_timeframe(timeframe), timeframe)
                raw = await asyncio.wait_for(
                    rest.futures_klines(sym, interval, limit=min(max(limit, 50), 250)),
                    timeout=2.5,
                )
                batch = []
                open_new = None
                for row in raw or []:
                    c = normalize_rest_kline(sym, timeframe, row)
                    if c is None:
                        continue
                    if not c.is_closed:
                        open_new = c
                    else:
                        batch.append(c)
                if batch:
                    await ohlcv_store.ingest_history(batch)
                if open_new is not None:
                    await ohlcv_store.upsert_candle(open_new)
                candles = ohlcv_store.get_closed(sym, timeframe, limit=limit)
                open_c = ohlcv_store.get_open(sym, timeframe)
            except Exception:  # noqa: BLE001
                pass

    rows = [c.model_dump(mode="json") for c in candles]

    # Snap forming candle to live mark/ticker so tip matches Binance price
    live_px: float | None = None
    mark = market_store.mark_prices.get(sym)
    if mark is not None and getattr(mark, "mark_price", None):
        try:
            live_px = float(mark.mark_price)
        except (TypeError, ValueError):
            live_px = None
    if live_px is None:
        tick = market_store.get_ticker(sym)
        if tick is not None and tick.price:
            try:
                live_px = float(tick.price)
            except (TypeError, ValueError):
                live_px = None

    if open_c is not None:
        row = open_c.model_dump(mode="json")
        if live_px is not None and live_px > 0:
            hi = max(float(row.get("high") or live_px), live_px)
            lo = min(float(row.get("low") or live_px), live_px)
            row["high"] = hi
            row["low"] = lo
            row["close"] = live_px
            row["source"] = f"{row.get('source') or 'ohlcv'}+ticker"
        rows.append(row)
    elif live_px is not None and rows:
        last = rows[-1]
        try:
            step = TIMEFRAME_MS.get(normalize_timeframe(timeframe), 60_000)
            ot = last.get("open_time")
            if isinstance(ot, str):
                ot_dt = datetime.fromisoformat(ot.replace("Z", "+00:00"))
            else:
                ot_dt = ot
            age_ms = (datetime.now(timezone.utc) - ot_dt).total_seconds() * 1000
            if ot_dt is not None and 0 <= age_ms < step:
                last = dict(last)
                last["close"] = live_px
                last["high"] = max(float(last.get("high") or live_px), live_px)
                last["low"] = min(float(last.get("low") or live_px), live_px)
                rows[-1] = last
        except Exception:  # noqa: BLE001
            pass

    status = "LIVE" if rows else "WAITING"
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "candles": rows,
        "count": len(rows),
        "status": status,
        "source": "ohlcv_store",
        "live_price": live_px,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/charts/{symbol}/liquidations")
async def chart_liquidations(
    symbol: str,
    window: str = Query(default="1h"),
) -> dict[str, Any]:
    orch = get_orchestrator()
    sym = symbol.upper()
    if orch is None:
        return {"symbol": sym, "status": "WAITING", "aggregates": {}, "events": []}
    aggs = orch.liquidations.aggregates(sym)
    events = []
    # Access underlying ingestion for event list (provider stays abstraction for status)
    underlying = getattr(orch.liquidations, "_ingestion", None)
    if underlying is not None:
        st = underlying._by_symbol.get(sym)
        if st is not None:
            events = [
                {
                    "symbol": e.symbol,
                    "timestamp": e.timestamp.isoformat(),
                    "side": e.side,
                    "price": e.price,
                    "quantity": e.quantity,
                    "notional": e.notional,
                }
                for e in list(st.events)[-200:]
            ]
    status = orch.liquidations.liquidation_status().value
    # Never fabricate events for non-LIVE/STALE presentation
    if status in ("WAITING", "UNAVAILABLE"):
        events = []
    last_event_time = events[-1]["timestamp"] if events else None
    return {
        "symbol": sym,
        "window": window,
        "aggregates": aggs,
        "events": events,
        "event_count": len(events),
        "last_event_time": last_event_time,
        "status": status,
        "liquidation_status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/charts/{symbol}/oi")
async def chart_oi(symbol: str) -> dict[str, Any]:
    orch = get_orchestrator()
    sym = symbol.upper()
    if orch is None:
        return {"symbol": sym, "status": "WAITING", "history": []}
    st = orch.oi.get_state(sym)
    if st is None or not st.history:
        return {"symbol": sym, "status": "WAITING", "history": []}
    history = [
        {
            "timestamp": s.timestamp.isoformat(),
            "open_interest": s.open_interest,
            "price": s.price,
            "open_interest_value": s.open_interest_value,
        }
        for s in st.history
    ]
    return {
        "symbol": sym,
        "history": history,
        "open_interest": st.open_interest.model_dump(mode="json"),
        "changes": st.changes,
        "classification": st.oi_classification.model_dump(mode="json"),
        "status": st.open_interest.status.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ingestion/status")
async def ingestion_status() -> dict[str, Any]:
    ingestion = get_ingestion()
    if ingestion is None:
        return {"status": "not_started"}
    return ingestion.status()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.active.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


ws_connections = ConnectionManager()


async def _forward_market_events(event: dict[str, Any]) -> None:
    if event.get("type") in {
        "ticker",
        "mark_price",
        "market_batch",
        "symbols_updated",
    }:
        await ws_connections.broadcast(event)


@ws_router.websocket("/alerts")
async def ws_alerts(websocket: WebSocket) -> None:
    """Push new alerts as they fire; initial snapshot of recent buffer."""
    from app.services.alerts import get_alert_feed

    await websocket.accept()
    feed = get_alert_feed()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)

    async def _on_alert(alert: dict[str, Any]) -> None:
        try:
            queue.put_nowait(alert)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(alert)
            except asyncio.QueueFull:
                pass

    feed.subscribe(_on_alert)
    try:
        snap = feed.list(limit=80)
        await websocket.send_json(
            {
                "type": "alerts_snapshot",
                "rows": snap.get("rows") or [],
                "latest_seq": snap.get("latest_seq"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        while True:
            try:
                alert = await asyncio.wait_for(queue.get(), timeout=15.0)
                await websocket.send_json(
                    {
                        "type": "alert",
                        "alert": alert,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "type": "alerts_heartbeat",
                        "latest_seq": feed.list(limit=1).get("latest_seq"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        feed.unsubscribe(_on_alert)


@ws_router.websocket("/market")
async def ws_market(websocket: WebSocket) -> None:
    await ws_connections.connect(websocket)
    market_store.subscribe(_forward_market_events)
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "symbols": len(market_store.symbols),
                "tickers": market_store.live_ticker_count(),
                "ingestion": market_store.ingestion_status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        market_store.unsubscribe(_forward_market_events)
        await ws_connections.disconnect(websocket)


@ws_router.websocket("/screener")
async def ws_screener(websocket: WebSocket) -> None:
    """Incremental row patches — not full 527-row payloads every tick."""
    await websocket.accept()
    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    prev: dict[str, dict[str, Any]] = {}
    try:
        from app.services.redis_state import redis_state

        # On reconnect: push Redis snapshot immediately if present
        cached = await redis_state.get_json("state:screener:latest")
        if isinstance(cached, dict) and cached.get("rows"):
            await websocket.send_json(
                {
                    "type": "screener_snapshot",
                    "total": cached.get("total"),
                    "rows": cached.get("rows"),
                    "ingestion": cached.get("ingestion") or market_store.ingestion_status,
                    "source": "redis",
                    "timestamp": cached.get("timestamp")
                    or datetime.now(timezone.utc).isoformat(),
                }
            )
            for r in cached.get("rows") or []:
                if isinstance(r, dict) and r.get("symbol"):
                    prev[r["symbol"]] = r

        # Fresh in-memory snapshot (authoritative) — ≤100 screen universe
        from app.services.screen_universe import MAX_SCREEN_SYMBOLS

        t0 = asyncio.get_event_loop().time()
        rows, total, meta = svc.futures_screener(limit=MAX_SCREEN_SYMBOLS, offset=0)
        performance_monitor.record_screener_latency(
            (asyncio.get_event_loop().time() - t0) * 1000
        )
        payload_rows = [r.model_dump(mode="json") for r in rows]
        for r in payload_rows:
            prev[r["symbol"]] = r
        # Preferential OI / backfill for visible screener symbols
        orch = get_orchestrator()
        if orch is not None:
            visible = [r["symbol"] for r in payload_rows]
            orch.oi.set_visible_symbols(visible)
            if getattr(orch, "backfill", None) is not None:
                orch.backfill.set_visible_symbols(visible)
        snapshot = {
            "type": "screener_snapshot",
            "total": total,
            "rows": payload_rows,
            "ingestion": market_store.ingestion_status,
            "source": "memory",
            "total_universe": meta.get("total_universe"),
            "eligible_count": meta.get("eligible_count"),
            "returned_count": meta.get("returned_count"),
            "limit": meta.get("limit"),
            "selection_updated_at": meta.get("selection_updated_at"),
            "excluded": meta.get("excluded") or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await websocket.send_json(snapshot)
        try:
            await redis_state.store_screener_state(
                {
                    "total": total,
                    "rows": payload_rows[:MAX_SCREEN_SYMBOLS],
                    "symbols": [r["symbol"] for r in payload_rows[:MAX_SCREEN_SYMBOLS]],
                    "ingestion": market_store.ingestion_status,
                    "total_universe": meta.get("total_universe"),
                    "eligible_count": meta.get("eligible_count"),
                    "timestamp": snapshot["timestamp"],
                }
            )
            performance_monitor.record_redis_op()
        except Exception:  # noqa: BLE001
            pass
        while True:
            await asyncio.sleep(1.0)
            t0 = asyncio.get_event_loop().time()
            # Membership refresh uses selector TTL; row fields still rebuild cheaply
            rows, total, meta = svc.futures_screener(limit=MAX_SCREEN_SYMBOLS, offset=0)
            performance_monitor.record_screener_latency(
                (asyncio.get_event_loop().time() - t0) * 1000
            )
            if orch is not None:
                visible = [r.symbol for r in rows]
                orch.oi.set_visible_symbols(visible)
                if getattr(orch, "backfill", None) is not None:
                    orch.backfill.set_visible_symbols(visible)
                # Enqueue only — ensure loop drains ≤25 / 0.5s (no universe sync)
                orch.request_setup_for_visible(visible)
            # If membership changed, send a fresh snapshot (anti-flicker keeps order stable)
            new_syms = [r.symbol for r in rows]
            old_syms = list(prev.keys())
            if new_syms != old_syms:
                prev = {r.symbol: r.model_dump(mode="json") for r in rows}
                await websocket.send_json(
                    {
                        "type": "screener_snapshot",
                        "total": total,
                        "rows": [r.model_dump(mode="json") for r in rows],
                        "ingestion": market_store.ingestion_status,
                        "source": "memory",
                        "total_universe": meta.get("total_universe"),
                        "eligible_count": meta.get("eligible_count"),
                        "returned_count": meta.get("returned_count"),
                        "limit": meta.get("limit"),
                        "selection_updated_at": meta.get("selection_updated_at"),
                        "excluded": meta.get("excluded") or {},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue
            patches = 0
            for row in rows:
                changes = svc.row_patch_changes(prev.get(row.symbol), row)
                if not changes:
                    continue
                # Always refresh cache
                full = row.model_dump(mode="json")
                prev[row.symbol] = full
                # Only send changed fields (plus identity)
                slim = {
                    k: v
                    for k, v in changes.items()
                    if k
                    not in {
                        "rank",
                        "base_asset",
                        "quote_asset",
                        "exchange",
                        "market_type",
                    }
                }
                if not slim:
                    continue
                await websocket.send_json(
                    {
                        "type": "row_patch",
                        "symbol": row.symbol,
                        "changes": slim,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
                patches += 1
            await websocket.send_json(
                {
                    "type": "screener_heartbeat",
                    "total": total,
                    "patches": patches,
                    "total_universe": meta.get("total_universe"),
                    "eligible_count": meta.get("eligible_count"),
                    "returned_count": meta.get("returned_count"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
    except WebSocketDisconnect:
        pass


@ws_router.websocket("/coin/{symbol}")
async def ws_coin(websocket: WebSocket, symbol: str) -> None:
    sym = symbol.upper()
    await websocket.accept()
    settings = get_settings()
    svc = ScreenerService(settings, market_store)

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("symbol") == sym or (
            event.get("type") == "market_batch"
            and any(u.get("symbol") == sym for u in event.get("updates") or [])
        ):
            info = market_store.symbols.get(sym)
            if info is None:
                return
            row = svc.build_row(info)
            await websocket.send_json(
                {
                    "type": "coin_update",
                    "symbol": sym,
                    "row": row.model_dump(mode="json"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

    market_store.subscribe(on_event)
    try:
        info = market_store.symbols.get(sym)
        if info is None:
            await websocket.send_json(
                {"type": "error", "message": "Data unavailable", "symbol": sym}
            )
        else:
            await websocket.send_json(
                {
                    "type": "coin_snapshot",
                    "symbol": sym,
                    "row": svc.build_row(info).model_dump(mode="json"),
                }
            )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        market_store.unsubscribe(on_event)
