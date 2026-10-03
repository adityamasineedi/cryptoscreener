"""Tests for BOS combination research module.

Synthetic OHLCV is TEST-ONLY — never used in production paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.bos_combinations import COMBINATIONS, get_combination, list_combinations
from app.research.combination_backtest import (
    compare_combinations,
    resolve_intrabar_outcome,
    run_combination_backtest,
    run_oos_split_backtest,
    run_walk_forward,
)
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import (
    AMBIGUOUS_CONSERVATIVE,
    AMBIGUOUS_EXCLUDE,
    AMBIGUOUS_NEUTRAL,
    RESEARCH_ENGINE_VERSION,
    ResearchConfig,
    classify_asset_group,
    split_period_indices,
    walk_forward_windows,
)
from app.research.data_quality import verify_ohlcv
from app.research.metrics import expectancy_r, max_drawdown_r, profit_factor
from app.signals.config import SignalConfig


def _ts(i: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i)


def candle(i: int, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    return {"time": _ts(i), "open": o, "high": h, "low": l, "close": c, "volume": v}


def make_research_series(n: int = 120) -> list[dict]:
    """Long enough synthetic series with HH/HL structure for engines."""
    candles: list[dict] = []
    price = 100.0
    for i in range(n):
        # Gentle uptrend with swings
        wave = 2.0 * ((i % 10) / 10.0)
        o = price
        c = price + 0.4 + (0.2 if i % 7 == 0 else 0.0)
        h = max(o, c) + 0.8 + wave
        l = min(o, c) - 0.6
        vol = 3000.0 if i % 19 == 0 else 1000.0
        candles.append(candle(i, o, h, l, c, vol))
        price = c
    return candles


class TestCombinationsDefined:
    def test_all_combos_present(self):
        expected = {
            "COMBO_01": "BOS_ONLY",
            "COMBO_02": "TREND_BOS",
            "COMBO_02_LOCAL": "TREND_BOS_SETUP_TF_ONLY",
            "COMBO_03": "TREND_BOS_PULLBACK",
            "COMBO_04": "TREND_BOS_IMPULSE_PULLBACK",
            "COMBO_05": "TREND_BOS_IMPULSE_PULLBACK_RVOL",
            "COMBO_06": "TREND_BOS_PULLBACK_SD",
            "COMBO_07": "TREND_BOS_IMPULSE_PULLBACK_RVOL_SD",
            "COMBO_08": "TREND_BOS_IMPULSE_PULLBACK_RVOL_SD_RR",
        }
        assert set(COMBINATIONS) == set(expected)
        for cid, name in expected.items():
            assert COMBINATIONS[cid].name == name
            assert COMBINATIONS[cid].conditions
        assert COMBINATIONS["COMBO_02"].require_htf_alignment is True
        assert COMBINATIONS["COMBO_02_LOCAL"].require_htf_alignment is False

    def test_list_and_get(self):
        assert len(list_combinations()) == 9
        assert get_combination("COMBO_05") is not None
        assert get_combination("TREND_BOS") is not None
        assert get_combination("COMBO_02_LOCAL") is not None
        assert get_combination("NOPE") is None


class TestAmbiguity:
    def test_conservative_defaults_to_sl(self):
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

    def test_neutral_marks_ambiguous(self):
        outcome, _, amb = resolve_intrabar_outcome(
            direction="LONG",
            high=110,
            low=90,
            stop=95,
            targets=[105.0],
            handling=AMBIGUOUS_NEUTRAL,
        )
        assert outcome == "AMBIGUOUS_INTRABAR"
        assert amb is True

    def test_exclude_marks_ambiguous(self):
        outcome, px, amb = resolve_intrabar_outcome(
            direction="SHORT",
            high=110,
            low=90,
            stop=108,
            targets=[95.0],
            handling=AMBIGUOUS_EXCLUDE,
        )
        assert outcome == "AMBIGUOUS_INTRABAR"
        assert px is None
        assert amb is True


class TestMetrics:
    def test_expectancy_and_profit_factor(self):
        rs = [1.0, -0.5, 2.0, -1.0]
        assert expectancy_r(rs) == pytest.approx(1.5 / 4)
        assert profit_factor(rs) == pytest.approx(3.0 / 1.5)

    def test_drawdown(self):
        equity = [0, 1, 2, 0.5, 0.0]
        dd, curve = max_drawdown_r(equity)
        assert dd == pytest.approx(2.0)
        assert len(curve) == len(equity)


class TestDataQuality:
    def test_insufficient_data(self):
        report = verify_ohlcv([candle(0, 1, 1, 1, 1)], "15m")
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_ok_series(self):
        series = make_research_series(80)
        report = verify_ohlcv(series, "15m")
        assert report["status"] in ("OK", "DATA_OK")
        assert report["coverage_ratio"] >= 0.85


class TestNoLookahead:
    def test_as_of_index_stable(self):
        series = make_research_series(100)
        cfg = SignalConfig()
        rcfg = ResearchConfig(min_bars=40)
        combo = COMBINATIONS["COMBO_01"]
        a = evaluate_combination_at_bar(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=series,
            as_of_index=60,
            combination=combo,
            signal_config=cfg,
            research_config=rcfg,
            compute_sd=False,
        )
        b = evaluate_combination_at_bar(
            symbol="TESTUSDT",
            timeframe="15m",
            candles=series[:61],
            as_of_index=60,
            combination=combo,
            signal_config=cfg,
            research_config=rcfg,
            compute_sd=False,
        )
        assert a.get("status") == b.get("status")
        assert a.get("gates") == b.get("gates")


class TestBacktestCombos:
    @pytest.mark.parametrize("cid", list(COMBINATIONS.keys()))
    def test_combo_runs(self, cid: str):
        series = make_research_series(140)
        run = run_combination_backtest(
            "TESTUSDT",
            "15m",
            series,
            cid,
            signal_config=SignalConfig(),
            research_config=ResearchConfig(min_bars=40),
        )
        assert run["status"] in ("OK", "INSUFFICIENT_DATA")
        assert "sample_size" in run
        assert run.get("label") == "RESEARCH_COMPARISON" or run["status"] == "INSUFFICIENT_DATA"
        assert "best" not in str(run.get("disclaimer", "")).lower()
        res = run.get("result") or {}
        assert "sample_size" in res
        assert res.get("regime") == "REGIME_NOT_AVAILABLE"

    def test_reproducible_config_hash(self):
        a = ResearchConfig(min_rr=2.0, min_rvol=1.5)
        b = ResearchConfig(min_rr=2.0, min_rvol=1.5)
        assert a.configuration_hash() == b.configuration_hash()
        c = ResearchConfig(min_rr=2.5)
        assert a.configuration_hash() != c.configuration_hash()

    def test_mae_mfe_fields(self):
        series = make_research_series(140)
        run = run_combination_backtest(
            "TESTUSDT",
            "15m",
            series,
            "COMBO_01",
            research_config=ResearchConfig(min_bars=40),
        )
        for t in run.get("trades") or []:
            if t.get("outcome") in ("TP1", "TP2", "TP3", "SL"):
                assert "mae_r" in t
                assert "mfe_r" in t


class TestOosAndWalkForward:
    def test_split_indices(self):
        splits = split_period_indices(100)
        assert splits["TRAINING_PERIOD"][1] == 60
        assert splits["OUT_OF_SAMPLE_PERIOD"][0] == 80
        # OOS end is exclusive at n
        assert splits["OUT_OF_SAMPLE_PERIOD"][1] == 100

    def test_oos_separation(self):
        series = make_research_series(180)
        out = run_oos_split_backtest(
            "TESTUSDT",
            "15m",
            series,
            "COMBO_01",
            research_config=ResearchConfig(min_bars=40),
        )
        assert "OUT_OF_SAMPLE_PERIOD" in out["periods"]
        assert "evaluation-only" in out["note"].lower() or "OUT_OF_SAMPLE" in out["note"]

    def test_walk_forward_windows(self):
        windows = walk_forward_windows(300, train_bars=100, test_bars=50)
        assert windows
        for w in windows:
            assert w["train_end"] == w["test_start"]
            assert w["test_end"] > w["test_start"]

    def test_walk_forward_run(self):
        series = make_research_series(220)
        wf = run_walk_forward(
            "TESTUSDT",
            "15m",
            series,
            "COMBO_01",
            research_config=ResearchConfig(
                min_bars=40,
                walk_forward_train_bars=80,
                walk_forward_test_bars=40,
            ),
        )
        assert "windows" in wf
        assert wf.get("label") == "RESEARCH_COMPARISON"


class TestCompareAndAssetGroup:
    def test_compare_no_winner_language(self):
        series = make_research_series(120)
        cmp = compare_combinations(
            "TESTUSDT",
            "15m",
            series,
            ["COMBO_01", "COMBO_02"],
            research_config=ResearchConfig(min_bars=40),
        )
        assert cmp["label"] == "RESEARCH_COMPARISON"
        assert "rows" in cmp
        # Must not auto-select a ranked winner field
        assert "best_combination" not in cmp
        assert "winner_id" not in cmp
        assert "rank" not in cmp

    def test_asset_group_not_hardcoded_list(self):
        assert classify_asset_group("BTCUSDT", 1e12) == "BTC"
        assert classify_asset_group("ETHUSDT", 5e11) == "ETH"
        assert classify_asset_group("AAAUSDT", 12e9) == "large-cap"
        assert classify_asset_group("BBBUSDT", 2e9) == "mid-cap"
        assert classify_asset_group("CCCUSDT", 1e8) == "small-cap"


class TestLiveIsolation:
    def test_research_does_not_touch_engine_store(self):
        from app.services.engine_store import engine_store

        before = dict(engine_store.setup_signals)
        series = make_research_series(100)
        run_combination_backtest(
            "ISOUSDT",
            "15m",
            series,
            "COMBO_01",
            research_config=ResearchConfig(min_bars=40),
        )
        assert engine_store.setup_signals == before

    def test_engine_version_present(self):
        assert RESEARCH_ENGINE_VERSION.startswith("bos_research_")
