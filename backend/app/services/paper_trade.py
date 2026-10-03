"""Virtual paper trading — open on Path A (Trend+BOS) or Path B (full entry).

No real exchange orders. Uses setup signal risk sizing + live mark/ticker.
Default entry mode is path_a (research-validated COMBO_02 / HL longs).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from app.core.logging import get_logger
from app.services.paper_risk import PaperRiskPolicy, evaluate_paper_entry_risk, policy_from_settings
from app.signals.risk_engine import position_size
from app.signals.schemas import SignalStatus

logger = get_logger("paper_trade")

ENTRY_STATUSES = {
    SignalStatus.LONG_ENTRY_CANDIDATE.value,
    SignalStatus.ENTRY_CANDIDATE.value,
}

# Research Path A — Trend + BOS (aligned with LONG_STRATEGY §4.1 / COMBO_02)
PATH_A = "path_a"
PATH_B = "path_b"

# Do not open paper trades on stale / incomplete OHLCV tips
_STALE_OHLCV = frozenset({"TRAILING_STALE", "STALE", "WAITING", "UNAVAILABLE"})

# Long may open slightly below planned entry (spread), but not deep into stop risk
_MAX_ADVERSE_ENTRY_FRAC = 0.10

PersistFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class PaperPosition:
    id: str
    symbol: str
    side: str = "LONG"
    status: str = "OPEN"  # OPEN | CLOSED | CANCELLED
    entry_price: float = 0.0
    stop_price: float = 0.0
    tp1_price: float | None = None
    quantity: float = 0.0
    risk_usd: float = 0.0
    opened_at: str = ""
    closed_at: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl_usd: float | None = None
    r_multiple: float | None = None
    source_candle_ts: str | None = None
    timeframe: str = "15m"
    mark_price: float | None = None
    unrealized_pnl_usd: float | None = None
    unrealized_r: float | None = None
    signal_snippet: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperTradeEngine:
    def __init__(
        self,
        *,
        starting_equity: float = 1000.0,
        risk_percent: float = 0.02,
        enabled: bool = True,
        max_closed: int = 200,
        entry_mode: str = PATH_A,
        min_rr: float = 2.0,
        risk_policy: PaperRiskPolicy | None = None,
    ) -> None:
        self.starting_equity = float(starting_equity)
        self.risk_percent = float(risk_percent)
        self.enabled = bool(enabled)
        self.max_closed = max_closed
        mode = str(entry_mode or PATH_A).strip().lower()
        self.entry_mode = PATH_B if mode == PATH_B else PATH_A
        self.min_rr = float(min_rr)
        self.risk_policy = risk_policy or PaperRiskPolicy()
        self.realized_pnl = 0.0
        self._open: dict[str, PaperPosition] = {}
        self._closed: list[PaperPosition] = []
        self._opened_keys: set[str] = set()
        self._lock = threading.RLock()
        self._persist: PersistFn | None = None
        self._pending_persist: list[dict[str, Any]] = []
        self._last_skip_reason: str | None = None

    def set_persist(self, fn: PersistFn | None) -> None:
        self._persist = fn

    @property
    def equity(self) -> float:
        return self.starting_equity + self.realized_pnl

    def enable(self) -> None:
        self.enabled = True
        self.scan_cached_setups()

    def disable(self) -> None:
        self.enabled = False

    def scan_cached_setups(self) -> int:
        """Open Path A/B papers from already-cached setups (e.g. after mode switch)."""
        try:
            from app.services.engine_store import engine_store
        except Exception:  # noqa: BLE001
            return 0
        opened = 0
        for sym, payload in list(engine_store.setup_signals.items()):
            if not isinstance(payload, dict):
                continue
            if self.on_setup_signal(sym, payload) is not None:
                opened += 1
        if opened:
            logger.info("paper_scan_cached_opened", count=opened, mode=self.entry_mode)
        return opened

    def reset(self) -> None:
        with self._lock:
            self._open.clear()
            self._closed.clear()
            self._opened_keys.clear()
            self.realized_pnl = 0.0

    def hydrate_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        """Restore open/closed book from DB rows after restart (no re-persist)."""
        if not rows:
            return {"open": 0, "closed": 0}

        def _iso(v: Any) -> str | None:
            if v is None:
                return None
            if isinstance(v, datetime):
                if v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
                return v.isoformat()
            s = str(v).strip()
            return s or None

        def _f(v: Any) -> float | None:
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        closed_rows: list[PaperPosition] = []
        realized = 0.0
        keys: set[str] = set()

        with self._lock:
            # Only hydrate into an empty book — never clobber a live session
            if self._open or self._closed:
                return {
                    "open": len(self._open),
                    "closed": len(self._closed),
                    "skipped": 1,
                }

            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                sym = str(raw.get("symbol") or "").upper()
                pid = str(raw.get("id") or "").strip()
                if not sym or not pid:
                    continue
                status = str(raw.get("status") or "").upper()
                snippet = raw.get("signal_snippet") or {}
                if isinstance(snippet, str):
                    try:
                        import json

                        snippet = json.loads(snippet)
                    except Exception:  # noqa: BLE001
                        snippet = {}
                if not isinstance(snippet, dict):
                    snippet = {}

                pos = PaperPosition(
                    id=pid,
                    symbol=sym,
                    side=str(raw.get("side") or "LONG"),
                    status=status or "CLOSED",
                    entry_price=float(raw.get("entry_price") or 0),
                    stop_price=float(raw.get("stop_price") or 0),
                    tp1_price=_f(raw.get("tp1_price")),
                    quantity=float(raw.get("quantity") or 0),
                    risk_usd=float(raw.get("risk_usd") or 0),
                    opened_at=_iso(raw.get("opened_at")) or "",
                    closed_at=_iso(raw.get("closed_at")),
                    exit_price=_f(raw.get("exit_price")),
                    exit_reason=(
                        str(raw.get("exit_reason")) if raw.get("exit_reason") else None
                    ),
                    pnl_usd=_f(raw.get("pnl_usd")),
                    r_multiple=_f(raw.get("r_multiple")),
                    source_candle_ts=(
                        str(raw.get("source_candle_ts"))
                        if raw.get("source_candle_ts")
                        else None
                    ),
                    timeframe=str(raw.get("timeframe") or "15m"),
                    signal_snippet=snippet,
                )

                path = str(snippet.get("path") or "PATH_A")
                bos_level = snippet.get("bos_level")
                src = pos.source_candle_ts or pos.opened_at or ""
                keys.add(f"{sym}|{src}|{path}|{bos_level if bos_level is not None else ''}")

                if status == "OPEN":
                    # Newest first from SQL — keep first per symbol
                    if sym not in self._open:
                        self._open[sym] = pos
                else:
                    closed_rows.append(pos)
                    if pos.pnl_usd is not None and status == "CLOSED":
                        realized += float(pos.pnl_usd)

            # Newest closed first
            closed_rows.sort(key=lambda p: p.closed_at or p.opened_at or "", reverse=True)
            self._closed = closed_rows[: self.max_closed]
            self._opened_keys = keys
            self.realized_pnl = realized

        logger.info(
            "paper_hydrated_from_db",
            open=len(self._open),
            closed=len(self._closed),
            realized_pnl=round(realized, 4),
        )
        return {"open": len(self._open), "closed": len(self._closed)}

    def status(self) -> dict[str, Any]:
        with self._lock:
            open_risk = sum(p.risk_usd for p in self._open.values())
            return {
                "enabled": self.enabled,
                "paper_only": True,
                "disclaimer": "Paper only — not real orders",
                "starting_equity": self.starting_equity,
                "equity": round(self.equity, 4),
                "realized_pnl_usd": round(self.realized_pnl, 4),
                "open_count": len(self._open),
                "closed_count": len(self._closed),
                "open_risk_usd": round(open_risk, 4),
                "risk_percent": self.risk_percent,
                "entry_mode": self.entry_mode,
                "entry_mode_label": (
                    "Path A · Trend+BOS (research)"
                    if self.entry_mode == PATH_A
                    else "Path B · full LONG_ENTRY_CANDIDATE"
                ),
                "risk_policy": self.risk_policy.to_dict(),
                "last_skip_reason": self._last_skip_reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def positions(self, *, closed_limit: int = 50) -> dict[str, Any]:
        with self._lock:
            return {
                "open": [p.to_dict() for p in self._open.values()],
                "closed": [p.to_dict() for p in self._closed[:closed_limit]],
                "status": self.status(),
            }

    def on_setup_signal(self, symbol: str, payload: dict[str, Any] | None) -> PaperPosition | None:
        """Open a paper long when Path A (Trend+BOS) or Path B (full entry) fires."""
        if not self.enabled or not payload:
            return None
        sym = symbol.upper()
        status = str(payload.get("status") or "")
        if status == SignalStatus.INVALIDATED.value:
            self._cancel_open(sym, reason="SETUP_INVALIDATED")
            return None
        if status == SignalStatus.CONFLICT.value:
            return None

        path_b = status in ENTRY_STATUSES
        path_a = False
        path_label = "PATH_B"
        if path_b:
            path_label = "PATH_B"
        elif self.entry_mode == PATH_A:
            path_a = _is_path_a_long(payload)
            path_label = "PATH_A"
        else:
            return None

        if not path_b and not path_a:
            return None

        direction = str(payload.get("direction") or "").upper()
        if direction == "SHORT":
            return None
        if direction not in ("LONG", ""):
            # Path A may leave direction empty while BOS is bullish
            if not path_a:
                return None

        entry = payload.get("entry") or {}
        stop = payload.get("stop") or {}
        targets = payload.get("targets") or []
        risk = payload.get("risk_management") or {}
        bos = payload.get("bos") or {}
        setup_tf = str(payload.get("timeframe") or "15m")
        source_ts = (payload.get("source_candle_timestamps") or {}).get(setup_tf)
        bos_level = _f(bos.get("broken_level"))
        dedupe_key = (
            f"{sym}|{source_ts or payload.get('calculated_at')}|{path_label}|{bos_level or ''}"
        )

        entry_price = _f(entry.get("entry_price")) or _f(risk.get("entry"))
        if entry_price is None and bos_level is not None and bos_level > 0:
            # Path A: prefer retest of broken level when formal entry not set
            entry_price = bos_level
        if entry_price is None:
            entry_price = _live_price(sym)
        stop_price = _f(stop.get("final_stop")) or _f(risk.get("stop"))
        tp1 = None
        if targets:
            tp1 = _f(targets[0].get("target_price") if isinstance(targets[0], dict) else None)
        rr = payload.get("risk_reward") or {}
        rr_ok = str(rr.get("RISK_REWARD") or "").upper() == "PASS"

        if entry_price is None or stop_price is None or entry_price <= 0:
            logger.info("paper_skip_missing_prices", symbol=sym, path=path_label)
            return None
        if stop_price >= entry_price:
            logger.info("paper_skip_bad_stop", symbol=sym, entry=entry_price, stop=stop_price)
            return None

        freshness = str(payload.get("ohlcv_freshness") or "").upper()
        if freshness in _STALE_OHLCV:
            logger.info(
                "paper_skip_stale_ohlcv",
                symbol=sym,
                path=path_label,
                ohlcv_freshness=freshness,
            )
            return None

        live = _live_price(sym)
        if live is None or live <= 0:
            # Cannot validate against market — avoid opening into a ghost fill
            logger.info("paper_skip_no_live_price", symbol=sym, path=path_label)
            return None
        if live <= stop_price:
            logger.info(
                "paper_skip_already_stopped",
                symbol=sym,
                path=path_label,
                live=live,
                stop=stop_price,
                entry=entry_price,
            )
            return None
        risk_dist = entry_price - stop_price
        if risk_dist > 0 and live < entry_price:
            adverse_frac = (entry_price - live) / risk_dist
            if adverse_frac > _MAX_ADVERSE_ENTRY_FRAC:
                logger.info(
                    "paper_skip_missed_entry",
                    symbol=sym,
                    path=path_label,
                    live=live,
                    entry=entry_price,
                    stop=stop_price,
                    adverse_frac=round(adverse_frac, 3),
                )
                return None

        if tp1 is None:
            # Synthesize min_rr target when engine didn't attach TP1 yet
            tp1 = entry_price + self.min_rr * risk_dist
            rr_ok = True
        elif not rr_ok:
            # Enforce Path A min RR on provided TP1
            tp1_r = (tp1 - entry_price) / risk_dist if risk_dist > 0 else 0.0
            if tp1_r + 1e-9 < self.min_rr:
                logger.info(
                    "paper_skip_rr",
                    symbol=sym,
                    tp1_r=round(tp1_r, 3),
                    min_rr=self.min_rr,
                )
                return None

        with self._lock:
            open_count = len(self._open)
            open_risk = sum(p.risk_usd for p in self._open.values())
            if sym in self._open:
                return None
            if dedupe_key in self._opened_keys:
                return None

        gate = evaluate_paper_entry_risk(
            sym,
            policy=self.risk_policy,
            open_count=open_count,
            open_risk_usd=open_risk,
            equity=self.equity,
        )
        if not gate.ok:
            self._last_skip_reason = f"{sym}:{gate.reason}"
            logger.info(
                "paper_skip_risk_gate",
                symbol=sym,
                path=path_label,
                reason=gate.reason,
                group=gate.group,
                mcap=gate.mcap,
                quote_volume_24h=gate.quote_volume_24h,
            )
            return None

        # Tiered risk % (gates on) — never trust meme-sized setup qty blindly
        eff_risk_pct = (
            float(gate.risk_percent)
            if self.risk_policy.enabled
            else float(self.risk_percent)
        )
        sized = position_size(
            account_equity=self.equity,
            risk_percent=eff_risk_pct,
            entry=entry_price,
            stop=stop_price,
        )
        qty = float(sized.get("final_quantity") or 0.0)
        risk_usd = float(sized.get("max_risk_amount") or 0.0)
        if not self.risk_policy.enabled:
            # Legacy: allow setup-provided quantity when gates disabled
            setup_qty = _f(risk.get("final_quantity")) or 0.0
            if setup_qty > 0:
                qty = setup_qty
                risk_usd = abs(entry_price - stop_price) * qty

        if qty <= 0:
            logger.info("paper_skip_zero_qty", symbol=sym)
            return None

        # Re-check book headroom with planned risk
        gate2 = evaluate_paper_entry_risk(
            sym,
            policy=self.risk_policy,
            open_count=open_count,
            open_risk_usd=open_risk,
            equity=self.equity,
            planned_risk_usd=risk_usd,
        )
        if not gate2.ok:
            self._last_skip_reason = f"{sym}:{gate2.reason}"
            logger.info(
                "paper_skip_risk_gate",
                symbol=sym,
                path=path_label,
                reason=gate2.reason,
                group=gate2.group,
            )
            return None

        with self._lock:
            if sym in self._open:
                return None
            if dedupe_key in self._opened_keys:
                return None
            pos = PaperPosition(
                id=str(uuid.uuid4()),
                symbol=sym,
                side="LONG",
                status="OPEN",
                entry_price=entry_price,
                stop_price=stop_price,
                tp1_price=tp1,
                quantity=qty,
                risk_usd=risk_usd,
                opened_at=datetime.now(timezone.utc).isoformat(),
                source_candle_ts=str(source_ts) if source_ts else None,
                timeframe=setup_tf,
                signal_snippet={
                    "status": status,
                    "asset_group": gate.group,
                    "risk_percent": eff_risk_pct,
                    "mcap": gate.mcap,
                    "quote_volume_24h": gate.quote_volume_24h,
                    "path": path_label,
                    "direction": direction or "LONG",
                    "calculated_at": payload.get("calculated_at"),
                    "ohlcv_freshness": payload.get("ohlcv_freshness"),
                    "bos_level": bos_level,
                },
            )
            self._open[sym] = pos
            self._opened_keys.add(dedupe_key)
            self._queue_persist(pos)
            self._last_skip_reason = None
            logger.info(
                "paper_opened",
                symbol=sym,
                path=path_label,
                entry=entry_price,
                stop=stop_price,
                tp1=tp1,
                qty=qty,
                group=gate.group,
                risk_percent=eff_risk_pct,
                risk_usd=round(risk_usd, 4),
            )
            try:
                from app.services.alerts import get_alert_feed

                get_alert_feed().observe_paper_open(pos)
            except Exception:  # noqa: BLE001
                pass
            return pos

    def tick(self, prices: dict[str, float]) -> list[PaperPosition]:
        """Manage open positions against mark/last prices. Returns newly closed."""
        closed: list[PaperPosition] = []
        with self._lock:
            for sym, pos in list(self._open.items()):
                px = prices.get(sym)
                if px is None or px <= 0:
                    continue
                pos.mark_price = px
                risk_per = abs(pos.entry_price - pos.stop_price)
                if risk_per > 0:
                    pos.unrealized_pnl_usd = (px - pos.entry_price) * pos.quantity
                    pos.unrealized_r = (px - pos.entry_price) / risk_per
                # Long exits — paper stop/limit fills at the planned level (not gap mark)
                if px <= pos.stop_price:
                    closed.append(self._close_locked(pos, float(pos.stop_price), "STOP"))
                elif pos.tp1_price is not None and px >= pos.tp1_price:
                    closed.append(self._close_locked(pos, float(pos.tp1_price), "TP1"))
        return closed

    def _cancel_open(self, symbol: str, *, reason: str) -> PaperPosition | None:
        with self._lock:
            pos = self._open.get(symbol.upper())
            if not pos:
                return None
            pos.status = "CANCELLED"
            pos.closed_at = datetime.now(timezone.utc).isoformat()
            pos.exit_reason = reason
            pos.exit_price = pos.mark_price or pos.entry_price
            pos.pnl_usd = 0.0
            pos.r_multiple = 0.0
            self._open.pop(symbol.upper(), None)
            self._closed.insert(0, pos)
            self._trim_closed()
            self._queue_persist(pos)
            try:
                from app.services.alerts import get_alert_feed

                get_alert_feed().observe_paper_close(pos)
            except Exception:  # noqa: BLE001
                pass
            return pos

    def _close_locked(self, pos: PaperPosition, exit_price: float, reason: str) -> PaperPosition:
        risk_per = abs(pos.entry_price - pos.stop_price)
        pnl = (exit_price - pos.entry_price) * pos.quantity
        r_mult = (pnl / pos.risk_usd) if pos.risk_usd > 0 else (
            ((exit_price - pos.entry_price) / risk_per) if risk_per > 0 else 0.0
        )
        pos.status = "CLOSED"
        pos.closed_at = datetime.now(timezone.utc).isoformat()
        pos.exit_price = exit_price
        pos.exit_reason = reason
        pos.pnl_usd = pnl
        pos.r_multiple = r_mult
        pos.mark_price = exit_price
        pos.unrealized_pnl_usd = 0.0
        pos.unrealized_r = 0.0
        self._open.pop(pos.symbol, None)
        self.realized_pnl += pnl
        self._closed.insert(0, pos)
        self._trim_closed()
        self._queue_persist(pos)
        logger.info(
            "paper_closed",
            symbol=pos.symbol,
            reason=reason,
            exit=exit_price,
            pnl=round(pnl, 4),
            r=round(r_mult, 4),
        )
        try:
            from app.services.alerts import get_alert_feed

            get_alert_feed().observe_paper_close(pos)
        except Exception:  # noqa: BLE001
            pass
        return pos

    def _trim_closed(self) -> None:
        if len(self._closed) > self.max_closed:
            self._closed = self._closed[: self.max_closed]

    def _queue_persist(self, pos: PaperPosition) -> None:
        self._pending_persist.append(pos.to_dict())

    def drain_persist_queue(self) -> list[dict[str, Any]]:
        with self._lock:
            out = list(self._pending_persist)
            self._pending_persist.clear()
            return out


def _live_price(symbol: str) -> float | None:
    try:
        from app.services.market_store import market_store

        mark = market_store.mark_prices.get(symbol.upper())
        if mark is not None and getattr(mark, "mark_price", None):
            return float(mark.mark_price)
        tick = market_store.get_ticker(symbol)
        if tick is not None and tick.price:
            return float(tick.price)
    except Exception:  # noqa: BLE001
        return None
    return None


def _setup_trend(payload: dict[str, Any]) -> str:
    trend = payload.get("trend") or {}
    setup_tf = str(payload.get("timeframe") or "15m")
    if not isinstance(trend, dict):
        return str(trend or "")
    t = trend.get(setup_tf) or trend.get("15m") or {}
    if isinstance(t, dict):
        return str(t.get("trend") or "")
    return str(t or "")


def _is_path_a_long(payload: dict[str, Any]) -> bool:
    """Research Path A: setup BULLISH + confirmed BULLISH_BOS (+ stop present)."""
    if str(payload.get("status") or "") in (
        SignalStatus.CONFLICT.value,
        SignalStatus.INVALIDATED.value,
    ):
        return False
    bos = payload.get("bos") or {}
    bos_ok = (
        str(bos.get("state") or "").upper() == "CONFIRMED"
        and str(bos.get("direction") or "").upper() == "BULLISH_BOS"
    )
    trend_ok = _setup_trend(payload).upper() == "BULLISH"
    stop = payload.get("stop") or {}
    stop_ok = _f(stop.get("final_stop")) is not None or _f(
        (payload.get("risk_management") or {}).get("stop")
    ) is not None
    return bool(bos_ok and trend_ok and stop_ok)


def _f(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN check


_engine: PaperTradeEngine | None = None


def get_paper_trade_engine(
    *,
    starting_equity: float = 1000.0,
    risk_percent: float = 0.02,
    enabled: bool = True,
    entry_mode: str = PATH_A,
    risk_policy: PaperRiskPolicy | None = None,
) -> PaperTradeEngine:
    global _engine
    if _engine is None:
        _engine = PaperTradeEngine(
            starting_equity=starting_equity,
            risk_percent=risk_percent,
            enabled=enabled,
            entry_mode=entry_mode,
            risk_policy=risk_policy or policy_from_settings(),
        )
    else:
        if entry_mode:
            mode = str(entry_mode).strip().lower()
            _engine.entry_mode = PATH_B if mode == PATH_B else PATH_A
        if risk_policy is not None:
            _engine.risk_policy = risk_policy
    return _engine


def list_trade_opportunities(
    *,
    limit: int = 40,
    open_symbols: set[str] | None = None,
    include_waiting: bool = True,
) -> list[dict[str, Any]]:
    """Rank cached setup signals by closeness to a paper long entry.

    Tiers:
      READY    — Path A (Trend+BOS) or Path B LONG_ENTRY_CANDIDATE
      NEAR     — bullish pieces without stop/TP1/RR yet
      FORMING  — bullish structure pieces without full entry yet
      WATCH    — computed NO_SETUP / other non-entry (still visible)
      WAITING  — OHLCV/setup not ready yet
      BLOCKED  — CONFLICT / INVALIDATED
    """
    from app.services.engine_store import engine_store

    open_set = {s.upper() for s in (open_symbols or set())}
    mode = PATH_A
    try:
        mode = get_paper_trade_engine().entry_mode
    except Exception:  # noqa: BLE001
        mode = PATH_A
    rows: list[dict[str, Any]] = []
    for sym, payload in list(engine_store.setup_signals.items()):
        if not isinstance(payload, dict):
            continue
        row = _opportunity_from_payload(
            sym,
            payload,
            already_open=sym.upper() in open_set,
            include_waiting=include_waiting,
            entry_mode=mode,
        )
        if row is None:
            continue
        rows.append(row)

    tier_rank = {
        "READY": 0,
        "NEAR": 1,
        "FORMING": 2,
        "WATCH": 3,
        "WAITING": 4,
        "BLOCKED": 5,
    }
    rows.sort(
        key=lambda r: (
            tier_rank.get(str(r.get("tier")), 9),
            -int(r.get("pass_count") or 0),
            -float(r.get("score") or 0),
            str(r.get("symbol") or ""),
        )
    )
    return rows[: max(1, min(int(limit), 300))]


def _opportunity_from_payload(
    symbol: str,
    payload: dict[str, Any],
    *,
    already_open: bool,
    include_waiting: bool = True,
    entry_mode: str = PATH_A,
) -> dict[str, Any] | None:
    status = str(payload.get("status") or "")
    direction = str(payload.get("direction") or "").upper() or None
    trend = payload.get("trend") or {}
    setup_tf = str(payload.get("timeframe") or "15m")
    setup_trend = ""
    if isinstance(trend, dict):
        t = trend.get(setup_tf) or trend.get("15m") or {}
        if isinstance(t, dict):
            setup_trend = str(t.get("trend") or "")
        else:
            setup_trend = str(t or "")

    bos = payload.get("bos") or {}
    impulse = payload.get("impulse") or {}
    pullback = payload.get("pullback") or {}
    retest = payload.get("retest") or {}
    entry = payload.get("entry") or {}
    stop = payload.get("stop") or {}
    targets = payload.get("targets") or []
    rr = payload.get("risk_reward") or {}
    conditions = payload.get("conditions") or []
    deps = payload.get("data_dependencies") or {}

    bos_ok = (
        str(bos.get("state") or "").upper() == "CONFIRMED"
        and str(bos.get("direction") or "").upper() == "BULLISH_BOS"
    )
    trend_ok = setup_trend.upper() == "BULLISH"
    impulse_ok = bool(impulse.get("is_impulse")) or str(impulse.get("quality") or "").upper() in {
        "STRONG",
        "MODERATE",
    }
    pb_state = str(pullback.get("pullback_state") or "").upper()
    pullback_ok = pb_state in {"ACTIVE", "CONFIRMED"}
    retest_ok = bool(retest.get("confirmed")) or str(retest.get("state") or "").upper() == "CONFIRMED"
    stop_px = _f(stop.get("final_stop"))
    entry_px = _f(entry.get("entry_price"))
    tp1_px = None
    if targets and isinstance(targets[0], dict):
        tp1_px = _f(targets[0].get("target_price"))
    rr_ok = str(rr.get("RISK_REWARD") or "").upper() == "PASS"

    pass_count = 0
    missing: list[str] = []
    for label, ok in (
        ("trend", trend_ok),
        ("bos", bos_ok),
        ("impulse", impulse_ok),
        ("pullback", pullback_ok),
        ("retest", retest_ok),
        ("stop", stop_px is not None),
        ("tp1", tp1_px is not None),
        ("rr", rr_ok),
    ):
        if ok:
            pass_count += 1
        else:
            missing.append(label)

    cond_pass = 0
    for c in conditions:
        if isinstance(c, dict) and str(c.get("verdict") or "").upper() == "PASS":
            cond_pass += 1

    # Short-only candidates are not paper-traded yet — still list as WATCH/BLOCKED
    longish = direction in (None, "", "LONG")
    dep = str(deps.get(setup_tf) or deps.get("15m") or "")
    ohlcv_waiting = (
        status == "WAITING"
        and (
            "WAITING FOR OHLCV" in dep.upper()
            or str(payload.get("ohlcv_freshness") or "").upper() == "WAITING"
            or (
                not bos_ok
                and not trend_ok
                and not impulse_ok
                and entry_px is None
                and stop_px is None
            )
        )
    )

    path_a_ready = (
        entry_mode == PATH_A
        and longish
        and bos_ok
        and trend_ok
        and stop_px is not None
    )

    if status in ENTRY_STATUSES and longish:
        tier = "READY"
        chance = "Auto will open (LONG_ENTRY_CANDIDATE / Path B)"
    elif path_a_ready:
        tier = "READY"
        chance = "Auto will open (Path A: Trend+BOS)"
    elif status in ("CONFLICT", "INVALIDATED"):
        tier = "BLOCKED"
        chance = status + (f" — {payload.get('invalidation_reason')}" if payload.get("invalidation_reason") else "")
    elif longish and bos_ok and trend_ok and stop_px is not None:
        # Path B mode: still needs impulse/pullback/retest
        tier = "NEAR"
        chance = "Near entry — waiting: " + (", ".join(missing[:4]) or "confirmation")
    elif longish and (bos_ok or trend_ok or impulse_ok) and status in ("NO_SETUP", "WAITING"):
        tier = "FORMING"
        chance = "Forming structure — waiting: " + (", ".join(missing[:4]) or "more confirms")
    elif ohlcv_waiting:
        if not include_waiting:
            return None
        tier = "WAITING"
        chance = f"Not ready — {dep or 'WAITING FOR OHLCV / setup'}"
    elif status == "WAITING":
        # Computed WAITING without OHLCV block (conditions not met yet)
        tier = "WATCH"
        chance = "Setup WAITING — missing: " + (", ".join(missing[:4]) or "structure confirms")
    elif status and status not in ("",):
        # Any other computed setup (bearish, NO_SETUP without structure, etc.)
        tier = "WATCH"
        chance = f"Computed {status}" + (f" ({direction})" if direction else "")
        if missing:
            chance += " — missing: " + ", ".join(missing[:4])
    else:
        return None

    score = 0.0
    if isinstance(payload.get("score"), dict):
        try:
            score = float(payload["score"].get("total") or 0)
        except (TypeError, ValueError):
            score = 0.0
    score = score + pass_count * 10 + cond_pass * 2
    if tier == "READY":
        score += 50
    elif tier == "NEAR":
        score += 20
    elif tier == "FORMING":
        score += 8

    return {
        "symbol": symbol.upper(),
        "tier": tier,
        "chance": chance,
        "status": status or "UNKNOWN",
        "direction": direction or ("LONG" if longish else None),
        "timeframe": setup_tf,
        "setup_trend": setup_trend or None,
        "bos": bos.get("direction"),
        "bos_state": bos.get("state"),
        "impulse": impulse.get("quality") or ("YES" if impulse_ok else None),
        "pullback": pb_state or None,
        "retest": "YES" if retest_ok else (retest.get("state") if retest else None),
        "entry_price": entry_px,
        "stop_price": stop_px,
        "tp1_price": tp1_px,
        "rr_pass": rr_ok,
        "pass_count": pass_count,
        "missing": missing,
        "score": round(score, 2),
        "already_open": already_open,
        "ohlcv_freshness": payload.get("ohlcv_freshness"),
        "calculated_at": payload.get("calculated_at"),
        "source_candle_ts": (payload.get("source_candle_timestamps") or {}).get(setup_tf),
    }
