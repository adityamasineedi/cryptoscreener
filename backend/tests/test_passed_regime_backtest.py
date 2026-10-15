from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research import passed_regime_backtest as book
from app.research import passed_regime_router as router
from app.research.passed_cap_book import LARGE, MID, PASSED_SIDES, PRINCIPAL_USD
from app.signals.config import SignalConfig

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
DAILY_START = START - timedelta(days=60)
LARGE_SYMBOL = LARGE[1]
MID_SYMBOL = MID[0]


def _hourly(n: int, start: datetime = START) -> list[dict]:
    return [
        {"time": start + timedelta(hours=i), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


def _daily(n: int, start: datetime = DAILY_START) -> list[dict]:
    return [
        {"time": start + timedelta(days=i), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


def _plan_trend(monkeypatch: pytest.MonkeyPatch, plan) -> None:
    """Daily trend chosen by the date of the last daily bar passed in."""

    def fake(candles, signal_config):
        last = candles[-1]["time"] if candles else None
        return {
            "trend": plan(last.date()) if last is not None else "NEUTRAL",
            "as_of_bar": last.isoformat() if last is not None else "",
            "bars": len(candles),
        }

    monkeypatch.setattr(book, "daily_trend", fake)


def _sig(bar: int, exit_bar: int | None, *, entry=100.0, stop=102.0, tp1=96.0, exit_price=None, outcome="SL", net=None):
    return {
        "bar": bar,
        "exit": exit_bar,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "exit_price": stop if exit_price is None else exit_price,
        "outcome": outcome,
        "net": net,
    }


class FakeBacktest:
    """Stands in for run_combination_backtest.

    Like the real walk it ignores a side's signals while that side's own trade
    is open, and reports a trade that has not exited before ``index_end`` as
    OPEN.
    """

    def __init__(self, plan: dict[tuple[str, str, str], list[dict]]) -> None:
        self.plan = plan
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        symbol = kwargs["symbol"]
        candles = kwargs["candles"]
        start = kwargs["index_start"] or 0
        end = kwargs["index_end"] if kwargs["index_end"] is not None else len(candles)
        trades = []
        free_from = start
        for raw in self.plan.get((symbol, kwargs["combination"], kwargs["direction_filter"]), []):
            i = raw["bar"]
            if i < free_from or i >= end:
                continue
            closed = raw["exit"] is not None and raw["exit"] < end
            trade = {
                "symbol": symbol,
                "timeframe": kwargs["timeframe"],
                "combination_id": kwargs["combination"],
                "entry_index": i,
                "signal_time": candles[i]["time"].isoformat(),
                "direction": kwargs["direction_filter"],
                "entry_price": raw["entry"],
                "stop_price": raw["stop"],
                "tp1": raw["tp1"],
                "tp2": None,
                "tp3": None,
                "rr": 2.0,
                "exit_index": raw["exit"] if closed else None,
                "exit_time": candles[raw["exit"]]["time"].isoformat() if closed else None,
                "exit_price": raw["exit_price"] if closed else None,
                "outcome": raw["outcome"] if closed else "OPEN",
                "r_multiple": -1.0 if closed else None,
                "condition_snapshot": {"entry_type": "MARKET", "bulky": list(range(5))},
                "_net": raw["net"],
            }
            trades.append(trade)
            free_from = (raw["exit"] + 1) if closed else end
        return {"status": "OK", "trades": trades}

    def sides(self) -> set[tuple[str, str]]:
        return {(c["combination"], c["direction_filter"]) for c in self.calls}


def _fake_postprocess(seen: list[dict] | None = None):
    def run(data: dict) -> dict:
        if seen is not None:
            seen.append(data)
        trade = dict(data["backtest_out"]["trades"][0])
        net = float(trade["_net"] or 0.0)
        return {
            "trades": [
                {
                    **trade,
                    "risk_usd": data["risk_usd"],
                    "requested_risk_usd": data["risk_usd"],
                    "account_equity": data["principal"],
                    "gross_pnl_usd": net + 1.0,
                    "fee_total_usd": -1.0,
                    "net_pnl_usd": net,
                }
            ]
        }

    return run


def _run(plan, n_bars: int = 96, symbols=(LARGE_SYMBOL,), **kwargs):
    fake = kwargs.pop("backtest_fn", None) or FakeBacktest(plan)
    kwargs.setdefault("postprocess_fn", _fake_postprocess())
    result = book.run_regime_book(
        {symbol: {"candles": _hourly(n_bars)} for symbol in symbols},
        _daily(70),
        backtest_fn=fake,
        **kwargs,
    )
    return result, fake


def _one_signal_per_side(symbol: str) -> dict:
    return {
        (symbol, cid, direction): [_sig(60 + 2 * n, 60 + 2 * n + 1)]
        for n, (cid, _, direction) in enumerate(PASSED_SIDES)
    }


def test_bullish_takes_only_long_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BULLISH")
    result, fake = _run(_one_signal_per_side(LARGE_SYMBOL))
    allowed = router.allowed_passed_sides("BULLISH")
    assert fake.sides() == {(cid, direction) for cid, _, direction in allowed}
    assert [(t["strategy_id"], t["direction"]) for t in result["trades"]] == [
        (cid, direction) for cid, _, direction in allowed
    ]
    assert {t["direction"] for t in result["trades"]} == {"LONG"}
    assert {t["trend_at_entry"] for t in result["trades"]} == {"BULLISH"}


def test_bearish_takes_only_short_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    result, fake = _run(_one_signal_per_side(LARGE_SYMBOL))
    allowed = router.allowed_passed_sides("BEARISH")
    assert fake.sides() == {(cid, direction) for cid, _, direction in allowed}
    assert [t["strategy_id"] for t in result["trades"]] == [cid for cid, _, _ in allowed]
    assert {t["direction"] for t in result["trades"]} == {"SHORT"}
    assert result["summary"]["by_trend"]["BEARISH"]["trades"] == len(allowed)
    assert result["summary"]["by_trend"]["BULLISH"]["trades"] == 0


def test_neutral_takes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "NEUTRAL")
    result, fake = _run(_one_signal_per_side(LARGE_SYMBOL))
    assert fake.calls == []
    assert result["trades"] == [] and result["signals"] == []
    assert result["summary"]["by_trend"]["NEUTRAL"]["symbol_bars"] == 96
    assert result["summary"]["by_trend"]["NEUTRAL"]["short_history_symbol_bars"] == 0
    assert result["equity_curve"] == [
        {
            "time": START.isoformat(),
            "equity_usd": PRINCIPAL_USD,
            "realized_pnl_usd_net": 0.0,
            "drawdown_usd": 0.0,
            "drawdown_pct": 0.0,
            "open_positions": 0,
        }
    ]


def test_router_decides_the_book(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    asked: list[object] = []

    def nothing(trend: object) -> tuple:
        asked.append(trend)
        return ()

    monkeypatch.setattr(router, "allowed_passed_sides", nothing)
    result, fake = _run(_one_signal_per_side(LARGE_SYMBOL))
    assert "BEARISH" in asked
    assert fake.calls == []
    assert result["trades"] == []


def test_open_position_blocks_same_symbol_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    first, second = router.allowed_passed_sides("BEARISH")[:2]
    plan = {
        (LARGE_SYMBOL, first[0], "SHORT"): [_sig(60, 70)],
        (LARGE_SYMBOL, second[0], "SHORT"): [_sig(65, 66)],
        (MID_SYMBOL, second[0], "SHORT"): [_sig(65, 66)],
    }
    result, _ = _run(plan, symbols=(LARGE_SYMBOL, MID_SYMBOL))
    assert [(t["symbol"], t["strategy_id"], t["entry_index"]) for t in result["trades"]] == [
        (LARGE_SYMBOL, first[0], 60),
        (MID_SYMBOL, second[0], 65),
    ]
    refused = [s for s in result["signals"] if not s["taken"]]
    assert len(refused) == 1
    assert refused[0]["symbol"] == LARGE_SYMBOL
    assert refused[0]["strategy_id"] == second[0]
    assert refused[0]["skip_reason"] == book.SKIP_POSITION_OPEN
    assert refused[0]["blocked_by"]["strategy_id"] == first[0]
    assert result["summary"]["skipped_signals"] == {book.SKIP_POSITION_OPEN: 1}
    assert result["summary"]["signals"] == 3 and result["summary"]["signals_taken"] == 2


def test_bar_after_exit_is_free_and_exit_bar_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    first, second, third = router.allowed_passed_sides("BEARISH")[:3]
    plan = {
        (LARGE_SYMBOL, first[0], "SHORT"): [_sig(60, 70)],
        (LARGE_SYMBOL, second[0], "SHORT"): [_sig(70, 71)],
        (LARGE_SYMBOL, third[0], "SHORT"): [_sig(71, 72)],
    }
    result, _ = _run(plan)
    assert [(t["strategy_id"], t["entry_index"]) for t in result["trades"]] == [
        (first[0], 60),
        (third[0], 71),
    ]


def test_same_bar_goes_to_passed_sides_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    allowed = router.allowed_passed_sides("BEARISH")
    early, late = allowed[1], allowed[4]
    assert PASSED_SIDES.index(early) < PASSED_SIDES.index(late)
    # The later row is planned first so plan order cannot decide the tie.
    plan = {
        (LARGE_SYMBOL, late[0], "SHORT"): [_sig(60, 62)],
        (LARGE_SYMBOL, early[0], "SHORT"): [_sig(60, 63)],
    }
    result, _ = _run(plan)
    assert [t["strategy_id"] for t in result["trades"]] == [early[0]]
    refused = [s for s in result["signals"] if not s["taken"]]
    assert [s["strategy_id"] for s in refused] == [late[0]]
    assert refused[0]["skip_reason"] == book.SKIP_SAME_BAR


def test_position_survives_trend_flip_and_exits_at_own_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    flip = (START + timedelta(days=1)).date()
    # Daily bar of 1 Jan (BEARISH) is in force for the bars of 2 Jan; the
    # daily bar of 2 Jan (BULLISH) from the bar that closes at 3 Jan 00:00.
    _plan_trend(monkeypatch, lambda day: "BULLISH" if day >= flip else "BEARISH")
    short = router.allowed_passed_sides("BEARISH")[0]
    long = router.allowed_passed_sides("BULLISH")[0]
    plan = {
        (LARGE_SYMBOL, short[0], "SHORT"): [_sig(40, 60, stop=103.5, exit_price=103.5)],
        (LARGE_SYMBOL, long[0], "LONG"): [_sig(50, 52), _sig(61, 63, stop=98.0, tp1=104.0)],
    }
    result, fake = _run(plan)
    trend_by_bar = {s["bar_index"]: s["trend"] for s in result["signals"]}
    assert trend_by_bar == {40: "BEARISH", 50: "BULLISH", 61: "BULLISH"}
    first, second = result["trades"]
    assert (first["strategy_id"], first["direction"], first["trend_at_entry"]) == (short[0], "SHORT", "BEARISH")
    assert first["outcome"] == "SL"
    assert first["exit_index"] == 60
    assert first["exit_price"] == 103.5 == first["stop_price"]
    assert first["exit_time"] == (START + timedelta(hours=60)).isoformat()
    assert first["exit_resolution"] == "IN_WALK"
    assert (second["direction"], second["entry_index"], second["trend_at_entry"]) == ("LONG", 61, "BULLISH")
    blocked = [s for s in result["signals"] if not s["taken"]]
    assert [(s["bar_index"], s["skip_reason"]) for s in blocked] == [(50, book.SKIP_POSITION_OPEN)]


def test_signal_hidden_by_a_refused_trade_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    first, second = router.allowed_passed_sides("BEARISH")[:2]
    plan = {
        (LARGE_SYMBOL, first[0], "SHORT"): [_sig(60, 70)],
        # In one single-strategy walk the trade at 62 runs to 80 and hides 75.
        (LARGE_SYMBOL, second[0], "SHORT"): [_sig(62, 80), _sig(66, 67), _sig(75, 78)],
    }
    result, fake = _run(plan)
    assert [(t["strategy_id"], t["entry_index"], t["exit_index"]) for t in result["trades"]] == [
        (first[0], 60, 70),
        (second[0], 75, 78),
    ]
    assert [s["bar_index"] for s in result["signals"] if not s["taken"]] == [62, 66]
    # Hidden bars are walked from the bar after the refused signal.
    starts = [c["index_start"] for c in fake.calls if c["combination"] == second[0]]
    assert starts == [None, 63, 67]
    assert result["summary"]["signals_on_rewalk_first_bar"] == 0


def test_signal_on_first_bar_of_a_rewalk_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    first, second = router.allowed_passed_sides("BEARISH")[:2]
    plan = {
        (LARGE_SYMBOL, first[0], "SHORT"): [_sig(60, 70)],
        (LARGE_SYMBOL, second[0], "SHORT"): [_sig(62, 80), _sig(63, 64), _sig(71, 73)],
    }
    result, _ = _run(plan)
    marks = {s["bar_index"]: s["rewalk_first_bar"] for s in result["signals"] if s["strategy_id"] == second[0]}
    assert marks == {62: False, 63: True, 71: False}
    assert result["summary"]["signals_on_rewalk_first_bar"] == 1
    assert result["summary"]["trades_on_rewalk_first_bar"] == 0
    assert [t["entry_index"] for t in result["trades"]] == [60, 71]


def test_trade_cut_off_by_a_rewalk_is_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    first, second = router.allowed_passed_sides("BEARISH")[:2]
    plan = {
        (LARGE_SYMBOL, first[0], "SHORT"): [_sig(60, 63)],
        (LARGE_SYMBOL, second[0], "SHORT"): [_sig(62, 66), _sig(65, 300, stop=107.0, exit_price=107.0)],
    }
    result, fake = _run(plan, n_bars=320)
    last = result["trades"][-1]
    assert (last["strategy_id"], last["entry_index"], last["exit_index"]) == (second[0], 65, 300)
    assert last["exit_price"] == 107.0 and last["outcome"] == "SL"
    assert last["exit_resolution"] == "REWALK"
    # Same start bar every time, only the end moves.
    rewalks = [(c["index_start"], c["index_end"]) for c in fake.calls if c["index_start"] == 63]
    assert rewalks == [(63, 67), (63, 115), (63, 211), (63, None)]


def test_closed_side_signal_opens_nothing_and_its_hidden_bars_are_walked(monkeypatch: pytest.MonkeyPatch) -> None:
    turn = (START + timedelta(days=1)).date()
    _plan_trend(monkeypatch, lambda day: "BEARISH" if day >= turn else "BULLISH")
    side = router.allowed_passed_sides("BEARISH")[0]
    # SHORT is allowed from bar 47. The signal at 45 would run to 55.
    plan = {(LARGE_SYMBOL, side[0], "SHORT"): [_sig(45, 55), _sig(50, 52)]}
    result, _ = _run(plan)
    assert [(s["bar_index"], s["trend"], s["taken"]) for s in result["signals"]] == [(50, "BEARISH", True)]
    assert [t["entry_index"] for t in result["trades"]] == [50]
    assert result["summary"]["signals_outside_trend"] == 1


def test_daily_bar_is_used_only_once_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "NEUTRAL")
    clock = book.RegimeClock(_daily(70), SignalConfig())
    day = START + timedelta(days=3)
    prior = (day - timedelta(days=1)).isoformat()
    # 22:00 bar of day D closes at 23:00: day D is still open.
    assert clock.at(day + timedelta(hours=22) + book.BAR)["as_of_bar"] == prior
    # 23:00 bar closes at 00:00 of D+1, the moment day D closes.
    assert clock.at(day + timedelta(hours=23) + book.BAR)["as_of_bar"] == day.isoformat()
    assert clock.at(day + book.BAR)["as_of_bar"] == prior


def test_trend_at_bar_does_not_see_the_open_day(monkeypatch: pytest.MonkeyPatch) -> None:
    turn = (START + timedelta(days=1)).date()
    _plan_trend(monkeypatch, lambda day: "BEARISH" if day >= turn else "NEUTRAL")
    side = router.allowed_passed_sides("BEARISH")[0]
    # Bars 24..46 are inside 2 Jan while its daily bar is still open.
    plan = {(LARGE_SYMBOL, side[0], "SHORT"): [_sig(30, 31), _sig(46, 46), _sig(47, 48)]}
    result, _ = _run(plan)
    assert [s["bar_index"] for s in result["signals"]] == [47]
    assert [t["entry_index"] for t in result["trades"]] == [47]
    assert result["summary"]["signals_outside_trend"] == 2
    assert result["trades"][0]["as_of_daily_bar"] == (START + timedelta(days=1)).isoformat()
    assert result["trades"][0]["signal_time"] == (START + timedelta(hours=47)).isoformat()


def test_unclosed_last_daily_bar_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def fake(candles, signal_config):
        seen.append(len(candles))
        return {"trend": "NEUTRAL", "as_of_bar": "", "bars": len(candles)}

    monkeypatch.setattr(book, "daily_trend", fake)
    daily = _daily(61)  # last bar opens 1 Jan 00:00
    clock = book.RegimeClock(daily, SignalConfig())
    clock.at(START + timedelta(hours=5))
    assert seen == [60]
    clock.at(START + timedelta(hours=6))
    assert seen == [60]


def test_short_daily_history_is_neutral() -> None:
    # Real daily_trend: 39 closed daily bars at the first 1h bar, 40 a day later.
    fake = FakeBacktest(_one_signal_per_side(LARGE_SYMBOL))
    result = book.run_regime_book(
        {LARGE_SYMBOL: {"candles": _hourly(23)}},
        _daily(39, START - timedelta(days=39)),
        backtest_fn=fake,
        postprocess_fn=_fake_postprocess(),
    )
    assert fake.calls == []
    assert result["trades"] == []
    neutral = result["summary"]["by_trend"]["NEUTRAL"]
    assert neutral == {"symbol_bars": 23, "short_history_symbol_bars": 23}
    assert result["symbols"][LARGE_SYMBOL]["short_history_until"] == (START + timedelta(hours=22)).isoformat()
    assert result["summary"]["regime_timeline"] == [
        {
            "trend": "NEUTRAL",
            "start_daily_bar": (START - timedelta(days=1)).isoformat(),
            "end_daily_bar": (START - timedelta(days=1)).isoformat(),
            "daily_bars": 1,
            "short_history": True,
        }
    ]
    clock = book.RegimeClock(_daily(40, START - timedelta(days=39)), SignalConfig())
    assert clock.at(START + timedelta(hours=23))["short_history"] is True
    assert clock.at(START + timedelta(hours=24))["short_history"] is False
    assert clock.at(START + timedelta(hours=24))["bars"] == 40


def test_regime_timeline_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    flip = (START + timedelta(days=1)).date()
    _plan_trend(monkeypatch, lambda day: "BULLISH" if day >= flip else "BEARISH")
    result, _ = _run({}, n_bars=96)
    assert result["summary"]["regime_timeline"] == [
        {
            "trend": "BEARISH",
            "start_daily_bar": (START - timedelta(days=1)).isoformat(),
            "end_daily_bar": START.isoformat(),
            "daily_bars": 2,
            "short_history": False,
        },
        {
            "trend": "BULLISH",
            "start_daily_bar": (START + timedelta(days=1)).isoformat(),
            "end_daily_bar": (START + timedelta(days=3)).isoformat(),
            "daily_bars": 3,
            "short_history": False,
        },
    ]
    assert result["summary"]["by_trend"]["BEARISH"]["symbol_bars"] == 47
    assert result["summary"]["by_trend"]["BULLISH"]["symbol_bars"] == 49


def test_equity_curve_arithmetic(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    side = router.allowed_passed_sides("BEARISH")[0]
    plan = {
        (LARGE_SYMBOL, side[0], "SHORT"): [
            _sig(50, 52, net=30.0),
            _sig(53, 60, net=-50.0),
            _sig(70, 72, net=10.0),
        ],
        (MID_SYMBOL, side[0], "SHORT"): [_sig(51, 56, net=-20.0), _sig(80, None)],
    }
    seen: list[dict] = []
    result, _ = _run(plan, symbols=(LARGE_SYMBOL, MID_SYMBOL), postprocess_fn=_fake_postprocess(seen))
    curve = result["equity_curve"]
    stamp = lambda bar: (START + timedelta(hours=bar)).isoformat()  # noqa: E731
    assert [p["time"] for p in curve] == [stamp(0), stamp(52), stamp(56), stamp(60), stamp(72)]
    assert [p["equity_usd"] for p in curve] == [1000.0, 1030.0, 1010.0, 960.0, 970.0]
    assert [p["realized_pnl_usd_net"] for p in curve] == [0.0, 30.0, 10.0, -40.0, -30.0]
    assert [p["drawdown_usd"] for p in curve] == [0.0, 0.0, 20.0, 70.0, 60.0]
    assert curve[3]["drawdown_pct"] == pytest.approx(70.0 / 1030.0 * 100.0)
    assert [p["open_positions"] for p in curve] == [0, 1, 1, 0, 0]
    summary = result["summary"]
    assert [t["trade_no"] for t in result["trades"]] == [1, 2, 3, 4, 5]
    assert summary["trades"] == 4 and summary["open_trades"] == 1
    assert summary["wins"] == 2 and summary["losses"] == 2
    assert summary["pnl_usd_net"] == pytest.approx(-30.0)
    assert summary["pnl_usd_gross"] == pytest.approx(-26.0)
    assert summary["fees_usd"] == pytest.approx(-4.0)
    assert summary["final_equity_usd"] == pytest.approx(970.0)
    assert summary["max_drawdown_usd"] == pytest.approx(70.0)
    assert summary["max_drawdown_pct"] == pytest.approx(70.0 / 1030.0 * 100.0)
    assert summary["by_strategy"][f"{side[0]}|SHORT"]["trades"] == 4
    assert summary["by_strategy"][f"{side[0]}|SHORT"]["taken"] == 5
    assert result["equity_basis"] == "REALIZED_ONLY"
    # Equity handed to the leverage cap: principal plus what had closed by the signal bar.
    by_bar = {(d["load_meta"]["symbol"], d["backtest_out"]["trades"][0]["entry_index"]): d["principal"] for d in seen}
    assert by_bar[(LARGE_SYMBOL, 50)] == 1000.0
    assert by_bar[(MID_SYMBOL, 51)] == 1000.0
    assert by_bar[(LARGE_SYMBOL, 53)] == 1030.0
    assert by_bar[(LARGE_SYMBOL, 70)] == 960.0
    open_trade = [t for t in result["trades"] if t["outcome"] == "OPEN"]
    assert len(open_trade) == 1 and "net_pnl_usd" not in open_trade[0]


def test_stake_follows_passed_risk_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    side = router.allowed_passed_sides("BEARISH")[0]
    plan = {
        (LARGE_SYMBOL, side[0], "SHORT"): [_sig(50, 52)],
        (MID_SYMBOL, side[0], "SHORT"): [_sig(50, 52)],
    }
    seen: list[dict] = []
    _run(plan, symbols=(LARGE_SYMBOL, MID_SYMBOL), postprocess_fn=_fake_postprocess(seen))
    assert {d["load_meta"]["symbol"]: d["risk_usd"] for d in seen} == {LARGE_SYMBOL: 20.0, MID_SYMBOL: 10.0}
    assert {(d["taker_fee"], d["maker_fee"], d["leverage"]) for d in seen} == {(0.0004, 0.0002, 2.0)}


def test_prices_pass_through_the_real_fee_step(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    side = router.allowed_passed_sides("BEARISH")[0]
    raw = _sig(50, 55, entry=100.0, stop=104.0, tp1=92.0, exit_price=92.0, outcome="TP1")
    result, fake = _run({(LARGE_SYMBOL, side[0], "SHORT"): [raw]}, postprocess_fn=None)
    (trade,) = result["trades"]
    assert trade["entry_price"] == 100.0
    assert trade["stop_price"] == 104.0
    assert trade["tp1"] == trade["target_price"] == 92.0
    assert trade["exit_price"] == 92.0
    assert trade["outcome"] == "TP1"
    assert trade["signal_time"] == trade["entry_time"] == (START + timedelta(hours=50)).isoformat()
    assert trade["exit_time"] == (START + timedelta(hours=55)).isoformat()
    assert trade["combination_id"] == trade["strategy_id"] == side[0]
    assert trade["requested_risk_usd"] == 20.0
    # $20 at a 4 point stop is 5 units; 8 points in favour.
    assert trade["qty"] == pytest.approx(5.0)
    assert trade["gross_pnl_usd"] == pytest.approx(40.0)
    assert trade["fee_total_usd"] == pytest.approx(-(500.0 + 460.0) * 0.0004)
    assert trade["net_pnl_usd"] == pytest.approx(40.0 - 0.384)
    assert trade["leverage"] == 2.0
    assert (trade["trade_no"], trade["trade_id"]) == (1, "t1")
    assert "condition_snapshot" not in trade
    # The walk gets the series and the side untouched.
    call = fake.calls[0]
    assert (call["symbol"], call["timeframe"], call["combination"], call["direction_filter"]) == (
        LARGE_SYMBOL,
        "1h",
        side[0],
        "SHORT",
    )


def test_cancel_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BEARISH")
    result, fake = _run(_one_signal_per_side(LARGE_SYMBOL), should_cancel=lambda: True)
    assert result["status"] == "CANCELLED"
    assert fake.calls == []
    assert result["trades"] == []


def test_failed_walk_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _plan_trend(monkeypatch, lambda day: "BULLISH")

    def failing(**kwargs: Any) -> dict[str, Any]:
        return {"status": "INSUFFICIENT_DATA", "trades": [], "disclaimer": "short"}

    result, _ = _run({}, backtest_fn=failing)
    errors = result["symbols"][LARGE_SYMBOL]["errors"]
    assert [e["status"] for e in errors] == ["INSUFFICIENT_DATA"] * len(router.allowed_passed_sides("BULLISH"))
    assert result["trades"] == []

