"""Unit tests for Candle-1/Candle-2 research V2 (no live engine changes)."""

from __future__ import annotations

import ast
import inspect
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research.candle12_v2 import (
    Candle12V2Config,
    _realized_r,
    _trigger_and_entry,
    _sample_warning,
    detect_c1_v2,
    run_candle12_v2,
    run_candle12_v2_with_periods,
    try_c2_entry_v2,
)
from app.research.config import split_period_indices
from app.research.data_quality import verify_ohlcv
from app.research.rolling_structure import (
    DEFAULT_LOOKBACK_BARS,
    MAX_LOOKBACK_BARS,
    RollingSwingState,
    analyze_structure_at_bar,
    clamp_lookback,
)
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings


def _c(i: int, o: float, h: float, l: float, cl: float, tf_min: int = 15):
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return {
        "time": base + timedelta(minutes=tf_min * i),
        "open": o,
        "high": h,
        "low": l,
        "close": cl,
        "volume": 1000.0,
    }


def test_entry_not_c2_close_long():
    trigger, entry = _trigger_and_entry(
        direction="LONG",
        c1_high=100.0,
        c1_low=99.0,
        cfg=Candle12V2Config(entry_slippage=0.001, tick_size=0.1),
    )
    assert trigger == 100.0
    assert entry == 100.0 * 1.001 + 0.1
    assert entry != 101.5


def test_entry_not_c2_close_short():
    trigger, entry = _trigger_and_entry(
        direction="SHORT",
        c1_high=100.0,
        c1_low=99.0,
        cfg=Candle12V2Config(entry_slippage=0.001, tick_size=0.05),
    )
    assert trigger == 99.0
    assert entry == 99.0 * 0.999 - 0.05


def test_realized_r_includes_fees_and_gross_net():
    cfg = Candle12V2Config(trading_fee=0.001, entry_slippage=0.0, exit_slippage=0.0)
    net_r, fees, risk, gross_r = _realized_r(
        direction="LONG", entry=100.0, stop=90.0, exit_price=110.0, cfg=cfg
    )
    assert risk == 10.0
    assert fees == 0.001 * (100.0 + 110.0)
    assert gross_r == 10.0 / 10.0
    assert net_r == (10.0 - fees) / 10.0
    assert net_r < gross_r


def test_min_sample_warning():
    w = _sample_warning(5, 30)
    assert w is not None
    assert w["flag"] == "MINIMUM_SAMPLE_SIZE_WARNING"
    assert w["label"] == "INSUFFICIENT_SAMPLE"
    assert _sample_warning(30, 30) is None


def test_ambiguous_same_bar_conservative():
    candles = [_c(i, 100, 101, 99, 100) for i in range(5)]
    candles.append(_c(5, 100, 105, 95, 102))
    candles.append(_c(6, 102, 106, 94, 103))

    setup = {
        "direction": "LONG",
        "c1_ohlc": {"open": 100, "high": 105, "low": 95, "close": 102},
        "pullback": {"retracement_low": 95, "impulse_origin": 95},
        "impulse": {"impulse_origin": 95, "atr": 1.0},
        "swings": [],
        "atr": 1.0,
        "bos_level": 101,
        "setup_as_of_index": 5,
    }
    plan = try_c2_entry_v2(
        setup=setup,
        candles=candles,
        candle1_index=5,
        signal_config=SignalConfig(),
        v2=Candle12V2Config(entry_slippage=0.0, exit_slippage=0.0, tick_size=0.0),
    )
    assert plan is not None
    assert plan["entry_price"] == 105.0
    assert plan["entry_price"] != plan["c2_close"]
    assert plan["c2_close_not_used_as_entry"] is True
    assert plan["confirmation_candle_index"] == 6
    assert plan["entry_candle_index"] >= plan["confirmation_candle_index"]
    assert plan.get("immediate_exit") is not None
    assert plan["immediate_exit"]["outcome"] in ("SL", "AMBIGUOUS_INTRABAR")
    if plan["immediate_exit"]["outcome"] == "SL" and plan.get("outcome_resolution"):
        assert plan["outcome_resolution"] == "CONSERVATIVE_SAME_BAR_SL"


def test_no_on2_full_history_slicing_pattern():
    """Source must not grow a 0..i prefix each bar (O(n²) full-history scan)."""
    root = Path(__file__).resolve().parents[1] / "app" / "research"
    forbidden_hits = []
    for path in (root / "candle12_v2.py", root / "rolling_structure.py"):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            sl = node.slice
            if isinstance(sl, ast.Slice) and sl.lower is None and sl.upper is not None:
                # candles[:name] growing prefix — forbidden in research hot path
                if isinstance(sl.upper, ast.Name):
                    forbidden_hits.append((path.name, f"prefix_slice:{sl.upper.id}"))
    assert not forbidden_hits, f"O(n²) slicing pattern found: {forbidden_hits}"
    # Positive contract: lookback is bounded
    assert "structure_lookback_bars" in (root / "candle12_v2.py").read_text(encoding="utf-8")
    assert "DEFAULT_LOOKBACK_BARS" in (root / "rolling_structure.py").read_text(
        encoding="utf-8"
    )


