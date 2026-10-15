"""Isolated OHLCV BOS/CHoCH timing experiment.

Does not alter live swing, BOS, or CHoCH detection. Research only.
"""

from __future__ import annotations

FEE_RATE_PER_SIDE = 0.0004
SLIPPAGE_RATE = 0.0002
HOLD_BARS = 20
CLOSE_BACK_BARS = 10
RETEST_WINDOW = 3
TARGET_R = 2.0
LEFT_BARS = 3
RIGHT_BARS_BASELINE = 3
RIGHT_BARS_FASTER = 1
DEVELOPMENT_FRACTION = 0.70

VARIANTS = (
    "A_corrected_baseline",
    "B_acceptance",
    "C_retest",
    "D_faster_confirmation",
)

ASSUMPTIONS = {
    "research_only": True,
    "validation_status": "forward_evaluation_still_required",
    "swing_left_bars": LEFT_BARS,
    "baseline_right_bars": RIGHT_BARS_BASELINE,
    "faster_right_bars": RIGHT_BARS_FASTER,
    "entry": "next completed bar open after qualification, with adverse slippage",
    "slippage_rate": SLIPPAGE_RATE,
    "fee_rate_per_side": FEE_RATE_PER_SIDE,
    "fee_note": "4 bps taker each side. 0.04 in the v1 UI is percent, so the fraction is 0.0004.",
    "hold_bars": HOLD_BARS,
    "hold_window": (
        "Inclusive of the entry bar through entry_index+19. "
        "If neither stop nor target has filled, exit at that bar's close with adverse slippage."
    ),
    "close_back_window": (
        "Ten completed bars after the break bar, bars break_index+1 through break_index+10. "
        "This is a level recross, not a trade loss."
    ),
    "post_entry_close_back_window": (
        "Ten completed bars starting at the entry bar. Reported separately from the break-time recross."
    ),
    "same_bar": "When stop and target both trade in one bar and order is unknown, stop is filled first and the bar is marked ambiguous.",
    "gaps": "A bar that opens through the stop exits at that open. A gap through the target fills the target, not the better open.",
    "invalid_entry": "If adverse slippage on the entry open crosses the frozen stop, the setup is skipped and not filled.",
    "stop_policy": "Opposite swing already confirmed at the break bar. Frozen for delayed entries.",
    "target": "2R from the modeled entry and the frozen stop.",
    "sizing": "One unit of initial price risk per fill. No overlapping positions on a symbol. Later signals skip while a position is open.",
    "level_reuse": "The first close through a confirmed swing consumes that pivot. It is not traded again.",
    "retest_equality": (
        "Long: cancel only when close < level; qualify only when low <= level and close > level. "
        "close == level neither cancels nor qualifies. "
        "Short: cancel only when close > level; qualify only when high >= level and close < level."
    ),
    "atr": "Wilder ATR(14) using only candles through the break bar. Null when fewer than 14 true ranges exist.",
    "funding": "Applied when a funding timestamp is after entry and at or before exit. Otherwise results are labeled excluding funding.",
    "windows": (
        "development is the first 70% of bars whose full outcome window ends before the split. "
        "later_slice starts at the split. Events whose outcome crosses the split are excluded from both. "
        "The full sample was already used to observe break failure rates before these entry rules were written, "
        "so neither window is an untouched final test."
    ),
}
