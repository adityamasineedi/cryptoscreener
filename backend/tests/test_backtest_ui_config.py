"""Strategy Backtest UI/API configuration safety — no trading-logic changes."""

from __future__ import annotations

import asyncio

import pytest

from app.research.backtest_job import BacktestJobService
from app.research.backtest_ui_config import (
    SHORT_RESEARCH_PAUSED_CODE,
    STRATEGY_ID_V1,
    BacktestConfigError,
    bars_to_approx_days,
    fee_display_metadata,
    job_identity_payload,
    period_mode_copy,
    period_mode_label,
    timeframe_role_label,
    validate_backtest_request,
)


def _resolve(**kwargs):
    defaults = dict(
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        direction="LONG",
        combination_id="COMBO_02",
        strategy_id="COMBO_02_V1",
        combo_version="v1",
        setup_timeframe="1h",
        risk_mode="V1_PRODUCTION_PROFILE",
        research_risk_override=False,
        risk_usd=20.0,
        principal_usd=1000.0,
        leverage=2.0,
        taker_fee_pct=0.04,
        maker_fee_pct=0.02,
    )
    defaults.update(kwargs)
    return validate_backtest_request(**defaults)


def test_btc_v1_uses_1_5_percent_risk():
    r = _resolve(symbols=["BTCUSDT"], risk_usd=20.0)
    cell = r.cells[0]
    assert cell.effective_risk_percent == pytest.approx(0.015)
    assert cell.risk_source == "V1_PRODUCTION_PROFILE"
    assert cell.production_comparable is True


def test_eth_v1_uses_0_5_percent_risk():
    r = _resolve(symbols=["ETHUSDT"], risk_usd=20.0)
    cell = r.cells[0]
    assert cell.effective_risk_percent == pytest.approx(0.005)
    assert cell.effective_risk_amount == pytest.approx(5.0)


def test_sol_v1_uses_0_5_percent_risk():
    r = _resolve(symbols=["SOLUSDT"], risk_usd=20.0)
    cell = r.cells[0]
    assert cell.effective_risk_percent == pytest.approx(0.005)
    assert cell.effective_risk_amount == pytest.approx(5.0)


def test_btc_risk_amount_15_at_1000_principal():
    r = _resolve(symbols=["BTCUSDT"], principal_usd=1000.0, risk_usd=20.0)
    assert r.cells[0].effective_risk_amount == pytest.approx(15.0)


def test_eth_sol_risk_amount_5_at_1000_principal():
    r = _resolve(symbols=["ETHUSDT", "SOLUSDT"], principal_usd=1000.0)
    by_sym = {c.symbol: c for c in r.cells}
    assert by_sym["ETHUSDT"].effective_risk_amount == pytest.approx(5.0)
    assert by_sym["SOLUSDT"].effective_risk_amount == pytest.approx(5.0)


def test_manual_2_percent_cannot_silently_be_production_comparable_under_override():
    r = _resolve(
        symbols=["BTCUSDT"],
        risk_usd=20.0,
        research_risk_override=True,
        risk_mode="RESEARCH_OVERRIDE",
    )
    assert r.cells[0].effective_risk_percent == pytest.approx(0.02)
    assert r.cells[0].risk_source == "RESEARCH_OVERRIDE"
    assert r.production_comparable is False
    assert "risk_mismatch" in r.mismatch_reasons


def test_research_risk_override_marks_production_comparable_false():
    r = _resolve(research_risk_override=True, risk_usd=20.0)
    identity = job_identity_payload(r)
    assert identity["production_comparable"] is False
    assert identity["risk_source"] == "RESEARCH_OVERRIDE"
    assert identity["configured_risk_percent"] == pytest.approx(0.02)
    assert identity["effective_risk_percent"] == pytest.approx(0.02)


def test_short_request_returns_short_research_paused():
    with pytest.raises(BacktestConfigError) as ei:
        _resolve(direction="SHORT")
    assert ei.value.code == SHORT_RESEARCH_PAUSED_CODE
    assert "short_research_paused" in str(ei.value) or ei.value.code == "short_research_paused"


