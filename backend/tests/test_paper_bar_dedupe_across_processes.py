"""One closed bar cannot book the same trade in two processes.

No database. Two in-memory engines stand in for two processes: the one that
does not hold the writer lock cannot open, and after the lock moves the second
process still refuses the bar the first process already booked.
"""

from __future__ import annotations

from types import SimpleNamespace

import app.services.paper_trade as paper_mod
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine, ledger_dedupe_key

SYM = "BTCUSDT"
BAR = "2026-10-12T10:00:00+00:00"
LONG = {"entry": 60000.0, "stop": 59000.0, "tp1": 62000.0}


def _engine() -> PaperTradeEngine:
    eng = PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        entry_mode="path_b",
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    eng.legacy_auto_entry_enabled = True
    return eng


def _open(eng: PaperTradeEngine):
    return eng.open_v1_combo_position(
        symbol=SYM,
        timeframe="1h",
        book=SimpleNamespace(tier="core", risk_percent=0.02),
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "entry_price": LONG["entry"],
            "stop_price": LONG["stop"],
            "tp1": LONG["tp1"],
            "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
        },
        setup_bar_time_utc=BAR,
        emit_alert=False,
    )


def test_one_closed_bar_cannot_book_the_same_trade_twice_across_processes(monkeypatch):
    monkeypatch.setattr(paper_mod, "_live_price_with_source", lambda _s: (None, "unavailable"))
    first = _engine()
    second = _engine()
    second.writer_lock_held = False

    opened = _open(first)
    assert opened is not None
    key = ledger_dedupe_key(SYM, BAR, "V1", "1h")
    assert key in first._opened_keys

    assert _open(second) is None
    assert second._last_skip_reason == f"{SYM}:paper_writer_lock_not_held"
    assert second.status()["open_count"] == 0

    second.writer_lock_held = True
    second.remember_dedupe_keys([key])
    assert _open(second) is None
    assert second.status()["open_count"] == 0
    assert first.status()["open_count"] == 1
