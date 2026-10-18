"""Exit-overlay tests for CANDLE12_V1_TIME48_PROGRESS_GUARD. No database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.candle12_v1_time48_progress_guard import (
    OUTCOME_PROGRESS_GUARD,
    OUTCOME_TIME_STOP,
    TIME_STOP_BARS,
    OverlayConfig,
    apply_overlay,
    breakeven_raw,
    classify_variant,
    config_hash,
    protective_stop,
    replay_exit,
)

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _c(i: int, o: float, h: float, l: float, cl: float) -> dict:
    return {
        "time": BASE + timedelta(hours=i),
        "open": o,
        "high": h,
        "low": l,
        "close": cl,
        "volume": 1.0,
    }


def _flat(n: int, price: float = 100.0, wick: float = 0.2) -> list[dict]:
    return [_c(i, price, price + wick, price - wick, price) for i in range(n)]


def _trade(**kw) -> dict:
    base = {
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "tp1": 140.0,
        "tp2": None,
        "tp3": None,
        "signal_time": BASE,
        "outcome": "OPEN",
        "exit_price": None,
        "exit_index": None,
    }
    base.update(kw)
    return base


def test_time_stop_exits_at_bar_48_close():
    candles = _flat(60)
    replay = replay_exit(_trade(), candles, OverlayConfig(time_stop_48=True, progress_guard_1r=False))
    assert replay.outcome == OUTCOME_TIME_STOP
    assert replay.exit_index == TIME_STOP_BARS - 1
    assert replay.raw_exit == candles[TIME_STOP_BARS - 1]["close"]
    assert replay.holding_bars == TIME_STOP_BARS - 1
    assert replay.guard_activated is False


def test_time_stop_does_not_fire_after_stop_or_tp1():
    stopped = _flat(60)
    stopped[9] = _c(9, 100, 100.2, 89.0, 95.0)
    stop_replay = replay_exit(_trade(), stopped, OverlayConfig(True, False))
    assert stop_replay.outcome == "SL"
    assert stop_replay.exit_index == 9

    targeted = _flat(60)
    targeted[TIME_STOP_BARS - 1] = _c(TIME_STOP_BARS - 1, 100, 140.0, 99.5, 130.0)
    tp_replay = replay_exit(_trade(), targeted, OverlayConfig(True, False))
    assert tp_replay.outcome == "TP1"
    assert tp_replay.exit_index == TIME_STOP_BARS - 1

    same_bar_stop = _flat(60)
    same_bar_stop[TIME_STOP_BARS - 1] = _c(TIME_STOP_BARS - 1, 100, 100.2, 89.0, 95.0)
    late_stop = replay_exit(_trade(), same_bar_stop, OverlayConfig(True, False))
    assert late_stop.outcome == "SL"
    assert late_stop.exit_index == TIME_STOP_BARS - 1


def test_progress_guard_arms_only_after_a_full_bar_at_1r_and_not_on_that_bar():
    shy = _flat(20)
    shy[4] = _c(4, 100, 100 + 0.99 * 10, 99.5, 105.0)
    shy[8] = _c(8, 100, 100.2, 89.0, 95.0)
    shy_replay = replay_exit(_trade(), shy, OverlayConfig(False, True))
    assert shy_replay.guard_activated is False
    assert shy_replay.outcome == "SL"

    armed = _flat(20, wick=0.0)
    # High prints +1R. Low is through breakeven but not through the structural stop.
    # The guard is not allowed to stop this same bar. The next bar stays above it.
    be = breakeven_raw(100.0)
    armed[4] = _c(4, 100, 110.0, be - 0.5, 108.0)
    armed[5] = _c(5, 108, 109.0, be + 0.2, 108.5)
    armed[6] = _c(6, 108, 108.2, be - 0.1, 107.0)
    armed_replay = replay_exit(_trade(), armed, OverlayConfig(False, True))
    assert armed_replay.guard_activated is True
    assert armed_replay.outcome == OUTCOME_PROGRESS_GUARD
    assert armed_replay.exit_index == 6
    assert armed_replay.raw_exit == pytest_approx_stop(armed_replay.protective_stop)
    assert armed_replay.protective_stop >= be


def pytest_approx_stop(value: float) -> float:
    return value


def test_progress_guard_stop_is_never_worse_than_breakeven_after_fees():
    be = breakeven_raw(100.0)
    assert protective_stop(90.0, be, "LONG") == be
    assert protective_stop(be + 1.0, be, "LONG") == be + 1.0
    short_be = breakeven_raw(100.0, direction="SHORT")
    assert protective_stop(110.0, short_be, "SHORT") == short_be
    assert protective_stop(short_be - 1.0, short_be, "SHORT") == short_be - 1.0

    rows = apply_overlay([_trade()], _guard_then_be_candles(), OverlayConfig(False, True))
    assert len(rows) == 1
    assert rows[0]["outcome"] == OUTCOME_PROGRESS_GUARD
    assert rows[0]["protective_stop"] >= be
    assert abs(rows[0]["net_pnl_usd"]) < 1e-6
    assert rows[0]["funding_usd"] == 0.0
    assert rows[0]["fee_entry_rate_applied"] == 0.0004
    assert rows[0]["exit_slippage_applied"] == 0.0002


def _guard_then_be_candles() -> list[dict]:
    be = breakeven_raw(100.0)
    candles = _flat(12, wick=0.0)
    candles[3] = _c(3, 100, 110.0, 99.0, 109.0)
    candles[4] = _c(4, 109, 109.5, be + 0.2, 109.2)
    candles[5] = _c(5, 109, 109.2, be - 0.05, 108.0)
    return candles


def test_same_bar_stop_and_1r_does_not_arm_the_guard():
    candles = _flat(10)
    candles[3] = _c(3, 100, 110.0, 89.0, 95.0)
    replay = replay_exit(_trade(), candles, OverlayConfig(False, True))
    assert replay.outcome == "SL"
    assert replay.exit_index == 3
    assert replay.guard_activated is False
    assert replay.protective_stop == 90.0


def test_overlays_toggle_independently_and_runs_match():
    candles = _flat(80)
    candles[5] = _c(5, 100, 110.0, 99.0, 109.0)
    candles[70] = _c(70, 100, 100.2, 89.0, 95.0)
    trade = _trade()
    off = replay_exit(trade, candles, OverlayConfig(False, False))
    time_only = replay_exit(trade, candles, OverlayConfig(True, False))
    guard_only = replay_exit(trade, candles, OverlayConfig(False, True))
    both = replay_exit(trade, candles, OverlayConfig(True, True))
    assert off.outcome == "SL" and off.exit_index == 70
    assert time_only.outcome == OUTCOME_TIME_STOP and time_only.exit_index == 47
    assert guard_only.outcome == OUTCOME_PROGRESS_GUARD and guard_only.exit_index > 5
    assert both.outcome == OUTCOME_PROGRESS_GUARD
    assert config_hash(OverlayConfig(True, False)) != config_hash(OverlayConfig(False, True))
    again = replay_exit(trade, candles, OverlayConfig(True, True))
    assert both == again


def test_a_bar_after_the_exit_cannot_change_the_result():
    candles = _flat(80, wick=0.0)
    be = breakeven_raw(100.0)
    candles[5] = _c(5, 100, 110.0, 99.0, 109.0)
    candles[6] = _c(6, 109, 109.5, be + 0.2, 109.2)
    candles[7] = _c(7, 109, 109.5, be + 0.2, 109.2)
    candles[8] = _c(8, 109, 109.2, be - 0.05, 108.0)
    first = replay_exit(_trade(), candles, OverlayConfig(False, True))
    assert first.exit_index is not None and first.exit_index < 79
    candles[first.exit_index + 1] = _c(first.exit_index + 1, 1, 500, 1, 1)
    second = replay_exit(_trade(), candles, OverlayConfig(False, True))
    assert second == first


def test_both_off_matches_the_frozen_intrabar_rule():
    from app.research.combination_backtest import resolve_intrabar_outcome

    candles = _flat(15)
    candles[4] = _c(4, 100, 101, 99, 100)
    candles[7] = _c(7, 100, 141, 99, 130)
    trade = _trade()
    found = None
    for i, candle in enumerate(candles):
        outcome, raw, _amb = resolve_intrabar_outcome(
            direction="LONG",
            high=candle["high"],
            low=candle["low"],
            stop=90.0,
            targets=[140.0],
            handling="CONSERVATIVE",
        )
        if outcome is not None:
            found = (i, outcome, raw)
            break
    replay = replay_exit(trade, candles, OverlayConfig(False, False))
    assert found is not None
    assert (replay.exit_index, replay.outcome, replay.raw_exit) == found


def test_apply_overlay_is_deterministic():
    candles = _guard_then_be_candles()
    a = apply_overlay([_trade()], candles, OverlayConfig(True, True))
    b = apply_overlay([_trade()], candles, OverlayConfig(True, True))
    assert a[0]["net_pnl_usd"] == b[0]["net_pnl_usd"]
    assert a[0]["outcome"] == b[0]["outcome"]
    assert a[0]["exit_index"] == b[0]["exit_index"]
    assert a[0]["loss_reason"] == b[0]["loss_reason"]


def test_decision_rule_bands():
    base = {"net_r": 10.0, "max_drawdown_r": 5.0}
    promising = classify_variant(
        {"net_r": 11.0, "max_drawdown_r": 4.0, "winners_lost_vs_baseline": 1}, base
    )
    assert promising["label"] == "PROMISING"
    rejected = classify_variant(
        {"net_r": 12.0, "max_drawdown_r": 4.0, "winners_lost_vs_baseline": 2}, base
    )
    assert rejected["label"] == "REJECT"
    drawdown = classify_variant(
        {"net_r": 11.0, "max_drawdown_r": 5.3, "winners_lost_vs_baseline": 0}, base
    )
    assert drawdown["label"] == "REJECT"
    neutral = classify_variant(
        {"net_r": 10.2, "max_drawdown_r": 4.9, "winners_lost_vs_baseline": 0}, base
    )
    assert neutral["label"] == "NEUTRAL"
