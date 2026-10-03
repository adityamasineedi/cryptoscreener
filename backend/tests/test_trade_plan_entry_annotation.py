"""Chart trade-plan ENTRY must accompany provisional SL/TP levels."""

from __future__ import annotations

from app.signals.signal_engine import (
    SignalEngine,
    ensure_trade_plan_entry_annotation,
    trade_plan_entry_annotation,
)
from app.signals.config import SignalConfig


def test_provisional_stop_emits_buy_entry_annotation():
    ann = trade_plan_entry_annotation(
        entry={"status": "CONFLICT", "entry_price": None, "entry_type": "WAITING"},
        stop={"entry_price": 1309.96, "final_stop": 1299.65, "provisional": True},
        bos={
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 1309.96,
            "break_timestamp": "2026-10-03T19:45:00+00:00",
        },
        targets=[{"name": "TP1", "target_price": 1336.94}],
    )
    assert ann is not None
    assert ann["kind"] == "BUY"
    assert ann["price"] == 1309.96
    assert ann["direction"] == "LONG"
    assert ann["label"] == "BUY_PROV"


def test_confirmed_entry_price_wins_over_stop():
    ann = trade_plan_entry_annotation(
        entry={
            "entry_price": 1310.5,
            "entry_type": "LIMIT_RETEST",
            "direction": "LONG",
        },
        stop={"entry_price": 1309.96, "final_stop": 1299.65, "provisional": True},
        bos={"direction": "BULLISH_BOS", "broken_level": 1309.96},
        targets=[],
    )
    assert ann is not None
    assert ann["kind"] == "BUY"
    assert ann["price"] == 1310.5
    assert ann["label"] == "LIMIT_RETEST"


def test_engine_annotations_include_entry_with_sl_tp():
    eng = SignalEngine(SignalConfig())
    anns = eng._annotations(
        swings=[],
        bos={
            "state": "CONFIRMED",
            "direction": "BULLISH_BOS",
            "broken_level": 100.0,
            "break_timestamp": "2026-10-03T19:45:00+00:00",
        },
        choch=None,
        impulse=None,
        pullback=None,
        entry={"status": "CONFLICT", "entry_price": None, "entry_type": "WAITING"},
        stop={"entry_price": 100.0, "final_stop": 95.0, "provisional": True},
        targets=[{"name": "TP1", "target_price": 110.0}],
        direction=None,
    )
    kinds = {a["kind"] for a in anns if a.get("group") == "trade_plan"}
    assert "BUY" in kinds
    assert "SL" in kinds
    assert "TAKE_PROFIT" in kinds


def test_ensure_patches_cached_payload_missing_entry():
    payload = {
        "entry": {"entry_price": None, "entry_type": "WAITING"},
        "stop": {"entry_price": 50.0, "final_stop": 45.0, "provisional": True},
        "bos": {"direction": "BEARISH_BOS", "broken_level": 50.0},
        "targets": [{"name": "TP1", "target_price": 40.0}],
        "annotations": [
            {
                "kind": "SL",
                "group": "trade_plan",
                "price": 45.0,
                "time": "2026-10-03T12:00:00+00:00",
                "label": "STOP",
            },
            {
                "kind": "TAKE_PROFIT",
                "group": "trade_plan",
                "price": 40.0,
                "time": "2026-10-03T12:00:00+00:00",
                "label": "TP1",
            },
        ],
    }
    out = ensure_trade_plan_entry_annotation(payload)
    assert out[0]["kind"] == "SELL"
    assert out[0]["price"] == 50.0
    assert out[0]["direction"] == "SHORT"
    assert len(out) == 3
