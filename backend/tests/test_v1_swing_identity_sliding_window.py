"""V1 watcher break check on a full, sliding 500-bar window.

The live store keeps at most 500 closed 1h bars, so the tip stays in slot 499
and the window slides under it. Swing identity has to follow the candle open
time. These tests slide stored AAVEUSDT 1h candles from the 10 Oct 19:00 UTC
bar through the 11 Oct 22:00 UTC bar and never open a position.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.research.combination_backtest import _bos_break_levels, _bos_break_possible
from app.services import v1_paper_watcher as watcher_mod
from app.services.v1_paper_watcher import V1PaperWatcher, swings_on_window
from app.signals.swing_detector import detect_swings

SYMBOL = "AAVEUSDT"
WINDOW = 500
FIRST_TIP = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)
LAST_TIP = datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "aaveusdt_1h_2026-09-20_2026-10-11.json"


class _NoPaper:
    """Any use of the paper engine fails the test."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"paper engine touched: {name}")


def _utc(day: int, hour: int) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def candles() -> list[dict[str, Any]]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = [
        {
            "time": datetime.fromisoformat(t),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }
        for t, o, h, l, c, v in raw["candles"]
    ]
    assert rows[WINDOW - 1]["time"] == FIRST_TIP
    assert rows[-1]["time"] == LAST_TIP
    assert all(
        (rows[i + 1]["time"] - rows[i]["time"]).total_seconds() == 3600
        for i in range(len(rows) - 1)
    )
    return rows


def _new_watcher() -> V1PaperWatcher:
    return V1PaperWatcher(enabled=True, paper_engine=_NoPaper(), emit_alerts=False)


def _fresh_swings(watcher: V1PaperWatcher, series: list[dict[str, Any]]) -> list[Any]:
    cfg = watcher.signal_config.swing_for("1h")
    return detect_swings(
        series,
        left=cfg.swing_left_bars,
        right=cfg.swing_right_bars,
        symbol=SYMBOL,
        timeframe="1h",
        atr_period=watcher.signal_config.atr_period,
        minimum_swing_distance_atr=cfg.minimum_swing_distance_atr,
        as_of_index=len(series) - 1,
    )


def _identity(swings: list[Any]) -> list[tuple[str, datetime, float, int, str | None]]:
    return [(s.swing_type, s.timestamp, s.price, s.bar_index, s.label) for s in swings]


def _slide(candles: list[dict[str, Any]], monkeypatch) -> dict[datetime, dict[str, Any]]:
    """Slide a full 500-bar window one closed bar at a time, tip always in slot 499."""
    real_extend = watcher_mod.extend_swings
    seen: dict[str, Any] = {}

    def recording_extend(swings, series, **kwargs):
        candidate = min(kwargs["as_of_index"], len(series) - 1) - kwargs["right"]
        seen["candidate_slot"] = candidate
        seen["candidate_candle"] = series[candidate]["time"]
        seen["early_return"] = bool(swings and swings[-1].bar_index >= candidate)
        return real_extend(swings, series, **kwargs)

    monkeypatch.setattr(watcher_mod, "extend_swings", recording_extend)
    watcher = _new_watcher()
    out: dict[datetime, dict[str, Any]] = {}
    for end in range(WINDOW - 1, len(candles)):
        series = candles[end - WINDOW + 1 : end + 1]
        tip = len(series) - 1
        allows = watcher._bos_break_allows_entry(SYMBOL, series, tip)
        swings = watcher._swing_state[SYMBOL]
        fresh = _fresh_swings(watcher, series)
        out[series[tip]["time"]] = {
            "tip_slot": tip,
            "window_len": len(series),
            "allows": allows,
            "break_possible": _bos_break_possible(series, tip, swings, direction_filter="LONG"),
            "levels": _bos_break_levels(swings, tip),
            "swings": _identity(swings),
            "fresh": _identity(fresh),
            "fresh_levels": _bos_break_levels(fresh, tip),
            **seen,
        }
    return out


