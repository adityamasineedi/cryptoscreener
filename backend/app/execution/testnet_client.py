"""Binance USD-M Futures TESTNET client for the execution adapter (owner rulings D128c, D131; CONSTITUTION v2.5 C1).

C1 v2.5: no real orders, no real money. Exchange API keys are allowed only for the Binance Futures testnet. The
owner sets them in the root .env, and they are never logged, printed or committed. The code refuses any
non-testnet base URL.

The client refuses to construct unless ALL of these hold (each check raises ``TestnetRefused``):
1. ``EXECUTION_TESTNET_ENABLED=true`` (default false);
2. the constitution in force is v2.5 or later: the first line of ``prompts/CONSTITUTION.md`` in the research
   worktree (``EXECUTION_CONSTITUTION_PATH``) reads "=== CONSTITUTION v<major>.<minor> ...";
3. the base URL is https and its host is on the testnet allow-list (``TESTNET_HOSTS``). Anything else is refused,
   ``fapi.binance.com`` included;
4. an API key and secret are present (``BINANCE_TESTNET_API_KEY`` / ``BINANCE_TESTNET_API_SECRET``).

The key and secret are kept as ``SecretStr``-like private fields. ``repr``/``str`` never show them, and neither do
errors or logs. Requests are signed with HMAC-SHA256 as Binance specifies. The base URL is re-checked on every
request, so a later change cannot point it at mainnet.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from app.execution.interface import (
    DuplicateClientOrderId,
    ExchangeClient,
    ExchangeError,
    OrderNotFound,
    ReduceOnlyRejected,
    TransientExchangeError,
)
from app.execution.models import LIMIT, Order, OrderRequest, Position

TESTNET_HOSTS = frozenset({"testnet.binancefuture.com", "demo-fapi.binance.com"})
DEFAULT_TESTNET_URL = "https://testnet.binancefuture.com"
DEFAULT_CONSTITUTION_PATH = "D:/cryptoscreener-worktrees/research-audit/prompts/CONSTITUTION.md"
MIN_CONSTITUTION = (2, 5)
_VERSION_RE = re.compile(r"CONSTITUTION\s+v(\d+)\.(\d+)")


class TestnetRefused(RuntimeError):
    """The testnet client may not be built or used (a C1 guard failed)."""

    __test__ = False  # not a pytest test class


def constitution_version(path: str | Path) -> tuple[int, int] | None:
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline()
    except OSError:
        return None
    m = _VERSION_RE.search(first)
    return (int(m.group(1)), int(m.group(2))) if m else None


def check_testnet_url(url: str) -> str:
    """The URL if it is an https Binance Futures testnet host; otherwise TestnetRefused."""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise TestnetRefused(f"refusing non-https base URL ({parsed.scheme or 'no scheme'}://{host})")
    if host not in TESTNET_HOSTS or parsed.username or parsed.password or (parsed.port not in (None, 443)):
        raise TestnetRefused(f"refusing non-testnet base URL host {host!r}; allowed: {sorted(TESTNET_HOSTS)}")
    return f"https://{host}"


class BinanceFuturesTestnetClient(ExchangeClient):
    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        api_key: str,
        api_secret: str,
        constitution_path: str | Path = DEFAULT_CONSTITUTION_PATH,
        http: Any | None = None,
        recv_window_ms: int = 5000,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if enabled is not True:
            raise TestnetRefused("EXECUTION_TESTNET_ENABLED is not true; the testnet client is off")
        version = constitution_version(constitution_path)
        if version is None or version < MIN_CONSTITUTION:
            raise TestnetRefused(
                f"constitution at {constitution_path} is {version and 'v%d.%d' % version or 'unreadable'}; "
                f"testnet use needs v{MIN_CONSTITUTION[0]}.{MIN_CONSTITUTION[1]} or later (C1)")
        self._base = check_testnet_url(base_url)
        if not api_key or not api_secret:
            raise TestnetRefused("testnet API key or secret missing (owner sets them in the root .env)")
        self.__key = str(api_key)
        self.__secret = str(api_secret).encode("utf-8")
        self._recv = int(recv_window_ms)
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._http = http  # injected for tests; created lazily otherwise

    def __repr__(self) -> str:
        return f"BinanceFuturesTestnetClient(base={self._base!r}, key=***)"

    __str__ = __repr__

    # --- signing and transport -------------------------------------------------------------------------------

    def sign(self, params: dict[str, Any]) -> str:
        query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None])
        sig = hmac.new(self.__secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        base = check_testnet_url(self._base)  # re-checked on every call
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=10.0)
        body = self.sign({**params, "recvWindow": self._recv, "timestamp": self._clock()})
        try:
            resp = self._http.request(method, f"{base}{path}?{body}", headers={"X-MBX-APIKEY": self.__key})
        except Exception as exc:  # noqa: BLE001 - network: the caller retries idempotently
            raise TransientExchangeError(f"{method} {path}: {exc.__class__.__name__}") from None
        if resp.status_code >= 500:
            raise TransientExchangeError(f"{method} {path}: HTTP {resp.status_code}")
        data = resp.json()
        if resp.status_code >= 400:
            code = int(data.get("code", 0)) if isinstance(data, dict) else 0
            msg = str(data.get("msg", "")) if isinstance(data, dict) else ""
            if code in (-4116, -2010) and "uplicate" in msg:
                raise DuplicateClientOrderId(msg, code=code)
            if code in (-2013, -2011):
                raise OrderNotFound(msg, code=code)
            if code == -2022:
                raise ReduceOnlyRejected(msg, code=code)
            raise ExchangeError(f"{method} {path}: {code} {msg}", code=code)
        return data

    @staticmethod
    def _order(d: dict[str, Any]) -> Order:
        def f(k: str) -> float | None:
            v = d.get(k)
            return float(v) if v not in (None, "", "0", "0.0", "0.00000000") else None

        return Order(symbol=d["symbol"], side=d["side"], type=d.get("origType") or d["type"],
                     quantity=float(d.get("origQty") or 0.0), client_order_id=d["clientOrderId"],
                     order_id=int(d["orderId"]), status=d["status"], price=f("price"), stop_price=f("stopPrice"),
                     reduce_only=bool(d.get("reduceOnly")), executed_qty=float(d.get("executedQty") or 0.0),
                     avg_price=float(d.get("avgPrice") or 0.0), update_seq=int(d.get("updateTime") or 0))

    # --- ExchangeClient --------------------------------------------------------------------------------------

    def place_order(self, req: OrderRequest) -> Order:
        params: dict[str, Any] = {"symbol": req.symbol, "side": req.side, "type": req.type,
                                  "quantity": f"{req.quantity:.8f}".rstrip("0").rstrip("."),
                                  "newClientOrderId": req.client_order_id, "newOrderRespType": "RESULT"}
        if req.reduce_only:
            params["reduceOnly"] = "true"
        if req.type == LIMIT:
            params.update(price=f"{req.price}", timeInForce=req.time_in_force or "GTC")
        if req.stop_price is not None:
            params.update(stopPrice=f"{req.stop_price}", workingType=req.working_type)
        return self._order(self._request("POST", "/fapi/v1/order", params))

    def cancel_order(self, symbol: str, client_order_id: str) -> Order:
        return self._order(self._request("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}))

    def amend_order(self, symbol: str, client_order_id: str, *, quantity: float, price: float) -> Order:
        cur = self.get_order(symbol, client_order_id)
        return self._order(self._request("PUT", "/fapi/v1/order", {
            "symbol": symbol, "origClientOrderId": client_order_id, "side": cur.side,
            "quantity": f"{quantity}", "price": f"{price}"}))

    def get_order(self, symbol: str, client_order_id: str) -> Order:
        return self._order(self._request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}))

    def open_orders(self, symbol: str | None = None) -> list[Order]:
        data = self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol} if symbol else {})
        return [self._order(d) for d in data]

    def positions(self) -> dict[str, Position]:
        data = self._request("GET", "/fapi/v2/positionRisk", {})
        out: dict[str, Position] = {}
        for d in data:
            qty = float(d.get("positionAmt") or 0.0)
            if abs(qty) > 0:
                out[d["symbol"]] = Position(d["symbol"], qty, float(d.get("entryPrice") or 0.0))
        return out


def make_testnet_client(settings: Any, **kw: Any) -> BinanceFuturesTestnetClient:
    """Build from settings (the root .env). Raises TestnetRefused unless every C1 guard passes."""
    key = getattr(settings, "binance_testnet_api_key", None)
    secret = getattr(settings, "binance_testnet_api_secret", None)
    reveal = lambda s: s.get_secret_value() if hasattr(s, "get_secret_value") else (s or "")  # noqa: E731
    return BinanceFuturesTestnetClient(
        enabled=bool(getattr(settings, "execution_testnet_enabled", False)),
        base_url=str(getattr(settings, "binance_testnet_futures_rest", DEFAULT_TESTNET_URL)),
        api_key=reveal(key),
        api_secret=reveal(secret),
        constitution_path=str(getattr(settings, "execution_constitution_path", DEFAULT_CONSTITUTION_PATH)),
        **kw,
    )
