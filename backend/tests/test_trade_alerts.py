"""Telegram trade alerts (owner message 2026-10-21): golden outputs and alert rules. No network."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.alerts.config import AlertConfig, CONFIG_PATH
from app.alerts.data import MemoryData
from app.alerts.pipeline import AlertPipeline
from app.alerts.plan import Signal, build_plan, render_alert, render_followup, round_to_tick, fmt_price
from app.alerts.sender import ReplyingTelegramSender
from app.alerts.sources import SkipRecord, fwd3_items, journal_items
from app.alerts.tracker import new_tracker, step, expire_if_due

T0 = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
STATUS = {
    "default": "UNPROVEN",
    "sources": {
        "PASSED_BOOK": {"status": "KILLED"},
        "CANDLE12_V2": {"status": "PAPER"},
        "LCSC_FWD3": {"status": "PAPER"},
        "FP": {"status": "FORWARD-PASSED"},
    },
}
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "ARBUSDT": 0.0001}


def cfg(round_trip: float | None = 0.0018, **over) -> AlertConfig:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw.update(over)
    return AlertConfig(raw, status_map=STATUS, ticks=TICKS, round_trip_cost=round_trip)


def sig(**kw) -> Signal:
    base = dict(source="CANDLE12_V2", strategy_id="CANDLE12_V2", symbol="BTCUSDT", side="LONG", timeframe="1h",
                signal_bar=T0, decided_at=T0 + timedelta(hours=1, minutes=2), stop=98000.0, tested_target=104500.0,
                signal_close=100000.0, atr=1000.0, journal_ref={"file": "decision_journal.jsonl", "line": 7})
    base.update(kw)
    return Signal(**base)


def bar(t, o, h, l, c):  # noqa: E741
    return {"time": t, "open": o, "high": h, "low": l, "close": c}


# --- config defaults ---------------------------------------------------------------------------
def test_flags_default_off_and_dry_run():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["enabled"] is False and raw["dry_run"] is True and raw["allow_killed"] is False
    c = AlertConfig()
    assert not c.sends_telegram and c.dry_run
    # The spec's numbers, all from the one config file.
    assert raw["validity_bars"] == 3 and raw["late_minutes"] == 15 and raw["rate_limit"]["max_alerts"] == 20
    assert raw["risk"]["risk_pct"] == 1.0 and raw["risk"]["max_leverage"] == 3 and raw["cost"]["multiple"] == 3.0
    assert raw["entry_zone"]["atr_period"] == 14 and raw["entry_zone"]["atr_multiple"] == 0.5


def test_round_trip_cost_is_the_apps_research_config():
    from app.research.config import ResearchConfig

    rc = ResearchConfig()
    assert AlertConfig().round_trip_cost() == pytest.approx(
        2 * rc.taker_fee_rate + rc.entry_slippage_rate + rc.exit_slippage_rate
    )


def test_labels_by_timeframe():
    c = cfg()
    assert [c.label_for(tf) for tf in ("5m", "15m", "1h", "4h", "1d")] == ["SCALP", "SCALP", "INTRADAY", "SWING", "SWING"]


def test_killed_sources_in_shipped_status_map():
    c = AlertConfig()
    assert c.status_for("PASSED_BOOK") == "KILLED"
    assert c.status_for("COMBO_02_V1") == "KILLED"
    assert c.status_for("CANDLE12_V2") == "PAPER"
    assert c.status_for("BOOK16_FWD1") == "PAPER" and c.status_for("LCSC_FWD3") == "PAPER"
    assert c.status_for("SOMETHING_NEW") == "UNPROVEN"


# --- golden messages ---------------------------------------------------------------------------
GOLDEN_LONG = """✅ INTRADAY TRADE - BTC
🏮 TYPE - LONG

👉 ENTRY - $100000.0 - $99500.0
👉 TARGET - $101500.0, $104500.0 (tested), $106250.0 & $108000.0+
👉 SL - $98000.0

