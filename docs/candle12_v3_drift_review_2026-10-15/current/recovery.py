"""Restart recovery for the V3 paper adapter.

Runs once at startup, after ``PaperAdapter.resume`` has rebuilt the local book
from state.json and before any bar is processed. It reads the venue (open
orders, position, recent fills), reconciles it with the local book, and either
resumes or halts. It never re-enters a trade, never guesses an order's state,
and never adopts a position it cannot attribute.

Decision table (first matching row per item; halts are checked before any order
is placed):

| Local state                               | Venue                                   | Action                          |
|-------------------------------------------|-----------------------------------------|---------------------------------|
| in-flight request (ORDER_REQUESTED)       | order or fill with that client id       | RECOVERY_ORDER_ADOPTED          |
| in-flight entry request                   | nothing with that client id             | ORDER_LOST (no re-entry)        |
| in-flight exit request                    | nothing with that client id             | ORDER_LOST, then exit replaced  |
| any                                       | fill not yet in the local book          | RECOVERY_FILL_APPLIED (resizes) |
| any                                       | open order unknown to the local book    | HALT UNRECONCILED_EXTERNAL_ORDER|
| no open trade (none, or POSITION_CLOSED)  | position != 0                           | HALT UNRECONCILED_EXTERNAL_POSITION |
| POSITION_OPEN                             | position != local open quantity         | HALT UNRECONCILED_EXTERNAL_POSITION |
| POSITION_OPEN                             | position = local qty, both exits sized  | RECOVERY_RESUMED_POSITION       |
| POSITION_OPEN                             | an exit missing                         | RECOVERY_EXIT_REPLACED (frozen levels) |
| POSITION_OPEN                             | an exit sized for another quantity      | RECOVERY_EXIT_RESIZED           |
| ORDER_ACCEPTED (entry resting)            | entry open                              | RECOVERY_ENTRY_RESUMED          |
| ORDER_ACCEPTED (entry resting)            | entry gone, no fill                     | ORDER_LOST (no re-entry)        |
| adapter halted, halt exception open       | any                                     | HALT STILL_HALTED               |

Every action is an append-only event with recovery_id, prior_state,
observed_broker_state, action_taken, order_ids, timestamp and reason_code, and
an alert (RECOVERY_ACTION, or UNEXPECTED_POSITION for a position halt).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

from app.research.candle12_v3_paper.broker import (
    ENTRY,
    STOP,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    TARGET,
    BrokerRejected,
    BrokerUnavailable,
    Fill,
    Order,
)

EPS = 1e-12
UNRECONCILED_EXTERNAL_POSITION = "UNRECONCILED_EXTERNAL_POSITION"
UNRECONCILED_EXTERNAL_ORDER = "UNRECONCILED_EXTERNAL_ORDER"
BROKER_UNREACHABLE = "BROKER_UNREACHABLE"
EXIT_REPLACE_FAILED = "EXIT_REPLACE_FAILED"
STILL_HALTED = "STILL_HALTED"
ORDER_LOST = "ORDER_LOST"
_FILL_FIELDS = {f.name for f in fields(Fill)}


class RecoveryHalted(RuntimeError):
    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class BrokerView:
    position: float
    open_orders: dict[str, Order]
    fills: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "open_orders": sorted(self.open_orders),
            "recent_fills": [{"client_id": f.get("client_id"), "qty": f.get("qty"), "trade_id": f.get("trade_id")}
                             for f in self.fills[-10:]],
        }


def observe(broker: Any) -> BrokerView:
    return BrokerView(
        position=float(broker.position_qty()),
        open_orders={o.client_id: o for o in broker.open_orders()},
        fills=list(broker.recent_fills()),
    )


def _signed_open_qty(slot: Any) -> float:
    q = slot.entry_qty - slot.exit_qty
    return q if slot.direction == "LONG" else -q


def run_recovery(adapter: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Reconcile the resumed adapter with its venue. Returns the summary or raises RecoveryHalted."""
    now = now or datetime.now(timezone.utc)
    store, broker = adapter.store, adapter.broker
    rid = f"REC-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    k = len(adapter.closed) - 1
    actions: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}

    def act(kind: str, reason_code: str, *, prior_state: Any, action_taken: str, order_ids: list[str],
            trade_id: str | None = None, alert: str = "RECOVERY_ACTION", **extra: Any) -> None:
        row = store.event(kind, recovery_id=rid, trade_id=trade_id, bar_index=k, prior_state=prior_state,
                          observed_broker_state=observed, action_taken=action_taken, order_ids=order_ids,
                          timestamp=now.isoformat(), reason_code=reason_code, **extra)
        store.alert(alert, recovery_id=rid, event=kind, reason_code=reason_code, trade_id=trade_id,
                    order_ids=order_ids)
        actions.append(row)

    def halt(code: str, *, prior_state: Any, order_ids: list[str], trade_id: str | None = None, **detail: Any):
        act("RECOVERY_HALTED", code, prior_state=prior_state, action_taken="HALT_TRADING", order_ids=order_ids,
            trade_id=trade_id, alert="UNEXPECTED_POSITION" if code == UNRECONCILED_EXTERNAL_POSITION
            else "RECOVERY_ACTION", **detail)
        adapter.halt(code, recovery_id=rid, **detail)
        adapter._persist()
        raise RecoveryHalted(code, recovery_id=rid, **detail)

    if adapter.halted:
        observed = {"skipped": "adapter halted"}
        act("RECOVERY_HALTED", STILL_HALTED, prior_state={"halted": adapter.halted}, action_taken="HALT_TRADING",
            order_ids=[], exception_id=adapter.halt_exception_id)
        raise RecoveryHalted(STILL_HALTED, reason=adapter.halted, exception_id=adapter.halt_exception_id)

    try:
        view = observe(broker)
    except BrokerUnavailable as exc:
        observed = {"error": str(exc)}
        halt(BROKER_UNREACHABLE, prior_state={"slots": list(adapter.slots)}, order_ids=[], error=str(exc))
        raise  # unreachable; halt raises
    observed = view.summary()
    fill_ids = {f.get("client_id") for f in view.fills}

    # 1. requests written ahead but never acknowledged
    for cid, rec in list(adapter.inflight.items()):
        purpose, trade_id = rec["purpose"], rec["trade_id"]
        prior = {"state": "ORDER_REQUESTED", "purpose": purpose, "order": rec.get("order")}
        reached = cid in view.open_orders or cid in fill_ids
        if reached:
            if purpose == ENTRY and rec["direction"] not in adapter.slots:
                slot = adapter._slot_from_record(rec["slot"])
                slot.state, slot.entry_id = "ORDER_ACCEPTED", cid
                adapter.slots[slot.direction] = slot
            slot = adapter.slots.get(rec["direction"])
            if slot is not None:
                adapter._orders[cid] = (slot, purpose)
                if purpose == STOP:
                    slot.stop_id = cid
                elif purpose == TARGET:
                    slot.target_id = cid
            act("RECOVERY_ORDER_ADOPTED", "REQUEST_REACHED_VENUE", prior_state=prior,
                action_taken="ADOPT_ORDER", order_ids=[cid], trade_id=trade_id)
        else:
            act(ORDER_LOST, "REQUEST_NOT_AT_VENUE", prior_state=prior,
                action_taken="RECORD_LOST_NO_REENTRY" if purpose == ENTRY else "RECORD_LOST_EXIT_WILL_BE_REPLACED",
                order_ids=[cid], trade_id=trade_id)
            store.exception(f"{trade_id}:{ORDER_LOST}:{cid}", ORDER_LOST, trade_id=trade_id, client_id=cid,
                            purpose=purpose, detail="order request did not reach the venue; not re-sent")
            if purpose == ENTRY:
                store.signal({"trade_id": trade_id, "direction": rec["direction"], "outcome": ORDER_LOST,
                              "client_id": cid, "recovery_id": rid})
        adapter.inflight.pop(cid, None)
    adapter._persist()

    # 2. fills the local book has not applied
    applied = {f.get("trade_id") for s in adapter.slots.values() for f in s.entry_fills + s.exit_fills}
    for f in view.fills:
        cid, tid = f.get("client_id"), f.get("trade_id")
        if cid not in adapter._orders or tid in applied:
            continue
        slot, purpose = adapter._orders[cid]
        prior = {"state": slot.state, "entry_qty": slot.entry_qty, "exit_qty": slot.exit_qty}
        order = broker.lookup(cid)
        if order is None:
            halt(UNRECONCILED_EXTERNAL_ORDER, prior_state=prior, order_ids=[cid], trade_id=slot.trade_id,
                 detail="fill for an order the venue cannot return")
        adapter.on_fill(order, Fill(**{key: v for key, v in f.items() if key in _FILL_FIELDS}), k)
        applied.add(tid)
        act("RECOVERY_FILL_APPLIED", "FILL_NOT_IN_LOCAL_BOOK", prior_state=prior, action_taken="APPLY_FILL",
            order_ids=[cid], trade_id=slot.trade_id, fill_qty=f.get("qty"), purpose=purpose)
    view = observe(broker)
    observed = view.summary()

    # 3. orders and position the local book cannot explain
    unknown = [cid for cid in view.open_orders if cid not in adapter._orders]
    if unknown:
        halt(UNRECONCILED_EXTERNAL_ORDER, prior_state={"known_orders": sorted(adapter._orders)}, order_ids=unknown)
    local_qty = sum(_signed_open_qty(s) for s in adapter.slots.values())
    if abs(view.position - local_qty) > EPS:
        open_slots = [s for s in adapter.slots.values() if s.entry_qty - s.exit_qty > EPS]
        reason = "POSITION_QTY_MISMATCH" if open_slots else (
            "LOCAL_POSITION_CLOSED" if adapter.last_exit_bar else "NO_LOCAL_TRADE_RECORD")
        halt(UNRECONCILED_EXTERNAL_POSITION,
             prior_state={"local_open_qty": local_qty, "slots": {s.trade_id: s.state for s in adapter.slots.values()}},
             order_ids=sorted(view.open_orders), reason=reason, venue_position=view.position)

    # 4. each local trade
    for side, slot in list(adapter.slots.items()):
        open_qty = slot.entry_qty - slot.exit_qty
        prior = {"state": slot.state, "open_qty": open_qty, "stop_id": slot.stop_id, "target_id": slot.target_id}
        if open_qty > EPS:
            changed = _repair_exits(adapter, slot, view, open_qty, act, halt, prior)
            if not changed:
                act("RECOVERY_RESUMED_POSITION", "POSITION_AND_EXITS_MATCH", prior_state=prior,
                    action_taken="RESUME", order_ids=[slot.stop_id or "", slot.target_id or ""], trade_id=slot.trade_id)
        elif slot.state == "ORDER_ACCEPTED":
            if slot.entry_id in view.open_orders:
                act("RECOVERY_ENTRY_RESUMED", "ENTRY_RESTING", prior_state=prior, action_taken="RESUME",
                    order_ids=[slot.entry_id or ""], trade_id=slot.trade_id)
            else:
                act(ORDER_LOST, "ENTRY_GONE_NO_FILL", prior_state=prior, action_taken="RECORD_LOST_NO_REENTRY",
                    order_ids=[slot.entry_id or ""], trade_id=slot.trade_id)
                store.exception(f"{slot.trade_id}:{ORDER_LOST}:{slot.entry_id}", ORDER_LOST, trade_id=slot.trade_id,
                                client_id=slot.entry_id, detail="accepted entry vanished from the venue; not re-sent")
                store.signal({"trade_id": slot.trade_id, "direction": slot.direction, "outcome": ORDER_LOST,
                              "client_id": slot.entry_id, "recovery_id": rid})
                del adapter.slots[side]
    act("RECOVERY_COMPLETE", "RECONCILED", prior_state={"slots": list(adapter.slots)}, action_taken="RESUME_TRADING",
        order_ids=sorted(observe(broker).open_orders))
    adapter._persist()
    return {"status": "RESUMED", "recovery_id": rid, "actions": [a["event"] for a in actions]}


