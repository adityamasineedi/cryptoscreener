"""A stream does not re-enter on or before the bar it last exited on.

The backtest walk skips the exit bar, so its earliest re-entry is exit bar + 1.
Standalone engines only. Nothing touches the live book.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.research.passed_cap_book import passed_stream
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import (
    STREAM_V1,
    STREAM_V2,
    STREAM_V21A,
    PaperPosition,
    PaperTradeEngine,
    exit_bar_open,
)

T0 = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
SYM = "BTCUSDT"
# Tick-aligned plans (BTC default tick 0.1).
LONG = {"entry": 60000.0, "stop": 59000.0, "tp1": 62000.0}
SHORT = {"entry": 60000.0, "stop": 61000.0, "tp1": 58000.0}
PASSED_COMBO = "COMBO_02_V2"
PATHS = ["v1", "passed", "v21a", "experimental"]
STREAMS = {
    "v1": STREAM_V1,
    "passed": passed_stream(PASSED_COMBO, "SHORT"),
    "v21a": STREAM_V21A,
    "experimental": STREAM_V2,
}
# Price that hits each path's stop.
STOP_TICK = {"v1": 59000.0, "passed": 61000.0, "v21a": 59000.0, "experimental": 59000.0}
BLOCKED = f"{SYM}:reentry_not_after_exit_bar"


def _engine() -> PaperTradeEngine:
    return PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
    )


def _long_eval() -> dict:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "entry_price": LONG["entry"],
        "stop_price": LONG["stop"],
        "tp1": LONG["tp1"],
        "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
    }


def _open(
    eng: PaperTradeEngine,
    path: str,
    bar: datetime,
    *,
    replay: bool = False,
    combo: str = PASSED_COMBO,
) -> PaperPosition | None:
    stamp = bar.isoformat()
    if path == "v1":
        return eng.open_v1_combo_position(
            symbol=SYM,
            timeframe="1h",
            book=SimpleNamespace(tier="core", risk_percent=0.02),
            eval_result=_long_eval(),
            setup_bar_time_utc=stamp,
            replay=replay,
            emit_alert=False,
        )
    if path == "passed":
        return eng.open_passed_position(
            symbol=SYM,
            combination_id=combo,
            timeframe="1h",
            direction="SHORT",
            eval_result={
                "status": "SHORT_ENTRY_CANDIDATE",
                "entry_price": SHORT["entry"],
                "stop_price": SHORT["stop"],
                "tp1": SHORT["tp1"],
            },
            risk_percent=0.02,
            setup_bar_time_utc=stamp,
            market_direction="NEUTRAL",
        )
    if path == "v21a":
        return eng.open_v21a_position(
            symbol=SYM,
            timeframe="1h",
            eval_result=_long_eval(),
            risk_percent=0.02,
            signal_snippet={
                "strategy_id": "COMBO_02_V2_1_A",
                "source": "V21A_PAPER_WATCHER",
            },
            setup_bar_time_utc=stamp,
            replay=replay,
            emit_alert=False,
        )
    return eng.open_experimental_position(
        symbol=SYM,
        timeframe="1h",
        eval_result=_long_eval(),
        risk_percent=0.0025,
        signal_snippet={
            "strategy_id": "COMBO_02_V2_RESEARCH",
            "source": "V2_CANDIDATE_PAPER_WATCHER",
        },
        setup_bar_time_utc=stamp,
        replay=replay,
        emit_alert=False,
    )


def _exit_during(eng: PaperTradeEngine, path: str, when: datetime) -> PaperPosition:
    eng._now = lambda: when
    closed = eng.tick({SYM: STOP_TICK[path]})
    assert len(closed) == 1 and closed[0].status == "CLOSED"
    return closed[0]


def _latest_rows(eng: PaperTradeEngine) -> list[dict]:
    """Last persisted state per trade id, as the ledger would hold it."""
    latest: dict[str, dict] = {}
    for row in eng.drain_persist_queue():
        latest[row["id"]] = row
    return list(latest.values())


def test_exit_bar_open_floors_to_the_position_timeframe():
    assert exit_bar_open("2026-10-12T11:25:30.5+00:00", "1h") == T1
    assert exit_bar_open("2026-10-12T11:25:30Z", "1h") == T1
    # Naive values are UTC. The bar's own open time belongs to that bar.
    assert exit_bar_open(datetime(2026, 10, 12, 11, 0, 0), "1h") == T1
    assert exit_bar_open(T1 + timedelta(minutes=25), "15m") == T1 + timedelta(minutes=15)
    assert exit_bar_open("", "1h") is None
    assert exit_bar_open(T1, "nonsense") is None


@pytest.mark.parametrize("path", PATHS)
def test_exit_bar_is_refused_and_the_next_bar_opens(path):
    eng = _engine()
    first = _open(eng, path, T0)
    assert first is not None
    when = T1 + timedelta(minutes=25)
    closed = _exit_during(eng, path, when)
    # The close stamp keeps its stored format.
    assert closed.closed_at == when.isoformat()
    assert eng._last_exit_bar_open[eng.book_key(SYM, STREAMS[path])] == T1

    assert _open(eng, path, T1) is None
    assert eng._last_skip_reason == BLOCKED
    assert eng.status()["last_skip_reason"] == BLOCKED
    assert not eng.has_open(SYM, STREAMS[path])

    again = _open(eng, path, T2)
    assert again is not None
    # The plan is untouched by the re-entry rule.
    assert again.entry_price == first.entry_price
    assert again.stop_price == first.stop_price
    assert again.tp1_price == first.tp1_price
    assert again.quantity > 0
    assert again.source_candle_ts == T2.isoformat()


def test_setup_bars_older_than_the_exit_bar_are_refused_too():
    eng = _engine()
    assert _open(eng, "passed", T0) is not None
    _exit_during(eng, "passed", T2 + timedelta(minutes=5))
    # T1 was skipped while the position was open. It is still not after the exit bar.
    assert _open(eng, "passed", T1) is None
    assert eng._last_skip_reason == BLOCKED
    assert _open(eng, "passed", T2) is None
    assert _open(eng, "passed", T2 + timedelta(hours=1)) is not None


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("closed_at_as_datetime", [False, True])
def test_rule_survives_a_restart_from_the_persisted_rows(path, closed_at_as_datetime):
    eng = _engine()
    assert _open(eng, path, T0) is not None
    _exit_during(eng, path, T1 + timedelta(minutes=25))
    rows = _latest_rows(eng)
    assert [r["status"] for r in rows] == ["CLOSED"]
    if closed_at_as_datetime:
        # Postgres hands back TIMESTAMPTZ as datetime.
        rows[0]["closed_at"] = datetime.fromisoformat(rows[0]["closed_at"])

    fresh = _engine()
    fresh.hydrate_from_rows(rows)
    assert fresh._last_exit_bar_open == {fresh.book_key(SYM, STREAMS[path]): T1}
    assert _open(fresh, path, T1) is None
    assert fresh._last_skip_reason == BLOCKED
    assert _open(fresh, path, T2) is not None


def test_hydrate_keeps_the_latest_exit_per_stream():
    def _row(pid: str, closed_at: datetime, status: str = "CLOSED") -> dict:
        return {
            "id": pid,
            "symbol": SYM,
            "side": "SHORT",
            "status": status,
            "entry_price": SHORT["entry"],
            "stop_price": SHORT["stop"],
            "tp1_price": SHORT["tp1"],
            "quantity": 0.001,
            "risk_usd": 1.0,
            "opened_at": (closed_at - timedelta(hours=1)).isoformat(),
            "closed_at": closed_at.isoformat(),
            "pnl_usd": -1.0,
            "source_candle_ts": (closed_at - timedelta(hours=2)).isoformat(),
            "timeframe": "1h",
            "signal_snippet": {
                "source": "PASSED_CAP_BOOK",
                "strategy_id": PASSED_COMBO,
                "stream": STREAMS["passed"],
            },
        }

    fresh = _engine()
    fresh.hydrate_from_rows(
        [
            _row("old", T0 + timedelta(minutes=10)),
            _row("new", T1 + timedelta(minutes=10)),
            # A later cancellation is not an exit.
            _row("cancelled", T2 + timedelta(minutes=10), status="CANCELLED"),
        ]
    )
    assert fresh._last_exit_bar_open == {fresh.book_key(SYM, STREAMS["passed"]): T1}
    assert _open(fresh, "passed", T1) is None
    assert _open(fresh, "passed", T2) is not None


def test_cancelled_position_does_not_block_reentry():
    eng = _engine()
    assert _open(eng, "passed", T0) is not None
    eng._now = lambda: T1 + timedelta(minutes=25)
    cancelled = eng._cancel_open(SYM, reason="legacy_cleanup", stream=STREAMS["passed"])
    assert cancelled is not None and cancelled.status == "CANCELLED"
    assert eng._last_exit_bar_open == {}
    rows = _latest_rows(eng)
    assert _open(eng, "passed", T1) is not None

    fresh = _engine()
    fresh.hydrate_from_rows(rows)
    assert fresh._last_exit_bar_open == {}
    assert _open(fresh, "passed", T1) is not None


def test_other_streams_on_the_same_symbol_are_not_blocked():
    eng = _engine()
    assert _open(eng, "passed", T0) is not None
    _exit_during(eng, "passed", T1 + timedelta(minutes=25))
    assert _open(eng, "passed", T1) is None
    # Another passed side, and V1, on the same symbol and the same bar.
    assert _open(eng, "passed", T1, combo="COMBO_01") is not None
    assert _open(eng, "v1", T1) is not None


@pytest.mark.parametrize("path", ["v1", "v21a", "experimental"])
def test_replay_opens_are_unaffected(path):
    # Replay closes carry the wall clock while replay bars are historical.
    eng = _engine()
    assert _open(eng, path, T0, replay=True) is not None
    _exit_during(eng, path, datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert _open(eng, path, T1, replay=True) is not None


def test_reset_clears_the_exit_map():
    eng = _engine()
    assert _open(eng, "passed", T0) is not None
    _exit_during(eng, "passed", T1 + timedelta(minutes=25))
    assert eng._last_exit_bar_open
    eng.reset()
    assert eng._last_exit_bar_open == {}
    assert _open(eng, "passed", T1) is not None
