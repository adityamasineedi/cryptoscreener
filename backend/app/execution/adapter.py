"""Exchange execution adapter (owner ruling D128c). Mock exchange by default; Binance Futures testnet only (C1 v2.5).

What it does, for one strategy signal (a "bracket"):
- **Entry.** MARKET or LIMIT, with a deterministic client order id: ``ids.client_order_id(strategy, signal, "EN")``.
- **Exchange-side protection.** As soon as any quantity is filled: a reduce-only STOP_MARKET and an optional
  reduce-only TAKE_PROFIT_MARKET for exactly the filled quantity. They are resized (cancel and replace with the
  next version id) when more of the entry fills. When one of them fills, the other is cancelled (OCO emulation).
- **Idempotent retries.**
  - A timeout or 5xx retries the same id.
  - If the exchange already has that id (a lost acknowledgement), the original order is fetched instead.
  - A restart that re-runs the same signal therefore never creates a second order.
- **Amend.**
  - A resting LIMIT entry is modified in place.
  - A stop or take-profit is cancel-and-replace (Binance cannot modify stop orders). If the old one has already
    filled, nothing is replaced.
- **Position sync and reconciliation.** ``reconcile(ledger)`` compares every exchange position with the ledger's
  signed quantity. It also checks that each exchange position has a reduce-only stop covering it. Any mismatch
  calls ``mismatch_hook``: the D128d ``KillSwitch.on_position_mismatch``, which trips, flattens the paper book and
  blocks entries. ``ReconciliationLoop`` runs it every 60 s.

Nothing here is wired into live paper. The adapter is a library; only a future, owner-approved change would call it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.execution.ids import client_order_id
from app.execution.interface import (
    DuplicateClientOrderId,
    ExchangeClient,
    ExchangeError,
    OrderNotFound,
    TransientExchangeError,
)
from app.execution.models import (
    BUY,
    FILLED,
    LIMIT,
    MARKET,
    QTY_EPS,
    SELL,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    Bracket,
    Order,
    OrderRequest,
)

logger = logging.getLogger("execution.adapter")

MAX_REPLACE_VERSIONS = 50


@dataclass
class Mismatch:
    symbol: str
    kind: str  # qty_mismatch | missing_stop
    exchange_qty: float
    ledger_qty: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "kind": self.kind, "exchange_qty": self.exchange_qty,
                "ledger_qty": self.ledger_qty, "detail": self.detail}


@dataclass
class ReconcileResult:
    ok: bool
    mismatches: list[Mismatch] = field(default_factory=list)
    exchange: dict[str, float] = field(default_factory=dict)
    ledger: dict[str, float] = field(default_factory=dict)
    hook_result: Any = None


class ExecutionAdapter:
    def __init__(
        self,
        client: ExchangeClient,
        *,
        mismatch_hook: Callable[[dict[str, Any]], Any] | None = None,
        max_retries: int = 3,
        qty_tolerance: float = 1e-9,
    ) -> None:
        self.client = client
        self.mismatch_hook = mismatch_hook
        self.max_retries = int(max_retries)
        self.qty_tolerance = float(qty_tolerance)
        self.brackets: dict[tuple[str, str], Bracket] = {}

    # --- idempotent placement --------------------------------------------------------------------------------

    def place(self, req: OrderRequest) -> Order:
        """Place once. Retries a transient error with the same client id; a known id returns the original order."""
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.client.place_order(req)
            except DuplicateClientOrderId:
                return self.client.get_order(req.symbol, req.client_order_id)
            except TransientExchangeError as exc:
                last = exc
                try:  # was it applied before the error?
                    return self.client.get_order(req.symbol, req.client_order_id)
                except OrderNotFound:
                    logger.warning("place retry %s/%s for %s: %s", attempt + 1, self.max_retries, req.client_order_id, exc)
                    continue
        raise TransientExchangeError(f"gave up placing {req.client_order_id}: {last}")

    # --- brackets --------------------------------------------------------------------------------------------

    def open_bracket(
        self,
        *,
        strategy: str,
        signal_key: str,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        tp_price: float | None = None,
        entry_type: str = MARKET,
        limit_price: float | None = None,
    ) -> Bracket:
        side = side.upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")
        key = (strategy, signal_key)
        b = self.brackets.get(key)
        if b is None:
            b = Bracket(strategy, signal_key, symbol.upper(), side, float(quantity), float(stop_price),
                        None if tp_price is None else float(tp_price))
            self.brackets[key] = b
        req = OrderRequest(symbol=b.symbol, side=b.entry_side, type=entry_type, quantity=b.quantity,
                           client_order_id=client_order_id(strategy, signal_key, "EN"),
                           price=limit_price if entry_type == LIMIT else None,
                           time_in_force="GTC" if entry_type == LIMIT else None)
        b.entry = self.place(req)
        b.events.append(f"entry:{b.entry.status}:{b.entry.executed_qty}")
        self.sync_protection(b)
        return b

    def _protect_req(self, b: Bracket, leg: str, version: int, qty: float) -> OrderRequest:
        typ, px = (STOP_MARKET, b.stop_price) if leg == "SL" else (TAKE_PROFIT_MARKET, b.tp_price)
        return OrderRequest(symbol=b.symbol, side=b.exit_side, type=typ, quantity=qty, stop_price=px,
                            reduce_only=True, client_order_id=client_order_id(b.strategy, b.signal_key, leg, version))

    def _ensure_leg(self, b: Bracket, leg: str, qty: float) -> None:
        attr, vattr = ("stop", "stop_version") if leg == "SL" else ("take_profit", "tp_version")
        current: Order | None = getattr(b, attr)
        if current is not None:
            current = self.client.get_order(b.symbol, current.client_order_id)
            setattr(b, attr, current)
            if current.status == FILLED:
                return
            if current.is_open and abs(current.quantity - qty) <= QTY_EPS:
                return
            if current.is_open:  # size changed (a later partial fill): cancel, then replace with the next version
                try:
                    self.client.cancel_order(b.symbol, current.client_order_id)
                except OrderNotFound:
                    current = self.client.get_order(b.symbol, current.client_order_id)
                    setattr(b, attr, current)
                    if current.status == FILLED:
                        return
                setattr(b, vattr, getattr(b, vattr) + 1)
        for _ in range(MAX_REPLACE_VERSIONS):
            order = self.place(self._protect_req(b, leg, getattr(b, vattr), qty))
            if order.is_open or order.status == FILLED:
                setattr(b, attr, order)
                b.events.append(f"{leg}v{getattr(b, vattr)}:{order.status}:{order.quantity}")
                return
            # That version exists but is cancelled or expired (e.g. after a restart): use the next one.
            setattr(b, vattr, getattr(b, vattr) + 1)
        raise ExchangeError(f"could not place {leg} for {b.signal_key}")

    def sync_protection(self, b: Bracket) -> Bracket:
        """Bring the exchange-side stop/TP in line with what is filled; cancel the sibling of a filled exit."""
        if b.entry is None:
            return b
        b.entry = self.client.get_order(b.symbol, b.entry.client_order_id)
        exits = [o for o in (b.stop, b.take_profit) if o is not None]
        exits = [self.client.get_order(b.symbol, o.client_order_id) for o in exits]
        if any(o.status == FILLED or (o.executed_qty > QTY_EPS and not o.is_open) for o in exits):
            for o in exits:  # an exit filled: the bracket is done; cancel whatever is still open (OCO)
                if o.is_open:
                    try:
                        self.client.cancel_order(b.symbol, o.client_order_id)
                    except OrderNotFound:
                        pass
            b.stop = self.client.get_order(b.symbol, b.stop.client_order_id) if b.stop else None
            b.take_profit = self.client.get_order(b.symbol, b.take_profit.client_order_id) if b.take_profit else None
            b.events.append("closed_by_exchange_exit")
            return b
        filled = b.entry.executed_qty
        if filled <= QTY_EPS:
            return b
        self._ensure_leg(b, "SL", filled)
        if b.tp_price is not None:
            self._ensure_leg(b, "TP", filled)
        return b

    def amend_stop(self, b: Bracket, new_stop: float) -> Bracket:
        """Move the exchange-side stop (cancel and replace with the next version id)."""
        b.stop_price = float(new_stop)
        if b.stop is not None:
            cur = self.client.get_order(b.symbol, b.stop.client_order_id)
            if cur.status == FILLED:
                b.stop = cur
                b.events.append("amend_stop_skipped:already_filled")
                return self.sync_protection(b)
            if cur.is_open:
                try:
                    self.client.cancel_order(b.symbol, cur.client_order_id)
                except OrderNotFound:
                    b.stop = self.client.get_order(b.symbol, cur.client_order_id)
                    return self.sync_protection(b)
            b.stop_version += 1
            b.stop = None
        b.events.append(f"amend_stop:{new_stop}")
        return self.sync_protection(b)

    def amend_entry(self, b: Bracket, *, quantity: float, price: float) -> Bracket:
        """Modify a resting LIMIT entry in place."""
        if b.entry is None or b.entry.type != LIMIT:
            raise ExchangeError("only a LIMIT entry can be amended")
        b.entry = self.client.amend_order(b.symbol, b.entry.client_order_id, quantity=quantity, price=price)
        b.quantity = float(quantity)
        b.events.append(f"amend_entry:{quantity}@{price}")
        return self.sync_protection(b)

    def cancel_entry(self, b: Bracket) -> Bracket:
        """Cancel the unfilled rest of the entry; protection stays sized to what filled."""
        if b.entry is not None and b.entry.is_open:
            try:
                b.entry = self.client.cancel_order(b.symbol, b.entry.client_order_id)
            except OrderNotFound:
                pass
        return self.sync_protection(b)

    def close_bracket(self, b: Bracket, *, reason: str = "exit") -> Order | None:
        """Cancel the protection and close what this bracket holds with a reduce-only MARKET order."""
        for o in (b.stop, b.take_profit):
            if o is not None:
                try:
                    self.client.cancel_order(b.symbol, o.client_order_id)
                except OrderNotFound:
                    pass
        if b.entry is not None and b.entry.is_open:
            self.cancel_entry(b)
        pos = self.client.positions().get(b.symbol)
        held = min(abs(pos.qty), b.entry.executed_qty if b.entry else 0.0) if pos else 0.0
        b.events.append(f"close:{reason}")
        if held <= QTY_EPS:
            return None
        req = OrderRequest(symbol=b.symbol, side=b.exit_side, type=MARKET, quantity=held, reduce_only=True,
                           client_order_id=client_order_id(b.strategy, b.signal_key, "XT"))
        return self.place(req)

    def poll(self) -> None:
        """Re-sync every bracket (after marks moved or fills arrived)."""
        for b in list(self.brackets.values()):
            self.sync_protection(b)

    # --- position sync and reconciliation --------------------------------------------------------------------

    def sync_positions(self) -> dict[str, float]:
        return {s: p.qty for s, p in self.client.positions().items()}

    def reconcile(self, ledger: Mapping[str, float]) -> ReconcileResult:
        exch = self.sync_positions()
        led = {str(k).upper(): float(v) for k, v in ledger.items() if abs(float(v)) > QTY_EPS}
        mismatches: list[Mismatch] = []
        for sym in sorted(set(exch) | set(led)):
            e, l_ = exch.get(sym, 0.0), led.get(sym, 0.0)
            if abs(e - l_) > self.qty_tolerance:
                mismatches.append(Mismatch(sym, "qty_mismatch", e, l_, f"exchange {e} vs ledger {l_}"))
        for sym, qty in sorted(exch.items()):
            exit_side = SELL if qty > 0 else BUY
            cover = sum(o.remaining for o in self.client.open_orders(sym)
                        if o.type == STOP_MARKET and o.reduce_only and o.side == exit_side)
            if cover + self.qty_tolerance < abs(qty):
                mismatches.append(Mismatch(sym, "missing_stop", qty, led.get(sym, 0.0),
                                           f"reduce-only stop covers {cover} of {abs(qty)}"))
        result = ReconcileResult(ok=not mismatches, mismatches=mismatches, exchange=exch, ledger=led)
        if mismatches and self.mismatch_hook is not None:
            result.hook_result = self.mismatch_hook({"source": "execution_reconcile",
                                                     "mismatches": [m.to_dict() for m in mismatches]})
        return result


class ReconciliationLoop:
    """Exchange position vs ledger every ``interval_s`` (default 60 s); a mismatch fires the adapter's hook."""

    def __init__(self, adapter: ExecutionAdapter, ledger_fn: Callable[[], Mapping[str, float]], *,
                 interval_s: float = 60.0) -> None:
        self.adapter = adapter
        self.ledger_fn = ledger_fn
        self.interval_s = float(interval_s)
        self.runs = 0
        self.last: ReconcileResult | None = None

    def run_once(self) -> ReconcileResult:
        self.adapter.poll()
        self.last = self.adapter.reconcile(self.ledger_fn())
        self.runs += 1
        return self.last

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - a failed check is logged and retried next interval
                logger.warning("reconcile failed: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass


def ledger_positions_from_engine(engine: Any, *, streams: set[str] | None = None) -> dict[str, float]:
    """Signed quantity per symbol of the paper ledger's open positions (optionally only some streams)."""
    out: dict[str, float] = {}
    with engine._lock:  # noqa: SLF001
        for pos in engine._open.values():  # noqa: SLF001
            if streams is not None and engine.stream_of(pos) not in streams:
                continue
            q = float(pos.quantity) * (1.0 if str(pos.side).upper() == "LONG" else -1.0)
            out[pos.symbol] = out.get(pos.symbol, 0.0) + q
    return out
