"""Deterministic, idempotent client order ids (D128c).

The same (strategy, signal, leg) always gives the same id, so a retry after a timeout can never place a second
order: the exchange refuses a duplicate newClientOrderId and the adapter looks the original up instead.

Binance allows ``^[\\.A-Z\\:/a-z0-9_-]{1,36}$``. Format: ``cs-<leg>-<24 hex>`` (at most 36 characters).
"""

from __future__ import annotations

import hashlib
import re

CLIENT_ID_RE = re.compile(r"^[\.A-Z\:/a-z0-9_-]{1,36}$")
LEGS = ("EN", "SL", "TP", "XT", "FL")  # entry, stop loss, take profit, exit, flatten


def client_order_id(strategy: str, signal_key: str, leg: str, version: int = 1) -> str:
    """Id for one order leg. ``version`` > 1 for a replacement (a stop moved by cancel-and-replace)."""
    leg = str(leg).upper()
    if leg not in LEGS:
        raise ValueError(f"leg must be one of {LEGS}, got {leg!r}")
    if not strategy or not signal_key:
        raise ValueError("strategy and signal_key are required")
    if int(version) < 1:
        raise ValueError("version must be >= 1")
    raw = f"{strategy}|{signal_key}|{leg}|{int(version)}".encode("utf-8")
    cid = f"cs-{leg}{int(version) if version > 1 else ''}-{hashlib.sha256(raw).hexdigest()[:24]}"
    cid = cid[:36]
    assert CLIENT_ID_RE.match(cid), cid
    return cid
