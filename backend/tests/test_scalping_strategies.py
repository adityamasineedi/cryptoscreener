from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.scalping.backtest import simulate_candidate
from app.research.scalping.catalog import SCALPING_STRATEGIES
from app.research.scalping.engine import ScalpingStrategyEngine, _Zone
from app.research.scalping.features import build_features
from app.research.scalping.models import ScalpCandidate
from app.research.scalping.validation import ScalpingValidationRegistry


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candle(i: int, *, o=100.0, h=101.0, l=99.0, c=100.0, v=100.0, minutes=1):
    return {
        "time": BASE + timedelta(minutes=i * minutes),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


def test_feature_initialization_rvol_excludes_current_and_vwap_resets_utc():
    rows = [candle(i) for i in range(24 * 60)]
    rows[20]["volume"] = 200.0
    rows[-1] = {**rows[-1], "time": BASE + timedelta(days=1), "h": 201.0} if False else {
        **rows[-1], "time": BASE + timedelta(days=1), "high": 201.0,
        "low": 199.0, "open": 200.0, "close": 200.0,
    }
    f = build_features(rows, "1m")
    assert f.atr[12] is None
    assert f.atr[13] == pytest.approx(2.0)
    assert f.rvol[19] is None
    assert f.rvol[20] == pytest.approx(2.0)  # prior 20 only
    assert f.vwap[-1] == pytest.approx(200.0)  # no prior-session carry


def test_missing_bar_starts_new_segment_and_swings_do_not_bridge_gap():
    rows = [candle(i) for i in range(10)] + [candle(i + 2) for i in range(10, 20)]
    f = build_features(rows, "1m")
    assert f.segment[9] != f.segment[10]
    assert f.confirmed_swings(10) == []
    assert f.atr[10] is None
    assert f.rvol[19] is None


def test_explicit_open_candle_is_rejected():
    rows = [candle(i) for i in range(20)]
    rows[-1]["is_closed"] = False
    with pytest.raises(ValueError, match="open candles"):
        build_features(rows, "1m")


def _bos_rows(first_retest_passes: bool = True):
    rows = [candle(i) for i in range(40)]
    rows[25] = candle(25, o=102, h=105, l=101, c=103)
    rows[26] = candle(26, o=102, h=104, l=101, c=102)
    rows[27] = candle(27, o=102, h=104, l=101, c=103)
    rows[28] = candle(28, o=103, h=108, l=102.5, c=107, v=200)
    if first_retest_passes:
        rows[29] = candle(29, o=105.2, h=106.5, l=104.8, c=106)
    else:
        rows[29] = candle(29, o=106, h=106.2, l=104.8, c=105.2)
        rows[30] = candle(30, o=105.2, h=106.5, l=104.8, c=106)
    rows[30 if first_retest_passes else 31] = candle(
        30 if first_retest_passes else 31, o=106.2, h=107, l=105.5, c=106.5
    )
    return rows


def test_bos_first_retest_enters_next_open_and_freezes_breakout_atr(monkeypatch):
    engine = ScalpingStrategyEngine()
    monkeypatch.setattr(engine, "_trend_at", lambda *_: "BULLISH")
    one = build_features(_bos_rows(), "1m")
    fifteen = build_features([candle(i, minutes=15) for i in range(20)], "15m")
    rows = engine._bos_retest("BTCUSDT", one, fifteen, False)
    match = [r for r in rows if r.direction == "LONG" and r.evidence["broken_level"] == 105]
    assert len(match) == 1
    result = match[0]
    assert result.signal_index == 29
    assert result.entry_index == 30
    assert result.entry_price == pytest.approx(106.2)
    assert result.target_price == pytest.approx(
        result.entry_price + 1.5 * (result.entry_price - result.stop_price)
    )


def test_bos_first_failed_touch_cancels_later_success(monkeypatch):
    engine = ScalpingStrategyEngine()
    monkeypatch.setattr(engine, "_trend_at", lambda *_: "BULLISH")
    one = build_features(_bos_rows(False), "1m")
    fifteen = build_features([candle(i, minutes=15) for i in range(20)], "15m")
    rows = engine._bos_retest("BTCUSDT", one, fifteen, False)
    assert not [r for r in rows if r.direction == "LONG" and r.evidence["broken_level"] == 105]


def test_sweep_reclaim_registers_reclaim_and_choch_candidates(monkeypatch):
    rows = [candle(i) for i in range(40)]
    rows[22] = candle(22, o=102, h=105, l=101, c=102)
    rows[23] = candle(23, h=103)
    rows[24] = candle(24, h=103)
    rows[25] = candle(25, o=100, h=102, l=97, c=99)
    rows[26] = candle(26, o=100, h=102, l=98, c=100)
    rows[27] = candle(27, o=100, h=102, l=98, c=100)
    rows[30] = candle(30, o=98.5, h=101, l=96.5, c=100, v=200)
    rows[31] = candle(31, o=100, h=107, l=99, c=106)
    rows[32] = candle(32, o=106.2, h=107, l=105, c=106.5)
    one = build_features(rows, "1m")
    five = build_features([candle(i, minutes=5) for i in range(20)], "5m")
    fifteen = build_features([candle(i, minutes=15) for i in range(20)], "15m")
    engine = ScalpingStrategyEngine()
    monkeypatch.setattr(engine, "_trend_at", lambda *_: "BULLISH")
    monkeypatch.setattr(type(one), "trend", lambda *_: "BEARISH")
    monkeypatch.setattr(
        engine,
        "_zones",
        lambda _: [_Zone("LONG", 96.0, 98.0, one.closes_at[28], 1)],
    )
    found = engine._sweep_reclaim("BTCUSDT", one, five, fifteen, False)
    variants = {row.variant: row for row in found if row.evidence.get("sweep_index") == 30}
    assert set(variants) == {"reclaim_only", "reclaim_choch"}
    assert variants["reclaim_only"].entry_index == 31
    assert variants["reclaim_choch"].entry_index == 32


def test_vwap_pullback_confirmation_accepts_rvol_equality(monkeypatch):
    rows = []
    for i in range(60):
        px = 100 + i * 0.1
        rows.append(candle(i, o=px - 0.05, h=px + 0.1, l=px - 0.1, c=px))
    # Session VWAP is around 102 here; pull back through it but reclaim on close.
    rows[40] = candle(40, o=103.8, h=104.2, l=101.5, c=104.0)
    rows[41] = candle(41, o=104.0, h=104.7, l=103.9, c=104.5, v=120)
    rows[42] = candle(42, o=104.6, h=105, l=104, c=104.8)
    one = build_features(rows, "1m")
    fifteen = build_features([candle(i, minutes=15) for i in range(20)], "15m")
    engine = ScalpingStrategyEngine()
    monkeypatch.setattr(engine, "_trend_at", lambda *_: "BULLISH")
    found = engine._vwap_pullback("BTCUSDT", one, fifteen, False)
    match = [r for r in found if r.evidence.get("pullback_index") == 40]
    assert len(match) == 1
    assert match[0].signal_index == 41
    assert match[0].evidence["confirmation_rvol"] == pytest.approx(1.2)


def test_catalog_registers_ablation_before_evaluation_and_incomplete_d_is_blocked():
    keys = {(r.strategy_id, r.variant): r for r in SCALPING_STRATEGIES}
    assert ("scalp_sweep_reclaim_v1", "reclaim_only") in keys
    assert ("scalp_sweep_reclaim_v1", "reclaim_choch") in keys
    assert keys[("scalp_compression_retest_v1", "default")].specification_complete is False


def test_validation_requires_historical_forward_and_non_holdout_selection():
    registry = ScalpingValidationRegistry()
    registry.record(
        strategy_id="scalp_bos_retest_v1",
        historical_passed=True,
        forward_paper_passed=False,
        selected_without_final_holdout=True,
        evidence={"artifact": "paper-window-1"},
    )
    assert registry.is_publishable("scalp_bos_retest_v1") is False
    registry.record(
        strategy_id="scalp_bos_retest_v1",
        historical_passed=True,
        forward_paper_passed=True,
        selected_without_final_holdout=True,
        evidence={"artifact": "paper-window-2"},
    )
    assert registry.is_publishable("scalp_bos_retest_v1") is True


def test_time_exit_is_at_maximum_holding_close():
    rows = [candle(i, o=100, h=100.5, l=99.5, c=100.25) for i in range(40)]
    candidate = ScalpCandidate(
        strategy_id="scalp_bos_retest_v1", variant="default", symbol="BTCUSDT",
        direction="LONG", signal_index=19, signal_time=BASE + timedelta(minutes=20),
        entry_index=20, entry_time=BASE + timedelta(minutes=20), entry_price=100,
        stop_price=90, target_price=115, max_holding_minutes=20, frozen_atr=2,
    )
    trade = simulate_candidate(candidate, rows)
    assert trade.outcome == "TIME"
    assert trade.exit_index == 39
    assert trade.exit_price == pytest.approx(100.25)
