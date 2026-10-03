"""Centralized health evaluation. Frontend only renders what this returns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.diagnostics.constants import HealthStatus, worst_status
from app.diagnostics.models import ComponentHealth, health_component
from app.diagnostics.resources import resource_sampler


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def _stale_seconds(last_success: str | datetime | None) -> float | None:
    if last_success is None:
        return None
    if isinstance(last_success, str):
        dt = _parse_iso(last_success)
    else:
        dt = last_success
    if dt is None:
        return None
    return max(0.0, (_utcnow() - dt).total_seconds())


def _provider_map(providers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in providers:
        name = str(p.get("provider") or p.get("name") or "").lower()
        if name:
            out[name] = p
    return out


def _map_provider_status(raw: str | None, *, disabled: bool = False) -> str:
    if disabled:
        return HealthStatus.DISABLED.value
    if not raw:
        return HealthStatus.UNKNOWN.value
    u = raw.upper()
    if u in {s.value for s in HealthStatus}:
        return u
    if u in ("OK", "CONNECTED", "LIVE", "HEALTHY"):
        return HealthStatus.HEALTHY.value
    if "RATE" in u or u == "DEGRADED":
        return HealthStatus.DEGRADED.value
    if u in ("DISABLED", "OFF"):
        return HealthStatus.DISABLED.value
    if u in ("WAITING",):
        return HealthStatus.WAITING.value
    if u in ("STALE",):
        return HealthStatus.STALE.value
    if u in ("UNAVAILABLE", "ERROR", "FAILED"):
        return HealthStatus.UNAVAILABLE.value if u == "UNAVAILABLE" else HealthStatus.ERROR.value
    return HealthStatus.UNKNOWN.value


async def evaluate_overview(settings: Any) -> dict[str, Any]:
    """Assemble overview cards from real subsystem measurements only."""
    from app.core.provider_health import provider_health
    from app.core.request_audit import request_audit
    from app.engines.orchestrator import get_orchestrator
    from app.ingestion.service import get_ingestion
    from app.services.database import db_manager
    from app.services.market_store import market_store
    from app.services.ohlcv_store import ohlcv_store
    from app.services.performance import performance_monitor
    from app.services.redis_manager import redis_manager
    from app.diagnostics.store import diagnostic_store

    now = _utcnow()
    checked = now.isoformat()
    orch = get_orchestrator()
    ingestion = get_ingestion()
    audit = await request_audit.stats_last_minute()
    redis_h = await redis_manager.health()
    db_h = await db_manager.health()
    perf = await performance_monitor.snapshot()
    providers = await provider_health.snapshot_all()
    pmap = _provider_map(providers)
    resources = resource_sampler.sample(settings)
    issue_counts = await diagnostic_store.open_issue_count()

    cards: list[ComponentHealth] = []

    # Backend process
    proc = resources.get("process") or {}
    cards.append(
        health_component(
            "backend",
            "Backend",
            HealthStatus.HEALTHY if proc.get("pid") else HealthStatus.UNKNOWN,
            reason="API process responding" if proc.get("pid") else "PID unknown",
            last_checked=checked,
            last_success=checked,
            metrics={
                "pid": proc.get("pid"),
                "rss_bytes": proc.get("rss_bytes"),
                "cpu_percent": proc.get("cpu_percent"),
                "uptime_seconds": (perf.get("process") or {}).get("uptime_seconds"),
            },
        )
    )

    # Frontend — backend cannot prove frontend health; mark UNKNOWN unless a probe exists
    cards.append(
        health_component(
            "frontend",
            "Frontend",
            HealthStatus.UNKNOWN,
            reason="Frontend health is client-reported; no server-side probe in Phase 1",
            last_checked=checked,
            metrics={"note": "UI presence is not verified by backend"},
        )
    )

    # PostgreSQL
    db_status_raw = str(db_h.get("status") or "unknown")
    if not db_h.get("enabled"):
        db_status = HealthStatus.DISABLED.value
        db_reason = "DATABASE_ENABLED=false"
    elif db_status_raw == "ok":
        db_status = HealthStatus.HEALTHY.value
        db_reason = "SELECT 1 succeeded"
    elif db_status_raw.startswith("unavailable") or db_status_raw.startswith("error"):
        db_status = HealthStatus.UNAVAILABLE.value
        db_reason = db_status_raw
    else:
        db_status = HealthStatus.UNKNOWN.value
        db_reason = db_status_raw
    cards.append(
        health_component(
            "postgresql",
            "PostgreSQL",
            db_status,
            reason=db_reason,
            last_checked=checked,
            last_success=checked if db_status == HealthStatus.HEALTHY.value else None,
            metrics={
                "enabled": db_h.get("enabled"),
                "timescale": db_h.get("timescale"),
                "schema_ready": db_h.get("schema_ready"),
            },
        )
    )

    # Redis
    redis_raw = str(redis_h.get("status") or "unknown")
    if redis_raw in ("disabled",):
        redis_status = HealthStatus.DISABLED.value
        redis_reason = "REDIS_ENABLED=false or not connected"
    elif redis_raw == "ok":
        redis_status = HealthStatus.HEALTHY.value
        redis_reason = "PING ok"
    else:
        redis_status = HealthStatus.UNAVAILABLE.value
        redis_reason = redis_raw
    cards.append(
        health_component(
            "redis",
            "Redis",
            redis_status,
            reason=redis_reason,
            last_checked=checked,
            last_success=checked if redis_status == HealthStatus.HEALTHY.value else None,
            metrics={"status_raw": redis_raw},
        )
    )

    # Binance REST
    br = pmap.get("binance_rest") or pmap.get("binance") or {}
    br_status = _map_provider_status(br.get("status"))
    if br and int(br.get("429_count") or 0) > 0:
        br_status = worst_status(br_status, HealthStatus.DEGRADED.value)
    cards.append(
        health_component(
            "binance_rest",
            "Binance REST",
            br_status if br else HealthStatus.UNKNOWN,
            reason=br.get("last_error")
            or ("measured via provider_health" if br else "no provider traffic recorded yet"),
            last_checked=checked,
            last_success=br.get("last_success"),
            latency_ms=br.get("last_latency_ms") or br.get("avg_latency_ms"),
            error_count=int(br.get("failed") or 0) + int(br.get("429_count") or 0),
            metrics={
                "requests": br.get("requests"),
                "429_count": br.get("429_count"),
                "success": br.get("successful"),
                "rest_last_minute": audit.get("count"),
                "429_last_minute": audit.get("count_429"),
            },
        )
    )

    # Binance WebSocket — CONNECTED + no data => STALE
    ws_card = await _evaluate_binance_ws(ingestion, orch, checked)
    cards.append(ws_card)

    # CoinGecko / DefiLlama
    for key, label in (("coingecko", "CoinGecko"), ("defillama", "DefiLlama")):
        p = pmap.get(key) or {}
        st = _map_provider_status(p.get("status"), disabled=p.get("status") == "DISABLED")
        cards.append(
            health_component(
                key,
                label,
                st if p else HealthStatus.UNKNOWN,
                reason=p.get("last_error")
                or (p.get("status") if p else "no provider traffic recorded yet"),
                last_checked=checked,
                last_success=p.get("last_success"),
                latency_ms=p.get("last_latency_ms") or p.get("avg_latency_ms"),
                error_count=int(p.get("failed") or 0) + int(p.get("429_count") or 0),
                metrics={
                    "requests": p.get("requests"),
                    "429_count": p.get("429_count"),
                },
            )
        )

    # Research pipeline — Phase 1: report UNKNOWN unless a run is known
    research_status = HealthStatus.UNKNOWN.value
    research_reason = "No active research pipeline probe in Phase 1 overview"
    research_metrics: dict[str, Any] = {}
    try:
        from app.research.backtest_job import backtest_job_service

        bj = backtest_job_service.status()
        if isinstance(bj, dict) and bj.get("status"):
            research_metrics["backtest_job"] = {
                "status": bj.get("status"),
                "job_id": bj.get("job_id") or bj.get("id"),
            }
            st = str(bj.get("status")).upper()
            if st in ("RUNNING", "QUEUED"):
                research_status = HealthStatus.HEALTHY.value
                research_reason = f"Backtest job {st}"
            elif st in ("ERROR", "FAILED"):
                research_status = HealthStatus.ERROR.value
                research_reason = bj.get("error") or "Backtest job failed"
    except Exception:  # noqa: BLE001
        pass
    cards.append(
        health_component(
            "research_pipeline",
            "Research Pipeline",
            research_status,
            reason=research_reason,
            last_checked=checked,
            metrics=research_metrics,
        )
    )

    # OHLCV
    ohlcv_live = 0
    try:
        ohlcv_live = int(ohlcv_store.live_kline_symbols() or 0)
    except Exception:  # noqa: BLE001
        ohlcv_live = 0
    symbols = len(market_store.symbols)
    if symbols == 0:
        ohlcv_status = HealthStatus.WAITING.value
        ohlcv_reason = "Universe not loaded yet"
    elif ohlcv_live == 0:
        ohlcv_status = HealthStatus.WAITING.value
        ohlcv_reason = "No live kline symbols yet — not marked ERROR"
    else:
        ohlcv_status = HealthStatus.HEALTHY.value
        ohlcv_reason = f"{ohlcv_live} symbols with live klines"
    cards.append(
        health_component(
            "ohlcv",
            "OHLCV",
            ohlcv_status,
            reason=ohlcv_reason,
            last_checked=checked,
            metrics={
                "live_kline_symbols": ohlcv_live,
                "universe": symbols,
                "closed_candles": ohlcv_store.closed_candle_count(),
            },
        )
    )

    # OI
    oi_status = HealthStatus.UNKNOWN.value
    oi_reason = "OI scheduler not started"
    oi_metrics: dict[str, Any] = {}
    if orch is not None and hasattr(orch, "oi"):
        try:
            report = orch.oi.oi_coverage_report()
            cov = report.get("coverage") or report
            oi_metrics = {
                "live": cov.get("live"),
                "stale": cov.get("stale"),
                "waiting": cov.get("waiting"),
                "unavailable": cov.get("unavailable"),
            }
            stale_n = int(cov.get("stale") or 0)
            unavail = int(cov.get("unavailable") or 0)
            live_n = int(cov.get("live") or 0) + int(cov.get("cached") or 0)
            if unavail > 0 and live_n == 0:
                oi_status = HealthStatus.UNAVAILABLE.value
                oi_reason = "OI unavailable for measured universe"
            elif stale_n > 0 and live_n == 0:
                oi_status = HealthStatus.STALE.value
                oi_reason = f"{stale_n} stale OI rows"
            elif live_n > 0:
                oi_status = (
                    HealthStatus.DEGRADED.value if stale_n > 0 else HealthStatus.HEALTHY.value
                )
                oi_reason = f"{live_n} OI rows with values"
            else:
                oi_status = HealthStatus.WAITING.value
                oi_reason = "OI waiting for first successful poll"
        except Exception as exc:  # noqa: BLE001
            oi_status = HealthStatus.UNKNOWN.value
            oi_reason = f"OI coverage read failed: {exc.__class__.__name__}"
    cards.append(
        health_component(
            "oi",
            "OI",
            oi_status,
            reason=oi_reason,
            last_checked=checked,
            metrics=oi_metrics,
        )
    )

    # Liquidations — connected without messages = STALE/WAITING, never HEALTHY
    liq_card = _evaluate_liquidations(orch, checked)
    cards.append(liq_card)

    # Disk / Memory / CPU
    disk = resources.get("disk") or {}
    mem = resources.get("memory") or {}
    cpu = resources.get("cpu") or {}
    cards.append(
        health_component(
            "disk",
            "Disk",
            disk.get("status") or HealthStatus.UNKNOWN.value,
            reason=disk.get("reason"),
            last_checked=checked,
            metrics={"drives": disk.get("drives") or []},
        )
    )
    cards.append(
        health_component(
            "memory",
            "Memory",
            mem.get("status") or HealthStatus.UNKNOWN.value,
            reason=mem.get("reason"),
            last_checked=checked,
            metrics={
                "usage_pct": mem.get("usage_pct"),
                "total_bytes": mem.get("total_bytes"),
                "available_bytes": mem.get("available_bytes"),
            },
        )
    )
    cards.append(
        health_component(
            "cpu",
            "CPU",
            cpu.get("status") or HealthStatus.UNKNOWN.value,
            reason=cpu.get("reason"),
            last_checked=checked,
            metrics={
                "utilization_pct": cpu.get("utilization_pct"),
                "trend_5m_points": len(cpu.get("trend_5m") or []),
            },
        )
    )

    # Backups — evidence from Phase 3 backup service (never invent HEALTHY)
    try:
        from app.diagnostics.backup.service import backup_service

        backups = await backup_service.list_backups(settings)
        cards.append(
            health_component(
                "backups",
                "Backups",
                backups.get("status") or HealthStatus.UNKNOWN.value,
                reason=backups.get("reason"),
                last_checked=checked,
                metrics={
                    "phase": 3,
                    "verified": backups.get("verified"),
                    "count": backups.get("count"),
                    "restore_capability": (backups.get("restore_capability") or {}).get(
                        "status"
                    ),
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        cards.append(
            health_component(
                "backups",
                "Backups",
                HealthStatus.UNKNOWN,
                reason=f"backup_probe_failed:{exc.__class__.__name__}",
                last_checked=checked,
                metrics={"phase": 3, "verified": False},
            )
        )

    overall = worst_status(*(c.status for c in cards))
    # Soften: UNKNOWN/DISABLED/WAITING alone should not make system ERROR
    open_errors = int((issue_counts.get("open_by_severity") or {}).get("ERROR") or 0)
    open_crit = int((issue_counts.get("open_by_severity") or {}).get("CRITICAL") or 0)
    if open_crit > 0:
        overall = HealthStatus.ERROR.value
    elif open_errors > 0 and overall not in (
        HealthStatus.ERROR.value,
        "CRITICAL",
    ):
        overall = worst_status(overall, HealthStatus.DEGRADED.value)

    return {
        "system_status": overall,
        "reason": _overall_reason(overall, cards, issue_counts),
        "last_checked": checked,
        "cards": [c.to_dict() for c in cards],
        "issue_counts": issue_counts,
        "resources": {
            "disk": disk,
            "memory": mem,
            "cpu": cpu,
            "process": proc,
            "thresholds": resources.get("thresholds"),
            "sampled_at": resources.get("sampled_at"),
            "cached": resources.get("cached"),
        },
        "polling": {
            "ui_refresh_seconds": int(
                getattr(settings, "diag_ui_refresh_seconds", 5) or 5
            ),
            "expensive_metrics_seconds": int(
                getattr(settings, "diag_expensive_metrics_seconds", 30) or 30
            ),
        },
    }


async def _evaluate_binance_ws(
    ingestion: Any, orch: Any, checked: str
) -> ComponentHealth:
    connections = 0
    expected = 0
    active = 0
    messages = 0
    last_msg_age: float | None = None
    parse_errors = 0
    sub_errors = 0
    details: list[dict[str, Any]] = []

    if ingestion is not None:
        for c in ingestion.ws.status():
            connections += 1
            if c.get("connected"):
                active += 1
            messages += int(c.get("message_count") or 0)
            details.append(c)
            age = c.get("seconds_since_last_message")
            if age is not None:
                last_msg_age = (
                    float(age)
                    if last_msg_age is None
                    else min(last_msg_age, float(age))
                )

    if orch is not None:
        try:
            kstat = orch.kline_ws.status()
            expected = int(kstat.get("expected_streams") or kstat.get("active_streams") or 0)
            connections += int(kstat.get("websocket_connections") or 0)
            active += int(kstat.get("connected_count") or 0)
            for c in kstat.get("connections") or []:
                messages += int(c.get("message_count") or 0)
                parse_errors += int(c.get("parse_errors") or 0)
                age = c.get("seconds_since_last_message")
                if age is not None:
                    last_msg_age = (
                        float(age)
                        if last_msg_age is None
                        else min(last_msg_age, float(age))
                    )
        except Exception:  # noqa: BLE001
            pass

    if connections == 0 and ingestion is None and orch is None:
        return health_component(
            "binance_ws",
            "Binance WebSocket",
            HealthStatus.WAITING,
            reason="Ingestion/orchestrator not started",
            last_checked=checked,
            metrics={"connections": 0},
        )

    # Connected but no recent frames → STALE (never HEALTHY just for TCP)
    stale_threshold = 120.0
    if active > 0 and (messages == 0 or (last_msg_age is not None and last_msg_age > stale_threshold)):
        status = HealthStatus.STALE.value
        reason = "Connected but no frames received" if messages == 0 else (
            f"Connected but last message {int(last_msg_age)}s ago"
        )
    elif active == 0 and connections > 0:
        status = HealthStatus.ERROR.value
        reason = "WebSocket connections exist but none active"
    elif active > 0:
        status = HealthStatus.HEALTHY.value
        reason = "Receiving frames"
    else:
        status = HealthStatus.WAITING.value
        reason = "No WebSocket connections yet"

    return health_component(
        "binance_ws",
        "Binance WebSocket",
        status,
        reason=reason,
        last_checked=checked,
        stale_seconds=last_msg_age,
        error_count=parse_errors + sub_errors,
        metrics={
            "connections": connections,
            "expected_streams": expected or None,
            "active": active,
            "messages_total": messages,
            "parse_errors": parse_errors,
            "subscription_errors": sub_errors,
            "last_message_age_seconds": last_msg_age,
        },
    )


def _evaluate_liquidations(orch: Any, checked: str) -> ComponentHealth:
    if orch is None:
        return health_component(
            "liquidations",
            "Liquidations",
            HealthStatus.UNAVAILABLE,
            reason="orchestrator not started",
            last_checked=checked,
        )
    try:
        diag = (
            orch.liquidations.diagnostic()
            if hasattr(orch.liquidations, "diagnostic")
            else orch.liquidations.status()
        )
    except Exception as exc:  # noqa: BLE001
        return health_component(
            "liquidations",
            "Liquidations",
            HealthStatus.UNKNOWN,
            reason=f"diagnostic read failed: {exc.__class__.__name__}",
            last_checked=checked,
        )

    raw = str(
        diag.get("liquidation_status")
        or diag.get("status")
        or HealthStatus.UNKNOWN.value
    ).upper()
    connected = bool(diag.get("connected"))
    frames = int(diag.get("frames_received") or diag.get("events_seen") or 0)
    # Never convert WAITING → ERROR
    if raw == "WAITING":
        status = HealthStatus.WAITING.value
        reason = diag.get("reason") or "Waiting for liquidation events"
    elif connected and frames == 0:
        status = HealthStatus.STALE.value
        reason = diag.get("reason") or "Connected but zero liquidation frames"
    elif raw in {s.value for s in HealthStatus}:
        status = raw
        reason = diag.get("reason") or raw
    else:
        status = _map_provider_status(raw)
        reason = diag.get("reason") or raw

    return health_component(
        "liquidations",
        "Liquidations",
        status,
        reason=reason,
        last_checked=checked,
        metrics={
            "connected": connected,
            "frames_received": frames,
            "normalized_events": diag.get("normalized_events"),
            "connection_status": diag.get("connection_status"),
        },
    )


def _overall_reason(
    overall: str, cards: list[ComponentHealth], issue_counts: dict[str, Any]
) -> str:
    bad = [
        c
        for c in cards
        if c.status
        in (
            HealthStatus.ERROR.value,
            HealthStatus.UNAVAILABLE.value,
            HealthStatus.STALE.value,
            HealthStatus.WARNING.value,
            HealthStatus.DEGRADED.value,
            "CRITICAL",
        )
    ]
    open_n = int((issue_counts.get("by_status") or {}).get("OPEN") or 0)
    if not bad and open_n == 0:
        if overall == HealthStatus.HEALTHY.value:
            return "All measured components healthy"
        return f"System status {overall} — some components unmeasured/disabled"
    parts = [f"{c.label}:{c.status}" for c in bad[:6]]
    if open_n:
        parts.append(f"open_issues={open_n}")
    return "; ".join(parts)
