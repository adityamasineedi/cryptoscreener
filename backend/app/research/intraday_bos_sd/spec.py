"""Frozen rules for INTRADAY_BOS_SD_V1.

These constants were fixed before any performance measurement of this version.
Do not retune them from backtest results. A change here is a new strategy version
and invalidates prior validation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STRATEGY_ID = "INTRADAY_BOS_SD_V1"
STRATEGY_VERSION = "intraday_bos_sd_v1.0.0"
SPEC_ID = "INTRADAY_BOS_SD_SPEC_V1"
GATE_POLICY_ID = "PROVISIONAL_RESEARCH_GATES_V1"
PAPER_POLICY_ID = "PROVISIONAL_PAPER_OBSERVATION_V1"

# Timeframes. Decisions use only candles whose close time is <= the decision time.
TF_DIRECTION = "1h"
TF_ZONE = "15m"
TF_SETUP = "5m"
TF_EXECUTION = "1m"

SWING_LEFT = 2
SWING_RIGHT = 2
ATR_PERIOD = 14
BREAK_BUFFER_ATR = 0.1
ZONE_BODY_ATR = 1.0
ZONE_LOOKBACK_CANDLES = 5
ZONE_EXPIRY_HOURS = 24
STOP_BUFFER_ATR = 0.2
VOLUME_LOOKBACK = 20
VOLUME_RATIO_MIN = 1.5
TARGET_R = 2.0
MAX_HOLD_HOURS = 4
CONFIRM_BARS_AFTER_TOUCH = 12

# Costs. Entry and exit are taker (next-bar market). Funding is applied only
# from persisted funding_rates; missing funding is not invented.
TAKER_FEE_RATE = 0.0004
BASELINE_SLIPPAGE_RATE = 0.0005
STRESS_SLIPPAGE_MULTIPLIER = 2.0
INITIAL_EQUITY = 10_000.0
# Research sizing assumption when exchange symbol filters are not loaded.
# Live signals must not treat this as account-approved.
ASSUMED_QTY_STEP = 0.001
ASSUMED_MIN_NOTIONAL = 5.0
LEVERAGE_CAP = 1.0  # no unsupported leverage

LARGE_RISK_PCT = 0.0025
MID_RISK_PCT = 0.00125
TOTAL_OPEN_STOP_RISK_PCT = 0.0075
CORRELATED_SAME_DIRECTION_RISK_PCT = 0.0050
CORRELATION_LOOKBACK_DAYS = 30
CORRELATION_THRESHOLD = 0.7
CORRELATION_MIN_OVERLAP_HOURS = 24
DAILY_LOSS_PCT = 0.01

# Universe floors, frozen before evaluation. Quote volume only.
MIN_LISTING_AGE_DAYS = 30
MIN_MEDIAN_DAILY_QUOTE_VOLUME_LARGE = 20_000_000.0
MIN_MEDIAN_DAILY_QUOTE_VOLUME_MID = 5_000_000.0
LIQUIDITY_LOOKBACK_DAYS = 30
DATA_QUALITY_LOOKBACK_DAYS = 7
MAX_GAP_FRACTION = 0.02
UNIVERSE_REBALANCE = "UTC_MONTH_START"
LARGE_RANK_MAX = 20
MID_RANK_MAX = 100
MIN_SYMBOLS_FOR_MARKET_CAP_RANK = 100

STABLE_BASES = frozenset(
    {
        "USDT",
        "USDC",
        "FDUSD",
        "TUSD",
        "DAI",
        "USDP",
        "BUSD",
        "USD1",
        "USDE",
        "EUR",
        "AEUR",
    }
)
WRAPPED_BASES = frozenset(
    {
        "WBTC",
        "WETH",
        "STETH",
        "WSTETH",
        "BETH",
        "CBETH",
        "WEETH",
        "BTCB",
        "RENBTC",
        "HBTC",
    }
)

# Ablation stop when no zone exists (variant A only).
# Stop is beyond the extreme from the broken swing through the break candle.
ABLATION_A_USES_SWING_EXTREME = True

WALK_FORWARD_FOLDS = 3
HOLDOUT_FRACTION = 0.20
HOLDOUT_MIN_DAYS = 60
EMBARGO_HOURS = MAX_HOLD_HOURS

PAPER_MIN_CALENDAR_DAYS = 30
PAPER_MIN_CANDIDATES = 20


def spec_document() -> dict[str, Any]:
    """Canonical specification. Hash covers every gate-relevant choice."""
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "spec_id": SPEC_ID,
        "gate_policy_id": GATE_POLICY_ID,
        "paper_policy_id": PAPER_POLICY_ID,
        "timeframes": {
            "direction": TF_DIRECTION,
            "zones": TF_ZONE,
            "setup": TF_SETUP,
            "execution": TF_EXECUTION,
        },
        "closed_candles_only": True,
        "swings": {
            "left": SWING_LEFT,
            "right": SWING_RIGHT,
            "equal_extreme": "strict_inequality_ties_are_not_swings",
            "occurrence_time": "pivot_candle_open_utc",
            "availability_time": "close_of_the_right_confirming_candle",
        },
        "structure": {
            "bullish": "last_two_confirmed_highs_and_lows_strictly_rising",
            "bearish": "last_two_confirmed_highs_and_lows_strictly_falling",
            "otherwise": "neutral_no_new_entries",
            "initial": "neutral_until_two_highs_and_two_lows_are_available",
            "transition": "relabel_on_the_bar_that_confirms_the_swing_which_changes_the_pair",
        },
        "bos": {
            "rule": "close_beyond_latest_unbroken_confirmed_swing_in_current_direction",
            "buffer": "0.1 * ATR(14) through the previous closed candle",
            "repeat": "a_swing_emits_at_most_one_break",
            "neutral": "no_bos",
        },
        "choch": {
            "rule": "first_close_beyond_latest_unbroken_opposing_swing",
            "buffer": "same_as_bos",
            "repeat": "opposing_swing_emits_at_most_one_choch",
            "neutral": "no_choch",
            "does_not_by_itself_flip_structure_label": True,
        },
        "zones": {
            "source": "15m_bos_whose_body_is_at_least_1x_preceding_atr",
            "demand_origin": "most_recent_bearish_candle_in_the_five_candles_before_bullish_bos",
            "supply_origin": "most_recent_bullish_candle_in_the_five_candles_before_bearish_bos",
            "bearish_candle": "close < open",
            "bullish_candle": "close > open",
            "doji": "close == open does not qualify",
            "range": "full_high_low_of_origin",
            "activation": "after_bos_candle_close_not_on_that_candle",
            "no_origin": "no_zone",
            "retest": "first_only",
            "expiry": "invalid_at_activation_plus_24h_inclusive_boundary",
            "invalidate_demand": "15m_close_below_zone_low",
            "invalidate_supply": "15m_close_above_zone_high",
            "overlap": "newest_activation_time_then_symbol_order",
        },
        "long": {
            "hour_structure": "bullish_on_last_fully_closed_1h_candle",
            "zone": "first_retest_of_active_15m_demand",
            "touch": "5m_range_overlaps_zone_and_5m_close_is_after_zone_activation",
            "entry_structure": "5m_structure_bearish_on_the_touch_candle",
            "choch": "bullish_5m_choch_on_touch_or_later_inside_the_window",
            "swing": "new_high_whose_occurrence_is_after_the_choch_candle",
            "confirmation": "later_than_swing_availability_close_above_that_high_plus_buffer",
            "window": "confirmation_index <= touch_index + 12",
            "volume": "confirmation_volume / mean(prior_20_same_symbol_volumes) >= 1.5",
            "entry": "next_5m_open_with_adverse_slippage",
            "stop": "below_min(zone_low, min_low_touch_through_confirmation) - 0.2 * signal_time_5m_atr",
            "signal_time_atr": "wilder_atr_including_the_confirmation_candle",
            "target": "entry + 2R from the simulated entry",
            "skip_supply": "active_supply_range_intersects_(entry, target]",
            "skip_entry_through_stop": "entry <= stop",
            "max_hold": "four_hours_from_entry_open",
        },
        "short": "exact_inverse",
        "ablations": {
            "A": "1h_direction_plus_5m_bos_stop_at_swing_to_break_extreme",
            "B": "A_plus_first_zone_retest_then_5m_bos_within_12_bars_zone_stop",
            "C": "B_plus_choch_then_post_choch_swing_break",
            "D": "C_plus_relative_volume_filter",
        },
        "event_order_on_a_closed_bar": [
            "confirm_swings",
            "update_structure",
            "invalidate_or_expire_zones",
            "emit_bos_choch_and_create_zones",
            "detect_touches_and_advance_setups",
            "emit_signal_for_next_bar",
        ],
        "execution": {
            "no_fill_on_signal_candle": True,
            "stop_and_target_same_bar": "stop_first",
            "gap_through_stop": "fill_at_open_plus_adverse_slippage",
            "gap_through_target": "fill_at_target_not_beyond_it",
            "entry_bar_stop": "evaluated_after_the_open_fill",
            "lower_timeframe": "walk_1m_when_present_else_setup_timeframe",
            "ohlcv_is_not_order_book_liquidity": True,
            "leverage": "blocked_above_1x_without_mark_and_margin_data",
        },
        "portfolio": {
            "shared_account": True,
            "large_risk_pct": LARGE_RISK_PCT,
            "mid_risk_pct": MID_RISK_PCT,
            "total_open_stop_risk_pct": TOTAL_OPEN_STOP_RISK_PCT,
            "correlated_same_direction_risk_pct": CORRELATED_SAME_DIRECTION_RISK_PCT,
            "correlation": "pearson_of_prior_30d_hourly_returns_threshold_0.7",
            "missing_correlation": "counts_as_correlated",
            "daily_loss_pct": DAILY_LOSS_PCT,
            "daily_loss_action": "flatten_and_block_until_next_utc_day",
            "no_averaging_down": True,
            "simultaneous_priority": ["large_before_mid", "symbol_ascending", "long_before_short"],
            "offsets_do_not_cancel_stop_risk": True,
        },
        "costs": {
            "taker_fee_rate": TAKER_FEE_RATE,
            "baseline_slippage_rate": BASELINE_SLIPPAGE_RATE,
            "stress_slippage_multiplier": STRESS_SLIPPAGE_MULTIPLIER,
            "initial_equity": INITIAL_EQUITY,
            "assumed_qty_step": ASSUMED_QTY_STEP,
            "assumed_min_notional": ASSUMED_MIN_NOTIONAL,
            "assumed_filters_label": "ASSUMED_FILTERS_NOT_EXCHANGE_INFO",
        },
        "universe": {
            "contract": "usdt_perpetual_on_intended_exchange",
            "historical_cap": "ranks_1_20_large_21_100_mid_when_at_least_100_asof_caps_exist",
            "fallback": "LIQUIDITY_TIER_NOT_MARKET_CAP",
            "exclude_bases": sorted(STABLE_BASES | WRAPPED_BASES),
            "min_listing_age_days": MIN_LISTING_AGE_DAYS,
            "liquidity_lookback_days": LIQUIDITY_LOOKBACK_DAYS,
            "volume_unit": "exchange_quote_volume_only_days_with_any_null_quote_are_dropped",
            "include_delisted_if_present": True,
        },
        "validation": {
            "folds": WALK_FORWARD_FOLDS,
            "holdout_fraction": HOLDOUT_FRACTION,
            "holdout_min_days": HOLDOUT_MIN_DAYS,
            "embargo_hours": EMBARGO_HOURS,
            "min_oos_portfolio_trades": 200,
            "min_oos_trades_per_enabled_tier": 50,
            "min_profit_factor": 1.20,
            "max_drawdown": 0.10,
            "min_positive_fold_fraction": 2 / 3,
            "stress_slippage_expectancy_positive": True,
            "exclude_best_symbol_pnl_positive": True,
        },
        "paper": {
            "authorizes": "monitoring_only_after_historical_validation",
            "min_calendar_days": PAPER_MIN_CALENDAR_DAYS,
            "min_candidates": PAPER_MIN_CANDIDATES,
            "signal_enabled_requires_operator_and_paper_policy": True,
            "forward_validation_not_claimed_by_implementation": True,
        },
        "passed_means_gates_met_not_guaranteed_profit": True,
    }


def spec_hash() -> str:
    payload = json.dumps(spec_document(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
