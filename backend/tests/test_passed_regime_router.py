from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.research import passed_regime_router as router
from app.research.passed_cap_book import PASSED_SIDES
from app.research.passed_regime_router import (
    TRENDS,
    allowed_passed_sides,
    is_side_allowed,
    normalize_trend,
    route_passed_book,
    skipped_passed_sides,
)

EXPECTED_LONG = (("COMBO_03_TRANSITION_B", "1h", "LONG"),)
EXPECTED_SHORT = (
    ("COMBO_02_V2", "1h", "SHORT"),
    ("COMBO_02", "1h", "SHORT"),
    ("COMBO_02_CLOSED_HTF", "1h", "SHORT"),
    ("COMBO_03_TRANSITION", "1h", "SHORT"),
    ("COMBO_03_TRANSITION_B", "1h", "SHORT"),
    ("COMBO_01", "1h", "SHORT"),
    ("COMBO_02_LOCAL", "1h", "SHORT"),
)
UNKNOWN_TRENDS = [None, "", "   ", "SIDEWAYS", "BULL", "bullish-ish", 0, 1, True, [], {}, b"BULLISH"]
ANY_TREND = list(TRENDS) + UNKNOWN_TRENDS


def _by_direction(direction: str) -> tuple[tuple[str, str, str], ...]:
    return tuple(side for side in PASSED_SIDES if side[2] == direction)


def test_trends_constant() -> None:
    assert TRENDS == ("BULLISH", "BEARISH", "NEUTRAL")


def test_bullish_allows_only_long_rows() -> None:
    assert allowed_passed_sides("BULLISH") == EXPECTED_LONG
    assert allowed_passed_sides("BULLISH") == _by_direction("LONG")
    assert skipped_passed_sides("BULLISH") == _by_direction("SHORT")


def test_bearish_allows_only_short_rows() -> None:
    assert allowed_passed_sides("BEARISH") == EXPECTED_SHORT
    assert allowed_passed_sides("BEARISH") == _by_direction("SHORT")
    assert skipped_passed_sides("BEARISH") == _by_direction("LONG")


def test_neutral_allows_nothing() -> None:
    assert allowed_passed_sides("NEUTRAL") == ()
    assert skipped_passed_sides("NEUTRAL") == PASSED_SIDES


@pytest.mark.parametrize("trend", UNKNOWN_TRENDS)
def test_unknown_trend_fails_closed(trend: object) -> None:
    assert normalize_trend(trend) == "NEUTRAL"
    assert allowed_passed_sides(trend) == ()
    assert skipped_passed_sides(trend) == PASSED_SIDES
    assert route_passed_book(trend)["trend"] == "NEUTRAL"
    for combination_id, timeframe, direction in PASSED_SIDES:
        assert is_side_allowed(trend, combination_id, timeframe, direction) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bullish", "BULLISH"),
        ("  Bearish \n", "BEARISH"),
        ("neutral", "NEUTRAL"),
        ("BULLISH", "BULLISH"),
    ],
)
def test_trend_case_and_whitespace(raw: str, expected: str) -> None:
    assert normalize_trend(raw) == expected
    assert allowed_passed_sides(raw) == allowed_passed_sides(expected)
    assert route_passed_book(raw) == route_passed_book(expected)


@pytest.mark.parametrize("trend", ANY_TREND)
def test_unknown_combo_rejected_under_every_trend(trend: object) -> None:
    assert is_side_allowed(trend, "COMBO_SD_F", "1h", "SHORT") is False
    assert is_side_allowed(trend, "COMBO_SD_F", "1h", "LONG") is False
    assert is_side_allowed(trend, "", "1h", "SHORT") is False
    assert is_side_allowed(trend, None, None, None) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("trend", ANY_TREND)
def test_other_side_of_passed_combo_rejected(trend: object) -> None:
    assert is_side_allowed(trend, "COMBO_02_V2", "1h", "LONG") is False
    assert is_side_allowed(trend, "COMBO_01", "1h", "LONG") is False


@pytest.mark.parametrize("trend", ANY_TREND)
def test_passed_combo_on_other_timeframe_rejected(trend: object) -> None:
    assert is_side_allowed(trend, "COMBO_02_V2", "4h", "SHORT") is False
    assert is_side_allowed(trend, "COMBO_03_TRANSITION_B", "4h", "LONG") is False
    assert is_side_allowed(trend, "COMBO_03_TRANSITION_B", "15m", "SHORT") is False


