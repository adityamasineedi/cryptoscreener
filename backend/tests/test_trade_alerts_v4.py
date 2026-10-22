"""Telegram trade alerts v4 (owner rulings D150, D193(a), D194; DECISIONS D221). No network, no database.

1. PASSED_BOOK opened-trade exception: a KILLED source listed in ``killed_opened_exception_sources`` alerts the
   trades its paper book OPENED, labelled; everything else stays as v3.
2. FWD-1 / FWD-3 results from their own ledgers, read only.
3. The D117 line stays first; the frozen files stay byte-identical.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.alerts import fwd_ledgers as fl
from app.alerts.config import CONFIG_PATH, KILLED_OPENED_EXCEPTION_LABEL, AlertConfig
from app.alerts.data import MemoryData
from app.alerts.fwd_ledgers import ForwardLedgers, fwd1_result, fwd3_result, read_jsonl_lines, read_jsonl_readonly
from app.alerts.pipeline import LEVELS_ONLY, AlertPipeline
from app.alerts.plan import LEDGER_PENDING_FWD, OBSERVATION_PREFIX, Signal
from app.alerts.sender import ReplyingTelegramSender
from app.alerts.sources import fwd1_items, fwd3_items

REPO = Path(__file__).resolve().parents[2]
LABEL = "⚠️ KILLED STRATEGY (research) · PARITY TEST · observation only"
T0 = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)  # the ICPUSDT COMBO_01 SHORT bar of 2026-10-22
STATUS = {"default": "UNPROVEN", "sources": {
    "PASSED_BOOK": {"status": "KILLED"}, "OTHER_KILLED": {"status": "KILLED"},
    "COMBO_02_V1": {"status": "PAPER"}, "BOOK16_FWD1": {"status": "PAPER"}, "LCSC_FWD3": {"status": "PAPER"}}}
TICKS = {"ICPUSDT": 0.001, "ETHUSDT": 0.01, "BTCUSDT": 0.1}


def cfg(*, v4: bool = True, **over) -> AlertConfig:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if v4:
        raw["killed_opened_exception_sources"] = ["PASSED_BOOK"]
    raw.update(over)
    return AlertConfig(raw, status_map=STATUS, ticks=TICKS, round_trip_cost=0.0018)


def icp(outcome: str = "opened", reason: str | None = None, **kw) -> Signal:
    """The real 2026-10-22 ICPUSDT COMBO_01 SHORT signal (engine stop and TP1 from the live alerts.jsonl row)."""
    extra = {"live_outcome": outcome, "engine_values": "journal"}
    if reason:
        extra["skip_reason"] = reason
    base = dict(source="PASSED_BOOK", strategy_id="COMBO_01", symbol="ICPUSDT", side="SHORT", timeframe="1h",
                signal_bar=T0, decided_at=T0 + timedelta(hours=1, seconds=38), stop=2.78693809162277,
                tested_target=2.6971238167544604, signal_close=2.757, atr=0.029690460267239465,
                journal_ref={"file": "decision_journal.jsonl", "line": 24838},
                position_id="c29ed960-7e0d-4e21-9c13-318e775168f6" if outcome == "opened" else None, extra=extra)
    base.update(kw)
    return Signal(**base)


def pipe(tmp_path, c=None, data=None, **kw) -> AlertPipeline:
    return AlertPipeline(c or cfg(), out_dir=tmp_path, data=data or MemoryData(), **kw)


def log_rows(tmp_path) -> list[dict]:
    return [json.loads(x) for x in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]


def bar(t, o, h, l, c):  # noqa: E741
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


NOW = T0 + timedelta(hours=1, minutes=1)


# --- 1. the exception ---------------------------------------------------------------------------------------
def test_label_text_is_exact_and_defaults_are_empty():
    assert KILLED_OPENED_EXCEPTION_LABEL == LABEL
    shipped = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert shipped["killed_opened_exception_sources"] == [] and shipped["killed_opened_exception_label"] == LABEL
    assert shipped["allow_killed"] is False and shipped["enabled"] is False and shipped["dry_run"] is True
    old = {k: v for k, v in shipped.items() if not k.startswith(("killed_opened", "_killed_opened"))}
    c = AlertConfig(old, status_map=STATUS, ticks=TICKS, round_trip_cost=0.0018)
    assert c.killed_opened_exception_sources == frozenset() and c.killed_opened_exception_label == LABEL
    assert not c.killed_opened_exception("PASSED_BOOK", "KILLED", "opened")


def test_opened_passed_book_signal_is_sent_with_the_label_after_the_unproven_line(tmp_path):
    row = pipe(tmp_path).handle(icp(), NOW)
    assert (row["kind"], row["code"]) == ("ALERT", "DRY_RUN")
    assert row["flags"]["killed"] is False and row["flags"]["killed_opened_exception"] is True
    lines = row["text"].split("\n")
    assert lines[0] == OBSERVATION_PREFIX and lines[1] == LABEL
    assert lines[2] == "✅ INTRADAY TRADE - ICP" and "🧪 KILLED · COMBO_01 · 1h" in lines
    assert "👉 SL - $2.787" in lines and "👉 ENTRY - $2.757 - $2.772" in lines
    assert row["result_ledger"] == "paper"


def test_v3_config_keeps_the_opened_passed_book_signal_suppressed(tmp_path):
    row = pipe(tmp_path, cfg(v4=False)).handle(icp(), NOW)
    assert row["code"] == "SUPPRESSED_KILLED" and "killed_opened_exception" not in row["flags"]
    assert LABEL not in row["text"] and "result_ledger" not in row


@pytest.mark.parametrize("outcome,reason", [("not_opened", "symbol_has_open_position"),
                                            ("not_opened", "engine_refused:group_not_allowed=small-cap"),
                                            ("missed", None), ("", None)])
def test_not_opened_passed_book_signals_stay_suppressed_killed(tmp_path, outcome, reason):
    s = icp(outcome, reason, strategy_id="COMBO_02_LOCAL")
    row = pipe(tmp_path).handle(s, NOW)
    assert row["code"] == "SUPPRESSED_KILLED" and row["flags"]["killed"] is True
    assert "killed_opened_exception" not in row["flags"] and LABEL not in (row["text"] or "")


def test_another_killed_source_is_still_suppressed_and_allow_killed_still_works(tmp_path):
    other = icp(source="OTHER_KILLED")
    assert pipe(tmp_path / "a").handle(other, NOW)["code"] == "SUPPRESSED_KILLED"
    # allow_killed (global, off by default) still releases every KILLED source, unlabelled for unlisted ones.
    row = pipe(tmp_path / "b", cfg(allow_killed=True)).handle(other, NOW)
    assert row["code"] == "DRY_RUN" and LABEL not in row["text"]
    row = pipe(tmp_path / "c", cfg(allow_killed=True)).handle(icp(), NOW)
    assert row["code"] == "DRY_RUN" and row["text"].split("\n")[1] == LABEL


def test_every_other_v3_rule_still_applies_to_the_exception(tmp_path):
    late = pipe(tmp_path / "late").handle(icp(), T0 + timedelta(hours=1, minutes=16))
    assert late["code"] == "LATE"
    # Entry-to-stop cost filter (D145): a stop 0.2% away is under the 0.42% cutoff.
    tight = icp(stop=2.757 * 1.002, atr=0.0005)
    assert pipe(tmp_path / "cs").handle(tight, NOW)["code"] in ("SKIPPED_COST", "SKIPPED_COST_STOP")
    cost_only = pipe(tmp_path / "c", cfg(cost={"multiple": 30.0, "fee_sides": 2})).handle(icp(), NOW)
    assert cost_only["code"] == "SKIPPED_COST"
    p = pipe(tmp_path / "dup")
    assert p.handle(icp(), NOW)["code"] == "DRY_RUN" and p.handle(icp(), NOW)["code"] == "DUPLICATE"
    rl = pipe(tmp_path / "rl", cfg(rate_limit={"max_alerts": 1, "window_minutes": 60}))
    assert rl.handle(icp(), NOW)["code"] == "DRY_RUN"
    assert rl.handle(icp(symbol="ETHUSDT", signal_close=2500.0, stop=2525.0, tested_target=2440.0, atr=10.0),
                     NOW)["code"] == "RATE_LIMITED"
    excl = cfg(symbol_exclusions={"reason": "D108/D120", "sources": {"PASSED_BOOK": {"exclude_symbols": ["ICPUSDT"]}}})
    assert pipe(tmp_path / "ex", excl).handle(icp(), NOW)["code"] == "EXCLUDED_SYMBOL"


def test_live_mode_sends_the_exception_to_the_private_chat_only(tmp_path):
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 41 + len(posted)}})

    sender = ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    p = pipe(tmp_path, cfg(enabled=True, dry_run=False), mode="watch", sender=sender)
    assert p.handle(icp(), NOW)["code"] == "SENT"
    assert p.handle(icp("not_opened", "symbol_has_open_position", strategy_id="COMBO_02_LOCAL"), NOW)["code"] == \
        "SUPPRESSED_KILLED"
    assert len(posted) == 1 and posted[0]["chat_id"] == "123456789"
    assert posted[0]["text"].startswith(OBSERVATION_PREFIX + "\n" + LABEL + "\n")


# --- 1b. follow-ups and the summary from the paper ledger (D149), labelled ----------------------------------------
def _icp_bars():
    t = T0 + timedelta(hours=1)
    return [bar(t, 2.757, 2.760, 2.740, 2.745),                       # fills the entry zone
            bar(t + timedelta(hours=1), 2.745, 2.746, 2.690, 2.695)]  # T1 and T2 at alert levels


def _icp_trade(**kw):
    base = {"id": "c29ed960-7e0d-4e21-9c13-318e775168f6", "symbol": "ICPUSDT", "side": "SHORT", "status": "OPEN",
            "entry_price": 2.757, "stop_price": 2.7869, "tp1_price": 2.6971, "opened_at": (T0 + timedelta(hours=1)).isoformat(),
            "closed_at": None, "exit_price": None, "exit_reason": None, "pnl_usd": None, "r_multiple": None,
            "source_candle_ts": T0.isoformat(), "timeframe": "1h", "signal_snippet": {"source": "PASSED_CAP_BOOK"}}
    base.update(kw)
    return base


def test_exception_followups_come_from_the_paper_ledger_with_the_label(tmp_path):
    data = MemoryData({("ICPUSDT", "1h"): _icp_bars()}, [_icp_trade()])
    p = pipe(tmp_path, data=data)
    p.handle(icp(), NOW)
    p.update_trackers(T0 + timedelta(hours=3))
    rows = [r for r in log_rows(tmp_path) if r["kind"] == "FOLLOWUP"]
    levels = [r for r in rows if r["code"] == LEVELS_ONLY]
    assert levels and {r["event"] for r in levels} >= {"fill", "target", "final"}
    assert all(r["text"].split("\n")[:2] == [OBSERVATION_PREFIX, LABEL] for r in levels)
    # The tracker's result ended first; the ledger trade is still OPEN: one "pending" reply.
    (pending,) = [r for r in rows if r["event"] == "ledger_pending"]
    assert pending["code"] == "DRY_RUN" and pending["text"] == "\n".join(
        [OBSERVATION_PREFIX, LABEL, "⏳ RESULT - ICP SHORT: result pending in paper ledger"])
    # The paper ledger closes the trade at TP1: the result reply carries the ledger's numbers.
    data.trades[0].update(status="CLOSED", exit_reason="TP1", exit_price=2.6971, r_multiple=1.93, pnl_usd=19.3,
                          closed_at=(T0 + timedelta(hours=3)).isoformat())
    p.update_trackers(T0 + timedelta(hours=4))
    (res,) = [r for r in log_rows(tmp_path) if r.get("event") == "ledger_result"]
    assert res["text"] == "\n".join([OBSERVATION_PREFIX, LABEL,
                                     "🏁 RESULT (paper ledger) - ICP SHORT: CLOSED, TP1, R +1.93, pnl +$19.30, exit $2.697"])
    assert res["ledger_result"]["r_multiple"] == 1.93 and p.state["ledger_watch"] == {}
    p.update_trackers(T0 + timedelta(hours=5))
    assert len([r for r in log_rows(tmp_path) if r.get("event", "").startswith("ledger_")]) == 2


def test_exception_live_replies_go_to_the_alert_and_tracker_levels_are_not_posted(tmp_path):
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 100 + len(posted)}})

    sender = ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    data = MemoryData({("ICPUSDT", "1h"): _icp_bars()},
                      [_icp_trade(status="CLOSED", exit_reason="STOP", exit_price=2.7869, r_multiple=-1.05,
                                  pnl_usd=-10.5)])
    p = pipe(tmp_path, cfg(enabled=True, dry_run=False), data=data, mode="watch", sender=sender)
    assert p.handle(icp(), NOW)["code"] == "SENT"
    p.update_trackers(T0 + timedelta(hours=3))
    assert [m.get("reply_to_message_id") for m in posted] == [None, 101]
    assert posted[1]["text"].endswith("🏁 RESULT (paper ledger) - ICP SHORT: CLOSED, STOP, R -1.05, pnl -$10.50, exit $2.787")
    assert posted[1]["text"].split("\n")[1] == LABEL


def test_summary_line_of_the_exception_shows_the_paper_ledger_result_and_the_label(tmp_path):
    data = MemoryData({("ICPUSDT", "1h"): _icp_bars()},
                      [_icp_trade(status="CLOSED", exit_reason="TP1", exit_price=2.6971, r_multiple=1.93, pnl_usd=19.3)])
    p = pipe(tmp_path, data=data)
    p.handle(icp(), NOW)
    p.state["last_summary_day"] = (T0.date() - timedelta(days=1)).isoformat()
    (out,) = p.maybe_daily_summary(datetime(2026, 10, 23, 0, 0, 30, tzinfo=timezone.utc))
    lines = out.split("\n")
    assert lines[0] == OBSERVATION_PREFIX
    assert "- COMBO_01 ICP SHORT 1h bar 10-22 14:00: CLOSED, TP1, R +1.93, pnl +$19.30 · " + LABEL in lines
    assert "KILLED: 1 alerts, open 0, closed 1, W/L 1/0, R +1.93, pnl +$19.30" in lines


# --- 2. FWD-1 / FWD-3 results from their own ledgers ---------------------------------------------------------
def _fwd1_rows(closed: bool = True, pending: bool = False):
    trade = {"trade_id": "ETHUSDT|2026-10-20 04:00:00+00:00", "symbol": "ETHUSDT", "side": 1,
             "entry_time": "2026-10-20 04:00:00+00:00", "entry_price": 2500.0, "stop_price": 2450.0,
             "exit_time": "2026-10-21 08:00:00+00:00", "exit_reason": "stop", "exit_price": 2449.5, "net_pnl": -20.4}
    run_open = {"seq": 5, "kind": "RUN", "written_utc": "2026-10-20T04:10:06+00:00", "hash": "h5", "payload": {
        "status": "COLLECTING", "pooled_closed_trades": 0,
        "symbols": {"ETHUSDT": {"open_position": {k: trade[k] for k in ("trade_id", "entry_time", "stop_price")},
                                "closed_trades": 0, "pending_closed": []}}}}
    rows = [{"seq": 0, "kind": "CONTRACT_START", "payload": {}}, run_open]
    if pending:
        rows.append({"seq": 6, "kind": "RUN", "written_utc": "2026-10-21T08:10:00+00:00", "hash": "h6", "payload": {
            "symbols": {"ETHUSDT": {"open_position": None, "closed_trades": 0, "pending_closed": [trade]}}}})
    if closed:
        rows.append({"seq": 7, "kind": "TRADE_CLOSED", "payload": trade, "hash": "h7"})
        rows.append({"seq": 8, "kind": "RUN", "written_utc": "2026-10-21T12:10:00+00:00", "hash": "h8", "payload": {
            "symbols": {"ETHUSDT": {"open_position": None, "closed_trades": 1, "pending_closed": []}}}})
    return rows


def _write(path: Path, rows, torn: str = "") -> Path:
    path.write_bytes(("".join(json.dumps(r) + "\n" for r in rows) + torn).encode("utf-8"))
    return path


def test_fwd1_result_from_its_hash_chained_ledger(tmp_path):
    tid = "ETHUSDT|2026-10-20 04:00:00+00:00"
    p = _write(tmp_path / "ledger.jsonl", _fwd1_rows(), torn='{"seq": 9, "kind": "RUN", "payl')
    rows, n = read_jsonl_readonly(p)
    assert n == 4 and [r["seq"] for r in rows] == [0, 5, 7, 8]  # the torn last line is not read
    res = ForwardLedgers(fwd1=p).result("BOOK16_FWD1", tid)
    assert res["status"] == "CLOSED" and res["exit_reason"] == "stop" and res["pnl_usd"] == -20.4
    assert res["r_multiple"] is None and res["ref"]["seq"] == 7
    assert fwd1_result(_fwd1_rows(closed=False), tid)["status"] == "OPEN"
    assert fwd1_result(_fwd1_rows(closed=False, pending=True), tid)["status"] == "CLOSED_NOT_RECORDED"
    assert fwd1_result(_fwd1_rows(), "ETHUSDT|2026-10-25 00:00:00+00:00") is None
    assert ForwardLedgers(fwd1=tmp_path / "missing.jsonl").result("BOOK16_FWD1", tid) is None
    assert ForwardLedgers().result("BOOK16_FWD1", tid) is None


def test_fwd1_run_summary_closed_trades_is_a_count_and_does_not_break_the_source(tmp_path):
    p = _write(tmp_path / "ledger.jsonl", _fwd1_rows())
    sigs, last = fwd1_items(p, after_seq=-1, data=MemoryData(), cfg=cfg(), seen=set(), enrich=False)
    assert last == 8 and [s.position_id for s in sigs] == ["ETHUSDT|2026-10-20 04:00:00+00:00"]


def _fwd3_rows(outcome: str | None = "SL"):
    cand = {"signal_time": "2026-10-16T11:00:00+00:00", "fate": "TAKEN", "close": 2476.41, "invalidation": 2403.82,
            "atr": 19.956}
    first = {"run_at": "2026-10-16T12:20:12+00:00", "row_hash": "r1", "candidates": [cand],
             "trades": [{"signal_time": "2026-10-16T11:00:00+00:00", "outcome": "OPEN", "exit_time": "2026-10-16T11:00:00+00:00"}]}
    rows = [first]
    if outcome:
        rows.append({"run_at": "2026-10-16T18:20:00+00:00", "row_hash": "r2", "candidates": [cand], "trades": [
            {"signal_time": "2026-10-16T11:00:00+00:00", "outcome": outcome, "R": -1.0782868544427562,
             "net_usd": -21.565737088855123, "exit_fill": 2401.41618, "exit_time": "2026-10-16T17:00:00+00:00"}]})
    return rows


def test_fwd3_result_from_its_runs_file_and_a_torn_line_is_read_next_time(tmp_path):
    p = _write(tmp_path / "runs.jsonl", _fwd3_rows(), torn='{"run_at": "2026-10-16T19:2')
    res = ForwardLedgers(fwd3=p).result("LCSC_FWD3", "2026-10-16T11:00:00+00:00")
    assert res == {"ledger": "FWD-3", "status": "CLOSED", "exit_reason": "SL", "exit_time": "2026-10-16T17:00:00+00:00",
                   "exit_price": 2401.41618, "r_multiple": -1.0782868544427562, "pnl_usd": -21.565737088855123,
                   "ref": {"run_at": "2026-10-16T18:20:00+00:00", "row_hash": "r2"}}
    assert fwd3_result(_fwd3_rows(None), "2026-10-16T11:00:00Z")["status"] == "OPEN"
    assert fwd3_result(_fwd3_rows(), "2026-10-17T11:00:00+00:00") is None
    # The alert source: the torn line is not counted, so its run is read once it is complete.
    items, last = fwd3_items(p, after_line=0, data=MemoryData(), cfg=cfg(), seen=set(), enrich=False)
    assert last == 2 and len(items) == 1
    with p.open("ab") as f:
        f.write(b'0:00+00:00", "row_hash": "r3", "candidates": [{"signal_time": "2026-10-16T19:00:00+00:00", '
                b'"fate": "PENDING_NEXT_BAR", "close": 2480.0, "invalidation": 2440.0, "atr": 15.0}], "trades": []}\n')
    seen = {"2026-10-16T11:00:00+00:00"}
    items, last = fwd3_items(p, after_line=2, data=MemoryData(), cfg=cfg(), seen=seen, enrich=False)
    assert last == 3 and [s.position_id for s in items] == ["2026-10-16T19:00:00+00:00"]


def _fwd1_sig(**kw) -> Signal:
    base = dict(source="BOOK16_FWD1", strategy_id="BOOK16_4H", symbol="ETHUSDT", side="LONG", timeframe="4h",
                signal_bar="2026-10-20T00:00:00+00:00", decided_at="2026-10-20T04:10:06+00:00", stop=2450.0,
                tested_target=None, signal_close=2500.0, atr=25.0, position_id="ETHUSDT|2026-10-20 04:00:00+00:00",
                journal_ref={"file": "book16_fwd1/ledger.jsonl", "seq": 5}, extra={"live_outcome": "opened"})
    base.update(kw)
    return Signal(**base)


def _fwd1_bars():
    t = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)
    return [bar(t, 2500.0, 2505.0, 2490.0, 2495.0), bar(t + timedelta(hours=4), 2495.0, 2496.0, 2440.0, 2445.0)]


def test_fwd1_alert_followup_and_summary_show_the_fwd_ledger_result(tmp_path):
    c = cfg(no_tested_target={"action": "fallback_r", "fallback_t2_r": 2.0})
    led = tmp_path / "ledger.jsonl"
    _write(led, _fwd1_rows(closed=False))
    p = pipe(tmp_path / "out", c, data=MemoryData({("ETHUSDT", "4h"): _fwd1_bars()}),
             fwd_ledgers=ForwardLedgers(fwd1=led))
    s = _fwd1_sig()
    row = p.handle(s, s.decided_at)
    assert row["code"] == "DRY_RUN" and row["result_ledger"] == "FWD-1" and LABEL not in row["text"]
    p.update_trackers(datetime(2026, 10, 20, 12, 1, tzinfo=timezone.utc))
    rows = [r for r in log_rows(tmp_path / "out") if r["kind"] == "FOLLOWUP"]
    assert {r["code"] for r in rows if r["event"] in ("fill", "sl", "final")} == {LEVELS_ONLY}
    (pending,) = [r for r in rows if r["event"] == "ledger_pending"]
    assert pending["text"] == OBSERVATION_PREFIX + "\n⏳ RESULT - ETH LONG: " + LEDGER_PENDING_FWD
    # Summary while the FWD ledger has no result.
    p.state["last_summary_day"] = "2026-10-19"
    (out,) = p.maybe_daily_summary(datetime(2026, 10, 21, 0, 0, 30, tzinfo=timezone.utc))
    assert "- BOOK16_4H ETH LONG 4h bar 10-20 00:00: FWD-1 ledger: result pending in FWD ledger" in out.split("\n")
    assert "PAPER: 1 alerts, open 1, closed 0, W/L 0/0, R +0.00, pnl +$0.00" in out.split("\n")
    # The collector records the close: the reply carries the FWD-1 ledger's own result.
    _write(led, _fwd1_rows())
    p.update_trackers(datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc))
    (res,) = [r for r in log_rows(tmp_path / "out") if r.get("event") == "ledger_result"]
    assert res["text"] == OBSERVATION_PREFIX + "\n🏁 RESULT (FWD-1 ledger) - ETH LONG: CLOSED, stop, R -, pnl -$20.40, exit $2449.50"
    a = p.summary_alerts([{"source": "BOOK16_FWD1", "strategy_id": "BOOK16_4H", "status": "PAPER", "symbol": "ETHUSDT",
                           "side": "LONG", "timeframe": "4h", "signal_bar": "2026-10-20T00:00:00+00:00",
                           "position_id": s.position_id, "paper_book": True, "opened": True}])
    from app.alerts.plan import render_summary

    lines = render_summary("2026-10-21", {}, alerts=a).split("\n")
    assert "- BOOK16_4H ETH LONG 4h bar 10-20 00:00: FWD-1 ledger: CLOSED, stop, R -, pnl -$20.40" in lines
    assert "PAPER: 1 alerts, open 0, closed 1, W/L 0/1, R +0.00, pnl -$20.40" in lines


def test_fwd3_summary_shows_its_ledger_result(tmp_path):
    runs = _write(tmp_path / "runs.jsonl", _fwd3_rows())
    p = pipe(tmp_path / "out", data=MemoryData(), fwd_ledgers=ForwardLedgers(fwd3=runs))
    a = p.summary_alerts([{"source": "LCSC_FWD3", "strategy_id": "LCSC_V1", "status": "PAPER", "symbol": "ETHUSDT",
                           "side": "LONG", "timeframe": "1h", "signal_bar": "2026-10-16T11:00:00+00:00",
                           "position_id": "2026-10-16T11:00:00+00:00", "paper_book": True, "opened": True}])
    from app.alerts.plan import render_summary

    lines = render_summary("2026-10-16", {}, alerts=a).split("\n")
    assert "- LCSC_V1 ETH LONG 1h bar 10-16 11:00: FWD-3 ledger: CLOSED, SL, R -1.08, pnl -$21.57" in lines


# --- 2b. read only ---------------------------------------------------------------------------------------------
def test_ledgers_are_opened_read_only_and_never_changed(tmp_path, monkeypatch):
    led = _write(tmp_path / "ledger.jsonl", _fwd1_rows(), torn='{"seq": 9')
    runs = _write(tmp_path / "runs.jsonl", _fwd3_rows())
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (led, runs)}
    for p in (led, runs):
        os.chmod(p, stat.S_IREAD)  # a read-only file still reads
    modes: list[tuple[str, str]] = []
    real_open, real_io_open = builtins.open, io.open

    def spy(file, mode="r", *a, **kw):
        if str(file).endswith((".jsonl",)) and Path(str(file)).parent == tmp_path:
            modes.append((Path(str(file)).name, mode))
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    monkeypatch.setattr(io, "open", spy)
    try:
        lg = ForwardLedgers(fwd1=led, fwd3=runs)
        assert lg.result("BOOK16_FWD1", "ETHUSDT|2026-10-20 04:00:00+00:00")["status"] == "CLOSED"
        assert lg.result("LCSC_FWD3", "2026-10-16T11:00:00+00:00")["status"] == "CLOSED"
        fwd1_items(led, after_seq=-1, data=MemoryData(), cfg=cfg(), seen=set(), enrich=False)
        fwd3_items(runs, after_line=0, data=MemoryData(), cfg=cfg(), seen=set(), enrich=False)
        read_jsonl_lines(led)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
        monkeypatch.setattr(io, "open", real_io_open)
        for p in (led, runs):
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    assert modes and {m for _, m in modes} == {"rb"} and {n for n, _ in modes} == {"ledger.jsonl", "runs.jsonl"}
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (led, runs)} == before


def test_ledger_reader_source_has_no_write_mode_or_lock():
    """Every open() in the ledger reader is open(p, READ_MODE) with READ_MODE "rb"; nothing writes, locks,
    renames or deletes; no lock module is imported."""
    import ast

    tree = ast.parse((REPO / "backend" / "app" / "alerts" / "fwd_ledgers.py").read_text(encoding="utf-8"))
    assert fl.READ_MODE == "rb"
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
    assert len(opens) == 1 and [ast.unparse(a) for a in opens[0].args] == ["p", "READ_MODE"] and not opens[0].keywords
    banned_calls = {"write", "write_text", "write_bytes", "replace", "rename", "unlink", "remove", "touch", "chmod",
                    "locking", "lockf", "flock", "truncate", "mkdir"}
    attrs = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not attrs & banned_calls, attrs & banned_calls
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not mods & {"msvcrt", "fcntl", "portalocker", "shutil", "os"}, mods


# --- 3. frozen files byte-identical ---------------------------------------------------------------------------
def _blob(path: Path) -> str:
    """git's blob id of the file as committed (CRLF normalised the way core.autocrlf checks it in)."""
    b = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def test_frozen_candle12_oos2_files_match_their_lock():
    lock = json.loads((REPO / "docs" / "candle12_v3_oos_contract.lock.json").read_text(encoding="utf-8"))
    files = lock["code_fingerprint"]["files"]
    assert len(files) == 37
    for rel, sha in files.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == sha, rel


