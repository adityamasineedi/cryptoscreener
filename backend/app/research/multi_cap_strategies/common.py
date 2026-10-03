"""Shared helpers for multi-cap research strategies.

No-lookahead: any rolling statistic at index i uses only candles with
index < i (shifted windows), unless the rule explicitly includes the
current closed candle for confirmation (e.g. close break).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from app.engines.mtf.indicators import atr_series
from app.research.multi_cap_strategies.config import MultiCapResearchConfig
from app.research.schemas import ResearchTrade
from app.signals._candle_utils import candle_time


def extract_ohlcv(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(candles)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    v = np.empty(n, dtype=np.float64)
    for i, row in enumerate(candles):
        o[i] = float(row.get("open") or row.get("o") or 0.0)
        h[i] = float(row.get("high") or row.get("h") or 0.0)
        l[i] = float(row.get("low") or row.get("l") or 0.0)
        c[i] = float(row.get("close") or row.get("c") or 0.0)
        v[i] = float(row.get("volume") or row.get("v") or 0.0)
    return o, h, l, c, v


def shifted_rolling_min(values: np.ndarray, window: int) -> np.ndarray:
    """Rolling min over the prior `window` bars; NaN until warmup.

    At index i the window is values[i-window : i] (excludes i).
    """
    n = len(values)
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 1 or n == 0:
        return out
    # Cumulative min via sliding window — exclude current bar
    for i in range(window, n):
        out[i] = float(np.min(values[i - window : i]))
    return out


def shifted_rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    """Rolling max over the prior `window` bars; excludes current bar."""
    n = len(values)
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 1 or n == 0:
        return out
    for i in range(window, n):
        out[i] = float(np.max(values[i - window : i]))
    return out


def shifted_sma(values: np.ndarray, window: int) -> np.ndarray:
    """SMA of prior `window` completed bars (excludes current)."""
    n = len(values)
    out = np.full(n, np.nan, dtype=np.float64)
    if window < 1 or n == 0:
        return out
    csum = np.cumsum(values, dtype=np.float64)
    for i in range(window, n):
        # sum of values[i-window : i]
        total = csum[i - 1] - (csum[i - window - 1] if i - window - 1 >= 0 else 0.0)
        out[i] = float(total / window)
    return out


def compute_atr_array(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    arr = atr_series(list(highs), list(lows), list(closes), period)
    out = np.full(len(arr), np.nan, dtype=np.float64)
    for i, v in enumerate(arr):
        if v is not None:
            out[i] = float(v)
    return out


def bar_time_iso(candles: Sequence[Mapping[str, Any]], index: int) -> str | None:
    if index < 0 or index >= len(candles):
        return None
    ts = candle_time(candles[index])
    return ts.isoformat() if ts else None


def research_stop_and_targets(
    *,
    direction: str,
    entry_price: float,
    atr: float | None,
    structural_invalidation: float | None,
    config: MultiCapResearchConfig,
) -> tuple[float, float | None, float | None, float | None, float | None, dict[str, Any]]:
    """TRADE EVALUATION LOGIC — research SL/TP (not production stop engine).

    LONG stop = min(entry - atr_multiplier*ATR, structural_invalidation)
    when structural is below entry; else ATR stop alone.
    TP1 = entry + min_rr * risk (LONG). TP2/TP3 = 3R / 4R research defaults.
    """
    meta: dict[str, Any] = {
        "trade_evaluation": "ATR_MIN_RR_RESEARCH",
        "atr_multiplier": config.atr_multiplier,
        "min_rr": config.min_rr,
        "atr": atr,
        "structural_invalidation": structural_invalidation,
        "note": (
            "SIGNAL LOGIC does not define exits. "
            "SL/TP supplied by multi-cap research trade evaluation mechanics "
            "via combination_backtest evaluator."
        ),
    }
    direction = direction.upper()
    atr_v = float(atr) if atr is not None and atr > 0 else None
    if atr_v is None and structural_invalidation is None:
        meta["status"] = "MISSING_STOP_INPUTS"
        return entry_price, None, None, None, None, meta

    if direction == "LONG":
        atr_stop = (entry_price - config.atr_multiplier * atr_v) if atr_v else None
        stop = atr_stop
        if structural_invalidation is not None and structural_invalidation < entry_price:
            if stop is None or structural_invalidation < stop:
                stop = float(structural_invalidation)
                meta["stop_source"] = "STRUCTURAL_INVALIDATION"
            else:
                meta["stop_source"] = "ATR"
        else:
            meta["stop_source"] = "ATR" if stop is not None else "NONE"
        if stop is None or stop >= entry_price:
            meta["status"] = "INVALID_STOP"
            return entry_price, None, None, None, None, meta
        risk = entry_price - stop
        tp1 = entry_price + config.min_rr * risk
        tp2 = entry_price + (config.min_rr + 1.0) * risk
        tp3 = entry_price + (config.min_rr + 2.0) * risk
        rr = config.min_rr
        meta["status"] = "OK"
        meta["risk"] = risk
        return stop, tp1, tp2, tp3, rr, meta

    # SHORT (supported for evaluator completeness; strategies are LONG-only)
    atr_stop = (entry_price + config.atr_multiplier * atr_v) if atr_v else None
    stop = atr_stop
    if structural_invalidation is not None and structural_invalidation > entry_price:
        if stop is None or structural_invalidation > stop:
            stop = float(structural_invalidation)
            meta["stop_source"] = "STRUCTURAL_INVALIDATION"
        else:
            meta["stop_source"] = "ATR"
    else:
        meta["stop_source"] = "ATR" if stop is not None else "NONE"
    if stop is None or stop <= entry_price:
        meta["status"] = "INVALID_STOP"
        return entry_price, None, None, None, None, meta
    risk = stop - entry_price
    tp1 = entry_price - config.min_rr * risk
    tp2 = entry_price - (config.min_rr + 1.0) * risk
    tp3 = entry_price - (config.min_rr + 2.0) * risk
    meta["status"] = "OK"
    meta["risk"] = risk
    return stop, tp1, tp2, tp3, config.min_rr, meta


def candidate_to_research_trade(
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    direction: str,
    entry_index: int,
    signal_time: str | None,
    entry_price: float,
    atr: float | None,
    structural_invalidation: float | None,
    asset_group: str,
    condition_snapshot: dict[str, Any],
    config: MultiCapResearchConfig,
    period_label: str = "FULL",
) -> ResearchTrade | None:
    stop, tp1, tp2, tp3, rr, eval_meta = research_stop_and_targets(
        direction=direction,
        entry_price=entry_price,
        atr=atr,
        structural_invalidation=structural_invalidation,
        config=config,
    )
    if eval_meta.get("status") != "OK" or stop is None:
        return None
    snap = {
        **condition_snapshot,
        "signal_logic": strategy_id,
        "trade_evaluation_logic": eval_meta,
        "entry_type": "MARKET",
    }
    return ResearchTrade(
        symbol=symbol.upper(),
        timeframe=timeframe,
        combination_id=strategy_id,
        entry_index=entry_index,
        signal_time=signal_time,
        direction=direction.upper(),
        entry_price=float(entry_price),
        stop_price=float(stop),
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        rr=rr,
        period_label=period_label,
        asset_group=asset_group,
        condition_snapshot=snap,
    )
