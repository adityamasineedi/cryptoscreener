from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.models.ohlcv import Candle
from app.models.schemas import DataStatus
from app.services.historical_performance import compute_performance_sync
from app.services.ohlcv_store import ohlcv_store


def test_performance_waiting_without_history():
    ohlcv_store.remove_symbol("PERFTEST")
    out = compute_performance_sync("PERFTEST", price_now=100.0)
    assert out["performance_1d"].status == DataStatus.WAITING
    assert out["performance_7d"].value is None
    assert out["performance_30d"].value is None


def test_performance_formula_from_ohlcv():
    sym = "PERFFORM"
    ohlcv_store.remove_symbol(sym)
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    candles = []
    for i in range(40, -1, -1):
        t = now - timedelta(days=i)
        close = 100.0 + (40 - i)
        candles.append(
            Candle(
                symbol=sym,
                timeframe="1d",
                open_time=t,
                close_time=t + timedelta(days=1) - timedelta(milliseconds=1),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=10.0,
                is_closed=True,
                timestamp=t,
                source="test",
            )
        )
    asyncio.run(ohlcv_store.ingest_history(candles))
    out = compute_performance_sync(sym, price_now=140.0)
    assert out["performance_1d"].status == DataStatus.HISTORICAL
    assert out["performance_1d"].value is not None
    assert out["performance_1d"].methodology is not None
    assert "price_now" in out["performance_1d"].methodology
