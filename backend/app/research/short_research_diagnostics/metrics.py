"""Pure diagnostic metrics: MFE/MAE, entry delay/extension, ATR distances."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def bars_between(earlier: Any, later: Any, *, bar_hours: float = 1.0) -> int | None:
    a = parse_ts(earlier)
    b = parse_ts(later)
    if a is None or b is None or bar_hours <= 0:
        return None
    delta_h = (b - a).total_seconds() / 3600.0
    return int(round(delta_h / bar_hours))


def entry_delay_bars(
    entry_time: Any,
    bos_confirmation_time: Any,
    *,
    bar_hours: float = 1.0,
) -> int | None:
    """entry_delay_bars = entry_time - BOS_confirmation_time (in bars)."""
    n = bars_between(bos_confirmation_time, entry_time, bar_hours=bar_hours)
    if n is None:
        return None
    return max(0, n)


def entry_extension_atr(
    entry_price: float,
    bos_level: float | None,
    atr_at_signal: float | None,
) -> float | None:
    """abs(entry_price - BOS_level) / ATR_at_signal."""
    if bos_level is None or atr_at_signal is None or float(atr_at_signal) <= 0:
        return None
    return abs(float(entry_price) - float(bos_level)) / float(atr_at_signal)


def atr_normalized_distance(
    price_a: float,
    price_b: float,
    atr: float | None,
) -> float | None:
    if atr is None or float(atr) <= 0:
        return None
    return abs(float(price_a) - float(price_b)) / float(atr)


def initial_risk(entry_price: float, stop_price: float) -> float:
    return abs(float(entry_price) - float(stop_price))


def reward_to_risk(
    entry_price: float,
    stop_price: float,
    take_profit: float | None,
    *,
    direction: str = "SHORT",
) -> float | None:
    risk = initial_risk(entry_price, stop_price)
    if risk <= 0 or take_profit is None:
        return None
    d = str(direction).upper()
    if d == "SHORT":
        reward = float(entry_price) - float(take_profit)
    else:
        reward = float(take_profit) - float(entry_price)
    return reward / risk


def compute_mfe_mae_short(
    *,
    entry_price: float,
    stop_price: float,
    highs: Sequence[float],
    lows: Sequence[float],
) -> dict[str, float | None]:
    """SHORT MFE = favorable down move; MAE = adverse up move."""
    risk = initial_risk(entry_price, stop_price)
    if not highs and not lows:
        return {"mfe": 0.0, "mae": 0.0, "mfe_r": 0.0, "mae_r": 0.0}
    worst = max(highs) if highs else entry_price
    best = min(lows) if lows else entry_price
    mae = max(0.0, worst - entry_price)
    mfe = max(0.0, entry_price - best)
    return {
        "mfe": mfe,
        "mae": mae,
        "mfe_r": (mfe / risk) if risk > 0 else None,
        "mae_r": (mae / risk) if risk > 0 else None,
    }


def price_at_offset(
    candles: Sequence[Mapping[str, Any]],
    entry_index: int,
    offset: int,
) -> float | None:
    idx = entry_index + offset
    if idx < 0 or idx >= len(candles):
        return None
    c = candles[idx]
    px = c.get("close") if c.get("close") is not None else c.get("c")
    return float(px) if px is not None else None


def candle_ohlc(c: Mapping[str, Any]) -> tuple[float, float, float, float]:
    o = float(c.get("open") if c.get("open") is not None else c.get("o") or 0)
    h = float(c.get("high") if c.get("high") is not None else c.get("h") or 0)
    l = float(c.get("low") if c.get("low") is not None else c.get("l") or 0)
    cl = float(c.get("close") if c.get("close") is not None else c.get("c") or 0)
    return o, h, l, cl