def test_lookback_defaults_and_clamp():
    assert DEFAULT_LOOKBACK_BARS == 300
    cfg = Candle12V2Config()
    assert cfg.structure_lookback_bars == 300
    assert cfg.lookback_bars == 300
    with pytest.raises(ValueError):
        clamp_lookback(MAX_LOOKBACK_BARS + 1)


def test_rolling_lookback_deterministic():
    candles = []
    p = 100.0
    for i in range(400):
        # create some structure
        c = p + (1.5 if i % 17 == 0 else (-0.4 if i % 5 == 0 else 0.1))
        o = p
        candles.append(
            _c(i, o, max(o, c) + 0.8, min(o, c) - 0.8, c, tf_min=60)
        )
        p = c
    v2 = Candle12V2Config(structure_lookback_bars=300, require_impulse=False)
    scfg = SignalConfig()
    a = run_candle12_v2("T", "1h", candles, signal_config=scfg, v2_config=v2)
    b = run_candle12_v2("T", "1h", candles, signal_config=scfg, v2_config=v2)
    assert a["sample_size"] == b["sample_size"]
    assert len(a["trades"]) == len(b["trades"])
    for ta, tb in zip(a["trades"], b["trades"]):
        assert ta["entry_index"] == tb["entry_index"]
        assert ta["entry_price"] == tb["entry_price"]
        assert ta["direction"] == tb["direction"]
        assert ta.get("r_multiple") == tb.get("r_multiple")


def test_incremental_swings_match_full_detect():
    candles = []
    p = 100.0
    for i in range(350):
        c = p + (2.0 if i % 11 == 0 else -0.3)
        o = p
        candles.append(_c(i, o, max(o, c) + 1.0, min(o, c) - 1.0, c, tf_min=60))
        p = c
    left, right, lookback = 3, 3, 300
    state = RollingSwingState(left=left, right=right, symbol="T", timeframe="1h")
    for end in range(40, 350):
        start = max(0, end - lookback + 1)
        window = candles[start : end + 1]
        full = detect_swings(
            window, left=left, right=right, symbol="T", timeframe="1h"
        )
        incr = state.update(candles, end=end, lookback=lookback)
        full_abs = [(s.swing_type, s.bar_index + start, s.price) for s in full]
        incr_abs = [(s.swing_type, s.bar_index, s.price) for s in incr]
        assert full_abs == incr_abs


def test_c1_c2_indices_and_no_lookahead_assertions():
    # Build a longer synthetic series; may or may not produce trades
    candles = [_c(i, 100 + i * 0.01, 101 + i * 0.01, 99 + i * 0.01, 100.5 + i * 0.01) for i in range(120)]
    v2 = Candle12V2Config(require_impulse=False, structure_lookback_bars=80)
    out = run_candle12_v2("T", "15m", candles, v2_config=v2)
    for t in out.get("trades") or []:
        snap = t.get("condition_snapshot") or {}
        setup_i = snap.get("setup_candle_index")
        conf_i = snap.get("confirmation_candle_index")
        entry_i = snap.get("entry_candle_index")
        assert conf_i == setup_i + 1
        assert entry_i >= conf_i
        assert snap.get("verification", {}).get("setup_as_of_index") <= setup_i


def test_missing_candles_not_fabricated():
    # Continuous 0..59 then jump to 61 (skip bar 60) — real gap, no fill
    candles = [_c(i, 1, 2, 0.5, 1.5, tf_min=15) for i in range(60)]
    candles.append(_c(61, 1, 2, 0.5, 1.5, tf_min=15))
    q = verify_ohlcv(candles, "15m")
    assert q.get("fabricated") is False
    assert q.get("interpolated") is False
    assert (q.get("gap_count") or 0) >= 1
    assert q["data_quality"] in ("DATA_GAPS", "INSUFFICIENT_DATA", "DUPLICATES")


def test_duplicate_candles_detected():
    c0 = _c(0, 1, 2, 0.5, 1.5)
    candles = [c0, dict(c0), _c(1, 1, 2, 0.5, 1.5)]
    # Pad to min_bars
    for i in range(2, 60):
        candles.append(_c(i, 1, 2, 0.5, 1.5))
    q = verify_ohlcv(candles, "15m")
    assert q["duplicate_count"] >= 1
    assert q["data_quality"] == "DUPLICATES" or q["status"] == "DUPLICATES"


