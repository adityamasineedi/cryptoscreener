"""Virtual paper trading — Path A (COMBO_02 v1) or Path B (experimental).

No real exchange orders. Uses setup signal risk sizing + live mark/ticker.

Default entry mode is path_a = COMBO_02 v1 long: setup BULLISH + confirmed
BULLISH_BOS + 4h/1h HTF hard gate (fail closed). Path B is a separate
experimental mode (full LONG_ENTRY_CANDIDATE via live entry-engine MTF) and
must not be labeled or claimed as COMBO_02 v1.
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

# Path A = COMBO_02 v1 (Trend + BOS + 4h/1h HTF). Path B = experimental only.
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
        v1_profile_enabled: bool = False,
        v1_universe_only: bool = True,
        v1_secondary_enabled: bool = True,
    ) -> None:
        self.starting_equity = float(starting_equity)
        self.risk_percent = float(risk_percent)
        self.enabled = bool(enabled)
        self.max_closed = max_closed
        mode = str(entry_mode or PATH_A).strip().lower()
        self.entry_mode = PATH_B if mode == PATH_B else PATH_A
        self.min_rr = float(min_rr)
        self.risk_policy = risk_policy or PaperRiskPolicy()
        # Off by default in unit tests; orchestrator enables via settings.
        self.v1_profile_enabled = bool(v1_profile_enabled)
        self.v1_universe_only = bool(v1_universe_only)
        self.v1_secondary_enabled = bool(v1_secondary_enabled)
        # When True, COMBO_02 v1 entries come only from V1PaperWatcher (1h combo eval).
        # Legacy 15m Path A on_setup_signal must not open/label v1 trades.
        self.v1_watcher_owns_entries = False
        # Legacy setup→paper auto-entry (default OFF — research only when explicitly enabled).
        self.legacy_auto_entry_enabled = False
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

                # Prefer open-time path label so dedupe survives LEGACY reclassification.
                path = str(
                    snippet.get("legacy_entry_mode")
                    or snippet.get("path")
                    or "PATH_A"
                )
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
                    "Path A · COMBO_02 v1 (HTF-gated)"
                    if self.entry_mode == PATH_A
                    else "Path B · experimental (not COMBO_02 v1)"
                ),
                "legacy_auto_entry_enabled": self.legacy_auto_entry_enabled,
                "v1_watcher_owns_entries": self.v1_watcher_owns_entries,
                "risk_policy": self.risk_policy.to_dict(),
                "v1_profile": {
                    "enabled": self.v1_profile_enabled,
                    "universe_only": self.v1_universe_only,
                    "secondary_enabled": self.v1_secondary_enabled,
                    "combo_version": "v1-combo02-long-htf",
                },
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
        """Open a paper long under the active entry mode.

        Path A (default, COMBO_02 v1): always requires ``_is_path_a_long``
        (setup BULLISH + confirmed BULLISH_BOS + 4h/1h HTF + stop). A live
        ``LONG_ENTRY_CANDIDATE`` status does **not** bypass the HTF gate.

        Path B (experimental): opens only on ``LONG_ENTRY_CANDIDATE`` /
        ``ENTRY_CANDIDATE`` via live entry-engine MTF — not COMBO_02 v1.
        """
        if not self.enabled or not payload:
            return None
        # Kill switch: screener/BOS/liq/UI alerts continue; only skip paper opens.
        if not self.legacy_auto_entry_enabled:
            self._last_skip_reason = f"{str(symbol).upper()}:legacy_auto_entry_disabled"
            return None
        # V1PaperWatcher owns COMBO_02 v1 entries — do not open from 15m screener Path A.
        if self.v1_watcher_owns_entries and self.entry_mode != PATH_B:
            self._last_skip_reason = f"{symbol.upper()}:v1_watcher_owns_path_a"
            return None
        sym = symbol.upper()
        status = str(payload.get("status") or "")
        # INVALIDATED / CONFLICT must not cancel an already-open paper fill.
        # After restart, setups often recompute as INVALIDATED while OHLCV is
        # still warming — that was wiping hydrated OPEN positions from the book.
        if status in (
            SignalStatus.INVALIDATED.value,
            SignalStatus.CONFLICT.value,
        ):
            return None

        path_a = False
        path_b = False
        if self.entry_mode == PATH_B:
            # Experimental only — not COMBO_02 v1 HTF-gated research path.
            if status not in ENTRY_STATUSES:
                return None
            path_b = True
            path_label = "PATH_B"
        else:
            # COMBO_02 v1: HTF hard gate always, even if status is ENTRY_CANDIDATE.
            if not _is_path_a_long(payload):
                return None
            path_a = True
            path_label = "PATH_A"

        if not path_b and not path_a:
            return None

        direction = str(payload.get("direction") or "").upper()
        if direction == "SHORT":
            return None
        if direction not in ("LONG", ""):
            # Path A may leave direction empty while BOS is bullish
            if not path_a:
                return None

        v1_tier = ""
        if path_a and self.v1_profile_enabled:
            from app.research.v1_production import paper_symbol_allowed

            allowed, tier_or_reason = paper_symbol_allowed(
                sym,
                secondary_enabled=self.v1_secondary_enabled,
                universe_only=self.v1_universe_only,
            )
            if not allowed:
                self._last_skip_reason = f"{sym}:v1_{tier_or_reason}"
                logger.info(
                    "paper_skip_v1_profile",
                    symbol=sym,
                    path=path_label,
                    reason=tier_or_reason,
                )
                return None
            v1_tier = tier_or_reason

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
        # v1 production profile overrides Path A sizing (BTC core / ETH·SOL secondary)
        if path_a and self.v1_profile_enabled:
            from app.research.v1_production import paper_risk_percent

            v1_pct = paper_risk_percent(
                sym,
                secondary_enabled=self.v1_secondary_enabled,
                fallback=eff_risk_pct,
            )
            if v1_pct > 0:
                eff_risk_pct = float(v1_pct)
        sized = position_size(
            account_equity=self.equity,
            risk_percent=eff_risk_pct,
            entry=entry_price,
            stop=stop_price,
        )
        qty = float(sized.get("final_quantity") or 0.0)
        risk_usd = float(sized.get("max_risk_amount") or 0.0)
        if not self.risk_policy.enabled and not (
            path_a and self.v1_profile_enabled
        ):
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

        trend_1h = _tf_trend_label(payload, "1h")
        trend_4h = _tf_trend_label(payload, "4h")
        mtf = payload.get("mtf") or {}
        htf_alignment = str(mtf.get("MTF_ALIGNMENT") or "").upper()
        if not htf_alignment:
            if trend_4h == "BULLISH" and trend_1h == "BULLISH":
                htf_alignment = "HTF_ALIGNED"
            elif trend_4h and trend_1h:
                htf_alignment = "HTF_CONFLICT"
            else:
                htf_alignment = "HTF_NEUTRAL_UNAVAILABLE"

        with self._lock:
            if sym in self._open:
                return None
            if dedupe_key in self._opened_keys:
                return None
            from app.services.paper_classification import classify_legacy_setup

            # Never stamp COMBO_02 v1 identity on legacy setup-signal opens.
            legacy_snip = classify_legacy_setup(
                symbol=sym,
                timeframe=setup_tf,
                path_b=path_b,
                extra={
                    "status": status,
                    "asset_group": gate.group,
                    "risk_percent": eff_risk_pct,
                    "mcap": gate.mcap,
                    "quote_volume_24h": gate.quote_volume_24h,
                    "v1_tier": None,
                    "direction": direction or "LONG",
                    "calculated_at": payload.get("calculated_at"),
                    "ohlcv_freshness": payload.get("ohlcv_freshness"),
                    "bos_level": bos_level,
                    "trend_1h": trend_1h or None,
                    "trend_4h": trend_4h or None,
                    "htf_alignment": htf_alignment,
                    "legacy_entry_mode": path_label,
                },
            )
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
                signal_snippet=legacy_snip,
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

    def open_v1_combo_position(
        self,
        *,
        symbol: str,
        timeframe: str,
        book: Any,
        eval_result: dict[str, Any],
        setup_bar_time_utc: str,
        replay: bool = False,
        emit_alert: bool = True,
    ) -> PaperPosition | None:
        """Open a COMBO_02 v1 paper long from combination-engine output.

        Used by ``V1PaperWatcher`` only. Reuses sizing / persist / alert machinery.
        Does not call Path A ``_is_path_a_long`` (15m screener path).
        """
        if not self.enabled and not replay:
            return None
        from app.research.v1_production import COMBO_ID, COMBO_VERSION
        from app.services.paper_classification import classify_v1_watcher

        sym = str(symbol or "").upper()
        tf = str(timeframe or "1h").lower()
        tier = str(getattr(book, "tier", "") or "core")
        risk_pct = float(getattr(book, "risk_percent", 0.0) or 0.0)
        if risk_pct <= 0:
            self._last_skip_reason = f"{sym}:v1_zero_risk"
            return None

        entry_price = _f(eval_result.get("entry_price"))
        stop_price = _f(eval_result.get("stop_price"))
        tp1 = _f(eval_result.get("tp1"))
        if tp1 is None:
            targets = eval_result.get("targets") or []
            if targets and isinstance(targets[0], dict):
                tp1 = _f(targets[0].get("target_price"))
        htf = eval_result.get("htf") or {}
        if entry_price is None or stop_price is None or entry_price <= 0:
            self._last_skip_reason = f"{sym}:v1_missing_prices"
            return None
        if stop_price >= entry_price:
            self._last_skip_reason = f"{sym}:v1_bad_stop"
            return None

        risk_dist = entry_price - stop_price
        if tp1 is None and risk_dist > 0:
            tp1 = entry_price + self.min_rr * risk_dist
        elif tp1 is not None and risk_dist > 0:
            tp1_r = (tp1 - entry_price) / risk_dist
            if tp1_r + 1e-9 < self.min_rr:
                self._last_skip_reason = f"{sym}:v1_rr"
                return None

        if not replay:
            live = _live_price(sym)
            if live is None or live <= 0:
                self._last_skip_reason = f"{sym}:v1_no_live_price"
                return None
            if live <= stop_price:
                self._last_skip_reason = f"{sym}:v1_already_stopped"
                return None
            if risk_dist > 0 and live < entry_price:
                adverse_frac = (entry_price - live) / risk_dist
                if adverse_frac > _MAX_ADVERSE_ENTRY_FRAC:
                    self._last_skip_reason = f"{sym}:v1_missed_entry"
                    return None

        dedupe_key = f"{sym}|{setup_bar_time_utc}|V1|{tf}"
        with self._lock:
            open_count = len(self._open)
            open_risk = sum(p.risk_usd for p in self._open.values())
            if sym in self._open:
                return None
            if dedupe_key in self._opened_keys:
                return None

        if not replay:
            gate = evaluate_paper_entry_risk(
                sym,
                policy=self.risk_policy,
                open_count=open_count,
                open_risk_usd=open_risk,
                equity=self.equity,
            )
            if not gate.ok:
                self._last_skip_reason = f"{sym}:{gate.reason}"
                return None
            group = gate.group
            mcap = gate.mcap
            qv = gate.quote_volume_24h
        else:
            group = "BTC" if sym == "BTCUSDT" else "ETH" if sym == "ETHUSDT" else "large-cap"
            mcap = None
            qv = None

        sized = position_size(
            account_equity=self.equity,
            risk_percent=risk_pct,
            entry=entry_price,
            stop=stop_price,
        )
        qty = float(sized.get("final_quantity") or 0.0)
        risk_usd = float(sized.get("max_risk_amount") or 0.0)
        if qty <= 0:
            self._last_skip_reason = f"{sym}:v1_zero_qty"
            return None

        if not replay:
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
                return None

        with self._lock:
            if sym in self._open:
                return None
            if dedupe_key in self._opened_keys:
                return None
            v1_snip = classify_v1_watcher(
                symbol=sym,
                timeframe=tf,
                combo_id=COMBO_ID,
                combo_version=COMBO_VERSION,
                path="A",
                htf_alignment=str(htf.get("htf_alignment") or "").upper() or None,
                trend_1h=str(htf.get("trend_1h") or "").upper() or None,
                trend_4h=str(htf.get("trend_4h") or "").upper() or None,
                v1_tier=tier,
                extra={
                    "setup_bar_time_utc": setup_bar_time_utc,
                    "bos_direction": "BULLISH_BOS",
                    "bos_state": "CONFIRMED",
                    "risk_percent": risk_pct,
                    "asset_group": group,
                    "mcap": mcap,
                    "quote_volume_24h": qv,
                    "status": str(eval_result.get("status") or ""),
                    "direction": "LONG",
                },
            )
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
                source_candle_ts=str(setup_bar_time_utc) if setup_bar_time_utc else None,
                timeframe=tf,
                signal_snippet=v1_snip,
            )
            self._open[sym] = pos
            self._opened_keys.add(dedupe_key)
            self._queue_persist(pos)
            self._last_skip_reason = None
            logger.info(
                "paper_opened_v1_watcher",
                symbol=sym,
                timeframe=tf,
                tier=tier,
                entry=entry_price,
                stop=stop_price,
                tp1=tp1,
                qty=qty,
                risk_percent=risk_pct,
                risk_usd=round(risk_usd, 4),
                setup_bar=setup_bar_time_utc,
                replay=bool(replay),
            )
            if emit_alert:
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

    def close_legacy_paper_positions(
        self,
        *,
        confirm: bool,
        reason: str = "legacy_cleanup",
    ) -> dict[str, Any]:
        """Operator action: archive open legacy/research paper positions.

        Requires explicit ``confirm=True``. Never touches COMBO_02_V1 watcher books.
        Does not auto-run — only when requested via API/UI.
        """
        if not confirm:
            return {
                "ok": False,
                "error": "confirmation_required",
                "closed": [],
                "skipped_v1": [],
                "message": "Pass confirm=true to close/archive legacy paper positions.",
            }
        from app.services.paper_classification import (
            STRATEGY_COMBO_02_V1,
            SOURCE_V1_PAPER_WATCHER,
            classification_fields_from_position,
            is_v1_classified,
        )

        closed: list[dict[str, Any]] = []
        skipped_v1: list[str] = []
        with self._lock:
            symbols = list(self._open.keys())
        for sym in symbols:
            pos = self._open.get(sym)
            if pos is None:
                continue
            fields = classification_fields_from_position(pos.to_dict())
            if is_v1_classified(fields) or (
                str(fields.get("strategy_id") or "") == STRATEGY_COMBO_02_V1
                and str(fields.get("source") or "") == SOURCE_V1_PAPER_WATCHER
            ):
                skipped_v1.append(sym)
                continue
            # Ensure archived rows stay research-classified
            snip = dict(pos.signal_snippet or {})
            snip.setdefault("strategy_id", fields.get("strategy_id"))
            snip.setdefault("source", fields.get("source"))
            snip["telegram_eligible"] = False
            snip["archive_reason"] = reason
            pos.signal_snippet = snip
            cancelled = self._cancel_open(sym, reason=reason)
            if cancelled:
                closed.append(cancelled.to_dict())
        logger.info(
            "paper_legacy_cleanup",
            reason=reason,
            closed=len(closed),
            skipped_v1=len(skipped_v1),
        )
        return {
            "ok": True,
            "reason": reason,
            "closed_count": len(closed),
            "closed": closed,
            "skipped_v1": skipped_v1,
        }

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

    def pending_persist_count(self) -> int:
        with self._lock:
            return len(self._pending_persist)


async def flush_paper_trade_persists(
    engine: PaperTradeEngine | None = None,
) -> int:
    """Write queued paper rows to Postgres immediately (open/close durability)."""
    from app.services.persistence import persistence

    eng = engine if engine is not None else get_paper_trade_engine()
    rows = eng.drain_persist_queue()
    for row in rows:
        await persistence.persist_paper_trade(row)
    return len(rows)


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


def _tf_trend_label(payload: dict[str, Any], timeframe: str) -> str:
    """Normalize trend label for a TF from payload.trend (dict or str)."""
    trend = payload.get("trend") or {}
    if not isinstance(trend, dict):
        return ""
    raw = trend.get(timeframe) or trend.get(timeframe.lower())
    if isinstance(raw, dict):
        return str(raw.get("trend") or "").upper()
    return str(raw or "").upper()


def _htf_bullish_for_long(payload: dict[str, Any]) -> bool:
    """Hard Path A HTF gate: 4h and 1h must both be BULLISH (fail closed)."""
    mtf = payload.get("mtf") or {}
    align = str(mtf.get("MTF_ALIGNMENT") or "").upper()
    if align == "STRONG_LONG":
        return True
    t4 = _tf_trend_label(payload, "4h")
    t1 = _tf_trend_label(payload, "1h")
    return t4 == "BULLISH" and t1 == "BULLISH"


def _is_path_a_long(payload: dict[str, Any]) -> bool:
    """Path A = COMBO_02: setup BULLISH + BULLISH_BOS + 4h/1h HTF (+ stop)."""
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
    htf_ok = _htf_bullish_for_long(payload)
    stop = payload.get("stop") or {}
    stop_ok = _f(stop.get("final_stop")) is not None or _f(
        (payload.get("risk_management") or {}).get("stop")
    ) is not None
    return bool(bos_ok and trend_ok and htf_ok and stop_ok)


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
    v1_profile_enabled: bool | None = None,
    v1_universe_only: bool | None = None,
    v1_secondary_enabled: bool | None = None,
) -> PaperTradeEngine:
    global _engine

    def _v1_flags() -> tuple[bool, bool, bool]:
        try:
            from app.config import get_settings

            s = get_settings()
            return (
                bool(getattr(s, "paper_v1_profile_enabled", True)),
                bool(getattr(s, "paper_v1_universe_only", True)),
                bool(getattr(s, "paper_v1_secondary_enabled", True)),
            )
        except Exception:  # noqa: BLE001
            return True, True, True

    settings_v1, settings_universe, settings_secondary = _v1_flags()
    use_v1 = (
        settings_v1 if v1_profile_enabled is None else bool(v1_profile_enabled)
    )
    use_universe = (
        settings_universe if v1_universe_only is None else bool(v1_universe_only)
    )
    use_secondary = (
        settings_secondary
        if v1_secondary_enabled is None
        else bool(v1_secondary_enabled)
    )

    if _engine is None:
        _engine = PaperTradeEngine(
            starting_equity=starting_equity,
            risk_percent=risk_percent,
            enabled=enabled,
            entry_mode=entry_mode,
            risk_policy=risk_policy or policy_from_settings(),
            v1_profile_enabled=use_v1,
            v1_universe_only=use_universe,
            v1_secondary_enabled=use_secondary,
        )
    else:
        if entry_mode:
            mode = str(entry_mode).strip().lower()
            _engine.entry_mode = PATH_B if mode == PATH_B else PATH_A
        if risk_policy is not None:
            _engine.risk_policy = risk_policy
        if v1_profile_enabled is not None:
            _engine.v1_profile_enabled = bool(v1_profile_enabled)
        if v1_universe_only is not None:
            _engine.v1_universe_only = bool(v1_universe_only)
        if v1_secondary_enabled is not None:
            _engine.v1_secondary_enabled = bool(v1_secondary_enabled)
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
        and _htf_bullish_for_long(payload)
        and stop_px is not None
    )

    if status in ENTRY_STATUSES and longish and entry_mode == PATH_B:
        tier = "READY"
        chance = "Auto will open (Path B experimental — not COMBO_02 v1)"
    elif path_a_ready:
        tier = "READY"
        chance = "Auto will open (Path A · COMBO_02 v1: Trend+BOS+HTF)"
    elif status in ("CONFLICT", "INVALIDATED"):
        tier = "BLOCKED"
        chance = status + (f" — {payload.get('invalidation_reason')}" if payload.get("invalidation_reason") else "")
    elif longish and bos_ok and trend_ok and stop_px is not None:
        if entry_mode == PATH_A and not _htf_bullish_for_long(payload):
            tier = "NEAR"
            chance = "Path A blocked — waiting for 4h+1h HTF_ALIGNED"
        else:
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
