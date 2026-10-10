"""Scanner scores a two-way range above a one-way trend. No orders."""

from __future__ import annotations

import math

from app.grid_trading.scanner import rank_scores, score_series


def _series(prices: list[float]) -> list[dict]:
    rows = []
    for i, close in enumerate(prices):
        rows.append(
            {
                "time": 1_700_000_000 + i * 3600,
                "open": close,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
            }
        )
    return rows


def test_oscillation_outranks_a_trend():
    wave = [100.0 + 8.0 * math.sin(i / 2.5) for i in range(80)]
    trend = [100.0 + i * 0.8 for i in range(80)]
    oscillating = score_series(
        _series(wave),
        fee_bps=1.0,
        slippage_bps=1.0,
        grid_count=4,
        symbol="WAV",
        timeframe="1h",
    )
    trending = score_series(
        _series(trend),
        fee_bps=1.0,
        slippage_bps=1.0,
        grid_count=4,
        symbol="TRD",
        timeframe="1h",
    )
    assert oscillating["market"] == "binance_usdt_m_futures"
    assert oscillating["score"] > trending["score"]
    assert oscillating["mid_crosses"] > trending["mid_crosses"]
    ranked = rank_scores([trending, oscillating])
    assert ranked[0]["symbol"] == "WAV"


def test_short_history_is_not_eligible():
    row = score_series(
        _series([100.0, 101.0, 100.5]),
        fee_bps=4.0,
        slippage_bps=1.0,
        grid_count=4,
        symbol="SHORT",
    )
    assert row["eligible"] is False
    assert row["reason"] == "insufficient_bars"
