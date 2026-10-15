"""CANDLE12_V3_EXEC_FIX gap acceptance: one deterministic BTCUSDT 1h fixture.

Six events, each checked against values worked out here by hand (no engine
helper is used for the expected numbers):

  A LONG gap through entry        B SHORT gap through entry
  C LONG gap through stop         D SHORT gap through stop
  E LONG bar opening beyond TP1   F LONG same-bar SL + TP, no gap

Book: the V3 defaults on BTCUSDT (step 0.001, min qty 0.001, min notional 50,
risk tolerance 10%), $20 risk, $1,000 equity, 2x, taker 0.04%, slippage 0.02%.
The Candle-1 detector is replaced by hand-made setups so each event is exact.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.research import candle12_v3 as v3
from app.research.candle12_v3 import Candle12V3Config, run_candle12_v3

T0 = datetime(2025, 3, 3, tzinfo=timezone.utc)
SLIP = 0.0002
FEE = 0.0004
RISK = 20.0
ATR = 1000.0  # stop buffer 0.2 x ATR = 200

# Candle-1 shapes at the 60,000 level.
LONG_C1 = (59_400.0, 60_000.0, 59_100.0, 59_900.0)  # trigger 60,000, stop 59,100 - 200 = 58,900
SHORT_C1 = (60_600.0, 60_900.0, 60_000.0, 60_100.0)  # trigger 60,000, stop 60,900 + 200 = 61,100

# event: (side, Candle-2 OHLC, next bar OHLC)
EVENTS = {
    "A": ("LONG", (60_300, 60_400, 60_250, 60_350), (60_400, 63_200, 60_300, 63_000)),
    "B": ("SHORT", (59_700, 59_750, 59_600, 59_650), (59_600, 59_700, 56_800, 57_000)),
    "C": ("LONG", (59_950, 60_100, 59_900, 60_050), (58_500, 58_600, 58_400, 58_450)),
    "D": ("SHORT", (60_050, 60_080, 59_900, 59_950), (61_500, 61_600, 61_400, 61_450)),
    "E": ("LONG", (59_950, 60_100, 59_900, 60_050), (62_500, 62_600, 58_000, 62_400)),
    "F": ("LONG", (59_950, 60_100, 59_900, 60_050), (60_050, 62_300, 58_800, 60_000)),
}


def _bar(i, o, h, l, c):
    return {"time": T0 + timedelta(hours=i), "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": 1.0}


def build_fixture():
    """Quiet bars, then per event: Candle-1, Candle-2, the decisive bar, two quiet bars."""
    candles = [_bar(i, 60_000, 60_000, 60_000, 60_000) for i in range(5)]
    c1_index = {}
    for name, (side, c2, nxt) in EVENTS.items():
        i = len(candles)
        c1_index[name] = i
        candles.append(_bar(i, *(LONG_C1 if side == "LONG" else SHORT_C1)))
        candles.append(_bar(i + 1, *c2))
        candles.append(_bar(i + 2, *nxt))
        candles += [_bar(i + 3 + j, 60_000, 60_000, 60_000, 60_000) for j in range(2)]
    return candles, c1_index


@pytest.fixture
def fixture(monkeypatch):
    candles, c1_index = build_fixture()
    setups = {}
    for name, idx in c1_index.items():
        side = EVENTS[name][0]
        o, h, l, c = candles[idx]["open"], candles[idx]["high"], candles[idx]["low"], candles[idx]["close"]
        setups[idx] = {
            "direction": side,
            "c1_ohlc": {"open": o, "high": h, "low": l, "close": c},
            "pullback": {},
            "impulse": {"impulse_origin": l if side == "LONG" else h},
            "atr": ATR,
            "swings": [],
            "structure_offset": 0,
        }
    monkeypatch.setattr(v3, "detect_c1_v2", lambda **kw: setups.get(kw["candle1_index"]))
    trades = {}
    for side in ("LONG", "SHORT"):
        cfg = Candle12V3Config(book_side=side, min_bars=2)
        res = run_candle12_v3("BTCUSDT", "1h", candles, config=cfg, index_start=2)
        assert res["rejected"] == [] and res["exchange_filter"]["min_notional"] == 50.0
        for t in res["trades"]:
            name = next(n for n, i in c1_index.items() if i == t["candle1_index"])
            trades[name] = t
    return trades


def expected(side, *, fill, stop, raw_exit, entry_base):
    """Hand calculation of every money field for a one-leg trade."""
    long_ = side == "LONG"
    rpu = abs(fill - stop)
    target_qty = min(RISK / rpu, 1000.0 * 2.0 / fill)
    qty = math.floor(target_qty * 1000 + 1e-9) / 1000
    exit_fill = raw_exit * (1 - SLIP) if long_ else raw_exit * (1 + SLIP)
    gross = qty * ((exit_fill - fill) if long_ else (fill - exit_fill))
    fee_in = FEE * qty * fill
    fee_out = FEE * qty * exit_fill
    net = gross - fee_in - fee_out
    actual = qty * rpu
    return {
        "qty": qty,
        "exit_fill": exit_fill,
        "fee_in": fee_in,
        "fee_out": fee_out,
        "gross": gross,
        "net": net,
        "actual_risk": actual,
        "r_gross": gross / actual,
        "r_net_actual": net / actual,
        "r_net_intended": net / RISK,
        "slippage_cost": qty * abs(fill - entry_base) + qty * abs(raw_exit - exit_fill),
    }


def check(t, side, *, fill, stop, raw_exit, entry_base, outcome, tags):
    e = expected(side, fill=fill, stop=stop, raw_exit=raw_exit, entry_base=entry_base)
    assert t["direction"] == side
    assert t["entry_price"] == pytest.approx(fill, rel=1e-12)
    assert t["stop_price"] == pytest.approx(stop, rel=1e-12)
    assert t["raw_exit_price"] == pytest.approx(raw_exit, rel=1e-12)
    assert t["exit_price"] == pytest.approx(e["exit_fill"], rel=1e-12)
    assert t["qty"] == pytest.approx(e["qty"], rel=1e-12)
    assert t["fee_entry_usd"] == pytest.approx(-e["fee_in"], rel=1e-9)
    assert t["fee_exit_usd"] == pytest.approx(-e["fee_out"], rel=1e-9)
    assert t["gross_pnl_usd"] == pytest.approx(e["gross"], rel=1e-9)
    assert t["net_pnl_usd"] == pytest.approx(e["net"], rel=1e-9)
    assert t["risk_usd"] == pytest.approx(e["actual_risk"], rel=1e-9)
    assert t["r_gross"] == pytest.approx(e["r_gross"], rel=1e-9)
    assert t["r_net_actual_risk"] == pytest.approx(e["r_net_actual"], rel=1e-9)
    assert t["r_net_intended_risk"] == pytest.approx(e["r_net_intended"], rel=1e-9)
    assert t["slippage_cost_usd"] == pytest.approx(e["slippage_cost"], rel=1e-9)
    assert t["funding_usd"] == 0.0
    assert t["outcome"] == outcome
    assert set(t["tags"]) == set(tags)
    return e


def test_a_long_gap_through_entry(fixture):
    fill = 60_300 * (1 + SLIP)  # Candle-2 opened at 60,300, above the 60,000 trigger
    rpu = fill - 58_900
    tp1 = fill + 2 * rpu
    e = check(fixture["A"], "LONG", fill=fill, stop=58_900, raw_exit=tp1, entry_base=60_300,
              outcome="TP1", tags=["GAP_ENTRY_FILL"])
    assert e["qty"] == pytest.approx(0.014)
    assert fill > 60_000 * (1 + SLIP)  # worse than the untraded trigger fill


def test_b_short_gap_through_entry(fixture):
    fill = 59_700 * (1 - SLIP)
    rpu = 61_100 - fill
    tp1 = fill - 2 * rpu
    e = check(fixture["B"], "SHORT", fill=fill, stop=61_100, raw_exit=tp1, entry_base=59_700,
              outcome="TP1", tags=["GAP_ENTRY_FILL"])
    assert e["qty"] == pytest.approx(0.014)


def test_c_long_gap_through_stop(fixture):
    fill = 60_000 * (1 + SLIP)
    e = check(fixture["C"], "LONG", fill=fill, stop=58_900, raw_exit=58_500, entry_base=60_000,
              outcome="SL", tags=["GAP_STOP_FILL"])
    assert e["qty"] == pytest.approx(0.017)
    assert fixture["C"]["exit_price"] == pytest.approx(58_500 * (1 - SLIP))
    assert e["r_net_actual"] < -1.3  # the gap costs more than 1R


def test_d_short_gap_through_stop(fixture):
    fill = 60_000 * (1 - SLIP)
    e = check(fixture["D"], "SHORT", fill=fill, stop=61_100, raw_exit=61_500, entry_base=60_000,
              outcome="SL", tags=["GAP_STOP_FILL"])
    assert fixture["D"]["exit_price"] == pytest.approx(61_500 * (1 + SLIP))
    assert e["r_net_actual"] < -1.3


def test_e_bar_opening_beyond_tp1(fixture):
    fill = 60_000 * (1 + SLIP)
    tp1 = fill + 2 * (fill - 58_900)
    assert 62_500 > tp1  # the bar opens beyond TP1; its low also trades through the stop
    check(fixture["E"], "LONG", fill=fill, stop=58_900, raw_exit=tp1, entry_base=60_000,
          outcome="TP1", tags=["GAP_TARGET_FILL"])


def test_f_same_bar_sl_and_tp_without_gap(fixture):
    fill = 60_000 * (1 + SLIP)
    tp1 = fill + 2 * (fill - 58_900)
    bar = EVENTS["F"][2]
    assert bar[1] >= tp1 and bar[2] <= 58_900 and 58_900 < bar[0] < tp1
    check(fixture["F"], "LONG", fill=fill, stop=58_900, raw_exit=58_900, entry_base=60_000,
          outcome="SL", tags=["CONSERVATIVE_SAME_BAR_SL"])


def test_fixture_is_deterministic():
    a, ia = build_fixture()
    b, ib = build_fixture()
    assert a == b and ia == ib
