"""Binance USD-M futures TESTNET broker for the V3 paper adapter.

Live trading is impossible here: any host other than a futures testnet is
refused, and construction needs CANDLE12_V3_PAPER_ENABLE=1 (the CLI also needs
``--paper``). This client has not been run against the testnet; verify the
order endpoints against current Binance documentation before the first
supervised run.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import time
from datetime import datetime
from typing import Any, Sequence
from urllib.parse import urlencode, urlparse

import httpx

from app.research.candle12_v3_paper.broker import (
    MARGIN_REJECTED,
    MIN_NOTIONAL_REJECTED,
    OPEN_STATUSES,
    ORDER_REJECTED,
    PRICE_BEYOND_TRIGGER,
    QTY_STEP_REJECTED,
    BrokerRejected,
    BrokerUnavailable,
    Fill,
    FillListener,
    Order,
)

PAPER_ENV = "CANDLE12_V3_PAPER_ENABLE"
TESTNET_HOSTS = ("testnet.binancefuture.com", "demo-fapi.binance.com")
TESTNET_BASE = "https://testnet.binancefuture.com"

# Binance error code -> adapter rejection reason.
ERROR_REASONS: dict[int, str] = {
    -2019: MARGIN_REJECTED,  # Margin is insufficient
    -4164: MIN_NOTIONAL_REJECTED,  # Order's notional must be no smaller than the minimum
    -1111: QTY_STEP_REJECTED,  # Precision is over the maximum defined for this asset
    -4003: QTY_STEP_REJECTED,  # Quantity less than or equal to zero
    -1013: QTY_STEP_REJECTED,  # Filter failure (LOT_SIZE)
    -2021: PRICE_BEYOND_TRIGGER,  # Order would immediately trigger
    -2022: ORDER_REJECTED,  # ReduceOnly order is rejected
}
UNAVAILABLE_CODES = (-1001, -1003, -1007, -1008)


class PaperGuardError(RuntimeError):
    pass


def assert_testnet(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    if host not in TESTNET_HOSTS:
        raise PaperGuardError(f"refusing {host!r}: CANDLE12_V3 paper orders go to a futures testnet only")


class BinanceTestnetBroker:
    name = "BINANCE_USDM_TESTNET"
    sends_orders = True

    def __init__(self, *, symbol: str, api_key: str, api_secret: str, base_url: str = TESTNET_BASE,
                 client: httpx.Client | None = None, recv_window: int = 5000, fee_rate: float = 0.0004,
                 tick: float = 0.1) -> None:
        if os.environ.get(PAPER_ENV) != "1":
            raise PaperGuardError(f"set {PAPER_ENV}=1 to send paper orders")
        assert_testnet(base_url)
        if not api_key or not api_secret:
            raise PaperGuardError("testnet API key and secret are required")
        self.symbol = symbol.upper()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.client = client or httpx.Client(timeout=10.0)
        self.recv_window = recv_window
        self.fee_rate = fee_rate
        self.tick = tick
        self.orders: dict[str, Order] = {}
        self.seen_trades: set[str] = set()

    # -- transport -----------------------------------------------------------

    def _signed(self, method: str, path: str, params: dict[str, Any]) -> Any:
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": self.recv_window}
        query = urlencode(params)
        sig = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        try:
            resp = self.client.request(method, url, headers={"X-MBX-APIKEY": self.api_key})
        except httpx.HTTPError as exc:
            raise BrokerUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            raise BrokerUnavailable(f"HTTP {resp.status_code}")
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            code = int(body.get("code", 0)) if isinstance(body, dict) else 0
            if code in UNAVAILABLE_CODES:
                raise BrokerUnavailable(f"{code}: {body.get('msg')}")
            raise BrokerRejected(ERROR_REASONS.get(code, ORDER_REJECTED), code, str(body.get("msg", "")))
        return body

    @staticmethod
    def tick_price(order: Order, tick: float) -> float:
        """Exchange tick grid, rounded against the trader: buys up, sells down."""
        units = order.stop_price / tick  # type: ignore[operator]
        units = math.ceil(units - 1e-9) if order.side == "BUY" else math.floor(units + 1e-9)
        return round(units * tick, 10)

    @staticmethod
    def order_params(order: Order, symbol: str, tick: float = 0.1) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": order.side,
            "type": order.type,
            "quantity": f"{order.qty:.8f}".rstrip("0").rstrip("."),
            "newClientOrderId": order.client_id[-36:],
        }
        if order.stop_price is not None:
            params["stopPrice"] = f"{BinanceTestnetBroker.tick_price(order, tick):.8f}".rstrip("0").rstrip(".")
            params["workingType"] = "CONTRACT_PRICE"
        if order.reduce_only:
            params["reduceOnly"] = "true"
        return params

    # -- broker interface ----------------------------------------------------

    def place_order(self, order: Order, *, bar_index: int, reference_price: float | None = None) -> Order:
        body = self._signed("POST", "/fapi/v1/order", self.order_params(order, self.symbol, self.tick))
        order.exchange_id = str(body.get("orderId"))
        order.status = str(body.get("status", "NEW"))
        order.placed_bar = bar_index
        self.orders[order.client_id] = order
        return order

    def cancel_order(self, client_id: str) -> Order:
        order = self.orders[client_id]
        if order.status in OPEN_STATUSES:
            try:
                body = self._signed("DELETE", "/fapi/v1/order",
                                    {"symbol": self.symbol, "origClientOrderId": client_id[-36:]})
                order.status = str(body.get("status", "CANCELED"))
            except BrokerRejected:
                pass  # already filled or gone; the next sync reads its real state
        return order

    def sync(self, bar_index: int, bar: Sequence[float], listener: FillListener) -> list[Fill]:
        fills: list[Fill] = []
        for order in list(self.orders.values()):
            if order.status not in OPEN_STATUSES:
                continue
            body = self._signed("GET", "/fapi/v1/order", {"symbol": self.symbol, "origClientOrderId": order.client_id[-36:]})
            status = str(body.get("status", order.status))
            if float(body.get("executedQty") or 0) > order.filled_qty + 1e-12:
                trades = self._signed("GET", "/fapi/v1/userTrades", {"symbol": self.symbol, "orderId": order.exchange_id})
                for tr in trades:
                    tid = str(tr["id"])
                    if tid in self.seen_trades:
                        continue
                    self.seen_trades.add(tid)
                    qty, price = float(tr["qty"]), float(tr["price"])
                    prev = order.filled_qty
                    order.filled_qty = prev + qty
                    order.avg_price = ((order.avg_price or 0.0) * prev + price * qty) / order.filled_qty
                    order.status = status
                    fill = Fill(client_id=order.client_id, qty=qty, price=price, fee=float(tr["commission"]),
                                bar_index=bar_index, trade_id=tid)
                    fills.append(fill)
                    listener.on_fill(order, fill, bar_index)
            order.status = status
        return fills

    def reported_funding(self, computed: float, *, start: datetime, end: datetime) -> float | None:
        rows = self._signed("GET", "/fapi/v1/income", {
            "symbol": self.symbol, "incomeType": "FUNDING_FEE",
            "startTime": int(start.timestamp() * 1000), "endTime": int(end.timestamp() * 1000), "limit": 1000})
        return sum(float(r["income"]) for r in rows)
