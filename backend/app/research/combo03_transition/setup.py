"""COMBO_03_TRANSITION setup helpers — reuse existing sweep / BOS / CHoCH / impulse."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.combo02_v2.playbooks import (
    _fresh_bos,
    _fresh_choch,
    detect_liquidity_sweep,
)
from app.research.combo03_transition.params import SWEEP_LOOKBACK
from app.signals._candle_utils import candle_time, ohlc


def regime_eligible(market_regime: str | None, eligible: frozenset[str]) -> bool:
    return str(market_regime or "UNKNOWN").upper() in eligible


def record_sweep_from_existing(
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    lookback: int = SWEEP_LOOKBACK,
) -> dict[str, Any] | None:
    """Wrap existing liquidity-sweep detector with COMBO_03 diagnostic labels."""
    sweep = detect_liquidity_sweep(candles, as_of_index, lookback=lookback)
    if not sweep:
        return None
    end = min(as_of_index, len(candles) - 1)
    o, h, l, c = ohlc(candles, end)
    ts = candle_time(candles[end])
    direction = str(sweep["direction"])
    if direction == "LONG":
        labels = ["sweep_below_low", "rejection_back_inside"]
        sweep_direction = "sweep_below_low"
    else:
        labels = ["sweep_above_high", "rejection_back_inside"]
        sweep_direction = "sweep_above_high"
    return {
        "event": sweep["event"],
        "direction": direction,
        "sweep_direction": sweep_direction,
        "level": float(sweep["level"]),
        "high": float(h) if h is not None else None,
        "low": float(l) if l is not None else None,
        "close": float(c) if c is not None else None,
        "close_relative_to_level": (
            "above" if c is not None and float(c) > float(sweep["level"]) else "below"
        ),
        "source": f"rolling_extreme_lookback_{lookback}",
        "timestamp": ts.isoformat() if ts else None,
        "bar_index": end,
        "labels": labels,
        # Existing detector only fires when wick beyond + close back inside,
        # so rejection is confirmed on the same bar as the sweep.
        "rejection_confirmed": True,
    }


def structure_shift_for_direction(
    *,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    direction: str,
    tf_analysis: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Require existing bullish/bearish BOS or CHoCH after the sweep."""
    bos = tf_analysis.get("bos")
    choch = tf_analysis.get("choch")
    if direction == "LONG":
        if (
            _fresh_choch(candles, as_of_index, choch)
            and choch
            and choch.get("direction") == "CHOCH_BULLISH"
        ):
            return {
                "structure_shift": "bullish_structure_shift",
                "bos_or_choch": "CHOCH_BULLISH",
                "level": choch.get("level"),
                "bos": bos,
                "choch": choch,
            }
        if (
            _fresh_bos(candles, as_of_index, bos)
            and bos
            and bos.get("direction") == "BULLISH_BOS"
        ):
            return {
                "structure_shift": "bullish_structure_shift",
                "bos_or_choch": "BULLISH_BOS",
                "level": bos.get("broken_level"),
                "bos": bos,
                "choch": choch,
            }
        return None
    if direction == "SHORT":
        if (
            _fresh_choch(candles, as_of_index, choch)
            and choch
            and choch.get("direction") == "CHOCH_BEARISH"
        ):
            return {
                "structure_shift": "bearish_structure_shift",
                "bos_or_choch": "CHOCH_BEARISH",
                "level": choch.get("level"),
                "bos": bos,
                "choch": choch,
            }
        if (
            _fresh_bos(candles, as_of_index, bos)
            and bos
            and bos.get("direction") == "BEARISH_BOS"
        ):
            return {
                "structure_shift": "bearish_structure_shift",
                "bos_or_choch": "BEARISH_BOS",
                "level": bos.get("broken_level"),
                "bos": bos,
                "choch": choch,
            }
        return None
    return None


