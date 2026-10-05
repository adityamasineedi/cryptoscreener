"""COMBO_02_V1_CLOSED_HTF — required regression tests.

Preserves frozen COMBO_02 forming-HTF behavior (htf_require_fully_closed=False).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from app.research.bos_combinations import get_combination
from app.research.bos_strategy_comparison.htf import (
    as_of_index_at_or_before,
    as_of_index_fully_closed,
    build_htf_as_of_index_map,
    build_htf_as_of_index_map_fully_closed,
    timeframe_seconds,
)
from app.research.combination_engine import hl_intact_for_long
from app.research.strategy_catalog import build_strategy_catalog
from app.signals.bos_engine import detect_bos
from app.signals.schemas import SwingRecord, TrendState
from app.signals.trend_engine import infer_trend
import app.research.service as svc

pytestmark = pytest.mark.v1_freeze


def _ts(hour: int, day: int = 15, month: int = 6) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=timezone.utc)


def _c(t: datetime, close: float = 100.0) -> dict:
    assert t.tzinfo is not None
    return {
        "time": t,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
    }


def _swing(kind: str, price: float, idx: int, label: str) -> SwingRecord:
    return SwingRecord(
        symbol="BTCUSDT",
        timeframe="1h",
        swing_type=kind,
        price=price,
        bar_index=idx,
        timestamp=_ts(idx),
        label=label,
        strength=1.0,
        confirmed_at=_ts(idx),
    )


# 1. 4h coverage calculation
def test_4h_coverage_formula_corrected():
    limit = 8640
    lim = max(int(limit) // 4 + 100, 200)
    assert lim == 2260
    assert lim == max(limit // 4 + 100, 200)
    buggy = max(int(limit) // 16 + 100, 200)
    assert buggy == 640
    assert lim > buggy


def test_service_loaders_use_corrected_4h_coverage():
    src_async = inspect.getsource(svc._load_htf_candles_for_combo)
    src_sync = inspect.getsource(svc._load_htf_candles_sync)
    for src in (src_async, src_sync):
        assert "int(limit) // 4 + 100" in src
        assert (
            'lim_4h = max(int(limit), 200) if tf == "4h" else max(int(limit) // 4 + 100, 200)'
            in src
        )


# 2. Original forming-HTF map preservation
def test_forming_htf_map_preserved_for_combo02():
    combo = get_combination("COMBO_02")
    assert combo is not None
    assert combo.htf_require_fully_closed is False
    setup = [_c(_ts(10))]
    htf = [_c(_ts(4)), _c(_ts(8))]
    forming = build_htf_as_of_index_map(setup, htf)
    assert forming[0] == 1  # open 08:00 <= setup open 10:00


# 3–5. Fully closed map, boundary, unfinished rejection
def test_fully_closed_4h_map_and_boundary_rules():
    # setup close 12:00 => setup open 11:00
    setup_open = _ts(11)
    decision = setup_open + timedelta(hours=1)  # 12:00
    htf = [
        _c(_ts(4)),  # closes 08:00
        _c(_ts(8)),  # closes 12:00 -> eligible
        _c(_ts(12)),  # closes 16:00 -> not eligible
    ]
    idx = as_of_index_fully_closed(
        htf, decision, htf_duration_seconds=timeframe_seconds("4h")
    )
    assert idx == 1
    # 4h open 08:00 close 12:00 eligible; open 12:00 close 16:00 not
    assert as_of_index_fully_closed(
        [_c(_ts(8))], decision, htf_duration_seconds=14400
    ) == 0
    assert (
        as_of_index_fully_closed(
            [_c(_ts(12))], decision, htf_duration_seconds=14400
        )
        is None
    )


def test_reject_unfinished_4h_at_non_boundary():
    setup = [_c(_ts(10))]  # close 11:00
    htf = [_c(_ts(4)), _c(_ts(8))]  # 08–12 still forming at 11:00
    closed = build_htf_as_of_index_map_fully_closed(
        setup, htf, setup_timeframe="1h", htf_timeframe="4h"
    )
    forming = build_htf_as_of_index_map(setup, htf)
    assert forming[0] == 1
    assert closed[0] == 0


# 6–7. UTC + no future
def test_utc_timezone_aware_and_no_future_4h():
    naive = datetime(2026, 6, 15, 10, 0)  # naive
    aware = naive.replace(tzinfo=timezone.utc)
    htf = [
        {"time": aware - timedelta(hours=6), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
        {"time": aware + timedelta(hours=2), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
    ]
    # as_of uses aware setup open
    idx = as_of_index_at_or_before(htf, aware)
    assert idx == 0
    # fully closed decision = aware+1h; future 4h open must not be selected
    idx2 = as_of_index_fully_closed(htf, aware + timedelta(hours=1), htf_duration_seconds=14400)
    assert idx2 is None or idx2 == 0


# 8. Monotonic HTF mapping
def test_htf_mapping_monotonic_nondecreasing():
    setup = [_c(_ts(h)) for h in range(0, 24)]
    htf = [_c(_ts(h)) for h in range(0, 24, 4)]
    closed = build_htf_as_of_index_map_fully_closed(
        setup, htf, setup_timeframe="1h", htf_timeframe="4h"
    )
    prev = -1
    for v in closed:
        if v is None:
            continue
        assert v >= prev
        prev = v


# 9. Missing HTF history
def _ts_hours(offset_h: int) -> datetime:
    return datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc) + timedelta(hours=offset_h)


def test_missing_htf_history_returns_none():
    setup = [_c(_ts_hours(h)) for h in range(0, 8)]
    htf = [_c(_ts_hours(h)) for h in range(20, 40, 4)]  # starts after setup window
    closed = build_htf_as_of_index_map_fully_closed(
        setup, htf, setup_timeframe="1h", htf_timeframe="4h"
    )
    assert all(x is None for x in closed)
    forming = build_htf_as_of_index_map(setup, htf)
    assert all(x is None for x in forming)


# 10–11. BOS / trend preservation
def test_bos_and_trend_logic_unchanged():
    candles = [_c(_ts(0), 100), _c(_ts(1), 116)]
    swings = [_swing("HIGH", 115.0, 0, "HH"), _swing("LOW", 99.0, 0, "HL")]
    trend = {"trend": TrendState.BULLISH.value}
    bos = detect_bos(
        candles, swings, trend, symbol="BTCUSDT", timeframe="1h", as_of_index=1
    )
    assert bos and bos["direction"] == "BULLISH_BOS"
    # wick-only: close below level -> not confirmed BULLISH_BOS
    candles2 = [_c(_ts(0), 100), _c(_ts(1), 108)]
    bos2 = detect_bos(
        candles2, swings, trend, symbol="BTCUSDT", timeframe="1h", as_of_index=1
    )
    assert bos2 is None or bos2.get("direction") != "BULLISH_BOS"
    # Need two highs + two lows for HH+HL inference
    swings2 = [
        _swing("HIGH", 110.0, 0, None),
        _swing("LOW", 95.0, 1, None),
        _swing("HIGH", 115.0, 2, "HH"),
        _swing("LOW", 100.0, 3, "HL"),
    ]
    inferred = infer_trend(swings2)
    assert inferred.get("trend") == TrendState.BULLISH.value


# 12. Higher-low preservation
def test_higher_low_logic_preserved():
    candles = [_c(_ts(i), 100 + i) for i in range(6)]
    swings = [_swing("LOW", 99.0, 1, "HL"), _swing("HIGH", 110.0, 2, "HH")]
    ok, reason = hl_intact_for_long(candles, swings, 5)
    assert ok and reason == "HL_INTACT"
    # break HL by closing below
    candles[4]["close"] = 98.0
    ok2, reason2 = hl_intact_for_long(candles, swings, 5)
    assert not ok2
    assert reason2 in ("HL_BROKEN", "CHOCH_BEARISH")


# 13–14. Stop/target/risk — combo flags do not alter require_rr etc.
def test_stop_target_risk_flags_preserved_across_variants():
    v1 = get_combination("COMBO_02")
    closed = get_combination("COMBO_02_CLOSED_HTF")
    assert v1 and closed
    for c in (v1, closed):
        assert c.require_bos is True
        assert c.require_trend is True
        assert c.require_htf_alignment is True
        assert c.require_impulse is False
        assert c.require_pullback is False
        assert c.require_rvol is False
        assert c.require_sd is False
        assert c.require_rr is False
    # only closed-HTF flag differs
    assert v1.htf_require_fully_closed is False
    assert closed.htf_require_fully_closed is True


# 15. Strategy ID separation
def test_strategy_ids_separated_in_catalog():
    cat = build_strategy_catalog()
    assert cat["primary_working"] == "COMBO_02_V1"
    ids = {s["id"]: s for s in cat["strategies"]}
    assert "COMBO_02_V1" in ids
    assert "COMBO_02_V1_CLOSED_HTF" in ids
    assert ids["COMBO_02_V1"]["status"] == "WORKING"
    assert ids["COMBO_02_V1_CLOSED_HTF"]["status"] == "RESEARCH"
    assert ids["COMBO_02_V1_CLOSED_HTF"]["combo_id"] == "COMBO_02_CLOSED_HTF"
    assert ids["COMBO_02_V1_CLOSED_HTF"]["parent_strategy_id"] == "COMBO_02_V1"
    assert ids["COMBO_02_V1"]["combo_id"] != ids["COMBO_02_V1_CLOSED_HTF"]["combo_id"]


# 16. Comparison script exists / importable
def test_three_label_comparison_script_present():
    from pathlib import Path
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "compare_combo02_v1_closed_htf.py"
    assert path.exists()
    spec = importlib.util.spec_from_file_location("compare_closed_htf", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.LIM_4H_ORIGINAL == 640
    assert mod.LIM_4H_CORRECTED == 2260
    assert callable(mod.main)


# 17. Every final mapping satisfies close <= setup close (property on builder)
def test_every_closed_map_entry_fully_closed_property():
    setup = [_c(_ts_hours(h)) for h in range(0, 48)]
    htf = [_c(_ts_hours(h)) for h in range(0, 48, 4)]
    closed = build_htf_as_of_index_map_fully_closed(
        setup, htf, setup_timeframe="1h", htf_timeframe="4h"
    )
    for i, idx in enumerate(closed):
        if idx is None:
            continue
        setup_close = setup[i]["time"] + timedelta(hours=1)
        htf_close = htf[idx]["time"] + timedelta(hours=4)
        assert htf_close <= setup_close
