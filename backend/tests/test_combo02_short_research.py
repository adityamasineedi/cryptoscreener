"""Phase 2: COMBO_02 SHORT research-only — never paper / Telegram / v1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.bos_strategy_comparison.htf import classify_htf_alignment
from app.research.candidate_state_machine import (
    ALLOWED_TRANSITIONS,
    CANDIDATE_STATES,
    assert_transition_allowed,
)
from app.research.combo02_short_research import (
    ShortResearchOnlyError,
    assert_short_research_only_boundary,
    assert_short_signal_geometry,
    build_short_eligibility_output,
    classify_short_oos,
    finalize_short_candidate,
    short_research_identity,
    short_research_registry,
    simulate_short_research_trade,
    validate_short_research_signal,
)
from app.research.short_research_constants import (
    COMBO_VERSION,
    SOURCE,
    STRATEGY_ID,
    TERMINAL_PASS_STATE,
)
from app.research.strategy_candidate_registry import can_approve_for_paper
from app.services.paper_trade import PaperPosition, PaperTradeEngine
from app.services.telegram_alerts import is_v1_paper_alert
from app.services.v1_paper_watcher import V1PaperWatcher, is_v1_long_entry
from app.services.v2_candidate_paper_watcher import V2CandidatePaperWatcher
from app.signals.bos_engine import detect_bos
from app.signals.config import SignalConfig
from app.signals.entry_engine import evaluate_entry
from app.signals.schemas import Direction, SwingRecord
from app.signals.stop_engine import compute_stop
from app.signals.target_engine import compute_targets
from app.signals.trade_math import DirectionRequiredError, require_direction
from app.signals.trend_engine import infer_trend
from tests.test_setup_signals import candle


def _lh_ll_swings() -> list[SwingRecord]:
    """Explicit LH/LL swing sequence for SHORT structure tests."""
    spec = [
        ("HIGH", 110.0, 2, "HH"),
        ("LOW", 100.0, 5, "HL"),
        ("HIGH", 107.0, 8, "LH"),
        ("LOW", 96.0, 11, "LL"),
    ]
    out: list[SwingRecord] = []
    for st, px, idx, label in spec:
        out.append(
            SwingRecord(
                symbol="T",
                timeframe="1h",
                swing_type=st,
                price=px,
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc)
                + timedelta(hours=idx),
                bar_index=idx,
                strength=1.0,
                confirmed_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
                + timedelta(hours=idx + 1),
                label=label,
            )
        )
    return out


def test_short_bearish_trend_and_lh_ll_fixture():
    swings = _lh_ll_swings()
    labels = [s.label for s in swings if s.label]
    assert "LH" in labels
    assert "LL" in labels
    trend = infer_trend(swings)
    assert trend["trend"] == "BEARISH"
    assert "Lower High" in trend["reason"] or "LH" in trend["reason"]


def test_short_bearish_bos_fixture():
    swings = _lh_ll_swings()
    trend = infer_trend(swings)
    assert trend["trend"] == "BEARISH"
    # Close below last swing low (96) while bearish.
    candles = [
        candle(i, 100, 101, 99, 100) for i in range(12)
    ] + [candle(12, 96, 96.5, 93, 94.0)]
    bos = detect_bos(candles, swings, trend, symbol="T", timeframe="1h")
    assert bos is not None
    assert bos.get("state") == "CONFIRMED"
    assert bos["direction"] == "BEARISH_BOS"
    assert bos["break_price"] < bos["broken_level"]


def test_short_bearish_htf_alignment():
    assert (
        classify_htf_alignment(
            bos_direction="BEARISH_BOS",
            trend_4h="BEARISH",
            trend_1h="BEARISH",
        )
        == "HTF_ALIGNED"
    )
    # Must not accept bullish HTF for SHORT.
    assert (
        classify_htf_alignment(
            bos_direction="BEARISH_BOS",
            trend_4h="BULLISH",
            trend_1h="BULLISH",
        )
        == "HTF_CONFLICT"
    )


def test_short_entry_candidate():
    cfg = SignalConfig()
    cfg.require_mtf_alignment = False
    cfg.min_rr = 1.0
    mtf = {
        "MTF_ALIGNMENT": "STRONG_SHORT",
        "trends": {"4h": "BEARISH", "1h": "BEARISH", "15m": "BEARISH", "5m": "BEARISH"},
        "reason": "ok",
    }
    result = evaluate_entry(
        mtf=mtf,
        setup_trend={"trend": "BEARISH"},
        bos={"state": "CONFIRMED", "direction": "BEARISH_BOS", "broken_level": 100.0},
        impulse={"is_impulse": True, "quality": "STRONG"},
        pullback={"pullback_state": "CONFIRMED", "structure_intact": True},
        retest={"retest": True, "state": "CONFIRMED", "reason": "ok"},
        stop={"final_stop": 105.0},
        targets=[{"name": "TP1", "target_price": 90.0, "r_multiple": 2.0}],
        risk_reward={"RISK_REWARD": "PASS", "best_R": 2.0},
        config=cfg,
        last_close=99.0,
        volume_ok=True,
    )
    assert result["status"] == "SHORT_ENTRY_CANDIDATE"
    assert result["direction"] == "SHORT"


def test_short_stop_above_and_tp_below_entry():
    stop = compute_stop(
        direction="SHORT",
        entry_price=100.0,
        pullback={"retracement_high": 102.0, "impulse_origin": 103.0},
        atr=2.0,
        sl_buffer_atr=0.2,
    )
    assert stop["final_stop"] > 100.0
    targets = compute_targets(
        direction="SHORT",
        entry_price=100.0,
        stop={"risk_per_unit": 3.0, "final_stop": stop["final_stop"]},
        swings=[type("S", (), {"swing_type": "LOW", "price": 94.0})()],
        config=SignalConfig(min_rr=1.0),
    )
    assert targets
    assert float(targets[0]["target_price"]) < 100.0
    assert_short_signal_geometry(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_price=float(stop["final_stop"]),
        take_profit_price=float(targets[0]["target_price"]),
    )


def test_invalid_short_geometry_rejected():
    with pytest.raises((AssertionError, ValueError, DirectionRequiredError)):
        assert_short_signal_geometry(
            direction="SHORT",
            entry_price=100.0,
            stop_price=95.0,  # wrong side
            take_profit_price=90.0,
        )
    with pytest.raises((AssertionError, ValueError, DirectionRequiredError)):
        assert_short_signal_geometry(
            direction="SHORT",
            entry_price=100.0,
            stop_price=105.0,
            take_profit_price=110.0,  # wrong side TP
        )


def test_short_market_and_limit_backtest_triggers_pnl_fees_equity():
    # Market entry + stop via candle high
    stop_sim = simulate_short_research_trade(
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        risk_usd=20.0,
        candle_high=106.0,
        candle_low=99.0,
        entry_type="MARKET",
    )
    assert stop_sim["hit_stop"] is True
    assert stop_sim["outcome"] == "STOP"
    assert stop_sim["gross_pnl"] < 0
    assert stop_sim["fees"] < 0  # NEGATIVE_COST convention
    assert stop_sim["net_pnl"] == pytest.approx(stop_sim["gross_pnl"] + stop_sim["fees"])
    assert stop_sim["equity_delta"] == stop_sim["net_pnl"]
    assert stop_sim["r_gross"] == pytest.approx(-1.0)

    # Limit retest entry + TP via candle low
    tp_sim = simulate_short_research_trade(
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        risk_usd=20.0,
        candle_high=101.0,
        candle_low=89.0,
        entry_type="LIMIT_RETEST",
    )
    assert tp_sim["hit_tp"] is True
    assert tp_sim["outcome"] == "TP"
    assert tp_sim["gross_pnl"] > 0
    assert tp_sim["r_gross"] == pytest.approx(2.0)
    assert tp_sim["precedence"] == "SL_FIRST"


def test_short_same_candle_sl_first():
    both = simulate_short_research_trade(
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        risk_usd=20.0,
        candle_high=110.0,
        candle_low=80.0,
        entry_type="MARKET",
    )
    assert both["ambiguous"] is True
    assert both["outcome"] == "STOP"
    assert both["exit_price"] == 105.0
    assert both["precedence"] == "SL_FIRST"


def test_short_candidate_registry_identity_and_eligibility():
    short_research_registry._rows.clear()
    stored = finalize_short_candidate(
        symbol="ADAUSDT",
        base_metrics={
            "trade_count": 25,
            "net_avg_r": 0.4,
            "net_pnl": 200.0,
            "profit_factor": 1.5,
            "max_dd_r": 3.0,
            "max_losing_streak": 3,
            "fee_share": 0.1,
        },
        oos_metrics={
            "trade_count": 12,
            "net_avg_r": 0.2,
            "net_pnl": 50.0,
            "profit_factor": 1.2,
            "max_dd_r": 2.0,
            "max_losing_streak": 2,
        },
        oos_usable=True,
        bearish_htf_aligned=True,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
    )
    assert stored["direction"] == "SHORT"
    assert stored["strategy_id"] == STRATEGY_ID
    assert stored["combo_version"] == COMBO_VERSION
    assert stored["source"] == SOURCE
    assert stored["production_approved"] is False
    assert stored["telegram_eligible"] is False
    assert stored["paper_eligible"] is False
    assert stored["state"] == TERMINAL_PASS_STATE
    assert stored["eligibility"]["research_tier"] == TERMINAL_PASS_STATE
    assert stored["state"] != "V2_PAPER_CANDIDATE"

    elig = build_short_eligibility_output(
        symbol="ADAUSDT",
        trade_count=25,
        net_avg_r=0.4,
        net_pnl=200.0,
        profit_factor=1.5,
        max_dd_r=3.0,
        max_lose_streak=3,
        fee_share=0.1,
        bearish_htf_aligned=True,
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        oos_label=TERMINAL_PASS_STATE,
    )
    rules = {c["rule"]: c for c in elig["conditions"]}
    assert rules["bearish_htf_alignment"]["passed"] is True
    assert rules["short_geometry"]["passed"] is True
    assert elig["paper_eligible"] is False


def test_short_cannot_become_v2_paper_candidate_state():
    label, _, _ = classify_short_oos(
        base_tier="PROMISING",
        oos_trade_count=12,
        oos_net_avg_r=0.2,
        oos_net_pnl=40.0,
        oos_profit_factor=1.2,
        oos_max_dd_r=2.0,
        oos_max_lose_streak=2,
        oos_usable=True,
    )
    assert label == TERMINAL_PASS_STATE
    assert label != "V2_PAPER_CANDIDATE"
    assert TERMINAL_PASS_STATE in CANDIDATE_STATES
    assert "PAPER_VALIDATING" not in ALLOWED_TRANSITIONS[TERMINAL_PASS_STATE]
    assert "APPROVED" not in ALLOWED_TRANSITIONS[TERMINAL_PASS_STATE]
    with pytest.raises(Exception):
        assert_transition_allowed(TERMINAL_PASS_STATE, "PAPER_VALIDATING")


def test_short_candidate_cannot_enter_v1_watcher():
    payload = {
        **short_research_identity(symbol="BTCUSDT"),
        "status": "SHORT_ENTRY_CANDIDATE",
        "direction": "SHORT",
        "combination_id": "COMBO_02",
        "gates": {"bos": True, "trend": True, "htf": True},
        "required": ["bos", "trend", "htf"],
        "htf": {
            "htf_alignment": "HTF_ALIGNED",
            "trend_1h": "BEARISH",
            "trend_4h": "BEARISH",
        },
        "entry_price": 100.0,
        "stop_price": 105.0,
    }
    assert not is_v1_long_entry(payload)
    paper = PaperTradeEngine(enabled=True)
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=True, emit_alerts=False)
    # Non-v1 symbols / SHORT identity never open via is_v1_long_entry gate.
    assert watcher.book_for("ADAUSDT") is None


@pytest.mark.asyncio
async def test_short_candidate_cannot_enter_v2_watcher():
    paper = PaperTradeEngine(enabled=True)
    watcher = V2CandidatePaperWatcher(paper_engine=paper, enabled=True, emit_alerts=False)

    async def _fake_eligible():
        return [
            {
                "symbol": "ADAUSDT",
                "strategy_id": STRATEGY_ID,
                "source": SOURCE,
                "combo_version": COMBO_VERSION,
                "state": TERMINAL_PASS_STATE,
                "direction": "SHORT",
                "risk_percent": 0.0025,
                "telegram_eligible": False,
                "production_approved": False,
                "operator_approved": True,
            }
        ]

    watcher.eligible_symbols = _fake_eligible  # type: ignore[method-assign]
    with pytest.raises(PermissionError, match="short_research_only"):
        await watcher.on_closed_1h("ADAUSDT", candles_1h=[{}], candles_4h=[{}], force=True)


def test_short_cannot_open_or_manage_paper():
    paper = PaperTradeEngine(enabled=True, entry_mode="path_b")
    paper.legacy_auto_entry_enabled = True
    paper.risk_policy.enabled = False
    with pytest.raises(PermissionError, match="short_research_only"):
        paper.on_setup_signal(
            "ADAUSDT",
            {
                **short_research_identity(),
                "status": "SHORT_ENTRY_CANDIDATE",
                "direction": "SHORT",
                "entry": {"entry_price": 100},
                "stop": {"final_stop": 105},
                "targets": [{"target_price": 90}],
                "bos": {"state": "CONFIRMED", "direction": "BEARISH_BOS"},
                "risk_reward": {"RISK_REWARD": "PASS"},
                "ohlcv_freshness": "FRESH",
            },
        )
    # Manage path: inject a SHORT-side position and ensure tick fails closed.
    pos = PaperPosition(
        id="x",
        symbol="ADAUSDT",
        side="SHORT",
        timeframe="1h",
        entry_price=100.0,
        stop_price=105.0,
        tp1_price=90.0,
        quantity=1.0,
        risk_usd=5.0,
        opened_at=datetime.now(timezone.utc).isoformat(),
        status="OPEN",
        signal_snippet=short_research_identity(),
    )
    paper._open[paper.book_key("ADAUSDT", "LEGACY")] = pos
    with pytest.raises(PermissionError, match="short_research_only"):
        paper.tick({"ADAUSDT": 99.0})


def test_short_cannot_trigger_telegram_or_production():
    identity = short_research_identity(symbol="ADAUSDT")
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "ADAUSDT",
        "telegram_eligible": True,
        "payload": {
            **identity,
            "telegram_eligible": True,
            "production_approved": True,
            "combo_id": "COMBO_02",
            "path": "A",
            "timeframe": "1h",
            "htf_alignment": "HTF_ALIGNED",
            "signal_snippet": {**identity, "telegram_eligible": True},
        },
    }
    assert not is_v1_paper_alert(alert)
    with pytest.raises(ShortResearchOnlyError):
        short_research_registry.set_production_approved("ADAUSDT", True)
    with pytest.raises(ShortResearchOnlyError):
        short_research_registry.set_telegram_eligible("ADAUSDT", True)
    with pytest.raises(ShortResearchOnlyError):
        short_research_registry.promote_to_paper("ADAUSDT")
    assert (
        can_approve_for_paper(
            {
                "state": TERMINAL_PASS_STATE,
                "strategy_id": STRATEGY_ID,
                "direction": "SHORT",
                "backtest_status": "PASS",
                "backtest_tier": "PROMISING",
                "oos_status": TERMINAL_PASS_STATE,
            }
        )
        is False
    )


def test_missing_short_direction_fails_closed():
    with pytest.raises(DirectionRequiredError):
        require_direction(None)
    with pytest.raises(DirectionRequiredError):
        assert_short_signal_geometry(
            direction=None,
            entry_price=100.0,
            stop_price=105.0,
            take_profit_price=90.0,
        )
    bad = validate_short_research_signal(
        {
            "status": "SHORT_ENTRY_CANDIDATE",
            "direction": None,
            "trend": {"trend": "BEARISH"},
            "bos": {"state": "CONFIRMED", "direction": "BEARISH_BOS"},
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BEARISH",
                "trend_4h": "BEARISH",
            },
            "entry_price": 100.0,
            "stop_price": 105.0,
            "tp1": 90.0,
        }
    )
    assert bad["ok"] is False


def test_short_rejects_bullish_assumptions():
    bad = validate_short_research_signal(
        {
            "status": "LONG_ENTRY_CANDIDATE",
            "direction": "LONG",
            "trend": {"trend": "BULLISH"},
            "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
            "htf": {
                "htf_alignment": "HTF_ALIGNED",
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
            },
            "entry_price": 100.0,
            "stop_price": 95.0,
            "tp1": 110.0,
        }
    )
    assert bad["ok"] is False
    with pytest.raises(PermissionError, match="short_research_only"):
        assert_short_research_only_boundary({"direction": "SHORT"})


def test_short_identity_mandatory_assertions():
    c = short_research_identity(symbol="SOLUSDT")
    assert c["direction"] == "SHORT"
    assert c["strategy_id"] == "COMBO_02_SHORT_RESEARCH"
    assert c["production_approved"] is False
    assert c["telegram_eligible"] is False
    assert c["paper_eligible"] is False