def _repair_exits(adapter: Any, slot: Any, view: BrokerView, open_qty: float, act, halt, prior) -> bool:
    """Re-place a missing exit, or resize one sized for another quantity, at the frozen plan levels."""
    if slot.plan is None:
        halt(EXIT_REPLACE_FAILED, prior_state=prior, order_ids=[], trade_id=slot.trade_id, detail="no frozen plan")
    side = "SELL" if slot.direction == "LONG" else "BUY"
    k = len(adapter.closed) - 1
    changed = False
    for purpose, attr, otype, level in ((STOP, "stop_id", STOP_MARKET, slot.plan["stop"]),
                                        (TARGET, "target_id", TAKE_PROFIT_MARKET, slot.plan["target"])):
        cid = getattr(slot, attr)
        live = view.open_orders.get(cid) if cid else None
        if live is not None and abs((live.qty - live.filled_qty) - open_qty) <= EPS:
            continue
        kind = "RECOVERY_EXIT_REPLACED" if live is None else "RECOVERY_EXIT_RESIZED"
        if live is not None:
            adapter._cancel(cid)
        slot.exit_seq += 1
        letter = "S" if purpose == STOP else "T"
        order = Order(client_id=f"{slot.trade_id}-{letter}{slot.exit_seq}", purpose=purpose, side=side, type=otype,
                      qty=open_qty, stop_price=float(level), reduce_only=True)
        try:
            adapter._place(order, slot, purpose, k)
        except (BrokerRejected, BrokerUnavailable) as exc:
            halt(EXIT_REPLACE_FAILED, prior_state=prior, order_ids=[order.client_id], trade_id=slot.trade_id,
                 error=str(exc))
        setattr(slot, attr, order.client_id)
        act(kind, "EXIT_MISSING" if live is None else "EXIT_QTY_MISMATCH", prior_state=prior,
            action_taken=f"PLACE_{otype}_AT_FROZEN_LEVEL", order_ids=[x for x in (cid, order.client_id) if x],
            trade_id=slot.trade_id, level=float(level), qty=open_qty)
        changed = True
    return changed
