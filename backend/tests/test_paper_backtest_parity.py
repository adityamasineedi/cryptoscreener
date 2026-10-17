"""The paper passed book fills and exits the way the backtest regime book does.

The backtest regime book prices every trade with ``causal_execution``
(``CAUSAL_NEXT_OPEN``). These tests run one signal through the paper path
(watcher fill + engine walk on closed bars) and through
``execute_causal_trade`` on the same synthetic bars, and compare.

Standalone engines only. Nothing touches the live book.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.services.passed_paper_watcher as watcher_mod
from app.research.causal_execution import execute_causal_trade
from app.research.config import ResearchConfig
from app.research.trade_fees import enrich_trade_execution
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PASSED_GAP_EXIT_GRACE_SECONDS, PaperTradeEngine
from app.services.passed_paper_watcher import FILL_PENDING, PassedPaperWatcher

H = timedelta(hours=1)
T0 = datetime(2026, 10, 12, 0, tzinfo=timezone.utc)
SLIP = ResearchConfig().entry_slippage_rate
EXIT_SLIP = ResearchConfig().exit_slippage_rate
SIGNAL = 5  # index of the signal bar in the synthetic series

LONG = {"side": "LONG", "combo": "COMBO_03_TRANSITION_B", "stop": 98.0, "tp1": 104.0}
SHORT = {"side": "SHORT", "combo": "COMBO_01", "stop": 102.0, "tp1": 96.0}


def _engine() -> PaperTradeEngine:
    return PaperTradeEngine(
        starting_equity=1000.0, enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
    )


def _bar(i: int, o: float, h: float, low: float, c: float, *, t: datetime | None = None) -> dict:
    return {"time": t or T0 + i * H, "open": o, "high": h, "low": low, "close": c}


def _series(after: list[tuple[float, float, float, float]], *, signal_close: float = 100.0) -> list[dict]:
    """Flat bars up to the signal bar, then ``after`` (open, high, low, close) per bar."""
    bars = [_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(SIGNAL)]
    bars.append(_bar(SIGNAL, 100.0, 100.5, 99.5, signal_close))
    for k, (o, h, low, c) in enumerate(after, start=1):
        bars.append(_bar(SIGNAL + k, o, h, low, c))
    return bars


def _plan(spec: dict, entry: float = 100.0) -> dict:
    status = "LONG_ENTRY_CANDIDATE" if spec["side"] == "LONG" else "SHORT_ENTRY_CANDIDATE"
    return {"status": status, "entry_price": entry, "stop_price": spec["stop"], "tp1": spec["tp1"]}


def _paper_open(eng: PaperTradeEngine, spec: dict, bars: list[dict], monkeypatch) -> Any:
    """The watcher's fill step on the signal bar, then the engine open."""
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    plan = _plan(spec, bars[SIGNAL]["close"])
    monkeypatch.setattr(
        watcher_mod,
        "evaluate_combination_at_bar",
        lambda **kw: dict(plan) if kw["combination"].combination_id == spec["combo"] else {"status": "NO_SETUP"},
    )
    history = [_bar(i, 100.0, 100.5, 99.5, 100.0, t=bars[SIGNAL]["time"] - (249 - i) * H) for i in range(250)]
    history[-1] = dict(bars[SIGNAL])
    h4_last = (bars[SIGNAL]["time"] + H).replace(hour=((bars[SIGNAL]["time"] + H).hour // 4) * 4) - 4 * H
    h4 = [_bar(0, 100, 101, 99, 100, t=h4_last - (79 - i) * 4 * H) for i in range(80)]
    nxt = bars[SIGNAL + 1] if len(bars) > SIGNAL + 1 else None
    opened = watcher.on_closed_1h(
        "ETHUSDT",
        candles_1h=history,
        candles_4h=h4,
        candles_15m=history,
        market_direction="BULLISH" if spec["side"] == "LONG" else "BEARISH",
        force=True,
        next_bar=nxt,
    )
    return opened[0] if opened else None, watcher


def _paper_exit(eng: PaperTradeEngine, bars: list[dict]) -> list:
    return eng.resolve_passed_exits({eng.replay_key("ETHUSDT", "1h"): bars})


def _backtest(spec: dict, bars: list[dict]) -> dict:
    trade = {
        "direction": spec["side"],
        "entry_index": SIGNAL,
        "timeframe": "1h",
        "entry_price": bars[SIGNAL]["close"],
        "stop_price": spec["stop"],
        "tp1": spec["tp1"],
    }
    return execute_causal_trade(trade, bars, entry_slippage_rate=SLIP, exit_slippage_rate=EXIT_SLIP)


def _assert_same(pos, bt: dict) -> None:
    """Paper fill, exit and dollars equal the backtest's at the paper quantity."""
    assert pos.entry_price == pytest.approx(bt["entry_price"], rel=1e-12)
    assert pos.exit_price == pytest.approx(bt["exit_price"], rel=1e-12)
    reason = {"SL": "STOP"}.get(bt["outcome"], bt["outcome"])
    assert pos.exit_reason == reason
    priced = enrich_trade_execution(
        {**bt, "entry_type": "MARKET"},
        risk_usd=pos.risk_usd,
        taker_fee=0.0004,
        maker_fee=0.0002,
        leverage=2.0,
        account_equity=1e9,
        slippage_rate=0.0,
    )
    assert priced["qty"] == pytest.approx(pos.quantity, rel=1e-9)
    execution = pos.signal_snippet["paper_execution"]
    assert execution["slippage_usd"] == 0.0
    assert execution["entry_fee_usd"] + execution["exit_fee_usd"] == pytest.approx(
        priced["fee_total_usd"], abs=1e-6
    )
    assert pos.pnl_usd == pytest.approx(priced["net_pnl_usd"], abs=1e-6)
    assert pos.r_multiple == pytest.approx(priced["r_net"], abs=1e-9)


# --- 1. slippage: one adverse slip per leg, in the price, no second charge -------


@pytest.mark.usefixtures("passed_book_open")
@pytest.mark.parametrize("spec", [LONG, SHORT], ids=["long", "short"])
@pytest.mark.parametrize("to", ["stop", "tp1"])
def test_fill_exit_and_fees_equal_the_causal_model(monkeypatch, spec, to):
    level = spec[to]
    # Fill bar opens at 100.2; the bar after it trades to the level.
    bars = _series([(100.2, 100.6, 99.8, 100.1), (100.1, max(100.1, level), min(100.1, level), level)])
    eng = _engine()
    pos, _ = _paper_open(eng, spec, bars, monkeypatch)
    assert pos is not None
    sign = 1 if spec["side"] == "LONG" else -1
    assert pos.entry_price == pytest.approx(100.2 * (1 + sign * SLIP))
    assert pos.signal_snippet["next_open"] == 100.2
    assert pos.signal_snippet["signal_entry_price"] == 100.0
    assert pos.signal_snippet["paper_execution"]["slippage_rate"] == 0.0
    closed = _paper_exit(eng, bars)
    assert closed == [pos]
    bt = _backtest(spec, bars)
    assert pos.exit_price == pytest.approx(level * (1 - sign * EXIT_SLIP))
    _assert_same(pos, bt)
    # Stamped at the exit bar's open, as the backtest's exit_time.
    assert pos.closed_at == bars[SIGNAL + 2]["time"].isoformat() == bt["exit_time"]


@pytest.mark.usefixtures("passed_book_open")
def test_no_exit_before_the_bar_closes_and_live_marks_do_not_exit(monkeypatch):
    bars = _series([(100.2, 100.6, 99.8, 100.1)])
    eng = _engine()
    pos, _ = _paper_open(eng, LONG, bars, monkeypatch)
    # Only bars before the fill bar: nothing to walk.
    assert _paper_exit(eng, bars[: SIGNAL + 1]) == []
    # A live mark through the stop, and a forming-bar range through it, only mark.
    assert eng.tick({"ETHUSDT": 97.0}) == []
    assert eng.replay_bar_ranges(
        {eng.replay_key("ETHUSDT", "1h"): [(bars[-1]["time"], 100.2, 97.0)]}
    ) == []
    assert pos.status == "OPEN"
    assert pos.unrealized_r is not None and pos.unrealized_r < -1.0


# --- 2. 2R at the fill: the backtest takes it, so paper does -----------------


@pytest.mark.usefixtures("passed_book_open")
def test_tp1_under_2r_from_the_fill_opens_and_matches_the_backtest(monkeypatch):
    spec = {**LONG, "tp1": 103.0}  # 1.5R from a 100 fill
    bars = _series([(100.4, 100.6, 100.0, 100.5), (100.5, 103.2, 100.4, 103.0)])
    eng = _engine()
    pos, _ = _paper_open(eng, spec, bars, monkeypatch)
    assert pos is not None
    assert pos.signal_snippet["tp1_r_at_fill"] < 2.0
    _paper_exit(eng, bars)
    bt = _backtest(spec, bars)
    assert bt["outcome"] == "TP1"
    _assert_same(pos, bt)


@pytest.mark.usefixtures("passed_book_open")
def test_opt_in_fill_min_r_still_refuses(monkeypatch):
    spec = {**LONG, "tp1": 103.0}
    bars = _series([(100.4, 100.6, 100.0, 100.5)])
    eng = _engine()
    eng.passed_fill_min_rr = 2.0
    pos, watcher = _paper_open(eng, spec, bars, monkeypatch)
    assert pos is None
    assert watcher.regime_status()["latest_signals"][0]["reason"] == "engine_refused:passed_rr"


# --- 3. no fill bar: no fill (FILL_BAR_GAP), never the signal close ----------


@pytest.mark.usefixtures("passed_book_open")
def test_missing_next_bar_is_not_filled_at_the_signal_close(monkeypatch):
    bars = _series([])
    # The next bar printed two hours after the signal bar: the one between is missing.
    bars.append(_bar(SIGNAL + 2, 100.3, 100.5, 100.0, 100.2))
    eng = _engine()
    pos, watcher = _paper_open(eng, LONG, bars, monkeypatch)
    assert pos is None
    assert eng.status()["open_count"] == 0
    newest = watcher.regime_status()["latest_signals"][0]
    assert (newest["outcome"], newest["reason"]) == ("not_opened", "FILL_BAR_GAP")
    # Processed: the bar is not retried.
    assert any(key.endswith(bars[SIGNAL]["time"].isoformat()) for key in watcher._processed)
    # The backtest leaves the same signal unfilled.
    gap_bars = bars[: SIGNAL + 1] + [bars[-1]]
    assert _backtest(LONG, gap_bars)["causal_skip_reason"] == "FILL_BAR_GAP"


@pytest.mark.usefixtures("passed_book_open")
def test_next_bar_not_in_yet_is_retried(monkeypatch):
    bars = _series([])
    eng = _engine()
    monkeypatch.setattr("app.services.ohlcv_store.ohlcv_store.get_open", lambda *_a: None)
    pos, watcher = _paper_open(eng, LONG, bars, monkeypatch)
    assert pos is None
    assert watcher.last_skip == f"ETHUSDT:{FILL_PENDING}"
    assert watcher._processed == set()


# --- 4. gaps: through the stop at the open; GAP_EXIT over missing bars --------


@pytest.mark.usefixtures("passed_book_open")
@pytest.mark.parametrize("spec", [LONG, SHORT], ids=["long", "short"])
def test_bar_opening_through_the_stop_exits_at_that_open(monkeypatch, spec):
    through = 97.0 if spec["side"] == "LONG" else 103.0
    bars = _series([(100.2, 100.6, 99.8, 100.1), (through, through + 0.5, through - 0.5, through)])
    eng = _engine()
    pos, _ = _paper_open(eng, spec, bars, monkeypatch)
    _paper_exit(eng, bars)
    bt = _backtest(spec, bars)
    assert bt["outcome"] == pos.exit_reason == "GAP_THROUGH_STOP"
    sign = 1 if spec["side"] == "LONG" else -1
    assert pos.exit_price == pytest.approx(through * (1 - sign * EXIT_SLIP))
    _assert_same(pos, bt)


@pytest.mark.usefixtures("passed_book_open")
def test_bar_reaching_stop_and_target_exits_at_the_stop(monkeypatch):
    bars = _series([(100.2, 100.6, 99.8, 100.1), (100.1, 105.0, 97.0, 101.0)])
    eng = _engine()
    pos, _ = _paper_open(eng, LONG, bars, monkeypatch)
    _paper_exit(eng, bars)
    bt = _backtest(LONG, bars)
    assert bt["outcome"] == "SL" and pos.exit_reason == "STOP"
    _assert_same(pos, bt)


@pytest.mark.usefixtures("passed_book_open")
def test_missing_bars_inside_the_trade_exit_at_the_next_open_after_the_grace(monkeypatch):
    bars = _series([(100.2, 100.6, 99.8, 100.1)])
    bars.append(_bar(SIGNAL + 4, 99.0, 99.4, 98.8, 99.1))  # bars +2 and +3 missing
    eng = _engine()
    pos, _ = _paper_open(eng, LONG, bars, monkeypatch)
    resume = bars[-1]["time"]
    eng._now = lambda: resume + H + timedelta(seconds=PASSED_GAP_EXIT_GRACE_SECONDS - 1)
    assert _paper_exit(eng, bars) == []  # the store may still backfill the gap
    eng._now = lambda: resume + H + timedelta(seconds=PASSED_GAP_EXIT_GRACE_SECONDS + 1)
    assert _paper_exit(eng, bars) == [pos]
    bt = _backtest(LONG, bars)
    assert bt["outcome"] == pos.exit_reason == "GAP_EXIT"
    assert pos.exit_price == pytest.approx(99.0 * (1 - EXIT_SLIP))
    assert pos.closed_at == resume.isoformat()
    assert pos.signal_snippet["causal_exit"]["gap_missing_bars"] == 2
    _assert_same(pos, bt)


@pytest.mark.usefixtures("passed_book_open")
def test_a_healed_gap_is_walked_normally(monkeypatch):
    bars = _series([(100.2, 100.6, 99.8, 100.1), (100.1, 100.3, 99.9, 100.0), (100.0, 104.5, 99.9, 104.2)])
    gapped = bars[: SIGNAL + 2] + bars[SIGNAL + 3 :]
    eng = _engine()
    pos, _ = _paper_open(eng, LONG, bars, monkeypatch)
    eng._now = lambda: bars[-1]["time"]  # inside the grace
    assert _paper_exit(eng, gapped) == []
    assert _paper_exit(eng, bars) == [pos]
    bt = _backtest(LONG, bars)
    assert bt["outcome"] == "TP1"
    _assert_same(pos, bt)


@pytest.mark.usefixtures("passed_book_open")
def test_exit_bar_blocks_reentry_like_the_regime_book(monkeypatch):
    bars = _series([(100.2, 100.6, 99.8, 100.1), (100.1, 100.3, 97.5, 98.0)])
    eng = _engine()
    pos, _ = _paper_open(eng, LONG, bars, monkeypatch)
    _paper_exit(eng, bars)
    exit_bar = bars[SIGNAL + 2]["time"]
    assert eng._last_exit_bar_open[eng.book_key("ETHUSDT", "PASSED|COMBO_03_TRANSITION_B|LONG")] == exit_bar
    reopen = dict(
        symbol="ETHUSDT",
        combination_id="COMBO_03_TRANSITION_B",
        timeframe="1h",
        direction="LONG",
        eval_result=_plan(LONG),
        risk_percent=0.02,
    )
    # run_symbol skips a signal on the exit bar (i <= busy_until) and takes the next one.
    assert eng.open_passed_position(**reopen, setup_bar_time_utc=exit_bar.isoformat()) is None
    assert eng.open_passed_position(**reopen, setup_bar_time_utc=(exit_bar + H).isoformat()) is not None


def test_positions_opened_before_the_change_keep_their_exit_path():
    """A passed row without the causal marker (opened earlier) still exits on marks."""
    from app.services.paper_trade import PaperPosition

    eng = _engine()
    pos = PaperPosition(
        id="old",
        symbol="ETHUSDT",
        side="LONG",
        entry_price=100.0,
        stop_price=98.0,
        tp1_price=104.0,
        quantity=1.0,
        risk_usd=2.0,
        opened_at="2026-10-12T05:05:00+00:00",
        timeframe="1h",
        signal_snippet={"source": "PASSED_CAP_BOOK", "stream": "PASSED|X|LONG"},
    )
    eng._open[eng.book_key("ETHUSDT", "PASSED|X|LONG")] = pos
    assert _paper_exit(eng, _series([(100.0, 100.0, 97.0, 97.0)])) == []
    closed = eng.tick({"ETHUSDT": 97.5})
    assert closed and closed[0].exit_reason == "STOP"
