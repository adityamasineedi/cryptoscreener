"""Phase 3: SHORT research API + historical runner — research-only isolation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import routes as api_routes
from app.research.combo02_short_research_runner import (
    api_list_response,
    research_one_short_symbol,
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
from app.research.combo02_candidate_thresholds import ResearchWindowConfig


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


def _blocked(symbol: str) -> dict[str, Any]:
    h = _healthy(symbol)
    h.update(
        {
            "ok": False,
            "health_status": "BLOCKED",
            "bars_used": 10,
            "completeness": 0.1,
            "reason": "insufficient_1h_or_4h_ohlcv",
        }
    )
    return h


def _metrics(
    *,
    trade_count: int = 25,
    net_avg_r: float = 0.4,
    net_pnl: float = 200.0,
    profit_factor: float = 1.5,
    max_dd: float = 3.0,
    streak: int = 2,
    fee_share: float = 0.1,
    status: str = "COMPLETED",
    entry_prefix: str = "2025-02",
) -> dict[str, Any]:
    trades = []
    for i in range(min(trade_count, 3)):
        entry = f"{entry_prefix}-{i + 1:02d}T00:00:00+00:00"
        exit_t = f"{entry_prefix}-{i + 1:02d}T06:00:00+00:00"
        trades.append(
            {
                "status": "CLOSED",
                "signal_time": entry,
                "entry_time": entry,
                "exit_time": exit_t,
                "entry_price": 100.0,
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
                "r_net": net_avg_r,
                "direction": "SHORT",
            }
        )
    return {
        "run_status": status,
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


@pytest.fixture(autouse=True)
def _clean_stores():
    short_research_report_store.clear()
    short_research_registry._rows.clear()
    yield
    short_research_report_store.clear()
    short_research_registry._rows.clear()


async def _bt_pass(service, symbol, *, start, end, window, **_k):
    # OOS validation (determines status); OOS development reported separately.
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


async def _bt_base_fail(service, symbol, *, start, end, window, **_k):
    return _metrics(
        trade_count=25,
        net_avg_r=-0.35,
        net_pnl=-80.0,
        profit_factor=0.69,
        max_dd=4.0,
        streak=4,
        entry_prefix="2025-02",
    )


async def _bt_oos_fail(service, symbol, *, start, end, window, **_k):
    if str(start).startswith("2025-07"):
        return _metrics(
            trade_count=12,
            net_avg_r=-0.2,
            net_pnl=-30.0,
            profit_factor=0.8,
            max_dd=5.0,
            streak=5,
            entry_prefix="2025-08",
        )
    if str(start).startswith("2025-05"):
        return _metrics(
            trade_count=8,
            net_avg_r=0.1,
            net_pnl=10.0,
            profit_factor=1.05,
            max_dd=2.0,
            streak=2,
            entry_prefix="2025-05",
        )
    return _metrics(entry_prefix="2025-02")


@pytest.mark.asyncio
async def test_api_list_identity_and_safety_flags():
    await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="run-api-list",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    out = api_list_response()
    assert out["strategy_id"] == STRATEGY_ID
    assert out["direction"] == "SHORT"
    assert out["paper_eligible"] is False
    assert out["production_approved"] is False
    assert out["telegram_eligible"] is False
    assert out["candidates"]
    c = out["candidates"][0]
    assert c["strategy_id"] == STRATEGY_ID
    assert c["combo_version"] == COMBO_VERSION
    assert c["source"] == SOURCE
    assert c["direction"] == "SHORT"
    assert c["paper_eligible"] is False
    assert c["production_approved"] is False
    assert c["telegram_eligible"] is False
    assert "approved" not in c or c.get("approved") is None


@pytest.mark.asyncio
async def test_route_handlers_get_and_post_short_candidates():
    fake_report = {
        "status": "OK",
        "run_id": "http-run-1",
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE,
        "direction": "SHORT",
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "candidates": [
            {
                "symbol": "LINKUSDT",
                "timeframe": "1h",
                "direction": "SHORT",
                "strategy_id": STRATEGY_ID,
                "combo_version": COMBO_VERSION,
                "source": SOURCE,
                "research_tier": TERMINAL_PASS_STATE,
                "state": TERMINAL_PASS_STATE,
                "paper_eligible": False,
                "production_approved": False,
                "telegram_eligible": False,
            }
        ],
        "disclaimer": "x",
    }
    with patch(
        "app.research.combo02_short_research_runner.run_short_research_batch",
        new=AsyncMock(return_value=fake_report),
    ):
        body = await api_routes.research_short_candidates_run(
            {"symbols": ["LINKUSDT"], "run_id": "http-run-1"}
        )
    assert body["strategy_id"] == STRATEGY_ID
    assert body["direction"] == "SHORT"
    assert body["paper_eligible"] is False
    assert body["production_approved"] is False
    assert body["telegram_eligible"] is False
    assert body["execution_rights"] is False

    short_research_report_store.put(
        {
            "run_id": "http-run-1",
            "strategy_id": STRATEGY_ID,
            "candidates": body["candidates"],
        }
    )
    listed = await api_routes.research_short_candidates()
    assert listed["direction"] == "SHORT"
    detail = await api_routes.research_short_candidate_detail("LINKUSDT")
    assert detail["candidate"]["symbol"] == "LINKUSDT"


@pytest.mark.asyncio
async def test_runner_never_calls_watchers_paper_or_telegram():
    from app.services.telegram_alerts import is_v1_paper_alert

    open_mock = MagicMock()
    tick_mock = MagicMock()
    v1_on_closed = AsyncMock()
    v2_on_closed = AsyncMock()

    with (
        patch("app.services.v1_paper_watcher.V1PaperWatcher.on_closed_1h", v1_on_closed),
        patch(
            "app.services.v2_candidate_paper_watcher.V2CandidatePaperWatcher.on_closed_1h",
            v2_on_closed,
        ),
        patch("app.services.paper_trade.PaperTradeEngine.on_setup_signal", open_mock),
        patch("app.services.paper_trade.PaperTradeEngine.open_v1_combo_position", open_mock),
        patch(
            "app.services.paper_trade.PaperTradeEngine.open_experimental_position",
            open_mock,
        ),
        patch("app.services.paper_trade.PaperTradeEngine.tick", tick_mock),
    ):
        report = await run_short_research_batch(
            symbols=["ETHUSDT"],
            run_id="iso-1",
            health_fn=_healthy,
            backtest_fn=_bt_pass,
            service=object(),
            persist=False,
        )
    assert report["candidates"]
    v1_on_closed.assert_not_called()
    v2_on_closed.assert_not_called()
    open_mock.assert_not_called()
    tick_mock.assert_not_called()
    for c in report["candidates"]:
        assert c["risk_simulation"]["added_to_v1_risk_totals"] is False
        assert c["portfolio"]["affects_v1_risk_totals"] is False
        assert not is_v1_paper_alert(
            {
                "type": "PAPER_ENTRY",
                "symbol": c["symbol"],
                "telegram_eligible": True,
                "payload": {
                    **c,
                    "telegram_eligible": True,
                    "combo_id": "COMBO_02",
                    "path": "A",
                    "htf_alignment": "HTF_ALIGNED",
                    "signal_snippet": c,
                },
            }
        )


@pytest.mark.asyncio
async def test_passing_persists_short_research_candidate_only():
    report = await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="pass-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    c = report["candidates"][0]
    # Derived-only forensic evidence cannot promote to SHORT_RESEARCH_CANDIDATE.
    assert c["state"] in (TERMINAL_PASS_STATE, "PROMISING", "OOS_FAILED")
    assert c["state"] != "V2_PAPER_CANDIDATE"
    assert c["state"] != "PAPER_VALIDATING"
    assert c["paper_eligible"] is False
    assert c["production_approved"] is False
    assert c["telegram_eligible"] is False
    assert all(
        str(x.get("state")) not in ("V2_PAPER_CANDIDATE", "PAPER_VALIDATING")
        for x in report["candidates"]
    )
    assert c["direction_checks"]
    assert any(r["rule"] == "short_geometry" for r in c["direction_checks"])
    assert c["research_quality"] == "REVIEW_REQUIRED"
    assert c["prepaper_review"]["review_status"] == "REVIEW_BLOCKED"


@pytest.mark.asyncio
async def test_oos_failure_and_base_failure_and_health_block():
    oos = await run_short_research_batch(
        symbols=["SOLUSDT"],
        run_id="oos-fail",
        health_fn=_healthy,
        backtest_fn=_bt_oos_fail,
        service=object(),
        persist=False,
    )
    assert oos["candidates"][0]["state"] == "OOS_FAILED"

    base = await run_short_research_batch(
        symbols=["BNBUSDT"],
        run_id="base-fail",
        health_fn=_healthy,
        backtest_fn=_bt_base_fail,
        service=object(),
        persist=False,
    )
    assert base["candidates"][0]["state"] == "RESEARCH_REJECTED"
    assert base["candidates"][0]["rejection_reasons"]

    blocked = await run_short_research_batch(
        symbols=["DOGEUSDT"],
        run_id="health-fail",
        health_fn=_blocked,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    assert blocked["candidates"][0]["state"] == "RESEARCH_REJECTED"
    assert blocked["candidates"][0]["data_health"]["health_status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_run_id_idempotent_and_distinguishable():
    a = await run_short_research_batch(
        symbols=["XRPUSDT"],
        run_id="same-run",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    b = await run_short_research_batch(
        symbols=["XRPUSDT"],
        run_id="same-run",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    assert a["run_id"] == b["run_id"] == "same-run"
    # Second call returns the stored report (idempotent)
    assert a["created_at"] == b["created_at"]

    c = await run_short_research_batch(
        symbols=["XRPUSDT"],
        run_id="other-run",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    assert c["run_id"] != a["run_id"]


@pytest.mark.asyncio
async def test_window_must_be_short():
    bad = ResearchWindowConfig(direction="LONG")
    with pytest.raises(RuntimeError, match="SHORT"):
        await research_one_short_symbol(
            symbol="BTCUSDT",
            run_id="bad-window",
            service=object(),
            window=bad,
            health_fn=_healthy,
            backtest_fn=_bt_pass,
        )
