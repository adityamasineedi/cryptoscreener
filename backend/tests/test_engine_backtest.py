"""Ranked engine strategies on the Strategy Backtest screen (research only)."""

import asyncio

import pytest

from app.research.backtest_job import BacktestJobService
from app.research.backtest_ui_config import BacktestConfigError
from app.research.engine_backtest import (
    ENGINE_STRATEGIES,
    build_engine_row,
    engine_request_error,
    engine_strategy_id,
)
from app.research.trade_fees import enrich_trade_execution


def test_engine_ids_resolve_case_insensitively_and_leave_combos_alone():
    assert engine_strategy_id("candle12_v2") == "CANDLE12_V2"
    # validate_backtest_request upper-cases ids; the OSE ids are mixed case.
    assert engine_strategy_id("OSE_A_CORRECTED_BASELINE") == "OSE_A_corrected_baseline"
    assert engine_strategy_id("COMBO_02") is None
    assert engine_strategy_id(None) is None


def test_engine_request_limits():
    assert engine_request_error("CANDLE12_V2", "LONG", ["1h"]) is None
    assert engine_request_error("OSE_B_acceptance", "SHORT", ["15m", "1h"]) is None
    assert "LONG only" in engine_request_error("LARGE_CAP_SWEEP_CHOCH", "SHORT", ["1h"])
    assert "4h" in engine_request_error("CANDLE12_V2", "LONG", ["1h", "4h"])
    assert "5m" in engine_request_error("OSE_A_corrected_baseline", "LONG", ["5m"])


def test_job_start_rejects_an_unsupported_engine_request():
    svc = BacktestJobService()
    with pytest.raises(BacktestConfigError) as err:
        asyncio.run(
            svc.start(
                symbols=["BTCUSDT"],
                timeframes=["4h"],
                direction="LONG",
                combination_id="CANDLE12_V2",
            )
        )
    assert err.value.code == "engine_request_unsupported"
    assert svc.status()["status"] == "idle"


def _trade(entry, stop, exit_px, outcome, exit_time):
    return enrich_trade_execution(
        {
            "direction": "LONG",
            "entry_price": entry,
            "stop_price": stop,
            "exit_price": exit_px,
            "outcome": outcome,
            "signal_time": "2026-03-01T00:00:00+00:00",
            "exit_time": exit_time,
        },
        risk_usd=20.0,
        taker_fee=0.0004,
        maker_fee=0.0002,
        leverage=2.0,
        account_equity=1000.0,
    )


def test_engine_row_sums_the_enriched_trades():
    trades = [
        _trade(100.0, 98.0, 104.0, "TP1", "2026-03-02T00:00:00+00:00"),
        _trade(100.0, 98.0, 98.0, "SL", "2026-03-03T00:00:00+00:00"),
    ]
    row = build_engine_row(
        {"trades": trades, "open_trades_at_end": 1, "execution_model": "x", "status": "OK"},
        strategy_id="CANDLE12_V2",
        direction="LONG",
        symbol="BTCUSDT",
        timeframe="1h",
        risk_usd=20.0,
        principal=1000.0,
        leverage=2.0,
        bars_loaded=1200,
        warmup_bars=1000,
        candle_source="postgresql_ohlcv",
        period_start=None,
        period_end=None,
    )
    assert row["combination_id"] == "CANDLE12_V2"
    assert row["sample_size"] == 2
    assert row["bars_evaluated"] == 200
    assert row["tp1_hits"] == 1 and row["sl_hits"] == 1
    assert row["pnl_usd"] == pytest.approx(sum(t["gross_pnl_usd"] for t in trades))
    assert row["pnl_usd_net"] == pytest.approx(sum(t["net_pnl_usd"] for t in trades))
    assert row["fees_usd"] == pytest.approx(sum(t["fee_total_usd"] for t in trades))
    assert row["win_rate_net"] == 0.5
    assert row["open_trades_at_end"] == 1
    assert len(row["equity_curve_usd_net"]) == 2


def test_engine_row_counts_ose_target_and_stop_outcomes():
    # OSE outcomes are lower-case target / stop / timeout; timeout is neither a TP nor a stop.
    trades = [
        _trade(100.0, 98.0, 104.0, "target", "2026-03-02T00:00:00+00:00"),
        _trade(100.0, 98.0, 98.0, "stop", "2026-03-03T00:00:00+00:00"),
        _trade(100.0, 98.0, 98.0, "stop", "2026-03-04T00:00:00+00:00"),
        _trade(100.0, 98.0, 100.5, "timeout", "2026-03-05T00:00:00+00:00"),
    ]
    row = build_engine_row(
        {"trades": trades, "open_trades_at_end": 0, "execution_model": "x", "status": "OK"},
        strategy_id="OSE_D_faster_confirmation",
        direction="LONG",
        symbol="BTCUSDT",
        timeframe="1h",
        risk_usd=20.0,
        principal=1000.0,
        leverage=2.0,
        bars_loaded=1200,
        warmup_bars=1000,
        candle_source="postgresql_ohlcv",
        period_start=None,
        period_end=None,
    )
    assert row["sample_size"] == 4
    assert row["tp1_hits"] == 1
    assert row["sl_hits"] == 2
    assert row["tp1_hit_rate"] == pytest.approx(0.25)
    assert row["sl_rate"] == pytest.approx(0.5)
    # The outcomes themselves are not renamed.
    assert [t["outcome"] for t in row["trades"]] == ["target", "stop", "stop", "timeout"]


def test_engine_row_tp_and_sl_counts_for_other_engines_are_unchanged():
    trades = [
        _trade(100.0, 98.0, 104.0, "TP1", "2026-03-02T00:00:00+00:00"),
        _trade(100.0, 98.0, 106.0, "TP2", "2026-03-03T00:00:00+00:00"),
        _trade(100.0, 98.0, 98.0, "SL", "2026-03-04T00:00:00+00:00"),
        _trade(100.0, 98.0, 98.0, "STOP_LOSS", "2026-03-05T00:00:00+00:00"),
    ]
    row = build_engine_row(
        {"trades": trades, "status": "OK"},
        strategy_id="CANDLE12_V2",
        direction="LONG",
        symbol="BTCUSDT",
        timeframe="1h",
        risk_usd=20.0,
        principal=1000.0,
        leverage=2.0,
        bars_loaded=1200,
        warmup_bars=1000,
        candle_source=None,
        period_start=None,
        period_end=None,
    )
    assert row["tp1_hits"] == 2
    assert row["sl_hits"] == 2


def test_registry_covers_the_ranked_engine_ids():
    for sid in (
        "CANDLE12_V2",
        "LARGE_CAP_SWEEP_CHOCH",
        "SMALL_CAP_VOLUME_BOS",
        "OSE_A_corrected_baseline",
        "OSE_B_acceptance",
        "OSE_D_faster_confirmation",
    ):
        assert "1h" in ENGINE_STRATEGIES[sid]["timeframes"]
        assert "LONG" in ENGINE_STRATEGIES[sid]["directions"]
