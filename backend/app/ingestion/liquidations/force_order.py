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

from app.ingestion.ws_manager import WebSocketManager

from app.models.schemas import FreshValue



logger = get_logger("liquidations")





@dataclass

class SymbolLiquidationState:

    symbol: str

    events: deque[LiquidationEvent] = field(default_factory=lambda: deque(maxlen=5000))

    summary: FreshValue[str] = field(default_factory=lambda: FreshValue.waiting("binance_ws"))

    last_event_at: datetime | None = None





class LiquidationIngestion:

    """Binance !forceOrder@arr — real events only; WAITING until data arrives."""



    STREAM_NAME = "binance_force_order"



    def __init__(

        self,

        settings: Settings,

        ws_manager: WebSocketManager,

        *,

        volume_lookup: Any | None = None,

    ) -> None:

        self.settings = settings

        self.ws_manager = ws_manager

        self.volume_lookup = volume_lookup

        self._by_symbol: dict[str, SymbolLiquidationState] = {}

        self._global_events: deque[LiquidationEvent] = deque(maxlen=10000)

        self._started = False

        self._raw_messages = 0

        self._parser_errors = 0

        self._last_raw: Any | None = None

        self._last_raw_at: datetime | None = None

        self._last_parsed_at: datetime | None = None

        self._connect_url: str | None = None



    def _state(self, symbol: str) -> SymbolLiquidationState:

        if symbol not in self._by_symbol:

            self._by_symbol[symbol] = SymbolLiquidationState(symbol=symbol)

        return self._by_symbol[symbol]



    async def start(self) -> None:

        if self._started:

            return

        base = self.settings.binance_futures_ws.rstrip("/")

        url = f"{base}/ws/!forceOrder@arr"

        self._connect_url = url

        await self.ws_manager.ensure(

            name=self.STREAM_NAME,

            url=url,

            handler=self._on_message,

        )

        self._started = True

        logger.info("liquidations_stream_started", url=url)



    async def stop(self) -> None:

        self._started = False



    def _unwrap(self, data: Any) -> Any:

        """Handle combined-stream envelopes and nested forceOrder payloads."""

        if isinstance(data, dict) and "data" in data and isinstance(data["data"], (dict, list)):

            return data["data"]

        return data



    async def _on_message(self, data: dict[str, Any]) -> None:

        self._raw_messages += 1

        self._last_raw_at = datetime.now(timezone.utc)

        try:

            # Keep a compact diagnostic snapshot (not full flood)

            if isinstance(data, dict):

                self._last_raw = {

                    k: data[k]

                    for k in list(data.keys())[:8]

                }

            else:

                self._last_raw = str(type(data))

        except Exception:  # noqa: BLE001

            self._last_raw = None



        payload = self._unwrap(data)

        if isinstance(payload, list):

            for item in payload:

                await self._ingest_one(item if isinstance(item, dict) else {})

            return

        if isinstance(payload, dict):

            await self._ingest_one(payload)



    async def _ingest_one(self, payload: dict[str, Any]) -> None:

        # Nested unwrap for {e:forceOrder,o:{...}}

        ev = parse_force_order(payload)

        if ev is None:

            # Ignore control/ping-like frames that aren't force orders

            if payload.get("e") not in (None, "forceOrder") and "s" not in payload:

                return

            if payload:

                self._parser_errors += 1

            return

        st = self._state(ev.symbol)

        st.events.append(ev)

        st.last_event_at = ev.timestamp

        self._global_events.append(ev)

        self._last_parsed_at = ev.timestamp

        st.summary = FreshValue.live(self._describe(st), "binance_ws")

        try:

            from app.services.persistence import persistence



            await persistence.persist_liquidation(

                symbol=ev.symbol,

                side=ev.side,

                price=ev.price,

                quantity=ev.quantity,

                quote_qty=getattr(ev, "notional", None),

                source="binance_ws",

                ts=ev.timestamp,

            )

        except Exception:  # noqa: BLE001

            pass



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

            return FreshValue.waiting("binance_ws")

        return st.summary



    def aggregates(self, symbol: str) -> dict[str, dict[str, float]]:

        st = self._by_symbol.get(symbol)

        if st is None:

            return {}

        return aggregate_windows(st.events)



    def status(self) -> dict[str, Any]:

        live = sum(1 for s in self._by_symbol.values() if s.events)

        return {

            "symbols_with_events": live,

            "total_events": len(self._global_events),

            "stream": self.STREAM_NAME,

            "started": self._started,

            "checked_at": datetime.now(timezone.utc).isoformat(),

        }



    def diagnostic(self) -> dict[str, Any]:
        conn = None
        for c in self.ws_manager.status():
            if c.get("name") == self.STREAM_NAME:
                conn = c
                break

        dns_ok: bool | None = None
        tls_ok: bool | None = None
        dns_error: str | None = None
        host = None
        try:
            from urllib.parse import urlparse
            import socket
            import ssl

            parsed = urlparse(self._connect_url or "")
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

        events = len(self._global_events)
        # Never convert quiet stream into zeros — remain WAITING
        liquidation_status = "LIVE" if events > 0 else "WAITING"

        return {
            "connection_status": (
                "CONNECTED"
                if conn and conn.get("connected")
                else ("STARTED" if self._started else "STOPPED")
            ),
            "liquidation_status": liquidation_status,
            "url": self._connect_url,
            "host": host,
            "dns_resolution": {"ok": dns_ok, "error": dns_error},
            "tls_connection": {"ok": tls_ok},
            "stream_handshake": {
                "ok": conn.get("last_handshake_ok") if conn else None,
                "connected": bool(conn and conn.get("connected")),
            },
            "bytes_received": conn.get("bytes_received") if conn else 0,
            "last_frame_bytes": conn.get("last_frame_bytes") if conn else None,
            "last_frame": self._last_raw,
            "connection_age_seconds": conn.get("connection_age_seconds") if conn else None,
            "reconnect_count": conn.get("reconnect_count") if conn else 0,
            "ws": conn,
            "last_message_time": self._last_raw_at.isoformat() if self._last_raw_at else None,
            "last_parsed_time": (
                self._last_parsed_at.isoformat() if self._last_parsed_at else None
            ),
            "raw_messages": self._raw_messages,
            "symbols_seen": len(self._by_symbol),
            "events_seen": events,
            "parser_errors": self._parser_errors,
            "note": (
                "WAITING is honest when the stream is quiet or blocked; "
                "no fabricated liquidation fallback — never convert WAITING to zero"
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

