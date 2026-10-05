"""UI calendar backtests bypass Parquet cache (direct Postgres path)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_load_research_candles_skips_parquet_when_disabled():
    from app.research.service import _load_research_candles

    candles = [
        {
            "time": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 10.0,
        },
        {
            "time": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "open": 1.05,
            "high": 1.2,
            "low": 1.0,
            "close": 1.1,
            "volume": 12.0,
        },
    ]

    mock_db = MagicMock()
    mock_db.enabled = True
    mock_db.engine = object()

    with (
        patch("app.services.database.db_manager", mock_db),
        patch(
            "app.research.postgres_ohlcv.load_ohlcv_series_range",
            new=AsyncMock(return_value=candles),
        ) as range_mock,
        patch(
            "app.research.data_cache.cache_manager.get_research_cache"
        ) as cache_factory,
    ):
        window, eval_start, meta = await _load_research_candles(
            "BTCUSDT",
            "1h",
            start_date="2026-01-01",
            end_date="2026-01-31",
            warmup_bars=0,
            use_research_cache=False,
        )

    cache_factory.assert_not_called()
    range_mock.assert_awaited()
    assert meta["candle_source"] == "postgresql_ohlcv"
    assert len(window) == 2
    assert eval_start >= 0


def test_strategy_matrix_defaults_use_research_cache_false():
    import inspect

    from app.research.service import BosResearchService

    params = inspect.signature(BosResearchService.strategy_matrix).parameters
    assert "use_research_cache" in params
    assert params["use_research_cache"].default is False
