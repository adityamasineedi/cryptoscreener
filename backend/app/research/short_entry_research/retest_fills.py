"""Research-only SHORT limit-retest fill simulation.

Rules (hard):
- Order created at/after BOS confirmation (bos candle close).
- Eligible fill starts on the NEXT candle (no retroactive BOS-candle fill).
- fill_time > order_creation_time always.
- Fill when a later candle's high touches/crosses limit (SHORT retest = price rises to level).
- Cancel after expiry_bars without fill.
- Gap-through: if a later candle opens above the limit, fill at open (worse for SHORT).

Does not modify production entry_engine / combination_engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.short_entry_research.constants import DEFAULT_FILTER_THRESHOLDS
from app.research.short_research_diagnostics.metrics import candle_ohlc, parse_ts
from app.signals._candle_utils import candle_time


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _bar_time(candle: Mapping[str, Any]) -> datetime | None:
    """Resolve bar time from datetime/epoch/ISO string fields."""
    t = candle_time(candle)
    if t is not None:
        return t
    raw = candle.get("time") or candle.get("timestamp") or candle.get("open_time")
    return parse_ts(raw)


def simulate_short_retest_fill(
    candles: Sequence[Mapping[str, Any]],
    *,
    bos_index: int,
    bos_level: float,
    atr: float | None = None,
    expiry_bars: int | None = None,
    tolerance_atr: float | None = None,
    allow_bos_candle_fill: bool = False,
) -> dict[str, Any]:
    """Simulate limit retest fill after a bearish BOS.

    SHORT limit is at bos_level (broken swing low). Fill when price revisits
    from below (high >= limit - tol).
    """
    th_exp = int(
        expiry_bars
        if expiry_bars is not None
        else DEFAULT_FILTER_THRESHOLDS["retest_expiry_bars"]
    )
    tol_mult = float(
        tolerance_atr
        if tolerance_atr is not None
        else DEFAULT_FILTER_THRESHOLDS["retest_tolerance_atr"]
    )
    atr_v = float(atr or 0.0)
    tol = atr_v * tol_mult
    limit_price = float(bos_level)

    if bos_index < 0 or bos_index >= len(candles):
        return {
            "filled": False,
            "fill_reason": "invalid_bos_index",
            "bos_index": bos_index,
            "bos_level": limit_price,
            "limit_price": limit_price,
            "paper_eligible": False,
        }

    bos_ts = _bar_time(candles[bos_index])
    # Order created at BOS candle close / signal time (bar open used as bar id).
    order_creation_time = bos_ts
    # First eligible fill candle is the next bar unless explicitly testing BOS fill.
    eligible_start_idx = bos_index if allow_bos_candle_fill else bos_index + 1
    if eligible_start_idx >= len(candles):
        return {
            "filled": False,
            "fill_reason": "no_later_candle",
            "bos_index": bos_index,
            "bos_time": _iso(bos_ts),
            "bos_level": limit_price,
            "limit_order_time": _iso(order_creation_time),
            "limit_price": limit_price,
            "eligible_fill_start": None,
            "fill_time": None,
            "expiry_time": None,
            "expiry_bars": th_exp,
            "paper_eligible": False,
        }

    eligible_start_ts = _bar_time(candles[eligible_start_idx])
    last_idx = min(len(candles) - 1, eligible_start_idx + th_exp - 1)
    expiry_ts = _bar_time(candles[last_idx])

    for i in range(eligible_start_idx, last_idx + 1):
        o, h, l, c = candle_ohlc(candles[i])
        bar_ts = _bar_time(candles[i])
        # Hard rule: fill must be strictly after order creation time.
        if order_creation_time is not None and bar_ts is not None:
            if bar_ts <= order_creation_time and not allow_bos_candle_fill:
                continue
            if allow_bos_candle_fill and i == bos_index:
                # Same-candle policy test path only.
                pass

        # Gap-through: open already above limit → fill at open.
        if o >= limit_price - tol and o > limit_price:
            fill_px = float(o)
            fill_reason = "gap_through_open"
        elif h >= limit_price - tol and l <= limit_price + tol:
            # Touched the limit zone; fill at limit.
            fill_px = limit_price
            fill_reason = "limit_touched"
        else:
            continue

        if order_creation_time is not None and bar_ts is not None and bar_ts <= order_creation_time:
            if not allow_bos_candle_fill:
                continue

        return {
            "filled": True,
            "fill_reason": fill_reason,
            "bos_index": bos_index,
            "bos_time": _iso(bos_ts),
            "bos_level": limit_price,
            "limit_order_time": _iso(order_creation_time),
            "limit_price": limit_price,
            "eligible_fill_start": _iso(eligible_start_ts),
            "fill_time": _iso(bar_ts),
            "fill_index": i,
            "fill_price": fill_px,
            "expiry_time": _iso(expiry_ts),
            "expiry_bars": th_exp,
            "tolerance": tol,
            "fill_after_order": True,
            "retroactive_bos_fill": bool(i == bos_index),
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
        }

    return {
        "filled": False,
        "fill_reason": "expired_no_retest",
        "bos_index": bos_index,
        "bos_time": _iso(bos_ts),
        "bos_level": limit_price,
        "limit_order_time": _iso(order_creation_time),
        "limit_price": limit_price,
        "eligible_fill_start": _iso(eligible_start_ts),
        "fill_time": None,
        "fill_index": None,
        "fill_price": None,
        "expiry_time": _iso(expiry_ts),
        "expiry_bars": th_exp,
        "tolerance": tol,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }


def assert_fill_after_order(fill: Mapping[str, Any]) -> bool:
    """Validate fill_time > order_creation_time when filled."""
    if not fill.get("filled"):
        return True
    order_t = parse_ts(fill.get("limit_order_time"))
    fill_t = parse_ts(fill.get("fill_time"))
    if order_t is None or fill_t is None:
        return False
    if fill.get("retroactive_bos_fill"):
        return True  # only allowed on explicit test path
    return fill_t > order_t
