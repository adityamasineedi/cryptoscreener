"""Leverage, liquidation, margin drawdown, take-profit exit, and the open-grid cap."""

from __future__ import annotations

import pytest

from app.grid_trading import engine
from app.grid_trading.config import GridConfig, GridConfigError, worst_case_liquidation_price
from app.grid_trading.engine import run_grid
from app.grid_trading.levels import build_levels
from app.grid_trading.metrics import summarize


def _config(**overrides) -> GridConfig:
    payload = {
        "range_low": 100.0,
        "range_high": 110.0,
        "grid_count": 1,
        "capital": 1000.0,
        "max_capital": 5000.0,
        "fee_bps": 0.0,
        "slippage_bps": 0.0,
        "stop_price": 90.0,
        "max_drawdown": 0.9,
        "max_open_orders": 5,
        "intrabar_path": "buy_then_sell",
        "funding_mode": "exclude",
    }
    payload.update(overrides)
    return GridConfig(**payload)


def _bar(time: int, open_: float, high: float, low: float, close: float) -> dict:
    return {"time": time, "open": open_, "high": high, "low": low, "close": close}


CYCLE = [_bar(1, 105, 106, 100, 102), _bar(2, 102, 110, 101, 108)]


def test_leverage_scales_slot_size_and_pnl_on_the_same_margin():
    one = run_grid(CYCLE, _config())
    two = run_grid(CYCLE, _config(leverage=2.0))
    assert one["fills"][0]["qty"] == pytest.approx(10.0)
    assert two["fills"][0]["qty"] == pytest.approx(20.0)
    assert one["realized_pnl"] == pytest.approx(100.0)
    assert two["realized_pnl"] == pytest.approx(200.0)
    assert two["return"] == pytest.approx(0.2)
    assert two["leverage"] == 2.0


@pytest.mark.parametrize("leverage", [0.5, 5.5])
def test_leverage_outside_1_to_5_is_rejected(leverage):
    with pytest.raises(GridConfigError) as exc:
        run_grid(CYCLE, _config(leverage=leverage))
    assert exc.value.code == "leverage"


def test_stop_below_worst_case_liquidation_is_rejected():
    # 5x on one slot: quote 1000 - 5000 = -4000, base 50, liquidation 4000 / (50 * 0.995).
    cfg = _config(leverage=5.0, stop_price=81.0)
    liq = worst_case_liquidation_price(cfg, build_levels(cfg))
    assert liq == pytest.approx(4000.0 / (50.0 * 0.995))
    run_grid(CYCLE, cfg)
    with pytest.raises(GridConfigError) as exc:
        run_grid(CYCLE, _config(leverage=5.0, stop_price=70.0))
    assert exc.value.code == "liquidation"


def test_open_grid_cap_lowers_the_worst_case_liquidation_price():
    base = {"range_high": 140.0, "grid_count": 4, "leverage": 5.0, "stop_price": 85.0}
    with pytest.raises(GridConfigError):
        run_grid(CYCLE, _config(**base))
    capped = _config(**base, max_open_grids=1)
    assert worst_case_liquidation_price(capped, build_levels(capped)) < 85.0


def test_liquidation_fires_before_the_stop_and_forfeits_the_margin():
    cfg = _config(leverage=5.0, stop_price=81.0)
    state = engine._new_state(cfg, build_levels(cfg), 105.0)
    state["quote"] = -4100.0  # funding raised the liquidation price above the stop
    state["base"] = 50.0
    state["cost_basis"] = 5000.0
    engine._finish_bar(state, cfg, _bar(3, 85, 85, 81.5, 82))
    assert state["halted"] == "liquidation"
    assert state["quote"] == pytest.approx(0.0)
    assert state["base"] == 0.0
    assert state["liquidation_fee"] > 0
    assert [e["type"] for e in state["events"]] == ["liquidation"]


def test_stop_fires_when_it_sits_above_liquidation():
    bars = [_bar(1, 105, 106, 100, 102), _bar(2, 102, 103, 85, 86)]
    result = run_grid(bars, _config(leverage=2.0))
    assert result["halted"] == "stop"
    assert result["stop_hits"] == 1
    assert result["liquidations"] == 0
    # 20 BTC bought at 100, stopped at 90 on 1000 margin.
    assert result["liquidated_equity"] == pytest.approx(800.0)


def test_drawdown_is_measured_on_margin():
    bars = [_bar(1, 105, 106, 100, 100), _bar(2, 100, 100, 90.5, 90.5)]
    one = run_grid(bars, _config())
    two = run_grid(bars, _config(leverage=2.0))
    assert one["max_drawdown"] == pytest.approx(95.0 / 1000.0)
    assert two["max_drawdown"] == pytest.approx(190.0 / 1000.0)
    killed = run_grid(bars, _config(leverage=2.0, max_drawdown=0.15))
    assert killed["drawdown_kills"] == 1
    assert killed["accepted"] is False


def test_close_above_range_takes_profit_and_ends_the_grid():
    bars = [*CYCLE[:1], _bar(2, 102, 112, 101, 111), _bar(3, 111, 111, 99, 100)]
    off = run_grid(bars, _config())
    on = run_grid(bars, _config(exit_above_range=True))
    assert sum(1 for f in off["fills"] if f["side"] == "buy") == 2
    assert sum(1 for f in on["fills"] if f["side"] == "buy") == 1
    assert on["halted"] == "take_profit"
    summary = summarize(on)
    assert summary["take_profit_exits"] == 1
    assert summary["round_trips"] == 1
    assert summary["stop_hits"] == 0


def test_wick_above_range_does_not_take_profit():
    bars = [*CYCLE[:1], _bar(2, 102, 112, 101, 108)]
    result = run_grid(bars, _config(exit_above_range=True))
    assert result["halted"] is None
    assert result["take_profit_exits"] == 0


def test_close_above_range_before_any_profit_keeps_the_grid_running():
    bars = [_bar(1, 105, 112, 104, 111), _bar(2, 111, 111, 99, 100)]
    result = run_grid(bars, _config(exit_above_range=True))
    assert result["halted"] is None
    assert result["take_profit_exits"] == 0
    assert sum(1 for f in result["fills"] if f["side"] == "buy") == 1


def test_open_grid_cap_blocks_buys_beyond_the_limit():
    bars = [_bar(1, 135, 136, 100, 101), _bar(2, 101, 102, 100, 101)]
    cfg = _config(range_high=140.0, grid_count=4, max_open_grids=2, intrabar_path="sell_then_buy")
    result = run_grid(bars, cfg)
    bought = [f["level"] for f in result["fills"] if f["side"] == "buy"]
    assert bought == pytest.approx([130.0, 120.0])
    assert result["blocked_buys"] == 4
    assert result["max_open_grids"] == 2


def test_buy_and_hold_benchmark_uses_the_grid_leverage():
    bars = [_bar(1, 100, 106, 100, 102), _bar(2, 102, 106, 101, 105)]
    one = run_grid(bars, _config(range_low=50.0, range_high=60.0, stop_price=40.0))
    two = run_grid(bars, _config(range_low=50.0, range_high=60.0, stop_price=40.0, leverage=2.0))
    assert one["buy_and_hold"]["return"] == pytest.approx(0.05)
    assert two["buy_and_hold"]["return"] == pytest.approx(0.10)
    assert two["buy_and_hold"]["liquidated"] is False
