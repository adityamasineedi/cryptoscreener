"""Structure-based stop loss — never default fixed % SL."""

from __future__ import annotations

from typing import Any


def compute_stop(
    *,
    direction: str,
    entry_price: float,
    pullback: dict[str, Any] | None,
    demand_zone: tuple[float, float] | None = None,
    supply_zone: tuple[float, float] | None = None,
    atr: float | None = None,
    sl_buffer_atr: float = 0.2,
) -> dict[str, Any]:
    if entry_price <= 0:
        return {
            "entry_price": entry_price,
            "structural_stop": None,
            "final_stop": None,
            "reason": "Invalid entry",
        }

    structural: float | None = None
    invalidation_reason = ""
    if direction == "LONG":
        candidates: list[float] = []
        if pullback and pullback.get("retracement_low") is not None:
            candidates.append(float(pullback["retracement_low"]))
        if demand_zone:
            candidates.append(float(min(demand_zone)))
        if pullback and pullback.get("impulse_origin") is not None:
            candidates.append(float(pullback["impulse_origin"]))
        if not candidates:
            return {
                "entry_price": entry_price,
                "structural_stop": None,
                "final_stop": None,
                "reason": "No structural low for LONG stop",
            }
        structural = min(candidates)
        buffer = (atr or 0.0) * sl_buffer_atr
        final = structural - buffer
        invalidation_reason = "Close below structural long stop / demand invalidation"
    else:
        candidates = []
        if pullback and pullback.get("retracement_high") is not None:
            candidates.append(float(pullback["retracement_high"]))
        if supply_zone:
            candidates.append(float(max(supply_zone)))
        if pullback and pullback.get("impulse_origin") is not None:
            candidates.append(float(pullback["impulse_origin"]))
        if not candidates:
            return {
                "entry_price": entry_price,
                "structural_stop": None,
                "final_stop": None,
                "reason": "No structural high for SHORT stop",
            }
        structural = max(candidates)
        buffer = (atr or 0.0) * sl_buffer_atr
        final = structural + buffer
        invalidation_reason = "Close above structural short stop / supply invalidation"

    risk = abs(entry_price - final)
    return {
        "entry_price": entry_price,
        "structural_stop": structural,
        "buffer": (atr or 0.0) * sl_buffer_atr,
        "buffer_atr_mult": sl_buffer_atr,
        "final_stop": final,
        "risk_per_unit": risk,
        "invalidation_reason": invalidation_reason,
        "methodology": "Structure-based stop + optional ATR buffer (not fixed %)",
    }
