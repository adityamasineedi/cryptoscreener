"""NEARUSDT Path A paper trade must not open.

Trade fc0feaa1-7295-4d16-8533-99b77ec55e90 was opened from a WAITING setup
with a stale target and a non-structural stop. The closed-candle evaluator
does not produce an entry. A corrected in-window target is not a fill.
"""

from __future__ import annotations

import pytest

from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.path_a_paper_validation import validate_path_a_paper_entry

NEAR_ID = "fc0feaa1-7295-4d16-8533-99b77ec55e90"

# Closed-candle replay at 2026-10-10 08:00:21Z. 1h is the 07:00Z bar.
NEAR_CLOSED_EVAL = {
    "status": "WAITING",
    "direction": None,
    "timeframe": "15m",
    "trend": {
        "15m": {"trend": "BULLISH"},
        "1h": {"trend": "NEUTRAL"},
        "4h": {"trend": "BULLISH"},
    },
    "mtf": {"MTF_ALIGNMENT": "MIXED"},
    "bos": {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 5.21,
    },
    "impulse": {
        "is_impulse": False,
        "reason": "range 0.98 ATR < 1.5; body_ratio 0.59 < 0.6; RVOL 1.04 < 1.5",
    },
    "pullback": {
        "pullback_state": "WAITING",
        "reason": "Waiting for valid impulse after BOS",
    },
    "entry": {"status": "WAITING"},
    "stop": {
        "final_stop": None,
        "provisional": True,
        "reason": "No structural low for LONG stop",
    },
    "targets": [],
    "risk_reward": {"RISK_REWARD": "WAITING"},
    "window_high": 5.632,
    "window_low": 4.5,
}

NEAR_PAYLOAD = {
    "status": "WAITING",
    "direction": "LONG",
    "timeframe": "15m",
    "trend": {
        "15m": {"trend": "BULLISH"},
        "1h": {"trend": "BULLISH"},
        "4h": {"trend": "BULLISH"},
    },
    "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
    "bos": {
        "state": "CONFIRMED",
        "direction": "BULLISH_BOS",
        "broken_level": 5.21,
    },
    "entry": {},
    "stop": {"final_stop": 5.086, "provisional": True},
    "targets": [{"target_price": 7.102, "target_type": "previous_swing_high"}],
    "risk_reward": {"RISK_REWARD": "PASS"},
    "risk_management": {},
    "source_candle_timestamps": {"15m": "2026-10-10T07:45:00+00:00"},
    "calculated_at": "2026-10-10T08:00:21+00:00",
    "ohlcv_freshness": "OK",
    "strategy_id": "RESEARCH_15M",
    "legacy_entry_mode": "path_a",
}


def _use_near_eval(monkeypatch) -> None:
    import app.services.path_a_paper_validation as validation

    monkeypatch.setattr(
        validation,
        "analyze_closed_path_a",
        lambda _symbol, _payload: dict(NEAR_CLOSED_EVAL),
    )


def test_near_closed_eval_rejects_every_failed_gate():
    gate = validate_path_a_paper_entry(NEAR_PAYLOAD, NEAR_CLOSED_EVAL)
    assert gate.ok is False
    assert gate.entry is None
    assert gate.stop is None
    assert gate.target is None
    for reason in (
        "closed_eval_WAITING",
        "htf_not_bullish",
        "htf_not_strong_long",
        "htf_label_stale",
        "impulse_fail",
        "pullback_not_ready",
        "no_structural_stop",
        "provisional_stop",
        "no_in_window_target",
        "payload_stop_not_structural",
        "payload_target_not_in_closed_eval",
    ):
        assert reason in gate.reasons
    assert gate.diagnostics["payload_target"] == pytest.approx(7.102)
    assert gate.diagnostics["closed_1h"] == "NEUTRAL"
    assert gate.diagnostics["window_high"] == pytest.approx(5.632)


