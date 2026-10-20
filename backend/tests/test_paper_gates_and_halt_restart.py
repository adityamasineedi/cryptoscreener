"""Every paper opener runs the risk gate, and a restart keeps the halts the ledger justifies.

Paper only. No exchange orders, no database: engines are in memory and the
"ledger" is the rows one engine hands to the next, in their persisted JSON form.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import app.services.paper_risk as pr
import app.services.paper_trade as paper_mod
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperPosition, PaperTradeEngine
from passed_paper_bars import exit_passed_on_bar

SYM = "BTCUSDT"
PLAN = {"entry": 60000.0, "stop": 59000.0, "tp1": 62000.0}
BAR0 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)

OPENERS = ["passed", "experimental", "v21a"]


def _bar(i: int) -> str:
    return (BAR0 + timedelta(hours=i)).isoformat()


def _policy(**overrides) -> PaperRiskPolicy:
    """Gates on. Each test tightens the one limit it is about."""
    base = dict(
        enabled=True,
        max_open_positions=50,
        max_open_risk_pct=0.90,
        liq_gate_enabled=False,
        daily_loss_halt_r=99.0,
        consecutive_loss_halt=99,
        consecutive_loss_symbol_halt=99,
        peak_drawdown_halt_pct=0.99,
        strategy_drawdown_halt_r=99.0,
    )
    base.update(overrides)
    return PaperRiskPolicy(**base)


def _engine(policy: PaperRiskPolicy) -> PaperTradeEngine:
    return PaperTradeEngine(starting_equity=1000.0, enabled=True, risk_policy=policy)


@pytest.fixture(autouse=True)
def _major_inputs(monkeypatch):
    """BTC identity with live 24h volume, and no live-price lookups."""
    monkeypatch.setattr(pr, "_mcap_and_group", lambda _s: ("BTC", None, "WAITING"))
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (1e9, "LIVE"))
    monkeypatch.setattr(
        paper_mod, "_live_price_with_source", lambda _s: (None, "unavailable")
    )


def _eval() -> dict:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "entry_price": PLAN["entry"],
        "stop_price": PLAN["stop"],
        "tp1": PLAN["tp1"],
    }


def _open(
    eng: PaperTradeEngine,
    opener: str,
    bar: str,
    *,
    risk: float = 0.01,
    replay: bool = False,
) -> PaperPosition | None:
    if opener == "passed":
        return eng.open_passed_position(
            symbol=SYM,
            combination_id="COMBO_03_TRANSITION_B",
            timeframe="1h",
            direction="LONG",
            eval_result=_eval(),
            risk_percent=risk,
            setup_bar_time_utc=bar,
        )
    if opener == "experimental":
        return eng.open_experimental_position(
            symbol=SYM,
            timeframe="1h",
            eval_result=_eval(),
            risk_percent=min(risk, 0.0025),
            signal_snippet={
                "strategy_id": "COMBO_02_V2_RESEARCH",
                "source": "V2_CANDIDATE_PAPER_WATCHER",
            },
            setup_bar_time_utc=bar,
            replay=replay,
            emit_alert=False,
        )
    return eng.open_v21a_position(
        symbol=SYM,
        timeframe="1h",
        eval_result=_eval(),
        risk_percent=risk,
        signal_snippet={
            "strategy_id": "COMBO_02_V2_1_A",
            "source": "V21A_PAPER_WATCHER",
        },
        setup_bar_time_utc=bar,
        replay=replay,
        emit_alert=False,
    )


def _open_row(i: int) -> dict:
    """An OPEN ledger row on a symbol the tests do not trade."""
    return {
        "id": f"open-{i}",
        "symbol": f"HELD{i}USDT",
        "side": "LONG",
        "status": "OPEN",
        "entry_price": 10.0,
        "stop_price": 9.0,
        "tp1_price": 12.0,
        "quantity": 1.0,
        "risk_usd": 1.0,
        "opened_at": "2026-03-01T00:00:00+00:00",
        "source_candle_ts": "2026-03-01T00:00:00+00:00",
        "timeframe": "1h",
        "signal_snippet": {"strategy_id": "COMBO_02_V1", "source": "V1_PAPER_WATCHER"},
    }


def _closed_row(
    i: int,
    *,
    r: float,
    pnl: float,
    closed_at: datetime,
    symbol: str = "ETHUSDT",
    status: str = "CLOSED",
    book: str = "PASSED",
) -> dict:
    return {
        "id": f"closed-{i}",
        "symbol": symbol,
        "side": "LONG",
        "status": status,
        "entry_price": 10.0,
        "stop_price": 9.0,
        "tp1_price": 12.0,
        "quantity": 1.0,
        "risk_usd": abs(pnl) or 1.0,
        "opened_at": (closed_at - timedelta(hours=1)).isoformat(),
        "closed_at": closed_at.isoformat(),
        "exit_price": 9.0,
        "exit_reason": "STOP" if r < 0 else "TP1",
        "pnl_usd": pnl,
        "r_multiple": r,
        "source_candle_ts": (closed_at - timedelta(hours=1)).isoformat(),
        "timeframe": "1h",
        # Each row feeds its own book's stop counters (owner ruling D27).
        "signal_snippet": _SNIPPETS[book],
    }


_SNIPPETS = {
    "PASSED": {"strategy_id": "COMBO_03_TRANSITION_B", "source": "PASSED_CAP_BOOK",
               "stream": "PASSED|COMBO_03_TRANSITION_B|LONG"},
    "LEGACY": {"strategy_id": "RESEARCH_15M", "source": "LEGACY_SETUP_SIGNAL"},
    "V1": {"strategy_id": "COMBO_02_V1", "source": "V1_PAPER_WATCHER"},
}


def _book(eng: PaperTradeEngine, name: str = "PASSED"):
    """One book's stop counters (owner ruling D27)."""
    return eng._stops(name)


