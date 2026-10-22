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
from app.alerts.sources import SkipRecord, fwd1_items, fwd3_items, journal_items
from app.alerts.tracker import new_tracker, step, expire_if_due

T0 = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
STATUS = {
    "default": "UNPROVEN",
    "sources": {
        "PASSED_BOOK": {"status": "KILLED"},
        # Synthetic map for the pipeline unit tests (the shipped map is tested on its own below).
        "CANDLE12_V2": {"status": "PAPER"},
        "COMBO_02_V1": {"status": "PAPER"},
        "LCSC_FWD3": {"status": "PAPER"},
        "BOOK16_FWD1": {"status": "PAPER"},
        "FP": {"status": "FORWARD-PASSED"},
    },
}
TICKS = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "ARBUSDT": 0.0001}
# Owner ruling D117: the exact first line of every message whose status is not FORWARD-PASSED.
PREFIX = "⚠️ UNPROVEN, for observation only, not for trading\n"


def cfg(round_trip: float | None = 0.0018, **over) -> AlertConfig:
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw.update(over)
    return AlertConfig(raw, status_map=STATUS, ticks=TICKS, round_trip_cost=round_trip)


def sig(**kw) -> Signal:
    base = dict(source="CANDLE12_V2", strategy_id="CANDLE12_V2", symbol="BTCUSDT", side="LONG", timeframe="1h",
                signal_bar=T0, decided_at=T0 + timedelta(hours=1, minutes=2), stop=98000.0, tested_target=104500.0,
                signal_close=100000.0, atr=1000.0, journal_ref={"file": "decision_journal.jsonl", "line": 7},
                # Owner ruling D145: a paper-book signal alerts only when its book opened it.
                extra={"live_outcome": "opened"})
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
    assert c.status_for("COMBO_02_V1") == "PAPER"  # owner ruling D51a
    # Owner ruling D120: CANDLE12_V2 (BTC-only) is dropped from alerts.
    assert "CANDLE12_V2" not in c.status_map["sources"] and "CANDLE12_V2" in c.status_map["dropped_sources"]
    assert c.status_for("BOOK16_FWD1") == "PAPER" and c.status_for("LCSC_FWD3") == "PAPER"
    assert c.status_for("SOMETHING_NEW") == "UNPROVEN"


# --- golden messages ---------------------------------------------------------------------------
GOLDEN_LONG = """⚠️ UNPROVEN, for observation only, not for trading
✅ INTRADAY TRADE - BTC
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


GOLDEN_SHORT = """⚠️ UNPROVEN, for observation only, not for trading
✅ SWING TRADE - ETH
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


GOLDEN_TICK = """⚠️ UNPROVEN, for observation only, not for trading
✅ INTRADAY TRADE - ARB
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


GOLDEN_CAP = """⚠️ UNPROVEN, for observation only, not for trading
✅ INTRADAY TRADE - ETH
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


def test_screener_cost_band_uses_tp1_to_stop(tmp_path):
    """Owner D63: |TP1 - stop| under 3x round trip rejects a screener signal.

    The stop itself is wide, so the older stop-distance check would let it through.
    A passed-book signal with the same prices is not a screener row.
    """
    c = cfg()
    tight = sig(source="SCREENER", strategy_id="SCREENER_SETUP", symbol="ETHUSDT",
                signal_close=100.0, atr=2.0, stop=90.0, tested_target=90.4, target_tested=False)
    plan, err = build_plan(tight, c)
    assert err is None and plan["cost_band"] and plan["cost_skip"]
    assert plan["stop_frac"] > plan["cost_floor_frac"]
    row = pipe(tmp_path).handle(tight, tight.bar_close)
    assert row["code"] == "SKIPPED_COST"
    wide = sig(source="SCREENER", strategy_id="SCREENER_SETUP", symbol="ETHUSDT",
               signal_close=100.0, atr=2.0, stop=90.0, tested_target=110.0, target_tested=False)
    wide_plan, wide_err = build_plan(wide, c)
    assert wide_err is None and wide_plan["cost_band"] is False and wide_plan["cost_skip"] is False
    other = sig(source="CANDLE12_V2", symbol="ETHUSDT", signal_close=100.0, atr=2.0,
                stop=90.0, tested_target=90.4)
    other_plan, other_err = build_plan(other, c)
    assert other_err == "INVALID_TARGET"


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
    assert render_followup(evs[0], tr) == PREFIX + "❌ NOT FILLED - expired"


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
    assert texts == [PREFIX + x for x in [
        "✅ ENTRY FILLED - BTC LONG @ $100000.0",
        "🎯 T1 HIT - BTC @ $101500.0 (+0.9R)",
        "🎯 T2 HIT - BTC @ $104500.0 (+2.6R)",
        "🏁 RESULT - BTC LONG +2.6R (tested exit: T2)",
    ]]


