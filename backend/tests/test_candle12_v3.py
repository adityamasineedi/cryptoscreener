"""CANDLE12_V3_EXEC_FIX regression tests. CANDLE12_V2 stays the frozen baseline."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.research import candle12_v3 as v3
from app.research.candle12_v3 import (
    TAG_GAP_ENTRY,
    TAG_GAP_STOP,
    TAG_GAP_TARGET,
    TAG_MAX_HOLD,
    TAG_SAME_BAR_SL,
    TAG_SIZE_REJECTED,
    Candle12V3Config,
    breakeven_stop,
    entry_fill,
    run_candle12_v3,
    size_position,
)
from app.research.engine_backtest import _run_candle12
from app.signals.schemas import SwingRecord

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
S = 0.0002
FEE = 0.0004


def _bar(i: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": T0 + timedelta(hours=i), "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def _quiet(i: int, px: float = 100.0) -> dict:
    return _bar(i, px, px + 0.05, px - 0.05, px)


def _swing(kind: str, idx: int, price: float) -> SwingRecord:
    return SwingRecord(
        symbol="X", timeframe="1h", swing_type=kind, price=price, timestamp=None,
        bar_index=idx, strength=1.0, confirmed_at=None,
    )


def _setup(direction: str, c1: dict, *, atr: float = 1.0, swings=None) -> dict:
    origin = c1["low"] if direction == "LONG" else c1["high"]
    return {
        "direction": direction,
        "c1_ohlc": {k: c1[k] for k in ("open", "high", "low", "close")},
        "pullback": {},
        "impulse": {"impulse_origin": origin},
        "atr": atr,
        "swings": swings or [],
        "structure_offset": 0,
        "bos_level": None,
        "setup_as_of_index": None,
    }


@pytest.fixture
def fake_setups(monkeypatch):
    """Replace the frozen detector with hand-made setups keyed by Candle-1 index."""
    table: dict[int, dict] = {}

    def detect(**kw):
        return table.get(kw["candle1_index"])

    monkeypatch.setattr(v3, "detect_c1_v2", detect)
    return table


def _cfg(**kw) -> Candle12V3Config:
    kw.setdefault("min_bars", 2)
    kw.setdefault("apply_exchange_filters", False)
    # Tests that are not about the risk tolerance keep it off so a capped
    # synthetic trade is not rejected.
    kw.setdefault("risk_tolerance_pct", None)
    return Candle12V3Config(**kw)


def _run(candles, cfg, **kw):
    return run_candle12_v3("X", "1h", candles, config=cfg, index_start=2, **kw)


def _long_case(after: list[tuple[float, float, float, float]]):
    """C1 at 5: H100 L94, ATR 5 -> stop 93. C2 at 6 trades through 100.02."""
    c = [_quiet(i, 97) for i in range(5)]
    c.append(_bar(5, 95, 100, 94, 99.5))
    c.append(_bar(6, 99.6, 101, 99, 100.5))
    for j, (o, h, l, cl) in enumerate(after):
        c.append(_bar(7 + j, o, h, l, cl))
    return c


def _short_case(after: list[tuple[float, float, float, float]]):
    """C1 at 5: H106 L100, ATR 5 -> stop 107. C2 at 6 trades through 99.98."""
    c = [_quiet(i, 103) for i in range(5)]
    c.append(_bar(5, 105, 106, 100, 100.5))
    c.append(_bar(6, 100.4, 101, 99, 99.5))
    for j, (o, h, l, cl) in enumerate(after):
        c.append(_bar(7 + j, o, h, l, cl))
    return c


# 1
def test_gap_through_long_entry_fills_at_c2_open_plus_slippage(fake_setups):
    c = [_quiet(i, 97) for i in range(5)]
    c.append(_bar(5, 95, 100, 94, 99.5))
    c.append(_bar(6, 102, 103, 101.5, 102.5))
    c += [_quiet(7 + j, 102.5) for j in range(3)]
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _cfg())["trades"][0]
    assert t["entry_price"] == pytest.approx(102 * (1 + S))
    assert t["entry_price"] > 100 * (1 + S)
    assert TAG_GAP_ENTRY in t["tags"]


# 2
def test_gap_through_short_entry_fills_at_c2_open_plus_slippage(fake_setups):
    c = [_quiet(i, 103) for i in range(5)]
    c.append(_bar(5, 105, 106, 100, 100.5))
    c.append(_bar(6, 98, 98.5, 97, 97.5))
    c += [_quiet(7 + j, 97.5) for j in range(3)]
    fake_setups[5] = _setup("SHORT", c[5], atr=5)
    t = _run(c, _cfg(book_side="SHORT"))["trades"][0]
    assert t["entry_price"] == pytest.approx(98 * (1 - S))
    assert TAG_GAP_ENTRY in t["tags"]


def test_entry_fill_never_uses_an_untraded_price():
    cfg = _cfg()
    assert entry_fill(direction="LONG", c1_high=100, c1_low=94, c2_open=99.6, c2_high=100.01,
                      c2_low=99, cfg=cfg) is None
    normal = entry_fill(direction="LONG", c1_high=100, c1_low=94, c2_open=99.6, c2_high=101,
                        c2_low=99, cfg=cfg)
    assert normal["fill"] == pytest.approx(100 * (1 + S)) and not normal["gap"]
    extra = entry_fill(direction="LONG", c1_high=100, c1_low=94, c2_open=99.6, c2_high=101,
                       c2_low=99, cfg=_cfg(entry_slippage_mode="additional"))
    assert extra["fill"] == pytest.approx(100 * (1 + S) ** 2)


# 3
def test_gap_through_long_stop_exits_at_open_minus_slippage(fake_setups):
    c = _long_case([(90, 91, 89, 90.5), (90, 90, 90, 90)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _cfg())["trades"][0]
    assert t["outcome"] == "SL" and t["exit_index"] == 7
    assert t["exit_price"] == pytest.approx(90 * (1 - S))
    assert TAG_GAP_STOP in t["tags"]
    qty = t["qty"]
    gross = qty * (90 * (1 - S) - t["entry_price"])
    fees = FEE * qty * (t["entry_price"] + 90 * (1 - S))
    assert t["net_pnl_usd"] == pytest.approx(gross - fees)


# 4
def test_gap_through_short_stop_exits_at_open_plus_slippage(fake_setups):
    c = _short_case([(110, 111, 109, 110.5), (110, 110, 110, 110)])
    fake_setups[5] = _setup("SHORT", c[5], atr=5)
    t = _run(c, _cfg(book_side="SHORT"))["trades"][0]
    assert t["outcome"] == "SL"
    assert t["exit_price"] == pytest.approx(110 * (1 + S))
    assert TAG_GAP_STOP in t["tags"]


# 5
def test_same_bar_sl_and_tp_without_gap_takes_the_stop(fake_setups):
    c = _long_case([(100, 115, 92, 101)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _cfg())["trades"][0]
    assert t["outcome"] == "SL"
    assert t["raw_exit_price"] == pytest.approx(93.0)
    assert TAG_SAME_BAR_SL in t["tags"]


def test_bar_opening_beyond_target_fills_at_target_level(fake_setups):
    c = _long_case([(120, 121, 92, 119)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _cfg())["trades"][0]
    assert t["outcome"] == "TP1"
    assert t["raw_exit_price"] == pytest.approx(t["tp1"])
    assert t["exit_price"] == pytest.approx(t["tp1"] * (1 - S))
    assert TAG_GAP_TARGET in t["tags"]


def _short_then_long():
    """SHORT setup at 5 stays open; LONG setup at 8 fills at 9."""
    c = [_quiet(i) for i in range(5)]
    c.append(_bar(5, 101, 101.5, 99.6, 99.8))
    c.append(_bar(6, 99.8, 99.9, 99.5, 99.7))
    c.append(_quiet(7, 99.7))
    c.append(_bar(8, 99.7, 100.2, 99.6, 100.1))
    c.append(_bar(9, 100.1, 100.5, 100.0, 100.4))
    c += [_quiet(10 + j, 100.3) for j in range(5)]
    return c


def _mirror(candles):
    return [
        {**b, "open": 200 - b["open"], "close": 200 - b["close"], "high": 200 - b["low"], "low": 200 - b["high"]}
        for b in candles
    ]


# 6
def test_long_only_book_is_never_blocked_by_a_short(fake_setups):
    c = _short_then_long()
    fake_setups[5] = _setup("SHORT", c[5])
    fake_setups[8] = _setup("LONG", c[8])
    res = _run(c, _cfg(book_side="LONG"))
    assert [t["entry_index"] for t in res["trades"]] == [9]
    assert res["counts"]["blocked_by_opposite"] == 0


# 7
def test_short_only_book_is_never_blocked_by_a_long(fake_setups):
    c = _mirror(_short_then_long())
    fake_setups[5] = _setup("LONG", c[5])
    fake_setups[8] = _setup("SHORT", c[8])
    res = _run(c, _cfg(book_side="SHORT"))
    assert [t["entry_index"] for t in res["trades"]] == [9]


# 8
def test_shared_lock_blocks_the_opposite_side(fake_setups):
    c = _short_then_long()
    fake_setups[5] = _setup("SHORT", c[5])
    fake_setups[8] = _setup("LONG", c[8])
    res = _run(c, _cfg(book_side="BOTH", portfolio_lock_mode="shared"))
    assert [(t["direction"], t["entry_index"]) for t in res["trades"]] == [("SHORT", 6)]
    assert res["counts"]["blocked_by_opposite"] == 1
    per_side = _run(c, _cfg(book_side="BOTH", portfolio_lock_mode="per_side"))
    assert len(per_side["trades"]) == 2


def test_shared_lock_needs_a_combined_book():
    with pytest.raises(ValueError):
        Candle12V3Config(book_side="LONG", portfolio_lock_mode="shared")


def _refire_case(second_swings):
    """Two LONG setups breaking the same swing high (bar 2, 99). The first stops out at 8."""
    c = [_quiet(i, 97) for i in range(5)]
    c.append(_bar(5, 95, 100, 94, 99.5))
    c.append(_bar(6, 99.6, 101, 99, 100.5))
    c.append(_quiet(7, 100.5))
    c.append(_bar(8, 100, 100.2, 92, 93))
    c += [_quiet(9 + j, 95) for j in range(3)]
    c.append(_bar(12, 95, 100, 94, 99.5))
    c.append(_bar(13, 99.6, 101, 99, 100.5))
    c += [_quiet(14 + j, 100.5) for j in range(3)]
    first = [_swing("HIGH", 2, 99.0), _swing("LOW", 3, 96.0)]
    return c, first, second_swings


# 9
def test_once_per_level_blocks_a_repeat_break_of_the_same_level(fake_setups):
    c, first, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5, swings=first)
    fake_setups[12] = _setup("LONG", c[12], atr=5, swings=first)
    res = _run(c, _cfg(bos_refire_policy="once_per_level"))
    assert [t["candle1_index"] for t in res["trades"]] == [5]
    assert res["counts"]["refire_suppressed"] == 1


def test_once_per_level_rearms_after_a_new_opposing_swing(fake_setups):
    c, first, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5, swings=first)
    fake_setups[12] = _setup("LONG", c[12], atr=5, swings=first + [_swing("LOW", 9, 94.5)])
    res = _run(c, _cfg(bos_refire_policy="once_per_level"))
    assert [t["candle1_index"] for t in res["trades"]] == [5, 12]


# 10
def test_every_close_keeps_the_v2_refire(fake_setups):
    c, first, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5, swings=first)
    fake_setups[12] = _setup("LONG", c[12], atr=5, swings=first)
    res = _run(c, _cfg(bos_refire_policy="every_close"))
    assert [t["candle1_index"] for t in res["trades"]] == [5, 12]
    assert res["counts"]["refire_suppressed"] == 0


def _synthetic(seed: int = 2, n: int = 3000) -> list[dict]:
    r = random.Random(seed)
    p, drift, out = 100.0, 0.0, []
    for i in range(n):
        if i % 150 == 0:
            drift = r.choice([-1, 1]) * 0.0006
        big = r.random() < 0.06
        ret = drift + r.gauss(0, 0.004 if big else 0.0015) + (r.choice([-1, 1]) * 0.006 if big else 0)
        o, c = p, p * (1 + ret)
        wick = 0.0006 if big else 0.001
        h = max(o, c) * (1 + abs(r.gauss(0, wick)))
        lo = min(o, c) * (1 - abs(r.gauss(0, wick)))
        v = r.lognormvariate(0, 0.3) * (3 if big else 1)
        out.append({"time": T0 + timedelta(hours=i), "open": o, "high": h, "low": lo, "close": c, "volume": v})
        p = c
    return out


# 11
def test_nearest_target_full_exit_matches_v2_on_non_gap_trades():
    candles = _synthetic()
    sizing = {"risk_usd": 20.0, "taker_fee": FEE, "maker_fee": 0.0002, "leverage": 2.0, "account_equity": 1000.0}
    base = []
    for side in ("LONG", "SHORT"):
        trades, _ = _run_candle12(
            {"symbol": "SYN", "timeframe": "1h", "candles": candles, "index_start": 300, "direction": side}, sizing
        )
        base += trades
    cfg = Candle12V3Config(
        book_side="BOTH", portfolio_lock_mode="shared", bos_refire_policy="every_close",
        apply_exchange_filters=False, risk_tolerance_pct=None,
    )
    res = run_candle12_v3("SYN", "1h", candles, config=cfg, index_start=300)
    new = {t["entry_index"]: t for t in res["trades"] if t["outcome"] != "OPEN"}
    gap_tags = {TAG_GAP_ENTRY, TAG_GAP_STOP, TAG_GAP_TARGET}
    assert len(base) >= 15
    assert {t["entry_index"] for t in base} == set(new)
    for old in base:
        t = new[old["entry_index"]]
        if gap_tags & set(t["tags"]):
            continue
        assert t["direction"] == old["direction"]
        assert t["entry_price"] == pytest.approx(old["entry_price"], rel=1e-12)
        assert t["stop_price"] == pytest.approx(old["stop_price"], rel=1e-12)
        assert t["outcome"] == old["outcome"] and t["exit_index"] == old["exit_index"]
        assert t["qty"] == pytest.approx(old["qty"], rel=1e-12)
        assert t["net_pnl_usd"] == pytest.approx(old["net_pnl_usd"], abs=1e-6)
        assert t["r_net_actual_risk"] == pytest.approx(old["r_net"], abs=1e-9)


# 12
def test_quantity_rounding_resizes_or_rejects():
    cfg = _cfg(size_rounding_tolerance=0.10)
    ok = size_position(entry=100, stop=90, cfg=cfg, step=0.3, min_qty=0.3)
    assert ok["qty"] == pytest.approx(1.8) and ok["rejected"] is None
    assert size_position(entry=100, stop=90, cfg=cfg, step=0.7, min_qty=0.7)["rejected"] == "ROUNDING_SHORTFALL"
    assert size_position(entry=100, stop=90, cfg=cfg, step=0.1, min_qty=5)["rejected"] == "BELOW_MIN_QTY"
    btc = Candle12V3Config().exchange_filter("BTCUSDT")
    assert btc[:3] == (0.001, 0.001, 50.0)
    assert Candle12V3Config().exchange_filter("ETHUSDT")[:3] == (0.001, 0.001, 20.0)
    assert Candle12V3Config().exchange_filter("DOGEUSDT")[3] == "no_exchange_filter"
    q = size_position(entry=60000, stop=59000, cfg=Candle12V3Config(), step=0.001, min_qty=0.001)["qty"]
    assert round(q / 0.001, 9).is_integer() and q <= 20 / 1000


def test_size_rejected_trade_takes_no_position(fake_setups):
    c, first, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    res = _run(c, _cfg(qty_step=10.0, min_qty=10.0))
    assert res["trades"] == []
    assert res["rejected"][0]["outcome"] == TAG_SIZE_REJECTED
    assert res["counts"]["size_rejected"] == 1


# 13
def test_funding_zero_when_disabled_and_accumulated_when_enabled(fake_setups):
    c = _long_case([(100.5, 100.8, 100.2, 100.5)] * 4 + [(100.5, 100.6, 92, 93)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    rows = [
        {"time": T0 + timedelta(hours=6), "rate": 0.001, "mark_price": 100.0},  # at C2 open: not held
        {"time": T0 + timedelta(hours=8), "rate": 0.0001, "mark_price": 101.0},
        {"time": T0 + timedelta(hours=11), "rate": -0.0002, "mark_price": None},  # exit-bar open: held
        {"time": T0 + timedelta(hours=12), "rate": 0.001, "mark_price": 100.0},  # after exit
    ]
    off = _run(c, _cfg(), funding=rows)["trades"][0]
    assert off["funding_usd"] == 0.0 and off["funding_events"] == 0
    on = _run(c, _cfg(funding_enabled=True), funding=rows)["trades"][0]
    qty = on["qty"]
    expected = -0.0001 * qty * 101.0 + 0.0002 * qty * 100.5
    assert on["exit_index"] == 11 and on["funding_events"] == 2
    assert on["funding_usd"] == pytest.approx(expected)
    assert on["net_pnl_usd"] == pytest.approx(off["net_pnl_usd"] + expected)
    assert on["fee_total_usd"] == pytest.approx(off["fee_total_usd"])
    assert on["funding_coverage"] == "FULL"
    with pytest.raises(ValueError):
        _run(c, _cfg(funding_enabled=True))


# 14
def test_capped_trade_reports_actual_and_intended_r_separately(fake_setups):
    c = [_quiet(i, 100) for i in range(5)]
    c.append(_bar(5, 99.95, 100.0, 99.9, 99.99))
    c.append(_bar(6, 99.99, 100.1, 99.95, 100.05))
    c.append(_bar(7, 100.05, 101, 99.8, 100))
    fake_setups[5] = _setup("LONG", c[5], atr=0.1)
    t = _run(c, _cfg())["trades"][0]
    assert t["capped_by_leverage"]
    assert t["risk_usd"] < 20.0
    assert t["r_net_actual_risk"] == pytest.approx(t["net_pnl_usd"] / t["risk_usd"])
    assert t["r_net_intended_risk"] == pytest.approx(t["net_pnl_usd"] / 20.0)
    assert t["r_net_actual_risk"] != pytest.approx(t["r_net_intended_risk"])
    assert t["pnl_usd"] == t["net_pnl_usd"]


def test_tp1_runner_partial_breakeven_and_max_hold(fake_setups):
    # entry 100.02, stop 93, risk 7.02 -> TP1 114.06 (2R), TP2 121.08 (3R)
    after = [(100.5, 115, 100, 114.5)] + [(114.5, 115, 113.5, 114)] * 3
    c = _long_case(after)
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    cfg = _cfg(target_policy="tp1_runner", max_hold_bars=4)
    t = _run(c, cfg)["trades"][0]
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "MAX_HOLD"]
    assert t["legs"][0]["qty"] == pytest.approx(t["qty"] / 2)
    assert TAG_MAX_HOLD in t["tags"] and "BREAKEVEN_STOP_SET" in t["tags"]
    assert t["exit_index"] == 6 + 4
    be = breakeven_stop("LONG", t["entry_price"], fee=FEE, exit_slippage=S)
    assert t["final_stop_price"] == pytest.approx(be)
    # A break to breakeven after TP1 nets about zero on the runner.
    c2 = _long_case([(100.5, 115, 100, 114.5), (114, 114.2, 99, 100)])
    t2 = _run(c2, cfg)["trades"][0]
    runner = t2["legs"][1]
    assert runner["reason"] == "SL" and runner["raw_exit"] == pytest.approx(be)
    q = runner["qty"]
    leg_net = q * (runner["fill"] - t2["entry_price"]) - FEE * q * (t2["entry_price"] + runner["fill"])
    assert leg_net == pytest.approx(0.0, abs=1e-9)


def test_backtest_adapter_reads_context_from_candle1(fake_setups):
    from app.research.engine_backtest import run_engine_cell

    c = [_quiet(i, 97) for i in range(60)]
    c.append(_bar(60, 95, 100, 94, 99.5))
    c.append(_bar(61, 99.6, 101, 99, 100.5))
    c += [_bar(62 + j, 100.5, 101, 100, 100.5) for j in range(5)]
    c.append(_bar(67, 100.5, 100.6, 92, 93))
    fake_setups[60] = _setup("LONG", c[60], atr=5)
    out = run_engine_cell({
        "strategy_id": "CANDLE12_V3_EXEC_FIX", "direction": "LONG", "symbol": "X", "timeframe": "1h",
        "candles": c, "index_start": 55, "risk_usd": 20.0, "taker_fee": FEE, "maker_fee": 0.0002,
        "leverage": 2.0, "principal": 1000.0, "market_cap": None,
    })
    t = out["trades"][0]
    assert t["entry_time"] == t["signal_time"] == c[61]["time"].isoformat()
    assert t["market_context"]["decision_bar"] == c[60]["time"].isoformat()


# ---------------------------------------------------------------- minimum notional


def _notional_cfg() -> Candle12V3Config:
    # Isolate the notional rule: no rounding-shortfall or risk-tolerance rejection.
    return _cfg(size_rounding_tolerance=1.0)


def test_min_notional_exactly_at_minimum_is_accepted():
    # qty 20 / 20000 = 0.001 BTC at 50,000 = 50.00 USDT, the BTCUSDT minimum.
    out = size_position(entry=50_000, stop=30_000, cfg=_notional_cfg(), step=0.001, min_qty=0.001, min_notional=50.0)
    assert out["qty"] == pytest.approx(0.001)
    assert out["notional_usd"] == pytest.approx(50.0)
    assert out["rejected"] is None


def test_min_notional_below_minimum_is_rejected():
    out = size_position(entry=49_990, stop=29_990, cfg=_notional_cfg(), step=0.001, min_qty=0.001, min_notional=50.0)
    assert out["qty"] == pytest.approx(0.001)
    assert out["notional_usd"] == pytest.approx(49.99)
    assert out["rejected"] == "MIN_NOTIONAL_REJECTED"


def test_min_notional_after_rounding():
    # Pre-rounding 0.0019 BTC. Floored to 0.001: at 50,000 that is exactly 50 (kept);
    # at 40,000 it is 40 (rejected) although 0.0019 x 40,000 = 76 was above the minimum.
    kept = size_position(entry=50_000, stop=50_000 - 20 / 0.0019, cfg=_notional_cfg(), step=0.001,
                         min_qty=0.001, min_notional=50.0)
    assert kept["target_quantity"] == pytest.approx(0.0019)
    assert kept["qty"] == pytest.approx(0.001) and kept["rejected"] is None
    dropped = size_position(entry=40_000, stop=40_000 - 20 / 0.0019, cfg=_notional_cfg(), step=0.001,
                            min_qty=0.001, min_notional=50.0)
    assert dropped["target_quantity"] * 40_000 > 50.0
    assert dropped["rejected"] == "MIN_NOTIONAL_REJECTED"


def test_min_notional_rejected_trade_is_tagged_and_takes_no_position(fake_setups):
    c, _, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    res = _run(c, _cfg(min_notional=1000.0, size_rounding_tolerance=1.0))
    assert res["trades"] == []
    row = res["rejected"][0]
    assert row["outcome"] == "MIN_NOTIONAL_REJECTED" and "MIN_NOTIONAL_REJECTED" in row["tags"]
    assert res["counts"]["min_notional_rejected"] == 1


# ---------------------------------------------------------------- risk tolerance


def test_risk_tolerance_rejects_a_margin_capped_trade(fake_setups):
    c = [_quiet(i, 100) for i in range(5)]
    c.append(_bar(5, 99.95, 100.0, 99.9, 99.99))
    c.append(_bar(6, 99.99, 100.1, 99.95, 100.05))
    c.append(_bar(7, 100.05, 101, 99.8, 100))
    fake_setups[5] = _setup("LONG", c[5], atr=0.1)
    res = _run(c, _cfg(risk_tolerance_pct=0.10))
    assert res["trades"] == []
    row = res["rejected"][0]
    assert row["capped_by_leverage"] and row["outcome"] == "RISK_TOLERANCE_REJECTED"
    assert row["rejection_reason"] == "RISK_TOLERANCE_REJECTED"
    assert row["intended_risk_usd"] == 20.0
    assert row["actual_risk_usd"] == pytest.approx(row["qty"] * row["risk_per_unit"])
    assert row["risk_diff_usd"] == pytest.approx(row["actual_risk_usd"] - 20.0)
    assert row["risk_diff_pct"] > 0.10


def test_risk_tolerance_boundary_and_off_switch():
    cfg = _cfg(risk_tolerance_pct=0.10, size_rounding_tolerance=1.0)
    at_edge = size_position(entry=100, stop=90, cfg=cfg, step=0.3, min_qty=None)  # 1.8 -> $18, 10% short
    assert at_edge["risk_diff_pct"] == pytest.approx(0.10) and at_edge["rejected"] is None
    past = size_position(entry=100, stop=90, cfg=cfg, step=0.7, min_qty=None)  # 1.4 -> $14, 30% short
    assert past["rejected"] == "RISK_TOLERANCE_REJECTED"
    off = size_position(entry=100, stop=90, cfg=_cfg(size_rounding_tolerance=1.0), step=0.7, min_qty=None)
    assert off["rejected"] is None and off["risk_diff_usd"] == pytest.approx(-6.0)


# ---------------------------------------------------------------- breakeven (once_at_tp1_close)
# LONG case: entry 100.02, stop 93, R 7.02 -> TP1 114.06, TP2 121.08, TP3 128.10.

BE = breakeven_stop("LONG", 100 * (1 + S), fee=FEE, exit_slippage=S)


def _runner_cfg(**kw) -> Candle12V3Config:
    return _cfg(target_policy="tp1_runner", **kw)


def test_breakeven_set_when_above_old_stop_and_below_close(fake_setups):
    c = _long_case([(100.5, 115, 100, 114.5), (114, 114.2, BE + 0.01, 114)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg(max_hold_bars=2))["trades"][0]
    assert "BREAKEVEN_STOP_SET" in t["tags"]
    assert t["final_stop_price"] == pytest.approx(BE)
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "MAX_HOLD"]


def test_breakeven_skipped_when_tp1_bar_closes_below_it_and_never_rechecked(fake_setups):
    after = [(100.5, 115, 100, BE - 0.05), (100.2, 112, 99, 111), (111, 113, 110, 112)]
    c = _long_case(after + [(112, 112.5, 92, 93)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg())["trades"][0]
    assert "BREAKEVEN_STOP_SKIPPED" in t["tags"] and "BREAKEVEN_STOP_SET" not in t["tags"]
    assert t["final_stop_price"] == pytest.approx(93.0)
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "SL"]
    assert t["legs"][1]["raw_exit"] == pytest.approx(93.0)


def test_breakeven_skipped_when_below_the_current_stop():
    walker = v3._Walker(_runner_cfg(), None, None)
    pos = v3._Position(direction="LONG", entry_index=0, entry=100.02, stop_initial=93.0, stop=101.0,
                       qty=1.0, targets=[114.06, 121.08, 128.1])
    walker.judge_breakeven(pos, close=114.0)
    assert pos.stop_from_next_bar is None and pos.tags == ["BREAKEVEN_STOP_SKIPPED"]


def test_tp1_and_stop_in_the_same_later_bar_stops_the_whole_position(fake_setups):
    c = _long_case([(100.5, 101, 100, 100.5), (100.5, 115, 92, 100)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg())["trades"][0]
    assert [leg["reason"] for leg in t["legs"]] == ["SL"]
    assert t["legs"][0]["qty"] == pytest.approx(t["qty"])
    assert TAG_SAME_BAR_SL in t["tags"] and "TP1_PARTIAL" not in t["tags"]


def test_max_hold_exit_after_breakeven(fake_setups):
    after = [(100.5, 115, 100, 114.5)] + [(114.5, 115, 113.5, 114)] * 3
    c = _long_case(after)
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg(max_hold_bars=4))["trades"][0]
    assert "BREAKEVEN_STOP_SET" in t["tags"] and TAG_MAX_HOLD in t["tags"]
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "MAX_HOLD"]
    assert t["legs"][1]["raw_exit"] == pytest.approx(114.0)
    assert t["exit_index"] == 6 + 4


# ---------------------------------------------------------------- runner target


def test_select_runner_target_branches():
    cfg = _runner_cfg()  # TP2 default, 1.0R beyond TP1
    assert v3.select_runner_target("LONG", 100, 90, [120, 130, 140], cfg) == (130, "TP2")
    assert v3.select_runner_target("LONG", 100, 90, [120, 125, 140], cfg) == (140, "TP3")
    assert v3.select_runner_target("LONG", 100, 90, [120, 125, 128], cfg) == (None, None)
    assert v3.select_runner_target("SHORT", 100, 110, [80, 70, 60], cfg) == (70, "TP2")
    # Slots out of distance order: TPn is the n-th nearest.
    assert v3.select_runner_target("LONG", 100, 90, [140, 120, 130], cfg) == (130, "TP2")


def test_runner_uses_tp2_when_it_is_far_enough(fake_setups):
    c = _long_case([(100.5, 115, 100, 114.5), (114.5, 122, 114, 121)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg())["trades"][0]
    assert t["runner_target_slot"] == "TP2" and "RUNNER_TARGET_TP2" in t["tags"]
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "TP2"]
    assert t["legs"][1]["raw_exit"] == pytest.approx(100 * (1 + S) + 3 * (100 * (1 + S) - 93))


def test_runner_falls_back_to_tp3(fake_setups):
    # 2R/3R/4R ladder: TP2 is 1R beyond TP1, TP3 2R. With 1.5R required, TP3 is used.
    c = _long_case([(100.5, 115, 100, 114.5), (114.5, 122, 114, 121), (121, 129, 120, 128)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg(min_runner_distance_r=1.5))["trades"][0]
    assert t["runner_target_slot"] == "TP3"
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "TP3"]
    assert t["exit_index"] == 9


def test_no_runner_target_exits_at_max_hold(fake_setups):
    c = _long_case([(100.5, 115, 100, 114.5)] + [(114.5, 130, 114, 120)] * 3)
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    t = _run(c, _runner_cfg(min_runner_distance_r=5.0, max_hold_bars=4))["trades"][0]
    assert t["runner_target_slot"] is None and "NO_RUNNER_TARGET" in t["tags"]
    assert [leg["reason"] for leg in t["legs"]] == ["TP1", "MAX_HOLD"]
    assert t["legs"][1]["raw_exit"] == pytest.approx(120.0)


# ---------------------------------------------------------------- BOS re-arm


def test_deep_pullback_without_a_new_opposing_swing_stays_suppressed(fake_setups):
    c, first, _ = _refire_case(None)
    for j in (9, 10, 11):  # far below the first setup's stop, but no swing low is confirmed
        c[j] = _bar(j, 85, 86, 80, 85)
    fake_setups[5] = _setup("LONG", c[5], atr=5, swings=first)
    fake_setups[12] = _setup("LONG", c[12], atr=5, swings=first)
    res = _run(c, _cfg())
    assert [t["candle1_index"] for t in res["trades"]] == [5]
    assert res["counts"]["refire_suppressed"] == 1


def test_an_opposing_swing_older_than_the_first_setup_does_not_rearm(fake_setups):
    c, first, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5, swings=first)
    fake_setups[12] = _setup("LONG", c[12], atr=5, swings=first + [_swing("LOW", 4, 95.0)])
    assert [t["candle1_index"] for t in _run(c, _cfg())["trades"]] == [5]


def test_a_new_level_of_the_same_side_is_a_new_setup(fake_setups):
    c, first, _ = _refire_case(None)
    fake_setups[5] = _setup("LONG", c[5], atr=5, swings=first)
    fake_setups[12] = _setup("LONG", c[12], atr=5, swings=first + [_swing("HIGH", 7, 100.6)])
    assert [t["candle1_index"] for t in _run(c, _cfg())["trades"]] == [5, 12]


def test_short_rearm_mirrors_long(fake_setups):
    c, _, _ = _refire_case(None)
    m = _mirror(c)
    first = [_swing("LOW", 2, 101.0), _swing("HIGH", 3, 104.0)]
    fake_setups[5] = _setup("SHORT", m[5], atr=5, swings=first)
    fake_setups[12] = _setup("SHORT", m[12], atr=5, swings=first)
    res = _run(m, _cfg(book_side="SHORT"))
    assert [t["candle1_index"] for t in res["trades"]] == [5]
    assert res["counts"]["refire_suppressed"] == 1
    # A swing HIGH after the first setup is the opposing swing for a SHORT: re-armed.
    fake_setups[12] = _setup("SHORT", m[12], atr=5, swings=first + [_swing("HIGH", 9, 105.5)])
    assert [t["candle1_index"] for t in _run(m, _cfg(book_side="SHORT"))["trades"]] == [5, 12]
    # A swing LOW after it is the same side, and below 101 it is a new level, not a re-arm.
    fake_setups[12] = _setup("SHORT", m[12], atr=5, swings=first + [_swing("LOW", 9, 100.5)])
    assert [t["candle1_index"] for t in _run(m, _cfg(book_side="SHORT"))["trades"]] == [5, 12]


def test_once_per_level_on_the_real_detector_suppresses_some_setups():
    candles = _synthetic()
    base = dict(book_side="BOTH", apply_exchange_filters=False, risk_tolerance_pct=None)
    once = run_candle12_v3("SYN", "1h", candles, config=Candle12V3Config(**base), index_start=300)
    every = run_candle12_v3(
        "SYN", "1h", candles, config=Candle12V3Config(bos_refire_policy="every_close", **base), index_start=300
    )
    assert once["counts"]["refire_suppressed"] > 0
    assert once["counts"]["setups_detected"] == every["counts"]["setups_detected"]
    assert all(t["broken_swing_index"] is not None for t in once["trades"])


# ---------------------------------------------------------------- funding label


def test_funding_partial_history_is_labelled(fake_setups):
    c = _long_case([(100.5, 100.8, 100.2, 100.5)] * 20 + [(100.5, 100.6, 92, 93)])
    fake_setups[5] = _setup("LONG", c[5], atr=5)
    rows = [{"time": T0 + timedelta(hours=8), "rate": 0.0001, "mark_price": 100.0}]
    res = _run(c, _cfg(funding_enabled=True), funding=rows)
    assert res["trades"][0]["funding_coverage"] == "PARTIAL"
    assert res["funding"]["label"] == "PARTIAL_HISTORY_FUNDING_SENSITIVITY"
    assert res["funding"]["first_settlement"] == rows[0]["time"].isoformat()
    assert _run(c, _cfg())["funding"]["label"] == "FUNDING_DISABLED"
