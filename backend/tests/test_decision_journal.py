"""The decision journal (owner ruling D19) is write-only and can never raise into a decision path."""

from __future__ import annotations

import json

from app.services import decision_journal


def test_off_unless_configured(tmp_path):
    decision_journal.configure(None)
    decision_journal.write("PASSED_BOOK", "signal", symbol="BTCUSDT")
    assert not decision_journal.enabled()
    assert list(tmp_path.iterdir()) == []


def test_appends_one_json_line_per_event(tmp_path):
    p = tmp_path / "j.jsonl"
    decision_journal.configure(p)
    decision_journal.write("PASSED_BOOK", "signal", symbol="BTCUSDT", outcome="opened")
    decision_journal.write("COMBO_02_V1", "skip", symbol="ETHUSDT", reason="late_tip")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["signal", "skip"]
    assert rows[0]["book"] == "PASSED_BOOK" and rows[1]["reason"] == "late_tip" and "ts" in rows[0]
    decision_journal.configure(None)


def test_a_failed_write_is_swallowed(tmp_path):
    decision_journal.configure(tmp_path)  # a directory: open() fails
    decision_journal.write("PASSED_BOOK", "signal", symbol="BTCUSDT", odd=object())
    decision_journal.configure(None)


def _jrows(p):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_candle12_v2_sync_identical_with_and_without_journal(tmp_path, monkeypatch):
    from app.services import candle12_v2_paper as c12
    from tests.test_candle12_v2_paper import _trade

    candles = [{"time": f"2026-10-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
               for i in range(60)]
    fake = {"status": "OK", "trades": [_trade(55, "OPEN", "2026-10-03T07:00:00+00:00")], "v2_config": {}}
    monkeypatch.setattr(c12, "run_candle12_v2", lambda *a, **k: fake)
    outs = []
    for on in (False, True):
        journal = tmp_path / f"j{on}.jsonl"
        decision_journal.configure(journal if on else None)
        state = tmp_path / f"s{on}.json"
        out = c12.sync_candles(candles, path=state)
        outs.append((out, state.read_text(encoding="utf-8")))
    decision_journal.configure(None)
    assert outs[0] == outs[1]
    events = [r["event"] for r in _jrows(tmp_path / "jTrue.jsonl")]
    assert events[0] == "tip_evaluated" and "entry" in events
    assert not (tmp_path / "jFalse.jsonl").exists()


def test_v1_live_open_identical_with_and_without_journal(tmp_path, monkeypatch):
    from app.services import v1_paper_watcher as v1
    from app.services.ohlcv_store import ohlcv_store
    from app.services.paper_trade import PaperTradeEngine
    from tests.test_v1_paper_watcher import _htf_ending_with_setup, _trending_series

    class Pos:
        timeframe, entry_price, stop_price, quantity, risk_usd = "1h", 100.0, 95.0, 2.0, 10.0

    h1 = _trending_series(n=80, minutes_per_bar=60, start_price=100.0, drift=1.0, wave=4.0)
    h4 = _htf_ending_with_setup(h1, n=80, minutes_per_bar=240, start_price=90.0, drift=2.0)
    monkeypatch.setattr(v1, "is_v1_long_entry", lambda result: True)
    monkeypatch.setattr(ohlcv_store, "get_open", lambda *a, **k: None)
    results = []
    for on in (False, True):
        journal = tmp_path / f"v{on}.jsonl"
        decision_journal.configure(journal if on else None)
        paper = PaperTradeEngine(enabled=True, risk_policy=PaperTradeEngine().risk_policy)
        paper.risk_policy.enabled = False
        calls = []
        monkeypatch.setattr(paper, "open_v1_combo_position", lambda **kw: (calls.append(kw["setup_bar_time_utc"]), Pos())[1])
        w = v1.V1PaperWatcher(paper_engine=paper, replay_mode=False, emit_alerts=False, secondary_enabled=True)
        monkeypatch.setattr(w, "_bos_break_allows_entry", lambda *a, **k: True)
        monkeypatch.setattr(w, "_trade_phase", lambda *a, **k: None)
        pos = w.on_closed_1h("BTCUSDT", candles_1h=h1, candles_4h=h4, force=True)
        results.append((pos is not None, calls, dict(w._skip_by_symbol)))
    decision_journal.configure(None)
    assert results[0] == results[1] and results[0][0]
    rows = _jrows(tmp_path / "vTrue.jsonl")
    assert any(r["event"] == "opened" and r["book"] == "COMBO_02_V1" and r["entry_price"] == 100.0 for r in rows)
