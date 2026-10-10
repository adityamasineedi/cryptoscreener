"""Score symbols for a long futures grid. Read-only candles, swings, and ATR.

The score is a filter. It does not emit orders and it does not call strategy,
paper, or risk code.
"""

from __future__ import annotations

from typing import Any

from app.engines.mtf.indicators import atr
from app.signals.swing_detector import detect_swings

from app.grid_trading.config import GridConfig, GridConfigError, validate_config
from app.grid_trading.levels import build_levels


def _crosses(closes: list[float], mid: float) -> int:
    count = 0
    for prev, close in zip(closes, closes[1:]):
        if (prev - mid) * (close - mid) < 0:
            count += 1
    return count


def _alternations(candles: list[dict[str, Any]]) -> int:
    swings = detect_swings(candles, left=2, right=2)
    kinds = [getattr(swing, "swing_type", None) for swing in swings]
    flips = 0
    previous = None
    for kind in kinds:
        if kind is None:
            continue
        if previous is not None and kind != previous:
            flips += 1
        previous = kind
    return flips


def score_series(
    candles: list[dict[str, Any]],
    *,
    fee_bps: float,
    slippage_bps: float,
    grid_count: int,
    symbol: str = "",
    timeframe: str = "",
) -> dict[str, Any]:
    if len(candles) < 30:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "score": 0.0,
            "eligible": False,
            "reason": "insufficient_bars",
            "market": "binance_usdt_m_futures",
        }
    highs = [float(bar["high"]) for bar in candles]
    lows = [float(bar["low"]) for bar in candles]
    closes = [float(bar["close"]) for bar in candles]
    range_low = min(lows)
    range_high = max(highs)
    mid = (range_low + range_high) / 2.0
    span = range_high - range_low
    if range_low <= 0 or span <= 0 or mid <= 0:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "score": 0.0,
            "eligible": False,
            "reason": "degenerate_range",
            "market": "binance_usdt_m_futures",
        }
    # Stop sits under the scanned range. Inset the grid so spacing still clears fees.
    inset = span * 0.05
    grid_low = range_low + inset
    grid_high = range_high - inset
    stop = range_low * 0.99
    atr_val = atr(highs, lows, closes, 14)
    trend = abs(closes[-1] - closes[0]) / closes[0]
    crosses = _crosses(closes, mid)
    flips = _alternations(candles)
    spacing_ok = False
    spacing_reason = None
    try:
        draft = GridConfig(
            symbol=symbol,
            timeframe=timeframe or "1h",
            grid_type="geometric",
            range_low=grid_low,
            range_high=grid_high,
            grid_count=grid_count,
            capital=1_000.0,
            max_capital=1_000_000.0,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            stop_price=stop if stop < grid_low else grid_low * 0.99,
            max_drawdown=1.0,
            max_open_orders=max(grid_count, 1),
        )
        levels = build_levels(draft)
        validate_config(draft, levels)
        spacing_ok = True
    except GridConfigError as exc:
        spacing_reason = exc.code

    range_score = min(span / mid, 0.5) / 0.5
    cross_score = min(crosses / 8.0, 1.0)
    flip_score = min(flips / 4.0, 1.0)
    trend_penalty = min(trend / 0.5, 1.0)
    raw = 100.0 * (0.35 * cross_score + 0.25 * flip_score + 0.25 * range_score + 0.15 * (1.0 - trend_penalty))
    if not spacing_ok:
        raw *= 0.25
    atr_pct = (atr_val / mid) if atr_val else None
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "score": round(raw, 4),
        "eligible": spacing_ok and raw >= 25.0,
        "reason": None if spacing_ok else spacing_reason,
        "market": "binance_usdt_m_futures",
        "range_low": grid_low,
        "range_high": grid_high,
        "stop_price": stop if stop < grid_low else grid_low * 0.99,
        "atr": atr_val,
        "atr_pct": atr_pct,
        "mid_crosses": crosses,
        "swing_alternations": flips,
        "trend": trend,
        "bars": len(candles),
        "window_end": candles[-1].get("time"),
    }


def rank_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (-float(row.get("score") or 0.0), str(row.get("symbol") or "")))
