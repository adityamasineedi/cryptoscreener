"""Passed cap-book sides open paper trades at the backtest plan."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.passed_cap_book import PASSED_SIDES, is_passed_side, passed_risk_percent
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import PassedPaperWatcher


def _eval(status: str = "SHORT_ENTRY_CANDIDATE") -> dict:
    return {
        "status": status,
        "entry_price": 100.0,
        "stop_price": 102.0,
        "tp1": 96.0,
    }


def test_same_passed_bar_is_refused_on_a_fresh_book():
    """A restarted process must not book a stream that already owns the bar."""
    from app.services.paper_trade import ledger_dedupe_key

    eng = PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    first = eng.open_passed_position(
        symbol="AVAXUSDT",
        combination_id="COMBO_01",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc="2026-10-10T23:00:00Z",
        market_direction="NEUTRAL",
    )
    assert first is not None
    key = ledger_dedupe_key(
        "AVAXUSDT",
        "2026-10-10T23:00:00+00:00",
        "PASSED|COMBO_01|SHORT",
        "1h",
    )
    fresh = PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    fresh.remember_dedupe_keys([key])
    second = fresh.open_passed_position(
        symbol="AVAXUSDT",
        combination_id="COMBO_01",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc="2026-10-10T23:00:00+00:00",
        market_direction="NEUTRAL",
    )
    assert second is None
    other = fresh.open_passed_position(
        symbol="AVAXUSDT",
        combination_id="COMBO_02_LOCAL",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc="2026-10-10T23:00:00+00:00",
        market_direction="NEUTRAL",
    )
    assert other is not None


def test_passed_list_covers_the_profitable_sides():
    assert ("COMBO_02_V2", "1h", "SHORT") in PASSED_SIDES
    assert ("COMBO_03_TRANSITION_B", "1h", "LONG") in PASSED_SIDES
    assert is_passed_side("COMBO_01", "1h", "SHORT")
    assert not is_passed_side("COMBO_SD_F", "1h", "SHORT")


def test_short_against_bullish_btc_uses_half_stake():
    assert passed_risk_percent("BTCUSDT", "SHORT", "BULLISH") == 0.01
    assert passed_risk_percent("BTCUSDT", "SHORT", "BEARISH") == 0.02
    assert passed_risk_percent("NEARUSDT", "LONG", "BULLISH") == 0.01


def test_passed_short_opens_at_plan_and_stops_out():
    eng = PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    pos = eng.open_passed_position(
        symbol="ETHUSDT",
        combination_id="COMBO_01",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc="2026-10-10T12:00:00+00:00",
        market_direction="BEARISH",
    )
    assert pos is not None
    assert pos.side == "SHORT"
    assert pos.entry_price == 100.0
    assert pos.stop_price == 102.0
    assert pos.tp1_price == 96.0
    assert pos.signal_snippet["execution"] == "backtest_plan"
    assert pos.signal_snippet["source"] == "PASSED_CAP_BOOK"
    closed = eng.tick({"ETHUSDT": 102.0})
    assert closed and closed[0].exit_reason == "STOP"


def test_unknown_version_is_not_opened():
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    pos = eng.open_passed_position(
        symbol="ETHUSDT",
        combination_id="COMBO_SD_F",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc="2026-10-10T12:00:00+00:00",
    )
    assert pos is None
    assert eng._last_skip_reason.endswith("not_passed_side")


def test_two_passed_versions_can_both_be_open():
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    bar = "2026-10-10T12:00:00+00:00"
    first = eng.open_passed_position(
        symbol="SOLUSDT",
        combination_id="COMBO_02",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc=bar,
    )
    second = eng.open_passed_position(
        symbol="SOLUSDT",
        combination_id="COMBO_02_V2",
        timeframe="1h",
        direction="SHORT",
        eval_result=_eval(),
        risk_percent=0.02,
        setup_bar_time_utc=bar,
    )
    assert first is not None and second is not None
    assert eng.status()["open_count"] == 2


def _closed_4h(tip_1h: dict, n: int = 80) -> list[dict]:
    """``n`` contiguous 4h bars, the last one fully closed when ``tip_1h`` closes."""
    decision = tip_1h["time"] + timedelta(hours=1)
    last = decision.replace(hour=decision.hour - decision.hour % 4) - timedelta(hours=4)
    return [
        {
            "time": last - timedelta(hours=4 * (n - 1 - i)),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1,
        }
        for i in range(n)
    ]


@pytest.mark.usefixtures("passed_book_open")
def test_watcher_opens_only_the_matching_passed_side(monkeypatch):
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)

    def _fake_eval(**kwargs):
        combo = kwargs["combination"]
        cid = combo.combination_id
        if cid == "COMBO_01":
            return _eval("SHORT_ENTRY_CANDIDATE")
        if cid == "COMBO_03_TRANSITION_B":
            return {
                "status": "LONG_ENTRY_CANDIDATE",
                "entry_price": 50.0,
                "stop_price": 48.0,
                "tp1": 54.0,
            }
        return {"status": "NO_SETUP"}

    monkeypatch.setattr(
        "app.services.passed_paper_watcher.evaluate_combination_at_bar",
        _fake_eval,
    )
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    candles = [
        {
            "time": start + timedelta(hours=hour),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1,
        }
        for hour in range(250)
    ]
    # The trend picks the book: BEARISH the short side, BULLISH the long side.
    opened = watcher.on_closed_1h(
        "BTCUSDT",
        candles_1h=candles,
        candles_4h=_closed_4h(candles[-1]),
        candles_15m=candles,
        market_direction="BEARISH",
        force=True,
    )
    sides = {(p.signal_snippet["strategy_id"], p.side) for p in opened}
    assert sides == {("COMBO_01", "SHORT")}
    opened_long = watcher.on_closed_1h(
        "ETHUSDT",
        candles_1h=candles,
        candles_4h=_closed_4h(candles[-1]),
        candles_15m=candles,
        market_direction="BULLISH",
        force=True,
    )
    sides_long = {(p.signal_snippet["strategy_id"], p.side) for p in opened_long}
    assert sides_long == {("COMBO_03_TRANSITION_B", "LONG")}
    assert all(
        p.signal_snippet["strategy_id"] != "COMBO_02_V2" for p in opened + opened_long
    )


def test_current_closed_bar_is_evaluated_on_first_sight(monkeypatch):
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    calls = {"n": 0}

    def _fake_eval(**kwargs):
        calls["n"] += 1
        return {"status": "NO_SETUP"}

    monkeypatch.setattr(
        "app.services.passed_paper_watcher.evaluate_combination_at_bar",
        _fake_eval,
    )
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    tip = now - timedelta(hours=1)
    candles = [
        {
            "time": tip - timedelta(hours=249 - hour),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1,
        }
        for hour in range(250)
    ]
    opened = watcher.on_closed_1h(
        "BTCUSDT",
        candles_1h=candles,
        candles_4h=_closed_4h(candles[-1]),
        candles_15m=candles,
        market_direction="BEARISH",
    )
    assert opened == []
    assert calls["n"] > 0
    assert watcher.last_skip == "BTCUSDT:no_passed_entry"


def test_short_15m_does_not_burn_the_passed_bar():
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    candles = [
        {
            "time": start + timedelta(hours=hour),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1,
        }
        for hour in range(250)
    ]
    opened = watcher.on_closed_1h(
        "BTCUSDT",
        candles_1h=candles,
        candles_4h=candles,
        candles_15m=candles[:99],
        market_direction="NEUTRAL",
    )
    assert opened == []
    assert watcher.last_skip == "BTCUSDT:ohlcv_catchup"
    assert "BTCUSDT" not in watcher._seeded


def test_passed_watcher_is_on_by_default():
    from app.config import Settings

    assert Settings.model_fields["paper_passed_watcher_enabled"].default is True