def test_short_job_start_rejected():
    svc = BacktestJobService()

    async def _run() -> None:
        with pytest.raises(BacktestConfigError) as ei:
            await svc.start(
                symbols=["BTCUSDT"],
                timeframes=["1h"],
                direction="SHORT",
            )
        assert ei.value.code == "short_research_paused"

    asyncio.run(_run())


def test_v1_strategy_identity_returned_explicitly():
    r = _resolve()
    identity = job_identity_payload(r)
    assert identity["strategy_id"] == STRATEGY_ID_V1
    assert identity["combo_version"] == "v1"
    assert identity["source"] == "V1_RESEARCH_BACKTEST"
    assert identity["direction"] == "LONG"
    assert identity["setup_timeframe"] == "1h"
    assert identity["htf_timeframes"] == ["1h", "4h"]
    assert identity["htf_alignment"] == "BULLISH"
    assert identity["production_comparable"] is True


def test_timeframe_role_labels():
    assert timeframe_role_label("1h") == "v1 setup timeframe"
    assert timeframe_role_label("4h") == "HTF context"
    assert timeframe_role_label("15m") == "research-only"


def test_bars_duration_labels_timeframe_aware():
    assert bars_to_approx_days(5760, "1h") == pytest.approx(240.0)
    assert bars_to_approx_days(5760, "15m") == pytest.approx(60.0)
    assert bars_to_approx_days(5760, "4h") == pytest.approx(960.0)


def test_requested_and_actual_ranges_on_enriched_row():
    from app.research.backtest_ui_config import enrich_row_with_config

    r = _resolve(start_date=None, end_date=None)
    row = enrich_row_with_config(
        {
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "period_start": "2025-01-01T00:00:00+00:00",
            "period_end": "2025-08-29T00:00:00+00:00",
            "bars_loaded": 5760,
            "risk_usd": 15.0,
        },
        r,
        dataset_fingerprint="ds_test",
    )
    assert row["actual_range"]["first_candle"] == "2025-01-01T00:00:00+00:00"
    assert row["actual_range"]["last_candle"] == "2025-08-29T00:00:00+00:00"
    assert row["actual_range"]["bars_loaded"] == 5760
    assert row["actual_range"]["bars_used"] == 5760
    assert row["requested_range"]["mode"] == "DB_TAIL"
    assert row["dataset_fingerprint"] == "ds_test"
    assert row["configuration_fingerprint"]


def test_db_tail_mode_not_mislabeled_as_calendar_range():
    assert period_mode_label(start_date=None, end_date=None) == "DB_TAIL"
    meta = period_mode_copy("DB_TAIL")
    assert meta["label"] == "DB-tail mode"
    assert "calendar-year" in meta["note"].lower()
    cal = period_mode_copy("CALENDAR_RANGE")
    assert cal["label"] == "Calendar-range mode"


def test_exact_frozen_v1_config_is_production_comparable():
    r = _resolve(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframes=["1h"],
        direction="LONG",
        risk_mode="V1_PRODUCTION_PROFILE",
        research_risk_override=False,
        leverage=2.0,
        taker_fee_pct=0.04,
        maker_fee_pct=0.02,
    )
    assert r.production_comparable is True
    assert r.research_only is False
    by_sym = {c.symbol: c for c in r.cells}
    assert by_sym["BTCUSDT"].effective_risk_percent == pytest.approx(0.015)
    assert by_sym["ETHUSDT"].effective_risk_percent == pytest.approx(0.005)
    assert by_sym["SOLUSDT"].effective_risk_percent == pytest.approx(0.005)


def test_mismatched_risk_timeframe_config_is_research_only():
    r = _resolve(timeframes=["4h"], symbols=["BTCUSDT"])
    assert r.production_comparable is False
    assert r.research_only is True
    assert "setup_timeframe_mismatch" in r.mismatch_reasons


