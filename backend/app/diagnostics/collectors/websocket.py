"""WebSocket forensics — semantic health; connected ≠ healthy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.diagnostics.constants import HealthStatus, worst_status
from app.ingestion.ws_forensics import ws_forensics


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def _semantic_ws_status(
    *,
    connected: bool,
    frames: int,
    last_frame_at: str | None,
    stale_after: float,
    started: bool = True,
    reader_running: bool | None = None,
) -> tuple[str, str | None, float | None]:
    last_dt = _parse_iso(last_frame_at)
    stale_seconds = None
    if last_dt is not None:
        stale_seconds = max(0.0, (_utcnow() - last_dt).total_seconds())

    if not started and not connected:
        return HealthStatus.WAITING.value, "NOT_STARTED", stale_seconds
    # CONNECTED but reader dead — never call this LIVE
    if connected and reader_running is False:
        return HealthStatus.ERROR.value, "CONNECTED_BUT_READER_DEAD", stale_seconds
    if connected and frames == 0 and last_dt is None:
        return (
            HealthStatus.WAITING.value,
            "WAITING_FOR_FIRST_FRAME",
            stale_seconds,
        )
    if connected and stale_seconds is not None and stale_seconds > stale_after:
        return (
            HealthStatus.STALE.value,
            "CONNECTED_BUT_NO_RECENT_FRAMES",
            stale_seconds,
        )
    if connected and frames > 0 and (
        stale_seconds is None or stale_seconds <= stale_after
    ):
        return "LIVE", None, stale_seconds
    if not connected and frames > 0:
        # Had data before disconnect
        if stale_seconds is not None and stale_seconds > stale_after:
            return HealthStatus.STALE.value, "DISCONNECTED_STALE", stale_seconds
        return HealthStatus.ERROR.value, "DISCONNECTED", stale_seconds
    if not connected:
        return HealthStatus.ERROR.value, "DISCONNECTED", stale_seconds
    return HealthStatus.UNKNOWN.value, "UNKNOWN", stale_seconds


def _enrich_connection(
    raw: dict[str, Any],
    *,
    provider: str,
    market: str | None,
    stream_type: str,
    shard: str | None,
    expected_subs: int | None,
    stale_after: float,
) -> dict[str, Any]:
    connected = bool(raw.get("connected"))
    frames = int(raw.get("frames_received") or raw.get("message_count") or 0)
    last_frame = raw.get("last_message_at") or raw.get("last_frame_at")
    reader_running = raw.get("reader_running")
    status, reason, stale_s = _semantic_ws_status(
        connected=connected,
        frames=frames,
        last_frame_at=last_frame,
        stale_after=stale_after,
        started=True,
        reader_running=reader_running if reader_running is not None else None,
    )
    subs = raw.get("subscribed")
    if subs is None:
        subs = raw.get("streams")
    fox = raw.get("forensics") if isinstance(raw.get("forensics"), dict) else None
    return {
        "connection_id": raw.get("connection_id")
        or raw.get("name")
        or shard
        or provider,
        "provider": provider,
        "market": market,
        "endpoint": raw.get("url") or (fox or {}).get("endpoint"),
        "stream_type": stream_type,
        "shard": shard or raw.get("name"),
        "connected": connected,
        "connected_at": (fox or {}).get("connected_at"),
        "connection_age_seconds": raw.get("connection_age_seconds"),
        "last_frame_at": last_frame,
        "last_valid_message_at": last_frame,
        "frames_total": frames,
        "messages_total": int(raw.get("message_count") or frames),
        "messages_per_second": (fox or {}).get("messages_per_second"),
        "bytes_per_second": (fox or {}).get("bytes_per_second"),
        "bytes_received": raw.get("bytes_received") or (fox or {}).get("bytes_received"),
        "parse_errors": raw.get("parse_errors")
        if raw.get("parse_errors") is not None
        else (fox or {}).get("parse_errors"),
        "subscription_count": int(subs) if subs is not None else None,
        "expected_subscription_count": expected_subs,
        "disconnect_count": raw.get("disconnect_count")
        if raw.get("disconnect_count") is not None
        else (fox or {}).get("disconnect_count"),
        "reconnect_count": raw.get("reconnect_count")
        if raw.get("reconnect_count") is not None
        else (fox or {}).get("reconnect_count"),
        "connect_count": raw.get("connect_count"),
        "last_error": raw.get("last_error"),
        "disconnect_reason": raw.get("disconnect_reason")
        or (fox or {}).get("disconnect_reason"),
        "disconnect_evidence": raw.get("disconnect_evidence")
        or (fox or {}).get("disconnect_evidence"),
        "close_code": raw.get("close_code")
        if raw.get("close_code") is not None
        else (fox or {}).get("close_code"),
        "close_reason": (fox or {}).get("close_reason"),
        "stale_seconds": stale_s,
        "status": status,
        "reason": reason,
        "reader_running": reader_running,
        "candle_count": raw.get("candle_count"),
        "load_bucket": (fox or {}).get("load_bucket"),
        "subscription_health": (fox or {}).get("subscription_health"),
        "recent_disconnects": (fox or {}).get("recent_disconnects"),
        "recent_data_gaps": (fox or {}).get("recent_data_gaps"),
        "forensics": fox,
    }


async def collect_websocket_forensics(settings: Any) -> dict[str, Any]:
    from app.engines.orchestrator import get_orchestrator
    from app.ingestion.service import get_ingestion

    stale_after = float(
        getattr(settings, "diag_ws_stale_seconds", 120) or 120
    )
    checked = _utcnow().isoformat()
    registry_report = ws_forensics.build_report()
    from app.ingestion.handler_latency import handler_latency
    from app.services.database import db_manager

    out: dict[str, Any] = {
        "phase": 17,
        "last_checked": checked,
        "collector": "diagnostics.collectors.websocket.collect_websocket_forensics",
        "file": "backend/app/diagnostics/collectors/websocket.py",
        "function": "collect_websocket_forensics",
        "stale_after_seconds": stale_after,
        "overall_status": HealthStatus.WAITING.value,
        "connections": [],
        "by_stream_type": {},
        "status": HealthStatus.WAITING.value,
        "reason": None,
        "disconnect_summary": registry_report["disconnect_summary"],
        "reconnect_summary": registry_report["reconnect_summary"],
        "event_loop": registry_report["event_loop"],
        "handler_latency": handler_latency.snapshot(),
        "db_startup": {
            "schema_ready": db_manager.schema_ready,
            "status": db_manager.status,
            "timescale": db_manager.timescale,
            "last_schema_error": db_manager.last_schema_error,
            "startup_stages": list(db_manager.startup_stages[-40:]),
        },
        "subscription_health": registry_report["subscription_health"],
        "data_gap_summary": registry_report["data_gap_summary"],
        "issues_candidates": list(registry_report.get("issues_candidates") or []),
        "errors": [],
        "root_cause": {
            "status": "UNKNOWN",
            "note": (
                "Do not invent root cause. Use disconnect_reason + evidence + "
                "aligned event_loop lag / load buckets from live observation."
            ),
        },
    }

    ingestion = get_ingestion()
    orch = get_orchestrator()
    connections: list[dict[str, Any]] = []
    runtime_connection_count = 0

    # Ticker / mark price WS
    if ingestion is not None:
        try:
            for raw in ingestion.ws.status():
                name = str(raw.get("name") or "")
                stream_type = "ticker"
                if "mark" in name.lower():
                    stream_type = "mark_price"
                connections.append(
                    _enrich_connection(
                        raw,
                        provider="binance",
                        market="futures",
                        stream_type=stream_type,
                        shard=name,
                        expected_subs=1,
                        stale_after=stale_after,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"code": "WS_TICKER", "error": str(exc)})

    # Kline WS
    if orch is not None:
        try:
            kstat = orch.kline_ws.status()
            expected = int(kstat.get("active_streams") or 0)
            for raw in kstat.get("connections") or []:
                connections.append(
                    _enrich_connection(
                        raw,
                        provider="binance",
                        market="futures",
                        stream_type="kline",
                        shard=raw.get("name"),
                        expected_subs=int(raw.get("streams") or 0) or None,
                        stale_after=stale_after,
                    )
                )
            # Aggregate kline
            kline_conns = [c for c in connections if c["stream_type"] == "kline"]
            out["by_stream_type"]["kline"] = {
                "connections": len(kline_conns),
                "expected_streams": expected or None,
                "active_subscriptions": sum(
                    int(c["subscription_count"] or 0) for c in kline_conns
                )
                if kline_conns
                else None,
                "connected_count": sum(1 for c in kline_conns if c["connected"]),
                "last_frame_at": min(
                    (c["last_frame_at"] for c in kline_conns if c["last_frame_at"]),
                    default=None,
                ),
                "parse_errors": sum(int(c.get("parse_errors") or 0) for c in kline_conns)
                if kline_conns
                else None,
                "status": worst_status(*(c["status"] for c in kline_conns))
                if kline_conns
                else HealthStatus.WAITING.value,
                "symbols": kstat.get("symbols"),
                "timeframes": kstat.get("timeframes"),
            }
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"code": "WS_KLINE", "error": str(exc)})

        # Liquidations
        try:
            liq = (
                orch.liquidations.diagnostic()
                if hasattr(orch.liquidations, "diagnostic")
                else orch.liquidations.status()
            )
            liq_status = str(
                liq.get("liquidation_status") or liq.get("status") or "UNKNOWN"
            ).upper()
            out["by_stream_type"]["liquidations"] = {
                "connected": bool(liq.get("connected")),
                "connection_status": liq.get("connection_status"),
                "last_event_at": liq.get("last_parsed_at") or liq.get("last_event_at"),
                "frames_received": liq.get("frames_received"),
                "normalized_events": liq.get("normalized_events"),
                "events_seen": liq.get("events_seen") or liq.get("total_events"),
                "status": liq_status,
                "reason": liq.get("reason"),
                "stream": liq.get("stream") or liq.get("stream_name"),
                "provider": liq.get("provider"),
            }
            # Merge registry forensics for force-order if present
            fox_row = ws_forensics.get("binance_force_order")
            connections.append(
                {
                    "connection_id": (
                        fox_row.connection_id if fox_row else "binance_force_order"
                    ),
                    "provider": "binance",
                    "market": "futures",
                    "endpoint": liq.get("endpoint"),
                    "stream_type": "liquidation",
                    "shard": "force_order",
                    "connected": bool(liq.get("connected")),
                    "last_frame_at": liq.get("last_frame_at")
                    or liq.get("last_parsed_at"),
                    "frames_total": liq.get("frames_received"),
                    "messages_total": liq.get("normalized_events"),
                    "messages_per_second": fox_row.messages_per_second if fox_row else None,
                    "parse_errors": liq.get("parse_errors"),
                    "subscription_count": 1,
                    "expected_subscription_count": 1,
                    "disconnect_count": liq.get("disconnect_count")
                    or (fox_row.disconnect_count if fox_row else None),
                    "reconnect_count": liq.get("reconnect_count")
                    or (fox_row.reconnect_count if fox_row else None),
                    "last_error": liq.get("last_error"),
                    "disconnect_reason": fox_row.disconnect_reason.value
                    if fox_row and fox_row.disconnect_reason
                    else None,
                    "disconnect_evidence": fox_row.disconnect_evidence if fox_row else None,
                    "close_code": fox_row.close_code if fox_row else None,
                    "stale_seconds": liq.get("seconds_since_last_event"),
                    "status": liq_status,
                    "reason": liq.get("reason"),
                    "forensics": fox_row.to_summary() if fox_row else None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"code": "WS_LIQ", "error": str(exc)})

        # Trade tip shards
        try:
            tips = orch.trade_tips.status() if hasattr(orch, "trade_tips") else None
            if isinstance(tips, dict):
                tip_shards = tips.get("shards") or []
                for raw in tip_shards:
                    if isinstance(raw, dict):
                        connections.append(
                            _enrich_connection(
                                raw,
                                provider="binance",
                                market="futures",
                                stream_type="trade_tip",
                                shard=raw.get("name"),
                                expected_subs=int(raw.get("symbols") or 0) or None,
                                stale_after=stale_after,
                            )
                        )
                out["by_stream_type"]["trade_tip"] = {
                    "message_count": tips.get("message_count"),
                    "shards": len(tip_shards)
                    if isinstance(tip_shards, list)
                    else tips.get("shard_count"),
                    "connected_count": tips.get("connected_count"),
                    "status": HealthStatus.UNKNOWN.value
                    if tips.get("message_count") is None
                    else (
                        "LIVE"
                        if tips.get("message_count")
                        else HealthStatus.WAITING.value
                    ),
                    "raw_keys": sorted(tips.keys()),
                }
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"code": "WS_TRADE_TIP", "error": str(exc)})

    # Aggregate ticker/mark
    for stype in ("ticker", "mark_price", "liquidation"):
        subset = [c for c in connections if c.get("stream_type") == stype]
        if not subset:
            continue
        if stype not in out["by_stream_type"]:
            out["by_stream_type"][stype] = {
                "connections": len(subset),
                "connected_count": sum(1 for c in subset if c.get("connected")),
                "last_frame_at": min(
                    (c.get("last_frame_at") for c in subset if c.get("last_frame_at")),
                    default=None,
                ),
                "status": worst_status(*(str(c.get("status")) for c in subset)),
                "parse_errors": None,
            }

    runtime_connection_count = len(connections)
    out["connections"] = connections

    # Merge registry-only connections not already listed (forensic detail only)
    seen_ids = {c.get("connection_id") for c in connections}
    for fox_sum in registry_report.get("connections") or []:
        cid = fox_sum.get("connection_id")
        if cid and cid not in seen_ids:
            connections.append(
                {
                    **fox_sum,
                    "status": HealthStatus.UNKNOWN.value,
                    "reason": "REGISTRY_ONLY",
                    "provider": "binance",
                }
            )
            seen_ids.add(cid)
    out["connections"] = connections

    if runtime_connection_count == 0:
        # Preserve empty runtime connections for WAITING; keep registry under side key
        out["registry_connections"] = [
            c for c in connections if c.get("reason") == "REGISTRY_ONLY"
        ]
        out["connections"] = []
        out["status"] = HealthStatus.WAITING.value
        out["overall_status"] = HealthStatus.WAITING.value
        out["reason"] = "No WebSocket connections started"
        return out

    statuses = [str(c.get("status")) for c in connections]
    out["status"] = worst_status(*statuses)
    out["overall_status"] = out["status"]

    # Prefer STALE / reader-dead reasons
    dead_readers = [
        c
        for c in connections
        if c.get("reason") == "CONNECTED_BUT_READER_DEAD"
    ]
    if dead_readers:
        out["reason"] = "CONNECTED_BUT_READER_DEAD"
        for c in dead_readers[:5]:
            out["issues_candidates"].append(
                {
                    "error_code": "WS_TASK_DIED",
                    "severity": "ERROR",
                    "message": f"Connected but reader dead: {c.get('connection_id')}",
                    "expected": "reader_running=true while connected",
                    "actual": "reader_running=false",
                    "details": {
                        "connection_id": c.get("connection_id"),
                        "stream_type": c.get("stream_type"),
                        "probable_root_cause": "STRONG_EVIDENCE",
                        "confidence": "STRONG_EVIDENCE",
                        "exact_location": "diagnostics.collectors.websocket",
                    },
                }
            )
    stale = [c for c in connections if c.get("status") == HealthStatus.STALE.value]
    if stale and not dead_readers:
        out["reason"] = "CONNECTED_BUT_NO_RECENT_FRAMES"
        for c in stale[:5]:
            out["issues_candidates"].append(
                {
                    "error_code": "WS_STALE",
                    "severity": "WARNING",
                    "message": f"WebSocket stale: {c.get('connection_id')}",
                    "expected": f"frames within {stale_after}s",
                    "actual": f"stale_seconds={c.get('stale_seconds')}",
                    "details": {
                        "connection_id": c.get("connection_id"),
                        "stream_type": c.get("stream_type"),
                        "connected": c.get("connected"),
                        "last_frame_at": c.get("last_frame_at"),
                        "probable_root_cause": "UNKNOWN",
                        "confidence": "UNKNOWN",
                    },
                }
            )
    elif out["status"] in (HealthStatus.ERROR.value,) and not dead_readers:
        out["reason"] = "One or more WebSocket connections disconnected"
    elif out["status"] in ("LIVE", HealthStatus.HEALTHY.value):
        out["status"] = "LIVE"
        out["overall_status"] = "LIVE"
        out["reason"] = "Receiving frames"
    elif out["status"] == HealthStatus.WAITING.value:
        out["reason"] = "WAITING_FOR_FIRST_FRAME"

    # Subscription mismatch on kline
    kline = out["by_stream_type"].get("kline") or {}
    exp = kline.get("expected_streams")
    act = kline.get("active_subscriptions")
    if exp is not None and act is not None and act < exp:
        out["issues_candidates"].append(
            {
                "error_code": "WS_SUBSCRIPTION_MISMATCH",
                "severity": "WARNING",
                "message": "Kline subscription count below expected",
                "expected": str(exp),
                "actual": str(act),
                "details": {
                    "probable_root_cause": "STRONG_EVIDENCE",
                    "confidence": "STRONG_EVIDENCE",
                },
            }
        )

    # Evidence-based root_cause rollup (still UNKNOWN unless single dominant confirmed reason)
    reasons = [
        c.get("disconnect_reason")
        for c in connections
        if c.get("disconnect_reason")
    ]
    if reasons:
        # Dominant recent reason — still not auto-confirmed as system root cause
        from collections import Counter

        top, n = Counter(reasons).most_common(1)[0]
        out["root_cause"] = {
            "status": "UNKNOWN" if n < 3 else "PROBABLE",
            "dominant_disconnect_reason": top,
            "count": n,
            "note": (
                "PROBABLE only when the same classified reason repeats; "
                "CONFIRMED requires live close-frame/task evidence alignment."
            ),
        }

    return out


async def collect_websocket_connection_detail(connection_id: str) -> dict[str, Any] | None:
    fox = ws_forensics.get(connection_id)
    if fox is None:
        return None
    detail = fox.to_detail()
    detail["event_loop"] = ws_forensics.event_loop.snapshot()
    # Enrich with live runtime flags if connection still in managers
    detail["overall_status"] = (
        "LIVE"
        if fox.connected and fox.raw_messages > 0 and fox.reader_running
        else (
            HealthStatus.WAITING.value
            if fox.connected and fox.raw_messages == 0
            else HealthStatus.ERROR.value
            if not fox.connected
            else HealthStatus.UNKNOWN.value
        )
    )
    return detail
