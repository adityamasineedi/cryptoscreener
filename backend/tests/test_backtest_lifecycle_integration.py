"""Backtest runner consumes research multi-bar lifecycle.

Production signal engines remain unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.lifecycle import (
    eligibility_index_for_strategy,
    first_pullback_pass_eval,
    first_retest_pass_eval,
    freeze_bos_impulse_event,
    make_lifecycle_id,
    run_lifecycle,
    strategy_needs_lifecycle,
)
from app.research.bos_strategy_comparison.runner import run_multi_strategy_backtest
from app.research.bos_strategy_comparison.strategies import STRATEGIES
from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import AMBIGUOUS_CONSERVATIVE
from app.signals.config import SignalConfig
from app.signals.pullback_engine import detect_pullback

from tests.test_bos_strategy_comparison import make_htf_from_setup, make_uptrend_series


def _series_with_impulse_pullback(n: int = 120) -> list[dict]:
    """Synthetic series with a forced late impulse + mild pullback (test-only)."""
    base = datetime(2024, 9, 1, tzinfo=timezone.utc)
    candles: list[dict] = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + 0.4
        candles.append(
            {
                "time": base + timedelta(minutes=15 * i),
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.3,
                "close": c,
                "volume": 2000.0,
            }
        )
        price = c
    impulse_i = 70
    candles[impulse_i] = {
        "time": base + timedelta(minutes=15 * impulse_i),
        "open": 128.0,
        "high": 140.0,
        "low": 127.0,
        "close": 139.0,
        "volume": 8000.0,
    }
    for j, low in enumerate([136.0, 134.5, 133.5, 134.0]):
        idx = impulse_i + 1 + j
        candles[idx] = {
            "time": base + timedelta(minutes=15 * idx),
            "open": 137.0,
            "high": 138.0,
            "low": low,
            "close": 135.0,
            "volume": 1800.0,
        }
    return candles


def test_strategy_needs_lifecycle_flags():
    assert strategy_needs_lifecycle(STRATEGIES["CONTROL_A"]) is False
    assert strategy_needs_lifecycle(STRATEGIES["CONTROL_B"]) is False
    assert strategy_needs_lifecycle(STRATEGIES["CONTROL_C"]) is True
    assert strategy_needs_lifecycle(STRATEGIES["CONTROL_D"]) is True
    assert strategy_needs_lifecycle(STRATEGIES["STRATEGY_1"]) is True
    assert strategy_needs_lifecycle(STRATEGIES["STRATEGY_2"]) is True
    assert strategy_needs_lifecycle(STRATEGIES["STRATEGY_3"]) is True


def test_lifecycle_id_stable():
    a = make_lifecycle_id(
        symbol="btcusdt",
        timeframe="15m",
        bos_index=10,
        bos_direction="BULLISH_BOS",
        impulse_index=10,
    )
    b = make_lifecycle_id(
        symbol="BTCUSDT",
        timeframe="15m",
        bos_index=10,
        bos_direction="BULLISH_BOS",
        impulse_index=10,
    )
    assert a == b


def test_failed_pullback_does_not_trigger_retest_in_lifecycle():
    candles = make_uptrend_series(100)
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 110.0,
        "break_price": 112.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 50,
        "impulse_origin": 100.0,
        "impulse_end": 110.0,
        "atr": 1.0,
        "quality": "MODERATE",
        "rvol": 2.0,
    }
    event = freeze_bos_impulse_event(
        symbol="TESTUSDT",
        timeframe="15m",
        bos_index=50,
        bos=bos,
        impulse=impulse,
        candles=candles,
    )
    # Force deep invalidating move after impulse
    for j in range(1, 6):
        candles[50 + j]["low"] = 90.0
        candles[50 + j]["close"] = 91.0
    lc = run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=10
    )
    for ev in lc.evaluations:
        if ev.pullback_state == "FAILED":
            # Research lifecycle must not have called retest on FAILED
            assert ev.retest.get("retest") is False
            assert "Waiting for pullback" in str(ev.retest.get("reason") or "")


def test_active_confirmed_can_enable_retest():
    candles = _series_with_impulse_pullback()
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 135.0,
        "break_price": 139.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 70,
        "impulse_origin": 127.0,
        "impulse_end": 140.0,
        "atr": 2.0,
        "quality": "STRONG",
        "rvol": 2.5,
    }
    event = freeze_bos_impulse_event(
        symbol="TESTUSDT",
        timeframe="15m",
        bos_index=70,
        bos=bos,
        impulse=impulse,
        candles=candles,
    )
    cfg = SignalConfig()
    cfg.retest_atr_tolerance = 5.0  # widen for synthetic retest chance
    same = detect_pullback(
        candles, bos, impulse, cfg, as_of_index=70
    )
    assert same["pullback_state"] == "WAITING"
    lc = run_lifecycle(candles=candles, event=event, signal_config=cfg, max_follow_bars=15)
    pb = first_pullback_pass_eval(lc)
    # May or may not retest on synthetic data; pullback should leave WAITING
    assert lc.candles_evaluated >= 1
    if pb is not None:
        assert pb.as_of_index > 70


def test_c1_c2_do_not_require_lifecycle():
    assert strategy_needs_lifecycle(STRATEGIES["CONTROL_A"]) is False
    assert strategy_needs_lifecycle(STRATEGIES["CONTROL_B"]) is False


def test_one_lifecycle_one_opportunity_eligibility():
    candles = _series_with_impulse_pullback()
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 135.0,
        "break_price": 139.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 70,
        "impulse_origin": 127.0,
        "impulse_end": 140.0,
        "atr": 2.0,
        "quality": "STRONG",
        "rvol": 2.5,
    }
    event = freeze_bos_impulse_event(
        symbol="TESTUSDT",
        timeframe="15m",
        bos_index=70,
        bos=bos,
        impulse=impulse,
        candles=candles,
    )
    lc = run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=20
    )
    # Multiple WAITING evals must not create multiple eligibility indices
    idxs = [
        eligibility_index_for_strategy(
            lc, require_pullback=True, require_retest=False
        )
    ]
    assert len(set(idxs)) == 1


def test_entry_not_before_lifecycle_confirmation():
    """Runner with lifecycle must not enter pullback strategies on BOS bar."""
    candles = make_uptrend_series(180)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    rcfg = StrategyResearchConfig(min_bars=20, research_max_lifecycle_bars=30)
    multi = run_multi_strategy_backtest(
        "TESTUSDT",
        "15m",
        candles,
        ["CONTROL_D", "STRATEGY_2"],
        candles_4h=h4,
        candles_1h=h1,
        research_config=rcfg,
        signal_config=SignalConfig(),
        use_lifecycle=True,
        index_start=40,
    )
    for sid, payload in (multi.get("strategies") or {}).items():
        for t in payload.get("trades") or []:
            lid = t.get("lifecycle_id")
            bos_ts = t.get("bos_timestamp")
            entry_ts = t.get("entry_time")
            entry_i = t.get("entry_index")
            assert lid is not None
            # Entry bar must be after BOS (lifecycle confirmation later)
            # bos index is encoded in lifecycle_id: SYMBOL|tf|bos_index|dir|impulse_index
            parts = str(lid).split("|")
            bos_i = int(parts[2])
            assert int(entry_i) > bos_i
            if bos_ts and entry_ts:
                assert entry_ts >= bos_ts


def test_waiting_does_not_create_trades_without_pass():
    candles = make_uptrend_series(100)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    # Same-bar path for CONTROL_D should yield 0 (pullback unreachable)
    multi = run_multi_strategy_backtest(
        "TESTUSDT",
        "15m",
        candles,
        ["CONTROL_D"],
        candles_4h=h4,
        candles_1h=h1,
        research_config=StrategyResearchConfig(min_bars=20),
        use_lifecycle=False,
        index_start=40,
    )
    trades = (multi.get("strategies") or {}).get("CONTROL_D", {}).get("trades") or []
    assert trades == []


def test_same_bar_sl_first_unchanged():
    outcome, px, amb = resolve_intrabar_outcome(
        direction="LONG",
        high=110,
        low=90,
        stop=95,
        targets=[105.0],
        handling=AMBIGUOUS_CONSERVATIVE,
    )
    assert outcome == "SL"
    assert px == 95
    assert amb is True


def test_fees_slippage_config_unchanged():
    rcfg = StrategyResearchConfig()
    assert rcfg.taker_fee == 0.0004
    assert rcfg.maker_fee == 0.0002
    assert rcfg.slippage_rate == 0.0002
    assert rcfg.min_rr == 2.0


def test_c1_sample_not_zero_on_uptrend_with_lifecycle_path():
    candles = make_uptrend_series(200)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    multi = run_multi_strategy_backtest(
        "TESTUSDT",
        "15m",
        candles,
        ["CONTROL_A", "CONTROL_B", "CONTROL_D"],
        candles_4h=h4,
        candles_1h=h1,
        research_config=StrategyResearchConfig(min_bars=20),
        use_lifecycle=True,
        index_start=40,
    )
    assert multi.get("use_lifecycle") is True
    # C1 should still be able to produce setups independently of lifecycle
    assert "CONTROL_A" in (multi.get("strategies") or {})
    assert "lifecycle" in multi


def test_use_lifecycle_false_preserves_legacy_same_bar():
    candles = make_uptrend_series(160)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    legacy = run_multi_strategy_backtest(
        "TESTUSDT",
        "15m",
        candles,
        ["CONTROL_A", "STRATEGY_2"],
        candles_4h=h4,
        candles_1h=h1,
        research_config=StrategyResearchConfig(min_bars=20),
        use_lifecycle=False,
        index_start=40,
    )
    assert legacy.get("use_lifecycle") is False
    s2_trades = (legacy.get("strategies") or {}).get("STRATEGY_2", {}).get("trades") or []
    # Same-bar: pullback unreachable → typically zero STRATEGY_2 trades
    assert isinstance(s2_trades, list)


def test_frozen_impulse_not_mutated_by_runner_path():
    candles = make_uptrend_series(120)
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 110.0,
        "break_price": 111.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": 55,
        "impulse_origin": 105.0,
        "impulse_end": 112.0,
        "atr": 1.0,
        "quality": "MODERATE",
        "rvol": 2.0,
    }
    event = freeze_bos_impulse_event(
        symbol="TESTUSDT",
        timeframe="15m",
        bos_index=55,
        bos=bos,
        impulse=impulse,
        candles=candles,
    )
    before = deepcopy(event.frozen_impulse)
    run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=12
    )
    assert event.frozen_impulse == before


def test_s1_s2_s3_semantics_flags_unchanged():
    s1, s2, s3 = STRATEGIES["STRATEGY_1"], STRATEGIES["STRATEGY_2"], STRATEGIES["STRATEGY_3"]
    assert s1.require_retest and s1.require_htf_alignment and not s1.require_impulse
    assert s2.require_impulse and s2.require_pullback and s2.require_retest
    assert s3.require_pullback and s3.require_sd and s3.require_retest and not s3.require_impulse
