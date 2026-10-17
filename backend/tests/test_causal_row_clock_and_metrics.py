"""Causal book: clock, excursion and row metrics come from the causal fills.

Display fields only. The pricing numbers of both books are pinned at the end.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from app.research.backtest_cpu_worker import build_matrix_row_postprocess
from app.research.causal_execution import (
    OUTCOME_GAP_THROUGH_STOP,
    causal_r_metrics,
    clear_isolated_execution_runs,
    execute_causal_trade,
)
from app.research.metrics import expectancy_r, profit_factor

RISK_USD = 20.0
SLIP = 0.0005
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _bar(i: int, o: float = 100.0, h: float = 100.5, l: float = 99.5, c: float = 100.0) -> dict:
    return {
        "time": T0 + timedelta(hours=i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1.0,
    }


def _iso(i: int) -> str:
    return (T0 + timedelta(hours=i)).isoformat()


def _candles() -> list[dict]:
    bars = [_bar(i) for i in range(30)]
    # Trade A: fill at bar 3, TP1 (104) prints on bar 5, TP2 (108) on bar 9.
    bars[3] = _bar(3, 101.0, 101.5, 100.5, 101.0)
    bars[4] = _bar(4, 101.0, 102.0, 100.8, 101.5)
    bars[5] = _bar(5, 101.5, 104.5, 101.2, 104.2)
    for i in (6, 7, 8):
        bars[i] = _bar(i, 105.0, 106.0, 104.6, 105.5)
    bars[9] = _bar(9, 105.5, 108.5, 105.2, 108.0)
    # Trade B: signal closes at 106, the fill bar trades through the stop (104).
    bars[10] = _bar(10, 106.0, 106.5, 105.5, 106.0)
    bars[11] = _bar(11, 106.0, 106.5, 103.5, 104.0)
    # Trade C: the fill bar opens below the stop (98).
    bars[15] = _bar(15, 97.0, 97.5, 96.0, 96.5)
    return bars


def _legacy(entry_index: int, entry: float, stop: float, tp1: float, **fields) -> dict:
    trade = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "combination_id": "COMBO_02_V2",
        "entry_index": entry_index,
        "signal_time": _iso(entry_index),
        "direction": "LONG",
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "tp2": None,
        "tp3": None,
        "rr": abs(tp1 - entry) / abs(entry - stop),
        "exit_index": None,
        "exit_time": None,
        "exit_price": None,
        "outcome": "OPEN",
        "r_multiple": None,
        "holding_bars": None,
        "time_to_tp1": None,
        "time_to_tp2": None,
        "time_to_tp3": None,
        "time_to_sl": None,
        "mae": None,
        "mfe": None,
        "mae_r": None,
        "mfe_r": None,
        "ambiguous": False,
        "condition_snapshot": {"entry_type": "MARKET"},
    }
    trade.update(fields)
    return trade


def _trade_a() -> dict:
    # Legacy book rode to TP2 on bar 9; the causal book works TP1 only.
    return _legacy(
        2, 100.0, 98.0, 104.0,
        tp2=108.0, exit_index=9, exit_time=_iso(9), exit_price=108.0, outcome="TP2",
        r_multiple=4.0, holding_bars=7, time_to_tp1=7, time_to_tp2=7,
        mae=0.0, mfe=8.5, mae_r=0.0, mfe_r=4.25,
    )


def _trade_b() -> dict:
    return _legacy(
        10, 106.0, 104.0, 110.0,
        exit_index=11, exit_time=_iso(11), exit_price=104.0, outcome="SL",
        r_multiple=-1.0, holding_bars=1, time_to_sl=1,
        mae=2.5, mfe=0.5, mae_r=1.25, mfe_r=0.25,
    )


def _trade_c() -> dict:
    return _legacy(
        14, 100.0, 98.0, 104.0,
        exit_index=15, exit_time=_iso(15), exit_price=98.0, outcome="SL",
        r_multiple=-1.0, holding_bars=1, time_to_sl=1,
        mae=4.0, mfe=0.0, mae_r=2.0, mfe_r=0.0,
    )


def _trade_d() -> dict:
    # Hand-set legacy close; the causal walk never reaches a level after bar 21.
    return _legacy(
        20, 100.0, 98.0, 104.0,
        exit_index=23, exit_time=_iso(23), exit_price=104.0, outcome="TP1",
        r_multiple=2.0, holding_bars=3, time_to_tp1=3,
        mae=0.5, mfe=4.0, mae_r=0.25, mfe_r=2.0, ambiguous=True,
    )


def _trades() -> list[dict]:
    return [_trade_a(), _trade_b(), _trade_c(), _trade_d()]


LEGACY_RESULT = {
    "sample_size": 4,
    "average_R": 1.0,
    "expectancy_R": 1.0,
    "tp1_hit_rate": 0.25,
    "sl_rate": 0.5,
    "profit_factor": 3.0,
    "max_drawdown_R": 2.0,
    "tp1_hits": 1,
    "sl_hits": 2,
    "r_values": [4.0, -1.0, -1.0, 2.0],
    "equity_curve_r": [0.0, 4.0, 3.0, 2.0, 4.0],
}


def _payload(combination_id: str, **overrides) -> dict:
    out = {
        "sample_size": 4,
        "status": "OK",
        "result": dict(LEGACY_RESULT),
        "trades": _trades(),
    }
    if combination_id == "COMBO_02_V2":
        out["execution_model"] = "DUAL_LEGACY_AND_CAUSAL"
    data = {
        "backtest_out": out,
        "risk_usd": RISK_USD,
        "taker_fee": 0.0004,
        "maker_fee": 0.0002,
        "leverage": 2.0,
        "principal": 1000.0,
        "include_trades": True,
        "direction": "LONG",
        "combination_id": combination_id,
        "load_meta": {"symbol": "BTCUSDT", "timeframe": "1h"},
        "candles": _candles(),
        "index_start": 0,
        "analytics_on": False,
    }
    data.update(overrides)
    return data


def _causal_row(**overrides) -> dict:
    clear_isolated_execution_runs()
    row = build_matrix_row_postprocess(_payload("COMBO_02_V2", **overrides))
    clear_isolated_execution_runs()
    return row


def _run(trade: dict, candles: list[dict] | None = None) -> dict:
    return execute_causal_trade(
        trade,
        candles if candles is not None else _candles(),
        entry_slippage_rate=SLIP,
        exit_slippage_rate=SLIP,
    )


def test_causal_clock_follows_the_causal_exit_bar_not_the_legacy_one():
    trade = _trade_a()
    frozen = copy.deepcopy(trade)
    row = _run(trade)

    assert trade == frozen
    assert (row["fill_index"], row["exit_index"], row["outcome"]) == (3, 5, "TP1")
    assert trade["exit_index"] == 9
    assert row["signal_time"] == _iso(2)
    assert row["fill_time"] == _iso(3)
    assert row["exit_time"] == _iso(5)
    assert row["exit_time"] != trade["exit_time"]
    assert row["holding_bars"] == row["exit_index"] - row["fill_index"] == 2
    assert row["time_to_tp1"] == 2
    assert row["time_to_tp2"] is None
    assert row["time_to_tp3"] is None
    assert row["time_to_sl"] is None
    assert row["ambiguous"] is None
    entry = 101.0 * (1 + SLIP)
    assert row["rr"] == pytest.approx((104.0 - entry) / (entry - 98.0))
    assert row["rr"] != trade["rr"]


def test_exit_on_the_fill_bar_holds_zero_bars():
    row = _run(_trade_b())

    assert (row["fill_index"], row["exit_index"], row["outcome"]) == (11, 11, "SL")
    assert row["holding_bars"] == 0
    assert row["time_to_sl"] == 0
    assert row["time_to_tp1"] is None
    assert row["fill_time"] == row["exit_time"] == _iso(11)
    entry = 106.0 * (1 + SLIP)
    assert row["mae"] == pytest.approx(entry - 103.5)
    assert row["mfe"] == pytest.approx(106.5 - entry)


def test_gap_through_stop_is_neither_a_tp_nor_an_sl_label():
    row = _run(_trade_c())

    assert row["outcome"] == OUTCOME_GAP_THROUGH_STOP
    assert row["fill_index"] == row["exit_index"] == 15
    assert row["holding_bars"] == 0
    assert row["fill_time"] == row["exit_time"] == _iso(15)
    for key in ("time_to_sl", "time_to_tp1", "time_to_tp2", "time_to_tp3"):
        assert row[key] is None, key
    # Filled and closed at the same open: no excursion.
    assert (row["mae"], row["mfe"], row["mae_r"], row["mfe_r"]) == (0.0, 0.0, 0.0, 0.0)

    metrics = causal_r_metrics([row])
    assert metrics["gap_through_stop_hits"] == 1
    assert metrics["sl_hits"] == 0
    assert metrics["tp1_hits"] == 0


def test_open_causal_trade_carries_no_legacy_clock():
    trade = _trade_d()
    row = _run(trade)

    assert row["outcome"] == "OPEN"
    assert trade["exit_time"] == _iso(23)
    for key in (
        "exit_time",
        "holding_bars",
        "time_to_tp1",
        "time_to_tp2",
        "time_to_tp3",
        "time_to_sl",
        "r_multiple",
        "exit_price",
        "ambiguous",
    ):
        assert row[key] is None, key
    assert row["fill_time"] == _iso(21)
    # Excursions run through the last loaded bar from the causal entry.
    entry = 100.0 * (1 + SLIP)
    assert row["mae"] == pytest.approx(entry - 99.5)
    assert row["mfe"] == pytest.approx(100.5 - entry)


def test_signal_on_the_last_bar_has_no_fill_and_no_legacy_clock():
    trade = _legacy(
        29, 100.0, 98.0, 104.0,
        exit_time=_iso(29), outcome="TP1", r_multiple=2.0, holding_bars=4,
        time_to_tp1=4, mae=1.0, mfe=4.0, mae_r=0.5, mfe_r=2.0,
    )
    row = _run(trade)

    assert row["outcome"] == "OPEN"
    assert row["entry_price"] is None
    for key in (
        "fill_time",
        "exit_time",
        "holding_bars",
        "time_to_tp1",
        "time_to_sl",
        "r_multiple",
        "mae",
        "mfe",
        "mae_r",
        "mfe_r",
        "rr",
    ):
        assert row[key] is None, key


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_excursions_are_measured_from_the_causal_entry(side):
    # Legacy: entry 100 at the signal close, target 9.5 away, so 4.75R and
    # mfe_r 4.75. The causal fill is 0.2 better, so the realised R is larger.
    sign = 1.0 if side == "LONG" else -1.0

    def px(offset: float) -> float:
        return 100.0 + sign * offset

    def bar(i: int, o: float, fav: float, adv: float, c: float) -> dict:
        hi, lo = (px(fav), px(adv)) if side == "LONG" else (px(adv), px(fav))
        return _bar(i, px(o), hi, lo, px(c))

    candles = [
        bar(0, 0.0, 0.3, -0.3, 0.0),
        bar(1, -0.2, 0.2, -0.5, 0.0),
        bar(2, 0.0, 9.6, -0.1, 9.0),
    ]
    trade = _legacy(
        0, 100.0, px(-2.0), px(9.5),
        direction=side, exit_index=2, exit_time=_iso(2), exit_price=px(9.5),
        outcome="TP1", r_multiple=4.75, holding_bars=2, time_to_tp1=2,
        mae=0.5, mfe=9.6, mae_r=0.25, mfe_r=4.75,
    )
    row = _run(trade, candles)

    assert row["outcome"] == "TP1"
    entry = row["entry_price"]
    assert entry == pytest.approx(px(-0.2) * (1 + sign * SLIP))
    risk = abs(entry - px(-2.0))
    realised = sign * (row["exit_price"] - entry) / risk
    assert realised > 4.75
    assert row["mfe"] == pytest.approx(abs(px(9.6) - entry))
    assert row["mae"] == pytest.approx(abs(entry - px(-0.5)))
    assert row["mfe_r"] == pytest.approx(row["mfe"] / risk)
    assert row["mae_r"] == pytest.approx(row["mae"] / risk)
    assert row["mfe_r"] >= realised
    assert row["mfe_r"] != trade["mfe_r"]


def test_realised_r_never_exceeds_mfe_r_in_the_row_book():
    row = _causal_row()
    winners = [t for t in row["causal_trades"] if (t.get("r_multiple") or 0) > 0]
    assert winners
    for trade in winners:
        assert trade["mfe_r"] >= trade["r_multiple"]


def test_causal_row_metrics_come_from_the_causal_trades():
    row = _causal_row()
    causal = row["causal_trades"]
    assert [t["outcome"] for t in causal] == ["TP1", "SL", OUTCOME_GAP_THROUGH_STOP, "OPEN"]
    assert row["trades"] == causal

    closed = causal[:3]
    rs = [t["r_multiple"] for t in closed]
    assert rs[0] > 0 > rs[1]
    assert rs[2] == 0.0
    assert row["r_values"] == rs
    assert row["equity_curve_r"] == [0.0, rs[0], rs[0] + rs[1], rs[0] + rs[1] + rs[2]]
    assert row["expectancy_R"] == expectancy_r(rs) == sum(rs) / 3
    assert row["profit_factor"] == profit_factor(rs) == rs[0] / abs(rs[1])
    assert row["tp1_hits"] == 1
    assert row["sl_hits"] == 1
    assert row["gap_through_stop_hits"] == 1
    assert row["tp1_hit_rate"] == pytest.approx(1 / 3)
    assert row["sl_rate"] == pytest.approx(1 / 3)
    # Same book as the count and the average beside them (that one is rounded).
    assert row["sample_size"] == 3
    assert row["expectancy_R"] == pytest.approx(row["average_R"], abs=1e-6)

    # None of them is the engine (legacy) value any more.
    for key in ("expectancy_R", "profit_factor", "tp1_hit_rate", "sl_rate", "sl_hits"):
        assert row[key] != LEGACY_RESULT[key], key
    assert row["r_values"] != LEGACY_RESULT["r_values"]
    assert row["equity_curve_r"] != LEGACY_RESULT["equity_curve_r"]

    # The legacy book stays under its explicit names.
    legacy = row["legacy_trades"]
    assert [t["outcome"] for t in legacy] == ["TP2", "SL", "SL", "TP1"]
    assert [t["r_multiple"] for t in legacy] == [4.0, -1.0, -1.0, 2.0]
    assert [t["holding_bars"] for t in legacy] == [7, 1, 1, 3]
    assert [t["exit_time"] for t in legacy] == [_iso(9), _iso(11), _iso(15), _iso(23)]
    assert "fill_time" not in legacy[0]
    assert row["optimistic_comparison"]["trades"] == 4
    assert row["optimistic_comparison"]["gross_r"] == pytest.approx(4.0)
    assert row["execution_comparison"]["legacy"]["trades"] == 4
    assert row["execution_comparison"]["causal"]["trades"] == 3


def test_causal_row_with_no_closed_trades_has_empty_metrics():
    metrics = causal_r_metrics([_run(_trade_d())])
    assert metrics == {
        "expectancy_R": None,
        "profit_factor": None,
        "tp1_hit_rate": None,
        "sl_rate": None,
        "tp1_hits": 0,
        "sl_hits": 0,
        "gap_through_stop_hits": 0,
        "gap_exit_hits": 0,
        "r_values": [],
        "equity_curve_r": [],
    }


def test_non_causal_row_still_reports_the_engine_values():
    row = build_matrix_row_postprocess(_payload("COMBO_02"))

    assert "pnl_model" not in row
    assert "gap_through_stop_hits" not in row
    assert "legacy_trades" not in row
    for key, value in LEGACY_RESULT.items():
        if key == "sample_size":
            continue
        assert row[key] == value, key
    assert row["sample_size"] == 4
    assert [t["holding_bars"] for t in row["trades"]] == [7, 1, 1, 3]
    assert [t["exit_time"] for t in row["trades"]] == [_iso(9), _iso(11), _iso(15), _iso(23)]
    assert all("fill_time" not in t for t in row["trades"])


# Pricing numbers of the causal book for the candles above. They were produced
# by the module before the display change and must not move. First trade by
# hand: entry 101 * 1.0005, exit 104 * 0.9995, R = (exit - entry) / (entry - 98).
EXPECTED_CAUSAL_TRADES = [{"entry_price": 101.0505,
  "exit_price": 103.94800000000001,
  "outcome": "TP1",
  "exit_index": 5,
  "fill_index": 3,
  "r_multiple": 0.9498442878216713,
  "r_gross": 0.9498442878216713,
  "r_net": 0.9229636453216715,
  "quantity": 6.55630224553352,
  "fees": -0.53761285,
  "gross_pnl_usd": 18.99688575643343,
  "net_pnl_usd": 18.45927290643343},
 {"entry_price": 106.053,
  "exit_price": 103.94800000000001,
  "outcome": "SL",
  "exit_index": 11,
  "fill_index": 11,
  "r_multiple": -1.025328787140766,
  "r_gross": -1.025328787140766,
  "r_net": -1.0662447146407659,
  "quantity": 9.741841207988323,
  "fees": -0.81831855,
  "gross_pnl_usd": -20.50657574281532,
  "net_pnl_usd": -21.32489429281532},
 {"entry_price": 97.04849999999999,
  "exit_price": 97.04849999999999,
  "outcome": "GAP_THROUGH_STOP",
  "exit_index": 15,
  "fill_index": 15,
  "r_multiple": 0.0,
  "r_gross": 0.0,
  "r_net": -0.08159621650026187,
  "quantity": 20.60825257474356,
  "fees": -1.6,
  "gross_pnl_usd": 0.0,
  "net_pnl_usd": -1.6},
 {"entry_price": 100.05,
  "exit_price": None,
  "outcome": "OPEN",
  "exit_index": 29,
  "fill_index": 21,
  # The one intended change: the old module left the legacy 2.0 here.
  "r_multiple": None,
  "r_gross": None,
  "r_net": None,
  "quantity": None,
  "fees": None,
  "gross_pnl_usd": None,
  "net_pnl_usd": None}]
EXPECTED_CAUSAL_ROW = {"pnl_usd": -1.50969,
 "pnl_usd_net": -4.465621,
 "fees_usd": -2.955931,
 "average_R": -0.02516133333333333,
 "average_R_net": -0.074959,
 "win_rate": 0.333333,
 "sample_size": 3,
 "max_drawdown_R": 1.147841,
 "max_drawdown_usd": 22.924894}


def test_display_change_does_not_move_any_pricing_number():
    row = _causal_row()
    causal = row["causal_trades"]
    assert len(causal) == len(EXPECTED_CAUSAL_TRADES) == 4
    for trade, expected in zip(causal, EXPECTED_CAUSAL_TRADES):
        for key, value in expected.items():
            if isinstance(value, float):
                assert trade.get(key) == pytest.approx(value, rel=1e-12, abs=1e-12), key
            else:
                assert trade.get(key) == value, key
    for key, value in EXPECTED_CAUSAL_ROW.items():
        if isinstance(value, float):
            assert row[key] == pytest.approx(value, rel=1e-12, abs=1e-12), key
        else:
            assert row[key] == value, key


def test_causal_profit_factor_is_none_not_infinite_when_no_trade_loses():
    import json

    from app.research.causal_execution import causal_r_metrics

    winners = [
        {"outcome": "TP1", "r_multiple": 2.0},
        {"outcome": "TP1", "r_multiple": 1.5},
    ]
    metrics = causal_r_metrics(winners)
    assert metrics["profit_factor"] is None
    assert metrics["expectancy_R"] == 1.75
    # The job response must stay valid JSON.
    json.dumps(metrics, allow_nan=False)

    mixed = winners + [{"outcome": "SL", "r_multiple": -1.0}]
    assert causal_r_metrics(mixed)["profit_factor"] == 3.5


# ---------------------------------------------------------------- audit #20: causal trade identity


def test_causal_trades_get_trade_no_and_trade_id_in_book_order():
    row = _causal_row()
    causal = row["causal_trades"]
    assert [t["trade_no"] for t in causal] == [1, 2, 3, 4]
    assert [t["trade_id"] for t in causal] == ["t1", "t2", "t3", "t4"]
    # Book order is the signal order the legacy rows are numbered in.
    assert [t["signal_index"] for t in causal] == [2, 10, 14, 20]
    assert row["trades"] == causal
    # The legacy book keeps its own numbering from the fee step.
    assert [t["trade_no"] for t in build_matrix_row_postprocess(_payload("COMBO_02"))["trades"]] == [1, 2, 3, 4]


def test_causal_identity_is_stamped_without_the_blotter_too():
    row = _causal_row(include_trades=False)
    assert row["trades"] == []
    assert [t["trade_id"] for t in row["causal_trades"]] == ["t1", "t2", "t3", "t4"]


def test_causal_identity_changes_no_other_field():
    from app.research.backtest_cpu_worker import number_causal_trades
    from app.research.causal_execution import build_execution_run_record
    from app.research.config import ResearchConfig

    cfg = ResearchConfig()
    cfg.execution_model = "DUAL_LEGACY_AND_CAUSAL"
    cfg.risk_usd = RISK_USD
    clear_isolated_execution_runs()
    record = build_execution_run_record(
        run_id="identity", symbol="BTCUSDT", timeframe="1h", candles=_candles(),
        trades=_trades(), config=cfg, account_equity=1000.0,
    )
    before = copy.deepcopy(record["causal_trades"])
    assert all(t.get("trade_id") is None and "trade_no" not in t for t in before)
    number_causal_trades(record["causal_trades"])
    for got, was in zip(record["causal_trades"], before):
        assert {k: v for k, v in got.items() if k not in ("trade_id", "trade_no")} == {
            k: v for k, v in was.items() if k != "trade_id"
        }


def test_causal_rows_have_unique_blotter_and_replay_keys():
    from app.research.vectorbt_orders_combo import combo_replay_orders

    row = _causal_row()
    keys = [f"n{t['trade_no']}" for t in row["trades"]]  # frontend tradeRowKey
    assert len(set(keys)) == len(keys)
    orders = combo_replay_orders("COMBO_02_V2", row["trades"])
    # Closed trades only, keyed by trade number at the causal fill bar.
    assert [o.trade_key for o in orders] == ["1@3", "2@11", "3@15"]