def _legacy_gate(eng: PaperTradeEngine):
    """The entry gate a LEGACY-book open would meet (no open position, no other book)."""
    from app.services.paper_risk import evaluate_paper_entry_risk

    return evaluate_paper_entry_risk(SYM, policy=eng.risk_policy, open_count=0, open_risk_usd=0.0,
                                     equity=eng.equity, **eng._halt_kwargs(SYM, book="LEGACY"))


def _ledger_rows(eng: PaperTradeEngine) -> list[dict]:
    book = eng.positions(closed_limit=1000)
    return json.loads(json.dumps(book["open"] + book["closed"]))


OLD_DAY = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
# Inside every reset window opened by rows on OLD_DAY (book stops clear at the next
# 00:00 UTC, the per-coin stop after 24 h; owner ruling 2026-10-20, D4).
BEFORE_RESET = OLD_DAY + timedelta(hours=10)
AFTER_BOOK_RESET = OLD_DAY.replace(hour=0) + timedelta(days=1)


def _at(eng: PaperTradeEngine, when: datetime) -> PaperTradeEngine:
    eng._now = lambda: when
    return eng


# --- A. the gate runs in every opener ----------------------------------------


@pytest.mark.parametrize("opener", OPENERS)
def test_opener_returns_none_when_book_is_over_the_open_count_cap(opener):
    eng = _engine(_policy(max_open_positions=2))
    eng.hydrate_from_rows([_open_row(i) for i in range(3)])
    assert eng.status()["open_count"] == 3

    assert _open(eng, opener, _bar(0)) is None

    assert eng._last_skip_reason == f"{SYM}:max_open_positions=2"
    assert eng.status()["open_count"] == 3
    assert eng.pending_persist_count() == 0


@pytest.mark.parametrize("opener", OPENERS)
def test_same_open_passes_once_the_book_is_under_the_cap(opener):
    """The cap is what blocked the open above, not the test inputs."""
    eng = _engine(_policy(max_open_positions=4))
    eng.hydrate_from_rows([_open_row(i) for i in range(3)])

    pos = _open(eng, opener, _bar(0))

    assert pos is not None
    assert eng._last_skip_reason is None
    assert eng.status()["open_count"] == 4


@pytest.mark.parametrize("opener", OPENERS)
def test_second_gate_blocks_when_planned_risk_would_exceed_the_book(opener):
    # Book room is 0.2% of 1000 = 2 USD. 1 USD is open; any plan here adds more than 1.
    eng = _engine(_policy(max_open_risk_pct=0.002))
    eng.hydrate_from_rows([_open_row(0)])

    assert _open(eng, opener, _bar(0), risk=0.02) is None

    assert eng._last_skip_reason == f"{SYM}:open_risk_would_exceed=2.00"
    assert eng.status()["open_count"] == 1