def test_stop_and_target_on_one_bar_counts_as_stop():
    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    tr = new_tracker(s, plan, c, sent=True)
    t = s.bar_close
    step(tr, bar(t, 99800, 99900, 99600, 99700))
    evs = step(tr, bar(t + timedelta(hours=1), 99700, 102000, 97000, 98500))
    assert [e["kind"] for e in evs] == ["sl", "final"] and evs[1]["exit"] == "SL"
    assert render_followup(evs[0], tr) == PREFIX + "🛑 SL HIT - BTC @ $98000.0 (-1.0R)"


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
    # Owner ruling D149: CANDLE12_V2 trades are not in paper_trades, so its opened alert has no ledger row.
    assert out == [
        PREFIX + "📅 DAILY SUMMARY - 2026-10-20 UTC\n"
        "Paper trades from sent alerts (paper ledger): 1\n"
        "PAPER: 1 alerts, open 0, closed 0, W/L 0/0, R +0.00, pnl +$0.00, ledger: not found 1\n"
        "- CANDLE12_V2 BTC LONG 1h bar 10-20 17:00: ledger: not found\n"
        "\n"
        "alert levels touched (not paper trades)\n"
        "Alerts sent: 1 | Entry filled: 1\n"
        "TP/SL hit: wins 0 | losses 1 | Tracked R: -1.1\n"
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

    sender = ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    s = sig()
    t = s.bar_close
    data = MemoryData({("BTCUSDT", "1h"): [bar(t, 100200, 100300, 99700, 99900)]})
    p = pipe(tmp_path, cfg(enabled=True, dry_run=False), data=data, sender=sender)
    assert p.delivering
    assert p.handle(s, t)["code"] == "SENT"
    p.update_trackers(t + timedelta(hours=1))
    assert sent[0]["text"] == GOLDEN_LONG and "reply_to_message_id" not in sent[0]
    assert sent[1]["reply_to_message_id"] == 101 and sent[1]["text"].startswith(PREFIX + "✅ ENTRY FILLED")
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
    data = MemoryData({(s, "1h"): _closed_hours(s, bar_t) for s in ("ARBUSDT", "BTCUSDT")}, trades)
    items = list(journal_items(lines, first_line_no=11, data=data, cfg=cfg()))
    assert len(items) == 3  # the CANDLE12_V2 row is ignored (owner ruling D120)
    a, b, v = items
    assert (a.source, a.stop, a.tested_target, a.signal_close, a.journal_ref["line"]) == ("PASSED_BOOK", 102.0, 95.0, 100.0, 11)
    assert a.atr == pytest.approx(2.0) and a.journal_ref["sha256"]
    assert isinstance(b, SkipRecord) and b.code == "NOT_OPENED" and "symbol_has_open_position" in b.reason
    assert (v.source, v.stop, v.tested_target, v.position_id) == ("COMBO_02_V1", 97.0, 109.0, "v1")


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
    paths = {"journal": journal, "fwd1": live / "none1", "fwd3": live / "none3"}
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


# --- forward runners (owner ruling D51b) ---------------------------------------------------------
GOLDEN_FWD3 = """⚠️ UNPROVEN, for observation only, not for trading
✅ INTRADAY TRADE - ETH
🏮 TYPE - LONG

👉 ENTRY - $2476.41 - $2466.43
👉 TARGET - $2539.02, $2621.59 (tested), $2689.19 & $2756.79+
👉 SL - $2403.82

🚨 LEVERAGE - 1x
📊 R:R - 1:2.2 | Risk 1% of account
🧪 PAPER · LCSC_V1 · 1h
⏱ Valid 3 bars · Not financial advice
⏱ signal +20m"""


def _fwd3_sig():
    # The real FWD-3 candidate of 2026-10-16 11:00 (forward_out/runs.jsonl), recorded at 12:20:12 UTC.
    close, stop = 2476.41, 2403.82
    return sig(source="LCSC_FWD3", strategy_id="LCSC_V1", symbol="ETHUSDT", signal_bar="2026-10-16T11:00:00+00:00",
               decided_at="2026-10-16T12:20:12+00:00", signal_close=close, atr=19.956005821806066, stop=stop,
               tested_target=close + 2 * (close - stop))


def test_forward_runner_late_limit_is_45_minutes():
    c = AlertConfig()
    assert c.late_minutes_for("LCSC_FWD3") == 45 and c.late_minutes_for("BOOK16_FWD1") == 45
    assert c.late_minutes_for("PASSED_BOOK") == 15 and c.late_minutes_for("CANDLE12_V2") == 15


def test_golden_fwd3_alert_shows_minutes_since_bar_close(tmp_path):
    p = pipe(tmp_path)
    s = _fwd3_sig()
    row = p.handle(s, s.decided_at)
    assert row["code"] == "DRY_RUN" and not row["flags"]["late"] and row["lag_minutes"] == pytest.approx(20.2)
    assert row["text"] == GOLDEN_FWD3
    assert GOLDEN_FWD3 in (tmp_path / "preview.md").read_text(encoding="utf-8")


def test_forward_runner_late_after_45_minutes(tmp_path):
    p = pipe(tmp_path)
    s = _fwd3_sig()
    assert p.handle(s, s.bar_close + timedelta(minutes=45))["code"] == "DRY_RUN"
    late = _fwd3_sig()
    late.signal_bar = late.signal_bar + timedelta(hours=1)
    row = p.handle(late, late.bar_close + timedelta(minutes=45, seconds=1))
    assert row["code"] == "LATE" and row["flags"]["late"]


def test_fwd1_uses_the_45_minute_limit_and_signal_line(tmp_path):
    fb = cfg(no_tested_target={"action": "fallback_r", "fallback_t2_r": 2.0})
    p = pipe(tmp_path, fb)
    # ETH: FWD-1 BTC trades are excluded from alerts (owner ruling D120).
    s = sig(source="BOOK16_FWD1", strategy_id="BOOK16_4H", timeframe="4h", tested_target=None, symbol="ETHUSDT",
            signal_bar="2026-10-20T00:00:00+00:00", decided_at="2026-10-20T04:10:06+00:00")
    row = p.handle(s, s.decided_at)
    assert row["code"] == "DRY_RUN" and row["text"].endswith("⏱ signal +10m")
    assert "SWING TRADE - ETH" in row["text"] and "(untested)" in row["text"]
    late = sig(source="BOOK16_FWD1", strategy_id="BOOK16_4H", timeframe="4h", tested_target=None, symbol="ETHUSDT",
               signal_bar="2026-10-20T04:00:00+00:00", decided_at="2026-10-20T08:46:00+00:00")
    assert p.handle(late, late.decided_at)["code"] == "LATE"
    # Not a forward runner: no extra line.
    other = sig()
    assert "signal +" not in p.handle(other, other.bar_close + timedelta(minutes=5))["text"]


# --- P14 journal detail rows (owner ruling D51c) -----------------------------------------------------
GOLDEN_SCREENER = """⚠️ UNPROVEN, for observation only, not for trading
✅ SCALP TRADE - IOST
🏮 TYPE - LONG

👉 ENTRY - $1.0000 - $0.9950
👉 TARGET - $1.0450, $1.1200 (untested), $1.1675 & $1.2150+
👉 SL - $0.9500

🚨 LEVERAGE - 1x
📊 R:R - 1:2.6 | Risk 1% of account
🧪 UNPROVEN · SCREENER_SETUP · 15m
⏱ Valid 3 bars · Not financial advice"""

SCREENER_ROW = {"book": "SCREENER", "event": "signal", "source": "SETUP_SIGNAL_SERVICE", "strategy_id": "SCREENER_SETUP",
                "symbol": "IOSTUSDT", "direction": "LONG", "timeframe": "15m", "setup_bar_time": "2026-10-21T05:00:00+00:00",
                "status": "LONG_ENTRY_CANDIDATE", "outcome": "signal", "stop_price": 0.95, "tp1": 1.12, "tp1_r": 2.4,
                "signal_entry_price": 1.0, "detail_version": 1, "ts": "2026-10-21T05:15:04+00:00"}


def _m15(symbol, end_open, n=40):
    return [bar(end_open - timedelta(minutes=15 * (n - 1 - i)), 1.0, 1.005, 0.995, 1.0) for i in range(n)]


def test_golden_screener_alert(tmp_path):
    c = cfg()
    c.ticks["IOSTUSDT"] = 0.0001
    data = MemoryData({("IOSTUSDT", "15m"): _m15("IOSTUSDT", datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc))})
    (item,) = list(journal_items([(0, json.dumps(SCREENER_ROW))], first_line_no=42, data=data, cfg=c))
    assert item.source == "SCREENER" and not item.target_tested and item.atr == pytest.approx(0.01)
    row = pipe(tmp_path, c, data=data).handle(item, item.decided_at)
    assert row["code"] == "DRY_RUN" and row["status"] == "UNPROVEN" and row["text"] == GOLDEN_SCREENER
    assert row["journal_ref"]["line"] == 42 and row["journal_ref"]["book"] == "SCREENER"


GOLDEN_NOT_OPENED = """⚠️ UNPROVEN, for observation only, not for trading
❌ NOT OPENED: sticky_bos_skip
✅ INTRADAY TRADE - ETH
🏮 TYPE - LONG

👉 ENTRY - $2500.00 - $2487.50
👉 TARGET - $2537.50, $2612.50 (tested), $2656.25 & $2700.00+
👉 SL - $2450.00

🚨 LEVERAGE - 1x
📊 R:R - 1:2.7 | Risk 1% of account
🧪 PAPER · COMBO_02_V1 · 1h
⏱ Valid 3 bars · Not financial advice"""


