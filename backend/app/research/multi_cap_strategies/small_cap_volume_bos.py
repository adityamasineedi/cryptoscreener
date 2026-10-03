"""SMALL_CAP_VOLUME_BOS — Volume velocity + BOS with deep-retrace invalidation.

SIGNAL LOGIC:
  close > prior 10-period rolling max high (current excluded)
  AND volume > 3.0 * volume_sma_50 (prior bars only)
  Then monitor deep-retrace window; invalidate or confirm entry.

TRADE EVALUATION LOGIC: combination_backtest + research ATR/min_rr SL/TP.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from app.research.multi_cap_strategies.common import (
    bar_time_iso,
    compute_atr_array,
    extract_ohlcv,
    shifted_rolling_max,
    shifted_sma,
)
from app.research.multi_cap_strategies.config import (
    DEEP_RETRACE_RULE,
    MultiCapResearchConfig,
)
from app.research.multi_cap_strategies.schemas import (
    StrategyCandidate,
    StrategyDefinition,
    StrategyEvent,
)

STRATEGY_ID = "SMALL_CAP_VOLUME_BOS"

DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="Small-Cap Volume BOS",
    description=(
        "SMALL_CAP only. Close breaks prior 10-bar rolling max high with "
        "volume > 3× SMA50. Deep retrace after breakout invalidates before entry "
        f"(threshold configurable; default documented in DEEP_RETRACE_RULE)."
    ),
    asset_group="SMALL_CAP",
    timeframes=("5m", "15m", "1h"),
    required_features=("ohlcv", "volume", "atr", "market_cap"),
    direction="LONG",
    metadata={
        "volume_sma": 50,
        "bos_lookback": 10,
        "volume_ratio": 3.0,
        "deep_retrace_rule": DEEP_RETRACE_RULE,
        "bos_definition": (
            "close[i] > max(high[i-10:i]) — current candle excluded from max."
        ),
        "signal_logic": "Volume-velocity BOS + deep-retrace filter",
        "trade_evaluation_logic": "combination_backtest + research ATR/min_rr",
    },
)


def _pending_signal(
    *,
    bos_i: int,
    bos_level: float,
    impulse_high: float,
    volume: float,
    volume_sma: float,
    volume_ratio: float,
    atr_i: float | None,
    closes_i: float,
) -> dict[str, Any]:
    return {
        "bos_i": bos_i,
        "bos_level": bos_level,
        "impulse_high": impulse_high,
        "volume": volume,
        "volume_sma": volume_sma,
        "volume_ratio": volume_ratio,
        "atr": atr_i,
        "bos_close": closes_i,
        "bos_distance": closes_i - bos_level,
        "bos_distance_atr": (
            (closes_i - bos_level) / atr_i if atr_i and atr_i > 0 else None
        ),
    }


def generate_candidates(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    config: MultiCapResearchConfig | None = None,
    index_start: int | None = None,
    index_end: int | None = None,
) -> list[StrategyCandidate]:
    cfg = config or MultiCapResearchConfig()
    if len(candles) < cfg.min_bars:
        return []
    _, highs, lows, closes, volumes = extract_ohlcv(candles)
    atr = compute_atr_array(highs, lows, closes, cfg.atr_period)
    bos_lb = int(cfg.small_cap_bos_lookback)
    vol_lb = int(cfg.small_cap_volume_sma)
    vol_ratio_min = float(cfg.small_cap_volume_ratio)
    retrace_thr = float(cfg.small_cap_deep_retrace_threshold)
    retrace_win = int(cfg.small_cap_deep_retrace_window)

    prior_max = shifted_rolling_max(highs, bos_lb)
    vol_sma = shifted_sma(volumes, vol_lb)

    start = max(vol_lb, bos_lb, index_start or 0)
    end = min(len(candles), index_end if index_end is not None else len(candles))

    candidates: list[StrategyCandidate] = []
    pending: dict[str, Any] | None = None
    signals_raw = 0

    for i in range(start, end):
        # Manage pending deep-retrace confirmation
        if pending is not None:
            bos_i = int(pending["bos_i"])
            elapsed = i - bos_i
            if elapsed >= 1:
                impulse_high = float(pending["impulse_high"])
                bos_level = float(pending["bos_level"])
                denom = impulse_high - bos_level
                if denom > 0:
                    retrace_depth = (impulse_high - float(lows[i])) / denom
                    if retrace_depth >= retrace_thr:
                        pending = None
                        continue
            if elapsed >= retrace_win:
                # Confirmed — enter at close of confirmation bar
                atr_i = pending.get("atr")
                entry_px = float(closes[i])
                events = [
                    StrategyEvent(
                        strategy_id=STRATEGY_ID,
                        symbol=symbol.upper(),
                        timeframe=timeframe,
                        event_time=bar_time_iso(candles, bos_i),
                        event_type="VOLUME_BOS",
                        direction="LONG",
                        price=float(pending["bos_close"]),
                        bar_index=bos_i,
                        reference_level=float(pending["bos_level"]),
                        atr=atr_i,
                        volume_ratio=float(pending["volume_ratio"]),
                        payload={
                            "volume": pending["volume"],
                            "volume_sma": pending["volume_sma"],
                            "volume_ratio": pending["volume_ratio"],
                            "bos_level": pending["bos_level"],
                            "bos_distance": pending["bos_distance"],
                            "bos_distance_atr": pending["bos_distance_atr"],
                        },
                    ),
                    StrategyEvent(
                        strategy_id=STRATEGY_ID,
                        symbol=symbol.upper(),
                        timeframe=timeframe,
                        event_time=bar_time_iso(candles, i),
                        event_type="DEEP_RETRACE_CLEARED",
                        direction="LONG",
                        price=entry_px,
                        bar_index=i,
                        reference_level=float(pending["bos_level"]),
                        atr=atr_i,
                        payload={
                            "deep_retrace_threshold": retrace_thr,
                            "deep_retrace_window": retrace_win,
                            "deep_retrace_rule": DEEP_RETRACE_RULE,
                        },
                    ),
                ]
                candidates.append(
                    StrategyCandidate(
                        strategy_id=STRATEGY_ID,
                        symbol=symbol.upper(),
                        timeframe=timeframe,
                        direction="LONG",
                        entry_index=i,
                        signal_time=bar_time_iso(candles, i),
                        entry_price=entry_px,
                        structural_invalidation=float(pending["bos_level"]),
                        metadata={
                            "volume": pending["volume"],
                            "volume_sma": pending["volume_sma"],
                            "volume_ratio": pending["volume_ratio"],
                            "bos_level": pending["bos_level"],
                            "bos_distance": pending["bos_distance"],
                            "bos_distance_atr": pending["bos_distance_atr"],
                            "bos_index": bos_i,
                            "deep_retrace_threshold": retrace_thr,
                            "deep_retrace_window": retrace_win,
                            "deep_retrace_rule": DEEP_RETRACE_RULE,
                            "atr": atr_i,
                            "raw_signal_count_note": signals_raw,
                        },
                        events=events,
                    )
                )
                pending = None
            continue

        # New BOS + volume signal
        level = prior_max[i]
        sma = vol_sma[i]
        if np.isnan(level) or np.isnan(sma) or sma <= 0:
            continue
        if not (closes[i] > level):
            continue
        ratio = float(volumes[i] / sma)
        if not (ratio > vol_ratio_min):
            continue

        atr_i = float(atr[i]) if not np.isnan(atr[i]) and atr[i] > 0 else None
        signals_raw += 1
        pending = _pending_signal(
            bos_i=i,
            bos_level=float(level),
            impulse_high=float(highs[i]),
            volume=float(volumes[i]),
            volume_sma=float(sma),
            volume_ratio=ratio,
            atr_i=atr_i,
            closes_i=float(closes[i]),
        )

    return candidates


def evaluate_entry(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    config: MultiCapResearchConfig | None = None,
) -> StrategyCandidate | None:
    cands = generate_candidates(
        symbol,
        timeframe,
        candles,
        config=config,
        index_start=0,
        index_end=as_of_index + 1,
    )
    for c in reversed(cands):
        if c.entry_index == as_of_index:
            return c
    return None


class SmallCapVolumeBosStrategy:
    definition = DEFINITION
    generate_candidates = staticmethod(generate_candidates)
    evaluate_entry = staticmethod(evaluate_entry)
