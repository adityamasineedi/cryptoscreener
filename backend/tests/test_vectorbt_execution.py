"""Optional VectorBT execution gate on Strategy Backtest rows (research only).

Adapters are monkeypatched so these tests do not depend on the per-family
order adapter modules.
"""

from __future__ import annotations

import asyncio
import copy
import math
import sys
from typing import Any

import pytest

from app.research import vectorbt_execution as vx
from app.research.backtest_cpu_worker import build_matrix_row_postprocess
from app.research.engine_backtest import build_engine_row
from app.research.trade_fees import enrich_trade_execution
from app.research.vectorbt_replay import ReplayOrder

TAKER = 0.0004
MAKER = 0.0002
NEW_KEYS = {"execution_engine", "vectorbt_parity", "vectorbt_mismatches"}


def _raw(tid, direction, ei, xi, entry, stop, exit_px, outcome):
    return {
        "trade_id": tid,
        "direction": direction,
        "entry_index": ei,
        "exit_index": xi,
        "entry_price": entry,
        "stop_price": stop,
        "exit_price": exit_px,
        "outcome": outcome,
        "signal_time": f"2026-03-01T{ei % 24:02d}:00:00+00:00",
        "exit_time": f"2026-03-02T{xi % 24:02d}:00:00+00:00",
    }


# qty = 20 / 2 = 10 and every fee has <= 8 decimals, so the engine's 1e-8 fee
# quantization is exact and VectorBT's fills reproduce the dollars exactly.
RAW = [
    _raw("t1", "LONG", 5, 9, 100.0, 98.0, 104.0, "TP1"),
    _raw("t2", "LONG", 9, 9, 100.0, 98.0, 98.0, "SL"),
    _raw("t3", "LONG", 12, 15, 100.0, 98.0, 101.5, "TP1"),
    _raw("t4", "LONG", 16, 20, 100.0, 98.0, 98.0, "SL"),
]


def _enriched(raw):
    return [
        enrich_trade_execution(
            t, risk_usd=20.0, taker_fee=TAKER, maker_fee=MAKER, leverage=2.0, account_equity=1000.0
        )
        for t in raw
    ]


def _engine_row(raw=RAW, include_trades=True):
    return build_engine_row(
        {"trades": _enriched(raw), "open_trades_at_end": 0, "execution_model": "x", "status": "OK"},
        strategy_id="CANDLE12_V2",
        direction="LONG",
        symbol="BTCUSDT",
        timeframe="1h",
        risk_usd=20.0,
        principal=1000.0,
        leverage=2.0,
        bars_loaded=1200,
        warmup_bars=1000,
        candle_source="postgresql_ohlcv",
        period_start=None,
        period_end=None,
        include_trades=include_trades,
    )


def _combo_row(raw=RAW):
    rs = [2.0, -1.0, 0.75, -1.0]
    eq = [0.0]
    for r in rs:
        eq.append(eq[-1] + r)
    return build_matrix_row_postprocess(
        {
            "backtest_out": {
                "trades": raw,
                "sample_size": len(raw),
                "result": {"r_values": rs, "equity_curve_r": eq, "max_drawdown_R": 1.0},
                "status": "OK",
            },
            "risk_usd": 20.0,
            "taker_fee": TAKER,
            "maker_fee": MAKER,
            "leverage": 2.0,
            "principal": 1000.0,
            "include_trades": True,
            "direction": "LONG",
            "combination_id": "COMBO_02",
            "load_meta": {"symbol": "BTCUSDT", "timeframe": "1h"},
            "candles": [],
        }
    )


def _fake_adapter(strategy_id: str, trades: list[dict[str, Any]], **_kw) -> list[ReplayOrder]:
    return [
        ReplayOrder(
            trade_key=str(t["trade_id"]),
            direction=str(t["direction"]),
            entry_index=int(t["entry_index"]),
            exit_index=int(t["exit_index"]),
            entry_price=float(t["entry_price"]),
            exit_price=float(t["exit_price"]),
            qty=float(t["qty"]),
            entry_fee_rate=float(t["fee_entry_rate"]),
            exit_fee_rate=float(t["fee_exit_rate"]),
            risk_usd=float(t["risk_usd"]),
            stop=float(t["stop_price"]),
            target=None,
            outcome=str(t["outcome"]),
            net_r=t.get("r_net"),
        )
        for t in trades
    ]


