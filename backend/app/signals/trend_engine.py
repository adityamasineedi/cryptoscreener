"""Trend from confirmed market structure only (never candle color)."""

from __future__ import annotations

from typing import Any

from app.signals.schemas import SwingRecord, TrendState


def infer_trend(swings: list[SwingRecord]) -> dict[str, Any]:
    """Determine BULLISH / BEARISH / NEUTRAL / INSUFFICIENT_DATA from swings."""
    highs = [s for s in swings if s.swing_type == "HIGH"]
    lows = [s for s in swings if s.swing_type == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return {
            "trend": TrendState.INSUFFICIENT_DATA.value,
            "trend_strength": 0.0,
            "last_swing_high": highs[-1].price if highs else None,
            "last_swing_low": lows[-1].price if lows else None,
            "structure_sequence": [s.label for s in swings[-6:] if s.label],
            "reason": "Not enough confirmed swings for HH/HL or LH/LL sequence",
        }

    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    seq = [s.label for s in swings[-8:] if s.label]

    if h2.label == "HH" and l2.label == "HL" and h2.price > h1.price and l2.price > l1.price:
        strength = min(
            1.0,
            ((h2.price - h1.price) + (l2.price - l1.price))
            / max(abs(h1.price), 1e-12)
            * 50,
        )
        return {
            "trend": TrendState.BULLISH.value,
            "trend_strength": float(max(0.3, strength)),
            "last_swing_high": h2.price,
            "last_swing_low": l2.price,
            "structure_sequence": seq,
            "reason": "Higher High + Higher Low",
        }

    if h2.label == "LH" and l2.label == "LL" and h2.price < h1.price and l2.price < l1.price:
        strength = min(
            1.0,
            ((h1.price - h2.price) + (l1.price - l2.price))
            / max(abs(h1.price), 1e-12)
            * 50,
        )
        return {
            "trend": TrendState.BEARISH.value,
            "trend_strength": float(max(0.3, strength)),
            "last_swing_high": h2.price,
            "last_swing_low": l2.price,
            "structure_sequence": seq,
            "reason": "Lower High + Lower Low",
        }

    return {
        "trend": TrendState.NEUTRAL.value,
        "trend_strength": 0.1,
        "last_swing_high": h2.price,
        "last_swing_low": l2.price,
        "structure_sequence": seq,
        "reason": "No sufficiently clear HH+HL or LH+LL structure",
    }
