"""The trade-chances note names the v1 watcher by its book list.

Everything the route touches is stubbed: no orchestrator (so the warm loop cannot
run), a stub paper engine and an empty opportunities list. Nothing is written.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.api import routes
from app.services import paper_trade as paper_mod
from app.services import setup_signals as setup_mod


class _StubEngine:
    enabled = True

    def __init__(self, *, owns: bool, legacy: bool, entry_mode: str) -> None:
        self.v1_watcher_owns_entries = owns
        self.legacy_auto_entry_enabled = legacy
        self.entry_mode = entry_mode

    def open_symbols(self, stream: str | None = None) -> set[str]:
        assert stream is None, "already_open must cover every stream"
        return set()

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"paper engine touched: {name}")


def _note(monkeypatch, *, owns: bool, legacy: bool, entry_mode: str) -> dict[str, Any]:
    engine = _StubEngine(owns=owns, legacy=legacy, entry_mode=entry_mode)
    config = SimpleNamespace(mtf_major="4h", mtf_primary="1h", mtf_setup="15m", mtf_entry="5m")

    def _no_warm(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("warm loop ran")

    monkeypatch.setattr(paper_mod, "get_paper_trade_engine", lambda: engine)
    monkeypatch.setattr(paper_mod, "list_trade_opportunities", lambda **_kw: [])
    # Trade chances reads the display list (owner D199). The note tests still force an empty book.
    monkeypatch.setattr(
        "app.services.screener_display.list_display_opportunities",
        lambda **_kw: [],
    )
    monkeypatch.setattr(
        setup_mod,
        "get_setup_signal_service",
        lambda: SimpleNamespace(config=config, ensure_computed=_no_warm),
    )
    monkeypatch.setattr(routes, "get_orchestrator", lambda: None)
    payload = asyncio.run(routes.paper_opportunities(limit=100, warm=0))
    assert payload["warmed"] == 0
    assert payload["rows"] == []
    return payload


def test_watcher_owned_note_names_the_book_list_not_three_symbols(monkeypatch):
    payload = _note(monkeypatch, owns=True, legacy=False, entry_mode="path_a")
    note = payload["note"]

    assert payload["v1_watcher_owns_entries"] is True
    assert payload["legacy_auto_entry_enabled"] is False
    assert "BTC/ETH/SOL" not in note
    assert "its own book list" in note
    assert note == (
        "15m screener research only — auto paper opens come from the "
        "COMBO_02 v1 1h watcher (its own book list, shown in the book table), "
        "not these READY rows."
    )


def test_legacy_off_note_says_ready_rows_do_not_open(monkeypatch):
    payload = _note(monkeypatch, owns=False, legacy=False, entry_mode="path_a")
    note = payload["note"]

    assert payload["legacy_auto_entry_enabled"] is False
    assert "opens when Auto ON" not in note
    assert "legacy auto-entry is off" in note
    assert "15m RESEARCH_15M" in note


@pytest.mark.parametrize(
    ("owns", "legacy"),
    [(False, True), (True, True)],
)
def test_path_a_note_is_unchanged(monkeypatch, owns: bool, legacy: bool):
    payload = _note(monkeypatch, owns=owns, legacy=legacy, entry_mode="path_a")
    assert payload["note"] == (
        "Auto mode=path_a. READY = Path A Trend+BOS opens when Auto ON"
    )


def test_default_note_is_unchanged(monkeypatch):
    payload = _note(monkeypatch, owns=False, legacy=True, entry_mode="path_b")
    assert payload["note"] == (
        "READY = LONG_ENTRY_CANDIDATE. WAITING = OHLCV/setup not ready."
    )