def test_golden_not_opened_signal_is_a_skip_unless_send_not_opened(tmp_path):
    """Owner ruling D145: a V1 signal the book did not open is a NOT_OPENED skip carrying the paper reason;
    send_not_opened=true sends it, marked on the line after the D117 line."""
    jrow = {"book": "COMBO_02_V1", "event": "signal", "outcome": "not_opened", "reason": "sticky_bos_skip",
            "source": "V1_PAPER_WATCHER", "strategy_id": "COMBO_02_V1", "symbol": "ETHUSDT", "direction": "LONG",
            "timeframe": "1h", "setup_bar_time": T0.isoformat(), "stop_price": 2450.0, "tp1": 2612.5,
            "signal_entry_price": 2500.0, "detail_version": 1, "ts": (T0 + timedelta(hours=1, minutes=2)).isoformat()}
    bars_ = [bar(T0 - timedelta(hours=39 - i), 2500.0, 2512.5, 2487.5, 2500.0) for i in range(40)]
    data = MemoryData({("ETHUSDT", "1h"): bars_})
    (item,) = list(journal_items([(0, json.dumps(jrow))], first_line_no=9, data=data, cfg=cfg()))
    assert item.atr == pytest.approx(25.0)
    row = pipe(tmp_path / "a", data=data).handle(item, item.decided_at)
    assert (row["kind"], row["code"], row["reason"]) == ("SKIP", "NOT_OPENED", "sticky_bos_skip")
    assert row["flags"]["not_opened"] == "sticky_bos_skip"
    assert row["paper_outcome"] == "not_opened" and row["paper_skip_reason"] == "sticky_bos_skip"
    assert "WOULD SEND" not in (tmp_path / "a" / "preview.md").read_text(encoding="utf-8")
    (item,) = list(journal_items([(0, json.dumps(jrow))], first_line_no=9, data=data, cfg=cfg()))
    row = pipe(tmp_path / "b", cfg(send_not_opened=True), data=data).handle(item, item.decided_at)
    assert row["code"] == "DRY_RUN" and row["kind"] == "ALERT" and row["text"] == GOLDEN_NOT_OPENED
    logged = rows(tmp_path / "b")[0]
    assert logged["paper_skip_reason"] == "sticky_bos_skip" and logged["journal_ref"]["line"] == 9


def test_not_opened_without_detail_is_still_a_skip():
    jrow = {"book": "PASSED_BOOK", "event": "signal", "outcome": "not_opened", "reason": "symbol_has_open_position",
            "strategy_id": "COMBO_01", "direction": "SHORT", "symbol": "ARBUSDT", "timeframe": "1h",
            "setup_bar_time": T0.isoformat(), "ts": (T0 + timedelta(hours=1)).isoformat()}
    (item,) = list(journal_items([(0, json.dumps(jrow))], first_line_no=1, data=MemoryData(), cfg=cfg()))
    assert isinstance(item, SkipRecord) and item.code == "NOT_OPENED"
    with_detail = dict(jrow, stop_price=102.0, tp1=95.0, source="PASSED_CAP_BOOK", detail_version=1)
    (sig_,) = list(journal_items([(0, json.dumps(with_detail))], first_line_no=1, data=MemoryData(), cfg=cfg()))
    assert isinstance(sig_, Signal) and sig_.stop == 102.0 and sig_.extra["skip_reason"] == "symbol_has_open_position"


def test_v1_signal_rows_with_detail():
    base = {"book": "COMBO_02_V1", "event": "signal", "source": "V1_PAPER_WATCHER", "strategy_id": "COMBO_02_V1",
            "symbol": "BTCUSDT", "direction": "LONG", "timeframe": "1h", "setup_bar_time": T0.isoformat(),
            "stop_price": 97.0, "tp1": 109.0, "detail_version": 1, "ts": (T0 + timedelta(hours=1)).isoformat()}
    opened = dict(base, outcome="opened", reason="")
    skipped = dict(base, outcome="not_opened", reason="sticky_bos_skip")
    items = list(journal_items([(0, json.dumps(opened)), (1, json.dumps(skipped))], first_line_no=1,
                               data=MemoryData(), cfg=cfg()))
    assert len(items) == 1 and items[0].extra["skip_reason"] == "sticky_bos_skip" and items[0].tested_target == 109.0


# --- owner ruling D117: observation prefix, sources, private chat only -------------------------------
D117_LINE = "⚠️ UNPROVEN, for observation only, not for trading"


def test_d117_prefix_constant_is_exact():
    from app.alerts.plan import OBSERVATION_PREFIX

    assert OBSERVATION_PREFIX == D117_LINE
    assert OBSERVATION_PREFIX.encode("utf-8") == (
        b"\xe2\x9a\xa0\xef\xb8\x8f UNPROVEN, for observation only, not for trading"
    )


@pytest.mark.parametrize("status", ["PAPER", "UNPROVEN", "KILLED", "", "SOMETHING_ELSE"])
def test_d117_every_non_forward_passed_alert_starts_with_the_line(status):
    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    text = render_alert(s, plan, status, c)
    lines = text.split("\n")
    assert lines[0] == D117_LINE and lines[1].startswith("✅ INTRADAY TRADE - BTC")
    assert text.count(D117_LINE) == 1


GOLDEN_FORWARD_PASSED = """✅ INTRADAY TRADE - BTC
🏮 TYPE - LONG

👉 ENTRY - $100000.0 - $99500.0
👉 TARGET - $101500.0, $104500.0 (tested), $106250.0 & $108000.0+
👉 SL - $98000.0

🚨 LEVERAGE - 1x
📊 R:R - 1:2.7 | Risk 1% of account
🧪 FORWARD-PASSED · CANDLE12_V2 · 1h
⏱ Valid 3 bars · Not financial advice"""


def test_d117_golden_forward_passed_has_no_prefix():
    c = cfg()
    s = sig(source="FP")
    plan, _ = build_plan(s, c)
    assert c.status_for("FP") == "FORWARD-PASSED"
    assert render_alert(s, plan, "FORWARD-PASSED", c) == GOLDEN_FORWARD_PASSED
    assert GOLDEN_LONG == D117_LINE + "\n" + GOLDEN_FORWARD_PASSED.replace("FORWARD-PASSED", "PAPER")


def test_d117_shipped_sources_and_statuses():
    c = AlertConfig()
    assert not c.allow_killed  # D51: KILLED stays off
    expected = {
        "COMBO_02_V1": "PAPER",  # V1
        "BOOK16_FWD1": "PAPER",  # FWD-1
        "LCSC_FWD3": "PAPER",  # FWD-3
        "SCREENER": "UNPROVEN",
        "PASSED_BOOK": "KILLED",
    }
    assert {k: c.status_for(k) for k in expected} == expected
    assert set(c.status_map["sources"]) == set(expected)
    assert "FORWARD-PASSED" not in {v["status"] for v in c.status_map["sources"].values()}
    ev = {k: v["evidence"] for k, v in c.status_map["sources"].items()}
    dropped = c.status_map["dropped_sources"]["CANDLE12_V2"]["evidence"]
    assert "KILLED.md" in dropped and "Gate 2" in dropped and "D117" in dropped and "D120" in dropped
    assert "Not wired" not in ev["SCREENER"] and "DECISION_JOURNAL_SIGNAL_DETAIL" in ev["SCREENER"]


