"""Tests for BOS strategy comparison research.

Synthetic OHLCV is TEST-ONLY — never used in production paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.engine import evaluate_strategy_at_bar
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_UNAVAILABLE,
    as_of_index_at_or_before,
    classify_htf_alignment,
)
from app.research.bos_strategy_comparison.metrics import (
    chronological_splits,
    compute_direction_metrics,
    cost_sensitivity,
)
from app.research.bos_strategy_comparison.runner import run_strategy_backtest
from app.research.bos_strategy_comparison.schemas import StrategyTrade
from app.research.bos_strategy_comparison.strategies import (
    STRATEGIES,
    get_strategy,
    list_strategies,
)
from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import AMBIGUOUS_CONSERVATIVE
from app.research.data_quality import verify_ohlcv
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings


def _ts(i: int, minutes: int = 15) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes * i)


def candle(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0, minutes: int = 15) -> dict:
    return {"time": _ts(i, minutes), "open": o, "high": h, "low": l, "close": c, "volume": v}


def make_uptrend_series(n: int = 160) -> list[dict]:
    candles: list[dict] = []
    price = 100.0
    for i in range(n):
        wave = 2.0 * ((i % 10) / 10.0)
        o = price
        c = price + 0.45 + (0.35 if i % 9 == 0 else 0.0)
        h = max(o, c) + 0.9 + wave
        l = min(o, c) - 0.55
        vol = 3500.0 if i % 17 == 0 else 1100.0
        candles.append(candle(i, o, h, l, c, vol))
        price = c
    return candles


def make_htf_from_setup(setup: list[dict], factor: int) -> list[dict]:
    """Downsample setup bars into coarser TF without fabricating prices."""
    out: list[dict] = []
    for i in range(0, len(setup), factor):
        chunk = setup[i : i + factor]
        if not chunk:
            continue
        out.append(
            {
                "time": chunk[0]["time"],
                "open": chunk[0]["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(c["volume"] for c in chunk),
            }
        )
    return out


class TestStrategyCatalog:
    def test_all_strategies_and_controls_present(self):
        expected = {
            "STRATEGY_1",
            "STRATEGY_2",
            "STRATEGY_3",
            "CONTROL_A",
            "CONTROL_B",
            "CONTROL_C",
            "CONTROL_D",
        }
        assert set(STRATEGIES) == expected
        assert len(list_strategies()) == 7
        assert get_strategy("STRATEGY_2") is not None
        assert get_strategy("NOPE") is None

    def test_strategies_are_independent_definitions(self):
        s1 = STRATEGIES["STRATEGY_1"]
        s2 = STRATEGIES["STRATEGY_2"]
        assert s1.require_impulse is False
        assert s2.require_impulse is True
        assert s1.require_htf_alignment is True
        assert STRATEGIES["CONTROL_A"].require_htf_alignment is False


class TestNoLookAhead:
    def test_as_of_index_never_uses_future(self):
        candles = make_uptrend_series(80)
        mid_ts = candles[40]["time"]
        idx = as_of_index_at_or_before(candles, mid_ts)
        assert idx == 40
        # Future candle times must not be selected
        assert as_of_index_at_or_before(candles[:40], mid_ts) == 39

    def test_swings_use_confirmed_bars_only(self):
        candles = make_uptrend_series(60)
        as_of = 30
        swings = detect_swings(candles, left=2, right=2, as_of_index=as_of)
        for s in swings:
            bar_i = getattr(s, "bar_index", None)
            if bar_i is None:
                bar_i = getattr(s, "index", 0)
            assert int(bar_i) + 2 <= as_of

    def test_evaluate_respects_as_of_index(self):
        candles = make_uptrend_series(120)
        h4 = make_htf_from_setup(candles, 16)
        h1 = make_htf_from_setup(candles, 4)
        setup = evaluate_strategy_at_bar(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=candles,
            as_of_index=50,
            strategy=STRATEGIES["CONTROL_A"],
            signal_config=SignalConfig(),
            research_config=StrategyResearchConfig(min_bars=20),
            candles_4h=h4,
            candles_1h=h1,
        )
        assert setup["as_of_index"] == 50
        assert "status" in setup


class TestHtfClassification:
    def test_aligned_long(self):
        assert (
            classify_htf_alignment(
                bos_direction="BULLISH_BOS",
                trend_4h="BULLISH",
                trend_1h="BULLISH",
            )
            == HTF_ALIGNED
        )

    def test_conflict_long(self):
        assert (
            classify_htf_alignment(
                bos_direction="BULLISH_BOS",
                trend_4h="BEARISH",
                trend_1h="BEARISH",
            )
            == HTF_CONFLICT
        )

    def test_neutral_unavailable(self):
        assert (
            classify_htf_alignment(
                bos_direction="BULLISH_BOS",
                trend_4h="NEUTRAL",
                trend_1h="BULLISH",
            )
            == HTF_NEUTRAL_UNAVAILABLE
        )

    def test_bearish_aligned_and_conflict(self):
        assert (
            classify_htf_alignment(
                bos_direction="BEARISH_BOS",
                trend_4h="BEARISH",
                trend_1h="BEARISH",
            )
            == HTF_ALIGNED
        )
        assert (
            classify_htf_alignment(
                bos_direction="BEARISH_BOS",
                trend_4h="BULLISH",
                trend_1h="BULLISH",
            )
            == HTF_CONFLICT
        )


class TestSameBarAndFees:
    def test_same_bar_sl_first_conservative(self):
        outcome, px, amb = resolve_intrabar_outcome(
            direction="LONG",
            high=110,
            low=90,
            stop=95,
            targets=[105.0],
            handling=AMBIGUOUS_CONSERVATIVE,
        )
        assert outcome == "SL"
        assert px == 95
        assert amb is True

    def test_cost_sensitivity_multipliers(self):
        trades = [
            StrategyTrade(
                strategy_id="CONTROL_A",
                symbol="BTCUSDT",
                direction="LONG",
                timeframe="15m",
                entry_index=10,
                entry_time="2024-01-01T00:00:00+00:00",
                entry_price=100.0,
                sl=95.0,
                tp1=110.0,
                tp2=None,
                tp3=None,
                exit_time="2024-01-01T01:00:00+00:00",
                exit_price=110.0,
                exit_reason="TP1",
                gross_R=2.0,
                risk_per_unit=5.0,
                entry_type="MARKET",
            )
        ]
        sens = cost_sensitivity(
            trades,
            taker_fee=0.0004,
            maker_fee=0.0002,
            slippage_rate=0.0002,
            multipliers=(1.0, 1.5, 2.0),
        )
        assert "1.0x" in sens and "2.0x" in sens
        # Higher cost should not improve net expectancy
        assert sens["2.0x"]["net_expectancy"] <= sens["1.0x"]["net_expectancy"]


class TestDataQuality:
    def test_insufficient_history(self):
        q = verify_ohlcv([candle(0, 1, 2, 0.5, 1.5)], "15m")
        assert q["status"] == "INSUFFICIENT_DATA"

    def test_duplicate_candles_flagged(self):
        c = make_uptrend_series(80)
        c.append(dict(c[10]))  # duplicate timestamp
        q = verify_ohlcv(c, "15m")
        assert q["duplicate_count"] >= 1 or q.get("data_quality") == "DUPLICATES"

    def test_no_fabricated_flag_in_research_quality(self):
        from app.research.bos_strategy_comparison.data_quality import series_quality_report

        q = series_quality_report(make_uptrend_series(80), "15m")
        assert q.get("fabricated") is False
        assert q.get("interpolated") is False
        assert q.get("synthetic_fill") is False


class TestLongShortAndSplits:
    def test_long_short_separation(self):
        trades = [
            StrategyTrade(
                strategy_id="CONTROL_A",
                symbol="BTCUSDT",
                direction="LONG",
                timeframe="15m",
                entry_index=1,
                entry_time="2024-01-01T00:00:00+00:00",
                entry_price=100,
                sl=90,
                tp1=120,
                tp2=None,
                tp3=None,
                exit_reason="TP1",
                gross_R=2.0,
            ),
            StrategyTrade(
                strategy_id="CONTROL_A",
                symbol="BTCUSDT",
                direction="SHORT",
                timeframe="15m",
                entry_index=2,
                entry_time="2024-01-02T00:00:00+00:00",
                entry_price=100,
                sl=110,
                tp1=80,
                tp2=None,
                tp3=None,
                exit_reason="SL",
                gross_R=-1.0,
            ),
        ]
        all_m = compute_direction_metrics(trades, direction="ALL")
        long_m = compute_direction_metrics(trades, direction="LONG")
        short_m = compute_direction_metrics(trades, direction="SHORT")
        assert all_m.sample_size == 2
        assert long_m.sample_size == 1
        assert short_m.sample_size == 1
        assert long_m.expectancy_R == pytest.approx(2.0)
        assert short_m.expectancy_R == pytest.approx(-1.0)

    def test_chronological_train_val_oos(self):
        trades = []
        for i in range(10):
            trades.append(
                StrategyTrade(
                    strategy_id="CONTROL_A",
                    symbol="BTCUSDT",
                    direction="LONG",
                    timeframe="15m",
                    entry_index=i,
                    entry_time=f"2024-01-{i+1:02d}T00:00:00+00:00",
                    entry_price=100,
                    sl=90,
                    tp1=120,
                    tp2=None,
                    tp3=None,
                    exit_reason="TP1",
                    gross_R=1.0,
                )
            )
        splits = chronological_splits(trades)
        assert len(splits["train"]) == 6
        assert len(splits["validation"]) == 2
        assert len(splits["oos"]) == 2
        # Chronological — first train entry before first oos
        assert splits["train"][0].entry_time < splits["oos"][0].entry_time


class TestStrategyCombinationsAndRunner:
    def test_control_a_runs_without_crash(self):
        candles = make_uptrend_series(140)
        h4 = make_htf_from_setup(candles, 16)
        h1 = make_htf_from_setup(candles, 4)
        out = run_strategy_backtest(
            "TESTUSDT",
            "15m",
            candles,
            "CONTROL_A",
            candles_4h=h4,
            candles_1h=h1,
            research_config=StrategyResearchConfig(min_bars=30),
        )
        assert out["status"] in ("OK", "INSUFFICIENT_DATA")
        assert "result" in out
        assert out["disclaimer"].startswith("Research only")
        assert "BEST" not in out.get("label", "").upper()

    def test_strategy_gates_differ(self):
        candles = make_uptrend_series(140)
        h4 = make_htf_from_setup(candles, 16)
        h1 = make_htf_from_setup(candles, 4)
        a = run_strategy_backtest(
            "TESTUSDT",
            "15m",
            candles,
            "CONTROL_A",
            candles_4h=h4,
            candles_1h=h1,
            research_config=StrategyResearchConfig(min_bars=30),
        )
        s2 = run_strategy_backtest(
            "TESTUSDT",
            "15m",
            candles,
            "STRATEGY_2",
            candles_4h=h4,
            candles_1h=h1,
            research_config=StrategyResearchConfig(min_bars=30),
        )
        # Stricter strategy cannot produce more setups than BOS-only control
        assert (s2.get("setups_processed") or 0) <= (a.get("setups_processed") or 0)

    def test_htf_conflict_not_silently_dropped_from_trade_fields(self):
        # Even when strategy requires alignment, classification helper separates conflicts
        assert HTF_CONFLICT != HTF_ALIGNED
        trade = StrategyTrade(
            strategy_id="CONTROL_A",
            symbol="BTCUSDT",
            direction="LONG",
            timeframe="15m",
            entry_index=1,
            entry_time="2024-01-01T00:00:00+00:00",
            entry_price=100,
            sl=90,
            tp1=120,
            tp2=None,
            tp3=None,
            exit_reason="TP1",
            gross_R=1.0,
            htf_alignment=HTF_CONFLICT,
        )
        assert trade.htf_alignment == HTF_CONFLICT


class TestMetrics:
    def test_win_rate_and_expectancy(self):
        trades = [
            StrategyTrade(
                strategy_id="CONTROL_A",
                symbol="X",
                direction="LONG",
                timeframe="15m",
                entry_index=0,
                entry_time="2024-01-01T00:00:00+00:00",
                entry_price=1,
                sl=0.5,
                tp1=2,
                tp2=None,
                tp3=None,
                exit_reason="TP1",
                gross_R=2.0,
            ),
            StrategyTrade(
                strategy_id="CONTROL_A",
                symbol="X",
                direction="LONG",
                timeframe="15m",
                entry_index=1,
                entry_time="2024-01-02T00:00:00+00:00",
                entry_price=1,
                sl=0.5,
                tp1=2,
                tp2=None,
                tp3=None,
                exit_reason="SL",
                gross_R=-1.0,
            ),
        ]
        m = compute_direction_metrics(trades)
        assert m.sample_size == 2
        assert m.win_rate == pytest.approx(0.5)
        assert m.expectancy_R == pytest.approx(0.5)
        assert m.profit_factor == pytest.approx(2.0)
