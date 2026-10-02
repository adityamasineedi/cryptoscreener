"""Dependency-aware WAITING annotations — no fabrication."""

from app.models.schemas import DataStatus, FreshValue
from app.services.metric_dependencies import (
    tech_rating_requires,
    with_requires,
)


def test_with_requires_only_annotates_waiting_null():
    live = FreshValue.live(1.2, "volume_engine")
    assert with_requires(live, "15m OHLCV") is live

    waiting = FreshValue.waiting("market_structure")
    out = with_requires(waiting, "15m OHLCV")
    assert out.status == DataStatus.WAITING
    assert out.value is None
    assert out.methodology == "Requires 15m OHLCV"


def test_tech_rating_requires_compact():
    assert tech_rating_requires([]) == "Requires Structure + Volume + Momentum"
    assert "Structure" in tech_rating_requires(["structure", "volume"])
    assert "OI" in tech_rating_requires(["open_interest"])


def test_tech_rating_insufficient_uses_requires_methodology():
    from app.engines.signals.tech_rating import TechRatingEngine

    eng = TechRatingEngine({"signals": {"weights": {"structure": 0.25, "volume": 0.2, "supply_demand": 0.2, "liquidation": 0.15, "open_interest": 0.2}}})
    out = eng.evaluate(
        structure=None,
        rvol=None,
        rsi=None,
        zone=None,
        oi_classification=None,
        liquidation_status="WAITING",
    )
    label = out["label"]
    assert label.status == DataStatus.WAITING
    assert label.value is None or label.value == "INSUFFICIENT_DATA" or label.methodology
    assert "Requires" in (label.methodology or "")