@pytest.fixture
def fake_adapter(monkeypatch):
    calls: list[str] = []

    def _get(family):
        calls.append(family)
        return _fake_adapter

    monkeypatch.setattr(vx, "_adapter", _get)
    return calls


def _close(a, b, tol=1e-8):
    if isinstance(a, list) or isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)


# --- flag off ---------------------------------------------------------------


def test_flag_off_job_rows_have_no_vectorbt_keys(monkeypatch):
    """With the flag off the gate is never called and rows carry no new keys."""

    def _boom(*_a, **_k):
        raise AssertionError("apply_vectorbt_execution must not run with the flag off")

    monkeypatch.setattr(vx, "apply_vectorbt_execution", _boom)
    st, calls = _run_job(monkeypatch, vectorbt_execution=False, include_trades=True)
    assert st["status"] == "done"
    assert st["vectorbt_execution"] is False
    assert "vectorbt_mismatch_total" not in st
    row = st["rows"][0]
    assert not (NEW_KEYS & set(row))
    assert calls[0]["include_trades"] is True


def test_input_row_is_never_mutated(fake_adapter):
    pytest.importorskip("vectorbt")
    row = _engine_row()
    before = copy.deepcopy(row)
    vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert row == before


# --- PASS -------------------------------------------------------------------


def test_engine_pass_uses_vectorbt_and_summary_matches_bar_loop(fake_adapter):
    pytest.importorskip("vectorbt")
    row = _engine_row()
    out = vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert out["execution_engine"] == "vectorbt", out["vectorbt_parity"]
    assert out["vectorbt_parity"]["verdict"] == "PASS"
    assert out["vectorbt_mismatches"] == 0
    assert fake_adapter == ["engine"]
    assert set(out) == set(row) | NEW_KEYS
    for key in vx.ENGINE_RECOMPUTED_FIELDS:
        assert _close(out[key], row[key]), key
    assert len(out["trades"]) == len(row["trades"])
    for new, old in zip(out["trades"], row["trades"]):
        assert new["trade_id"] == old["trade_id"]
        assert _close(new["net_pnl_usd"], old["net_pnl_usd"])
        assert _close(new["fee_total_usd"], old["fee_total_usd"])  # sign kept (negative cost)
        assert new["fee_total_usd"] < 0
        assert _close(new["r_net"], old["r_net"])
        assert new["entry_price"] == old["entry_price"]


def test_engine_pass_summary_really_comes_from_the_replayed_fills(monkeypatch, fake_adapter):
    """Prove the summary is recomputed from VectorBT values, not copied."""
    pytest.importorskip("vectorbt")
    from app.research import vectorbt_replay

    real = vectorbt_replay.check_parity

    def _shifted(orders):
        res = real(orders)
        for t in res.replayed:
            t.pnl_usd += 1.0
        return res

    monkeypatch.setattr(vectorbt_replay, "check_parity", _shifted)
    row = _engine_row()
    out = vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert out["execution_engine"] == "vectorbt"
    assert out["pnl_usd_net"] == pytest.approx(row["pnl_usd_net"] + len(RAW))


def test_combo_pass_recomputes_net_summary_and_lists_r_fields(fake_adapter):
    pytest.importorskip("vectorbt")
    row = _combo_row()
    assert row["trades"] and row["trades"][0].get("entry_index") is not None
    out = vx.apply_vectorbt_execution(row, strategy_id="COMBO_02", family="combo")
    assert out["execution_engine"] == "vectorbt", out["vectorbt_parity"]
    assert fake_adapter == ["combo"]
    for key in (*vx.COMBO_NET_SUMMARY_FIELDS, "pnl_usd_net", "fees_usd", "sample_size"):
        assert _close(out[key], row[key]), key
    assert out["vectorbt_unrecomputed_fields"] == ["r_values", "equity_curve_r", "max_drawdown_R"]
    assert out["r_values"] == row["r_values"]


# --- FAIL / UNSUPPORTED / UNAVAILABLE --------------------------------------