def test_d117_every_shipped_source_alerts_with_the_prefix_and_killed_stays_off(tmp_path):
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    c = AlertConfig(raw, ticks=TICKS, round_trip_cost=0.0018)  # the shipped status map
    p = pipe(tmp_path, c)
    for i, source in enumerate(["COMBO_02_V1", "BOOK16_FWD1", "LCSC_FWD3", "SCREENER"]):
        s = sig(source=source, strategy_id=f"S{i}", symbol="ETHUSDT")  # BTC is excluded (D120)
        row = p.handle(s, s.bar_close + timedelta(minutes=1))
        assert row["code"] == "DRY_RUN" and row["text"].split("\n")[0] == D117_LINE, source
        assert row["status"] == ("UNPROVEN" if source == "SCREENER" else "PAPER")
    killed = sig(source="PASSED_BOOK", strategy_id="COMBO_01")
    assert p.handle(killed, killed.bar_close)["code"] == "SUPPRESSED_KILLED"
    preview = (tmp_path / "preview.md").read_text(encoding="utf-8")
    assert preview.count("```\n" + D117_LINE + "\n") == 5  # 4 would-send + the rendered KILLED preview


def test_d117_followups_and_summary_carry_the_prefix_unless_forward_passed():
    from app.alerts.plan import render_summary

    c = cfg()
    s = sig()
    plan, _ = build_plan(s, c)
    tr = new_tracker(s, plan, c, sent=True)
    ev = {"kind": "fill", "price": 100000.0}
    tr["status"] = "PAPER"
    assert render_followup(ev, tr) == D117_LINE + "\n✅ ENTRY FILLED - BTC LONG @ $100000.0"
    tr["status"] = "FORWARD-PASSED"
    assert render_followup(ev, tr) == "✅ ENTRY FILLED - BTC LONG @ $100000.0"
    g = {"sent": 1, "not_sent": 0, "filled": 0, "wins": 0, "losses": 0, "total_r": 0.0}
    assert render_summary("2026-10-21", {"PAPER": g}).split("\n")[0] == D117_LINE
    assert render_summary("2026-10-21", {}).split("\n")[0] == D117_LINE
    assert render_summary("2026-10-21", {"FORWARD-PASSED": g, "UNPROVEN": g}).split("\n")[0] == D117_LINE
    assert render_summary("2026-10-21", {"FORWARD-PASSED": g}).startswith("📅 DAILY SUMMARY")


@pytest.mark.parametrize("chat_id,private", [
    ("123456789", True), (" 42 ", True), ("-123456789", False), ("-1001234567890", False),
    ("@somechannel", False), ("c", False), ("", False), ("0", False), ("１２３", False), ("12.5", False),
])
def test_d117_private_chat_ids_only(chat_id, private):
    from app.alerts.sender import is_private_chat_id

    assert is_private_chat_id(chat_id) is private


@pytest.mark.parametrize("chat_id", ["-123456789", "-1001234567890", "@somechannel"])
def test_d117_group_or_channel_chat_never_sends(tmp_path, chat_id):
    from app.alerts.runner import _sender

    posted = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    sender = ReplyingTelegramSender(bot_token="t", chat_id=chat_id, timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert not sender.enabled and sender.send("x") is None
    p = pipe(tmp_path, cfg(enabled=True, dry_run=False), sender=sender)
    assert not p.delivering
    s = sig()
    assert p.handle(s, s.bar_close)["code"] == "DRY_RUN"
    assert posted == []
    env = {"TELEGRAM_BOT_TOKEN": "t", "TRADE_ALERTS_TELEGRAM_CHAT_ID": chat_id}
    assert _sender(cfg(enabled=True, dry_run=False), env) is None


def test_d117_private_chat_sender_posts_only_to_that_chat(tmp_path):
    from app.alerts.runner import _sender

    env = {"TELEGRAM_BOT_TOKEN": "t", "TRADE_ALERTS_TELEGRAM_CHAT_ID": "123456789"}
    built = _sender(cfg(enabled=True, dry_run=False), env)
    assert built is not None and built.enabled and built.chat_id == "123456789"
    # The live app's own chat id (TELEGRAM_CHAT_ID) is never read by the sidecar.
    assert _sender(cfg(enabled=True, dry_run=False), {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "123"}) is None
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    sender = ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert sender.send("a") == 7 and sender.send("b", reply_to=7) == 7
    assert [b["chat_id"] for b in bodies] == ["123456789", "123456789"]


# --- owner ruling D120 (D108: BTC is for non-directional strategies only) ------------------------------
D120_REASON = "D108/D120 BTC non-directional only"


def test_d120_shipped_symbol_exclusions():
    c = AlertConfig()
    assert c.symbol_exclusion_reason == D120_REASON
    for source in ("COMBO_02_V1", "BOOK16_FWD1", "SCREENER"):
        assert c.excluded_symbols(source) == frozenset({"BTCUSDT"}), source
        assert c.symbol_excluded(source, "btcusdt") and not c.symbol_excluded(source, "ETHUSDT")
    assert c.excluded_symbols("LCSC_FWD3") == frozenset() and c.excluded_symbols("PASSED_BOOK") == frozenset()
    assert "candle12_v2_state" not in c.raw["sources"]


@pytest.mark.parametrize("source,strategy,tf", [
    ("COMBO_02_V1", "COMBO_02_V1", "1h"), ("BOOK16_FWD1", "BOOK16_4H", "4h"), ("SCREENER", "SCREENER_SETUP", "15m"),
])
def test_d120_btc_signal_is_a_logged_skip_and_eth_alerts(tmp_path, source, strategy, tf):
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    c = AlertConfig(raw, ticks=TICKS, round_trip_cost=0.0018)  # the shipped status map and exclusions
    p = pipe(tmp_path, c)
    btc = sig(source=source, strategy_id=strategy, timeframe=tf, symbol="BTCUSDT")
    row = p.handle(btc, btc.bar_close + timedelta(minutes=1))
    assert (row["kind"], row["code"], row["reason"]) == ("SKIP", "EXCLUDED_SYMBOL", D120_REASON)
    assert row["symbol"] == "BTCUSDT" and "text" not in row and btc.alert_id not in p.state["trackers"]
    assert not (tmp_path / "preview.md").exists()  # never rendered
    eth = sig(source=source, strategy_id=strategy, timeframe=tf, symbol="ETHUSDT", signal_close=2500.0, atr=25.0,
              stop=2450.0, tested_target=2612.5)
    row = p.handle(eth, eth.bar_close + timedelta(minutes=1))
    assert row["kind"] == "ALERT" and row["code"] == "DRY_RUN" and row["text"].split("\n")[0] == D117_LINE
    logged = rows(tmp_path)
    assert [(r["symbol"], r["code"]) for r in logged] == [("BTCUSDT", "EXCLUDED_SYMBOL"), ("ETHUSDT", "DRY_RUN")]
    assert logged[0]["reason"] == D120_REASON


def test_d120_btc_screener_journal_row_is_excluded(tmp_path):
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    c = AlertConfig(raw, ticks=dict(TICKS, IOSTUSDT=0.0001), round_trip_cost=0.0018)
    btc_row = dict(SCREENER_ROW, symbol="BTCUSDT", stop_price=95000.0, tp1=112000.0, signal_entry_price=100000.0)
    data = MemoryData({("BTCUSDT", "15m"): _m15("BTCUSDT", datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc))})
    (item,) = list(journal_items([(0, json.dumps(btc_row))], first_line_no=3, data=data, cfg=c))
    row = pipe(tmp_path, c, data=data).handle(item, item.decided_at)
    assert row["code"] == "EXCLUDED_SYMBOL" and row["reason"] == D120_REASON and row["journal_ref"]["line"] == 3


