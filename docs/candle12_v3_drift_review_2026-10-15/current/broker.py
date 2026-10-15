"""Broker interface for the V3 paper adapter, and the simulated (dry-run) broker.

The simulated broker never leaves the process. It fills orders against closed
bars with the CANDLE12_V3 engine's conventions, so a dry run reproduces the
engine and any reconciliation exception points at the adapter, not the fill
model:

- entry stop order (resting at C1 extreme x 1.0002): fills at its stop price
  when the bar trades it; a market entry fills at the bar open with adverse
  slippage (the gap path)
- exits resting from an earlier bar: open beyond the stop -> open with slippage;
  open beyond the target -> target with slippage; otherwise stop before target
  when the bar touches both (conservative); stop or target with slippage
- exits placed on the entry bar: the same, without the open-gap rules
- taker fee on every fill's notional
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence

from app.research.candle12_v3_paper.capabilities import require_supported

ENTRY, STOP, TARGET = "ENTRY", "STOP", "TARGET"
MARKET, STOP_MARKET, TAKE_PROFIT_MARKET = "MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"
OPEN_STATUSES = ("NEW", "PARTIALLY_FILLED")

# Rejection reasons the adapter handles. Exchange error codes map onto these.
ORDER_REJECTED = "ORDER_REJECTED"
MARGIN_REJECTED = "MARGIN_REJECTED"
MIN_NOTIONAL_REJECTED = "MIN_NOTIONAL_REJECTED"
QTY_STEP_REJECTED = "QTY_STEP_REJECTED"
PRICE_BEYOND_TRIGGER = "PRICE_BEYOND_TRIGGER"
DUPLICATE_CLIENT_ID = "DUPLICATE_CLIENT_ID"
UNSUPPORTED_ENDPOINT = "UNSUPPORTED_ENDPOINT"  # -4120: conditional type sent to the plain order endpoint
AUTH_REJECTED = "AUTH_REJECTED"
NO_SUCH_ORDER = "NO_SUCH_ORDER"
REJECT_REASONS = (ORDER_REJECTED, MARGIN_REJECTED, MIN_NOTIONAL_REJECTED, QTY_STEP_REJECTED, PRICE_BEYOND_TRIGGER,
                  DUPLICATE_CLIENT_ID, UNSUPPORTED_ENDPOINT, AUTH_REJECTED, NO_SUCH_ORDER)


class BrokerRejected(Exception):
    def __init__(self, reason: str, code: int | None = None, message: str = "") -> None:
        super().__init__(f"{reason} ({code}): {message}")
        self.reason = reason
        self.code = code
        self.message = message


class BrokerUnavailable(Exception):
    """Network, exchange downtime, or a 5xx. The order state is unknown or unchanged.

    ``unknown`` is True when the request may have reached the exchange (a timeout
    after sending). The caller must look the order up before any retry.
    """

    def __init__(self, message: str = "", *, unknown: bool = False) -> None:
        super().__init__(message)
        self.unknown = unknown


@dataclass
class Order:
    client_id: str
    purpose: str  # ENTRY / STOP / TARGET
    side: str  # BUY / SELL
    type: str  # MARKET / STOP_MARKET / TAKE_PROFIT_MARKET
    qty: float
    stop_price: float | None = None
    reduce_only: bool = False
    status: str = "NEW"
    exchange_id: str | None = None
    filled_qty: float = 0.0
    avg_price: float | None = None
    placed_bar: int | None = None
    placed_on_entry_bar: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Fill:
    client_id: str
    qty: float
    price: float
    fee: float  # exchange-reported commission, quote currency, positive = paid
    bar_index: int
    gap: bool = False
    same_bar_conflict: bool = False
    trade_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FillListener(Protocol):
    def on_fill(self, order: Order, fill: Fill, bar_index: int) -> None: ...


class Broker(Protocol):
    name: str
    sends_orders: bool

    def place_order(self, order: Order, *, bar_index: int, reference_price: float | None = None) -> Order: ...

    def cancel_order(self, client_id: str) -> Order: ...

    def sync(self, bar_index: int, bar: Sequence[float], listener: FillListener) -> list[Fill]: ...

    def reported_funding(self, computed: float, *, start: datetime, end: datetime) -> float | None: ...

    # -- venue state, for restart recovery ------------------------------------

    def lookup(self, client_id: str) -> Order | None: ...

    def open_orders(self) -> list[Order]: ...

    def position_qty(self) -> float: ...

    def recent_fills(self) -> list[dict[str, Any]]: ...


def _floor_units(qty: float, step: float) -> float:
    return math.floor(qty / step + 1e-9) * step


@dataclass
class SimFaults:
    """Injected failures for tests and the smoke run. Empty means a clean venue."""

    reject: list[tuple[str, str, int | None]] = field(default_factory=list)  # (purpose, reason, code), FIFO
    unavailable_calls: int = 0  # the next N place_order calls raise BrokerUnavailable
    entry_fill_fraction: float | None = None  # partial entry fill
    fill_offset: dict[str, float] = field(default_factory=dict)  # purpose -> relative price offset
    funding_offset: float = 0.0  # added to reported funding


class SimulatedBroker:
    name = "SIMULATED"
    sends_orders = False

    def __init__(
        self,
        *,
        slippage: float = 0.0002,
        fee_rate: float = 0.0004,
        step: float | None = None,
        min_notional: float | None = None,
        equity: float = 1000.0,
        leverage: float = 2.0,
        faults: SimFaults | None = None,
    ) -> None:
        self.slippage = slippage
        self.fee_rate = fee_rate
        self.step = step
        self.min_notional = min_notional
        self.equity = equity
        self.leverage = leverage
        self.faults = faults or SimFaults()
        self.orders: dict[str, Order] = {}
        self.position = 0.0  # signed
        self._entry_phase = False
        self._fill_seq = 0
        self.fills: deque[dict[str, Any]] = deque(maxlen=200)

    # -- venue state ---------------------------------------------------------

    def lookup(self, client_id: str) -> Order | None:
        return self.orders.get(client_id)

    def open_orders(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status in OPEN_STATUSES]

    def position_qty(self) -> float:
        return self.position

    def recent_fills(self) -> list[dict[str, Any]]:
        return list(self.fills)

    def snapshot(self) -> dict[str, Any]:
        """The virtual venue's state. Persisted with the adapter so a restart sees the same venue."""
        recent = set(list(self.orders)[-20:])
        return {
            "name": self.name,
            "position": self.position,
            "fill_seq": self._fill_seq,
            # Open orders, plus the 20 most recent others (the entry and exits of the trade in hand).
            "orders": {k: o.to_dict() for k, o in self.orders.items()
                       if o.status in OPEN_STATUSES or k in recent},
            "fills": list(self.fills),
        }

    def restore(self, snap: dict[str, Any]) -> None:
        self.position = float(snap.get("position", 0.0))
        self._fill_seq = int(snap.get("fill_seq", 0))
        self.orders = {k: Order(**v) for k, v in (snap.get("orders") or {}).items()}
        self.fills = deque(snap.get("fills") or [], maxlen=200)

    # -- orders --------------------------------------------------------------

    def place_order(self, order: Order, *, bar_index: int, reference_price: float | None = None) -> Order:
        require_supported(order.type, reduce_only=order.reduce_only)
        existing = self.orders.get(order.client_id)
        if existing is not None and existing is not order:
            raise BrokerRejected(DUPLICATE_CLIENT_ID, -4116, "clientOrderId is duplicated")
        if self.faults.unavailable_calls > 0:
            self.faults.unavailable_calls -= 1
            raise BrokerUnavailable("simulated exchange downtime")
        for j, (purpose, reason, code) in enumerate(self.faults.reject):
            if purpose == order.purpose:
                self.faults.reject.pop(j)
                order.status = "REJECTED"
                raise BrokerRejected(reason, code, "simulated rejection")
        if order.qty <= 0:
            order.status = "REJECTED"
            raise BrokerRejected(QTY_STEP_REJECTED, -4003, "quantity must be positive")
        if self.step and abs(order.qty / self.step - round(order.qty / self.step)) > 1e-6:
            order.status = "REJECTED"
            raise BrokerRejected(QTY_STEP_REJECTED, -1111, "quantity precision")
        price = order.stop_price if order.stop_price is not None else reference_price
        if order.reduce_only:
            if order.qty > abs(self.position) + 1e-12:
                order.status = "REJECTED"
                raise BrokerRejected(ORDER_REJECTED, -2022, "reduce-only order exceeds position")
        elif price is not None:
            notional = order.qty * price
            if self.min_notional and notional < self.min_notional - 1e-9:
                order.status = "REJECTED"
                raise BrokerRejected(MIN_NOTIONAL_REJECTED, -4164, "notional below minimum")
            if notional / self.leverage > self.equity + 1e-9:
                order.status = "REJECTED"
                raise BrokerRejected(MARGIN_REJECTED, -2019, "margin is insufficient")
        order.status = "NEW"
        order.exchange_id = f"sim-{len(self.orders) + 1}"
        order.placed_bar = bar_index
        order.placed_on_entry_bar = self._entry_phase
        self.orders[order.client_id] = order
        return order

    def cancel_order(self, client_id: str) -> Order:
        order = self.orders[client_id]
        if order.status in OPEN_STATUSES:
            order.status = "CANCELED"
        return order

    def reported_funding(self, computed: float, *, start: datetime, end: datetime) -> float | None:
        # The simulator has no settlement feed of its own; it reports the
        # settlement-history amount (plus an injected offset for tests).
        return computed + self.faults.funding_offset

    # -- fills ---------------------------------------------------------------

    def _fill(self, order: Order, qty: float, price: float, bar_index: int, listener: FillListener,
              *, gap: bool = False, conflict: bool = False) -> Fill:
        price *= 1.0 + self.faults.fill_offset.get(order.purpose, 0.0)
        self._fill_seq += 1
        fill = Fill(
            client_id=order.client_id, qty=qty, price=price, fee=self.fee_rate * qty * price,
            bar_index=bar_index, gap=gap, same_bar_conflict=conflict, trade_id=f"simfill-{self._fill_seq}",
        )
        prev = order.filled_qty
        order.filled_qty = prev + qty
        order.avg_price = ((order.avg_price or 0.0) * prev + price * qty) / order.filled_qty
        order.status = "FILLED" if order.filled_qty >= order.qty - 1e-12 else "PARTIALLY_FILLED"
        signed = qty if order.side == "BUY" else -qty
        self.position += signed
        self.fills.append({**fill.to_dict(), "purpose": order.purpose})
        listener.on_fill(order, fill, bar_index)
        return fill

    def _adverse(self, side: str, price: float) -> float:
        return price * (1.0 + self.slippage) if side == "BUY" else price * (1.0 - self.slippage)

    def _resolve_exits(self, exits: list[Order], bar: Sequence[float], bar_index: int,
                       listener: FillListener, *, entry_bar: bool) -> list[Fill]:
        if not exits or abs(self.position) <= 1e-12:
            return []
        o, h, l, _ = bar
        long_pos = self.position > 0
        stops = [x for x in exits if x.purpose == STOP]
        targets = [x for x in exits if x.purpose == TARGET]

        def beyond_stop(px: float, x: Order) -> bool:
            return px < x.stop_price if long_pos else px > x.stop_price  # type: ignore[operator]

        def beyond_target(px: float, x: Order) -> bool:
            return px > x.stop_price if long_pos else px < x.stop_price  # type: ignore[operator]

        def stop_touched(x: Order) -> bool:
            return l <= x.stop_price if long_pos else h >= x.stop_price  # type: ignore[operator]

        def target_touched(x: Order) -> bool:
            return h >= x.stop_price if long_pos else l <= x.stop_price  # type: ignore[operator]

        chosen, raw, gap, conflict = None, None, False, False
        if not entry_bar:
            gap_stop = [x for x in stops if beyond_stop(o, x)]
            gap_tgt = [x for x in targets if beyond_target(o, x)]
            if gap_stop:
                chosen, raw, gap = gap_stop[0], o, True
            elif gap_tgt:
                chosen = gap_tgt[0]
                raw, gap = chosen.stop_price, True
        if chosen is None:
            hit_stop = [x for x in stops if stop_touched(x)]
            hit_tgt = [x for x in targets if target_touched(x)]
            if hit_stop:
                chosen = hit_stop[0]
                raw, conflict = chosen.stop_price, bool(hit_tgt)
            elif hit_tgt:
                chosen = hit_tgt[0]
                raw = chosen.stop_price
        if chosen is None:
            return []
        qty = min(chosen.qty - chosen.filled_qty, abs(self.position))
        return [self._fill(chosen, qty, self._adverse(chosen.side, float(raw)), bar_index, listener,
                           gap=gap, conflict=conflict)]

    def sync(self, bar_index: int, bar: Sequence[float], listener: FillListener) -> list[Fill]:
        o, h, l, _ = bar
        fills: list[Fill] = []
        resting = [x for x in self.orders.values()
                   if x.reduce_only and x.status in OPEN_STATUSES and (x.placed_bar or 0) < bar_index]
        fills += self._resolve_exits(resting, bar, bar_index, listener, entry_bar=False)

        entries = [x for x in self.orders.values()
                   if x.purpose == ENTRY and x.status in OPEN_STATUSES and x.placed_bar == bar_index]
        self._entry_phase = True
        try:
            for e in entries:
                if e.type == MARKET:
                    price, gap = self._adverse(e.side, o), True
                else:
                    buy = e.side == "BUY"
                    triggered = h >= e.stop_price if buy else l <= e.stop_price  # type: ignore[operator]
                    if not triggered:
                        continue
                    at_open = o >= e.stop_price if buy else o <= e.stop_price  # type: ignore[operator]
                    price, gap = (self._adverse(e.side, o), True) if at_open else (float(e.stop_price), False)  # type: ignore[arg-type]
                qty = e.qty - e.filled_qty
                if self.faults.entry_fill_fraction is not None:
                    part = e.qty * self.faults.entry_fill_fraction
                    qty = _floor_units(part, self.step) if self.step else part
                if qty <= 0:
                    continue
                fills.append(self._fill(e, qty, price, bar_index, listener, gap=gap))
        finally:
            self._entry_phase = False

        fresh = [x for x in self.orders.values()
                 if x.reduce_only and x.status in OPEN_STATUSES and x.placed_bar == bar_index and x.placed_on_entry_bar]
        fills += self._resolve_exits(fresh, bar, bar_index, listener, entry_bar=True)
        return fills
