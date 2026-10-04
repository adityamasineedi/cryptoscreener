"""Entry / stop / regime classifiers for SHORT research diagnostics."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_research_diagnostics.constants import (
    CHASE_BEAR_BARS,
    CHASE_BODY_ATR,
    EARLY_ZONE_ATR,
    ENTRY_CHASING,
    ENTRY_EARLY,
    ENTRY_IMMEDIATE_BREAK,
    ENTRY_LATE,
    ENTRY_RETEST,
    HIGH_VOL_ATR_PERCENTILE,
    LATE_EXTENSION_ATR,
    LOW_VOL_ATR_PERCENTILE,
    NEAR_SUPPORT_ATR,
    REGIME_HIGH_VOL,
    REGIME_LOW_VOL,
    REGIME_NEAR_SUPPORT,
    REGIME_SIDEWAYS,
    REGIME_STRONG_BEAR,
    REGIME_STRONG_BULL,
    REGIME_WEAK_BEAR,
    REGIME_WEAK_BULL,
    RETEST_DISTANCE_ATR,
    STOP_GOOD,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
    STOP_WRONG_SIDE,
    STOP_TIGHT_ATR,
    STOP_WIDE_ATR,
)
from app.research.short_research_diagnostics.metrics import (
    atr_normalized_distance,
    candle_ohlc,
    entry_extension_atr,
)


def classify_entry_quality(
    *,
    entry_type: str | None,
    entry_extension_atr_value: float | None,
    bos_level: float | None,
    entry_price: float,
    atr: float | None,
    candles: Sequence[Mapping[str, Any]] | None = None,
    entry_index: int | None = None,
    retest_flag: bool | None = None,
) -> str:
    """Classify SHORT entry timing. Prefer RETEST over IMMEDIATE_BREAK when clear."""
    et = str(entry_type or "").upper()
    is_retest = bool(retest_flag) or et == "LIMIT_RETEST"
    ext = entry_extension_atr_value
    if ext is None:
        ext = entry_extension_atr(entry_price, bos_level, atr)

    # Chase: large bearish impulse into entry
    chasing = False
    if candles is not None and entry_index is not None and atr and atr > 0:
        start = max(0, entry_index - CHASE_BEAR_BARS + 1)
        bear_bars = 0
        large_body = False
        for i in range(start, entry_index + 1):
            o, h, l, c = candle_ohlc(candles[i])
            if c < o:
                bear_bars += 1
            body = abs(c - o)
            if body / atr >= CHASE_BODY_ATR:
                large_body = True
        chasing = large_body or bear_bars >= CHASE_BEAR_BARS

    if chasing and (ext is None or ext >= LATE_EXTENSION_ATR * 0.5):
        return ENTRY_CHASING
    if is_retest or (
        ext is not None and ext <= RETEST_DISTANCE_ATR and et != "MARKET"
    ):
        return ENTRY_RETEST
    if ext is not None and ext <= EARLY_ZONE_ATR:
        return ENTRY_EARLY
    if ext is not None and ext >= LATE_EXTENSION_ATR:
        return ENTRY_LATE
    if is_retest:
        return ENTRY_RETEST
    return ENTRY_IMMEDIATE_BREAK


def classify_stop_placement(
    *,
    direction: str,
    entry_price: float,
    stop_price: float,
    atr: float | None,
    recent_swing_high: float | None = None,
    outcome: str | None = None,
    mfe_r: float | None = None,
    mae_r: float | None = None,
    moved_to_tp_after_stop: bool | None = None,
) -> str:
    d = str(direction).upper()
    if d == "SHORT" and float(stop_price) <= float(entry_price):
        return STOP_WRONG_SIDE
    if d == "LONG" and float(stop_price) >= float(entry_price):
        return STOP_WRONG_SIDE

    stop_atr = atr_normalized_distance(entry_price, stop_price, atr)
    if stop_atr is not None and stop_atr < STOP_TIGHT_ATR:
        # Tight stop that got hit after little adverse structure
        if str(outcome or "").upper() in {"SL", "STOP"} and (
            (mfe_r is not None and mfe_r < 0.35)
            or (moved_to_tp_after_stop is True)
        ):
            return STOP_TOO_TIGHT
        if str(outcome or "").upper() in {"SL", "STOP"} and mae_r is not None and mae_r < 1.05:
            return STOP_TOO_TIGHT
        if stop_atr < STOP_TIGHT_ATR * 0.75:
            return STOP_TOO_TIGHT
    if stop_atr is not None and stop_atr > STOP_WIDE_ATR:
        return STOP_TOO_WIDE

    # If a meaningful swing high exists above stop for SHORT, stop may be inside
    # structure (too tight), not geometrically on the wrong side of entry.
    if (
        d == "SHORT"
        and recent_swing_high is not None
        and atr
        and float(recent_swing_high) > float(entry_price)
        and float(stop_price) < float(recent_swing_high)
        and (float(recent_swing_high) - float(stop_price)) / atr > 1.0
        and stop_atr is not None
        and stop_atr < 1.25
    ):
        return STOP_TOO_TIGHT

    return STOP_GOOD


def _trend_side(label: str | None) -> str | None:
    t = str(label or "").upper()
    if t == "BEARISH":
        return "BEAR"
    if t == "BULLISH":
        return "BULL"
    return None


def classify_market_regimes(
    *,
    symbol_trend: str | None,
    htf_1h: str | None,
    htf_4h: str | None,
    btc_trend: str | None = None,
    atr_percentile: float | None = None,
    distance_to_support_atr: float | None = None,
    adx_1h: float | None = None,
    adx_4h: float | None = None,
) -> list[str]:
    """Tag regimes using only pre-entry information (caller must not look ahead)."""
    labels: list[str] = []
    sides = [
        _trend_side(symbol_trend),
        _trend_side(htf_1h),
        _trend_side(htf_4h),
        _trend_side(btc_trend),
    ]
    bear_n = sum(1 for s in sides if s == "BEAR")
    bull_n = sum(1 for s in sides if s == "BULL")
    strength = max(
        [x for x in (adx_1h, adx_4h) if x is not None] or [0.0]
    )

    if bear_n >= 3 and (strength >= 25 or bear_n >= 4):
        labels.append(REGIME_STRONG_BEAR)
    elif bear_n >= 2:
        labels.append(REGIME_WEAK_BEAR)
    elif bull_n >= 3 and (strength >= 25 or bull_n >= 4):
        labels.append(REGIME_STRONG_BULL)
    elif bull_n >= 2:
        labels.append(REGIME_WEAK_BULL)
    else:
        labels.append(REGIME_SIDEWAYS)

    if atr_percentile is not None:
        if atr_percentile >= HIGH_VOL_ATR_PERCENTILE:
            labels.append(REGIME_HIGH_VOL)
        elif atr_percentile <= LOW_VOL_ATR_PERCENTILE:
            labels.append(REGIME_LOW_VOL)

    if distance_to_support_atr is not None and distance_to_support_atr <= NEAR_SUPPORT_ATR:
        labels.append(REGIME_NEAR_SUPPORT)

    return labels


def support_distance_atr(
    entry_price: float,
    support_level: float | None,
    atr: float | None,
) -> float | None:
    if support_level is None or atr is None or float(atr) <= 0:
        return None
    # For SHORT, support is below entry; distance positive when support < entry
    return (float(entry_price) - float(support_level)) / float(atr)


def summarize_by_category(
    rows: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        cat = str(r.get(key) or "UNKNOWN")
        buckets.setdefault(cat, []).append(r)

    out: dict[str, dict[str, Any]] = {}
    for cat, items in buckets.items():
        nets = [float(x["net_pnl"]) for x in items if x.get("net_pnl") is not None]
        rs = [float(x["R"]) for x in items if x.get("R") is not None]
        fees = [float(x["fees"]) for x in items if x.get("fees") is not None]
        mfes = [float(x["MFE"]) for x in items if x.get("MFE") is not None]
        maes = [float(x["MAE"]) for x in items if x.get("MAE") is not None]
        wins = sum(1 for r in rs if r > 0)
        gains = sum(r for r in rs if r > 0)
        losses = sum(abs(r) for r in rs if r < 0)
        out[cat] = {
            "trade_count": len(items),
            "win_rate": (wins / len(rs)) if rs else None,
            "average_net_r": (sum(rs) / len(rs)) if rs else None,
            "profit_factor": (gains / losses) if losses > 0 else (float("inf") if gains > 0 else None),
            "avg_mfe": (sum(mfes) / len(mfes)) if mfes else None,
            "avg_mae": (sum(maes) / len(maes)) if maes else None,
            "avg_fees": (sum(fees) / len(fees)) if fees else None,
            "net_pnl": sum(nets) if nets else None,
        }
    return out