def test_d120_fwd1_btc_trade_excluded_eth_trade_alerts(tmp_path):
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["no_tested_target"] = {"action": "fallback_r", "fallback_t2_r": 2.0}
    c = AlertConfig(raw, ticks=TICKS, round_trip_cost=0.0018)
    t = {"entry_time": "2026-10-20T04:00:00+00:00", "stop_price": 0.0}
    run = {"seq": 5, "kind": "RUN", "written_utc": "2026-10-20T04:10:06+00:00", "hash": "h", "payload": {"symbols": {
        "BTCUSDT": {"open_position": dict(t, trade_id="b1", stop_price=98000.0)},
        "ETHUSDT": {"open_position": dict(t, trade_id="e1", stop_price=2450.0)},
    }}}
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(run) + "\n", encoding="utf-8")
    bar0 = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
    data = MemoryData({
        ("BTCUSDT", "4h"): [bar(bar0 - timedelta(hours=4 * (39 - i)), 100000.0, 100500.0, 99500.0, 100000.0) for i in range(40)],
        ("ETHUSDT", "4h"): [bar(bar0 - timedelta(hours=4 * (39 - i)), 2500.0, 2512.5, 2487.5, 2500.0) for i in range(40)],
    })
    sigs, _ = fwd1_items(ledger, after_seq=-1, data=data, cfg=c, seen=set())
    p = pipe(tmp_path / "out", c, data=data)
    codes = {s.symbol: p.handle(s, s.decided_at)["code"] for s in sigs}
    assert codes == {"BTCUSDT": "EXCLUDED_SYMBOL", "ETHUSDT": "DRY_RUN"}


def test_d120_candle12_v2_journal_rows_are_not_a_source():
    rows_ = [
        {"book": "CANDLE12_V2", "event": "entry", "key": "k1", "entry_time": T0.isoformat(), "entry_price": 100.5,
         "stop_price": 96.0, "tp1": 108.0, "detail_version": 1, "ts": (T0 + timedelta(hours=1)).isoformat()},
        {"book": "CANDLE12_V2", "event": "skip", "reason": "second_open_ignored", "setup_bar_time": T0.isoformat(),
         "stop_price": 96.0, "tp1": 108.0, "detail_version": 1, "ts": (T0 + timedelta(hours=1)).isoformat()},
    ]
    lines = [(i, json.dumps(r)) for i, r in enumerate(rows_)]
    assert list(journal_items(lines, first_line_no=1, data=MemoryData(), cfg=AlertConfig())) == []


# --- owner ruling D145: alert only what the paper book opened; entry-to-stop cost filter ------------
SOL_TICKS = dict(TICKS, SOLUSDT=0.01)


def shipped(**over) -> AlertConfig:
    """The shipped config, status map and cost model (ResearchConfig round trip), plus the test ticks."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw.update(over)
    return AlertConfig(raw, ticks=SOL_TICKS)


def _mock_sender(posted: list):
    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 500 + len(posted)}})

    return ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                  client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def _sol_2026_10_21_17h(outcome="not_opened", reason="position_open", strategy_id="COMBO_02_V1") -> Signal:
    """The second V1 SOL alert SENT on 2026-10-21 (live alerts.jsonl line 8, journal line 10128): bar 17:00 UTC,
    entry 108.54-108.12, SL 107.70; the journal row says not_opened / position_open (V1 already held SOL)."""
    extra = {"engine_values": "journal", "live_outcome": outcome, "market_source": "ohlcv"}
    if reason:
        extra["skip_reason"] = reason
    return Signal(source="COMBO_02_V1", strategy_id=strategy_id, symbol="SOLUSDT", side="LONG", timeframe="1h",
                  signal_bar="2026-10-21T17:00:00+00:00", decided_at="2026-10-21T18:00:15.479720+00:00",
                  stop=107.69983550980047, tested_target=112.73, signal_close=108.54, atr=0.9008224336387686,
                  journal_ref={"file": "decision_journal.jsonl", "line": 10128}, extra=extra)


SOL_SENT_AT = datetime(2026, 10, 21, 18, 0, 36, 937775, tzinfo=timezone.utc)


def test_d145_shipped_defaults():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["send_not_opened"] is False and raw["cost_stop_entry_ref"] == "near_edge"
    assert raw["cost_stop"]["taker_fee_rate"] == 0.0005 and raw["cost_stop"]["slippage_rate"] == 0.0002
    assert raw["cost_stop"]["multiple"] == 3.0 and raw["cost_stop"]["fee_sides"] == 2
    assert raw["paper_book_sources"] == ["COMBO_02_V1", "BOOK16_FWD1", "LCSC_FWD3", "PASSED_BOOK"]
    assert raw["no_paper_outcome_sources"] == ["SCREENER"]
    c = AlertConfig()
    assert not c.send_not_opened and c.cost_stop_entry_ref == "near_edge"
    assert c.cost_stop_round_trip() == pytest.approx(0.0014) and c.cost_stop_floor() == pytest.approx(0.0042)
    assert "mid" in raw["_cost_stop_entry_ref_doc"] and "108.12" in raw["_cost_stop_entry_ref_doc"]
    # The older stop-distance check keeps the ResearchConfig round trip (unchanged).
    assert raw["cost"]["multiple"] == 3.0 and raw["cost"]["fee_sides"] == 2


def test_d145_older_config_copy_without_the_keys_gets_the_same_defaults():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for k in ("send_not_opened", "cost_stop", "cost_stop_entry_ref", "paper_book_sources", "no_paper_outcome_sources"):
        raw.pop(k)
    c = AlertConfig(raw)
    assert not c.send_not_opened and c.cost_stop_entry_ref == "near_edge"
    assert c.cost_stop_floor() == pytest.approx(0.0042)
    assert c.has_paper_outcome("COMBO_02_V1", None) and not c.has_paper_outcome("SCREENER", "not_opened")
    # Only an explicit true sends not-opened signals.
    assert not AlertConfig(dict(raw, send_not_opened="true")).send_not_opened


def test_d145_unknown_entry_ref_fails_at_load():
    with pytest.raises(ValueError, match="cost_stop_entry_ref"):
        cfg(cost_stop_entry_ref="edge")


def test_d145_golden_a_second_sol_alert_is_not_sent_by_default(tmp_path):
    """(a) The 2026-10-21 17:00 SOL V1 alert, replayed in live mode (mock transport): nothing is posted."""
    posted: list = []
    p = pipe(tmp_path, shipped(enabled=True, dry_run=False), sender=_mock_sender(posted))
    assert p.delivering
    s = _sol_2026_10_21_17h()
    row = p.handle(s, SOL_SENT_AT)
    assert (row["kind"], row["code"], row["reason"]) == ("SKIP", "NOT_OPENED", "position_open")
    assert row["paper_outcome"] == "not_opened" and row["paper_skip_reason"] == "position_open"
    # Its stop is also too tight under near_edge: both rules flag it, NOT_OPENED comes first.
    assert row["flags"]["not_opened"] == "position_open" and row["flags"]["cost_stop"] is True
    assert row["flags"]["cost"] is False  # the old mid-based check let it through (it was SENT)
    assert posted == [] and p.state["trackers"] == {} and p.state["sent_times"] == []
    assert "telegram_message_id" not in row
    # Same prices as the alert that was sent.
    assert row["plan"]["rounded"]["zone_a"] == "108.54" and row["plan"]["rounded"]["zone_b"] == "108.12"
    assert row["plan"]["rounded"]["sl"] == "107.70"


def test_d145_golden_a_forced_opened_sol_is_cost_stop_under_near_edge_not_mid(tmp_path):
    s = _sol_2026_10_21_17h(outcome="opened", reason="")
    near, _ = build_plan(s, shipped())
    # Near edge 108.12: (108.1199 - 107.6998) / 108.1199 = 0.389% < 3 x 0.14% = 0.42%.
    assert near["cost_stop_entry_ref"] == "near_edge" and near["cost_stop_entry_price"] == pytest.approx(108.11991775)
    assert near["cost_stop_frac"] == pytest.approx(0.0038854, abs=1e-6) and near["cost_stop_skip"]
    assert near["cost_stop_floor_frac"] == pytest.approx(0.0042) and not near["cost_skip"]
    row = pipe(tmp_path / "near", shipped()).handle(s, SOL_SENT_AT)
    assert (row["kind"], row["code"]) == ("SKIP", "SKIPPED_COST_STOP")
    assert row["flags"]["not_opened"] is None and row["flags"]["cost"] is False
    # Mid 108.33: 0.58% >= 0.42%: not skipped, the alert goes out as before.
    s = _sol_2026_10_21_17h(outcome="opened", reason="")
    mid, _ = build_plan(s, shipped(cost_stop_entry_ref="mid"))
    assert mid["cost_stop_entry_price"] == pytest.approx(108.32995888)
    assert mid["cost_stop_frac"] == pytest.approx(0.0058167, abs=1e-6) and not mid["cost_stop_skip"]
    row = pipe(tmp_path / "mid", shipped(cost_stop_entry_ref="mid")).handle(s, SOL_SENT_AT)
    assert (row["kind"], row["code"]) == ("ALERT", "DRY_RUN")
    assert row["text"].split("\n")[:2] == [D117_LINE, "✅ INTRADAY TRADE - SOL"]
    assert "👉 ENTRY - $108.54 - $108.12" in row["text"] and "👉 SL - $107.70" in row["text"]


def test_d145_cost_stop_boundary_and_short_side():
    c = cfg()
    floor = c.cost_stop_floor()
    # LONG: zone 100 down to 99.8 (atr 0.4); a stop exactly 0.42% under the near edge is not skipped (not "<").
    stop = 99.8 * (1 - floor)
    at, _ = build_plan(sig(symbol="ETHUSDT", signal_close=100.0, atr=0.4, stop=stop, tested_target=110.0), c)
    assert not at["zone_capped_by_sl"] and at["cost_stop_entry_price"] == pytest.approx(99.8)
    assert at["cost_stop_frac"] == pytest.approx(floor) and not at["cost_stop_skip"]
    under, _ = build_plan(sig(symbol="ETHUSDT", signal_close=100.0, atr=0.4, stop=stop + 0.01, tested_target=110.0), c)
    assert under["cost_stop_skip"]
    # SHORT: the near edge is the zone top (signal close + 0.5 ATR), nearest the stop above.
    short, _ = build_plan(sig(symbol="ETHUSDT", side="SHORT", signal_close=100.0, atr=0.6, stop=100.7,
                              tested_target=95.0), c)
    assert short["cost_stop_entry_price"] == pytest.approx(100.3) and short["zone_hi"] == pytest.approx(100.3)
    assert short["cost_stop_frac"] == pytest.approx(0.4 / 100.3) and short["cost_stop_skip"]


GOLDEN_V1_ETH_OPENED = """⚠️ UNPROVEN, for observation only, not for trading
✅ INTRADAY TRADE - ETH
🏮 TYPE - LONG

