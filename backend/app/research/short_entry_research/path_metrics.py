"""Stop/TP path-geometry metrics for SHORT entry research."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.combination_backtest import simulate_research_trade
from app.research.schemas import ResearchTrade
from app.research.short_entry_research.constants import (
    DEFAULT_FILTER_THRESHOLDS,
    STOP_GOOD,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
)
from app.research.short_research_diagnostics.metrics import (
    atr_normalized_distance,
    candle_ohlc,
    compute_mfe_mae_short,
    initial_risk,
)


def classify_stop_distance_atr(
    stop_distance_atr: float | None,
    *,
    thresholds: Mapping[str, float | int] | None = None,
) -> str:
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    if stop_distance_atr is None:
        return STOP_GOOD
    if float(stop_distance_atr) < float(th["stop_tight_atr"]):
        return STOP_TOO_TIGHT
    if float(stop_distance_atr) > float(th["stop_wide_atr"]):
        return STOP_TOO_WIDE
    return STOP_GOOD


def path_flags_before_stop(
    *,
    entry_price: float,
    stop_price: float,
    highs: Sequence[float],
    lows: Sequence[float],
) -> dict[str, Any]:
    """Walk bar path; detect whether stop hit before MFE reached 1R / 1.5R."""
    risk = initial_risk(entry_price, stop_price)
    mfe = 0.0
    hit_stop = False
    mfe_at_stop = None
    reached_1r_before_stop = False
    reached_15r_before_stop = False
    for h, l in zip(highs, lows):
        # favorable first conceptually for SHORT is down; but we track both
        mfe = max(mfe, entry_price - l)
        mfe_r = (mfe / risk) if risk > 0 else 0.0
        if mfe_r >= 1.0:
            reached_1r_before_stop = True
        if mfe_r >= 1.5:
            reached_15r_before_stop = True
        if h >= stop_price:
            hit_stop = True
            mfe_at_stop = mfe_r
            break
    return {
        "did_stop_hit": hit_stop,
        "mfe_r_at_stop": mfe_at_stop,
        "did_stop_hit_before_MFE_1R": bool(hit_stop and not reached_1r_before_stop),
        "did_stop_hit_before_MFE_1_5R": bool(hit_stop and not reached_15r_before_stop),
        "reached_1R_before_stop": reached_1r_before_stop,
        "reached_1_5R_before_stop": reached_15r_before_stop,
    }


def compute_path_geometry(
    *,
    entry_price: float,
    stop_price: float,
    tp1: float | None,
    candles: Sequence[Mapping[str, Any]],
    entry_index: int,
    exit_index: int | None = None,
    atr: float | None = None,
    support_level: float | None = None,
) -> dict[str, Any]:
    end = exit_index if exit_index is not None else min(len(candles) - 1, entry_index + 80)
    highs: list[float] = []
    lows: list[float] = []
    for i in range(entry_index + 1, max(entry_index + 1, end + 1)):
        if i >= len(candles):
            break
        _, h, l, _ = candle_ohlc(candles[i])
        highs.append(h)
        lows.append(l)
    exc = compute_mfe_mae_short(
        entry_price=entry_price, stop_price=stop_price, highs=highs, lows=lows
    )
    flags = path_flags_before_stop(
        entry_price=entry_price, stop_price=stop_price, highs=highs, lows=lows
    )
    risk = initial_risk(entry_price, stop_price)
    planned_rr = None
    if tp1 is not None and risk > 0:
        planned_rr = (entry_price - float(tp1)) / risk
    stop_atr = atr_normalized_distance(entry_price, stop_price, atr)
    tp_atr = (
        atr_normalized_distance(entry_price, float(tp1), atr) if tp1 is not None else None
    )
    support_atr = (
        atr_normalized_distance(entry_price, float(support_level), atr)
        if support_level is not None
        else None
    )
    tp_reached = False
    if tp1 is not None and lows:
        tp_reached = min(lows) <= float(tp1)
    return {
        "stop_distance_atr": stop_atr,
        "tp_distance_atr": tp_atr,
        "support_distance_atr": support_atr,
        "planned_rr": planned_rr,
        "mfe_r": exc["mfe_r"],
        "mae_r": exc["mae_r"],
        "mfe": exc["mfe"],
        "mae": exc["mae"],
        "tp_reached": tp_reached,
        "stop_class": classify_stop_distance_atr(stop_atr),
        **flags,
    }


def resimulate_short_trade(
    *,
    symbol: str,
    candles: Sequence[Mapping[str, Any]],
    entry_index: int,
    entry_price: float,
    stop_price: float,
    tp1: float | None,
    tp2: float | None = None,
    tp3: float | None = None,
    signal_time: str | None = None,
) -> dict[str, Any]:
    trade = ResearchTrade(
        symbol=symbol,
        timeframe="1h",
        combination_id="COMBO_02",
        entry_index=entry_index,
        signal_time=signal_time,
        direction="SHORT",
        entry_price=float(entry_price),
        stop_price=float(stop_price),
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        rr=None,
    )
    sim = simulate_research_trade(trade, candles)
    return sim.to_dict()


def build_stop_price(
    *,
    variant: str,
    entry_price: float,
    current_stop: float,
    atr: float | None,
    bos_candle_high: float | None = None,
    swing_high: float | None = None,
    thresholds: Mapping[str, float | int] | None = None,
) -> float:
    from app.research.short_entry_research.constants import (
        STOP_BOS_HIGH_ATR,
        STOP_CURRENT,
        STOP_FIXED_ATR,
        STOP_SWING_ATR,
    )

    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    buf = float(th["stop_atr_buffer"])
    atr_v = float(atr or 0.0)
    if variant == STOP_CURRENT:
        return float(current_stop)
    if variant == STOP_SWING_ATR:
        base = float(swing_high) if swing_high is not None else float(current_stop)
        return base + atr_v * buf
    if variant == STOP_BOS_HIGH_ATR:
        base = float(bos_candle_high) if bos_candle_high is not None else float(current_stop)
        return base + atr_v * buf
    if variant == STOP_FIXED_ATR:
        return float(entry_price) + atr_v * float(th["fixed_atr_stop_mult"])
    return float(current_stop)


def build_tp_price(
    *,
    variant: str,
    entry_price: float,
    stop_price: float,
    current_tp: float | None,
    support_level: float | None = None,
) -> float | None:
    from app.research.short_entry_research.constants import (
        TP_CURRENT,
        TP_FIXED_15R,
        TP_FIXED_1R,
        TP_FIXED_2R,
        TP_SUPPORT,
    )

    risk = initial_risk(entry_price, stop_price)
    if variant == TP_CURRENT:
        return float(current_tp) if current_tp is not None else None
    if variant == TP_SUPPORT:
        if support_level is not None and float(support_level) < float(entry_price):
            return float(support_level)
        return float(entry_price) - 1.5 * risk if risk > 0 else current_tp
    if variant == TP_FIXED_1R:
        return float(entry_price) - 1.0 * risk if risk > 0 else None
    if variant == TP_FIXED_15R:
        return float(entry_price) - 1.5 * risk if risk > 0 else None
    if variant == TP_FIXED_2R:
        return float(entry_price) - 2.0 * risk if risk > 0 else None
    return current_tp
