"""Regime Lab: indicators, regime rules, fills, costs, gating, causality, folds."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.research.regime_lab import indicators as ind
from app.research.regime_lab.config import (
    BOLLINGER,
    CHOPPY,
    DONCHIAN,
    DOWNTREND,
    LONG,
    RSI2,
    SIDEWAYS,
    TRANSITION,
    UPTREND,
    WARMUP,
    RegimeParams,
    RunConfig,
    Safeguards,
)
from app.research.regime_lab.engine import Trade, monte_carlo, run_backtest
from app.research.regime_lab.regime import classify, confirm, hysteresis_labels, map_higher_timeframe
from app.research.regime_lab.strategies import SymbolData
from app.research.regime_lab.walkforward import folds, plateau_scores

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
STEP = timedelta(hours=4)
SMALL_REGIME = RegimeParams(sma_period=10, adx_period=3, atr_period=3, atr_rank_window=10)


def _candles(rows: list[tuple[float, float, float, float]]) -> list[dict]:
    return [
        {"time": T0 + STEP * k, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}
        for k, (o, h, l, c) in enumerate(rows)
    ]


def _set_regime(d: SymbolData, regime: str) -> None:
    d.regime = [regime] * len(d.times)
    d.route = list(d.regime)
    d.size = [1.0] * len(d.times)


def _data(symbol: str, rows, regime: str | None = None) -> SymbolData:
    d = SymbolData.from_candles(symbol, _candles(rows), SMALL_REGIME)
    if regime is not None:
        _set_regime(d, regime)
    return d


def _cfg(**kw) -> RunConfig:
    base = dict(
        strategies=(DONCHIAN,),
        slippage=0.001,
        fee_rate=0.0004,
        stop_atr_mult=2.0,
        risk_pct=0.01,
        max_leverage=100.0,
    )
    base.update(kw)
    return RunConfig(**base)


def _breakout_rows(gap_low: float | None = None) -> list[tuple[float, float, float, float]]:
    rows = [(100.0, 101.0, 99.0, 100.0)] * 30
    rows.append((100.0, 110.5, 99.5, 110.0))  # bar 30 closes above the prior 20-bar high
    rows.append((111.0, 112.0, 110.0, 111.5))  # bar 31: entry at this open
    rows.append((111.5, 112.5, 110.5, 112.0))
    if gap_low is not None:
        rows.append((gap_low, gap_low + 1, gap_low - 1, gap_low))  # gaps through the stop
    rows += [(112.0, 113.0, 111.0, 112.0)] * 5
    return rows


def test_prior_channel_excludes_current_bar():
    highs = [1, 5, 2, 3, 9, 4]
    assert ind.prior_high(highs, 3) == [None, None, None, 5, 5, 9]
    assert ind.prior_low(highs, 2) == [None, None, 1, 2, 2, 3]


def test_sma_rsi_and_percentile_rank():
    assert ind.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    up = ind.rsi([float(x) for x in range(30)], 2)
    assert up[-1] == 100.0
    ranks = ind.percentile_rank([1.0, 2.0, 3.0, 0.5], 3)
    assert ranks == [None, None, 1.0, pytest.approx(1 / 3)]


def test_adx_high_in_steady_trend_low_in_chop():
    n = 120
    trend = [100 + k for k in range(n)]
    adx_trend = ind.adx([c + 0.5 for c in trend], [c - 0.5 for c in trend], trend, 14)
    chop = [100 + (1 if k % 2 else -1) for k in range(n)]
    adx_chop = ind.adx([c + 1 for c in chop], [c - 1 for c in chop], chop, 14)
    assert adx_trend[-1] > 50
    assert adx_chop[-1] < 20
    assert adx_trend[2 * 14 - 2] is None and adx_trend[2 * 14 - 1] is not None


def test_regime_rules():
    p = RegimeParams()
    assert classify(110, 100, 25, 0.5, p) == UPTREND
    assert classify(90, 100, 25, 0.5, p) == DOWNTREND
    assert classify(110, 100, 15, 0.5, p) == SIDEWAYS
    assert classify(110, 100, 15, 0.9, p) == CHOPPY
    assert classify(110, None, 15, 0.9, p) == WARMUP


def test_entry_fills_at_next_open_with_costs():
    d = _data("ETHUSDT", _breakout_rows(), UPTREND)
    res = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP)
    tr = res["trades"][0]
    assert tr["entry_time"] == d.times[31].isoformat()
    assert tr["entry_price"] == pytest.approx(111.0 * 1.001)
    assert tr["exit_reason"] == "end_of_test"
    assert tr["exit_price"] == pytest.approx(112.0 * 0.999)
    expected_fees = (tr["entry_price"] + tr["exit_price"]) * tr["qty"] * 0.0004
    assert tr["fees"] == pytest.approx(expected_fees)
    assert tr["net_pnl"] == pytest.approx(tr["gross_pnl"] - expected_fees)
    assert res["summary"]["end_equity"] == pytest.approx(10_000 + tr["net_pnl"])


def test_risk_sizing_uses_signal_bar_atr():
    d = _data("ETHUSDT", _breakout_rows(), UPTREND)
    res = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP)
    tr = res["trades"][0]
    stop_dist = 2.0 * d.atr[30]
    assert tr["qty"] == pytest.approx(10_000 * 0.01 / stop_dist)


def test_stop_gap_fills_at_open_not_stop():
    d = _data("ETHUSDT", _breakout_rows(gap_low=90.0), UPTREND)
    res = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP)
    tr = res["trades"][0]
    assert tr["exit_reason"] == "stop"
    assert tr["exit_time"] == d.times[33].isoformat()
    assert tr["exit_price"] == pytest.approx(90.0 * 0.999)
    assert tr["r_multiple"] < -1


def test_regime_routing_blocks_donchian_outside_trend():
    d = _data("ETHUSDT", _breakout_rows(), SIDEWAYS)
    res = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP)
    assert res["trades"] == []


def test_btc_gate_blocks_alt_longs_in_btc_downtrend():
    d = _data("ETHUSDT", _breakout_rows(), UPTREND)
    btc = _data("BTCUSDT", _breakout_rows(), DOWNTREND)
    res = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP, btc=btc)
    assert res["trades"] == []
    # Bars 30 and 31 both close above the prior 20-bar high.
    assert res["counters"]["entries_blocked_by_btc_gate"] == 2
    _set_regime(btc, UPTREND)
    res = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP, btc=btc)
    assert len(res["trades"]) == 1


def test_one_position_per_symbol_and_sides():
    rng = random.Random(3)
    price = 100.0
    rows = []
    for _ in range(400):
        nxt = price * (1 + rng.uniform(-0.03, 0.03))
        rows.append((price, max(price, nxt) * 1.01, min(price, nxt) * 0.99, nxt))
        price = nxt
    d = _data("SOLUSDT", rows)
    res = run_backtest({"SOLUSDT": d}, _cfg(strategies=(DONCHIAN, BOLLINGER)), d.times[0], d.times[-1] + STEP)
    trades = res["trades"]
    for a, b in zip(trades, trades[1:]):
        assert b["entry_time"] >= a["exit_time"]
    for t in trades:
        assert t["entry_time"] < t["exit_time"] or t["exit_reason"] == "stop"


def test_no_lookahead_future_bars_do_not_change_the_past():
    rng = random.Random(11)
    price = 100.0
    rows = []
    for _ in range(500):
        nxt = price * (1 + rng.uniform(-0.025, 0.025))
        rows.append((price, max(price, nxt) * 1.005, min(price, nxt) * 0.995, nxt))
        price = nxt
    cut = 350
    altered = rows[:cut] + [(r[0] * 1.5, r[1] * 1.5, r[2] * 1.5, r[3] * 1.5) for r in rows[cut:]]
    a = _data("SOLUSDT", rows)
    b = _data("SOLUSDT", altered)
    assert a.regime[:cut] == b.regime[:cut]
    cfg = _cfg(strategies=(DONCHIAN, BOLLINGER), max_leverage=2.0)
    end = a.times[-1] + STEP
    ta = [t for t in run_backtest({"SOLUSDT": a}, cfg, a.times[0], end)["trades"] if t["exit_time"] < a.times[cut].isoformat()]
    tb = [t for t in run_backtest({"SOLUSDT": b}, cfg, b.times[0], end)["trades"] if t["exit_time"] < b.times[cut].isoformat()]
    assert ta == tb


def test_walk_forward_folds_are_contiguous_and_disjoint():
    start = datetime(2023, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, tzinfo=timezone.utc)
    plan = folds(start, end, 12, 3)
    assert plan[0]["test_start"] == datetime(2024, 6, 1, tzinfo=timezone.utc)
    for f in plan:
        assert f["train_end"] == f["test_start"]
    for a, b in zip(plan, plan[1:]):
        assert b["test_start"] == a["test_end"]
    assert plan[-1]["test_end"] == end


# --- Safeguards -------------------------------------------------------------


def _sg_cfg(**sg) -> RunConfig:
    return replace(_cfg(), safeguards=Safeguards(**sg))


def test_hysteresis_holds_trend_until_exit_threshold():
    p = RegimeParams()
    sg = Safeguards(adx_enter=25, adx_exit=18)
    adx = [20, 26, 22, 19, 17, 22]
    labels = hysteresis_labels([110] * 6, [100] * 6, adx, [0.1] * 6, p, sg)
    assert labels == [SIDEWAYS, UPTREND, UPTREND, UPTREND, SIDEWAYS, SIDEWAYS]


def test_confirm_needs_consecutive_bars():
    raw = [SIDEWAYS, UPTREND, SIDEWAYS, UPTREND, UPTREND, UPTREND]
    assert confirm(raw, 2) == [SIDEWAYS, SIDEWAYS, SIDEWAYS, SIDEWAYS, UPTREND, UPTREND]
    assert confirm(raw, 1) == raw


def test_higher_timeframe_value_only_after_its_bar_closes():
    day = timedelta(days=1)
    intraday = [T0 + STEP * k for k in range(12)]
    mapped = map_higher_timeframe(intraday, STEP, [T0, T0 + day], day, ["D0", "D1"], WARMUP)
    # The day-0 bar closes with the 6th 4h bar (index 5).
    assert mapped[:5] == [WARMUP] * 5
    assert mapped[5:11] == ["D0"] * 6
    assert mapped[11] == "D1"


def test_vote_method_produces_transition_and_half_size():
    rng = random.Random(5)
    price = 100.0
    rows = []
    for k in range(600):
        drift = 0.004 if (k // 120) % 2 == 0 else -0.004
        nxt = price * (1 + drift + rng.uniform(-0.01, 0.01))
        rows.append((price, max(price, nxt) * 1.003, min(price, nxt) * 0.997, nxt))
        price = nxt
    sg = Safeguards(regime_method="vote", transition_size=0.5)
    d = SymbolData.from_candles("X", _candles(rows), RegimeParams(atr_rank_window=50), sg)
    assert TRANSITION in d.regime
    for lab, route, size in zip(d.regime, d.route, d.size):
        if lab == TRANSITION and route != TRANSITION:
            assert route in (UPTREND, DOWNTREND) and size == 0.5
        else:
            assert size == 1.0


def test_heat_cap_limits_open_risk():
    d = _data("ETHUSDT", _breakout_rows(), UPTREND)
    res = run_backtest({"ETHUSDT": d}, _sg_cfg(heat_cap=0.004), d.times[0], d.times[-1] + STEP)
    tr = res["trades"][0]
    assert tr["qty"] * 2.0 * d.atr[30] == pytest.approx(10_000 * 0.004)
    assert res["counters"]["entries_capped_by_heat"] == 1


def test_slippage_buffer_shrinks_size():
    d = _data("ETHUSDT", _breakout_rows(), UPTREND)
    plain = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP)["trades"][0]["qty"]
    buffered = run_backtest({"ETHUSDT": d}, _sg_cfg(slippage_buffer=True), d.times[0], d.times[-1] + STEP)
    assert buffered["trades"][0]["qty"] < plain


def test_daily_loss_limit_flattens_and_blocks_rest_of_day():
    d = _data("ETHUSDT", _breakout_rows(gap_low=90.0), UPTREND)
    # 1% risk plus a gap through the stop loses well over 1% in one bar.
    res = run_backtest({"ETHUSDT": d}, _sg_cfg(daily_loss_limit=0.01), d.times[0], d.times[-1] + STEP)
    assert res["counters"]["daily_loss_limit_hits"] == 1
    assert res["counters"].get("blocked_daily_loss_limit", 0) >= 1


def test_kill_switch_stops_trading_for_good():
    d = _data("ETHUSDT", _breakout_rows(gap_low=90.0), UPTREND)
    res = run_backtest({"ETHUSDT": d}, _sg_cfg(kill_drawdown=0.01), d.times[0], d.times[-1] + STEP)
    assert res["kill_switch_at"] == d.times[33].isoformat()
    assert len(res["trades"]) == 1


def test_cooldown_after_consecutive_stops():
    rows = [(100.0, 101.0, 99.0, 100.0)] * 30
    for _ in range(4):
        rows.append((100.0, 110.5, 99.5, 110.0))  # breakout close
        rows.append((111.0, 112.0, 80.0, 85.0))  # stopped out
        rows += [(85.0, 86.0, 84.0, 85.0)] * 2
        rows += [(100.0, 101.0, 99.0, 100.0)] * 20  # rebuild the channel
    d = _data("ETHUSDT", rows, UPTREND)
    end = d.times[-1] + STEP
    base = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], end)
    cooled = run_backtest({"ETHUSDT": d}, _sg_cfg(cooldown_stops=2, cooldown_bars=40), d.times[0], end)
    assert sum(t["exit_reason"] == "stop" for t in base["trades"]) == 4
    assert len(cooled["trades"]) == 3
    assert cooled["counters"]["cooldowns_started"] >= 1


def test_max_positions_cap():
    a = _data("ETHUSDT", _breakout_rows(), UPTREND)
    b = _data("SOLUSDT", _breakout_rows(), UPTREND)
    res = run_backtest({"ETHUSDT": a, "SOLUSDT": b}, _sg_cfg(max_positions=1), a.times[0], a.times[-1] + STEP)
    assert len(res["trades"]) == 1
    assert res["counters"]["skipped_max_positions"] >= 1


def test_dip_confirm_waits_for_turn():
    rows = [(100.0, 101.0, 99.0, 100.0 + (0.5 if k % 2 else -0.5)) for k in range(40)]
    rows += [(100.0, 100.5, 96.5, 97.0), (97.0, 97.5, 93.5, 94.0)]  # sharp dip: RSI(2) collapses
    rows += [(94.0, 95.0, 93.0, 94.5)]  # small bounce, EMA(5) still falling
    rows += [(94.5, 99.0, 94.0, 98.5)] * 6  # close above the prior high
    d = _data("ETHUSDT", rows, SIDEWAYS)
    end = d.times[-1] + STEP
    base = run_backtest({"ETHUSDT": d}, _cfg(strategies=(RSI2,)), d.times[0], end)
    conf_cfg = replace(_cfg(strategies=(RSI2,)), safeguards=Safeguards(dip_confirm=True))
    conf = run_backtest({"ETHUSDT": d}, conf_cfg, d.times[0], end)
    first_base = [t for t in base["trades"] if t["side"] == LONG][0]
    first_conf = [t for t in conf["trades"] if t["side"] == LONG][0]
    assert first_conf["entry_time"] > first_base["entry_time"]


def test_crash_filter_pauses_after_huge_range_bar():
    rows = _breakout_rows()
    rows[30] = (100.0, 130.0, 99.5, 110.0)  # the breakout bar spans many ATRs
    d = _data("ETHUSDT", rows, UPTREND)
    res = run_backtest({"ETHUSDT": d}, _sg_cfg(crash_filter=True, crash_pause_bars=3), d.times[0], d.times[-1] + STEP)
    assert res["counters"]["coin_crash_pauses"] >= 1
    assert all(t["entry_time"] != d.times[31].isoformat() for t in res["trades"])


def test_plateau_prefers_broad_zone_over_spike():
    grid = [{"a": 1, "b": 1}, {"a": 1, "b": 2}, {"a": 2, "b": 1}, {"a": 2, "b": 2}]
    plat = plateau_scores(grid, [10.0, -5.0, -5.0, 3.0])
    assert plat[0] == pytest.approx(0.0)
    assert plat[3] == pytest.approx(-7 / 3)
    # Neighbours under the train trade minimum count as 0.
    plat2 = plateau_scores(grid, [10.0, None, None, 3.0])
    assert plat2[1] is None
    assert plat2[0] == pytest.approx(10 / 3)


def test_monte_carlo_percentiles_are_ordered():
    rng = random.Random(2)
    trades = [
        Trade(
            symbol="X", strategy="d", side=LONG, regime=UPTREND,
            entry_time="2024-01-01", exit_time=f"2024-02-01T{k:03d}",
            entry_price=1, exit_price=1, qty=1, notional=1, gross_pnl=0, fees=0, slippage_cost=0,
            net_pnl=rng.gauss(5, 50), r_multiple=0, bars_held=1, exit_reason="x",
        )
        for k in range(60)
    ]
    mc = monte_carlo(trades, 10_000, runs=200)
    dd = mc["shuffle_max_dd_pct"]
    assert dd["p50"] <= dd["p95"] <= dd["p99"]
    r = mc["bootstrap_return_pct"]
    assert r["p5"] <= r["p50"] <= r["p95"]
    assert monte_carlo(trades[:5], 10_000) is None


# --- Fix-study flags ---------------------------------------------------------


def _p_cfg(strategies=(DONCHIAN,), **params) -> RunConfig:
    base = _cfg(strategies=strategies)
    return replace(base, params=replace(base.params, **params))


def _retest_rows() -> list[tuple[float, float, float, float]]:
    rows = [(100.0, 101.0, 99.0, 100.0)] * 30
    rows.append((100.0, 110.5, 99.5, 110.0))  # bar 30: breakout above 101; midpoint 105
    rows.append((111.0, 112.0, 110.0, 111.5))  # bar 31: no pullback
    rows.append((111.0, 111.5, 100.5, 104.0))  # bar 32: dips to 101, closes back above it
    rows.append((104.5, 106.0, 104.0, 105.5))  # bar 33: dips to 105, closes back above it
    rows += [(105.5, 106.5, 105.0, 106.0)] * 5
    return rows


def test_retest_level_waits_for_pullback_to_breakout_level():
    d = _data("ETHUSDT", _retest_rows(), UPTREND)
    end = d.times[-1] + STEP
    plain = run_backtest({"ETHUSDT": d}, _p_cfg(), d.times[0], end)["trades"][0]
    level = run_backtest({"ETHUSDT": d}, _p_cfg(donchian_entry_mode="retest_level"), d.times[0], end)["trades"][0]
    mid = run_backtest({"ETHUSDT": d}, _p_cfg(donchian_entry_mode="retest_mid"), d.times[0], end)["trades"][0]
    assert plain["entry_time"] == d.times[31].isoformat()
    assert level["entry_time"] == d.times[33].isoformat()
    assert level["entry_price"] == pytest.approx(104.5 * 1.001)
    assert mid["entry_time"] == d.times[34].isoformat()


def test_retest_expires():
    rows = _retest_rows()[:32] + [(112.0, 113.0, 111.5, 112.5)] * 10  # never pulls back
    d = _data("ETHUSDT", rows, UPTREND)
    res = run_backtest({"ETHUSDT": d}, _p_cfg(donchian_entry_mode="retest_level", retest_bars=3), d.times[0], d.times[-1] + STEP)
    assert res["trades"] == []
    assert res["counters"]["retest_entries_expired"] >= 1


def _breakdown_rows() -> list[tuple[float, float, float, float]]:
    rows = [(100.0, 101.0, 99.0, 100.0)] * 60
    rows.append((100.0, 100.5, 89.5, 90.0))  # bar 60 closes below the prior 20- and 55-bar lows
    rows += [(89.0, 90.0, 88.0, 89.0)] * 5
    return rows


def test_donchian_shorts_flag_and_short_filter():
    d = _data("ETHUSDT", _breakdown_rows(), DOWNTREND)
    end = d.times[-1] + STEP
    assert run_backtest({"ETHUSDT": d}, _p_cfg(), d.times[0], end)["trades"][0]["side"] == "SHORT"
    assert run_backtest({"ETHUSDT": d}, _p_cfg(donchian_shorts=False), d.times[0], end)["trades"] == []
    d.adx = [30.0] * len(d.times)
    d.trend_sma = [200.0] * len(d.times)
    assert len(run_backtest({"ETHUSDT": d}, _p_cfg(donchian_short_filter=True), d.times[0], end)["trades"]) == 1
    d.adx = [20.0] * len(d.times)  # ADX too weak
    assert run_backtest({"ETHUSDT": d}, _p_cfg(donchian_short_filter=True), d.times[0], end)["trades"] == []
    d.adx = [30.0] * len(d.times)
    d.trend_sma = [50.0] * len(d.times)  # price above the 200 SMA
    assert run_backtest({"ETHUSDT": d}, _p_cfg(donchian_short_filter=True), d.times[0], end)["trades"] == []


def _walk(seed: int, n: int, step: float = 0.025) -> list[tuple[float, float, float, float]]:
    rng = random.Random(seed)
    price = 100.0
    rows = []
    for _ in range(n):
        nxt = price * (1 + rng.uniform(-step, step))
        rows.append((price, max(price, nxt) * 1.005, min(price, nxt) * 0.995, nxt))
        price = nxt
    return rows


def test_rsi_exit_mode_exits_on_rsi_level():
    d = _data("SOLUSDT", _walk(4, 400), SIDEWAYS)
    res = run_backtest(
        {"SOLUSDT": d}, _p_cfg(strategies=(RSI2,), rsi_exit_mode="rsi"), d.times[0], d.times[-1] + STEP
    )
    rsi_exits = [t for t in res["trades"] if t["exit_reason"] == "rsi_exit"]
    assert rsi_exits and not any(t["exit_reason"] == "sma_exit" for t in res["trades"])
    r = d.rsi(2)
    for t in rsi_exits:
        signal = d.index[datetime.fromisoformat(t["exit_time"])] - 1
        assert r[signal] > 70 if t["side"] == LONG else r[signal] < 30


def test_symbol_slippage_override():
    d = _data("ETHUSDT", _breakout_rows(), UPTREND)
    cfg = replace(_cfg(), symbol_slippage={"ETHUSDT": 0.003})
    tr = run_backtest({"ETHUSDT": d}, cfg, d.times[0], d.times[-1] + STEP)["trades"][0]
    assert tr["entry_price"] == pytest.approx(111.0 * 1.003)
    assert tr["exit_price"] == pytest.approx(112.0 * 0.997)


def test_daily_loss_realized_basis_ignores_giveback_of_open_profit():
    rows = [(100.0, 101.0, 99.0, 100.0)] * 30
    rows.append((100.0, 110.5, 99.5, 110.0))
    for c in (115.0, 120.0, 125.0, 128.0, 130.0):  # bars 31-35 run up
        rows.append((c - 3, c + 0.5, c - 3.5, c))
    rows.append((130.0, 130.5, 114.5, 115.0))  # bar 36, the next UTC day: gives back most of it
    rows += [(115.0, 116.0, 114.0, 115.0)] * 3
    d = _data("ETHUSDT", rows, UPTREND)
    end = d.times[-1] + STEP
    eq = run_backtest({"ETHUSDT": d}, _sg_cfg(daily_loss_limit=0.01), d.times[0], end)
    real = run_backtest({"ETHUSDT": d}, _sg_cfg(daily_loss_limit=0.01, daily_loss_basis="realized"), d.times[0], end)
    assert eq["counters"].get("daily_loss_limit_hits") == 1
    assert eq["trades"][0]["exit_reason"] == "daily_loss_limit" and eq["trades"][0]["net_pnl"] > 0
    assert "daily_loss_limit_hits" not in real["counters"]


def test_report_metrics():
    rows = _breakout_rows()[:32] + [(90.0, 91.0, 89.0, 90.0)] * 5  # stopped on bar 32, 2 bars held
    d = _data("ETHUSDT", rows, UPTREND)
    s = run_backtest({"ETHUSDT": d}, _cfg(), d.times[0], d.times[-1] + STEP)["summary"]
    assert s["quick_stops"] == 1
    assert s["max_losing_streak"] == 1
    assert s["pnl_ex_top5"] == pytest.approx(s["net_pnl"], abs=1e-6)
    assert s["max_drawdown_usd"] > 0


def test_symbol_on_its_own_daily_timeframe_fills_at_next_daily_open():
    day = timedelta(days=1)
    daily = [
        {"time": T0 + day * k, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}
        for k, (o, h, l, c) in enumerate(_breakout_rows())
    ]
    btc = SymbolData.from_candles("BTCUSDT", daily, SMALL_REGIME)
    _set_regime(btc, UPTREND)
    btc.bar_delta = day
    eth = _data("ETHUSDT", [(100.0, 101.0, 99.0, 100.0)] * (len(daily) * 6), SIDEWAYS)
    res = run_backtest({"BTCUSDT": btc, "ETHUSDT": eth}, _cfg(btc_gate=False), T0, btc.times[-1] + day)
    tr = res["trades"][0]
    assert tr["symbol"] == "BTCUSDT"
    assert tr["entry_time"] == (T0 + day * 31).isoformat()


def test_no_lookahead_with_every_new_flag_on():
    rows = _walk(11, 600)
    cut = 450
    altered = rows[:cut] + [(r[0] * 1.5, r[1] * 1.5, r[2] * 1.5, r[3] * 1.5) for r in rows[cut:]]
    sg = Safeguards(
        regime_method="vote", confirm_bars=2, transition_size=0.0, chop_filter=True, cooldown_stops=2,
        crash_filter=True, dip_confirm=True, slippage_buffer=True, daily_loss_limit=0.02,
        daily_loss_basis="realized", heat_cap=0.04, perf_cut=0.5,
    )
    params = dict(
        rsi_exit_mode="rsi", donchian_short_filter=True, donchian_entry_mode="retest_mid",
    )
    a = SymbolData.from_candles("SOLUSDT", _candles(rows), RegimeParams(atr_rank_window=50), sg)
    b = SymbolData.from_candles("SOLUSDT", _candles(altered), RegimeParams(atr_rank_window=50), sg)
    assert a.regime[:cut] == b.regime[:cut]
    base = RunConfig(strategies=(DONCHIAN, RSI2, BOLLINGER), safeguards=sg, symbol_slippage={"SOLUSDT": 0.0015})
    cfg = replace(base, params=replace(base.params, **params))
    end = a.times[-1] + STEP
    cut_iso = a.times[cut].isoformat()
    ta = [t for t in run_backtest({"SOLUSDT": a}, cfg, a.times[0], end)["trades"] if t["exit_time"] < cut_iso]
    tb = [t for t in run_backtest({"SOLUSDT": b}, cfg, b.times[0], end)["trades"] if t["exit_time"] < cut_iso]
    assert ta and ta == tb
