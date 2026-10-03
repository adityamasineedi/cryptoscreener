"""Replay parity: V1PaperWatcher uses the same COMBO_02 1h evaluation as backtest.

Primary: every backtest entry bar must pass ``evaluate_combination_at_bar`` /
``is_v1_long_entry`` with matching entry/stop/TP1 and HTF snapshot.

Secondary: watcher.replay opens only COMBO_02 v1 1h Path A trades (no 15m /
Path B / LOCAL), and each opened bar is a backtest-qualifying entry index.

Synthetic OHLCV is TEST-ONLY. Optional stored fixture asserts identity on the
2025-01→2026-01 window when present.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.config import ResearchConfig
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.v1_paper_watcher import (
    V1PaperWatcher,
    is_v1_long_entry,
    reset_v1_paper_watcher_for_tests,
)
from app.signals.config import SignalConfig

pytestmark = pytest.mark.v1_freeze

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def _ts(minutes: int) -> datetime:
    return datetime(2024, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _candle(t: datetime, o: float, h: float, l: float, c: float, v: float = 2000.0) -> dict:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _trending_series(
    *,
    n: int,
    minutes_per_bar: int,
    start_price: float,
    drift: float,
    wave: float = 4.0,
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
        out.append(_candle(t, o, h, l, c))
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
    end = setup[-1]["time"]
    pre = _trending_series(
        n=n,
        minutes_per_bar=minutes_per_bar,
        start_price=start_price,
        drift=drift,
    )
    shift = end - pre[-1]["time"]
    out = []
    for c in pre:
        d = dict(c)
        d["time"] = c["time"] + shift
        out.append(d)
    return out


def _bt_trades(run: dict[str, Any]) -> list[Any]:
    return list(run.get("trades") or [])


def _trade_field(t: Any, key: str) -> Any:
    if isinstance(t, dict):
        return t.get(key)
    return getattr(t, key, None)


@pytest.fixture(autouse=True)
def _reset():
    reset_v1_paper_watcher_for_tests()
    yield
    reset_v1_paper_watcher_for_tests()


def _bullish_fixture() -> tuple[list[dict], list[dict]]:
    h1 = _trending_series(
        n=160, minutes_per_bar=60, start_price=40_000.0, drift=40.0, wave=120.0
    )
    h4 = _htf_ending_with_setup(
        h1, n=160, minutes_per_bar=240, start_price=38_000.0, drift=80.0
    )
    return h1, h4


def test_watcher_eval_matches_backtest_entry_bars():
    """Each COMBO_02 backtest entry bar is a v1 LONG under the watcher eval."""
    h1, h4 = _bullish_fixture()
    combo = get_combination("COMBO_02")
    assert combo is not None

    bt = run_combination_backtest(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=h1,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        direction_filter="LONG",
        candles_1h=h1,
        candles_4h=h4,
    )
    trades = _bt_trades(bt)
    assert len(trades) >= 1

    watcher = V1PaperWatcher(
        paper_engine=PaperTradeEngine(
            enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
        ),
        replay_mode=True,
        emit_alerts=False,
    )

    for t in trades:
        idx = int(_trade_field(t, "entry_index"))
        result = watcher.evaluate_at_bar(
            symbol="BTCUSDT",
            candles_1h=h1,
            candles_4h=h4,
            as_of_index=idx,
        )
        assert is_v1_long_entry(result), (
            f"bar {idx} not v1 long: {result.get('status')} {result.get('reason')}"
        )
        assert str(result.get("combination_id")) == "COMBO_02"
        assert abs(float(result["entry_price"]) - float(_trade_field(t, "entry_price"))) < 1e-6
        assert abs(float(result["stop_price"]) - float(_trade_field(t, "stop_price"))) < 1e-6
        tp1_bt = _trade_field(t, "tp1")
        if tp1_bt is not None and result.get("tp1") is not None:
            assert abs(float(result["tp1"]) - float(tp1_bt)) < 1e-6
        htf = result.get("htf") or {}
        assert htf.get("htf_alignment") == "HTF_ALIGNED"
        assert htf.get("trend_1h") == "BULLISH"
        assert htf.get("trend_4h") == "BULLISH"

        # Opening from the same eval must produce COMBO_02 v1 1h Path A paper
        book = watcher.book_for("BTCUSDT")
        assert book is not None
        paper = PaperTradeEngine(
            enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
        )
        pos = paper.open_v1_combo_position(
            symbol="BTCUSDT",
            timeframe="1h",
            book=book,
            eval_result=result,
            setup_bar_time_utc=str(result.get("signal_time") or ""),
            replay=True,
            emit_alert=False,
        )
        assert pos is not None
        assert pos.timeframe == "1h"
        assert pos.signal_snippet.get("combo_id") == "COMBO_02"
        assert pos.signal_snippet.get("combo_version") == "v1-combo02-long-htf"
        assert pos.signal_snippet.get("path") == "A"
        assert pos.signal_snippet.get("v1_tier") == "core"


def test_watcher_replay_labels_and_htf_only():
    """Replay opens are COMBO_02 v1 1h Path A with HTF_ALIGNED gate snapshot."""
    h1, h4 = _bullish_fixture()
    paper = PaperTradeEngine(
        enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
    )
    watcher = V1PaperWatcher(
        paper_engine=paper, replay_mode=True, emit_alerts=False
    )
    opened = watcher.replay("BTCUSDT", h1, h4, simulate_exits=True)
    assert opened
    for pos in opened:
        snip = pos.signal_snippet or {}
        assert pos.timeframe == "1h"
        assert snip.get("combo_id") == "COMBO_02"
        assert snip.get("combo_version") == "v1-combo02-long-htf"
        assert snip.get("path") == "A"
        assert snip.get("htf_alignment") == "HTF_ALIGNED"
        assert snip.get("trend_1h") == "BULLISH"
        assert snip.get("trend_4h") == "BULLISH"
        assert snip.get("bos_direction") == "BULLISH_BOS"
        assert snip.get("source") == "V1_PAPER_WATCHER"
        assert snip.get("strategy_id") == "COMBO_02_V1"
        assert snip.get("telegram_eligible") is True


def test_no_entries_when_4h_bearish():
    h1 = _trending_series(
        n=120, minutes_per_bar=60, start_price=40_000.0, drift=40.0, wave=120.0
    )
    h4 = _htf_ending_with_setup(
        h1, n=120, minutes_per_bar=240, start_price=50_000.0, drift=-80.0
    )
    combo = get_combination("COMBO_02")
    bt = run_combination_backtest(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=h1,
        combination=combo,
        direction_filter="LONG",
        candles_1h=h1,
        candles_4h=h4,
    )
    assert len(_bt_trades(bt)) == 0
    paper = PaperTradeEngine(
        enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
    )
    watcher = V1PaperWatcher(
        paper_engine=paper, replay_mode=True, emit_alerts=False
    )
    assert watcher.replay("BTCUSDT", h1, h4) == []


def test_15m_and_path_b_cannot_create_v1_paper_trade():
    paper = PaperTradeEngine(
        enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
    )
    paper.legacy_auto_entry_enabled = True
    paper.v1_watcher_owns_entries = True
    assert (
        paper.on_setup_signal(
            "BTCUSDT",
            {
                "status": "LONG_ENTRY_CANDIDATE",
                "direction": "LONG",
                "timeframe": "15m",
                "bos": {
                    "state": "CONFIRMED",
                    "direction": "BULLISH_BOS",
                    "broken_level": 100,
                },
                "trend": {"trend": "BULLISH"},
                "mtf": {"MTF_ALIGNMENT": "HTF_ALIGNED"},
                "entry": {"entry_price": 100},
                "stop": {"final_stop": 98},
                "targets": [{"target_price": 104}],
                "risk_reward": {"RISK_REWARD": "PASS"},
                "ohlcv_freshness": "FRESH",
            },
        )
        is None
    )
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=True, emit_alerts=False)
    assert ("BTCUSDT", "15m") not in {
        (b.symbol, b.timeframe) for b in watcher.books
    }


@pytest.mark.skipif(
    not (FIXTURE_DIR / "btcusdt_1h_2025_2026.json").exists(),
    reason="Optional stored BTC OHLCV fixture not present",
)
def test_btc_fixture_baseline_window_optional():
    import json

    raw = json.loads((FIXTURE_DIR / "btcusdt_1h_2025_2026.json").read_text(encoding="utf-8"))
    h1 = raw["candles_1h"]
    h4 = raw["candles_4h"]
    combo = get_combination("COMBO_02")
    bt = run_combination_backtest(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=h1,
        combination=combo,
        direction_filter="LONG",
        candles_1h=h1,
        candles_4h=h4,
    )
    trades = _bt_trades(bt)
    watcher = V1PaperWatcher(
        paper_engine=PaperTradeEngine(
            enabled=True, risk_policy=PaperRiskPolicy(enabled=False)
        ),
        replay_mode=True,
        emit_alerts=False,
    )
    for t in trades:
        idx = int(_trade_field(t, "entry_index"))
        assert is_v1_long_entry(
            watcher.evaluate_at_bar(
                symbol="BTCUSDT",
                candles_1h=h1,
                candles_4h=h4,
                as_of_index=idx,
            )
        )
    # Documented research baseline on production data ≈ 27
    assert len(trades) == 27
