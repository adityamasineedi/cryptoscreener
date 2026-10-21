"""The exchange interface the execution adapter talks to (D128c).

Two implementations: ``MockBinanceFutures`` (in-repo, deterministic; the default everywhere, all tests) and
``BinanceFuturesTestnetClient`` (Binance Futures TESTNET only, inert unless the owner enables it; C1 v2.5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.execution.models import Order, OrderRequest, Position


class ExchangeError(Exception):
    """Any exchange-side refusal."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class DuplicateClientOrderId(ExchangeError):
    """The client order id is already used (Binance -4116 / -2010 duplicate)."""


class OrderNotFound(ExchangeError):
    """No order with that id (Binance -2013), or it can no longer be cancelled (-2011)."""


class ReduceOnlyRejected(ExchangeError):
    """A reduce-only order would increase or open a position (Binance -2022)."""


class TransientExchangeError(ExchangeError):
    """Timeout, 5xx or a dropped connection: the request may or may not have been applied."""


class ExchangeClient(ABC):
    @abstractmethod
    def place_order(self, req: OrderRequest) -> Order: ...

    @abstractmethod
    def cancel_order(self, symbol: str, client_order_id: str) -> Order: ...

    @abstractmethod
    def amend_order(self, symbol: str, client_order_id: str, *, quantity: float, price: float) -> Order:
        """Modify a resting LIMIT order in place (Binance PUT /fapi/v1/order). Stop orders are cancel-and-replace."""

    @abstractmethod
    def get_order(self, symbol: str, client_order_id: str) -> Order: ...

    @abstractmethod
    def open_orders(self, symbol: str | None = None) -> list[Order]: ...

    @abstractmethod
    def positions(self) -> dict[str, Position]: ...
