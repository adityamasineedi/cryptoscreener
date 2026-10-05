"""Strategies catalog API — read-only operator view."""

from app.research.strategy_catalog import build_strategy_catalog


def test_catalog_has_working_combo02_v1():
    cat = build_strategy_catalog()
    assert cat["primary_working"] == "COMBO_02_V1"
    ids = {s["id"] for s in cat["strategies"]}
    assert "COMBO_02_V1" in ids
    assert "COMBO_02_LOCAL" in ids
    assert "COMBO_03" in ids
    assert "COMBO_04" in ids

    v1 = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_V1")
    assert v1["status"] == "WORKING"
    assert "BTCUSDT" in v1["symbols"]["freeze_claim"]
    assert v1["example"]["scenario"]
    assert cat["sizing_example"]["risk_usd"] == 20


def test_catalog_marks_short_and_local_as_non_production():
    cat = build_strategy_catalog()
    short = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_SHORT_RESEARCH")
    local = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_LOCAL")
    assert short["status"] == "RESEARCH"
    assert local["status"] == "BASELINE"
    assert local["tier"] == "research_baseline"
