"""Honest regime funnel counters. Analytics only."""

from __future__ import annotations

import pytest

from app.research.market_structure.funnel import (
    assert_funnel_monotonic,
    build_regime_funnel,
    funnel_counters,
    parse_legacy_opportunity_row,
)
from app.research.market_structure.summaries import build_opportunity_summary


def _bar(**kwargs):
    base = {
        "combined_regime": "TREND_UP|HIGH",
        "symbol": "BTCUSDT",
        "fng_bucket": "NEUTRAL",
        "vol_state": "HIGH",
        "signal_attempted": False,
        "signal_rejected": False,
        "signal_accepted": False,
        "order_placed": False,
        "filled": False,
    }
    base.update(kwargs)
    return base


def test_funnel_does_not_treat_idle_bars_as_rejections():
    rows = [_bar() for _ in range(10)]
    rows.append(
        _bar(
            signal_attempted=True,
            signal_rejected=True,
            rejection_reason="HTF_BLOCK",
        )
    )
    counts = funnel_counters(rows)
    assert counts["bars"] == 11
    assert counts["signals_attempted"] == 1
    assert counts["rejected_by_reason"] == {"HTF_BLOCK": 1}
    assert counts["fills"] == 0
    assert_funnel_monotonic(counts)


def test_funnel_monotonic_and_independent_stages():
    rows = [
        _bar(signal_attempted=True, signal_accepted=True, order_placed=True, filled=True, r_multiple=1.2, mfe=2.0, mae=0.4, opportunity_long=True),
        _bar(signal_attempted=True, signal_accepted=True, order_placed=True),
        _bar(signal_attempted=True, signal_rejected=True, rejection_reason="NO_STOP"),
        _bar(),
    ]
    report = build_regime_funnel(rows, low_sample_bars=30, fills_warning_below=100)
    c = report["counters"]
    assert c["bars"] == 4
    assert c["signals_attempted"] == 3
    assert c["signals_accepted"] == 2
    assert c["orders_placed"] == 2
    assert c["fills"] == 1
    assert_funnel_monotonic(c)
    regime = report["by_regime"][0]
    assert regime["capture_rate"] == 1.0
    assert regime["precision"] == 1.0
    assert regime["mean_R"] == pytest.approx(1.2)
    assert regime["mean_MFE"] == pytest.approx(2.0)
    assert regime["low_sample"] is True
    assert report["fills_warning"]
    assert report["totals"]["group"] == "TOTAL"
    assert report["by_coin"][0]["group"] == "BTCUSDT"
    assert report["by_fng_bucket"][0]["group"] == "NEUTRAL"
    assert report["by_volume_state"][0]["group"] == "HIGH"


def test_missing_fng_is_fng_na_not_neutral():
    report = build_regime_funnel([_bar(fng_bucket=None)])
    assert report["by_fng_bucket"][0]["group"] == "FNG_NA"


def test_ambiguous_opportunities_excluded_from_rates():
    rows = [
        _bar(
            opportunity_long=True,
            opportunity_short=True,
            opportunity_ambiguous=True,
            signal_attempted=True,
            signal_accepted=True,
            order_placed=True,
            filled=True,
        ),
        _bar(opportunity_long=True),
    ]
    report = build_regime_funnel(rows, fills_warning_below=0)
    row = report["by_regime"][0]
    assert row["ambiguous_opportunities"] == 1
    assert row["opportunities"] == 1
    assert row["opportunity_fills"] == 0
    assert row["fills"] == 1
    assert row["precision"] == 0.0


def test_legacy_opportunity_row_still_parses_and_old_builder_keeps_shape():
    legacy = {
        "regime": "BULL_TREND",
        "bars_in_regime": 63,
        "accepted_signals": 2,
        "rejected_signals": 61,
        "trades_executed": 2,
        "signal_rate": 2 / 63,
        "trade_rate": 2 / 63,
    }
    parsed = parse_legacy_opportunity_row(legacy)
    assert parsed["trades_executed"] == parsed["accepted_signals"]
    built = build_opportunity_summary(
        [{"regime_1h": "BULL_TREND", "entry_attribution_type": "EXECUTION_BAR"}]
        + [{"regime_1h": "BULL_TREND"}]
    )
    assert set(parsed) <= set(built[0]) | set(parsed)
    assert built[0]["regime"] == "BULL_TREND"
    assert "bars_in_regime" in built[0]
    assert "signal_rate" in built[0]


def test_non_monotonic_counters_fail():
    with pytest.raises(AssertionError):
        assert_funnel_monotonic(
            {
                "bars": 1,
                "signals_attempted": 2,
                "signals_accepted": 0,
                "orders_placed": 0,
                "fills": 0,
            }
        )
