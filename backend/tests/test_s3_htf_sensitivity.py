"""Research-only S3 HTF sensitivity tests.

Does not modify production S3 / HTF / signal engines.
"""

from __future__ import annotations

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    classify_htf_alignment,
)
from app.research.bos_strategy_comparison.htf_variants import (
    HTF_VARIANTS,
    htf_gate_baseline,
    htf_gate_h1_only,
    htf_gate_h4_neutral_1h,
    htf_gate_h4_only,
    htf_gate_no_htf,
)
from app.research.bos_strategy_comparison.metrics import chronological_splits
from app.research.bos_strategy_comparison.s3_htf_sensitivity import (
    SEP2024_EXPECTED,
    reconcile_sep2024,
    run_s3_htf_sensitivity_symbol,
    sample_status_label,
)
from app.research.bos_strategy_comparison.schemas import StrategyTrade
from app.signals.config import SignalConfig

from tests.test_bos_strategy_comparison import make_htf_from_setup, make_uptrend_series


def test_baseline_matches_existing_classify():
    for d, t4, t1 in [
        ("LONG", "BULLISH", "BULLISH"),
        ("LONG", "BULLISH", "NEUTRAL"),
        ("LONG", "BEARISH", "BEARISH"),
        ("SHORT", "BEARISH", "BEARISH"),
        ("SHORT", "BULLISH", "BULLISH"),
        ("SHORT", "NEUTRAL", "BEARISH"),
    ]:
        ok, status = htf_gate_baseline(d, t4, t1)
        bos = "BULLISH_BOS" if d == "LONG" else "BEARISH_BOS"
        expected = classify_htf_alignment(
            bos_direction=bos, trend_4h=t4, trend_1h=t1
        )
        assert ok == (expected == HTF_ALIGNED)
        assert status == expected


def test_variant_eligibility_rules():
    assert htf_gate_h4_only("LONG", "BULLISH", "BEARISH")[0] is True
    assert htf_gate_h4_only("LONG", "NEUTRAL", "BULLISH")[0] is False
    assert htf_gate_h4_neutral_1h("LONG", "BULLISH", "NEUTRAL")[0] is True
    assert htf_gate_h4_neutral_1h("LONG", "BULLISH", "BEARISH")[0] is False
    assert htf_gate_h1_only("SHORT", "BULLISH", "BEARISH")[0] is True
    assert htf_gate_h1_only("SHORT", "BEARISH", "NEUTRAL")[0] is False
    assert htf_gate_no_htf("LONG", "NEUTRAL", "NEUTRAL")[0] is True


def test_long_short_handling_opposite_conflict():
    assert htf_gate_h4_neutral_1h("LONG", "BULLISH", "BEARISH")[1] == (
        "HTF_1H_OPPOSITE_CONFLICT"
    )
    assert htf_gate_baseline("LONG", "BEARISH", "BEARISH")[1] == HTF_CONFLICT


def test_sample_status_labels():
    assert sample_status_label(5) == "VERY_SMALL_SAMPLE"
    assert sample_status_label(20) == "INSUFFICIENT_SAMPLE"
    assert sample_status_label(40) == "OK"


def test_chronological_splits_no_shuffle():
    trades = [
        StrategyTrade(
            strategy_id="S3_BASELINE",
            symbol="BTCUSDT",
            direction="LONG",
            timeframe="15m",
            entry_index=i,
            entry_time=f"2024-01-0{i+1}T00:00:00+00:00" if i < 9 else f"2024-01-{i+1}T00:00:00+00:00",
            entry_price=100.0,
            sl=99.0,
            tp1=102.0,
            tp2=None,
            tp3=None,
            exit_reason="TP1",
            gross_R=1.0,
        )
        for i in range(10)
    ]
    # Fix dates for i>=9
    for i, t in enumerate(trades):
        t.entry_time = f"2024-01-{i+1:02d}T00:00:00+00:00"
    splits = chronological_splits(trades, train_fraction=0.6, validation_fraction=0.2, oos_fraction=0.2)
    assert len(splits["train"]) + len(splits["validation"]) + len(splits["oos"]) == 10
    # Order preserved
    assert splits["train"][0].entry_index < splits["oos"][-1].entry_index


