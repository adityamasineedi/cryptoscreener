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
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {"recommendation": "V2_PAPER_ONLY"},
            "ohlcv_1h_completeness": 0.99,
            "ohlcv_4h_completeness": 0.99,
        },
    )

    with pytest.raises(PermissionError, match="0.5%"):
        await strategy_candidate_registry.approve_paper(
            "LINKUSDT",
            confirm=True,
            approval_note="too much risk",
            requested_risk_percent=0.01,
            actor="op",
            risk_override_above_half_pct=False,
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
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {"ok": True},
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
            "oos_status": "V2_PAPER_CANDIDATE",
            "portfolio_report": {"ok": True},
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
    assert V1_SYMBOLS == frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
    books = enabled_v1_books()
    assert {b.symbol for b in books} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
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
    assert row["risk_percent"] == 0.0


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
