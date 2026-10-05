"""Focused unit tests for realistic paper sizing (tick/lot/leverage/fees)."""

from __future__ import annotations

import pytest

from app.models.schemas import SymbolInfo
from app.services.paper_sizing import (
    floor_qty_to_step,
    net_paper_pnl,
    resolve_symbol_filters,
    round_price_to_tick,
    size_paper_long,
)
from app.services.paper_trade import PaperTradeEngine
from app.services.paper_risk import PaperRiskPolicy


def test_sol_defaults_tick_and_lot_step():
    tick, step, source = resolve_symbol_filters("SOLUSDT")
    assert tick == pytest.approx(0.01)
    assert step == pytest.approx(0.1)
    assert "default" in source


def test_resolve_uses_market_store_filters(monkeypatch):
    from app.services.market_store import market_store

    market_store.symbols["SOLUSDT"] = SymbolInfo(
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        market_type="futures_perp",
        tick_size=0.01,
        step_size=0.1,
        price_precision=2,
        qty_precision=1,
    )
    try:
        tick, step, source = resolve_symbol_filters("SOLUSDT")
        assert tick == pytest.approx(0.01)
        assert step == pytest.approx(0.1)
        assert source == "market_store"
    finally:
        market_store.symbols.pop("SOLUSDT", None)


def test_qty_and_stop_rounding():
    assert round_price_to_tick(150.004, 0.01, mode="up") == pytest.approx(150.01)
    assert round_price_to_tick(149.996, 0.01, mode="down") == pytest.approx(149.99)
    assert floor_qty_to_step(13.37, 0.1) == pytest.approx(13.3)

    sized = size_paper_long(
        symbol="SOLUSDT",
        account_equity=1000.0,
        risk_percent=0.02,
        entry=150.004,
        stop=149.5,
        leverage=2.0,
    )
    assert sized["entry_price"] == pytest.approx(150.01)
    assert sized["stop_price"] == pytest.approx(149.50)
    assert sized["lot_step"] == pytest.approx(0.1)
    # qty floored to 0.1
    assert abs(sized["quantity"] / 0.1 - round(sized["quantity"] / 0.1)) < 1e-9


def test_tight_stop_sol_capped_by_2x_leverage():
    """$1000 / 2% with a tight SOL stop must not exceed 2x notional."""
    entry = 150.0
    stop = 149.9  # $0.10 risk / unit → uncapped qty would be 200 SOL
    sized = size_paper_long(
        symbol="SOLUSDT",
        account_equity=1000.0,
        risk_percent=0.02,
        entry=entry,
        stop=stop,
        leverage=2.0,
    )
    assert sized["capped_by_leverage"] is True
    assert sized["quantity"] == pytest.approx(13.3)  # floor(2000/150/0.1)*0.1
    assert sized["notional"] == pytest.approx(13.3 * 150.0)
    assert sized["notional"] <= 1000.0 * 2.0 + 1e-9
    # Effective risk after leverage cap + lot rounding (not the full $20).
    assert sized["risk_usd"] == pytest.approx((150.0 - 149.9) * 13.3)
    assert sized["risk_usd"] < 20.0


def test_fees_reduce_net_pnl():
    settled = net_paper_pnl(
        entry_price=150.0,
        exit_price=151.0,
        quantity=10.0,
        risk_usd=5.0,
        fee_rate=0.0004,
        slippage_rate=0.0002,
    )
    gross = 10.0  # (151-150)*10
    assert settled["gross_pnl_usd"] == pytest.approx(gross)
    assert settled["pnl_usd"] < gross
    assert settled["entry_fee_usd"] < 0
    assert settled["exit_fee_usd"] < 0
    assert settled["slippage_usd"] < 0
    assert settled["r_multiple"] < gross / 5.0


def test_paper_engine_sol_tight_stop_uses_leverage_cap(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 150.0)
    eng = PaperTradeEngine(
        starting_equity=1000.0,
        risk_percent=0.02,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
        v1_profile_enabled=False,
        max_leverage=2.0,
    )
    eng.legacy_auto_entry_enabled = True
    payload = {
        "status": "WAITING",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": "BULLISH"},
            "4h": {"trend": "BULLISH"},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
        "bos": {
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 150.0,
        },
        "entry": {"entry_price": 150.0},
        "stop": {"final_stop": 149.9},
        "targets": [{"target_price": 150.5}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "risk_management": {},
        "source_candle_timestamps": {"15m": "2026-10-04T12:00:00+00:00"},
        "calculated_at": "2026-10-04T12:05:00+00:00",
        "ohlcv_freshness": "OK",
    }
    pos = eng.on_setup_signal("SOLUSDT", payload)
    assert pos is not None
    assert pos.quantity == pytest.approx(13.3)
    assert pos.risk_usd == pytest.approx(1.33)
    assert (pos.signal_snippet.get("paper_execution") or {}).get("capped_by_leverage") is True

    closed = eng.tick({"SOLUSDT": 149.8})
    assert len(closed) == 1
    assert closed[0].pnl_usd is not None
    # Gross stop ≈ -1.33; fees make it more negative.
    assert closed[0].pnl_usd < -1.33
