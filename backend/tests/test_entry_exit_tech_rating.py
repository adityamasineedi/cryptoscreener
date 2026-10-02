from __future__ import annotations

from app.engines.signals.entry_exit import EntryExitEngine
from app.engines.signals.tech_rating import TechRatingEngine
from app.models.schemas import DataStatus


def test_entry_exit_waiting_when_no_data():
    eng = EntryExitEngine()
    out = eng.evaluate(
        structure=None,
        bos=None,
        choch=None,
        rvol=None,
        rsi=None,
        nearest_demand=None,
        nearest_supply=None,
    )
    assert out["state"].value == "WAITING"
    assert out["state"].status == DataStatus.WAITING
    assert all(c["passed"] is None for c in out["entry_conditions"])


def test_entry_candidate_explicit_conditions():
    eng = EntryExitEngine({"entry_exit": {"require_rvol_above": 1.2, "rsi_entry_max": 70}})
    out = eng.evaluate(
        structure="bullish",
        bos="BULLISH_BOS",
        choch=None,
        rvol=1.5,
        rsi=55,
        nearest_demand="DEMAND_FRESH",
        nearest_supply=None,
    )
    assert out["state"].value == "ENTRY_CANDIDATE"
    assert out["entry_pass_count"] >= 2
    # Every condition is visible
    ids = {c["id"] for c in out["entry_conditions"]}
    assert "structure_bullish" in ids
    assert "rvol_expansion" in ids


def test_never_emits_strong_buy():
    eng = TechRatingEngine(
        {
            "signals": {
                "weights": {
                    "structure": 0.25,
                    "supply_demand": 0.2,
                    "volume": 0.2,
                    "liquidation": 0.15,
                    "open_interest": 0.2,
                }
            },
            "tech_rating": {"momentum_weight": 0.1, "labels": {"bullish": 0.65, "bearish": 0.35}},
        }
    )
    out = eng.evaluate(
        structure="bullish",
        rvol=2.0,
        rsi=50,
        zone="DEMAND_FRESH",
        oi_classification="PRICE_UP_OI_UP",
        liquidation_status="WAITING",
        bos="BULLISH_BOS",
    )
    label = out["label"].value
    assert label in {"BULLISH_BIAS", "NEUTRAL", "BEARISH_BIAS", "INSUFFICIENT_DATA"}
    assert "Strong" not in str(label)
    assert "Buy" not in str(label)
    assert "structure" in out["components"]
    assert "volume" in out["components"]
    assert "momentum" in out["components"]
    assert "supply_demand" in out["components"]
    assert "open_interest" in out["components"]
    assert "liquidation" in out["components"]


def test_tech_rating_insufficient_when_mostly_missing():
    eng = TechRatingEngine()
    out = eng.evaluate(
        structure=None,
        rvol=None,
        rsi=None,
        zone=None,
        oi_classification=None,
        liquidation_status="WAITING",
    )
    assert out["label"].status == DataStatus.WAITING or out["label"].value in {
        None,
        "INSUFFICIENT_DATA",
        "WAITING",
    }
