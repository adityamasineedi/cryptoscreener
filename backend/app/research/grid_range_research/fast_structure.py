"""Research-only causal regime/bounds precompute (fast path).

Same ``classify_market_regime`` + confirmed swing delay as market_structure.
Structure/BOS recomputed only when a new swing confirms (indicators every bar).
Does not modify market_structure or COMBO_02_V2.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr_series
from app.research.market_structure.config import MarketStructureFeatureConfig
from app.research.market_structure.indicators_ext import (
    adx_di_series,
    atr_percentile_series,
    atr_relative_series,
    choppiness_index_series,
    directional_alternation_count,
    efficiency_ratio_series,
    ema_series,
    roc_series,
    rolling_high_low,
    rolling_slope,
    rsi_series,
)
from app.research.market_structure.regime import classify_market_regime
from app.signals._candle_utils import series_ohlcv
from app.signals.schemas import SwingRecord
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import infer_trend

# Research speed: BOS detection is O(expensive) per call; grid gates primarily need
# CHOPPY/RANGE/HVR from ADX/chop/efficiency/structure votes. BOS/CHoCH votes are
# left neutral so we do not modify the classifier itself.
_BOS_NEUTRAL = "NO_CONFIRMED_BOS"
_CHOCH_NEUTRAL = "NO_CONFIRMED_CHOCH"


def _label_one(
    swing: SwingRecord,
    *,
    last_high: float | None,
    last_low: float | None,
) -> tuple[SwingRecord, float | None, float | None]:
    """Incremental swing labeling (same rules as ``_label_swings``)."""
    s = SwingRecord(
        symbol=swing.symbol,
        timeframe=swing.timeframe,
        swing_type=swing.swing_type,
        price=swing.price,
        timestamp=swing.timestamp,
        bar_index=swing.bar_index,
        strength=swing.strength,
        confirmed_at=swing.confirmed_at,
        label=None,
    )
    if s.swing_type == "HIGH":
        s.label = "HH" if last_high is None or s.price > last_high else "LH"
        last_high = s.price
    else:
        s.label = "HL" if last_low is None or s.price > last_low else "LL"
        last_low = s.price
    return s, last_high, last_low


def precompute_regimes_and_bounds_fast(
    candles_1h: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    index_start: int = 0,
    config: MarketStructureFeatureConfig | None = None,
) -> tuple[list[str], list[tuple[float, float] | None]]:
    cfg = config or MarketStructureFeatureConfig()
    series = list(candles_1h)
    n = len(series)
    regimes = ["UNKNOWN"] * n
    bounds: list[tuple[float, float] | None] = [None] * n
    if n == 0:
        return regimes, bounds

    _opens, highs, lows, closes, _vols = series_ohlcv(series)

    ema20 = ema_series(closes, cfg.ema_fast)
    ema50 = ema_series(closes, cfg.ema_mid)
    ema200 = ema_series(closes, cfg.ema_slow)
    atr = atr_series(highs, lows, closes, cfg.atr_period)
    atr_pct = atr_percentile_series(atr, cfg.atr_percentile_lookback)
    atr_rel = atr_relative_series(atr, cfg.atr_percentile_lookback)
    adx, di_p, di_m = adx_di_series(highs, lows, closes, cfg.adx_period)
    efficiency = efficiency_ratio_series(closes, cfg.efficiency_lookback)
    choppiness = choppiness_index_series(highs, lows, closes, cfg.choppiness_lookback)
    slope = rolling_slope(closes, cfg.slope_lookback)
    roc = roc_series(closes, cfg.momentum_lookback)
    rsi = rsi_series(closes, cfg.rsi_period)
    alternations = directional_alternation_count(closes, cfg.direction_change_lookback)
    roll_high, roll_low = rolling_high_low(highs, lows, cfg.range_lookback)

    all_swings = detect_swings(
        series,
        left=cfg.swing_left,
        right=cfg.swing_right,
        symbol=symbol,
        timeframe="1h",
        atr_period=cfg.atr_period,
        minimum_swing_distance_atr=0.0,
        as_of_index=n - 1,
        highs=highs,
        lows=lows,
        closes=closes,
    )

    right = cfg.swing_right
    swing_ptr = 0
    labeled: list[SwingRecord] = []
    last_high_lbl: float | None = None
    last_low_lbl: float | None = None
    prev_trend = "NEUTRAL"
    bos_direction: list[str | None] = [None] * n
    min_i = max(cfg.swing_left + cfg.swing_right, cfg.min_bars_basic - 1)
    start = max(int(index_start), 0)

    # Cached structure fields (refresh on new swing confirm)
    trend_state = "UNKNOWN_INSUFFICIENT_HISTORY"
    structure_state = "UNKNOWN"
    direction = "UNKNOWN"
    bos_state = "NO_CONFIRMED_BOS"
    choch_state = "NO_CONFIRMED_CHOCH"
    sh: float | None = None
    sl: float | None = None

    for i in range(n):
        added = False
        while swing_ptr < len(all_swings):
            s = all_swings[swing_ptr]
            if int(s.bar_index) + right > i:
                break
            lab, last_high_lbl, last_low_lbl = _label_one(
                s, last_high=last_high_lbl, last_low=last_low_lbl
            )
            labeled.append(lab)
            swing_ptr += 1
            added = True

        if added and i >= min_i:
            # Bound structure/BOS work to recent swings (causal subset).
            swings = labeled[-80:] if len(labeled) > 80 else labeled
            trend = infer_trend(swings)
            tlabel = str(trend.get("trend") or "NEUTRAL").upper()
            if tlabel == "INSUFFICIENT_DATA":
                trend_state = "UNKNOWN_INSUFFICIENT_HISTORY"
                structure_state = "UNKNOWN"
                direction = "UNKNOWN"
            elif tlabel == "BULLISH":
                trend_state = "BULLISH"
                structure_state = "UPTREND_HH_HL"
                direction = "BULLISH"
            elif tlabel == "BEARISH":
                trend_state = "BEARISH"
                structure_state = "DOWNTREND_LH_LL"
                direction = "BEARISH"
            else:
                if prev_trend in {"BULLISH", "BEARISH"}:
                    trend_state = "TRANSITION"
                    structure_state = "TRANSITION_STRUCTURE"
                else:
                    trend_state = "NEUTRAL"
                    structure_state = "RANGE_STRUCTURE"
                direction = "NEUTRAL"
            if tlabel in {"BULLISH", "BEARISH", "NEUTRAL"}:
                prev_trend = tlabel

            # Range bounds use latest confirmed swing high/low (full causal set).
            sh = next(
                (s.price for s in reversed(labeled) if s.swing_type == "HIGH"),
                None,
            )
            sl = next(
                (s.price for s in reversed(labeled) if s.swing_type == "LOW"),
                None,
            )

            seq = list(trend.get("structure_sequence") or [])
            if len(seq) >= 4:
                labels = {str(x) for x in seq[-4:]}
                if {"HH", "HL", "LH", "LL"}.issubset(labels) or (
                    "HH" in labels and "LL" in labels
                ):
                    structure_state = "MIXED_STRUCTURE"

            bos_state = _BOS_NEUTRAL
            bos_direction[i] = None
            choch_state = _CHOCH_NEUTRAL
        else:
            bos_direction[i] = None

        if i < min_i or i < start:
            continue

        atr_i = atr[i]
        close_i = closes[i]
        if atr_i and atr_i > 0 and sh is not None and sl is not None:
            if direction == "BULLISH":
                depth = (sh - close_i) / atr_i
                if depth < 0:
                    pullback_state = "NO_PULLBACK"
                elif depth >= cfg.pullback_atr_deep:
                    pullback_state = "DEEP_PULLBACK"
                elif depth >= cfg.pullback_atr_shallow:
                    pullback_state = "BULLISH_PULLBACK"
                else:
                    pullback_state = "NO_PULLBACK"
            elif direction == "BEARISH":
                depth = (close_i - sl) / atr_i
                if depth < 0:
                    pullback_state = "NO_PULLBACK"
                elif depth >= cfg.pullback_atr_deep:
                    pullback_state = "DEEP_PULLBACK"
                elif depth >= cfg.pullback_atr_shallow:
                    pullback_state = "BEARISH_PULLBACK"
                else:
                    pullback_state = "NO_PULLBACK"
            else:
                pullback_state = "NO_PULLBACK"
        else:
            pullback_state = "PULLBACK_UNKNOWN"

        rh, rl = roll_high[i], roll_low[i]
        if rh is not None and rl is not None and atr_i and atr_i > 0:
            width = (rh - rl) / atr_i
            if rl <= close_i <= rh:
                if width <= 2.0:
                    range_state = "RANGE_COMPRESSION"
                elif width >= 6.0:
                    range_state = "RANGE_EXPANSION"
                else:
                    range_state = "INSIDE_RANGE"
            else:
                range_state = "RANGE_EXPANSION"
        else:
            range_state = "UNKNOWN"

        ap = atr_pct[i]
        if ap is None:
            volatility_state = (
                "INSUFFICIENT_HISTORY" if i < cfg.atr_percentile_lookback else "UNKNOWN"
            )
        elif ap >= cfg.atr_high_percentile:
            volatility_state = "HIGH"
        elif ap <= cfg.atr_low_percentile:
            volatility_state = "LOW"
        else:
            volatility_state = "NORMAL"

        r = roc[i]
        s = slope[i]
        if r is None:
            momentum_state = "UNKNOWN"
        elif r > 0 and (s is None or s >= 0):
            momentum_state = "BULLISH"
        elif r < 0 and (s is None or s <= 0):
            momentum_state = "BEARISH"
        else:
            momentum_state = "NEUTRAL"
        if i >= cfg.momentum_lookback and roc[i] is not None and roc[i - 1] is not None:
            if abs(roc[i]) > abs(roc[i - 1]) * 1.15:
                momentum_state = "ACCELERATING"
            elif abs(roc[i]) < abs(roc[i - 1]) * 0.85:
                momentum_state = "DECELERATING"

        e20, e50, e200 = ema20[i], ema50[i], ema200[i]
        if e20 is not None and e50 is not None:
            if e20 > e50 and (e200 is None or e50 > e200):
                ema_alignment = "BULLISH"
            elif e20 < e50 and (e200 is None or e50 < e200):
                ema_alignment = "BEARISH"
            else:
                ema_alignment = "MIXED"
        else:
            ema_alignment = "UNKNOWN"

        data_q = "UNKNOWN_INSUFFICIENT_HISTORY" if i < cfg.min_bars_basic else "OK"
        snap = {
            "data_quality_state": data_q,
            "trend_state": trend_state,
            "structure_state": structure_state,
            "direction": direction,
            "bos_state": bos_state,
            "choch_state": choch_state,
            "volatility_state": volatility_state,
            "momentum_state": momentum_state,
            "adx": adx[i],
            "efficiency_ratio": efficiency[i],
            "choppiness_index": choppiness[i],
            "atr_percentile": atr_pct[i],
            "ema_alignment": ema_alignment,
            "range_state": range_state,
            "direction_changes": alternations[i],
            "pullback_state": pullback_state,
            "di_plus": di_p[i],
            "di_minus": di_m[i],
            "atr": atr[i],
            "atr_relative": atr_rel[i],
            "rsi": rsi[i],
            "roc": roc[i],
        }
        regimes[i] = classify_market_regime(snap, cfg).market_regime
        if sh is not None and sl is not None and float(sh) > float(sl):
            bounds[i] = (float(sl), float(sh))
        else:
            bounds[i] = None

    return regimes, bounds
