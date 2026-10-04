"""HTF / LTF regime and structure helpers (no-lookahead)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.engines.structure.engine import (
    SwingLabel,
    TrendBias,
    detect_swings,
    infer_trend,
    label_swings,
)


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def candle_time(c: Mapping[str, Any]) -> datetime | None:
    return _parse_time(c.get("time") or c.get("timestamp") or c.get("open_time"))


def candle_ohlc(c: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(c.get("open") or c.get("o") or 0),
        float(c.get("high") or c.get("h") or 0),
        float(c.get("low") or c.get("l") or 0),
        float(c.get("close") or c.get("c") or 0),
    )


def atr_at(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    period: int = 14,
) -> float | None:
    if index < 0 or index >= len(candles) or index + 1 < period:
        return None
    highs = [candle_ohlc(c)[1] for c in candles[: index + 1]]
    lows = [candle_ohlc(c)[2] for c in candles[: index + 1]]
    closes = [candle_ohlc(c)[3] for c in candles[: index + 1]]
    return calc_atr(highs, lows, closes, period)


def truncate_closed_htf(
    htf_candles: Sequence[Mapping[str, Any]],
    *,
    asof: datetime,
) -> list[dict[str, Any]]:
    """Include only HTF bars whose open_time is strictly before ``asof``.

    Fail-closed no-lookahead: a 4h bar that has not yet opened is unavailable.
    Bars that opened before asof but have not closed are treated as forming;
    we keep them only when their open_time + duration would still leave the
    close at/before asof. For simplicity and safety we require open_time < asof
    and prefer the last fully known bar by excluding the bar whose open equals
    the current HTF bucket when asof falls inside it.
    """
    out: list[dict[str, Any]] = []
    for c in htf_candles:
        ts = candle_time(c)
        if ts is None:
            continue
        # Only bars that have opened before the signal moment.
        if ts < asof:
            out.append(dict(c))
    # Drop the last bar if it may still be forming (open + 4h > asof).
    if out:
        last_ts = candle_time(out[-1])
        if last_ts is not None:
            # 4h = 14400s; if asof is still within this bar, exclude it.
            if (asof - last_ts).total_seconds() < 4 * 3600:
                out = out[:-1]
    return out


def structure_snapshot(
    candles: Sequence[Mapping[str, Any]],
    *,
    swing_left: int = 2,
    swing_right: int = 2,
    end_index: int | None = None,
) -> dict[str, Any]:
    """Structure using only candles[:end_index+1] (no future bars)."""
    if not candles:
        return {
            "trend": "RANGE",
            "structure": None,
            "has_lh_ll": False,
            "is_bearish": False,
            "is_corrective": False,
            "swing_highs": [],
            "swing_lows": [],
            "last_swing_high": None,
            "last_swing_low": None,
        }
    end = len(candles) - 1 if end_index is None else int(end_index)
    end = max(0, min(end, len(candles) - 1))
    window = [dict(c) for c in candles[: end + 1]]
    # Swings need right confirmation bars; only confirm swings with index <= end - swing_right.
    swings = detect_swings(window, swing_left, swing_right)
    confirmed = [s for s in swings if s.index <= end - swing_right]
    labeled = label_swings(confirmed)
    trend = infer_trend(labeled)
    highs = [s for s in labeled if s.kind == "high"]
    lows = [s for s in labeled if s.kind == "low"]
    recent_labels = {s.label for s in labeled[-6:] if s.label}
    has_lh = SwingLabel.LH in recent_labels
    has_ll = SwingLabel.LL in recent_labels
    has_lh_ll = has_lh and has_ll
    is_bearish = trend == TrendBias.BEARISH or has_lh_ll
    # Corrective: range / pullback while last confirmed swing high is LH, or mixed.
    is_corrective = (not is_bearish) and (
        trend == TrendBias.RANGE or has_lh or SwingLabel.HL in recent_labels
    )
    last_high = highs[-1].price if highs else None
    last_low = lows[-1].price if lows else None
    structure_label = None
    if has_lh_ll:
        structure_label = "LH/LL"
    elif has_lh:
        structure_label = "LH"
    elif has_ll:
        structure_label = "LL"
    elif trend == TrendBias.BULLISH:
        structure_label = "HH/HL"
    else:
        structure_label = str(trend.value).upper()
    return {
        "trend": str(trend.value).upper(),
        "structure": structure_label,
        "has_lh_ll": has_lh_ll,
        "is_bearish": bool(is_bearish),
        "is_corrective": bool(is_corrective or is_bearish),
        "swing_highs": [{"index": s.index, "price": s.price, "label": s.label.value if s.label else None} for s in highs],
        "swing_lows": [{"index": s.index, "price": s.price, "label": s.label.value if s.label else None} for s in lows],
        "last_swing_high": last_high,
        "last_swing_low": last_low,
    }


def htf_regime_at(
    candles_4h: Sequence[Mapping[str, Any]],
    *,
    asof: datetime,
    swing_left: int = 2,
    swing_right: int = 2,
) -> dict[str, Any]:
    truncated = truncate_closed_htf(candles_4h, asof=asof)
    snap = structure_snapshot(
        truncated, swing_left=swing_left, swing_right=swing_right
    )
    bearish = bool(snap["is_bearish"] and snap["has_lh_ll"])
    close = candle_ohlc(truncated[-1])[3] if truncated else None
    near_support = False
    atr_v = atr_at(truncated, len(truncated) - 1) if truncated else None
    if (
        close is not None
        and snap["last_swing_low"] is not None
        and atr_v
        and atr_v > 0
    ):
        dist = abs(close - float(snap["last_swing_low"])) / atr_v
        near_support = dist <= 0.50
    return {
        "htf_time": candle_time(truncated[-1]).isoformat() if truncated and candle_time(truncated[-1]) else None,
        "htf_regime": "BEARISH" if bearish else str(snap["trend"]),
        "htf_structure": snap["structure"],
        "htf_bearish_lh_ll": bearish,
        "htf_near_major_support": near_support,
        "htf_last_swing_low": snap["last_swing_low"],
        "htf_last_swing_high": snap["last_swing_high"],
        "htf_bars_used": len(truncated),
        **snap,
    }
