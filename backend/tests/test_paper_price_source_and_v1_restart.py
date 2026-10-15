"""Paper opens label where the live price came from, and V1 bars stay deduped after a restart.

The live price only labels the position. The fill stays at the backtest plan.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.services.paper_trade as paper_mod
from app.research.passed_cap_book import passed_stream
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperPosition, PaperTradeEngine

BAR = "2026-10-12T10:00:00+00:00"
NEXT_BAR = "2026-10-12T11:00:00+00:00"

# Tick-aligned plans (BTC default tick 0.1, NEAR generic tick 0.01).
V1_PLANS = {
    "BTCUSDT": {"entry": 60000.0, "stop": 59000.0, "tp1": 62000.0},
    "NEARUSDT": {"entry": 2.5, "stop": 2.4, "tp1": 2.7},
}
PASSED_PLANS = {
    ("BTCUSDT", "SHORT"): {"entry": 60000.0, "stop": 61000.0, "tp1": 58000.0},
    ("BTCUSDT", "LONG"): {"entry": 60000.0, "stop": 59000.0, "tp1": 62000.0},
    ("NEARUSDT", "SHORT"): {"entry": 2.5, "stop": 2.6, "tp1": 2.3},
    ("NEARUSDT", "LONG"): {"entry": 2.5, "stop": 2.4, "tp1": 2.7},
}
PASSED_CASES = [
    ("COMBO_02_V2", "SHORT", "BTCUSDT"),
    ("COMBO_02_V2", "SHORT", "NEARUSDT"),
    ("COMBO_03_TRANSITION_B", "LONG", "BTCUSDT"),
    ("COMBO_03_TRANSITION_B", "LONG", "NEARUSDT"),
]


# Close stamp for every engine here. It sits before BAR, as the wall clock did
# when these fixed bars were written, so the re-entry rule (setup bar after the
# exit bar) does not depend on the day the suite runs.
CLOSE_CLOCK = datetime(2026, 10, 12, 9, 30, tzinfo=timezone.utc)


def _engine() -> PaperTradeEngine:
    eng = PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    eng._now = lambda: CLOSE_CLOCK
    return eng


def _book() -> SimpleNamespace:
    # open_v1_combo_position reads only tier and risk_percent from the book.
    return SimpleNamespace(tier="core", risk_percent=0.02)


def _v1_eval(symbol: str = "BTCUSDT") -> dict:
    plan = V1_PLANS[symbol]
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "entry_price": plan["entry"],
        "stop_price": plan["stop"],
        "tp1": plan["tp1"],
        "htf": {
            "htf_alignment": "HTF_ALIGNED",
            "trend_1h": "BULLISH",
            "trend_4h": "BULLISH",
        },
    }


def _open_v1(
    eng: PaperTradeEngine,
    symbol: str = "BTCUSDT",
    bar: str = BAR,
    *,
    replay: bool = False,
) -> PaperPosition | None:
    return eng.open_v1_combo_position(
        symbol=symbol,
        timeframe="1h",
        book=_book(),
        eval_result=_v1_eval(symbol),
        setup_bar_time_utc=bar,
        replay=replay,
        emit_alert=False,
    )


def _passed_eval(symbol: str, side: str) -> dict:
    plan = PASSED_PLANS[(symbol, side)]
    return {
        "status": f"{side}_ENTRY_CANDIDATE",
        "entry_price": plan["entry"],
        "stop_price": plan["stop"],
        "tp1": plan["tp1"],
    }


def _open_passed(
    eng: PaperTradeEngine,
    combo: str,
    side: str,
    symbol: str,
    bar: str = BAR,
) -> PaperPosition | None:
    return eng.open_passed_position(
        symbol=symbol,
        combination_id=combo,
        timeframe="1h",
        direction=side,
        eval_result=_passed_eval(symbol, side),
        risk_percent=0.01,
        setup_bar_time_utc=bar,
    )


def _patch_live(monkeypatch, price: float | None, source: str) -> list[str]:
    calls: list[str] = []

    def _fake(symbol: str) -> tuple[float | None, str]:
        calls.append(symbol)
        return price, source

    monkeypatch.setattr(paper_mod, "_live_price_with_source", _fake)
    return calls


def _forbid_live(monkeypatch) -> None:
    def _boom(symbol: str):
        raise AssertionError(f"live price must not be read for {symbol}")

    monkeypatch.setattr(paper_mod, "_live_price_with_source", _boom)
    monkeypatch.setattr(paper_mod, "_live_price", _boom)


def _restart(eng: PaperTradeEngine) -> PaperTradeEngine:
    """New engine fed the rows the old one held, in their persisted JSON form."""
    book = eng.positions(closed_limit=100)
    rows = json.loads(json.dumps(book["open"] + book["closed"]))
    fresh = _engine()
    fresh.hydrate_from_rows(rows)
    return fresh


def _assert_fill_is_plan(pos: PaperPosition, plan: dict) -> None:
    assert pos.entry_price == plan["entry"]
    assert pos.stop_price == plan["stop"]
    assert pos.tp1_price == plan["tp1"]


def _assert_source_survives_api_form(pos: PaperPosition, source: str) -> None:
    row = pos.to_dict()
    assert row["signal_snippet"]["price_source"] == source
    assert json.loads(json.dumps(row))["signal_snippet"]["price_source"] == source


# --- A. source string stored -------------------------------------------------


@pytest.mark.parametrize("source", ["mark_price", "ticker_price"])
@pytest.mark.parametrize("symbol", ["BTCUSDT", "NEARUSDT"])
def test_v1_open_stores_live_source_and_fills_at_plan(monkeypatch, symbol, source):
    plan = V1_PLANS[symbol]
    far_live = plan["entry"] * 0.2  # 80% below the plan, under the stop
    calls = _patch_live(monkeypatch, far_live, source)
    eng = _engine()

    pos = _open_v1(eng, symbol)

    assert pos is not None
    assert calls == [symbol]
    assert pos.side == "LONG"
    assert pos.signal_snippet["strategy_id"] == "COMBO_02_V1"
    assert pos.signal_snippet["price_source"] == source
    assert pos.signal_snippet["live_mark"] == far_live
    assert pos.mark_price == far_live
    _assert_fill_is_plan(pos, plan)
    _assert_source_survives_api_form(pos, source)


def test_v1_open_without_live_price_still_fills_at_plan(monkeypatch):
    _patch_live(monkeypatch, None, "unavailable")
    eng = _engine()

    pos = _open_v1(eng)

    assert pos is not None
    assert pos.signal_snippet["price_source"] == "unavailable"
    assert pos.mark_price is None
    assert pos.signal_snippet["live_mark"] is None
    _assert_fill_is_plan(pos, V1_PLANS["BTCUSDT"])
    row = json.loads(json.dumps(pos.to_dict()))
    assert row["mark_price"] is None
    assert row["signal_snippet"]["live_mark"] is None
    assert row["signal_snippet"]["price_source"] == "unavailable"


def test_v1_replay_stores_plan_and_reads_no_live_price(monkeypatch):
    _forbid_live(monkeypatch)
    eng = _engine()

    pos = _open_v1(eng, replay=True)

    assert pos is not None
    assert pos.signal_snippet["price_source"] == "plan"
    assert pos.mark_price is None
    assert pos.signal_snippet["live_mark"] is None
    _assert_fill_is_plan(pos, V1_PLANS["BTCUSDT"])
    _assert_source_survives_api_form(pos, "plan")


@pytest.mark.parametrize(("combo", "side", "symbol"), PASSED_CASES)
def test_cap_book_open_stores_plan_and_reads_no_live_price(
    monkeypatch, combo, side, symbol
):
    _forbid_live(monkeypatch)
    eng = _engine()

    pos = _open_passed(eng, combo, side, symbol)

    assert pos is not None
    assert pos.side == side
    assert pos.signal_snippet["strategy_id"] == combo
    assert pos.signal_snippet["source"] == "PASSED_CAP_BOOK"
    assert pos.signal_snippet["price_source"] == "plan"
    assert pos.mark_price is None
    _assert_fill_is_plan(pos, PASSED_PLANS[(symbol, side)])
    _assert_source_survives_api_form(pos, "plan")


def test_positions_payload_carries_the_stored_source(monkeypatch):
    _patch_live(monkeypatch, 60010.0, "mark_price")
    eng = _engine()
    assert _open_v1(eng) is not None
    assert _open_passed(eng, "COMBO_02_V2", "SHORT", "BTCUSDT") is not None

    payload = json.loads(json.dumps(eng.positions()))

    by_strategy = {
        row["signal_snippet"]["strategy_id"]: row["signal_snippet"]["price_source"]
        for row in payload["open"]
    }
    assert by_strategy == {"COMBO_02_V1": "mark_price", "COMBO_02_V2": "plan"}


# --- B. V1 restart dedupe ----------------------------------------------------


def test_v1_open_writes_the_literal_open_time_key(monkeypatch):
    _patch_live(monkeypatch, 60010.0, "mark_price")
    eng = _engine()

    assert _open_v1(eng) is not None

    assert "BTCUSDT|2026-10-12T10:00:00+00:00|V1|1h" in eng._opened_keys


@pytest.mark.parametrize("symbol", ["BTCUSDT", "NEARUSDT"])
def test_v1_same_bar_does_not_reopen_after_restart(monkeypatch, symbol):
    plan = V1_PLANS[symbol]
    _patch_live(monkeypatch, plan["entry"], "mark_price")
    eng = _engine()
    first = _open_v1(eng, symbol)
    assert first is not None
    closed = eng.tick({symbol: plan["stop"]})
    assert [p.exit_reason for p in closed] == ["STOP"]
    assert eng.status()["open_count"] == 0

    fresh = _restart(eng)

    old_key = f"{symbol}|{BAR}|A|"
    new_key = f"{symbol}|{BAR}|V1|1h"
    assert old_key in fresh._opened_keys
    assert new_key in fresh._opened_keys
    assert fresh._opened_keys == {old_key, new_key}
    assert _open_v1(fresh, symbol) is None
    book = fresh.positions(closed_limit=100)
    assert book["open"] == []
    assert [row["id"] for row in book["closed"]] == [first.id]


def test_v1_literal_keys_after_restart(monkeypatch):
    _patch_live(monkeypatch, 60000.0, "mark_price")
    eng = _engine()
    assert _open_v1(eng) is not None
    eng.tick({"BTCUSDT": 59000.0})

    fresh = _restart(eng)

    assert "BTCUSDT|2026-10-12T10:00:00+00:00|A|" in fresh._opened_keys
    assert "BTCUSDT|2026-10-12T10:00:00+00:00|V1|1h" in fresh._opened_keys


def test_v1_restart_block_comes_from_the_open_time_key(monkeypatch):
    """The old-format key alone does not stop the bar from reopening."""
    _patch_live(monkeypatch, 60000.0, "mark_price")
    eng = _engine()
    assert _open_v1(eng) is not None
    eng.tick({"BTCUSDT": 59000.0})
    fresh = _restart(eng)
    assert _open_v1(fresh) is None

    fresh._opened_keys.discard(f"BTCUSDT|{BAR}|V1|1h")

    assert f"BTCUSDT|{BAR}|A|" in fresh._opened_keys
    assert _open_v1(fresh) is not None


@pytest.mark.parametrize("symbol", ["BTCUSDT", "NEARUSDT"])
def test_v1_next_bar_still_opens_after_restart(monkeypatch, symbol):
    plan = V1_PLANS[symbol]
    _patch_live(monkeypatch, plan["entry"], "mark_price")
    eng = _engine()
    assert _open_v1(eng, symbol) is not None
    eng.tick({symbol: plan["stop"]})
    fresh = _restart(eng)

    pos = _open_v1(fresh, symbol, NEXT_BAR)

    assert pos is not None
    assert pos.source_candle_ts == NEXT_BAR
    assert f"{symbol}|{NEXT_BAR}|V1|1h" in fresh._opened_keys
    assert fresh.status()["open_count"] == 1
    _assert_fill_is_plan(pos, plan)


def test_v1_open_position_survives_restart_and_blocks_its_bar(monkeypatch):
    _patch_live(monkeypatch, 60000.0, "mark_price")
    eng = _engine()
    first = _open_v1(eng)
    assert first is not None

    fresh = _restart(eng)

    assert f"BTCUSDT|{BAR}|V1|1h" in fresh._opened_keys
    assert _open_v1(fresh) is None
    open_rows = fresh.positions()["open"]
    assert [row["id"] for row in open_rows] == [first.id]
    assert open_rows[0]["signal_snippet"]["price_source"] == "mark_price"


# --- Guard: cap-book keys unchanged ------------------------------------------


def test_passed_stream_id_is_unchanged():
    assert passed_stream("COMBO_02_V2", "SHORT") == "PASSED|COMBO_02_V2|SHORT"


@pytest.mark.parametrize(("combo", "side", "symbol"), PASSED_CASES)
def test_cap_book_key_is_unchanged_after_restart(monkeypatch, combo, side, symbol):
    _forbid_live(monkeypatch)
    plan = PASSED_PLANS[(symbol, side)]
    eng = _engine()
    first = _open_passed(eng, combo, side, symbol)
    assert first is not None
    key = f"{symbol}|{BAR}|PASSED|{combo}|{side}|1h"
    assert eng._opened_keys == {key}
    closed = eng.tick({symbol: plan["stop"]})
    assert [p.exit_reason for p in closed] == ["STOP"]

    fresh = _restart(eng)

    assert fresh._opened_keys == {key}
    assert _open_passed(fresh, combo, side, symbol) is None
    assert fresh.positions()["open"] == []
    assert _open_passed(fresh, combo, side, symbol, NEXT_BAR) is not None


def test_cap_book_short_key_literal_after_restart(monkeypatch):
    _forbid_live(monkeypatch)
    eng = _engine()
    assert _open_passed(eng, "COMBO_02_V2", "SHORT", "BTCUSDT") is not None
    eng.tick({"BTCUSDT": 61000.0})

    fresh = _restart(eng)

    assert fresh._opened_keys == {
        "BTCUSDT|2026-10-12T10:00:00+00:00|PASSED|COMBO_02_V2|SHORT|1h"
    }