👉 ENTRY - $2500.00 - $2487.50
👉 TARGET - $2537.50, $2612.50 (tested), $2656.25 & $2700.00+
👉 SL - $2450.00

🚨 LEVERAGE - 1x
📊 R:R - 1:2.7 | Risk 1% of account
🧪 PAPER · COMBO_02_V1 · 1h
⏱ Valid 3 bars · Not financial advice"""


def _v1_eth(outcome="opened", reason=None) -> Signal:
    extra = {"live_outcome": outcome, "engine_values": "journal"}
    if reason:
        extra["skip_reason"] = reason
    return sig(source="COMBO_02_V1", strategy_id="COMBO_02_V1", symbol="ETHUSDT", signal_close=2500.0, atr=25.0,
               stop=2450.0, tested_target=2612.5, extra=extra)


def test_d145_golden_b_opened_v1_eth_wide_stop_is_sent(tmp_path):
    posted: list = []
    p = pipe(tmp_path, shipped(enabled=True, dry_run=False), sender=_mock_sender(posted))
    s = _v1_eth()
    row = p.handle(s, s.bar_close + timedelta(minutes=1))
    assert (row["kind"], row["code"]) == ("ALERT", "SENT") and row["flags"]["not_opened"] is None
    assert row["flags"]["cost_stop"] is False and row["plan"]["cost_stop_frac"] == pytest.approx(37.5 / 2487.5)
    assert [b["text"] for b in posted] == [GOLDEN_V1_ETH_OPENED]
    assert posted[0]["text"].split("\n")[0] == D117_LINE
    assert s.alert_id in p.state["trackers"] and p.state["trackers"][s.alert_id]["sent"] is True


GOLDEN_NOT_OPENED_POSITION_OPEN = D117_LINE + "\n❌ NOT OPENED: position_open\n" + GOLDEN_V1_ETH_OPENED.split("\n", 1)[1]


def test_d145_golden_c_send_not_opened_marks_the_line_after_unproven(tmp_path):
    posted: list = []
    p = pipe(tmp_path, shipped(enabled=True, dry_run=False, send_not_opened=True), sender=_mock_sender(posted))
    s = _v1_eth(outcome="not_opened", reason="position_open")
    row = p.handle(s, s.bar_close + timedelta(minutes=1))
    assert (row["kind"], row["code"]) == ("ALERT", "SENT") and row["flags"]["not_opened"] == "position_open"
    assert [b["text"] for b in posted] == [GOLDEN_NOT_OPENED_POSITION_OPEN]
    lines = posted[0]["text"].split("\n")
    assert lines[0] == D117_LINE and lines[1] == "❌ NOT OPENED: position_open"
    assert lines[2] == "✅ INTRADAY TRADE - ETH" and posted[0]["text"].count("NOT OPENED") == 1
    # The real SOL signal stays unsent with send_not_opened: its stop is under the near-edge cost floor.
    sol = p.handle(_sol_2026_10_21_17h(), SOL_SENT_AT)
    assert sol["code"] == "SKIPPED_COST_STOP" and len(posted) == 1
    # A FORWARD-PASSED source has no D117 line: the mark is then the first line.
    plan, _ = build_plan(s, cfg())
    assert render_alert(s, plan, "FORWARD-PASSED", cfg(), not_opened_reason="x").startswith("❌ NOT OPENED: x\n✅")


def test_d145_not_opened_reason_falls_back_to_outcome_and_missing_outcome_is_not_opened(tmp_path):
    p = pipe(tmp_path)
    missed = _v1_eth(outcome="missed")
    assert p.handle(missed, missed.bar_close)["reason"] == "missed"
    for source in ("COMBO_02_V1", "BOOK16_FWD1", "LCSC_FWD3"):
        s = sig(source=source, strategy_id=f"N{source}", symbol="ETHUSDT", extra={})
        row = p.handle(s, s.bar_close)
        assert (row["code"], row["reason"]) == ("NOT_OPENED", "no_paper_outcome"), source
    # Killed and late come first (a missed passed-book row stays LATE).
    late = _v1_eth(outcome="not_opened", reason="position_open")
    late.signal_bar = late.signal_bar + timedelta(hours=1)
    assert p.handle(late, late.bar_close + timedelta(minutes=16))["code"] == "LATE"
    killed = sig(source="PASSED_BOOK", strategy_id="COMBO_01", extra={"live_outcome": "not_opened"})
    assert p.handle(killed, killed.bar_close)["code"] == "SUPPRESSED_KILLED"


def test_d145_golden_d_screener_is_not_affected(tmp_path):
    c = shipped()
    c.ticks["IOSTUSDT"] = 0.0001
    data = MemoryData({("IOSTUSDT", "15m"): _m15("IOSTUSDT", datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc))})
    (item,) = list(journal_items([(0, json.dumps(SCREENER_ROW))], first_line_no=42, data=data, cfg=c))
    assert item.extra["live_outcome"] == "screener_signal" and not c.has_paper_outcome("SCREENER", "screener_signal")
    row = pipe(tmp_path / "a", c, data=data).handle(item, item.decided_at)
    assert row["code"] == "DRY_RUN" and row["text"] == GOLDEN_SCREENER and row["flags"]["not_opened"] is None
    # Even a screener signal carrying a non-opened outcome is not a NOT_OPENED skip.
    odd = sig(source="SCREENER", strategy_id="SCREENER_SETUP", symbol="ETHUSDT", target_tested=False,
              extra={"live_outcome": "not_opened", "skip_reason": "x"})
    assert pipe(tmp_path / "b", c).handle(odd, odd.bar_close)["code"] == "DRY_RUN"


def test_d145_skipped_signals_are_not_tracked(tmp_path):
    # Live: no tracker and no follow-up for a NOT_OPENED or a SKIPPED_COST_STOP skip.
    posted: list = []
    t = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
    data = MemoryData({("SOLUSDT", "1h"): [bar(t, 108.3, 108.4, 107.0, 107.2)]})
    p = pipe(tmp_path / "live", shipped(enabled=True, dry_run=False), data=data, sender=_mock_sender(posted))
    assert p.handle(_sol_2026_10_21_17h(), SOL_SENT_AT)["code"] == "NOT_OPENED"
    forced = _sol_2026_10_21_17h(outcome="opened", reason="", strategy_id="COMBO_02_V1_FORCED")
    assert p.handle(forced, SOL_SENT_AT)["code"] == "SKIPPED_COST_STOP"
    assert p.state["trackers"] == {} and p.update_trackers(t + timedelta(hours=2)) == 0 and posted == []
    # Dry run with track_suppressed_in_dry_run false: none either.
    q = pipe(tmp_path / "dry", shipped(preview={"render_suppressed": True, "track_suppressed_in_dry_run": False}))
    assert q.handle(_sol_2026_10_21_17h(), SOL_SENT_AT)["code"] == "NOT_OPENED" and q.state["trackers"] == {}
    # The existing dry-run preview tracking (default true) keeps a not-sent tracker, marked not sent.
    r = pipe(tmp_path / "dry2", shipped())
    r.handle(_sol_2026_10_21_17h(), SOL_SENT_AT)
    assert [(v["sent"], v["not_sent_code"]) for v in r.state["trackers"].values()] == [(False, "NOT_OPENED")]


def _fwd3_run(trades):
    return {"run_at": "2026-10-16T12:20:12+00:00", "row_hash": "abc", "trades": trades,
            "candidates": [{"signal_time": "2026-10-16T11:00:00+00:00", "fate": "PENDING_NEXT_BAR",
                            "close": 2476.41, "invalidation": 2403.82, "atr": 19.956}]}


@pytest.mark.parametrize("trades,outcome", [
    ([], "opened"),
    # An open trade (exit_time = the run's last closed bar) blocks the pending candidate in the contract walk.
    ([{"signal_time": "2026-10-15T20:00:00+00:00", "exit_time": "2026-10-16T11:00:00+00:00", "outcome": "OPEN"}],
     "not_opened"),
    ([{"signal_time": "2026-10-15T20:00:00+00:00", "exit_time": "2026-10-16T09:00:00+00:00", "outcome": "SL"}],
     "opened"),
])
def test_d145_fwd3_pending_candidate_outcome(tmp_path, trades, outcome):
    p = tmp_path / "runs.jsonl"
    p.write_text(json.dumps(_fwd3_run(trades)) + "\n", encoding="utf-8")
    (s,), _ = fwd3_items(p, after_line=0, data=MemoryData(), cfg=cfg(), seen=set())
    assert s.extra["live_outcome"] == outcome and s.extra["fwd3_fate"] == "PENDING_NEXT_BAR"
    row = pipe(tmp_path / "out").handle(s, s.decided_at)
    if outcome == "opened":
        assert row["code"] == "DRY_RUN"
    else:
        assert (row["code"], row["reason"]) == ("NOT_OPENED", "open_trade")


def test_d145_fwd1_trades_and_backfilled_positions_are_opened(tmp_path):
    from app.alerts.sources import paper_trade_items

    run = {"seq": 5, "kind": "RUN", "written_utc": "2026-10-20T04:10:06+00:00", "hash": "h", "payload": {"symbols": {
        "ETHUSDT": {"open_position": {"trade_id": "e1", "entry_time": "2026-10-20T04:00:00+00:00", "stop_price": 2450.0}},
    }}}
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(run) + "\n", encoding="utf-8")
    (s,), _ = fwd1_items(ledger, after_seq=-1, data=MemoryData(), cfg=cfg(), seen=set(), enrich=False)
    assert s.extra["live_outcome"] == "opened"
    rows_ = [{"id": "p9", "symbol": "ETHUSDT", "side": "LONG", "timeframe": "1h", "source_candle_ts": T0.isoformat(),
              "opened_at": (T0 + timedelta(hours=1)).isoformat(), "stop_price": 2450.0, "tp1_price": 2612.5,
              "signal_snippet": {"source": "V1_PAPER_WATCHER"}}]
    (b,) = paper_trade_items(rows_, data=MemoryData(), cfg=cfg())
    assert b.source == "COMBO_02_V1" and b.extra["live_outcome"] == "opened"


# --- owner ruling D149: the daily summary counts paper trades the book opened, from the paper ledger ----------
D149_LABEL = "alert levels touched (not paper trades)"
SUMMARY_AT = datetime(2026, 10, 21, 0, 0, 30, tzinfo=timezone.utc)


def _eth_sig(**kw) -> Signal:
    base = dict(source="COMBO_02_V1", strategy_id="COMBO_02_V1", symbol="ETHUSDT", side="LONG", timeframe="1h",
                signal_bar=T0, decided_at=T0 + timedelta(hours=1, minutes=1), stop=2450.0, tested_target=2612.5,
                signal_close=2500.0, atr=25.0, journal_ref={"file": "decision_journal.jsonl", "line": 3},
                extra={"live_outcome": "opened"})
    base.update(kw)
    return Signal(**base)


def _eth_bars():
    t = T0 + timedelta(hours=1)
    return [bar(t, 2500.0, 2505.0, 2490.0, 2495.0),        # fills the entry zone
            bar(t + timedelta(hours=1), 2495.0, 2496.0, 2400.0, 2420.0)]  # through the SL


def _v1_trade(**kw):
    base = {"id": "pt-1", "symbol": "ETHUSDT", "side": "LONG", "status": "CLOSED", "entry_price": 2500.0,
            "stop_price": 2450.0, "tp1_price": 2612.5, "opened_at": (T0 + timedelta(hours=1)).isoformat(),
            "closed_at": (T0 + timedelta(hours=3)).isoformat(), "exit_price": 2446.0, "exit_reason": "STOP",
            "pnl_usd": -19.72, "r_multiple": -1.08, "source_candle_ts": T0.isoformat(), "timeframe": "1h",
            "signal_snippet": {"source": "V1_PAPER_WATCHER"}}
    base.update(kw)
    return base


def _summary(p):
    t = T0 + timedelta(hours=1)
    p.state["last_summary_day"] = (T0.date() - timedelta(days=1)).isoformat()
    p.update_trackers(t + timedelta(hours=3))
    (out,) = p.maybe_daily_summary(SUMMARY_AT)
    return out


GOLDEN_D149 = PREFIX + """📅 DAILY SUMMARY - 2026-10-20 UTC
Paper trades from sent alerts (paper ledger): 1
PAPER: 1 alerts, open 0, closed 1, W/L 0/1, R -1.08, pnl -$19.72
- COMBO_02_V1 ETH LONG 1h bar 10-20 17:00: CLOSED, STOP, R -1.08, pnl -$19.72