# Paper engine files at 1251e57 (git blob ids). v4 changes only backend/app/alerts/* and its tests.
PAPER_ENGINE_BLOBS = {
    "backend/app/services/paper_trade.py": "92df788dc4483e90735bd5e7af7d01c5ebea9c6e",
    "backend/app/services/paper_risk.py": "e8ac4307b04e6367039bb24685c5f80443377847",
    "backend/app/services/paper_sizing.py": "8506aa5012c6eb88fa3a8afe61a761336596e276",
    "backend/app/services/paper_classification.py": "37ef93c9fdc1fad2af428600d58b3b61e1670d28",
    "backend/app/services/passed_paper_watcher.py": "27c7e286bd02a799eed131629661f860c007b356",
    "backend/app/services/v1_paper_watcher.py": "a6971a3848c84e2e15edb4a4f8e05966611d0b99",
    "backend/app/services/decision_journal.py": "5da94a7f92326d9ee425b36eccd4b4321f1adb7b",
    "backend/app/services/book16_paper.py": "10b854b5ba52c0cdd2c3b3af4a3719cfdd070c86",
    "backend/app/services/candle12_v2_paper.py": "1c231c37b569c17add00320ba09921f77ebf2726",
}


@pytest.mark.parametrize("rel", sorted(PAPER_ENGINE_BLOBS))
def test_paper_engine_files_byte_identical(rel):
    assert _blob(REPO / rel) == PAPER_ENGINE_BLOBS[rel], rel
