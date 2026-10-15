"""Lock the existing same-bar stop-and-target rule. Fill behavior is unchanged."""

from __future__ import annotations

from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import (
    AMBIGUOUS_CONSERVATIVE,
    AMBIGUOUS_EXCLUDE,
    AMBIGUOUS_NEUTRAL,
)


def test_long_bar_touching_stop_and_target_is_conservative_stop():
    """Default handling: one bar that touches both levels exits at the stop."""
    outcome, exit_price, ambiguous = resolve_intrabar_outcome(
        direction="LONG",
        high=110.0,
        low=100.0,
        stop=100.0,
        targets=[110.0],
        handling=AMBIGUOUS_CONSERVATIVE,
    )
    assert outcome == "SL"
    assert exit_price == 100.0
    assert ambiguous is True


def test_short_bar_touching_stop_and_target_is_conservative_stop():
    outcome, exit_price, ambiguous = resolve_intrabar_outcome(
        direction="SHORT",
        high=110.0,
        low=100.0,
        stop=110.0,
        targets=[100.0],
        handling=AMBIGUOUS_CONSERVATIVE,
    )
    assert outcome == "SL"
    assert exit_price == 110.0
    assert ambiguous is True


def test_neutral_both_touch_marks_ambiguous_intrabar_at_stop():
    outcome, exit_price, ambiguous = resolve_intrabar_outcome(
        direction="LONG",
        high=110.0,
        low=100.0,
        stop=100.0,
        targets=[110.0],
        handling=AMBIGUOUS_NEUTRAL,
    )
    assert outcome == "AMBIGUOUS_INTRABAR"
    assert exit_price == 100.0
    assert ambiguous is True


def test_exclude_both_touch_marks_ambiguous_intrabar_without_price():
    outcome, exit_price, ambiguous = resolve_intrabar_outcome(
        direction="LONG",
        high=110.0,
        low=100.0,
        stop=100.0,
        targets=[110.0],
        handling=AMBIGUOUS_EXCLUDE,
    )
    assert outcome == "AMBIGUOUS_INTRABAR"
    assert exit_price is None
    assert ambiguous is True
