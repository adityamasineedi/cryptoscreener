"""Account-level kill switches for live paper (owner ruling D128d). Behind KILL_SWITCHES_ENABLED, default OFF.

Paper only today (C1 v2.5: no real orders, no real money). The same object is the hook the execution adapter's
reconciliation loop calls on a position mismatch (D128c).

With the flag off, nothing here is created: the engine's ``kill_switch`` stays None, no opener calls into this
module, no monitor task runs, and the flatten endpoint refuses. Live decisions are unchanged (replay test
``test_kill_switch_flag_off_replay.py``).

With the flag on:

* **Entry checks** (every paper opener, after the existing risk gate, never in replay mode):
  - ``daily_loss_limit``: account P&L of the current UTC day (realized closes stamped today plus the open
    positions' unrealized P&L) at or below ``-daily_loss_limit_pct`` of the start-of-day equity. Latches until the
    next 00:00 UTC.
  - ``max_open_risk``: open risk plus the new trade's risk above ``max_open_risk_pct`` of equity (account-wide,
    every book together).
  - ``max_position_notional``: the new position's notional above ``max_position_notional_usd`` (0 = off) or above
    ``max_position_notional_pct`` of equity.
  - any latched trip (heartbeat, mismatch, manual flatten) blocks every new entry until the owner clears it.
* **Flatten all** (manual: POST /api/paper/kill-switch/flatten-all, or scripts/ops/flatten_all.py): closes every
  open paper position at the current price, books it in the ledger with exit_reason ``KILL_SWITCH_FLATTEN``, and
  latches ``manual_flatten``.
* **Auto-flatten**:
  - ``heartbeat``: no closed-bar processing for ``heartbeat_timeout_minutes``. The heartbeat is the newest
    closed-bar evaluation time reported by the registered sources (the passed-book watcher's last evaluated bar
    and the COMBO_02 v1 watcher's last evaluation). An in-process check: it covers a stalled feed or a hung loop,
    not a dead process. For a dead process the exchange-side stops (D128c) and the external monitor are the
    protection.
  - ``mismatch``: ``on_position_mismatch`` is called by a reconciliation loop (D128c adapter) when the exchange
    position and the ledger disagree.

Every trip, clear, block and flatten is appended to ``data/paper/kill_switch_journal.jsonl`` (write-only).
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger("kill_switch")

EXIT_REASON_FLATTEN = "KILL_SWITCH_FLATTEN"
REASON_PREFIX = "kill_switch"

TRIP_DAILY_LOSS = "daily_loss_limit"
TRIP_HEARTBEAT = "heartbeat"
TRIP_MISMATCH = "mismatch"
TRIP_MANUAL = "manual_flatten"
LATCHING_TRIPS = (TRIP_HEARTBEAT, TRIP_MISMATCH, TRIP_MANUAL)

HeartbeatSource = Callable[[], "datetime | None"]
PriceFn = Callable[[str], "float | None"]


@dataclass(frozen=True)
class KillSwitchConfig:
    enabled: bool = False
    daily_loss_limit_pct: float = 0.05
    max_open_risk_pct: float = 0.10
    max_position_notional_usd: float = 0.0
    max_position_notional_pct: float = 2.0
    heartbeat_timeout_minutes: float = 90.0
    auto_flatten_on_heartbeat: bool = True
    auto_flatten_on_mismatch: bool = True
    # A heartbeat check needs a first heartbeat; until one arrives the age counts from this process's start.
    heartbeat_grace_minutes: float = 90.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_from_settings(settings: Any) -> KillSwitchConfig:
    g = lambda name, default: getattr(settings, name, default)  # noqa: E731
    return KillSwitchConfig(
        enabled=bool(g("kill_switches_enabled", False)),
        daily_loss_limit_pct=float(g("kill_switch_daily_loss_limit_pct", 0.05)),
        max_open_risk_pct=float(g("kill_switch_max_open_risk_pct", 0.10)),
        max_position_notional_usd=float(g("kill_switch_max_position_notional_usd", 0.0)),
        max_position_notional_pct=float(g("kill_switch_max_position_notional_pct", 2.0)),
        heartbeat_timeout_minutes=float(g("kill_switch_heartbeat_timeout_minutes", 90.0)),
        auto_flatten_on_heartbeat=bool(g("kill_switch_auto_flatten_on_heartbeat", True)),
        auto_flatten_on_mismatch=bool(g("kill_switch_auto_flatten_on_mismatch", True)),
    )


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class Trip:
    key: str
    at: datetime
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "at": self.at.isoformat(), "detail": self.detail}


class KillSwitch:
    """Account kill switches. One instance per backend process; the orchestrator creates it when the flag is on."""

    def __init__(
        self,
        config: KillSwitchConfig,
        *,
        journal_path: str | Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.journal_path = Path(journal_path) if journal_path else None
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._trips: dict[str, Trip] = {}
        self._heartbeat_sources: list[tuple[str, HeartbeatSource]] = []
        self._started_at = self._now()
        self._engine: Any | None = None
        self._price_fn: PriceFn | None = None
        self.last_monitor: dict[str, Any] | None = None

    # --- wiring ------------------------------------------------------------------------------------------

    def attach(self, engine: Any, price_fn: PriceFn | None = None) -> None:
        """The paper engine whose positions this switch guards, and the current-price source for a flatten."""
        self._engine = engine
        self._price_fn = price_fn

    def add_heartbeat_source(self, name: str, source: HeartbeatSource) -> None:
        self._heartbeat_sources.append((str(name), source))

    # --- journal -----------------------------------------------------------------------------------------

    def _journal(self, event: str, **fields: Any) -> None:
        row = {"ts": self._now().isoformat(), "event": event, **fields}
        logger.warning("kill_switch_" + event, **{k: v for k, v in row.items() if k not in ("ts", "event")})
        if self.journal_path is None:
            return
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str, sort_keys=True) + "\n")
        except OSError as exc:
            logger.warning("kill_switch_journal_write_failed", error=str(exc))

    # --- trips -------------------------------------------------------------------------------------------

    def trip(self, key: str, **detail: Any) -> bool:
        """Latch ``key``. Returns True when it was not already tripped."""
        with self._lock:
            if key in self._trips:
                return False
            self._trips[key] = Trip(key, self._now(), dict(detail))
        self._journal("trip", trip=key, detail=detail)
        return True

    def clear(self, key: str, *, by: str = "owner") -> bool:
        """The owner clears a latched trip (entries resume). Returns False when it was not tripped."""
        with self._lock:
            trip = self._trips.pop(key, None)
        if trip is None:
            return False
        self._journal("clear", trip=key, by=by, tripped_at=trip.at.isoformat())
        return True

    def _expire(self, now: datetime) -> None:
        with self._lock:
            trip = self._trips.get(TRIP_DAILY_LOSS)
            if trip is not None and now >= _day_start(trip.at) + timedelta(days=1):
                del self._trips[TRIP_DAILY_LOSS]
                expired = trip
            else:
                expired = None
        if expired is not None:
            self._journal("clear", trip=TRIP_DAILY_LOSS, by="utc_day_reset", tripped_at=expired.at.isoformat())

    def tripped(self) -> dict[str, dict[str, Any]]:
        self._expire(self._now())
        with self._lock:
            return {k: t.to_dict() for k, t in sorted(self._trips.items())}

    # --- account figures ---------------------------------------------------------------------------------

    def account_day_pnl(self, engine: Any, now: datetime | None = None) -> dict[str, float]:
        """Realized P&L of closes stamped on the current UTC day, plus the open positions' unrealized P&L."""
        now = now or self._now()
        start = _day_start(now)
        with engine._lock:  # noqa: SLF001
            realized = 0.0
            for pos in engine._closed:  # noqa: SLF001
                closed = _utc(getattr(pos, "closed_at", None))
                if closed is not None and closed >= start and str(getattr(pos, "status", "")) == "CLOSED":
                    realized += float(getattr(pos, "pnl_usd", 0.0) or 0.0)
            unrealized = sum(float(getattr(p, "unrealized_pnl_usd", 0.0) or 0.0) for p in engine._open.values())  # noqa: SLF001
            open_risk = sum(float(getattr(p, "risk_usd", 0.0) or 0.0) for p in engine._open.values())  # noqa: SLF001
        equity = float(engine.equity)
        return {
            "realized_today": realized,
            "unrealized": unrealized,
            "day_pnl": realized + unrealized,
            "equity": equity,
            "start_of_day_equity": equity - realized,
            "open_risk": open_risk,
        }

    # --- entry gate --------------------------------------------------------------------------------------

    def check_entry(self, engine: Any, symbol: str, *, risk_usd: float, notional_usd: float) -> str | None:
        """None when a new entry may open; otherwise the refusal reason (``kill_switch:<rule>...``)."""
        if not self.config.enabled:
            return None
        now = self._now()
        self._expire(now)
        cfg = self.config
        with self._lock:
            latched = [k for k in LATCHING_TRIPS if k in self._trips]
            daily = TRIP_DAILY_LOSS in self._trips
        if latched:
            return self._refuse(symbol, f"{REASON_PREFIX}:tripped:{latched[0]}")
        if daily:
            return self._refuse(symbol, f"{REASON_PREFIX}:{TRIP_DAILY_LOSS}:latched")
        acct = self.account_day_pnl(engine, now)
        limit = -abs(float(cfg.daily_loss_limit_pct)) * max(acct["start_of_day_equity"], 0.0)
        if cfg.daily_loss_limit_pct > 0 and acct["day_pnl"] <= limit:
            self.trip(TRIP_DAILY_LOSS, day_pnl=round(acct["day_pnl"], 4), limit=round(limit, 4))
            return self._refuse(symbol, f"{REASON_PREFIX}:{TRIP_DAILY_LOSS}={acct['day_pnl']:.2f}<={limit:.2f}")
        equity = acct["equity"]
        if cfg.max_open_risk_pct > 0:
            cap = float(cfg.max_open_risk_pct) * equity
            if acct["open_risk"] + float(risk_usd) > cap + 1e-9:
                return self._refuse(symbol, f"{REASON_PREFIX}:max_open_risk={acct['open_risk'] + float(risk_usd):.2f}>{cap:.2f}")
        notional = abs(float(notional_usd))
        if cfg.max_position_notional_usd > 0 and notional > float(cfg.max_position_notional_usd) + 1e-9:
            return self._refuse(symbol, f"{REASON_PREFIX}:max_position_notional={notional:.2f}>{cfg.max_position_notional_usd:.2f}")
        if cfg.max_position_notional_pct > 0:
            cap_n = float(cfg.max_position_notional_pct) * equity
            if notional > cap_n + 1e-9:
                return self._refuse(symbol, f"{REASON_PREFIX}:max_position_notional={notional:.2f}>{cap_n:.2f}")
        return None

    def _refuse(self, symbol: str, reason: str) -> str:
        self._journal("entry_blocked", symbol=str(symbol).upper(), reason=reason)
        return reason

    # --- flatten -----------------------------------------------------------------------------------------

    def flatten_all(self, *, reason: str, by: str, engine: Any | None = None, price_fn: PriceFn | None = None) -> dict[str, Any]:
        """Close every open paper position at the current price (ledger exit_reason KILL_SWITCH_FLATTEN)."""
        eng = engine if engine is not None else self._engine
        if eng is None:
            return {"ok": False, "error": "no_engine", "closed": []}
        prices = price_fn or self._price_fn
        closed = eng.flatten_all_positions(reason=reason, price_fn=prices, by=by)
        self._journal("flatten", reason=reason, by=by, closed=[
            {"id": c["id"], "symbol": c["symbol"], "exit_price": c["exit_price"], "price_source": c.get("price_source"),
             "pnl_usd": c["pnl_usd"], "r_multiple": c["r_multiple"]} for c in closed
        ])
        return {"ok": True, "reason": reason, "by": by, "closed_count": len(closed), "closed": closed}

    def manual_flatten(self, *, by: str = "owner", note: str = "") -> dict[str, Any]:
        self.trip(TRIP_MANUAL, by=by, note=note)
        return self.flatten_all(reason=TRIP_MANUAL, by=by)

    # --- heartbeat ---------------------------------------------------------------------------------------

    def heartbeat(self) -> dict[str, Any]:
        """Newest closed-bar processing time over every source, and its age."""
        now = self._now()
        if not self._heartbeat_sources:
            return {"newest": None, "age_seconds": None, "timeout_seconds": self.config.heartbeat_timeout_minutes * 60.0,
                    "stale": False, "sources": {}, "note": "no heartbeat source registered; heartbeat check off"}
        seen: dict[str, str | None] = {}
        newest: datetime | None = None
        for name, source in self._heartbeat_sources:
            try:
                at = _utc(source())
            except Exception as exc:  # noqa: BLE001 - a broken source is a missing heartbeat, not a crash
                logger.warning("kill_switch_heartbeat_source_failed", source=name, error=str(exc))
                at = None
            seen[name] = at.isoformat() if at else None
            if at is not None and (newest is None or at > newest):
                newest = at
        ref = newest or (self._started_at + timedelta(minutes=self.config.heartbeat_grace_minutes - self.config.heartbeat_timeout_minutes))
        age = (now - ref).total_seconds()
        return {
            "newest": newest.isoformat() if newest else None,
            "age_seconds": round(age, 1),
            "timeout_seconds": self.config.heartbeat_timeout_minutes * 60.0,
            "stale": age > self.config.heartbeat_timeout_minutes * 60.0,
            "sources": seen,
        }

    def monitor_tick(self) -> dict[str, Any]:
        """One monitor pass (the orchestrator runs it every 60 s when the flag is on)."""
        if not self.config.enabled:
            return {"enabled": False}
        self._expire(self._now())
        hb = self.heartbeat()
        out: dict[str, Any] = {"enabled": True, "heartbeat": hb, "action": None}
        if hb["stale"]:
            first = self.trip(TRIP_HEARTBEAT, age_seconds=hb["age_seconds"], newest=hb["newest"])
            if first and self.config.auto_flatten_on_heartbeat:
                out["action"] = self.flatten_all(reason=TRIP_HEARTBEAT, by="auto")
        self.last_monitor = {"at": self._now().isoformat(), "stale": hb["stale"], "age_seconds": hb["age_seconds"]}
        return out

    # --- reconciliation hook (D128c) ---------------------------------------------------------------------

    def on_position_mismatch(self, details: dict[str, Any]) -> dict[str, Any]:
        """Called by a reconciliation loop when the exchange position and the ledger disagree."""
        if not self.config.enabled:
            logger.warning("kill_switch_mismatch_ignored_disabled", details=details)
            return {"enabled": False, "action": None}
        first = self.trip(TRIP_MISMATCH, **{k: v for k, v in dict(details).items() if k != "key"})
        action = None
        if first and self.config.auto_flatten_on_mismatch:
            action = self.flatten_all(reason=TRIP_MISMATCH, by="auto")
        return {"enabled": True, "tripped": True, "first": first, "action": action}

    # --- status ------------------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"enabled": self.config.enabled, "config": self.config.to_dict(), "tripped": self.tripped(),
                               "last_monitor": self.last_monitor}
        if self._engine is not None:
            out["account"] = {k: round(v, 4) for k, v in self.account_day_pnl(self._engine).items()}
        if self._heartbeat_sources:
            out["heartbeat"] = self.heartbeat()
        return out


_active: KillSwitch | None = None


def get_kill_switch() -> KillSwitch | None:
    """The process's kill switch, or None when KILL_SWITCHES_ENABLED is off."""
    return _active


def set_kill_switch(ks: KillSwitch | None) -> None:
    global _active
    _active = ks
