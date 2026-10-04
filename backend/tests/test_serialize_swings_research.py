"""Research-only serialize_swings=False must not change structure semantics.

Production default (serialize_swings=True) must keep API swing dicts identical.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine


def _ts(i: int) -> datetime:
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i)


def _candles() -> list[dict]:
    """Bullish HH/HL fixture that yields confirmed swings (same shape as setup tests)."""
    prices = [
        (100, 101, 99, 100.5),
        (100.5, 101.2, 99.8, 100.8),
        (100.8, 101.5, 100.0, 101.0),
        (101.0, 101.2, 98.0, 98.5),
        (98.5, 99.0, 97.8, 98.2),
        (98.2, 99.5, 98.0, 99.0),
        (99.0, 100.5, 98.8, 100.0),
        (100.0, 105.0, 99.5, 104.0),
        (104.0, 104.5, 102.0, 102.5),
        (102.5, 103.0, 101.5, 102.0),
        (102.0, 102.5, 101.0, 101.5),
        (101.5, 102.0, 100.8, 101.2),
        (101.2, 103.0, 101.0, 102.5),
        (102.5, 110.0, 102.0, 109.0),
        (109.0, 109.5, 107.0, 107.5),
        (107.5, 108.0, 106.5, 107.0),
        (107.0, 107.5, 106.0, 106.5),
        (106.5, 107.0, 105.8, 106.2),
        (106.2, 108.0, 106.0, 107.5),
        (107.5, 113.0, 107.0, 112.5),
        (112.5, 112.8, 110.2, 110.5),
        (110.5, 111.0, 109.8, 110.3),
        (110.3, 111.5, 110.0, 111.0),
    ]
    # Pad with quiet bars so default swing right-bars can confirm
    while len(prices) < 80:
        last = prices[-1][3]
        prices.append((last, last + 0.3, last - 0.3, last + 0.1))
    return [
        {
            "time": _ts(i),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000.0 + i,
        }
        for i, (o, h, l, c) in enumerate(prices)
    ]


def _structure_digest(analysis: dict) -> dict:
    swings = analysis.get("_swings_objs") or []
    return {
        "trend": analysis.get("trend"),
        "bos": analysis.get("bos"),
        "choch": analysis.get("choch"),
        "impulse": analysis.get("impulse"),
        "pullback": analysis.get("pullback"),
        "retest": analysis.get("retest"),
        "swing_count": len(swings),
        "swings": [
            {
                "bar_index": s.bar_index,
                "swing_type": s.swing_type,
                "price": s.price,
                "label": s.label,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            }
            for s in swings
        ],
    }


def test_serialize_swings_false_skips_api_list_keeps_objs():
    eng = SignalEngine(SignalConfig())
    candles = _candles()
    out = eng.analyze_timeframe(
        "TESTUSDT", "15m", candles, as_of_index=len(candles) - 1, serialize_swings=False
    )
    assert out["swings"] == []
    assert out.get("_swings_objs")
    assert all(hasattr(s, "bar_index") for s in out["_swings_objs"])


def test_serialize_swings_default_matches_true_and_api_shape():
    eng = SignalEngine(SignalConfig())
    candles = _candles()
    as_of = len(candles) - 1
    default = eng.analyze_timeframe("TESTUSDT", "15m", candles, as_of_index=as_of)
    explicit = eng.analyze_timeframe(
        "TESTUSDT", "15m", candles, as_of_index=as_of, serialize_swings=True
    )
    assert default["swings"] == explicit["swings"]
    assert default["swings"]
    assert isinstance(default["swings"][0], dict)
    assert "bar_index" in default["swings"][0]
    assert "timestamp" in default["swings"][0]


def test_research_path_structure_equals_production_serialization_path():
    eng = SignalEngine(SignalConfig())
    candles = _candles()
    as_of = len(candles) - 1
    prod = eng.analyze_timeframe(
        "TESTUSDT", "15m", candles, as_of_index=as_of, serialize_swings=True
    )
    research = eng.analyze_timeframe(
        "TESTUSDT", "15m", candles, as_of_index=as_of, serialize_swings=False
    )
    assert _structure_digest(prod) == _structure_digest(research)
    # Re-serializing research objects must equal production API swings list
    rebuilt = [s.to_dict() for s in research["_swings_objs"]]
    assert rebuilt == prod["swings"]


def test_analyze_strips_private_swings_objs_and_keeps_serialized_swings():
    eng = SignalEngine(SignalConfig())
    candles = _candles()
    result = eng.analyze(
        "TESTUSDT",
        {"15m": candles, "1h": candles, "4h": candles, "5m": candles, "1m": candles},
    )
    payload = result.to_dict()
    assert "_swings_objs" not in payload
    assert isinstance(payload.get("swings"), list)
    # Default production path still serializes swings for API consumers
    if payload["swings"]:
        assert isinstance(payload["swings"][0], dict)