🚨 LEVERAGE - 1x
📊 R:R - 1:2.7 | Risk 1% of account
🧪 PAPER · CANDLE12_V2 · 1h
⏱ Valid 3 bars · Not financial advice"""


def test_golden_long():
    c = cfg()
    s = sig()
    plan, err = build_plan(s, c)
    assert err is None
    assert plan["entry_mid"] == 99750.0 and plan["r_unit"] == 1750.0 and not plan["zone_capped_by_sl"]
    assert render_alert(s, plan, c.status_for(s.source), c) == GOLDEN_LONG


GOLDEN_SHORT = """✅ SWING TRADE - ETH
🏮 TYPE - SHORT

👉 ENTRY - $2500.00 - $2520.00
👉 TARGET - $2460.00, $2380.00 (tested), $2330.00 & $2280.00+
👉 SL - $2560.00

🚨 LEVERAGE - 1x
📊 R:R - 1:2.6 | Risk 1% of account
🧪 PAPER · LCSC_V1 · 4h
⏱ Valid 3 bars · Not financial advice"""


def test_golden_short_zone_low_to_high():
    c = cfg()
    s = sig(source="LCSC_FWD3", strategy_id="LCSC_V1", symbol="ETHUSDT", side="SHORT", timeframe="4h",
            signal_close=2500.0, atr=40.0, stop=2560.0, tested_target=2380.0)
    plan, err = build_plan(s, c)
    assert err is None and plan["entry_mid"] == pytest.approx(2510.0) and plan["r_unit"] == pytest.approx(50.0)
    assert render_alert(s, plan, "PAPER", c) == GOLDEN_SHORT


GOLDEN_TICK = """✅ INTRADAY TRADE - ARB
🏮 TYPE - SHORT

👉 ENTRY - $0.1891 - $0.1897
👉 TARGET - $0.1882, $0.1849 (tested), $0.1837 & $0.1825+
👉 SL - $0.1906

🚨 LEVERAGE - 2x
📊 R:R - 1:3.8 | Risk 1% of account
🧪 KILLED · COMBO_01 · 1h
⏱ Valid 3 bars · Not financial advice"""


def test_golden_tick_rounding():
    c = cfg()
    s = sig(source="PASSED_BOOK", strategy_id="COMBO_01", symbol="ARBUSDT", side="SHORT",
            signal_close=0.18909, atr=0.00123, stop=0.19058617338710704, tested_target=0.1849)
    plan, err = build_plan(s, c)
    assert err is None
    assert render_alert(s, plan, "KILLED", c) == GOLDEN_TICK
    assert round_to_tick(0.18915, 0.0001) == pytest.approx(0.1892)  # half up
    assert fmt_price(82699.04, 0.1) == "82699.0" and fmt_price(1.23456, 1.0) == "1"


def test_entry_zone_never_closer_than_half_the_stop_to_the_sl():
    c = cfg()
    s = sig(signal_close=100.0, atr=10.0, stop=98.0, tested_target=104.0, symbol="ETHUSDT")
    plan, _ = build_plan(s, c)
    assert plan["zone_capped_by_sl"] and plan["zone_lo"] == pytest.approx(99.0) and plan["zone_hi"] == 100.0
    assert plan["entry_mid"] == pytest.approx(99.5) and plan["r_unit"] == pytest.approx(1.5)


GOLDEN_CAP = """✅ INTRADAY TRADE - ETH
🏮 TYPE - LONG

👉 ENTRY - $100.00 - $99.90
👉 TARGET - $100.20, $100.50 (tested), $100.75 & $101.00+
👉 SL - $99.70

