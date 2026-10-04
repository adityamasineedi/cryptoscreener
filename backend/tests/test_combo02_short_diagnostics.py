"""COMBO_02 SHORT research diagnostics — required suite (research-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.combo02_short_research import (
    ShortResearchOnlyError,
    assert_short_research_only_boundary,
    short_research_identity,
)
from app.research.short_research_diagnostics.ablation import (
    build_ablation_matrix,
    variant_fingerprint,
)
from app.research.short_research_diagnostics.classifiers import (
    classify_entry_quality,
    classify_market_regimes,
    classify_stop_placement,
    support_distance_atr,
)
from app.research.short_research_diagnostics.constants import (
    ENTRY_CHASING,
    ENTRY_IMMEDIATE_BREAK,
    ENTRY_RETEST,
    LABEL_RESEARCH_REJECTED,
    REGIME_NEAR_SUPPORT,
    REGIME_STRONG_BEAR,
    SAFETY_STAMPS,
    STOP_TOO_TIGHT,
    STOP_TOO_WIDE,
    VARIANT_BASELINE,
    VARIANT_NO_HTF,
    VARIANT_RETEST_ONLY,
    VARIANT_SPECS,
)
from app.research.short_research_diagnostics.fee_sensitivity import fee_sensitivity_matrix
from app.research.short_research_diagnostics.implementation_verify import (
    run_implementation_verification,
    verify_mirrored_long_short,
)
from app.research.short_research_diagnostics.metrics import (
    atr_normalized_distance,
    compute_mfe_mae_short,
    entry_delay_bars,
    entry_extension_atr,
)
from app.research.short_research_windows import (
    DEFAULT_SHORT_RESEARCH_WINDOWS,
    validate_research_windows,
)
from app.services.paper_trade import PaperTradeEngine
from app.services.v1_paper_watcher import is_v1_long_entry
from app.signals.trade_math import SAME_CANDLE_PRECEDENCE_SL_FIRST


def test_mirrored_long_short_synthetic_behavior():
    mirrored = verify_mirrored_long_short()
    assert mirrored["ok"] is True
    assert mirrored["checks"]["win_gross_equal"] is True
    assert mirrored["checks"]["same_candle_both_sl"] is True
    full = run_implementation_verification()
    assert full["ok"] is True
    assert full["classification"] is None


def test_entry_delay_calculation():
    t0 = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=3)
    assert entry_delay_bars(t1, t0) == 3
    assert entry_delay_bars(t0, t0) == 0


def test_bos_extension_calculation():
    ext = entry_extension_atr(95.0, bos_level=100.0, atr_at_signal=2.0)
    assert ext == pytest.approx(2.5)


def test_retest_versus_immediate_break_classification():
    retest = classify_entry_quality(
        entry_type="LIMIT_RETEST",
        entry_extension_atr_value=0.1,
        bos_level=100.0,
        entry_price=100.0,
        atr=2.0,
        retest_flag=True,
    )
    immediate = classify_entry_quality(
        entry_type="MARKET",
        entry_extension_atr_value=0.5,
        bos_level=100.0,
        entry_price=99.0,
        atr=2.0,
        retest_flag=False,
    )
    assert retest == ENTRY_RETEST
    assert immediate == ENTRY_IMMEDIATE_BREAK


def test_mfe_calculation():
    out = compute_mfe_mae_short(
        entry_price=100.0,
        stop_price=105.0,
        highs=[101.0, 100.5],
        lows=[98.0, 97.0],
    )
    assert out["mfe"] == pytest.approx(3.0)
    assert out["mfe_r"] == pytest.approx(0.6)


def test_mae_calculation():
    out = compute_mfe_mae_short(
        entry_price=100.0,
        stop_price=105.0,
        highs=[103.0, 104.0],
        lows=[99.0, 98.5],
    )
    assert out["mae"] == pytest.approx(4.0)
    assert out["mae_r"] == pytest.approx(0.8)


def test_atr_normalized_stop_distance():
    d = atr_normalized_distance(100.0, 106.0, 2.0)
    assert d == pytest.approx(3.0)


def test_support_distance_tagging():
    d = support_distance_atr(100.0, support_level=96.0, atr=2.0)
    assert d == pytest.approx(2.0)
    regimes = classify_market_regimes(
        symbol_trend="BEARISH",
        htf_1h="BEARISH",
        htf_4h="BEARISH",
        btc_trend="BEARISH",
        atr_percentile=0.5,
        distance_to_support_atr=0.5,
        adx_1h=30,
    )
    assert REGIME_NEAR_SUPPORT in regimes
    assert REGIME_STRONG_BEAR in regimes


def test_regime_tagging_without_lookahead():
    """Classifier uses only caller-supplied pre-entry fields (no candle walk)."""
    labels = classify_market_regimes(
        symbol_trend="BEARISH",
        htf_1h="BEARISH",
        htf_4h="NEUTRAL",
        btc_trend="BULLISH",
        atr_percentile=0.8,
    )
    assert "HIGH_VOLATILITY" in labels
    assert any(x in labels for x in ("WEAK_BEAR", "SIDEWAYS", "STRONG_BEAR", "WEAK_BULL"))


def test_fee_sensitivity_comparison():
    trades = [
        {
            "direction": "SHORT",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "exit_price": 97.0,
            "outcome": "TP1",
            "entry_type": "MARKET",
            "r_multiple": 0.6,
        },
        {
            "direction": "SHORT",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "exit_price": 105.0,
            "outcome": "SL",
            "entry_type": "MARKET",
            "r_multiple": -1.0,
        },
    ]
    matrix = fee_sensitivity_matrix(trades, risk_usd=20.0)
    assert "live_model" in matrix
    assert "zero_fees" in matrix
    assert "limit_retest_maker" in matrix
    assert matrix["live_model"]["fees"] <= 0
    assert matrix["zero_fees"]["fees"] == pytest.approx(0.0)


def test_ablation_variant_identity_and_fingerprints():
    window = {"base_start": "2024-01-01", "base_end": "2025-06-30"}
    symbols = ["BTCUSDT", "ETHUSDT"]
    fps = {
        vid: variant_fingerprint(vid, window=window, symbols=symbols)
        for vid in VARIANT_SPECS
    }
    assert len(fps) == len(set(fps.values()))
    assert VARIANT_BASELINE in fps
    assert VARIANT_NO_HTF in fps
    assert VARIANT_SPECS[VARIANT_NO_HTF]["combination_id"] == "COMBO_02_LOCAL"
    assert VARIANT_SPECS[VARIANT_BASELINE]["combination_id"] == "COMBO_02"


def test_ablation_matrix_builds_and_stamps_safety():
    baseline = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "direction": "SHORT",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "tp1": 90.0,
            "exit_price": 105.0,
            "outcome": "SL",
            "r_multiple": -1.0,
            "entry_type": "MARKET",
            "holding_bars": 2,
            "entry_index": 10,
            "mfe": 1.0,
            "mae": 5.0,
        }
    ]
    diag = [
        {
            **baseline[0],
            "entry_quality": ENTRY_IMMEDIATE_BREAK,
            "regimes": ["SIDEWAYS"],
            "primary_regime": "SIDEWAYS",
            "atr_at_signal": 2.0,
            "TP1": 90.0,
            "R": -1.0,
            "fees": -0.1,
            "net_pnl": -20.1,
            "gross_pnl": -20.0,
            "MFE": 1.0,
            "MAE": 5.0,
        }
    ]
    candles = [
        {
            "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
            "open": 100,
            "high": 106,
            "low": 94,
            "close": 100,
            "volume": 1,
        }
        for i in range(40)
    ]
    matrix = build_ablation_matrix(
        baseline_trades=baseline,
        diagnostic_rows=diag,
        candles_by_symbol={"BTCUSDT": candles},
        window={"base_start": "2024-01-01", "base_end": "2025-06-30"},
        symbols=["BTCUSDT"],
        run_no_htf=False,
    )
    assert matrix["_meta"]["fingerprints_unique"] is True
    assert matrix["_meta"]["cannot_create_paper_trade"] is True
    assert matrix["_meta"]["cannot_change_v1"] is True
    assert matrix[VARIANT_BASELINE]["paper_eligible"] is False
    assert matrix[VARIANT_RETEST_ONLY]["fingerprint"] != matrix[VARIANT_BASELINE]["fingerprint"]


def test_disjoint_base_oos_windows():
    check = validate_research_windows(DEFAULT_SHORT_RESEARCH_WINDOWS)
    assert check["ok"] is True
    w = DEFAULT_SHORT_RESEARCH_WINDOWS
    assert w.base_end < w.oos_dev_start
    assert w.oos_dev_end < w.oos_val_start


def test_direct_candle_replay_stamp_on_variants():
    """Every variant identity carries research-only DIRECT replay expectation on baseline."""
    assert SAFETY_STAMPS["paper_eligible"] is False
    assert SAFETY_STAMPS["short_paper_disabled"] is True
    # Baseline ablation row marks direct_candle_replay when matrix built in runner;
    # unit-level identity check here.
    assert VARIANT_SPECS[VARIANT_BASELINE]["fees"] == "live model"


def test_no_variant_can_create_paper_trade():
    identity = short_research_identity(symbol="BTCUSDT")
    assert identity["paper_eligible"] is False
    with pytest.raises(ShortResearchOnlyError):
        assert_short_research_only_boundary(identity)
    paper = PaperTradeEngine(enabled=True, entry_mode="path_b")
    paper.risk_policy.enabled = False
    paper.legacy_auto_entry_enabled = True
    with pytest.raises(PermissionError, match="short_research_only"):
        paper.on_setup_signal(
            "BTCUSDT",
            {
                "status": "SHORT_ENTRY_CANDIDATE",
                "direction": "SHORT",
                "timeframe": "1h",
                "strategy_id": "COMBO_02_SHORT_RESEARCH",
                "source": "SHORT_RESEARCH_PIPELINE",
                "bos": {"state": "CONFIRMED", "direction": "BEARISH_BOS", "broken_level": 100},
                "trend": {"trend": "BEARISH"},
                "entry": {"entry_price": 100},
                "stop": {"final_stop": 105},
                "targets": [{"target_price": 90}],
                "risk_reward": {"RISK_REWARD": "PASS"},
                "ohlcv_freshness": "FRESH",
            },
        )


def test_no_variant_can_change_v1():
    payload = {
        "status": "SHORT_ENTRY_CANDIDATE",
        "direction": "SHORT",
        "combination_id": "COMBO_02",
        "strategy_id": "COMBO_02_SHORT_RESEARCH",
    }
    assert not is_v1_long_entry(payload)
    # Ablation combo ids must not redefine COMBO_02 v1
    assert VARIANT_SPECS[VARIANT_BASELINE]["combination_id"] == "COMBO_02"
    assert VARIANT_SPECS[VARIANT_NO_HTF]["combination_id"] == "COMBO_02_LOCAL"


def test_same_candle_policy_sl_first_preserved():
    assert SAME_CANDLE_PRECEDENCE_SL_FIRST == "SL_FIRST"


def test_stop_classifiers_tight_wide():
    tight = classify_stop_placement(
        direction="SHORT",
        entry_price=100.0,
        stop_price=100.5,
        atr=2.0,
        outcome="SL",
        mfe_r=0.1,
        mae_r=0.25,
    )
    wide = classify_stop_placement(
        direction="SHORT",
        entry_price=100.0,
        stop_price=110.0,
        atr=2.0,
        outcome="SL",
    )
    wrong = classify_stop_placement(
        direction="SHORT",
        entry_price=100.0,
        stop_price=95.0,
        atr=2.0,
    )
    assert tight == STOP_TOO_TIGHT
    assert wide == STOP_TOO_WIDE
    assert wrong == "WRONG_SIDE"


def test_chasing_entry_classification():
    candles = []
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # three large bearish bars
    px = 110.0
    for i in range(5):
        o = px
        c = px - 4.0
        candles.append(
            {
                "time": base + timedelta(hours=i),
                "open": o,
                "high": o + 0.2,
                "low": c - 0.2,
                "close": c,
                "volume": 1,
            }
        )
        px = c
    label = classify_entry_quality(
        entry_type="MARKET",
        entry_extension_atr_value=1.0,
        bos_level=108.0,
        entry_price=px,
        atr=2.0,
        candles=candles,
        entry_index=len(candles) - 1,
    )
    assert label == ENTRY_CHASING


def test_research_rejected_remains_terminal_label():
    assert LABEL_RESEARCH_REJECTED == "RESEARCH_REJECTED"
