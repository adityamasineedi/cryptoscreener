from __future__ import annotations

from app.engines.filters.engine import apply_filters, parse_filter_pattern
from app.models.schemas import FreshValue


def test_apply_filters_numeric():
    rows = [
        {"symbol": "BTC", "score": 10},
        {"symbol": "ETH", "score": 5},
    ]
    out = apply_filters(rows, [{"field": "score", "operator": ">=", "value": 8}])
    assert len(out) == 1
    assert out[0]["symbol"] == "BTC"


def test_apply_filters_fresh_value():
    rows = [
        {"symbol": "BTC", "rvol": FreshValue.live(2.0, "test")},
        {"symbol": "ETH", "rvol": FreshValue.live(0.5, "test")},
    ]
    out = apply_filters(rows, [{"field": "rvol", "operator": ">", "value": 1.0}])
    assert len(out) == 1
    assert out[0]["symbol"] == "BTC"


def test_between_and_contains():
    rows = [{"tag": "alpha-beta", "x": 5}, {"tag": "gamma", "x": 15}]
    out = apply_filters(rows, [{"field": "x", "operator": "between", "value": [3, 10]}])
    assert len(out) == 1
    out2 = apply_filters(rows, [{"field": "tag", "operator": "contains", "value": "beta"}])
    assert len(out2) == 1


def test_parse_filter_pattern():
    spec = parse_filter_pattern("score >= 10")
    assert spec == {"field": "score", "operator": ">=", "value": 10}
