"""Database-free unit coverage for the 15m and 4h discovery OHLCV audit."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_discovery_ohlcv_mtf.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("audit_discovery_ohlcv_mtf_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candles(step: timedelta, offsets: list[int]) -> list[dict[str, Any]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, Any]] = []
    for offset in offsets:
        open_time = start + step * offset
        rows.append(
            {
                "time": open_time,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
            }
        )
    return rows


@pytest.mark.asyncio
async def test_audit_reports_rows_span_and_gaps_from_fake_candles() -> None:
    script = _load_script()
    step_15m = timedelta(minutes=15)
    step_4h = timedelta(hours=4)
    source = {
        ("BTCUSDT", "15m"): _candles(step_15m, [0, 1, 2, 5]),
        ("ETHUSDT", "15m"): _candles(step_15m, [0, 1]),
        ("SOLUSDT", "15m"): [],
        ("BTCUSDT", "4h"): _candles(step_4h, [0, 1, 3]),
        ("ETHUSDT", "4h"): _candles(step_4h, [0, 1]),
        ("SOLUSDT", "4h"): [],
    }
    loads: list[tuple[str, str]] = []

    async def fake_loader(symbol: str, timeframe: str) -> list[dict[str, Any]]:
        loads.append((symbol, timeframe))
        return source[(symbol, timeframe)]

    report = await script.audit_existing_ohlcv(candle_loader=fake_loader)

    assert loads == [
        (symbol, timeframe)
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        for timeframe in ("15m", "4h")
    ]
    assert report["timeframes"] == ["15m", "4h"]
    assert report["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert report["read_only"] is True

    btc_15m = report["by_symbol"]["BTCUSDT"]["15m"]
    assert btc_15m["rows"] == 4
    assert btc_15m["first_open_time"] == "2025-01-01T00:00:00+00:00"
    assert btc_15m["last_open_time"] == "2025-01-01T01:15:00+00:00"
    assert btc_15m["gap_count"] == 1
    assert btc_15m["missing_bars"] == 2
    assert btc_15m["gaps"] == [
        {
            "previous_open_time": "2025-01-01T00:30:00+00:00",
            "next_open_time": "2025-01-01T01:15:00+00:00",
            "missing_bars": 2,
        }
    ]

    eth_15m = report["by_symbol"]["ETHUSDT"]["15m"]
    assert eth_15m["rows"] == 2
    assert eth_15m["gap_count"] == 0
    assert eth_15m["gaps"] == []

    sol_15m = report["by_symbol"]["SOLUSDT"]["15m"]
    assert sol_15m["rows"] == 0
    assert sol_15m["first_open_time"] is None
    assert sol_15m["last_open_time"] is None
    assert sol_15m["gaps"] == []

    btc_4h = report["by_symbol"]["BTCUSDT"]["4h"]
    assert btc_4h["rows"] == 3
    assert btc_4h["first_open_time"] == "2025-01-01T00:00:00+00:00"
    assert btc_4h["last_open_time"] == "2025-01-01T12:00:00+00:00"
    assert btc_4h["gap_count"] == 1
    assert btc_4h["missing_bars"] == 1
    assert btc_4h["gaps"] == [
        {
            "previous_open_time": "2025-01-01T04:00:00+00:00",
            "next_open_time": "2025-01-01T12:00:00+00:00",
            "missing_bars": 1,
        }
    ]

    eth_4h = report["by_symbol"]["ETHUSDT"]["4h"]
    assert eth_4h["rows"] == 2
    assert eth_4h["gap_count"] == 0
    assert eth_4h["gaps"] == []

    sol_4h = report["by_symbol"]["SOLUSDT"]["4h"]
    assert sol_4h["rows"] == 0
    assert sol_4h["first_open_time"] is None
    assert sol_4h["last_open_time"] is None
    assert sol_4h["gaps"] == []


@pytest.mark.asyncio
async def test_audit_stops_when_read_path_fails() -> None:
    script = _load_script()

    async def unavailable(symbol: str, timeframe: str) -> list[dict[str, Any]]:
        raise RuntimeError("DATABASE unavailable")

    with pytest.raises(RuntimeError, match="15m candle read path failed for BTCUSDT"):
        await script.audit_existing_ohlcv(candle_loader=unavailable)
