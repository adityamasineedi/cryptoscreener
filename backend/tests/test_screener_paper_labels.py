"""Display-only audit fixes: screener levels, R:R, v1 status, 15m chance text.

S1  INVALIDATED potential levels are reference-only candidates (prices kept).
S2  The screener R:R field is TP1's R; the cached best_R stays the best target.
S5  A bar the v1 watcher refused after the combo eval is not shown V1 ELIGIBLE.
P1  A READY 15m row does not say "Auto will open ... COMBO_02 v1" while legacy
    auto-entry is off.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.screener_presentation import (
    STATUS_V1_BLOCKED_WATCHER,
    STATUS_V1_ELIGIBLE,
    build_potential_levels,
    classify_v1_status,
)


def _fv(value, status="LIVE"):
    return {"value": value, "status": status}


# --- S1 -----------------------------------------------------------------------


def test_invalidated_levels_are_reference_only_candidates():
    pl = build_potential_levels(
        {
            "setup_signal": _fv("INVALIDATED"),
            "setup_entry": _fv(105.0),
            "setup_sl": _fv(98.0),
            "setup_tp1": _fv(104.0),
            "setup_rr": _fv(4.0),
        }
    )
    assert pl["reference_only"] is True
    assert pl["is_confirmed"] is False
    assert pl["display_mode"] == "candidate"
    # Prices are kept; the R shown is TP1's R from those prices (TP1 is below entry).
    assert (pl["entry"], pl["stop"], pl["tp1"]) == (105.0, 98.0, 104.0)
    assert pl["rr"] == pytest.approx(-1.0 / 7.0)


def test_confirmed_candidate_still_shows_live_levels():
    pl = build_potential_levels(
        {
            "setup_signal": _fv("LONG_ENTRY_CANDIDATE"),
            "setup_entry": _fv(100.0),
            "setup_sl": _fv(98.0),
            "setup_tp1": _fv(104.0),
            "setup_rr": _fv(2.0),
        }
    )
    assert pl["reference_only"] is False
    assert pl["is_confirmed"] is True
    assert pl["rr"] == pytest.approx(2.0)


# --- S2 -----------------------------------------------------------------------


def test_screener_rr_field_is_tp1_r_while_best_r_stays_best_target(monkeypatch):
    from app.services import setup_signals as svc_mod
    from app.services.engine_store import engine_store

    svc = svc_mod.SetupSignalService()
    monkeypatch.setattr(svc, "is_stale", lambda *_a, **_k: False)
    cached = {
        "symbol": "TESTUSDT",
        "status": "LONG_ENTRY_CANDIDATE",
        "signal_status": "LIVE",
        "trend": {},
        "entry": {"entry_price": 100.0, "entry_type": "MARKET"},
        "stop": {"final_stop": 98.0},
        "targets": [
            {"name": "TP1", "target_price": 104.0, "r_multiple": 2.0},
            {"name": "TP2", "target_price": 106.0, "r_multiple": 3.0},
            {"name": "TP3", "target_price": 108.0, "r_multiple": 4.0},
        ],
        "risk_reward": {"TP1_R": 2.0, "TP2_R": 3.0, "TP3_R": 4.0, "best_R": 4.0},
        "market_signal": "BUY",
        "calculated_at": "2026-10-17T05:00:00+00:00",
    }
    monkeypatch.setitem(engine_store.setup_signals, "TESTUSDT", cached)

    fields = svc.screener_fields("TESTUSDT")

    assert fields["setup_rr"].value == pytest.approx(2.0)
    assert f"{fields['setup_rr'].value:.2f}" == "2.00"
    # Gates and research read the best-of-targets value; it is unchanged.
    assert engine_store.setup_signals["TESTUSDT"]["risk_reward"]["best_R"] == 4.0
    assert fields["setup_tp1"].value == 104.0


# --- S5 -----------------------------------------------------------------------


def _eligible_eval() -> dict:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "combination_id": "COMBO_02",
        "gates": {"bos": True, "trend": True, "htf": True},
        "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
        "entry_price": 100.0,
        "stop_price": 98.0,
    }


BAR = "2026-10-17T04:00:00+00:00"


def test_v1_eligible_when_the_watcher_did_not_refuse_the_bar():
    status = classify_v1_status(
        symbol="BTCUSDT",
        snapshot={"eval": _eligible_eval(), "tip_bar": BAR},
    )
    assert status["status"] == STATUS_V1_ELIGIBLE


@pytest.mark.parametrize(
    "reason",
    ["sticky_bos_skip", "exit_bar_no_entry", "late_tip", "paper_open_skipped"],
)
def test_refused_bar_is_blocked_with_the_skip_reason(reason):
    from app.services.v1_paper_watcher import V1PaperWatcher

    watcher = V1PaperWatcher(paper_engine=SimpleNamespace())
    watcher._store_presentation_snapshot("BTCUSDT", eval_result=_eligible_eval(), tip_bar=BAR)  # noqa: SLF001
    watcher._note_skip(  # noqa: SLF001
        "BTCUSDT", f"BTCUSDT:{reason}", BAR, detail="halt_daily_loss" if reason == "paper_open_skipped" else None
    )
    # A re-run of the same bar notes already_processed; the refusal is kept.
    watcher._note_skip("BTCUSDT", "BTCUSDT:already_processed", BAR)  # noqa: SLF001

    snap = watcher.get_presentation_snapshots(refresh=False)["BTCUSDT"]
    status = classify_v1_status(symbol="BTCUSDT", snapshot=snap)

    assert status["status"] == STATUS_V1_BLOCKED_WATCHER
    assert reason in status["label"]
    assert "ELIGIBLE" not in status["label"]
    if reason == "paper_open_skipped":
        assert status["watcher_skip_detail"] == "halt_daily_loss"


def test_refusal_on_an_older_bar_does_not_block_the_new_bar():
    from app.services.v1_paper_watcher import V1PaperWatcher

    watcher = V1PaperWatcher(paper_engine=SimpleNamespace())
    watcher._note_skip("BTCUSDT", "BTCUSDT:sticky_bos_skip", "2026-10-17T03:00:00+00:00")  # noqa: SLF001
    watcher._store_presentation_snapshot("BTCUSDT", eval_result=_eligible_eval(), tip_bar=BAR)  # noqa: SLF001

    snap = watcher.get_presentation_snapshots(refresh=False)["BTCUSDT"]
    assert "watcher_refusal" not in snap
    assert classify_v1_status(symbol="BTCUSDT", snapshot=snap)["status"] == STATUS_V1_ELIGIBLE


# --- P1 -----------------------------------------------------------------------


def _ready_payload() -> dict:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "timeframe": "15m",
        "trend": {"15m": {"trend": "BULLISH"}},
        "bos": {"state": "CONFIRMED", "direction": "BULLISH_BOS"},
        "entry": {"entry_price": 100.0},
        "stop": {"final_stop": 98.0},
        "targets": [{"target_price": 104.0}],
        "risk_reward": {"RISK_REWARD": "PASS"},
    }


@pytest.mark.parametrize("entry_mode", ["path_a", "path_b"])
def test_ready_chance_says_legacy_auto_entry_is_off(monkeypatch, entry_mode):
    import app.services.path_a_paper_validation as pav
    from app.services.paper_trade import _opportunity_from_payload

    monkeypatch.setattr(
        pav, "validate_path_a_paper_entry", lambda *_a, **_k: SimpleNamespace(ok=True)
    )
    off = _opportunity_from_payload(
        "TRXUSDT", _ready_payload(), already_open=False, entry_mode=entry_mode, legacy_opens=False
    )
    on = _opportunity_from_payload(
        "TRXUSDT", _ready_payload(), already_open=False, entry_mode=entry_mode, legacy_opens=True
    )
    # The tier is the same either way; only the words change.
    assert off["tier"] == on["tier"] == "READY"
    assert "Auto will open" not in off["chance"]
    assert "legacy auto-entry is off" in off["chance"]
    assert "Not COMBO_02 v1" in off["chance"]
    assert on["chance"].startswith("Auto will open (15m")
    assert "not COMBO_02 v1" in on["chance"]
    assert "Path A · COMBO_02 v1" not in on["chance"]


def test_list_trade_opportunities_reads_the_legacy_flag(monkeypatch):
    import app.services.paper_trade as paper_mod
    import app.services.path_a_paper_validation as pav
    from app.services.engine_store import engine_store

    monkeypatch.setattr(
        pav, "validate_path_a_paper_entry", lambda *_a, **_k: SimpleNamespace(ok=True)
    )
    eng = SimpleNamespace(
        entry_mode="path_a", enabled=True, legacy_auto_entry_enabled=False, v1_watcher_owns_entries=False
    )
    monkeypatch.setattr(paper_mod, "get_paper_trade_engine", lambda: eng)
    monkeypatch.setattr(engine_store, "setup_signals", {"TRXUSDT": _ready_payload()})

    rows = paper_mod.list_trade_opportunities(limit=10, open_symbols=set())
    assert rows[0]["tier"] == "READY"
    assert "legacy auto-entry is off" in rows[0]["chance"]


def test_already_open_covers_every_stream():
    from app.services.paper_risk import PaperRiskPolicy
    from app.services.paper_trade import PaperPosition, PaperTradeEngine

    eng = PaperTradeEngine(risk_policy=PaperRiskPolicy(enabled=False), v1_profile_enabled=False)
    eng._open[("BTCUSDT", "V1")] = PaperPosition(  # noqa: SLF001
        id="v1", symbol="BTCUSDT", signal_snippet={"source": "V1_PAPER_WATCHER"}
    )
    eng._open[("ETHUSDT", "PASSED|COMBO_02_V2|LONG")] = PaperPosition(  # noqa: SLF001
        id="passed", symbol="ETHUSDT", signal_snippet={"source": "PASSED_CAP_BOOK"}
    )
    # The route passes eng.open_symbols() (no stream), so both rows read "Open".
    assert eng.open_symbols() == {"BTCUSDT", "ETHUSDT"}
    assert eng.open_symbols("LEGACY") == set()
