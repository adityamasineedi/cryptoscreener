"""Signal generation for SHORT pullback-rejection research."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr_series
from app.engines.structure.engine import (
    SwingLabel,
    TrendBias,
    detect_swings,
    infer_trend,
    label_swings,
)
from app.research.short_pullback_rejection.constants import (
    DEFAULT_THRESHOLDS,
    DIRECTION,
    SAFETY_STAMPS,
    STRATEGY_ID,
)
from app.research.short_pullback_rejection.regime import (
    candle_ohlc,
    candle_time,
)
from app.research.short_pullback_rejection.rejection import detect_rejection
from app.research.short_pullback_rejection.zones import (
    entry_extension_atr,
    is_overextended,
)
from app.signals.schemas import Direction
from app.signals.trade_math import validate_trade_geometry


class PullbackRejectionSignalError(ValueError):
    """Malformed or invalid pullback-rejection signal."""


def strategy_identity(**extra: Any) -> dict[str, Any]:
    out = {**SAFETY_STAMPS, **extra}
    out["strategy_id"] = STRATEGY_ID
    out["combo_version"] = SAFETY_STAMPS["combo_version"]
    out["source"] = SAFETY_STAMPS["source"]
    out["direction"] = DIRECTION
    out["paper_eligible"] = False
    out["production_approved"] = False
    out["telegram_eligible"] = False
    return out


def is_pullback_rejection_identity(payload: Mapping[str, Any] | None) -> bool:
    if not payload or not isinstance(payload, Mapping):
        return False
    snip = (
        payload.get("signal_snippet")
        if isinstance(payload.get("signal_snippet"), Mapping)
        else {}
    )
    for src in (payload, snip):
        if str(src.get("strategy_id") or "") == STRATEGY_ID:
            return True
        if str(src.get("combo_version") or "").lower() == str(
            SAFETY_STAMPS["combo_version"]
        ).lower():
            return True
    return False


def build_stop_tp(
    *,
    entry_price: float,
    rejection_swing_high: float,
    atr: float,
    support: float | None,
    stop_buffer_atr: float,
    tp_mode: str,
    tp_r: float | None,
) -> dict[str, Any]:
    stop = float(rejection_swing_high) + float(stop_buffer_atr) * float(atr)
    risk = stop - float(entry_price)
    if risk <= 0:
        raise PullbackRejectionSignalError("stop_not_above_entry")

    if tp_mode == "nearest_support" or tp_r is None:
        if support is None or float(support) >= float(entry_price):
            raise PullbackRejectionSignalError("no_reachable_support")
        tp = float(support)
    else:
        tp = float(entry_price) - float(tp_r) * risk
        if support is not None and float(support) < float(entry_price):
            tp = max(float(support), tp)

    geom = validate_trade_geometry(
        Direction.SHORT, entry_price, stop, tp, require_take_profit=True
    )
    if not geom.ok:
        raise PullbackRejectionSignalError(geom.reason or "invalid_geometry")
    planned_rr = (float(entry_price) - float(tp)) / risk if risk > 0 else None
    support_distance = (
        float(entry_price) - float(support) if support is not None else None
    )
    support_distance_r = (
        support_distance / risk if support_distance is not None and risk > 0 else None
    )
    return {
        "stop_price": stop,
        "take_profit_price": tp,
        "stop_distance": risk,
        "stop_distance_atr": risk / float(atr) if atr else None,
        "planned_rr": planned_rr,
        "support_distance": support_distance,
        "support_distance_r": support_distance_r,
    }


def validate_signal_geometry(signal: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if str(signal.get("direction") or "").upper() != "SHORT":
        errors.append("direction_not_short")
    try:
        entry = float(signal["entry_price"])
        stop = float(signal["stop_price"])
        tp = float(signal["take_profit_price"])
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "errors": ["missing_prices"]}
    if not (stop > entry > tp):
        errors.append("geometry_not_short")
    geom = validate_trade_geometry(
        Direction.SHORT, entry, stop, tp, require_take_profit=True
    )
    if not geom.ok:
        errors.append(geom.reason or "validate_trade_geometry_failed")

    retest_t = signal.get("retest_time")
    rejection_t = signal.get("rejection_time")
    entry_t = signal.get("entry_time")
    if not (retest_t and rejection_t and entry_t):
        errors.append("missing_timestamps")
    else:
        if str(retest_t) > str(rejection_t):
            errors.append("retest_after_rejection")
        if str(rejection_t) > str(entry_t):
            errors.append("rejection_after_entry")

    if signal.get("paper_eligible") is not False:
        errors.append("paper_eligible_not_false")
    if signal.get("production_approved") is not False:
        errors.append("production_approved_not_false")
    if signal.get("telegram_eligible") is not False:
        errors.append("telegram_eligible_not_false")

    return {"ok": not errors, "errors": errors}


def _structure_from_swings(labeled: list[Any]) -> dict[str, Any]:
    highs = [s for s in labeled if s.kind == "high"]
    lows = [s for s in labeled if s.kind == "low"]
    trend = infer_trend(labeled)
    recent_labels = {s.label for s in labeled[-6:] if s.label}
    has_lh = SwingLabel.LH in recent_labels
    has_ll = SwingLabel.LL in recent_labels
    has_lh_ll = has_lh and has_ll
    is_bearish = trend == TrendBias.BEARISH or has_lh_ll
    is_corrective = (not is_bearish) and (
        trend == TrendBias.RANGE or has_lh or SwingLabel.HL in recent_labels
    )
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
        "swing_highs": highs,
        "swing_lows": lows,
        "last_swing_high": highs[-1].price if highs else None,
        "last_swing_low": lows[-1].price if lows else None,
    }


def _precompute_htf_cache(
    candles_4h: Sequence[Mapping[str, Any]],
    *,
    swing_left: int,
    swing_right: int,
) -> list[dict[str, Any]]:
    """One HTF snapshot per 4h bar index (using only bars confirmed by that tip)."""
    cache: list[dict[str, Any]] = []
    if not candles_4h:
        return cache
    swings = detect_swings(list(candles_4h), swing_left, swing_right)
    for tip in range(len(candles_4h)):
        confirmed = [s for s in swings if s.index <= tip - swing_right]
        labeled = label_swings(confirmed)
        snap = _structure_from_swings(labeled)
        bearish = bool(snap["is_bearish"] and snap["has_lh_ll"])
        close = candle_ohlc(candles_4h[tip])[3]
        atr_vals = [
            candle_ohlc(c)[1] for c in candles_4h[: tip + 1]
        ]  # placeholder unused
        _ = atr_vals
        near_support = False
        # Approximate ATR from recent ranges
        if tip >= 14:
            ranges = [
                candle_ohlc(candles_4h[j])[1] - candle_ohlc(candles_4h[j])[2]
                for j in range(tip - 13, tip + 1)
            ]
            atr_v = sum(ranges) / len(ranges)
            if atr_v > 0 and snap["last_swing_low"] is not None:
                near_support = abs(close - float(snap["last_swing_low"])) / atr_v <= 0.50
        ts = candle_time(candles_4h[tip])
        cache.append(
            {
                "htf_time": ts.isoformat() if ts else None,
                "htf_regime": "BEARISH" if bearish else str(snap["trend"]),
                "htf_structure": snap["structure"],
                "htf_bearish_lh_ll": bearish,
                "htf_near_major_support": near_support,
                "htf_last_swing_low": snap["last_swing_low"],
                "htf_last_swing_high": snap["last_swing_high"],
                "is_bearish": snap["is_bearish"],
                "has_lh_ll": snap["has_lh_ll"],
            }
        )
    return cache


def _build_htf_index_map(
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
) -> list[int | None]:
    """For each 1h bar, the last fully closed 4h index (or None)."""
    from datetime import timedelta

    out: list[int | None] = [None] * len(candles_1h)
    j = -1
    for i, c in enumerate(candles_1h):
        asof = candle_time(c)
        if asof is None:
            continue
        while j + 1 < len(candles_4h):
            ts = candle_time(candles_4h[j + 1])
            if ts is None:
                j += 1
                continue
            if ts + timedelta(hours=4) <= asof:
                j += 1
            else:
                break
        out[i] = j if j >= 0 else None
    return out


def generate_signals_for_symbol(
    symbol: str,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
    *,
    thresholds: Mapping[str, float | int] | None = None,
    stop_buffer_atr: float = 0.25,
    tp_mode: str = "nearest_support",
    tp_r: float | None = None,
) -> list[dict[str, Any]]:
    """Scan 1h series for pullback-rejection SHORT signals (research-only).

    Uses precomputed swings / ATR / HTF cache for O(n) scanning.
    """
    th = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    signals: list[dict[str, Any]] = []
    n = len(candles_1h)
    if n < 50:
        return signals

    swing_left = int(th["swing_left"])
    swing_right = int(th["swing_right"])
    atr_period = int(th["atr_period"])
    zone_w_atr = float(th["zone_width_atr"])
    retest_tol_atr = float(th["retest_tolerance_atr"])
    expiry = int(th["rejection_expiry_bars"])
    min_sup_atr = float(th["min_support_distance_atr"])

    highs = [candle_ohlc(c)[1] for c in candles_1h]
    lows = [candle_ohlc(c)[2] for c in candles_1h]
    closes = [candle_ohlc(c)[3] for c in candles_1h]
    atrs = atr_series(highs, lows, closes, atr_period)

    swings_all = detect_swings(list(candles_1h), swing_left, swing_right)
    htf_cache = _precompute_htf_cache(
        candles_4h, swing_left=swing_left, swing_right=swing_right
    )
    htf_for_1h = _build_htf_index_map(candles_1h, candles_4h)

    # Active broken-support zones: list of dicts
    active_zones: list[dict[str, Any]] = []
    broken_swing_ids: set[int] = set()
    # Unbroken candidate swing lows awaiting break confirmation.
    watch_lows: dict[int, float] = {}
    last_entry_index = -10_000
    pending_retests: dict[int, dict[str, Any]] = {}
    swing_ptr = 0
    confirmed: list[Any] = []
    ltf: dict[str, Any] = {
        "is_bearish": False,
        "is_corrective": False,
        "swing_lows": [],
        "last_swing_high": None,
        "last_swing_low": None,
    }

    for i in range(40, n):
        if i - last_entry_index < 3:
            continue
        asof = candle_time(candles_1h[i])
        if asof is None:
            continue
        atr_v = atrs[i]
        if atr_v is None or atr_v <= 0:
            continue

        htf_i = htf_for_1h[i]
        if htf_i is None or htf_i >= len(htf_cache):
            continue
        htf = htf_cache[htf_i]
        if not htf.get("htf_bearish_lh_ll"):
            continue

        # Advance confirmed swings (index <= i - swing_right).
        changed = False
        limit = i - swing_right
        while swing_ptr < len(swings_all) and swings_all[swing_ptr].index <= limit:
            confirmed.append(swings_all[swing_ptr])
            swing_ptr += 1
            changed = True
        if changed:
            labeled = label_swings(list(confirmed))
            ltf = _structure_from_swings(labeled)
            for sw in ltf["swing_lows"]:
                zi = int(sw.index)
                if zi not in broken_swing_ids and zi not in watch_lows:
                    watch_lows[zi] = float(sw.price)

        if not (ltf.get("is_bearish") or ltf.get("is_corrective")):
            continue

        width = zone_w_atr * float(atr_v)
        # Check newly closed bar for breaks of watched swing lows (O(1) per watch).
        to_activate: list[int] = []
        for zi, level in list(watch_lows.items()):
            if closes[i] < level:
                # Require a subsequent trade below zone after break.
                if lows[i] < level - width or any(
                    lows[j] < level - width for j in range(max(zi + swing_right + 1, i - 5), i + 1)
                ):
                    to_activate.append(zi)
        for zi in to_activate:
            level = watch_lows.pop(zi)
            broken_swing_ids.add(zi)
            # Find break bar (first close below) — scan only from swing confirm to i once.
            broken_at = i
            for j in range(zi + swing_right + 1, i + 1):
                if closes[j] < level:
                    broken_at = j
                    break
            traded_below = any(lows[j] < level - width for j in range(broken_at, i + 1))
            if not traded_below:
                continue
            active_zones.append(
                {
                    "id": zi,
                    "zone_mid": level,
                    "setup_zone_high": level + width,
                    "setup_zone_low": level - width,
                    "zone_width": 2.0 * width,
                    "swing_low_index": zi,
                    "broken_at_index": broken_at,
                    "invalidation_swing_high": ltf["last_swing_high"],
                }
            )

        active_zones = [z for z in active_zones if i - int(z["broken_at_index"]) <= 120][-8:]

        tol = retest_tol_atr * float(atr_v)
        _, high_i, low_i, close_i = candle_ohlc(candles_1h[i])

        # Update retests / look for rejection on this bar.
        for zone in active_zones:
            zid = int(zone["id"])
            z_hi = float(zone["setup_zone_high"])
            z_lo = float(zone["setup_zone_low"])
            inv = zone.get("invalidation_swing_high")

            if zid not in pending_retests:
                touched = (
                    high_i >= (z_lo - tol)
                    and low_i <= (z_hi + tol)
                    and high_i >= z_lo
                    and i > int(zone["broken_at_index"])
                )
                if not touched:
                    continue
                if inv is not None and high_i > float(inv):
                    continue
                if close_i > z_hi + tol:
                    continue
                ts = candle_time(candles_1h[i])
                pending_retests[zid] = {
                    "retest_index": i,
                    "retest_time": ts.isoformat() if ts else None,
                    "setup_zone_high": z_hi,
                    "setup_zone_low": z_lo,
                    "zone_width": float(zone["zone_width"]),
                    "invalidation_swing_high": inv,
                }
                # Rejection may also occur on the retest bar (engulfing/wick).
                ri = i
            else:
                ri = int(pending_retests[zid]["retest_index"])
                if i - ri > expiry + 2:
                    pending_retests.pop(zid, None)
                    continue

            retest = pending_retests.get(zid)
            if not retest:
                continue

            rejection = detect_rejection(
                candles_1h,
                retest_index=ri,
                zone_high=z_hi,
                zone_low=z_lo,
                from_index=i,
                to_index=i,
                thresholds=th,
            )
            if not rejection or int(rejection["rejection_index"]) != i:
                continue

            entry_price = close_i
            if is_overextended(
                entry_price=entry_price,
                zone_low=z_lo,
                atr=float(atr_v),
                thresholds=th,
            ):
                pending_retests.pop(zid, None)
                continue

            # Nearest support from confirmed swing lows below entry.
            supports = [
                float(s.price)
                for s in ltf["swing_lows"]
                if float(s.price) < float(entry_price)
            ]
            support = max(supports) if supports else None
            if support is None:
                lookback = min(i, int(th["pullback_lookback"]))
                below = [lows[j] for j in range(i - lookback, i + 1) if lows[j] < entry_price]
                support = min(below) if below else None

            if htf.get("htf_near_major_support"):
                if support is None or float(support) >= float(entry_price):
                    continue
                if (entry_price - float(support)) < min_sup_atr * float(atr_v):
                    continue

            try:
                levels = build_stop_tp(
                    entry_price=entry_price,
                    rejection_swing_high=float(rejection["rejection_swing_high"]),
                    atr=float(atr_v),
                    support=support,
                    stop_buffer_atr=float(stop_buffer_atr),
                    tp_mode=tp_mode,
                    tp_r=tp_r,
                )
            except PullbackRejectionSignalError:
                continue

            entry_ts = candle_time(candles_1h[i])
            ext = entry_extension_atr(
                entry_price=entry_price, zone_low=z_lo, atr=float(atr_v)
            )
            signal = strategy_identity(
                symbol=str(symbol).upper(),
                timeframe="1h",
                signal_time=entry_ts.isoformat() if entry_ts else None,
                htf_time=htf.get("htf_time"),
                htf_regime=htf.get("htf_regime"),
                htf_structure=htf.get("htf_structure"),
                setup_zone_high=z_hi,
                setup_zone_low=z_lo,
                zone_width=float(zone["zone_width"]),
                retest_time=retest.get("retest_time"),
                retest_index=ri,
                rejection_time=rejection.get("rejection_time"),
                rejection_index=i,
                rejection_type=rejection.get("rejection_type"),
                rejection_swing_high=float(rejection["rejection_swing_high"]),
                entry_time=entry_ts.isoformat() if entry_ts else None,
                entry_index=i,
                entry_price=float(entry_price),
                entry_delay=i - ri,
                entry_type="MARKET",
                atr_at_entry=float(atr_v),
                entry_extension_atr=ext,
                support_level=support,
                **levels,
            )
            check = validate_signal_geometry(signal)
            if not check["ok"]:
                continue
            signals.append(signal)
            last_entry_index = i
            pending_retests.pop(zid, None)
            # Invalidate this zone after fill.
            active_zones = [z for z in active_zones if int(z["id"]) != zid]
            break

    return signals
