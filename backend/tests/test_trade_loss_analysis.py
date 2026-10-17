"""Loss analysis for a finished Backtest cell: reasons, flags, hindsight, what-ifs, endpoint.

Synthetic hourly candles: a default bar is open 100 / high 100.5 / low 99.5 /
close 100, so Wilder ATR(14) is exactly 1.0. Trades are LONG at 100 with a
2.0 stop distance (stop 98, 2 ATR) and TP1 at 104 (2R) unless a test says
otherwise. Stored trade dollars use the same fee model the what-ifs use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.research import trade_loss_analysis as tla

T0 = datetime(2025, 1, 6, 0, 0, tzinfo=timezone.utc)  # a Monday
FEE = 0.001


def _t(i: int) -> str:
    return (T0 + timedelta(hours=i)).isoformat()


def _candles(n: int = 120, **overrides: dict[str, float]) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        bar = {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}
        bar.update(overrides.get(f"b{i}", {}))
        out.append({"time": _t(i), "volume": 1.0, **bar})
    return out


def _trade(
    no: int,
    entry_i: int,
    exit_i: int,
    exit_raw: float,
    *,
    outcome: str = "SL",
    entry: float = 100.0,
    stop: float = 98.0,
    tp1: float | None = 104.0,
    risk: float = 20.0,
    exit_slip: float = 0.0,
    context: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    qty = risk / abs(entry - stop)
    fill = exit_raw * (1.0 - exit_slip)
    gross = qty * (fill - entry)
    fees = -(qty * entry * FEE + qty * fill * FEE)
    t = {
        "trade_no": no,
        "trade_id": f"t{no}",
        "direction": "LONG",
        "signal_time": _t(entry_i),
        "entry_time": _t(entry_i),
        "exit_time": _t(exit_i),
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "tp2": None,
        "tp3": None,
        "exit_price": fill,
        "outcome": outcome,
        "qty": qty,
        "risk_usd": risk,
        "requested_risk_usd": risk,
        "max_leverage_quantity": 1e9,
        "fee_entry_rate": FEE,
        "fee_exit_rate": FEE,
        "slippage_rate": 0.0,
        "gross_pnl_usd": gross,
        "fee_total_usd": fees,
        "net_pnl_usd": gross + fees,
        "r_net": (gross + fees) / risk,
        "market_context": context
        if context is not None
        else {"structure": "CORRECT", "trend": "BULLISH", "range_position": 0.3, "decision_bar": _t(entry_i - 1)},
    }
    if exit_slip:
        t["raw_exit_price"] = exit_raw
    t.update(extra)
    return t


def _row(trades: list[dict[str, Any]], *, sid: str = "CANDLE12_V2", symbol: str = "ETHUSDT", n: int = 120, family: str = "candle12") -> dict[str, Any]:
    return {
        "combination_id": sid,
        "symbol": symbol,
        "timeframe": "1h",
        "direction": "LONG",
        "engine_family": family,
        "period_start": _t(15),
        "period_end": _t(n - 1),
        "trades": trades,
    }


def _one(trade: dict[str, Any], candles: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    out = tla.analyze_row(_row([trade], n=len(candles)), candles, **kw)
    assert len(out["losses"]) == 1
    return out["losses"][0]


# --------------------------------------------------------------------------- primary reasons


def test_stop_before_progress():
    c = _candles(b23={"low": 97.9})
    rec = _one(_trade(1, 20, 23, 98.0), c)
    assert rec["primary_reason"] == "STOP_BEFORE_PROGRESS"
    m = rec["metrics"]
    assert m["hold_bars"] == 3
    assert m["mfe_r_before_exit"] == pytest.approx(0.25)
    assert m["mfe_pct_to_tp1"] == pytest.approx(12.5)
    assert m["mae_r"] == pytest.approx(1.05)
    assert m["tp1_distance_r"] == pytest.approx(2.0)
    assert m["stop_distance_atr"] == pytest.approx(2.0)
    assert m["stop_distance_pct"] == pytest.approx(2.0)
    assert m["fees_r"] == pytest.approx(abs(rec["fees_usd"]) / 20.0, abs=1e-3)
    assert "Stopped 3 bars in after reaching only 12% of the way to TP1" in rec["explanation"]


def test_gave_back_with_hindsight_stop_needed():
    c = _candles(b22={"high": 102.5}, b25={"low": 97.5}, b30={"high": 104.5})
    rec = _one(_trade(1, 20, 25, 98.0), c)
    assert rec["primary_reason"] == "GAVE_BACK"
    assert rec["metrics"]["mfe_pct_to_tp1"] == pytest.approx(62.5)
    assert rec["metrics"]["bars_to_mfe"] == 2
    h = rec["hindsight"]
    assert h["reached_tp1_after_exit"] is True
    assert h["bars_after_exit"] == 5
    assert h["stop_needed_r"] == pytest.approx(1.25)  # worst low 97.5 before TP1, R = 2
    assert h["max_favourable_after_exit_r"] == pytest.approx(2.25)
    assert "reached TP1 5 bars later" in rec["explanation"]
    assert "stop wider than 1.2R" in rec["explanation"] or "stop wider than 1.3R" in rec["explanation"]


def _mc_cell(loser_ctx: dict[str, Any], winner_ctx: dict[str, Any] | None = None, *, family: str = "multicap",
             sid: str = "LARGE_CAP_SWEEP_CHOCH", extra_loser: bool = False) -> dict[str, Any]:
    """One loser entered at bar 20 (stopped bar 23) plus two TP1 winners (bars 40->43, 60->63)."""
    good = winner_ctx or {"structure": "CORRECT", "trend": "BULLISH", "range_position": 0.3, "decision_bar": _t(39)}
    c = _candles(b23={"low": 97.9}, b43={"high": 104.2}, b63={"high": 104.2}, b83={"low": 97.9})
    trades = [
        _trade(1, 20, 23, 98.0, context=loser_ctx),
        _trade(2, 40, 43, 104.0, outcome="TP1", context=dict(good)),
        _trade(3, 60, 63, 104.0, outcome="TP1", context=dict(good)),
    ]
    if extra_loser:
        trades.append(_trade(4, 80, 83, 98.0, context=dict(good)))
    return tla.analyze_row(_row(trades, sid=sid, family=family), c)


def test_against_context_primary_when_it_discriminates():
    ctx = {"structure": "WRONG", "trend": "BEARISH", "range_position": 0.9, "decision_bar": _t(20)}
    out = _mc_cell(ctx)
    rec = out["losses"][0]
    assert rec["primary_reason"] == "AGAINST_CONTEXT"
    assert {"AGAINST_CONTEXT", "WRONG_RANGE_HALF", "STOP_BEFORE_PROGRESS"} <= set(rec["flags"])
    assert rec["explanation"].startswith("Entered LONG against the downtrend (structure WRONG)")
    assert out["summary"]["non_discriminating_flags"] == []


def test_wrong_range_half_primary_when_concentrated_in_losers():
    ctx = {"structure": "CORRECT", "trend": "BULLISH", "range_position": 0.8, "decision_bar": _t(20)}
    out = _mc_cell(ctx, extra_loser=True)
    by_no = {r["trade_no"]: r for r in out["losses"]}
    assert by_no[1]["primary_reason"] == "WRONG_RANGE_HALF"  # losers 50%, winners 0%, 25% of trades
    assert by_no[4]["primary_reason"] == "STOP_BEFORE_PROGRESS"
    assert "80% of the prior 24-bar range (upper half)" in by_no[1]["explanation"]
    w = _wi(out, "skip_wrong_range_half")
    assert w["discriminating"] is True and w["losers_rate"] == pytest.approx(0.5)


def test_flag_on_all_trades_never_primary():
    hot = {"structure": "CORRECT", "trend": "BULLISH", "range_position": 0.9, "decision_bar": _t(20)}
    out = _mc_cell(hot, winner_ctx={**hot, "decision_bar": _t(39)})
    rec = out["losses"][0]
    assert "WRONG_RANGE_HALF" in rec["flags"]
    assert rec["primary_reason"] == "STOP_BEFORE_PROGRESS"
    nd = {f["flag"]: f for f in out["summary"]["non_discriminating_flags"]}
    assert nd["WRONG_RANGE_HALF"]["winners_rate"] == pytest.approx(1.0)
    assert nd["WRONG_RANGE_HALF"]["losers_rate"] == pytest.approx(1.0)
    assert "100% of winners" in nd["WRONG_RANGE_HALF"]["reason"]
    assert "of all trades" in nd["WRONG_RANGE_HALF"]["reason"]
    assert all(s.get("what_if_id") != "skip_wrong_range_half" for s in out["suggestions"])
    assert _wi(out, "skip_wrong_range_half")["discriminating"] is False


def test_post_fill_context_never_primary():
    # CANDLE12_V2 stamps market_context on Candle-2 (the fill bar): never primary,
    # even when the flag is concentrated in losers.
    ctx = {"structure": "WRONG", "trend": "BEARISH", "range_position": 0.9, "decision_bar": _t(20)}
    out = _mc_cell(ctx, family="candle12", sid="CANDLE12_V2")
    rec = out["losses"][0]
    assert "AGAINST_CONTEXT" in rec["flags"]
    assert rec["primary_reason"] == "STOP_BEFORE_PROGRESS"
    assert out["summary"]["context_read_after_fill"] is True
    nd = {f["flag"]: f for f in out["summary"]["non_discriminating_flags"]}
    assert "context read after the fill" in nd["AGAINST_CONTEXT"]["reason"]
    assert all(not str(s.get("what_if_id", "")).startswith("skip_") for s in out["suggestions"])
    # Another engine counts as post-fill when the decision bar is the intrabar-filled entry bar.
    ose = _mc_cell(ctx, family="ohlcv_structure_experiment", sid="OSE_A_corrected_baseline")
    assert ose["summary"]["context_read_after_fill"] is True
    assert ose["losses"][0]["primary_reason"] != "AGAINST_CONTEXT"


def test_late_stop():
    c = _candles(b21={"high": 101.0}, b36={"low": 97.9})
    rec = _one(_trade(1, 20, 36, 98.0), c)
    assert rec["primary_reason"] == "LATE_STOP"
    assert rec["metrics"]["bars_to_mfe"] == 1
    assert "drifted 15 bars to the stop" in rec["explanation"]


def test_fees_flipped_in_both_groups():
    c = _candles()
    t = _trade(1, 20, 30, 100.05, outcome="TIMEOUT")
    assert t["gross_pnl_usd"] > 0 > t["net_pnl_usd"]
    out = tla.analyze_row(_row([t]), c)
    assert out["losses"][0]["primary_reason"] == "FEES_FLIPPED"
    assert [r["trade_no"] for r in out["fee_flipped"]] == [1]
    assert "in fees" in out["losses"][0]["explanation"]


def test_insufficient_data_when_bars_do_not_map():
    c = _candles()
    t = _trade(1, 20, 23, 98.0, tp1=None)
    t["exit_time"] = (T0 + timedelta(days=400)).isoformat()
    out = tla.analyze_row(_row([t]), c)
    rec = out["losses"][0]
    assert rec["primary_reason"] == "INSUFFICIENT_DATA"
    assert rec["metrics"]["hold_bars"] is None
    assert out["summary"]["trades_unmatched_to_candles"] == 1


def test_time_mapping_tolerates_mid_bar_and_z_suffix():
    c = _candles(b23={"low": 97.9})
    t = _trade(1, 20, 23, 98.0)
    t["entry_time"] = (T0 + timedelta(hours=20, minutes=30)).isoformat().replace("+00:00", "Z")
    t["signal_time"] = t["entry_time"]
    rec = _one(t, c)
    assert rec["metrics"]["hold_bars"] == 3


# --------------------------------------------------------------------------- flags


def test_chased_tight_wide_and_gap_flags():
    # Chase: signal bar (entry-1) closes at 100, fill 100.6 with a 2.0 stop -> 0.6 > 0.5.
    c = _candles(b23={"low": 97.9})
    rec = _one(_trade(1, 20, 23, 98.6, entry=100.6, stop=98.6, tp1=104.6), c)
    assert "CHASED_ENTRY" in rec["flags"]
    # Tight: stop distance 0.5 < 1 ATR.
    c = _candles(b23={"low": 99.4})
    rec = _one(_trade(1, 20, 23, 99.5, stop=99.5, tp1=101.0), c)
    assert "TIGHT_STOP" in rec["flags"] and "WIDE_STOP" not in rec["flags"]
    # Wide: stop distance 4 > 3 ATR.
    c = _candles(b23={"low": 95.9})
    rec = _one(_trade(1, 20, 23, 96.0, stop=96.0, tp1=108.0), c)
    assert "WIDE_STOP" in rec["flags"]
    # Gap through the stop: the stop bar opens below the stop.
    c = _candles(b23={"open": 97.5, "low": 97.0, "high": 97.8, "close": 97.6})
    rec = _one(_trade(1, 20, 23, 98.0, tags=["GAP_STOP"]), c)
    assert "GAP_THROUGH_STOP" in rec["flags"] and "GAP_EXIT" in rec["flags"]


# --------------------------------------------------------------------------- what-ifs


def _whatif_row(symbol: str = "ETHUSDT") -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    # A: stopped at bar 22 (low 97.5) then TP1 touched at bar 26; exits carry 0.02% slippage.
    # B: drifts from bar 40 and is stopped at bar 60.
    c = _candles(b22={"low": 97.5}, b26={"high": 104.2}, b60={"low": 97.9})
    a = _trade(1, 20, 22, 98.0, exit_slip=0.0002)
    b = _trade(2, 40, 60, 98.0)
    return _row([a, b], symbol=symbol), c, a, b


def _wi(out: dict[str, Any], wid: str) -> dict[str, Any]:
    return next(w for w in out["what_ifs"] if w["id"] == wid)


def test_replay_reproduces_stored_trades():
    row, c, _a, _b = _whatif_row()
    out = tla.analyze_row(row, c)
    assert out["replay_check"]["replayed_exactly"] == 2


def test_what_if_stop_x1_5_hand_computed():
    row, c, a, b = _whatif_row()
    out = tla.analyze_row(row, c)
    w = _wi(out, "stop_x1_5")
    assert w["computable"] is True
    # A: stop 97 (R=3) survives 97.5, exits at TP1 104 less 0.02% slippage; qty = 20/3.
    qty = 20.0 / 3.0
    fill = 104.0 * (1 - 0.0002)
    a_new = qty * (fill - 100.0) - (qty * 100.0 * FEE + qty * fill * FEE)
    # B: stop 97 never touched (low 97.9); still open at the last bar -> marked at its close (100).
    b_new = qty * (100.0 - 100.0) - (qty * 100.0 * FEE + qty * 100.0 * FEE)
    actual = a["net_pnl_usd"] + b["net_pnl_usd"]
    assert w["net_usd"] == pytest.approx(a_new + b_new, abs=0.01)
    assert w["delta_usd"] == pytest.approx(a_new + b_new - actual, abs=0.01)
    assert w["losers_to_winners"] == 1
    assert w["winners_lost"] == 0
    assert w["open_at_end"] == 1
    assert "in-sample" in w["caveat"].lower() and "one-position lock" in w["caveat"]


def test_what_if_time_stop_hand_computed():
    row, c, a, b = _whatif_row()
    out = tla.analyze_row(row, c)
    w12 = _wi(out, "time_stop_12")
    # A is stopped on bar 22, inside 12 bars: unchanged. B exits at the close of bar 51 (= 100).
    b_new = 10.0 * 0.0 - (10.0 * 100.0 * FEE * 2)
    assert w12["net_usd"] == pytest.approx(a["net_pnl_usd"] + b_new, abs=0.01)
    assert w12["delta_usd"] == pytest.approx(b_new - b["net_pnl_usd"], abs=0.01)
    assert w12["trades_changed"] == 1
    # 24 bars: B's last bar is 63, the stop at bar 60 comes first -> nothing changes.
    assert _wi(out, "time_stop_24")["delta_usd"] == pytest.approx(0.0, abs=0.01)


def test_skip_what_ifs_and_suggestion_order():
    row, c, a, b = _whatif_row()
    row["trades"][1]["market_context"] = {"structure": "WRONG", "trend": "BEARISH", "range_position": 0.2, "decision_bar": _t(39)}
    out = tla.analyze_row(row, c)
    w = _wi(out, "skip_against_context")
    assert w["trades"] == 1 and w["trades_skipped"] == 1
    assert w["delta_usd"] == pytest.approx(-b["net_pnl_usd"], abs=0.01)
    assert out["frozen_book"] is False
    deltas = [s["net_change_usd"] for s in out["suggestions"]]
    assert deltas == sorted(deltas, reverse=True) and all(d > 0 for d in deltas)
    assert all(s["label"] == "research only" for s in out["suggestions"])
    assert {"rule", "reason_bucket", "trades_affected", "net_change_usd", "winners_lost", "label"} <= set(out["suggestions"][0])


def test_frozen_candle12_v2_btc_1h_long_has_no_suggestions():
    row, c, _a, _b = _whatif_row(symbol="BTCUSDT")
    out = tla.analyze_row(row, c)
    assert out["frozen_book"] is True
    assert out["suggestions"] == []
    assert out["suggestions_note"] == (
        "Frozen BTC 1h CANDLE12_V2 book — diagnostics only; no rule change is suggested."
    )
    assert out["losses"]  # diagnostics still returned
    assert any(w["computable"] for w in out["what_ifs"])
    # Same trades on another symbol do produce suggestions.
    row2, c2, _a, _b = _whatif_row(symbol="ETHUSDT")
    assert tla.analyze_row(row2, c2)["suggestions"]


def test_candle12_v3_rewalks_not_computable():
    row, c, _a, _b = _whatif_row()
    row["combination_id"] = "CANDLE12_V3_EXEC_FIX"
    row["engine_family"] = "candle12_v3"
    out = tla.analyze_row(row, c)
    for wid in ("stop_x1_5", "stop_x2", "time_stop_12", "time_stop_24", "time_stop_48"):
        w = _wi(out, wid)
        assert w["computable"] is False and w["net_usd"] is None and "multi-leg" in w["caveat"]
    assert _wi(out, "skip_against_context")["computable"] is True


def test_multicap_walk_starts_after_entry_bar():
    # Entry at bar 20's close; bar 20's own low (97.0) must not stop the walk.
    c = _candles(b20={"low": 97.0}, b24={"low": 97.9})
    t = _trade(1, 20, 24, 98.0)
    out = tla.analyze_row(_row([t], sid="LARGE_CAP_SWEEP_CHOCH", family="multicap"), c)
    assert out["replay_check"]["replayed_exactly"] == 1
    assert out["losses"][0]["metrics"]["mae_r"] == pytest.approx(1.05)


def test_summary_contract_keys():
    row, c, _a, _b = _whatif_row()
    out = tla.analyze_row(row, c)
    for k in ("symbol", "timeframe", "strategy_id", "direction", "frozen_book", "suggestions_note", "period",
              "hindsight_bars", "summary", "losses", "fee_flipped", "what_ifs", "suggestions", "disclaimer"):
        assert k in out
    s = out["summary"]
    for k in ("trades", "winners", "losers", "scratches", "net_usd", "gross_usd", "fees_usd", "by_reason",
              "by_context", "largest_losses", "loss_weeks", "held_would_have_won"):
        assert k in s
    assert s["trades"] == 2 and s["losers"] == 2
    assert s["held_would_have_won"]["count"] == 1  # A reaches TP1 after its stop
    assert sum(r["share_of_loss"] for r in s["by_reason"]) == pytest.approx(1.0, abs=1e-3)
    assert s["loss_weeks"][0]["week"] == "2025-W02"
    rec = out["losses"][0]
    for k in ("trade_no", "trade_id", "entry_time", "exit_time", "direction", "outcome", "net_usd", "gross_usd",
              "fees_usd", "r_net", "primary_reason", "flags", "metrics", "hindsight", "explanation"):
        assert k in rec
    for w in out["what_ifs"]:
        assert {"id", "label", "computable", "trades", "net_usd", "delta_usd", "winners_lost", "caveat"} <= set(w)
    assert out["disclaimer"] == "Research only. Hindsight and what-ifs are in-sample and do not change any strategy."


# --------------------------------------------------------------------------- endpoint


def _client() -> TestClient:
    from app.api import routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    return TestClient(app)


URL = "/api/research/long-strategy/backtest/loss-analysis?symbol=ETHUSDT&timeframe=1h"


def _set_job(monkeypatch: pytest.MonkeyPatch, job: Any) -> None:
    from app.research import backtest_job

    monkeypatch.setattr(backtest_job.backtest_job_service, "_job", job)


def test_endpoint_409_without_job(monkeypatch: pytest.MonkeyPatch):
    _set_job(monkeypatch, None)
    res = _client().get(URL)
    assert res.status_code == 409
    assert "No backtest job" in res.json()["detail"]


def test_endpoint_409_when_job_not_finished(monkeypatch: pytest.MonkeyPatch):
    row, _c, _a, _b = _whatif_row()
    _set_job(monkeypatch, SimpleNamespace(status="running", rows=[row], include_trades=True))
    assert _client().get(URL).status_code == 409


def test_endpoint_409_when_trades_were_off(monkeypatch: pytest.MonkeyPatch):
    row, _c, _a, _b = _whatif_row()
    row["trades"] = []
    _set_job(monkeypatch, SimpleNamespace(status="done", rows=[row], include_trades=False))
    res = _client().get(URL)
    assert res.status_code == 409 and "include_trades" in res.json()["detail"]


def test_endpoint_409_when_no_trades(monkeypatch: pytest.MonkeyPatch):
    row, _c, _a, _b = _whatif_row()
    row["trades"] = []
    _set_job(monkeypatch, SimpleNamespace(status="done", rows=[row], include_trades=True))
    res = _client().get(URL)
    assert res.status_code == 409 and "no closed trades" in res.json()["detail"]


def test_endpoint_409_when_no_row_for_cell(monkeypatch: pytest.MonkeyPatch):
    row, _c, _a, _b = _whatif_row()
    _set_job(monkeypatch, SimpleNamespace(status="done", rows=[row], include_trades=True))
    res = _client().get("/api/research/long-strategy/backtest/loss-analysis?symbol=SOLUSDT&timeframe=1h")
    assert res.status_code == 409


def test_endpoint_ok_reads_job_row_and_loader(monkeypatch: pytest.MonkeyPatch):
    from app.research import service

    row, c, _a, _b = _whatif_row()
    _set_job(monkeypatch, SimpleNamespace(job_id="j1", status="done", rows=[row], include_trades=True, combination_id="CANDLE12_V2"))
    calls: list[dict[str, Any]] = []

    async def _loader(symbol: str, timeframe: str, **kw: Any):
        calls.append({"symbol": symbol, "timeframe": timeframe, **kw})
        return c, 0, {}

    monkeypatch.setattr(service, "_load_research_candles", _loader)
    res = _client().get(URL + "&max_hold_after_exit=24")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job_id"] == "j1" and body["hindsight_bars"] == 24
    assert body["summary"]["trades"] == 2
    assert calls and calls[0]["use_research_cache"] is False
    assert calls[0]["start_date"] == "2025-01-06"
    # Stored trades are untouched by the analysis.
    assert row["trades"][0]["net_pnl_usd"] == _a["net_pnl_usd"]
