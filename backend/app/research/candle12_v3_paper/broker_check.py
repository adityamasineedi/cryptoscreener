"""Supervised Binance futures TESTNET broker check (testnet_broker_check mode).

API and order-lifecycle validation only. It uses testnet prices and testnet
fills at the exchange's minimum viable quantity, holds at most ``max_trades``
entry (default 1), and always cleans up: open orders are cancelled and any
position is flattened with a reduce-only market order, in a finally block.

It never validates the strategy, is never compared with the engine, and never
counts toward OOS: the store is TESTNET_BROKER_CHECK, labelled NON_OOS, with
counts_toward_oos=false and strategy_validation=false.

BROKER_CHECK_PASS is printed only when every required check passes. Partial
fills are checked only if the testnet produces one; otherwise that check is
SKIPPED and does not block the pass.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.research.candle12_v3_paper.binance import PaperGuardError, assert_testnet
from app.research.candle12_v3_paper.broker import (
    ENTRY,
    MARKET,
    STOP,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    TARGET,
    BrokerRejected,
    BrokerUnavailable,
    Fill,
    Order,
)
from app.research.candle12_v3_paper.modes import MAINNET_DATA_BASE
from app.research.candle12_v3_paper.store import PaperStore

PASS, FAIL, SKIPPED = "PASS", "FAIL", "SKIPPED"
BROKER_CHECK_PASS, BROKER_CHECK_FAIL = "BROKER_CHECK_PASS", "BROKER_CHECK_FAIL"
REQUIRED = (
    "testnet_host_enforced",
    "authentication",
    "order_rejection",
    "market_entry",
    "fill_sync",
    "stop_market_reduce_only",
    "take_profit_market_reduce_only",
    "open_order_query",
    "duplicate_client_id",
    "order_cancellation",
    "api_timeout_handling",
    "exit_fill_cancels_other",
    "no_order_after_terminal",
    "transitions_persisted",
)
CONDITIONAL = ("partial_fill_handling",)


class TerminalStateError(RuntimeError):
    pass


@dataclass
class CheckSession:
    """Every broker call goes through here: persisted before it returns, refused after terminal."""

    broker: Any
    store: PaperStore
    max_trades: int = 1
    terminal: bool = False
    entries: int = 0
    seqs: list[int] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)

    def record(self, kind: str, **payload: Any) -> None:
        row = self.store.event(kind, mode="testnet_broker_check", **payload)
        self.seqs.append(row["seq"])

    def place(self, order: Order, *, timeout: float | None = None) -> Order:
        if self.terminal:
            raise TerminalStateError(f"session is terminal; refusing {order.client_id}")
        if order.purpose == ENTRY and not order.reduce_only and self.entries >= self.max_trades:
            raise TerminalStateError(f"max trades {self.max_trades} reached")
        self.record("ORDER_REQUESTED", order=order.to_dict())
        try:
            kwargs = {"timeout": timeout} if timeout is not None else {}
            placed = self.broker.place_order(order, bar_index=0, **kwargs)
        except BrokerRejected as exc:
            self.record("ORDER_REJECTED", client_id=order.client_id, reason=exc.reason, code=exc.code,
                        message=exc.message)
            raise
        except BrokerUnavailable as exc:
            self.record("BROKER_UNAVAILABLE", client_id=order.client_id, error=str(exc), unknown=exc.unknown)
            raise
        if order.purpose == ENTRY and not order.reduce_only:
            self.entries += 1
        self.record("ORDER_ACCEPTED", order=placed.to_dict())
        return placed

    def cancel(self, client_id: str) -> Order:
        order = self.broker.cancel_order(client_id)
        self.record("ORDER_CANCELED", client_id=client_id, status=order.status)
        return order

    def sync(self) -> list[Fill]:
        return self.broker.sync(0, (0.0, 0.0, 0.0, 0.0), self)

    def on_fill(self, order: Order, fill: Fill, bar_index: int) -> None:
        row = {**fill.to_dict(), "purpose": order.purpose, "order_status": order.status}
        self.fills.append(row)
        self.record("ORDER_FILLED" if order.status == "FILLED" else "ORDER_PARTIALLY_FILLED", fill=row,
                    filled_qty=order.filled_qty, order_qty=order.qty)


def _floor_step(x: float, step: float) -> float:
    return round(math.floor(x / step + 1e-9) * step, 12)


def _ceil_step(x: float, step: float) -> float:
    return round(math.ceil(x / step - 1e-9) * step, 12)


def run_broker_check(
    broker: Any,
    store: PaperStore,
    *,
    max_trades: int = 1,
    exit_wait_s: float = 900.0,
    poll_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    run_tag: str | None = None,
) -> dict[str, Any]:
    tag = run_tag or time.strftime("%Y%m%d%H%M%S", time.gmtime())
    session = CheckSession(broker, store, max_trades=max_trades)
    checks: dict[str, dict[str, Any]] = {}
    cleanup: dict[str, Any] = {}

    def mark(name: str, status: str, **detail: Any) -> None:
        checks[name] = {"status": status, **detail}
        session.record("CHECK_RESULT", check=name, status=status, **detail)

    def cid(suffix: str) -> str:
        return f"c12chk_{tag}_{suffix}"

    session.record("BROKER_CHECK_STARTED", symbol=broker.symbol, max_trades=max_trades, counts_toward_oos=False,
                   strategy_validation=False)
    try:
        # 1. host enforcement
        try:
            assert_testnet(MAINNET_DATA_BASE)
            mark("testnet_host_enforced", FAIL, detail="mainnet host was not refused")
        except PaperGuardError:
            assert_testnet(broker.base_url)
            mark("testnet_host_enforced", PASS, host=broker.base_url)

        # 2. authentication
        try:
            broker.account()
            mark("authentication", PASS)
        except (BrokerRejected, BrokerUnavailable) as exc:
            mark("authentication", FAIL, error=str(exc))
            return _result(checks, session, cleanup)

        filters = broker.symbol_filters()
        broker.tick = filters["tick"]
        price = broker.last_price()
        step = filters["step"]
        qty = max(filters["min_qty"], _ceil_step(filters["min_notional"] * 1.1 / price, step))
        session.record("CHECK_SIZING", price=price, qty=qty, filters=filters)
        if abs(broker.position_qty()) > 1e-12:
            mark("order_rejection", FAIL, detail="testnet account is not flat; refusing to start")
            return _result(checks, session, cleanup)

        # 3. rejection: reduce-only while flat must be refused, and nothing may open
        try:
            session.place(Order(cid("rej"), STOP, "SELL", MARKET, qty, reduce_only=True))
            mark("order_rejection", FAIL, detail="reduce-only order accepted while flat")
        except BrokerRejected as exc:
            flat = abs(broker.position_qty()) <= 1e-12
            mark("order_rejection", PASS if flat else FAIL, reason=exc.reason, code=exc.code, flat=flat)

        # 4-5. entry and fill sync
        entry = Order(cid("E"), ENTRY, "BUY", MARKET, qty)
        try:
            session.place(entry)
            mark("market_entry", PASS, exchange_id=entry.exchange_id)
        except (BrokerRejected, BrokerUnavailable) as exc:
            mark("market_entry", FAIL, error=str(exc))
            return _result(checks, session, cleanup)
        deadline = clock() + 60.0
        while entry.filled_qty < qty - 1e-12 and clock() < deadline:
            session.sync()
            if entry.filled_qty < qty - 1e-12:
                sleep(min(poll_s, 2.0))
        entry_fills = [f for f in session.fills if f["client_id"] == entry.client_id]
        pos = broker.position_qty()
        ok = abs(sum(f["qty"] for f in entry_fills) - qty) <= 1e-9 and abs(pos - qty) <= 1e-9 and all(
            f["price"] > 0 for f in entry_fills)
        mark("fill_sync", PASS if ok else FAIL, fills=len(entry_fills), filled=entry.filled_qty, position=pos,
             fees=[f["fee"] for f in entry_fills])
        if len(entry_fills) > 1:
            partial_events = sum(1 for f in entry_fills if f["order_status"] == "PARTIALLY_FILLED")
            mark("partial_fill_handling", PASS if partial_events and ok else FAIL, fills=len(entry_fills),
                 partial_events=partial_events)
        else:
            mark("partial_fill_handling", SKIPPED, detail="the testnet filled the entry in one trade")
        if not ok:
            return _result(checks, session, cleanup)

        def exit_order(name: str, purpose: str, otype: str, trigger: float) -> Order:
            return Order(cid(name), purpose, "SELL", otype, qty, stop_price=trigger, reduce_only=True)

        # 6-7. reduce-only conditional exits on the Algo endpoint
        s1 = exit_order("S1", STOP, STOP_MARKET, price * 0.98)
        t1 = exit_order("T1", TARGET, TAKE_PROFIT_MARKET, price * 1.02)
        for name, order in (("stop_market_reduce_only", s1), ("take_profit_market_reduce_only", t1)):
            try:
                session.place(order)
                mark(name, PASS if order.status == "NEW" else FAIL, order_status=order.status, algo_id=order.exchange_id)
            except (BrokerRejected, BrokerUnavailable) as exc:
                mark(name, FAIL, error=str(exc))

        # 8. open-order query
        open_ids = {o.client_id for o in broker.open_orders()}
        mark("open_order_query", PASS if {s1.client_id, t1.client_id} <= open_ids else FAIL, open=sorted(open_ids))

        # 9. duplicate client id
        dup = exit_order("S1", STOP, STOP_MARKET, price * 0.97)
        try:
            session.place(dup)
            mark("duplicate_client_id", FAIL, detail="exchange accepted a duplicate client id")
        except BrokerRejected as exc:
            mark("duplicate_client_id", PASS, reason=exc.reason, code=exc.code, message=exc.message)

        # 10. cancellation
        session.cancel(t1.client_id)
        open_ids = {o.client_id for o in broker.open_orders()}
        mark("order_cancellation", PASS if t1.client_id not in open_ids and t1.status == "CANCELED" else FAIL,
             order_status=t1.status)

        # 11. timeout: the call must surface as unavailable, and the order's state must then be resolved
        probe = exit_order("TO", STOP, STOP_MARKET, price * 0.5)
        try:
            session.place(probe, timeout=0.0001)
            mark("api_timeout_handling", FAIL, detail="no timeout was produced; handling not exercised")
            session.cancel(probe.client_id)
        except BrokerUnavailable as exc:
            found = broker.lookup(probe.client_id)
            if found is not None and found.status in ("NEW", "PARTIALLY_FILLED"):
                session.cancel(probe.client_id)
            session.record("TIMEOUT_RESOLVED", client_id=probe.client_id, found=found is not None,
                           status=found.status if found else None)
            mark("api_timeout_handling", PASS, unknown=exc.unknown, found_after_timeout=found is not None)

        # 12. an exit fill cancels the other exit
        session.cancel(s1.client_id)
        last = broker.last_price()
        width, s2, t2 = 0.0005, None, None
        for attempt in range(4):
            try:
                s2 = exit_order(f"S2{attempt}", STOP, STOP_MARKET, last * (1 - width))
                session.place(s2)
                t2 = exit_order(f"T2{attempt}", TARGET, TAKE_PROFIT_MARKET, last * (1 + width))
                session.place(t2)
                break
            except BrokerRejected:
                for o in (s2, t2):
                    if o is not None and o.status == "NEW":
                        session.cancel(o.client_id)
                s2 = t2 = None
                width *= 2
                last = broker.last_price()
        if s2 is None or t2 is None:
            mark("exit_fill_cancels_other", FAIL, detail="could not place a tight exit pair")
        else:
            deadline = clock() + exit_wait_s
            hit = None
            while hit is None and clock() < deadline:
                session.sync()
                done = [f for f in session.fills if f["client_id"] in (s2.client_id, t2.client_id)]
                if done and abs(broker.position_qty()) <= 1e-12:
                    hit = done[-1]["client_id"]
                    break
                sleep(poll_s)
            if hit is None:
                mark("exit_fill_cancels_other", FAIL, detail=f"no exit triggered within {exit_wait_s:.0f}s")
            else:
                other = t2 if hit == s2.client_id else s2
                session.cancel(other.client_id)
                open_ids = {o.client_id for o in broker.open_orders()}
                ok = other.client_id not in open_ids and abs(broker.position_qty()) <= 1e-12
                mark("exit_fill_cancels_other", PASS if ok else FAIL, filled=hit, canceled=other.client_id,
                     open=sorted(open_ids))

        # 13. terminal state refuses any further order without calling the exchange
        session.terminal = True
        session.record("SESSION_TERMINAL")
        calls = broker.calls
        try:
            session.place(Order(cid("after"), ENTRY, "BUY", MARKET, qty))
            mark("no_order_after_terminal", FAIL, detail="order sent after terminal state")
        except TerminalStateError:
            mark("no_order_after_terminal", PASS if broker.calls == calls else FAIL, calls_delta=broker.calls - calls)
    finally:
        cleanup.update(_cleanup(broker, session))

    # 14. persistence: every transition the session made is on disk
    reloaded = PaperStore(store.root, mode=store.mode)
    on_disk = {e["seq"] for e in reloaded.read("events")}
    missing = [s for s in session.seqs if s not in on_disk]
    checks["transitions_persisted"] = {"status": PASS if not missing else FAIL, "transitions": len(session.seqs),
                                       "missing": missing}
    reloaded.event("CHECK_RESULT", check="transitions_persisted", status=checks["transitions_persisted"]["status"])
    return _result(checks, session, cleanup)


def _cleanup(broker: Any, session: CheckSession) -> dict[str, Any]:
    out: dict[str, Any] = {"canceled": [], "flattened": None, "errors": []}
    try:
        for o in broker.open_orders():
            try:
                broker.cancel_order(o.client_id)
                out["canceled"].append(o.client_id)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"cancel {o.client_id}: {exc}")
        pos = broker.position_qty()
        if abs(pos) > 1e-12:
            side = "SELL" if pos > 0 else "BUY"
            flat = Order(f"c12chk_flat_{int(time.time())}", STOP, side, MARKET, abs(pos), reduce_only=True)
            broker.place_order(flat, bar_index=0)
            out["flattened"] = abs(pos)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(str(exc))
    session.store.event("CLEANUP", mode="testnet_broker_check", **out)
    return out


def _result(checks: dict[str, Any], session: CheckSession, cleanup: dict[str, Any]) -> dict[str, Any]:
    for name in REQUIRED + CONDITIONAL:
        checks.setdefault(name, {"status": FAIL, "detail": "not reached"})
    passed = all(checks[n]["status"] == PASS for n in REQUIRED) and all(
        checks[n]["status"] in (PASS, SKIPPED) for n in CONDITIONAL) and not cleanup.get("errors")
    return {
        "result": BROKER_CHECK_PASS if passed else BROKER_CHECK_FAIL,
        "mode": "testnet_broker_check",
        "label": "NON_OOS",
        "counts_toward_oos": False,
        "strategy_validation": False,
        "checks": checks,
        "cleanup": cleanup,
        "transitions": len(session.seqs),
        "store": str(session.store.root),
    }
