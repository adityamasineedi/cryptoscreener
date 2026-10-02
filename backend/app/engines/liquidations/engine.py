from __future__ import annotations

from dataclasses import dataclass, field
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


def parse_force_order(payload: Mapping[str, Any]) -> LiquidationEvent | None:
    """Normalize Binance !forceOrder@arr or single forceOrder event."""
    row = payload
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
    ts_ms = row.get("T") or payload.get("E")
    if ts_ms is None:
        ts = datetime.now(timezone.utc)
    else:
        ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
    notional = price * qty
    return LiquidationEvent(
        symbol=str(sym),
        timestamp=ts,
        side=side,
        price=price,
        quantity=qty,
        notional=notional,
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
