"""Binance USD-M futures TESTNET broker for the V3 paper adapter.

Live trading is impossible here: any host other than a futures testnet is
refused, and construction needs CANDLE12_V3_PAPER_ENABLE=1 (the CLI also needs
``--mode testnet_broker_check``). It is used only with testnet market data
(modes.py); testnet fills are never OOS evidence.

Endpoints follow broker_capabilities.json (Binance docs checked 2026-10-15).
Since 2025-12-09 STOP_MARKET and TAKE_PROFIT_MARKET go to the Algo Service
(POST /fapi/v1/algoOrder, algoType=CONDITIONAL, triggerPrice, clientAlgoId); the
plain order endpoint rejects them with -4120. MARKET orders stay on
/fapi/v1/order. A timeout leaves the order state unknown: ``lookup`` queries it
before anything is retried.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import time
from datetime import datetime
from typing import Any, Sequence
from urllib.parse import urlencode, urlparse

import httpx

from app.research.candle12_v3_paper.broker import (
    AUTH_REJECTED,
    DUPLICATE_CLIENT_ID,
    MARGIN_REJECTED,
    MIN_NOTIONAL_REJECTED,
    NO_SUCH_ORDER,
    OPEN_STATUSES,
    ORDER_REJECTED,
    PRICE_BEYOND_TRIGGER,
    QTY_STEP_REJECTED,
    UNSUPPORTED_ENDPOINT,
    BrokerRejected,
    BrokerUnavailable,
    Fill,
    FillListener,
    Order,
)
from app.research.candle12_v3_paper.capabilities import ALGO_ENDPOINT, ORDER_ENDPOINT, is_algo, require_supported
from app.research.candle12_v3_paper.modes import TESTNET_BROKER, TESTNET_DATA_BASE, TESTNET_HOSTS

PAPER_ENV = "CANDLE12_V3_PAPER_ENABLE"
TESTNET_BASE = TESTNET_DATA_BASE

# Binance error code -> adapter rejection reason.
ERROR_REASONS: dict[int, str] = {
    -2019: MARGIN_REJECTED,  # Margin is insufficient
    -4164: MIN_NOTIONAL_REJECTED,  # Order's notional must be no smaller than the minimum
    -1111: QTY_STEP_REJECTED,  # Precision is over the maximum defined for this asset
    -4003: QTY_STEP_REJECTED,  # Quantity less than or equal to zero
    -1013: QTY_STEP_REJECTED,  # Filter failure (LOT_SIZE)
    -2021: PRICE_BEYOND_TRIGGER,  # Order would immediately trigger
    -2022: ORDER_REJECTED,  # ReduceOnly order is rejected
    -4116: DUPLICATE_CLIENT_ID,  # clientOrderId is duplicated
    -4015: ORDER_REJECTED,  # Client order id is not valid
    -4120: UNSUPPORTED_ENDPOINT,  # STOP_ORDER_SWITCH_ALGO
    -1021: AUTH_REJECTED,  # Timestamp outside recvWindow
    -1022: AUTH_REJECTED,  # Signature not valid
    -2014: AUTH_REJECTED,  # API-key format invalid
    -2015: AUTH_REJECTED,  # Invalid API-key, IP, or permissions
    -2013: NO_SUCH_ORDER,  # Order does not exist
    -2011: NO_SUCH_ORDER,  # Cancel rejected: unknown order
}
UNAVAILABLE_CODES = (-1001, -1003, -1008)
UNKNOWN_STATE_CODES = (-1007,)  # timeout waiting for the backend: execution status unknown
ALGO_TERMINAL = {"CANCELED": "CANCELED", "CANCELLED": "CANCELED", "EXPIRED": "EXPIRED", "REJECTED": "REJECTED"}
_CLIENT_ID_OK = re.compile(r"^[\.A-Z\:/a-z0-9_-]{1,36}$")


class PaperGuardError(RuntimeError):
    pass


def assert_testnet(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    if host not in TESTNET_HOSTS:
        raise PaperGuardError(f"refusing {host!r}: CANDLE12_V3 paper orders go to a futures testnet only")


def exchange_client_id(client_id: str) -> str:
    """The id sent to Binance: the adapter id when it is valid there, else a stable hash.

    Binance accepts ^[.A-Z:/a-z0-9_-]{1,36}$. Adapter ids carry an ISO time with
    '+00:00' and run past 36 characters, so they are hashed (deterministically, so
    a restart maps the same adapter id to the same exchange id).
    """
    if _CLIENT_ID_OK.match(client_id):
        return client_id
    return "c12_" + hashlib.sha256(client_id.encode()).hexdigest()[:32]


class BinanceTestnetBroker:
    name = TESTNET_BROKER
    sends_orders = True

    def __init__(self, *, symbol: str, api_key: str, api_secret: str, base_url: str = TESTNET_BASE,
                 client: httpx.Client | None = None, recv_window: int = 5000, fee_rate: float = 0.0004,
                 tick: float = 0.1, timeout_s: float = 10.0) -> None:
        if os.environ.get(PAPER_ENV) != "1":
            raise PaperGuardError(f"set {PAPER_ENV}=1 to send paper orders")
        assert_testnet(base_url)
        if not api_key or not api_secret:
            raise PaperGuardError("testnet API key and secret are required")
        self.symbol = symbol.upper()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.client = client or httpx.Client(timeout=timeout_s)
        self.recv_window = recv_window
        self.fee_rate = fee_rate
        self.tick = tick
        self.orders: dict[str, Order] = {}
        self.algo_actual: dict[str, str] = {}  # client_id -> triggered order id
        self.seen_trades: set[str] = set()
        self.fills: list[dict[str, Any]] = []
        self.calls = 0

    # -- transport -----------------------------------------------------------

    def _signed(self, method: str, path: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
        assert_testnet(self.base_url)
        self.calls += 1
        params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": self.recv_window}
        query = urlencode(params)
        sig = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={sig}"
        kwargs: dict[str, Any] = {"headers": {"X-MBX-APIKEY": self.api_key}}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = self.client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            # The request may have reached the matching engine.
            raise BrokerUnavailable(f"timeout: {exc}", unknown=method != "GET") from exc
        except httpx.HTTPError as exc:
            raise BrokerUnavailable(str(exc)) from exc
        if resp.status_code >= 500:
            # 503 "Unknown error" means sent but unanswered: the state is unknown.
            raise BrokerUnavailable(f"HTTP {resp.status_code}", unknown=method != "GET")
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            code = int(body.get("code", 0)) if isinstance(body, dict) else 0
            if code in UNKNOWN_STATE_CODES:
                raise BrokerUnavailable(f"{code}: {body.get('msg')}", unknown=True)
            if code in UNAVAILABLE_CODES:
                raise BrokerUnavailable(f"{code}: {body.get('msg')}")
            raise BrokerRejected(ERROR_REASONS.get(code, ORDER_REJECTED), code, str(body.get("msg", "")))
        return body

    def _public(self, path: str, params: dict[str, Any]) -> Any:
        assert_testnet(self.base_url)
        try:
            resp = self.client.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            raise BrokerUnavailable(str(exc)) from exc
        if resp.status_code >= 400:
            raise BrokerUnavailable(f"HTTP {resp.status_code} on {path}")
        return resp.json()

    # -- request building ----------------------------------------------------

    @staticmethod
    def tick_price(order: Order, tick: float) -> float:
        """Exchange tick grid, rounded against the trader: buys up, sells down."""
        units = order.stop_price / tick  # type: ignore[operator]
        units = math.ceil(units - 1e-9) if order.side == "BUY" else math.floor(units + 1e-9)
        return round(units * tick, 10)

    @staticmethod
    def _num(v: float) -> str:
        return f"{v:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def endpoint_for(order: Order) -> str:
        """The REST path this order type must use (broker_capabilities.json)."""
        return ALGO_ENDPOINT if is_algo(order.type) else ORDER_ENDPOINT

    @staticmethod
    def order_params(order: Order, symbol: str, tick: float = 0.1) -> dict[str, Any]:
        """Request parameters for ``endpoint_for(order)``."""
        require_supported(order.type, reduce_only=order.reduce_only)
        qty = BinanceTestnetBroker._num(order.qty)
        cid = exchange_client_id(order.client_id)
        if is_algo(order.type):
            if order.stop_price is None:
                raise ValueError(f"{order.type} needs a trigger price")
            params: dict[str, Any] = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": order.side,
                "type": order.type,
                "quantity": qty,
                "triggerPrice": BinanceTestnetBroker._num(BinanceTestnetBroker.tick_price(order, tick)),
                "workingType": "CONTRACT_PRICE",
                "clientAlgoId": cid,
            }
        else:
            params = {"symbol": symbol, "side": order.side, "type": order.type, "quantity": qty,
                      "newClientOrderId": cid, "newOrderRespType": "RESULT"}
        if order.reduce_only:
            params["reduceOnly"] = "true"
        return params

    # -- broker interface ----------------------------------------------------

    def place_order(self, order: Order, *, bar_index: int, reference_price: float | None = None,
                    timeout: float | None = None) -> Order:
        path = self.endpoint_for(order)
        body = self._signed("POST", path, self.order_params(order, self.symbol, self.tick), timeout=timeout)
        if path == ALGO_ENDPOINT:
            order.exchange_id = str(body.get("algoId"))
            order.status = self._algo_status(body)
        else:
            order.exchange_id = str(body.get("orderId"))
            order.status = str(body.get("status", "NEW"))
        order.placed_bar = bar_index
        self.orders[order.client_id] = order
        return order

    @staticmethod
    def _algo_status(body: dict[str, Any]) -> str:
        raw = str(body.get("algoStatus", "NEW")).upper()
        if raw in ALGO_TERMINAL:
            return ALGO_TERMINAL[raw]
        return "NEW" if raw == "NEW" else "TRIGGERED"

    def cancel_order(self, client_id: str) -> Order:
        order = self.orders[client_id]
        if order.status in OPEN_STATUSES or order.status == "TRIGGERED":
            cid = exchange_client_id(client_id)
            try:
                if is_algo(order.type):
                    self._signed("DELETE", ALGO_ENDPOINT, {"clientAlgoId": cid})
                    order.status = "CANCELED"
                else:
                    body = self._signed("DELETE", ORDER_ENDPOINT, {"symbol": self.symbol, "origClientOrderId": cid})
                    order.status = str(body.get("status", "CANCELED"))
            except BrokerRejected:
                pass  # already filled or gone; the next sync reads its real state
        return order

    def lookup(self, client_id: str) -> Order | None:
        """Read one order by client id from the exchange. None if the exchange never saw it."""
        known = self.orders.get(client_id)
        cid = exchange_client_id(client_id)
        candidates = [known.type] if known else ["MARKET", "STOP_MARKET"]
        for order_type in candidates:
            try:
                if is_algo(order_type):
                    body = self._signed("GET", ALGO_ENDPOINT, {"clientAlgoId": cid})
                else:
                    body = self._signed("GET", ORDER_ENDPOINT, {"symbol": self.symbol, "origClientOrderId": cid})
            except BrokerRejected as exc:
                if exc.reason == NO_SUCH_ORDER:
                    continue
                raise
            if not body:
                continue
            return self._order_from_body(client_id, body, known)
        return None

    def _order_from_body(self, client_id: str, body: dict[str, Any], known: Order | None) -> Order:
        algo = "algoId" in body
        order = known or Order(
            client_id=client_id, purpose=str(body.get("purpose") or "UNKNOWN"), side=str(body.get("side")),
            type=str(body.get("orderType") if algo else body.get("type")),
            qty=float(body.get("quantity") if algo else body.get("origQty") or 0),
            stop_price=float(body["triggerPrice"]) if algo and body.get("triggerPrice") else None,
            reduce_only=str(body.get("reduceOnly")).lower() == "true",
        )
        order.exchange_id = str(body.get("algoId") if algo else body.get("orderId"))
        order.status = self._algo_status(body) if algo else str(body.get("status", order.status))
        if algo and body.get("actualOrderId"):
            self.algo_actual[client_id] = str(body["actualOrderId"])
        self.orders[client_id] = order
        return order

    def open_orders(self) -> list[Order]:
        out: list[Order] = []
        by_ex = {exchange_client_id(k): k for k in self.orders}
        for row in self._signed("GET", "/fapi/v1/openOrders", {"symbol": self.symbol}) or []:
            cid = by_ex.get(str(row.get("clientOrderId")), str(row.get("clientOrderId")))
            out.append(self._order_from_body(cid, row, self.orders.get(cid)))
        algo_rows = self._signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": self.symbol}) or []
        if isinstance(algo_rows, dict):
            algo_rows = algo_rows.get("orders") or algo_rows.get("rows") or []
        for row in algo_rows:
            cid = by_ex.get(str(row.get("clientAlgoId")), str(row.get("clientAlgoId")))
            out.append(self._order_from_body(cid, row, self.orders.get(cid)))
        return [o for o in out if o.status in OPEN_STATUSES]

    def position_qty(self) -> float:
        rows = self._signed("GET", "/fapi/v3/positionRisk", {"symbol": self.symbol}) or []
        return sum(float(r.get("positionAmt") or 0.0) for r in rows if str(r.get("symbol")) == self.symbol)

    def recent_fills(self) -> list[dict[str, Any]]:
        """Fills this client has synced, plus exchange trades on known orders it has not seen yet.

        After a restart the in-memory log is empty; the unseen trades are what
        happened while the process was down. Nothing is applied here.
        """
        out = list(self.fills)
        for order in list(self.orders.values()):
            if order.status in ("CANCELED", "EXPIRED", "REJECTED") and order.filled_qty <= 0:
                continue
            try:
                _status, executed, ex_order = self._executed(order)
            except BrokerRejected:
                continue
            if executed <= order.filled_qty + 1e-12 or not ex_order:
                continue
            for tr in self._signed("GET", "/fapi/v1/userTrades", {"symbol": self.symbol, "orderId": ex_order}):
                tid = str(tr["id"])
                if tid in self.seen_trades:
                    continue
                out.append({"client_id": order.client_id, "qty": float(tr["qty"]), "price": float(tr["price"]),
                            "fee": float(tr["commission"]), "bar_index": 0, "gap": False,
                            "same_bar_conflict": False, "trade_id": tid, "purpose": order.purpose})
        return out

    def absorb_fill(self, order: Order, fill: Fill) -> None:
        """Account a recovered fill on its order, as sync would have."""
        if fill.trade_id in self.seen_trades:
            return
        self.seen_trades.add(str(fill.trade_id))
        prev = order.filled_qty
        order.filled_qty = prev + fill.qty
        order.avg_price = ((order.avg_price or 0.0) * prev + fill.price * fill.qty) / order.filled_qty
        order.status = "FILLED" if order.filled_qty >= order.qty - 1e-12 else "PARTIALLY_FILLED"
        self.fills.append({**fill.to_dict(), "purpose": order.purpose})

    def account(self) -> Any:
        return self._signed("GET", "/fapi/v2/balance", {})

    def symbol_filters(self) -> dict[str, float]:
        info = self._public("/fapi/v1/exchangeInfo", {})
        for s in info.get("symbols", []):
            if s.get("symbol") != self.symbol:
                continue
            f = {x["filterType"]: x for x in s.get("filters", [])}
            return {
                "step": float(f["LOT_SIZE"]["stepSize"]),
                "min_qty": float(f["LOT_SIZE"]["minQty"]),
                "min_notional": float(f.get("MIN_NOTIONAL", {}).get("notional") or 0.0),
                "tick": float(f["PRICE_FILTER"]["tickSize"]),
            }
        raise BrokerRejected(ORDER_REJECTED, None, f"{self.symbol} not listed on the testnet")

    def last_price(self) -> float:
        return float(self._public("/fapi/v1/ticker/price", {"symbol": self.symbol})["price"])

    def snapshot(self) -> dict[str, Any]:
        return {"name": self.name, "orders": {k: o.to_dict() for k, o in self.orders.items()},
                "algo_actual": dict(self.algo_actual), "seen_trades": sorted(self.seen_trades)}

    def restore(self, snap: dict[str, Any]) -> None:
        self.orders = {k: Order(**v) for k, v in (snap.get("orders") or {}).items()}
        self.algo_actual = dict(snap.get("algo_actual") or {})
        self.seen_trades = set(snap.get("seen_trades") or [])

    def _executed(self, order: Order) -> tuple[str, float, str | None]:
        """(status, executed qty, exchange order id holding the fills)."""
        if is_algo(order.type):
            body = self._signed("GET", ALGO_ENDPOINT, {"clientAlgoId": exchange_client_id(order.client_id)})
            status = self._algo_status(body)
            actual = str(body.get("actualOrderId") or self.algo_actual.get(order.client_id) or "")
            if not actual:
                return status, 0.0, None
            self.algo_actual[order.client_id] = actual
            triggered = self._signed("GET", ORDER_ENDPOINT, {"symbol": self.symbol, "orderId": actual})
            return str(triggered.get("status", "NEW")), float(triggered.get("executedQty") or 0), actual
        body = self._signed("GET", ORDER_ENDPOINT,
                            {"symbol": self.symbol, "origClientOrderId": exchange_client_id(order.client_id)})
        return str(body.get("status", order.status)), float(body.get("executedQty") or 0), order.exchange_id

    def sync(self, bar_index: int, bar: Sequence[float], listener: FillListener) -> list[Fill]:
        fills: list[Fill] = []
        for order in list(self.orders.values()):
            # A MARKET order can come back FILLED from the placement call; its trades still need syncing.
            unsynced_fill = order.status == "FILLED" and order.filled_qty < order.qty - 1e-12
            if order.status not in OPEN_STATUSES and order.status != "TRIGGERED" and not unsynced_fill:
                continue
            status, executed, ex_order = self._executed(order)
            if executed > order.filled_qty + 1e-12 and ex_order:
                for tr in self._signed("GET", "/fapi/v1/userTrades", {"symbol": self.symbol, "orderId": ex_order}):
                    tid = str(tr["id"])
                    if tid in self.seen_trades:
                        continue
                    self.seen_trades.add(tid)
                    qty, price = float(tr["qty"]), float(tr["price"])
                    prev = order.filled_qty
                    order.filled_qty = prev + qty
                    order.avg_price = ((order.avg_price or 0.0) * prev + price * qty) / order.filled_qty
                    order.status = "FILLED" if order.filled_qty >= order.qty - 1e-12 else "PARTIALLY_FILLED"
                    fill = Fill(client_id=order.client_id, qty=qty, price=price, fee=float(tr["commission"]),
                                bar_index=bar_index, trade_id=tid)
                    self.fills.append({**fill.to_dict(), "purpose": order.purpose})
                    fills.append(fill)
                    listener.on_fill(order, fill, bar_index)
            order.status = status if status != "TRIGGERED" or executed <= 0 else order.status
        return fills

    def reported_funding(self, computed: float, *, start: datetime, end: datetime) -> float | None:
        rows = self._signed("GET", "/fapi/v1/income", {
            "symbol": self.symbol, "incomeType": "FUNDING_FEE",
            "startTime": int(start.timestamp() * 1000), "endTime": int(end.timestamp() * 1000), "limit": 1000})
        return sum(float(r["income"]) for r in rows)