🚨 LEVERAGE - 3x
📊 R:R - 1:2.2 | Risk 0.75% of account
🧪 PAPER · CANDLE12_V2 · 1h
⏱ Valid 3 bars · Not financial advice"""


def test_golden_leverage_cap_shows_real_risk():
    c = cfg(round_trip=0.0001)
    s = sig(symbol="ETHUSDT", signal_close=100.0, atr=0.2, stop=99.7, tested_target=100.5)
    plan, err = build_plan(s, c)
    assert err is None and plan["leverage_cap_bound"] and plan["leverage"] == 3
    assert plan["leverage_needed"] == pytest.approx(3.998, abs=1e-3)
    assert render_alert(s, plan, "PAPER", c) == GOLDEN_CAP


def test_leverage_is_ceil_and_at_least_one():
    c = cfg()
    wide, _ = build_plan(sig(stop=90000.0, atr=1000.0), c)  # stop far away: 1x, risk stays 1%
    assert wide["leverage"] == 1 and wide["risk_pct_shown"] == 1.0
    two, _ = build_plan(sig(symbol="ETHUSDT", signal_close=100.0, atr=0.4, stop=99.0, tested_target=103.0), c)
    # mid 99.9, R 0.9 -> stop 0.9009% -> 1.11 -> 2x
    assert two["leverage"] == 2 and not two["leverage_cap_bound"]


def test_missing_inputs_are_skip_codes():
    c = cfg()
    assert build_plan(sig(symbol="XYZUSDT"), c) == (None, "NO_TICK_SIZE")
    assert build_plan(sig(stop=None), c) == (None, "NO_ENGINE_STOP")
    assert build_plan(sig(atr=None), c) == (None, "NO_MARKET_DATA")
    assert build_plan(sig(stop=101000.0), c) == (None, "INVALID_STOP")
    assert build_plan(sig(tested_target=None), c) == (None, "NO_TESTED_TARGET")
    fb = cfg(no_tested_target={"action": "fallback_r", "fallback_t2_r": 2.0})
    plan, err = build_plan(sig(tested_target=None), fb)
    assert err is None and not plan["t2_tested"] and plan["rr_tested"] == 2.0
    assert "(untested)" in render_alert(sig(tested_target=None), plan, "PAPER", fb)


# --- pipeline rules ----------------------------------------------------------------------------
def pipe(tmp_path, c=None, data=None, **kw):
    return AlertPipeline(c or cfg(), out_dir=tmp_path, data=data or MemoryData(), **kw)


def rows(tmp_path):
    return [json.loads(x) for x in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]


def test_dry_run_renders_preview_and_logs_with_journal_link(tmp_path):
    p = pipe(tmp_path)
    s = sig()
    row = p.handle(s, s.bar_close + timedelta(minutes=2))
    assert row["code"] == "DRY_RUN" and row["kind"] == "ALERT"
    assert GOLDEN_LONG in (tmp_path / "preview.md").read_text(encoding="utf-8")
    logged = rows(tmp_path)[0]
    assert logged["journal_ref"] == {"file": "decision_journal.jsonl", "line": 7}
    assert logged["alert_id"] == s.alert_id and logged["mode"] == "dry_run"


def test_cost_skip(tmp_path):
    p = pipe(tmp_path)
    s = sig(symbol="ETHUSDT", signal_close=100.0, atr=0.2, stop=99.7, tested_target=100.5)
    row = p.handle(s, s.bar_close)
    # Stop 0.25% < 3 x 0.18% round trip.
    assert row["code"] == "SKIPPED_COST" and row["flags"]["cost"] is True
    assert "WOULD SEND" not in (tmp_path / "preview.md").read_text(encoding="utf-8")


def test_late_after_15_minutes(tmp_path):
    p = pipe(tmp_path)
    on_time = sig(signal_bar=T0)
    late = sig(signal_bar=T0 + timedelta(hours=1))
    assert p.handle(on_time, on_time.bar_close + timedelta(minutes=15))["code"] == "DRY_RUN"
    row = p.handle(late, late.bar_close + timedelta(minutes=15, seconds=1))
    assert row["code"] == "LATE" and row["flags"]["late"] and row["lag_minutes"] > 15


def test_killed_suppressed_unless_allowed(tmp_path):
    s = sig(source="PASSED_BOOK", strategy_id="COMBO_01")
    assert pipe(tmp_path / "a").handle(s, s.bar_close)["code"] == "SUPPRESSED_KILLED"
    assert pipe(tmp_path / "b", cfg(allow_killed=True)).handle(s, s.bar_close)["code"] == "DRY_RUN"


def test_dedup_per_strategy_coin_side_bar(tmp_path):
    p = pipe(tmp_path)
    s = sig()
    assert p.handle(s, s.bar_close)["code"] == "DRY_RUN"
    assert p.handle(sig(), s.bar_close)["code"] == "DUPLICATE"
    assert p.handle(sig(strategy_id="OTHER"), s.bar_close)["code"] == "DRY_RUN"
    assert p.handle(sig(signal_bar=T0 + timedelta(hours=1)), s.bar_close + timedelta(hours=1))["code"] == "DRY_RUN"


def test_rate_limit_20_per_hour(tmp_path):
    p = pipe(tmp_path)
    now = T0 + timedelta(hours=1, minutes=1)
    codes = [p.handle(sig(strategy_id=f"S{i}"), now)["code"] for i in range(21)]
    assert codes[:20] == ["DRY_RUN"] * 20 and codes[20] == "RATE_LIMITED"
    later = sig(strategy_id="S99", signal_bar=T0 + timedelta(hours=1))
    assert p.handle(later, later.bar_close + timedelta(minutes=1))["code"] == "DRY_RUN"


# --- follow-ups --------------------------------------------------------------------------------
def test_expiry_after_validity_bars():
    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    tr = new_tracker(s, plan, c, sent=True)
    t = s.bar_close
    evs = []
    for k in range(3):  # never trades into 99,500-100,000
        evs += step(tr, bar(t + timedelta(hours=k), 100500, 101000, 100200, 100600))
    assert [e["kind"] for e in evs] == ["expired"]
    assert evs[0]["at"] == (t + timedelta(hours=3)).isoformat()
    assert render_followup(evs[0], tr) == "❌ NOT FILLED - expired"


def test_expiry_with_missing_bars():
    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    tr = new_tracker(s, plan, c, sent=True)
    assert expire_if_due(tr, s.bar_close + timedelta(hours=3)) == []  # the third bar may still arrive
    assert [e["kind"] for e in expire_if_due(tr, s.bar_close + timedelta(hours=4))] == ["expired"]


def test_fill_targets_and_result_in_r():
    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    tr = new_tracker(s, plan, c, sent=True)
    t = s.bar_close
    e1 = step(tr, bar(t, 100200, 100300, 99700, 99900))
    assert [e["kind"] for e in e1] == ["fill"] and e1[0]["price"] == 100000.0
    e2 = step(tr, bar(t + timedelta(hours=1), 99900, 104600, 99800, 104000))
    assert [(e["kind"], e.get("target")) for e in e2] == [("target", 1), ("target", 2), ("final", None)]
    assert e2[-1]["r"] == pytest.approx((104500 - 100000) / 1750)
    texts = [render_followup(e, tr) for e in e1 + e2]
    assert texts == [
        "✅ ENTRY FILLED - BTC LONG @ $100000.0",
        "🎯 T1 HIT - BTC @ $101500.0 (+0.9R)",
        "🎯 T2 HIT - BTC @ $104500.0 (+2.6R)",
        "🏁 RESULT - BTC LONG +2.6R (tested exit: T2)",
    ]


def test_stop_and_target_on_one_bar_counts_as_stop():
    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    tr = new_tracker(s, plan, c, sent=True)
    t = s.bar_close
    step(tr, bar(t, 99800, 99900, 99600, 99700))
    evs = step(tr, bar(t + timedelta(hours=1), 99700, 102000, 97000, 98500))
    assert [e["kind"] for e in evs] == ["sl", "final"] and evs[1]["exit"] == "SL"
    assert render_followup(evs[0], tr) == "🛑 SL HIT - BTC @ $98000.0 (-1.0R)"


def test_pipeline_followups_and_daily_summary(tmp_path):
    s = sig()
    t = s.bar_close
    data = MemoryData({("BTCUSDT", "1h"): [bar(t, 100200, 100300, 99700, 99900),
                                           bar(t + timedelta(hours=1), 99700, 99800, 97000, 98500)]})
    p = pipe(tmp_path, data=data)
    p.handle(s, t + timedelta(minutes=1))
    p.state["last_summary_day"] = (T0.date() - timedelta(days=1)).isoformat()
    assert p.update_trackers(t + timedelta(hours=3)) == 3
    out = p.maybe_daily_summary(datetime(2026, 10, 21, 0, 0, 30, tzinfo=timezone.utc))
    assert out == [
        "📅 DAILY SUMMARY - 2026-10-20 UTC\n"
        "Alerts sent: 1 | Filled: 1\n"
        "Wins: 0 | Losses: 1 | Total R: -1.1\n"
        "PAPER: sent 1, filled 1, W/L 0/1, R -1.1, not sent 0"
    ]
    kinds = [(r["kind"], r.get("event")) for r in rows(tmp_path)]
    assert kinds == [("ALERT", None), ("FOLLOWUP", "fill"), ("FOLLOWUP", "sl"), ("FOLLOWUP", "final"), ("SUMMARY", None)]
    assert all(r["reply_to"] == s.alert_id for r in rows(tmp_path) if r["kind"] == "FOLLOWUP")


# --- delivery: reuses the app's Telegram sender, replies to the alert (mock transport, no network) ----------
def test_live_mode_sends_and_replies(tmp_path):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 100 + len(sent)}})

    sender = ReplyingTelegramSender(bot_token="t", chat_id="c", timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    s = sig()
    t = s.bar_close
    data = MemoryData({("BTCUSDT", "1h"): [bar(t, 100200, 100300, 99700, 99900)]})
    p = pipe(tmp_path, cfg(enabled=True, dry_run=False), data=data, sender=sender)
    assert p.delivering
    assert p.handle(s, t)["code"] == "SENT"
    p.update_trackers(t + timedelta(hours=1))
    assert sent[0]["text"] == GOLDEN_LONG and "reply_to_message_id" not in sent[0]
    assert sent[1]["reply_to_message_id"] == 101 and sent[1]["text"].startswith("✅ ENTRY FILLED")
    assert not (tmp_path / "preview.md").exists()


def test_dry_run_never_builds_a_sender(tmp_path):
    from app.alerts.runner import _sender

    assert _sender(AlertConfig(), {"TELEGRAM_BOT_TOKEN": "x", "TRADE_ALERTS_TELEGRAM_CHAT_ID": "y"}) is None
    p = pipe(tmp_path, cfg(enabled=True, dry_run=True), sender=None)
    assert not p.delivering


# --- sources -------------------------------------------------------------------------------------
def _closed_hours(symbol, end_open, n=40, base=100.0):
    return [bar(end_open - timedelta(hours=n - 1 - i), base, base + 1, base - 1, base) for i in range(n)]


def test_journal_rows_become_signals_with_engine_stop_and_tp1(tmp_path):
    bar_t = T0
    jl = [
        {"book": "PASSED_BOOK", "event": "signal", "outcome": "opened", "position_id": "p1", "strategy_id": "COMBO_01",
         "direction": "SHORT", "symbol": "ARBUSDT", "timeframe": "1h", "setup_bar_time": bar_t.isoformat(),
         "recorded_at": "2026-10-20T18:05:31+00:00", "ts": "2026-10-20T18:05:31+00:00"},
        {"book": "PASSED_BOOK", "event": "signal", "outcome": "not_opened", "reason": "symbol_has_open_position",
         "strategy_id": "COMBO_02_LOCAL", "direction": "SHORT", "symbol": "ARBUSDT", "timeframe": "1h",
         "setup_bar_time": bar_t.isoformat(), "ts": "2026-10-20T18:05:32+00:00"},
        {"book": "PASSED_BOOK", "event": "bar_evaluated", "symbol": "ARBUSDT", "bar": bar_t.isoformat(), "ts": "2026-10-20T18:05:33+00:00"},
        {"book": "COMBO_02_V1", "event": "opened", "symbol": "BTCUSDT", "bar": bar_t.isoformat(), "entry_price": 101.0,
         "stop_price": 97.0, "ts": "2026-10-20T18:03:00+00:00"},
        {"book": "CANDLE12_V2", "event": "entry", "key": "k1", "entry_time": bar_t.isoformat(), "entry_price": 100.5,
         "stop_price": 96.0, "ts": "2026-10-20T18:04:00+00:00"},
    ]
    lines = [(i, json.dumps(r)) for i, r in enumerate(jl)]
    trades = [
        {"id": "p1", "symbol": "ARBUSDT", "stop_price": 102.0, "tp1_price": 95.0, "status": "CLOSED",
         "signal_snippet": {"source": "PASSED_CAP_BOOK"}},
        {"id": "v1", "symbol": "BTCUSDT", "stop_price": 97.0, "tp1_price": 109.0, "status": "CLOSED",
         "source_candle_ts": bar_t.isoformat(), "signal_snippet": {"source": "V1_PAPER_WATCHER"}},
    ]
    state = tmp_path / "c12.json"
    state.write_text(json.dumps({"open": {"key": "k1", "tp1": 108.0}, "closed": []}), encoding="utf-8")
    data = MemoryData({(s, "1h"): _closed_hours(s, bar_t) for s in ("ARBUSDT", "BTCUSDT")}, trades)
    items = list(journal_items(lines, first_line_no=11, data=data, cfg=cfg(), candle12_state=state))
    assert len(items) == 4
    a, b, v, c12 = items
    assert (a.source, a.stop, a.tested_target, a.signal_close, a.journal_ref["line"]) == ("PASSED_BOOK", 102.0, 95.0, 100.0, 11)
    assert a.atr == pytest.approx(2.0) and a.journal_ref["sha256"]
    assert isinstance(b, SkipRecord) and b.code == "NOT_OPENED" and "symbol_has_open_position" in b.reason
    assert (v.source, v.stop, v.tested_target, v.position_id) == ("COMBO_02_V1", 97.0, 109.0, "v1")
    assert (c12.source, c12.stop, c12.tested_target, c12.journal_ref["line"]) == ("CANDLE12_V2", 96.0, 108.0, 15)


def test_fwd3_target_is_the_contracts_2r_from_signal_close(tmp_path):
    run = {"run_at": "2026-10-16T12:20:12+00:00", "row_hash": "abc", "trades": [],
           "candidates": [{"signal_time": "2026-10-16T11:00:00+00:00", "fate": "PENDING_NEXT_BAR",
                           "close": 2476.41, "invalidation": 2403.82, "atr": 19.956}]}
    p = tmp_path / "runs.jsonl"
    p.write_text(json.dumps(run) + "\n", encoding="utf-8")
    items, last = fwd3_items(p, after_line=0, data=MemoryData(), cfg=cfg(), seen=set())
    assert last == 1 and len(items) == 1
    s = items[0]
    # The recorded trade for this signal has target 2621.59 (forward_out/runs.jsonl).
    assert s.tested_target == pytest.approx(2621.59) and s.stop == 2403.82 and s.extra["market_source"] == "record"
    lag = (s.decided_at - s.bar_close).total_seconds() / 60
    assert lag > 15  # FWD-3 records land at HH:20: always LATE under the 15-minute rule


# --- watch loop --------------------------------------------------------------------------------
def test_watch_starts_at_the_end_defers_until_the_position_exists(tmp_path):
    from app.alerts.runner import poll_once, prime

    live = tmp_path / "live"
    journal = live / "data" / "paper" / "decision_journal.jsonl"
    journal.parent.mkdir(parents=True)
    old = {"book": "PASSED_BOOK", "event": "signal", "outcome": "opened", "position_id": "old", "strategy_id": "COMBO_01",
           "direction": "SHORT", "symbol": "BTCUSDT", "timeframe": "1h", "setup_bar_time": "2026-10-19T00:00:00+00:00",
           "ts": "2026-10-19T01:05:00+00:00"}
    journal.write_text(json.dumps(old) + "\n", encoding="utf-8")
    paths = {"journal": journal, "candle12": live / "none.json", "fwd1": live / "none1", "fwd3": live / "none3"}
    data = MemoryData({("BTCUSDT", "1h"): _closed_hours("BTCUSDT", T0)})
    p = pipe(tmp_path / "out", cfg(allow_killed=True), data=data)
    prime(p, paths)
    now = T0 + timedelta(hours=1, minutes=5)
    assert poll_once(p, paths, now).get("DRY_RUN") is None  # history is not replayed
    new = dict(old, position_id="p2", setup_bar_time=T0.isoformat(), ts=(T0 + timedelta(hours=1, minutes=4)).isoformat())
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(new) + "\n")
    assert poll_once(p, paths, now)["deferred"] == 1  # paper position not readable yet
    data.trades.append({"id": "p2", "symbol": "BTCUSDT", "stop_price": 102.0, "tp1_price": 95.0, "status": "OPEN",
                        "signal_snippet": {"source": "PASSED_CAP_BOOK"}})
    counts = poll_once(p, paths, now + timedelta(seconds=30))
    assert counts["DRY_RUN"] == 1 and "deferred" not in counts
    alert = [r for r in rows(tmp_path / "out") if r["kind"] == "ALERT"][0]
    assert alert["journal_ref"]["line"] == 2 and alert["position_id"] == "p2"