def test_overlapping_trades_fail_parity_and_keep_bar_loop_row(fake_adapter):
    pytest.importorskip("vectorbt")
    raw = [
        _raw("t1", "LONG", 5, 12, 100.0, 98.0, 104.0, "TP1"),
        _raw("t2", "LONG", 8, 15, 100.0, 98.0, 98.0, "SL"),  # enters while t1 is open
    ]
    row = _engine_row(raw)
    out = vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert out["execution_engine"] == "bar_loop"
    assert out["vectorbt_parity"]["verdict"] == "FAIL"
    assert out["vectorbt_mismatches"] >= 1
    assert out["vectorbt_parity"]["first_mismatch_field"]
    assert {k: v for k, v in out.items() if k not in NEW_KEYS} == row


def test_vectorbt_missing_is_unavailable(monkeypatch, fake_adapter):
    monkeypatch.setitem(sys.modules, "vectorbt", None)  # import -> ImportError
    row = _engine_row()
    out = vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert out["execution_engine"] == "bar_loop"
    assert out["vectorbt_parity"]["verdict"] == "UNAVAILABLE"
    assert "not installed" in out["vectorbt_parity"]["reason"]
    assert out["vectorbt_mismatches"] == 0
    assert {k: v for k, v in out.items() if k not in NEW_KEYS} == row


def test_adapter_replay_unsupported_is_unsupported(monkeypatch):
    pytest.importorskip("vectorbt")

    class ReplayUnsupported(Exception):
        pass

    def _raises(strategy_id, trades, **_kw):
        raise ReplayUnsupported("no bar index on the trade")

    monkeypatch.setattr(vx, "_adapter", lambda family: _raises)
    row = _engine_row()
    out = vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert out["execution_engine"] == "bar_loop"
    assert out["vectorbt_parity"] == {"verdict": "UNSUPPORTED", "reason": "no bar index on the trade"}
    assert {k: v for k, v in out.items() if k not in NEW_KEYS} == row


def test_missing_adapter_module_is_unsupported(monkeypatch):
    pytest.importorskip("vectorbt")
    monkeypatch.setitem(vx._ADAPTERS, "combo", ("app.research._no_such_adapter_module", "x"))
    out = vx.apply_vectorbt_execution(_combo_row(), strategy_id="COMBO_02", family="combo")
    assert out["execution_engine"] == "bar_loop"
    assert out["vectorbt_parity"]["verdict"] == "UNSUPPORTED"


def test_row_without_trades_is_unavailable(fake_adapter):
    row = _engine_row(include_trades=False)
    out = vx.apply_vectorbt_execution(row, strategy_id="CANDLE12_V2", family="engine")
    assert out["execution_engine"] == "bar_loop"
    assert out["vectorbt_parity"]["verdict"] == "UNAVAILABLE"
    assert fake_adapter == []


# --- job wiring -------------------------------------------------------------


def _run_job(monkeypatch, *, vectorbt_execution, include_trades, engine=False):
    from app.research.backtest_job import BacktestJobService

    calls: list[dict[str, Any]] = []

    async def _fake_matrix(**kwargs):
        calls.append(kwargs)
        row = _engine_row() if engine else _combo_row()
        if not kwargs["include_trades"]:
            row["trades"] = []
        return {
            "status": "OK",
            "playbook": "test",
            "combination_name": "x",
            "disclaimer": "research",
            "label": "LONG_STRATEGY_BACKTEST",
            "dataset_id": "ds1",
            "rows": [row],
        }

    import app.research.engine_backtest as eb
    import app.research.service as svc_mod

    class Fake:
        async def strategy_matrix(self, **kwargs):
            return await _fake_matrix(**kwargs)

    monkeypatch.setattr(svc_mod, "get_bos_research_service", lambda: Fake())
    monkeypatch.setattr(eb, "engine_strategy_matrix", _fake_matrix)
    svc = BacktestJobService()

    async def _go():
        started = await svc.start(
            symbols=["BTCUSDT"],
            timeframes=["1h"],
            direction="LONG",
            combination_id="CANDLE12_V2" if engine else "COMBO_02",
            strategy_id="CANDLE12_V2" if engine else "COMBO_02_V1",
            combo_version=None if engine else "v1",
            risk_mode=None if engine else "V1_PRODUCTION_PROFILE",
            principal_usd=1000.0,
            risk_usd=20.0,
            include_trades=include_trades,
            vectorbt_execution=vectorbt_execution,
        )
        for _ in range(200):
            if svc.status().get("status") in {"done", "error", "cancelled", "stalled"}:
                break
            await asyncio.sleep(0.02)
        return started

    started = asyncio.run(_go())
    st = svc.status()
    st["_started"] = started
    return st, calls


