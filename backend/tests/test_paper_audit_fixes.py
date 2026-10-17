"""Paper defects N1–N3: pre-entry bar replay, passed min-R, legacy cleanup."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.paper_classification import (
    COMBO_ID_V1,
    COMBO_VERSION_V1,
    PATH_V1,
    SOURCE_V1_PAPER_WATCHER,
    STRATEGY_COMBO_02_V1,
)
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import (
    STREAM_LEGACY,
    STREAM_V1,
    PaperPosition,
    PaperTradeEngine,
)


def _engine() -> PaperTradeEngine:
    return PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
    )


def _hold(
    eng: PaperTradeEngine,
    *,
    symbol: str,
    stream: str,
    timeframe: str,
    opened_at: str,
    entry: float = 60000.0,
    stop: float = 59700.0,
    tp1: float = 60600.0,
    snippet: dict | None = None,
) -> PaperPosition:
    identity = {"source": "PASSED_CAP_BOOK", "stream": stream, "strategy_id": "COMBO_01"}
    if snippet:
        identity.update(snippet)
    pos = PaperPosition(
        id=f"{symbol}-{stream}",
        symbol=symbol,
        side="LONG",
        status="OPEN",
        entry_price=entry,
        stop_price=stop,
        tp1_price=tp1,
        quantity=0.033,
        risk_usd=9.90,
        opened_at=opened_at,
        timeframe=timeframe,
        signal_snippet=identity,
    )
    eng._open[eng.book_key(symbol, stream)] = pos
    return pos


def test_pre_entry_bar_does_not_stop_a_new_trade_after_an_earlier_trade_closed():
    eng = _engine()
    opened = "2026-10-17T12:05:00+00:00"
    pos = _hold(eng, symbol="BTCUSDT", stream="PASSED|X|LONG", timeframe="1h", opened_at=opened)
    # Bar closed at 11:00, before the new trade opened. Its low is through the stop.
    still_open = eng.replay_bar_ranges(
        {eng.replay_key("BTCUSDT", "1h"): [(datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc), 60100.0, 59500.0)]}
    )
    assert still_open == []
    assert pos.status == "OPEN"
    # The bar that closes at 14:00 is after entry and trades through the stop.
    closed = eng.replay_bar_ranges(
        {eng.replay_key("BTCUSDT", "1h"): [(datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc), 60100.0, 59500.0)]}
    )
    assert closed and closed[0].exit_reason == "STOP"
    assert closed[0].id == pos.id


def test_bar_replay_is_keyed_by_timeframe():
    eng = _engine()
    opened = "2026-10-17T12:05:00+00:00"
    pos = _hold(eng, symbol="ETHUSDT", stream="PASSED|X|LONG", timeframe="1h", opened_at=opened)
    # A 15m bar after entry trades through the stop. It must not hit the 1h position.
    missed = eng.replay_bar_ranges(
        {eng.replay_key("ETHUSDT", "15m"): [(datetime(2026, 10, 17, 12, 15, tzinfo=timezone.utc), 60100.0, 59000.0)]}
    )
    assert missed == []
    assert pos.status == "OPEN"
    # The forming 1h bar (opens 12:00, closes 13:00) is after entry and does hit.
    hit = eng.replay_bar_ranges(
        {eng.replay_key("ETHUSDT", "1h"): [(datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc), 60100.0, 59000.0)]}
    )
    assert hit and hit[0].exit_reason == "STOP"


def test_same_bar_stop_still_wins_over_target_after_entry():
    eng = _engine()
    pos = _hold(
        eng,
        symbol="BTCUSDT",
        stream="PASSED|X|LONG",
        timeframe="1h",
        opened_at="2026-10-17T12:05:00+00:00",
        tp1=61000.0,
    )
    closed = eng.replay_bar_ranges(
        {
            eng.replay_key("BTCUSDT", "1h"): [
                (datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc), 62000.0, 59000.0)
            ]
        }
    )
    assert closed and closed[0].exit_reason == "STOP"
    assert closed[0].id == pos.id


def _passed(eng: PaperTradeEngine, **overrides):
    payload = {
        "symbol": "ETHUSDT",
        "combination_id": "COMBO_03_TRANSITION_B",
        "timeframe": "1h",
        "direction": "LONG",
        "eval_result": {
            "status": "LONG_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 104.0,
        },
        "risk_percent": 0.02,
        "setup_bar_time_utc": "2026-10-17T12:00:00+00:00",
        "market_direction": "BULLISH",
    }
    payload.update(overrides)
    return eng.open_passed_position(**payload)


def _gated() -> PaperTradeEngine:
    """The opt-in fill re-check: TP1 must clear 2R from the fill."""
    eng = _engine()
    eng.passed_fill_min_rr = 2.0
    return eng


def test_fill_min_r_recheck_is_off_by_default_like_the_backtest():
    # The backtest regime book takes the trade whatever TP1 is worth from the
    # next-open fill; the signal cleared min R at the signal close.
    assert _engine().passed_fill_min_rr is None
    eng = _engine()
    pos = _passed(
        eng,
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 103.0,
        },
    )
    assert pos is not None
    assert pos.signal_snippet["tp1_r_at_fill"] == pytest.approx(1.5)


def test_passed_open_refused_when_next_open_is_past_tp1():
    eng = _gated()
    pos = _passed(
        eng,
        symbol="BTCUSDT",
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "entry_price": 60500.0,
            "stop_price": 59700.0,
            "tp1": 60400.0,
        },
    )
    assert pos is None
    assert eng._last_skip_reason.endswith("passed_rr")


def test_passed_open_refused_when_tp1_is_inside_min_rr():
    eng = _gated()
    pos = _passed(
        eng,
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 103.0,
        },
    )
    assert pos is None
    assert eng._last_skip_reason.endswith("passed_rr")


def test_passed_short_refused_when_tp1_is_above_entry():
    eng = _gated()
    pos = _passed(
        eng,
        combination_id="COMBO_01",
        direction="SHORT",
        eval_result={
            "status": "SHORT_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 102.0,
            "tp1": 101.0,
        },
        market_direction="BEARISH",
    )
    assert pos is None
    assert eng._last_skip_reason.endswith("passed_rr")


def test_passed_open_with_target_clearing_min_rr_still_opens():
    eng = _gated()
    long_pos = _passed(eng)
    assert long_pos is not None
    assert long_pos.side == "LONG"
    short = _passed(
        eng,
        symbol="AVAXUSDT",
        combination_id="COMBO_01",
        direction="SHORT",
        eval_result={
            "status": "SHORT_ENTRY_CANDIDATE",
            "entry_price": 100.0,
            "stop_price": 102.0,
            "tp1": 96.0,
        },
        setup_bar_time_utc="2026-10-17T13:00:00+00:00",
        market_direction="BEARISH",
    )
    assert short is not None
    assert short.side == "SHORT"


def test_close_legacy_closes_only_legacy_and_keeps_v1_and_passed():
    eng = _engine()
    _hold(
        eng,
        symbol="BTCUSDT",
        stream=STREAM_V1,
        timeframe="1h",
        opened_at="2026-10-17T12:00:00+00:00",
        snippet={
            "strategy_id": STRATEGY_COMBO_02_V1,
            "source": SOURCE_V1_PAPER_WATCHER,
            "combo_id": COMBO_ID_V1,
            "combo_version": COMBO_VERSION_V1,
            "path": PATH_V1,
        },
    )
    _hold(
        eng,
        symbol="ETHUSDT",
        stream="PASSED|COMBO_01|SHORT",
        timeframe="1h",
        opened_at="2026-10-17T12:00:00+00:00",
        snippet={"strategy_id": "COMBO_01", "source": "PASSED_CAP_BOOK", "stream": "PASSED|COMBO_01|SHORT"},
    )
    _hold(
        eng,
        symbol="SOLUSDT",
        stream=STREAM_LEGACY,
        timeframe="15m",
        opened_at="2026-10-17T12:00:00+00:00",
        snippet={"strategy_id": "RESEARCH_15M", "source": "LEGACY_SETUP_SIGNAL"},
    )
    result = eng.close_legacy_paper_positions(confirm=True)
    assert result["ok"] is True
    assert [row["symbol"] for row in result["closed"]] == ["SOLUSDT"]
    assert result["skipped_v1"] == ["BTCUSDT"]
    assert result["skipped_passed"] == ["ETHUSDT"]
    assert eng._open[eng.book_key("BTCUSDT", STREAM_V1)].status == "OPEN"
    assert eng._open[eng.book_key("ETHUSDT", "PASSED|COMBO_01|SHORT")].status == "OPEN"
    assert eng.book_key("SOLUSDT", STREAM_LEGACY) not in eng._open
