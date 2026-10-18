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


def test_registry_pins_every_engine_id_and_family():
    families = {sid: spec["family"] for sid, spec in ENGINE_STRATEGIES.items()}
    assert families == {
        "CANDLE12_V2": "candle12",
        "CANDLE12_V3_EXEC_FIX": "candle12_v3",
        "LARGE_CAP_SWEEP_CHOCH": "multicap",
        "MID_CAP_FVG_DISCOUNT": "multicap",
        "SMALL_CAP_VOLUME_BOS": "multicap",
        "OSE_A_corrected_baseline": "ohlcv_structure_experiment",
        "OSE_B_acceptance": "ohlcv_structure_experiment",
        "OSE_C_retest": "ohlcv_structure_experiment",
        "OSE_D_faster_confirmation": "ohlcv_structure_experiment",
        "DAILY_TREND_01_SMA200": "daily_trend",
        "DAILY_TREND_02_EMA200": "daily_trend",
        "DAILY_TREND_03_SMA50": "daily_trend",
        "DAILY_TREND_04_MA_SLOPE": "daily_trend",
        "DAILY_TREND_05_GOLDEN_CROSS": "daily_trend",
        "DAILY_TREND_06_WEINSTEIN": "daily_trend",
        "DAILY_TREND_09_DONCHIAN20_WEINSTEIN": "daily_trend",
        "DAILY_TREND_11_ADX_DMI": "daily_trend",
        "DAILY_TREND_12_ICHIMOKU": "daily_trend",
        "DAILY_TREND_14_ROC90": "daily_trend",
        "DAILY_TREND_15_ROC30": "daily_trend",
        "DAILY_TREND_19_COMBINED": "daily_trend",
        "DAILY_TREND_ROTATION": "daily_trend",
        "DAILY_TREND_STATIC12": "daily_trend",
        "MTF_4H_16_BREAKOUT252": "daily_trend",
    }
    # Only the daily trend books carry a BOTH row (the 4h trend book is LONG only); nothing else
    # changed direction.
    both = {sid for sid, spec in ENGINE_STRATEGIES.items() if "BOTH" in spec["directions"]}
    assert both == {sid for sid, fam in families.items() if fam == "daily_trend"} - {"MTF_4H_16_BREAKOUT252"}
    assert ENGINE_STRATEGIES["MTF_4H_16_BREAKOUT252"]["directions"] == ("LONG",)
    assert engine_strategy_id("daily_trend_01_sma200") == "DAILY_TREND_01_SMA200"
    assert "1d" in engine_request_error("DAILY_TREND_01_SMA200", "LONG", ["1h"])
    assert engine_request_error("CANDLE12_V2", "BOTH", ["1h"]) == "CANDLE12_V2 runs LONG or SHORT only"


# ---------------------------------------------------------------- audit #19: OSE clock labels


def _ose_candles(n: int = 120, price: float = 101.0) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {"time": t0 + timedelta(hours=i), "open": price, "high": price + 0.2, "low": price - 0.2, "close": price, "volume": 1.0}
        for i in range(n)
    ]


def _ose_event(break_index: int):
    from app.research.ohlcv_structure_experiment.engine import BreakEvent

    # LONG break of 100 with a far stop: neither stop nor 2R target trades, so a timeout.
    return BreakEvent(
        event_id="BTCUSDT:LONG:1:100", symbol="BTCUSDT", direction="LONG", call_type="bos_with_trend",
        pivot_index=break_index - 6, pivot_at="bar", confirmed_index=break_index - 3, confirmed_at="bar",
        break_index=break_index, break_at="bar", level=100.0, stop=90.0, atr=2.0,
        confirmation_delay_bars=3, bars_from_confirmation_to_break=3,
    )


