"""Backtest job fixes from the 2026-10-18 Backtest-screen review.

A fake engine cell stands in for the child process, so no DB or subprocess runs.
"""

from __future__ import annotations

import asyncio

from app.research.backtest_job import BacktestJobService
from app.research.engine_backtest import _gross_profit_factor

COMMON = dict(
    symbols=["BTCUSDT", "ETHUSDT"],
    timeframes=["1h"],
    direction="LONG",
    combination_id="CANDLE12_V2",
)


def _patch_cell(monkeypatch, cell) -> None:
    monkeypatch.setattr("app.research.engine_backtest.engine_strategy_matrix", cell)


async def _wait_for(pred, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.02)


def test_old_run_cleanup_keeps_the_next_jobs_watchdog(monkeypatch):
    release = asyncio.Event()
    calls: list[int] = []

    async def cell(*, should_cancel, **_kw):
        calls.append(1)
        if len(calls) == 1:
            # The cancelled job's cell is still finishing when the new job starts.
            while not release.is_set():
                await asyncio.sleep(0.02)
        else:
            while not should_cancel():
                await asyncio.sleep(0.02)
        return {"status": "CANCELLED", "rows": []}

    _patch_cell(monkeypatch, cell)
    svc = BacktestJobService()

    async def run() -> None:
        await svc.start(**COMMON)
        old_task = svc._task  # noqa: SLF001
        await _wait_for(lambda: len(calls) == 1)
        await svc.cancel()
        await svc.start(**COMMON)
        new_watchdog = svc._watchdog  # noqa: SLF001
        release.set()
        await asyncio.wait_for(old_task, 5)
        assert svc._job.status == "running"  # noqa: SLF001
        assert not new_watchdog.done(), "old run cancelled the new job's watchdog"
        svc._job._cancel = True  # noqa: SLF001
        await asyncio.wait_for(svc._task, 5)  # noqa: SLF001

    asyncio.run(run())


def test_watchdog_timeout_is_not_reported_as_cancelled(monkeypatch):
    async def cell(*, should_cancel, **_kw):
        while not should_cancel():
            await asyncio.sleep(0.02)
        return {"status": "CANCELLED", "rows": []}

    _patch_cell(monkeypatch, cell)
    svc = BacktestJobService()

    async def run() -> None:
        await svc.start(**COMMON)
        job = svc._job  # noqa: SLF001
        # What _watchdog_loop does on job_timeout.
        job.status = "error"
        job.error = "Job exceeded timeout (3s)"
        job.error_code = "job_timeout"
        job._cancel = True
        await asyncio.wait_for(svc._task, 5)  # noqa: SLF001
        assert job.status == "error"
        assert job.error_code == "job_timeout"

    asyncio.run(run())


def test_trade_count_adds_up_across_cells(monkeypatch):
    sizes = {"BTCUSDT": 3, "ETHUSDT": 2}

    async def cell(*, symbol, timeframe, **_kw):
        return {
            "status": "OK",
            "rows": [
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": "LONG",
                    "sample_size": sizes[symbol],
                    "trades": [],
                }
            ],
        }

    _patch_cell(monkeypatch, cell)
    svc = BacktestJobService()

    async def run() -> None:
        await svc.start(**COMMON)
        await asyncio.wait_for(svc._task, 5)  # noqa: SLF001
        await _wait_for(lambda: svc._job.status != "running")  # noqa: SLF001

    asyncio.run(run())
    st = svc._job.to_dict()  # noqa: SLF001
    assert st["status"] == "done"
    assert st["trades"] == 5


def test_engine_gross_profit_factor_uses_gross_pnl():
    trades = [
        {"gross_pnl_usd": 30.0, "net_pnl_usd": 28.0},
        {"gross_pnl_usd": -10.0, "net_pnl_usd": -12.0},
        {"gross_pnl_usd": None, "net_pnl_usd": 1.0},
    ]
    assert _gross_profit_factor(trades) == 3.0
    assert _gross_profit_factor([{"gross_pnl_usd": 5.0}]) is None
    assert _gross_profit_factor([]) is None
