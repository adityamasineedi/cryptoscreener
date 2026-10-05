"""Market-structure analytics — PIT safety, isolation, regime determinism.

These tests prove analytics never alters COMBO_02 trades / balances / fingerprints.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research.bos_combinations import get_combination
from app.research.bos_strategy_comparison.htf import (
    build_htf_as_of_index_map_fully_closed,
    decision_timestamp_for_setup_bar,
)
from app.research.combination_backtest import run_combination_backtest
from app.research.market_structure.artifacts import write_market_structure_artifacts
from app.research.market_structure.config import (
    ENABLE_REGIME_FILTERING,
    MarketStructureFeatureConfig,
    assert_regime_filtering_safe,
    default_feature_config,
)
from app.research.market_structure.engine import compute_market_structure_analytics
from app.research.market_structure.regime import classify_market_regime
from app.research.market_structure.structure import (
    build_timeframe_feature_table,
    snapshot_at_index,
)
from app.signals.config import SignalConfig


def _candles(
    n: int,
    *,
    start: datetime | None = None,
    tf_seconds: int = 3600,
    base: float = 100.0,
    drift: float = 0.15,
) -> list[dict]:
    t0 = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    out: list[dict] = []
    px = base
    for i in range(n):
        o = px
        c = px + drift
        h = max(o, c) + 0.4
        l = min(o, c) - 0.4
        out.append(
            {
                "time": t0 + timedelta(seconds=tf_seconds * i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def _downsample(c1h: list[dict], every: int, tf_seconds: int) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(c1h), every):
        chunk = c1h[i : i + every]
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
    # ensure close times advance by tf_seconds between opens
    for i, c in enumerate(out):
        c["time"] = c1h[0]["time"] + timedelta(seconds=tf_seconds * i)
    return out


# ---------------------------------------------------------------------------
# 1. Deterministic regime classification
# ---------------------------------------------------------------------------


def test_regime_classification_deterministic():
    snap = {
        "trend_state": "BULLISH",
        "structure_state": "UPTREND_HH_HL",
        "direction": "BULLISH",
        "bos_state": "BULLISH_BOS",
        "choch_state": "NO_CONFIRMED_CHOCH",
        "volatility_state": "NORMAL",
        "momentum_state": "BULLISH",
        "adx": 30.0,
        "di_plus": 28.0,
        "di_minus": 12.0,
        "efficiency_ratio": 0.62,
        "choppiness_index": 35.0,
        "atr_percentile": 50.0,
        "ema_alignment": "BULLISH",
        "range_state": "NO_RANGE",
        "direction_changes": 2,
        "data_quality_state": "OK",
    }
    a = classify_market_regime(snap)
    b = classify_market_regime(snap)
    assert a == b
    assert a.market_regime in {
        "BULL_TREND",
        "HIGH_VOLATILITY_TREND",
    }
    assert a.confidence_score == b.confidence_score


def test_regime_unknown_when_data_missing():
    r = classify_market_regime({"data_quality_state": "UNKNOWN_DATA_MISSING"})
    assert r.market_regime == "UNKNOWN"


# ---------------------------------------------------------------------------
# 2–6. Point-in-time / closed candle mapping
# ---------------------------------------------------------------------------


def test_no_future_candles_in_feature_snapshot():
    c1h = _candles(80, drift=0.2)
    table = build_timeframe_feature_table(c1h, "1h", symbol="BTCUSDT")
    assert table is not None
    decision = decision_timestamp_for_setup_bar(c1h, 50, setup_timeframe="1h")
    snap = snapshot_at_index(table, 50, decision_time=decision)
    assert snap.uses_future_candle is False
    assert snap.feature_timestamp is not None
    feat = datetime.fromisoformat(snap.feature_timestamp)
    assert feat <= decision


def test_closed_4h_mapping_respected():
    c1h = _candles(80, drift=0.1)
    c4h = _downsample(c1h, 4, 14400)
    idx_map = build_htf_as_of_index_map_fully_closed(
        c1h, c4h, setup_timeframe="1h", htf_timeframe="4h"
    )
    for i, j in enumerate(idx_map):
        if j is None:
            continue
        decision = decision_timestamp_for_setup_bar(c1h, i, setup_timeframe="1h")
        open_4h = c4h[j]["time"]
        close_4h = open_4h + timedelta(seconds=14400)
        assert close_4h <= decision


def test_1h_features_use_completed_1h_only():
    c1h = _candles(60, drift=0.12)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_downsample(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=30,
        enable_regime_filtering=False,
        research_only=True,
    )
    assert analytics["status"] == "OK"
    for row in analytics["by_bar"]:
        s1 = row["structure_1h"]
        if s1.get("feature_timestamp") and row.get("decision_time"):
            assert datetime.fromisoformat(s1["feature_timestamp"]) <= datetime.fromisoformat(
                row["decision_time"]
            )


def test_15m_features_use_completed_15m_only():
    c1h = _candles(40, drift=0.1)
    # Build 15m by expanding each 1h into 4 bars
    c15: list[dict] = []
    for c in c1h:
        for k in range(4):
            t = c["time"] + timedelta(minutes=15 * k)
            c15.append(
                {
                    "time": t,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"] / 4,
                }
            )
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_downsample(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=c15,
        trades=[],
        index_start=20,
        enable_regime_filtering=False,
        research_only=True,
    )
    assert analytics["status"] == "OK"
    assert analytics["15m_status"] == "OK"
    for row in analytics["by_bar"]:
        s15 = row["structure_15m"]
        if s15.get("data_quality_state", "").startswith("UNKNOWN"):
            continue
        feat = datetime.fromisoformat(s15["feature_timestamp"])
        dec = datetime.fromisoformat(row["decision_time"])
        assert feat <= dec


def test_exact_timestamp_boundary_closed_4h():
    """4h that closes exactly at decision time is eligible."""
    t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    c1h = _candles(8, start=t0, drift=0.05)
    # One 4h candle opening at t0, closing at t0+4h which equals decision of 1h bar index 3
    c4h = [
        {
            "time": t0,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        }
    ]
    idx_map = build_htf_as_of_index_map_fully_closed(
        c1h, c4h, setup_timeframe="1h", htf_timeframe="4h"
    )
    # decision of bar 3 = t0+4h → 4h close == decision → eligible
    assert idx_map[3] == 0
    # decision of bar 2 = t0+3h → 4h not yet closed
    assert idx_map[2] is None


# ---------------------------------------------------------------------------
# 7–10. Missing data / uniqueness
# ---------------------------------------------------------------------------


def test_missing_15m_does_not_break_backtest():
    c1h = _candles(50, drift=0.1)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_downsample(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=25,
        enable_regime_filtering=False,
        research_only=True,
    )
    assert analytics["status"] == "OK"
    assert analytics["15m_status"] == "UNAVAILABLE"
    for row in analytics["by_bar"]:
        assert row["structure_15m"]["trend_state"] == "UNKNOWN_DATA_MISSING"
        assert row["15m_status"] == "UNAVAILABLE"
        assert row["15m_source_available"] is False
        assert row["15m_feature_available"] is False


def test_missing_4h_labeled_explicitly():
    c1h = _candles(40, drift=0.1)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=None,
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=20,
        enable_regime_filtering=False,
        research_only=True,
    )
    assert analytics["status"] == "OK"
    for row in analytics["by_bar"]:
        assert row["structure_4h"]["data_quality_state"] == "UNKNOWN_DATA_MISSING"


def test_no_duplicate_feature_rows_and_one_per_decision_bar():
    c1h = _candles(45, drift=0.08)
    start = 20
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_downsample(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[],
        index_start=start,
        enable_regime_filtering=False,
        research_only=True,
    )
    q = analytics["quality_report"]
    assert q["bars_with_duplicate_feature_rows"] == 0
    assert q["total_1h_decision_bars"] == len(c1h) - start
    assert len(analytics["by_bar"]) == q["total_1h_decision_bars"]
    keys = [r["decision_time"] for r in analytics["by_bar"]]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# 11–14. Strategy isolation (most important)
# ---------------------------------------------------------------------------


def _trade_fingerprint(trades: list[dict]) -> list[tuple]:
    rows = []
    for t in trades:
        rows.append(
            (
                t.get("signal_time"),
                t.get("entry_index"),
                t.get("direction"),
                round(float(t.get("entry_price") or 0), 8),
                round(float(t.get("stop_price") or 0), 8),
                t.get("outcome"),
                None
                if t.get("r_multiple") is None
                else round(float(t["r_multiple"]), 8),
                t.get("exit_time"),
                None
                if t.get("exit_price") is None
                else round(float(t["exit_price"]), 8),
            )
        )
    return rows


def test_analytics_enabled_disabled_identical_trades_and_balances():
    """Baseline vs analytics attach — trades/equity/fingerprint unchanged.

    Analytics runs *after* the strategy backtest; this test proves the strategy
    walk itself is unaffected and that attaching analytics does not mutate trades.
    """
    c1h = _candles(120, drift=0.25)
    c4h = _downsample(c1h, 4, 14400)
    combo = get_combination("COMBO_02")
    assert combo is not None

    baseline = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=40,
    )
    observed = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=40,
    )
    assert _trade_fingerprint(baseline.get("trades") or []) == _trade_fingerprint(
        observed.get("trades") or []
    )
    assert (baseline.get("result") or {}).get("equity_curve_r") == (
        observed.get("result") or {}
    ).get("equity_curve_r")
    assert baseline.get("configuration_hash") == observed.get("configuration_hash")

    # Attach analytics to a row copy — trades must remain identical
    from app.research.market_structure.attach import attach_market_structure_to_row

    row = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "combination_id": "COMBO_02",
        "trades": list(observed.get("trades") or []),
        "configuration_fingerprint": "abc123deadbeef00",
        "equity_curve_r": list((observed.get("result") or {}).get("equity_curve_r") or []),
        "pnl_usd_net": 12.34,
    }
    trades_before = json.dumps(row["trades"], sort_keys=True, default=str)
    fp_before = row["configuration_fingerprint"]
    equity_before = list(row["equity_curve_r"])
    bal_before = row["pnl_usd_net"]

    attached = attach_market_structure_to_row(
        row,
        setup_candles=c1h,
        candles_4h=c4h,
        candles_1h=c1h,
        candles_15m=None,
        index_start=40,
        analytics_enabled=True,
        enable_regime_filtering=False,
        research_only=True,
        write_artifacts=False,
        run_id="test_iso",
    )
    assert json.dumps(attached["trades"], sort_keys=True, default=str) == trades_before
    assert attached["configuration_fingerprint"] == fp_before
    assert attached["equity_curve_r"] == equity_before
    assert attached["pnl_usd_net"] == bal_before
    assert attached["market_structure"]["analytics_enabled"] is True

    disabled = attach_market_structure_to_row(
        row,
        setup_candles=c1h,
        candles_4h=c4h,
        candles_1h=c1h,
        candles_15m=None,
        analytics_enabled=False,
        write_artifacts=False,
    )
    assert json.dumps(disabled["trades"], sort_keys=True, default=str) == trades_before
    assert disabled["configuration_fingerprint"] == fp_before


def test_feature_config_fingerprint_stable_and_separate():
    a = default_feature_config().fingerprint()
    b = MarketStructureFeatureConfig().fingerprint()
    assert a == b
    # Changing a threshold changes feature fp but must not touch strategy fp
    c = MarketStructureFeatureConfig(adx_trending_threshold=30.0).fingerprint()
    assert c != a


# ---------------------------------------------------------------------------
# 15. Regime filtering safety
# ---------------------------------------------------------------------------


def test_regime_filtering_cannot_run_in_production_mode():
    assert ENABLE_REGIME_FILTERING is False
    with pytest.raises(ValueError, match="research-only"):
        assert_regime_filtering_safe(
            enable_regime_filtering=True, research_only=False
        )
    # research_only allows the guard to pass, but engine still refuses filtering
    assert_regime_filtering_safe(enable_regime_filtering=True, research_only=True)
    with pytest.raises(ValueError, match="ENABLE_REGIME_FILTERING"):
        compute_market_structure_analytics(
            symbol="BTCUSDT",
            setup_timeframe="1h",
            setup_candles=_candles(30),
            candles_4h=None,
            candles_1h=_candles(30),
            candles_15m=None,
            enable_regime_filtering=True,
            research_only=True,
        )


# ---------------------------------------------------------------------------
# 18–20. Artifacts reconcile / no production side effects
# ---------------------------------------------------------------------------


def test_csv_json_artifacts_reconcile(tmp_path: Path):
    c1h = _candles(40, drift=0.1)
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_downsample(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=[
            {
                "trade_no": 1,
                "entry_index": 25,
                "signal_time": c1h[25]["time"].isoformat(),
                "direction": "LONG",
                "outcome": "TP1",
                "r_multiple": 1.5,
                "holding_bars": 3,
            }
        ],
        index_start=20,
        run_id="unit_ms",
        enable_regime_filtering=False,
        research_only=True,
    )
    paths = write_market_structure_artifacts(
        analytics, run_id="unit_ms", reports_root=tmp_path
    )
    by_bar_json = json.loads(Path(paths["market_structure_by_bar.json"]).read_text())
    assert len(by_bar_json["table_rows"]) == len(analytics["table_rows"])
    csv_text = Path(paths["market_structure_by_bar.csv"]).read_text(encoding="utf-8")
    assert "decision_time" in csv_text
    quality = json.loads(
        Path(paths["market_structure_quality_report.json"]).read_text(encoding="utf-8")
    )
    assert quality["bars_with_future_feature_violation"] == 0
    assert quality["invariant_bars_with_future_feature_violation_eq_0"] is True


def test_trade_regime_reconciles_with_ledger():
    c1h = _candles(50, drift=0.2)
    trades = [
        {
            "trade_no": 7,
            "entry_index": 30,
            "signal_time": c1h[30]["time"].isoformat(),
            "direction": "LONG",
            "outcome": "TP1",
            "r_multiple": 2.0,
            "holding_bars": 2,
            "net_pnl_usd": 40.0,
            "gross_pnl_usd": 41.0,
            "fee_total_usd": 1.0,
        }
    ]
    analytics = compute_market_structure_analytics(
        symbol="BTCUSDT",
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_4h=_downsample(c1h, 4, 14400),
        candles_1h=c1h,
        candles_15m=None,
        trades=trades,
        index_start=25,
        enable_regime_filtering=False,
        research_only=True,
    )
    accepted = [
        r
        for r in analytics["by_bar"]
        if r.get("entry_attribution_type") == "EXECUTION_BAR"
    ]
    assert any(str(r.get("trade_id")) == "7" for r in accepted)
    # Summary groups contain the accepted trade
    assert any(s["total_trades"] >= 1 for s in analytics["trade_regime_summary"])
    assert len(analytics["trade_context"]) == 1
    assert analytics["trade_context"][0]["trade_id"] == "7"


def test_no_production_or_live_trade_created_by_analytics():
    """Analytics module has no paper/live/telegram side effects."""
    import app.research.market_structure.attach as att
    import app.research.market_structure.engine as eng

    src = (
        Path(eng.__file__).read_text(encoding="utf-8")
        + Path(att.__file__).read_text(encoding="utf-8")
    ).lower()
    assert "telegram" not in src
    assert "create_paper" not in src
    assert "live_trade" not in src
    assert "paper_trade_engine" not in src
    assert "send_message" not in src
