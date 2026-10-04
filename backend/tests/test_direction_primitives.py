"""Phase 1 direction-neutral primitives — shared math only.

Does not enable SHORT trading, paper opens, Telegram, or production approval.
"""

from __future__ import annotations

import pytest

from app.services.paper_trade import PaperTradeEngine
from app.services.telegram_alerts import is_v1_paper_alert
from app.services.v1_paper_watcher import V1PaperWatcher, is_v1_long_entry
from app.signals.config import SignalConfig
from app.signals.retest_engine import detect_retest, resolve_retest_side
from app.signals.risk_engine import position_size
from app.signals.schemas import Direction
from app.signals.trade_math import (
    SAME_CANDLE_PRECEDENCE_SL_FIRST,
    DirectionRequiredError,
    calculate_gross_pnl,
    require_direction,
    resolve_same_candle_exit,
    stop_triggered,
    take_profit_triggered,
    validate_trade_geometry,
)


def test_direction_enum_accepts_long():
    assert require_direction("LONG") is Direction.LONG
    assert require_direction(Direction.LONG) is Direction.LONG


def test_direction_enum_accepts_short():
    assert require_direction("SHORT") is Direction.SHORT
    assert require_direction(Direction.SHORT) is Direction.SHORT


def test_missing_required_direction_fails_closed():
    with pytest.raises(DirectionRequiredError):
        require_direction(None)
    with pytest.raises(DirectionRequiredError):
        require_direction("")
    with pytest.raises(DirectionRequiredError):
        calculate_gross_pnl(None, 100.0, 110.0, 1.0)
    with pytest.raises(DirectionRequiredError):
        stop_triggered(None, 110.0, 90.0, 95.0)


def test_long_geometry_valid_only_when_stop_lt_entry_lt_tp():
    ok = validate_trade_geometry("LONG", 100.0, 95.0, 110.0)
    assert ok.ok is True
    bad_stop = validate_trade_geometry("LONG", 100.0, 105.0, 110.0)
    assert bad_stop.ok is False
    bad_tp = validate_trade_geometry("LONG", 100.0, 95.0, 90.0)
    assert bad_tp.ok is False


def test_short_geometry_valid_only_when_tp_lt_entry_lt_stop():
    ok = validate_trade_geometry("SHORT", 100.0, 105.0, 90.0)
    assert ok.ok is True
    bad_stop = validate_trade_geometry("SHORT", 100.0, 95.0, 90.0)
    assert bad_stop.ok is False
    bad_tp = validate_trade_geometry("SHORT", 100.0, 105.0, 110.0)
    assert bad_tp.ok is False


def test_long_gross_pnl_sign():
    assert calculate_gross_pnl("LONG", 100.0, 110.0, 2.0) == pytest.approx(20.0)
    assert calculate_gross_pnl("LONG", 100.0, 90.0, 2.0) == pytest.approx(-20.0)


def test_short_gross_pnl_sign():
    assert calculate_gross_pnl("SHORT", 100.0, 90.0, 2.0) == pytest.approx(20.0)
    assert calculate_gross_pnl("SHORT", 100.0, 110.0, 2.0) == pytest.approx(-20.0)


def test_gross_pnl_invalid_quantity():
    with pytest.raises(ValueError):
        calculate_gross_pnl("LONG", 100.0, 110.0, 0.0)
    with pytest.raises(ValueError):
        calculate_gross_pnl("SHORT", 100.0, 90.0, -1.0)


def test_long_stop_uses_candle_low():
    assert stop_triggered("LONG", candle_high=105.0, candle_low=94.0, stop_price=95.0)
    assert not stop_triggered("LONG", candle_high=105.0, candle_low=96.0, stop_price=95.0)


def test_short_stop_uses_candle_high():
    assert stop_triggered("SHORT", candle_high=106.0, candle_low=99.0, stop_price=105.0)
    assert not stop_triggered("SHORT", candle_high=104.0, candle_low=99.0, stop_price=105.0)


