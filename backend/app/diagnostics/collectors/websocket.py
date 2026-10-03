"""WebSocket forensics — semantic health; connected ≠ healthy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.diagnostics.constants import HealthStatus, worst_status


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
) -> tuple[str, str | None, float | None]:
    last_dt = _parse_iso(last_frame_at)
    stale_seconds = None
    if last_dt is not None:
        stale_seconds = max(0.0, (_utcnow() - last_dt).total_seconds())

    if not started and not connected:
        return HealthStatus.WAITING.value, "NOT_STARTED", stale_seconds
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
    status, reason, stale_s = _semantic_ws_status(
        connected=connected,
        frames=frames,
        last_frame_at=last_frame,
        stale_after=stale_after,
        started=True,
    )
    subs = raw.get("subscribed")
    if subs is None:
        subs = raw.get("streams")
    return {
        "connection_id": raw.get("name") or shard or provider,
        "provider": provider,
        "market": market,
        "endpoint": raw.get("url"),
        "stream_type": stream_type,
        "shard": shard or raw.get("name"),
        "connected": connected,
        "connected_at": None,  # not always tracked as absolute ts
        "connection_age_seconds": raw.get("connection_age_seconds"),
        "last_frame_at": last_frame,
        "last_valid_message_at": last_frame,
        "frames_total": frames,
        "messages_total": int(raw.get("message_count") or frames),
        "messages_per_minute": None,  # not instrumented — UNKNOWN/null
        "bytes_received": raw.get("bytes_received"),
        "parse_errors": raw.get("parse_errors"),  # null if not instrumented
        "subscription_count": int(subs) if subs is not None else None,
        "expected_subscription_count": expected_subs,
        "disconnect_count": raw.get("disconnect_count"),
        "reconnect_count": raw.get("reconnect_count"),
        "connect_count": raw.get("connect_count"),
        "last_error": raw.get("last_error"),
        "stale_seconds": stale_s,
        "status": status,
        "reason": reason,
        "reader_running": raw.get("reader_running"),
        "candle_count": raw.get("candle_count"),
    }


async def collect_websocket_forensics(settings: Any) -> dict[str, Any]:
    from app.engines.orchestrator import get_orchestrator
    from app.ingestion.service import get_ingestion

    stale_after = float(
        getattr(settings, "diag_ws_stale_seconds", 120) or 120
    )
    checked = _utcnow().isoformat()
    out: dict[str, Any] = {
        "phase": 2,
        "last_checked": checked,
        "collector": "diagnostics.collectors.websocket.collect_websocket_forensics",
        "file": "backend/app/diagnostics/collectors/websocket.py",
        "function": "collect_websocket_forensics",
        "stale_after_seconds": stale_after,
        "connections": [],
        "by_stream_type": {},
        "status": HealthStatus.WAITING.value,
        "reason": None,
        "issues_candidates": [],
        "errors": [],
    }

    ingestion = get_ingestion()
    orch = get_orchestrator()
    connections: list[dict[str, Any]] = []

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
                        expected_subs=None,
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
                "parse_errors": None,
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
            # Never convert WAITING → ERROR; never invent zero event rate as missing
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
            # Also as a connection row
            connections.append(
                {
                    "connection_id": "binance_force_order",
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
                    "messages_per_minute": None,
                    "parse_errors": liq.get("parse_errors"),
                    "subscription_count": 1,
                    "expected_subscription_count": 1,
                    "disconnect_count": liq.get("disconnect_count"),
                    "reconnect_count": liq.get("reconnect_count"),
                    "last_error": liq.get("last_error"),
                    "stale_seconds": liq.get("seconds_since_last_event"),
                    "status": liq_status,
                    "reason": liq.get("reason"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"code": "WS_LIQ", "error": str(exc)})

        # Trade tip shards if present
        try:
            tips = orch.trade_tips.status() if hasattr(orch, "trade_tips") else None
            if isinstance(tips, dict):
                out["by_stream_type"]["trade_tip"] = {
                    "message_count": tips.get("message_count"),
                    "shards": tips.get("shards") or tips.get("shard_count"),
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

    out["connections"] = connections

    if not connections:
        out["status"] = HealthStatus.WAITING.value
        out["reason"] = "No WebSocket connections started"
        return out

    statuses = [str(c.get("status")) for c in connections]
    out["status"] = worst_status(*statuses)
    # Prefer STALE reason when applicable
    stale = [c for c in connections if c.get("status") == HealthStatus.STALE.value]
    if stale:
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
                    },
                }
            )
    elif out["status"] in (HealthStatus.ERROR.value,):
        out["reason"] = "One or more WebSocket connections disconnected"
    elif out["status"] in ("LIVE", HealthStatus.HEALTHY.value):
        out["status"] = "LIVE"
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
            }
        )

    return out