def test_sliding_window_is_full_and_tip_stays_in_the_last_slot(candles, monkeypatch):
    steps = _slide(candles, monkeypatch)
    assert list(steps)[0] == FIRST_TIP and list(steps)[-1] == LAST_TIP
    assert len(steps) == 28
    assert {s["window_len"] for s in steps.values()} == {WINDOW}
    assert {s["tip_slot"] for s in steps.values()} == {WINDOW - 1}
    # extend_swings always looks at the same slot; the candle in it moves on each bar.
    assert {s["candidate_slot"] for s in steps.values()} == {WINDOW - 4}
    candidates = [s["candidate_candle"] for s in steps.values()]
    assert candidates == sorted(set(candidates))


def test_extend_swings_does_not_return_early_after_a_swing_lands_on_the_candidate_slot(
    candles, monkeypatch
):
    steps = _slide(candles, monkeypatch)
    # The 04:00 bar confirms the 169.80 swing high from the 01:00 candle, in the candidate slot.
    at_0400 = steps[_utc(11, 4)]
    assert ("HIGH", _utc(11, 1), 169.8) in [(t, ts, p) for t, ts, p, _i, _l in at_0400["swings"]]
    assert at_0400["swings"][-1][3] == WINDOW - 4
    # Before the fix every call from 05:00 through 22:00 returned early (18 bars).
    after = [tip for tip in steps if _utc(11, 5) <= tip <= LAST_TIP]
    assert len(after) == 18
    assert [tip for tip in after if steps[tip]["early_return"]] == []
    assert not any(step["early_return"] for step in steps.values())


def test_sliding_state_matches_a_fresh_detection_on_every_bar(candles, monkeypatch):
    steps = _slide(candles, monkeypatch)
    for tip, step in steps.items():
        assert step["swings"] == step["fresh"], tip
        assert step["levels"] == step["fresh_levels"], tip


def test_2200_bar_passes_the_break_check_on_the_1713_swing_high(candles, monkeypatch):
    steps = _slide(candles, monkeypatch)
    last = steps[LAST_TIP]
    trend, swing_high, _swing_low = last["levels"]
    assert trend == "BULLISH"
    assert swing_high == pytest.approx(171.13)
    assert ("HIGH", _utc(11, 16), 171.13) in [(t, ts, p) for t, ts, p, _i, _l in last["swings"]]
    assert candles[-1]["close"] == pytest.approx(171.76)
    assert last["break_possible"] is True
    assert last["allows"] is True
    # 20:00 breaks, 21:00 closes back under the level, 22:00 is a new break.
    assert steps[_utc(11, 20)]["allows"] is True
    assert steps[_utc(11, 21)]["allows"] is False
    assert steps[_utc(11, 21)]["break_possible"] is False


def test_sliding_window_gives_the_same_answers_as_a_growing_series(candles, monkeypatch):
    sliding = _slide(candles, monkeypatch)
    watcher = _new_watcher()
    for end in range(WINDOW - 1, len(candles)):
        series = candles[: end + 1]
        allows = watcher._bos_break_allows_entry(SYMBOL, series, end)
        tip = series[end]["time"]
        assert allows == sliding[tip]["allows"], tip
        levels = _bos_break_levels(watcher._swing_state[SYMBOL], end)
        assert levels == sliding[tip]["levels"], tip


def test_swings_on_window_leaves_a_growing_series_untouched(candles):
    watcher = _new_watcher()
    series = candles[:WINDOW]
    swings = _fresh_swings(watcher, series)
    longer = candles[: WINDOW + 5]
    assert swings_on_window(swings, longer, left=3) is swings


def test_swings_on_window_moves_slots_and_drops_candles_that_left(candles):
    watcher = _new_watcher()
    swings = _fresh_swings(watcher, candles[:WINDOW])
    slid = candles[5 : WINDOW + 5]
    moved = swings_on_window(swings, slid, left=3)
    times = {c["time"]: i for i, c in enumerate(slid)}
    assert moved, "expected swings to remain in the window"
    for swing in moved:
        assert times[swing.timestamp] == swing.bar_index
        assert swing.bar_index >= 3
        assert slid[swing.bar_index]["high" if swing.swing_type == "HIGH" else "low"] == swing.price
    kept = {s.timestamp for s in moved}
    for swing in swings:
        slot = times.get(swing.timestamp)
        assert (swing.timestamp in kept) == (slot is not None and slot >= 3)
