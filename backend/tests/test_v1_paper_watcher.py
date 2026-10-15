"""COMBO_02 v1 paper watcher — closed-bar gating, universe, dedupe, negatives."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.v1_production import (
    EXTENDED_PAPER_SYMBOLS,
    FROZEN_V1_SYMBOLS,
    V1_SYMBOLS,
    enabled_v1_books,
)
from app.services.paper_trade import PaperTradeEngine, PATH_A, PATH_B
from app.services.v1_paper_watcher import (
    V1PaperWatcher,
    is_v1_long_entry,
    reset_v1_paper_watcher_for_tests,
)
from app.signals.config import SignalConfig

pytestmark = pytest.mark.v1_freeze


def _ts(minutes: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _candle(t: datetime, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _trending_series(
    *,
    n: int,
    minutes_per_bar: int,
    start_price: float,
    drift: float,
    wave: float = 3.0,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    price = start_price
    bull = drift >= 0
    step = min(abs(drift) if abs(drift) > 1e-9 else 0.5, wave * 0.25)
    for i in range(n):
        t = _ts(i * minutes_per_bar)
        phase = i % 8
        if phase == 0:
            o = c = price
            if bull:
                h, l = price + 0.2, price - wave
            else:
                h, l = price + wave, price - 0.2
        elif phase == 4:
            o = c = price
            if bull:
                h, l = price + wave, price - 0.2
            else:
                h, l = price + 0.2, price - wave
        else:
            o = price
            c = price + (0.25 if bull else -0.25)
            h = max(o, c) + 0.3
            l = min(o, c) - 0.3
        out.append(_candle(t, o, h, l, c, v=2000.0))
        price = price + (step if bull else -step)
    return out


def _htf_ending_with_setup(
    setup: list[dict[str, Any]],
    *,
    n: int,
    minutes_per_bar: int,
    start_price: float,
    drift: float,
) -> list[dict[str, Any]]:
    if not setup:
        return []
    end = setup[-1]["time"]
    start = end - timedelta(minutes=minutes_per_bar * (n - 1))
    pre = _trending_series(
        n=n,
        minutes_per_bar=minutes_per_bar,
        start_price=start_price,
        drift=drift,
    )
    # Align last bar time to setup tip
    shift = end - pre[-1]["time"]
    out = []
    for c in pre:
        d = dict(c)
        d["time"] = c["time"] + shift
        out.append(d)
    return out


@pytest.fixture(autouse=True)
def _reset_watcher():
    reset_v1_paper_watcher_for_tests()
    yield
    reset_v1_paper_watcher_for_tests()


def test_enabled_v1_books_are_1h_frozen_trio_and_extended_majors():
    books = enabled_v1_books(secondary_enabled=True, timeframes={"1h"})
    assert {b.timeframe for b in books} == {"1h"}
    symbols = {b.symbol for b in books}
    # The frozen claim set is unchanged and still watched.
    assert FROZEN_V1_SYMBOLS == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert FROZEN_V1_SYMBOLS <= symbols
    # Extended majors are books too. Nothing else is.
    assert symbols - FROZEN_V1_SYMBOLS == EXTENDED_PAPER_SYMBOLS
    assert symbols == V1_SYMBOLS
    core = enabled_v1_books(secondary_enabled=False, timeframes={"1h"})
    assert [b.symbol for b in core] == ["BTCUSDT"]


def test_watcher_rejects_outside_universe():
    paper = PaperTradeEngine(enabled=True, v1_profile_enabled=True)
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=True, emit_alerts=False)
    assert "PEPEUSDT" not in V1_SYMBOLS
    assert watcher.book_for("PEPEUSDT") is None
    assert watcher.on_closed_1h("PEPEUSDT", candles_1h=[], candles_4h=[]) is None
    # An extended major is a book.
    assert watcher.book_for("DOGEUSDT") is not None


def test_watcher_dedupes_same_closed_bar():
    h1 = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0, wave=4.0)
    h4 = _htf_ending_with_setup(
        h1, n=80, minutes_per_bar=240, start_price=90.0, drift=2.0
    )
    paper = PaperTradeEngine(enabled=True, risk_policy=PaperTradeEngine().risk_policy)
    paper.risk_policy.enabled = False
    watcher = V1PaperWatcher(
        paper_engine=paper,
        replay_mode=True,
        emit_alerts=False,
        secondary_enabled=True,
    )
    # Force process tip twice — second must no-op via processed set
    a = watcher.on_closed_1h("BTCUSDT", candles_1h=h1, candles_4h=h4, force=True)
    b = watcher.on_closed_1h("BTCUSDT", candles_1h=h1, candles_4h=h4, force=False)
    # Regardless of whether tip was an entry, second call must not open again
    assert b is None
    if a is not None:
        assert a.timeframe == "1h"
        assert a.signal_snippet.get("combo_id") == "COMBO_02"
        assert a.signal_snippet.get("path") == "A"


def test_fail_closed_missing_4h():
    h1 = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0)
    paper = PaperTradeEngine(enabled=True)
    paper.risk_policy.enabled = False
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=True, emit_alerts=False)
    result = watcher.evaluate_at_bar(
        symbol="BTCUSDT", candles_1h=h1, candles_4h=[], as_of_index=len(h1) - 1
    )
    assert result.get("status") == "NO_SETUP"
    assert not is_v1_long_entry(result)


def test_path_a_15m_blocked_when_watcher_owns_entries():
    paper = PaperTradeEngine(enabled=True, entry_mode=PATH_A, v1_profile_enabled=True)
    paper.legacy_auto_entry_enabled = True
    paper.v1_watcher_owns_entries = True
    paper.risk_policy.enabled = False
    payload = {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "timeframe": "15m",
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 100},
        "trend": {"trend": "BULLISH"},
        "mtf": {"MTF_ALIGNMENT": "HTF_ALIGNED"},
        "entry": {"entry_price": 100},
        "stop": {"final_stop": 98},
        "targets": [{"target_price": 104}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "ohlcv_freshness": "FRESH",
    }
    assert paper.on_setup_signal("BTCUSDT", payload) is None
    assert "v1_watcher_owns_path_a" in (paper._last_skip_reason or "")


def test_path_a_15m_opens_in_parallel_with_v1_on_same_symbol(monkeypatch):
    import app.services.paper_trade as paper_mod
    from app.research.v1_production import V1Book

    monkeypatch.setattr(
        paper_mod, "_live_price_with_source", lambda _s: (100.0, "mark_price")
    )
    paper = PaperTradeEngine(enabled=True, entry_mode=PATH_A, v1_profile_enabled=True)
    paper.legacy_auto_entry_enabled = True
    paper.v1_watcher_owns_entries = False
    paper.risk_policy.enabled = False
    payload = {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "timeframe": "15m",
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 100},
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": "BULLISH"},
            "4h": {"trend": "BULLISH"},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
        "entry": {"entry_price": 100},
        "stop": {"final_stop": 98, "provisional": False, "reason": "structural pullback low"},
        "targets": [{"target_price": 104, "target_type": "previous_swing_high"}],
        "impulse": {"is_impulse": True},
        "pullback": {"pullback_state": "CONFIRMED"},
        "risk_reward": {"RISK_REWARD": "PASS"},
        "ohlcv_freshness": "OK",
    }
    legacy = paper.on_setup_signal("BTCUSDT", payload)
    assert legacy is not None
    assert legacy.signal_snippet.get("source") == "LEGACY_SETUP_SIGNAL"
    assert legacy.signal_snippet.get("strategy_id") == "RESEARCH_15M"

    v1 = paper.open_v1_combo_position(
        symbol="BTCUSDT",
        timeframe="1h",
        book=V1Book("BTCUSDT", "1h", "core", 0.02, True),
        eval_result={
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 104.0,
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
            },
            "gates": {"hl_intact": True},
        },
        setup_bar_time_utc="2026-10-02T15:00:00+00:00",
        emit_alert=False,
    )
    assert v1 is not None
    assert v1.signal_snippet["price_source"] == "mark_price"
    assert v1.entry_price == pytest.approx(100.0)
    assert paper.has_open("BTCUSDT", "LEGACY")
    assert paper.has_open("BTCUSDT", "V1")
    assert paper.status()["open_count"] == 2


def test_path_b_cannot_label_combo02_v1():
    paper = PaperTradeEngine(enabled=True, entry_mode=PATH_B, v1_profile_enabled=True)
    paper.v1_watcher_owns_entries = True
    paper.risk_policy.enabled = False
    # Even if Path B somehow opened, snippet must not be COMBO_02 v1 —
    # Path B path_label is PATH_B / experimental.
    # Direct check: open_v1 is the only COMBO_02 v1 open path with path=A.
    from app.services.telegram_alerts import is_v1_paper_alert

    fake = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "payload": {
            "id": "x",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "signal_snippet": {
                "combo_id": "COMBO_02",
                "combo_version": "v1-combo02-long-htf",
                "path": "PATH_B",
            },
        },
    }
    assert not is_v1_paper_alert(fake)
    fake15 = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "payload": {
            "id": "y",
            "timeframe": "15m",
            "signal_snippet": {
                "combo_id": "COMBO_02",
                "combo_version": "v1-combo02-long-htf",
                "path": "A",
            },
        },
    }
    assert not is_v1_paper_alert(fake15)


def test_live_seed_does_not_open_historical_tip():
    h1 = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0)
    h4 = _htf_ending_with_setup(
        h1, n=80, minutes_per_bar=240, start_price=90.0, drift=2.0
    )
    paper = PaperTradeEngine(enabled=True)
    paper.risk_policy.enabled = False
    watcher = V1PaperWatcher(
        paper_engine=paper,
        replay_mode=False,
        emit_alerts=False,
    )
    # First sighting seeds watermark — no open
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=h1, candles_4h=h4) is None
    assert "BTCUSDT" in watcher._seeded


def test_thin_history_does_not_burn_the_live_bar():
    thin = _trending_series(n=8, minutes_per_bar=60, start_price=100.0, drift=1.0)
    paper = PaperTradeEngine(enabled=True)
    watcher = V1PaperWatcher(
        paper_engine=paper,
        replay_mode=False,
        emit_alerts=False,
    )
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=thin, candles_4h=[]) is None
    assert watcher._last_skip == "BTCUSDT:ohlcv_catchup"
    assert "BTCUSDT" not in watcher._watermarks
    assert "BTCUSDT" not in watcher._seeded

    ready_1h = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0)
    ready_4h = _htf_ending_with_setup(
        ready_1h, n=40, minutes_per_bar=240, start_price=90.0, drift=2.0
    )
    # Same symbol can be evaluated once depth arrives (stale tip seeds, does not stay blocked).
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=ready_1h, candles_4h=ready_4h) is None
    assert watcher._last_skip == "BTCUSDT:seeded_no_backlog"
    assert "BTCUSDT" in watcher._seeded


class _SpyPaper:
    """Records v1 open attempts; never opens."""

    def __init__(self) -> None:
        self.open_calls = 0

    def open_v1_combo_position(self, **_kwargs: Any) -> None:
        self.open_calls += 1
        return None


def _rows(watcher: V1PaperWatcher) -> dict[str, dict[str, Any]]:
    return {row["symbol"]: row for row in watcher.peek_books()}


def test_each_book_keeps_its_own_last_skip_and_bar():
    paper = _SpyPaper()
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=False, emit_alerts=False)

    btc_1h = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0)
    btc_4h = _htf_ending_with_setup(
        btc_1h, n=40, minutes_per_bar=240, start_price=90.0, drift=2.0
    )
    eth_1h = _trending_series(n=8, minutes_per_bar=60, start_price=50.0, drift=1.0)
    btc_bar = btc_1h[-1]["time"].isoformat()
    eth_bar = eth_1h[-1]["time"].isoformat()
    assert btc_bar != eth_bar

    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc_1h, candles_4h=btc_4h) is None
    assert watcher.on_closed_1h("ETHUSDT", candles_1h=eth_1h, candles_4h=[]) is None

    # The later ETH skip owns the watcher-wide slot but not the BTC row.
    assert watcher._last_skip == "ETHUSDT:ohlcv_catchup"
    rows = _rows(watcher)
    assert rows["BTCUSDT"]["last_skip"] == "BTCUSDT:seeded_no_backlog"
    assert rows["BTCUSDT"]["last_skip_bar"] == btc_bar
    assert rows["ETHUSDT"]["last_skip"] == "ETHUSDT:ohlcv_catchup"
    assert rows["ETHUSDT"]["last_skip_bar"] == eth_bar
    assert rows["SOLUSDT"]["last_skip"] is None
    assert rows["SOLUSDT"]["last_skip_bar"] is None

    # Same closed bar again: the existing skip still refuses the open.
    watermark = watcher._watermarks["BTCUSDT"]
    assert watcher.on_closed_1h("BTCUSDT", candles_1h=btc_1h, candles_4h=btc_4h) is None
    assert paper.open_calls == 0
    assert watcher._watermarks["BTCUSDT"] == watermark
    rows = _rows(watcher)
    assert rows["BTCUSDT"]["last_skip"] == "BTCUSDT:already_processed"
    assert rows["BTCUSDT"]["last_skip_bar"] == btc_bar
    assert rows["ETHUSDT"]["last_skip"] == "ETHUSDT:ohlcv_catchup"
    assert rows["ETHUSDT"]["last_skip_bar"] == eth_bar


def test_recreated_watcher_loads_skips_from_process_memory():
    thin = _trending_series(n=8, minutes_per_bar=60, start_price=100.0, drift=1.0)
    first = V1PaperWatcher(paper_engine=_SpyPaper(), emit_alerts=False)
    assert first.on_closed_1h("BTCUSDT", candles_1h=thin, candles_4h=[]) is None

    second = V1PaperWatcher(paper_engine=_SpyPaper(), emit_alerts=False)
    row = _rows(second)["BTCUSDT"]
    assert row["last_skip"] == "BTCUSDT:ohlcv_catchup"
    assert row["last_skip_bar"] == thin[-1]["time"].isoformat()
    # Skips only: watermarks and the processed set are not carried over.
    assert second._watermarks == {}
    assert second._processed_bars == set()

    reset_v1_paper_watcher_for_tests()
    assert _rows(V1PaperWatcher(emit_alerts=False))["BTCUSDT"]["last_skip"] is None