def test_long_tp_uses_candle_high():
    assert take_profit_triggered(
        "LONG", candle_high=111.0, candle_low=100.0, take_profit_price=110.0
    )
    assert not take_profit_triggered(
        "LONG", candle_high=109.0, candle_low=100.0, take_profit_price=110.0
    )


def test_short_tp_uses_candle_low():
    assert take_profit_triggered(
        "SHORT", candle_high=100.0, candle_low=89.0, take_profit_price=90.0
    )
    assert not take_profit_triggered(
        "SHORT", candle_high=100.0, candle_low=91.0, take_profit_price=90.0
    )


def test_same_candle_precedence_sl_first():
    neither = resolve_same_candle_exit("LONG", 104.0, 96.0, 90.0, 110.0)
    assert neither["outcome"] is None
    assert neither["ambiguous"] is False

    both_long = resolve_same_candle_exit("LONG", 120.0, 80.0, 95.0, 110.0)
    assert both_long["outcome"] == "STOP"
    assert both_long["exit_price"] == 95.0
    assert both_long["ambiguous"] is True
    assert both_long["precedence"] == SAME_CANDLE_PRECEDENCE_SL_FIRST

    both_short = resolve_same_candle_exit("SHORT", 120.0, 80.0, 105.0, 90.0)
    assert both_short["outcome"] == "STOP"
    assert both_short["exit_price"] == 105.0
    assert both_short["ambiguous"] is True


def test_long_risk_sizing_correct():
    pos = position_size(
        account_equity=10_000,
        risk_percent=0.01,
        entry=100.0,
        stop=98.0,
        direction="LONG",
        contract_quantity_step=0.001,
        minimum_quantity=0.001,
    )
    assert pos["geometry_ok"] is True
    assert pos["risk_per_unit"] == pytest.approx(2.0)
    assert pos["max_risk_amount"] == pytest.approx(100.0)
    assert pos["final_quantity"] == pytest.approx(50.0)
    assert pos["direction"] == "LONG"


def test_short_risk_sizing_correct():
    pos = position_size(
        account_equity=10_000,
        risk_percent=0.01,
        entry=100.0,
        stop=102.0,
        direction="SHORT",
        contract_quantity_step=0.001,
        minimum_quantity=0.001,
    )
    assert pos["geometry_ok"] is True
    assert pos["risk_per_unit"] == pytest.approx(2.0)
    assert pos["final_quantity"] == pytest.approx(50.0)
    assert pos["direction"] == "SHORT"


def test_invalid_long_geometry_rejected_by_sizing():
    pos = position_size(
        account_equity=10_000,
        risk_percent=0.01,
        entry=100.0,
        stop=105.0,
        direction="LONG",
    )
    assert pos["final_quantity"] == 0.0
    assert pos["geometry_ok"] is False
    assert "LONG" in (pos["reason"] or "")


def test_invalid_short_geometry_rejected_by_sizing():
    pos = position_size(
        account_equity=10_000,
        risk_percent=0.01,
        entry=100.0,
        stop=95.0,
        direction="SHORT",
    )
    assert pos["final_quantity"] == 0.0
    assert pos["geometry_ok"] is False
    assert "SHORT" in (pos["reason"] or "")


def test_stop_equal_entry_and_zero_risk_rejected():
    eq = validate_trade_geometry("LONG", 100.0, 100.0, 110.0)
    assert eq.ok is False
    zero = position_size(
        account_equity=10_000,
        risk_percent=0.0,
        entry=100.0,
        stop=98.0,
        direction="LONG",
    )
    assert zero["final_quantity"] == 0.0
    neg = position_size(
        account_equity=10_000,
        risk_percent=-0.01,
        entry=100.0,
        stop=98.0,
        direction="LONG",
    )
    assert neg["final_quantity"] == 0.0


