from __future__ import annotations

from app.config import Settings
from app.engines.filters.engine import apply_filters
from app.services.presets import get_preset, list_presets


def test_list_presets_from_yaml():
    settings = Settings()
    presets = list_presets(settings)
    ids = {p["id"] for p in presets}
    assert "large_cap" in ids
    assert "mid_cap" in ids
    assert "small_cap" in ids
    assert "custom" in ids
    # Never hardcoded coin lists
    for p in presets:
        assert "coins" not in p
        assert "symbols" not in p
        assert isinstance(p["filters"], list)


def test_large_cap_filter_config():
    settings = Settings()
    p = get_preset(settings, "large_cap")
    assert p is not None
    rows = [
        {"symbol": "BTC", "market_cap": {"value": 1.2e12, "status": "LIVE"}},
        {"symbol": "TINY", "market_cap": {"value": 5e7, "status": "LIVE"}},
        {"symbol": "NONE", "market_cap": {"value": None, "status": "WAITING"}},
    ]
    kept = apply_filters(rows, p["filters"])
    assert [r["symbol"] for r in kept] == ["BTC"]


def test_custom_preset_empty_filters():
    settings = Settings()
    p = get_preset(settings, "custom")
    assert p is not None
    assert p["filters"] == []
