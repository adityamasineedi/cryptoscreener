"""Retest of broken structure with ATR-based (or % ) tolerance."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.signals._candle_utils import ohlc, series_ohlcv
from app.signals.config import SignalConfig


def detect_retest(
    candles: Sequence[Mapping[str, Any]],
    bos: dict[str, Any] | None,
    pullback: dict[str, Any] | None,
    config: SignalConfig,
    *,
    direction: str | None,
    as_of_index: int | None = None,
) -> dict[str, Any]:
    if not bos or bos.get("state") != "CONFIRMED" or bos.get("broken_level") is None:
        return {"retest": False, "state": "WAITING", "reason": "No broken level to retest"}
    if not pullback or pullback.get("pullback_state") in ("WAITING", "NONE", None):
        return {"retest": False, "state": "WAITING", "reason": "Waiting for pullback"}

    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    level = float(bos["broken_level"])
    _, highs, lows, closes, _ = series_ohlcv(list(candles[: end + 1]))
    atr_val = calc_atr(highs, lows, closes, config.atr_period) or 0.0
    tol = atr_val * config.retest_atr_tolerance
    if config.retest_pct_tolerance is not None:
        tol = max(tol, abs(level) * float(config.retest_pct_tolerance) / 100.0)

    _, h, l, c = ohlc(candles, end)
    distance = abs(((l + h) / 2.0) - level)
    if direction == "BULLISH" or (bos.get("direction") == "BULLISH_BOS"):
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
    }
