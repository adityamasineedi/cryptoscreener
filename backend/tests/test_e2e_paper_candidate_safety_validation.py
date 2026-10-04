"""End-to-end paper candidate safety VALIDATION (read-only against production code).

MODE: VALIDATION ONLY — does not change strategy logic, production approval,
dynamic Telegram, or v1 behavior. Reports safety posture for BNB/SUI lifecycle.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
from pathlib import Path

import pytest

from app.research.candidate_state_machine import (
    IllegalStateTransition,
    OperatorOnlyTransition,
)
from app.research.dynamic_candidate_constants import (
    COMBO_VERSION,
    MAX_DEFAULT_RISK,
    MAX_OVERRIDE_RISK,
    SOURCE_WATCHER,
    STRATEGY_ID,
)
from app.research.strategy_candidate_registry import (
    can_approve_for_paper,
    serialize_candidate,
    strategy_candidate_registry,
)
from app.research.v1_production import V1_SYMBOLS, enabled_v1_books
from app.services.paper_trade import PaperTradeEngine
from app.services.telegram_alerts import is_v1_paper_alert
from app.services.v2_candidate_paper_watcher import (
    V2CandidatePaperWatcher,
    classify_v2_candidate,
    get_v2_candidate_paper_watcher,
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


async def _seed_v2_paper_candidate(symbol: str, **field_overrides) -> None:
    await strategy_candidate_registry.upsert_discovered(
        symbol=symbol,
        selector_version="validation",
        manifest_id="e2e-safety",
        rank=1,
        volume_usd=1.0,
        discovery_reason="e2e_validation",
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
            symbol, st, reason="e2e_seed", actor="validation"
        )
    fields = {
        "backtest_status": "COMPLETED",
        "backtest_tier": "PROMISING",
        "oos_status": "V2_PAPER_CANDIDATE",
        "portfolio_report": {
            "recommendation": "V2_PAPER_ONLY",
            "portfolio_fail": False,
            "portfolio_status": "PASS",
            "base_eligibility": {
                "tier": "PROMISING",
                "passed": True,
                "reasons": [
                    {
                        "rule": "net_avg_r_gt_0_25",
                        "actual": 0.34,
                        "required": "> 0.25",
                        "passed": True,
                    }
                ],
            },
            "oos_conditions": [
                {
                    "rule": "oos_net_avg_r_gt_0",
                    "actual": 0.37,
                    "required": "> 0",
                    "passed": True,
                }
            ],
        },
        "ohlcv_1h_completeness": 0.99,
        "ohlcv_4h_completeness": 0.99,
        "backtest_trade_count": 30,
        "backtest_net_avg_r": 0.34,
        "backtest_profit_factor": 1.55,
        "oos_trade_count": 12,
        "oos_net_avg_r": 0.37,
    }
    fields.update(field_overrides)
    await strategy_candidate_registry.update_fields(symbol, fields)


def _candles(n: int = 60) -> list[dict]:
    return [
        {
            "time": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
        }
        for i in range(n)
    ]


def _eval_long() -> dict:
    return {
        "status": "SETUP",
        "entry_price": 100.0,
        "stop_price": 98.0,
        "tp1": 104.0,
        "htf": {
            "htf_alignment": "HTF_ALIGNED",
            "trend_1h": "BULLISH",
            "trend_4h": "BULLISH",
        },
    }


async def _approve_bnb(risk: float = 0.0025, **kwargs):
    await _seed_v2_paper_candidate("BNBUSDT")
    return await strategy_candidate_registry.approve_paper(
        "BNBUSDT",
        confirm=True,
        approval_note="e2e validation approve",
        requested_risk_percent=risk,
        actor="validator",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. BNB lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_bnb_lifecycle_research_to_paper_watcher():
    row = await _approve_bnb()
    view = serialize_candidate(row)

    assert view["strategy_id"] == STRATEGY_ID
    assert view["combo_version"] == COMBO_VERSION
    assert view["source"] == SOURCE_WATCHER
    assert view["research_tier"] == "PROMISING"
    assert view["base_backtest_status"] == "PASS"
    assert view["oos_display_status"] == "PASS"
    assert view["portfolio_status"] in ("PASS", "REVIEWED_PASS")
    assert view["operational_state"] == "PAPER_VALIDATING"
    assert view["production_approved"] is False
    assert view["telegram_eligible"] is False
    assert float(view["risk_percent"]) <= MAX_DEFAULT_RISK + 1e-15
    assert float(view["risk_percent"]) <= MAX_OVERRIDE_RISK + 1e-15

    # Audit for every successful transition in the seed path + approve
    audit = await strategy_candidate_registry.list_audit("BNBUSDT")
    actions = [a["action"] for a in audit]
    assert any(a.startswith("TRANSITION:") for a in actions)
    assert "APPROVE_PAPER" in actions
    for a in audit:
        if str(a.get("action", "")).startswith("TRANSITION:") or a.get("action") == "APPROVE_PAPER":
            assert a.get("symbol") == "BNBUSDT"
            assert a.get("old_state") is not None or a.get("previous_state") is not None
            assert a.get("new_state") is not None
            assert a.get("operator") is not None or a.get("actor") is not None
            assert "risk_before" in a or "previous_risk" in a
            assert "risk_after" in a or "new_risk" in a
            assert a.get("timestamp") is not None or a.get("created_at_utc") is not None

    # Paper watcher open (replay — no live order API)
    paper = PaperTradeEngine(enabled=True, starting_equity=10_000.0)
    watcher = V2CandidatePaperWatcher(
        enabled=True,
        replay_mode=True,
        emit_alerts=False,
        paper_engine=paper,
        max_open_positions=1,
        max_total_risk_percent=MAX_OVERRIDE_RISK,
        max_v1_book_risk_percent=0.05,
    )
    watcher.evaluate_closed_bar = lambda *a, **k: _eval_long()  # type: ignore[method-assign]
    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    try:
        pos = await watcher.on_closed_1h(
            "BNBUSDT",
            candles_1h=_candles(60),
            candles_4h=_candles(30),
            force=True,
        )
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]

    assert pos is not None, f"expected paper open, skip={watcher._last_skip}"
    snip = getattr(pos, "signal_snippet", None) or {}
    assert snip.get("strategy_id") == STRATEGY_ID
    assert snip.get("strategy_id") != "COMBO_02_V1"
    assert snip.get("source") == SOURCE_WATCHER
    assert snip.get("telegram_eligible") is False
    assert snip.get("production_approved") is False

    # No live order helpers on experimental open path
    src = inspect.getsource(PaperTradeEngine.open_experimental_position)
    assert "create_order" not in src
    assert "place_order" not in src
    assert "new_order" not in src


# ---------------------------------------------------------------------------
# 2. SUI blocked path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_sui_oos_failed_blocked_path():
    await strategy_candidate_registry.upsert_discovered(
        symbol="SUIUSDT",
        selector_version="validation",
        manifest_id="e2e-safety",
        rank=2,
        volume_usd=1.0,
        discovery_reason="e2e_validation",
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
            "SUIUSDT", st, reason="oos_fail", actor="validation"
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
    row = await strategy_candidate_registry.get_by_symbol("SUIUSDT")
    assert row is not None
    assert row["state"] == "OOS_FAILED"
    assert can_approve_for_paper(row) is False

    with pytest.raises(PermissionError, match="OOS_FAILED"):
        await strategy_candidate_registry.approve_paper(
            "SUIUSDT",
            confirm=True,
            approval_note="should fail",
            requested_risk_percent=0.0025,
            actor="validator",
        )

    after = await strategy_candidate_registry.get_by_symbol("SUIUSDT")
    assert after is not None
    assert after["state"] == "OOS_FAILED"
    assert after["production_approved"] is False
    assert after["telegram_eligible"] is False

    watcher = V2CandidatePaperWatcher(enabled=True, replay_mode=True)
    eligible = await watcher.eligible_symbols()
    assert all(str(r.get("symbol")) != "SUIUSDT" for r in eligible)

    paper = PaperTradeEngine(enabled=True, starting_equity=10_000.0)
    watcher2 = V2CandidatePaperWatcher(
        enabled=True, replay_mode=True, emit_alerts=False, paper_engine=paper
    )
    watcher2.evaluate_closed_bar = lambda *a, **k: _eval_long()  # type: ignore[method-assign]
    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    try:
        pos = await watcher2.on_closed_1h(
            "SUIUSDT",
            candles_1h=_candles(60),
            candles_4h=_candles(30),
            force=True,
        )
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]
    assert pos is None
    assert "not_paper_validating" in (watcher2._last_skip or "")

    # Rejection path: successful OOS_FAILED transition is audited; failed approve is not.
    audit = await strategy_candidate_registry.list_audit("SUIUSDT")
    assert any(
        a.get("new_state") == "OOS_FAILED" or "OOS_FAILED" in str(a.get("action"))
        for a in audit
    )


# ---------------------------------------------------------------------------
# 3. Invalid transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_invalid_transitions_rejected():
    # SUSPENDED → PAPER_VALIDATING
    await _seed_v2_paper_candidate("SUSUSDT")
    await strategy_candidate_registry.transition(
        "SUSUSDT", "SUSPENDED", reason="pause", actor="validation"
    )
    with pytest.raises((IllegalStateTransition, PermissionError, OperatorOnlyTransition)):
        await strategy_candidate_registry.transition(
            "SUSUSDT",
            "PAPER_VALIDATING",
            reason="illegal",
            actor="attacker",
            operator_approved_action=True,
        )

    # PAPER_VALIDATING → production-approved (APPROVED)
    await _approve_bnb()
    with pytest.raises(PermissionError, match="production-approved"):
        await strategy_candidate_registry.transition(
            "BNBUSDT",
            "APPROVED",
            reason="illegal prod",
            actor="attacker",
            operator_approved_action=True,
        )
    bnb = await strategy_candidate_registry.get_by_symbol("BNBUSDT")
    assert bnb is not None
    assert bnb["production_approved"] is False
    assert bnb["state"] == "PAPER_VALIDATING"

    # V2_PAPER_CANDIDATE → APPROVED (illegal edge)
    await _seed_v2_paper_candidate("V2PRODUSDT")
    with pytest.raises((IllegalStateTransition, PermissionError)):
        await strategy_candidate_registry.transition(
            "V2PRODUSDT",
            "APPROVED",
            reason="illegal",
            actor="attacker",
            operator_approved_action=True,
        )


# ---------------------------------------------------------------------------
# 4. Risk caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_risk_caps():
    await _seed_v2_paper_candidate("RISKOKUSDT")
    ok = await strategy_candidate_registry.approve_paper(
        "RISKOKUSDT",
        confirm=True,
        approval_note="0.25 ok",
        requested_risk_percent=0.0025,
        actor="validator",
    )
    assert float(ok["risk_percent"]) == 0.0025

    await _seed_v2_paper_candidate("RISKHIUSDT")
    with pytest.raises(PermissionError, match="0\\.25%|override"):
        await strategy_candidate_registry.approve_paper(
            "RISKHIUSDT",
            confirm=True,
            approval_note="0.26 no override",
            requested_risk_percent=0.0026,
            actor="validator",
        )

    await _seed_v2_paper_candidate("RISKMAXUSDT")
    with pytest.raises(PermissionError, match="0\\.50%|exceed"):
        await strategy_candidate_registry.approve_paper(
            "RISKMAXUSDT",
            confirm=True,
            approval_note="0.51 with override",
            requested_risk_percent=0.0051,
            actor="validator",
            risk_override_above_default=True,
        )


# ---------------------------------------------------------------------------
# 5. Duplicate watcher + duplicate paper-open + concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_duplicate_watcher_and_one_open_protections():
    await _approve_bnb()
    paper = PaperTradeEngine(enabled=True, starting_equity=10_000.0)

    reset_v2_candidate_paper_watcher()
    w1 = get_v2_candidate_paper_watcher(
        enabled=True,
        replay_mode=True,
        emit_alerts=False,
        paper_engine=paper,
    )
    w2 = get_v2_candidate_paper_watcher(
        enabled=True,
        replay_mode=True,
        emit_alerts=False,
        paper_engine=PaperTradeEngine(enabled=True),  # kwargs ignored after first
    )
    assert w1 is w2, "duplicate get_v2_candidate_paper_watcher must return same instance"

    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    w1.evaluate_closed_bar = lambda *a, **k: _eval_long()  # type: ignore[method-assign]
    try:
        pos1 = await w1.on_closed_1h(
            "BNBUSDT",
            candles_1h=_candles(60),
            candles_4h=_candles(30),
            force=True,
        )
        assert pos1 is not None
        # Duplicate open same bar (force) — book already has symbol open
        pos2 = await w1.on_closed_1h(
            "BNBUSDT",
            candles_1h=_candles(60),
            candles_4h=_candles(30),
            force=True,
        )
        assert pos2 is None
        open_n = len(getattr(paper, "_open", {}) or {})
        assert open_n == 1

        # Concurrent duplicate requests
        async def _once(force: bool):
            return await w1.on_closed_1h(
                "BNBUSDT",
                candles_1h=_candles(60),
                candles_4h=_candles(30),
                force=force,
            )

        results = await asyncio.gather(_once(True), _once(True), _once(True))
        assert all(r is None for r in results)
        assert len(paper._open) == 1
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_validation_direct_duplicate_experimental_open():
    await _approve_bnb()
    paper = PaperTradeEngine(enabled=True, starting_equity=10_000.0)
    snip = classify_v2_candidate(
        symbol="BNBUSDT",
        timeframe="1h",
        risk_percent=0.0025,
        htf_alignment="HTF_ALIGNED",
        extra={"production_approved": False},
    )
    eval_result = _eval_long()
    p1 = paper.open_experimental_position(
        symbol="BNBUSDT",
        timeframe="1h",
        eval_result=eval_result,
        risk_percent=0.0025,
        signal_snippet=snip,
        setup_bar_time_utc="2026-01-01T59:00:00+00:00",
        replay=True,
        emit_alert=False,
    )
    assert p1 is not None
    p2 = paper.open_experimental_position(
        symbol="BNBUSDT",
        timeframe="1h",
        eval_result=eval_result,
        risk_percent=0.0025,
        signal_snippet=snip,
        setup_bar_time_utc="2026-01-01T59:00:00+00:00",
        replay=True,
        emit_alert=False,
    )
    assert p2 is None
    assert len(paper._open) == 1


# ---------------------------------------------------------------------------
# 6. Restart persistence (in-memory registry snapshot = process restart sim)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_restart_persistence_no_duplicate_open():
    row = await _approve_bnb()
    paper = PaperTradeEngine(enabled=True, starting_equity=10_000.0)
    snip = classify_v2_candidate(
        symbol="BNBUSDT",
        timeframe="1h",
        risk_percent=0.0025,
        htf_alignment="HTF_ALIGNED",
    )
    pos = paper.open_experimental_position(
        symbol="BNBUSDT",
        timeframe="1h",
        eval_result=_eval_long(),
        risk_percent=0.0025,
        signal_snippet=snip,
        setup_bar_time_utc="2026-01-01T59:00:00+00:00",
        replay=True,
        emit_alert=False,
    )
    assert pos is not None

    # Snapshot persisted-like state
    saved_row = copy.deepcopy(row)
    saved_audit = copy.deepcopy(await strategy_candidate_registry.list_audit("BNBUSDT"))
    saved_pos = {
        "id": pos.id,
        "symbol": pos.symbol,
        "side": pos.side,
        "status": pos.status,
        "entry_price": pos.entry_price,
        "stop_price": pos.stop_price,
        "tp1_price": pos.tp1_price,
        "quantity": pos.quantity,
        "risk_usd": pos.risk_usd,
        "opened_at": pos.opened_at,
        "timeframe": pos.timeframe,
        "source_candle_ts": pos.source_candle_ts,
        "signal_snippet": copy.deepcopy(pos.signal_snippet),
    }

    # Simulate backend restart: clear registry + watcher + paper book, then reload
    strategy_candidate_registry.reset_memory()
    reset_v2_candidate_paper_watcher()
    paper2 = PaperTradeEngine(enabled=True, starting_equity=10_000.0)
    paper2.hydrate_from_rows([saved_pos])

    # Restore candidate registry row
    strategy_candidate_registry._store_memory(saved_row)  # noqa: SLF001
    reloaded = await strategy_candidate_registry.get_by_symbol("BNBUSDT")
    assert reloaded is not None
    view = serialize_candidate(reloaded)
    assert view["state"] == "PAPER_VALIDATING"
    assert view["source"] == SOURCE_WATCHER
    assert view["production_approved"] is False
    assert view["telegram_eligible"] is False
    assert float(view["risk_percent"]) == 0.0025
    assert view["strategy_id"] == STRATEGY_ID

    # Watcher must not duplicate the open position
    watcher = V2CandidatePaperWatcher(
        enabled=True,
        replay_mode=True,
        emit_alerts=False,
        paper_engine=paper2,
        max_open_positions=1,
    )
    watcher.evaluate_closed_bar = lambda *a, **k: _eval_long()  # type: ignore[method-assign]
    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    try:
        again = await watcher.on_closed_1h(
            "BNBUSDT",
            candles_1h=_candles(60),
            candles_4h=_candles(30),
            force=True,
        )
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]

    assert again is None
    assert len(paper2._open) == 1
    assert "BNBUSDT" in paper2._open
    # Prior audit snapshot existed (restart cleared memory audit — gap if DB not used)
    assert any(a.get("action") == "APPROVE_PAPER" for a in saved_audit)


# ---------------------------------------------------------------------------
# 7–8. Production + Telegram isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_production_and_telegram_isolation():
    row = await _approve_bnb()
    assert row["production_approved"] is False
    assert row["telegram_eligible"] is False
    assert row["symbol"] not in V1_SYMBOLS
    books = enabled_v1_books()
    assert "BNBUSDT" not in {b.symbol for b in books}

    snip = classify_v2_candidate(
        symbol="BNBUSDT",
        timeframe="1h",
        risk_percent=0.0025,
        htf_alignment="HTF_ALIGNED",
    )
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

    # emit_alerts=False on watcher path — open with emit_alert=False must not route v1 TG
    paper = PaperTradeEngine(enabled=True, starting_equity=10_000.0)
    pos = paper.open_experimental_position(
        symbol="BNBUSDT",
        timeframe="1h",
        eval_result=_eval_long(),
        risk_percent=0.0025,
        signal_snippet=snip,
        setup_bar_time_utc="2026-01-01T10:00:00+00:00",
        replay=True,
        emit_alert=False,
    )
    assert pos is not None
    assert pos.signal_snippet.get("telegram_eligible") is False


# ---------------------------------------------------------------------------
# 9. Blocked portfolio reason + peak concurrent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_blocked_reason_and_peak_concurrent():
    await _approve_bnb()

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
        emit_alerts=False,
        paper_engine=_FakePaper(),
        max_v1_book_risk_percent=0.05,
        max_total_risk_percent=0.01,
    )
    watcher.evaluate_closed_bar = lambda *a, **k: _eval_long()  # type: ignore[method-assign]
    import app.services.v2_candidate_paper_watcher as mod

    original = mod.is_v1_long_entry
    mod.is_v1_long_entry = lambda _r: True  # type: ignore[assignment]
    try:
        pos = await watcher.on_closed_1h(
            "BNBUSDT",
            candles_1h=_candles(60),
            candles_4h=_candles(30),
            force=True,
        )
    finally:
        mod.is_v1_long_entry = original  # type: ignore[assignment]

    assert pos is None
    row = await strategy_candidate_registry.get_by_symbol("BNBUSDT")
    assert row is not None
    port = row.get("portfolio_report") or {}
    assert port.get("last_block_reason") == "blocked_portfolio_risk_cap"
    assert port.get("blocked_reason") == "blocked_portfolio_risk_cap"
    detail = port.get("last_block_detail") or {}
    assert detail.get("peak_concurrent_positions") is not None
    assert detail.get("blocked_reason") == "blocked_portfolio_risk_cap"
    audit = await strategy_candidate_registry.list_audit("BNBUSDT")
    assert any(a["action"] == "TRADE_DECISION_BLOCKED" for a in audit)


# ---------------------------------------------------------------------------
# Static: no live orders / no strategy mutation / v1 unchanged
# ---------------------------------------------------------------------------


def test_validation_static_safety_invariants():
    paper_src = Path(__file__).resolve().parents[1] / "app" / "services" / "paper_trade.py"
    text = paper_src.read_text(encoding="utf-8")
    assert "No real exchange orders" in text or "no real exchange" in text.lower()
    assert "create_order" not in text
    assert "place_order" not in text

    books = enabled_v1_books()
    by_sym = {b.symbol: b for b in books if b.timeframe == "1h"}
    assert set(by_sym) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert by_sym["BTCUSDT"].risk_percent == 0.015
    assert by_sym["ETHUSDT"].risk_percent == 0.005
    assert by_sym["SOLUSDT"].risk_percent == 0.005
    assert V1_SYMBOLS == frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
