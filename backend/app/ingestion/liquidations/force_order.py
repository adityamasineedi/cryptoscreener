from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.core.logging import get_logger
from app.engines.liquidations.engine import (
    LiquidationEvent,
    aggregate_windows,
    imbalance_ratio,
    liquidation_spike,
    parse_force_order,
    volume_ratio,
)
from app.ingestion.binance_futures_ws import (
    FORCE_ORDER_AGGREGATE,
    MARKET_TYPE,
    force_order_aggregate_url,
)
from app.ingestion.ws_manager import WebSocketManager
from app.models.schemas import FreshValue

logger = get_logger("liquidations")

# Freshness window for LIVE vs STALE after at least one real event.
STALE_AFTER_SECONDS = 300


@dataclass
class SymbolLiquidationState:
    symbol: str
    events: deque[LiquidationEvent] = field(default_factory=lambda: deque(maxlen=5000))
    summary: FreshValue[str] = field(
        default_factory=lambda: FreshValue.waiting("binance_force_order")
    )
    last_event_at: datetime | None = None


class LiquidationIngestion:
    """Binance !forceOrder@arr — real events only; WAITING until data arrives."""

    STREAM_NAME = "binance_force_order"
    STREAM = FORCE_ORDER_AGGREGATE

    def __init__(
        self,
        settings: Settings,
        ws_manager: WebSocketManager,
        *,
        volume_lookup: Any | None = None,
        stale_after_seconds: int = STALE_AFTER_SECONDS,
    ) -> None:
        self.settings = settings
        self.ws_manager = ws_manager
        self.volume_lookup = volume_lookup
        self.stale_after_seconds = stale_after_seconds
        self._by_symbol: dict[str, SymbolLiquidationState] = {}
        self._global_events: deque[LiquidationEvent] = deque(maxlen=10000)
        self._seen_keys: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=20000)
        self._started = False
        self._connect_url: str | None = None
        self._market_type = MARKET_TYPE

        # Pipeline counters (raw frames counted in ws_manager; parse here)
        self._ws_parse_attempts = 0
        self._ws_parse_success = 0
        self._ws_parse_errors = 0
        self._raw_events = 0
        self._normalized_events = 0
        self._duplicates_removed = 0
        self._db_rows_written = 0
        self._db_rows_rejected = 0
        self._cache_writes = 0
        self._cache_reads = 0
        self._cache_errors = 0
        self._last_raw: Any | None = None
        self._last_raw_at: datetime | None = None
        self._last_parsed_at: datetime | None = None
        self._last_frame_preview: str | None = None
        self._subscription_status = "url_stream"  # dedicated /market/ws stream URL

    def _state(self, symbol: str) -> SymbolLiquidationState:
        if symbol not in self._by_symbol:
            self._by_symbol[symbol] = SymbolLiquidationState(symbol=symbol)
        return self._by_symbol[symbol]

    def build_url(self) -> str:
        return force_order_aggregate_url(self.settings.binance_futures_ws)

    async def start(self) -> None:
        if self._started:
            return
        url = self.build_url()
        self._connect_url = url
        logger.info(
            "liquidations_connecting",
            market_type=self._market_type,
            base_ws_url=self.settings.binance_futures_ws,
            stream_name=self.STREAM,
            url=url,
        )
        await self.ws_manager.ensure(
            name=self.STREAM_NAME,
            url=url,
            handler=self._on_message,
        )
        self._started = True
        self._subscription_status = "subscribed_via_url"
        logger.info(
            "liquidations_stream_started",
            url=url,
            market_type=self._market_type,
            stream=self.STREAM,
        )

    async def stop(self) -> None:
        self._started = False
        if self.ws_manager.has(self.STREAM_NAME):
            conn = self.ws_manager._connections.get(self.STREAM_NAME)
            if conn is not None:
                await conn.stop()

    def _unwrap(self, data: Any) -> Any:
        """Handle combined-stream envelopes and nested forceOrder payloads."""
        if isinstance(data, dict) and "data" in data and isinstance(
            data["data"], (dict, list)
        ):
            return data["data"]
        return data

    def _conn(self) -> dict[str, Any] | None:
        for c in self.ws_manager.status():
            if c.get("name") == self.STREAM_NAME:
                return c
        return None

    async def _on_message(self, data: dict[str, Any]) -> None:
        self._ws_parse_attempts += 1
        self._last_raw_at = datetime.now(timezone.utc)
        try:
            if isinstance(data, dict):
                self._last_raw = {k: data[k] for k in list(data.keys())[:8]}
                preview = str(data)[:200]
            else:
                self._last_raw = str(type(data))
                preview = str(data)[:200]
            self._last_frame_preview = preview
        except Exception:  # noqa: BLE001
            self._last_raw = None

        # Ignore pure subscription acknowledgements
        if isinstance(data, dict) and "result" in data and "e" not in data:
            self._subscription_status = f"ack:{data.get('result')!r}"
            self._ws_parse_success += 1
            return

        payload = self._unwrap(data)
        try:
            if isinstance(payload, list):
                for item in payload:
                    await self._ingest_one(item if isinstance(item, dict) else {})
            elif isinstance(payload, dict):
                await self._ingest_one(payload)
            else:
                self._ws_parse_errors += 1
                return
            self._ws_parse_success += 1
        except Exception as exc:  # noqa: BLE001
            self._ws_parse_errors += 1
            logger.warning("liquidation_parse_failed", error=str(exc))

    def _remember_key(self, key: str) -> bool:
        """Return True if newly seen; False if duplicate."""
        if not key:
            return True
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        self._seen_order.append(key)
        while len(self._seen_keys) > len(self._seen_order):
            # defensive; deque maxlen drops oldest key string but set needs sync
            break
        if len(self._seen_order) == self._seen_order.maxlen:
            # Rebuild set from deque when at capacity periodically
            if len(self._seen_keys) > self._seen_order.maxlen:
                self._seen_keys = set(self._seen_order)
        return True

    async def _ingest_one(self, payload: dict[str, Any]) -> None:
        if not payload:
            return
        self._raw_events += 1
        ev = parse_force_order(payload, received_at=datetime.now(timezone.utc))
        if ev is None:
            # Ignore control frames that aren't force orders
            if payload.get("e") not in (None, "forceOrder") and "s" not in payload:
                return
            if payload.get("e") == "forceOrder" or "s" in payload:
                self._ws_parse_errors += 1
            return

        if not self._remember_key(ev.event_key):
            self._duplicates_removed += 1
            return

        st = self._state(ev.symbol)
        st.events.append(ev)
        st.last_event_at = ev.timestamp
        self._global_events.append(ev)
        self._normalized_events += 1
        # Freshness uses ingest time, not exchange trade time (events can be older).
        self._last_parsed_at = ev.received_at or datetime.now(timezone.utc)
        st.summary = FreshValue.live(self._describe(st), "binance_force_order")

        try:
            from app.services.persistence import persistence

            before = getattr(persistence, "_writes", 0)
            await persistence.persist_liquidation(
                symbol=ev.symbol,
                side=ev.side,
                price=ev.price,
                quantity=ev.quantity,
                quote_qty=getattr(ev, "notional", None),
                source=ev.source,
                ts=ev.timestamp,
            )
            after = getattr(persistence, "_writes", 0)
            if after > before:
                self._db_rows_written += 1
            elif not getattr(persistence, "active", False):
                # DB not active — not a rejection of the event itself
                pass
            else:
                # ON CONFLICT DO NOTHING or no-op
                self._db_rows_rejected += 0
        except Exception as exc:  # noqa: BLE001
            self._db_rows_rejected += 1
            logger.warning("liquidation_persist_failed", error=str(exc))

    def _describe(self, st: SymbolLiquidationState) -> str:
        aggs = aggregate_windows(st.events)
        w5 = aggs.get("5m", {})
        imb = imbalance_ratio(
            w5.get("long_liq_notional", 0), w5.get("short_liq_notional", 0)
        )
        w15 = aggs.get("15m", {})
        spike = liquidation_spike(
            w5.get("total_notional", 0),
            (w15.get("total_notional", 0) or 0) / 3.0 if w15 else 0,
        )
        vol = None
        if self.volume_lookup:
            vol = self.volume_lookup(st.symbol)
        vr = volume_ratio(w5.get("total_notional", 0), vol)
        parts = []
        if imb is not None:
            parts.append(f"imb={imb:.2f}")
        if spike:
            parts.append("spike")
        if vr is not None:
            parts.append(f"vol_ratio={vr:.4f}")
        return ";".join(parts) if parts else "active"

    def get_summary(self, symbol: str) -> FreshValue[str]:
        st = self._by_symbol.get(symbol)
        if st is None or not st.events:
            return FreshValue.waiting("binance_force_order")
        return st.summary

    def aggregates(self, symbol: str) -> dict[str, dict[str, float]]:
        st = self._by_symbol.get(symbol)
        if st is None:
            return {}
        return aggregate_windows(st.events)

    def recent_events(self, symbol: str | None = None, *, limit: int = 100) -> list[LiquidationEvent]:
        if symbol:
            st = self._by_symbol.get(symbol)
            if st is None:
                return []
            return list(st.events)[-limit:]
        return list(self._global_events)[-limit:]

    def compute_status(self) -> str:
        """WAITING | LIVE | STALE | UNAVAILABLE — never LIVE on connect alone."""
        conn = self._conn()
        connected = bool(conn and conn.get("connected"))
        reader = bool(conn and conn.get("reader_running"))
        events = self._normalized_events
        # Real normalized events decide LIVE/STALE even if the socket later drops.
        if events > 0:
            last = self._last_parsed_at
            if last is None:
                return "WAITING"
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age > self.stale_after_seconds:
                return "STALE"
            return "LIVE"
        if self._started and (connected or reader):
            return "WAITING"
        if self._started and not connected:
            return "UNAVAILABLE"
        # Not started yet — honest waiting (cold boot), not fabricated LIVE.
        return "WAITING"

    def status(self) -> dict[str, Any]:
        live = sum(1 for s in self._by_symbol.values() if s.events)
        return {
            "symbols_with_events": live,
            "total_events": len(self._global_events),
            "normalized_events": self._normalized_events,
            "stream": self.STREAM_NAME,
            "stream_name": self.STREAM,
            "started": self._started,
            "liquidation_status": self.compute_status(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def diagnostic(self) -> dict[str, Any]:
        conn = self._conn()
        dns_ok: bool | None = None
        tls_ok: bool | None = None
        dns_error: str | None = None
        host = None
        try:
            from urllib.parse import urlparse
            import socket
            import ssl

            parsed = urlparse(self._connect_url or self.build_url())
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme in ("wss", "https") else 80)
            if host:
                socket.getaddrinfo(host, port)
                dns_ok = True
                try:
                    ctx = ssl.create_default_context()
                    with socket.create_connection((host, port), timeout=5) as sock:
                        with ctx.wrap_socket(sock, server_hostname=host):
                            tls_ok = True
                except Exception as exc:  # noqa: BLE001
                    tls_ok = False
                    dns_error = f"tls:{exc}"
        except Exception as exc:  # noqa: BLE001
            dns_ok = False
            dns_error = str(exc)

        status = self.compute_status()
        frames = int(conn.get("frames_received") or conn.get("message_count") or 0) if conn else 0
        reason = None
        if status == "WAITING" and frames == 0:
            reason = "NO FRAMES RECEIVED"
        elif status == "WAITING" and frames > 0 and self._normalized_events == 0:
            reason = "FRAMES RECEIVED BUT NO FORCE ORDER EVENTS"
        elif status == "STALE":
            reason = "NO RECENT EVENTS"
        elif status == "UNAVAILABLE":
            reason = "PROVIDER/CONNECTION UNAVAILABLE"

        return {
            "provider": self.STREAM_NAME,
            "endpoint": self._connect_url or self.build_url(),
            "stream": self.STREAM,
            "market_type": self._market_type,
            "base_ws_url": self.settings.binance_futures_ws,
            "connected": bool(conn and conn.get("connected")),
            "reader_running": bool(conn and conn.get("reader_running")),
            "connection_status": (
                "CONNECTED"
                if conn and conn.get("connected")
                else ("STARTED" if self._started else "STOPPED")
            ),
            "liquidation_status": status,
            "status": status,
            "reason": reason,
            "frames_received": frames,
            "bytes_received": conn.get("bytes_received") if conn else 0,
            "ws_connect_attempts": conn.get("connect_attempts") if conn else 0,
            "ws_connected": conn.get("connect_count") if conn else 0,
            "ws_disconnected": conn.get("disconnect_count") if conn else 0,
            "ws_frames_received": frames,
            "ws_bytes_received": conn.get("bytes_received") if conn else 0,
            "ws_parse_attempts": self._ws_parse_attempts,
            "ws_parse_success": self._ws_parse_success,
            "ws_parse_errors": self._ws_parse_errors,
            "parser_errors": self._ws_parse_errors,
            "raw_events": self._raw_events,
            "normalized_events": self._normalized_events,
            "duplicates_removed": self._duplicates_removed,
            "events_seen": len(self._global_events),
            "symbols_with_events": sum(1 for s in self._by_symbol.values() if s.events),
            "last_frame_at": (
                conn.get("last_message_at")
                if conn and conn.get("last_message_at")
                else (self._last_raw_at.isoformat() if self._last_raw_at else None)
            ),
            "last_event_at": (
                self._last_parsed_at.isoformat() if self._last_parsed_at else None
            ),
            "last_message_time": self._last_raw_at.isoformat() if self._last_raw_at else None,
            "last_parsed_time": (
                self._last_parsed_at.isoformat() if self._last_parsed_at else None
            ),
            "last_frame_preview": self._last_frame_preview,
            "last_frame": self._last_raw,
            "last_frame_bytes": conn.get("last_frame_bytes") if conn else None,
            "reconnects": conn.get("reconnect_count") if conn else 0,
            "reconnect_count": conn.get("reconnect_count") if conn else 0,
            "subscription_status": self._subscription_status,
            "db_rows_written": self._db_rows_written,
            "db_rows_rejected": self._db_rows_rejected,
            "cache_writes": self._cache_writes,
            "cache_reads": self._cache_reads,
            "cache_errors": self._cache_errors,
            "liquidation_cache_writes": self._cache_writes,
            "liquidation_cache_reads": self._cache_reads,
            "liquidation_cache_errors": self._cache_errors,
            "host": host,
            "dns_resolution": {"ok": dns_ok, "error": dns_error},
            "tls_connection": {"ok": tls_ok},
            "stream_handshake": {
                "ok": conn.get("last_handshake_ok") if conn else None,
                "connected": bool(conn and conn.get("connected")),
            },
            "connection_age_seconds": conn.get("connection_age_seconds") if conn else None,
            "ws": conn,
            "lifecycle": conn.get("lifecycle") if conn else [],
            "note": (
                "WAITING is honest when the stream is quiet or blocked; "
                "no fabricated liquidation fallback — LIVE requires normalized events"
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
