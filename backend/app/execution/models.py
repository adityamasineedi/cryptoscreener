"""Order, fill and position records shared by the execution adapter, the mock exchange and the testnet client.

Binance USD-M Futures vocabulary (one-way mode, positionSide BOTH). Quantities are base-asset units; a position's
``qty`` is signed (long > 0, short < 0). No real orders: C1 v2.5 allows the Binance Futures testnet only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

BUY, SELL = "BUY", "SELL"
MARKET, LIMIT = "MARKET", "LIMIT"
STOP_MARKET, TAKE_PROFIT_MARKET = "STOP_MARKET", "TAKE_PROFIT_MARKET"
ORDER_TYPES = (MARKET, LIMIT, STOP_MARKET, TAKE_PROFIT_MARKET)

NEW, PARTIALLY_FILLED, FILLED, CANCELED, EXPIRED, REJECTED = (
    "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "REJECTED")
OPEN_STATES = (NEW, PARTIALLY_FILLED)
FINAL_STATES = (FILLED, CANCELED, EXPIRED, REJECTED)

QTY_EPS = 1e-12


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    type: str
    quantity: float
    client_order_id: str
    price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False
    time_in_force: str | None = None  # LIMIT: GTC
    working_type: str = "MARK_PRICE"  # stop/TP trigger on the mark price

    def __post_init__(self) -> None:
        if self.side not in (BUY, SELL):
            raise ValueError(f"side must be BUY or SELL, got {self.side!r}")
        if self.type not in ORDER_TYPES:
            raise ValueError(f"unsupported order type {self.type!r}")
        if not (self.quantity > 0):
            raise ValueError("quantity must be > 0")
        if self.type == LIMIT and not (self.price and self.price > 0):
            raise ValueError("LIMIT needs a price")
        if self.type in (STOP_MARKET, TAKE_PROFIT_MARKET) and not (self.stop_price and self.stop_price > 0):
            raise ValueError(f"{self.type} needs a stop_price")


@dataclass
class Order:
    symbol: str
    side: str
    type: str
    quantity: float
    client_order_id: str
    order_id: int
    status: str = NEW
    price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False
    executed_qty: float = 0.0
    avg_price: float = 0.0
    update_seq: int = 0

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATES

    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.executed_qty)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Fill:
    order_id: int
    client_order_id: str
    symbol: str
    side: str
    qty: float
    price: float
    seq: int


@dataclass
class Position:
    symbol: str
    qty: float = 0.0  # signed
    entry_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def side(self) -> str:
        if self.qty > QTY_EPS:
            return "LONG"
        if self.qty < -QTY_EPS:
            return "SHORT"
        return "FLAT"


@dataclass
class Bracket:
    """One strategy entry and its exchange-side protection."""

    strategy: str
    signal_key: str
    symbol: str
    side: str  # LONG / SHORT
    quantity: float
    stop_price: float
    tp_price: float | None
    entry: Order | None = None
    stop: Order | None = None
    take_profit: Order | None = None
    stop_version: int = 1
    tp_version: int = 1
    events: list[str] = field(default_factory=list)

    @property
    def entry_side(self) -> str:
        return BUY if self.side == "LONG" else SELL

    @property
    def exit_side(self) -> str:
        return SELL if self.side == "LONG" else BUY