def _recording_gate(monkeypatch):
    seen: list[dict[str, Any]] = []

    def _gate(row, *, strategy_id, family, adapter_kwargs=None):
        seen.append(
            {"strategy_id": strategy_id, "family": family, "n_trades": len(row.get("trades") or [])}
        )
        return {
            **row,
            "execution_engine": "bar_loop",
            "vectorbt_parity": {"verdict": "FAIL", "first_mismatch_field": "exit_price"},
            "vectorbt_mismatches": 3,
        }

    monkeypatch.setattr(vx, "apply_vectorbt_execution", _gate)
    return seen


IDENTITY_KEYS = ("combination_id", "strategy_id", "combo_version", "direction")


@pytest.mark.parametrize("engine", [False, True])
def test_job_flag_propagates_and_identity_is_unchanged(monkeypatch, engine):
    off, _ = _run_job(monkeypatch, vectorbt_execution=False, include_trades=True, engine=engine)
    seen = _recording_gate(monkeypatch)
    on, _ = _run_job(monkeypatch, vectorbt_execution=True, include_trades=True, engine=engine)
    assert off["status"] == on["status"] == "done", on.get("error")
    assert off["vectorbt_execution"] is False and on["vectorbt_execution"] is True
    assert on["_started"]["vectorbt_execution"] is True
    for key in IDENTITY_KEYS:
        assert on.get(key) == off.get(key), key
        assert on["_started"].get(key) == off["_started"].get(key), key
        assert on["rows"][0].get(key) == off["rows"][0].get(key), key
    expect_id = "CANDLE12_V2" if engine else "COMBO_02"
    assert seen == [
        {"strategy_id": expect_id, "family": "engine" if engine else "combo", "n_trades": len(RAW)}
    ]
    # Gate runs before config enrichment: enriched identity keys still present.
    assert on["rows"][0]["paper_trade_created"] is False
    assert on["rows"][0]["execution_engine"] == "bar_loop"
    assert on["vectorbt_mismatch_total"] == 3
    assert on["vectorbt_rows"][0]["first_mismatch_field"] == "exit_price"
    # Flag off: the row matches the flag-on row minus the annotations.
    assert {k: v for k, v in on["rows"][0].items() if k not in NEW_KEYS} == off["rows"][0]


def test_job_include_trades_false_replays_then_strips(monkeypatch):
    seen = _recording_gate(monkeypatch)
    st, calls = _run_job(monkeypatch, vectorbt_execution=True, include_trades=False)
    assert st["status"] == "done", st.get("error")
    assert calls[0]["include_trades"] is True  # requested internally for the replay
    assert seen[0]["n_trades"] == len(RAW)
    assert st["include_trades"] is False
    assert st["rows"][0]["trades"] == []
    assert st["rows"][0]["execution_engine"] == "bar_loop"


def test_job_include_trades_false_without_flag_requests_no_trades(monkeypatch):
    st, calls = _run_job(monkeypatch, vectorbt_execution=False, include_trades=False)
    assert st["status"] == "done"
    assert calls[0]["include_trades"] is False
    assert st["rows"][0]["trades"] == []


def test_job_survives_gate_exception(monkeypatch):
    def _boom(row, **_kw):
        raise RuntimeError("numba exploded")

    monkeypatch.setattr(vx, "apply_vectorbt_execution", _boom)
    st, _ = _run_job(monkeypatch, vectorbt_execution=True, include_trades=True)
    assert st["status"] == "done", st.get("error")
    assert st["rows"][0]["execution_engine"] == "bar_loop"
    assert st["rows"][0]["vectorbt_parity"]["verdict"] == "ERROR"
