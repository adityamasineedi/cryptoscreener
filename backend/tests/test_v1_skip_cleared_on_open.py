"""A book's last_skip is display state: it clears when that book opens, not before.

The paper engine here is a stub. Nothing touches the live book.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.v1_paper_watcher import (
    V1PaperWatcher,
    is_v1_long_entry,
    reset_v1_paper_watcher_for_tests,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

ENTRY = {
    "status": "LONG_ENTRY_CANDIDATE",
    "direction": "LONG",
    "combination_id": "COMBO_02",
    "gates": {"bos": True, "trend": True, "htf": True, "hl_intact": True},
    "required": ["bos", "trend", "hl_intact", "htf"],
    "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
    "entry_price": 100.0,
    "stop_price": 98.0,
    "tp1": 104.0,
}


class _StubPaper:
    """Returns whatever the test queued for the next v1 open. Never opens anything real."""

    def __init__(self) -> None:
        self.results: list[Any] = []
        self.bars: list[str] = []

    def open_v1_combo_position(self, **kwargs: Any) -> Any:
        self.bars.append(kwargs["setup_bar_time_utc"])
        return self.results.pop(0)


@pytest.fixture(autouse=True)
def _reset_watcher():
    reset_v1_paper_watcher_for_tests()
    yield
    reset_v1_paper_watcher_for_tests()


def _bars(n: int, *, hours: int) -> list[dict[str, Any]]:
    return [
        {"time": T0 + timedelta(hours=hours * i), "open": 100.0, "high": 101.0, "low": 99.0,
         "close": 100.0, "volume": 1000.0}
        for i in range(n)
    ]


def _rows(watcher: V1PaperWatcher) -> dict[str, dict[str, Any]]:
    return {row["symbol"]: row for row in watcher.peek_books()}


def _watcher(paper: _StubPaper, monkeypatch) -> V1PaperWatcher:
    """Live-mode watcher whose gates always say "enter"; watermark and open path are real."""
    assert is_v1_long_entry(ENTRY)
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=False, emit_alerts=False)
    monkeypatch.setattr(watcher, "evaluate_at_bar", lambda **_kw: dict(ENTRY))
    monkeypatch.setattr(watcher, "_bos_break_allows_entry", lambda *_a, **_kw: True)
    # Controlled clock: the 2024 bars under test are inside the live open-lag bound.
    monkeypatch.setattr(
        "app.services.v1_paper_watcher._utc_now", lambda: T0 + timedelta(hours=81)
    )
    return watcher


def test_skip_stays_until_the_book_opens_then_only_that_book_clears(monkeypatch):
    paper = _StubPaper()
    watcher = _watcher(paper, monkeypatch)
    h4 = _bars(40, hours=4)
    btc = _bars(82, hours=1)
    bar_a = btc[79]["time"].isoformat()
    bar_b = btc[80]["time"].isoformat()
    bar_c = btc[81]["time"].isoformat()
    eth_thin = _bars(8, hours=1)
    eth_bar = eth_thin[-1]["time"].isoformat()

    # Bar A: first sight of a stale tip seeds the watermark and skips.
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc[:80], candles_4h=h4) is None
    # ETH skips on its own bar, after BTC.
    assert watcher.on_closed_1h("ETHUSDT", candles_1h=eth_thin, candles_4h=[]) is None
    rows = _rows(watcher)
    assert rows["BTCUSDT"]["last_skip"] == "BTCUSDT:seeded_no_backlog"
    assert rows["BTCUSDT"]["last_skip_bar"] == bar_a
    assert rows["ETHUSDT"]["last_skip"] == "ETHUSDT:ohlcv_catchup"
    assert rows["ETHUSDT"]["last_skip_bar"] == eth_bar
    assert paper.bars == []

    # Bar B: the gates pass but the paper engine refuses. The skip stays, now for bar B.
    paper.results.append(None)
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc[:81], candles_4h=h4) is None
    rows = _rows(watcher)
    assert rows["BTCUSDT"]["last_skip"] == "BTCUSDT:paper_open_skipped"
    assert rows["BTCUSDT"]["last_skip_bar"] == bar_b
    assert watcher._last_skip == "BTCUSDT:paper_open_skipped"

    # Bar C: the open returns a position. BTC's row clears; ETH's is untouched.
    position = SimpleNamespace(symbol="BTCUSDT", status="OPEN")
    paper.results.append(position)
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc[:82], candles_4h=h4) is position
    assert paper.bars == [bar_b, bar_c]
    rows = _rows(watcher)
    assert rows["BTCUSDT"]["last_skip"] is None
    assert rows["BTCUSDT"]["last_skip_bar"] is None
    assert rows["ETHUSDT"]["last_skip"] == "ETHUSDT:ohlcv_catchup"
    assert rows["ETHUSDT"]["last_skip_bar"] == eth_bar

    status = watcher.status()
    assert "BTCUSDT" not in status["last_skip_by_symbol"]
    assert status["last_skip_by_symbol"]["ETHUSDT"] == {
        "reason": "ETHUSDT:ohlcv_catchup",
        "bar": eth_bar,
    }
    # The watcher-wide slot is left as it was.
    assert watcher._last_skip == "BTCUSDT:paper_open_skipped"
    assert status["last_skip"] == "BTCUSDT:paper_open_skipped"


def test_open_does_not_change_which_bar_is_watermarked_or_processed(monkeypatch):
    paper = _StubPaper()
    watcher = _watcher(paper, monkeypatch)
    h4 = _bars(40, hours=4)
    btc = _bars(81, hours=1)
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc[:80], candles_4h=h4) is None
    paper.results.append(SimpleNamespace(symbol="BTCUSDT", status="OPEN"))
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc, candles_4h=h4) is not None

    opened_bar = btc[80]["time"].isoformat()
    assert watcher._watermarks["BTCUSDT"] == opened_bar
    assert len(watcher._processed_bars) == 2
    # The same bar again is refused before the paper engine is asked, and that is a new skip.
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc, candles_4h=h4) is None
    assert paper.bars == [opened_bar]
    row = _rows(watcher)["BTCUSDT"]
    assert row["last_skip"] == "BTCUSDT:already_processed"
    assert row["last_skip_bar"] == opened_bar


def test_cleared_skip_is_not_brought_back_by_a_recreated_watcher(monkeypatch):
    paper = _StubPaper()
    watcher = _watcher(paper, monkeypatch)
    h4 = _bars(40, hours=4)
    btc = _bars(81, hours=1)
    eth_thin = _bars(8, hours=1)
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc[:80], candles_4h=h4) is None
    assert watcher.on_closed_1h("ETHUSDT", candles_1h=eth_thin, candles_4h=[]) is None
    paper.results.append(SimpleNamespace(symbol="BTCUSDT", status="OPEN"))
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc, candles_4h=h4) is not None

    again = _rows(V1PaperWatcher(paper_engine=_StubPaper(), emit_alerts=False))
    assert again["BTCUSDT"]["last_skip"] is None
    assert again["ETHUSDT"]["last_skip"] == "ETHUSDT:ohlcv_catchup"