@pytest.mark.parametrize("opener", ["experimental", "v21a"])
def test_replay_keeps_the_same_bypass_as_v1(opener):
    """open_v1_combo_position skips the gate under replay; these two match it."""
    eng = _engine(_policy(max_open_positions=2))
    eng.hydrate_from_rows([_open_row(i) for i in range(3)])

    assert _open(eng, opener, _bar(0), replay=True) is not None


def test_non_major_without_market_cap_is_blocked_in_the_passed_book(monkeypatch):
    monkeypatch.setattr(pr, "_mcap_and_group", lambda _s: ("UNKNOWN", None, "WAITING"))
    eng = _engine(_policy())

    assert _open(eng, "passed", _bar(0)) is None

    assert eng._last_skip_reason == f"{SYM}:mcap_unavailable_status=WAITING"


# --- B. a restart keeps the halts the ledger justifies -------------------------


def _lose_until_halted(eng: PaperTradeEngine, *, risk: float = 0.03) -> int:
    """Open and stop out passed-book longs until the engine refuses to open."""
    losers = 0
    for i in range(12):
        # Setup on bar 2i fills on bar 2i+1 and stops out there; the next
        # setup bar (2i+2) is the one after the exit bar.
        eng._now = lambda i=i: BAR0 + timedelta(hours=2 * i + 1, minutes=30)
        pos = _open(eng, "passed", _bar(2 * i), risk=risk)
        if pos is None:
            return losers
        closed = exit_passed_on_bar(eng, SYM, PLAN["stop"], _bar(2 * i + 1))
        assert [p.exit_reason for p in closed] == ["STOP"]
        losers += 1
    raise AssertionError("engine never halted")


def test_drawdown_halt_survives_a_restart_from_the_ledger():
    policy = _policy(peak_drawdown_halt_pct=0.10)
    eng = _engine(policy)

    losers = _lose_until_halted(eng)

    assert losers >= 3
    assert eng._last_skip_reason.startswith(f"{SYM}:peak_drawdown_halt_pct=")
    assert eng.equity <= 900.0

    fresh = _engine(policy)
    fresh._now = eng._now  # restart inside the stop's reset window
    fresh.hydrate_from_rows(_ledger_rows(eng))

    assert _book(fresh).peak_equity == pytest.approx(1000.0)
    assert fresh.equity == pytest.approx(eng.equity)
    next_bar = _bar(2 * losers + 5)
    assert _open(fresh, "passed", next_bar) is None
    assert fresh._last_skip_reason.startswith(f"{SYM}:peak_drawdown_halt_pct=")
    assert fresh.status()["open_count"] == 0
    # The passed book's losses do not halt another book (owner ruling D27).
    other = _engine(policy)
    other._now = eng._now
    other.hydrate_from_rows(_ledger_rows(eng))
    assert _open(other, "v21a", next_bar) is not None
    # At the next 00:00 UTC the stop clears and the peak re-bases to the book's equity.
    midnight = eng._now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    _at(fresh, midnight)
    assert _open(fresh, "passed", next_bar) is not None
    assert _book(fresh).peak_equity == pytest.approx(_book(fresh).equity)


def test_reset_still_clears_a_rebuilt_drawdown_halt():
    policy = _policy(peak_drawdown_halt_pct=0.10)
    eng = _engine(policy)
    losers = _lose_until_halted(eng)
    fresh = _engine(policy)
    fresh._now = eng._now
    fresh.hydrate_from_rows(_ledger_rows(eng))
    assert _open(fresh, "passed", _bar(2 * losers + 5)) is None

    fresh.reset()

    assert fresh.peak_equity == 1000.0
    assert fresh._consecutive_losses == 0
    assert fresh._symbol_consecutive_losses == {}
    assert fresh._day_pnl_r == 0.0
    assert fresh._strategy_equity_r == 0.0
    assert fresh._strategy_peak_r == 0.0
    assert _open(fresh, "passed", _bar(2 * losers + 5)) is not None


def test_peak_is_rebuilt_from_the_running_equity_not_the_final_value():
    # +200 then -150: peak 1200, equity 1050, drawdown 12.5%.
    rows = [
        _closed_row(1, r=2.0, pnl=200.0, closed_at=OLD_DAY),
        _closed_row(2, r=-1.5, pnl=-150.0, closed_at=OLD_DAY + timedelta(hours=2)),
    ]
    eng = _at(_engine(_policy(peak_drawdown_halt_pct=0.10)), BEFORE_RESET)

    # Newest first, as the ledger query returns them.
    eng.hydrate_from_rows(list(reversed(rows)))

    assert eng.equity == pytest.approx(1050.0)
    assert _book(eng).peak_equity == pytest.approx(1200.0)
    assert _open(eng, "passed", _bar(0)) is None
    assert eng._last_skip_reason == f"{SYM}:peak_drawdown_halt_pct=0.1250"


