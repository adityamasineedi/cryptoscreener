"""Unit tests for background long-strategy backtest job service."""

from __future__ import annotations

import asyncio

from app.research.backtest_job import ALLOWED_TFS, BacktestJob, BacktestJobService


def test_allowed_timeframes_include_ui_defaults():
    assert "15m" in ALLOWED_TFS
    assert "1h" in ALLOWED_TFS
    assert "4h" in ALLOWED_TFS


def test_start_rejects_empty_symbols():
    svc = BacktestJobService()

    async def _run() -> None:
        try:
            await svc.start(symbols=[], timeframes=["15m"])
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "symbol" in str(exc).lower()

    asyncio.run(_run())


def test_start_rejects_unsupported_timeframes():
    svc = BacktestJobService()

    async def _run() -> None:
        try:
            await svc.start(symbols=["BTCUSDT"], timeframes=["3m"])
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "timeframe" in str(exc).lower()

    asyncio.run(_run())


def test_single_flight_busy_and_cancel():
    svc = BacktestJobService()
    svc._job = BacktestJob(  # noqa: SLF001
        job_id="abc123",
        status="running",
        symbols=["BTCUSDT"],
        timeframes=["15m"],
        total_cells=1,
    )

    async def _run() -> None:
        try:
            await svc.start(symbols=["ETHUSDT"], timeframes=["15m"])
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "already running" in str(exc).lower()

        st = await svc.cancel()
        assert st["status"] == "cancelled"
        assert st["job_id"] == "abc123"

    asyncio.run(_run())


def test_idle_status():
    svc = BacktestJobService()
    st = svc.status()
    assert st["status"] == "idle"
    assert st["job_id"] is None


def test_backtest_job_pct_from_cells():
    job = BacktestJob(
        job_id="bt1",
        status="running",
        total_cells=6,
        done_cells=3,
        current="ETHUSDT 1h",
    )
    d = job.to_dict()
    assert d["pct"] == 50.0
    assert d["current"] == "ETHUSDT 1h"
