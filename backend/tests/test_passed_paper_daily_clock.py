"""The passed paper watcher reads the BTC daily trend with the backtest's clock.

The trend for a 1h bar is ``RegimeClock.at`` that bar's close, from one daily
read per tick. A failed read leaves the bar for the next tick; a real NEUTRAL
consumes it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.services.passed_paper_watcher as watcher_mod
from app.research.passed_regime_backtest import RegimeClock
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import DailySnapshot, PassedPaperWatcher

# These tests observe the passed watcher's gates through an open, so they run
# with the regime book open. Production keeps it closed.
pytestmark = pytest.mark.usefixtures("passed_book_open")

DAY0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
LEG = 5
# Higher highs and higher lows, then a leg down to a lower low.
POINTS = [100, 120, 110, 130, 120, 140, 130, 150, 140, 160, 150, 125, 135, 115, 128]
SHORT_PLAN = {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": 100.0, "stop_price": 102.0, "tp1": 96.0}


def _daily_bar(i: int, close: float) -> dict[str, Any]:
    return {
        "time": DAY0 + timedelta(days=i),
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1.0,
    }


def _daily(points: list[float] = POINTS) -> list[dict[str, Any]]:
    closes: list[float] = [float(points[0])]
    for a, b in zip(points, points[1:]):
        closes.extend(a + (b - a) * step / LEG for step in range(1, LEG + 1))
    return [_daily_bar(i, c) for i, c in enumerate(closes)]


def _flat_daily(n: int) -> list[dict[str, Any]]:
    return [_daily_bar(i, 100.0) for i in range(n)]


def _h1(tip: datetime, n: int = 250) -> list[dict[str, Any]]:
    return [
        {"time": tip - timedelta(hours=n - 1 - i), "open": 1, "high": 2, "low": 0.5, "close": 1}
        for i in range(n)
    ]


def _h4(tip: datetime, n: int = 80) -> list[dict[str, Any]]:
    """``n`` contiguous 4h bars, the last one fully closed when ``tip`` closes."""
    decision = tip + timedelta(hours=1)
    last = decision.replace(hour=decision.hour - decision.hour % 4) - timedelta(hours=4)
    return [
        {"time": last - timedelta(hours=4 * (n - 1 - i)), "open": 1, "high": 2, "low": 0.5, "close": 1}
        for i in range(n)
    ]


def _store(monkeypatch, daily: list[dict[str, Any]] | Exception) -> dict[str, int]:
    """Serve BTC daily bars (or raise) from the store. Counts daily reads."""
    from app.services.ohlcv_store import ohlcv_store

    reads = {"n": 0}

    def _get(symbol, timeframe, include_open=False):
        assert (symbol, timeframe, include_open) == ("BTCUSDT", "1d", False)
        reads["n"] += 1
        if isinstance(daily, Exception):
            raise daily
        return list(daily)

    monkeypatch.setattr(ohlcv_store, "get_candles_for_engine", _get)
    return reads


@pytest.fixture
def rig(monkeypatch):
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    evaluated: list[tuple[str, str]] = []

    def _fake_eval(**kwargs):
        evaluated.append((kwargs["symbol"], kwargs["combination"].combination_id))
        return dict(SHORT_PLAN)

    monkeypatch.setattr(watcher_mod, "evaluate_combination_at_bar", _fake_eval)

    def run(symbol: str, tip: datetime, **kwargs) -> list[Any]:
        candles = _h1(tip)
        # The bar after the tip opens at the plan entry (the causal fill bar).
        kwargs.setdefault(
            "next_bar", {"time": tip + timedelta(hours=1), "open": SHORT_PLAN["entry_price"]}
        )
        return watcher.on_closed_1h(
            symbol,
            candles_1h=candles,
            candles_4h=_h4(tip),
            candles_15m=candles,
            force=kwargs.pop("force", True),
            **kwargs,
        )

    return eng, watcher, evaluated, run


def _bar_key(symbol: str, tip: datetime) -> str:
    return f"{symbol}|{tip.isoformat()}"


def _flip_day(daily: list[dict[str, Any]]) -> tuple[datetime, int]:
    """Open time of the first daily bar whose close changes the trend, and its count."""
    clock = RegimeClock(daily, PassedPaperWatcher().signal_config)
    for count in range(41, len(daily) + 1):
        if clock.by_count(count)["trend"] != clock.by_count(count - 1)["trend"]:
            return daily[count - 1]["time"], count
    raise AssertionError("no trend flip in the daily series")


# --- 1. a thrown daily read retries the bar ------------------------------------


def test_thrown_daily_read_leaves_the_bar_for_the_next_call(rig, monkeypatch):
    eng, watcher, evaluated, run = rig
    # Daily bars closed by this bar's close read BEARISH.
    tip = DAY0 + timedelta(days=66, hours=9)
    _store(monkeypatch, RuntimeError("store down"))
    # force=False so the processed-bar guard is live; skip first-sight seeding.
    watcher._seeded.add("BTCUSDT")
    assert run("BTCUSDT", tip, force=False) == []
    assert watcher.last_skip == "BTCUSDT:daily_read_failed"
    assert _bar_key("BTCUSDT", tip) not in watcher._processed
    assert watcher._inflight == set()
    assert evaluated == []
    status = watcher.regime_status()
    assert status["daily_read_ok"] is False
    assert status["daily_read_error"] == "daily_read_failed"
    assert status["regime_bar"] == tip.isoformat()

    # The store heals: the same bar is evaluated on the next call.
    _store(monkeypatch, _daily())
    monkeypatch.setattr(
        "app.services.v1_paper_watcher.tip_open_lag_seconds", lambda *_a, **_kw: 0.0
    )
    opened = run("BTCUSDT", tip, force=False)
    assert watcher.last_evaluated_bar["trend"] == "BEARISH"
    assert len(evaluated) == 7
    assert [p.side for p in opened] == ["SHORT"]
    assert _bar_key("BTCUSDT", tip) in watcher._processed
    assert watcher.regime_status()["daily_read_ok"] is True


def test_a_failed_snapshot_passed_in_is_retried_too(rig):
    _eng, watcher, evaluated, run = rig
    tip = DAY0 + timedelta(days=60, hours=9)
    failed = DailySnapshot(None, error="daily_read_failed")
    assert run("ETHUSDT", tip, daily=failed) == []
    assert watcher.last_skip == "ETHUSDT:daily_read_failed"
    assert _bar_key("ETHUSDT", tip) not in watcher._processed
    assert evaluated == []


def test_no_daily_bar_is_a_failed_read(rig, monkeypatch):
    _eng, watcher, evaluated, run = rig
    tip = DAY0 + timedelta(days=60, hours=9)
    _store(monkeypatch, [])
    assert run("BTCUSDT", tip) == []
    assert watcher.last_skip == "BTCUSDT:no_daily_bar"
    assert _bar_key("BTCUSDT", tip) not in watcher._processed
    assert evaluated == []
    status = watcher.regime_status()
    assert (status["daily_read_ok"], status["daily_read_error"]) == (False, "no_daily_bar")


def test_daily_bar_missing_from_the_store_is_retried(rig, monkeypatch):
    """At the 23:00 bar the daily bar of that day has closed; without it the read is stale."""
    _eng, watcher, evaluated, run = rig
    daily = _daily()
    flip_open, count = _flip_day(daily)
    tip = flip_open + timedelta(hours=23)
    _store(monkeypatch, daily[: count - 1])
    assert run("BTCUSDT", tip) == []
    assert watcher.last_skip == "BTCUSDT:daily_bar_pending"
    assert _bar_key("BTCUSDT", tip) not in watcher._processed
    assert evaluated == []


# --- 2. a real NEUTRAL consumes the bar ----------------------------------------


def test_real_neutral_with_enough_bars_marks_the_bar_processed(rig, monkeypatch):
    eng, watcher, evaluated, run = rig
    daily = _flat_daily(60)
    tip = DAY0 + timedelta(days=59, hours=23)
    _store(monkeypatch, daily)
    assert run("BTCUSDT", tip) == []
    assert watcher.last_skip == "BTCUSDT:neutral_trend"
    assert _bar_key("BTCUSDT", tip) in watcher._processed
    assert evaluated == []
    assert eng.status()["open_count"] == 0
    status = watcher.regime_status()
    assert status["daily_read_ok"] is True
    assert status["trend"] == "NEUTRAL"
    assert status["daily_bars"] == 60 >= status["min_daily_bars"]
    assert status["short_history"] is False


def test_real_neutral_leaves_an_open_position_alone(rig, monkeypatch):
    eng, watcher, _evaluated, run = rig
    held = eng.open_passed_position(
        symbol="BTCUSDT",
        combination_id="COMBO_01",
        timeframe="1h",
        direction="SHORT",
        eval_result=dict(SHORT_PLAN),
        risk_percent=0.02,
        setup_bar_time_utc="2026-07-01T00:00:00+00:00",
        market_direction="BEARISH",
    )
    before = held.to_dict()
    _store(monkeypatch, _flat_daily(60))
    assert run("BTCUSDT", DAY0 + timedelta(days=59, hours=23)) == []
    assert eng.get_open("BTCUSDT", "PASSED|COMBO_01|SHORT").to_dict() == before


def test_short_history_is_a_successful_neutral_and_consumes_the_bar(rig, monkeypatch):
    _eng, watcher, evaluated, run = rig
    tip = DAY0 + timedelta(days=29, hours=23)
    _store(monkeypatch, _flat_daily(30))
    assert run("BTCUSDT", tip) == []
    assert watcher.last_skip == "BTCUSDT:neutral_trend"
    assert _bar_key("BTCUSDT", tip) in watcher._processed
    assert evaluated == []
    status = watcher.regime_status()
    assert status["daily_read_ok"] is True
    assert status["daily_bars"] == 30
    assert status["short_history"] is True


# --- 3. same moment and same clock as the backtest -----------------------------


def test_flip_day_2300_bar_matches_the_backtest_clock_for_two_symbols(rig, monkeypatch):
    _eng, watcher, _evaluated, run = rig
    daily = _daily()
    flip_open, count = _flip_day(daily)
    reads = _store(monkeypatch, daily)
    snapshot = watcher.read_daily_snapshot()
    assert reads["n"] == 1
    clock = RegimeClock(daily, watcher.signal_config)

    tip = flip_open + timedelta(hours=23)
    expected = clock.at(tip + timedelta(hours=1))
    # It is a flip day: the 23:00 bar sees the flip, the 22:00 bar does not.
    before = clock.at(tip)
    assert expected["trend"] != before["trend"]
    assert expected["as_of_bar"] == flip_open.isoformat()

    seen = []
    for symbol in ("ETHUSDT", "SOLUSDT"):
        run(symbol, tip, daily=snapshot)
        bar = watcher.last_evaluated_bar
        assert bar["symbol"] == symbol
        seen.append((bar["trend"], bar["as_of_daily_bar"]))
    assert seen == [(expected["trend"], expected["as_of_bar"])] * 2
    # One read for the tick; the symbols did not read the store again.
    assert reads["n"] == 1

    # The 22:00 bar on the same snapshot uses the day before.
    run("ADAUSDT", tip - timedelta(hours=1), daily=snapshot)
    assert (watcher.last_evaluated_bar["trend"], watcher.last_evaluated_bar["as_of_daily_bar"]) == (
        before["trend"],
        before["as_of_bar"],
    )


def test_later_daily_bars_in_the_snapshot_do_not_leak_into_an_earlier_bar(rig, monkeypatch):
    _eng, watcher, _evaluated, run = rig
    daily = _daily()
    flip_open, _count = _flip_day(daily)
    _store(monkeypatch, daily)
    snapshot = watcher.read_daily_snapshot()
    tip = flip_open + timedelta(hours=22)
    run("ETHUSDT", tip, daily=snapshot)
    clock = RegimeClock(daily, watcher.signal_config)
    assert watcher.last_evaluated_bar["as_of_daily_bar"] == clock.at(tip + timedelta(hours=1))["as_of_bar"]
    assert watcher.last_evaluated_bar["as_of_daily_bar"] == (flip_open - timedelta(days=1)).isoformat()
