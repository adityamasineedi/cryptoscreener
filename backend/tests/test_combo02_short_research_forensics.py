"""Phase 5: strict base/OOS windows + per-trade forensic lookahead."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.research.combo02_short_research import SHORT_RESEARCH_WINDOW
from app.research.combo02_short_research_runner import (
    run_short_research_batch,
    short_research_report_store,
)
from app.research.combo02_short_research import short_research_registry
from app.research.short_research_constants import STRATEGY_ID, TERMINAL_PASS_STATE
from app.research.short_research_forensics import (
    audit_trades_for_lookahead,
    build_trade_forensic_audit,
    forensic_future_bos_cannot_alter_signal,
    forensic_future_htf_cannot_alter_signal,
    forensic_future_swing_cannot_alter_signal,
    forensic_limit_not_retroactive,
    short_stop_tp_directional_checks,
)
from app.research.short_research_windows import (
    DEFAULT_SHORT_RESEARCH_WINDOWS,
    ResearchWindows,
    assert_partition_time_order,
    filter_candles_to_window,
    partition_candles,
    research_windows_from_config,
    validate_research_windows,
)
from app.signals.trade_math import SAME_CANDLE_PRECEDENCE_SL_FIRST


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
        "data_health_status": "HEALTHY",
        "health_status": "OK",
        "dataset_hash": f"ds-{symbol}",
        "ok": True,
    }


def _trade(*, entry: str, exit_t: str, bad_htf: bool = False) -> dict[str, Any]:
    return {
        "signal_time": entry,
        "entry_time": entry,
        "exit_time": exit_t,
        "entry_price": 100.0,
        "stop_price": 105.0,
        "take_profit_price": 90.0,
        "tp1": 90.0,
        "holding_bars": 2,
        "trend_data_end_time": entry,
        "bos_data_end_time": entry,
        "htf_data_end_time": entry,
        "entry_data_end_time": entry,
        "stop_tp_data_end_time": entry,
        "exit_scan_start_time": (
            datetime.fromisoformat(entry.replace("Z", "+00:00")) + timedelta(hours=1)
        ).isoformat(),
        "htf_candle_closed_before_signal": not bad_htf,
        "direction": "SHORT",
        "r_net": 0.4,
        "net_pnl": 8.0,
        "fees": 1.0,
        "gross_pnl": 9.0,
    }


def _metrics(*, entry_prefix: str, trade_count: int = 25, **kw: Any) -> dict[str, Any]:
    trades = [
        _trade(
            entry=f"{entry_prefix}-{i+1:02d}T00:00:00+00:00",
            exit_t=f"{entry_prefix}-{i+1:02d}T06:00:00+00:00",
        )
        for i in range(min(trade_count, 3))
    ]
    net = float(kw.get("net_pnl", 200.0))
    avg = float(kw.get("net_avg_r", 0.4))
    return {
        "run_status": "COMPLETED",
        "trade_count": trade_count,
        "wins": trade_count // 2,
        "losses": trade_count // 2,
        "win_rate": 0.5,
        "gross_pnl": net + 20,
        "net_pnl": net,
        "total_fees": 20,
        "gross_avg_r": avg + 0.05,
        "net_avg_r": avg,
        "profit_factor": float(kw.get("profit_factor", 1.5)),
        "max_drawdown_r": float(kw.get("max_dd", 3.0)),
        "max_losing_streak": int(kw.get("streak", 2)),
        "fees_over_gross_pnl": 0.1,
        "closed_trades": trades,
        "direction": "SHORT",
        "strategy_id": STRATEGY_ID,
    }


async def _bt_pass(service, symbol, *, start, end, window, **_k):
    if str(start).startswith("2025-07"):
        return _metrics(
            entry_prefix="2025-08",
            trade_count=12,
            net_avg_r=0.2,
            net_pnl=40.0,
            profit_factor=1.2,
            max_dd=2.0,
        )
    if str(start).startswith("2025-05"):
        return _metrics(
            entry_prefix="2025-05",
            trade_count=8,
            net_avg_r=0.15,
            net_pnl=20.0,
            profit_factor=1.1,
        )
    return _metrics(entry_prefix="2025-02")


@pytest.fixture(autouse=True)
def _clean():
    short_research_report_store.clear()
    short_research_registry._rows.clear()
    yield
    short_research_report_store.clear()
    short_research_registry._rows.clear()


def test_base_and_oos_windows_strictly_disjoint():
    w = DEFAULT_SHORT_RESEARCH_WINDOWS
    check = validate_research_windows(w)
    assert check["ok"] is True
    assert check["window_status"] == "OK"
    assert w.base_end < w.oos_dev_start
    assert w.oos_dev_end < w.oos_val_start


def test_overlapping_windows_fail_closed():
    bad = ResearchWindows(
        requested_start="2025-01-01",
        requested_end="2026-01-31",
        base_start="2025-01-01",
        base_end="2025-08-01",
        oos_dev_start="2025-05-01",
        oos_dev_end="2025-06-30",
        oos_val_start="2025-07-01",
        oos_val_end="2026-01-31",
    )
    check = validate_research_windows(bad)
    assert check["ok"] is False
    assert check["window_status"] == "INVALID_OVERLAP"
    assert check["oos_status"] == "INVALID_SPLIT"
    assert check["research_quality"] == "REVIEW_REQUIRED"


def test_partition_filters_exclude_other_windows():
    w = DEFAULT_SHORT_RESEARCH_WINDOWS
    candles = []
    for day, month in [(1, 2), (15, 5), (10, 8)]:
        candles.append(
            {
                "time": datetime(2025, month, day, tzinfo=timezone.utc),
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 1,
            }
        )
    parts = partition_candles(candles, w)
    assert len(parts["base"]) == 1
    assert len(parts["oos_dev"]) == 1
    assert len(parts["oos_val"]) == 1
    base_only = filter_candles_to_window(
        candles, start=w.base_start, end=w.base_end
    )
    assert all(
        c["time"].month <= 4 for c in base_only
    )
    order = assert_partition_time_order(parts["base"], parts["oos_dev"], parts["oos_val"])
    assert order["ok"] is True


def test_canonical_window_object_from_short_config():
    windows = research_windows_from_config(SHORT_RESEARCH_WINDOW)
    assert windows.policy == "POLICY_A_STRICT_DISJOINT"
    assert windows.base_end == "2025-04-30"
    assert windows.oos_dev_start == "2025-05-01"
    d = windows.to_dict()
    for key in (
        "requested_start",
        "requested_end",
        "base_start",
        "base_end",
        "oos_dev_start",
        "oos_dev_end",
        "oos_val_start",
        "oos_val_end",
    ):
        assert key in d


def test_per_trade_audit_record_and_time_bounds():
    entry = "2025-02-01T00:00:00+00:00"
    t = _trade(entry=entry, exit_t="2025-02-01T06:00:00+00:00")
    t["forensic_evidence_class"] = "DERIVED_FROM_RESEARCH_TRADE"
    audit = build_trade_forensic_audit(t)
    assert audit["lookahead_status"] == "PASS"
    assert audit["research_quality"] == "REVIEW_REQUIRED"  # derived cannot PASS
    assert audit["lookahead_checks"]["trend_before_entry"] is True
    assert audit["lookahead_checks"]["bos_before_entry"] is True
    assert audit["lookahead_checks"]["htf_before_entry"] is True
    assert audit["lookahead_checks"]["stop_tp_known_before_fill"] is True
    assert audit["lookahead_checks"]["exit_after_entry"] is True
    assert audit["entry_candle_close_time"]


def test_failed_forensic_audit_review_required():
    bad = _trade(
        entry="2025-02-01T00:00:00+00:00",
        exit_t="2025-02-01T06:00:00+00:00",
    )
    bad["trend_data_end_time"] = "2025-02-02T00:00:00+00:00"  # after signal
    audit = build_trade_forensic_audit(bad)
    assert audit["lookahead_status"] == "FAIL"
    assert audit["research_quality"] == "REVIEW_REQUIRED"
    bundle = audit_trades_for_lookahead([bad])
    assert bundle["lookahead_status"] == "FAIL"


def test_future_htf_swing_bos_cannot_alter_earlier_signal():
    signal = datetime(2025, 2, 1, 12, tzinfo=timezone.utc)
    htf = forensic_future_htf_cannot_alter_signal(
        signal_time=signal,
        htf_closed_before={"trend_4h": "BEARISH", "signal_trend_4h": "BEARISH"},
        htf_with_future={
            "trend_4h": "BULLISH",
            "future_candle_open": (signal + timedelta(hours=4)).isoformat(),
        },
    )
    assert htf["ok"] is True
    assert htf["retained_earlier_classification"] is True

    swings = forensic_future_swing_cannot_alter_signal(
        signal_time=signal,
        swings_at_signal=[
            {"timestamp": (signal - timedelta(hours=2)).isoformat(), "label": "LH"},
            {"timestamp": (signal - timedelta(hours=1)).isoformat(), "label": "LL"},
        ],
        swings_with_future=[
            {"timestamp": (signal - timedelta(hours=2)).isoformat(), "label": "LH"},
            {"timestamp": (signal - timedelta(hours=1)).isoformat(), "label": "LL"},
            {"timestamp": (signal + timedelta(hours=5)).isoformat(), "label": "LL"},
        ],
    )
    assert swings["ok"] is True

    bos = forensic_future_bos_cannot_alter_signal(
        signal_time=signal,
        bos_at_signal={"timestamp": (signal - timedelta(minutes=30)).isoformat()},
        bos_after={"timestamp": (signal + timedelta(hours=2)).isoformat()},
    )
    assert bos["ok"] is True


def test_limit_not_retroactive_and_short_stop_tp_sl_first():
    created = datetime(2025, 2, 1, 10, tzinfo=timezone.utc)
    ok = forensic_limit_not_retroactive(
        order_created=created,
        fill_candle_time=created + timedelta(hours=1),
    )
    assert ok["ok"] is True
    bad = forensic_limit_not_retroactive(
        order_created=created,
        fill_candle_time=created - timedelta(hours=1),
    )
    assert bad["ok"] is False
    assert bad["retroactive"] is True

    checks = short_stop_tp_directional_checks(
        candle_high=106.0,
        candle_low=89.0,
        entry=100.0,
        stop=105.0,
        tp=90.0,
    )
    assert checks["stop_uses_high"] is True
    assert checks["tp_uses_low"] is True
    assert checks["same_candle_policy"] == SAME_CANDLE_PRECEDENCE_SL_FIRST
    assert checks["same_candle_outcome"] == "STOP"
    assert checks["ambiguous"] is True


@pytest.mark.asyncio
async def test_runner_persists_windows_forensics_and_separate_oos():
    report = await run_short_research_batch(
        symbols=["BTCUSDT"],
        run_id="p5-1",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    assert report["research_windows"]["base_end"] == "2025-04-30"
    assert report["window_status"] == "OK"
    c = report["candidates"][0]
    assert c["research_windows"]["oos_dev_start"] == "2025-05-01"
    assert c["oos_development"]["partition"] == "oos_development"
    assert c["oos_validation"]["partition"] == "oos_validation"
    assert c["trade_forensic_audits"]
    # Time-check may PASS, but derived evidence cannot unlock research PASS.
    assert c["forensic_lookahead"]["research_quality"] == "REVIEW_REQUIRED"
    assert c["forensic_lookahead"].get("forensic_pass_for_promotion") is False
    assert c["paper_eligible"] is False
    assert c["state"] != "V2_PAPER_CANDIDATE"
    assert c["research_quality"] == "REVIEW_REQUIRED"
    assert c["prepaper_review"]["review_status"] == "REVIEW_BLOCKED"


@pytest.mark.asyncio
async def test_invalid_overlap_produces_oos_failed_review_required():
    from app.research.combo02_candidate_thresholds import ResearchWindowConfig

    overlapping = ResearchWindowConfig(
        **{
            **SHORT_RESEARCH_WINDOW.to_dict(),
            "base_end": "2025-08-01",  # overlaps OOS
            "oos_dev_end": "2025-06-30",
            "oos_val_start": "2025-07-01",
        }
    )
    # Force overlapping ResearchWindows via from_config remap avoidance:
    # research_windows_from_config remaps legacy overlap to defaults — pass explicit.
    from app.research.combo02_short_research_runner import research_one_short_symbol

    bad_windows = ResearchWindows(
        requested_start="2025-01-01",
        requested_end="2026-01-31",
        base_start="2025-01-01",
        base_end="2025-08-01",
        oos_dev_start="2025-05-01",
        oos_dev_end="2025-06-30",
        oos_val_start="2025-07-01",
        oos_val_end="2026-01-31",
    )
    row = await research_one_short_symbol(
        symbol="ETHUSDT",
        run_id="overlap-1",
        service=object(),
        window=overlapping,
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        research_windows=bad_windows,
    )
    assert row["window_status"] == "INVALID_OVERLAP"
    assert row["oos"]["oos_status"] == "INVALID_SPLIT"
    assert row["research_quality"] == "REVIEW_REQUIRED"
    assert row["state"] == "OOS_FAILED"
    assert row["paper_eligible"] is False


@pytest.mark.asyncio
async def test_failed_lookahead_prevents_short_research_candidate():
    async def _bt_bad_forensic(service, symbol, *, start, end, window, **_k):
        m = await _bt_pass(service, symbol, start=start, end=end, window=window)
        for t in m["closed_trades"]:
            t["exit_scan_start_time"] = t["entry_time"]  # not strictly after
            t["holding_bars"] = 0
            t.pop("forensic_exit_scan_after_entry", None)
        return m

    report = await run_short_research_batch(
        symbols=["SOLUSDT"],
        run_id="p5-fail-la",
        health_fn=_healthy,
        backtest_fn=_bt_bad_forensic,
        service=object(),
        persist=False,
    )
    c = report["candidates"][0]
    assert c["state"] != TERMINAL_PASS_STATE
    assert c["research_quality"] == "REVIEW_REQUIRED"
    assert c["forensic_lookahead"]["lookahead_status"] == "FAIL"


@pytest.mark.asyncio
async def test_api_exposes_windows_and_quality():
    from app.research.combo02_short_research_runner import api_list_response

    await run_short_research_batch(
        symbols=["LINKUSDT"],
        run_id="p5-api",
        health_fn=_healthy,
        backtest_fn=_bt_pass,
        service=object(),
        persist=False,
    )
    out = api_list_response()
    assert out["research_windows"]["policy"] == "POLICY_A_STRICT_DISJOINT"
    c = out["candidates"][0]
    assert c["research_windows"]
    assert c["research_quality"]
    assert c["oos_development"] is not None
    assert c["oos_validation"] is not None


@pytest.mark.asyncio
async def test_phase5_isolation_no_paper_paths():
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
    ):
        report = await run_short_research_batch(
            symbols=["XRPUSDT"],
            run_id="p5-iso",
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