def test_fee_metadata_display_without_changing_calculations():
    meta = fee_display_metadata(taker_fee_pct=0.04, maker_fee_pct=0.02)
    assert meta["fee_basis"] == "executed notional"
    assert meta["fee_convention"] == "negative cost"
    assert meta["taker_fee_pct"] == 0.04
    assert meta["maker_fee_pct"] == 0.02
    assert "Market entry -> taker fee" in meta["examples"]


def test_backtest_creates_no_paper_live_or_telegram():
    r = _resolve()
    identity = job_identity_payload(r)
    assert identity["paper_trade_created"] is False
    assert identity["live_trade_created"] is False
    assert identity["telegram_sent"] is False


def test_ambiguous_direction_rejected_for_v1():
    with pytest.raises(BacktestConfigError) as ei:
        _resolve(direction="")
    assert ei.value.code == "ambiguous_direction"


def test_invalid_risk_rejected():
    with pytest.raises(BacktestConfigError) as ei:
        _resolve(risk_usd=-5)
    assert ei.value.code == "invalid_risk"


def test_v1_production_profile_ignores_configured_2_percent_for_effective_risk():
    """Configured UI 2% must not become the effective v1 risk."""
    r = _resolve(symbols=["BTCUSDT"], risk_usd=20.0, risk_mode="V1_PRODUCTION_PROFILE")
    identity = job_identity_payload(r)
    assert identity["configured_risk_percent"] == pytest.approx(0.02)
    assert identity["effective_risk_percent"] == pytest.approx(0.015)
    assert identity["risk_source"] == "V1_PRODUCTION_PROFILE"
    assert identity["production_comparable"] is True


def test_job_start_long_v1_identity_and_no_side_effects():
    svc = BacktestJobService()

    async def _fake_matrix(**kwargs):
        return {
            "status": "OK",
            "playbook": "test",
            "combination_name": "COMBO_02",
            "disclaimer": "research",
            "label": "LONG_STRATEGY_BACKTEST",
            "dataset_id": "ds1",
            "rows": [
                {
                    "symbol": kwargs["symbols"][0],
                    "timeframe": kwargs["timeframes"][0],
                    "direction": "LONG",
                    "sample_size": 0,
                    "risk_usd": kwargs["risk_usd"],
                    "period_start": "2025-01-01T00:00:00+00:00",
                    "period_end": "2025-02-01T00:00:00+00:00",
                    "bars_loaded": 100,
                    "trades": [],
                }
            ],
        }

    async def _run() -> None:
        import app.research.service as svc_mod

        class Fake:
            async def strategy_matrix(self, **kwargs):
                return await _fake_matrix(**kwargs)

        # Patch get_bos_research_service used inside _run
        original = svc_mod.get_bos_research_service
        svc_mod.get_bos_research_service = lambda: Fake()  # type: ignore[assignment]
        try:
            started = await svc.start(
                symbols=["BTCUSDT"],
                timeframes=["1h"],
                direction="LONG",
                strategy_id="COMBO_02_V1",
                combo_version="v1",
                risk_mode="V1_PRODUCTION_PROFILE",
                principal_usd=1000.0,
                risk_usd=20.0,
            )
            assert started["strategy_id"] == "COMBO_02_V1"
            assert started["effective_risk_percent"] == pytest.approx(0.015)
            assert started["paper_trade_created"] is False
            assert started["live_trade_created"] is False
            assert started["telegram_sent"] is False
            # Wait for fake job to finish
            for _ in range(50):
                st = svc.status()
                if st.get("status") in {"done", "error", "cancelled"}:
                    break
                await asyncio.sleep(0.02)
            st = svc.status()
            assert st["status"] == "done"
            assert st["rows"][0]["effective_risk_amount"] == pytest.approx(15.0)
            assert st["rows"][0]["risk_source"] == "V1_PRODUCTION_PROFILE"
            assert st["rows"][0]["paper_trade_created"] is False
            assert st["telegram_sent"] is False
        finally:
            svc_mod.get_bos_research_service = original  # type: ignore[assignment]

    asyncio.run(_run())
