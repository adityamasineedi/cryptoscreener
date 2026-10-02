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
            "connection_status": "STOPPED",
            "events_seen": 0,
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
    from app.ingestion.providers.sentiment import SentimentProvider
    from app.services.persistence import persistence
    from app.services.redis_state import redis_state

    onchain = OnChainProvider(settings)
    sentiment = SentimentProvider(settings)
    if not onchain.configured:
        await provider_health.mark_disabled("onchain")
    if not sentiment.configured:
        await provider_health.mark_disabled("sentiment")

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
    for p in providers:
        pname = p.get("provider") or p.get("name")
        cd = cooldown_map.get(pname or "")
        if cd and cd.get("cooldown"):
            p["cooldown_until"] = f"in_{round(cd.get('remaining') or 0, 1)}s"
        if pname in ("onchain", "sentiment"):
            p["enabled"] = False
            p["healthy"] = False

    for name, status in (
        ("onchain", onchain.status()),
        ("sentiment", sentiment.status()),
    ):
        if name not in names:
            providers.append(
                {
                    "provider": name,
                    "enabled": bool(status.get("configured")),
                    "healthy": False,
                    "status": "DISABLED" if not status.get("configured") else "HEALTHY",
                    "requests": 0,
                    "successful": 0,
                    "failed": 0,
                    "429_count": 0,
                    "cache_hits": 0,
                    "cache_misses": 0,
                    "cache_hit_rate": (status.get("cache_stats") or {}).get(
                        "cache_hit_rate", 0.0
                    ),
                    "last_success": None,
                    "last_error": status.get("last_error"),
                    "cooldown_until": None,
                    "note": status.get("note"),
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
        "persistence": persistence.stats(),
        "redis": await redis_state.health_detail(),
        "database": await db_manager.health(),
        "retention_policies": await db_manager.retention_policies(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    t0 = asyncio.get_event_loop().time()
    rows, total = svc.futures_screener(
        search=search,
        min_change=min_change,
        max_change=max_change,
        min_volume=min_volume,
        min_funding=min_funding,
        max_funding=max_funding,
        preset=preset,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
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
        sync_budget = 40
        for i, row in enumerate(rows):
            cached = es.get_setup_signal(row.symbol)
            needs = setup_svc.setup_ohlcv_ready(row.symbol) and (
                not cached or setup_svc.is_stale(cached, row.symbol)
            )
            if not needs:
                continue
            if sync_budget > 0:
                setup_svc.ensure_computed(row.symbol)
                info = market_store.symbols.get(row.symbol)
                if info is not None:
                    rows[i] = svc.build_row(info, rank=row.rank)
                sync_budget -= 1
            else:
                setup_svc.enqueue_ensure([row.symbol])
    payload_rows = [r.model_dump(mode="json") for r in rows]
    # Mirror latest page into Redis for reconnect (no-op if Redis disabled)
    try:
        from app.services.redis_state import redis_state

        if offset == 0:
            await redis_state.store_screener_state(
                {
                    "total": total,
                    "rows": payload_rows[:100],
                    "symbols": [r["symbol"] for r in payload_rows[:100]],
                    "ingestion": market_store.ingestion_status,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            performance_monitor.record_redis_op()
    except Exception:  # noqa: BLE001
        pass
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "preset": preset,
        "rows": payload_rows,
        "use_real_data": settings.use_real_data,
        "ingestion": market_store.ingestion_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/screener/filter")
async def screener_filter(
    body: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    filters = body.get("filters") or []
    sort_by = body.get("sort_by") or "buy_opportunity"
    limit = int(body.get("limit") or 200)
    offset = int(body.get("offset") or 0)
    search = body.get("search")
    preset = body.get("preset")
    rows, total = svc.futures_screener(
        search=search,
        filters=filters,
        preset=preset,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "rows": [r.model_dump(mode="json") for r in rows],
        "filters": filters,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/screener/fundamentals")
async def screener_fundamentals(
    preset: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    settings = get_settings()
    svc = ScreenerService(settings, market_store)
    rows, total = svc.futures_screener(
        search=search,
        preset=preset,
        sort_by="market_cap",
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "preset": preset,
        "rows": [r.model_dump(mode="json") for r in rows],
        "timestamp": datetime.now(timezone.utc).isoformat(),
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


@router.get("/signals/{symbol}")
async def signal_for_symbol(
    symbol: str,
    account_equity: float | None = None,
    risk_percent: float | None = None,
    leverage: float | None = None,
) -> dict[str, Any]:
    from app.services.setup_signals import get_setup_signal_service

    svc = get_setup_signal_service()
    payload = svc.analyze_symbol(
        symbol,
        account_equity=account_equity,
        risk_percent=risk_percent,
        leverage=leverage,
    )
    return {
        **payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


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
    sym = symbol.upper()
    candles = ohlcv_store.get_closed(sym, timeframe, limit=limit)
    open_c = ohlcv_store.get_open(sym, timeframe)
    rows = [c.model_dump(mode="json") for c in candles]
    if open_c is not None:
        rows.append(open_c.model_dump(mode="json"))
    status = "LIVE" if rows else "WAITING"
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "candles": rows,
        "count": len(rows),
        "status": status,
        "source": "ohlcv_store",
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
    return {
        "symbol": sym,
        "window": window,
        "aggregates": aggs,
        "events": events,
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

        # Fresh in-memory snapshot (authoritative)
        t0 = asyncio.get_event_loop().time()
        rows, total = svc.futures_screener(limit=300, offset=0)
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await websocket.send_json(snapshot)
        try:
            await redis_state.store_screener_state(
                {
                    "total": total,
                    "rows": payload_rows[:100],
                    "symbols": [r["symbol"] for r in payload_rows[:100]],
                    "ingestion": market_store.ingestion_status,
                    "timestamp": snapshot["timestamp"],
                }
            )
            performance_monitor.record_redis_op()
        except Exception:  # noqa: BLE001
            pass
        while True:
            await asyncio.sleep(1.0)
            t0 = asyncio.get_event_loop().time()
            rows, total = svc.futures_screener(limit=300, offset=0)
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
