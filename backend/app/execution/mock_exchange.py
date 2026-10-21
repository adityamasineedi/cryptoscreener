"""Deterministic in-repo fake of Binance USD-M Futures (one-way mode) for the execution adapter (D128c).

The default exchange everywhere: tests, dry runs and development. No network, no keys.

Behaviour modelled on Binance:
- MARKET fills at once at the current mark. LIMIT rests and fills when the mark crosses its price. A test can
  fill any resting order partially with ``fill_partial``.
- STOP_MARKET / TAKE_PROFIT_MARKET trigger on the mark (``working_type`` MARK_PRICE) and fill at the mark. A SELL
  stop triggers at mark <= stop, a BUY stop at mark >= stop, a SELL take-profit at mark >= stop, and a BUY
  take-profit at mark <= stop.
- Reduce-only orders never open or grow a position. Placing one against a flat position is refused (-2022). A
  triggered one is capped to the open size, and EXPIRED if the position is already flat.
- A reused client order id is refused (-4116), so retries must look the original up.
- Cancel of a filled or cancelled order is refused (-2011). Amend works on a resting LIMIT order only (as Binance:
  stop orders are cancel-and-replace).
- Fault injection: ``fail_next_place(n, applied=True)`` makes the next n place calls raise
  TransientExchangeError, by default AFTER the exchange accepted the order (a lost acknowledgement, the
  idempotency case).
"""

from __future__ import annotations

from dataclasses import replace

from app.execution.interface import (
    DuplicateClientOrderId,
    ExchangeClient,
    ExchangeError,
    OrderNotFound,
    ReduceOnlyRejected,
    TransientExchangeError,
)
from app.execution.models import (
    BUY,
    CANCELED,
    EXPIRED,
    FILLED,
    LIMIT,
    MARKET,
    NEW,
    PARTIALLY_FILLED,
    QTY_EPS,
    SELL,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    Fill,
    Order,
    OrderRequest,
    Position,
)


