"""Research-only S3 forensic diagnostics tests.

Does not modify production S/D, BOS, pullback, retest, or signal engines.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from app.engines.supply_demand.engine import (
    SupplyDemandEngine,
    SupplyDemandZone,
    ZoneStatus,
    ZoneType,
)
from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.htf import as_of_index_at_or_before
from app.research.bos_strategy_comparison.lifecycle import candles_as_of
from app.research.bos_strategy_comparison.s3_diagnostics import (
    ZONE_MAPPING,
    assert_broken_zone_not_selected,
    assert_no_future_zone,
    classify_sd_confluence,
    inspect_zones_as_of,
    run_s3_forensic_diagnostic,
)
from app.research.combination_engine import _sd_confluence, detect_zones_as_of
from app.signals.config import SignalConfig

from tests.test_bos_strategy_comparison import make_htf_from_setup, make_uptrend_series
from tests.test_lifecycle import TrackingCandles


def test_zone_mapping_long_demand_short_supply():
    assert ZONE_MAPPING["LONG"] == "demand"
    assert ZONE_MAPPING["SHORT"] == "supply"
    # Existing helper: LONG ignores supply-only
    assert (
        _sd_confluence(
            pullback={},
            direction="LONG",
            demand_zone=None,
            supply_zone=(100.0, 101.0),
            last_close=100.5,
        )
        is False
    )
    # SHORT ignores demand-only
    assert (
        _sd_confluence(
            pullback={},
            direction="SHORT",
            demand_zone=(100.0, 101.0),
            supply_zone=None,
            last_close=100.5,
        )
        is False
    )


def test_unavailable_vs_failed_classification():
    unavailable = classify_sd_confluence(
        direction="LONG",
        pullback={},
        demand_zone=None,
        supply_zone=None,
        last_close=100.0,
        zone_snap={"rejection_reason": "NO_ZONES_DETECTED"},
    )
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["pass"] is False
    assert unavailable["reason"] == "NO_ZONES_DETECTED"

    failed = classify_sd_confluence(
        direction="LONG",
        pullback={},
        demand_zone=(90.0, 91.0),
        supply_zone=None,
        last_close=100.0,
        zone_snap={
            "demand_detail": {
                "distance_to_close": 9.5,
                "status": "FRESH",
                "created_at": "2024-09-01T00:00:00+00:00",
                "low": 90.0,
                "high": 91.0,
            }
        },
    )
    assert failed["status"] == "FAIL"
    assert failed["availability"] == "AVAILABLE"
    assert failed["reason"] == "CLOSE_OUTSIDE_CONFLUENCE_BAND"


def test_zone_timestamp_le_eval_and_no_future():
    eval_t = datetime(2024, 9, 15, tzinfo=timezone.utc)
    created = datetime(2024, 9, 14, tzinfo=timezone.utc)
    future = datetime(2024, 9, 16, tzinfo=timezone.utc)
    assert assert_no_future_zone(created, eval_t) is True
    assert assert_no_future_zone(future, eval_t) is False


def test_broken_zone_cannot_qualify():
    broken = SupplyDemandZone(
        symbol="BTCUSDT",
        timeframe="15m",
        zone_type=ZoneType.DEMAND,
        high=101.0,
        low=100.0,
        created_at=datetime(2024, 9, 1, tzinfo=timezone.utc),
        strength=1.0,
        freshness=0.0,
        status=ZoneStatus.BROKEN,
    )
    fresh = SupplyDemandZone(
        symbol="BTCUSDT",
        timeframe="15m",
        zone_type=ZoneType.DEMAND,
        high=102.0,
        low=101.0,
        created_at=datetime(2024, 9, 1, tzinfo=timezone.utc),
        strength=1.0,
        freshness=1.0,
        status=ZoneStatus.FRESH,
    )
    assert assert_broken_zone_not_selected([fresh]) is True
    assert assert_broken_zone_not_selected([broken]) is False


def test_detect_zones_as_of_truncates_no_future_access():
    base = make_uptrend_series(120)
    tracked = TrackingCandles(base)
    as_of = 80
    detect_zones_as_of("TESTUSDT", "15m", tracked, as_of)
    assert tracked.max_accessed <= as_of


def test_inspect_zones_uses_candles_as_of_only():
    candles = make_uptrend_series(100)
    as_of = 60
    window = candles_as_of(candles, as_of)
    assert len(window) == as_of + 1
    snap = inspect_zones_as_of("TESTUSDT", "15m", candles, as_of)
    assert snap["truncated_len"] == as_of + 1
    assert snap["as_of_index"] == as_of


def test_pullback_timestamp_le_sd_evaluation():
    """Synthetic lifecycle ordering: impulse < pullback <= sd/retest index."""
    candles = make_uptrend_series(100)
    impulse_i = 50
    pullback_i = 55
    sd_i = 60
    assert impulse_i < pullback_i <= sd_i
    t_pb = candles[pullback_i]["time"]
    t_sd = candles[sd_i]["time"]
    assert t_pb <= t_sd


def test_htf_as_of_index_at_or_before_no_future():
    setup = make_uptrend_series(64)
    h4 = make_htf_from_setup(setup, 16)
    as_of_ts = setup[40]["time"]
    idx = as_of_index_at_or_before(h4, as_of_ts)
    assert idx is not None
    assert h4[idx]["time"] <= as_of_ts
    if idx + 1 < len(h4):
        assert h4[idx + 1]["time"] > as_of_ts


def test_sequential_funnel_subset_property():
    candles = make_uptrend_series(200)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    report = run_s3_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=h4,
        candles_1h=h1,
        index_start=50,
        signal_config=SignalConfig(),
        research_config=StrategyResearchConfig(min_bars=50),
        trace=True,
        limit=20,
    )
    f = report["funnel"]
    assert f["bos_lifecycles"] >= f["pullback_pass"]
    assert f["pullback_pass"] >= f["retest_pass"]
    assert f["retest_pass"] >= f["sd_evaluated"]
    assert f["sd_evaluated"] >= f["sd_available"]
    assert f["sd_available"] >= f["sd_confluence_pass"]
    assert f["sd_confluence_pass"] >= f["htf_pass"]
    assert f["htf_pass"] >= f["entry_ready"]
    assert f["entry_ready"] >= f["final_s3_trades"]
    assert f["rr_pass"] >= f["final_s3_trades"]
    # Direction buckets are subsets of totals
    for d in ("LONG", "SHORT"):
        bucket = report["direction"][d]
        assert bucket["bos_lifecycles"] <= f["bos_lifecycles"]
        assert bucket["retest_pass"] <= f["retest_pass"]
        assert bucket["final_s3_trades"] <= f["final_s3_trades"]


def test_s3_vs_c3_reconciliation_structure():
    candles = make_uptrend_series(200)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    report = run_s3_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=h4,
        candles_1h=h1,
        index_start=50,
    )
    c3 = report["c3_reconciliation"]
    assert c3["c3_retest_pass"] == report["funnel"]["retest_pass"]
    assert c3["sd_pass"] == report["funnel"]["sd_confluence_pass"]
    assert c3["rejected_by_sd"] == c3["c3_retest_pass"] - c3["sd_pass"]
    assert "rejection_reasons" in c3
    assert report["root_cause_classification"]["classification"]


def test_deterministic_s3_diagnostic():
    candles = make_uptrend_series(180)
    h4 = make_htf_from_setup(candles, 16)
    h1 = make_htf_from_setup(candles, 4)
    a = run_s3_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=h4,
        candles_1h=h1,
        index_start=40,
    )
    b = run_s3_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=deepcopy(candles),
        candles_4h=deepcopy(h4),
        candles_1h=deepcopy(h1),
        index_start=40,
    )
    assert a["data_quality"]["deterministic_fingerprint"] == b[
        "data_quality"
    ]["deterministic_fingerprint"]
    assert a["funnel"] == b["funnel"]
    assert a["sd_rejection_reasons"] == b["sd_rejection_reasons"]


def test_lookahead_check_flag_present():
    candles = make_uptrend_series(160)
    report = run_s3_forensic_diagnostic(
        symbol="TESTUSDT",
        timeframe="15m",
        candles=candles,
        candles_4h=make_htf_from_setup(candles, 16),
        candles_1h=make_htf_from_setup(candles, 4),
        index_start=40,
    )
    assert report["lookahead"]["check"] in ("PASS", "FAIL")
    assert report["lookahead"]["htf_uses_as_of_index_at_or_before"] is True
    assert report["live_engines_unchanged"] is True
    assert report["sd_logic_unchanged"] is True


def test_close_in_band_passes_existing_helper():
    ok = _sd_confluence(
        pullback={"zone_hit": None},
        direction="LONG",
        demand_zone=(100.0, 101.0),
        supply_zone=None,
        last_close=100.5,
    )
    assert ok is True
    classified = classify_sd_confluence(
        direction="LONG",
        pullback={},
        demand_zone=(100.0, 101.0),
        supply_zone=None,
        last_close=100.5,
    )
    assert classified["pass"] is True
    assert classified["reason"] == "CLOSE_IN_DEMAND_BAND"
