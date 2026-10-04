"""Broken support→resistance zone detection and pullback retest (no-lookahead)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import DEFAULT_THRESHOLDS
from app.research.short_pullback_rejection.regime import (
    atr_at,
    candle_ohlc,
    candle_time,
    structure_snapshot,
)


def find_broken_support_zones(
    candles: Sequence[Mapping[str, Any]],
    *,
    asof_index: int,
    thresholds: Mapping[str, float | int] | None = None,
) -> list[dict[str, Any]]:
    """Prior swing lows that have been broken (close below) before ``asof_index``.

    Zone = [low - w, low + w] with w = zone_width_atr * ATR at break.
    Only uses information available at asof_index (no future bars).
    """
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    if asof_index < 10 or asof_index >= len(candles):
        return []
    snap = structure_snapshot(
        candles,
        swing_left=int(th["swing_left"]),
        swing_right=int(th["swing_right"]),
        end_index=asof_index,
    )
    atr_v = atr_at(candles, asof_index, period=int(th["atr_period"]))
    if not atr_v or atr_v <= 0:
        return []
    width = float(th["zone_width_atr"]) * atr_v
    zones: list[dict[str, Any]] = []
    for sw in snap["swing_lows"]:
        zi = int(sw["index"])
        level = float(sw["price"])
        if zi >= asof_index - int(th["swing_right"]):
            continue
        broken_at = None
        for j in range(zi + int(th["swing_right"]) + 1, asof_index + 1):
            _, _, _, cl = candle_ohlc(candles[j])
            if cl < level:
                broken_at = j
                break
        if broken_at is None:
            continue
        # After break, price must have traded below the zone (extension available).
        traded_below = False
        for j in range(broken_at, asof_index + 1):
            _, _, low, _ = candle_ohlc(candles[j])
            if low < level - width:
                traded_below = True
                break
        if not traded_below:
            continue
        zones.append(
            {
                "zone_mid": level,
                "setup_zone_high": level + width,
                "setup_zone_low": level - width,
                "zone_width": 2.0 * width,
                "swing_low_index": zi,
                "broken_at_index": broken_at,
                "invalidation_swing_high": snap["last_swing_high"],
            }
        )
    # Prefer most recent broken levels.
    zones.sort(key=lambda z: z["broken_at_index"], reverse=True)
    return zones


def detect_retest(
    candles: Sequence[Mapping[str, Any]],
    zone: Mapping[str, Any],
    *,
    from_index: int,
    to_index: int,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any] | None:
    """First bar in (from_index, to_index] whose high revisits the resistance zone."""
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    z_hi = float(zone["setup_zone_high"])
    z_lo = float(zone["setup_zone_low"])
    inv = zone.get("invalidation_swing_high")
    atr_v = atr_at(candles, to_index, period=int(th["atr_period"])) or 0.0
    tol = float(th["retest_tolerance_atr"]) * atr_v
    for i in range(max(from_index + 1, 0), min(to_index, len(candles) - 1) + 1):
        _, high, low, close = candle_ohlc(candles[i])
        # Pullback into zone from below: high reaches zone, close still not deeply above.
        touched = high >= (z_lo - tol) and low <= (z_hi + tol) and high >= z_lo
        if not touched:
            continue
        if inv is not None and high > float(inv):
            continue
        if close > z_hi + tol:
            # Broke above zone — invalid retest for SHORT resistance.
            continue
        ts = candle_time(candles[i])
        return {
            "retest_index": i,
            "retest_time": ts.isoformat() if ts else None,
            "retest_high": high,
            "retest_close": close,
            "setup_zone_high": z_hi,
            "setup_zone_low": z_lo,
            "zone_width": float(zone["zone_width"]),
            "invalidation_swing_high": inv,
        }
    return None


def entry_extension_atr(
    *,
    entry_price: float,
    zone_low: float,
    atr: float | None,
) -> float | None:
    """How far below the zone the entry sits, in ATR (overextension for SHORT)."""
    if atr is None or atr <= 0:
        return None
    # Positive = entry below zone (extended); 0 / negative = at or inside zone.
    return (float(zone_low) - float(entry_price)) / float(atr)


def is_overextended(
    *,
    entry_price: float,
    zone_low: float,
    atr: float | None,
    thresholds: Mapping[str, float | int] | None = None,
) -> bool:
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    ext = entry_extension_atr(entry_price=entry_price, zone_low=zone_low, atr=atr)
    if ext is None:
        return True  # fail closed
    return float(ext) > float(th["max_entry_extension_atr"])


def nearest_confirmed_support(
    candles: Sequence[Mapping[str, Any]],
    *,
    asof_index: int,
    entry_price: float,
    thresholds: Mapping[str, float | int] | None = None,
) -> float | None:
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    snap = structure_snapshot(
        candles,
        swing_left=int(th["swing_left"]),
        swing_right=int(th["swing_right"]),
        end_index=asof_index,
    )
    supports = [
        float(s["price"])
        for s in snap["swing_lows"]
        if float(s["price"]) < float(entry_price)
    ]
    if not supports:
        # Fallback: recent trough low below entry.
        lookback = min(asof_index, int(th["pullback_lookback"]))
        lows = [candle_ohlc(candles[i])[2] for i in range(asof_index - lookback, asof_index + 1)]
        below = [l for l in lows if l < float(entry_price)]
        return min(below) if below else None
    return max(supports)  # nearest below entry
