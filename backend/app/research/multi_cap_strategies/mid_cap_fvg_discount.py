"""MID_CAP_FVG_DISCOUNT — Premium/Discount + bullish FVG mitigation entry.

SIGNAL LOGIC (all required):
  MID_CAP, valid unmitigated bullish FVG, price in Discount, current candle
  intersects the FVG, FVG already formed, no lookahead.

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
    shifted_rolling_min,
)
from app.research.multi_cap_strategies.config import (
    FVG_MITIGATION_RULE,
    MultiCapResearchConfig,
)
from app.research.multi_cap_strategies.fvg import (
    FVGState,
    detect_fvg_at,
    unmitigated_zones,
    update_mitigation,
)
from app.research.multi_cap_strategies.schemas import (
    StrategyCandidate,
    StrategyDefinition,
    StrategyEvent,
)

STRATEGY_ID = "MID_CAP_FVG_DISCOUNT"

DEFINITION = StrategyDefinition(
    strategy_id=STRATEGY_ID,
    name="Mid-Cap FVG + Discount",
    description=(
        "MID_CAP only. Rolling premium/discount vs equilibrium (50% of shifted "
        "swing range). Long when an unmitigated bullish FVG is intersected while "
        "price is in discount. Stateful FVG mitigation — no reuse after full fill."
    ),
    asset_group="MID_CAP",
    timeframes=("5m", "15m", "1h"),
    required_features=("ohlcv", "atr", "fvg", "market_cap"),
    direction="LONG",
    metadata={
        "range_window_default": 24,
        "equilibrium": "swing_low + 0.50 * (swing_high - swing_low)",
        "discount": "price < equilibrium",
        "premium": "price > equilibrium",
        "fvg_mitigation_rule": FVG_MITIGATION_RULE,
        "no_lookahead": (
            "swing high/low at i use prior window excluding i; "
            "FVG known only after Candle 3 close."
        ),
        "signal_logic": "Discount + unmitigated bullish FVG intersection",
        "trade_evaluation_logic": "combination_backtest + research ATR/min_rr",
    },
)


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
    _, highs, lows, closes, _ = extract_ohlcv(candles)
    atr = compute_atr_array(highs, lows, closes, cfg.atr_period)
    window = int(cfg.mid_cap_range_window)
    swing_high = shifted_rolling_max(highs, window)
    swing_low = shifted_rolling_min(lows, window)

    start = max(window, index_start or 0)
    end = min(len(candles), index_end if index_end is not None else len(candles))

    state = FVGState()
    candidates: list[StrategyCandidate] = []
    # Avoid multiple entries on the same FVG
    used_fvg_keys: set[tuple[int, float, float]] = set()

    for i in range(start, end):
        sh = swing_high[i]
        sl = swing_low[i]
        atr_i = float(atr[i]) if not np.isnan(atr[i]) and atr[i] > 0 else None
        price = float(closes[i])

        # ENTRY before mitigation update so first-touch bar can still qualify
        # while FVG was unmitigated at the start of the bar.
        if not (np.isnan(sh) or np.isnan(sl) or sh <= sl):
            equilibrium = float(sl + 0.50 * (sh - sl))
            in_discount = price < equilibrium
            if in_discount:
                for z in unmitigated_zones(state, direction="BULLISH", as_of_index=i - 1):
                    if z.created_at >= i:
                        continue
                    key = (z.created_at, z.lower, z.upper)
                    if key in used_fvg_keys:
                        continue
                    if not (float(lows[i]) <= z.upper and float(highs[i]) >= z.lower):
                        continue

                    fvg_age = i - z.created_at
                    discount_distance = equilibrium - price
                    size_atr = (z.size / atr_i) if atr_i else None
                    events = [
                        StrategyEvent(
                            strategy_id=STRATEGY_ID,
                            symbol=symbol.upper(),
                            timeframe=timeframe,
                            event_time=bar_time_iso(candles, z.created_at),
                            event_type="FVG_BULLISH",
                            direction="LONG",
                            price=z.lower,
                            bar_index=z.created_at,
                            reference_level=z.upper,
                            atr=atr_i,
                            payload=z.to_dict(),
                        ),
                        StrategyEvent(
                            strategy_id=STRATEGY_ID,
                            symbol=symbol.upper(),
                            timeframe=timeframe,
                            event_time=bar_time_iso(candles, i),
                            event_type="FVG_DISCOUNT_ENTRY",
                            direction="LONG",
                            price=price,
                            bar_index=i,
                            reference_level=equilibrium,
                            atr=atr_i,
                            payload={
                                "equilibrium": equilibrium,
                                "discount_distance": discount_distance,
                                "fvg_lower": z.lower,
                                "fvg_upper": z.upper,
                                "fvg_age": fvg_age,
                                "fvg_size": z.size,
                                "fvg_size_atr": size_atr,
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
                            entry_price=price,
                            structural_invalidation=float(z.lower),
                            metadata={
                                "equilibrium": equilibrium,
                                "discount_distance": discount_distance,
                                "premium": False,
                                "discount": True,
                                "swing_high": float(sh),
                                "swing_low": float(sl),
                                "fvg_lower": z.lower,
                                "fvg_upper": z.upper,
                                "fvg_age": fvg_age,
                                "fvg_size": z.size,
                                "fvg_size_atr": size_atr,
                                "time_to_mitigation": None,
                                "fvg_mitigation_rule": FVG_MITIGATION_RULE,
                                "atr": atr_i,
                                "range_window": window,
                            },
                            events=events,
                        )
                    )
                    used_fvg_keys.add(key)
                    break

        # Mitigate after entry decision; then form new FVG at this close.
        update_mitigation(
            state,
            index=i,
            low=float(lows[i]),
            high=float(highs[i]),
            close=float(closes[i]),
        )
        zone = detect_fvg_at(highs, lows, i)
        if zone is not None:
            state.zones.append(zone)

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


class MidCapFvgDiscountStrategy:
    definition = DEFINITION
    generate_candidates = staticmethod(generate_candidates)
    evaluate_entry = staticmethod(evaluate_entry)
