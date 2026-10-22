"""Grid fee defaults (owner D144; D185 option a / D207 taker, D209 maker): maker 0.02%, taker 0.05%."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.grid_trading.api import GridRunRequest, ScanRequest, _to_config
from app.grid_trading.config import GridConfig, maker_fee_rate, taker_fee_rate
from app.grid_trading.engine import _flatten, run_grid

BASE = {"range_low": 95.0, "range_high": 105.0, "grid_count": 8, "capital": 1000.0, "stop_price": 90.0}


def test_defaults_maker_2_bps_taker_5_bps():
    cfg = GridConfig(**BASE)
    assert cfg.fee_bps == 2.0 and cfg.taker_fee_bps == 5.0
    assert maker_fee_rate(cfg) == pytest.approx(0.0002)
    assert taker_fee_rate(cfg) == pytest.approx(0.0005)
    req = GridRunRequest(symbol="BTCUSDT", **BASE)
    assert req.fee_bps == 2.0 and req.taker_fee_bps == 5.0
    assert maker_fee_rate(_to_config(req)) == pytest.approx(0.0002)
    assert taker_fee_rate(_to_config(req)) == pytest.approx(0.0005)
    assert ScanRequest().fee_bps == 2.0  # the scanner has one fee field: the maker rate of grid fills


def test_explicit_fees_still_win():
    cfg = GridConfig(**BASE, fee_bps=1.0, taker_fee_bps=3.0)
    assert maker_fee_rate(cfg) == pytest.approx(0.0001)
    assert taker_fee_rate(cfg) == pytest.approx(0.0003)


def test_a_known_market_exit_pays_0_05_pct_taker():
    cfg = GridConfig(**BASE, slippage_bps=0.0)
    state = {"base": 2.0, "quote": 0.0, "realized": 0.0, "cost_basis": 190.0, "fills": [], "events": [], "slots": []}
    _flatten(state, cfg, 100.0, datetime(2026, 7, 1, tzinfo=timezone.utc), "stop")
    fill = state["fills"][-1]
    assert fill["side"] == "sell"
    assert fill["fee"] == pytest.approx(2.0 * 100.0 * 0.0005)  # $0.10 on $200 notional
    assert state["quote"] == pytest.approx(200.0 - 0.10)


def test_a_known_grid_limit_fill_pays_0_02_pct_maker():
    cfg = GridConfig(range_low=90.0, range_high=110.0, grid_count=4, capital=1000.0, stop_price=50.0,
                     slippage_bps=0.0, funding_mode="exclude")
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    bars = [
        {"time": t0, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1.0},
        {"time": t0.replace(hour=1), "open": 100.0, "high": 100.2, "low": 94.0, "close": 96.0, "volume": 1.0},
        {"time": t0.replace(hour=2), "open": 96.0, "high": 96.5, "low": 95.5, "close": 96.0, "volume": 1.0},
    ]
    result = run_grid(bars, cfg, None)
    buys = [f for f in result["fills"] if f.get("side") == "buy" and f.get("slot") is not None]
    assert buys, result["fills"]
    for fill in buys:
        assert fill["fee"] == pytest.approx(fill["price"] * fill["qty"] * 0.0002)
