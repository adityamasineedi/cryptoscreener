"""BTC daily regime: confirmed swings, legacy trend, closed daily bars."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.research.btc_daily_regime import MIN_DAILY_BARS, daily_trend
from app.services import ohlcv_store as ohlcv_store_module
from app.services.passed_paper_watcher import PassedPaperWatcher
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings
from app.signals.trend_engine import infer_trend

LEG = 5
START = datetime(2025, 1, 1, tzinfo=timezone.utc)

# Turning points of a zigzag, LEG daily bars apart.
BULL = [100, 120, 110, 130, 120, 140, 130, 150, 140, 160, 150]
BEAR = [200, 180, 190, 170, 180, 160, 170, 150, 160, 140, 150]
EXPANDING = [100, 110, 95, 115, 90, 120, 85, 125, 80, 130, 75]
# BULL with the last leg driven to a lower low at index 50, then a bounce.
BULL_THEN_LOWER_LOW = BULL[:-1] + [100, 112]
LAST_HIGHER_LOW_INDEX = 40
LOWER_LOW_INDEX = 50


def _bar(i: int, close: float) -> dict:
    return {
        "time": START + timedelta(days=i),
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1.0,
    }


def _zigzag(points: list[float]) -> list[dict]:
    closes: list[float] = [float(points[0])]
    for a, b in zip(points, points[1:]):
        closes.extend(a + (b - a) * step / LEG for step in range(1, LEG + 1))
    return [_bar(i, c) for i, c in enumerate(closes)]


def _flat(n: int) -> list[dict]:
    return [_bar(i, 100.0) for i in range(n)]


def _structural_trend(candles: list[dict]) -> str:
    """Swing trend with no bar-count floor."""
    swings = detect_swings(candles, left=3, right=3, as_of_index=len(candles) - 1)
    return str(infer_trend(swings)["trend"])


def _old_rule(candles: list[dict], signal_config: SignalConfig) -> str:
    """The body ``_market_direction`` had before the extraction."""
    if len(candles) < 40:
        return "NEUTRAL"
    cfg = signal_config.swing_for("1d")
    swings = detect_swings(
        candles,
        left=cfg.swing_left_bars,
        right=cfg.swing_right_bars,
        symbol="BTCUSDT",
        timeframe="1d",
        as_of_index=len(candles) - 1,
    )
    trend = str(infer_trend(list(swings)).get("trend") or "")
    if trend in {"BULLISH", "BEARISH"}:
        return trend
    return "NEUTRAL"


def _watcher(monkeypatch: pytest.MonkeyPatch, candles: list[dict]) -> PassedPaperWatcher:
    calls: list[tuple] = []

    def fake(symbol: str, timeframe: str, *, include_open: bool = False) -> list[dict]:
        calls.append((symbol, timeframe, include_open))
        return list(candles)

    monkeypatch.setattr(ohlcv_store_module.ohlcv_store, "get_candles_for_engine", fake)
    watcher = PassedPaperWatcher(enabled=False, paper_engine=object())
    watcher._store_calls = calls  # type: ignore[attr-defined]
    return watcher


def test_min_daily_bars_is_forty() -> None:
    assert MIN_DAILY_BARS == 40


def test_bullish() -> None:
    candles = _zigzag(BULL)
    out = daily_trend(candles, SignalConfig())
    assert out == {
        "trend": "BULLISH",
        "as_of_bar": (START + timedelta(days=len(candles) - 1)).isoformat(),
        "bars": len(candles),
    }


def test_bearish() -> None:
    assert daily_trend(_zigzag(BEAR), SignalConfig())["trend"] == "BEARISH"


def test_neutral_when_highs_rise_and_lows_fall() -> None:
    candles = _zigzag(EXPANDING)
    assert len(candles) >= MIN_DAILY_BARS
    assert _structural_trend(candles) == "NEUTRAL"
    assert daily_trend(candles, SignalConfig())["trend"] == "NEUTRAL"


def test_neutral_when_no_swings_form() -> None:
    candles = _flat(60)
    assert _structural_trend(candles) == "INSUFFICIENT_DATA"
    out = daily_trend(candles, SignalConfig())
    assert out["trend"] == "NEUTRAL"
    assert out["bars"] == 60


def test_no_bars() -> None:
    assert daily_trend([], SignalConfig()) == {"trend": "NEUTRAL", "as_of_bar": "", "bars": 0}


def test_thirty_nine_bars_neutral_forty_bars_trend() -> None:
    candles = _zigzag(BULL)
    short, enough = candles[:39], candles[:40]
    # The structure is already bullish at 39 bars; only the floor holds it back.
    assert _structural_trend(short) == "BULLISH"
    out_short = daily_trend(short, SignalConfig())
    assert out_short["trend"] == "NEUTRAL"
    assert out_short["bars"] == 39
    assert out_short["as_of_bar"] == (START + timedelta(days=38)).isoformat()
    out_enough = daily_trend(enough, SignalConfig())
    assert out_enough["trend"] == "BULLISH"
    assert out_enough["bars"] == 40
    assert out_enough["as_of_bar"] == (START + timedelta(days=39)).isoformat()


def test_short_bearish_history_is_neutral() -> None:
    candles = _zigzag(BEAR)[:39]
    assert _structural_trend(candles) == "BEARISH"
    assert daily_trend(candles, SignalConfig())["trend"] == "NEUTRAL"


def test_unconfirmed_trailing_low_does_not_flip_trend() -> None:
    candles = _zigzag(BULL_THEN_LOWER_LOW)
    assert candles[LOWER_LOW_INDEX]["low"] < candles[LAST_HIGHER_LOW_INDEX]["low"]

    # Two bars after the low: one short of the three needed to confirm it.
    pending = candles[: LOWER_LOW_INDEX + 3]
    assert daily_trend(pending, SignalConfig())["trend"] == "BULLISH"

    confirmed = candles[: LOWER_LOW_INDEX + 4]
    assert daily_trend(confirmed, SignalConfig())["trend"] == "NEUTRAL"


def test_trend_mode_setting_does_not_change_the_rule() -> None:
    # Higher high then lower low: legacy says NEUTRAL, choch_persistent would
    # keep BULLISH. The regime uses legacy whatever the config says.
    candles = _zigzag(BULL_THEN_LOWER_LOW)[: LOWER_LOW_INDEX + 4]
    swings = detect_swings(candles, left=3, right=3, as_of_index=len(candles) - 1)
    assert infer_trend(swings, mode="choch_persistent")["trend"] == "BULLISH"
    cfg = SignalConfig(trend_mode="choch_persistent")
    assert daily_trend(candles, cfg)["trend"] == "NEUTRAL"


def test_open_time_key_and_epoch_ms_are_read_for_as_of_bar() -> None:
    candles = []
    for c in _zigzag(BULL):
        row = dict(c)
        row["open_time"] = int(row.pop("time").timestamp() * 1000)
        candles.append(row)
    out = daily_trend(candles, SignalConfig())
    assert out["trend"] == "BULLISH"
    assert out["as_of_bar"] == (START + timedelta(days=len(candles) - 1)).isoformat()


def test_matches_old_rule_on_random_walks() -> None:
    rng = random.Random(7)
    cfg = SignalConfig()
    seen: set[str] = set()
    for _ in range(300):
        n = rng.randint(30, 140)
        price = 100.0
        candles = []
        drift = rng.choice([-0.6, 0.0, 0.6])
        for i in range(n):
            price = max(1.0, price + drift + rng.uniform(-3.0, 3.0))
            candles.append(_bar(i, price))
        got = daily_trend(candles, cfg)["trend"]
        assert got == _old_rule(candles, cfg)
        seen.add(got)
    assert seen == {"BULLISH", "BEARISH", "NEUTRAL"}


@pytest.mark.parametrize(
    ("points", "expected"),
    [(BULL, "BULLISH"), (BEAR, "BEARISH"), (EXPANDING, "NEUTRAL")],
)
def test_market_direction_reads_closed_btc_daily(
    monkeypatch: pytest.MonkeyPatch, points: list[float], expected: str
) -> None:
    candles = _zigzag(points)
    watcher = _watcher(monkeypatch, candles)
    assert watcher._market_direction() == expected
    assert watcher._store_calls == [("BTCUSDT", "1d", False)]
    assert watcher._market_direction() == _old_rule(candles, watcher.signal_config)


def test_market_direction_short_history_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = _zigzag(BULL)
    assert _watcher(monkeypatch, candles[:39])._market_direction() == "NEUTRAL"
    assert _watcher(monkeypatch, candles[:40])._market_direction() == "BULLISH"
    assert _watcher(monkeypatch, [])._market_direction() == "NEUTRAL"


def test_market_direction_store_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ohlcv_store_module.ohlcv_store,
        "get_candles_for_engine",
        lambda *a, **k: None,
    )
    watcher = PassedPaperWatcher(enabled=False, paper_engine=object())
    assert watcher._market_direction() == "NEUTRAL"


def test_market_direction_store_error_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(ohlcv_store_module.ohlcv_store, "get_candles_for_engine", boom)
    watcher = PassedPaperWatcher(enabled=False, paper_engine=object())
    assert watcher._market_direction() == "NEUTRAL"
    assert watcher._market_regime() == {"trend": "NEUTRAL", "as_of_bar": "", "bars": 0}


def test_market_direction_bad_candles_are_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = _zigzag(BULL)
    candles[20] = {"time": candles[20]["time"], "high": "not-a-number"}
    assert _watcher(monkeypatch, candles)._market_direction() == "NEUTRAL"


def test_market_regime_carries_the_as_of_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = _zigzag(BEAR)
    watcher = _watcher(monkeypatch, candles)
    assert watcher._market_regime() == {
        "trend": "BEARISH",
        "as_of_bar": candles[-1]["time"].isoformat(),
        "bars": len(candles),
    }
