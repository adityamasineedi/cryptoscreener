"""Golden grid fills, input rejection, and the disabled-by-default routes."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.grid_trading.api import router
from app.grid_trading.config import GridConfig, GridConfigError
from app.grid_trading.engine import run_grid
from app.grid_trading.execution import GridExecution
from app.grid_trading.levels import build_levels


def _config(**overrides) -> GridConfig:
    payload = {
        "range_low": 100.0,
        "range_high": 110.0,
        "grid_count": 1,
        "capital": 1000.0,
        "max_capital": 5000.0,
        "fee_bps": 0.0,
        # These cases price every fill at fee_bps: the taker falls back to it (D207 default is 5).
        "taker_fee_bps": None,
        "slippage_bps": 0.0,
        "stop_price": 90.0,
        "max_drawdown": 0.9,
        "max_open_orders": 5,
        "intrabar_path": "buy_then_sell",
    }
    payload.update(overrides)
    return GridConfig(**payload)


def _bar(time: int, open_: float, high: float, low: float, close: float) -> dict:
    return {"time": time, "open": open_, "high": high, "low": low, "close": close}


def test_arithmetic_and_geometric_levels():
    arithmetic = build_levels(_config(grid_count=2, range_high=130.0))
    assert arithmetic == pytest.approx([100.0, 115.0, 130.0])
    geometric = build_levels(_config(grid_type="geometric", grid_count=2, range_high=121.0))
    assert geometric[0] == pytest.approx(100.0)
    assert geometric[-1] == pytest.approx(121.0)
    assert geometric[1] == pytest.approx(110.0)


def test_input_rejections():
    cases = [
        {"range_low": 110.0, "range_high": 100.0},
        {"grid_count": 0},
        {"capital": 0},
        {"capital": 9000.0},
        {"fee_bps": 500.0, "slippage_bps": 0.0},
        {"leverage": 0.5},
        {"leverage": 6.0},
        {"stop_price": 100.0},
        {"max_drawdown": 0},
        {"max_open_orders": 0, "grid_count": 1},
    ]
    for overrides in cases:
        cfg = _config(**overrides)
        with pytest.raises(GridConfigError):
            run_grid([_bar(0, 105, 106, 104, 105), _bar(1, 105, 106, 104, 105)], cfg)


def test_limit_fills_use_the_maker_fee_and_exits_use_taker_plus_slippage():
    maker = 0.001
    taker = 0.002
    slip = 0.001
    cfg = _config(
        fee_bps=10.0,
        taker_fee_bps=20.0,
        slippage_bps=10.0,
        intrabar_path="buy_then_sell",
    )
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 112, 99, 108),
    ]
    result = run_grid(candles, cfg)
    buy_px = 100.0
    qty = 1000.0 / (buy_px * (1.0 + maker))
    sell_px = 110.0
    credit = qty * sell_px * (1.0 - maker)
    assert result["fills"][0]["price"] == pytest.approx(buy_px)
    assert result["fills"][0]["fee"] == pytest.approx(qty * buy_px * maker)
    assert result["fills"][1]["price"] == pytest.approx(sell_px)
    assert result["fills"][1]["fee"] == pytest.approx(qty * sell_px * maker)
    assert result["liquidated_equity"] == pytest.approx(credit)
    assert result["fees_paid"] == pytest.approx(result["fills"][0]["fee"] + result["fills"][1]["fee"])
    assert result["funding_included"] is False
    assert result["funding_status"] == "unavailable"
    assert result["net_pnl"] is None
    assert "Funding is unavailable" in result["warnings"][0]

    stopped = run_grid(
        [
            _bar(0, 105, 106, 104, 105),
            _bar(1, 105, 106, 99, 101),
            _bar(2, 101, 102, 89, 95),
        ],
        cfg,
    )
    exit_px = 90.0 * (1.0 - slip)
    exit_fee = qty * exit_px * taker
    exit_credit = qty * exit_px - exit_fee
    assert stopped["halted"] == "stop"
    assert stopped["fills"][-1]["reason"] == "stop"
    assert stopped["fills"][-1]["price"] == pytest.approx(exit_px)
    assert stopped["fills"][-1]["fee"] == pytest.approx(exit_fee)
    assert stopped["realized_pnl"] == pytest.approx(exit_credit - qty * buy_px * (1.0 + maker))
    assert stopped["unrealized_pnl"] == pytest.approx(0.0)


def test_worst_path_keeps_the_lower_close_equity():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 111, 99, 100),
    ]
    optimistic = run_grid(candles, _config(intrabar_path="buy_then_sell"))
    pessimistic = run_grid(candles, _config(intrabar_path="worst"))
    assert optimistic["liquidated_equity"] == pytest.approx(1100.0)
    assert pessimistic["paths"][0]["path"] == "sell_then_buy"
    assert pessimistic["base"] == pytest.approx(10.0)
    assert pessimistic["equity"] == pytest.approx(1000.0)
    assert pessimistic["liquidated_equity"] < optimistic["liquidated_equity"]


def test_prefix_fills_match_the_full_run():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 104),
        _bar(2, 104, 112, 103, 111),
        _bar(3, 111, 112, 108, 109),
    ]
    full = run_grid(candles, _config())
    prefix = run_grid(candles[:3], _config())
    assert prefix["fills"] == [fill for fill in full["fills"] if fill["time"] <= 2]


def test_stop_flattens_and_halts():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
        _bar(2, 101, 102, 89, 95),
        _bar(3, 95, 112, 94, 110),
    ]
    result = run_grid(candles, _config())
    assert result["halted"] == "stop"
    assert result["base"] == pytest.approx(0.0)
    assert all(fill["time"] != 3 for fill in result["fills"])


def test_max_drawdown_rejects_the_run():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
        _bar(2, 101, 101, 80, 80),
    ]
    result = run_grid(candles, _config(max_drawdown=0.05, stop_price=50.0))
    assert result["accepted"] is False
    assert result["error"] == "max_drawdown"
    assert result["halted"] == "max_drawdown"


def test_buy_and_hold_uses_the_same_fee_model():
    candles = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 121, 100, 120)]
    cfg = _config(fee_bps=10.0, slippage_bps=10.0, range_low=90.0, range_high=130.0, stop_price=80.0)
    result = run_grid(candles, cfg)
    entry = 100.0 * 1.001
    qty = 1000.0 / (entry * 1.001)
    credit = qty * 120.0 * 0.999 * 0.999
    assert result["buy_and_hold"]["equity"] == pytest.approx(credit)


@pytest.mark.asyncio
async def test_live_submit_raises_after_the_rate_limiter():
    execution = GridExecution()
    with pytest.raises(GridConfigError) as caught:
        await execution.submit_live({"symbol": "BTCUSDT"})
    assert caught.value.code == "live_disabled"
    assert execution.limiter.stats.acquired == 1


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_routes_are_404_when_disabled(monkeypatch):
    monkeypatch.setenv("GRID_TRADING_ENABLED", "false")
    client = _client()
    assert client.get("/api/grid-trading/status").status_code == 404
    assert client.post("/api/grid-trading/backtest", json={}).status_code == 404


def test_backtest_route_labels_futures_and_rejects_a_bad_range(monkeypatch):
    monkeypatch.setenv("GRID_TRADING_ENABLED", "true")
    client = _client()
    status = client.get("/api/grid-trading/status")
    assert status.status_code == 200
    assert status.json()["market"] == "binance_usdt_m_futures"
    assert status.json()["enabled"] is True
    body = {
        "range_low": 100,
        "range_high": 110,
        "grid_count": 1,
        "capital": 1000,
        "stop_price": 90,
        "fee_bps": 0,
        "slippage_bps": 0,
        "intrabar_path": "buy_then_sell",
        "candles": [
            _bar(0, 105, 106, 104, 105),
            _bar(1, 105, 112, 99, 108),
        ],
    }
    ok = client.post("/api/grid-trading/backtest", json=body)
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["summary"]["market"] == "binance_usdt_m_futures"
    assert payload["summary"]["fill_count"] == 2
    bad = client.post("/api/grid-trading/backtest", json={**body, "stop_price": 100})
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "stop"
    live = client.post("/api/grid-trading/live", json={"symbol": "BTCUSDT"})
    assert live.status_code == 422
    assert live.json()["detail"]["code"] == "live_disabled"


def test_open_inventory_can_show_a_loss_while_a_cycle_is_profitable():
    """Two slots. The upper slot completes; the lower slot is marked under its cost."""
    cfg = _config(grid_count=2, capital=1000.0, max_open_orders=5, intrabar_path="buy_then_sell")
    candles = [
        _bar(0, 108, 109, 107, 108),
        _bar(1, 108, 108, 99, 102),
    ]
    result = run_grid(candles, cfg)
    qty_upper = 5.0
    realized = qty_upper * 105.0 - 500.0
    unrealized = (500.0 / 105.0) * 102.0 - 500.0
    assert realized == pytest.approx(25.0)
    assert unrealized == pytest.approx(-1500.0 / 105.0)
    assert result["realized_pnl"] == pytest.approx(realized)
    assert result["unrealized_pnl"] == pytest.approx(unrealized)
    assert result["funding_status"] == "unavailable"
    assert result["net_pnl"] is None
    assert result["pnl_before_funding"] == pytest.approx(realized + unrealized)
    assert result["realized_pnl"] > 0 > result["unrealized_pnl"]
    assert result["avg_profit_per_grid_cycle"] == pytest.approx(realized)
    assert result["cycle_count"] == 1
    assert result["pct_time_in_range"] == pytest.approx(1.0)
    assert any(event["type"] == "range_exit" and event["time"] == 1 for event in result["events"])


def test_fee_larger_than_grid_spacing_is_rejected():
    cfg = _config(fee_bps=500.0, slippage_bps=0.0)
    with pytest.raises(GridConfigError) as caught:
        run_grid([_bar(0, 105, 106, 104, 105), _bar(1, 105, 112, 99, 108)], cfg)
    assert caught.value.code == "spacing"


def test_one_bar_can_gap_through_several_levels():
    cfg = _config(
        range_low=100.0,
        range_high=130.0,
        grid_count=3,
        capital=1200.0,
        stop_price=90.0,
        max_open_orders=5,
        intrabar_path="buy_then_sell",
    )
    candles = [
        _bar(0, 125, 126, 124, 125),
        _bar(1, 125, 125, 99, 104),
    ]
    result = run_grid(candles, cfg)
    assert [fill["side"] for fill in result["fills"]] == ["buy", "buy", "buy", "sell", "sell"]
    assert result["cycle_count"] == 2
    assert result["base"] == pytest.approx(400.0 / 120.0)
    realized = 4.0 * 10.0 + (400.0 / 110.0) * 120.0 - 400.0
    unrealized = (400.0 / 120.0) * 104.0 - 400.0
    assert result["realized_pnl"] == pytest.approx(realized)
    assert result["unrealized_pnl"] == pytest.approx(unrealized)
    assert result["fill_rate"] == pytest.approx(1.0)


def test_stop_and_range_exit_are_timestamped_and_halt_the_book():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
        _bar(2, 101, 102, 89, 95),
        _bar(3, 95, 112, 94, 110),
    ]
    result = run_grid(candles, _config())
    kinds = [event["type"] for event in result["events"]]
    assert "stop" in kinds
    assert "range_exit" in kinds
    assert result["events"][kinds.index("stop")]["time"] == 2
    assert all(fill["time"] != 3 for fill in result["fills"])
    assert result["base"] == pytest.approx(0.0)
    assert result["unrealized_pnl"] == pytest.approx(0.0)


def test_pause_marks_to_market_and_takes_no_fills():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 112, 99, 108),
    ]
    result = run_grid(candles, _config(paused=True))
    assert result["halted"] == "paused"
    assert result["fill_count"] == 0
    assert result["base"] == pytest.approx(0.0)
    assert result["unrealized_pnl"] == pytest.approx(0.0)
    assert result["funding_status"] == "unavailable"
    assert result["net_pnl"] is None
    assert result["pnl_before_funding"] == pytest.approx(0.0)
    assert result["equity_curve"][-1]["equity"] == pytest.approx(1000.0)
    assert result["events"][0]["type"] == "paused"


def test_backtest_starts_after_the_scanner_window(monkeypatch):
    monkeypatch.setenv("GRID_TRADING_ENABLED", "true")
    client = _client()
    candles = [
        _bar(1, 105, 106, 104, 105),
        _bar(2, 105, 106, 104, 105),
        _bar(3, 105, 106, 99, 101),
        _bar(4, 101, 112, 100, 110),
    ]
    body = {
        "range_low": 100,
        "range_high": 110,
        "grid_count": 1,
        "capital": 1000,
        "stop_price": 90,
        "fee_bps": 0,
        "slippage_bps": 0,
        "intrabar_path": "buy_then_sell",
        "candles": candles,
        "after_time": 2,
    }
    kept = client.post("/api/grid-trading/backtest", json=body)
    assert kept.status_code == 200
    assert kept.json()["summary"]["bar_count"] == 2
    blocked = client.post("/api/grid-trading/backtest", json={**body, "after_time": 4})
    assert blocked.status_code == 422
    assert blocked.json()["detail"]["code"] == "lookback"
    overlap = client.post(
        "/api/grid-trading/backtest",
        json={**body, "after_time": 4, "allow_overlap": True},
    )
    assert overlap.status_code == 200
    assert overlap.json()["overlap"] is True
    assert "overlaps the scanner lookback" in overlap.json()["warnings"][0]


def _fund(time: int, rate: float, mark: float | None = None) -> dict:
    row = {"time": time, "rate": rate}
    if mark is not None:
        row["mark_price"] = mark
    return row


def test_positive_funding_is_paid_by_the_long_and_negative_funding_is_received():
    candles = [
        _bar(0, 105, 106, 99, 101),
        _bar(10, 101, 102, 100, 102),
        _bar(20, 102, 103, 101, 102),
    ]
    cfg = _config(fee_bps=0.0, slippage_bps=0.0)
    paid = run_grid(candles, cfg, [_fund(15, 0.001)])
    received = run_grid(candles, cfg, [_fund(15, -0.001)])
    # Held across candle 10. Settlement 15 is inside it, so both signs use qty 10 at close 102.
    payment = 10.0 * 102.0 * 0.001
    assert paid["funding_paid"] == pytest.approx(payment)
    assert paid["unrealized_pnl"] == pytest.approx(20.0)
    assert paid["net_pnl"] == pytest.approx(20.0 - payment)
    assert paid["equity_curve"][-1]["equity"] == pytest.approx(1000.0 + 20.0 - payment)
    assert received["funding_paid"] == pytest.approx(-payment)
    assert received["net_pnl"] == pytest.approx(20.0 + payment)
    assert paid["events"][-1]["price_source"] == "close"
    marked = run_grid(candles, cfg, [_fund(15, 0.001, 50.0)])
    assert marked["funding_paid"] == pytest.approx(10.0 * 50.0 * 0.001)
    assert marked["events"][-1]["price_source"] == "mark_price"
    assert marked["funding_mark_prices"] == 1
    assert marked["funding_closes"] == 0
    assert paid["funding_mark_prices"] == 0
    assert paid["funding_closes"] == 1


def test_zero_inventory_at_settlement_pays_nothing():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
    ]
    result = run_grid(candles, _config(fee_bps=0.0, slippage_bps=0.0), [_fund(0, 0.01, 100.0)])
    assert result["funding_paid"] == pytest.approx(0.0)
    assert result["base"] == pytest.approx(10.0)


def test_inventory_is_charged_at_each_settlement_it_is_held():
    candles = [
        _bar(0, 105, 106, 99, 101),
        _bar(10, 101, 102, 100, 102),
        _bar(20, 102, 103, 101, 102),
    ]
    result = run_grid(
        candles,
        _config(fee_bps=0.0, slippage_bps=0.0),
        [_fund(5, 0.001), _fund(15, 0.002)],
    )
    assert result["funding_paid"] == pytest.approx(10.0 * 101.0 * 0.001 + 10.0 * 102.0 * 0.002)
    assert result["fill_count"] == 1


def test_funding_can_turn_a_winning_cycle_into_a_net_loss():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
        _bar(2, 101, 112, 100, 108),
    ]
    result = run_grid(candles, _config(fee_bps=0.0, slippage_bps=0.0), [_fund(1.5, 0.2, 100.0)])
    assert result["realized_pnl"] == pytest.approx(100.0)
    assert result["funding_paid"] == pytest.approx(200.0)
    assert result["net_pnl"] == pytest.approx(-100.0)
    assert result["realized_pnl"] > 0 > result["net_pnl"]


def test_missing_funding_rows_are_not_treated_as_zero():
    candles = [_bar(time, 105, 106, 104, 105) for time in (0, 10, 20, 30, 40)]
    result = run_grid(
        candles,
        _config(fee_bps=0.0, slippage_bps=0.0),
        [_fund(0, 0.001), _fund(10, 0.001), _fund(30, 0.001), _fund(40, 0.001)],
    )
    assert result["funding_coverage_pct"] == pytest.approx(0.8)
    assert result["funding_status"] == "incomplete"
    assert result["net_pnl"] is None
    assert result["funding_included"] is False
    assert any("No settlement data from 10.0 to 30.0" in warning for warning in result["warnings"])
    assert any("Funding is incomplete" in warning for warning in result["warnings"])


def test_no_settlement_source_is_unavailable_and_not_a_silent_exclude():
    candles = [_bar(0, 105, 106, 104, 105), _bar(1, 105, 112, 99, 108)]
    result = run_grid(candles, _config())
    excluded = run_grid(candles, _config(funding_mode="exclude"))
    assert result["funding_status"] == "unavailable"
    assert result["funding_paid"] == pytest.approx(0.0)
    assert result["net_pnl"] is None
    assert result["pnl_before_funding"] == pytest.approx(excluded["net_pnl"])
    assert result["funding_coverage_pct"] is None
    assert "Funding is unavailable" in result["warnings"][0]
    assert "not charged" not in result["warnings"][0]
    assert excluded["funding_status"] == "excluded"
    assert excluded["warnings"][0] == "Funding is not charged."


def test_paused_inventory_still_pays_funding(monkeypatch):
    from app.grid_trading import engine

    original = engine._new_state

    def seeded(config, levels, anchor):
        state = original(config, levels, anchor)
        state["base"] = 2.0
        state["quote"] = 800.0
        state["cost_basis"] = 200.0
        return state

    monkeypatch.setattr(engine, "_new_state", seeded)
    candles = [
        _bar(0, 100, 101, 99, 100),
        _bar(1, 100, 101, 99, 100),
    ]
    result = run_grid(candles, _config(paused=True, fee_bps=0.0, slippage_bps=0.0), [_fund(1, 0.01, 100.0)])
    assert result["halted"] == "paused"
    assert result["fill_count"] == 0
    assert result["base"] == pytest.approx(2.0)
    assert result["funding_paid"] == pytest.approx(2.0)
    assert result["net_pnl"] == pytest.approx(-2.0)


def test_no_funding_after_the_stop_flatten():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
        _bar(2, 101, 102, 89, 95),
        _bar(3, 95, 96, 94, 95),
    ]
    result = run_grid(
        candles,
        _config(fee_bps=0.0, slippage_bps=0.0),
        [_fund(1, 0.001), _fund(2, 0.05, 90.0), _fund(3, 0.05, 95.0)],
    )
    assert result["halted"] == "stop"
    # t=1 is the buy candle's open, so it pays nothing. t=2 is the stop candle's
    # open and pays the inventory held into that candle. t=3 is after the stop.
    assert result["funding_paid"] == pytest.approx(10.0 * 90.0 * 0.05)


def test_exclude_mode_matches_a_run_with_no_funding_cashflow():
    candles = [
        _bar(0, 105, 106, 104, 105),
        _bar(1, 105, 106, 99, 101),
        _bar(2, 101, 102, 100, 101),
    ]
    funding = [_fund(1.5, 0.001)]
    cfg = _config(fee_bps=0.0, slippage_bps=0.0)
    plain = run_grid(candles, cfg)
    excluded = run_grid(candles, cfg.model_copy(update={"funding_mode": "exclude"}), funding)
    charged = run_grid(candles, cfg, funding)
    assert plain["funding_status"] == "unavailable"
    assert plain["net_pnl"] is None
    assert excluded["funding_status"] == "excluded"
    assert excluded["net_pnl"] == pytest.approx(plain["pnl_before_funding"])
    assert excluded["funding_paid"] == pytest.approx(0.0)
    assert excluded["warnings"] == ["Funding is not charged."]
    assert charged["net_pnl"] < excluded["net_pnl"]


def test_settlement_timing_uses_previous_close_inventory_and_the_worse_inside_quantity():
    cfg = _config(fee_bps=0.0, slippage_bps=0.0)
    buy_then_hold = [
        _bar(0, 105, 106, 99, 101),
        _bar(10, 101, 102, 100, 102),
    ]
    at_buy_open = run_grid(buy_then_hold, cfg, [_fund(0, 0.001)])
    assert at_buy_open["funding_paid"] == pytest.approx(0.0)
    assert at_buy_open["fill_count"] == 1
    inside_positive = run_grid(buy_then_hold, cfg, [_fund(5, 0.001)])
    inside_negative = run_grid(buy_then_hold, cfg, [_fund(5, -0.001)])
    assert inside_positive["funding_paid"] == pytest.approx(10.0 * 101.0 * 0.001)
    assert inside_negative["funding_paid"] == pytest.approx(0.0)
    held_then_sold = [
        _bar(0, 105, 106, 99, 101),
        _bar(10, 101, 112, 100, 108),
    ]
    at_sell_open = run_grid(held_then_sold, cfg, [_fund(10, 0.001)])
    assert at_sell_open["fill_count"] == 2
    assert at_sell_open["funding_paid"] == pytest.approx(10.0 * 108.0 * 0.001)
    stop_candles = [
        _bar(0, 105, 106, 99, 101),
        _bar(10, 101, 102, 89, 95),
        _bar(20, 95, 96, 94, 95),
    ]
    on_stop_open = run_grid(stop_candles, cfg, [_fund(10, 0.01, 90.0), _fund(20, 0.05, 95.0)])
    assert on_stop_open["halted"] == "stop"
    assert on_stop_open["funding_paid"] == pytest.approx(10.0 * 90.0 * 0.01)
    jittered = run_grid(
        [
            _bar(1_700_000_000, 105, 106, 104, 105),
            _bar(1_700_003_600, 105, 106, 99, 101),
        ],
        cfg,
        [_fund(1_700_003_600.4, 0.001)],
    )
    assert jittered["funding_paid"] == pytest.approx(0.0)
    assert jittered["fill_count"] == 1


def test_daily_candles_warn_that_settlement_inventory_is_approximated():
    day = 86_400
    step = 8 * 3600
    start = 1_700_000_000
    candles = [
        _bar(start, 105, 106, 99, 101),
        _bar(start + day, 101, 102, 100, 102),
        _bar(start + 2 * day, 102, 103, 101, 102),
    ]
    funding = [_fund(start + step, 0.001), _fund(start + 2 * step, 0.001), _fund(start + 3 * step, 0.001)]
    daily = run_grid(candles, _config(timeframe="1d", fee_bps=0.0, slippage_bps=0.0), funding)
    hourly_candles = [
        _bar(start + index * 3600, 105, 106, 104, 105) for index in range(0, 25)
    ]
    hourly_candles[1] = _bar(start + 3600, 105, 106, 99, 101)
    hourly = run_grid(
        hourly_candles,
        _config(timeframe="1h", fee_bps=0.0, slippage_bps=0.0),
        funding,
    )
    assert daily["accepted"] is True
    assert any("approximated" in warning for warning in daily["warnings"])
    assert not any("approximated" in warning for warning in hourly["warnings"])


def test_a_funding_interval_change_warns_and_keeps_the_run():
    # 8h, 8h, then 4h, 4h. Median is 6h, so none of these gaps is a missing row.
    times = [0, 8, 16, 20, 24]
    candles = [_bar(time, 105, 106, 104, 105) for time in times]
    result = run_grid(
        candles,
        _config(fee_bps=0.0, slippage_bps=0.0),
        [_fund(time, 0.001) for time in times],
    )
    assert result["accepted"] is True
    assert result["funding_coverage_pct"] == pytest.approx(1.0)
    assert result["funding_included"] is True
    assert any("interval changes inside the window" in warning for warning in result["warnings"])
    assert not any("No settlement data" in warning for warning in result["warnings"])


def test_duplicate_funding_time_is_charged_once_and_a_high_rate_warns():
    start = 1_700_000_000
    step = 8 * 3600
    candles = [
        _bar(start + index * step, 105, 106, 99 if index == 0 else 100, 101)
        for index in range(4)
    ]
    once = [_fund(start + index * step, 0.001, 100.0) for index in range(4)]
    doubled = once + [_fund(start + step, 0.001, 100.0)]
    single = run_grid(candles, _config(fee_bps=0.0, slippage_bps=0.0), once)
    duplicate = run_grid(candles, _config(fee_bps=0.0, slippage_bps=0.0), doubled)
    assert duplicate["funding_paid"] == pytest.approx(single["funding_paid"])
    assert single["funding_paid"] > 0
    assert single["funding_status"] == "charged"
    assert any("above 50%" in warning for warning in single["warnings"])


def test_coverage_under_95_percent_hides_funded_net_and_is_not_ranked():
    from app.grid_trading.validation import sweep_grid_count

    start = 1_700_000_000
    step = 8 * 3600
    times = [start, start + step, start + 2 * step, start + 6 * step]
    candles = [_bar(when, 105, 106, 99 if when == start else 100, 101) for when in times]
    funding = [_fund(when, 0.0001, 100.0) for when in times]
    cfg = _config(fee_bps=0.0, slippage_bps=0.0)
    result = run_grid(candles, cfg, funding)
    assert result["funding_coverage_pct"] < 0.95
    assert result["funding_status"] == "incomplete"
    assert result["net_pnl"] is None
    assert result["funding_paid"] > 0
    assert result["pnl_before_funding"] is not None
    report = sweep_grid_count(candles, cfg, [1], funding)
    assert report["ranked"] is False
    assert report["funding_status"] == "incomplete"
    assert report["rows"] == []


def test_quote_table_rows_are_unavailable_and_charge_nothing():
    start = 1_700_000_000
    candles = [
        _bar(start, 105, 106, 99, 101),
        _bar(start + 3600, 101, 102, 100, 102),
    ]
    quotes = []
    for index in range(3600):
        quotes.append(
            {
                "time": start + index,
                "funding_rate": 0.0001 + (index % 5) * 1e-6,
                "mark_price": 100.0 + index * 0.01,
                "source": "binance_ws" if index % 2 == 0 else "binance_rest",
            }
        )
    result = run_grid(candles, _config(fee_bps=0.0, slippage_bps=0.0), quotes)
    assert result["funding_status"] == "unavailable"
    assert result["funding_paid"] == pytest.approx(0.0)
    assert result["net_pnl"] is None
    assert result["buy_and_hold"]["funding_paid"] == pytest.approx(0.0)
    assert not any(event.get("type") == "funding" for event in result["events"])
    assert "live funding quotes" in result["warnings"][0]


def test_buy_and_hold_is_charged_the_same_settlements():
    candles = [
        _bar(0, 100, 101, 99, 100),
        _bar(1, 100, 101, 99, 100),
    ]
    cfg = _config(
        range_low=10.0,
        range_high=20.0,
        stop_price=5.0,
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    result = run_grid(candles, cfg, [_fund(0, 0.001, 100.0)])
    assert result["fill_count"] == 0
    assert result["funding_paid"] == pytest.approx(0.0)
    assert result["buy_and_hold"]["funding_paid"] == pytest.approx(1.0)
    assert result["buy_and_hold"]["equity"] == pytest.approx(999.0)


def test_create_app_does_not_run_grid_paths(monkeypatch):
    monkeypatch.setenv("GRID_TRADING_ENABLED", "false")
    calls: list[str] = []
    monkeypatch.setattr("app.grid_trading.api.run_grid", lambda *a, **k: calls.append("run"))
    monkeypatch.setattr("app.grid_trading.api.score_series", lambda *a, **k: calls.append("scan"))
    monkeypatch.setattr(
        "app.grid_trading.api._execution.submit_live",
        lambda *a, **k: calls.append("live"),
    )
    from app.main import create_app

    create_app()
    assert calls == []
