"""COMBO trade dicts -> VectorBT replay orders (no DB).

Trade dicts are built the way a strategy_matrix row builds them: a
ResearchTrade-shaped dict run through trade_fees.enrich_trade_execution.
"""

from __future__ import annotations

import pytest

from app.research.trade_fees import enrich_trade_execution
from app.research.vectorbt_orders_combo import ReplayUnsupported, combo_replay_orders
from app.research.vectorbt_replay import check_parity

TAKER = 0.0004
MAKER = 0.0002


def _trade(
    *,
    entry_index: int,
    exit_index: int | None,
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 98.0,
    tp1: float | None = 104.0,
    tp2: float | None = None,
    exit_price: float | None = 104.0,
    outcome: str = "TP1",
    ambiguous: bool = False,
    entry_type: str = "MARKET",
    slippage_rate: float = 0.0,
    **extra,
) -> dict:
    raw = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "combination_id": "COMBO_01",
        "entry_index": entry_index,
        "signal_time": None,
        "direction": direction,
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": None,
        "exit_index": exit_index,
        "exit_price": exit_price,
        "outcome": outcome,
        "ambiguous": ambiguous,
        "condition_snapshot": {"entry_type": entry_type},
        **extra,
    }
    if outcome == "OPEN":
        return raw
    return enrich_trade_execution(
        raw,
        risk_usd=20.0,
        taker_fee=TAKER,
        maker_fee=MAKER,
        leverage=2.0,
        account_equity=1000.0,
        slippage_rate=slippage_rate,
    )


def test_market_long_and_short_pass():
    trades = [
        _trade(entry_index=10, exit_index=14),
        _trade(
            entry_index=20, exit_index=22, direction="SHORT", entry=100.0, stop=102.0,
            tp1=96.0, exit_price=102.0, outcome="SL",
        ),
    ]
    orders = combo_replay_orders("COMBO_01", trades)
    assert [o.entry_index for o in orders] == [10, 20]
    assert orders[0].entry_fee_rate == TAKER and orders[0].exit_fee_rate == TAKER
    assert orders[0].risk_usd == trades[0]["risk_usd"]
    assert orders[0].net_r == trades[0]["r_net"]
    res = check_parity(orders)
    assert res.passed, res.details
    assert res.bar_loop_trades == res.vectorbt_trades == 2


def test_ambiguous_same_bar_label_carried_unchanged():
    # CONSERVATIVE: stop and target on one bar -> engine labels it "SL",
    # ambiguous=True, exits at the stop. The adapter must not relabel it.
    conservative = _trade(
        entry_index=5, exit_index=6, exit_price=98.0, outcome="SL", ambiguous=True,
    )
    # NEUTRAL handling would label it AMBIGUOUS_INTRABAR (exit at stop); same rule.
    neutral = _trade(
        entry_index=8, exit_index=9, exit_price=98.0, outcome="AMBIGUOUS_INTRABAR",
        ambiguous=True,
    )
    orders = combo_replay_orders("COMBO_02", [conservative, neutral])
    assert [o.outcome for o in orders] == ["SL", "AMBIGUOUS_INTRABAR"]
    assert all(o.exit_price == 98.0 for o in orders)
    res = check_parity(orders)
    assert [t.outcome for t in res.replayed] == ["SL", "AMBIGUOUS_INTRABAR"]
    # The engine's r_net for the NEUTRAL row is the dollar result at the stop,
    # so the replay matches it; the label is never re-derived.
    assert res.passed, res.details


def test_open_trades_are_skipped():
    trades = [
        _trade(entry_index=1, exit_index=3),
        _trade(entry_index=4, exit_index=None, exit_price=None, outcome="OPEN"),
    ]
    assert len(combo_replay_orders("COMBO_01", trades)) == 1


def test_limit_retest_uses_maker_entry_fee():
    t = _trade(entry_index=2, exit_index=7, entry_type="LIMIT_RETEST")
    (o,) = combo_replay_orders("COMBO_02", [t])
    assert o.entry_fee_rate == MAKER and o.exit_fee_rate == TAKER
    assert check_parity([o]).passed


def test_tp2_outcome_carries_tp2_as_target():
    t = _trade(entry_index=2, exit_index=9, tp2=106.0, exit_price=106.0, outcome="TP2")
    (o,) = combo_replay_orders("COMBO_01", [t])
    assert o.target == 106.0 and o.outcome == "TP2"


def test_same_bar_next_open_exit():
    # SD rows fill at the open and are managed on the fill bar itself.
    t = _trade(entry_index=30, exit_index=30, exit_price=98.0, outcome="SL", entry_type="NEXT_OPEN")
    (o,) = combo_replay_orders("COMBO_SD_C", [t])
    assert o.entry_index == o.exit_index == 30
    assert check_parity([o]).passed


def test_causal_row_enters_on_fill_index():
    t = _trade(
        entry_index=40, exit_index=44, execution_model="CAUSAL_NEXT_OPEN",
        signal_index=40, fill_index=41,
    )
    (o,) = combo_replay_orders("COMBO_02_V2", [t])
    assert o.entry_index == 41 and o.exit_index == 44
    assert check_parity([o]).passed


def test_slippage_rows_use_slipped_fills_and_now_reach_parity():
    # enrich_trade_execution now computes gross P&L from the same slipped fills
    # it charges fees on (fix/locked-trade-fixes), so VectorBT agrees on net R.
    # Before that fix this row was a net_r mismatch.
    t = _trade(entry_index=3, exit_index=6, slippage_rate=0.0002)
    (o,) = combo_replay_orders("COMBO_SD_C", [t])
    assert o.entry_price == pytest.approx(100.0 * 1.0002, rel=1e-15)
    assert o.exit_price == pytest.approx(104.0 * 0.9998, rel=1e-15)
    res = check_parity([o])
    assert res.passed, res
    assert res.first_mismatch_field is None


def test_slipped_notional_disagreeing_raises():
    t = _trade(entry_index=3, exit_index=6, slippage_rate=0.0002)
    t["notional_entry_usd"] = t["notional_entry_usd"] * 1.01
    with pytest.raises(ReplayUnsupported, match="notional_entry_usd"):
        combo_replay_orders("COMBO_SD_C", [t])


@pytest.mark.parametrize("field", ["qty", "fee_entry_rate", "risk_usd", "exit_index", "slippage_rate"])
def test_missing_field_raises(field):
    t = _trade(entry_index=1, exit_index=2)
    t.pop(field)
    with pytest.raises(ReplayUnsupported, match=field):
        combo_replay_orders("COMBO_01", [t])


def test_overlapping_positions_are_not_hidden():
    a = _trade(entry_index=1, exit_index=10)
    b = _trade(entry_index=5, exit_index=8)
    res = check_parity(combo_replay_orders("COMBO_01", [a, b]))
    assert not res.passed
