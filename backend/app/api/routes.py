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
    from app.services.active_universe import active_universe_meta

    uni = active_universe_meta()
    return HealthResponse(
        status="ok",
        use_real_data=settings.use_real_data,
        redis=redis_h.get("status", "unknown"),
        database=db_h.get("status", "unknown"),
        ingestion=market_store.ingestion_status,
        # Nav/screens show active compute universe (not full Binance discovery).
        symbols_loaded=int(uni["active_universe"]),
        discovered_symbols=int(uni["discovered_universe"]),
        active_universe=int(uni["active_universe"]),
        active_universe_cap=int(uni["active_universe_cap"]),
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
    from app.services.active_universe import list_active_symbols

    _active_n = len(list_active_symbols())
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
        "free_social": sentiment.diagnostic(universe_size=_active_n),
        "lunarcrush": (
            sentiment.diagnostic(universe_size=_active_n)
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
    from app.services.active_universe import list_active_symbols

    sentiment = (
        orch.sentiment
        if orch is not None and getattr(orch, "sentiment", None) is not None
        else get_sentiment_provider(get_settings())
    )
    universe = list_active_symbols()
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
    scope: str = Query(
        default="active",
        description="active = screen/compute universe; discovered = full Binance list",
    ),
) -> dict[str, Any]:
    from app.services.active_universe import (
        active_universe_meta,
        list_active_symbols,
    )

    discovered = market_store.list_symbols(market_type=market_type)
    scope_l = (scope or "active").strip().lower()
    if scope_l in {"discovered", "all", "full"}:
        symbols = discovered
    else:
        active = set(list_active_symbols([s.symbol for s in discovered]))
        symbols = [s for s in discovered if s.symbol.upper() in active]
    uni = active_universe_meta([s.symbol for s in discovered])
    return {
        "count": len(symbols),
        "symbols": [s.model_dump() for s in symbols],
        "scope": "discovered" if scope_l in {"discovered", "all", "full"} else "active",
        "discovered_universe": uni["discovered_universe"],
        "active_universe": uni["active_universe"],
        "active_universe_cap": uni["active_universe_cap"],
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
    payload = {
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
        "discovered_universe": meta.get("discovered_universe"),
        "active_universe": meta.get("active_universe"),
        "active_universe_cap": meta.get("active_universe_cap"),
        "eligible_count": meta.get("eligible_count"),
        "returned_count": meta.get("returned_count", len(payload_rows)),
        "selection_updated_at": meta.get("selection_updated_at"),
        "excluded": meta.get("excluded") or {},
        "search_mode": bool(meta.get("search_mode")),
        "screen_filter": meta.get("screen_filter"),
        "max_screen_symbols": meta.get("max_screen_symbols", 100),
        "screen_label": "ACTIVE UNIVERSE",
    }
    # Presentation-only enrichment (v1 labels / potential levels). Never opens trades.
    try:
        from app.services.screener_presentation import enrich_screener_payload

        enrich_screener_payload(payload)
    except Exception:  # noqa: BLE001
        payload["is_telegram_eligible"] = False
    return payload


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
        # Eagerly fill setup for THIS page only (cap sync work; rest via ensure queue).
        # Tip REST refreshes previously ran sequentially (≤12 symbols × 4 TFs) and
        # dominated screener latency; refresh in parallel then compute/persist once.
        from app.ingestion.klines import is_trailing_stale
        from app.services.engine_store import engine_store as es
        from app.services.setup_signals import get_setup_signal_service

        setup_svc = get_setup_signal_service()
        sync_budget = 12
        cfg = setup_svc.config
        mtf = [cfg.mtf_major, cfg.mtf_primary, cfg.mtf_setup, cfg.mtf_entry]
        sync_jobs: list[tuple[int, Any, bool]] = []
        deferred: list[str] = []
        for i, row in enumerate(rows):
            cached = es.get_setup_signal(row.symbol)
            needs = setup_svc.setup_ohlcv_ready(row.symbol) and (
                not cached or setup_svc.is_stale(cached, row.symbol)
            )
            tip = ohlcv_store.get_closed(row.symbol, cfg.mtf_setup)
            tip_stale = bool(tip) and is_trailing_stale(tip, cfg.mtf_setup)
            if not needs and not tip_stale:
                continue
            if len(sync_jobs) < sync_budget:
                sync_jobs.append((i, row, tip_stale))
            else:
                deferred.append(row.symbol)

        if sync_jobs and getattr(orch, "backfill", None) is not None:

            async def _refresh_one(sym: str) -> None:
                try:
                    await orch.backfill.ensure_setup_mtf_fresh(
                        sym,
                        mtf,
                        force_setup_tip=True,
                        setup_tf=cfg.mtf_setup,
                    )
                except Exception:  # noqa: BLE001
                    pass

            await asyncio.gather(
                *[_refresh_one(row.symbol) for _, row, _ in sync_jobs]
            )

        paper_touched = False
        for i, row, tip_stale in sync_jobs:
            setup_svc.ensure_computed(row.symbol, force=tip_stale)
            try:
                from app.services.paper_trade import get_paper_trade_engine
                from app.services.engine_store import engine_store as es2

                eng = get_paper_trade_engine()
                eng.on_setup_signal(row.symbol, es2.get_setup_signal(row.symbol))
                paper_touched = True
            except Exception:  # noqa: BLE001
                pass
            info = market_store.symbols.get(row.symbol)
            if info is not None:
                rows[i] = svc.build_row(info, rank=row.rank)
                rows[i].screen_priority_score = row.screen_priority_score
                rows[i].screen_priority_reason = row.screen_priority_reason

        if paper_touched:
            try:
                from app.services.paper_trade import (
                    flush_paper_trade_persists,
                    get_paper_trade_engine,
                )

                await flush_paper_trade_persists(get_paper_trade_engine())
            except Exception:  # noqa: BLE001
                pass

        if deferred:
            setup_svc.enqueue_ensure(deferred)
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
    from app.signals.signal_engine import ensure_trade_plan_entry_annotation

    payload = es.get_setup_signal(symbol) or get_setup_signal_service().analyze_symbol(
        symbol
    )
    return {
        "symbol": symbol.upper(),
        "annotations": ensure_trade_plan_entry_annotation(payload or {}),
        "status": payload.get("status") if payload else None,
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
    # One grouped scan instead of N×M sequential COUNT/MIN/MAX round-trips.
    rows: list[dict[str, Any]] = []
    found: dict[tuple[str, str], dict[str, Any]] = {}
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT symbol, timeframe, COUNT(*), MIN(time), MAX(time)
                FROM ohlcv
                WHERE symbol = ANY(:syms) AND timeframe = ANY(:tfs)
                GROUP BY symbol, timeframe
                """
            ),
            {"syms": syms, "tfs": tfs},
        )
        for sym, tf, n, t0, t1 in r.fetchall():
            found[(str(sym).upper(), str(tf))] = {
                "symbol": str(sym).upper(),
                "timeframe": str(tf),
                "bars": int(n or 0),
                "start": t0.isoformat() if t0 else None,
                "end": t1.isoformat() if t1 else None,
            }
    for sym in syms:
        for tf in tfs:
            rows.append(
                found.get(
                    (sym, tf),
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "bars": 0,
                        "start": None,
                        "end": None,
                    },
                )
            )
    return {
        "status": "OK",
        "rows": rows,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _parse_research_chart_ts(raw: str) -> datetime:
    """Parse ISO / YYYY-MM-DD timestamps for blotter chart window loads."""
    s = str(raw or "").strip()
    if not s:
        raise ValueError("empty timestamp")
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/research/ohlcv-candles")
async def research_ohlcv_candles(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    start: str = Query(..., description="Window start (ISO or YYYY-MM-DD)"),
    end: str | None = Query(
        default=None, description="Window end (ISO or YYYY-MM-DD); defaults to start"
    ),
    pad_bars: int = Query(default=64, ge=0, le=500),
) -> dict[str, Any]:
    """Load Postgres OHLCV around a blotter trade window for backtest chart overlay.

    Presentation-only — does not alter research entry logic.
    """
    from datetime import timedelta

    from app.ingestion.klines import TIMEFRAME_MS
    from app.research.postgres_ohlcv import load_ohlcv_series_range
    from app.research.query_utils import (
        normalize_research_symbol,
        normalize_research_timeframe,
    )
    from app.services.database import db_manager

    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    try:
        start_dt = _parse_research_chart_ts(start)
        end_dt = _parse_research_chart_ts(end) if end else start_dt
    except ValueError as exc:
        return {
            "status": "ERROR",
            "reason": f"invalid_timestamp: {exc}",
            "symbol": sym,
            "timeframe": tf,
            "candles": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    step_ms = int(TIMEFRAME_MS.get(tf) or 60_000)
    pad = timedelta(milliseconds=step_ms * int(pad_bars))
    # end_exclusive: one bar past padded exit so the exit candle is included
    range_start = start_dt - pad
    range_end_excl = end_dt + pad + timedelta(milliseconds=step_ms)

    if db_manager.engine is None:
        return {
            "status": "UNAVAILABLE",
            "reason": "database_unavailable",
            "symbol": sym,
            "timeframe": tf,
            "candles": [],
            "window_start": range_start.isoformat(),
            "window_end_exclusive": range_end_excl.isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    try:
        raw = await load_ohlcv_series_range(
            sym,
            tf,
            start=range_start,
            end_exclusive=range_end_excl,
            warmup_bars=0,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "ERROR",
            "reason": str(exc),
            "symbol": sym,
            "timeframe": tf,
            "candles": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    candles: list[dict[str, Any]] = []
    for c in raw:
        t = c.get("time")
        if isinstance(t, datetime):
            open_time = t.astimezone(timezone.utc).isoformat()
        else:
            open_time = str(t)
        candles.append(
            {
                "open_time": open_time,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume") or 0),
            }
        )

    return {
        "status": "OK" if candles else "EMPTY",
        "symbol": sym,
        "timeframe": tf,
        "pad_bars": int(pad_bars),
        "window_start": range_start.isoformat(),
        "window_end_exclusive": range_end_excl.isoformat(),
        "candles": candles,
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
      until: YYYY-MM-DD (walk history back to this UTC day; omit for tip-only)
      refresh_tip: bool (fill forward to now; default true; required if until omitted)
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
    """Start a background HL/LH Trend+BOS matrix backtest (survives UI navigation).

    Configuration-safety only: resolves v1 risk profile metadata and rejects
    paused SHORT requests. Does not create paper/live trades or send Telegram.
    """
    from app.research.backtest_job import backtest_job_service
    from app.research.backtest_ui_config import BacktestConfigError

    def _as_list(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v]
        return [s for s in str(v).split(",") if s.strip()]

    # Support single-symbol request shape from the safety contract.
    if body.get("symbol") and not body.get("symbols"):
        symbols = _as_list(body.get("symbol"))
    else:
        symbols = _as_list(body.get("symbols") or "BTCUSDT,ETHUSDT,SOLUSDT")
    if body.get("setup_timeframe") and not body.get("timeframes"):
        timeframes = _as_list(body.get("setup_timeframe"))
    else:
        timeframes = _as_list(body.get("timeframes") or "1h")

    principal = float(body.get("principal") or body.get("principal_usd") or 1000.0)
    risk_usd = body.get("risk_usd")
    if risk_usd is None and body.get("risk_percent") is not None:
        rp = float(body.get("risk_percent"))
        pct = rp / 100.0 if rp > 1.0 else rp
        risk_usd = principal * pct
    if risk_usd is None:
        risk_usd = 20.0

    try:
        job = await backtest_job_service.start(
            symbols=symbols,
            timeframes=timeframes,
            direction=str(body.get("direction") or "LONG"),
            combination_id=str(body.get("combination_id") or "COMBO_02"),
            strategy_id=(
                str(body.get("strategy_id")) if body.get("strategy_id") else None
            ),
            combo_version=(
                str(body.get("combo_version")) if body.get("combo_version") else None
            ),
            setup_timeframe=(
                str(body.get("setup_timeframe"))
                if body.get("setup_timeframe")
                else None
            ),
            risk_mode=(
                str(body.get("risk_mode")) if body.get("risk_mode") else None
            ),
            research_risk_override=bool(body.get("research_risk_override") or False),
            limit=int(body.get("limit") or 1200),
            risk_usd=float(risk_usd),
            risk_percent=(
                float(body.get("risk_percent"))
                if body.get("risk_percent") is not None
                else None
            ),
            principal_usd=principal,
            leverage=float(body.get("leverage") or 2.0),
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
    except BacktestConfigError as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "error_code": exc.code,
            "short_research_paused": exc.code == "short_research_paused",
            "paper_trade_created": False,
            "live_trade_created": False,
            "telegram_sent": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except ValueError as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "error_code": "validation_error",
            "paper_trade_created": False,
            "live_trade_created": False,
            "telegram_sent": False,
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
        "paper_trade_created": False,
        "live_trade_created": False,
        "telegram_sent": False,
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


@router.get("/research/combo02-candidate-results")
async def research_combo02_candidate_results(
    stamp: str | None = Query(
        default=None,
        description="Optional results timestamp suffix (YYYYMMDDTHHMMSSZ)",
    ),
) -> dict[str, Any]:
    """Read-only latest COMBO_02 candidate research report (no execution rights).

    Does not start backtests, promote symbols, or alter v1/Telegram eligibility.
    """
    from pathlib import Path

    reports = Path(__file__).resolve().parents[1] / "reports"
    pattern = "combo02_candidate_results_*.json"
    files = sorted(reports.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if stamp:
        target = reports / f"combo02_candidate_results_{stamp}.json"
        if not target.is_file():
            return {
                "status": "NOT_FOUND",
                "error": f"No results for stamp={stamp}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        chosen = target
    elif files:
        chosen = files[0]
    else:
        return {
            "status": "EMPTY",
            "results": [],
            "disclaimer": (
                "Research only. No symbol from this report was added to the frozen "
                "COMBO_02 v1 paper/live universe or Telegram eligibility list."
            ),
            "note": "No candidate research artifacts yet. Run scripts/run_combo02_candidate_research.py",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    import json

    payload = json.loads(chosen.read_text(encoding="utf-8"))
    # Slim UI payload — drop bulky condition matrices if huge
    slim_results = []
    for r in payload.get("results") or []:
        slim_results.append(
            {
                "symbol": r.get("symbol"),
                "eligibility_tier": r.get("eligibility_tier"),
                "oos_label": r.get("oos_label"),
                "net_avg_r": r.get("net_avg_r"),
                "net_pnl": r.get("net_pnl"),
                "profit_factor": r.get("profit_factor"),
                "max_drawdown_r": r.get("max_drawdown_r"),
                "trade_count": r.get("trade_count"),
                "overlap_pct_BTCUSDT": r.get("overlap_pct_BTCUSDT"),
                "corr_daily_net_r_vs_btc": r.get("corr_daily_net_r_vs_btc"),
                "peak_concurrent_with_v1_book": r.get("peak_concurrent_with_v1_book"),
                "run_status": r.get("run_status"),
            }
        )
    return {
        "status": "OK",
        "source_file": chosen.name,
        "generated_at_utc": payload.get("generated_at_utc"),
        "fingerprint": payload.get("fingerprint"),
        "final_lists": payload.get("final_lists"),
        "portfolio": payload.get("portfolio") or [],
        "oos": payload.get("oos") or [],
        "results": slim_results,
        "disclaimer": payload.get("disclaimer"),
        "v1_unchanged": True,
        "read_only": True,
        "execution_rights": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }



@router.get("/research/short-candidates")
async def research_short_candidates() -> dict[str, Any]:
    """Read-only COMBO_02 SHORT research candidates (never paper/Telegram/v1)."""
    from app.research.combo02_short_research_runner import api_list_response

    return api_list_response()


@router.get("/research/short-candidates/{symbol}")
async def research_short_candidate_detail(symbol: str) -> dict[str, Any]:
    """Read-only detail for one SHORT research symbol."""
    from app.research.combo02_short_research_runner import api_symbol_response

    return api_symbol_response(symbol)


@router.post("/research/short-candidates/run")
async def research_short_candidates_run(
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Start SHORT research batch only — never creates paper/live trades.

    Optional body: ``symbols``, ``run_id``, ``run_oos`` (default true),
    ``timeframe`` (must be ``1h`` when provided).
    """
    from fastapi import HTTPException

    from app.research.combo02_short_research import (
        SHORT_RESEARCH_MODULE_INCOMPLETE_CODE,
        assert_short_research_pipeline_complete,
        short_research_pipeline_status,
    )
    from app.research.combo02_short_research_runner import run_short_research_batch
    from app.research.short_research_constants import (
        DEFAULT_SHORT_RESEARCH_UNIVERSE,
        SETUP_TIMEFRAME,
    )
    from app.research.short_research_quality import validate_timeframe

    try:
        assert_short_research_pipeline_complete()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": SHORT_RESEARCH_MODULE_INCOMPLETE_CODE,
                "message": str(exc),
                "pipeline_status": short_research_pipeline_status(),
                "paper_eligible": False,
                "production_approved": False,
                "telegram_eligible": False,
            },
        ) from exc

    body = payload or {}
    symbols = body.get("symbols")
    if symbols is None:
        universe = list(DEFAULT_SHORT_RESEARCH_UNIVERSE)
    else:
        universe = [str(s).upper().strip() for s in symbols]
        for sym in universe:
            if not sym or not sym.isalnum() or not sym.endswith("USDT"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "unknown_symbol",
                        "symbol": sym,
                        "message": "Symbol must be a non-empty *USDT pair",
                    },
                )
    tf = body.get("timeframe")
    if tf is not None:
        tf_check = validate_timeframe(str(tf))
        if not tf_check["ok"] or str(tf).lower() != SETUP_TIMEFRAME:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_timeframe",
                    "timeframe": tf,
                    "message": f"SHORT research setup timeframe must be {SETUP_TIMEFRAME}",
                },
            )
    run_id = body.get("run_id")
    run_oos = bool(body.get("run_oos", True))
    report = await run_short_research_batch(
        symbols=universe,
        run_id=str(run_id) if run_id else None,
        run_oos=run_oos,
        persist=True,
    )
    return {
        "status": "OK",
        "strategy_id": report.get("strategy_id"),
        "combo_version": report.get("combo_version"),
        "source": report.get("source"),
        "direction": report.get("direction"),
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "run_id": report.get("run_id"),
        "candidates": report.get("candidates") or [],
        "disclaimer": report.get("disclaimer"),
        "historical_disclaimer": report.get("historical_disclaimer"),
        "configuration_hash": report.get("configuration_hash"),
        "dataset_hash": report.get("dataset_hash"),
        "engine_version": report.get("engine_version"),
        "execution_model": report.get("execution_model"),
        "research_windows": report.get("research_windows"),
        "window_status": report.get("window_status"),
        "evidence_mode": report.get("evidence_mode"),
        "batch_summary": report.get("batch_summary"),
        "read_only": True,
        "execution_rights": False,
        "v1_unchanged": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/candidates")
async def research_dynamic_candidates(
    state: str | None = Query(default=None, description="Optional state filter"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Read-only dynamic candidate pipeline registry (v2 research only)."""
    from app.research.candidate_state_machine import badge_for_state
    from app.research.dynamic_candidate_constants import DISCLAIMER, STRATEGY_ID
    from app.research.strategy_candidate_registry import (
        serialize_candidate,
        strategy_candidate_registry,
    )

    await strategy_candidate_registry.ensure_schema()
    states = [s.strip().upper() for s in state.split(",")] if state else None
    rows = await strategy_candidate_registry.list_candidates(states=states, limit=limit)
    out = []
    for r in rows:
        item = serialize_candidate(r)
        item["badge"] = badge_for_state(
            str(item.get("state") or ""),
            state_reason=str(item.get("state_reason") or ""),
        )
        item["telegram_eligible"] = False
        item["enable_for_v1"] = False
        out.append(item)
    return {
        "status": "OK",
        "strategy_id": STRATEGY_ID,
        "candidates": out,
        "count": len(out),
        "disclaimer": DISCLAIMER,
        "read_only": True,
        "v1_unchanged": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/candidates/{symbol}")
async def research_dynamic_candidate_detail(symbol: str) -> dict[str, Any]:
    from app.research.candidate_state_machine import badge_for_state
    from app.research.dynamic_candidate_constants import DISCLAIMER
    from app.research.strategy_candidate_registry import (
        serialize_candidate,
        strategy_candidate_registry,
    )

    row = await strategy_candidate_registry.get_by_symbol(symbol)
    if row is None:
        return {
            "status": "NOT_FOUND",
            "symbol": symbol.upper(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    item = serialize_candidate(row)
    item["badge"] = badge_for_state(
        str(item.get("state") or ""),
        state_reason=str(item.get("state_reason") or ""),
    )
    audit = await strategy_candidate_registry.list_audit(symbol, limit=40)
    return {
        "status": "OK",
        "candidate": item,
        "audit": audit,
        "disclaimer": DISCLAIMER,
        "read_only_except_approve_paper": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/research/candidates/discovery/run")
async def research_dynamic_discovery_run(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Manual discovery job (also intended for daily schedule). No paper/Telegram."""
    from app.research.dynamic_candidate_discovery import run_dynamic_discovery

    payload = body or {}
    settings = get_settings()
    top_n = int(payload.get("top_n") or settings.dynamic_candidate_discovery_top_n)
    return await run_dynamic_discovery(top_n=top_n)


@router.post("/research/candidates/data-health/run")
async def research_dynamic_data_health_run(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    from app.research.candidate_data_health import (
        check_candidate_data_health,
        run_data_health_batch,
    )

    payload = body or {}
    symbol = payload.get("symbol")
    if symbol:
        return await check_candidate_data_health(str(symbol))
    return await run_data_health_batch(limit=int(payload.get("limit") or 50))


@router.post("/research/candidates/advance")
async def research_dynamic_candidates_advance(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Data-health → frozen COMBO_02 backtest → OOS for registry candidates.

    Research only. Never paper-trades, never Telegram, never joins v1.
    """
    from app.research.advance_dynamic_candidates import advance_dynamic_candidates

    payload = body or {}
    symbols = payload.get("symbols")
    if isinstance(symbols, str):
        symbols = [symbols]
    return await advance_dynamic_candidates(
        symbols=list(symbols) if symbols else None,
        run_data_health=not bool(payload.get("backtest_only")),
        run_backtest=not bool(payload.get("health_only")),
        run_oos=not bool(payload.get("skip_oos")) and not bool(payload.get("health_only")),
        limit=int(payload.get("limit") or 50),
    )


@router.post("/research/candidates/{symbol}/approve-paper")
async def research_approve_candidate_paper(
    symbol: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Explicit operator gate: V2_PAPER_CANDIDATE → PAPER_VALIDATING.

    Never enables Telegram. Never joins COMBO_02 v1.
    """
    from app.research.dynamic_candidate_constants import (
        DEFAULT_REQUESTED_RISK_PERCENT,
        DISCLAIMER,
        STRATEGY_ID,
    )
    from app.research.strategy_candidate_registry import (
        serialize_candidate,
        strategy_candidate_registry,
    )

    from app.research.dynamic_candidate_constants import COMBO_VERSION

    confirm = bool(body.get("confirm"))
    note = body.get("approval_note")
    risk = body.get("requested_risk_percent")
    if risk is None:
        risk = DEFAULT_REQUESTED_RISK_PERCENT
    override = bool(body.get("risk_override_above_half_pct")) or bool(
        body.get("risk_override_above_default")
    )
    actor = str(body.get("operator") or body.get("actor") or "operator")

    try:
        row = await strategy_candidate_registry.approve_paper(
            symbol,
            confirm=confirm,
            approval_note=str(note) if note is not None else None,
            requested_risk_percent=float(risk),
            actor=actor,
            risk_override_above_half_pct=override,
            risk_override_above_default=override,
        )
    except KeyError as exc:
        return {
            "status": "NOT_FOUND",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except PermissionError as exc:
        return {
            "status": "REJECTED",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "ERROR",
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    item = serialize_candidate(row)
    assert item.get("telegram_eligible") is False
    assert item.get("production_approved") is False
    assert str(item.get("strategy_id")) == STRATEGY_ID
    assert str(item.get("combo_version")) == COMBO_VERSION
    risk_out = float(item.get("risk_percent") or 0)
    return {
        "status": "OK",
        "state": "PAPER_VALIDATING",
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "production_approved": False,
        "telegram_eligible": False,
        "risk_percent": risk_out,
        "candidate": item,
        "disclaimer": DISCLAIMER,
        "v1_unchanged": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/trade-plan-forensics")
async def research_trade_plan_forensics(
    source: str = Query(
        default="auto",
        description="auto|paper|backtest_job|json — research ingest only",
    ),
    strategy: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    start: str | None = Query(default=None, description="UTC YYYY-MM-DD"),
    end: str | None = Query(default=None, description="UTC YYYY-MM-DD"),
    max_trades: int = Query(default=150, ge=1, le=500),
    write_files: bool = Query(default=True),
) -> dict[str, Any]:
    """Research-only Trade Plan / entry-timing forensics.

    Does NOT modify live signals, Trade Plan, thresholds, SL/TP, or execution.
    """
    from app.research.trade_plan_forensics import run_trade_plan_forensics

    payload = await run_trade_plan_forensics(
        source=source,
        strategy=strategy,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        max_trades=max_trades,
        write_files=write_files,
    )
    # Keep HTTP payload lean: drop full per-trade bodies unless small
    trades = payload.get("trades") or []
    if len(trades) > 40:
        lean = dict(payload)
        lean["trades"] = [
            {
                "trade_id": t.get("trade_id"),
                "outcome_class": t.get("outcome_class"),
                "htf_state": t.get("htf_state"),
                "entry_timing": (t.get("entry_timing") or {}).get("class"),
                "regime": (t.get("regime") or {}).get("primary"),
                "symbol": (t.get("trade") or {}).get("symbol"),
                "timeframe": (t.get("trade") or {}).get("timeframe"),
                "direction": (t.get("trade") or {}).get("direction"),
                "R": (t.get("trade") or {}).get("R"),
            }
            for t in trades
        ]
        lean["trades_truncated_in_response"] = True
        lean["detail_hint"] = "GET /api/research/trade-plan-forensics/{trade_id}"
        return {**lean, "timestamp": datetime.now(timezone.utc).isoformat()}
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/trade-plan-forensics/{trade_id}")
async def research_trade_plan_forensics_detail(trade_id: str) -> dict[str, Any]:
    """Per-trade forensic detail from the last forensics run."""
    from app.research.trade_plan_forensics import get_trade_forensic_detail

    return {
        **get_trade_forensic_detail(trade_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/long-strategy/backtest")
async def research_long_strategy_backtest(
    symbols: str = Query(
        default="BTCUSDT,ETHUSDT,SOLUSDT",
        description="Comma-separated symbols",
    ),
    timeframes: str = Query(
        default="1h",
        description="Comma-separated timeframes",
    ),
    direction: str = Query(default="LONG", description="LONG or SHORT"),
    combination_id: str = Query(default="COMBO_02"),
    limit: int = Query(
        default=1200,
        ge=50,
        le=20000,
        description="OHLCV bars from DB tail (duration depends on timeframe)",
    ),
    risk_usd: float = Query(default=20.0, ge=1.0, le=10_000.0),
    principal_usd: float = Query(default=1000.0, ge=100.0, le=10_000_000.0),
    leverage: float = Query(default=2.0, ge=1.0, le=125.0),
    risk_mode: str | None = Query(default=None),
    research_risk_override: bool = Query(default=False),
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
    """UI Backtest tab — lean HL/LH Trend+BOS matrix (same engine as research scripts).

    Rejects paused SHORT requests. Applies v1 production risk metadata per cell.
    Does not create paper/live trades or send Telegram.
    """
    from app.research.backtest_ui_config import (
        BacktestConfigError,
        cell_risk_lookup,
        enrich_row_with_config,
        job_identity_payload,
        validate_backtest_request,
    )
    from app.research.service import get_bos_research_service

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in timeframes.split(",") if t.strip()]
    if not syms or not tfs:
        return {
            "status": "ERROR",
            "reason": "symbols and timeframes required",
            "rows": [],
            "paper_trade_created": False,
            "live_trade_created": False,
            "telegram_sent": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    try:
        resolved = validate_backtest_request(
            symbols=syms,
            timeframes=tfs,
            direction=direction,
            combination_id=combination_id.upper(),
            risk_mode=risk_mode,
            research_risk_override=research_risk_override,
            risk_usd=risk_usd,
            principal_usd=principal_usd,
            leverage=leverage,
            taker_fee_pct=taker_fee_pct,
            maker_fee_pct=maker_fee_pct,
            start_date=start_date,
            end_date=end_date,
            allow_short=False,
        )
    except BacktestConfigError as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "error_code": exc.code,
            "reason": exc.code,
            "short_research_paused": exc.code == "short_research_paused",
            "rows": [],
            "paper_trade_created": False,
            "live_trade_created": False,
            "telegram_sent": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Sync path: run one matrix call per cell so each keeps its effective v1 risk.
    rows: list[dict[str, Any]] = []
    playbook = None
    combination_name = None
    disclaimer = None
    label = None
    dataset_fp = None
    elapsed = 0.0
    svc = get_bos_research_service()
    for sym in syms:
        for tf in tfs:
            cell = cell_risk_lookup(resolved, sym, tf)
            cell_risk = float(cell.effective_risk_amount) if cell else float(risk_usd)
            payload = await svc.strategy_matrix(
                combination_id=combination_id.upper(),
                symbols=[sym],
                timeframes=[tf],
                direction=resolved.direction,
                limit=limit,
                risk_usd=cell_risk,
                principal_usd=principal_usd,
                start_date=start_date,
                end_date=end_date,
                taker_fee=taker_fee_pct / 100.0,
                maker_fee=maker_fee_pct / 100.0,
                leverage=leverage,
                include_trades=include_trades,
            )
            if playbook is None:
                playbook = payload.get("playbook")
                combination_name = payload.get("combination_name")
                disclaimer = payload.get("disclaimer")
                label = payload.get("label")
                dataset_fp = payload.get("dataset_id") or payload.get("dataset")
            elapsed += float(payload.get("elapsed_seconds") or 0)
            for row in payload.get("rows") or []:
                enriched = enrich_row_with_config(
                    row, resolved, dataset_fingerprint=str(dataset_fp) if dataset_fp else None
                )
                enriched["requested_range"] = {
                    "mode": resolved.period_mode,
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                    "requested_range_available": bool(
                        row.get("period_start") and row.get("period_end")
                    ),
                }
                enriched["risk_usd"] = cell_risk
                rows.append(enriched)

    identity = job_identity_payload(resolved)
    return {
        "status": "OK",
        "label": label,
        "playbook": playbook,
        "combination_id": combination_id.upper(),
        "combination_name": combination_name,
        "direction": resolved.direction,
        "limit": limit,
        "risk_usd": risk_usd,
        "principal_usd": principal_usd,
        "leverage": leverage,
        "symbols": syms,
        "timeframes": tfs,
        "rows": rows,
        "elapsed_seconds": round(elapsed, 3),
        "disclaimer": disclaimer,
        "dataset_fingerprint": str(dataset_fp) if dataset_fp else None,
        "requested_range": {
            "mode": resolved.period_mode,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
        },
        **identity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/research/strategies")
async def research_strategies_catalog() -> dict[str, Any]:
    """Operator catalog of working vs research strategies (read-only)."""
    from app.research.strategy_catalog import build_strategy_catalog

    return {
        **build_strategy_catalog(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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


@router.get("/research/multi-cap-strategies")
async def research_multi_cap_strategies() -> dict[str, Any]:
    """Formal multi-cap research strategy definitions (catalog).

    Research only — live engine unchanged. Does not modify S1/S2/S3/C1-C4.
    """
    from app.research.multi_cap_strategies.service import (
        get_multi_cap_strategies_service,
    )

    return {
        **get_multi_cap_strategies_service().list_strategies(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/research/multi-cap-strategies/run")
async def research_multi_cap_strategies_run(
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Run multi-cap research strategies via existing evaluator infrastructure.

    SIGNAL LOGIC = strategy candidate generation.
    TRADE EVALUATION LOGIC = combination_backtest + fees/slippage/period splits.
    """
    from app.research.multi_cap_strategies.service import (
        get_multi_cap_strategies_service,
    )

    strategy_ids = body.get("strategy_ids") or body.get("strategy_id")
    if isinstance(strategy_ids, str):
        strategy_ids = [strategy_ids]
    symbols = body.get("symbols")
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    timeframes = body.get("timeframes") or body.get("timeframe")
    if isinstance(timeframes, str):
        timeframes = [timeframes]

    payload = await get_multi_cap_strategies_service().run(
        strategy_ids=strategy_ids,
        symbols=symbols,
        timeframes=timeframes,
        start=body.get("start") or body.get("start_date"),
        end=body.get("end") or body.get("end_date"),
        limit=body.get("limit"),
        max_symbols=body.get("max_symbols"),
        taker_fee=body.get("taker_fee") or body.get("fees"),
        maker_fee=body.get("maker_fee"),
        slippage_rate=body.get("slippage") or body.get("slippage_rate"),
        persist=bool(body.get("persist", True)),
        market_caps=body.get("market_caps"),
    )
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/research/multi-cap-strategies/{run_id}")
async def research_multi_cap_strategies_run_get(run_id: str) -> dict[str, Any]:
    from app.research.multi_cap_strategies.service import (
        get_multi_cap_strategies_service,
    )

    payload = await get_multi_cap_strategies_service().get_run(run_id)
    return {**payload, "timestamp": datetime.now(timezone.utc).isoformat()}


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
        from app.services.paper_trade import flush_paper_trade_persists

        eng = get_paper_trade_engine()
        eng.on_setup_signal(symbol, payload)
        await flush_paper_trade_persists(eng)
    except Exception:  # noqa: BLE001
        pass
    return {
        **payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


_WATCHER_LIVE_CACHE: dict[str, Any] = {"at": 0.0, "rows": None}
_WATCHER_LIVE_TTL_SEC = 20.0


def _enrich_paper_status(status: dict[str, Any]) -> dict[str, Any]:
    """Attach v1 watcher + Telegram monitor metadata (no secrets)."""
    import time

    from app.config import get_settings
    from app.services.telegram_alerts import telegram_delivery_status
    from app.services.v1_paper_watcher import get_v1_paper_watcher

    settings = get_settings()
    out = dict(status)
    watcher_enabled = bool(getattr(settings, "paper_v1_watcher_enabled", True))
    legacy_on = bool(out.get("legacy_auto_entry_enabled"))
    owns = bool(out.get("v1_watcher_owns_entries"))
    if watcher_enabled and legacy_on and not owns:
        auto_source = "V1_PAPER_WATCHER+LEGACY_15M"
        explanation = (
            "Parallel paper streams: COMBO_02 v1 1h watcher (BTC/ETH/SOL) and "
            "15m RESEARCH_15M Trade chances. Separate sources/labels — one open "
            "position per symbol; follow paper_opened / paper_skip_* logs."
        )
    elif watcher_enabled and owns:
        auto_source = "V1_PAPER_WATCHER"
        explanation = (
            "Auto paper opens only from the COMBO_02 v1 1h watcher "
            "(BTC/ETH/SOL closed bars). Trade chances stay research-only while "
            "PAPER_V1_WATCHER_OWNS_ENTRIES=true."
        )
    elif legacy_on:
        auto_source = "LEGACY_15M_SETUP"
        explanation = "Legacy 15m Path A/B setup→paper auto-entry is active."
    elif watcher_enabled:
        auto_source = "V1_PAPER_WATCHER"
        explanation = (
            "COMBO_02 v1 1h watcher active; legacy 15m auto-entry is OFF."
        )
    else:
        auto_source = "NONE"
        explanation = "Paper auto-entry is paused — no watcher and legacy auto-entry OFF."
    out["monitor"] = {
        "auto_source": auto_source,
        "v1_watcher_enabled": watcher_enabled,
        "legacy_auto_entry_enabled": legacy_on,
        "v1_watcher_owns_entries": owns,
        "explanation": explanation,
    }
    try:
        if watcher_enabled:
            watcher = get_v1_paper_watcher()
            wstatus = watcher.status(include_live=False)
            now = time.monotonic()
            cached = _WATCHER_LIVE_CACHE
            if (
                cached["rows"] is not None
                and (now - float(cached["at"])) < _WATCHER_LIVE_TTL_SEC
            ):
                wstatus["live"] = cached["rows"]
            else:
                try:
                    # Cheap tip rows + refresh presentation snapshots (BTC/ETH/SOL only).
                    try:
                        watcher.refresh_presentation_snapshots(force=False)
                    except Exception:  # noqa: BLE001
                        pass
                    live = watcher.peek_books()
                    _WATCHER_LIVE_CACHE["at"] = now
                    _WATCHER_LIVE_CACHE["rows"] = live
                    wstatus["live"] = live
                except Exception as exc:  # noqa: BLE001
                    wstatus["live"] = []
                    wstatus["live_error"] = str(exc)
            out["v1_watcher"] = wstatus
            # Clarify risk: engine.risk_percent is legacy RESEARCH_15M default;
            # v1 opens size from book.risk_percent (BTC/ETH/SOL 2%).
            out["risk_percent_legacy_research_15m"] = out.get("risk_percent")
            out["v1_book_risk"] = {
                str(b.get("symbol")): {
                    "tier": b.get("tier"),
                    "risk_percent": b.get("risk_percent"),
                    "risk_pct_display": (
                        f"{float(b.get('risk_percent') or 0) * 100:g}%"
                        if b.get("risk_percent") is not None
                        else None
                    ),
                }
                for b in (wstatus.get("books") or [])
                if isinstance(b, dict) and b.get("symbol")
            }
        else:
            out["v1_watcher"] = {"enabled": False}
            out["v1_book_risk"] = {}
    except Exception as exc:  # noqa: BLE001
        out["v1_watcher"] = {"enabled": watcher_enabled, "error": str(exc)}
    try:
        out["telegram"] = telegram_delivery_status(settings)
    except Exception as exc:  # noqa: BLE001
        out["telegram"] = {"ready": False, "reason": str(exc)}
    return out


@router.get("/paper/status")
async def paper_status() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    return _enrich_paper_status(get_paper_trade_engine().status())


@router.get("/paper/positions")
async def paper_positions(closed_limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    data = get_paper_trade_engine().positions(closed_limit=closed_limit)
    data["status"] = _enrich_paper_status(data.get("status") or {})
    return data


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

    open_syms = eng.open_symbols("LEGACY")
    rows = list_trade_opportunities(limit=limit, open_symbols=open_syms, include_waiting=True)
    by_tier: dict[str, int] = {}
    for r in rows:
        t = str(r.get("tier") or "?")
        by_tier[t] = by_tier.get(t, 0) + 1
    watcher_owns = bool(getattr(eng, "v1_watcher_owns_entries", False))
    legacy_on = bool(getattr(eng, "legacy_auto_entry_enabled", False))
    if watcher_owns and not legacy_on:
        note = (
            "15m screener research only — auto paper opens come from the "
            "COMBO_02 v1 1h watcher (BTC/ETH/SOL), not these READY rows."
        )
    elif eng.entry_mode == "path_a":
        note = f"Auto mode={eng.entry_mode}. READY = Path A Trend+BOS opens when Auto ON"
    else:
        note = "READY = LONG_ENTRY_CANDIDATE. WAITING = OHLCV/setup not ready."
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
        "note": note,
        "entry_mode": eng.entry_mode,
        "v1_watcher_owns_entries": watcher_owns,
        "legacy_auto_entry_enabled": legacy_on,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/paper/enable")
async def paper_enable() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    eng = get_paper_trade_engine()
    eng.enable()
    return _enrich_paper_status(eng.status())


@router.post("/paper/disable")
async def paper_disable() -> dict[str, Any]:
    from app.services.paper_trade import get_paper_trade_engine

    eng = get_paper_trade_engine()
    eng.disable()
    return _enrich_paper_status(eng.status())


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
    return _enrich_paper_status(eng.status())


@router.post("/paper/close-legacy")
async def paper_close_legacy(
    confirm: bool = Query(
        default=False,
        description="Must be true — closes/archives open RESEARCH_15M / experimental paper positions only.",
    ),
) -> dict[str, Any]:
    """Operator action: close/archive open legacy paper positions (not v1 watcher).

    Requires explicit confirm=true. Reason recorded as ``legacy_cleanup``.
    Does not auto-run and never reclassifies legacy rows as v1.
    """
    from app.services.paper_trade import (
        flush_paper_trade_persists,
        get_paper_trade_engine,
    )

    eng = get_paper_trade_engine()
    result = eng.close_legacy_paper_positions(confirm=confirm, reason="legacy_cleanup")
    if result.get("ok"):
        try:
            await flush_paper_trade_persists(eng)
        except Exception:  # noqa: BLE001
            pass
    return {**result, "status": _enrich_paper_status(eng.status())}


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
    source_used = "memory"
    fallback_used = False
    chart_error: str | None = None
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

    # Cold memory after restart: hydrate this series from Postgres before REST.
    # Keeps chart usable while universe backfill is still walking hundreds of pairs.
    if len(candles) < max(20, min(limit, 80)):
        try:
            from app.models.ohlcv import Candle as DbCandle
            from app.models.schemas import DataStatus as DbStatus
            from app.research.postgres_ohlcv import load_ohlcv_series_tail

            raw = await load_ohlcv_series_tail(sym, timeframe, limit=limit)
            if raw:
                batch: list[Any] = []
                for c in raw:
                    ot = c["time"]
                    if getattr(ot, "tzinfo", None) is None:
                        ot = ot.replace(tzinfo=timezone.utc)
                    batch.append(
                        DbCandle(
                            symbol=sym,
                            timeframe=normalize_timeframe(timeframe),
                            open_time=ot,
                            close_time=ot,
                            open=float(c["open"]),
                            high=float(c["high"]),
                            low=float(c["low"]),
                            close=float(c["close"]),
                            volume=float(c.get("volume") or 0),
                            is_closed=True,
                            timestamp=ot,
                            source="db_chart_hydrate",
                            status=DbStatus.CACHED,
                        )
                    )
                if batch:
                    try:
                        await ohlcv_store.ingest_history(batch)
                    except Exception:  # noqa: BLE001
                        pass
                    # Prefer store view; fall back to direct batch if ingest no-ops.
                    stored = ohlcv_store.get_closed(sym, timeframe, limit=limit)
                    candles = stored if len(stored) >= len(batch) else batch
                    open_c = ohlcv_store.get_open(sym, timeframe)
                    source_used = "postgres"
                    fallback_used = True
        except Exception as exc:  # noqa: BLE001
            chart_error = f"postgres_fallback:{exc.__class__.__name__}"

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
                source_used = "rest"
                fallback_used = True
            except Exception as exc:  # noqa: BLE001
                chart_error = f"rest_fallback:{exc.__class__.__name__}:{exc}"

    rows = [c.model_dump(mode="json") if hasattr(c, "model_dump") else dict(c) for c in candles]

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

    # Deduplicate by open_time (keep last) and sort ascending — display hygiene only.
    by_ot: dict[str, dict[str, Any]] = {}
    for r in rows:
        ot = r.get("open_time")
        key = ot if isinstance(ot, str) else (ot.isoformat() if ot is not None else "")
        by_ot[key] = r
    deduped = list(by_ot.values())
    deduplicated = len(deduped) != len(rows)

    def _ot_key(r: dict[str, Any]) -> str:
        ot = r.get("open_time")
        if isinstance(ot, str):
            return ot
        if ot is None:
            return ""
        return ot.isoformat()

    sorted_rows = sorted(deduped, key=_ot_key)
    rows = sorted_rows

    first_ts = rows[0].get("open_time") if rows else None
    last_ts = rows[-1].get("open_time") if rows else None
    closed_count = sum(1 for r in rows if r.get("is_closed", True))
    open_included = any(not r.get("is_closed", True) for r in rows)
    # Contract: ``limit`` applies to closed bars. The forming/open candle may be
    # appended (+1) so charts can paint the live tip. Do not treat returned_count
    # == limit+1 as an off-by-one bug when open_included is true.
    status = "LIVE" if rows else "WAITING"
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "candles": rows,
        "count": len(rows),
        "returned_count": len(rows),
        "requested_limit": limit,
        "closed_count": closed_count,
        "open_included": open_included,
        "limit_applies_to": "closed_candles",
        "contract_note": (
            "requested_limit counts closed candles; an open/forming tip may add +1"
        ),
        "status": status,
        "source": source_used,
        "fallback_used": fallback_used,
        "deduplicated": deduplicated,
        "sorted": True,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
        "error": chart_error,
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
        snapshot = {
            "type": "screener_snapshot",
            "total": total,
            "rows": payload_rows,
            "ingestion": market_store.ingestion_status,
            "source": "memory",
            "total_universe": meta.get("total_universe"),
            "discovered_universe": meta.get("discovered_universe"),
            "active_universe": meta.get("active_universe"),
            "active_universe_cap": meta.get("active_universe_cap"),
            "eligible_count": meta.get("eligible_count"),
            "returned_count": meta.get("returned_count"),
            "limit": meta.get("limit"),
            "selection_updated_at": meta.get("selection_updated_at"),
            "excluded": meta.get("excluded") or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            from app.services.screener_presentation import enrich_screener_payload

            enrich_screener_payload(snapshot)
            payload_rows = snapshot["rows"]
        except Exception:  # noqa: BLE001
            snapshot["is_telegram_eligible"] = False
        for r in payload_rows:
            prev[r["symbol"]] = r
        # Preferential OI / backfill for visible screener symbols
        orch = get_orchestrator()
        if orch is not None:
            visible = [r["symbol"] for r in payload_rows]
            orch.oi.set_visible_symbols(visible)
            if getattr(orch, "backfill", None) is not None:
                orch.backfill.set_visible_symbols(visible)
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
                membership_snap: dict[str, Any] = {
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
                try:
                    from app.services.screener_presentation import enrich_screener_payload

                    enrich_screener_payload(membership_snap)
                except Exception:  # noqa: BLE001
                    membership_snap["is_telegram_eligible"] = False
                prev = {
                    r["symbol"]: r
                    for r in (membership_snap.get("rows") or [])
                    if isinstance(r, dict) and r.get("symbol")
                }
                await websocket.send_json(membership_snap)
                continue
            patches = 0
            for row in rows:
                changes = svc.row_patch_changes(prev.get(row.symbol), row)
                if not changes:
                    continue
                # Always refresh cache — preserve presentation labels from prior row
                full = row.model_dump(mode="json")
                prior = prev.get(row.symbol) or {}
                for key in (
                    "screen_timeframe",
                    "local_trend",
                    "screen_signal",
                    "screen_setup",
                    "potential_levels",
                    "v1_status",
                    "v1_paper_trade",
                    "is_telegram_eligible",
                ):
                    if key in prior:
                        full[key] = prior[key]
                full["is_telegram_eligible"] = False
                # Recompute potential levels from updated setup fields (presentation only)
                try:
                    from app.services.screener_presentation import build_potential_levels

                    full["potential_levels"] = build_potential_levels(full)
                    full["local_trend"] = (
                        str(
                            (full.get("setup_trend") or {}).get("value")
                            or (full.get("market_structure") or {}).get("value")
                            or (full.get("structure") or {}).get("value")
                            or ""
                        ).upper()
                        or full.get("local_trend")
                    )
                    full["screen_signal"] = (
                        str((full.get("market_signal") or {}).get("value") or "").upper()
                        or full.get("screen_signal")
                    )
                    full["screen_setup"] = (
                        str((full.get("setup_signal") or {}).get("value") or "").upper()
                        or full.get("screen_setup")
                    )
                except Exception:  # noqa: BLE001
                    pass
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
                # Include refreshed presentation keys so UI keeps labels current
                for key in (
                    "potential_levels",
                    "local_trend",
                    "screen_signal",
                    "screen_setup",
                    "is_telegram_eligible",
                ):
                    if key in full:
                        slim[key] = full[key]
                if "v1_status" in full:
                    slim["v1_status"] = full["v1_status"]
                if "v1_paper_trade" in full:
                    slim["v1_paper_trade"] = full["v1_paper_trade"]
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
