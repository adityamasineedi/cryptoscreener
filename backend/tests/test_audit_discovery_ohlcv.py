"""Database-free unit coverage for the discovery OHLCV audit."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_discovery_ohlcv.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("audit_discovery_ohlcv_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candles(hours: list[int]) -> list[dict[str, Any]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows: list[dict[str, Any]] = []
    for hour in hours:
        open_time = start + timedelta(hours=hour)
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
    source = {
        "BTCUSDT": _candles([0, 1, 2, 5]),
        "ETHUSDT": _candles([0, 1]),
        "SOLUSDT": [],
    }
    loads: list[tuple[str, str]] = []

    async def fake_loader(symbol: str, timeframe: str) -> list[dict[str, Any]]:
        loads.append((symbol, timeframe))
        return source[symbol]

    report = await script.audit_existing_ohlcv(candle_loader=fake_loader)

    assert loads == [(symbol, "1h") for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    assert report["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert report["read_only"] is True

    btc = report["by_symbol"]["BTCUSDT"]
    assert btc["rows"] == 4
    assert btc["first_open_time"] == "2025-01-01T00:00:00+00:00"
    assert btc["last_open_time"] == "2025-01-01T05:00:00+00:00"
    assert btc["gap_count"] == 1
    assert btc["missing_bars"] == 2
    assert btc["gaps"] == [
        {
            "previous_open_time": "2025-01-01T02:00:00+00:00",
            "next_open_time": "2025-01-01T05:00:00+00:00",
            "missing_bars": 2,
        }
    ]

    eth = report["by_symbol"]["ETHUSDT"]
    assert eth["rows"] == 2
    assert eth["gap_count"] == 0
    assert eth["gaps"] == []

    sol = report["by_symbol"]["SOLUSDT"]
    assert sol["rows"] == 0
    assert sol["first_open_time"] is None
    assert sol["last_open_time"] is None
    assert sol["gaps"] == []


@pytest.mark.asyncio
async def test_audit_stops_when_read_path_fails() -> None:
    script = _load_script()

    async def unavailable(symbol: str, timeframe: str) -> list[dict[str, Any]]:
        raise RuntimeError("DATABASE unavailable")

    with pytest.raises(RuntimeError, match="read path failed for BTCUSDT"):
        await script.audit_existing_ohlcv(candle_loader=unavailable)
