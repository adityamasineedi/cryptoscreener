"""COMBO_02 V2.1-A Backtest UI/API registration — research logic reused, V2 untouched."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.backtest_ui_config import (
    COMBINATION_ID_V21A,
    STRATEGY_ID_V21A,
    validate_backtest_request,
)
from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combo02_v2.research_variants import (
    COMBO_V2_FAMILY,
    VARIANT_A,
    route_playbook_variant,
    variant_for_combination_id,
)
from app.research.combo02_v2.router import PLAYBOOK_RANGE, PLAYBOOK_WAIT, route_playbook
from app.research.config import ResearchConfig
from app.research.strategy_catalog import build_strategy_catalog
from app.signals.config import SignalConfig


def _candles(n: int, *, drift: float = 0.2, base: float = 100.0) -> list[dict]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    px = base
    for i in range(n):
        o = px
        c = px + drift
        out.append(
            {
                "time": t0 + timedelta(hours=i),
                "open": o,
                "high": max(o, c) + 0.8,
                "low": min(o, c) - 0.8,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def test_v21a_ui_id_registered_and_maps_to_variant_a():
    c = get_combination(COMBINATION_ID_V21A)
    assert c is not None
    assert c.combination_id == COMBINATION_ID_V21A
    assert COMBINATION_ID_V21A in COMBO_V2_FAMILY
    assert variant_for_combination_id(COMBINATION_ID_V21A) == VARIANT_A
    assert variant_for_combination_id("COMBO_02_V2_1A") == VARIANT_A


def test_v21a_choppy_wait_not_range():
    assert route_playbook_variant("CHOPPY", variant=VARIANT_A) == PLAYBOOK_WAIT
    # Frozen v2 router unchanged
    assert route_playbook("CHOPPY") == PLAYBOOK_RANGE
    assert route_playbook_variant("CHOPPY", variant=None) == PLAYBOOK_RANGE


def test_frozen_v2_definition_unchanged():
    v2 = get_combination("COMBO_02_V2")
    assert v2 is not None
    assert v2.require_bos is False
    assert v2.require_htf_alignment is False
    assert variant_for_combination_id("COMBO_02_V2") is None


def test_validate_backtest_routes_v21a_identity():
    resolved = validate_backtest_request(
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        direction="LONG",
        combination_id=COMBINATION_ID_V21A,
        strategy_id=STRATEGY_ID_V21A,
        combo_version="v2.1-a-choppy-wait",
        research_risk_override=True,
        risk_usd=20.0,
        principal_usd=1000.0,
        allow_short=True,
    )
    assert resolved.combination_id == COMBINATION_ID_V21A
    assert resolved.strategy_id == STRATEGY_ID_V21A
    assert resolved.research_only is True
    assert resolved.production_comparable is False
    assert "non_v1_strategy" in resolved.mismatch_reasons


def test_validate_aliases_research_id_to_ui_id():
    resolved = validate_backtest_request(
        symbols=["BTCUSDT"],
        timeframes=["1h"],
        direction="LONG",
        combination_id="COMBO_02_V2_1A",
        strategy_id="COMBO_02_V2_1A",
        research_risk_override=True,
        risk_usd=20.0,
        principal_usd=1000.0,
        allow_short=True,
    )
    assert resolved.combination_id == COMBINATION_ID_V21A
    assert resolved.strategy_id == STRATEGY_ID_V21A


def test_catalog_lists_v21a_research():
    cat = build_strategy_catalog()
    ids = {s["id"] for s in cat["strategies"]}
    assert STRATEGY_ID_V21A in ids
    item = next(s for s in cat["strategies"] if s["id"] == STRATEGY_ID_V21A)
    assert item["status"] == "RESEARCH"
    assert item["combo_id"] == COMBINATION_ID_V21A
    assert any(w.get("path") == "/backtest" for w in item["where_to_run"])


def test_v21a_and_research_id_same_evaluator_path():
    """UI id and research id both invoke VARIANT_A — no duplicate strategy body."""
    c1h = _candles(120, drift=0.25)
    c15 = []
    for c in c1h:
        for k in range(4):
            c15.append(
                {
                    "time": c["time"] + timedelta(minutes=15 * k),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"] / 4,
                }
            )
    common = dict(
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c1h[::4] or c1h,
        candles_15m=c15,
        direction_filter=None,
        index_start=50,
    )
    a_ui = run_combination_backtest("BTCUSDT", "1h", c1h, COMBINATION_ID_V21A, **common)
    a_research = run_combination_backtest(
        "BTCUSDT", "1h", c1h, "COMBO_02_V2_1A", **common
    )
    assert a_ui.get("status") == a_research.get("status")
    ui_trades = [
        t
        for t in (a_ui.get("trades") or [])
        if t.get("outcome") not in (None, "OPEN")
    ]
    research_trades = [
        t
        for t in (a_research.get("trades") or [])
        if t.get("outcome") not in (None, "OPEN")
    ]
    assert len(ui_trades) == len(research_trades)
    # Frozen V2 still differs from V2.1-A on CHOPPY policy (identity check).
    v2 = run_combination_backtest("BTCUSDT", "1h", c1h, "COMBO_02_V2", **common)
    assert get_combination("COMBO_02_V2").combination_id == "COMBO_02_V2"
    assert v2.get("combination_id") == "COMBO_02_V2"