def test_missing_direction_never_silently_becomes_long_in_shared_math():
    with pytest.raises(DirectionRequiredError):
        require_direction(None)
    geom = validate_trade_geometry(None, 100.0, 95.0, 110.0)
    assert geom.ok is False
    assert geom.direction is None
    sized = position_size(
        account_equity=10_000,
        risk_percent=0.01,
        entry=100.0,
        stop=98.0,
        require_direction=True,
    )
    assert sized["final_quantity"] == 0.0
    assert sized["direction"] is None
    assert "direction is required" in (sized["reason"] or "")
    # Legacy unsigned path also must not invent LONG.
    legacy = position_size(
        account_equity=10_000,
        risk_percent=0.01,
        entry=100.0,
        stop=98.0,
    )
    assert legacy["direction"] is None


def test_retest_long_bullish_explicit_direction():
    cfg = SignalConfig(retest_atr_tolerance=5.0)
    candles = [
        {"o": 100, "h": 102, "l": 99, "c": 101, "v": 1},
        {"o": 101, "h": 103, "l": 100.5, "c": 102, "v": 1},
    ]
    bos = {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 101.0}
    pullback = {"pullback_state": "ACTIVE", "structure_intact": True}
    rt = detect_retest(candles, bos, pullback, cfg, direction="LONG")
    assert rt["direction"] == "LONG"
    assert "mismatch" not in (rt.get("reason") or "").lower()


def test_retest_short_bearish_explicit_direction():
    cfg = SignalConfig(retest_atr_tolerance=5.0)
    candles = [
        {"o": 100, "h": 101, "l": 98, "c": 99, "v": 1},
        {"o": 99, "h": 100.5, "l": 97, "c": 98.5, "v": 1},
    ]
    bos = {"state": "CONFIRMED", "direction": "BEARISH_BOS", "broken_level": 99.0}
    pullback = {"pullback_state": "ACTIVE", "structure_intact": True}
    rt = detect_retest(candles, bos, pullback, cfg, direction="SHORT")
    assert rt["direction"] == "SHORT"


