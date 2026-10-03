"""Research-only trade plan forensics tests — no production logic changes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.bos_strategy_comparison.htf import as_of_index_at_or_before
from app.research.trade_plan_forensics.aggregates import summarize, winner_loser_compare
from app.research.trade_plan_forensics.hypotheses import evaluate_hypotheses, issue_table
from app.research.trade_plan_forensics.ingest import (
    normalize_backtest_trade,
    normalize_research_json_row,
)
from app.research.trade_plan_forensics.reconstruct import (
    classify_entry_vs_bos,
    classify_htf_state,
    classify_regime,
    classify_timing,
    entry_candle_metrics,
    path_excursions,
)
from app.research.trade_plan_forensics.schemas import sample_size_status
from app.research.trade_plan_forensics.thresholds import SAFETY_NO_PROD_CHANGE


def _candles(n: int = 80, start: datetime | None = None) -> list[dict]:
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    out = []
    px = 100.0
    for i in range(n):
        o = px
        c = px + (1.0 if i % 7 else -0.5)
        h = max(o, c) + 0.8
        l = min(o, c) - 0.8
        out.append(
            {
                "time": start + timedelta(minutes=15 * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 10 + i,
            }
        )
        px = c
    return out


def test_sample_size_labels():
    assert sample_size_status(10) == "INSUFFICIENT_SAMPLE"
    assert sample_size_status(50) == "LIMITED_SAMPLE"
    assert sample_size_status(120) == "MORE_RELIABLE_DESCRIPTIVE_SAMPLE"


def test_timing_classification_thresholds_documented():
    assert classify_timing(0) == "EARLY"
    assert classify_timing(2) == "TIMELY"
    assert classify_timing(5) == "LATE"
    assert classify_timing(12) == "VERY_LATE"
    assert classify_timing(None) == "UNAVAILABLE"


def test_entry_vs_bos_classes():
    assert (
        classify_entry_vs_bos(bars_since_bos=0, bos_distance_atr=0.1, bos_confirmed=True)
        == "ENTRY_AT_BOS"
    )
    assert (
        classify_entry_vs_bos(bars_since_bos=3, bos_distance_atr=0.5, bos_confirmed=True)
        == "ENTRY_AFTER_BOS"
    )
    assert (
        classify_entry_vs_bos(bars_since_bos=2, bos_distance_atr=1.5, bos_confirmed=True)
        == "ENTRY_TOO_FAR_AFTER_BOS"
    )
    assert (
        classify_entry_vs_bos(bars_since_bos=0, bos_distance_atr=0.1, bos_confirmed=False)
        == "ENTRY_BEFORE_CONFIRMATION"
    )


def test_htf_conflict_not_collapsed_to_neutral():
    assert (
        classify_htf_state(
            direction="LONG",
            trends={"4h": "BULLISH", "1h": "BEARISH"},
        )
        == "HTF_CONFLICT"
    )
    assert (
        classify_htf_state(
            direction="LONG",
            trends={"4h": "BULLISH", "1h": "BULLISH"},
        )
        == "HTF_ALIGNED"
    )
    assert (
        classify_htf_state(
            direction="LONG",
            trends={"4h": "NEUTRAL", "1h": "NEUTRAL"},
        )
        == "HTF_NEUTRAL"
    )


def test_regime_stores_measurements():
    reg = classify_regime(
        trend="BULLISH",
        trend_strength=0.7,
        atr_percent=3.0,
        recent_range_atr=2.0,
        direction_changes=1,
        bos_freq=0.1,
        dist_to_range_edge_atr=0.2,
    )
    assert "TRENDING_UP" in reg["labels"]
    assert "VOLATILITY_EXPANSION" in reg["labels"]
    assert "measurements" in reg
    assert reg["measurements"]["atr_percent"] == 3.0


def test_no_lookahead_as_of_index():
    candles = _candles(20)
    # Pick a mid timestamp — index must be <= that candle
    ts = candles[10]["time"]
    idx = as_of_index_at_or_before(candles, ts)
    assert idx == 10
    # Future timestamp relative to series end still clamps
    future = candles[-1]["time"] + timedelta(hours=5)
    assert as_of_index_at_or_before(candles, future) == len(candles) - 1


def test_entry_candle_metrics_and_path():
    candles = _candles(60)
    ctx = entry_candle_metrics(candles, 40)
    assert ctx["status"] == "OK"
    assert ctx["atr"] is not None
    path = path_excursions(
        direction="LONG",
        entry=float(candles[40]["close"]),
        stop=float(candles[40]["close"]) - 2.0,
        tp=float(candles[40]["close"]) + 4.0,
        candles=candles,
        entry_idx=40,
        exit_idx=50,
    )
    assert path["status"] == "OK"
    assert path["mae"] is not None
    assert path["mfe"] is not None


def test_ingest_normalization_missing_fields():
    t = normalize_backtest_trade(
        {
            "symbol": "ETHUSDT",
            "timeframe": "15m",
            "signal_time": "2026-01-02T00:00:00+00:00",
            "direction": "LONG",
            "entry_price": 1.0,
            "stop_price": 0.9,
            "tp1": 1.2,
            "outcome": "SL",
            "r_multiple": -1.0,
            "entry_index": 10,
        }
    )
    assert t.balance == "UNAVAILABLE"
    assert t.strategy == "COMBO_02"
    j = normalize_research_json_row(
        {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "direction": "LONG",
            "entry_time": "2026-01-01T00:00:00+00:00",
            "R": 1.5,
            "result": "TP1",
        }
    )
    assert j.fees is None
    assert j.take_profit is None  # not inferred


def test_insufficient_sample_hypothesis_status():
    # Tiny synthetic forensic records
    recs = []
    for i in range(5):
        recs.append(
            {
                "status": "OK",
                "trade": {"R": 1.0 if i % 2 == 0 else -1.0, "direction": "LONG", "timeframe": "15m"},
                "htf_state": "HTF_CONFLICT" if i < 2 else "HTF_ALIGNED",
                "entry_timing": {"class": "VERY_LATE" if i < 2 else "TIMELY"},
                "regime": {"primary": "CHOP", "labels": ["CHOP"]},
                "local_structure": {
                    "retest": {"retest": False},
                    "entry_vs_bos_class": "ENTRY_AFTER_BOS",
                    "bars_since_bos": 10,
                    "bos_distance_atr": 0.4,
                },
                "liquidity": {"state": "WEAK_SWEEP"},
                "supply_demand": {"state": "FAR_FROM_ZONE"},
                "fvg": {"state": "UNAVAILABLE"},
                "stop_forensics": {"sl_distance_atr": 0.4, "path": {"mae_r": 1.0}},
                "tp_forensics": {"mfe_r": 0.2, "reached_0_5R": False},
                "entry_context": {"atr_percent": 1.0, "rvol": 1.0},
                "session": {"bin": "08-12"},
            }
        )
    hyps = evaluate_hypotheses(recs)
    assert all(h["status"] == "INSUFFICIENT_DATA" for h in hyps)
    table = issue_table(hyps)
    assert len(table) == 14
    assert SAFETY_NO_PROD_CHANGE


def test_winner_loser_compare_and_summarize():
    recs = [
        {
            "status": "OK",
            "trade": {"R": 2.0, "direction": "LONG", "bars_held": 3},
            "htf_state": "HTF_ALIGNED",
            "entry_timing": {"class": "TIMELY"},
            "regime": {"labels": ["TRENDING_UP"], "primary": "TRENDING_UP"},
            "entry_context": {"atr_percent": 1.2, "rvol": 1.5},
            "local_structure": {"bos_distance_atr": 0.3, "bars_since_bos": 1, "pullback": {}},
            "stop_forensics": {"path": {"mae_r": 0.2}, "sl_distance_atr": 1.0},
            "tp_forensics": {"mfe_r": 2.0},
        },
        {
            "status": "OK",
            "trade": {"R": -1.0, "direction": "LONG", "bars_held": 2},
            "htf_state": "HTF_CONFLICT",
            "entry_timing": {"class": "VERY_LATE"},
            "regime": {"labels": ["CHOP"], "primary": "CHOP"},
            "entry_context": {"atr_percent": 2.5, "rvol": 0.7},
            "local_structure": {"bos_distance_atr": 1.2, "bars_since_bos": 9, "pullback": {}},
            "stop_forensics": {"path": {"mae_r": 1.0}, "sl_distance_atr": 0.5},
            "tp_forensics": {"mfe_r": 0.1},
        },
    ]
    s = summarize(recs)
    assert s["n"] == 2
    assert s["sample_status"] == "INSUFFICIENT_SAMPLE"
    wl = winner_loser_compare(recs)
    assert wl["winners_n"] == 1
    assert wl["losers_n"] == 1


def test_chronological_reconstruction_index_order():
    candles = _candles(30)
    times = [c["time"] for c in candles]
    assert times == sorted(times)
    for i in range(len(candles)):
        idx = as_of_index_at_or_before(candles, candles[i]["time"])
        assert idx == i
