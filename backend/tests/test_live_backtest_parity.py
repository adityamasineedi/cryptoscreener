"""Live ↔ backtest entry parity — research / paper validation tests.

Does not modify COMBO_02 parameters, V1, or enable production Telegram.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.research.live_backtest_parity.candle_validation import (
    TrackingCandles,
    validate_candle_close,
)
from app.research.live_backtest_parity.constants import (
    LIVE_BACKTEST_MATCH,
    LIVE_BACKTEST_MISMATCH,
    PRODUCTION_APPROVED,
    TELEGRAM_ELIGIBLE,
)
from app.research.live_backtest_parity.entry_price import (
    build_entry_triad,
    check_entry_price,
    difference_pct,
    deviation_bucket,
    deviation_distribution,
)
from app.research.live_backtest_parity.models import EventTimeline, ParityEvent
from app.research.live_backtest_parity.replay import replay_symbol, replay_universe
from app.research.live_backtest_parity.report import write_parity_artifacts
from app.research.live_backtest_parity.runner import build_synthetic_universe
from app.research.live_backtest_parity.shadow_compare import compare_live_vs_asof
from app.research.live_backtest_parity.telegram_validation import (
    MockTelegramSink,
    build_research_alert_payload,
    validate_alert_preflight,
)


def test_entry_triad_never_overwrites():
    triad = build_entry_triad(
        backtest_entry=100.0,
        live_signal_price=100.05,
        paper_entry=100.05,
        price_source="mark_price",
    )
    assert triad.backtest_entry == 100.0
    assert triad.live_signal_price == 100.05
    assert triad.paper_entry == 100.05
    assert triad.backtest_entry != triad.live_signal_price


def test_difference_pct_formula():
    assert abs(difference_pct(100.1, 100.0) - 0.1) < 1e-9


def test_entry_price_check_stale_and_valid():
    valid = build_entry_triad(
        backtest_entry=100.0, live_signal_price=100.05, paper_entry=100.05
    )
    stale = build_entry_triad(
        backtest_entry=100.0, live_signal_price=101.0, paper_entry=101.0
    )
    assert check_entry_price(valid, threshold_pct=0.10)["entry_price_status"] == "VALID"
    st = check_entry_price(stale, threshold_pct=0.10)
    assert st["entry_price_status"] == "STALE"
    assert st["entry_price_label"] == "STALE_ENTRY"
    unavail = check_entry_price(
        build_entry_triad(backtest_entry=100.0, live_signal_price=None, paper_entry=None)
    )
    assert unavail["entry_price_status"] == "UNAVAILABLE"


def test_deviation_buckets_descriptive():
    assert deviation_bucket(0.01) == "0-0.02%"
    assert deviation_bucket(0.03) == "0.02-0.05%"
    assert deviation_bucket(0.6) == ">0.50%"
    dist = deviation_distribution([0.01, 0.02, 0.05, 0.1, 0.2, 1.0])
    assert dist["n"] == 6
    assert dist["MAX"] == 1.0
    assert dist["P50"] is not None


def test_timeline_latencies_use_recorded_only():
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tl = EventTimeline(
        candle_close_time=t0,
        signal_detected_at=t0 + timedelta(milliseconds=120),
        trade_plan_created_at=t0 + timedelta(milliseconds=150),
        alert_generated_at=t0 + timedelta(milliseconds=180),
        paper_entry_at=t0 + timedelta(milliseconds=200),
    )
    lat = tl.latencies_ms()
    assert lat["signal_latency_ms"] == 120
    assert lat["total_signal_to_entry_ms"] == 80
    assert lat["telegram_latency_ms"] is None  # not recorded


def test_shadow_compare_match_and_mismatch():
    live = {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "entry_price": 100.0,
        "stop_price": 98.0,
        "tp1": 104.0,
        "rr": 2.0,
    }
    same = compare_live_vs_asof(
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp="2026-01-01T00:00:00+00:00",
        live_result=live,
        asof_result=dict(live),
    )
    assert same["parity_result"] == LIVE_BACKTEST_MATCH
    bad = dict(live)
    bad["entry_price"] = 101.0
    mm = compare_live_vs_asof(
        symbol="BTCUSDT",
        timeframe="15m",
        timestamp="2026-01-01T00:00:00+00:00",
        live_result=live,
        asof_result=bad,
    )
    assert mm["parity_result"] == LIVE_BACKTEST_MISMATCH
    assert mm["reason"]


def test_telegram_blocks_production_and_dynamic():
    payload = {
        "strategy_id": "LIVE_BACKTEST_PARITY_RESEARCH",
        "source": "LIVE_BACKTEST_PARITY",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "LONG",
        "entry": {"backtest_entry": 1, "live_signal_price": 1, "paper_entry": 1},
        "SL": 0.9,
        "TP": 1.2,
        "RR": 2.0,
        "production_approved": False,
        "telegram_eligible": False,
        "entry_price_status": "VALID",
    }
    assert validate_alert_preflight(payload)["ok"] is True
    bad = dict(payload)
    bad["production_approved"] = True
    assert validate_alert_preflight(bad)["ok"] is False
    tel = dict(payload)
    tel["telegram_eligible"] = True
    assert validate_alert_preflight(tel)["ok"] is False
    assert PRODUCTION_APPROVED is False
    assert TELEGRAM_ELIGIBLE is False


def test_candle_validation_detects_future():
    from datetime import datetime, timezone

    as_of = datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    ok_series = [
        {
            "time": datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 1,
        }
    ]
    v = validate_candle_close(
        as_of=as_of,
        signal_detected_at=datetime(2026, 1, 1, 1, 15, tzinfo=timezone.utc),
        setup_candles=ok_series,
        setup_timeframe="15m",
        candles_1h=ok_series,
        candles_4h=ok_series,
    )
    assert v.future_data_detected is False
    future = [
        {
            "time": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 1,
        }
    ]
    v2 = validate_candle_close(
        as_of=as_of,
        signal_detected_at=datetime(2026, 1, 1, 1, 15, tzinfo=timezone.utc),
        setup_candles=future,
        setup_timeframe="15m",
    )
    assert v2.future_data_detected is True


def test_tracking_candles_records_access():
    tracked = TrackingCandles([{"time": i} for i in range(10)])
    _ = tracked[3]
    assert tracked.max_accessed == 3
    _ = tracked[:5]
    assert tracked.max_accessed >= 4


def test_replay_live_matches_asof_no_lookahead():
    universe = build_synthetic_universe(["BTCUSDT"])
    pack = universe["BTCUSDT"]
    result = replay_symbol(
        symbol="BTCUSDT",
        candles_15m=pack["15m"],
        candles_1h=pack["1h"],
        candles_4h=pack["4h"],
        timeframe="15m",
    )
    assert result["future_data_fails"] == 0
    assert result["mismatches"] == []
    for e in result["events"]:
        assert e.parity_result == LIVE_BACKTEST_MATCH
        assert e.production_approved is False
        assert e.telegram_eligible is False
        assert e.candle_validation.future_data_detected is False
        # Triad preserved
        assert e.entry_triad.backtest_entry is not None
        assert e.entry_triad.live_signal_price is not None
        assert e.entry_triad.paper_entry is not None


def test_replay_universe_and_report(tmp_path: Path):
    universe = build_synthetic_universe(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    result = replay_universe(
        series_by_symbol=universe,
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframe="15m",
    )
    assert result["future_data_fails"] == 0
    assert result["mismatches"] == []
    assert result["live_orders"] is False
    assert result["strategy_changed"] is False
    assert result["v1_changed"] is False

    summary = write_parity_artifacts(
        tmp_path,
        events=result["events"],
        mismatches=result["mismatches"],
        future_data_fails=result["future_data_fails"],
        telegram_stats=result["telegram_stats"],
        per_symbol=result["per_symbol"],
    )
    for name in (
        "parity_events.csv",
        "entry_deviation.csv",
        "latency.csv",
        "replay_results.csv",
        "summary.json",
        "validation_report.md",
    ):
        assert (tmp_path / name).exists()
    verdict = summary["verdict"]
    assert verdict["LIVE_BACKTEST_PARITY"] == "PASS"
    assert verdict["NO_LOOKAHEAD"] == "PASS"
    assert verdict["NO_LIVE_ORDERS"] == "CONFIRMED"
    assert verdict["NO_PRODUCTION_APPROVAL"] == "CONFIRMED"
    assert verdict["NO_STRATEGY_CHANGES"] == "CONFIRMED"


def test_mock_telegram_blocks_dynamic():
    sink = MockTelegramSink()
    event = ParityEvent(
        event_id="t1",
        symbol="BNBUSDT",
        timeframe="15m",
        live_direction="LONG",
        live_sl=90.0,
        live_tp=110.0,
        live_rr=2.0,
        strategy_id="COMBO_02_V2_RESEARCH",
        source="DYNAMIC_CANDIDATE_PIPELINE",
    )
    event.entry_triad = build_entry_triad(
        backtest_entry=100.0, live_signal_price=100.0, paper_entry=100.0
    )
    event.entry_price_status = "VALID"
    out = sink.process(event)
    assert out["delivery_status"] == "blocked"
    payload = build_research_alert_payload(event)
    assert payload["production_approved"] is False
    assert payload["telegram_eligible"] is False
    assert "backtest_entry" in payload["entry"]
    assert "live_signal_price" in payload["entry"]
    assert "paper_entry" in payload["entry"]


def test_v2_paper_records_entry_triad(monkeypatch):
    """Experimental paper path records triad without claiming live==paper silently."""
    from app.services.paper_trade import PaperTradeEngine

    engine = PaperTradeEngine(enabled=True)
    monkeypatch.setattr(
        "app.services.paper_trade._live_price_with_source",
        lambda _sym: (100.2, "mark_price"),
    )
    pos = engine.open_experimental_position(
        symbol="BNBUSDT",
        timeframe="1h",
        eval_result={
            "entry_price": 100.0,
            "stop_price": 98.0,
            "tp1": 104.0,
            "direction": "LONG",
        },
        risk_percent=0.0025,
        signal_snippet={
            "strategy_id": "COMBO_02_V2_RESEARCH",
            "source": "V2_CANDIDATE_PAPER_WATCHER",
            "telegram_eligible": False,
            "production_approved": False,
        },
        setup_bar_time_utc="2026-01-01T00:00:00+00:00",
        replay=False,
        emit_alert=False,
    )
    assert pos is not None
    snip = pos.signal_snippet
    assert snip["backtest_entry"] == 100.0
    assert snip["live_signal_price"] == 100.2
    assert snip["paper_entry"] == 100.0  # simulator fill = strategy entry
    assert snip["paper_equals_backtest"] is True
    assert snip["live_signal_price"] != snip["paper_entry"]
    assert snip["production_approved"] is False
    assert snip["telegram_eligible"] is False
    assert snip.get("entry_deviation_pct") is not None
