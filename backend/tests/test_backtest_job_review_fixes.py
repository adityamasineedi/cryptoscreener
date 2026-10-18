"""Backtest job fixes from the 2026-10-18 Backtest-screen review.

A fake engine cell stands in for the child process, so no DB or subprocess runs.
"""

from __future__ import annotations

import asyncio

from app.research import backtest_job as bj
from app.research.backtest_cpu_pool import _child_stderr_file, _read_child_stderr, _unlink_child_file
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


def test_timeout_is_per_cell_so_a_long_matrix_finishes(monkeypatch):
    # 3 cells of 1.5 s each (4.5 s in all) against a 2.5 s limit.
    monkeypatch.setattr(bj, "_settings_timeouts", lambda: (2.5, 120.0, 60.0))

    async def cell(*, symbol, timeframe, progress_callback, **_kw):
        for _ in range(15):
            progress_callback({"phase": "ENGINE_WALK"})
            await asyncio.sleep(0.1)
        return {"status": "OK", "rows": [{"symbol": symbol, "timeframe": timeframe, "trades": []}]}

    _patch_cell(monkeypatch, cell)
    svc = BacktestJobService()

    async def run() -> None:
        await svc.start(**{**COMMON, "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]})
        await asyncio.wait_for(svc._task, 15)  # noqa: SLF001

    asyncio.run(run())
    assert svc._job.status == "done", svc._job.error  # noqa: SLF001


def test_a_single_cell_over_the_limit_still_times_out(monkeypatch):
    monkeypatch.setattr(bj, "_settings_timeouts", lambda: (2.5, 120.0, 60.0))

    async def cell(*, should_cancel, progress_callback, **_kw):
        while not should_cancel():
            progress_callback({"phase": "ENGINE_WALK"})
            await asyncio.sleep(0.1)
        return {"status": "CANCELLED", "rows": []}

    _patch_cell(monkeypatch, cell)
    svc = BacktestJobService()

    async def run() -> None:
        await svc.start(**{**COMMON, "symbols": ["BTCUSDT"]})
        await asyncio.wait_for(svc._task, 15)  # noqa: SLF001

    asyncio.run(run())
    job = svc._job  # noqa: SLF001
    assert job.status == "error"
    assert job.error_code == "job_timeout"
    assert "BTCUSDT 1h" in (job.error or "")


def _sync_backtest(**kw):
    from app.api.routes import research_long_strategy_backtest

    args = dict(
        symbols="BTCUSDT", timeframes="1h", direction="LONG", combination_id="COMBO_01",
        limit=1200, risk_usd=20.0, principal_usd=1000.0, leverage=2.0, risk_mode=None,
        research_risk_override=False, taker_fee_pct=0.04, maker_fee_pct=0.02,
        include_trades=True, start_date=None, end_date=None,
    )
    args.update(kw)
    return asyncio.run(research_long_strategy_backtest(**args))


def test_sync_route_refuses_engine_strategies():
    out = _sync_backtest(combination_id="CANDLE12_V2")
    assert out["status"] == "ERROR"
    assert out["error_code"] == "engine_sync_unsupported"


def test_sync_route_reports_a_failed_cell(monkeypatch):
    class _Svc:
        async def strategy_matrix(self, **_kw):
            return {"status": "NOT_FOUND", "reason": "Unknown combination", "rows": []}

    monkeypatch.setattr("app.research.service.get_bos_research_service", lambda: _Svc())
    out = _sync_backtest()
    assert out["status"] == "NOT_FOUND"
    assert "Unknown combination" in out["error"]


def test_child_stderr_goes_to_a_file_that_is_read_and_removed():
    import os
    import subprocess
    import sys

    fd, path = _child_stderr_file("bt_test_")
    try:
        # More than a pipe buffer: with stderr=PIPE read after exit this would block.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('x' * 300000); sys.stderr.write('END')"],
            stdout=subprocess.DEVNULL,
            stderr=fd,
        )
    finally:
        os.close(fd)
    assert proc.wait(timeout=20) == 0
    assert _read_child_stderr(path).endswith("END")
    _unlink_child_file(path, retry_seconds=3.0)
    assert not os.path.exists(path)
