"""COMBO_02 v2 playbook evaluators — structure/BOS/CHoCH/sweep only."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.combo02_v2.htf_policy import trend_htf_allows
from app.research.combo02_v2.router import (
    PLAYBOOK_RANGE,
    PLAYBOOK_REVERSAL,
    PLAYBOOK_TREND,
)
from app.signals._candle_utils import ohlc

SWEEP_LOOKBACK = 24


def _fresh_bos(candles: Sequence[Mapping[str, Any]], as_of: int, bos: Mapping[str, Any] | None) -> bool:
    if not bos or bos.get("state") != "CONFIRMED" or not bos.get("direction"):
        return False
    level = bos.get("broken_level")
    if level is None or as_of < 1:
        return bool(bos.get("direction"))
    prev_c = ohlc(candles, as_of - 1)[3]
    if prev_c is None:
        return True
    if bos.get("direction") == "BULLISH_BOS":
        return float(prev_c) <= float(level)
    if bos.get("direction") == "BEARISH_BOS":
        return float(prev_c) >= float(level)
    return False


def _fresh_choch(
    candles: Sequence[Mapping[str, Any]], as_of: int, choch: Mapping[str, Any] | None
) -> bool:
    if not choch or choch.get("state") != "CONFIRMED" or not choch.get("direction"):
        return False
    level = choch.get("level")
    if level is None or as_of < 1:
        return True
    prev_c = ohlc(candles, as_of - 1)[3]
    if prev_c is None:
        return True
    if choch.get("direction") == "CHOCH_BULLISH":
        return float(prev_c) <= float(level)
    if choch.get("direction") == "CHOCH_BEARISH":
        return float(prev_c) >= float(level)
    return False


def detect_liquidity_sweep(
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    lookback: int = SWEEP_LOOKBACK,
) -> dict[str, Any] | None:
    """Failed breakdown/breakout: wick beyond rolling extreme, close back inside."""
    end = min(as_of_index, len(candles) - 1)
    if end < lookback + 1:
        return None
    # Prior window excludes current bar (no lookahead).
    window = candles[end - lookback : end]
    if len(window) < lookback:
        return None
    prior_low = min(float(ohlc(window, i)[2]) for i in range(len(window)))
    prior_high = max(float(ohlc(window, i)[1]) for i in range(len(window)))
    o, h, l, c = ohlc(candles, end)
    if None in (h, l, c):
        return None
    # Low sweep / failed breakdown
    if float(l) < prior_low and float(c) > prior_low:
        return {
            "event": "LOW_SWEEP",
            "direction": "LONG",
            "level": prior_low,
            "wick": float(l),
            "close": float(c),
        }
    # High sweep / failed breakout
    if float(h) > prior_high and float(c) < prior_high:
        return {
            "event": "HIGH_SWEEP",
            "direction": "SHORT",
            "level": prior_high,
            "wick": float(h),
            "close": float(c),
        }
    return None


def _15m_bullish_confirm(snap_15m: Any | None, analysis_15m: Mapping[str, Any] | None) -> tuple[bool, str]:
    if analysis_15m:
        bos = analysis_15m.get("bos") or {}
        choch = analysis_15m.get("choch") or {}
        if bos.get("state") == "CONFIRMED" and bos.get("direction") == "BULLISH_BOS":
            return True, "15M_BULLISH_BOS"
        if choch.get("state") == "CONFIRMED" and choch.get("direction") == "CHOCH_BULLISH":
            return True, "15M_BULLISH_CHOCH"
        trend = str((analysis_15m.get("trend") or {}).get("trend") or "")
        if trend == "BULLISH":
            return True, "15M_BULLISH_TREND"
    if snap_15m is not None:
        if str(getattr(snap_15m, "bos_state", "")).startswith("BULLISH"):
            return True, "15M_SNAP_BULLISH_BOS"
        if str(getattr(snap_15m, "choch_state", "")).startswith("BULLISH"):
            return True, "15M_SNAP_BULLISH_CHOCH"
        if str(getattr(snap_15m, "trend_state", "")) == "BULLISH":
            return True, "15M_SNAP_BULLISH_TREND"
    # 15m optional when unavailable — do not hard-block.
    if snap_15m is None and not analysis_15m:
        return True, "15M_UNAVAILABLE_OPTIONAL_PASS"
    return False, "15M_NO_BULLISH_CONFIRM"


def _15m_bearish_confirm(snap_15m: Any | None, analysis_15m: Mapping[str, Any] | None) -> tuple[bool, str]:
    if analysis_15m:
        bos = analysis_15m.get("bos") or {}
        choch = analysis_15m.get("choch") or {}
        if bos.get("state") == "CONFIRMED" and bos.get("direction") == "BEARISH_BOS":
            return True, "15M_BEARISH_BOS"
        if choch.get("state") == "CONFIRMED" and choch.get("direction") == "CHOCH_BEARISH":
            return True, "15M_BEARISH_CHOCH"
        trend = str((analysis_15m.get("trend") or {}).get("trend") or "")
        if trend == "BEARISH":
            return True, "15M_BEARISH_TREND"
    if snap_15m is not None:
        if str(getattr(snap_15m, "bos_state", "")).startswith("BEARISH"):
            return True, "15M_SNAP_BEARISH_BOS"
        if str(getattr(snap_15m, "choch_state", "")).startswith("BEARISH"):
            return True, "15M_SNAP_BEARISH_CHOCH"
        if str(getattr(snap_15m, "trend_state", "")) == "BEARISH":
            return True, "15M_SNAP_BEARISH_TREND"
    if snap_15m is None and not analysis_15m:
        return True, "15M_UNAVAILABLE_OPTIONAL_PASS"
    return False, "15M_NO_BEARISH_CONFIRM"


def evaluate_trend_playbook(
    *,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    tf_analysis: Mapping[str, Any],
    htf: Mapping[str, Any],
    snap_15m: Any | None,
    analysis_15m: Mapping[str, Any] | None,
    market_regime: str,
) -> dict[str, Any] | None:
    trend = tf_analysis.get("trend") or {}
    bos = tf_analysis.get("bos")
    trend_state = str(trend.get("trend") or "")
    if not _fresh_bos(candles, as_of_index, bos):
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_TREND,
            "reason": "NO_FRESH_1H_BOS",
            "regime": market_regime,
        }

    if bos.get("direction") == "BULLISH_BOS" and trend_state == "BULLISH":
        direction = "LONG"
        ok15, conf15 = _15m_bullish_confirm(snap_15m, analysis_15m)
    elif bos.get("direction") == "BEARISH_BOS" and trend_state == "BEARISH":
        direction = "SHORT"
        ok15, conf15 = _15m_bearish_confirm(snap_15m, analysis_15m)
    else:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_TREND,
            "reason": "TREND_BOS_MISMATCH",
            "regime": market_regime,
        }

    htf_ok, htf_reason = trend_htf_allows(direction, htf)
    if not htf_ok:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_TREND,
            "direction": direction,
            "reason": htf_reason,
            "regime": market_regime,
            "event": bos.get("direction"),
            "confirmation": conf15,
        }
    if not ok15:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_TREND,
            "direction": direction,
            "reason": conf15,
            "regime": market_regime,
            "event": bos.get("direction"),
        }
    return {
        "status": "SETUP",
        "playbook": PLAYBOOK_TREND,
        "direction": direction,
        "event": bos.get("direction"),
        "confirmation": conf15,
        "htf_state": htf_reason,
        "regime": market_regime,
        "bos": bos,
        "event_key": f"TREND:{direction}:{bos.get('broken_level')}",
    }


def evaluate_range_playbook(
    *,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    snap_15m: Any | None,
    analysis_15m: Mapping[str, Any] | None,
    market_regime: str,
) -> dict[str, Any] | None:
    sweep = detect_liquidity_sweep(candles, as_of_index)
    if not sweep:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_RANGE,
            "reason": "NO_RANGE_SWEEP",
            "regime": market_regime,
        }
    direction = str(sweep["direction"])
    if direction == "LONG":
        ok15, conf15 = _15m_bullish_confirm(snap_15m, analysis_15m)
    else:
        ok15, conf15 = _15m_bearish_confirm(snap_15m, analysis_15m)
    if not ok15:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_RANGE,
            "direction": direction,
            "reason": conf15,
            "regime": market_regime,
            "event": sweep["event"],
        }
    return {
        "status": "SETUP",
        "playbook": PLAYBOOK_RANGE,
        "direction": direction,
        "event": sweep["event"],
        "confirmation": conf15,
        "htf_state": "HTF_NOT_REQUIRED_RANGE",
        "regime": market_regime,
        "sweep": sweep,
        "event_key": f"RANGE:{direction}:{sweep['level']}:{as_of_index}",
    }


def evaluate_reversal_playbook(
    *,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    tf_analysis: Mapping[str, Any],
    snap_15m: Any | None,
    analysis_15m: Mapping[str, Any] | None,
    market_regime: str,
) -> dict[str, Any] | None:
    sweep = detect_liquidity_sweep(candles, as_of_index)
    choch = tf_analysis.get("choch")
    bos = tf_analysis.get("bos")

    # Prefer fresh CHoCH; allow fresh opposing BOS as structure shift.
    long_shift = (
        _fresh_choch(candles, as_of_index, choch)
        and choch
        and choch.get("direction") == "CHOCH_BULLISH"
    ) or (
        _fresh_bos(candles, as_of_index, bos)
        and bos
        and bos.get("direction") == "BULLISH_BOS"
    )
    short_shift = (
        _fresh_choch(candles, as_of_index, choch)
        and choch
        and choch.get("direction") == "CHOCH_BEARISH"
    ) or (
        _fresh_bos(candles, as_of_index, bos)
        and bos
        and bos.get("direction") == "BEARISH_BOS"
    )

    choch_d = (choch or {}).get("direction")
    if sweep and sweep["direction"] == "LONG" and long_shift:
        direction = "LONG"
        event = f"{sweep['event']}+BULLISH_SHIFT"
        ok15, conf15 = _15m_bullish_confirm(snap_15m, analysis_15m)
        level = sweep["level"]
    elif sweep and sweep["direction"] == "SHORT" and short_shift:
        direction = "SHORT"
        event = f"{sweep['event']}+BEARISH_SHIFT"
        ok15, conf15 = _15m_bearish_confirm(snap_15m, analysis_15m)
        level = sweep["level"]
    elif long_shift and not sweep:
        # Structure shift alone in TRANSITION — still require 15m confirm.
        direction = "LONG"
        event = choch_d if choch_d == "CHOCH_BULLISH" else "BULLISH_BOS"
        ok15, conf15 = _15m_bullish_confirm(snap_15m, analysis_15m)
        level = (choch or {}).get("level") or (bos or {}).get("broken_level")
    elif short_shift and not sweep:
        direction = "SHORT"
        event = choch_d if choch_d == "CHOCH_BEARISH" else "BEARISH_BOS"
        ok15, conf15 = _15m_bearish_confirm(snap_15m, analysis_15m)
        level = (choch or {}).get("level") or (bos or {}).get("broken_level")
    else:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_REVERSAL,
            "reason": "NO_REVERSAL_SHIFT",
            "regime": market_regime,
        }

    if not ok15:
        return {
            "status": "NO_SETUP",
            "playbook": PLAYBOOK_REVERSAL,
            "direction": direction,
            "reason": conf15,
            "regime": market_regime,
            "event": event,
        }
    return {
        "status": "SETUP",
        "playbook": PLAYBOOK_REVERSAL,
        "direction": direction,
        "event": event,
        "confirmation": conf15,
        "htf_state": "HTF_NOT_REQUIRED_REVERSAL",
        "regime": market_regime,
        "sweep": sweep,
        "choch": choch,
        "bos": bos,
        "event_key": f"REV:{direction}:{level}:{as_of_index}",
    }
