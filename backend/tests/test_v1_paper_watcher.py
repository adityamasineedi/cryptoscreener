"""COMBO_02 v1 paper watcher — closed-bar gating, universe, dedupe, negatives."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.v1_production import enabled_v1_books
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


def test_enabled_v1_books_are_1h_btc_eth_sol():
    books = enabled_v1_books(secondary_enabled=True, timeframes={"1h"})
    assert {(b.symbol, b.timeframe) for b in books} == {
        ("BTCUSDT", "1h"),
        ("ETHUSDT", "1h"),
        ("SOLUSDT", "1h"),
    }
    core = enabled_v1_books(secondary_enabled=False, timeframes={"1h"})
    assert [b.symbol for b in core] == ["BTCUSDT"]


def test_watcher_rejects_outside_universe():
    paper = PaperTradeEngine(enabled=True, v1_profile_enabled=True)
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=True, emit_alerts=False)
    assert watcher.book_for("DOGEUSDT") is None
    assert watcher.on_closed_1h("DOGEUSDT", candles_1h=[], candles_4h=[]) is None


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


def test_path_a_15m_opens_in_parallel_when_watcher_does_not_own(monkeypatch):
    import app.services.paper_trade as paper_mod

    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    paper = PaperTradeEngine(enabled=True, entry_mode=PATH_A, v1_profile_enabled=True)
    paper.legacy_auto_entry_enabled = True
    paper.v1_watcher_owns_entries = False
    paper.risk_policy.enabled = False
    payload = {
        "status": "WAITING",
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
        "stop": {"final_stop": 98},
        "targets": [{"target_price": 104}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "ohlcv_freshness": "OK",
    }
    pos = paper.on_setup_signal("BTCUSDT", payload)
    assert pos is not None
    assert pos.signal_snippet.get("source") == "LEGACY_SETUP_SIGNAL"
    assert pos.signal_snippet.get("strategy_id") == "RESEARCH_15M"


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
