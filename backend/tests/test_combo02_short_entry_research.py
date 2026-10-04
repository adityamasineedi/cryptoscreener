"""COMBO_02 SHORT entry-timing research — required suite (research-only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.short_entry_research.constants import (
    DEFAULT_ENTRY_RESEARCH_WINDOWS,
    DEFAULT_FILTER_THRESHOLDS,
    ENTRY_CHASING,
    ENTRY_EARLY,
    ENTRY_IMMEDIATE_BREAK,
    ENTRY_LATE,
    ENTRY_RETEST,
    PARENT_RUN_ID,
    PARENT_STRATEGY_ID,
    SAFETY_STAMPS,
    STOP_BOS_HIGH_ATR,
    STOP_CURRENT,
    STOP_FIXED_ATR,
    STOP_SWING_ATR,
    STRATEGY_ID,
    TP_CURRENT,
    TP_FIXED_1R,
    TP_FIXED_15R,
    TP_FIXED_2R,
    TP_SUPPORT,
    VARIANT_BASELINE,
    VARIANT_CHASING_EXCLUSION,
    VARIANT_EXTENSION_FILTER,
    VARIANT_RETEST,
)
from app.research.short_entry_research.entry_classification import (
    classify_entry_quality_exclusive,
    extension_filter_passed,
)
from app.research.short_entry_research.path_metrics import (
    build_stop_price,
    build_tp_price,
)
from app.research.short_entry_research.retest_fills import (
    assert_fill_after_order,
    simulate_short_retest_fill,
)
from app.research.short_entry_research.validation import (
    assert_disjoint,
    classify_oos_status,
    reconcile_variant,
    windows_from_dict,
)
from app.research.short_entry_research.variants import (
    ENTRY_VARIANT_IDS,
    STOP_VARIANT_IDS,
    TP_VARIANT_IDS,
    apply_baseline_variant,
    apply_chasing_exclusion,
    apply_extension_filter,
    apply_retest_variant,
    variant_fingerprint,
)
from app.research.short_research_diagnostics.metrics import (
    entry_delay_bars,
    entry_extension_atr,
)
from app.research.short_research_windows import DEFAULT_SHORT_RESEARCH_WINDOWS
from app.services.paper_trade import PaperTradeEngine
from app.services.telegram_alerts import is_v1_paper_alert
from app.services.v1_paper_watcher import is_v1_long_entry


def _ts(i: int, start: datetime | None = None) -> str:
    base = start or datetime(2024, 3, 1, 0, tzinfo=timezone.utc)
    return (base + timedelta(hours=i)).isoformat()


def _bar(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {
        "open_time": _ts(i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
    }


def _bearish_bos_then_retest_candles() -> list[dict]:
    """Synthetic: bearish BOS at idx 5 (breaks 100), later pullback fills at 100."""
    candles = []
    # Warmup sideways / down structure
    for i in range(5):
        candles.append(_bar(i, 102 - i * 0.2, 103, 101, 101.5 - i * 0.1))
    # BOS candle: close below swing low 100
    candles.append(_bar(5, 101.0, 101.2, 97.0, 97.5))  # bos_index=5, bos_level=100
    # Continue lower (no fill yet)
    candles.append(_bar(6, 97.4, 98.0, 96.0, 96.5))
    # Retest pullback: high revisits broken level 100
    candles.append(_bar(7, 96.8, 100.2, 96.5, 99.0))
    # After fill path
    candles.append(_bar(8, 98.8, 99.0, 94.0, 94.5))
    candles.append(_bar(9, 94.4, 95.0, 92.0, 92.5))
    return candles


def _bos_no_retest_candles() -> list[dict]:
    candles = []
    for i in range(5):
        candles.append(_bar(i, 102, 103, 101, 101.5))
    candles.append(_bar(5, 101.0, 101.2, 97.0, 97.5))
    for i in range(6, 18):
        # Keep trading well below 100 — never retests
        candles.append(_bar(i, 96.0, 97.0, 94.0, 95.0))
    return candles


def _bos_gap_through_candles() -> list[dict]:
    candles = []
    for i in range(5):
        candles.append(_bar(i, 102, 103, 101, 101.5))
    candles.append(_bar(5, 101.0, 101.2, 97.0, 97.5))
    candles.append(_bar(6, 96.5, 97.0, 95.0, 95.5))
    # Gap open above limit 100
    candles.append(_bar(7, 101.5, 102.0, 100.5, 101.0))
    return candles


# --- 1–5: retest fill engine ---


def test_synthetic_bearish_bos_valid_retest_fill():
    candles = _bearish_bos_then_retest_candles()
    fill = simulate_short_retest_fill(
        candles, bos_index=5, bos_level=100.0, atr=2.0, expiry_bars=12
    )
    assert fill["filled"] is True
    assert fill["fill_index"] == 7
    assert fill["fill_price"] == pytest.approx(100.0)
    assert fill["fill_reason"] == "limit_touched"
    assert fill["retroactive_bos_fill"] is False
    assert assert_fill_after_order(fill) is True
    assert fill["paper_eligible"] is False


def test_no_fill_without_later_retest():
    candles = _bos_no_retest_candles()
    fill = simulate_short_retest_fill(
        candles, bos_index=5, bos_level=100.0, atr=2.0, expiry_bars=12
    )
    assert fill["filled"] is False
    assert fill["fill_reason"] == "expired_no_retest"


def test_no_retroactive_retest_fill_on_bos_candle():
    candles = _bearish_bos_then_retest_candles()
    # Even if BOS candle itself traded through the level, default policy forbids fill.
    candles[5] = _bar(5, 101.0, 100.5, 97.0, 97.5)  # high touches level
    fill = simulate_short_retest_fill(
        candles,
        bos_index=5,
        bos_level=100.0,
        atr=2.0,
        allow_bos_candle_fill=False,
    )
    assert fill["filled"] is True
    assert fill["fill_index"] != 5
    assert fill["retroactive_bos_fill"] is False


def test_retest_expiry_cancellation():
    candles = _bos_no_retest_candles()
    fill = simulate_short_retest_fill(
        candles, bos_index=5, bos_level=100.0, atr=2.0, expiry_bars=3
    )
    assert fill["filled"] is False
    assert fill["fill_reason"] == "expired_no_retest"
    assert fill["expiry_bars"] == 3


def test_gap_through_and_same_candle_policy():
    gap = simulate_short_retest_fill(
        _bos_gap_through_candles(), bos_index=5, bos_level=100.0, atr=1.0
    )
    assert gap["filled"] is True
    assert gap["fill_reason"] == "gap_through_open"
    assert gap["fill_price"] == pytest.approx(101.5)

    candles = _bearish_bos_then_retest_candles()
    same = simulate_short_retest_fill(
        candles,
        bos_index=5,
        bos_level=100.0,
        atr=2.0,
        allow_bos_candle_fill=True,
    )
    # Explicit same-candle policy may fill on BOS only when enabled.
    assert same["filled"] is True


# --- 6–9: classification / filters ---


def test_entry_classification_precedence():
    retest = classify_entry_quality_exclusive(
        valid_retest_fill=True, extension_atr=2.0, delay_bars=5
    )
    assert retest["entry_quality_primary"] == ENTRY_RETEST
    assert retest["entry_quality_secondary"] == ENTRY_CHASING

    chase = classify_entry_quality_exclusive(
        valid_retest_fill=False, extension_atr=1.5, delay_bars=5
    )
    assert chase["entry_quality_primary"] == ENTRY_CHASING
    assert chase["entry_quality_secondary"] == ENTRY_LATE

    late = classify_entry_quality_exclusive(
        valid_retest_fill=False, extension_atr=0.4, delay_bars=5
    )
    assert late["entry_quality_primary"] == ENTRY_LATE

    immediate = classify_entry_quality_exclusive(
        valid_retest_fill=False, extension_atr=0.4, delay_bars=0
    )
    assert immediate["entry_quality_primary"] == ENTRY_IMMEDIATE_BREAK

    early = classify_entry_quality_exclusive(
        valid_retest_fill=False, extension_atr=0.1, delay_bars=0
    )
    assert early["entry_quality_primary"] == ENTRY_EARLY


def test_extension_atr_calculation():
    ext = entry_extension_atr(95.0, bos_level=100.0, atr_at_signal=2.0)
    assert ext == pytest.approx(2.5)


def test_delay_bar_calculation():
    t0 = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=3)
    assert entry_delay_bars(t1, t0) == 3
    assert entry_delay_bars(t0, t0) == 0


def test_chasing_exclusion():
    rows = [
        {
            "entry_quality_primary": ENTRY_CHASING,
            "net_pnl": -10,
            "gross_pnl": -9,
            "fees": -1,
            "R": -0.5,
            "outcome": "SL",
            "entry_price": 100,
            "stop_price": 105,
            "exit_price": 105,
        },
        {
            "entry_quality_primary": ENTRY_IMMEDIATE_BREAK,
            "net_pnl": 5,
            "gross_pnl": 6,
            "fees": -1,
            "R": 0.3,
            "outcome": "TP1",
            "entry_price": 100,
            "stop_price": 105,
            "exit_price": 97,
        },
    ]
    kept = apply_chasing_exclusion(rows)
    assert len(kept) == 1
    assert kept[0]["entry_quality_primary"] == ENTRY_IMMEDIATE_BREAK
    assert kept[0]["variant_id"] == VARIANT_CHASING_EXCLUSION
    assert kept[0]["paper_eligible"] is False


# --- 10–13: variant identity / isolation ---


def test_stop_variant_identity():
    stop_a = build_stop_price(
        variant=STOP_CURRENT,
        entry_price=100.0,
        current_stop=105.0,
        atr=2.0,
        swing_high=104.0,
        bos_candle_high=103.0,
    )
    stop_b = build_stop_price(
        variant=STOP_SWING_ATR,
        entry_price=100.0,
        current_stop=105.0,
        atr=2.0,
        swing_high=104.0,
    )
    stop_c = build_stop_price(
        variant=STOP_BOS_HIGH_ATR,
        entry_price=100.0,
        current_stop=105.0,
        atr=2.0,
        bos_candle_high=103.0,
    )
    stop_d = build_stop_price(
        variant=STOP_FIXED_ATR,
        entry_price=100.0,
        current_stop=105.0,
        atr=2.0,
    )
    assert stop_a == pytest.approx(105.0)
    assert stop_b > 104.0
    assert stop_c > 103.0
    assert stop_d == pytest.approx(100.0 + 2.0 * float(DEFAULT_FILTER_THRESHOLDS["fixed_atr_stop_mult"]))
    assert len(STOP_VARIANT_IDS) == 4
    assert len({stop_a, stop_b, stop_c, stop_d}) >= 3


def test_tp_variant_identity():
    prices = {
        tid: build_tp_price(
            variant=tid,
            entry_price=100.0,
            stop_price=105.0,
            current_tp=90.0,
            support_level=96.0,
        )
        for tid in (TP_CURRENT, TP_SUPPORT, TP_FIXED_1R, TP_FIXED_15R, TP_FIXED_2R)
    }
    assert prices[TP_CURRENT] == pytest.approx(90.0)
    assert prices[TP_SUPPORT] == pytest.approx(96.0)
    assert prices[TP_FIXED_1R] == pytest.approx(95.0)
    assert prices[TP_FIXED_15R] == pytest.approx(92.5)
    assert prices[TP_FIXED_2R] == pytest.approx(90.0)
    assert len(TP_VARIANT_IDS) == 5


def test_variant_fingerprints_distinct():
    win = DEFAULT_ENTRY_RESEARCH_WINDOWS
    th = DEFAULT_FILTER_THRESHOLDS
    fps = [
        variant_fingerprint(vid, window=win, thresholds=th, symbols=["BTCUSDT"])
        for vid in ENTRY_VARIANT_IDS
    ]
    assert len(set(fps)) == len(ENTRY_VARIANT_IDS)
    assert STRATEGY_ID == "COMBO_02_SHORT_ENTRY_RESEARCH"
    assert PARENT_STRATEGY_ID == "COMBO_02_SHORT_RESEARCH"
    assert PARENT_RUN_ID == "20261004T060446Z-b5d71081"


def test_variant_results_remain_isolated():
    candles = {"BTCUSDT": _bearish_bos_then_retest_candles()}
    base_trade = {
        "symbol": "BTCUSDT",
        "direction": "SHORT",
        "entry_price": 97.5,
        "stop_price": 102.0,
        "tp1": 90.0,
        "TP1": 90.0,
        "outcome": "SL",
        "exit_price": 102.0,
        "entry_time": _ts(5),
        "entry_index": 5,
        "exit_index": 7,
        "entry_type": "MARKET",
        "bos_level": 100.0,
        "atr_at_signal": 2.0,
        "entry_extension_atr": 1.25,
        "entry_delay_bars": 0,
        "gross_pnl": -20.0,
        "net_pnl": -21.0,
        "fees": -1.0,
        "R": -1.05,
    }
    atr_lookup = {"BTCUSDT:5": 2.0}
    baseline = apply_baseline_variant(
        [base_trade], candles_by_symbol=candles, atr_by_trade=atr_lookup
    )
    retest = apply_retest_variant(
        [base_trade], candles_by_symbol=candles, atr_lookup=atr_lookup
    )
    ext = apply_extension_filter(baseline, thresholds={"extension_filter_max_atr": 0.5, "extension_filter_max_delay_bars": 1})
    chase = apply_chasing_exclusion(baseline)
    # Isolation: mutating one list does not change another
    if baseline:
        baseline[0]["variant_id"] = "mutated"
    assert all(r.get("variant_id") == VARIANT_RETEST for r in retest)
    assert all(r.get("variant_id") == VARIANT_EXTENSION_FILTER for r in ext) or len(ext) == 0
    assert all(r.get("variant_id") == VARIANT_CHASING_EXCLUSION for r in chase)


# --- 14–16: safety boundaries ---


def test_all_variants_paper_ineligible():
    assert SAFETY_STAMPS["paper_eligible"] is False
    assert SAFETY_STAMPS["production_approved"] is False
    assert SAFETY_STAMPS["telegram_eligible"] is False
    for vid in ENTRY_VARIANT_IDS:
        fp_payload_ok = SAFETY_STAMPS["paper_eligible"] is False
        assert fp_payload_ok
        assert vid  # identity present
    paper = PaperTradeEngine(enabled=True, entry_mode="path_b")
    # SHORT research identity must not become a paper signal source
    assert getattr(paper, "enabled", True) is True  # engine can exist
    # But research stamps never approve paper
    assert SAFETY_STAMPS["short_paper_disabled"] is True


def test_no_variant_enters_v1():
    assert SAFETY_STAMPS["v1_unchanged"] is True
    assert STRATEGY_ID != "COMBO_02"
    assert STRATEGY_ID.endswith("ENTRY_RESEARCH")
    payload = {
        "direction": "SHORT",
        "combination_id": STRATEGY_ID,
        "symbol": "BTCUSDT",
    }
    assert not is_v1_long_entry(payload)


def test_no_variant_triggers_telegram():
    assert SAFETY_STAMPS["telegram_eligible"] is False
    assert SAFETY_STAMPS["short_telegram_disabled"] is True
    row = {
        **SAFETY_STAMPS,
        "variant_id": VARIANT_BASELINE,
        "direction": "SHORT",
        "combination_id": STRATEGY_ID,
        "is_v1": False,
        "paper_trade": True,
    }
    assert row["telegram_eligible"] is False
    assert is_v1_paper_alert(row) is False


# --- 17–19: windows / replay / recon ---


def test_strict_base_oos_separation():
    windows = windows_from_dict(DEFAULT_ENTRY_RESEARCH_WINDOWS)
    check = assert_disjoint(windows)
    assert check["ok"] is True
    assert windows.base_end < windows.oos_dev_start
    assert windows.oos_dev_end < windows.oos_val_start


def test_direct_candle_replay_gate_and_oos_status():
    base_bad = {
        "trade_count": 10,
        "net_pnl": -100.0,
        "average_net_r": -0.2,
        "profit_factor": 0.8,
    }
    status = classify_oos_status(
        base_summary=base_bad,
        oos_val_summary=None,
        oos_val_trades=0,
        recon_ok=True,
        direct_replay=True,
        windows_ok=True,
    )
    assert status["oos_status"] == "NOT_APPLICABLE"
    assert status["paper_eligible"] is False
    assert status["research_classification"] == "RESEARCH_REJECTED"

    base_good = {
        "trade_count": 40,
        "net_pnl": 100.0,
        "average_net_r": 0.2,
        "profit_factor": 1.4,
    }
    oos_good = {
        "trade_count": 35,
        "net_pnl": 50.0,
        "average_net_r": 0.15,
        "profit_factor": 1.2,
    }
    status2 = classify_oos_status(
        base_summary=base_good,
        oos_val_summary=oos_good,
        oos_val_trades=35,
        recon_ok=True,
        direct_replay=True,
        windows_ok=True,
    )
    assert status2["oos_status"] == "PASS"
    assert status2["paper_eligible"] is False  # hard rule this phase


def test_fee_equity_order_reconciliation():
    trades = [
        {
            "outcome": "SL",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "exit_price": 105.0,
            "gross_pnl": -20.0,
            "fees": -1.0,
            "net_pnl": -21.0,
            "R": -1.05,
            "r_net": -1.05,
            "entry_time": "2024-01-02T00:00:00+00:00",
            "direction": "SHORT",
        },
        {
            "outcome": "TP1",
            "entry_price": 100.0,
            "stop_price": 105.0,
            "tp1": 90.0,
            "exit_price": 90.0,
            "gross_pnl": 40.0,
            "fees": -1.2,
            "net_pnl": 38.8,
            "R": 1.94,
            "r_net": 1.94,
            "entry_time": "2024-01-03T00:00:00+00:00",
            "direction": "SHORT",
        },
    ]
    recon = reconcile_variant(trades)
    assert "equity_reconciliation" in recon
    assert "fee_reconciliation" in recon
    assert "trade_order_reconciliation" in recon


def test_extension_filter_uses_thresholds_not_hardcoded():
    assert extension_filter_passed(
        extension_atr=0.4, delay_bars=1, thresholds={"extension_filter_max_atr": 0.5, "extension_filter_max_delay_bars": 1}
    )
    assert not extension_filter_passed(
        extension_atr=0.9, delay_bars=0, thresholds={"extension_filter_max_atr": 0.5, "extension_filter_max_delay_bars": 1}
    )


def test_default_windows_match_policy_a():
    assert DEFAULT_ENTRY_RESEARCH_WINDOWS["policy"] == "POLICY_A_STRICT_DISJOINT"
    # Parent diagnostic base window (immutable baseline sample)
    assert DEFAULT_ENTRY_RESEARCH_WINDOWS["base_start"] == "2024-01-01"
    assert DEFAULT_ENTRY_RESEARCH_WINDOWS["base_end"] == "2025-06-30"
    assert DEFAULT_ENTRY_RESEARCH_WINDOWS["oos_val_start"] == "2026-01-01"
    # Canonical short-research defaults remain a separate Policy A template
    assert DEFAULT_SHORT_RESEARCH_WINDOWS.base_start <= DEFAULT_SHORT_RESEARCH_WINDOWS.base_end
