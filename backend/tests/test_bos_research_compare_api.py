"""BOS Combination Research compare API / query consistency tests.

These cover presentation-layer and query-layer bugs only.
They do not modify live signal logic or Candle-1/Candle-2 V2 result files.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.klines import normalize_timeframe
from app.research.bos_combinations import COMBINATIONS
from app.research.combination_backtest import compare_combinations, run_combination_backtest
from app.research.config import ResearchConfig, count_parameters_tested
from app.research.metrics import compute_metrics, max_drawdown_r
from app.research.query_utils import (
    DATASET_ID,
    OTHER_DATASET_ID,
    normalize_research_symbol,
    normalize_research_timeframe,
    resolve_date_bounds,
    slice_candles_for_research,
)
from app.research.schemas import ResearchTrade
from app.research.service import BosResearchService
from app.signals.config import SignalConfig


def _ts(i: int) -> datetime:
    return datetime(2026, 9, 20, tzinfo=timezone.utc) + timedelta(minutes=15 * i)


def candle(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {"time": _ts(i), "open": o, "high": h, "low": l, "close": c, "volume": v}


def make_series(n: int = 200) -> list[dict]:
    candles: list[dict] = []
    price = 100.0
    for i in range(n):
        wave = 2.0 * ((i % 10) / 10.0)
        o = price
        c = price + 0.4 + (0.2 if i % 7 == 0 else 0.0)
        h = max(o, c) + 0.8 + wave
        l = min(o, c) - 0.6
        vol = 3000.0 if i % 19 == 0 else 1000.0
        candles.append(candle(i, o, h, l, c, vol))
        price = c
    return candles


class TestNormalization:
    def test_symbol_normalization(self):
        assert normalize_research_symbol("btcusdt") == "BTCUSDT"
        assert normalize_research_symbol(" BTCUSDT ") == "BTCUSDT"

    def test_timeframe_normalization_variants(self):
        assert normalize_research_timeframe("15M") == "15m"
        assert normalize_research_timeframe("15m") == "15m"
        assert normalize_research_timeframe("15min") == "15m"
        assert normalize_research_timeframe("15-minute") == "15m"
        assert normalize_timeframe("15M") == "15m"
        assert normalize_timeframe("15min") == "15m"


class TestDateBounds:
    def test_end_date_inclusivity_to_exclusive(self):
        bounds = resolve_date_bounds("2026-09-27", "2026-10-01")
        assert bounds["start"].isoformat() == "2026-09-27T00:00:00+00:00"
        assert bounds["end_exclusive"].isoformat() == "2026-10-02T00:00:00+00:00"
        assert bounds["convention"] == "start <= timestamp < end_exclusive"
        assert bounds["timezone"] == "UTC"

    def test_empty_date_range_degenerate(self):
        bounds = resolve_date_bounds("2026-10-01", "2026-09-27")
        assert bounds["end_exclusive"] <= bounds["start"]

    def test_slice_respects_half_open_and_warmup(self):
        series = make_series(96 * 5)  # ~5 days of 15m from 2026-09-20
        start = datetime(2026, 9, 27, tzinfo=timezone.utc)
        end_excl = datetime(2026, 10, 2, tzinfo=timezone.utc)
        window, eval_start, meta = slice_candles_for_research(
            series, start=start, end_exclusive=end_excl, warmup_bars=10
        )
        assert eval_start == 10 or meta["warmup_bars_applied"] <= 10
        assert meta["eval_bars"] >= 0
        # No candle at/after end_exclusive
        for c in window[eval_start:]:
            assert c["time"] < end_excl


class TestEmptyMetricsNull:
    def test_zero_sample_max_drawdown_is_null(self):
        result = compute_metrics(
            [],
            combination_id="COMBO_01",
            description="test",
            symbol="BTCUSDT",
            timeframe="15m",
        )
        assert result.sample_size == 0
        assert result.max_drawdown_R is None
        assert result.average_R is None
        assert result.expectancy_R is None
        assert result.profit_factor is None
        assert result.tp1_hit_rate is None
        assert result.average_MAE_R is None
        assert result.average_MFE_R is None

    def test_max_drawdown_helper_empty(self):
        dd, curve = max_drawdown_r([])
        assert dd is None
        assert curve == []


class TestCompareMetadata:
    def test_all_combinations_produce_rows(self):
        series = make_series(160)
        cmp = compare_combinations(
            "BTCUSDT",
            "15M",
            series,
            research_config=ResearchConfig(min_bars=40),
            signal_config=SignalConfig(),
        )
        n = len(COMBINATIONS)
        assert cmp["combinations_tested"] == n
        assert len(cmp["rows"]) == n
        assert cmp["dataset_id"] == DATASET_ID
        assert cmp["not_dataset"] == OTHER_DATASET_ID
        assert set(r["combination_id"] for r in cmp["rows"]) == set(COMBINATIONS)

    def test_parameters_tested_meaning(self):
        assert count_parameters_tested({}) == 1
        assert count_parameters_tested(None) == 1
        assert count_parameters_tested({"min_rr": [2.0, 2.5], "min_rvol": [1.5]}) == 2

    def test_empty_combo_row_metrics_null_not_zero(self):
        series = make_series(80)
        cmp = compare_combinations(
            "BTCUSDT",
            "15m",
            series,
            ["COMBO_08"],
            research_config=ResearchConfig(min_bars=40),
        )
        row = cmp["rows"][0]
        if row["sample_size"] == 0:
            assert row["max_drawdown_R"] is None
            assert row["average_R"] is None
            assert row["profit_factor"] is None


class TestServiceCompareAsync:
    @pytest.mark.asyncio
    async def test_compare_uses_date_filter_and_dataset_label(self):
        series = make_series(300)

        async def fake_load(*_a, **_k):
            return series, 50, {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "start_date": "2026-09-27",
                "end_date_inclusive": "2026-10-01",
                "start_utc": "2026-09-27T00:00:00+00:00",
                "end_exclusive_utc": "2026-10-02T00:00:00+00:00",
                "convention": "start <= timestamp < end_exclusive",
                "timezone": "UTC",
                "ui_dates_interpreted_as": "UTC calendar days",
                "candle_source": "postgresql_ohlcv",
                "candles_loaded": len(series),
                "warmup_bars_applied": 50,
                "period_start": series[50]["time"].isoformat(),
                "period_end": series[-1]["time"].isoformat(),
                "limit": 500,
            }

        svc = BosResearchService()
        with patch("app.research.service._load_research_candles", new=AsyncMock(side_effect=fake_load)):
            payload = await svc.compare(
                symbol="btcusdt",
                timeframe="15M",
                start_date="2026-09-27",
                end_date="2026-10-01",
                limit=500,
            )
        assert payload["dataset_id"] == DATASET_ID
        assert payload["not_dataset"] == OTHER_DATASET_ID
        n = len(COMBINATIONS)
        assert payload["combinations_tested"] == n
        assert payload["parameter_variants_tested"] == 1
        assert payload["parameters_tested"] == 1
        assert "parameter grid" in (payload.get("parameters_tested_meaning") or "").lower()
        assert payload["date_filter"]["end_exclusive_utc"] == "2026-10-02T00:00:00+00:00"
        assert payload["timezone"] == "UTC"
        assert payload["status"] in ("SUCCESS_WITH_DATA", "SUCCESS_EMPTY")
        assert len(payload["rows"]) == n
        if payload["status"] == "SUCCESS_EMPTY":
            assert payload["empty_reason"] == "NO QUALIFYING DATA FOR SELECTED FILTERS"
            for row in payload["rows"]:
                assert row["max_drawdown_R"] is None

    @pytest.mark.asyncio
    async def test_empty_window_status(self):
        async def fake_load(*_a, **_k):
            return [], 0, {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "start_date": "2099-01-01",
                "end_date_inclusive": "2099-01-02",
                "start_utc": "2099-01-01T00:00:00+00:00",
                "end_exclusive_utc": "2099-01-03T00:00:00+00:00",
                "convention": "start <= timestamp < end_exclusive",
                "timezone": "UTC",
                "ui_dates_interpreted_as": "UTC calendar days",
                "candle_source": "postgresql_ohlcv",
                "candles_loaded": 0,
                "warmup_bars_applied": 0,
                "period_start": None,
                "period_end": None,
                "limit": 500,
            }

        svc = BosResearchService()
        with patch("app.research.service._load_research_candles", new=AsyncMock(side_effect=fake_load)):
            payload = await svc.compare(
                symbol="BTCUSDT",
                timeframe="15m",
                start_date="2099-01-01",
                end_date="2099-01-02",
            )
        assert payload["status"] == "SUCCESS_EMPTY"
        assert payload["closed_trades_sample"] == 0
        for row in payload["rows"]:
            assert row["sample_size"] == 0
            assert row["max_drawdown_R"] is None

    @pytest.mark.asyncio
    async def test_datasets_not_mixed(self):
        series = make_series(120)

        async def fake_load(*_a, **_k):
            return series, 0, {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "start_date": None,
                "end_date_inclusive": None,
                "start_utc": None,
                "end_exclusive_utc": None,
                "convention": "start <= timestamp < end_exclusive",
                "timezone": "UTC",
                "ui_dates_interpreted_as": "UTC calendar days",
                "candle_source": "postgresql_ohlcv",
                "candles_loaded": len(series),
                "warmup_bars_applied": 0,
                "period_start": series[0]["time"].isoformat(),
                "period_end": series[-1]["time"].isoformat(),
                "limit": 500,
            }

        svc = BosResearchService()
        with patch("app.research.service._load_research_candles", new=AsyncMock(side_effect=fake_load)):
            payload = await svc.compare(symbol="BTCUSDT", timeframe="15m")
        assert payload["dataset_id"] == "bos_combination_research"
        assert payload["not_dataset"] == "candle12_v2"
        assert "candle12" not in str(payload.get("dataset", "")).lower() or "not" in str(
            payload.get("disclaimer", "")
        ).lower()


class TestMultipleTestingTruth:
    @pytest.mark.asyncio
    async def test_flag_uses_combinations_and_parameter_variants(self):
        series = make_series(120)

        async def fake_load(*_a, **_k):
            return series, 0, {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "start_date": None,
                "end_date_inclusive": None,
                "start_utc": None,
                "end_exclusive_utc": None,
                "convention": "start <= timestamp < end_exclusive",
                "timezone": "UTC",
                "ui_dates_interpreted_as": "UTC calendar days",
                "candle_source": "postgresql_ohlcv",
                "candles_loaded": len(series),
                "warmup_bars_applied": 0,
                "period_start": None,
                "period_end": None,
                "limit": 500,
            }

        svc = BosResearchService()
        with patch("app.research.service._load_research_candles", new=AsyncMock(side_effect=fake_load)):
            payload = await svc.compare(symbol="BTCUSDT", timeframe="15m")
        detail = payload["multiple_testing_detail"]
        n = len(COMBINATIONS)
        assert detail["combinations_tested"] == n
        assert detail["parameter_variants_tested"] == 1
        assert detail["configurations_explored"] == n
        assert payload["multiple_testing_flag"] == "MULTIPLE_TESTING_RISK"


class TestUtcLocalNotConfused:
    def test_ui_local_looking_string_still_utc_calendar(self):
        # Date-only strings must not be treated as local wall-clock.
        bounds = resolve_date_bounds("2026-09-27", "2026-10-01")
        assert bounds["start"].tzinfo == timezone.utc
        assert bounds["ui_dates_interpreted_as"] == "UTC calendar days"


class TestTradeFilterNotFabricated:
    def test_open_trades_excluded_from_metrics(self):
        trades = [
            ResearchTrade(
                symbol="BTCUSDT",
                timeframe="15m",
                combination_id="COMBO_01",
                entry_index=10,
                signal_time=_ts(10).isoformat(),
                direction="LONG",
                entry_price=100.0,
                stop_price=99.0,
                tp1=None,
                tp2=None,
                tp3=None,
                rr=None,
                outcome="OPEN",
                r_multiple=None,
            )
        ]
        result = compute_metrics(
            trades,
            combination_id="COMBO_01",
            description="t",
        )
        assert result.sample_size == 0
        assert result.max_drawdown_R is None
