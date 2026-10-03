"""Tests for research historical market-cap classification (no fabrication)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.research.config import ResearchConfig, classify_asset_group
from app.research.historical_market_cap.classifier import (
    CLASSIFICATION_RULE_VERSION,
    cap_group_at,
    classify_strategy_cap_group,
    filter_candidates_by_historical_cap,
)


def _obs(ts: datetime, mcap: float, symbol: str = "AAAUSDT") -> dict:
    return {
        "symbol": symbol,
        "effective_time": ts,
        "market_cap": mcap,
        "source": "coingecko_market_chart",
        "currency": "usd",
        "cap_group": "large-cap",
        "classification_rule_version": CLASSIFICATION_RULE_VERSION,
    }


def test_thresholds_unchanged():
    cfg = ResearchConfig()
    assert cfg.large_cap_min == 10_000_000_000.0
    assert cfg.mid_cap_min == 1_000_000_000.0
    assert cfg.mid_cap_max == 10_000_000_000.0
    assert cfg.small_cap_min == 50_000_000.0
    assert cfg.small_cap_max == 1_000_000_000.0


def test_missing_cap_is_unavailable_not_guessed():
    t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    r = cap_group_at("FOOUSDT", t0, [])
    assert r.status == "CAP_GROUP_UNAVAILABLE"
    assert r.strategy_cap_group == "UNAVAILABLE"
    assert r.market_cap is None


def test_btc_eth_not_strategy_large_cap():
    assert classify_asset_group("BTCUSDT", 1e12) == "BTC"
    assert classify_strategy_cap_group("BTCUSDT", 1e12) == "UNAVAILABLE"
    assert classify_asset_group("ETHUSDT", 4e11) == "ETH"
    assert classify_strategy_cap_group("ETHUSDT", 4e11) == "UNAVAILABLE"


def test_threshold_buckets():
    assert classify_strategy_cap_group("BNBUSDT", 20e9) == "LARGE_CAP"
    assert classify_strategy_cap_group("LINKUSDT", 5e9) == "MID_CAP"
    assert classify_strategy_cap_group("ORDIUSDT", 200e6) == "SMALL_CAP"
    assert classify_strategy_cap_group("TINYUSDT", 10e6) == "UNAVAILABLE"


def test_as_of_uses_latest_observation_not_future():
    """Future market-cap observations must not alter an earlier cap group."""
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    t30 = t0 + timedelta(days=30)
    # At t0: mid-cap. Later observations jump to large-cap.
    observations = [
        _obs(t0 - timedelta(days=1), 2e9),  # mid
        _obs(t1, 15e9),  # large — AFTER t0
        _obs(t30, 20e9),  # large — AFTER t0
    ]
    at_t0 = cap_group_at("AAAUSDT", t0, observations)
    assert at_t0.strategy_cap_group == "MID_CAP"
    assert at_t0.market_cap == 2e9

    at_t1 = cap_group_at("AAAUSDT", t1, observations)
    assert at_t1.strategy_cap_group == "LARGE_CAP"
    assert at_t1.market_cap == 15e9

    at_t30 = cap_group_at("AAAUSDT", t30, observations)
    assert at_t30.strategy_cap_group == "LARGE_CAP"
    assert at_t30.market_cap == 20e9

    # Re-check t0 unchanged after "knowing" future exists in the series
    at_t0_again = cap_group_at("AAAUSDT", t0, observations)
    assert at_t0_again.strategy_cap_group == "MID_CAP"
    assert at_t0_again.market_cap == 2e9


def test_never_uses_observation_strictly_after_T():
    t = datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)
    observations = [
        _obs(t + timedelta(seconds=1), 20e9),  # 1s in the future
    ]
    r = cap_group_at("AAAUSDT", t, observations)
    assert r.status == "CAP_GROUP_UNAVAILABLE"


def test_filter_candidates_separates_cross_cap():
    t = datetime(2024, 2, 1, tzinfo=timezone.utc)
    obs = {
        "MIDUSDT": [_obs(t - timedelta(days=2), 3e9, "MIDUSDT")],
        "BIGUSDT": [_obs(t - timedelta(days=2), 20e9, "BIGUSDT")],
    }
    cands = [
        SimpleNamespace(
            symbol="MIDUSDT",
            signal_time=t.isoformat(),
            entry_index=10,
        ),
        SimpleNamespace(
            symbol="BIGUSDT",
            signal_time=t.isoformat(),
            entry_index=11,
        ),
    ]
    elig, cross, stats = filter_candidates_by_historical_cap(
        cands, required_group="MID_CAP", observations_by_symbol=obs
    )
    assert len(elig) == 1 and elig[0].symbol == "MIDUSDT"
    assert len(cross) == 1 and cross[0].symbol == "BIGUSDT"
    assert stats["cap_eligible_signals"] == 1
    assert stats["cross_cap_signal_count"] == 1
