from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.models.ohlcv import Candle
from app.models.schemas import DataStatus
from app.services.ohlcv_store import ohlcv_store
from app.services.volume_change import compute_volume_changes


def test_volume_change_waiting_without_two_windows():
    ohlcv_store.remove_symbol("VOLCHG1")
    out = compute_volume_changes("VOLCHG1")
    for k in (
        "volume_change_1h",
        "volume_change_4h",
        "volume_change_24h",
        "volume_change_7d",
    ):
        assert out[k].status == DataStatus.WAITING
        assert out[k].value is None


def test_volume_change_uses_distinct_windows():
    sym = "VOLCHG2"
    ohlcv_store.remove_symbol(sym)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candles = []
    for i in range(48, 0, -1):
        t = now - timedelta(hours=i)
        vol = 20.0 if i <= 24 else 10.0
        candles.append(
            Candle(
                symbol=sym,
                timeframe="1h",
                open_time=t,
                close_time=t + timedelta(hours=1) - timedelta(milliseconds=1),
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=vol,
                is_closed=True,
                timestamp=t,
                source="test",
            )
        )
    asyncio.run(ohlcv_store.ingest_history(candles))
    # Use the same aligned `now` so windows match candle open_times exactly
    out = compute_volume_changes(sym, now=now)
    fv = out["volume_change_24h"]
    assert fv.status == DataStatus.LIVE
    assert fv.value is not None
    assert abs(fv.value - 100.0) < 1e-6
    assert "non-overlapping" in (fv.methodology or "")
