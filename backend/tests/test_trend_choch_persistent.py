"""CHOCH-persistent trend mode — SOL Sept 2026 rally + synthetic contracts.

Research-only classifier tests. Default ``legacy`` mode is unchanged for
COMBO_02 v1 fingerprint parity.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.signals.config import SignalConfig
from app.signals.schemas import SwingRecord, TrendState
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import (
    TREND_MODE_CHOCH_PERSISTENT,
    TREND_MODE_LEGACY,
    ChochPersistentCursor,
    apply_persistent_close,
    infer_trend,
)


def _sw(
    *,
    swing_type: str,
    price: float,
    bar_index: int,
    label: str,
) -> SwingRecord:
    ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return SwingRecord(
        symbol="T",
        timeframe="1h",
        swing_type=swing_type,
        price=price,
        timestamp=ts,
        bar_index=bar_index,
        strength=1.0,
        confirmed_at=ts,
        label=label,
    )


def test_legacy_mode_unchanged_on_hh_hl():
    swings = [
        _sw(swing_type="HIGH", price=10.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=8.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=12.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=9.0, bar_index=4, label="HL"),
    ]
    t = infer_trend(swings, mode=TREND_MODE_LEGACY)
    assert t["trend"] == TrendState.BULLISH.value
    assert t["trend_mode"] == TREND_MODE_LEGACY


def test_legacy_micro_lh_ll_flips_bearish_at_rally_highs():
    """Documents the stale-reference bug: last-2 LH+LL → BEARISH mid-rally."""
    swings = [
        _sw(swing_type="HIGH", price=100.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=90.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=120.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=110.0, bar_index=4, label="HL"),
        _sw(swing_type="HIGH", price=122.0, bar_index=5, label="HH"),
        _sw(swing_type="LOW", price=118.0, bar_index=6, label="HL"),
        # Micro pullback at the highs
        _sw(swing_type="HIGH", price=121.0, bar_index=7, label="LH"),
        _sw(swing_type="LOW", price=117.5, bar_index=8, label="LL"),
    ]
    legacy = infer_trend(swings, mode=TREND_MODE_LEGACY)
    assert legacy["trend"] == TrendState.BEARISH.value

    # LL 117.5 still above the impulse HL 110 → stay BULLISH.
    persistent = infer_trend(
        swings, close=120.0, mode=TREND_MODE_CHOCH_PERSISTENT
    )
    assert persistent["trend"] == TrendState.BULLISH.value
    assert persistent["trend_mode"] == TREND_MODE_CHOCH_PERSISTENT


def test_persistent_true_choch_flips_bearish():
    # Impulse HL that preceded HH 120 is 90; LL must break that level.
    swings = [
        _sw(swing_type="HIGH", price=100.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=90.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=120.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=110.0, bar_index=4, label="HL"),
        _sw(swing_type="HIGH", price=118.0, bar_index=5, label="LH"),
        _sw(swing_type="LOW", price=85.0, bar_index=6, label="LL"),  # < impulse HL 90
    ]
    t = infer_trend(swings, mode=TREND_MODE_CHOCH_PERSISTENT)
    assert t["trend"] == TrendState.BEARISH.value


def test_persistent_close_reclaim_above_lh_is_bullish():
    """4h-style stale BEARISH while price already reclaimed the LH."""
    swings = [
        _sw(swing_type="HIGH", price=119.99, bar_index=10, label="HH"),
        _sw(swing_type="LOW", price=115.53, bar_index=11, label="HL"),
        _sw(swing_type="HIGH", price=119.68, bar_index=12, label="LH"),
        _sw(swing_type="LOW", price=112.39, bar_index=13, label="LL"),
    ]
    legacy = infer_trend(swings, close=120.79, mode=TREND_MODE_LEGACY)
    assert legacy["trend"] == TrendState.BEARISH.value

    persistent = infer_trend(
        swings, close=120.79, mode=TREND_MODE_CHOCH_PERSISTENT
    )
    assert persistent["trend"] == TrendState.BULLISH.value
    assert "CHOCH" in persistent["reason"] or "reclaim" in persistent["reason"]


def test_persistent_close_below_hl_is_bearish_choch():
    """As-of close below active impulse HL flips bullish structure."""
    swings = [
        _sw(swing_type="HIGH", price=100.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=90.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=110.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=95.0, bar_index=4, label="HL"),
    ]
    # Active HL is 90 (preceded HH 110); close 89 breaks it.
    t = infer_trend(swings, close=89.0, mode=TREND_MODE_CHOCH_PERSISTENT)
    assert t["trend"] == TrendState.BEARISH.value


@pytest.mark.parametrize("close", [85.0, 89.0, 96.0, 111.0, 125.0])
def test_cached_persistent_close_matches_full_replay(close: float):
    swings = [
        _sw(swing_type="HIGH", price=100.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=90.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=120.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=110.0, bar_index=4, label="HL"),
        _sw(swing_type="HIGH", price=118.0, bar_index=5, label="LH"),
        _sw(swing_type="LOW", price=108.0, bar_index=6, label="LL"),
    ]
    structural = infer_trend(
        swings, close=None, mode=TREND_MODE_CHOCH_PERSISTENT
    )
    cached = apply_persistent_close(
        structural,
        close=close,
        last_swing_high=swings[-2],
        last_swing_low=swings[-1],
    )
    replayed = infer_trend(
        swings, close=close, mode=TREND_MODE_CHOCH_PERSISTENT
    )
    assert cached == replayed


def test_default_mode_is_legacy():
    swings = [
        _sw(swing_type="HIGH", price=10.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=8.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=12.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=9.0, bar_index=4, label="HL"),
    ]
    assert infer_trend(swings)["trend_mode"] == TREND_MODE_LEGACY


@pytest.mark.asyncio
async def test_sol_sept_2026_rally_persistent_bullish_1h_and_4h():
    """Price rose ~98 → 121; choch_persistent must read BULLISH on 1h and 4h.

    Window: closed bars with close >= 118 from 2026-09-21 through 2026-09-27
    (rally peak). Uses only as-of confirmed swings + that bar's close.
    """
    from app.config import get_settings
    from app.research.service import _load_research_candles
    from app.services.database import db_manager
    from app.signals._candle_utils import candle_time, ohlc

    settings = get_settings()
    if not getattr(settings, "database_enabled", True):
        pytest.skip("database disabled")

    await db_manager.connect(settings, ensure_schema=False)
    try:
        cfg = SignalConfig()
        start = datetime(2026, 9, 21, tzinfo=timezone.utc)
        end = datetime(2026, 9, 28, tzinfo=timezone.utc)
        failures: list[str] = []
        checked = 0
        for tf in ("1h", "4h"):
            candles, _, _ = await _load_research_candles(
                "SOLUSDT",
                tf,
                limit=2000,
                start_date="2026-09-01",
                end_date="2026-10-06",
                warmup_bars=100,
            )
            if len(candles) < 50:
                pytest.skip(f"insufficient SOLUSDT {tf} history")
            for i, c in enumerate(candles):
                t = candle_time(c)
                if t is None:
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if t < start or t >= end:
                    continue
                close = ohlc(candles, i)[3]
                if close is None or float(close) < 118.0:
                    continue
                swings = detect_swings(
                    candles,
                    left=cfg.swing_left_bars,
                    right=cfg.swing_right_bars,
                    symbol="SOLUSDT",
                    timeframe=tf,
                    as_of_index=i,
                )
                tr = infer_trend(
                    swings,
                    close=float(close),
                    mode=TREND_MODE_CHOCH_PERSISTENT,
                )
                checked += 1
                if tr["trend"] != TrendState.BULLISH.value:
                    failures.append(
                        f"{tf} {t.isoformat()} close={close} → {tr['trend']} "
                        f"({tr.get('reason')})"
                    )
        assert checked >= 10, f"expected rally samples, got {checked}"
        assert not failures, (
            f"{len(failures)}/{checked} bars not BULLISH under choch_persistent:\n"
            + "\n".join(failures[:20])
        )
    finally:
        await db_manager.close()


def test_signal_config_default_trend_mode_legacy():
    assert SignalConfig().trend_mode == TREND_MODE_LEGACY


def test_feature_table_trend_matches_full_replay():
    """CONTEXT_BUILD fold must label each bar the same as infer_trend."""
    from datetime import timedelta

    from app.research.market_structure.config import MarketStructureFeatureConfig
    from app.research.market_structure.structure import build_timeframe_feature_table
    from app.signals.swing_detector import extend_swings

    cfg = MarketStructureFeatureConfig()
    n = 420
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    px = 100.0
    for i in range(n):
        px += (1.2 if (i // 5) % 2 == 0 else -0.8)
        candles.append(
            {
                "time": (start + timedelta(hours=i)).isoformat(),
                "open": px,
                "high": px + 1.5,
                "low": px - 1.5,
                "close": px + 0.2,
                "volume": 1000.0 + (i % 17),
            }
        )
    table = build_timeframe_feature_table(candles, "1h", symbol="ETHUSDT", config=cfg)
    assert table is not None
    swings: list = []
    prev = "NEUTRAL"
    min_i = max(cfg.swing_left + cfg.swing_right, cfg.min_bars_basic - 1)
    for i in range(min_i, n):
        if not swings:
            swings = detect_swings(
                candles,
                left=cfg.swing_left,
                right=cfg.swing_right,
                symbol="ETHUSDT",
                timeframe="1h",
                atr_period=cfg.atr_period,
                as_of_index=i,
            )
        else:
            swings = extend_swings(
                swings,
                candles,
                left=cfg.swing_left,
                right=cfg.swing_right,
                symbol="ETHUSDT",
                timeframe="1h",
                atr_period=cfg.atr_period,
                as_of_index=i,
            )
        base = infer_trend(swings, close=None, mode=TREND_MODE_CHOCH_PERSISTENT)
        highs = [s for s in swings if s.swing_type == "HIGH"]
        lows = [s for s in swings if s.swing_type == "LOW"]
        trend = apply_persistent_close(
            base,
            close=float(candles[i]["close"]),
            last_swing_high=highs[-1] if highs else None,
            last_swing_low=lows[-1] if lows else None,
        )
        label = str(trend.get("trend") or "NEUTRAL").upper()
        if label == "INSUFFICIENT_DATA":
            expected = "UNKNOWN_INSUFFICIENT_HISTORY"
        elif label == "BULLISH":
            expected = "BULLISH"
        elif label == "BEARISH":
            expected = "BEARISH"
        elif prev in {"BULLISH", "BEARISH"}:
            expected = "TRANSITION"
        else:
            expected = "NEUTRAL"
        assert table.trend_state[i] == expected
        if label in {"BULLISH", "BEARISH", "NEUTRAL"}:
            prev = label


def test_choch_cursor_prefixes_match_full_replay():
    """Appending swings must match infer_trend(close=None) on every prefix."""
    swings = [
        _sw(swing_type="HIGH", price=100.0, bar_index=1, label="HH"),
        _sw(swing_type="LOW", price=90.0, bar_index=2, label="HL"),
        _sw(swing_type="HIGH", price=110.0, bar_index=3, label="HH"),
        _sw(swing_type="LOW", price=95.0, bar_index=4, label="HL"),
        _sw(swing_type="HIGH", price=108.0, bar_index=5, label="LH"),
        _sw(swing_type="LOW", price=88.0, bar_index=6, label="LL"),
        _sw(swing_type="HIGH", price=112.0, bar_index=7, label="HH"),
        _sw(swing_type="LOW", price=99.0, bar_index=8, label="HL"),
    ]
    cursor = ChochPersistentCursor()
    for k in range(1, len(swings) + 1):
        prefix = swings[:k]
        cursor.sync(prefix)
        folded = cursor.structural(prefix)
        full = infer_trend(prefix, close=None, mode=TREND_MODE_CHOCH_PERSISTENT)
        assert folded == full
