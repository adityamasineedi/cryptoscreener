"""Retest of broken structure with ATR-based (or % ) tolerance."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.signals._candle_utils import ohlc, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.schemas import Direction
from app.signals.trade_math import parse_direction_optional


def _direction_from_bos(bos: Mapping[str, Any] | None) -> Direction | None:
    """v1-compatible adapter: infer trade side from BOS when callers omit it."""
    if not bos:
        return None
    bos_dir = str(bos.get("direction") or "").upper()
    if bos_dir in ("BULLISH_BOS", "BULLISH", "LONG"):
        return Direction.LONG
    if bos_dir in ("BEARISH_BOS", "BEARISH", "SHORT"):
        return Direction.SHORT
    return None


def _normalize_trade_direction(direction: str | None) -> Direction | None:
    """Accept LONG/SHORT or legacy BULLISH/BEARISH labels for retest side."""
    if direction is None:
        return None
    text = str(direction).strip().upper()
    if not text:
        return None
    if text in ("LONG", "BULLISH"):
        return Direction.LONG
    if text in ("SHORT", "BEARISH"):
        return Direction.SHORT
    return parse_direction_optional(text)


def resolve_retest_side(
    *,
    direction: str | None,
    bos: Mapping[str, Any] | None,
) -> tuple[Direction | None, str | None]:
    """Resolve retest side without silently preferring LONG.

    Priority:
    1. Explicit trade direction (LONG/SHORT or BULLISH/BEARISH)
    2. BOS adapter when direction omitted (preserves existing v1 callers)
    3. Mismatch → fail closed
    """
    trade_side = _normalize_trade_direction(direction)
    bos_side = _direction_from_bos(bos)
    if trade_side is not None and bos_side is not None and trade_side != bos_side:
        return None, (
            f"direction/BOS mismatch: direction={trade_side.value} "
            f"bos={bos.get('direction') if bos else None}"
        )
    if trade_side is not None:
        return trade_side, None
    if bos_side is not None:
        return bos_side, None
    return None, "missing direction and BOS side for retest"


def detect_retest(
    candles: Sequence[Mapping[str, Any]],
    bos: dict[str, Any] | None,
    pullback: dict[str, Any] | None,
    config: SignalConfig,
    *,
    direction: str | None,
    as_of_index: int | None = None,
    atr_value: float | None = None,
) -> dict[str, Any]:
    if not bos or bos.get("state") != "CONFIRMED" or bos.get("broken_level") is None:
        return {"retest": False, "state": "WAITING", "reason": "No broken level to retest"}
    if not pullback or pullback.get("pullback_state") in ("WAITING", "NONE", None):
        return {"retest": False, "state": "WAITING", "reason": "Waiting for pullback"}

    side, side_reason = resolve_retest_side(direction=direction, bos=bos)
    if side is None:
        return {
            "retest": False,
            "state": "WAITING",
            "reason": side_reason or "Unable to resolve retest side",
            "direction": None,
            "mismatch": "mismatch" in (side_reason or "").lower(),
        }

    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    level = float(bos["broken_level"])
    if atr_value is None:
        _, highs, lows, closes, _ = series_ohlcv(list(candles[: end + 1]))
        atr_val = calc_atr(highs, lows, closes, config.atr_period) or 0.0
    else:
        atr_val = float(atr_value)
    tol = atr_val * config.retest_atr_tolerance
    if config.retest_pct_tolerance is not None:
        tol = max(tol, abs(level) * float(config.retest_pct_tolerance) / 100.0)

    _, h, l, c = ohlc(candles, end)
    distance = abs(((l + h) / 2.0) - level)
    if side == Direction.LONG:
        touched = l <= level + tol and h >= level - tol
        held = c >= level - tol
        candidate = touched and held and pullback.get("structure_intact", True)
        reason = (
            f"Bullish retest of {level}: distance={distance:.6g}, tol={tol:.6g}"
            if candidate
            else f"No bullish retest hold near {level}"
        )
    else:
        touched = h >= level - tol and l <= level + tol
        held = c <= level + tol
        candidate = touched and held and pullback.get("structure_intact", True)
        reason = (
            f"Bearish retest of {level}: distance={distance:.6g}, tol={tol:.6g}"
            if candidate
            else f"No bearish retest hold near {level}"
        )

    return {
        "retest": bool(candidate),
        "state": "CONFIRMED" if candidate else "ACTIVE" if pullback.get("pullback_state") == "ACTIVE" else "WAITING",
        "level": level,
        "distance": distance,
        "tolerance": tol,
        "atr": atr_val,
        "reason": reason,
        "direction": side.value,
    }