alert levels touched (not paper trades)
Alerts sent: 2 | Entry filled: 2
TP/SL hit: wins 0 | losses 2 | Tracked R: -2.3
PAPER: sent 1, filled 1, W/L 0/1, R -1.1, not sent 0
UNPROVEN: sent 1, filled 1, W/L 0/1, R -1.1, not sent 0
SCREENER (no paper book): sent 1"""


def test_d149_golden_daily_summary(tmp_path):
    """One opened V1 alert whose ledger trade closed -1.08 R, and one screener alert the tracker followed only."""
    data = MemoryData({("ETHUSDT", "1h"): _eth_bars()}, [_v1_trade()])
    p = pipe(tmp_path, data=data)
    v1 = _eth_sig(position_id="pt-1")
    scr = _eth_sig(source="SCREENER", strategy_id="SCREENER_SETUP", extra={"live_outcome": "screener_signal"},
                   journal_ref={"file": "decision_journal.jsonl", "line": 4})
    assert p.handle(v1, v1.bar_close + timedelta(minutes=1))["code"] == "DRY_RUN"
    assert p.handle(scr, scr.bar_close + timedelta(minutes=1))["code"] == "DRY_RUN"
    out = _summary(p)
    assert out == GOLDEN_D149
    row = rows(tmp_path)[-1]
    assert row["kind"] == "SUMMARY" and row["alerts"][0]["ledger"]["r_multiple"] == -1.08
    assert row["alerts"][1]["paper_book"] is False and "ledger" not in row["alerts"][1]
    assert p.state["summary_alerts"] == {}


def test_d149_not_opened_signals_are_excluded_from_the_main_section(tmp_path):
    data = MemoryData({("ETHUSDT", "1h"): _eth_bars()}, [_v1_trade()])
    p = pipe(tmp_path, cfg(send_not_opened=True), data=data)
    s = _eth_sig(extra={"live_outcome": "not_opened", "skip_reason": "sticky_bos_skip"})
    row = p.handle(s, s.bar_close + timedelta(minutes=1))
    assert row["kind"] == "ALERT" and row["flags"]["not_opened"] == "sticky_bos_skip"
    out = _summary(p).split("\n")
    assert "Paper trades from sent alerts (paper ledger): 0" in out
    assert not any(x.startswith("- ") for x in out)  # no ledger line for the not-opened signal
    main, touched = out[:out.index(D149_LABEL)], out[out.index(D149_LABEL):]
    assert not any(x.startswith("PAPER:") for x in main)
    assert "Alerts sent: 1 | Entry filled: 1" in touched


def test_d149_label_text_is_exact():
    from app.alerts.plan import TOUCHED_LABEL, render_summary

    assert TOUCHED_LABEL == D149_LABEL
    assert D149_LABEL in render_summary("2026-10-21", {}).split("\n")
    assert D149_LABEL in render_summary("2026-10-21", {"FORWARD-PASSED": {"sent": 1}}).split("\n")


def test_d149_missing_ledger_row_is_shown_as_not_found(tmp_path):
    data = MemoryData({("ETHUSDT", "1h"): _eth_bars()}, [])
    p = pipe(tmp_path, data=data)
    s = _eth_sig(position_id="pt-missing")
    p.handle(s, s.bar_close + timedelta(minutes=1))
    out = _summary(p).split("\n")
    assert "- COMBO_02_V1 ETH LONG 1h bar 10-20 17:00: ledger: not found" in out
    assert "PAPER: 1 alerts, open 0, closed 0, W/L 0/0, R +0.00, pnl +$0.00, ledger: not found 1" in out


def test_d149_ledger_match_by_symbol_side_and_open_time_within_the_book(tmp_path):
    """No position_id: the book's own trade on the symbol and side opened near the signal bar; another book's
    trade on the same bar is not taken, nor a trade opened two bars later."""
    trades = [_v1_trade(id="other", signal_snippet={"source": "PASSED_CAP_BOOK"}, r_multiple=3.0),
              _v1_trade(id="late", opened_at=(T0 + timedelta(hours=3)).isoformat(), r_multiple=2.0),
              _v1_trade(id="open", status="OPEN", closed_at=None, exit_price=None, exit_reason=None, pnl_usd=None,
                        r_multiple=None, opened_at=(T0 + timedelta(hours=1, seconds=4)).isoformat())]
    p = pipe(tmp_path, data=MemoryData({("ETHUSDT", "1h"): _eth_bars()}, trades))
    s = _eth_sig()
    p.handle(s, s.bar_close + timedelta(minutes=1))
    out = _summary(p).split("\n")
    assert "- COMBO_02_V1 ETH LONG 1h bar 10-20 17:00: OPEN, R -, pnl -" in out
    assert "PAPER: 1 alerts, open 1, closed 0, W/L 0/0, R +0.00, pnl +$0.00" in out


def test_d149_ledger_read_failure_is_marked_and_the_summary_still_goes_out(tmp_path):
    class Broken(MemoryData):
        def paper_trade(self, position_id):
            raise ConnectionError("db down")

    p = pipe(tmp_path, data=Broken({("ETHUSDT", "1h"): _eth_bars()}))
    s = _eth_sig(position_id="pt-1")
    p.handle(s, s.bar_close + timedelta(minutes=1))
    assert "- COMBO_02_V1 ETH LONG 1h bar 10-20 17:00: ledger: read failed (ConnectionError)" in _summary(p).split("\n")
