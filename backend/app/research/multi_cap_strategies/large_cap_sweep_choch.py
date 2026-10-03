"""LARGE_CAP_SWEEP_CHOCH — Liquidity Sweep + bullish CHOCH (research only).

SIGNAL LOGIC:
  1. Downside liquidity sweep of prior HTF rolling low (shifted, no lookahead)
  2. No entry on sweep candle
  3. After sweep, wait for confirmed local swing high (production swing detector)
  4. CHOCH: subsequent close > confirmed local swing high (no wick-only)

TRADE EVALUATION LOGIC: combination_backtest + research ATR/min_rr SL/TP.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from app.research.multi_cap_strategies.common import (
    bar_time_iso,
    compute_atr_array,
    extract_ohlcv,
    shifted_rolling_min,
)
from app.research.multi_cap_strategies.config import MultiCapResearchConfig
from app.research.multi_cap_strategies.schemas import (
    StrategyCandidate,
    StrategyDefinition,
    StrategyEvent,
)
from app.signals.swing_detector import detect_swings, extend_swings

STRATEGY_ID = "LARGE_CAP_SWEEP_CHOCH"

DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="Large-Cap Sweep + CHOCH",
    description=(
        "LARGE_CAP only. Downside liquidity sweep of prior HTF rolling low, "
        "then bullish CHOCH via close above a chronologically confirmed local "
        "swing high. No entry on sweep candle. No wick-only CHOCH."
    ),
    asset_group="LARGE_CAP",
    timeframes=("5m", "15m", "1h"),
    required_features=("ohlcv", "atr", "swings", "market_cap"),
    direction="LONG",
    metadata={
        "htf_swing_lookback_default": 48,
        "no_lookahead": (
            "prior_htf_low at i = min(low[i-lookback:i]); current bar excluded. "
            "Swings confirmed only with right bars available as_of i."
        ),
        "signal_logic": "SWEEP then CHOCH close-break",
        "trade_evaluation_logic": "combination_backtest + research ATR/min_rr",
    },
)


def _detect_sweeps(
    lows: np.ndarray,
    closes: np.ndarray,
    atr: np.ndarray,
    lookback: int,
) -> list[dict[str, Any]]:
    prior_low = shifted_rolling_min(lows, lookback)
    sweeps: list[dict[str, Any]] = []
    for i in range(lookback, len(lows)):
        level = prior_low[i]
        if np.isnan(level):
            continue
        if lows[i] < level and closes[i] > level:
            depth = float(level - lows[i])
            atr_i = float(atr[i]) if not np.isnan(atr[i]) and atr[i] > 0 else None
            sweeps.append(
                {
                    "index": i,
                    "sweep_level": float(level),
                    "sweep_depth": depth,
                    "sweep_depth_atr": (depth / atr_i) if atr_i else None,
                    "sweep_close_distance": float(closes[i] - level),
                }
            )
    return sweeps


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
    _, highs_arr, lows_arr, closes_arr, _ = extract_ohlcv(candles)
    atr = compute_atr_array(highs_arr, lows_arr, closes_arr, cfg.atr_period)
    lookback = int(cfg.large_cap_htf_swing_lookback)
    sweeps = _detect_sweeps(lows_arr, closes_arr, atr, lookback)
    # extend_swings uses Python truthiness on slices; pass lists (not ndarray).
    highs = [float(x) for x in highs_arr]
    lows = [float(x) for x in lows_arr]
    closes = [float(x) for x in closes_arr]

    start = index_start if index_start is not None else 0
    end = index_end if index_end is not None else len(candles)
    start = max(0, start)
    end = min(len(candles), end)

    # No-lookahead: advance swings chronologically via extend_swings (O(1)/bar),
    # equivalent to detect_swings(..., as_of_index=i) — see test_swing_extend.
    candidates: list[StrategyCandidate] = []
    sweep_ptr = 0
    active: dict[str, Any] | None = None
    active_swing_high: float | None = None
    active_swing_bar: int | None = None
    loop_start = max(lookback, start)
    swing_kwargs = dict(
        left=cfg.swing_left_bars,
        right=cfg.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=cfg.atr_period,
        minimum_swing_distance_atr=cfg.minimum_swing_distance_atr,
        highs=highs,
        lows=lows,
        closes=closes,
    )
    # Bootstrap to loop_start-1 so incremental extend matches full as_of detect.
    if loop_start > 0:
        swings = detect_swings(
            candles, as_of_index=loop_start - 1, **swing_kwargs
        )
    else:
        swings = []

    for i in range(loop_start, end):
        swings = extend_swings(
            swings,
            candles,
            as_of_index=i,
            **swing_kwargs,
        )

        # Activate next sweep when we reach / pass it (sweep itself not an entry)
        while sweep_ptr < len(sweeps) and sweeps[sweep_ptr]["index"] < i:
            # Prefer the most recent sweep still awaiting CHOCH
            if active is None or sweeps[sweep_ptr]["index"] > active["index"]:
                if sweeps[sweep_ptr]["index"] >= start or True:
                    active = sweeps[sweep_ptr]
                    active_swing_high = None
                    active_swing_bar = None
            sweep_ptr += 1
        # Also activate if sweep is exactly at a prior bar we skipped
        while sweep_ptr < len(sweeps) and sweeps[sweep_ptr]["index"] == i:
            # Do not enter on sweep candle; arm for subsequent bars
            active = sweeps[sweep_ptr]
            active_swing_high = None
            active_swing_bar = None
            sweep_ptr += 1
            continue

        if active is None:
            continue
        if i <= int(active["index"]):
            continue

        # Relevant local swing high: confirmed HIGH with bar_index > sweep index
        # and confirmation available as_of i (extend_swings already enforces).
        post = [
            s
            for s in swings
            if s.swing_type == "HIGH" and s.bar_index > int(active["index"])
        ]
        if post:
            sh = post[-1]
            active_swing_high = float(sh.price)
            active_swing_bar = int(sh.bar_index)

        if active_swing_high is None or active_swing_bar is None:
            continue
        # CHOCH requires a subsequent candle after the swing bar itself
        if i <= active_swing_bar:
            continue
        if not (closes[i] > active_swing_high):
            continue

        atr_i = float(atr[i]) if not np.isnan(atr[i]) else None
        choch_dist = float(closes[i] - active_swing_high)
        sweep_to_choch = i - int(active["index"])
        events = [
            StrategyEvent(
                strategy_id=STRATEGY_ID,
                symbol=symbol.upper(),
                timeframe=timeframe,
                event_time=bar_time_iso(candles, int(active["index"])),
                event_type="SWEEP",
                direction="LONG",
                price=float(active["sweep_level"]),
                bar_index=int(active["index"]),
                reference_level=float(active["sweep_level"]),
                atr=atr_i,
                payload=dict(active),
            ),
            StrategyEvent(
                strategy_id=STRATEGY_ID,
                symbol=symbol.upper(),
                timeframe=timeframe,
                event_time=bar_time_iso(candles, i),
                event_type="CHOCH_BULLISH",
                direction="LONG",
                price=float(closes[i]),
                bar_index=i,
                reference_level=float(active_swing_high),
                atr=atr_i,
                payload={
                    "choch_price": float(closes[i]),
                    "choch_distance_atr": (choch_dist / atr_i) if atr_i else None,
                    "sweep_to_choch_bars": sweep_to_choch,
                    "confirmed_local_swing_high": float(active_swing_high),
                    "swing_bar_index": active_swing_bar,
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
                entry_price=float(closes[i]),
                structural_invalidation=float(active["sweep_level"]),
                metadata={
                    "sweep_time": bar_time_iso(candles, int(active["index"])),
                    "sweep_level": float(active["sweep_level"]),
                    "sweep_depth": active["sweep_depth"],
                    "sweep_depth_atr": active["sweep_depth_atr"],
                    "sweep_close_distance": active["sweep_close_distance"],
                    "sweep_to_choch_bars": sweep_to_choch,
                    "choch_price": float(closes[i]),
                    "choch_distance_atr": (choch_dist / atr_i) if atr_i else None,
                    "atr": atr_i,
                    "htf_swing_lookback": lookback,
                },
                events=events,
            )
        )
        # Consume active sweep — one CHOCH per sweep
        active = None
        active_swing_high = None
        active_swing_bar = None

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


class LargeCapSweepChochStrategy:
    definition = DEFINITION
    generate_candidates = staticmethod(generate_candidates)
    evaluate_entry = staticmethod(evaluate_entry)
