from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.core.request_audit import request_audit
from app.services.market_store import market_store
from app.services.redis_manager import redis_manager

try:
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None


class PerformanceMonitor:
    """Process + pipeline measurements — no fabricated metrics."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._ws_msg_times: deque[float] = deque(maxlen=5000)
        self._redis_ops: deque[float] = deque(maxlen=5000)
        self._screener_latencies_ms: deque[float] = deque(maxlen=200)
        self._signal_calc_ms: deque[float] = deque(maxlen=500)
        self._proc = psutil.Process(os.getpid()) if psutil else None

    def record_ws_message(self) -> None:
        self._ws_msg_times.append(time.monotonic())

    def record_redis_op(self) -> None:
        self._redis_ops.append(time.monotonic())

    def record_screener_latency(self, ms: float) -> None:
        self._screener_latencies_ms.append(ms)

    def record_signal_calc(self, ms: float) -> None:
        self._signal_calc_ms.append(ms)

    def _rate(self, times: deque[float], window: float = 1.0) -> float:
        if not times:
            return 0.0
        now = time.monotonic()
        cutoff = now - window
        n = sum(1 for t in times if t >= cutoff)
        return round(n / window, 2)

    async def snapshot(self) -> dict[str, Any]:
        # Lazy imports avoid circular dependency with orchestrator
        from app.engines.orchestrator import get_orchestrator
        from app.ingestion.service import get_ingestion

        audit = await request_audit.stats_last_minute()
        ingestion = get_ingestion()
        orch = get_orchestrator()

        cpu = None
        rss_mb = None
        if self._proc is not None:
            try:
                cpu = self._proc.cpu_percent(interval=0.0)
                rss_mb = round(self._proc.memory_info().rss / (1024 * 1024), 2)
            except Exception:  # noqa: BLE001
                pass

        ws_connections = 0
        ws_messages = 0
        kline_streams = 0
        if ingestion is not None:
            for c in ingestion.ws.status():
                if c.get("connected"):
                    ws_connections += 1
                ws_messages += int(c.get("message_count") or 0)
        if orch is not None:
            kstat = orch.kline_ws.status()
            kline_streams = int(kstat.get("active_streams") or 0)
            ws_connections += int(kstat.get("connected_count") or 0)
            for c in kstat.get("connections") or []:
                ws_messages += int(c.get("message_count") or 0)
            for c in orch._liq_ws.status():
                if c.get("connected"):
                    ws_connections += 1
                ws_messages += int(c.get("message_count") or 0)

        avg_screener = (
            round(sum(self._screener_latencies_ms) / len(self._screener_latencies_ms), 2)
            if self._screener_latencies_ms
            else None
        )
        avg_signal = (
            round(sum(self._signal_calc_ms) / len(self._signal_calc_ms), 3)
            if self._signal_calc_ms
            else None
        )
        redis_h = await redis_manager.health()
        ohlcv_stats = {}
        backfill = {}
        db_writes = 0
        setup_metrics: dict[str, Any] = {}
        if orch is not None:
            ohlcv_stats = orch.ohlcv.stats()
            backfill = orch.backfill.status()
            db_writes = int(ohlcv_stats.get("db_writes") or 0)
            try:
                from app.services.setup_signals import get_setup_signal_service

                setup_metrics = get_setup_signal_service().metrics()
            except Exception:  # noqa: BLE001
                setup_metrics = {}
        uptime = max(time.monotonic() - self._started, 1.0)

        return {
            "process": {
                "pid": os.getpid(),
                "uptime_seconds": round(uptime, 1),
                "cpu_percent": cpu,
                "rss_mb": rss_mb,
                "psutil": psutil is not None,
            },
            "pipeline": {
                "symbols": len(market_store.symbols),
                "ticker_live": market_store.live_ticker_count(),
                "websocket_connections": ws_connections,
                "kline_streams": kline_streams,
                "ws_messages_total": ws_messages,
                "ws_messages_per_sec": self._rate(self._ws_msg_times, 1.0),
                "rest_requests_last_minute": int(audit.get("count") or 0),
                "rest_weight_last_minute": int(audit.get("total_weight") or 0),
                "rate_limit_errors_last_minute": int(audit.get("count_429") or 0),
                "redis_ops_per_sec": self._rate(self._redis_ops, 1.0),
                "redis_status": redis_h.get("status"),
                "screener_update_latency_ms_avg": avg_screener,
                "signal_calculation_ms_avg": avg_signal or setup_metrics.get("signal_calculation_ms_avg"),
                "symbols_recomputed": setup_metrics.get("symbols_recomputed"),
                "timeframes_recomputed": setup_metrics.get("timeframes_recomputed"),
                "setup_queue_depth": setup_metrics.get("queue_depth"),
                "signals_updated": setup_metrics.get("signals_updated"),
                "setup_cache_size": setup_metrics.get("cache_size"),
                "db_writes_total": db_writes,
                "db_writes_per_sec": round(db_writes / uptime, 3),
                "backfill_queue_size": backfill.get("queue_size"),
                "backfill_completed_series": backfill.get("completed_series"),
                "backfill_candles_written": backfill.get("candles_written"),
                "backfill_requests": backfill.get("requests"),
                "backfill_throughput_candles_per_sec": round(
                    float(backfill.get("candles_written") or 0) / uptime, 3
                ),
            },
            "browser": {
                "fps": None,
                "note": "Browser FPS is measured client-side; not available in this process.",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


performance_monitor = PerformanceMonitor()