def _run_ose_cell(monkeypatch, strategy_id: str, break_index: int = 60) -> tuple[dict, list[dict]]:
    import app.research.ohlcv_structure_experiment.engine as ose
    from app.research.engine_backtest import run_engine_cell

    candles = _ose_candles()
    monkeypatch.setattr(ose, "breaks_for_variant", lambda c, symbol, variant: [_ose_event(break_index)])
    out = run_engine_cell(
        {
            "strategy_id": strategy_id, "symbol": "BTCUSDT", "timeframe": "1h", "direction": "LONG",
            "candles": candles, "index_start": 0, "risk_usd": 20.0, "taker_fee": 0.0004,
            "maker_fee": 0.0002, "leverage": 2.0, "principal": 1000.0,
        }
    )
    return out, candles


@pytest.mark.parametrize(
    "strategy_id, decision_index",
    [
        # A and D enter at the open after the break bar.
        ("OSE_A_corrected_baseline", 60),
        ("OSE_D_faster_confirmation", 60),
        # B decides on the close after the break bar and enters one bar later.
        ("OSE_B_acceptance", 61),
    ],
)
def test_ose_signal_time_is_the_decision_bar_and_a_timeout_holds_hold_bars(
    monkeypatch, strategy_id, decision_index
):
    from app.research.ohlcv_structure_experiment.config import HOLD_BARS

    out, candles = _run_ose_cell(monkeypatch, strategy_id)
    (trade,) = out["trades"]
    entry_index = decision_index + 1
    assert trade["outcome"] == "timeout"
    assert trade["signal_time"] == candles[decision_index]["time"].isoformat()
    # Entry and fill stay at the next bar's open.
    assert trade["entry_index"] == entry_index
    assert trade["entry_time"] == trade["fill_time"] == candles[entry_index]["time"].isoformat()
    assert trade["exit_index"] == entry_index + HOLD_BARS - 1
    assert trade["exit_time"] == candles[trade["exit_index"]]["time"].isoformat()
    # The engine's count: entry bar through exit bar inclusive.
    assert trade["holding_bars"] == HOLD_BARS == 20


def test_ose_market_context_still_describes_the_same_decision_bar(monkeypatch):
    import copy

    from app.research.trade_context import attach_trade_context

    out, candles = _run_ose_cell(monkeypatch, "OSE_A_corrected_baseline")
    (trade,) = out["trades"]
    assert trade["market_context"]["decision_bar"] == candles[60]["time"].isoformat()
    # The old stamp (signal_time on the entry bar, read one bar back) gives the same context.
    old = copy.deepcopy(trade)
    old.pop("market_context")
    old["signal_time"] = old["entry_time"]
    attach_trade_context([old], candles, timeframe="1h", symbol="BTCUSDT", decision_offset=-1)
    assert old["market_context"] == trade["market_context"]


def test_ose_trade_prices_and_result_are_unchanged(monkeypatch):
    import app.research.ohlcv_structure_experiment.engine as ose

    out, candles = _run_ose_cell(monkeypatch, "OSE_A_corrected_baseline")
    (trade,) = out["trades"]
    (record,) = [
        r for r in ose.run_variant(candles, [_ose_event(60)], "A_corrected_baseline") if r.status == "filled"
    ]
    assert trade["entry_price"] == record.entry_price
    assert trade["exit_price"] == record.exit_price
    assert trade["engine_net_r"] == record.net_r
    expected = enrich_trade_execution(
        {
            "direction": "LONG", "entry_price": record.entry_price, "stop_price": record.stop_price,
            "exit_price": record.exit_price, "outcome": record.outcome, "entry_type": "MARKET",
        },
        risk_usd=20.0, taker_fee=0.0004, maker_fee=0.0002, leverage=2.0, account_equity=1000.0,
    )
    for key in ("qty", "gross_pnl_usd", "fee_total_usd", "net_pnl_usd", "r_gross", "r_net"):
        assert trade[key] == expected[key], key
    assert (trade["trade_no"], trade["trade_id"]) == (1, "t1")
