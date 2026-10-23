"""Synthetic states for the coin context checklist. No database, no paper, no alerts."""

from datetime import datetime, timedelta, timezone

import pytest

from app.screener_tool.context_checklist import (
    ALIGN_LONG,
    ALIGN_NEUTRAL,
    ALIGN_SHORT,
    BANNER,
    HOURS_30D,
    NO_FUNDING,
    NO_LIQ,
    NO_OI,
    NO_STRUCTURE,
    NO_VOLUME,
    NO_ZONE,
    build_checklist,
    liquidation_facts,
    oi_facts,
    population_z,
    rvol_versus_prior_20,
    structure_from_payload,
)

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _snap(**kwargs):
    base = {
        "symbol": "SYNUSDT",
        "timeframe": "1h",
        "structure": {},
        "zone": {},
        "rvol": None,
        "funding_rate": None,
        "funding_z": None,
        "oi_has_history": False,
        "liquidations": {"has_30d": False},
        "htf": {},
    }
    base.update(kwargs)
    return base


def test_banner_and_counts_and_missing_texts():
    out = build_checklist(_snap())
    assert out["banner"] == BANNER
    assert out["read_only"] is True
    by = {row["category"]: row for row in out["rows"]}
    assert by["Structure"]["state"] == NO_STRUCTURE
    assert by["Supply/demand"]["state"] == NO_ZONE
    assert by["Volume"]["state"] == NO_VOLUME
    assert by["Futures"]["state"] == NO_FUNDING
    assert by["Open interest"]["state"] == NO_OI
    assert by["Liquidations"]["state"] == NO_LIQ
    assert "no closed-bar EMA" in by["Higher timeframe"]["state"]
    assert out["counts"] == {ALIGN_LONG: 0, ALIGN_SHORT: 0, ALIGN_NEUTRAL: 7}
    blob = " ".join(row["state"] + row["alignment"] for row in out["rows"]) + out["banner"]
    assert "buy" not in blob.lower()
    assert "sell" not in blob.lower()


def test_each_row_alignment_from_synthetic_inputs():
    out = build_checklist(
        _snap(
            structure={"trend": "up", "event": "BOS", "event_side": "up", "confirmed": True},
            zone={"kind": "demand", "distance_pct": 1.25},
            rvol=1.8,
            funding_rate=0.0003,
            funding_z=1.5,
            oi_has_history=True,
            oi_change_24h_pct=4.5,
            oi_z=0.4,
            liquidations={
                "has_30d": True,
                "long_24h": 10,
                "short_24h": 80,
                "p95_long": 50,
                "p95_short": 40,
            },
            htf={
                "4h": {"status": "OK", "trend": "UP", "close_vs_ema50": "UP", "ema20_vs_ema50": "UP"},
                "1d": {"status": "OK", "trend": "UP", "close_vs_ema50": "UP", "ema20_vs_ema50": "UP"},
            },
        )
    )
    by = {row["category"]: row for row in out["rows"]}
    assert by["Structure"]["alignment"] == ALIGN_LONG
    assert "trend up; last BOS; confirmed" == by["Structure"]["state"]
    assert by["Supply/demand"]["alignment"] == ALIGN_LONG
    assert "1.25%" in by["Supply/demand"]["state"]
    assert by["Volume"]["alignment"] == ALIGN_NEUTRAL
    assert by["Volume"]["state"] == "RVOL 1.80 vs the 20-bar average"
    assert by["Futures"]["alignment"] == ALIGN_SHORT
    assert by["Open interest"]["state"] == "24h change 4.50%; z-score 0.40"
    assert by["Open interest"]["alignment"] == ALIGN_NEUTRAL
    assert by["Liquidations"]["alignment"] == ALIGN_LONG
    assert "24h short 80 USD" in by["Liquidations"]["state"]
    assert by["Higher timeframe"]["alignment"] == ALIGN_LONG
    assert out["counts"][ALIGN_LONG] == 4
    assert out["counts"][ALIGN_SHORT] == 1
    assert out["counts"][ALIGN_NEUTRAL] == 2


def test_unconfirmed_structure_and_opposite_funding_are_neutral_or_long():
    out = build_checklist(
        _snap(
            structure={"trend": "down", "event": "CHoCH", "event_side": "down", "confirmed": False},
            funding_rate=-0.0002,
            funding_z=-1.2,
            htf={
                "4h": {"status": "OK", "trend": "DOWN", "close_vs_ema50": "DOWN", "ema20_vs_ema50": "DOWN"},
                "1d": {"status": "OK", "trend": "UP", "close_vs_ema50": "UP", "ema20_vs_ema50": "UP"},
            },
        )
    )
    by = {row["category"]: row for row in out["rows"]}
    assert by["Structure"]["alignment"] == ALIGN_NEUTRAL
    assert "unconfirmed" in by["Structure"]["state"]
    assert by["Futures"]["alignment"] == ALIGN_LONG
    assert by["Higher timeframe"]["alignment"] == ALIGN_NEUTRAL


def test_rvol_uses_the_prior_20_closed_bars():
    volumes = [10.0] * 20 + [25.0]
    assert rvol_versus_prior_20(volumes) == pytest.approx(2.5)
    assert rvol_versus_prior_20([10.0] * 20) is None


def test_population_z_of_a_known_series():
    # mean 0.25, latest 1, population stdev sqrt(0.1875)
    assert population_z([0, 0, 0, 1]) == pytest.approx(1.73205080757, rel=1e-6)
    assert population_z([1, 2]) is None


def test_structure_payload_picks_the_later_event():
    got = structure_from_payload(
        {
            "trend": {"trend": "BULLISH"},
            "bos": {"direction": "BULLISH_BOS", "state": "CONFIRMED", "break_timestamp": "2026-07-01T00:00:00+00:00"},
            "choch": {"direction": "BEARISH_CHOCH", "state": "CONFIRMED", "break_timestamp": "2026-07-02T00:00:00+00:00"},
        }
    )
    assert got == {"trend": "up", "event": "CHoCH", "event_side": "down", "confirmed": True}


def test_short_liquidation_history_is_not_enough_and_30_days_is():
    short = [{"long": 1, "short": 1} for _ in range(100)]
    assert liquidation_facts(short)["has_30d"] is False
    hourly = [{"long": 1, "short": 2} for _ in range(HOURS_30D)]
    facts = liquidation_facts(hourly)
    assert facts["has_30d"] is True
    assert facts["long_24h"] == 24
    assert facts["short_24h"] == 48


def test_oi_missing_and_present():
    assert oi_facts([], NOW)["has_history"] is False
    samples = [
        (NOW - timedelta(hours=30), 100.0),
        (NOW - timedelta(hours=10), 110.0),
        (NOW - timedelta(hours=1), 120.0),
    ]
    facts = oi_facts(samples, NOW)
    assert facts["has_history"] is True
    assert facts["change_24h_pct"] == pytest.approx(20.0)
    assert facts["z"] is not None