def test_truncated_window_starts_the_equity_walk_at_the_full_ledger_level():
    # Full ledger: +300 (outside the window), then +100, then -200 (in the window).
    # Equity before the window is 1300, peak 1400, equity now 1200: 14.29% down.
    window = [
        _closed_row(2, r=1.0, pnl=100.0, closed_at=OLD_DAY + timedelta(hours=1), book="LEGACY"),
        _closed_row(3, r=-2.0, pnl=-200.0, closed_at=OLD_DAY + timedelta(hours=2), book="LEGACY"),
    ]
    eng = _at(_engine(_policy(peak_drawdown_halt_pct=0.10)), BEFORE_RESET)

    # PnL before the window cannot be split by book; it goes to the LEGACY catch-all (D27).
    eng.hydrate_from_rows(window, closed_pnl_total=200.0)

    assert eng.equity == pytest.approx(1200.0)
    assert eng.peak_equity == pytest.approx(1400.0)
    gate = _legacy_gate(eng)
    assert not gate.ok and gate.reason.startswith("peak_drawdown_halt_pct=0.1429")


def test_truncated_window_never_puts_the_peak_below_starting_equity():
    # 300 was lost before the window. The window only holds a small win.
    window = [_closed_row(9, r=0.5, pnl=10.0, closed_at=OLD_DAY, book="LEGACY")]
    eng = _at(_engine(_policy(peak_drawdown_halt_pct=0.10)), BEFORE_RESET)

    eng.hydrate_from_rows(window, closed_pnl_total=-290.0)

    assert eng.equity == pytest.approx(710.0)
    assert eng.peak_equity == pytest.approx(1000.0)
    gate = _legacy_gate(eng)
    assert not gate.ok and gate.reason == "peak_drawdown_halt_pct=0.2900"


def test_loss_streak_survives_a_restart_and_a_later_win_ends_it():
    losses = [
        _closed_row(i, r=-1.0, pnl=-5.0, closed_at=OLD_DAY + timedelta(hours=i))
        for i in range(3)
    ]
    halted = _at(_engine(_policy(consecutive_loss_halt=3)), BEFORE_RESET)
    halted.hydrate_from_rows(list(reversed(losses)))

    assert _book(halted).consecutive_losses == 3
    assert _open(halted, "passed", _bar(0)) is None
    assert halted._last_skip_reason == f"{SYM}:consecutive_loss_halt=3"

    # The streak clears at the next 00:00 UTC even without a win.
    late = _at(_engine(_policy(consecutive_loss_halt=3)), AFTER_BOOK_RESET)
    late.hydrate_from_rows(list(reversed(losses)))
    assert _book(late).consecutive_losses == 0

    win = _closed_row(7, r=2.0, pnl=10.0, closed_at=OLD_DAY + timedelta(hours=7))
    cleared = _at(_engine(_policy(consecutive_loss_halt=3)), BEFORE_RESET)
    cleared.hydrate_from_rows([win, *reversed(losses)])

    assert _book(cleared).consecutive_losses == 0
    assert _open(cleared, "passed", _bar(0)) is not None


def test_symbol_loss_streak_survives_a_restart_for_that_symbol_only():
    rows = [
        _closed_row(
            i, r=-1.0, pnl=-5.0, closed_at=OLD_DAY + timedelta(hours=i), symbol=SYM
        )
        for i in range(3)
    ]
    # A win on another symbol ends the book streak but not BTC's own.
    rows.append(_closed_row(8, r=1.0, pnl=5.0, closed_at=OLD_DAY + timedelta(hours=8)))
    eng = _at(_engine(_policy(consecutive_loss_symbol_halt=3)), BEFORE_RESET)

    eng.hydrate_from_rows(rows)

    assert _book(eng).consecutive_losses == 0
    assert _book(eng).symbol_consecutive_losses == {SYM: 3, "ETHUSDT": 0}
    assert _open(eng, "passed", _bar(0)) is None
    assert eng._last_skip_reason == f"{SYM}:symbol_consecutive_loss_halt=3"

    # The coin is paused for 24 h from the losing close that tripped it (OLD_DAY + 2 h).
    _at(eng, OLD_DAY + timedelta(hours=2 + 24))
    assert _open(eng, "passed", _bar(0)) is not None


