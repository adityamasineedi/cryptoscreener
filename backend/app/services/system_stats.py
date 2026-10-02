from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.provider_health import provider_health
from app.core.rate_limiter import rate_limiters
from app.core.request_audit import request_audit
from app.engines.orchestrator import get_orchestrator
from app.ingestion.service import get_ingestion
from app.services.database import db_manager
from app.services.market_store import market_store
from app.services.ohlcv_store import ohlcv_store
from app.services.redis_manager import redis_manager


async def build_system_stats() -> dict[str, Any]:
    ingestion = get_ingestion()
    orch = get_orchestrator()
    audit = await request_audit.stats_last_minute()
    redis_h = await redis_manager.health()
    db_h = await db_manager.health()

    ticker_ws = 0
    kline_conns = 0
    active_streams = 0
    kline_live = ohlcv_store.live_kline_symbols()
    if ingestion is not None:
        ticker_ws = sum(1 for c in ingestion.ws.status() if c.get("connected"))
    if orch is not None:
        kstat = orch.kline_ws.status()
        kline_conns = int(kstat.get("websocket_connections") or 0)
        active_streams = int(kstat.get("active_streams") or 0)
        # include liquidation WS
        ticker_ws += sum(1 for c in orch._liq_ws.status() if c.get("connected"))

    oi_rows = 0
    liq_rows = 0
    if orch is not None:
        oi_rows = sum(
            1
            for s in getattr(orch.oi, "_states", {}).values()
            if s.open_interest.value is not None
        )
        liq_rows = int(orch.liquidations.status().get("total_events") or 0)

    return {
        "symbols": len(market_store.symbols),
        "ticker_live": market_store.live_ticker_count(),
        "kline_live": kline_live,
        "websocket_connections": ticker_ws + kline_conns,
        "ticker_websocket_connections": ticker_ws,
        "kline_websocket_connections": kline_conns,
        "active_streams": active_streams,
        "rest_requests_last_minute": int(audit.get("count") or 0),
        "rate_limit_errors": int(audit.get("count_429") or 0),
        "database": db_h.get("status", "disabled"),
        "redis": redis_h.get("status", "disabled"),
        "ohlcv_closed_ingested": ohlcv_store.closed_candle_count(),
        "oi_rows": oi_rows,
        "liquidation_rows": liq_rows,
        "rate_limiters": rate_limiters.all_snapshots(),
        "provider_health": await provider_health.snapshot_all(),
        "ingestion": market_store.ingestion_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
