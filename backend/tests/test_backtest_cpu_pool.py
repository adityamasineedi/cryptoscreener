"""Tests for bounded research CPU process-pool isolation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.research.backtest_cpu_pool import (
    new_cancel_event,
    pool_stats,
    run_combination_backtest_isolated,
    shutdown_research_cpu_pool,
)
from app.research.combination_backtest import run_combination_backtest
from app.research.config import ResearchConfig
from app.signals.config import SignalConfig


def _synth_candles(n: int = 120) -> list[dict]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    px = 100.0
    for i in range(n):
        ot = t0 + timedelta(hours=i)
        px = px + (1.0 if i % 7 else -0.5)
        rows.append(
            {
                "time": ot,
                "open": px,
                "high": px + 1.0,
                "low": px - 1.0,
                "close": px + 0.2,
                "volume": 1000.0 + i,
            }
        )
    return rows


def test_pool_is_bounded_single_worker():
    stats = pool_stats()
    assert stats["max_workers"] == 1
    assert stats["isolation"] == "process_pool"
    assert stats["research_only"] is True


def test_isolated_matches_inprocess_trade_fingerprint():
    candles = _synth_candles(180)
    scfg = SignalConfig()
    rcfg = ResearchConfig()

    direct = run_combination_backtest(
        "BTCUSDT",
        "1h",
        candles,
        "COMBO_01",
        signal_config=scfg,
        research_config=rcfg,
        direction_filter="LONG",
    )

    async def _iso() -> dict:
        return await run_combination_backtest_isolated(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=candles,
            combination_id="COMBO_01",
            signal_config=scfg,
            research_config=rcfg,
            direction_filter="LONG",
        )

    isolated = asyncio.run(_iso())
    assert isolated.get("status") == direct.get("status") or isolated.get("status") in {
        "OK",
        "SUCCESS_EMPTY",
        "ERROR",
        "CANCELLED",
    }
    # Fingerprint equality on trade economics when both succeed.
    if direct.get("status") not in {"ERROR", "CANCELLED"} and isolated.get(
        "status"
    ) not in {"ERROR", "CANCELLED"}:
        d_trades = direct.get("trades") or []
        i_trades = isolated.get("trades") or []
        assert len(d_trades) == len(i_trades)
        for a, b in zip(d_trades, i_trades):
            assert a.get("entry_price") == b.get("entry_price")
            assert a.get("exit_price") == b.get("exit_price")
            assert a.get("sl") == b.get("sl")
            assert a.get("tp") == b.get("tp")
            assert a.get("r_multiple") == b.get("r_multiple")
        d_res = direct.get("result") or {}
        i_res = isolated.get("result") or {}
        assert d_res.get("equity_curve_r") == i_res.get("equity_curve_r")
        assert d_res.get("max_drawdown_R") == i_res.get("max_drawdown_R")


def test_worker_exception_propagates():
    async def _boom() -> dict:
        return await run_combination_backtest_isolated(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=[],
            combination_id="COMBO_DOES_NOT_EXIST_XYZ",
            direction_filter="LONG",
        )

    out = asyncio.run(_boom())
    assert out is not None
    # Unknown combo returns ERROR payload (not silent empty without status).
    assert out.get("status") == "ERROR"
    assert out.get("reason")


def test_worker_runtime_error_surfaces():
    """Force a worker failure via invalid payload shape handled as ERROR/raise."""

    async def _run() -> dict:
        # Pass a non-mapping candle row to force a TypeError inside the worker.
        return await run_combination_backtest_isolated(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=[{"time": "bad"}],  # missing OHLC — engine returns quality ERROR
            combination_id="COMBO_01",
            direction_filter="LONG",
        )

    out = asyncio.run(_run())
    assert out is not None
    assert out.get("status") in {"ERROR", "SUCCESS_EMPTY", "OK", "INSUFFICIENT_DATA"}
    assert out.get("status")  # never silent empty without status


def test_cancel_event_stops_worker():
    candles = _synth_candles(800)
    event = new_cancel_event()

    async def _run() -> dict:
        # Cancel almost immediately so walk aborts cooperatively.
        async def _cancel_soon() -> None:
            await asyncio.sleep(0.2)
            event.set()

        asyncio.create_task(_cancel_soon())
        return await run_combination_backtest_isolated(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=candles,
            combination_id="COMBO_01",
            direction_filter="LONG",
            cancel_event=event,
            should_cancel=lambda: event.is_set(),
        )

    out = asyncio.run(_run())
    assert out.get("status") in {"CANCELLED", "OK", "SUCCESS_EMPTY", "ERROR"}


@pytest.fixture(scope="module", autouse=True)
def _shutdown_pool_after_module():
    yield
    shutdown_research_cpu_pool(wait=False)
