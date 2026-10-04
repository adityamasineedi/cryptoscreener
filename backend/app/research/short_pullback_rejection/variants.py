"""Stop / TP research variants for pullback-rejection (no winner on base alone)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import (
    SAFETY_STAMPS,
    STOP_BUFFER_0,
    STOP_BUFFER_025,
    STOP_BUFFER_050,
    STOP_BUFFER_BY_VARIANT,
    TP_FIXED_15R,
    TP_FIXED_1R,
    TP_FIXED_2R,
    TP_NEAREST_SUPPORT,
    TP_R_BY_VARIANT,
)
from app.research.short_pullback_rejection.signals import (
    build_stop_tp,
    generate_signals_for_symbol,
)
from app.research.short_pullback_rejection.simulation import simulate_signals

STOP_VARIANT_IDS = (STOP_BUFFER_0, STOP_BUFFER_025, STOP_BUFFER_050)
TP_VARIANT_IDS = (TP_NEAREST_SUPPORT, TP_FIXED_1R, TP_FIXED_15R, TP_FIXED_2R)


def generate_variant_signals(
    symbol: str,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
    *,
    stop_variant: str = STOP_BUFFER_025,
    tp_variant: str = TP_NEAREST_SUPPORT,
    thresholds: Mapping[str, float | int] | None = None,
) -> list[dict[str, Any]]:
    stop_buf = float(STOP_BUFFER_BY_VARIANT.get(stop_variant, 0.25))
    tp_r = TP_R_BY_VARIANT.get(tp_variant)
    tp_mode = "nearest_support" if tp_variant == TP_NEAREST_SUPPORT else "fixed_r"
    signals = generate_signals_for_symbol(
        symbol,
        candles_1h,
        candles_4h,
        thresholds=thresholds,
        stop_buffer_atr=stop_buf,
        tp_mode=tp_mode,
        tp_r=tp_r,
    )
    for s in signals:
        s["stop_variant"] = stop_variant
        s["tp_variant"] = tp_variant
        s.update(SAFETY_STAMPS)
    return signals


def run_variant_matrix(
    symbols_data: Mapping[str, tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]],
    *,
    thresholds: Mapping[str, float | int] | None = None,
    risk_usd: float = 20.0,
    stop_variants: Sequence[str] = STOP_VARIANT_IDS,
    tp_variants: Sequence[str] = TP_VARIANT_IDS,
) -> dict[str, dict[str, Any]]:
    """Run all stop×TP variants. Does not select a winner on base alone."""
    results: dict[str, dict[str, Any]] = {}
    candles_by_symbol = {sym: pair[0] for sym, pair in symbols_data.items()}

    for stop_v in stop_variants:
        for tp_v in tp_variants:
            key = f"{stop_v}__{tp_v}"
            all_signals: list[dict[str, Any]] = []
            for sym, (c1, c4) in symbols_data.items():
                all_signals.extend(
                    generate_variant_signals(
                        sym,
                        c1,
                        c4,
                        stop_variant=stop_v,
                        tp_variant=tp_v,
                        thresholds=thresholds,
                    )
                )
            trades = simulate_signals(all_signals, candles_by_symbol, risk_usd=risk_usd)
            results[key] = {
                "stop_variant": stop_v,
                "tp_variant": tp_v,
                "stop_buffer_atr": STOP_BUFFER_BY_VARIANT[stop_v],
                "tp_r": TP_R_BY_VARIANT[tp_v],
                "signals": all_signals,
                "trades": trades,
                **SAFETY_STAMPS,
            }
    return results


def apply_stop_buffer_to_signal(
    signal: Mapping[str, Any],
    *,
    stop_buffer_atr: float,
    tp_mode: str = "nearest_support",
    tp_r: float | None = None,
) -> dict[str, Any]:
    """Rebuild stop/TP for an existing signal under a stop-buffer variant."""
    row = dict(signal)
    levels = build_stop_tp(
        entry_price=float(row["entry_price"]),
        rejection_swing_high=float(row["rejection_swing_high"]),
        atr=float(row["atr_at_entry"]),
        support=row.get("support_level"),
        stop_buffer_atr=float(stop_buffer_atr),
        tp_mode=tp_mode,
        tp_r=tp_r,
    )
    row.update(levels)
    row.update(SAFETY_STAMPS)
    return row
