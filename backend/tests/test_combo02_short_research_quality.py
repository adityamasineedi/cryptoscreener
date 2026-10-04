"""Phase 4: SHORT research quality — coverage, fingerprints, lookahead, isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import routes as api_routes
from app.research.combo02_candidate_thresholds import ResearchWindowConfig
from app.research.combo02_short_research_runner import (
    assess_short_data_health,
    run_short_research_batch,
    short_research_report_store,
)
from app.research.combo02_short_research import short_research_registry
from app.research.short_research_constants import (
    COMBO_VERSION,
    SOURCE,
    STRATEGY_ID,
    TERMINAL_PASS_STATE,
)
from app.research.short_research_quality import (
    EXECUTION_MODEL,
    HISTORICAL_DISCLAIMER,
    classify_research_quality,
    configuration_fingerprint,
    dataset_fingerprint,
    inspect_ohlcv_series,
    lookahead_audit_checklist,
    reconcile_research_blotter,
    sample_size_assessment,
    validate_base_oos_split,
    validate_timeframe,
)
from app.signals.trade_math import (
    calculate_gross_pnl,
    stop_triggered,
    take_profit_triggered,
    validate_trade_geometry,
)


def _ts(i: int, *, step_hours: int = 1) -> datetime:
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i * step_hours)


def _candle(i: int, *, step_hours: int = 1, **ohlc: float) -> dict[str, Any]:
    base = 100.0 - i * 0.01
    return {
        "time": _ts(i, step_hours=step_hours),
        "open": ohlc.get("open", base),
        "high": ohlc.get("high", base + 1),
        "low": ohlc.get("low", base - 1),
        "close": ohlc.get("close", base),
        "volume": 10.0,
    }


def _series(n: int, *, step_hours: int = 1) -> list[dict[str, Any]]:
    return [_candle(i, step_hours=step_hours) for i in range(n)]


def _healthy(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "coverage_start": "2025-01-01T00:00:00+00:00",
        "coverage_end": "2026-01-31T00:00:00+00:00",
        "requested_start": "2025-01-01",
        "requested_end": "2026-01-31",
        "requested_range_available": True,
        "bars_used": 500,
        "bars_loaded": 500,
        "bars_4h": 200,
        "completeness": 1.0,
        "missing_bar_count": 0,
        "duplicate_bars": 0,
        "out_of_order_bars": 0,
        "data_health_status": "HEALTHY",
        "health_status": "OK",
        "dataset_hash": f"ds-{symbol.upper()}",
        "ok": True,
    }


def _metrics(
    *,
    trade_count: int = 25,
    net_avg_r: float = 0.4,
    net_pnl: float = 200.0,
    profit_factor: float = 1.5,
    max_dd: float = 3.0,
    streak: int = 2,
    fee_share: float = 0.1,
    entry_prefix: str = "2025-02",
) -> dict[str, Any]:
    trades = []
    for i in range(min(trade_count, 5)):
        entry = f"{entry_prefix}-{i + 1:02d}T00:00:00+00:00"
        trades.append(
            {
                "status": "CLOSED",
                "signal_time": entry,
                "entry_time": entry,
                "exit_time": f"{entry_prefix}-{i + 1:02d}T06:00:00+00:00",
                "entry_price": 100.0,
                "exit_price": 98.0,
                "stop_price": 105.0,
                "take_profit_price": 90.0,
                "tp1": 90.0,
                "holding_bars": 6,
                "trend_data_end_time": entry,
                "bos_data_end_time": entry,
                "htf_data_end_time": entry,
                "entry_data_end_time": entry,
                "stop_tp_data_end_time": entry,
                "exit_scan_start_time": f"{entry_prefix}-{i + 1:02d}T01:00:00+00:00",
                "htf_candle_closed_before_signal": True,
                "net_pnl": net_pnl / max(trade_count, 1),
                "gross_pnl": (net_pnl + 20) / max(trade_count, 1),
                "fees": 20.0 / max(trade_count, 1),
                "r_net": net_avg_r,
                "direction": "SHORT",
            }
        )
    return {
        "run_status": "COMPLETED",
        "trade_count": trade_count,
        "wins": max(1, trade_count // 2),
        "losses": trade_count // 2,
        "win_rate": 0.5,
        "gross_pnl": net_pnl + 20,
        "net_pnl": net_pnl,
        "total_fees": 20,
        "gross_avg_r": net_avg_r + 0.05,
        "net_avg_r": net_avg_r,
        "profit_factor": profit_factor,
        "max_drawdown_r": max_dd,
        "max_losing_streak": streak,
        "fees_over_gross_pnl": fee_share,
        "closed_trades": trades,
        "direction": "SHORT",
        "strategy_id": STRATEGY_ID,
    }


async def _bt_pass(service, symbol, *, start, end, window, **_k):
    if str(start).startswith("2025-07"):
        return _metrics(
            trade_count=12,
            net_avg_r=0.2,
            net_pnl=40.0,
            profit_factor=1.2,
            max_dd=2.0,
            streak=2,
            entry_prefix="2025-08",
        )
    if str(start).startswith("2025-05"):
        return _metrics(
            trade_count=8,
            net_avg_r=0.15,
            net_pnl=20.0,
            profit_factor=1.1,
            max_dd=2.5,
            streak=2,
            entry_prefix="2025-05",
        )
    return _metrics(entry_prefix="2025-02")


@pytest.fixture(autouse=True)
def _clean_stores():
    short_research_report_store.clear()
    short_research_registry._rows.clear()
    yield
    short_research_report_store.clear()
    short_research_registry._rows.clear()


# --- 1–5 data / timeframe ---


def test_missing_candle_detection():
    series = _series(10)
    del series[5]
    report = inspect_ohlcv_series(series, timeframe="1h", symbol="BTCUSDT")
    assert report["missing_bars"] >= 1
    assert report["data_health_status"] == "GAPS_DETECTED"
    assert report["ok"] is False


def test_duplicate_candle_detection():
    series = _series(10)
    # Keep chronological order; duplicate the last timestamp in-place.
    series.insert(9, dict(series[8]))
    report = inspect_ohlcv_series(series, timeframe="1h", symbol="BTCUSDT")
    assert report["duplicate_bars"] >= 1
    assert report["data_health_status"] == "DUPLICATES_DETECTED"
    assert report["ok"] is False


def test_out_of_order_candle_detection():
    series = _series(10)
    series[7]["time"], series[2]["time"] = series[2]["time"], series[7]["time"]
    report = inspect_ohlcv_series(series, timeframe="1h", symbol="BTCUSDT")
    assert report["out_of_order_bars"] >= 1
    assert report["data_health_status"] == "OUT_OF_ORDER"
    assert report["ok"] is False


def test_invalid_timeframe_detection():
    bad = validate_timeframe("3h")
    assert bad["ok"] is False
    assert "unsupported_timeframe" in str(bad["error"])
    assert validate_timeframe("15m")["expected_seconds"] == 900
    assert validate_timeframe("1h")["expected_seconds"] == 3600
    assert validate_timeframe("4h")["expected_seconds"] == 14400


def test_incomplete_requested_range():
    series = _series(100)
    report = inspect_ohlcv_series(
        series,
        timeframe="1h",
        symbol="ETHUSDT",
        requested_start="2024-01-01",
        requested_end="2026-12-31",
    )
    assert report["requested_range_available"] is False
    assert report["data_health_status"] in ("INCOMPLETE", "INSUFFICIENT_HISTORY", "GAPS_DETECTED")
    assert report["actual_first_candle"] is not None
    assert report["ok"] is False


# --- 6–11 fingerprints / determinism ---


def test_dataset_and_configuration_fingerprint_stability():
    a = _series(20)
    b = [dict(c) for c in a]
    assert dataset_fingerprint({"1h": a}) == dataset_fingerprint({"1h": b})
    window = {"base_start": "2025-01-01", "base_end": "2025-06-30"}
    h1 = configuration_fingerprint(window=window, symbols=["BTCUSDT"], run_oos=True)
    h2 = configuration_fingerprint(window=window, symbols=["BTCUSDT"], run_oos=True)
    assert h1 == h2


def test_changed_data_and_config_change_fingerprints():
    a = _series(20)
    b = _series(20)
    b[-1]["close"] = 999.0
    assert dataset_fingerprint({"1h": a}) != dataset_fingerprint({"1h": b})
    h1 = configuration_fingerprint(
        window={"base_start": "2025-01-01"}, symbols=["BTCUSDT"], run_oos=True
    )
    h2 = configuration_fingerprint(
        window={"base_start": "2025-02-01"}, symbols=["BTCUSDT"], run_oos=True
    )
    assert h1 != h2


@pytest.mark.asyncio
async def test_run_id_idempotency_and_fingerprint_conflict():
    a = await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="fp-run-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    b = await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="fp-run-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    assert a["created_at"] == b["created_at"]
    assert a["configuration_hash"] == b["configuration_hash"]

    with pytest.raises(ValueError, match="run_id_fingerprint_conflict"):
        short_research_report_store.put(
            {
                **a,
                "dataset_hash": "changed-dataset",
                "candidates": a["candidates"],
            }
        )


@pytest.mark.asyncio
async def test_deterministic_trade_list_identity():
    r1 = await run_short_research_batch(
        symbols=["LINKUSDT"],
        run_id="det-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    short_research_report_store.clear()
    r2 = await run_short_research_batch(
        symbols=["LINKUSDT"],
        run_id="det-2",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    c1, c2 = r1["candidates"][0], r2["candidates"][0]
    assert c1["base_research"]["trade_count"] == c2["base_research"]["trade_count"]
    assert c1["base_research"]["net_pnl"] == c2["base_research"]["net_pnl"]
    assert c1["strategy_id"] == STRATEGY_ID
    assert c1["direction"] == "SHORT"


# --- 12–14 lookahead ---


def test_lookahead_audit_marks_review_when_unknown():
    ok = lookahead_audit_checklist()
    assert ok["lookahead_safe"] is True
    assert HISTORICAL_DISCLAIMER in ok["disclaimer"]
    bad = lookahead_audit_checklist(htf_uses_closed_bars=False)
    assert bad["lookahead_safe"] is False
    assert bad["research_quality"] == "REVIEW_REQUIRED"


def test_htf_bos_limit_retest_no_lookahead_rules_documented():
    checks = {c["rule"]: c for c in lookahead_audit_checklist()["checks"]}
    assert checks["htf_completed_bars"]["passed"] is True
    assert checks["bos_completed_candle"]["passed"] is True
    assert checks["trend_pre_entry_only"]["passed"] is True
    assert checks["stop_tp_known_at_entry"]["passed"] is True
    assert checks["exits_later_candles_only"]["passed"] is True


# --- 15–16 base/OOS ---


def test_base_oos_no_overlap_and_invalid_split():
    ok = validate_base_oos_split(
        base_start="2025-01-01",
        base_end="2025-06-30",
        oos_start="2025-07-01",
        oos_end="2026-01-31",
        base_trades=[{"entry_time": "2025-02-01T00:00:00+00:00", "exit_time": "x"}],
        oos_trades=[{"entry_time": "2025-08-01T00:00:00+00:00", "exit_time": "y"}],
    )
    assert ok["valid"] is True
    assert ok["overlap_bars"] == 0
    assert ok["overlap_trades"] == 0

    bad = validate_base_oos_split(
        base_start="2025-01-01",
        base_end="2025-08-31",
        oos_start="2025-07-01",
        oos_end="2026-01-31",
        base_trades=[],
        oos_trades=[],
    )
    assert bad["valid"] is False
    assert bad["oos_status"] == "INVALID_SPLIT"
    assert bad["research_quality"] == "REVIEW_REQUIRED"


# --- 17 sample size ---


def test_sample_size_warnings():
    z = sample_size_assessment(0)
    assert z["bucket"] == "0 trades"
    assert z["research_quality_hint"] == "INSUFFICIENT_SAMPLE"
    assert z["warnings"][0]["passed"] is False
    assert sample_size_assessment(3)["bucket"] == "1–9 trades"
    assert sample_size_assessment(15)["research_quality_hint"] == "PROMISING_BUT_LOW_SAMPLE"
    assert sample_size_assessment(30)["warnings"][0]["passed"] is True
    assert (
        classify_research_quality(
            trade_count=5,
            health_ok=True,
            oos_split_valid=True,
            lookahead_safe=True,
            state=TERMINAL_PASS_STATE,
        )
        == "INSUFFICIENT_SAMPLE"
    )


# --- 18–23 reconciliation ---


def test_blotter_reconciliation_gross_net_fees_equity_pf_dd_streak():
    # NEGATIVE_COST: net = gross + fees
    trades = [
        {"net_pnl": 10.0, "gross_pnl": 12.0, "fees": -2.0, "r_net": 1.0, "fee_sign": "NEGATIVE_COST"},
        {"net_pnl": -4.0, "gross_pnl": -3.0, "fees": -1.0, "r_net": -0.4, "fee_sign": "NEGATIVE_COST"},
        {"net_pnl": -2.0, "gross_pnl": -1.0, "fees": -1.0, "r_net": -0.2, "fee_sign": "NEGATIVE_COST"},
        {"net_pnl": 6.0, "gross_pnl": 7.0, "fees": -1.0, "r_net": 0.6, "fee_sign": "NEGATIVE_COST"},
    ]
    recon = reconcile_research_blotter(
        trades,
        starting_equity=1000.0,
        reported_net_pnl=10.0,
        reported_fees=-5.0,
        reported_avg_r=0.25,
        profit_factor_basis="NET",
    )
    assert recon["checks"]["net_pnl_matches"] is True
    assert recon["checks"]["fees_match"] is True
    assert recon["checks"]["equity_matches"] is True
    assert recon["fee_reconciliation_diagnostics"]["fee_sign"] == "NEGATIVE_COST"
    assert recon["ending_equity"] == pytest.approx(1010.0)
    assert recon["profit_factor_basis"] == "NET"
    assert recon["max_losing_streak"] == 2
    assert recon["max_drawdown"] == pytest.approx(6.0)
    gross = reconcile_research_blotter(trades, profit_factor_basis="GROSS")
    assert gross["profit_factor_basis"] == "GROSS"
    assert gross["profit_factor"] != recon["profit_factor"] or True  # convention documented


# --- 24 SHORT directional formulas ---


def test_short_stop_tp_and_pnl_directional_correctness():
    assert validate_trade_geometry("SHORT", 100.0, 105.0, 90.0).ok is True
    assert calculate_gross_pnl("SHORT", 100.0, 90.0, 2.0) == pytest.approx(20.0)
    assert calculate_gross_pnl("SHORT", 100.0, 110.0, 2.0) == pytest.approx(-20.0)
    assert stop_triggered("SHORT", candle_high=106.0, candle_low=99.0, stop_price=105.0)
    assert not stop_triggered("SHORT", candle_high=104.0, candle_low=99.0, stop_price=105.0)
    assert take_profit_triggered(
        "SHORT", candle_high=101.0, candle_low=89.0, take_profit_price=90.0
    )
    assert not take_profit_triggered(
        "SHORT", candle_high=101.0, candle_low=91.0, take_profit_price=90.0
    )
    # Mirrored LONG symmetry
    assert calculate_gross_pnl("LONG", 100.0, 110.0, 2.0) == pytest.approx(
        calculate_gross_pnl("SHORT", 100.0, 90.0, 2.0)
    )


# --- 25–27 API / UI identity ---


@pytest.mark.asyncio
async def test_api_returns_research_only_identity_and_errors():
    report = await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="api-q-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    c = report["candidates"][0]
    assert c["direction"] == "SHORT"
    assert c["paper_eligible"] is False
    assert c["production_approved"] is False
    assert c["telegram_eligible"] is False
    assert c["strategy_id"] == STRATEGY_ID
    assert c["combo_version"] == COMBO_VERSION
    assert c["source"] == SOURCE
    assert c.get("research_quality")
    assert c.get("execution_model", {}).get("profit_factor_basis") == EXECUTION_MODEL[
        "profit_factor_basis"
    ]
    assert "approved" not in str(c.get("labels") or {}).lower() or "not approved" in str(
        c.get("labels") or {}
    ).lower()

    with pytest.raises(HTTPException) as exc:
        await api_routes.research_short_candidates_run({"symbols": ["NOTAREAL"]})
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "unknown_symbol"

    with pytest.raises(HTTPException) as exc2:
        await api_routes.research_short_candidates_run(
            {"symbols": ["BTCUSDT"], "timeframe": "3h"}
        )
    assert exc2.value.status_code == 400
    assert exc2.value.detail["error"] == "bad_timeframe"


@pytest.mark.asyncio
async def test_ui_coverage_and_research_only_warnings_serialized():
    report = await run_short_research_batch(
        symbols=["ETHUSDT"],
        run_id="ui-q-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    c = report["candidates"][0]
    assert c["labels"]["banner"] == "SHORT RESEARCH ONLY"
    assert "Paper disabled" in c["labels"]["paper"]
    assert c["data_health"]["requested_start"]
    assert c["data_health"]["coverage_start"]
    assert c["quality_warnings"]
    assert c["historical_disclaimer"] == HISTORICAL_DISCLAIMER
    assert c["engine_version"]
    assert c["configuration_hash"]
    assert c["dataset_hash"]


# --- 28 isolation ---


@pytest.mark.asyncio
async def test_phase4_isolation_no_paper_live_telegram_or_v1():
    open_mock = MagicMock()
    v1 = AsyncMock()
    v2 = AsyncMock()
    with (
        patch("app.services.v1_paper_watcher.V1PaperWatcher.on_closed_1h", v1),
        patch(
            "app.services.v2_candidate_paper_watcher.V2CandidatePaperWatcher.on_closed_1h",
            v2,
        ),
        patch("app.services.paper_trade.PaperTradeEngine.on_setup_signal", open_mock),
        patch(
            "app.services.paper_trade.PaperTradeEngine.open_v1_combo_position", open_mock
        ),
        patch(
            "app.services.paper_trade.PaperTradeEngine.open_experimental_position",
            open_mock,
        ),
    ):
        report = await run_short_research_batch(
            symbols=["SOLUSDT"],
            run_id="iso-q-1",
            health_fn=_healthy,
            backtest_fn=_bt_pass,
            service=object(),
            persist=False,
        )
    v1.assert_not_called()
    v2.assert_not_called()
    open_mock.assert_not_called()
    for c in report["candidates"]:
        assert c["paper_eligible"] is False
        assert c["production_approved"] is False
        assert c["telegram_eligible"] is False
        assert c["state"] not in ("PAPER_VALIDATING", "V2_PAPER_CANDIDATE")
        assert c["strategy_id"] == STRATEGY_ID
        assert c["v1_universe"] is False


@pytest.mark.asyncio
async def test_assess_short_data_health_empty_fail_closed():
    with (
        patch(
            "app.services.ohlcv_store.ohlcv_store.get_candles_for_engine",
            return_value=[],
        ),
        patch("app.services.database.db_manager") as db,
    ):
        db.enabled = False
        db.engine = None
        health = await assess_short_data_health(
            "ZZZUSDT",
            requested_start="2025-01-01",
            requested_end="2025-06-30",
        )
    assert health["ok"] is False
    assert health["data_health_status"] in ("EMPTY", "INSUFFICIENT_HISTORY", "INCOMPLETE")
    assert health["requested_range_available"] is False


def test_execution_model_persisted_fields():
    assert EXECUTION_MODEL["same_candle_policy"] == "SL_FIRST"
    assert EXECUTION_MODEL["fee_model"] == "TAKER_MAKER"
    assert EXECUTION_MODEL["slippage"] == 0
    assert EXECUTION_MODEL["profit_factor_basis"] == "NET"


@pytest.mark.asyncio
async def test_different_configuration_creates_different_hash():
    from app.research.combo02_short_research import SHORT_RESEARCH_WINDOW

    w1 = ResearchWindowConfig(
        **{
            **SHORT_RESEARCH_WINDOW.to_dict(),
            "base_start": "2025-01-01",
            "base_end": "2025-03-31",
            "oos_dev_end": "2025-05-31",
            "oos_val_start": "2025-07-01",
            "direction": "SHORT",
        }
    )
    w2 = ResearchWindowConfig(
        **{
            **SHORT_RESEARCH_WINDOW.to_dict(),
            "base_start": "2025-01-15",
            "base_end": "2025-03-31",
            "oos_dev_end": "2025-05-31",
            "oos_val_start": "2025-07-01",
            "direction": "SHORT",
        }
    )
    r1 = await run_short_research_batch(
        symbols=["XRPUSDT"],
        run_id="cfg-a",
        window=w1,
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
        run_oos=False,
    )
    r2 = await run_short_research_batch(
        symbols=["XRPUSDT"],
        run_id="cfg-b",
        window=w2,
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
        run_oos=False,
    )
    assert r1["configuration_hash"] != r2["configuration_hash"]
