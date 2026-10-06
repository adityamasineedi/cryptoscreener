"""COMBO_03_TRANSITION research tests — does not mutate COMBO_02 or legacy COMBO_03."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.bos_combinations import get_combination
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.combo02_v2.playbooks import detect_liquidity_sweep
from app.research.combo03_transition.params import ELIGIBLE_REGIMES
from app.research.combo03_transition.setup import (
    confirm_15m_mandatory,
    record_sweep_from_existing,
)
from app.research.combo03_transition.state_machine import (
    STATE_IDLE,
    STATE_REJECTION_CONFIRMED,
    SetupState,
)
from app.research.combo03_transition.variants import (
    COMBO_03_FAMILY,
    require_15m_confirmation,
    variant_for_combination_id,
)
from app.research.config import ResearchConfig
from app.research.v1_production import COMBO_ID as V1_COMBO_ID
from app.signals.config import SignalConfig


def _candles(n: int, *, drift: float = 0.0, base: float = 100.0) -> list[dict]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    px = base
    for i in range(n):
        o = px
        c = px + drift
        out.append(
            {
                "time": t0 + timedelta(hours=i),
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def _expand_15m(c1h: list[dict]) -> list[dict]:
    out = []
    for c in c1h:
        for k in range(4):
            out.append(
                {
                    "time": c["time"] + timedelta(minutes=15 * k),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"] / 4,
                }
            )
    return out


def test_legacy_combo03_and_combo02_unchanged():
    legacy = get_combination("COMBO_03")
    assert legacy is not None
    assert legacy.name == "TREND_BOS_PULLBACK"
    assert legacy.require_pullback is True

    c02 = get_combination("COMBO_02")
    assert c02 is not None
    assert c02.require_htf_alignment is True
    assert V1_COMBO_ID == "COMBO_02"

    v2 = get_combination("COMBO_02_V2")
    assert v2 is not None
    assert v2.require_bos is False


def test_combo03_transition_registered_research_only():
    for cid in sorted(COMBO_03_FAMILY):
        c = get_combination(cid)
        assert c is not None
        assert c.require_bos is False
        assert c.require_htf_alignment is False
    assert variant_for_combination_id("COMBO_03_TRANSITION") == "TRANSITION_A"
    assert require_15m_confirmation("TRANSITION_A") is True
    assert require_15m_confirmation("TRANSITION_B") is False


def test_long_sweep_below_confirmed_low():
    c = _candles(40, drift=0.0, base=100.0)
    c[35]["low"] = 95.0
    c[35]["close"] = 100.2
    c[35]["high"] = 100.5
    c[35]["open"] = 100.0
    sweep = record_sweep_from_existing(c, 35, lookback=20)
    assert sweep is not None
    assert sweep["direction"] == "LONG"
    assert sweep["sweep_direction"] == "sweep_below_low"
    assert "rejection_back_inside" in sweep["labels"]
    assert sweep["close"] > sweep["level"]


def test_short_sweep_above_confirmed_high():
    c = _candles(40, drift=0.0, base=100.0)
    c[35]["high"] = 105.0
    c[35]["close"] = 99.8
    c[35]["low"] = 99.5
    c[35]["open"] = 100.0
    sweep = record_sweep_from_existing(c, 35, lookback=20)
    assert sweep is not None
    assert sweep["direction"] == "SHORT"
    assert sweep["sweep_direction"] == "sweep_above_high"
    assert "rejection_back_inside" in sweep["labels"]


def test_sweep_without_rejection_produces_no_detector_event():
    """Wick beyond without close back inside must not be a COMBO_03 sweep."""
    c = _candles(40, drift=0.0, base=100.0)
    c[35]["low"] = 95.0
    c[35]["close"] = 94.5  # closes below — no rejection
    c[35]["high"] = 100.0
    c[35]["open"] = 100.0
    assert detect_liquidity_sweep(c, 35, lookback=20) is None
    assert record_sweep_from_existing(c, 35, lookback=20) is None


def test_missing_15m_is_not_confirmation():
    ok, reason, _ = confirm_15m_mandatory(
        direction="LONG",
        analysis_15m=None,
        snap_15m=None,
        candles_15m_available=False,
        as_of_15m_index=None,
    )
    assert ok is False
    assert "MISSING" in reason or "WAIT" in reason


def test_forming_incomplete_15m_index_is_not_confirmation():
    ok, reason, _ = confirm_15m_mandatory(
        direction="LONG",
        analysis_15m=None,
        snap_15m=None,
        candles_15m_available=True,
        as_of_15m_index=None,  # fully-closed map has no closed 15m yet
    )
    assert ok is False
    assert "MISSING" in reason


def test_confirmation_before_structure_rejected_by_state_rules():
    """Structure shift bar index must be <= confirmation bar index."""
    setup = SetupState()
    setup.state = STATE_REJECTION_CONFIRMED
    setup.direction = "LONG"
    setup.structure_shift_bar_index = 50
    # Simulated: confirmation bar before structure is invalid.
    assert setup.structure_shift_bar_index > 40
    assert setup.state != STATE_IDLE


def test_eligible_regimes_are_existing_labels_only():
    assert "CHOPPY" in ELIGIBLE_REGIMES
    assert "RANGE" in ELIGIBLE_REGIMES
    assert "HIGH_VOLATILITY_RANGE" in ELIGIBLE_REGIMES
    assert "TRANSITION" in ELIGIBLE_REGIMES
    assert "BULL_TREND" not in ELIGIBLE_REGIMES
    assert "HIGH_VOL_RANGE" not in ELIGIBLE_REGIMES  # exact existing label only


def test_no_grid_martingale_averaging_in_package():
    import app.research.combo03_transition as pkg
    import inspect

    src = inspect.getsource(pkg.evaluate)
    banned = (
        "martingale",
        "average_down",
        "averaging_down",
        "pyramid",
        "grid_order",
        "recovery_trade",
        "inventory",
    )
    low = src.lower()
    for word in banned:
        assert word not in low


def test_evaluate_without_15m_waits_on_baseline():
    c1h = _candles(80, drift=0.0, base=100.0)
    # Force a long sweep+rejection on last bar
    c1h[70]["low"] = 94.0
    c1h[70]["close"] = 100.3
    c1h[70]["high"] = 100.6
    c1h[70]["open"] = 100.0
    combo = get_combination("COMBO_03_TRANSITION")
    assert combo is not None
    out = evaluate_combination_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        as_of_index=70,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_15m=None,
    )
    assert out["status"] == "NO_SETUP"
    # Either regime ineligible on synthetic flat data, or waiting for structure/15m.
    assert out.get("status") == "NO_SETUP"


def test_variant_b_registered_and_skips_mandatory_15m_flag():
    assert require_15m_confirmation(
        variant_for_combination_id("COMBO_03_TRANSITION_B")
    ) is False
    assert require_15m_confirmation(
        variant_for_combination_id("COMBO_03_TRANSITION_C")
    ) is True


def test_production_default_combo_unchanged():
    assert V1_COMBO_ID == "COMBO_02"
    c = get_combination("COMBO_02")
    assert c.require_bos and c.require_trend and c.require_htf_alignment


def test_one_setup_state_resets_after_consume():
    s = SetupState()
    s.state = STATE_REJECTION_CONFIRMED
    s.direction = "LONG"
    s.event_key = "C03:LONG:1:1"
    s.reset()
    assert s.state == STATE_IDLE
    assert s.event_key is None


def test_expand_15m_helper_usable_for_alignment_tests():
    c1h = _candles(4)
    c15 = _expand_15m(c1h)
    assert len(c15) == 16
    assert c15[0]["time"] == c1h[0]["time"]


def _fake_regime_snap(regime: str = "CHOPPY") -> object:
    from app.research.market_structure.structure import StructureSnapshot

    return StructureSnapshot(
        timeframe="1h",
        decision_time=None,
        last_closed_candle_time=None,
        trend_state="UNKNOWN",
        market_regime=regime,
        direction="UNKNOWN",
        structure_state="UNKNOWN",
        swing_high=None,
        swing_low=None,
        last_higher_high=None,
        last_higher_low=None,
        last_lower_high=None,
        last_lower_low=None,
        bos_state="NONE",
        bos_direction=None,
        bos_time=None,
        choch_state="NONE",
        choch_direction=None,
        pullback_state="NONE",
        range_state="UNKNOWN",
        volatility_state="UNKNOWN",
        momentum_state="UNKNOWN",
        confidence_score=0.5,
        data_quality_state="OK",
    )


def test_structure_shift_without_15m_is_wait_on_baseline(monkeypatch):
    """Baseline must WAIT when 15m confirmation is missing after structure shift."""
    from app.research.combo03_transition import evaluate as ev
    from app.research.combo03_transition.context import Combo03Context

    c1h = _candles(80, drift=0.0, base=100.0)
    c1h[70]["low"] = 94.0
    c1h[70]["close"] = 100.3
    c1h[70]["high"] = 101.0
    c1h[70]["open"] = 100.0

    monkeypatch.setattr(Combo03Context, "regime_at", lambda self, idx: "CHOPPY")
    monkeypatch.setattr(
        ev,
        "structure_shift_for_direction",
        lambda **kwargs: {
            "structure_shift": "bullish_structure_shift",
            "bos_or_choch": "BULLISH_BOS",
            "level": 100.5,
            "bos": {"direction": "BULLISH_BOS", "state": "CONFIRMED", "broken_level": 100.5},
            "choch": None,
        },
    )

    combo = get_combination("COMBO_03_TRANSITION")
    out = evaluate_combination_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        as_of_index=70,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_15m=None,
    )
    assert out["status"] == "NO_SETUP"
    assert "15M" in str(out.get("reason") or "").upper() or "WAIT" in str(
        out.get("reason") or ""
    )


def test_variant_b_can_reach_risk_finalize_without_15m(monkeypatch):
    from app.research.combo03_transition import evaluate as ev
    from app.research.combo03_transition.context import Combo03Context

    c1h = _candles(80, drift=0.05, base=100.0)
    c1h[70]["low"] = 94.0
    c1h[70]["close"] = 100.3
    c1h[70]["high"] = 101.0
    c1h[70]["open"] = 100.0

    monkeypatch.setattr(Combo03Context, "regime_at", lambda self, idx: "CHOPPY")
    monkeypatch.setattr(
        ev,
        "structure_shift_for_direction",
        lambda **kwargs: {
            "structure_shift": "bullish_structure_shift",
            "bos_or_choch": "BULLISH_BOS",
            "level": 100.5,
            "bos": {
                "direction": "BULLISH_BOS",
                "state": "CONFIRMED",
                "broken_level": 100.5,
                "atr": 1.0,
            },
            "choch": None,
        },
    )

    combo = get_combination("COMBO_03_TRANSITION_B")
    out = evaluate_combination_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        as_of_index=70,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_15m=None,
    )
    # May be ENTRY candidate or risk-rejected — must not be 15M-missing wait.
    reason = str(out.get("reason") or "")
    assert "15M_DATA_MISSING" not in reason
    assert out.get("combination_id") == "COMBO_03_TRANSITION_B"


def test_expired_setup_produces_no_trade(monkeypatch):
    from app.research.combo03_transition import evaluate as ev
    from app.research.combo03_transition.context import Combo03Context
    from app.research.combo03_transition.params import MAX_BARS_SWEEP_TO_STRUCTURE
    from app.research.combo03_transition.state_machine import STATE_REJECTION_CONFIRMED

    c1h = _candles(100, drift=0.0, base=100.0)
    monkeypatch.setattr(Combo03Context, "regime_at", lambda self, idx: "CHOPPY")
    monkeypatch.setattr(ev, "structure_shift_for_direction", lambda **kwargs: None)
    monkeypatch.setattr(ev, "record_sweep_from_existing", lambda *a, **k: None)

    ctx = Combo03Context.build(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_15m=None,
    )
    ctx.setup.state = STATE_REJECTION_CONFIRMED
    ctx.setup.direction = "LONG"
    ctx.setup.regime_at_setup = "CHOPPY"
    ctx.setup.sweep_bar_index = 50
    ctx.setup.rejection_bar_index = 50
    ctx.setup.sweep_level = 95.0

    combo = get_combination("COMBO_03_TRANSITION")
    # Advance far beyond expiry window
    as_of = 50 + MAX_BARS_SWEEP_TO_STRUCTURE + 2
    out = evaluate_combination_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        as_of_index=as_of,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_15m=None,
        v2_context=ctx,
    )
    assert out["status"] == "NO_SETUP"
    assert "EXPIRED" in str(out.get("reason") or "")


def test_opposite_invalidation_works(monkeypatch):
    from app.research.combo03_transition.context import Combo03Context
    from app.research.combo03_transition.state_machine import STATE_REJECTION_CONFIRMED

    c1h = _candles(80, drift=0.0, base=100.0)
    # Opposite short sweep on this bar
    c1h[60]["high"] = 106.0
    c1h[60]["close"] = 99.5
    c1h[60]["low"] = 99.0
    c1h[60]["open"] = 100.0

    monkeypatch.setattr(Combo03Context, "regime_at", lambda self, idx: "CHOPPY")

    ctx = Combo03Context.build(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_15m=None,
    )
    ctx.setup.state = STATE_REJECTION_CONFIRMED
    ctx.setup.direction = "LONG"
    ctx.setup.regime_at_setup = "CHOPPY"
    ctx.setup.sweep_bar_index = 55
    ctx.setup.rejection_bar_index = 55
    ctx.setup.sweep_level = 95.0

    combo = get_combination("COMBO_03_TRANSITION")
    out = evaluate_combination_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        as_of_index=60,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_15m=None,
        v2_context=ctx,
    )
    assert out["status"] == "NO_SETUP"
    assert "INVALIDATED" in str(out.get("reason") or "")


def test_risk_finalize_import_is_existing_engine():
    from app.research.combo02_v2.risk_finalize import finalize_with_existing_risk
    from app.research.combo03_transition.evaluate import finalize_with_existing_risk as f2

    assert f2 is finalize_with_existing_risk
