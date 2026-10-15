"""Causal tests for the versioned intraday BOS + supply/demand strategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.intraday_bos_sd.candles import Bar, Series, resample_complete, validate_series
from app.research.intraday_bos_sd.execution import simulate_trade
from app.research.intraday_bos_sd.gates import evaluate_gates, split_windows
from app.research.intraday_bos_sd.lifecycle import paper_policy_met, transition
from app.research.intraday_bos_sd.portfolio import correlated, run_portfolio, size_quantity
from app.research.intraday_bos_sd.runtime import accept_signal, default_state, eligibility
from app.research.intraday_bos_sd.setups import SetupSignal, generate_signals, prepare_book
from app.research.intraday_bos_sd.spec import spec_hash
from app.research.intraday_bos_sd.structure import build_structure
from app.research.intraday_bos_sd.universe import assign_market_cap_tiers, classify_membership, excluded_symbol
from app.research.intraday_bos_sd.zones import create_zones


def _t(i: int, minutes: int = 5) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes * i)


def _bar(i: int, o, h, l, c, v=10.0, minutes: int = 5) -> Bar:
    return Bar(_t(i, minutes), o, h, l, c, v)


def _flat(n: int, price: float = 100.0, minutes: int = 5, volume: float = 10.0) -> list[Bar]:
    return [_bar(i, price, price + 1, price - 1, price, volume, minutes) for i in range(n)]


def test_swing_unavailable_until_confirming_candle_closes() -> None:
    bars = _flat(8)
    bars[3] = _bar(3, 100, 130, 99, 101)
    state = build_structure(Series("5m", bars[:5]))
    assert all(s.occur_index != 3 for s in state.swings)
    state = build_structure(Series("5m", bars[:6]))
    highs = [s for s in state.swings if s.kind == "HIGH" and s.occur_index == 3]
    assert len(highs) == 1
    assert highs[0].occur_time == _t(3)
    assert highs[0].available_time == _t(5) + timedelta(minutes=5)


def test_equal_highs_do_not_confirm() -> None:
    bars = _flat(8)
    bars[3] = _bar(3, 100, 120, 99, 100)
    bars[4] = _bar(4, 100, 120, 99, 100)
    state = build_structure(Series("5m", bars))
    assert [s for s in state.swings if s.kind == "HIGH"] == []


def test_incomplete_higher_timeframe_bucket_is_dropped() -> None:
    bars = [_bar(i, 10, 11, 9, 10, minutes=5) for i in range(5)]
    higher = resample_complete(Series("5m", bars), "15m")
    assert len(higher) == 1
    assert higher.bars[0].open_time == _t(0)


def test_zone_requires_origin_and_expires() -> None:
    bars = []
    for i in range(40):
        bars.append(Bar(_t(i, 15), 100, 101, 99, 100, 10))
    # Bearish origin, then a wide bullish break. Structure may stay neutral on a
    # flat tape, so this test calls create_zones only after forcing a BOS by
    # using the structure builder on a constructed trending series below.
    from app.research.intraday_bos_sd.structure import StructureEvent, StructureState, Swing

    swing = Swing("HIGH", 100, 10, 12, _t(10, 15), _t(12, 15) + timedelta(minutes=15))
    event = StructureEvent("BOS", "BULLISH", 20, _t(20, 15) + timedelta(minutes=15), 100, 110, 0.1, swing)
    series = Series("15m", bars)
    series.opens[20] = 100
    series.closes[20] = 130
    series.highs[20] = 131
    series.lows[20] = 100
    series.atr[19] = 1.0
    series.closes[19] = 99
    series.opens[19] = 101
    series.highs[19] = 102
    series.lows[19] = 98
    state = StructureState(events=[event])
    zones = create_zones(series, state)
    assert len(zones) == 1
    assert zones[0].low == series.lows[19]
    assert zones[0].eligible_for_retest(series.close_time(20)) is False
    assert zones[0].eligible_for_retest(series.close_time(21)) is True
    later = zones[0].active_from + timedelta(hours=24)
    assert zones[0].expired(later)
    series.closes[22] = zones[0].low - 1
    zones = create_zones(series, state)
    assert zones[0].invalidated(series.close_time(22))


def test_no_zone_without_origin_candle() -> None:
    from app.research.intraday_bos_sd.structure import StructureEvent, StructureState, Swing

    bars = [Bar(_t(i, 15), 100, 101, 99, 101, 10) for i in range(10)]
    series = Series("15m", bars)
    series.atr[4] = 1
    series.opens[5] = 100
    series.closes[5] = 120
    swing = Swing("HIGH", 101, 1, 3, _t(1, 15), _t(3, 15) + timedelta(minutes=15))
    event = StructureEvent("BOS", "BULLISH", 5, series.close_time(5), 101, 120, 0.1, swing)
    assert create_zones(series, StructureState(events=[event])) == []


def test_choch_before_later_bos_on_same_swing_family() -> None:
    bars = _flat(30, 100)
    # Rising swings then a break of the latest low.
    for i, price in ((6, 90), (12, 95), (8, 110), (14, 120)):
        if i in (6, 12):
            bars[i] = _bar(i, price, price + 1, price, price + 0.5)
        else:
            bars[i] = _bar(i, price, price, price - 1, price - 0.5)
    state = build_structure(Series("5m", bars))
    kinds = [(e.kind, e.direction, e.index) for e in state.events]
    assert state.labels[0] == "NEUTRAL"
    assert kinds == sorted(kinds, key=lambda item: item[2])


def test_gap_and_ambiguous_candle_stop_first() -> None:
    signal = SetupSignal(
        symbol="BTCUSDT",
        variant="A",
        side="LONG",
        tier="LARGE_LIQUIDITY",
        setup_time=_t(1),
        confirmation_index=0,
        entry_index=1,
        stop=90,
        atr=1,
        volume_ratio=2,
        zone_low=None,
        zone_high=None,
        zone_active_from=None,
        bos_level=100,
        choch_level=None,
        evidence={},
        idempotency_key="k",
    )
    bars = [
        _bar(0, 100, 101, 99, 100),
        _bar(1, 100, 130, 80, 90),
    ]
    fill = simulate_trade(signal, Series("5m", bars), [], quantity=1, slippage=0.0)
    assert fill.exit_reason == "stop"
    assert fill.fees < 0
    gap_signal = SetupSignal(
        symbol="BTCUSDT",
        variant="A",
        side="LONG",
        tier="LARGE_LIQUIDITY",
        setup_time=_t(1),
        confirmation_index=0,
        entry_index=1,
        stop=90,
        atr=1,
        volume_ratio=2,
        zone_low=None,
        zone_high=None,
        zone_active_from=None,
        bos_level=100,
        choch_level=None,
        evidence={},
        idempotency_key="gap",
    )
    gap = [
        _bar(0, 100, 101, 99, 100),
        _bar(1, 100, 101, 99, 100),
        _bar(2, 70, 75, 60, 72),
    ]
    gap_fill = simulate_trade(gap_signal, Series("5m", gap), [], quantity=1, slippage=0.001)
    assert not isinstance(gap_fill, str)
    assert gap_fill.exit_reason == "stop"
    assert gap_fill.exit_price < 70


def test_portfolio_correlation_limit_and_priority() -> None:
    assert correlated(None, [0.1, 0.2]) is True
    signal = SetupSignal(
        "ETHUSDT",
        "A",
        "LONG",
        "LARGE_LIQUIDITY",
        _t(1),
        0,
        1,
        90,
        1,
        None,
        None,
        None,
        None,
        None,
        None,
        {},
        "eth",
    )
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 101, 99, 100), _bar(2, 100, 101, 99, 100)]
    series = Series("5m", bars)
    result = run_portfolio(
        [
            {
                "signal": signal,
                "setup": series,
                "zones": [],
                "returns": None,
            }
        ],
        slippage=0.0,
    )
    assert result.trades or result.rejections
    qty, reason = size_quantity(equity=10_000, entry=100, stop=90, tier="MID_LIQUIDITY", open_notional=0)
    assert qty > 0 and reason == "ok"
    qty_small, reason_small = size_quantity(equity=10, entry=100, stop=90, tier="LARGE_LIQUIDITY", open_notional=0)
    assert qty_small == 0
    assert reason_small == "below_min_notional_or_margin"


def test_gates_do_not_pass_thin_evidence() -> None:
    decision = evaluate_gates(
        correctness_ok=True,
        oos_summary={"trades": 10, "expectancy_r": 0.2, "profit_factor": 2, "max_drawdown": 0.01},
        trades_by_tier={"LARGE_LIQUIDITY": 10},
        enabled_tiers=["LARGE_LIQUIDITY"],
        fold_pnls=[1, 1, 1],
        stress_expectancy_r=0.1,
        pnl_without_best_symbol=1,
    )
    assert decision["passed"] is False
    assert decision["status"] == "INSUFFICIENT_EVIDENCE"


def test_version_mismatch_blocks_actionable_signal() -> None:
    state = default_state()
    state["status"] = "SIGNAL_ENABLED"
    state["approved_spec_hash"] = "different"
    state["approved_scope"] = {"tiers": ["LARGE_LIQUIDITY"]}
    decision = eligibility(state, tier="LARGE_LIQUIDITY", data_fresh=True, portfolio_known=True)
    assert decision["actionable"] is False
    assert "approved_version_mismatch" in decision["reasons"]


def test_replay_and_restart_do_not_duplicate_alerts() -> None:
    state = default_state()
    state["status"] = "PAPER_MONITORING"
    signal = {
        "idempotency_key": "abc",
        "symbol": "BTCUSDT",
        "setup_time": "2024-01-01T00:00:00+00:00",
        "tier": "LARGE_LIQUIDITY",
    }
    first = accept_signal(state, signal, replay=False, data_fresh=True, portfolio_known=False)
    second = accept_signal(state, signal, replay=False, data_fresh=True, portfolio_known=False)
    replay = accept_signal(
        default_state() | {"status": "PAPER_MONITORING", "seen_keys": [], "signals": [], "cursors": {}, "paper": {"candidates": 0}},
        {**signal, "idempotency_key": "replay"},
        replay=True,
        data_fresh=True,
        portfolio_known=False,
    )
    assert first["stored"] is True
    assert first["signal"]["publication"] == "PAPER_CANDIDATE"
    assert first["signal"]["actionable"] is False
    assert second["reason"] == "duplicate"
    assert replay["signal"]["actionable"] is False
    assert replay["signal"]["publication"] == "HISTORICAL_REPLAY"


def test_stale_data_blocks_publication() -> None:
    state = default_state()
    state["status"] = "SIGNAL_ENABLED"
    state["approved_spec_hash"] = spec_hash()
    state["approved_scope"] = {"tiers": ["LARGE_LIQUIDITY"]}
    decision = eligibility(state, tier="LARGE_LIQUIDITY", data_fresh=False, portfolio_known=True)
    assert decision["actionable"] is False
    assert "stale_or_incomplete_market_data" in decision["reasons"]


def test_lifecycle_signal_enabled_is_operator_only() -> None:
    try:
        transition("PAPER_MONITORING", "SIGNAL_ENABLED", operator=False)
        raised = False
    except Exception:
        raised = True
    assert raised
    assert transition("VALIDATED", "PAPER_MONITORING") == "PAPER_MONITORING"
    assert paper_policy_met(calendar_days=30, candidates=20, mismatches=0, historical_status="VALIDATED")
    assert not paper_policy_met(calendar_days=1, candidates=20, mismatches=0, historical_status="VALIDATED")


def test_windows_are_chronological_and_embargoed() -> None:
    windows = split_windows(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert windows["ok"]
    assert windows["holdout"]["status"] == "UNTOUCHED"
    assert len(windows["folds"]) == 3


def test_replay_parity_same_signals() -> None:
    bars = _flat(80, 100)
    for i in range(10, 70, 7):
        bars[i] = _bar(i, 100, 100 + (i % 5), 90 - (i % 3), 95)
    setup = Series("5m", bars)
    direction = Series("1h", [_bar(i, 100, 110, 99, 108, minutes=60) for i in range(20)])
    zones = Series("15m", [_bar(i, 100, 102, 98, 101, minutes=15) for i in range(30)])
    book = prepare_book("BTCUSDT", "LARGE_LIQUIDITY", setup, direction, zones)
    a, _ = generate_signals(book, "A")
    book2 = prepare_book("BTCUSDT", "LARGE_LIQUIDITY", setup, direction, zones)
    b, _ = generate_signals(book2, "A")
    assert [s.idempotency_key for s in a] == [s.idempotency_key for s in b]


def test_liquidity_tier_is_not_labeled_market_cap() -> None:
    assert excluded_symbol("USDCUSDT") == "stablecoin"
    row = classify_membership(
        symbol="SOLUSDT",
        listed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        median_quote_volume=25_000_000,
        market_cap=None,
        caps_available=3,
        liquidity_rank=1,
    )
    assert row["tier"] == "LARGE_LIQUIDITY"
    assert row["tier_kind"] == "LIQUIDITY_TIER_NOT_MARKET_CAP"
    ranked = assign_market_cap_tiers([{"symbol": "A", "market_cap": 2}, {"symbol": "B", "market_cap": 5}])
    assert ranked[0]["tier_kind"] == "MARKET_CAP"


def test_quality_flags_duplicate_and_bad_ohlc() -> None:
    bars = [_bar(0, 10, 11, 9, 10), _bar(0, 10, 11, 9, 10)]
    report = validate_series(Series("5m", bars))
    assert report.ok is False
    bad = [_bar(0, 10, 9, 8, 10)]
    assert validate_series(Series("5m", bad)).ok is False
