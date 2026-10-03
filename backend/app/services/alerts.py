"""Live alert feed — thin projection over setup / paper / liquidation transitions.

Does not invent signals. First write per symbol seeds baseline without emitting
(so DB hydrate does not flood the feed).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

import structlog

logger = structlog.get_logger(__name__)

AlertType = Literal[
    "BOS",
    "SETUP_STATUS",
    "MARKET_SIGNAL",
    "PAPER_ENTRY",
    "PAPER_EXIT",
    "LIQ_SPIKE",
]
Severity = Literal["info", "watch", "action"]

Subscriber = Callable[[dict[str, Any]], Awaitable[None] | None]

ACTION_STATUSES = {
    "ENTRY_CANDIDATE",
    "LONG_ENTRY_CANDIDATE",
    "SHORT_ENTRY_CANDIDATE",
    "ENTRY_READY",
}
DIRECTIONAL_MARKETS = {
    "BUY",
    "SELL",
    "STRONG_BUY",
    "STRONG_SELL",
}
# Ignore micro force-orders that trip spike math when the 15m baseline is ~0
MIN_LIQ_SPIKE_NOTIONAL = 25_000.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bos_parts(payload: dict[str, Any] | None) -> tuple[str, str]:
    bos = (payload or {}).get("bos") or {}
    if not isinstance(bos, dict):
        return "", ""
    return str(bos.get("state") or "").upper(), str(bos.get("direction") or "").upper()


def _market_signal(payload: dict[str, Any] | None) -> str:
    ms = (payload or {}).get("market_signal")
    if isinstance(ms, dict):
        return str(ms.get("signal") or ms.get("value") or "").upper()
    return str(ms or "").upper()


class AlertFeed:
    """In-memory ring buffer + fan-out for live alerts."""

    def __init__(self, *, maxlen: int = 400) -> None:
        self._lock = threading.Lock()
        self.maxlen = int(maxlen)
        self._alerts: deque[dict[str, Any]] = deque(maxlen=self.maxlen)
        self._setup_prev: dict[str, dict[str, Any]] = {}
        self._liq_spike_on: dict[str, bool] = {}
        self._subscribers: list[Subscriber] = []
        self._seq = 0

    def subscribe(self, callback: Subscriber) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        self._subscribers = [c for c in self._subscribers if c is not callback]

    def emit(
        self,
        *,
        alert_type: AlertType,
        symbol: str,
        title: str,
        detail: str = "",
        severity: Severity = "info",
        timeframe: str | None = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any] | None:
        sym = symbol.upper()
        with self._lock:
            if dedupe_key:
                # Skip if an identical key is already the newest matching alert
                for a in self._alerts:
                    if a.get("dedupe_key") == dedupe_key:
                        return None
            self._seq += 1
            alert = {
                "id": str(uuid.uuid4()),
                "seq": self._seq,
                "time": _now_iso(),
                "type": alert_type,
                "symbol": sym,
                "timeframe": timeframe,
                "severity": severity,
                "title": title,
                "detail": detail,
                "payload": payload or {},
                "dedupe_key": dedupe_key,
            }
            self._alerts.appendleft(alert)
        self._fanout(alert)
        self._schedule_persist(alert)
        return alert

    def snapshot_state(self) -> dict[str, Any]:
        """Full durable snapshot (alerts + transition baselines)."""
        with self._lock:
            return {
                "rows": list(self._alerts),
                "latest_seq": self._seq,
                "setup_prev": dict(self._setup_prev),
                "liq_spike_on": dict(self._liq_spike_on),
                "timestamp": _now_iso(),
            }

    def hydrate_from_snapshot(self, snap: dict[str, Any] | None) -> int:
        """Restore ring buffer + baselines after restart. No fanout."""
        if not snap or not isinstance(snap, dict):
            return 0
        rows = snap.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        with self._lock:
            if self._alerts:
                return 0  # never clobber a live feed
            restored: list[dict[str, Any]] = []
            for a in rows:
                if isinstance(a, dict) and a.get("id"):
                    restored.append(a)
            # Newest first (same as emit order)
            restored.sort(key=lambda a: int(a.get("seq") or 0), reverse=True)
            self._alerts = deque(restored[: self.maxlen], maxlen=self.maxlen)
            max_seq = max((int(a.get("seq") or 0) for a in restored), default=0)
            self._seq = max(self._seq, int(snap.get("latest_seq") or 0), max_seq)
            prev = snap.get("setup_prev")
            if isinstance(prev, dict):
                self._setup_prev = {
                    str(k).upper(): v for k, v in prev.items() if isinstance(v, dict)
                }
            spikes = snap.get("liq_spike_on")
            if isinstance(spikes, dict):
                self._liq_spike_on = {
                    str(k).upper(): bool(v) for k, v in spikes.items()
                }
        n = len(self._alerts)
        if n:
            logger.info("alerts_hydrated", count=n, latest_seq=self._seq)
        return n

    def _schedule_persist(self, alert: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._persist_async(alert), name="alert_persist")

    async def _persist_async(self, alert: dict[str, Any]) -> None:
        try:
            from app.services.persistence import persistence
            from app.services.redis_state import redis_state

            await persistence.persist_alert(alert)
            await redis_state.store_alerts_state(self.snapshot_state())
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert_persist_failed", error=str(exc))

    def list(
        self,
        *,
        limit: int = 100,
        types: list[str] | None = None,
        symbol: str | None = None,
        since_seq: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            rows = list(self._alerts)
        if types:
            want = {t.upper() for t in types}
            rows = [a for a in rows if str(a.get("type") or "").upper() in want]
        if symbol:
            sym = symbol.upper()
            rows = [a for a in rows if a.get("symbol") == sym]
        if since_seq is not None:
            rows = [a for a in rows if int(a.get("seq") or 0) > since_seq]
            # chronological for incremental poll
            rows = list(reversed(rows))
        else:
            rows = rows[: max(1, min(limit, 500))]
        return {
            "rows": rows[: max(1, min(limit, 500))],
            "count": len(rows[: max(1, min(limit, 500))]),
            "total_buffered": len(self._alerts),
            "latest_seq": self._seq,
            "timestamp": _now_iso(),
        }

    def observe_setup_signal(self, symbol: str, payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Compare against prior setup payload; emit transition alerts only."""
        if not payload or not isinstance(payload, dict):
            return []
        sym = symbol.upper()
        status = str(payload.get("status") or "").upper()
        signal_status = str(payload.get("signal_status") or "").upper()
        # Ignore WAITING placeholders so they don't wipe the last live baseline
        if status in ("", "WAITING") or signal_status == "WAITING":
            return []

        bos_state, bos_dir = _bos_parts(payload)
        market = _market_signal(payload)
        tf = str(payload.get("timeframe") or payload.get("triggered_timeframe") or "")
        calc_at = str(payload.get("calculated_at") or "")

        with self._lock:
            prev = self._setup_prev.get(sym)
            self._setup_prev[sym] = {
                "status": status,
                "bos_state": bos_state,
                "bos_dir": bos_dir,
                "market": market,
                "tf": tf,
            }

        if prev is None:
            # Seed baseline (hydrate / first compute) — no emit
            return []

        emitted: list[dict[str, Any]] = []

        if (
            bos_state == "CONFIRMED"
            and bos_dir in ("BULLISH_BOS", "BEARISH_BOS")
            and (prev.get("bos_state") != "CONFIRMED" or prev.get("bos_dir") != bos_dir)
        ):
            sev: Severity = "action" if bos_dir == "BULLISH_BOS" else "watch"
            a = self.emit(
                alert_type="BOS",
                symbol=sym,
                timeframe=tf or None,
                severity=sev,
                title=f"{bos_dir.replace('_', ' ')} confirmed",
                detail=f"Setup TF {tf or '—'}",
                payload={
                    "bos_state": bos_state,
                    "bos_direction": bos_dir,
                    "broken_level": (payload.get("bos") or {}).get("broken_level"),
                    "status": status,
                },
                dedupe_key=f"bos|{sym}|{bos_dir}|{calc_at}",
            )
            if a:
                emitted.append(a)

        if status in ACTION_STATUSES and prev.get("status") != status:
            a = self.emit(
                alert_type="SETUP_STATUS",
                symbol=sym,
                timeframe=tf or None,
                severity="action",
                title=status.replace("_", " "),
                detail=f"Market {market or '—'}",
                payload={
                    "status": status,
                    "direction": payload.get("direction"),
                    "entry": payload.get("entry"),
                    "stop": payload.get("stop"),
                },
                dedupe_key=f"setup|{sym}|{status}|{calc_at}",
            )
            if a:
                emitted.append(a)
        elif status == "INVALIDATED" and prev.get("status") != status:
            a = self.emit(
                alert_type="SETUP_STATUS",
                symbol=sym,
                timeframe=tf or None,
                severity="watch",
                title="Setup invalidated",
                detail="",
                payload={"status": status},
                dedupe_key=f"setup|{sym}|INVALIDATED|{calc_at}",
            )
            if a:
                emitted.append(a)

        if market in DIRECTIONAL_MARKETS and prev.get("market") != market:
            a = self.emit(
                alert_type="MARKET_SIGNAL",
                symbol=sym,
                timeframe=tf or None,
                severity="watch" if market in ("BUY", "SELL") else "action",
                title=f"Market signal {market.replace('_', ' ')}",
                detail=f"Setup {status or '—'}",
                payload={"market_signal": market, "status": status},
                dedupe_key=f"mkt|{sym}|{market}|{calc_at}",
            )
            if a:
                emitted.append(a)

        return emitted

    def observe_paper_open(self, pos: Any) -> dict[str, Any] | None:
        d = pos.to_dict() if hasattr(pos, "to_dict") else dict(pos)
        path = ((d.get("signal_snippet") or {}) or {}).get("path") or "PAPER"
        return self.emit(
            alert_type="PAPER_ENTRY",
            symbol=str(d.get("symbol") or ""),
            timeframe=str(d.get("timeframe") or "") or None,
            severity="action",
            title=f"Paper {d.get('side') or 'LONG'} opened ({path})",
            detail=(
                f"Entry {d.get('entry_price')} · stop {d.get('stop_price')}"
                + (f" · TP1 {d.get('tp1_price')}" if d.get("tp1_price") is not None else "")
            ),
            payload=d,
            dedupe_key=f"paper_open|{d.get('id')}",
        )

    def observe_paper_close(self, pos: Any) -> dict[str, Any] | None:
        d = pos.to_dict() if hasattr(pos, "to_dict") else dict(pos)
        reason = str(d.get("exit_reason") or "EXIT")
        pnl = d.get("pnl_usd")
        r = d.get("r_multiple")
        sev: Severity = "info"
        if reason == "TP1":
            sev = "action"
        elif reason == "STOP":
            sev = "watch"
        return self.emit(
            alert_type="PAPER_EXIT",
            symbol=str(d.get("symbol") or ""),
            timeframe=str(d.get("timeframe") or "") or None,
            severity=sev,
            title=f"Paper closed · {reason}",
            detail=f"PnL {pnl} · R {r}",
            payload=d,
            dedupe_key=f"paper_close|{d.get('id')}|{reason}",
        )

    def observe_liquidation_spike(
        self,
        symbol: str,
        *,
        is_spike: bool,
        side: str | None = None,
        notional_5m: float | None = None,
        price: float | None = None,
    ) -> dict[str, Any] | None:
        sym = symbol.upper()
        notional = float(notional_5m or 0.0)
        meaningful = bool(is_spike) and notional >= MIN_LIQ_SPIKE_NOTIONAL
        with self._lock:
            was = self._liq_spike_on.get(sym, False)
            self._liq_spike_on[sym] = meaningful
        if not meaningful or was:
            return None
        side_u = str(side or "").upper()
        # Binance: SELL = long liquidated, BUY = short liquidated
        who = "longs" if side_u == "SELL" else "shorts" if side_u == "BUY" else "flow"
        # Edge detection (_liq_spike_on) is the dedupe — allow re-alert after spike clears
        return self.emit(
            alert_type="LIQ_SPIKE",
            symbol=sym,
            severity="action",
            title=f"Liquidation spike ({who})",
            detail=f"5m notional {notional:,.0f}",
            payload={
                "side": side_u,
                "notional_5m": notional,
                "price": price,
            },
        )

    def _fanout(self, alert: dict[str, Any]) -> None:
        dead: list[Subscriber] = []
        for sub in list(self._subscribers):
            try:
                result = sub(alert)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(result)
                    except RuntimeError:
                        # No running loop (sync test / worker thread) — skip async fanout
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("alert_subscriber_failed", error=str(exc))
                dead.append(sub)
        for d in dead:
            self.unsubscribe(d)


alert_feed = AlertFeed()


def get_alert_feed() -> AlertFeed:
    return alert_feed
