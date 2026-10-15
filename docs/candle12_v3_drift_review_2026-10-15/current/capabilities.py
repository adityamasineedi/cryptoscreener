"""Order types the V3 paper brokers may send, from broker_capabilities.json.

Every broker calls ``require_supported`` before it places an order, so a request
for a type the venue endpoint does not take fails here, not at the exchange.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CAPABILITIES_PATH = Path(__file__).resolve().parent / "broker_capabilities.json"
ORDER_ENDPOINT = "/fapi/v1/order"
ALGO_ENDPOINT = "/fapi/v1/algoOrder"


class UnsupportedOrderType(ValueError):
    def __init__(self, order_type: str) -> None:
        super().__init__(f"UNSUPPORTED_ORDER_TYPE: {order_type!r} is not in broker_capabilities.json")
        self.order_type = order_type


@lru_cache(maxsize=1)
def capabilities() -> dict[str, Any]:
    return json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))


def supported_types() -> tuple[str, ...]:
    return tuple(capabilities()["order_types"])


def require_supported(order_type: str, *, reduce_only: bool = False) -> dict[str, Any]:
    spec = capabilities()["order_types"].get(order_type)
    if spec is None:
        raise UnsupportedOrderType(order_type)
    if reduce_only and not spec.get("reduce_only"):
        raise UnsupportedOrderType(f"{order_type} reduceOnly")
    return spec


def is_algo(order_type: str) -> bool:
    """Conditional types go to the Algo Service endpoint (migration effective 2025-12-09)."""
    return require_supported(order_type)["endpoint"].endswith(ALGO_ENDPOINT)