class MockBinanceFutures(ExchangeClient):
    def __init__(self, marks: dict[str, float] | None = None) -> None:
        self.marks: dict[str, float] = {k.upper(): float(v) for k, v in (marks or {}).items()}
        self._orders: dict[tuple[str, str], Order] = {}
        self._positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self._next_order_id = 1000
        self._seq = 0
        self._fail_place: list[bool] = []  # one entry per injected failure: True = applied before failing
        self.place_calls = 0
        self.accepted = 0

    # --- test controls ---------------------------------------------------------------------------------------

    def fail_next_place(self, n: int = 1, *, applied: bool = True) -> None:
        self._fail_place.extend([bool(applied)] * int(n))

    def set_mark(self, symbol: str, price: float) -> list[Fill]:
        """Move the mark and run every trigger and resting LIMIT that it crosses (oldest order first)."""
        sym = symbol.upper()
        self.marks[sym] = float(price)
        out: list[Fill] = []
        for order in sorted((o for o in self._orders.values() if o.symbol == sym and o.is_open), key=lambda o: o.order_id):
            if order.type in (STOP_MARKET, TAKE_PROFIT_MARKET) and self._triggered(order, price):
                out += self._fill(order, order.remaining, float(price))
            elif order.type == LIMIT and self._limit_crossed(order, price):
                out += self._fill(order, order.remaining, float(order.price))
        return out

    def fill_partial(self, symbol: str, client_order_id: str, qty: float, price: float | None = None) -> list[Fill]:
        order = self._get(symbol, client_order_id)
        if not order.is_open:
            raise ExchangeError("order not open", code=-2011)
        px = float(price if price is not None else (order.price or self.marks[order.symbol]))
        return self._fill(order, min(float(qty), order.remaining), px)

    def force_position(self, symbol: str, qty: float, entry_price: float = 0.0) -> None:
        """Simulate a change made outside the adapter (e.g. a manual close in the exchange UI)."""
        self._positions[symbol.upper()] = Position(symbol.upper(), float(qty), float(entry_price))

    # --- ExchangeClient --------------------------------------------------------------------------------------

    def place_order(self, req: OrderRequest) -> Order:
        self.place_calls += 1
        applied_then_fail = self._fail_place.pop(0) if self._fail_place else None
        if applied_then_fail is False:
            raise TransientExchangeError("timeout before the exchange saw the order")
        key = (req.symbol.upper(), req.client_order_id)
        if key in self._orders:
            raise DuplicateClientOrderId(f"client order id {req.client_order_id} is duplicated", code=-4116)
        sym = req.symbol.upper()
        if req.reduce_only and abs(self._pos(sym).qty) <= QTY_EPS and req.type == MARKET:
            raise ReduceOnlyRejected("reduce-only order rejected: no position", code=-2022)
        if req.type == MARKET and sym not in self.marks:
            raise ExchangeError(f"no mark price for {sym}", code=-1121)
        self._next_order_id += 1
        order = Order(symbol=sym, side=req.side, type=req.type, quantity=float(req.quantity),
                      client_order_id=req.client_order_id, order_id=self._next_order_id, status=NEW,
                      price=req.price, stop_price=req.stop_price, reduce_only=bool(req.reduce_only))
        self._orders[key] = order
        self.accepted += 1
        if req.type == MARKET:
            self._fill(order, order.quantity, self.marks[sym])
        elif req.type == LIMIT and sym in self.marks and self._limit_crossed(order, self.marks[sym]):
            self._fill(order, order.quantity, float(order.price))
        elif req.type in (STOP_MARKET, TAKE_PROFIT_MARKET) and sym in self.marks and self._triggered(order, self.marks[sym]):
            # Binance refuses a stop that would trigger immediately (-2021).
            del self._orders[key]
            self.accepted -= 1
            raise ExchangeError("order would immediately trigger", code=-2021)
        if applied_then_fail:
            raise TransientExchangeError("timeout after the exchange accepted the order")
        return replace(order)

    def cancel_order(self, symbol: str, client_order_id: str) -> Order:
        order = self._get(symbol, client_order_id)
        if not order.is_open:
            raise OrderNotFound(f"order {client_order_id} is {order.status}; cannot cancel", code=-2011)
        order.status = CANCELED
        self._bump(order)
        return replace(order)

    def amend_order(self, symbol: str, client_order_id: str, *, quantity: float, price: float) -> Order:
        order = self._get(symbol, client_order_id)
        if order.type != LIMIT or not order.is_open:
            raise ExchangeError("only a resting LIMIT order can be modified", code=-4028)
        if float(quantity) + QTY_EPS < order.executed_qty or float(price) <= 0:
            raise ExchangeError("quantity below the filled amount, or a bad price", code=-4003)
        order.quantity, order.price = float(quantity), float(price)
        self._bump(order)
        if order.remaining <= QTY_EPS:
            order.status = FILLED
        elif self.marks.get(order.symbol) is not None and self._limit_crossed(order, self.marks[order.symbol]):
            self._fill(order, order.remaining, float(order.price))
        return replace(order)

    def get_order(self, symbol: str, client_order_id: str) -> Order:
        return replace(self._get(symbol, client_order_id))

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        sym = symbol.upper() if symbol else None
        return [replace(o) for o in sorted(self._orders.values(), key=lambda o: o.order_id)
                if o.is_open and (sym is None or o.symbol == sym)]

    def positions(self) -> dict[str, Position]:
        return {s: replace(p) for s, p in sorted(self._positions.items()) if abs(p.qty) > QTY_EPS}

    # --- internals -------------------------------------------------------------------------------------------

    def _get(self, symbol: str, client_order_id: str) -> Order:
        order = self._orders.get((symbol.upper(), client_order_id))
        if order is None:
            raise OrderNotFound(f"unknown order {client_order_id}", code=-2013)
        return order

    def _pos(self, sym: str) -> Position:
        return self._positions.setdefault(sym, Position(sym))

    def _bump(self, order: Order) -> None:
        self._seq += 1
        order.update_seq = self._seq

    @staticmethod
    def _triggered(order: Order, mark: float) -> bool:
        stop = float(order.stop_price or 0.0)
        if order.type == STOP_MARKET:
            return mark <= stop if order.side == SELL else mark >= stop
        return mark >= stop if order.side == SELL else mark <= stop  # TAKE_PROFIT_MARKET

    @staticmethod
    def _limit_crossed(order: Order, mark: float) -> bool:
        return mark <= float(order.price) if order.side == BUY else mark >= float(order.price)

    def _fill(self, order: Order, qty: float, price: float) -> list[Fill]:
        pos = self._pos(order.symbol)
        qty = float(qty)
        if order.reduce_only:
            reducing = (order.side == SELL and pos.qty > QTY_EPS) or (order.side == BUY and pos.qty < -QTY_EPS)
            qty = min(qty, abs(pos.qty)) if reducing else 0.0
            if qty <= QTY_EPS:
                order.status = EXPIRED
                self._bump(order)
                return []
        signed = qty if order.side == BUY else -qty
        old = pos.qty
        new = old + signed
        if old == 0 or (old > 0) == (signed > 0):  # open or add: weighted entry
            pos.entry_price = (abs(old) * pos.entry_price + qty * price) / abs(new) if abs(new) > QTY_EPS else 0.0
        else:  # reduce, close or flip
            closed = min(abs(signed), abs(old))
            pos.realized_pnl += closed * (price - pos.entry_price) * (1 if old > 0 else -1)
            if abs(new) > QTY_EPS and (new > 0) != (old > 0):
                pos.entry_price = price
            elif abs(new) <= QTY_EPS:
                pos.entry_price = 0.0
        pos.qty = 0.0 if abs(new) <= QTY_EPS else new
        total = order.executed_qty + qty
        order.avg_price = (order.executed_qty * order.avg_price + qty * price) / total
        order.executed_qty = total
        order.status = FILLED if order.remaining <= QTY_EPS else PARTIALLY_FILLED
        if order.reduce_only and order.status == PARTIALLY_FILLED and abs(pos.qty) <= QTY_EPS:
            order.status = EXPIRED  # nothing left to reduce
        self._bump(order)
        self._seq += 1
        fill = Fill(order.order_id, order.client_order_id, order.symbol, order.side, qty, float(price), self._seq)
        self.fills.append(fill)
        return [fill]
