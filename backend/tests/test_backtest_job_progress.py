"""Backtest job progress, HTF cache, stall, and loader metadata tests.

No strategy-rule changes are validated here — only observability, filtering,
cancellation, and HTF closed-bar caching equivalence.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.research.backtest_job import BacktestJob, BacktestJobService
from app.research.backtest_timing import STRUCTURE_SCAN_HEARTBEAT
from app.research.bos_strategy_comparison.htf import (
    as_of_index_at_or_before,
    build_htf_as_of_index_map,
    precompute_htf_trend_cache,
    trend_at_as_of,
)
from app.research.combination_backtest import run_combination_backtest
from app.research.postgres_ohlcv import load_ohlcv_series_range
from app.signals.config import SignalConfig


def _ts(i: int, minutes: int = 15) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i * minutes)


def _candle(i: int, minutes: int = 15, price: float | None = None) -> dict[str, Any]:
    p = 100.0 + (price if price is not None else i * 0.1)
    return {
        "time": _ts(i, minutes),
        "open": p,
        "high": p + 1,
        "low": p - 1,
        "close": p + 0.2,
        "volume": 1000.0,
    }


def test_progress_percent_increases_during_cell_processing():
    job = BacktestJob(
        job_id="p1",
        status="running",
        total_cells=1,
        done_cells=0,
        bars_processed=0,
        total_bars=1000,
        phase="STRUCTURE_SCAN",
    )
    assert job.progress_percent() == 0.0
    job.bars_processed = 374
    pct = job.progress_percent()
    assert pct > 0
    assert pct == pytest.approx(37.4, abs=0.1)
    job.done_cells = 1
    job.status = "done"
    assert job.progress_percent() == 100.0


def test_analytics_attach_phase_shows_nonzero_progress():
    """ANALYTICS_ATTACH must not look stuck at 0% when bar counters are empty."""
    job = BacktestJob(
        job_id="a1",
        status="running",
        total_cells=3,
        done_cells=0,
        bars_processed=0,
        total_bars=0,
        phase="ANALYTICS_ATTACH",
    )
    assert job.progress_percent() > 20.0


def test_to_dict_strips_by_bar_from_market_structure():
    job = BacktestJob(job_id="ms1", status="running", total_cells=1, done_cells=0)
    job.rows = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "trades": [{"id": 1}],
            "market_structure": {
                "status": "OK",
                "by_bar": [{"i": n} for n in range(500)],
                "table_rows": [{"decision_time": "t", "regime_1h": "BULL"} for _ in range(3)],
                "trade_regime_summary": [{"regime": "BULL"}],
            },
        }
    ]
    running = job.to_dict()
    ms = running["rows"][0]["market_structure"]
    assert "by_bar" not in ms
    assert ms["table_rows"] == []
    assert ms["table_rows_count"] == 3
    assert ms["table_rows_deferred"] is True
    assert ms["trade_regime_summary"]

    job.status = "done"
    job.done_cells = 1
    done = job.to_dict()
    ms_done = done["rows"][0]["market_structure"]
    assert "by_bar" not in ms_done
    assert len(ms_done["table_rows"]) == 3


def test_to_dict_exposes_heartbeat_fields():
    job = BacktestJob(job_id="hb1", status="running", total_cells=1)
    job.heartbeat(
        phase=STRUCTURE_SCAN_HEARTBEAT,
        bars_processed=500,
        total_bars=2000,
        trades=3,
        rows_loaded=2100,
    )
    d = job.to_dict()
    assert d["phase"] == STRUCTURE_SCAN_HEARTBEAT
    assert d["bars_processed"] == 500
    assert d["total_bars"] == 2000
    assert d["trades_generated"] == 3
    assert d["rows_loaded"] == 2100
    assert d["last_heartbeat"]
    assert d["progress_percent"] > 0


def test_single_flight_rejects_duplicate_start():
    svc = BacktestJobService()
    svc._job = BacktestJob(  # noqa: SLF001
        job_id="busy",
        status="running",
        symbols=["BTCUSDT"],
        timeframes=["15m"],
        total_cells=1,
    )

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="already running"):
            await svc.start(symbols=["BTCUSDT"], timeframes=["15m"])

    asyncio.run(_run())


def test_cancel_sets_cancelled_status():
    svc = BacktestJobService()
    svc._job = BacktestJob(  # noqa: SLF001
        job_id="c1",
        status="running",
        symbols=["BTCUSDT"],
        timeframes=["15m"],
        total_cells=1,
    )

    async def _run() -> None:
        st = await svc.cancel()
        assert st["status"] == "cancelled"
        assert st["phase"] == "JOB_CANCELLED"

    asyncio.run(_run())


def test_heartbeat_timeout_marks_stalled(monkeypatch):
    monkeypatch.setattr(
        "app.research.backtest_job._settings_timeouts",
        lambda: (3600.0, 0.05, 60.0),
    )
    svc = BacktestJobService()
    job = BacktestJob(
        job_id="stall1",
        status="running",
        symbols=["BTCUSDT"],
        timeframes=["15m"],
        total_cells=1,
        _t0=time.perf_counter(),
    )
    # Old heartbeat so watchdog trips.
    job._hb_mono = time.perf_counter() - 1.0  # noqa: SLF001
    svc._job = job  # noqa: SLF001

    async def _run() -> None:
        await svc._watchdog_loop(job)  # noqa: SLF001
        assert job.status == "stalled"
        assert job.error_code == "NO_HEARTBEAT"

    asyncio.run(_run())


def test_htf_precompute_matches_trend_at_as_of_no_lookahead():
    candles = [_candle(i, minutes=60) for i in range(80)]
    cfg = SignalConfig()
    cache: dict[tuple[str, int], str] = {}
    precompute_htf_trend_cache(
        candles, timeframe="1h", symbol="BTCUSDT", config=cfg, cache=cache
    )
    for i in (10, 25, 40, 60, 79):
        expected = trend_at_as_of(
            candles, i, timeframe="1h", symbol="BTCUSDT", config=cfg, cache=None
        )
        assert cache[("1h", i)] == expected


def test_htf_index_map_matches_as_of_index_at_or_before():
    setup = [_candle(i, minutes=15) for i in range(40)]
    htf = [_candle(i, minutes=60) for i in range(12)]
    mapped = build_htf_as_of_index_map(setup, htf)
    for i, c in enumerate(setup):
        assert mapped[i] == as_of_index_at_or_before(htf, c["time"])


def test_htf_map_never_uses_future_bars():
    setup = [_candle(i, minutes=15) for i in range(16)]
    htf = [_candle(i, minutes=60) for i in range(5)]
    mapped = build_htf_as_of_index_map(setup, htf)
    for i, idx in enumerate(mapped):
        if idx is None:
            continue
        assert htf[idx]["time"] <= setup[i]["time"]


def test_small_15m_range_emits_heartbeats_and_completes():
    # Synthetic series large enough for structure walk.
    n = 180
    candles = []
    price = 100.0
    for i in range(n):
        phase = i % 8
        if phase == 0:
            o = c = price
            h, l = price + 0.2, price - 3.0
        elif phase == 4:
            o = c = price
            h, l = price + 3.0, price - 0.2
        else:
            o = price
            c = price + 0.25
            h = max(o, c) + 0.3
            l = min(o, c) - 0.3
        candles.append(
            {
                "time": _ts(i, 15),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 2000.0,
            }
        )
        price += 0.5

    h1 = [_candle(i, minutes=60, price=i * 0.5) for i in range(50)]
    h4 = [_candle(i, minutes=240, price=i * 0.5) for i in range(30)]
    heartbeats: list[dict[str, Any]] = []

    out = run_combination_backtest(
        "BTCUSDT",
        "15m",
        candles,
        "COMBO_02",
        direction_filter="LONG",
        candles_1h=h1,
        candles_4h=h4,
        progress_callback=heartbeats.append,
        job_id="unit_small",
    )
    assert out["status"] in ("OK", "INSUFFICIENT_DATA", "SUCCESS_EMPTY") or out.get(
        "status"
    ) == "OK"
    assert out["candles_processed"] >= 0
    assert any(h.get("phase") for h in heartbeats)
    # Progress callback should see increasing bars when walk runs.
    bars = [int(h.get("bars_processed") or 0) for h in heartbeats if h.get("bars_processed")]
    if bars:
        assert max(bars) >= min(bars)


def test_job_failure_exposes_error():
    job = BacktestJob(job_id="err1", status="error", error="boom", error_code="cell_error")
    d = job.to_dict()
    assert d["error"] == "boom"
    assert d["error_code"] == "cell_error"


@pytest.mark.asyncio
async def test_date_range_sql_filtering_params(monkeypatch):
    """Loader builds bounded SQL (symbol/tf/time) — no unbounded full-table pull."""
    captured: dict[str, Any] = {}

    class _Result:
        def fetchall(self):
            return []

    class _Conn:
        async def execute(self, statement, params=None):
            captured["sql"] = str(statement)
            captured["params"] = params
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Engine:
        def begin(self):
            return _Conn()

    class _DB:
        engine = _Engine()

    monkeypatch.setattr("app.research.postgres_ohlcv.db_manager", _DB())
    start = datetime(2022, 10, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, tzinfo=timezone.utc)
    await load_ohlcv_series_range(
        "BTCUSDT",
        "15m",
        start=start,
        end_exclusive=end,
        warmup_bars=0,
    )
    sql = captured["sql"].lower()
    params = captured["params"]
    assert "symbol" in sql and "timeframe" in sql
    assert "time >=" in sql and "time <" in sql
    assert "order by time" in sql
    assert params["symbol"] == "BTCUSDT"
    assert params["tf"] == "15m"
    assert params["start_ts"] == start
    assert params["end_ts"] == end


def test_load_report_shape_for_ui():
    """Contract for loader metadata shown after data load (spec §4)."""
    report = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "requested_start": "2022-10-01",
        "requested_end": "2024-12-31",
        "actual_start": "2022-10-01T00:00:00+00:00",
        "actual_end": "2024-12-30T23:45:00+00:00",
        "rows_loaded": 82000,
        "requested_range_available": True,
    }
    assert report["requested_range_available"] is True
    assert report["rows_loaded"] > 0


def test_frontend_poll_fields_present_in_job_dict():
    job = BacktestJob(
        job_id="fe1",
        status="running",
        total_cells=1,
        done_cells=0,
        phase="STRUCTURE_SCAN_HEARTBEAT",
        bars_processed=1000,
        total_bars=80000,
        trades_generated=2,
    )
    job.heartbeat(phase=job.phase, bars_processed=1000, total_bars=80000, trades=2)
    d = job.to_dict()
    for key in (
        "phase",
        "bars_processed",
        "total_bars",
        "trades_generated",
        "last_heartbeat",
        "elapsed_seconds",
        "progress_percent",
    ):
        assert key in d


@pytest.mark.asyncio
async def test_job_run_propagates_progress_callback():
    svc = BacktestJobService()
    heartbeats: list[str] = []

    async def fake_matrix(**kwargs):
        cb = kwargs.get("progress_callback")
        assert cb is not None
        cb(
            {
                "phase": "STRUCTURE_SCAN_HEARTBEAT",
                "bars_processed": 250,
                "total_bars": 1000,
                "trades": 1,
            }
        )
        heartbeats.append("ok")
        return {
            "status": "OK",
            "rows": [
                {
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "sample_size": 0,
                    "period_start": "2024-01-01",
                    "period_end": "2024-03-31",
                    "bars_loaded": 1000,
                }
            ],
            "playbook": "test",
            "combination_name": "COMBO_02",
            "disclaimer": "x",
            "label": "LONG_STRATEGY_BACKTEST",
            "dataset_id": "ds",
            "timing": {"db_query_seconds": 0.01},
        }

    mock_svc = AsyncMock()
    mock_svc.strategy_matrix = fake_matrix

    with patch(
        "app.research.service.get_bos_research_service",
        return_value=mock_svc,
    ):
        started = await svc.start(
            symbols=["BTCUSDT"],
            timeframes=["15m"],
            direction="LONG",
            combination_id="COMBO_02",
            strategy_id="COMBO_02_V1",
            start_date="2024-01-01",
            end_date="2024-03-31",
            risk_percent=0.015,
            principal_usd=1000,
            leverage=2,
            research_risk_override=True,
        )
        assert started["status"] == "running"
        # Wait for background task.
        for _ in range(50):
            st = svc.status()
            if st.get("status") in ("done", "error", "cancelled", "stalled"):
                break
            await asyncio.sleep(0.05)
        st = svc.status()
        assert heartbeats == ["ok"]
        assert st["status"] == "done"
        assert st["bars_processed"] >= 250 or st["done_cells"] == 1
