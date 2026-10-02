"""Look-ahead / circular-definition guards for trend regime diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.trend_regime_diagnostic import (
    assert_no_future_influence,
    classify_regime_category,
    diagnose_at_entry,
    range_like_candidate,
)
from app.signals.config import SignalConfig
from app.signals.trend_engine import infer_trend
from app.signals.schemas import SwingRecord


def _candles(n: int = 80, start: float = 100.0):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    px = start
    for i in range(n):
        o = px
        c = px + (0.4 if i % 7 == 0 else -0.15)
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        out.append(
            {
                "time": t0 + timedelta(minutes=15 * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000 + i,
            }
        )
        px = c
    return out


def test_future_candles_do_not_change_trend_at_entry():
    candles = _candles(120)
    cfg = SignalConfig()
    entry = 60
    assert_no_future_influence(candles, entry, cfg, symbol="T", timeframe="15m")


def test_htf_unavailable_not_bullish_or_bearish():
    cat, align = classify_regime_category(
        "BULLISH", "HTF_UNAVAILABLE", "HTF_UNAVAILABLE", setup_tf="15m"
    )
    assert cat == "LOCAL_ONLY"
    assert "BULLISH" not in align or align != "ALIGNED"


def test_htf_conflict_detected():
    cat, align = classify_regime_category(
        "BULLISH", "BEARISH", "BEARISH", setup_tf="15m"
    )
    assert cat == "HTF_CONFLICT"
    assert align == "CONFLICT"


def test_neutral_setup_category():
    cat, _ = classify_regime_category(
        "NEUTRAL", "BULLISH", "BULLISH", setup_tf="15m"
    )
    assert cat == "NEUTRAL_STRUCTURE"


def test_regime_not_defined_by_outcome():
    """Outcome must not appear in classification inputs."""
    candles = _candles(100)
    cfg = SignalConfig()
    trade_win = {
        "direction": "LONG",
        "signal_time": candles[50]["time"].isoformat(),
        "exit_time": candles[70]["time"].isoformat(),
        "entry_price": 100.0,
        "exit_price": 110.0,
        "outcome": "TP1",
        "r_multiple": 2.0,
        "mae": 1.0,
        "mfe": 5.0,
        "mae_r": 0.2,
        "mfe_r": 2.0,
    }
    trade_loss = {**trade_win, "outcome": "SL", "r_multiple": -1.0, "exit_price": 95.0}
    empty_htf: list = []
    a = diagnose_at_entry(
        symbol="T",
        timeframe="15m",
        entry_index=50,
        setup_candles=candles,
        h1_candles=empty_htf,
        h4_candles=empty_htf,
        signal_config=cfg,
        trade=trade_win,
    )
    b = diagnose_at_entry(
        symbol="T",
        timeframe="15m",
        entry_index=50,
        setup_candles=candles,
        h1_candles=empty_htf,
        h4_candles=empty_htf,
        signal_config=cfg,
        trade=trade_loss,
    )
    assert a.regime_category == b.regime_category
    assert a.range_like_candidate == b.range_like_candidate
    assert a.setup_trend == b.setup_trend


def test_range_like_candidate_rule_is_pre_entry_metrics_only():
    flag, rule = range_like_candidate(
        trend_strength=0.4, direction_changes=5, recent_range_atr=2.0
    )
    assert flag is True
    assert "pre-entry" in rule
    flag2, _ = range_like_candidate(
        trend_strength=0.9, direction_changes=5, recent_range_atr=2.0
    )
    assert flag2 is False


def test_production_infer_trend_unchanged_contract():
    """Sanity: trend_engine still only HH+HL / LH+LL / NEUTRAL."""
    now = datetime.now(timezone.utc)
    swings = [
        SwingRecord("X", "15m", "HIGH", 10.0, now, 1, 1.0, now, label="HH"),
        SwingRecord("X", "15m", "LOW", 8.0, now, 2, 1.0, now, label="HL"),
        SwingRecord("X", "15m", "HIGH", 12.0, now, 3, 1.0, now, label="HH"),
        SwingRecord("X", "15m", "LOW", 9.0, now, 4, 1.0, now, label="HL"),
    ]
    t = infer_trend(swings)
    assert t["trend"] == "BULLISH"
    assert "Higher High + Higher Low" in t["reason"]
