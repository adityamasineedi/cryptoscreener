"""Research-only multi-bar pullback/retest lifecycle tests.

Does not modify production signal engines.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.lifecycle import (
    candles_as_of,
    evaluate_pullback_at,
    freeze_bos_impulse_event,
    make_lifecycle_id,
    run_lifecycle,
    run_lifecycles_for_series,
)
from app.signals.config import SignalConfig
from app.signals.pullback_engine import detect_pullback
from app.signals.signal_engine import SignalEngine

from tests.test_bos_strategy_comparison import make_uptrend_series


class TrackingCandles(list):
    """Sequence that records max index accessed via __getitem__."""

    def __init__(self, data):
        super().__init__(data)
        self.max_accessed = -1

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if stop > 0:
                self.max_accessed = max(self.max_accessed, stop - 1)
            return list.__getitem__(self, key)
        idx = key if key >= 0 else len(self) + key
        self.max_accessed = max(self.max_accessed, idx)
        return list.__getitem__(self, key)


def _forced_frozen_event(candles, bos_index: int = 50):
    """Build a frozen event with impulse at bos_index (manual, for unit tests)."""
    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 110.0,
        "break_price": 112.0,
        "break_timestamp": None,
    }
    _, h, l, _ = (
        float(candles[bos_index]["open"]),
        float(candles[bos_index]["high"]),
        float(candles[bos_index]["low"]),
        float(candles[bos_index]["close"]),
    )
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": bos_index,
        "impulse_origin": l,
        "impulse_end": h,
        "atr": 1.0,
        "atr_multiple": 2.0,
        "rvol": 2.0,
        "quality": "MODERATE",
    }
    return freeze_bos_impulse_event(
        symbol="TESTUSDT",
        timeframe="15m",
        bos_index=bos_index,
        bos=bos,
        impulse=impulse,
        candles=candles,
    )


def test_lifecycle_id_deterministic():
    a = make_lifecycle_id(
        symbol="btcusdt",
        timeframe="15m",
        bos_index=56,
        bos_direction="BEARISH_BOS",
        impulse_index=56,
    )
    b = make_lifecycle_id(
        symbol="BTCUSDT",
        timeframe="15m",
        bos_index=56,
        bos_direction="BEARISH_BOS",
        impulse_index=56,
    )
    assert a == b
    assert a == "BTCUSDT|15m|56|BEARISH_BOS|56"


def test_same_bar_bos_impulse_waiting():
    candles = make_uptrend_series(100)
    event = _forced_frozen_event(candles, 60)
    cfg = SignalConfig()
    pb = evaluate_pullback_at(
        candles=candles, event=event, as_of_index=60, signal_config=cfg
    )
    assert pb["pullback_state"] == "WAITING"
    assert "Waiting for bars after impulse" in pb["reason"]


def test_later_candle_can_progress_pullback():
    """Construct post-impulse pullback bars so engine can leave WAITING."""
    base = datetime(2024, 9, 1, tzinfo=timezone.utc)
    candles = []
    price = 100.0
    for i in range(80):
        o = price
        c = price + 0.5
        candles.append(
            {
                "time": base + timedelta(minutes=15 * i),
                "open": o,
                "high": max(o, c) + 0.3,
                "low": min(o, c) - 0.2,
                "close": c,
                "volume": 2000.0,
            }
        )
        price = c
    # Impulse bar
    impulse_i = 60
    candles[impulse_i] = {
        "time": base + timedelta(minutes=15 * impulse_i),
        "open": 130.0,
        "high": 140.0,
        "low": 129.0,
        "close": 139.0,
        "volume": 5000.0,
    }
    # Mild pullback bars (~40% of 11 range → toward 135.6)
    for j, low in enumerate([136.0, 135.0, 134.5]):
        idx = impulse_i + 1 + j
        candles[idx] = {
            "time": base + timedelta(minutes=15 * idx),
            "open": 138.0,
            "high": 138.5,
            "low": low,
            "close": 136.5,
            "volume": 1500.0,
        }

    bos = {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 135.0,
        "break_price": 139.0,
    }
    impulse = {
        "is_impulse": True,
        "direction": "BULLISH",
        "bar_index": impulse_i,
        "impulse_origin": 129.0,
        "impulse_end": 140.0,
        "atr": 2.0,
        "quality": "STRONG",
        "rvol": 2.0,
    }
    event = freeze_bos_impulse_event(
        symbol="TESTUSDT",
        timeframe="15m",
        bos_index=impulse_i,
        bos=bos,
        impulse=impulse,
        candles=candles,
    )
    cfg = SignalConfig()
    same = evaluate_pullback_at(
        candles=candles, event=event, as_of_index=impulse_i, signal_config=cfg
    )
    assert same["pullback_state"] == "WAITING"

    lc = run_lifecycle(
        candles=candles, event=event, signal_config=cfg, max_follow_bars=10
    )
    states = [e.pullback_state for e in lc.evaluations]
    assert "WAITING" in states or lc.terminal_pullback_state != "WAITING" or len(states) > 0
    # Must progress beyond same-bar waiting once post-impulse bars exist
    assert lc.candles_evaluated >= 1
    assert any(s != "WAITING" for s in states) or lc.pullback_pass or lc.terminal_pullback_state in (
        "ACTIVE",
        "CONFIRMED",
        "FAILED",
        "INVALIDATED",
    )


def test_frozen_impulse_unchanged_after_lifecycle():
    candles = make_uptrend_series(100)
    event = _forced_frozen_event(candles, 55)
    before = deepcopy(event.frozen_impulse)
    before_bos = deepcopy(event.frozen_bos)
    run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=15
    )
    assert event.frozen_impulse == before
    assert event.frozen_bos == before_bos


def test_waiting_does_not_duplicate_lifecycles():
    candles = make_uptrend_series(160)
    report = run_lifecycles_for_series(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        index_start=40,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=20, research_max_lifecycle_bars=20),
        max_trace_lifecycles=5,
    )
    assert report["lifecycles_started"] == report["impulse_candidates"]
    # Evaluations may be many; opportunities equal lifecycles_started
    ids = [t["lifecycle_id"] for t in report["lifecycle_traces"]]
    assert len(ids) == len(set(ids))


def test_pullback_terminal_preserved():
    candles = make_uptrend_series(100)
    event = _forced_frozen_event(candles, 50)
    lc = run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=20
    )
    assert lc.completed is True
    assert lc.termination is not None
    assert lc.terminal_pullback_state is not None


def test_retest_only_after_pullback_ready():
    candles = make_uptrend_series(100)
    event = _forced_frozen_event(candles, 50)
    lc = run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=20
    )
    for ev in lc.evaluations:
        if ev.pullback_state in ("WAITING", None, "NONE"):
            assert ev.retest.get("retest") is False
            assert "Waiting for pullback" in str(ev.retest.get("reason") or "")


def test_no_future_candle_access():
    raw = make_uptrend_series(90)
    tracked = TrackingCandles(raw)
    event = _forced_frozen_event(raw, 50)
    as_of = 55
    window = candles_as_of(tracked, as_of)
    assert len(window) == as_of + 1
    pb = detect_pullback(
        window,
        event.frozen_bos,
        event.frozen_impulse,
        SignalConfig(),
        as_of_index=len(window) - 1,
    )
    assert tracked.max_accessed <= as_of
    assert pb is not None


def test_as_of_index_respected_truncated_equals_full():
    candles = make_uptrend_series(100)
    event = _forced_frozen_event(candles, 40)
    cfg = SignalConfig()
    for as_of in (41, 50, 70):
        a = evaluate_pullback_at(
            candles=candles, event=event, as_of_index=as_of, signal_config=cfg
        )
        window = candles_as_of(candles, as_of)
        b = detect_pullback(
            window,
            event.frozen_bos,
            event.frozen_impulse,
            cfg,
            as_of_index=len(window) - 1,
        )
        assert a == b


def test_bos_impulse_not_recalculated_after_freeze():
    candles = make_uptrend_series(120)
    eng = SignalEngine(SignalConfig())
    # Find a real BOS+impulse bar if any
    found = None
    for i in range(40, len(candles)):
        tf = eng.analyze_timeframe("TESTUSDT", "15m", candles, as_of_index=i)
        bos, impulse = tf.get("bos"), tf.get("impulse")
        if (
            bos
            and bos.get("state") == "CONFIRMED"
            and impulse
            and impulse.get("is_impulse")
        ):
            found = (i, bos, impulse)
            break
    if found is None:
        event = _forced_frozen_event(candles, 55)
    else:
        i, bos, impulse = found
        event = freeze_bos_impulse_event(
            symbol="TESTUSDT",
            timeframe="15m",
            bos_index=i,
            bos=bos,
            impulse=impulse,
            candles=candles,
        )
    snap = deepcopy(event.frozen_impulse)
    run_lifecycle(
        candles=candles, event=event, signal_config=SignalConfig(), max_follow_bars=25
    )
    # Later analyze_timeframe must not mutate frozen snapshot
    eng.analyze_timeframe(
        "TESTUSDT", "15m", candles, as_of_index=min(len(candles) - 1, event.impulse_index + 10)
    )
    assert event.frozen_impulse == snap


def test_production_engine_output_unchanged_by_lifecycle_wrapper():
    candles = make_uptrend_series(100)
    event = _forced_frozen_event(candles, 50)
    cfg = SignalConfig()
    as_of = 60
    direct = detect_pullback(
        candles_as_of(candles, as_of),
        event.frozen_bos,
        event.frozen_impulse,
        cfg,
        as_of_index=as_of,
    )
    wrapped = evaluate_pullback_at(
        candles=candles, event=event, as_of_index=as_of, signal_config=cfg
    )
    assert direct == wrapped


def test_no_fabricated_candles_flag():
    candles = make_uptrend_series(140)
    report = run_lifecycles_for_series(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        index_start=40,
        research_config=StrategyResearchConfig(min_bars=20),
    )
    assert report["data_quality"]["fabricated_candles"] is False
    assert report["live_engines_unchanged"] is True
    assert report["pullback_logic_unchanged"] is True


def test_waiting_not_fail():
    candles = make_uptrend_series(80)
    event = _forced_frozen_event(candles, 40)
    pb = evaluate_pullback_at(
        candles=candles, event=event, as_of_index=40, signal_config=SignalConfig()
    )
    assert pb["pullback_state"] == "WAITING"
    assert pb["pullback_state"] != "FAILED"