def test_is_side_allowed_follows_trend() -> None:
    assert is_side_allowed("BULLISH", "COMBO_03_TRANSITION_B", "1h", "LONG") is True
    assert is_side_allowed("BULLISH", "COMBO_03_TRANSITION_B", "1h", "SHORT") is False
    assert is_side_allowed("BEARISH", "COMBO_03_TRANSITION_B", "1h", "SHORT") is True
    assert is_side_allowed("BEARISH", "COMBO_03_TRANSITION_B", "1h", "LONG") is False
    assert is_side_allowed("BEARISH", "COMBO_02_V2", "1h", "SHORT") is True
    assert is_side_allowed("BULLISH", "COMBO_02_V2", "1h", "SHORT") is False
    assert is_side_allowed("NEUTRAL", "COMBO_02_V2", "1h", "SHORT") is False
    assert is_side_allowed("NEUTRAL", "COMBO_03_TRANSITION_B", "1h", "LONG") is False


def test_is_side_allowed_normalises_like_is_passed_side() -> None:
    assert is_side_allowed("bearish", "COMBO_02_V2", "1H", "short") is True
    assert is_side_allowed("bullish", "COMBO_03_TRANSITION_B", "1H", "long") is True
    # combination_id is case-sensitive, as in is_passed_side
    assert is_side_allowed("BEARISH", "combo_02_v2", "1h", "SHORT") is False


@pytest.mark.parametrize("trend", ANY_TREND)
def test_is_side_allowed_matches_allowed_rows(trend: object) -> None:
    allowed = allowed_passed_sides(trend)
    for side in PASSED_SIDES:
        assert is_side_allowed(trend, *side) is (side in allowed)


@pytest.mark.parametrize("trend", ANY_TREND)
def test_allowed_and_skipped_partition_the_book_in_order(trend: object) -> None:
    allowed = allowed_passed_sides(trend)
    skipped = skipped_passed_sides(trend)
    assert set(allowed).isdisjoint(skipped)
    assert len(allowed) + len(skipped) == len(PASSED_SIDES)
    assert set(allowed) | set(skipped) == set(PASSED_SIDES)
    assert set(allowed) <= set(PASSED_SIDES)
    order = {side: index for index, side in enumerate(PASSED_SIDES)}
    assert [order[side] for side in allowed] == sorted(order[side] for side in allowed)
    assert [order[side] for side in skipped] == sorted(order[side] for side in skipped)


def test_route_passed_book_shape() -> None:
    bullish = route_passed_book("BULLISH")
    assert bullish == {
        "trend": "BULLISH",
        "allowed": [
            {"strategy_id": "COMBO_03_TRANSITION_B", "timeframe": "1h", "direction": "LONG"}
        ],
        "skipped": [
            {"strategy_id": cid, "timeframe": tf, "direction": side}
            for cid, tf, side in EXPECTED_SHORT
        ],
    }
    bearish = route_passed_book("BEARISH")
    assert bearish["trend"] == "BEARISH"
    assert bearish["allowed"] == bullish["skipped"]
    assert bearish["skipped"] == bullish["allowed"]
    neutral = route_passed_book("NEUTRAL")
    assert neutral["allowed"] == []
    assert [(r["strategy_id"], r["timeframe"], r["direction"]) for r in neutral["skipped"]] == list(
        PASSED_SIDES
    )
    for trend in TRENDS:
        routed = route_passed_book(trend)
        assert json.loads(json.dumps(routed)) == routed


def test_router_is_pure() -> None:
    before = tuple(PASSED_SIDES)
    for trend in ANY_TREND:
        assert allowed_passed_sides(trend) == allowed_passed_sides(trend)
        assert skipped_passed_sides(trend) == skipped_passed_sides(trend)
        first = route_passed_book(trend)
        first["allowed"].append({"strategy_id": "X", "timeframe": "1h", "direction": "LONG"})
        first["skipped"].clear()
        second = route_passed_book(trend)
        assert second == route_passed_book(trend)
        assert {"strategy_id": "X", "timeframe": "1h", "direction": "LONG"} not in second["allowed"]
    assert PASSED_SIDES == before
    assert router.PASSED_SIDES is PASSED_SIDES


def test_module_imports_no_io_and_hard_codes_no_strategy() -> None:
    source = Path(router.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert sorted(imported) == ["__future__", "app.research.passed_cap_book"]
    for name in ("open(", "print(", "logging", "getLogger"):
        assert name not in source
    assert "COMBO_" not in source
