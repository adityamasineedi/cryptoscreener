"""Owner D181: display-only passed-book health extras (read-only journal summary)."""

from __future__ import annotations

import json

from app.services import passed_health_view as phv


def _line(**row):
    return json.dumps({"book": "PASSED_BOOK", "event": "signal", **row})


def test_counts_missed_since_forward_start_and_collapses_history_short():
    lines = [
        _line(outcome="missed", reason="late_tip", symbol="DOGEUSDT", ts="2026-10-19T17:44:18+00:00"),
        _line(outcome="missed", reason="late_tip", symbol="LTCUSDT", ts="2026-10-21T00:00:00+00:00"),
        _line(outcome="missed", reason="late_tip", symbol="LTCUSDT", ts="2026-10-22T03:00:00+00:00"),
        _line(outcome="not_evaluated", reason="history_short", symbol="TONUSDT", bars=1, ts="2026-10-21T19:01:00+00:00"),
        _line(outcome="not_evaluated", reason="history_short", symbol="TONUSDT", bars=1, ts="2026-10-21T20:01:00+00:00"),
        _line(outcome="not_evaluated", reason="late_lock", symbol="ETHUSDT", bars=2, ts="2026-10-21T20:01:00+00:00"),
        json.dumps({"book": "COMBO_02_V1", "event": "signal", "outcome": "missed", "ts": "2026-10-22T00:00:00+00:00"}),
        "not json",
    ]
    out = phv.summarize(lines)
    assert out["missed_signals_since"] == {"since": "2026-10-21T00:00:00+00:00", "count": 2}
    assert out["not_tradable"] == [{"symbol": "TONUSDT", "reason": "history_short",
                                    "label": "no history / no market cap", "bars": 2,
                                    "last": "2026-10-21T20:01:00+00:00"}]


def test_missing_journal_is_zero_and_empty(tmp_path):
    out = phv.passed_health_extras(tmp_path / "nope.jsonl")
    assert out["missed_signals_since"]["count"] == 0
    assert out["not_tradable"] == []


def test_reads_the_file_and_caches_on_size_and_mtime(tmp_path):
    p = tmp_path / "decision_journal.jsonl"
    p.write_text(_line(outcome="missed", reason="late_tip", symbol="X", ts="2026-10-22T00:00:00+00:00") + "\n",
                 encoding="utf-8")
    assert phv.passed_health_extras(p)["missed_signals_since"]["count"] == 1
    with p.open("a", encoding="utf-8") as fh:
        fh.write(_line(outcome="missed", reason="late_tip", symbol="Y", ts="2026-10-22T01:00:00+00:00") + "\n")
    assert phv.passed_health_extras(p)["missed_signals_since"]["count"] == 2
