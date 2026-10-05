"""Safety tests for the dynamic COMBO_02 v2 candidate pipeline.

Proves v1 boundaries remain sealed: no automatic paper, no Telegram, no
background promotion to PAPER_VALIDATING / APPROVED.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.research.candidate_data_health import check_candidate_data_health, evaluate_health
from app.research.candidate_state_machine import (
    IllegalStateTransition,
    OperatorOnlyTransition,
    assert_transition_allowed,
)
from app.research.dynamic_candidate_constants import (
    COMBO_VERSION,
    SOURCE_PIPELINE,
    SOURCE_WATCHER,
    STRATEGY_ID,
)
from app.research.strategy_candidate_registry import (
    default_candidate_row,
    strategy_candidate_registry,
)
from app.research.v1_production import V1_SYMBOLS, enabled_v1_books
from app.services.paper_classification import classification_fields_from_position
from app.services.telegram_alerts import is_v1_paper_alert
from app.services.v2_candidate_paper_watcher import (
    V2CandidatePaperWatcher,
    classify_v2_candidate,
    reset_v2_candidate_paper_watcher,
)


@pytest.fixture(autouse=True)
def _memory_registry():
    strategy_candidate_registry.force_memory = True
    strategy_candidate_registry.reset_memory()
    reset_v2_candidate_paper_watcher()
    yield
    strategy_candidate_registry.reset_memory()
    strategy_candidate_registry.force_memory = False
    reset_v2_candidate_paper_watcher()


@pytest.mark.asyncio
async def test_discovered_symbol_cannot_create_paper_trade():
    """Screener discovery only writes DISCOVERED — no paper, risk=0, TG=false."""
    row, is_new = await strategy_candidate_registry.upsert_discovered(
        symbol="BNBUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=50_000_000,
        discovery_reason="top_liquidity",
    )
    assert is_new
    assert row["state"] == "DISCOVERED"
    assert float(row["risk_percent"]) == 0.0
    assert row["telegram_eligible"] is False
    assert row["operator_approved"] is False
    assert row["strategy_id"] == STRATEGY_ID

    watcher = V2CandidatePaperWatcher(enabled=True, replay_mode=True)
    # Not PAPER_VALIDATING → watcher must ignore
    pos = await watcher.on_closed_1h(
        "BNBUSDT",
        candles_1h=[{"time": "2026-01-01T00:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 60,
        candles_4h=[{"time": "2026-01-01T00:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 30,
    )
    assert pos is None
    assert "not_paper_validating" in (watcher._last_skip or "")


@pytest.mark.asyncio
async def test_missing_4h_remains_data_pending():
    await strategy_candidate_registry.upsert_discovered(
        symbol="ADAUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=2,
        volume_usd=20_000_000,
        discovery_reason="top_liquidity",
    )
    # Inject 1h-only health fields
    await strategy_candidate_registry.update_fields(
        "ADAUSDT",
        {
            "ohlcv_1h_start_utc": "2024-01-01T00:00:00+00:00",
            "ohlcv_1h_end_utc": "2026-01-01T00:00:00+00:00",
            "ohlcv_1h_completeness": 0.995,
            "history_days": 700,
            "_test_bars_1h": 16000,
            # 4h intentionally missing
            "ohlcv_4h_completeness": None,
            "ohlcv_4h_start_utc": None,
        },
    )
    result = await check_candidate_data_health("ADAUSDT", queue_backfill=False)
    assert result["ready"] is False
    assert result["state"] == "DATA_PENDING"
    assert any("missing 4h" in b for b in result["blocks"])
    row = await strategy_candidate_registry.get_by_symbol("ADAUSDT")
    assert row["state"] == "DATA_PENDING"
    assert float(row["risk_percent"]) == 0.0


def test_evaluate_health_block_messages():
    bad = evaluate_health(
        {
            "exists": True,
            "bars": 10000,
            "history_days": 402,
            "completeness": 0.995,
            "duplicate_count": 0,
            "gap_count": 0,
            "boundary_ok": True,
        },
        {
            "exists": True,
            "bars": 2500,
            "history_days": 402,
            "completeness": 0.978,
            "duplicate_count": 0,
            "gap_count": 0,
            "boundary_ok": True,
        },
    )
    assert bad["ready"] is False
    assert any("1h history 402" in b for b in bad["blocks"])
    assert any("4h completeness 97.8%" in b for b in bad["blocks"])


@pytest.mark.asyncio
async def test_background_jobs_cannot_promote_to_paper_or_approved():
    await strategy_candidate_registry.upsert_discovered(
        symbol="XRPUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=3,
        volume_usd=30_000_000,
        discovery_reason="top_liquidity",
    )
    # Force to V2_PAPER_CANDIDATE via controlled transitions
    for nxt, reason in [
        ("DATA_PENDING", "t"),
        ("DATA_READY", "t"),
        ("BACKTEST_QUEUED", "t"),
        ("BACKTEST_RUNNING", "t"),
        ("PROMISING", "t"),
        ("OOS_PENDING", "t"),
        ("V2_PAPER_CANDIDATE", "t"),
    ]:
        await strategy_candidate_registry.transition(
            "XRPUSDT", nxt, reason=reason, actor="test_setup"
        )

    with pytest.raises(OperatorOnlyTransition):
        await strategy_candidate_registry.transition(
            "XRPUSDT",
            "PAPER_VALIDATING",
            reason="background",
            actor="background_job",
            operator_approved_action=False,
        )

    with pytest.raises((OperatorOnlyTransition, IllegalStateTransition)):
        await strategy_candidate_registry.transition(
            "XRPUSDT",
            "APPROVED",
            reason="background",
            actor="background_job",
            operator_approved_action=False,
        )

    row = await strategy_candidate_registry.get_by_symbol("XRPUSDT")
    assert row["state"] == "V2_PAPER_CANDIDATE"
    assert row["telegram_eligible"] is False


@pytest.mark.asyncio
async def test_operator_approval_rejects_invalid_state_and_excessive_risk():
    await strategy_candidate_registry.upsert_discovered(
        symbol="LINKUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=4,
        volume_usd=15_000_000,
        discovery_reason="top_liquidity",
    )
    with pytest.raises(PermissionError):
        await strategy_candidate_registry.approve_paper(
            "LINKUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )

    for nxt in [
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "V2_PAPER_CANDIDATE",
    ]:
        await strategy_candidate_registry.transition(
            "LINKUSDT", nxt, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        "LINKUSDT",
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {"recommendation": "V2_PAPER_ONLY", "portfolio_fail": False},
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
        },
    )

    with pytest.raises(PermissionError, match="0\\.50%|cannot exceed"):
        await strategy_candidate_registry.approve_paper(
            "LINKUSDT",
            confirm=True,
            approval_note="too much risk",
            requested_risk_percent=0.01,
            actor="op",
            risk_override_above_half_pct=False,
        )

    with pytest.raises(PermissionError, match="0\\.25%|explicit override"):
        await strategy_candidate_registry.approve_paper(
            "LINKUSDT",
            confirm=True,
            approval_note="needs override",
            requested_risk_percent=0.004,
            actor="op",
            risk_override_above_default=False,
        )

    row = await strategy_candidate_registry.approve_paper(
        "LINKUSDT",
        confirm=True,
        approval_note="Reviewed OOS report and portfolio overlap.",
        requested_risk_percent=0.0025,
        actor="op",
    )
    assert row["state"] == "PAPER_VALIDATING"
    assert row["operator_approved"] is True
    assert float(row["risk_percent"]) == 0.0025
    assert row["telegram_eligible"] is False
    audit = await strategy_candidate_registry.list_audit("LINKUSDT")
    assert any(a["action"] == "APPROVE_PAPER" for a in audit)


@pytest.mark.asyncio
async def test_approved_v2_remains_telegram_ineligible():
    await strategy_candidate_registry.upsert_discovered(
        symbol="DOTUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=5,
        volume_usd=12_000_000,
        discovery_reason="top_liquidity",
    )
    for nxt in [
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "V2_PAPER_CANDIDATE",
    ]:
        await strategy_candidate_registry.transition(
            "DOTUSDT", nxt, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        "DOTUSDT",
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {"ok": True, "portfolio_fail": False},
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
        },
    )
    row = await strategy_candidate_registry.approve_paper(
        "DOTUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )
    assert row["telegram_eligible"] is False
    # Persist strips any attempt to flip telegram
    await strategy_candidate_registry.update_fields(
        "DOTUSDT", {"telegram_eligible": True}, actor="attacker"
    )
    row2 = await strategy_candidate_registry.get_by_symbol("DOTUSDT")
    assert row2["telegram_eligible"] is False


@pytest.mark.asyncio
async def test_v2_watcher_only_sees_paper_validating():
    await strategy_candidate_registry.upsert_discovered(
        symbol="AVAXUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=6,
        volume_usd=18_000_000,
        discovery_reason="top_liquidity",
    )
    watcher = V2CandidatePaperWatcher(enabled=True)
    assert await watcher.eligible_symbols() == []

    for nxt in [
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "V2_PAPER_CANDIDATE",
    ]:
        await strategy_candidate_registry.transition(
            "AVAXUSDT", nxt, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        "AVAXUSDT",
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {"ok": True, "portfolio_fail": False},
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
        },
    )
    await strategy_candidate_registry.approve_paper(
        "AVAXUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )
    rows = await watcher.eligible_symbols()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AVAXUSDT"
    assert rows[0]["state"] == "PAPER_VALIDATING"
    assert rows[0]["telegram_eligible"] is False


def test_v2_watcher_fails_closed_without_4h():
    watcher = V2CandidatePaperWatcher(enabled=True, replay_mode=True)
    result = watcher.evaluate_closed_bar(
        "BNBUSDT",
        candles_1h=[{"time": "2026-01-01T00:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 60,
        candles_4h=[],
        risk_percent=0.0025,
    )
    assert result["status"] == "NO_SETUP"
    assert result["reason"] == "missing_4h"


def test_v1_universe_unchanged():
    from app.research.v1_production import FROZEN_V1_SYMBOLS

    assert FROZEN_V1_SYMBOLS == frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
    assert FROZEN_V1_SYMBOLS <= V1_SYMBOLS
    books = enabled_v1_books()
    assert FROZEN_V1_SYMBOLS <= {b.symbol for b in books}
    assert "BNBUSDT" in {b.symbol for b in books}
    # v1_production.py must not reference dynamic pipeline
    root = Path(__file__).resolve().parents[1]
    v1 = (root / "app" / "research" / "v1_production.py").read_text(encoding="utf-8")
    assert "DYNAMIC_CANDIDATE" not in v1
    assert "COMBO_02_V2_RESEARCH" not in v1
    watcher_src = (root / "app" / "services" / "v1_paper_watcher.py").read_text(
        encoding="utf-8"
    )
    assert "strategy_candidate_registry" not in watcher_src
    assert "DYNAMIC_CANDIDATE" not in watcher_src


def test_dynamic_events_cannot_reach_v1_telegram():
    snip = classify_v2_candidate(
        symbol="BNBUSDT",
        timeframe="1h",
        risk_percent=0.0025,
        htf_alignment="HTF_ALIGNED",
        trend_1h="BULLISH",
        trend_4h="BULLISH",
    )
    # Even if an attacker flips telegram_eligible on the alert envelope:
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "BNBUSDT",
        "payload": {
            "symbol": "BNBUSDT",
            "timeframe": "1h",
            "telegram_eligible": True,
            "signal_snippet": {**snip, "telegram_eligible": True},
        },
    }
    assert is_v1_paper_alert(alert) is False
    fields = classification_fields_from_position(alert)
    # classification re-evaluates; v2 identity is not v1
    assert fields["strategy_id"] == STRATEGY_ID
    assert fields["telegram_eligible"] is False or fields["source"] == SOURCE_WATCHER


def test_state_transitions_deterministic():
    assert_transition_allowed("DISCOVERED", "DATA_PENDING")
    assert_transition_allowed("DATA_PENDING", "DATA_READY")
    assert_transition_allowed("V2_PAPER_CANDIDATE", "PAPER_VALIDATING", operator_approved_action=True)
    with pytest.raises(OperatorOnlyTransition):
        assert_transition_allowed(
            "V2_PAPER_CANDIDATE", "PAPER_VALIDATING", operator_approved_action=False
        )
    with pytest.raises(IllegalStateTransition):
        assert_transition_allowed("DISCOVERED", "APPROVED", operator_approved_action=True)


def test_default_row_identity_constants():
    row = default_candidate_row("BNBUSDT")
    assert row["strategy_id"] == STRATEGY_ID
    assert row["combo_version"] == COMBO_VERSION
    assert row["source"] == SOURCE_PIPELINE
    assert row["telegram_eligible"] is False
    assert row["production_approved"] is False
    assert row["risk_percent"] == 0.0


@pytest.mark.asyncio
async def test_registry_summary_counts_durable_states():
    await strategy_candidate_registry.upsert_discovered(
        symbol="AAAUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    await strategy_candidate_registry.upsert_discovered(
        symbol="BBBUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=2,
        volume_usd=1.0,
        discovery_reason="t",
    )
    await strategy_candidate_registry.transition(
        "BBBUSDT", "DATA_PENDING", reason="t", actor="test"
    )
    await strategy_candidate_registry.transition(
        "BBBUSDT", "DATA_READY", reason="t", actor="test"
    )
    await strategy_candidate_registry.upsert_discovered(
        symbol="CCCUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=3,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
    ):
        await strategy_candidate_registry.transition(
            "CCCUSDT", st, reason="t", actor="test"
        )

    summary = await strategy_candidate_registry.registry_summary()
    assert summary["discovered"] == 1
    assert summary["data_ready"] == 1
    assert summary["backtest_completed"] == 1  # PROMISING
    assert summary["research_rejected"] == 0
    assert summary["v2_paper_candidate"] == 0
    assert summary["paper_validating"] == 0
    assert summary["production_approved"] == 0
    # identity: summary must not invent a production path
    assert "enable_for_v1" not in summary


@pytest.mark.asyncio
async def test_advance_splits_run_summary_from_registry_summary():
    """Registry can already have completed rows while this run did nothing."""
    from app.research import advance_dynamic_candidates as adv

    await strategy_candidate_registry.upsert_discovered(
        symbol="DDDUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "V2_PAPER_CANDIDATE",
    ):
        await strategy_candidate_registry.transition(
            "DDDUSDT", st, reason="t", actor="test"
        )

    # Already past health/backtest/oos — advance should not re-run gates.
    out = await adv.advance_dynamic_candidates(
        symbols=["DDDUSDT"],
        run_data_health=True,
        run_backtest=True,
        run_oos=True,
    )
    assert "run_summary" in out
    assert "registry_summary" in out
    assert out["run_summary"] == {
        "health_ready": 0,
        "backtests_started": 0,
        "oos_started": 0,
        "rejected": 0,
        "advanced": 0,
        "errors": 0,
    }
    assert out["registry_summary"]["v2_paper_candidate"] == 1
    assert out["registry_summary"]["discovered"] == 0
    # Old flat counters removed so UI cannot conflate this-run with registry.
    assert "health_ready" not in out
    assert "backtests_run" not in out
    assert "oos_run" not in out
    assert out.get("telegram_eligible") is False
    assert out.get("paper_trades_created") == 0
    assert out["strategy_id"] == STRATEGY_ID


def test_pipeline_modules_do_not_mutate_v1_ast():
    """Static guard: pipeline files never assign into v1 production symbols."""
    root = Path(__file__).resolve().parents[1] / "app"
    files = [
        root / "research" / "dynamic_candidate_discovery.py",
        root / "research" / "candidate_data_health.py",
        root / "research" / "dynamic_candidate_pipeline.py",
        root / "research" / "strategy_candidate_registry.py",
        root / "services" / "v2_candidate_paper_watcher.py",
    ]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        src = path.read_text(encoding="utf-8")
        assert "V1_SYMBOLS.add" not in src
        assert "enabled_v1_books" not in src or path.name == "v2_candidate_paper_watcher.py"
        # Ensure no writes to v1_production module attributes
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "v1_production":
                    raise AssertionError(f"{path.name} references v1_production attr")


async def _seed_v2_paper_candidate(symbol: str) -> None:
    await strategy_candidate_registry.upsert_discovered(
        symbol=symbol,
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "V2_PAPER_CANDIDATE",
    ):
        await strategy_candidate_registry.transition(
            symbol, st, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        symbol,
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {
                "recommendation": "V2_PAPER_ONLY",
                "portfolio_fail": False,
                "base_eligibility": {
                    "tier": "PROMISING",
                    "passed": True,
                    "reasons": [
                        {
                            "rule": "net_avg_r_gt_0_25",
                            "actual": 0.4,
                            "required": "> 0.25",
                            "passed": True,
                        }
                    ],
                },
            },
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
            "backtest_trade_count": 30,
            "backtest_net_avg_r": 0.4,
            "backtest_profit_factor": 1.5,
            "oos_trade_count": 12,
            "oos_net_avg_r": 0.2,
        },
    )


@pytest.mark.asyncio
async def test_bnb_paper_validating_not_generic_approved():
    from app.research.strategy_candidate_registry import serialize_candidate

    await _seed_v2_paper_candidate("BNBUSDT")
    row = await strategy_candidate_registry.approve_paper(
        "BNBUSDT",
        confirm=True,
        approval_note="Reviewed base, OOS, and portfolio reports.",
        requested_risk_percent=0.0025,
        actor="op",
    )
    view = serialize_candidate(row)
    assert view["operational_state"] == "PAPER_VALIDATING"
    assert view["research_tier"] == "PROMISING"
    assert view["base_backtest_status"] == "PASS"
    assert view["oos_display_status"] == "PASS"
    assert view["production_approved"] is False
    assert view["production_approval"] == "NOT_APPROVED_FOR_PRODUCTION"
    assert view["telegram_eligible"] is False
    assert view["telegram_eligibility"] == "TELEGRAM_DISABLED"
    assert view["operator_paper_approval"] == "EXPERIMENTAL_PAPER_APPROVED"
    assert view["source"] == SOURCE_WATCHER
    assert "PAPER ONLY" in view["safety_badges"]
    assert "TELEGRAM OFF" in view["safety_badges"]
    assert float(view["risk_percent"]) == 0.0025
    # Never expose a bare approved label as the primary state.
    assert view["operational_state"] != "APPROVED"
    assert "approved" not in str(view["operational_state"]).lower() or "paper" in str(
        view["operator_paper_approval"]
    ).lower()
    # No bare "approved"/"not approved" in operator-facing label codes.
    for key in ("operator_paper_approval", "production_approval", "telegram_eligibility"):
        label = str(view[key]).lower()
        assert label not in ("approved", "not approved", "not_approved")
        assert "approved" not in label or "paper" in label or "production" in label


@pytest.mark.asyncio
async def test_oos_failed_and_research_rejected_cannot_paper_approve():
    await strategy_candidate_registry.upsert_discovered(
        symbol="SUIUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "OOS_FAILED",
    ):
        await strategy_candidate_registry.transition(
            "SUIUSDT", st, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        "SUIUSDT",
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "oos_status": "OOS_FAIL",
            "portfolio_report": {"portfolio_fail": True},
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
        },
    )
    with pytest.raises(PermissionError, match="OOS_FAILED"):
        await strategy_candidate_registry.approve_paper(
            "SUIUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )

    await strategy_candidate_registry.upsert_discovered(
        symbol="LINKUSDT2",
        selector_version="test",
        manifest_id="m1",
        rank=2,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "RESEARCH_REJECTED",
    ):
        await strategy_candidate_registry.transition(
            "LINKUSDT2", st, reason="setup", actor="test"
        )
    with pytest.raises(PermissionError, match="RESEARCH_REJECTED"):
        await strategy_candidate_registry.approve_paper(
            "LINKUSDT2",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )


@pytest.mark.asyncio
async def test_promising_without_oos_cannot_paper_approve():
    await strategy_candidate_registry.upsert_discovered(
        symbol="NEARUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
    ):
        await strategy_candidate_registry.transition(
            "NEARUSDT", st, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        "NEARUSDT",
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
            "portfolio_report": {"ok": True},
        },
    )
    with pytest.raises(PermissionError, match="PROMISING"):
        await strategy_candidate_registry.approve_paper(
            "NEARUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )


@pytest.mark.asyncio
async def test_every_approve_transition_writes_audit():
    await _seed_v2_paper_candidate("APTUSDT")
    await strategy_candidate_registry.approve_paper(
        "APTUSDT",
        confirm=True,
        approval_note="audit me",
        requested_risk_percent=0.0025,
        actor="alice",
    )
    audit = await strategy_candidate_registry.list_audit("APTUSDT")
    approve = [a for a in audit if a["action"] == "APPROVE_PAPER"]
    assert len(approve) == 1
    assert approve[0]["previous_state"] == "V2_PAPER_CANDIDATE"
    assert approve[0]["new_state"] == "PAPER_VALIDATING"
    assert approve[0]["old_state"] == "V2_PAPER_CANDIDATE"
    assert approve[0]["operator"] == "alice"
    assert approve[0]["actor"] == "alice"
    assert float(approve[0]["previous_risk"] or 0) == 0.0
    assert float(approve[0]["new_risk"]) == 0.0025
    assert float(approve[0]["risk_before"] or 0) == 0.0
    assert float(approve[0]["risk_after"]) == 0.0025
    assert approve[0]["reason"] == "audit me"
    assert approve[0].get("timestamp") is not None
    assert approve[0]["payload"].get("production_approved") is False
    assert approve[0]["payload"].get("telegram_eligible") is False
    assert approve[0]["payload"].get("source") == SOURCE_WATCHER


@pytest.mark.asyncio
async def test_risk_cap_persists_blocked_portfolio_risk_cap():
    await _seed_v2_paper_candidate("OPUSDT")
    await strategy_candidate_registry.approve_paper(
        "OPUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )

    class _FakePaper:
        def snapshot(self):
            return {
                "open": [
                    {
                        "signal_snippet": {
                            "source": "V1_PAPER_WATCHER",
                            "strategy_id": "COMBO_02_V1",
                            "risk_percent": 0.049,
                        }
                    }
                ]
            }

        def status(self):
            return {"open": []}

    watcher = V2CandidatePaperWatcher(
        enabled=True,
        replay_mode=True,
        paper_engine=_FakePaper(),
        max_v1_book_risk_percent=0.05,
        max_total_risk_percent=0.01,
    )
    # Force past entry gate by stubbing evaluate + entry check path:
    # call _persist via the combined-risk branch using on_closed_1h with mocked eval.
    candles = [
        {"time": f"2026-01-01T{i:02d}:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for i in range(60)
    ]
    candles_4h = [
        {"time": f"2026-01-01T{i:02d}:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for i in range(30)
    ]

    # Monkeypatch evaluate / entry to force open attempt
    watcher.evaluate_closed_bar = lambda *a, **k: {  # type: ignore[method-assign]
        "status": "SETUP",
        "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
    }
    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    try:
        pos = await watcher.on_closed_1h(
            "OPUSDT", candles_1h=candles, candles_4h=candles_4h, force=True
        )
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]

    assert pos is None
    assert "blocked_portfolio_risk_cap" in (watcher._last_skip or "")
    row = await strategy_candidate_registry.get_by_symbol("OPUSDT")
    assert row is not None
    assert (row.get("portfolio_report") or {}).get("last_block_reason") == (
        "blocked_portfolio_risk_cap"
    )


@pytest.mark.asyncio
async def test_bnb_detail_separates_base_oos_portfolio_approval():
    from app.research.strategy_candidate_registry import serialize_candidate

    await _seed_v2_paper_candidate("BNBUSDT")
    await strategy_candidate_registry.approve_paper(
        "BNBUSDT",
        confirm=True,
        approval_note="Reviewed",
        requested_risk_percent=0.0025,
        actor="op",
    )
    view = serialize_candidate(
        await strategy_candidate_registry.get_by_symbol("BNBUSDT")  # type: ignore[arg-type]
    )
    assert view["base_research_status"] == "PROMISING" or view["backtest_tier"] == "PROMISING"
    assert view["oos_status"] == "V2_PAPER_CANDIDATE"
    assert view["oos_display_status"] == "PASS"
    assert view["base_backtest_status"] == "PASS"
    assert view["portfolio_status"] in ("PASS", "REVIEWED_PASS", "V2_PAPER_ONLY", "CHECKED")
    assert view["production_approval"] == "NOT_APPROVED_FOR_PRODUCTION"
    assert view["telegram_eligibility"] == "TELEGRAM_DISABLED"
    assert view["operational_state"] == "PAPER_VALIDATING"
    assert view["source"] == SOURCE_WATCHER
    assert view["strategy_id"] == STRATEGY_ID
    assert view["combo_version"] == COMBO_VERSION
    assert float(view["risk_percent"]) == 0.0025
    assert view["production_approved"] is False
    assert view["telegram_eligible"] is False


async def _seed_partial_v2(symbol: str, **fields):
    await strategy_candidate_registry.upsert_discovered(
        symbol=symbol,
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "V2_PAPER_CANDIDATE",
    ):
        await strategy_candidate_registry.transition(
            symbol, st, reason="setup", actor="test"
        )
    base = {
        "backtest_status": "COMPLETED",
        "backtest_tier": "PROMISING",
        "oos_status": "V2_PAPER_CANDIDATE",
        "portfolio_report": {
            "recommendation": "V2_PAPER_ONLY",
            "portfolio_fail": False,
            "portfolio_status": "PASS",
        },
        "ohlcv_1h_completeness": 0.99,
        "ohlcv_4h_completeness": 0.99,
    }
    base.update(fields)
    await strategy_candidate_registry.update_fields(symbol, base)


@pytest.mark.asyncio
async def test_approval_rejects_backtest_artifact_but_failed():
    """Approval rejects when backtest artifact exists but backtest failed."""
    await _seed_partial_v2("FAILBTUSDT", backtest_status="ENGINE_ERROR")
    with pytest.raises(PermissionError, match="backtest_status"):
        await strategy_candidate_registry.approve_paper(
            "FAILBTUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )


@pytest.mark.asyncio
async def test_approval_rejects_oos_artifact_but_failed():
    """Approval rejects when OOS artifact exists but OOS_FAILED semantics."""
    await _seed_partial_v2("FAILOOSUSDT", oos_status="OOS_FAILED")
    # State is still V2_PAPER_CANDIDATE in this synthetic seed; OOS field fails.
    with pytest.raises(PermissionError, match="OOS"):
        await strategy_candidate_registry.approve_paper(
            "FAILOOSUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )


@pytest.mark.asyncio
async def test_approval_rejects_portfolio_artifact_but_failed():
    """Approval rejects when portfolio artifact exists but portfolio failed."""
    await _seed_partial_v2(
        "FAILPORTUSDT",
        portfolio_report={
            "recommendation": "REJECT",
            "portfolio_fail": True,
            "portfolio_status": "FAIL",
            "checked": True,
        },
    )
    with pytest.raises(PermissionError, match="portfolio"):
        await strategy_candidate_registry.approve_paper(
            "FAILPORTUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )


@pytest.mark.asyncio
async def test_approval_rejects_health_below_99():
    await _seed_partial_v2(
        "LOWHEALTHUSDT",
        ohlcv_1h_completeness=0.98,
        ohlcv_4h_completeness=0.99,
    )
    with pytest.raises(PermissionError, match="0\\.99|health|completeness"):
        await strategy_candidate_registry.approve_paper(
            "LOWHEALTHUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )


@pytest.mark.asyncio
async def test_risk_025_passes_without_override():
    await _seed_v2_paper_candidate("RISK025USDT")
    row = await strategy_candidate_registry.approve_paper(
        "RISK025USDT",
        confirm=True,
        approval_note="default risk",
        requested_risk_percent=0.0025,
        actor="op",
        risk_override_above_default=False,
    )
    assert float(row["risk_percent"]) == 0.0025
    assert row["production_approved"] is False


@pytest.mark.asyncio
async def test_risk_026_fails_without_override():
    await _seed_v2_paper_candidate("RISK026USDT")
    with pytest.raises(PermissionError, match="0\\.25%|explicit override"):
        await strategy_candidate_registry.approve_paper(
            "RISK026USDT",
            confirm=True,
            approval_note="too high",
            requested_risk_percent=0.0026,
            actor="op",
            risk_override_above_default=False,
        )


@pytest.mark.asyncio
async def test_risk_050_passes_only_with_override():
    await _seed_v2_paper_candidate("RISK050USDT")
    with pytest.raises(PermissionError, match="0\\.25%|explicit override"):
        await strategy_candidate_registry.approve_paper(
            "RISK050USDT",
            confirm=True,
            approval_note="needs flag",
            requested_risk_percent=0.005,
            actor="op",
            risk_override_above_default=False,
        )
    row = await strategy_candidate_registry.approve_paper(
        "RISK050USDT",
        confirm=True,
        approval_note="override accepted",
        requested_risk_percent=0.005,
        actor="op",
        risk_override_above_default=True,
    )
    assert float(row["risk_percent"]) == 0.005


@pytest.mark.asyncio
async def test_risk_051_fails_even_with_override():
    await _seed_v2_paper_candidate("RISK051USDT")
    with pytest.raises(PermissionError, match="0\\.50%|cannot exceed"):
        await strategy_candidate_registry.approve_paper(
            "RISK051USDT",
            confirm=True,
            approval_note="above hard cap",
            requested_risk_percent=0.0051,
            actor="op",
            risk_override_above_default=True,
            risk_override_above_half_pct=True,
        )


@pytest.mark.asyncio
async def test_watcher_persists_blocked_portfolio_risk_cap_fields():
    await _seed_v2_paper_candidate("BLOCKCAPUSDT")
    await strategy_candidate_registry.approve_paper(
        "BLOCKCAPUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )

    class _FakePaper:
        def snapshot(self):
            return {
                "open": [
                    {
                        "signal_snippet": {
                            "source": "V1_PAPER_WATCHER",
                            "strategy_id": "COMBO_02_V1",
                            "risk_percent": 0.049,
                        }
                    }
                ]
            }

        def status(self):
            return {"open": []}

    watcher = V2CandidatePaperWatcher(
        enabled=True,
        replay_mode=True,
        paper_engine=_FakePaper(),
        max_v1_book_risk_percent=0.05,
        max_total_risk_percent=0.01,
    )
    candles = [
        {
            "time": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        for i in range(60)
    ]
    candles_4h = [
        {
            "time": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        for i in range(30)
    ]
    watcher.evaluate_closed_bar = lambda *a, **k: {  # type: ignore[method-assign]
        "status": "SETUP",
        "htf": {
            "htf_alignment": "HTF_ALIGNED",
            "trend_1h": "BULLISH",
            "trend_4h": "BULLISH",
        },
    }
    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    try:
        pos = await watcher.on_closed_1h(
            "BLOCKCAPUSDT", candles_1h=candles, candles_4h=candles_4h, force=True
        )
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]

    assert pos is None
    row = await strategy_candidate_registry.get_by_symbol("BLOCKCAPUSDT")
    assert row is not None
    port = row.get("portfolio_report") or {}
    assert port.get("last_block_reason") == "blocked_portfolio_risk_cap"
    assert port.get("blocked_reason") == "blocked_portfolio_risk_cap"
    detail = port.get("last_block_detail") or {}
    assert detail.get("decision") == "BLOCKED"
    assert detail.get("reason") == "blocked_portfolio_risk_cap"
    assert "v1_open_risk_percent" in detail
    assert "dynamic_requested_risk_percent" in detail
    assert "max_total_risk_percent" in detail
    assert "peak_concurrent_positions" in detail
    assert detail.get("blocked_reason") == "blocked_portfolio_risk_cap"
    audit = await strategy_candidate_registry.list_audit("BLOCKCAPUSDT")
    assert any(a["action"] == "TRADE_DECISION_BLOCKED" for a in audit)


@pytest.mark.asyncio
async def test_api_exposes_production_approved_false():
    from app.research.strategy_candidate_registry import serialize_candidate

    await _seed_v2_paper_candidate("PRODFALSEUSDT")
    await strategy_candidate_registry.approve_paper(
        "PRODFALSEUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )
    row = await strategy_candidate_registry.get_by_symbol("PRODFALSEUSDT")
    assert row is not None
    assert row.get("production_approved") is False
    view = serialize_candidate(row)
    assert view["production_approved"] is False
    assert view["telegram_eligible"] is False


@pytest.mark.asyncio
async def test_ui_labels_never_bare_approved():
    import re
    from app.research.strategy_candidate_registry import serialize_candidate
    from pathlib import Path

    panel = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "DynamicCandidatePipelinePanel.tsx"
    )
    src = panel.read_text(encoding="utf-8")
    # Forbid bare approved / not approved string literals used as UI copy.
    assert re.search(r'["\']approved["\']', src, re.I) is None
    assert re.search(r'["\']not approved["\']', src, re.I) is None
    assert "Paper not approved" not in src
    assert "Experimental paper approved" in src
    assert "Not approved for paper" in src
    assert "Not approved for production" in src
    assert "Telegram disabled" in src
    assert "Paper approved by" in src
    assert "Source:" in src

    await _seed_v2_paper_candidate("LABELUSDT")
    view = serialize_candidate(
        await strategy_candidate_registry.get_by_symbol("LABELUSDT")  # type: ignore[arg-type]
    )
    for key in ("operator_paper_approval", "production_approval"):
        assert str(view[key]).lower() not in ("approved", "not approved", "not_approved")


@pytest.mark.asyncio
async def test_detail_endpoint_fields_and_rule_conditions():
    from app.research.strategy_candidate_registry import serialize_candidate

    await _seed_v2_paper_candidate("DETAILUSDT")
    await strategy_candidate_registry.approve_paper(
        "DETAILUSDT",
        confirm=True,
        approval_note="Reviewed",
        requested_risk_percent=0.0025,
        actor="op",
    )
    view = serialize_candidate(
        await strategy_candidate_registry.get_by_symbol("DETAILUSDT")  # type: ignore[arg-type]
    )
    assert view["eligibility_reasons"]
    assert view["eligibility_reasons"][0]["passed"] is True
    assert "rule" in view["eligibility_reasons"][0]
    assert view["production_approved"] is False
    assert view["experimental_paper_approved"] is True
    # Frontend client must call the detail route.
    client = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "client.ts"
    ).read_text(encoding="utf-8")
    assert "fetchDynamicCandidateDetail" in client
    panel = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "DynamicCandidatePipelinePanel.tsx"
    ).read_text(encoding="utf-8")
    assert "fetchDynamicCandidateDetail" in panel
    assert "Backtest checks" in panel
    assert "OOS checks" in panel


@pytest.mark.asyncio
async def test_dynamic_candidate_never_enters_v1_monitoring_or_telegram():
    await _seed_v2_paper_candidate("ISOUSDT")
    row = await strategy_candidate_registry.approve_paper(
        "ISOUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )
    assert row["symbol"] not in V1_SYMBOLS
    assert row["telegram_eligible"] is False
    assert row["production_approved"] is False
    assert row["strategy_id"] == STRATEGY_ID
    assert row["source"] == SOURCE_WATCHER
    snip = classify_v2_candidate(
        symbol="ISOUSDT",
        timeframe="1h",
        risk_percent=0.0025,
        htf_alignment="HTF_ALIGNED",
    )
    assert snip["telegram_eligible"] is False
    assert snip["strategy_id"] != "COMBO_02_V1"
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "ISOUSDT",
        "payload": {
            "symbol": "ISOUSDT",
            "timeframe": "1h",
            "telegram_eligible": True,
            "signal_snippet": {**snip, "telegram_eligible": True},
        },
    }
    assert is_v1_paper_alert(alert) is False
    books = enabled_v1_books()
    assert "ISOUSDT" not in {str(getattr(b, "symbol", b)).upper() for b in books}


@pytest.mark.asyncio
async def test_can_approve_for_paper_helper_and_sui_rejects():
    from app.research.strategy_candidate_registry import can_approve_for_paper

    await _seed_v2_paper_candidate("BNBUSDT")
    row = await strategy_candidate_registry.get_by_symbol("BNBUSDT")
    assert can_approve_for_paper(row) is True

    await strategy_candidate_registry.upsert_discovered(
        symbol="SUIUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    for st in (
        "DATA_PENDING",
        "DATA_READY",
        "BACKTEST_QUEUED",
        "BACKTEST_RUNNING",
        "PROMISING",
        "OOS_PENDING",
        "OOS_FAILED",
    ):
        await strategy_candidate_registry.transition(
            "SUIUSDT", st, reason="setup", actor="test"
        )
    await strategy_candidate_registry.update_fields(
        "SUIUSDT",
        {
            "backtest_status": "COMPLETED",
            "backtest_tier": "PROMISING",
            "oos_status": "OOS_FAILED",
            "portfolio_report": {"portfolio_fail": True, "portfolio_status": "FAIL"},
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
        },
    )
    sui = await strategy_candidate_registry.get_by_symbol("SUIUSDT")
    assert can_approve_for_paper(sui) is False
    with pytest.raises(PermissionError, match="OOS_FAILED"):
        await strategy_candidate_registry.approve_paper(
            "SUIUSDT",
            confirm=True,
            approval_note="nope",
            requested_risk_percent=0.0025,
            actor="op",
        )
    watcher = V2CandidatePaperWatcher(enabled=True)
    eligible = await watcher.eligible_symbols()
    assert all(str(r.get("symbol")) != "SUIUSDT" for r in eligible)


@pytest.mark.asyncio
async def test_forbidden_transitions_to_paper_validating():
    """RESEARCH_REJECTED / OOS_FAILED / PROMISING / DATA_PENDING cannot → PAPER_VALIDATING."""
    for sym, path in (
        (
            "REJUSDT",
            (
                "DATA_PENDING",
                "DATA_READY",
                "BACKTEST_QUEUED",
                "BACKTEST_RUNNING",
                "RESEARCH_REJECTED",
            ),
        ),
        (
            "OOSFAILT",
            (
                "DATA_PENDING",
                "DATA_READY",
                "BACKTEST_QUEUED",
                "BACKTEST_RUNNING",
                "PROMISING",
                "OOS_PENDING",
                "OOS_FAILED",
            ),
        ),
        (
            "PROMONLY",
            (
                "DATA_PENDING",
                "DATA_READY",
                "BACKTEST_QUEUED",
                "BACKTEST_RUNNING",
                "PROMISING",
            ),
        ),
        ("DATAPEND", ("DATA_PENDING",)),
    ):
        await strategy_candidate_registry.upsert_discovered(
            symbol=sym,
            selector_version="test",
            manifest_id="m1",
            rank=1,
            volume_usd=1.0,
            discovery_reason="t",
        )
        for st in path:
            await strategy_candidate_registry.transition(
                sym, st, reason="setup", actor="test"
            )
        with pytest.raises((PermissionError, IllegalStateTransition, OperatorOnlyTransition)):
            await strategy_candidate_registry.transition(
                sym,
                "PAPER_VALIDATING",
                reason="bypass",
                actor="attacker",
                operator_approved_action=True,
            )


@pytest.mark.asyncio
async def test_dynamic_cannot_become_production_approved():
    await _seed_v2_paper_candidate("NOPRODUSDT")
    await strategy_candidate_registry.approve_paper(
        "NOPRODUSDT",
        confirm=True,
        approval_note="ok",
        requested_risk_percent=0.0025,
        actor="op",
    )
    with pytest.raises(PermissionError, match="production-approved"):
        await strategy_candidate_registry.transition(
            "NOPRODUSDT",
            "APPROVED",
            reason="try production",
            actor="attacker",
            operator_approved_action=True,
        )
    row = await strategy_candidate_registry.get_by_symbol("NOPRODUSDT")
    assert row is not None
    assert row["production_approved"] is False
    assert row["state"] == "PAPER_VALIDATING"


@pytest.mark.asyncio
async def test_transition_audit_contains_contract_fields():
    await strategy_candidate_registry.upsert_discovered(
        symbol="AUDUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    await strategy_candidate_registry.transition(
        "AUDUSDT",
        "DATA_PENDING",
        reason="health_check",
        actor="pipeline",
    )
    audit = await strategy_candidate_registry.list_audit("AUDUSDT")
    trans = [a for a in audit if str(a.get("action", "")).startswith("TRANSITION:")]
    assert trans
    entry = trans[0]
    assert entry["symbol"] == "AUDUSDT"
    assert entry["old_state"] == "DISCOVERED"
    assert entry["new_state"] == "DATA_PENDING"
    assert entry["operator"] == "pipeline"
    assert entry["reason"] == "health_check"
    assert "risk_before" in entry
    assert "risk_after" in entry
    assert entry.get("timestamp") is not None


@pytest.mark.asyncio
async def test_bnb_approval_requires_all_explicit_pass_values():
    from app.research.strategy_candidate_registry import (
        can_approve_for_paper,
        serialize_candidate,
    )

    await _seed_v2_paper_candidate("BNBUSDT")
    row = await strategy_candidate_registry.get_by_symbol("BNBUSDT")
    assert can_approve_for_paper(row) is True
    approved = await strategy_candidate_registry.approve_paper(
        "BNBUSDT",
        confirm=True,
        approval_note="All passes reviewed",
        requested_risk_percent=0.0025,
        actor="op",
    )
    view = serialize_candidate(approved)
    assert view["research_tier"] == "PROMISING"
    assert view["base_backtest_status"] == "PASS"
    assert view["oos_display_status"] == "PASS"
    assert view["portfolio_status"] in ("PASS", "REVIEWED_PASS")
    assert view["operational_state"] == "PAPER_VALIDATING"
    assert float(view["risk_percent"]) == 0.0025
    assert view["production_approved"] is False
    assert view["telegram_eligible"] is False
    assert view["strategy_id"] == STRATEGY_ID
    assert view["combo_version"] == COMBO_VERSION
    assert view["source"] == SOURCE_WATCHER


@pytest.mark.asyncio
async def test_run_summary_and_registry_summary_keys_remain_separate():
    from app.research.advance_dynamic_candidates import advance_dynamic_candidates

    await strategy_candidate_registry.upsert_discovered(
        symbol="SUMUSDT",
        selector_version="test",
        manifest_id="m1",
        rank=1,
        volume_usd=1.0,
        discovery_reason="t",
    )
    out = await advance_dynamic_candidates(
        symbols=["SUMUSDT"],
        run_backtest=False,
        run_oos=False,
    )
    assert "run_summary" in out
    assert "registry_summary" in out
    run = out["run_summary"]
    reg = out["registry_summary"]
    for k in (
        "health_ready",
        "backtests_started",
        "oos_started",
        "rejected",
        "advanced",
        "errors",
    ):
        assert k in run
    for k in (
        "discovered",
        "data_pending",
        "data_ready",
        "backtest_completed",
        "research_rejected",
        "oos_failed",
        "v2_paper_candidate",
        "paper_validating",
        "production_approved",
        "suspended",
    ):
        assert k in reg
    # Meanings stay separate: run keys must not appear as registry identity.
    assert "health_ready" not in reg
    assert "discovered" not in run


@pytest.mark.asyncio
async def test_update_fields_cannot_set_risk_above_hard_cap():
    await _seed_v2_paper_candidate("RISKCAPUSDT")
    with pytest.raises(PermissionError, match="0\\.50%|exceed"):
        await strategy_candidate_registry.update_fields(
            "RISKCAPUSDT",
            {"risk_percent": 0.02},
            actor="attacker",
        )
    with pytest.raises(PermissionError, match="0\\.25%|approve_paper"):
        await strategy_candidate_registry.update_fields(
            "RISKCAPUSDT",
            {"risk_percent": 0.004},
            actor="attacker",
        )
