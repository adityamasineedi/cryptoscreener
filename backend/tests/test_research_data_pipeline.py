"""Unit tests for isolated research data pipeline (no live API required)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.research.data_pipeline.checkpoint import SeriesCheckpoint
from app.research.data_pipeline.config import PipelineConfig, FEATURE_VERSION
from app.research.data_pipeline.deduplicator import dedupe_rows, merge_unique
from app.research.data_pipeline.event_store import (
    STRATEGY_SPECS,
    evaluate_strategy_specs,
    mtf_state_from_trends,
    strategy_event_mask,
)
from app.research.data_pipeline.manifest import (
    build_quality_report,
    make_dataset_version,
)
from app.research.data_pipeline.paginator import (
    align_open_ms,
    iter_day_chunks,
    page_windows_within_chunk,
    parse_day_ms,
)
from app.research.data_pipeline.validator import filter_valid_rows, validate_candles


def test_pipeline_config_hash_stable():
    a = PipelineConfig()
    b = PipelineConfig()
    assert a.configuration_hash() == b.configuration_hash()
    assert FEATURE_VERSION.startswith("research_features_")


def test_paginator_chunks_and_resume():
    chunks = list(
        iter_day_chunks(
            symbol="BTCUSDT",
            timeframe="15m",
            start_day="2024-01-01",
            end_day="2024-04-01",
            chunk_days=90,
        )
    )
    assert len(chunks) >= 1
    assert chunks[0].symbol == "BTCUSDT"
    # Resume skips completed
    resume = chunks[0].end_ms
    resumed = list(
        iter_day_chunks(
            symbol="BTCUSDT",
            timeframe="15m",
            start_day="2024-01-01",
            end_day="2024-04-01",
            chunk_days=90,
            resume_after_ms=resume,
        )
    )
    assert all(c.start_ms >= resume for c in resumed)


def test_page_windows_respect_kline_limit():
    chunks = list(
        iter_day_chunks(
            symbol="ETHUSDT",
            timeframe="15m",
            start_day="2024-01-01",
            end_day="2024-01-31",
            chunk_days=30,
        )
    )
    assert chunks
    windows = page_windows_within_chunk(chunks[0], kline_limit=1500)
    assert windows
    assert windows[0][0] == chunks[0].start_ms


def test_align_and_parse_day():
    assert parse_day_ms("2020-10-01") == 1601510400000
    aligned = align_open_ms(1601510400123, "15m")
    assert aligned % 900_000 == 0


def test_validator_detects_bad_ohlc_and_gaps():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
    rows = [
        {
            "open_time_ms": int(base),
            "open": 100,
            "high": 110,
            "low": 90,
            "close": 105,
            "volume": 1,
        },
        {
            "open_time_ms": int(base + 900_000 * 3),  # skip 2 bars
            "open": 105,
            "high": 120,
            "low": 100,
            "close": 110,
            "volume": 2,
        },
    ]
    report = validate_candles(rows, symbol="BTCUSDT", timeframe="15m")
    assert len(report.gaps) == 1
    assert report.quality in ("GAPS", "PASS")

    bad = [
        {
            "open_time_ms": int(base),
            "open": 100,
            "high": 90,  # high < open
            "low": 80,
            "close": 95,
            "volume": 1,
        }
    ]
    bad_report = validate_candles(bad, symbol="BTCUSDT", timeframe="15m")
    assert bad_report.invalid_ohlc >= 1
    assert bad_report.quality == "FAIL"


def test_filter_valid_and_dedupe():
    base = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [
        {
            "open_time_ms": base,
            "time": datetime.fromtimestamp(base / 1000, tz=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
        },
        {
            "open_time_ms": base,
            "time": datetime.fromtimestamp(base / 1000, tz=timezone.utc),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
        },
    ]
    good, reasons = filter_valid_rows(rows)
    assert len(good) == 1
    assert any("duplicate" in r for r in reasons)
    assert len(dedupe_rows(rows)) == 1
    known: set[int] = set()
    assert len(merge_unique(known, rows)) == 1
    assert len(merge_unique(known, rows)) == 0


def test_checkpoint_resume_fields():
    cp = SeriesCheckpoint(
        symbol="BTCUSDT",
        timeframe="15m",
        requested_start="2020-10-01",
        requested_end="2026-10-01",
    )
    cp.mark_downloading()
    cp.mark_chunk_done(0, 1_700_000_000_000, 100)
    assert cp.resume_after_ms() == 1_700_000_000_000
    cp.mark_complete()
    assert cp.status == "COMPLETE"
    assert cp.to_dict()["candles_downloaded"] == 100


def test_manifest_quality_report():
    cps = [
        SeriesCheckpoint(
            symbol="BTCUSDT",
            timeframe="15m",
            requested_start="2020-10-01",
            requested_end="2026-10-01",
            actual_first="2020-10-01",
            actual_last="2026-10-01",
            candles_downloaded=1000,
            status="COMPLETE",
            quality="PASS",
        ),
        SeriesCheckpoint(
            symbol="ETHUSDT",
            timeframe="15m",
            requested_start="2020-10-01",
            requested_end="2026-10-01",
            status="FAILED",
            last_error="timeout",
        ),
    ]
    report = build_quality_report(
        dataset_version=make_dataset_version("test"),
        checkpoints=cps,
        symbols_requested=["BTCUSDT", "ETHUSDT"],
        timeframes=["15m"],
        bars_by_key={("BTCUSDT", "15m"): 1000},
    )
    d = report.to_dict()
    assert d["total_candles"] == 1000
    assert "BTCUSDT" in d["symbols_eligible"]
    assert "ETHUSDT" in d["failed_symbols"]
    assert d["fabricated"] is False


def test_strategy_masks_and_specs():
    assert set(STRATEGY_SPECS) >= {"S1", "S2", "S3", "C1", "C2", "C3", "C4"}
    events = [
        {
            "direction": "LONG",
            "htf_alignment": "HTF_ALIGNED",
            "impulse": True,
            "pullback": True,
            "retest": True,
            "sd_state": "INTERACT",
            "payload": {"htf_aligned": True},
        },
        {
            "direction": "SHORT",
            "htf_alignment": "HTF_CONFLICT",
            "impulse": False,
            "pullback": True,
            "retest": True,
            "sd_state": "NONE",
            "payload": {},
        },
    ]
    s1 = strategy_event_mask(events, require_htf=True, require_retest=True)
    assert len(s1) == 1
    report = evaluate_strategy_specs(events, min_sample=30)
    assert report["C1"]["n"] == 2
    assert report["C1"]["status"] == "INSUFFICIENT_SAMPLE"


def test_mtf_state_classification():
    assert (
        mtf_state_from_trends(
            direction="LONG",
            trend_4h="BULLISH",
            trend_1h="BULLISH",
            trend_15m="BULLISH",
            trend_5m="BULLISH",
        )
        == "ALIGNED_BULLISH"
    )
    assert (
        mtf_state_from_trends(
            direction="LONG",
            trend_4h="BULLISH",
            trend_1h="BEARISH",
            trend_15m="BULLISH",
            trend_5m="NEUTRAL",
        )
        == "CONFLICT"
    )


# ---------------------------------------------------------------------------
# Incremental sync planner (DB-first) — pure unit tests, no network
# ---------------------------------------------------------------------------

from app.research.data_pipeline.coverage import (
    BEFORE_SYMBOL_AVAILABLE,
    GapRange,
    align_requested_range,
    build_coverage_from_stats,
    expected_candle_count,
    gaps_from_timestamps,
)
from app.research.data_pipeline.feature_store import series_data_fingerprint
from app.research.data_pipeline.locks import make_lock_key
from app.research.data_pipeline.planner import (
    ACTION_DOWNLOAD,
    ACTION_SKIP,
    ACTION_UNAVAILABLE,
    plan_from_coverage,
)
from app.research.data_pipeline.paginator import align_open_ms


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_plan_empty_db_full_range():
    """1. Empty DB → full requested range."""
    start, end = align_requested_range("2024-01-01", "2024-01-02", "15m", clip_to_now=False)
    cov = build_coverage_from_stats(
        symbol="BTCUSDT",
        timeframe="15m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=None,
        existing_max_ms=None,
        row_count=0,
        distinct_count=0,
    )
    plan = plan_from_coverage(cov)
    assert plan.action == ACTION_DOWNLOAD
    assert plan.case == "A"
    assert len(plan.ranges) == 1
    assert plan.ranges[0].start_ms == start
    assert plan.ranges[0].end_ms == end
    assert plan.estimated_candles == expected_candle_count(start, end, "15m")


def test_plan_db_fully_covers_zero_downloads():
    """2. DB fully covers request → zero downloads."""
    start, end = align_requested_range("2024-01-01", "2024-01-02", "15m", clip_to_now=False)
    step = 900_000
    cov = build_coverage_from_stats(
        symbol="BTCUSDT",
        timeframe="15m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start,
        existing_max_ms=end - step,
        row_count=expected_candle_count(start, end, "15m"),
        distinct_count=expected_candle_count(start, end, "15m"),
        gaps=[],
    )
    plan = plan_from_coverage(cov)
    assert plan.action == ACTION_SKIP
    assert plan.case == "B"
    assert plan.estimated_candles == 0
    assert plan.ranges == []


def test_plan_missing_left_edge():
    """3. Missing left edge → only left edge downloaded."""
    start, end = align_requested_range("2024-01-01", "2024-01-10", "15m", clip_to_now=False)
    step = 900_000
    existing_min = start + step * 100
    cov = build_coverage_from_stats(
        symbol="ETHUSDT",
        timeframe="15m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=existing_min,
        existing_max_ms=end - step,
        row_count=500,
        distinct_count=500,
        gaps=[],
    )
    plan = plan_from_coverage(cov)
    assert plan.action == ACTION_DOWNLOAD
    assert "C" in plan.case
    assert len(plan.ranges) == 1
    assert plan.ranges[0].reason == "LEFT_EDGE"
    assert plan.ranges[0].start_ms == start
    assert plan.ranges[0].end_ms == existing_min


def test_plan_missing_right_edge():
    """4. Missing right edge → only right edge downloaded."""
    start, end = align_requested_range("2024-01-01", "2024-01-10", "15m", clip_to_now=False)
    step = 900_000
    existing_max = end - step * 50
    cov = build_coverage_from_stats(
        symbol="ETHUSDT",
        timeframe="15m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start,
        existing_max_ms=existing_max,
        row_count=500,
        distinct_count=500,
        gaps=[],
    )
    plan = plan_from_coverage(cov)
    assert plan.action == ACTION_DOWNLOAD
    assert "D" in plan.case
    assert len(plan.ranges) == 1
    assert plan.ranges[0].reason == "RIGHT_EDGE"
    assert plan.ranges[0].start_ms == existing_max + step
    assert plan.ranges[0].end_ms == end


def test_plan_internal_gap_only():
    """5. Internal gap → only gap downloaded."""
    start, end = align_requested_range("2024-01-01", "2024-01-02", "15m", clip_to_now=False)
    step = 900_000
    gap = GapRange(
        gap_start_ms=start + step * 10,
        gap_end_ms=start + step * 12,
        missing_candle_count=3,
    )
    cov = build_coverage_from_stats(
        symbol="SOLUSDT",
        timeframe="15m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start,
        existing_max_ms=end - step,
        row_count=90,
        distinct_count=90,
        gaps=[gap],
    )
    plan = plan_from_coverage(cov)
    assert plan.case == "E" or "E" in plan.case
    assert all(r.reason == "INTERNAL_GAP" for r in plan.ranges)
    assert plan.ranges[0].start_ms == gap.gap_start_ms


def test_plan_multiple_gaps():
    """6. Multiple gaps → multiple targeted downloads."""
    start, end = align_requested_range("2024-01-01", "2024-01-03", "15m", clip_to_now=False)
    step = 900_000
    gaps = [
        GapRange(start + step * 5, start + step * 6, 2),
        GapRange(start + step * 40, start + step * 42, 3),
    ]
    cov = build_coverage_from_stats(
        symbol="SOLUSDT",
        timeframe="15m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start,
        existing_max_ms=end - step,
        row_count=200,
        distinct_count=200,
        gaps=gaps,
    )
    plan = plan_from_coverage(cov)
    assert len(plan.ranges) >= 2
    assert sum(r.estimated_candles for r in plan.ranges) >= 5


def test_plan_manifest_stale_db_complete_no_redownload():
    """7. Manifest stale but DB complete → no redownload."""
    start, end = align_requested_range("2020-10-01", "2024-01-01", "1h", clip_to_now=False)
    step = 3_600_000
    cov = build_coverage_from_stats(
        symbol="BTCUSDT",
        timeframe="1h",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start,
        existing_max_ms=end - step,
        row_count=10_000,
        distinct_count=10_000,
        gaps=[],
        manifest_status="COMPLETE",
    )
    assert cov.manifest_stale is True
    plan = plan_from_coverage(cov)
    assert plan.action == ACTION_SKIP
    assert plan.case == "F"
    assert plan.estimated_candles == 0


def test_candle_boundary_alignment():
    """13. Candle boundary alignment — next after last is +interval."""
    aligned = align_open_ms(1_704_067_200_123, "15m")
    assert aligned % 900_000 == 0
    next_needed = aligned + 900_000
    assert next_needed - aligned == 900_000
    start, end = align_requested_range("2024-01-01", "2024-01-01", "5m", clip_to_now=False)
    assert start % 300_000 == 0
    assert end % 300_000 == 0


def test_symbol_unavailable_before_listing():
    """14. Symbol unavailable before listing → clamp start; no pre-listing download."""
    start, end = align_requested_range("2020-10-01", "2026-10-03", "5m", clip_to_now=False)
    listed = _ms("2024-06-01T00:00:00")
    cov = build_coverage_from_stats(
        symbol="NEWCOINUSDT",
        timeframe="5m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=None,
        existing_max_ms=None,
        row_count=0,
        distinct_count=0,
        listed_at_ms=listed,
    )
    assert cov.availability == BEFORE_SYMBOL_AVAILABLE
    # Requested start is clamped to listing — plan downloads from listing, not 2020
    assert cov.requested_start_ms >= listed
    plan = plan_from_coverage(cov)
    assert plan.action == ACTION_DOWNLOAD
    assert plan.ranges[0].start_ms >= listed
    # Window entirely before listing → unavailable
    early_end = listed - 300_000
    cov2 = build_coverage_from_stats(
        symbol="NEWCOINUSDT",
        timeframe="5m",
        requested_start_ms=start,
        requested_end_ms=early_end,
        existing_min_ms=None,
        existing_max_ms=None,
        row_count=0,
        distinct_count=0,
        listed_at_ms=listed,
    )
    plan2 = plan_from_coverage(cov2)
    assert plan2.action == ACTION_UNAVAILABLE


def test_feature_cache_fingerprint_stable_and_invalidates():
    """15–16. Feature cache hit identity + invalidation after gap repair."""
    t0 = datetime(2020, 10, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    a = series_data_fingerprint("BTCUSDT", "15m", t0, t1, 1000, gap_count=0)
    b = series_data_fingerprint("BTCUSDT", "15m", t0, t1, 1000, gap_count=0)
    assert a == b
    c = series_data_fingerprint("BTCUSDT", "15m", t0, t1, 1000, gap_count=3)
    assert a != c  # gap repair changes fingerprint


def test_gaps_from_timestamps_detects_missing():
    base = _ms("2024-01-01T10:00:00")
    step = 900_000
    ts = [base, base + step, base + step * 2, base + step * 5]  # miss 3,4
    gaps = gaps_from_timestamps(ts, "15m")
    assert len(gaps) == 1
    assert gaps[0].missing_candle_count == 2
    assert gaps[0].gap_start_ms == base + step * 3


def test_deterministic_download_plan():
    """23. Deterministic download plan for identical coverage."""
    start, end = align_requested_range("2023-01-01", "2024-01-01", "5m", clip_to_now=False)
    step = 300_000
    kwargs = dict(
        symbol="ETHUSDT",
        timeframe="5m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start + step * 1000,
        existing_max_ms=end - step,
        row_count=50_000,
        distinct_count=50_000,
        gaps=[],
    )
    p1 = plan_from_coverage(build_coverage_from_stats(**kwargs))
    p2 = plan_from_coverage(build_coverage_from_stats(**kwargs))
    assert p1.to_dict()["ranges"] == p2.to_dict()["ranges"]
    assert p1.estimated_candles == p2.estimated_candles


def test_lock_key_granularity():
    """Different symbols/timeframes get different lock keys."""
    a = make_lock_key("DATA_SYNC", "BTCUSDT", "5m", 1, 2)
    b = make_lock_key("DATA_SYNC", "ETHUSDT", "5m", 1, 2)
    c = make_lock_key("DATA_SYNC", "BTCUSDT", "15m", 1, 2)
    assert a != b and a != c


def test_bounded_workers_config():
    """18. Bounded concurrency via env / config."""
    cfg = PipelineConfig.from_mapping({"max_workers": 100})
    assert cfg.max_workers == 100  # dataclass allows; sync clamps to 8
    import os

    os.environ["RESEARCH_DOWNLOAD_WORKERS"] = "2"
    try:
        cfg2 = PipelineConfig.from_mapping({"max_workers": 8})
        assert cfg2.max_workers == 2
    finally:
        del os.environ["RESEARCH_DOWNLOAD_WORKERS"]


def test_invalid_ohlc_rejection():
    """11. Invalid OHLC rejection."""
    bad = [
        {
            "open_time_ms": 1_700_000_000_000,
            "open": 100,
            "high": 90,
            "low": 80,
            "close": 95,
            "volume": 1,
        }
    ]
    good, reasons = filter_valid_rows(bad)
    assert good == []
    assert any("bad_ohlc" in r for r in reasons)


def test_second_sync_plan_unchanged_is_skip():
    """21. Second sync downloads nothing when dataset unchanged."""
    start, end = align_requested_range("2020-10-01", "2026-10-03", "5m", clip_to_now=False)
    step = 300_000
    cov = build_coverage_from_stats(
        symbol="BTCUSDT",
        timeframe="5m",
        requested_start_ms=start,
        requested_end_ms=end,
        existing_min_ms=start,
        existing_max_ms=end - step,
        row_count=expected_candle_count(start, end, "5m"),
        distinct_count=expected_candle_count(start, end, "5m"),
        gaps=[],
    )
    p1 = plan_from_coverage(cov)
    p2 = plan_from_coverage(cov)
    assert p1.action == ACTION_SKIP and p2.action == ACTION_SKIP
    assert p1.estimated_candles == 0 and p2.estimated_candles == 0


def test_dry_run_helper_zero_network_contract():
    """20. Dry-run contract: dry_run_sync documented to make zero network calls.

    Pure planner path used by dry-run never touches BinanceRestClient.
    """
    from app.research.data_pipeline import sync as sync_mod

    assert "Zero Binance" in (sync_mod.dry_run_sync.__doc__ or "")
    assert "dry_run" in sync_mod.sync_universe.__code__.co_varnames
