"""P14 (owner ruling D51c): signal detail in the decision journal is output-only and off by default.

With ``DECISION_JOURNAL_SIGNAL_DETAIL`` on, every paper-book and screener signal is journaled with its engine
stop, tested target (TP1) and source, including signals that do not open. The replays below run the same bars
with detail off and on: paper decisions, positions and state are identical, and the journal rows are identical
once the added fields and the detail-only rows are removed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import decision_journal
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine
from app.services.passed_paper_watcher import PassedPaperWatcher
from passed_paper_bars import exit_passed_on_bar

START = datetime(2026, 10, 1, tzinfo=timezone.utc)
VOLATILE = {"ts", "recorded_at", "position_id", "lag_seconds"}
DETAIL_KEYS = {"source", "stop_price", "tp1", "signal_entry_price", "detail_version", "strategy_id", "symbol",
               "direction", "timeframe", "setup_bar_time", "signal_outcome", "signal_reason"}


def _rows(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# --- the writer ------------------------------------------------------------------------------------
def test_detail_is_off_by_default_and_never_computed_when_off(tmp_path):
    from app.config import Settings

    assert Settings.model_fields["decision_journal_signal_detail"].default is False
    p = tmp_path / "j.jsonl"
    decision_journal.configure(p)
    calls = []
    decision_journal.write("PASSED_BOOK", "signal", symbol="BTCUSDT", _detail=lambda: calls.append(1) or {"x": 1})
    decision_journal.write_signal("SCREENER", "signal", lambda: calls.append(2) or {"x": 2})
    decision_journal.write_signal_once("SCREENER", "signal", lambda: calls.append(3) or ("k", {"x": 3}))
    decision_journal.configure(None)
    assert calls == [] and not decision_journal.signal_detail_enabled()
    rows = _rows(p)
    assert len(rows) == 1 and "x" not in rows[0] and "_detail" not in rows[0]


def test_detail_adds_fields_without_replacing_and_swallows_errors(tmp_path):
    p = tmp_path / "j.jsonl"
    decision_journal.configure(p, signal_detail=True)
    decision_journal.write("PASSED_BOOK", "signal", symbol="BTCUSDT", reason="a",
                           _detail=lambda: {"reason": "replaced?", "stop_price": 101.0})
    decision_journal.write("PASSED_BOOK", "signal", symbol="ETHUSDT", _detail=lambda: 1 / 0)
    decision_journal.write_signal("SCREENER", "signal", lambda: None)
    decision_journal.write_signal("SCREENER", "signal", lambda: {"symbol": "SOLUSDT"})
    for _ in range(3):
        decision_journal.write_signal_once("SCREENER", "signal", lambda: ("SOL|15m", {"symbol": "SOLUSDT", "n": 1}))
    decision_journal.configure(None)
    rows = _rows(p)
    assert rows[0]["reason"] == "a" and rows[0]["stop_price"] == 101.0
    assert rows[1]["symbol"] == "ETHUSDT" and "stop_price" not in rows[1]
    assert [r.get("n") for r in rows[2:]] == [None, 1]  # None-builder wrote nothing; once-key wrote once


def test_detail_needs_a_journal_path(tmp_path):
    decision_journal.configure(None, signal_detail=True)
    assert not decision_journal.signal_detail_enabled()
    decision_journal.set_signal_detail(True)
    assert not decision_journal.signal_detail_enabled()
    decision_journal.configure(tmp_path / "j.jsonl")
    decision_journal.set_signal_detail(True)
    assert decision_journal.signal_detail_enabled()
    decision_journal.configure(None)


# --- passed book replay ----------------------------------------------------------------------------------
def _candles(n: int) -> list[dict]:
    out = []
    for h in range(n):
        base = 100.0 + (h % 7) * 0.3
        out.append({"time": START + timedelta(hours=h), "open": base, "high": base + 1.2, "low": base - 1.1,
                    "close": base + 0.2})
    return out


def _closed_4h(tip_time: datetime, n: int = 80) -> list[dict]:
    decision = tip_time + timedelta(hours=1)
    last = decision.replace(hour=decision.hour - decision.hour % 4) - timedelta(hours=4)
    return [{"time": last - timedelta(hours=4 * (n - 1 - i)), "open": 1, "high": 2, "low": 0.5, "close": 1}
            for i in range(n)]


def _fake_eval(**kwargs):
    combo = kwargs["combination"].combination_id
    tip = kwargs["candles"][kwargs["as_of_index"]]
    close = float(tip["close"])
    hour = int((tip["time"] - START).total_seconds() // 3600)
    if combo == "COMBO_01" and hour % 2 == 1:
        return {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": close, "stop_price": close + 2.0, "tp1": close - 4.5}
    if combo == "COMBO_02_LOCAL" and hour % 3 == 0:
        return {"status": "SHORT_ENTRY_CANDIDATE", "entry_price": close, "stop_price": close + 1.5, "tp1": close - 3.5}
    return {"status": "NO_SETUP"}


def _passed_run(tmp: Path, monkeypatch, *, detail: bool) -> dict:
    monkeypatch.setattr("app.services.passed_paper_watcher.evaluate_combination_at_bar", _fake_eval)
    journal = tmp / "j.jsonl"
    decision_journal.configure(journal, signal_detail=detail)
    eng = PaperTradeEngine(enabled=True, risk_policy=PaperRiskPolicy(enabled=False))
    watcher = PassedPaperWatcher(enabled=True, paper_engine=eng)
    candles = _candles(262)
    decisions = []
    for n in range(250, 262):
        window = candles[:n]
        opened = watcher.on_closed_1h(
            "BTCUSDT", candles_1h=window, candles_4h=_closed_4h(window[-1]["time"]), candles_15m=window,
            market_direction="BEARISH", force=True,
            next_bar={"time": window[-1]["time"] + timedelta(hours=1), "open": candles[n]["open"]},
        )
        decisions.append([(p.signal_snippet.get("strategy_id"), p.side, p.entry_price, p.stop_price, p.tp1_price)
                          for p in opened])
        if n == 255:
            for p in list(eng._open.values()):  # noqa: SLF001
                exit_passed_on_bar(eng, p.symbol, float(p.stop_price) + 0.5, window[-1]["time"] + timedelta(hours=1))
    decision_journal.configure(None)
    positions = sorted(
        ({k: v for k, v in p.to_dict().items() if k not in ("id", "opened_at", "closed_at", "updated_at")}
         for p in list(eng._open.values()) + list(getattr(eng, "_closed", []) or [])),  # noqa: SLF001
        key=lambda d: (d["symbol"], str(d.get("source_candle_ts"))),
    )
    return {"decisions": decisions, "positions": json.dumps(positions, sort_keys=True, default=str),
            "signals": [dict(s) for s in watcher._signals], "rows": _rows(journal),  # noqa: SLF001
            "opened": watcher.opened_count, "last_skip": watcher.last_skip}


def _strip(row: dict, added: set[str]) -> dict:
    return {k: v for k, v in row.items() if k not in VOLATILE and k not in added}


@pytest.mark.usefixtures("passed_book_open")
def test_passed_book_identical_with_signal_detail_on_and_off(tmp_path, monkeypatch):
    off = _passed_run(tmp_path / "off", monkeypatch, detail=False)
    on = _passed_run(tmp_path / "on", monkeypatch, detail=True)
    assert on["decisions"] == off["decisions"] and on["positions"] == off["positions"]
    assert on["opened"] == off["opened"] >= 2 and on["last_skip"] == off["last_skip"]
    strip_sig = [{k: v for k, v in s.items() if k != "recorded_at" and k != "position_id"} for s in on["signals"]]
    assert strip_sig == [{k: v for k, v in s.items() if k != "recorded_at" and k != "position_id"} for s in off["signals"]]
    # Journal: same rows once the added fields are dropped.
    added = {"source", "stop_price", "tp1", "signal_entry_price", "detail_version"}
    assert json.dumps([_strip(r, set()) for r in off["rows"]], sort_keys=True) == json.dumps(
        [_strip(r, added) for r in on["rows"]], sort_keys=True)
    assert not any(added & set(r) for r in off["rows"])
    sig_rows = [r for r in on["rows"] if r["event"] == "signal"]
    outcomes = {r["outcome"] for r in sig_rows}
    assert {"opened", "not_opened"} <= outcomes
    for r in sig_rows:  # every signal, opened or not, carries the engine stop, TP1 and source
        assert r["source"] == "PASSED_CAP_BOOK" and r["stop_price"] is not None and r["tp1"] is not None
        assert r["signal_entry_price"] is not None
    nb = [r for r in sig_rows if r["outcome"] == "not_opened"][0]
    assert nb["reason"] and nb["stop_price"] == pytest.approx(nb["signal_entry_price"] + (2.0 if nb["strategy_id"] == "COMBO_01" else 1.5))


# --- COMBO_02 v1 -------------------------------------------------------------------------------------
def test_v1_open_identical_with_signal_detail_on_and_off(tmp_path, monkeypatch):
    from app.services import v1_paper_watcher as v1
    from app.services.ohlcv_store import ohlcv_store
    from tests.test_v1_paper_watcher import _htf_ending_with_setup, _trending_series

    class Pos:
        id = "pos-1"
        timeframe, entry_price, stop_price, tp1_price, quantity, risk_usd = "1h", 100.0, 95.0, 110.0, 2.0, 10.0

    h1 = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0, wave=4.0)
    h4 = _htf_ending_with_setup(h1, n=80, minutes_per_bar=240, start_price=90.0, drift=2.0)
    monkeypatch.setattr(v1, "is_v1_long_entry", lambda result: True)
    monkeypatch.setattr(ohlcv_store, "get_open", lambda *a, **k: None)
    results = []
    for on in (False, True):
        journal = tmp_path / f"v{on}.jsonl"
        decision_journal.configure(journal, signal_detail=on)
        paper = PaperTradeEngine(enabled=True, risk_policy=PaperTradeEngine().risk_policy)
        paper.risk_policy.enabled = False
        calls = []
        monkeypatch.setattr(paper, "open_v1_combo_position", lambda **kw: (calls.append((kw["setup_bar_time_utc"], dict(kw["eval_result"]))), Pos())[1])
        w = v1.V1PaperWatcher(paper_engine=paper, replay_mode=False, emit_alerts=False, secondary_enabled=True)
        monkeypatch.setattr(w, "_bos_break_allows_entry", lambda *a, **k: True)
        monkeypatch.setattr(w, "_trade_phase", lambda *a, **k: None)
        pos = w.on_closed_1h("BTCUSDT", candles_1h=h1, candles_4h=h4, force=True)
        results.append((pos is not None, json.dumps(calls, sort_keys=True, default=str), dict(w._skip_by_symbol)))
        decision_journal.configure(None)
    assert results[0] == results[1] and results[0][0]
    off, on = _rows(tmp_path / "vFalse.jsonl"), _rows(tmp_path / "vTrue.jsonl")
    assert [_strip(r, set()) for r in off] == [_strip(r, set()) for r in on if r["event"] != "signal"]
    sig = [r for r in on if r["event"] == "signal"]
    assert len(sig) == 1 and sig[0]["outcome"] == "opened" and sig[0]["source"] == "V1_PAPER_WATCHER"
    assert sig[0]["position_stop_price"] == 95.0 and sig[0]["position_tp1"] == 110.0 and sig[0]["position_id"] == "pos-1"


# --- CANDLE12_V2 -----------------------------------------------------------------------------------
def test_candle12_identical_with_signal_detail_on_and_off(tmp_path, monkeypatch):
    from app.services import candle12_v2_paper as c12
    from tests.test_candle12_v2_paper import _trade

    def bars(n):
        return [{"time": f"2026-10-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00", "open": 1, "high": 1, "low": 1,
                 "close": 1, "volume": 1} for i in range(n)]

    a, b = _trade(55, "OPEN", "2026-10-03T07:00:00+00:00"), _trade(61, "OPEN", "2026-10-03T13:00:00+00:00")
    outs = []
    for on in (False, True):
        decision_journal.configure(tmp_path / f"j{on}.jsonl", signal_detail=on)
        state = tmp_path / f"s{on}.json"
        # Tick 1 books A; tick 2 sees B open while A is held: second_open_ignored (a signal that does not open).
        for trades, n in (([a], 60), ([a, b], 64)):
            monkeypatch.setattr(c12, "run_candle12_v2", lambda *x, t=trades, **k: {"status": "OK", "trades": t, "v2_config": {}})
            outs.append((on, c12.sync_candles(bars(n), path=state), state.read_text(encoding="utf-8")))
        decision_journal.configure(None)
    assert [o[1:] for o in outs[:2]] == [o[1:] for o in outs[2:]]
    off, on = _rows(tmp_path / "jFalse.jsonl"), _rows(tmp_path / "jTrue.jsonl")
    assert len(off) == len(on)
    for r_off, r_on in zip(off, on):  # each row only gains detail fields; no field it had changes
        assert {k: r_on[k] for k in r_off if k not in VOLATILE} == _strip(r_off, set())
        assert set(r_on) - set(r_off) <= DETAIL_KEYS
    entry = [r for r in on if r["event"] == "entry"][0]
    skip = [r for r in on if r["event"] == "skip"][0]
    assert entry["tp1"] == 120.0 and entry["source"] == "CANDLE12_V2_PAPER" and entry["signal_outcome"] == "opened"
    assert skip["signal_reason"] == "second_open_ignored" and skip["stop_price"] == 90.0 and skip["tp1"] == 120.0


# --- screener ----------------------------------------------------------------------------------------
PAYLOAD = {
    "status": "LONG_ENTRY_CANDIDATE", "direction": "LONG", "timeframe": "15m",
    "source_candle_timestamps": {"15m": "2026-10-21T05:00:00+00:00", "1h": "2026-10-21T04:00:00+00:00"},
    "stop": {"final_stop": 0.95, "risk_per_unit": 0.05}, "entry": {"entry_price": 1.0},
    "targets": [{"target_price": 1.12, "r_multiple": 2.4}, {"target_price": 1.2, "r_multiple": 4.0}],
    "calculated_at": "2026-10-21T05:15:03+00:00",
}


def test_screener_entry_signal_detail():
    from app.services.setup_signals import screener_signal_detail

    key, fields = screener_signal_detail("iostusdt", dict(PAYLOAD))()
    assert key == "IOSTUSDT|15m|LONG|2026-10-21T05:00:00+00:00"
    assert fields["stop_price"] == 0.95 and fields["tp1"] == 1.12 and fields["source"] == "SETUP_SIGNAL_SERVICE"
    assert fields["setup_bar_time"] == "2026-10-21T05:00:00+00:00" and fields["strategy_id"] == "SCREENER_SETUP"
    assert screener_signal_detail("X", dict(PAYLOAD, status="WAITING"))() is None
    assert screener_signal_detail("X", dict(PAYLOAD, stop={"final_stop": None}))() is None


def test_screener_payload_identical_and_journaled_once(tmp_path, monkeypatch):
    from app.services.setup_signals import SetupSignalService

    class FakeAnalysis:
        def to_dict(self):
            return json.loads(json.dumps(PAYLOAD))

    outs = []
    for on in (False, True):
        decision_journal.configure(tmp_path / f"s{on}.jsonl", signal_detail=on)
        svc = SetupSignalService()
        monkeypatch.setattr(svc.engine, "analyze", lambda *a, **k: FakeAnalysis())
        payloads = [svc.analyze_symbol("IOSTUSDT") for _ in range(3)]
        outs.append([{k: v for k, v in p.items()} for p in payloads])
        decision_journal.configure(None)
    assert json.dumps(outs[0], sort_keys=True) == json.dumps(outs[1], sort_keys=True)
    assert _rows(tmp_path / "sFalse.jsonl") == []
    rows = _rows(tmp_path / "sTrue.jsonl")
    assert len(rows) == 1 and rows[0]["book"] == "SCREENER" and rows[0]["symbol"] == "IOSTUSDT"


def test_no_decision_path_reads_the_flag():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    hits = []
    for py in app_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if "signal_detail_enabled(" in text or "decision_journal_signal_detail" in text:
            hits.append(str(py.relative_to(app_dir)).replace("\\", "/"))
    # Defined in the journal and settings; read only where the journal is configured.
    assert sorted(hits) == ["config.py", "engines/orchestrator.py", "services/decision_journal.py"]