def confirm_15m_mandatory(
    *,
    direction: str,
    analysis_15m: Mapping[str, Any] | None,
    snap_15m: Any | None,
    candles_15m_available: bool,
    as_of_15m_index: int | None,
) -> tuple[bool, str, float | None]:
    """Genuine 15m confirmation only. Missing/forming data → WAIT / NO TRADE.

    Unlike COMBO_02 v2, unavailable 15m is never treated as confirmation.
    """
    if not candles_15m_available or as_of_15m_index is None:
        return False, "WAIT_15M_DATA_MISSING", None
    if as_of_15m_index < 20:
        return False, "WAIT_15M_INSUFFICIENT_HISTORY", None

    close_px: float | None = None
    if analysis_15m is not None:
        # Prefer live analysis BOS/CHoCH/structure shift.
        bos = analysis_15m.get("bos") or {}
        choch = analysis_15m.get("choch") or {}
        if direction == "LONG":
            if bos.get("state") == "CONFIRMED" and bos.get("direction") == "BULLISH_BOS":
                return True, "bullish_15m_confirmation:15M_BULLISH_BOS", _maybe_float(
                    bos.get("close") or bos.get("broken_level")
                )
            if choch.get("state") == "CONFIRMED" and choch.get("direction") == "CHOCH_BULLISH":
                return True, "bullish_15m_confirmation:15M_BULLISH_CHOCH", _maybe_float(
                    choch.get("level")
                )
            retest = analysis_15m.get("retest") or {}
            if (
                retest.get("state") in ("PASS", "CONFIRMED", "HELD")
                and str((analysis_15m.get("trend") or {}).get("trend") or "") == "BULLISH"
            ):
                return True, "bullish_15m_confirmation:15M_RETEST_CONTINUATION", None
        else:
            if bos.get("state") == "CONFIRMED" and bos.get("direction") == "BEARISH_BOS":
                return True, "bearish_15m_confirmation:15M_BEARISH_BOS", _maybe_float(
                    bos.get("close") or bos.get("broken_level")
                )
            if choch.get("state") == "CONFIRMED" and choch.get("direction") == "CHOCH_BEARISH":
                return True, "bearish_15m_confirmation:15M_BEARISH_CHOCH", _maybe_float(
                    choch.get("level")
                )
            retest = analysis_15m.get("retest") or {}
            if (
                retest.get("state") in ("PASS", "CONFIRMED", "HELD")
                and str((analysis_15m.get("trend") or {}).get("trend") or "") == "BEARISH"
            ):
                return True, "bearish_15m_confirmation:15M_RETEST_CONTINUATION", None

    if snap_15m is not None:
        if direction == "LONG":
            if str(getattr(snap_15m, "bos_state", "")).startswith("BULLISH"):
                return True, "bullish_15m_confirmation:15M_SNAP_BULLISH_BOS", close_px
            if str(getattr(snap_15m, "choch_state", "")).startswith("BULLISH"):
                return True, "bullish_15m_confirmation:15M_SNAP_BULLISH_CHOCH", close_px
        else:
            if str(getattr(snap_15m, "bos_state", "")).startswith("BEARISH"):
                return True, "bearish_15m_confirmation:15M_SNAP_BEARISH_BOS", close_px
            if str(getattr(snap_15m, "choch_state", "")).startswith("BEARISH"):
                return True, "bearish_15m_confirmation:15M_SNAP_BEARISH_CHOCH", close_px

    if analysis_15m is None and snap_15m is None:
        return False, "WAIT_15M_NO_CONFIRMATION_AVAILABLE", None
    return False, "WAIT_15M_NO_DIRECTIONAL_CONFIRM", None


def displacement_from_impulse(impulse: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Record existing impulse/displacement metadata; do not invent new indicators."""
    if not impulse:
        return None
    return {
        "is_impulse": bool(impulse.get("is_impulse")),
        "quality": impulse.get("quality"),
        "atr_multiple": impulse.get("atr_multiple"),
        "body_ratio": impulse.get("body_ratio"),
        "direction": impulse.get("direction"),
        "reason": impulse.get("reason"),
    }


def displacement_passes(impulse: Mapping[str, Any] | None) -> bool:
    """Variant C: require existing impulse engine PASS (aligned displacement)."""
    return bool(impulse and impulse.get("is_impulse"))


def opposite_boundary_invalidated(
    *,
    direction: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    sweep_level: float | None,
) -> bool:
    """Invalidate if price closes beyond the opposite side of the swept level aggressively."""
    if sweep_level is None or as_of_index < 0:
        return False
    c = ohlc(candles, as_of_index)[3]
    if c is None:
        return False
    # Long setup from low sweep: invalidate if close well below swept level again
    # without recovery (sustained breakdown). Short: sustained breakout above.
    if direction == "LONG":
        return float(c) < float(sweep_level)
    if direction == "SHORT":
        return float(c) > float(sweep_level)
    return False


def _maybe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
