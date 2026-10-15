"""Copy in the paper status "monitor" block: the v1 watcher is named by its book list.

Uses a stub watcher and stub settings. Nothing touches the live book.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.api import routes
from app.services import v1_paper_watcher as watcher_mod


class _StubWatcher:
    def status(self, include_live: bool = True) -> dict[str, Any]:
        return {"enabled": True, "books": [], "last_skip": None}

    def refresh_presentation_snapshots(self, force: bool = False) -> None:
        return None

    def peek_books(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture(autouse=True)
def _watcher_enabled(monkeypatch):
    settings = SimpleNamespace(paper_v1_watcher_enabled=True)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(watcher_mod, "get_v1_paper_watcher", lambda: _StubWatcher())
    monkeypatch.setitem(routes._WATCHER_LIVE_CACHE, "rows", None)
    monkeypatch.setitem(routes._WATCHER_LIVE_CACHE, "at", 0.0)


def _monitor(*, legacy: bool, owns: bool) -> dict[str, Any]:
    status = {"legacy_auto_entry_enabled": legacy, "v1_watcher_owns_entries": owns}
    return routes._enrich_paper_status(status)["monitor"]


def test_parallel_streams_copy_names_the_book_list_not_three_symbols():
    monitor = _monitor(legacy=True, owns=False)
    text = monitor["explanation"]

    assert monitor["auto_source"] == "V1_PAPER_WATCHER+LEGACY_15M"
    assert monitor["v1_watcher_enabled"] is True
    assert monitor["legacy_auto_entry_enabled"] is True
    assert monitor["v1_watcher_owns_entries"] is False
    assert "BTC/ETH/SOL" not in text
    assert "one open position per symbol" not in " ".join(text.lower().split())
    assert "per symbol" not in text.lower()
    assert "its own book list" in text
    assert "COMBO_02 v1 1h watcher" in text
    assert "15m RESEARCH_15M Trade chances" in text


def test_watcher_owns_entries_copy_names_the_book_list_not_three_symbols():
    monitor = _monitor(legacy=False, owns=True)
    text = monitor["explanation"]

    assert monitor["auto_source"] == "V1_PAPER_WATCHER"
    assert monitor["v1_watcher_enabled"] is True
    assert monitor["v1_watcher_owns_entries"] is True
    assert "BTC/ETH/SOL" not in text
    assert "its own book list" in text
    assert "Auto paper opens only from the COMBO_02 v1 1h watcher" in text
    assert "PAPER_V1_WATCHER_OWNS_ENTRIES=true" in text


def test_ownership_wins_over_legacy_when_both_are_on():
    # Unchanged rule: with ownership on, the legacy flag does not make the streams parallel.
    monitor = _monitor(legacy=True, owns=True)
    assert monitor["auto_source"] == "V1_PAPER_WATCHER"
    assert "BTC/ETH/SOL" not in monitor["explanation"]
