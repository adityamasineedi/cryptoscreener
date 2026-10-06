"""Strategies catalog API — read-only operator view."""

from app.research.strategy_catalog import build_strategy_catalog


def test_catalog_has_working_combo02_v1():
    cat = build_strategy_catalog()
    assert cat["primary_working"] == "COMBO_02_V1"
    ids = {s["id"] for s in cat["strategies"]}
    assert "COMBO_02_V1" in ids
    assert "COMBO_02_V1_1_RISK_CONTROLLED" in ids
    assert "COMBO_02_V1_CLOSED_HTF" in ids
    assert "COMBO_02_V2_1_A" in ids
    assert "COMBO_02_LOCAL" in ids
    assert "COMBO_03" in ids
    assert "COMBO_04" in ids

    v21a = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_V2_1_A")
    assert v21a["status"] == "RESEARCH"
    assert v21a["combo_id"] == "COMBO_02_V2_1_A"

    v1 = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_V1")
    assert v1["status"] == "WORKING"
    assert "BTCUSDT" in v1["symbols"]["freeze_claim"]
    assert v1["example"]["scenario"]
    assert cat["sizing_example"]["risk_usd"] == 20

    closed = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_V1_CLOSED_HTF")
    assert closed["status"] == "RESEARCH"
    assert closed["combo_id"] == "COMBO_02_CLOSED_HTF"
    assert closed["parent_strategy_id"] == "COMBO_02_V1"

    v11 = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_V1_1_RISK_CONTROLLED")
    assert v11["status"] == "RESEARCH"
    assert v11["parent_strategy_id"] == "COMBO_02_V1"
    assert v11["risk"]["risk_percent_by_symbol"]["BTCUSDT"] == 0.015
    assert "BTC 1.5%" in v11["risk"]["display"]
    assert v11["risk"]["example_risk_usd"] == 15
    assert all(link.get("kind") == "repo_path" for link in v11["where_to_run"])


def test_catalog_marks_short_and_local_as_non_production():
    cat = build_strategy_catalog()
    short = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_SHORT_RESEARCH")
    local = next(s for s in cat["strategies"] if s["id"] == "COMBO_02_LOCAL")
    assert short["status"] == "RESEARCH"
    assert local["status"] == "BASELINE"
    assert local["tier"] == "research_baseline"
