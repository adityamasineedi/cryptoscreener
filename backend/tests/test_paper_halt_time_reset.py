"""Latching safety stops reset by time (owner ruling 2026-10-20, research audit D4)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine

T0 = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def _engine(tmp_path, now):
    # Live values (.env): 5 losses book-wide, 3 per coin, 10% peak drawdown, 6R strategy drawdown.
    eng = PaperTradeEngine(starting_equity=1000.0, enabled=True, risk_policy=PaperRiskPolicy(consecutive_loss_halt=5))
    clock = {"now": now}
    eng._now = lambda: clock["now"]
    eng.halt_journal_path = tmp_path / "halt_journal.jsonl"
    return eng, clock


def _journal(tmp_path):
    p = tmp_path / "halt_journal.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_loss_streak_trips_and_resets_at_the_next_utc_midnight(tmp_path) -> None:
    eng, clock = _engine(tmp_path, T0)
    for n in range(5):
        eng._note_close_for_halts(symbol=f"C{n}USDT", r_multiple=-1.0)
    assert "consecutive_losses" in eng.halt_state()
    assert eng._halt_kwargs("XUSDT")["consecutive_losses"] == 5
    clock["now"] = T0.replace(hour=23, minute=59)
    assert eng._halt_kwargs("XUSDT")["consecutive_losses"] == 5
    clock["now"] = T0.replace(hour=0, minute=0) + timedelta(days=1)
    assert eng._halt_kwargs("XUSDT")["consecutive_losses"] == 0
    assert "consecutive_losses" not in eng.halt_state()
    events = [(e["event"], e["halt"]) for e in _journal(tmp_path)]
    assert ("TRIP", "consecutive_losses") in events and ("RESET", "consecutive_losses") in events


def test_per_coin_stop_pauses_that_coin_for_24_hours(tmp_path) -> None:
    eng, clock = _engine(tmp_path, T0)
    for _ in range(3):
        eng._note_close_for_halts(symbol="AVAXUSDT", r_multiple=-1.0)
    assert eng._halt_kwargs("AVAXUSDT")["symbol_consecutive_losses"] == 3
    clock["now"] = T0 + timedelta(hours=23, minutes=59)
    assert eng._halt_kwargs("AVAXUSDT")["symbol_consecutive_losses"] == 3
    clock["now"] = T0 + timedelta(hours=24)
    assert eng._halt_kwargs("AVAXUSDT")["symbol_consecutive_losses"] == 0
    events = [(e["event"], e["halt"]) for e in _journal(tmp_path)]
    assert events == [("TRIP", "symbol:AVAXUSDT"), ("RESET", "symbol:AVAXUSDT")]


def test_peak_drawdown_rebases_to_current_equity_at_midnight(tmp_path) -> None:
    eng, clock = _engine(tmp_path, T0)
    # A loss that leaves the book 12% below its 1,000 peak (each book has its own curve, D27).
    eng._note_close_for_halts(symbol="BTCUSDT", r_multiple=-1.0, pnl_usd=-120.0)
    assert "peak_drawdown" in eng.halt_state()
    clock["now"] = T0.replace(hour=0, minute=0) + timedelta(days=1)
    kw = eng._halt_kwargs("BTCUSDT")
    assert kw["peak_equity"] == kw["drawdown_equity"] == 880.0
    assert "peak_drawdown" not in eng.halt_state()


def test_strategy_drawdown_rebases_at_midnight(tmp_path) -> None:
    eng, clock = _engine(tmp_path, T0)
    eng._note_close_for_halts(symbol="AUSDT", r_multiple=2.0)
    eng._note_close_for_halts(symbol="BUSDT", r_multiple=-8.5)  # -8.5R from a +2R peak
    assert eng._halt_kwargs("XUSDT")["strategy_drawdown_r"] <= -6.0
    clock["now"] = T0.replace(hour=0, minute=0) + timedelta(days=1)
    assert eng._halt_kwargs("XUSDT")["strategy_drawdown_r"] == 0.0


def test_restart_rebuild_applies_the_same_rule_without_journal_lines(tmp_path) -> None:
    from app.services.paper_trade import PaperPosition

    eng, clock = _engine(tmp_path, T0 + timedelta(days=2))
    rows = []
    for n in range(5):
        p = PaperPosition(id=f"p{n}", symbol=f"C{n}USDT", side="SHORT", status="CLOSED", entry_price=100.0,
                          stop_price=101.0, tp1_price=98.0, quantity=1.0, risk_usd=20.0,
                          opened_at=(T0 + timedelta(hours=n)).isoformat())
        p.closed_at = (T0 + timedelta(hours=n, minutes=30)).isoformat()
        p.r_multiple = -1.0
        p.pnl_usd = -20.0
        rows.append(p)
    eng._rebuild_halts_locked(rows, -100.0)
    # The streak tripped on 10-21 and its reset (10-22 00:00) has passed by 10-23.
    assert eng._consecutive_losses == 0
    assert eng.halt_state() == {}
    assert _journal(tmp_path) == []


def test_rebuild_keeps_a_stop_whose_reset_is_still_ahead(tmp_path) -> None:
    from app.services.paper_trade import PaperPosition

    eng, clock = _engine(tmp_path, T0 + timedelta(hours=6))
    rows = []
    for n in range(3):
        p = PaperPosition(id=f"q{n}", symbol="NEARUSDT", side="SHORT", status="CLOSED", entry_price=100.0,
                          stop_price=101.0, tp1_price=98.0, quantity=1.0, risk_usd=20.0,
                          opened_at=(T0 + timedelta(hours=n)).isoformat())
        p.closed_at = (T0 + timedelta(hours=n, minutes=30)).isoformat()
        p.r_multiple = -1.0
        p.pnl_usd = -20.0
        rows.append(p)
    eng._rebuild_halts_locked(rows, -60.0)
    assert eng._symbol_consecutive_losses["NEARUSDT"] == 3
    assert "symbol:NEARUSDT" in eng.halt_state()