def test_daily_loss_halt_survives_a_restart_on_the_same_utc_day():
    now = datetime.now(timezone.utc)
    today = [
        _closed_row(i, r=-1.2, pnl=-5.0, closed_at=now - timedelta(seconds=i))
        for i in range(3)
    ]
    if any(datetime.fromisoformat(r["closed_at"]).date() != now.date() for r in today):
        pytest.skip("run straddles UTC midnight")
    eng = _engine(_policy(daily_loss_halt_r=3.0))

    eng.hydrate_from_rows(today)

    assert _book(eng).day_key == now.strftime("%Y-%m-%d")
    assert _book(eng).day_pnl_r == pytest.approx(-3.6)
    assert _open(eng, "passed", _bar(0)) is None
    assert eng._last_skip_reason == f"{SYM}:daily_loss_halt_r=-3.60"


def test_losses_closed_on_an_earlier_utc_day_do_not_halt_today():
    rows = [
        _closed_row(i, r=-1.2, pnl=-5.0, closed_at=OLD_DAY + timedelta(hours=i))
        for i in range(3)
    ]
    eng = _engine(_policy(daily_loss_halt_r=3.0))

    eng.hydrate_from_rows(rows)

    assert eng._day_pnl_r == 0.0
    assert _open(eng, "passed", _bar(0)) is not None


def test_strategy_drawdown_survives_a_restart():
    # +4R then seven -1R: 7R below the R peak.
    rows = [_closed_row(0, r=4.0, pnl=4.0, closed_at=OLD_DAY)]
    rows += [
        _closed_row(i, r=-1.0, pnl=-1.0, closed_at=OLD_DAY + timedelta(hours=i))
        for i in range(1, 8)
    ]
    eng = _at(_engine(_policy(strategy_drawdown_halt_r=6.0)), BEFORE_RESET)

    eng.hydrate_from_rows(list(reversed(rows)))

    assert _book(eng).strategy_peak_r == pytest.approx(4.0)
    assert _book(eng).strategy_equity_r == pytest.approx(-3.0)
    assert _open(eng, "passed", _bar(0)) is None
    assert eng._last_skip_reason == f"{SYM}:strategy_drawdown_halt_r=-7.00"


def test_cancelled_rows_do_not_feed_the_halts():
    rows = [
        _closed_row(
            i,
            r=-1.0,
            pnl=-50.0,
            closed_at=OLD_DAY + timedelta(hours=i),
            status="CANCELLED",
        )
        for i in range(4)
    ]
    eng = _engine(_policy(consecutive_loss_halt=3, peak_drawdown_halt_pct=0.10))

    eng.hydrate_from_rows(rows)

    assert eng._consecutive_losses == 0
    assert eng.peak_equity == 1000.0
    assert eng.equity == 1000.0
    assert _open(eng, "passed", _bar(0)) is not None


def test_rebuilt_trackers_match_the_engine_that_made_the_ledger():
    policy = _policy()
    eng = _engine(policy)
    for i, exit_px in enumerate([PLAN["tp1"], PLAN["stop"], PLAN["stop"]]):
        eng._now = lambda i=i: datetime.now(timezone.utc) + timedelta(microseconds=i)
        assert _open(eng, "passed", _bar(i * 2)) is not None
        assert len(exit_passed_on_bar(eng, SYM, exit_px, _bar(i * 2 + 1))) == 1
        # Later setup bars sit before the wall-clock exit bar; clear the re-entry memory.
        eng._last_exit_bar_open.clear()

    fresh = _engine(policy)
    fresh.hydrate_from_rows(_ledger_rows(eng))

    a, b = _book(fresh), _book(eng)
    assert a.peak_equity == pytest.approx(b.peak_equity)
    assert a.consecutive_losses == b.consecutive_losses == 2
    assert a.symbol_consecutive_losses == b.symbol_consecutive_losses
    assert a.strategy_equity_r == pytest.approx(b.strategy_equity_r)
    assert a.strategy_peak_r == pytest.approx(b.strategy_peak_r)
    if a.day_key == b.day_key:
        assert a.day_pnl_r == pytest.approx(b.day_pnl_r)