def test_sensitivity_shared_pre_htf_path_and_funnel_subset():
    candles = make_uptrend_series(220)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    report = run_s3_htf_sensitivity_symbol(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=h4,
        candles_1h=h1,
        index_start=50,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=50),
    )
    assert report["pre_htf_path_identical"] is True
    assert report["lookahead"]["pass"] is True
    shared = report["shared_funnel"]
    assert shared["bos_lifecycles"] >= shared["pullback_pass"]
    assert shared["pullback_pass"] >= shared["retest_pass"]
    assert shared["retest_pass"] >= shared["sd_pass"]
    for vid, payload in report["variants"].items():
        f = payload["funnel"]
        assert f["sd_pass"] == shared["sd_pass"]
        assert f["htf_pass"] <= f["sd_pass"]
        assert f["trades"] <= f["htf_pass"]
        assert f["entry_ready"] <= f["htf_pass"]
    # NO_HTF htf_pass should equal sd_pass (HTF counted before open-trade skip)
    no_htf = report["variants"]["S3_NO_HTF"]["funnel"]
    assert no_htf["htf_pass"] == shared["sd_pass"]
    assert no_htf["trades"] <= no_htf["htf_pass"]
    # Baseline HTF pass <= NO_HTF
    assert (
        report["variants"]["S3_BASELINE"]["funnel"]["htf_pass"]
        <= report["variants"]["S3_NO_HTF"]["funnel"]["htf_pass"]
    )


def test_identical_entry_path_uses_external_htf_strategy():
    """All variants use STRATEGY_3 with require_htf_alignment=False after gate."""
    from app.research.bos_strategy_comparison.s3_htf_sensitivity import (
        _s3_without_htf_requirement,
    )

    st = _s3_without_htf_requirement()
    assert st.require_sd is True
    assert st.require_pullback is True
    assert st.require_retest is True
    assert st.require_htf_alignment is False
    assert st.require_entry_ready is True


def test_fees_config_identical_across_variants():
    candles = make_uptrend_series(200)
    report = run_s3_htf_sensitivity_symbol(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=make_htf_from_setup(candles, 16),
        candles_1h=make_htf_from_setup(candles, 4),
        index_start=40,
        research_config=StrategyResearchConfig(
            min_bars=40, taker_fee=0.0004, maker_fee=0.0002, slippage_rate=0.0002
        ),
    )
    fees = {
        vid: p["metrics"]["all"].get("taker_fee")
        for vid, p in report["variants"].items()
    }
    assert len(set(fees.values())) == 1
    assert list(fees.values())[0] == 0.0004


def test_reconcile_sep2024_helper():
    fake = {
        "BTCUSDT": {
            "shared_funnel": {"sd_pass": 8},
            "variants": {"S3_BASELINE": {"funnel": {"sd_pass": 8, "htf_pass": 0}}},
        },
        "ETHUSDT": {
            "shared_funnel": {"sd_pass": 8},
            "variants": {"S3_BASELINE": {"funnel": {"sd_pass": 8, "htf_pass": 0}}},
        },
        "SOLUSDT": {
            "shared_funnel": {"sd_pass": 9},
            "variants": {"S3_BASELINE": {"funnel": {"sd_pass": 9, "htf_pass": 0}}},
        },
    }
    rec = reconcile_sep2024(fake)
    assert rec["match"] is True
    assert SEP2024_EXPECTED["BTCUSDT"]["sd_pass"] == 8


def test_all_variants_registered():
    expected = {
        "S3_BASELINE",
        "S3_H4",
        "S3_H4_NEUTRAL_1H",
        "S3_H1",
        "S3_NO_HTF",
    }
    assert set(HTF_VARIANTS) == expected
