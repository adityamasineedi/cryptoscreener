"""CANDLE12_V3_EXEC_FIX paper adapter: closed bars in, orders out, every transition recorded.

Per side, a trade moves through:

  SIGNAL_DETECTED (Candle-1 closed)
    -> SIGNAL_REJECTED | SIGNAL_INVALID | SIGNAL_MISSED | SIGNAL_BLOCKED   (no order)
    -> ORDER_REQUESTED -> ORDER_REJECTED                                  (no position)
                       -> ORDER_ACCEPTED -> ORDER_CANCELED (Candle-2 closed, nothing filled)
                                         -> ORDER_PARTIALLY_FILLED / ORDER_FILLED
                                            -> EXIT_ORDERS_PLACED (reduce-only stop + target)
                                            -> STOP_TRIGGERED | TARGET_TRIGGERED
                                            -> POSITION_CLOSED

Timing per bar k: ``on_bar_open(k)`` places the entry for a Candle-1 that closed at
k-1; ``on_bar_close(bar k)`` syncs the broker, cancels an unfilled Candle-2 entry,
then runs detection on the now-closed bar k. Detection only ever sees closed bars.

Persistence: every order request is written to state.json as in-flight before the
broker is called, and the adapter + venue snapshot is written again before the
call returns, so a restart can tell a lost request from a placed one
(recovery.py). Operational limits (safety.py) can only stop an entry; they never
change a price or a size, and they never stop a protective exit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper.broker import (
    ENTRY,
    MARKET,
    PRICE_BEYOND_TRIGGER,
    STOP,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    TARGET,
    Broker,
    BrokerRejected,
    BrokerUnavailable,
    Fill,
    Order,
)
from app.research.candle12_v3_paper.contract import SYMBOL, TIMEFRAME, contract_config
from app.research.candle12_v3_paper.safety import (
    HALTED,
    KILL_SWITCH,
    MAX_CONSECUTIVE_BROKER_ERRORS,
    MAX_DAILY_ORDER_ATTEMPTS,
    MAX_OPEN_TRADES,
    SafetyLimits,
)
from app.research.candle12_v3_paper.store import PaperStore
from app.research.candle12_v3_paper.tracker import SignalTracker, entry_prices, is_gap, plan_entry
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig

INTERVALS = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1h": timedelta(hours=1)}
EPS = 1e-12

TAG_PRICE_BEYOND_TRIGGER = "PRICE_BEYOND_TRIGGER_AT_PLACEMENT"
TAG_PARTIAL_FILL = "PARTIAL_FILL"
MISSING_FUNDING_DATA = "MISSING_FUNDING_DATA"
BAR_PROCESSING_INCOMPLETE = "BAR_PROCESSING_INCOMPLETE"
ORDER_STATE_UNKNOWN = "ORDER_STATE_UNKNOWN"


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _bar(c: Mapping[str, Any]) -> dict[str, float]:
    return {k: float(c[k]) for k in ("open", "high", "low", "close")}


@dataclass
class Slot:
    trade_id: str
    direction: str
    candle1_index: int
    candle1_time: str | None
    c1: dict[str, float]
    prices: dict[str, float]
    setup: Any = field(repr=False, default=None)
    state: str = "SIGNAL_DETECTED"
    entry_index: int | None = None
    gap: bool = False
    plan: dict[str, Any] | None = None
    entry_id: str | None = None
    stop_id: str | None = None
    target_id: str | None = None
    exit_seq: int = 0
    entry_fills: list[dict[str, Any]] = field(default_factory=list)
    exit_fills: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def entry_qty(self) -> float:
        return sum(f["qty"] for f in self.entry_fills)

    @property
    def exit_qty(self) -> float:
        return sum(f["qty"] for f in self.exit_fills)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("setup", None)
        return d


class PaperAdapter:
    def __init__(
        self,
        *,
        broker: Broker,
        store: PaperStore,
        cfg: v3.Candle12V3Config | None = None,
        symbol: str = SYMBOL,
        timeframe: str = TIMEFRAME,
        scfg: SignalConfig | None = None,
        funding_rows: Sequence[Mapping[str, Any]] | None = None,
        max_placement_delay_s: float = 300.0,
        stale_after_s: float = 900.0,
        max_place_attempts: int = 3,
        safety: SafetyLimits | None = None,
        require_funding: bool = False,
    ) -> None:
        self.broker = broker
        self.store = store
        self.cfg = cfg or contract_config()
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.scfg = scfg or SignalConfig()
        self.interval = INTERVALS[timeframe]
        self.tracker = SignalTracker(self.symbol, timeframe, self.cfg, self.scfg)
        self.funding_rows = sorted(
            ({**r, "time": v3._as_utc(r["time"])} for r in (funding_rows or [])), key=lambda r: r["time"]
        )
        self.max_placement_delay_s = max_placement_delay_s
        self.stale_after_s = stale_after_s
        self.max_place_attempts = max_place_attempts
        self.closed: list[Mapping[str, Any]] = []
        self.slots: dict[str, Slot] = {}
        self.pending: dict[str, Slot] = {}
        self.last_exit_bar: dict[str, int] = {}
        self.first_trade_index: int | None = None
        self._orders: dict[str, tuple[Slot, str]] = {}
        self._trade_seq = 0
        self.safety = safety or SafetyLimits()
        self.require_funding = require_funding
        self.inflight: dict[str, dict[str, Any]] = {}
        self.halted: str | None = None
        self.halt_exception_id: str | None = None
        self.consecutive_errors = 0
        self.attempts_by_day: dict[str, int] = defaultdict(int)
        self.funding_flagged: set[str] = set()
        self.completed_bar: int | None = None

    # ------------------------------------------------------------- helpers

    def _ev(self, kind: str, slot: Slot | None = None, bar_index: int | None = None, **payload: Any) -> None:
        bar_time = None
        if bar_index is not None and 0 <= bar_index < len(self.closed):
            bar_time = _iso(candle_time(self.closed[bar_index]))
        self.store.event(
            kind,
            trade_id=slot.trade_id if slot else None,
            direction=slot.direction if slot else None,
            bar_index=bar_index,
            bar_time=bar_time,
            **payload,
        )

    def _signal_outcome(self, slot: Slot, outcome: str, **detail: Any) -> None:
        self.store.signal({
            "trade_id": slot.trade_id, "direction": slot.direction, "candle1_index": slot.candle1_index,
            "candle1_time": slot.candle1_time, "entry_index": slot.entry_index, "outcome": outcome,
            "tags": list(slot.tags), **detail,
        })

    def _entry_side(self, direction: str) -> str:
        return "BUY" if direction == "LONG" else "SELL"

    def _exit_side(self, direction: str) -> str:
        return "SELL" if direction == "LONG" else "BUY"

    def snapshot(self) -> dict[str, Any]:
        last = self.closed[-1] if self.closed else None
        broker_snap = getattr(self.broker, "snapshot", None)
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "first_trade_index": self.first_trade_index,
            "last_closed_index": len(self.closed) - 1,
            "last_closed_time": _iso(candle_time(last)) if last else None,
            "completed_bar_index": self.completed_bar,
            "slots": {k: s.to_dict() for k, s in self.slots.items()},
            "pending": {k: s.to_dict() for k, s in self.pending.items()},
            "last_exit_bar": dict(self.last_exit_bar),
            "orders": {cid: [slot.trade_id, slot.direction, purpose] for cid, (slot, purpose) in self._orders.items()
                       if self.slots.get(slot.direction) is slot},
            "inflight": dict(self.inflight),
            "trade_seq": self._trade_seq,
            "halted": self.halted,
            "halt_exception_id": self.halt_exception_id,
            "consecutive_errors": self.consecutive_errors,
            "attempts_by_day": dict(self.attempts_by_day),
            "funding_flagged": sorted(self.funding_flagged),
            "last_funding_time": _iso(self.funding_rows[-1]["time"]) if self.funding_rows else None,
            "broker": broker_snap() if callable(broker_snap) else None,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def _persist(self) -> None:
        self.store.save_state(self.snapshot())

    # ------------------------------------------------------------- halt / broker calls

    def halt(self, reason: str, **detail: Any) -> None:
        """Stop placing entries until an operator resolves the HALTED exception."""
        k = len(self.closed) - 1
        exc_id = f"HALTED:{reason}:{k}:{datetime.now(timezone.utc).isoformat()}"  # unique, never reused
        if self.halted:
            # Already halted for another reason: record this one too, keep the first reason.
            self._ev("HALTED", None, k, reason=reason, already_halted=self.halted, **detail)
            self.store.exception(exc_id, HALTED, reason=reason, already_halted=self.halted, **detail)
            self.store.alert("HALTED", reason=reason, already_halted=self.halted, **detail)
            self._persist()
            return
        self.halted = reason
        self.halt_exception_id = exc_id
        self._ev("HALTED", None, k, reason=reason, **detail)
        self.store.exception(self.halt_exception_id, HALTED, reason=reason, **detail)
        self.store.alert("HALTED", reason=reason, **detail)
        self._persist()

    def _broker_error(self, k: int, exc: Exception) -> None:
        if isinstance(exc, BrokerRejected) and exc.reason == PRICE_BEYOND_TRIGGER:
            return  # a market condition, not a venue fault
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.safety.max_consecutive_broker_errors:
            self.halt(MAX_CONSECUTIVE_BROKER_ERRORS, errors=self.consecutive_errors, last_error=str(exc))

    def _lookup(self, client_id: str) -> Order | None:
        lookup = getattr(self.broker, "lookup", None)
        return lookup(client_id) if callable(lookup) else None

    def _place(self, order: Order, slot: Slot, purpose: str, k: int, reference_price: float | None = None) -> Order:
        """Write-ahead, place, persist. Raises the broker's exception when nothing was placed."""
        cid = order.client_id
        self.inflight[cid] = {"trade_id": slot.trade_id, "direction": slot.direction, "purpose": purpose,
                              "order": order.to_dict(), "bar_index": k, "slot": slot.to_dict()}
        self._persist()
        try:
            self.broker.place_order(order, bar_index=k, reference_price=reference_price)
        except BrokerUnavailable as exc:
            try:
                found = self._lookup(cid)
            except BrokerUnavailable:
                # Could not tell whether the exchange has it: never guess.
                self.halt(ORDER_STATE_UNKNOWN, client_id=cid, error=str(exc))
                raise exc
            if found is None:
                self.inflight.pop(cid, None)
                self._broker_error(k, exc)
                self._persist()
                raise
            self._ev("ORDER_FOUND_AFTER_TIMEOUT", slot, k, client_id=cid, status=found.status)
            order.exchange_id, order.status = found.exchange_id, found.status
        except BrokerRejected as exc:
            self.inflight.pop(cid, None)
            self._broker_error(k, exc)
            self._persist()
            raise
        self.inflight.pop(cid, None)
        self.consecutive_errors = 0
        self._orders[cid] = (slot, purpose)
        self._persist()
        return order

    def _cancel(self, client_id: str) -> Order:
        order = self.broker.cancel_order(client_id)
        self._persist()
        return order

    # ------------------------------------------------------------- lifecycle

    def start(self, history: Sequence[Mapping[str, Any]], first_trade_index: int) -> None:
        """Feed closed history to the tracker. Nothing before ``first_trade_index`` trades.

        If the history already runs past ``first_trade_index`` (a late start), the
        setups in that stretch are recorded as missed: the adapter was not running.
        """
        self.first_trade_index = first_trade_index
        for bar in history:
            self.closed.append(bar)
            i = len(self.closed) - 1
            setup = self.tracker.observe(self.closed, i, first_trade_index=first_trade_index)
            if setup:
                slot = self._new_slot(setup, i)
                self._ev("SIGNAL_MISSED", slot, i, reason="ADAPTER_NOT_RUNNING")
                self._signal_outcome(slot, "SIGNAL_MISSED", reason="ADAPTER_NOT_RUNNING")
        self.store.update_manifest(
            symbol=self.symbol,
            timeframe=self.timeframe,
            history_start=_iso(candle_time(history[0])) if history else None,
            first_trade_index=first_trade_index,
            config=self.cfg.to_dict(),
            broker=self.broker.name,
            sends_orders=self.broker.sends_orders,
        )
        self.completed_bar = len(self.closed) - 1
        self._ev("ADAPTER_STARTED", None, len(self.closed) - 1, first_trade_index=first_trade_index)

    def resume(self, history: Sequence[Mapping[str, Any]], first_trade_index: int, state: Mapping[str, Any]) -> None:
        """Rebuild the adapter from a persisted snapshot after a restart.

        The detector is re-fed the same closed bars up to the last processed one
        (silently: those bars were handled before the restart), which restores the
        swing and once-per-level bookkeeping exactly. Slots, pending signals,
        in-flight requests, counters and the virtual venue come from the snapshot.
        Bars that closed while the process was down are NOT processed here; the
        caller feeds them through on_bar_open/on_bar_close, where any entry they
        carried is late and recorded as missed. Broker state is reconciled
        afterwards by recovery.py, before anything trades.
        """
        last = int(state["last_closed_index"])
        if len(history) < last + 1:
            raise ValueError(f"HISTORY_SHORT: state ends at bar {last}, history has {len(history)} bars")
        saved = state.get("last_closed_time")
        if saved and _iso(candle_time(history[last])) != saved:
            raise ValueError(f"HISTORY_MISMATCH: bar {last} is {_iso(candle_time(history[last]))}, state says {saved}")
        self.first_trade_index = first_trade_index
        setups: dict[int, Mapping[str, Any]] = {}
        for bar in history[: last + 1]:
            self.closed.append(bar)
            i = len(self.closed) - 1
            setup = self.tracker.observe(self.closed, i, first_trade_index=first_trade_index)
            if setup:
                setups[i] = setup

        self._resume_setups = setups
        self.slots = {k: self._slot_from_record(d) for k, d in (state.get("slots") or {}).items()}
        self.pending = {k: self._slot_from_record(d) for k, d in (state.get("pending") or {}).items()}
        self.last_exit_bar = {k: int(v) for k, v in (state.get("last_exit_bar") or {}).items()}
        by_trade = {s.trade_id: s for s in self.slots.values()}
        self._orders = {cid: (by_trade[tid], purpose) for cid, (tid, _d, purpose) in (state.get("orders") or {}).items()
                        if tid in by_trade}
        self.inflight = dict(state.get("inflight") or {})
        self._trade_seq = int(state.get("trade_seq") or 0)
        self.consecutive_errors = int(state.get("consecutive_errors") or 0)
        self.attempts_by_day = defaultdict(int, {k: int(v) for k, v in (state.get("attempts_by_day") or {}).items()})
        self.funding_flagged = set(state.get("funding_flagged") or [])
        self.halted = state.get("halted")
        self.halt_exception_id = state.get("halt_exception_id")
        completed = state.get("completed_bar_index", last)
        self.completed_bar = last
        if self.halted and self.halt_exception_id:
            open_ids = {e["id"] for e in self.store.open_exceptions()}
            if self.halt_exception_id not in open_ids:
                self._ev("HALT_CLEARED", None, last, reason=self.halted, exception_id=self.halt_exception_id)
                self.halted = None
                self.halt_exception_id = None
        restore = getattr(self.broker, "restore", None)
        if state.get("broker") and callable(restore):
            restore(state["broker"])
        if completed is not None and int(completed) < last:
            # The process died while bar `last` was being processed: its deadline cancel,
            # funding check or Candle-1 detection may not have run. Never guess: halt for review.
            self.halt(BAR_PROCESSING_INCOMPLETE, incomplete_bar_index=last, incomplete_bar_time=saved, completed_bar_index=completed,
                      detail="the last closed bar was not fully processed before the restart")
        self._ev("ADAPTER_RESUMED", None, last, first_trade_index=first_trade_index,
                 slots=list(self.slots), pending=list(self.pending), inflight=list(self.inflight),
                 halted=self.halted)
        self.store.alert("PROCESS_RESTART", last_closed_time=saved, slots=list(self.slots),
                         inflight=list(self.inflight))

    def _slot_from_record(self, d: Mapping[str, Any]) -> Slot:
        """A Slot from its persisted dict; the detector setup comes from the resume replay."""
        setups = getattr(self, "_resume_setups", {})
        return Slot(**{**d, "setup": setups.get(int(d["candle1_index"]))})

    def replay(self, candles: Sequence[Mapping[str, Any]], first_trade_index: int) -> None:
        """Dry-run driver: bar opens and closes in order, as a live feed would deliver them."""
        self.start(candles[:first_trade_index], first_trade_index)
        for k in range(first_trade_index, len(candles)):
            bar = candles[k]
            t = candle_time(bar)
            self.on_bar_open(k, t, float(bar["open"]), now=t)
            self.on_bar_close(bar, now=t + self.interval)

    def _new_slot(self, setup: Mapping[str, Any], i: int) -> Slot:
        self._trade_seq += 1
        direction = str(setup["direction"])
        c1 = {k: float(setup["c1_ohlc"][k]) for k in ("open", "high", "low", "close")}
        t = _iso(candle_time(self.closed[i]))
        return Slot(
            trade_id=f"{self.symbol}-{direction}-{t}",
            direction=direction, candle1_index=i, candle1_time=t, c1=c1,
            prices=entry_prices(direction, c1, self.cfg), setup=setup,
        )

    # ------------------------------------------------------------- bar open: entries

    def on_bar_open(
        self, k: int, open_time: datetime, open_price: float, now: datetime, current_price: float | None = None
    ) -> None:
        if k != len(self.closed):
            raise ValueError(f"bar {k} opened but {len(self.closed)} bars are closed")
        handled = False
        for side in list(self.pending):
            slot = self.pending.pop(side)
            handled = True
            blocked = self._entry_gate(slot, open_time)
            if blocked:
                slot.entry_index = k
                self._missed(slot, k, blocked)
                if blocked == KILL_SWITCH:
                    self.store.alert("KILL_SWITCH", trade_id=slot.trade_id)
                continue
            self._place_entry(slot, k, open_time, open_price, now, current_price)
            handled = True
        if handled:
            self._persist()

    def _entry_gate(self, slot: Slot, open_time: datetime) -> str | None:
        """Why a new entry may not be placed now, or None. Never applies to exits."""
        if self.closed:
            prev = candle_time(self.closed[-1])
            if prev is not None and open_time - prev != self.interval:
                return "DATA_GAP"
        if self.halted:
            return HALTED
        if self.safety.kill_switch_engaged():
            return KILL_SWITCH
        if len(self.slots) >= self.safety.max_open_trades_per_symbol:
            return MAX_OPEN_TRADES
        if self.attempts_by_day[open_time.date().isoformat()] >= self.safety.max_daily_order_attempts:
            return MAX_DAILY_ORDER_ATTEMPTS
        return None

    def _missed(self, slot: Slot, k: int, reason: str) -> None:
        slot.state = "SIGNAL_MISSED"
        self._ev("SIGNAL_MISSED", slot, k, reason=reason)
        self._signal_outcome(slot, "SIGNAL_MISSED", reason=reason)
        self.store.exception(f"{slot.trade_id}:{reason}", reason, trade_id=slot.trade_id,
                             detail="engine would have had this entry; the paper book did not place it")

    def _place_entry(self, slot: Slot, k: int, open_time: datetime, open_price: float,
                     now: datetime, current_price: float | None) -> None:
        slot.entry_index = k
        delay = (now - open_time).total_seconds()
        if delay > self.max_placement_delay_s:
            self._missed(slot, k, "LATE_PLACEMENT")
            return
        d = slot.direction
        p, f = slot.prices["stop_order"], slot.prices["entry_stop_price"]
        gap = is_gap(d, open_price, p)
        moved = (not gap and current_price is not None
                 and (current_price >= f if d == "LONG" else current_price <= f))
        fill_est = v3._adverse(d, open_price, self.cfg.entry_slippage, entry=True) if gap else f
        plan = plan_entry(slot.setup, fill=fill_est, cfg=self.cfg, scfg=self.scfg, filters=self.tracker.filters)
        if plan is None:
            slot.state = "SIGNAL_INVALID"
            self._ev("SIGNAL_INVALID", slot, k, reason="no structural stop or target")
            self._signal_outcome(slot, "SIGNAL_INVALID")
            return
        slot.plan = plan
        slot.gap = gap
        sizing = plan["sizing"]
        if sizing["rejected"]:
            slot.state = "SIGNAL_REJECTED"
            outcome = v3.rejection_outcome(sizing["rejected"])
            slot.tags += [v3.rejection_outcome(r) for r in sizing["reject_reasons"]]
            self._ev("SIGNAL_REJECTED", slot, k, outcome=outcome, reasons=sizing["reject_reasons"],
                     intended_risk_usd=sizing["intended_risk_usd"], actual_risk_usd=sizing["actual_risk_usd"],
                     risk_diff_usd=sizing["risk_diff_usd"], qty=sizing["qty"], notional_usd=sizing["notional_usd"])
            self._signal_outcome(slot, "SIGNAL_REJECTED", rejection=outcome, reasons=sizing["reject_reasons"],
                                 gap_entry=gap, expected_fill=fill_est)
            return
        if gap:
            slot.tags.append(v3.TAG_GAP_ENTRY)
        if moved:
            slot.tags.append(TAG_PRICE_BEYOND_TRIGGER)
            self.store.exception(f"{slot.trade_id}:{TAG_PRICE_BEYOND_TRIGGER}", TAG_PRICE_BEYOND_TRIGGER,
                                 trade_id=slot.trade_id, current_price=current_price, stop_price=f)
        order = Order(
            client_id=f"{slot.trade_id}-E", purpose=ENTRY, side=self._entry_side(d),
            type=MARKET if (gap or moved) else STOP_MARKET, qty=sizing["qty"],
            stop_price=None if (gap or moved) else f,
        )
        self._submit_entry(slot, order, k, open_price if (gap or moved) else None, open_time)

    def _submit_entry(self, slot: Slot, order: Order, k: int, reference_price: float | None,
                      open_time: datetime | None = None) -> None:
        day = (open_time or candle_time(self.closed[-1]) or datetime.now(timezone.utc)).date().isoformat()
        for attempt in range(1, self.max_place_attempts + 1):
            if self.halted:
                break
            self.attempts_by_day[day] += 1
            self._ev("ORDER_REQUESTED", slot, k, order=order.to_dict(), attempt=attempt)
            try:
                self._place(order, slot, ENTRY, k, reference_price)
            except BrokerRejected as exc:
                if exc.reason == PRICE_BEYOND_TRIGGER and order.type == STOP_MARKET:
                    # The stop would trigger at once: the market already passed it.
                    slot.tags.append(TAG_PRICE_BEYOND_TRIGGER)
                    self.store.exception(f"{slot.trade_id}:{TAG_PRICE_BEYOND_TRIGGER}", TAG_PRICE_BEYOND_TRIGGER,
                                         trade_id=slot.trade_id, code=exc.code)
                    order = Order(client_id=order.client_id + "M", purpose=ENTRY, side=order.side,
                                  type=MARKET, qty=order.qty)
                    reference_price = slot.prices["entry_stop_price"]
                    continue
                slot.state = "ORDER_REJECTED"
                slot.tags.append(exc.reason)
                self._ev("ORDER_REJECTED", slot, k, reason=exc.reason, code=exc.code, message=exc.message)
                self.store.alert("ORDER_REJECTION", trade_id=slot.trade_id, reason=exc.reason, code=exc.code)
                self._signal_outcome(slot, "ORDER_REJECTED", rejection=exc.reason, code=exc.code)
                self.store.exception(f"{slot.trade_id}:{exc.reason}", exc.reason, trade_id=slot.trade_id,
                                     code=exc.code, detail="exchange refused an entry the engine would take")
                return
            except BrokerUnavailable as exc:
                self._ev("BROKER_UNAVAILABLE", slot, k, error=str(exc), attempt=attempt)
                continue
            slot.state = "ORDER_ACCEPTED"
            slot.entry_id = order.client_id
            self.slots[slot.direction] = slot
            self._ev("ORDER_ACCEPTED", slot, k, order=order.to_dict())
            self._persist()
            return
        self._missed(slot, k, HALTED if self.halted else "BROKER_UNAVAILABLE")

    # ------------------------------------------------------------- bar close

    def on_bar_close(self, bar: Mapping[str, Any], now: datetime) -> None:
        self.closed.append(bar)
        k = len(self.closed) - 1
        t = candle_time(bar)
        if k > 0:
            prev = candle_time(self.closed[k - 1])
            if t is None or prev is None or t - prev != self.interval:
                self._ev("DATA_GAP", None, k, previous=_iso(prev), current=_iso(t))
                self.store.exception(f"DATA_GAP:{_iso(t)}", "DATA_GAP", previous=_iso(prev), current=_iso(t))
                self.store.alert("MISSED_CANDLE", previous=_iso(prev), current=_iso(t))
        if t is not None and (now - (t + self.interval)).total_seconds() > self.stale_after_s:
            self._ev("DATA_STALE", None, k, bar_close=_iso(t + self.interval), now=_iso(now))
            self.store.exception(f"DATA_STALE:{_iso(t)}", "DATA_STALE", bar_close=_iso(t + self.interval),
                                 processed_at=_iso(now), detail="bar processed late; entries it carried may be missed")
            self.store.alert("MISSED_CANDLE", stale_bar=_iso(t), processed_at=_iso(now))
        try:
            self.broker.sync(k, ohlc(self.closed, k), self)
            self.consecutive_errors = 0  # "consecutive": any successful venue round trip resets the count
        except BrokerUnavailable as exc:
            self._ev("BROKER_UNAVAILABLE", None, k, error=str(exc), during="sync")
            self._broker_error(k, exc)
        self._entry_deadline(k)
        if self.require_funding and t is not None:
            self._check_funding(k, t)
        if self.first_trade_index is None:
            raise RuntimeError("start() must run before bars close")
        setup = self.tracker.observe(self.closed, k, first_trade_index=self.first_trade_index)
        if setup:
            slot = self._new_slot(setup, k)
            if slot.direction in self.slots or self.last_exit_bar.get(slot.direction) == k:
                slot.state = "SIGNAL_BLOCKED"
                self._ev("SIGNAL_BLOCKED", slot, k, reason="position open on this side")
                self._signal_outcome(slot, "SIGNAL_BLOCKED")
            else:
                self._ev("SIGNAL_DETECTED", slot, k, c1=slot.c1, prices=slot.prices)
                self.pending[slot.direction] = slot
        self.completed_bar = k  # only now is bar k fully processed (see resume)
        self._persist()

    def _check_funding(self, k: int, t: datetime) -> None:
        """Every settlement an open position has lived through must be in the funding feed.

        Settlements t_in < s <= t (t = open of the bar that just closed), on the
        contract's funding interval. A missing one is MISSING_FUNDING_DATA: an open
        exception, so it blocks OOS acceptance until explained. Nothing is filled in.
        """
        interval = timedelta(hours=self.cfg.funding_interval_hours)
        have = [r["time"] for r in self.funding_rows]
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        for slot in self.slots.values():
            if slot.entry_index is None or slot.entry_qty <= EPS:
                continue
            t_in = candle_time(self.closed[slot.entry_index])
            if t_in is None:
                continue
            s = epoch + (int((t_in - epoch) // interval) + 1) * interval
            while s <= t:
                key = f"{slot.trade_id}:{s.isoformat()}"
                if key not in self.funding_flagged and not any(abs((h - s).total_seconds()) < 60 for h in have):
                    self.funding_flagged.add(key)
                    self._ev(MISSING_FUNDING_DATA, slot, k, settlement=s.isoformat())
                    self.store.exception(f"{MISSING_FUNDING_DATA}:{key}", MISSING_FUNDING_DATA,
                                         trade_id=slot.trade_id, settlement=s.isoformat(),
                                         detail="funding settlement missing for an open position; blocks OOS")
                    self.store.alert("MISSING_FUNDING", trade_id=slot.trade_id, settlement=s.isoformat())
                s += interval

    def _entry_deadline(self, k: int) -> None:
        """Candle-2 has closed: whatever of the entry did not fill is cancelled."""
        for side, slot in list(self.slots.items()):
            if slot.entry_index != k or slot.state not in ("ORDER_ACCEPTED", "ORDER_PARTIALLY_FILLED"):
                continue
            self._cancel(slot.entry_id)  # type: ignore[arg-type]
            self._ev("ORDER_CANCELED", slot, k, client_id=slot.entry_id, reason="CANDLE2_CLOSED",
                     filled_qty=slot.entry_qty)
            if slot.entry_qty <= EPS:
                slot.state = "NO_FILL"
                self._signal_outcome(slot, "NO_FILL")
                del self.slots[side]
            else:
                slot.state = "POSITION_OPEN"
                if TAG_PARTIAL_FILL not in slot.tags:
                    slot.tags.append(TAG_PARTIAL_FILL)
                self.store.exception(f"{slot.trade_id}:{TAG_PARTIAL_FILL}", TAG_PARTIAL_FILL,
                                     trade_id=slot.trade_id, filled_qty=slot.entry_qty,
                                     planned_qty=slot.plan["sizing"]["qty"] if slot.plan else None)

    # ------------------------------------------------------------- fills (broker listener)

    def on_fill(self, order: Order, fill: Fill, bar_index: int) -> None:
        slot, purpose = self._orders[order.client_id]
        row = {**fill.to_dict(), "purpose": purpose}
        if purpose == ENTRY:
            slot.entry_fills.append(row)
            kind = "ORDER_FILLED" if order.status == "FILLED" else "ORDER_PARTIALLY_FILLED"
            slot.state = "POSITION_OPEN" if order.status == "FILLED" else "ORDER_PARTIALLY_FILLED"
            self._ev(kind, slot, bar_index, fill=row, filled_qty=order.filled_qty, order_qty=order.qty)
            self._place_exits(slot, bar_index)
            return
        reason = "SL" if purpose == STOP else str(slot.plan["target_slot"]) if slot.plan else "TP"
        slot.exit_fills.append({**row, "reason": reason})
        if purpose == STOP:
            if fill.gap:
                slot.tags.append(v3.TAG_GAP_STOP)
            if fill.same_bar_conflict:
                slot.tags.append(v3.TAG_SAME_BAR_SL)
        elif fill.gap:
            slot.tags.append(v3.TAG_GAP_TARGET)
        self._ev("STOP_TRIGGERED" if purpose == STOP else "TARGET_TRIGGERED", slot, bar_index, fill=row)
        self._ev("ORDER_FILLED", slot, bar_index, fill=row, filled_qty=order.filled_qty, order_qty=order.qty)
        if slot.entry_qty - slot.exit_qty > EPS:
            return
        for cid in (slot.stop_id, slot.target_id, slot.entry_id):
            if cid and cid != order.client_id:
                o = self._cancel(cid)
                if o.status == "CANCELED":
                    self._ev("ORDER_CANCELED", slot, bar_index, client_id=cid, reason="POSITION_CLOSED")
        self._finalize(slot, bar_index)
        self.last_exit_bar[slot.direction] = bar_index
        self.slots.pop(slot.direction, None)

    def _place_exits(self, slot: Slot, bar_index: int) -> None:
        """(Re)place the reduce-only stop and target for the filled quantity."""
        assert slot.plan is not None
        for cid in (slot.stop_id, slot.target_id):
            if cid:
                self._cancel(cid)
                self._ev("ORDER_CANCELED", slot, bar_index, client_id=cid, reason="RESIZED_FOR_FILL")
        slot.exit_seq += 1
        qty = slot.entry_qty
        side = self._exit_side(slot.direction)
        stop = Order(client_id=f"{slot.trade_id}-S{slot.exit_seq}", purpose=STOP, side=side, type=STOP_MARKET,
                     qty=qty, stop_price=slot.plan["stop"], reduce_only=True)
        target = Order(client_id=f"{slot.trade_id}-T{slot.exit_seq}", purpose=TARGET, side=side,
                       type=TAKE_PROFIT_MARKET, qty=qty, stop_price=slot.plan["target"], reduce_only=True)
        # Each exit id is recorded on the slot, and persisted, as soon as that order is placed,
        # so a crash between the stop and the target never hides a live stop from recovery.
        slot.stop_id = slot.target_id = None
        for order, attr in ((stop, "stop_id"), (target, "target_id")):
            try:
                self._place(order, slot, order.purpose, bar_index)
            except (BrokerRejected, BrokerUnavailable) as exc:
                self._ev("EXIT_ORDER_FAILED", slot, bar_index, order=order.to_dict(), error=str(exc))
                self.store.exception(f"{slot.trade_id}:EXIT_ORDER_FAILED:{order.client_id}", "EXIT_ORDER_FAILED",
                                     trade_id=slot.trade_id, error=str(exc),
                                     detail="position is not protected; close it by hand")
                self.store.alert("EXIT_ORDER_FAILURE", trade_id=slot.trade_id, client_id=order.client_id,
                                 error=str(exc))
                continue
            setattr(slot, attr, order.client_id)
            self._persist()
        self._ev("EXIT_ORDERS_PLACED", slot, bar_index, stop=stop.to_dict(), target=target.to_dict())

    # ------------------------------------------------------------- closed trade

    def _finalize(self, slot: Slot, exit_index: int) -> None:
        assert slot.plan is not None and slot.entry_index is not None
        long_ = slot.direction == "LONG"
        qty = slot.entry_qty
        entry_avg = sum(f["qty"] * f["price"] for f in slot.entry_fills) / qty
        exit_avg = sum(f["qty"] * f["price"] for f in slot.exit_fills) / slot.exit_qty
        gross = sum(f["qty"] * ((f["price"] - entry_avg) if long_ else (entry_avg - f["price"])) for f in slot.exit_fills)
        fills = slot.entry_fills + slot.exit_fills
        fees_reported = sum(f["fee"] for f in fills)
        fees_computed = self.cfg.taker_fee * sum(f["qty"] * f["price"] for f in fills)
        pos = v3._Position(
            direction=slot.direction, entry_index=slot.entry_index, entry=entry_avg,
            stop_initial=slot.plan["stop"], stop=slot.plan["stop"], qty=qty, targets=list(slot.plan["targets"]),
            legs=[{"bar": f["bar_index"], "qty": f["qty"]} for f in slot.exit_fills], exit_index=exit_index,
        )
        t_in = candle_time(self.closed[slot.entry_index])
        t_out = candle_time(self.closed[exit_index])
        if self.cfg.funding_enabled and self.funding_rows:
            funding_computed, funding_events = v3._funding_usd(pos, self.closed, self.funding_rows)
            coverage = v3._funding_coverage(t_in, t_out, self.funding_rows[0]["time"], self.funding_rows[-1]["time"],
                                            timedelta(hours=self.cfg.funding_interval_hours))
        else:
            funding_computed, funding_events, coverage = 0.0, 0, None
        funding_reported = self.broker.reported_funding(funding_computed, start=t_in, end=t_out)
        funding = funding_reported if funding_reported is not None else funding_computed
        net_ex_funding = gross - fees_reported
        net = net_ex_funding + funding
        actual_risk = qty * abs(entry_avg - slot.plan["stop"])
        record = {
            "trade_id": slot.trade_id,
            "symbol": self.symbol,
            "direction": slot.direction,
            "candle1_index": slot.candle1_index,
            "candle1_time": slot.candle1_time,
            "c1": slot.c1,
            "entry_index": slot.entry_index,
            "entry_time": _iso(t_in),
            "c2": _bar(self.closed[slot.entry_index]),
            "trigger": slot.prices["trigger"],
            "entry_stop_price": slot.prices["entry_stop_price"],
            "expected_fill": slot.plan["expected_fill"],
            "entry_fill": entry_avg,
            "entry_slippage_vs_trigger": entry_avg / slot.prices["trigger"] - 1.0,
            "stop_price": slot.plan["stop"],
            "target_price": slot.plan["target"],
            "target_slot": slot.plan["target_slot"],
            "planned_qty": slot.plan["sizing"]["qty"],
            "qty": qty,
            "notional_usd": qty * entry_avg,
            "intended_risk_usd": self.cfg.risk_usd,
            "actual_risk_usd": actual_risk,
            "fees_reported_usd": fees_reported,
            "fees_computed_usd": fees_computed,
            "funding_computed_usd": funding_computed,
            "funding_reported_usd": funding_reported,
            "funding_events": funding_events,
            "funding_coverage": coverage,
            "gross_pnl_usd": gross,
            "net_pnl_ex_funding_usd": net_ex_funding,
            "net_pnl_usd": net,
            "r_net_actual_risk": net / actual_risk if actual_risk else None,
            "r_net_intended_risk": net / self.cfg.risk_usd,
            "exit_reason": slot.exit_fills[-1]["reason"],
            "exit_index": exit_index,
            "exit_time": _iso(t_out),
            "exit_fill": exit_avg,
            "entry_fills": slot.entry_fills,
            "exit_fills": slot.exit_fills,
            "tags": list(dict.fromkeys(slot.tags)),
            "rejection_tags": [],
        }
        if self.require_funding and self.cfg.funding_enabled and coverage != "FULL":
            self.store.exception(f"{MISSING_FUNDING_DATA}:{slot.trade_id}:coverage", MISSING_FUNDING_DATA,
                                 trade_id=slot.trade_id, coverage=coverage,
                                 detail="closed trade without full funding coverage; blocks OOS")
            self.store.alert("MISSING_FUNDING", trade_id=slot.trade_id, coverage=coverage)
        slot.state = "POSITION_CLOSED"
        self._ev("POSITION_CLOSED", slot, exit_index, net_pnl_usd=net, exit_reason=record["exit_reason"])
        self.store.trade(record)
