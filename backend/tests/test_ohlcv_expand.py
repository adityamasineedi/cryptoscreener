"""Unit tests for OHLCV history expand helpers."""

from __future__ import annotations

import asyncio

from app.research.ohlcv_expand import (
    ALLOWED_TFS,
    ExpandJob,
    OhlcvExpandService,
    parse_until_ms,
)


def test_parse_until_ms_utc_midnight():
    assert parse_until_ms("2023-01-01") == 1672531200000
    assert parse_until_ms("2023-01-01T12:00:00Z") == 1672531200000


def test_allowed_timeframes_include_backtest_defaults():
    assert "15m" in ALLOWED_TFS
    assert "1h" in ALLOWED_TFS


def test_start_rejects_empty_symbols():
    svc = OhlcvExpandService()

    async def _run() -> None:
        try:
            await svc.start(symbols=[], timeframes=["15m"], until="2023-01-01")
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "symbol" in str(exc).lower()

    asyncio.run(_run())


def test_expand_job_pct_includes_cell_fraction():
    job = ExpandJob(
        job_id="t1",
        status="running",
        total_cells=4,
        done_cells=1,
        cell_fraction=0.5,
    )
    d = job.to_dict()
    # 1.5 / 4 = 37.5%
    assert d["pct"] == 37.5
    assert d["cell_fraction"] == 0.5
    assert d["done_cells"] == 1


def test_expand_job_pct_visible_at_cell_start():
    """Starting a cell with fraction 0.1 must not round-trip as a blank 0% bar."""
    job = ExpandJob(
        job_id="t2",
        status="running",
        total_cells=6,
        done_cells=0,
        cell_fraction=0.1,
        current="BTCUSDT 15m · starting…",
    )
    d = job.to_dict()
    assert d["pct"] == round(100.0 * 0.1 / 6, 1)  # ~1.7
    assert d["pct"] >= 1.0
    assert "starting" in (d["current"] or "")