def test_retest_direction_bos_mismatch_fails_closed():
    side, reason = resolve_retest_side(
        direction="SHORT",
        bos={"direction": "BULLISH_BOS"},
    )
    assert side is None
    assert reason is not None and "mismatch" in reason.lower()

    cfg = SignalConfig()
    rt = detect_retest(
        [{"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}],
        {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 1.0},
        {"pullback_state": "ACTIVE", "structure_intact": True},
        cfg,
        direction="SHORT",
    )
    assert rt["retest"] is False
    assert rt.get("mismatch") is True


def test_retest_missing_direction_uses_bos_adapter():
    """v1 compatibility: omitted direction may resolve from BOS, not default LONG."""
    side, reason = resolve_retest_side(
        direction=None,
        bos={"direction": "BEARISH_BOS"},
    )
    assert side is Direction.SHORT
    assert reason is None
    missing = resolve_retest_side(direction=None, bos={"direction": None})
    assert missing[0] is None


# --- Isolation: SHORT remains blocked from v1 / Telegram / watchers -------------


def _short_candidate_payload() -> dict:
    return {
        "status": "SHORT_ENTRY_CANDIDATE",
        "direction": "SHORT",
        "combination_id": "COMBO_02",
        "required": ["bos", "trend", "htf"],
        "gates": {"bos": True, "trend": True, "htf": True},
        "htf": {
            "htf_alignment": "HTF_ALIGNED",
            "trend_1h": "BEARISH",
            "trend_4h": "BEARISH",
        },
        "entry_price": 100.0,
        "stop_price": 105.0,
    }


def test_short_rejected_by_is_v1_long_entry():
    assert not is_v1_long_entry(_short_candidate_payload())
    # Even if status is forged LONG with SHORT direction:
    forged = {**_short_candidate_payload(), "status": "LONG_ENTRY_CANDIDATE"}
    assert not is_v1_long_entry(forged)


def test_short_excluded_from_v1_telegram():
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "BTCUSDT",
        "telegram_eligible": True,
        "payload": {
            "telegram_eligible": True,
            "strategy_id": "COMBO_02_SHORT_RESEARCH",
            "source": "SHORT_RESEARCH_PIPELINE",
            "combo_id": "COMBO_02",
            "combo_version": "v1",
            "path": "A",
            "timeframe": "1h",
            "htf_alignment": "HTF_ALIGNED",
            "direction": "SHORT",
            "signal_snippet": {
                "telegram_eligible": True,
                "strategy_id": "COMBO_02_SHORT_RESEARCH",
                "source": "SHORT_RESEARCH_PIPELINE",
                "direction": "SHORT",
            },
        },
    }
    assert not is_v1_paper_alert(alert)


def test_dynamic_short_cannot_become_production_or_telegram_eligible():
    # Registry/watcher contracts force false; assert SHORT payload cannot satisfy
    # v1 telegram filter even if attacker sets eligible flags.
    snip = {
        "strategy_id": "COMBO_02_V2_RESEARCH",
        "source": "V2_CANDIDATE_PAPER_WATCHER",
        "combo_id": "COMBO_02",
        "combo_version": "v2-research",
        "path": "A",
        "timeframe": "1h",
        "htf_alignment": "HTF_ALIGNED",
        "production_approved": True,
        "telegram_eligible": True,
        "direction": "SHORT",
    }
    alert = {
        "type": "PAPER_ENTRY",
        "symbol": "ADAUSDT",
        "telegram_eligible": True,
        "payload": {
            **snip,
            "telegram_eligible": True,
            "production_approved": True,
            "signal_snippet": snip,
        },
    }
    assert not is_v1_paper_alert(alert)
    assert snip["production_approved"] is True  # attacker field
    # Gate still rejects identity — production_approved on snippet is irrelevant.
    assert alert["payload"]["strategy_id"] != "COMBO_02_V1"


def test_dynamic_short_not_opened_by_long_only_watcher():
    paper = PaperTradeEngine(enabled=True, entry_mode="path_b")
    paper.risk_policy.enabled = False
    paper.legacy_auto_entry_enabled = True
    watcher = V1PaperWatcher(paper_engine=paper, replay_mode=True, emit_alerts=False)
    assert not is_v1_long_entry(_short_candidate_payload())
    # Path B would accept LONG_ENTRY_CANDIDATE; SHORT must fail closed.
    with pytest.raises(PermissionError, match="short_research_only"):
        paper.on_setup_signal(
            "BTCUSDT",
            {
                "status": "SHORT_ENTRY_CANDIDATE",
                "direction": "SHORT",
                "timeframe": "1h",
                "bos": {
                    "state": "CONFIRMED",
                    "direction": "BEARISH_BOS",
                    "broken_level": 100,
                },
                "trend": {"trend": "BEARISH"},
                "mtf": {"MTF_ALIGNMENT": "STRONG_SHORT"},
                "entry": {"entry_price": 100},
                "stop": {"final_stop": 105},
                "targets": [{"target_price": 90}],
                "risk_reward": {"RISK_REWARD": "PASS"},
                "ohlcv_freshness": "FRESH",
            },
        )
    # Also reject forged LONG status with SHORT direction.
    with pytest.raises(PermissionError, match="short_research_only"):
        paper.on_setup_signal(
            "ETHUSDT",
            {
                "status": "LONG_ENTRY_CANDIDATE",
                "direction": "SHORT",
                "timeframe": "1h",
                "bos": {
                    "state": "CONFIRMED",
                    "direction": "BEARISH_BOS",
                    "broken_level": 100,
                },
                "trend": {"trend": "BEARISH"},
                "entry": {"entry_price": 100},
                "stop": {"final_stop": 105},
                "targets": [{"target_price": 90}],
                "risk_reward": {"RISK_REWARD": "PASS"},
                "ohlcv_freshness": "FRESH",
            },
        )
    assert watcher is not None