def test_near_paper_signal_does_not_open(monkeypatch):
    import app.services.paper_trade as paper_mod

    _use_near_eval(monkeypatch)
    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 5.21)
    eng = PaperTradeEngine(
        enabled=True,
        entry_mode="path_a",
        starting_equity=1000,
        risk_percent=0.02,
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    eng.legacy_auto_entry_enabled = True
    pos = eng.on_setup_signal("NEARUSDT", NEAR_PAYLOAD)
    assert pos is None
    assert eng.status()["open_count"] == 0
    reason = eng.status()["last_skip_reason"] or ""
    assert "path_a_rejected" in reason
    assert "closed_eval_WAITING" in reason
    assert "no_structural_stop" in reason
    diag = eng.status()["last_path_a_diagnostics"]
    assert diag["payload_target"] == pytest.approx(7.102)
    assert diag["closed_target"] is None
    assert NEAR_ID not in reason


def test_corrected_in_window_target_still_does_not_open(monkeypatch):
    """5.554 is the in-window swing. The setup is still WAITING, so no fill."""
    import app.services.paper_trade as paper_mod

    _use_near_eval(monkeypatch)
    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 5.21)
    corrected = dict(NEAR_PAYLOAD)
    corrected["targets"] = [{"target_price": 5.554, "target_type": "previous_swing_high"}]
    corrected["stop"] = {"final_stop": 5.086, "provisional": False}
    eng = PaperTradeEngine(
        enabled=True,
        entry_mode="path_a",
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    eng.legacy_auto_entry_enabled = True
    assert eng.on_setup_signal("NEARUSDT", corrected) is None
    assert eng.status()["open_count"] == 0
    assert "closed_eval_WAITING" in (eng.status()["last_skip_reason"] or "")


def test_target_mismatch_does_not_replace_the_payload_target():
    closed = {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": "BULLISH"},
            "4h": {"trend": "BULLISH"},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
        "impulse": {"is_impulse": True},
        "pullback": {"pullback_state": "CONFIRMED"},
        "entry": {"entry_price": 5.21},
        "stop": {"final_stop": 5.086, "provisional": False, "reason": "pullback low"},
        "targets": [{"target_price": 5.554, "target_type": "previous_swing_high"}],
        "window_high": 5.632,
    }
    payload = dict(NEAR_PAYLOAD)
    payload["status"] = "LONG_ENTRY_CANDIDATE"
    payload["entry"] = {"entry_price": 5.21}
    gate = validate_path_a_paper_entry(payload, closed)
    assert gate.ok is False
    assert "target_mismatch" in gate.reasons
    assert gate.target is None


def test_out_of_window_swing_target_is_rejected():
    payload = {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": "BULLISH"},
            "4h": {"trend": "BULLISH"},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
        "impulse": {"is_impulse": True},
        "pullback": {"pullback_state": "CONFIRMED"},
        "entry": {"entry_price": 5.21},
        "stop": {"final_stop": 5.086, "provisional": False, "reason": "pullback low"},
        "targets": [{"target_price": 7.102, "target_type": "previous_swing_high"}],
    }
    closed = dict(payload)
    closed["window_high"] = 5.632
    gate = validate_path_a_paper_entry(payload, closed)
    assert gate.ok is False
    assert "target_outside_window" in gate.reasons


def test_path_a_sizes_from_all_closed_pnl(monkeypatch):
    import app.services.paper_trade as paper_mod

    seen: dict[str, float] = {}
    real = paper_mod.size_paper_long

    def _capture(**kwargs):
        seen["equity"] = kwargs["account_equity"]
        return real(**kwargs)

    monkeypatch.setattr(paper_mod, "size_paper_long", _capture)
    monkeypatch.setattr(paper_mod, "_live_price", lambda _s: 100.0)
    eng = PaperTradeEngine(
        enabled=True,
        entry_mode="path_a",
        starting_equity=1000,
        risk_percent=0.02,
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    eng.legacy_auto_entry_enabled = True
    eng.hydrate_from_rows(
        [
            {
                "id": "near-loss-1",
                "symbol": "NEARUSDT",
                "status": "CLOSED",
                "entry_price": 5.0,
                "stop_price": 4.828,
                "pnl_usd": -20.426,
                "opened_at": "2026-10-09T08:00:00+00:00",
                "closed_at": "2026-10-09T12:00:00+00:00",
                "timeframe": "15m",
                "signal_snippet": {},
            }
        ],
        closed_pnl_total=-42.6309,
    )
    assert eng.equity == pytest.approx(1000 - 42.6309)
    payload = {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {
            "15m": {"trend": "BULLISH"},
            "1h": {"trend": "BULLISH"},
            "4h": {"trend": "BULLISH"},
        },
        "mtf": {"MTF_ALIGNMENT": "STRONG_LONG"},
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS", "broken_level": 100.0},
        "impulse": {"is_impulse": True},
        "pullback": {"pullback_state": "CONFIRMED"},
        "entry": {"entry_price": 100.0},
        "stop": {"final_stop": 98.0, "provisional": False, "reason": "structural pullback low"},
        "targets": [{"target_price": 104.0, "target_type": "previous_swing_high"}],
        "risk_reward": {"RISK_REWARD": "PASS"},
        "ohlcv_freshness": "OK",
        "source_candle_timestamps": {"15m": "2026-10-10T07:45:00+00:00"},
    }
    pos = eng.on_setup_signal("BTCUSDT", payload)
    assert pos is not None
    assert seen["equity"] == pytest.approx(1000 - 42.6309)