def test_fees_slippage_applied():
    cfg = Candle12V2Config(
        trading_fee=0.001, entry_slippage=0.001, exit_slippage=0.001, tick_size=0.0
    )
    trigger, entry = _trigger_and_entry(
        direction="LONG", c1_high=100.0, c1_low=99.0, cfg=cfg
    )
    assert entry > trigger
    net_r, fees, risk, gross_r = _realized_r(
        direction="LONG", entry=entry, stop=90.0, exit_price=110.0, cfg=cfg
    )
    assert fees > 0
    assert net_r < gross_r


def test_train_val_oos_chronological():
    n = 1000
    splits = split_period_indices(n, train_fraction=0.6, validation_fraction=0.2, oos_fraction=0.2)
    train = splits["TRAINING_PERIOD"]
    val = splits["VALIDATION_PERIOD"]
    oos = splits["OUT_OF_SAMPLE_PERIOD"]
    assert train[0] == 0
    assert train[1] <= val[0]
    assert val[1] <= oos[0]
    assert oos[1] == n
    # No overlap
    assert train[1] == val[0]
    assert val[1] == oos[0]


def test_oos_cannot_influence_configuration():
    candles = [_c(i, 100, 101, 99, 100.2) for i in range(200)]
    v2 = Candle12V2Config(structure_lookback_bars=100, require_impulse=True)
    frozen = v2.to_dict()
    out = run_candle12_v2_with_periods("T", "15m", candles, v2_config=v2)
    assert out["configuration_id"]["structure_lookback_bars"] == frozen["structure_lookback_bars"]
    assert out["configuration_id"]["trading_fee"] == frozen["trading_fee"]
    assert "OOS" in out["oos_note"].upper() or "out-of-sample" in out["oos_note"].lower()
    # Config identical before/after periods computed
    assert out["configuration_id"]["require_impulse"] == frozen["require_impulse"]


def test_insufficient_data_does_not_crash():
    out = run_candle12_v2("T", "15m", [_c(0, 1, 2, 0.5, 1)], v2_config=Candle12V2Config())
    assert out["status"] == "INSUFFICIENT_DATA"
    assert out["sample_size"] == 0


def test_uses_more_than_500_bar_memory_cap():
    """Research processes series longer than the in-memory 500-bar store cap."""
    n = 650
    candles = []
    p = 100.0
    for i in range(n):
        c = p + (1.2 if i % 13 == 0 else -0.2)
        o = p
        candles.append(_c(i, o, max(o, c) + 0.7, min(o, c) - 0.7, c, tf_min=60))
        p = c
    v2 = Candle12V2Config(structure_lookback_bars=300, require_impulse=False, min_bars=50)
    out = run_candle12_v2("T", "1h", candles, v2_config=v2)
    assert out["status"] == "OK"
    assert out["instrumentation"]["candles_processed"] > 500
    assert len(candles) > 500


def test_linear_scaling_not_quadratic():
    """Processing time should scale roughly linearly with n (bounded lookback)."""

    def make(n: int):
        candles = []
        p = 100.0
        for i in range(n):
            c = p + (0.8 if i % 19 == 0 else -0.15)
            o = p
            candles.append(_c(i, o, max(o, c) + 0.5, min(o, c) - 0.5, c, tf_min=60))
            p = c
        return candles

    v2 = Candle12V2Config(structure_lookback_bars=200, require_impulse=False)
    scfg = SignalConfig()
    t0 = time.perf_counter()
    run_candle12_v2("T", "1h", make(800), signal_config=scfg, v2_config=v2)
    t_small = time.perf_counter() - t0
    t0 = time.perf_counter()
    run_candle12_v2("T", "1h", make(1600), signal_config=scfg, v2_config=v2)
    t_large = time.perf_counter() - t0
    # Quadratic would be ~4x; allow generous overhead but fail if > 3.5x
    assert t_large < t_small * 3.5 + 0.5, (
        f"suspected super-linear scaling: {t_small:.3f}s -> {t_large:.3f}s"
    )


def test_detect_c1_as_of_not_beyond_setup():
    candles = [_c(i, 100, 101, 99, 100.2) for i in range(100)]
    scfg = SignalConfig()
    v2 = Candle12V2Config(require_impulse=False, structure_lookback_bars=60)
    state = RollingSwingState(left=3, right=3, symbol="T", timeframe="15m")
    for i in range(50, 80):
        setup = detect_c1_v2(
            symbol="T",
            timeframe="15m",
            candles=candles,
            candle1_index=i,
            signal_config=scfg,
            v2=v2,
            swing_state=state,
        )
        if setup:
            assert setup["setup_as_of_index"] == i
            assert setup["as_of_index"] == i


def test_live_signal_engine_module_not_modified_by_research_path():
    """Research C1 path must use rolling structure, not full SignalEngine analyze."""
    src = inspect.getsource(detect_c1_v2)
    assert "analyze_structure_at_bar" in src
    assert "SignalEngine" not in src
    # Live engine module remains importable and unchanged by this path
    from app.signals.signal_engine import SignalEngine

    assert hasattr(SignalEngine, "analyze_timeframe")
