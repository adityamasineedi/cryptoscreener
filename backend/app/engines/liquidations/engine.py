from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LiquidationEvent:
    symbol: str
    timestamp: datetime
    side: str  # BUY = short liq, SELL = long liq (Binance force order)
    price: float
    quantity: float
    notional: float
    order_type: str = ""
    average_price: float | None = None
    order_status: str = ""
    order_time: datetime | None = None
    event_time: datetime | None = None
    source: str = "binance_force_order"
    received_at: datetime | None = None
    event_key: str = ""


def _ms_to_dt(ts_ms: Any) -> datetime | None:
    if ts_ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def event_key_from_order(row: Mapping[str, Any], *, event_time: datetime | None) -> str:
    """Deterministic key from Binance force-order fields (not symbol+ts alone)."""
    sym = str(row.get("s") or "")
    side = str(row.get("S") or "")
    status = str(row.get("X") or "")
    price = str(row.get("p") or "")
    qty = str(row.get("q") or "")
    filled = str(row.get("z") or row.get("l") or "")
    trade_t = str(row.get("T") or "")
    avg = str(row.get("ap") or "")
    et = event_time.isoformat() if event_time else ""
    return "|".join((sym, side, status, price, qty, filled, avg, trade_t, et))


def parse_force_order(
    payload: Mapping[str, Any],
    *,
    received_at: datetime | None = None,
) -> LiquidationEvent | None:
    """Normalize Binance !forceOrder@arr or single forceOrder event."""
    row: Any = payload
    if row.get("e") == "forceOrder" and "o" in row:
        row = row["o"]
    if not isinstance(row, dict):
        return None
    sym = row.get("s")
    if not sym:
        return None
    side = str(row.get("S", "")).upper()
    try:
        price = float(row.get("p", 0))
        qty = float(row.get("q", 0))
    except (TypeError, ValueError):
        return None
    if price <= 0 or qty <= 0:
        return None

    avg_price: float | None
    try:
        avg_price = float(row["ap"]) if row.get("ap") not in (None, "") else None
    except (TypeError, ValueError):
        avg_price = None

    event_time = _ms_to_dt(payload.get("E"))
    order_time = _ms_to_dt(row.get("T")) or event_time
    ts = order_time or event_time or datetime.now(timezone.utc)
    recv = received_at or datetime.now(timezone.utc)
    notional = (avg_price if avg_price and avg_price > 0 else price) * qty
    key = event_key_from_order(row, event_time=event_time or ts)

    return LiquidationEvent(
        symbol=str(sym),
        timestamp=ts,
        side=side,
        price=price,
        quantity=qty,
        notional=notional,
        order_type=str(row.get("o") or ""),
        average_price=avg_price,
        order_status=str(row.get("X") or ""),
        order_time=order_time,
        event_time=event_time,
        source="binance_force_order",
        received_at=recv,
        event_key=key,
    )


def _side_bucket(side: str) -> str:
    # Binance: SELL = long position liquidated, BUY = short liquidated
    if side == "SELL":
        return "long"
    if side == "BUY":
        return "short"
    return "unknown"


@dataclass
class WindowAgg:
    long_notional: float = 0.0
    short_notional: float = 0.0
    event_count: int = 0

    def add(self, ev: LiquidationEvent) -> None:
        self.event_count += 1
        bucket = _side_bucket(ev.side)
        if bucket == "long":
            self.long_notional += ev.notional
        elif bucket == "short":
            self.short_notional += ev.notional


def aggregate_windows(
    events: Sequence[LiquidationEvent],
    *,
    now: datetime | None = None,
    windows_minutes: tuple[int, ...] = (5, 15, 60),
) -> dict[str, dict[str, float]]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, dict[str, float]] = {}
    for w in windows_minutes:
        cutoff = now - timedelta(minutes=w)
        agg = WindowAgg()
        for ev in events:
            if ev.timestamp >= cutoff:
                agg.add(ev)
        out[f"{w}m"] = {
            "long_liq_notional": agg.long_notional,
            "short_liq_notional": agg.short_notional,
            "total_notional": agg.long_notional + agg.short_notional,
            "event_count": float(agg.event_count),
        }
    return out


def imbalance_ratio(long_notional: float, short_notional: float) -> float | None:
    total = long_notional + short_notional
    if total <= 0:
        return None
    return (long_notional - short_notional) / total


def liquidation_spike(
    current_total: float,
    baseline_total: float,
    *,
    multiplier: float = 3.0,
) -> bool:
    if baseline_total <= 0:
        return current_total > 0
    return current_total >= baseline_total * multiplier


def volume_ratio(liq_notional: float, market_volume: float | None) -> float | None:
    if market_volume is None or market_volume <= 0:
        return None
    return liq_notional / market_volume
