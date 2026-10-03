"""Objective displacement / impulse detection using ATR, body ratio, RVOL."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.signals._candle_utils import ohlc, series_ohlcv, volume_at
from app.signals.config import SignalConfig
from app.signals.schemas import ImpulseQuality


def detect_impulse(
    candles: Sequence[Mapping[str, Any]],
    bos: dict[str, Any] | None,
    config: SignalConfig,
    *,
    rvol: float | None = None,
    as_of_index: int | None = None,
    atr_value: float | None = None,
    volumes: Sequence[float] | None = None,
) -> dict[str, Any]:
    if not candles:
        return {
            "is_impulse": False,
            "direction": None,
            "quality": ImpulseQuality.INVALID.value,
            "reason": "No candles",
        }
    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    o, h, l, c = ohlc(candles, end)
    rng = h - l
    body = abs(c - o)
    body_ratio = (body / rng) if rng > 0 else 0.0
    if atr_value is None or (rvol is None and volumes is None):
        _, highs, lows, closes, vols = series_ohlcv(list(candles[: end + 1]))
        atr_val = (
            float(atr_value)
            if atr_value is not None
            else (calc_atr(highs, lows, closes, config.atr_period) or 0.0)
        )
        if volumes is None:
            volumes = vols
    else:
        atr_val = float(atr_value)
    atr_multiple = (rng / atr_val) if atr_val > 0 else 0.0

    if rvol is None and volumes is not None and len(volumes) >= 20 and end < len(volumes):
        avg = sum(volumes[max(0, end - 20) : end]) / 20.0
        rvol = (volumes[end] / avg) if avg > 0 else None

    bos_dir = (bos or {}).get("direction")
    candle_bull = c > o
    candle_bear = c < o
    aligned = False
    direction = None
    if bos_dir == "BULLISH_BOS" and candle_bull:
        aligned = True
        direction = "BULLISH"
    elif bos_dir == "BEARISH_BOS" and candle_bear:
        aligned = True
        direction = "BEARISH"
    elif bos_dir in (None,):
        # Without BOS still measure displacement quality, but mark not aligned
        direction = "BULLISH" if candle_bull else "BEARISH" if candle_bear else None

    passes_range = atr_multiple >= config.atr_multiplier
    passes_body = body_ratio >= config.min_body_ratio
    passes_rvol = rvol is not None and rvol >= config.min_rvol
    is_impulse = bool(passes_range and passes_body and passes_rvol and aligned)

    if is_impulse and atr_multiple >= config.atr_multiplier * 1.3 and (rvol or 0) >= config.min_rvol * 1.2:
        quality = ImpulseQuality.STRONG.value
    elif is_impulse:
        quality = ImpulseQuality.MODERATE.value
    elif passes_range and passes_body:
        quality = ImpulseQuality.WEAK.value
    else:
        quality = ImpulseQuality.INVALID.value

    reasons = []
    if not passes_range:
        reasons.append(f"range {atr_multiple:.2f} ATR < {config.atr_multiplier}")
    if not passes_body:
        reasons.append(f"body_ratio {body_ratio:.2f} < {config.min_body_ratio}")
    if rvol is None:
        reasons.append("RVOL N/A")
    elif not passes_rvol:
        reasons.append(f"RVOL {rvol:.2f} < {config.min_rvol}")
    if not aligned:
        reasons.append("direction not aligned with BOS/structure")
    if is_impulse:
        reason = (
            f"Impulse {direction}: range={atr_multiple:.2f} ATR, "
            f"body={body_ratio:.2f}, RVOL={rvol}"
        )
    else:
        reason = "; ".join(reasons) if reasons else "Impulse criteria not met"

    return {
        "is_impulse": is_impulse,
        "direction": direction,
        "range": rng,
        "body_ratio": body_ratio,
        "atr": atr_val,
        "atr_multiple": atr_multiple,
        "rvol": rvol,
        "volume": volume_at(candles, end),
        "quality": quality,
        "reason": reason,
        "bar_index": end,
        "impulse_origin": l if direction == "BULLISH" else h if direction == "BEARISH" else None,
        "impulse_end": h if direction == "BULLISH" else l if direction == "BEARISH" else None,
        "config_defaults_note": (
            "ATR_MULTIPLIER / MIN_BODY_RATIO / MIN_RVOL are configuration defaults only"
        ),
    }
