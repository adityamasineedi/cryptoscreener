"""Owner D199 / D230: every screen shows the screener view; paper decisions and alert sources do not change.

- display_payload: the shared accessor (setup fields from payload["screener_setup"]).
- /api/signals (list, per symbol, risk/targets/explanation/annotations, per timeframe), the symbol detail
  Setup tab and Trade chances show the episode-impulse candidate; the payload paper reads still says WAITING.
- Alerts: the candidate reaches alerts only as a SCREENER journal row, labelled UNPROVEN, and the v3 cost
  (TP1-to-stop band) and D145 entry-to-stop filters apply (fake Telegram sender, no network).
Synthetic OHLCV is test-only.
"""

from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest

from app.services import decision_journal
from app.services.screener_display import display_payload, list_display_opportunities
from app.services.screener_pullback import ScreenerSignalEngine
from app.signals.signal_engine import SignalEngine
from test_screener_pullback_fix import CFG, ENTRY, _series, _tfs

SYM = "SYNUSDT"


@pytest.fixture
def live(monkeypatch, tmp_path):
    """Service + in-memory stores replayed over the synthetic series up to the pullback bar (imp + 1)."""
    from app.services import setup_signals as mod
    from app.services.engine_store import engine_store
    from app.services.ohlcv_store import ohlcv_store

    state: dict = {"tfs": {}}
    monkeypatch.setattr(ohlcv_store, "get_candles_for_engine",
                        lambda sym, tf, include_open=False: list(state["tfs"].get(tf) or []))
    monkeypatch.setattr(ohlcv_store, "get_closed", lambda sym, tf, limit=None: [])
    monkeypatch.setattr(engine_store, "setup_signals", {})
    monkeypatch.setattr(engine_store, "signals", {})
    svc = mod.SetupSignalService()
    svc.config, svc.engine, svc.screener_engine = CFG, SignalEngine(CFG), ScreenerSignalEngine(CFG)
    monkeypatch.setattr(mod, "_setup_service", svc)
    context, bars, imp = _series()
    journal = tmp_path / "decision_journal.jsonl"
    decision_journal.configure(journal, signal_detail=True)
    try:
        for end in range(imp - 3, imp + 2):
            state["tfs"] = _tfs(context, bars[: end + 1])
            svc.analyze_symbol(SYM)
    finally:
        decision_journal.configure(None)
    return {"svc": svc, "state": state, "bars": bars, "imp": imp, "journal": journal, "store": engine_store}


def test_display_payload_accessor():
    plain = {"status": "WAITING", "entry": {"entry_price": None}}
    assert display_payload(plain) is plain and display_payload(None) is None
    raw = {"status": "WAITING", "direction": None, "stop": {"provisional": True}, "bos": {"state": "CONFIRMED"},
           "screener_setup": {"status": "LONG_ENTRY_CANDIDATE", "direction": "LONG", "stop": {"final_stop": 1.0},
                              "pullback_rule": "episode_impulse_v1", "episode_impulse_used": True}}
    before = copy.deepcopy(raw)
    shown = display_payload(raw)
    assert raw == before  # the payload paper reads is never changed
    assert shown["status"] == "LONG_ENTRY_CANDIDATE" and shown["direction"] == "LONG"
    assert shown["stop"] == {"final_stop": 1.0} and shown["bos"] == {"state": "CONFIRMED"}
    assert shown["setup_view"] == {"engine": "screener", "pullback_rule": "episode_impulse_v1",
                                   "episode_impulse_used": True, "paper_status": "WAITING"}


def test_paper_payload_waits_while_every_display_shows_the_candidate(live):
    from app.api import routes

    raw = live["store"].get_setup_signal(SYM)
    assert raw["status"] == "WAITING" and raw["status"] not in ENTRY  # what paper reads
    assert raw["screener_setup"]["status"] == "LONG_ENTRY_CANDIDATE"
    run = asyncio.run
    lst = run(routes.list_setup_signals(status=None, limit=100))
    (row,) = [r for r in lst["rows"] if r["symbol"] == SYM]
    assert row["status"] == row["setup_status"] == "LONG_ENTRY_CANDIDATE" and row["direction"] == "LONG"
    assert row["setup_view"]["paper_status"] == "WAITING" and row["entry"]["entry_price"] is not None
    assert [r["symbol"] for r in run(routes.list_setup_signals(status="LONG_ENTRY_CANDIDATE", limit=100))["rows"]] == [SYM]
    one = run(routes.signal_for_symbol(SYM))
    assert one["status"] == "LONG_ENTRY_CANDIDATE" and one["pullback"]["pullback_state"] in ("ACTIVE", "CONFIRMED")
    assert one["setup_view"]["paper_status"] == "WAITING"
    assert run(routes.signal_risk(SYM))["status"] == "LONG_ENTRY_CANDIDATE"
    tg = run(routes.signal_targets(SYM))
    assert tg["status"] == "LONG_ENTRY_CANDIDATE" and tg["targets"]
    ex = run(routes.signal_explanation(SYM))
    assert ex["status"] == "LONG_ENTRY_CANDIDATE" and ex["direction"] == "LONG"
    an = run(routes.signal_annotations(SYM))
    assert an["status"] == "LONG_ENTRY_CANDIDATE" and any(a.get("kind") == "BUY" for a in an["annotations"])
    tf = run(routes.signal_for_symbol_tf(SYM, "15m"))
    assert tf["pullback"]["pullback_state"] in ("ACTIVE", "CONFIRMED") and tf["impulse"]["is_impulse"]
    # The payload paper reads is still the shared engine's result after all those reads.
    assert live["store"].get_setup_signal(SYM)["status"] == "WAITING"


def test_setup_tab_shows_the_screener_view(live):
    from app.services.screener import ScreenerService

    tab = ScreenerService._setup_tab(object(), SYM)  # uses no instance state
    assert tab["setup_state"]["status"] == "LONG_ENTRY_CANDIDATE"
    assert tab["setup_state"]["pullback"]["pullback_state"] in ("ACTIVE", "CONFIRMED")
    assert tab["trade_plan"]["entry"]["entry_price"] is not None
    assert tab["trade_plan"]["stop"]["final_stop"] is not None and not tab["trade_plan"]["stop"].get("provisional")
    assert tab["analysis"]["setup_view"]["paper_status"] == "WAITING"


def test_trade_chances_show_the_screener_view_and_say_paper_does_not_open(live):
    from app.services.paper_trade import list_trade_opportunities

    (row,) = [r for r in list_display_opportunities(limit=300) if r["symbol"] == SYM]
    assert row["setup_view"] == "screener" and row["status"] == "LONG_ENTRY_CANDIDATE"
    assert row["pullback"] in ("ACTIVE", "CONFIRMED") and row["stop_price"] is not None
    assert row["paper_status"] == "WAITING" and row["paper_tier"] != "READY"
    if row["tier"] == "READY":
        assert "does not open" in row["chance"]
    (paper_row,) = [r for r in list_trade_opportunities(limit=300) if r["symbol"] == SYM]
    assert paper_row["status"] == "WAITING" and paper_row["tier"] == row["paper_tier"]


def test_trade_chances_identical_to_paper_list_without_a_screener_view(live, monkeypatch):
    from app.services.paper_trade import list_trade_opportunities

    store = live["store"]
    base = {k: {kk: vv for kk, vv in v.items() if kk != "screener_setup"} for k, v in store.setup_signals.items()}
    base["ABCUSDT"] = {**base[SYM], "symbol": "ABCUSDT"}
    monkeypatch.setattr(store, "setup_signals", base)
    for inc in (True, False):
        assert list_display_opportunities(limit=40, include_waiting=inc) == list_trade_opportunities(limit=40, include_waiting=inc)


# --- alerts: SCREENER journal rows only, UNPROVEN, cost-filtered --------------------------------------


def _alert_setup(live, tmp_path, **cfg_over):
    from app.alerts.config import CONFIG_PATH, AlertConfig
    from app.alerts.data import MemoryData
    from app.alerts.pipeline import AlertPipeline
    from app.alerts.sender import ReplyingTelegramSender
    from app.alerts.sources import journal_items

    lines = live["journal"].read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in lines]
    screener = [r for r in rows if r.get("book") == "SCREENER"]
    assert len(screener) == 1 and screener[0]["status"] == "LONG_ENTRY_CANDIDATE"
    assert screener[0]["pullback_rule"] == "episode_impulse_v1"
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    rt = cfg_over.pop("round_trip_cost", None)
    raw.update(enabled=True, dry_run=False, **cfg_over)
    cfg = AlertConfig(raw, ticks={SYM: 0.01}, **({"round_trip_cost": rt} if rt is not None else {}))
    data = MemoryData({(SYM, "15m"): live["bars"]})
    items = list(journal_items(list(enumerate(lines)), first_line_no=1, data=data, cfg=cfg))
    posted: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 900 + len(posted)}})

    sender = ReplyingTelegramSender(bot_token="t", chat_id="123456789", timeout=1.0,
                                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    pipe = AlertPipeline(cfg, out_dir=tmp_path / "alerts", data=data, sender=sender)
    return items, pipe, posted


def test_fixed_candidate_alert_is_a_screener_row_labelled_unproven(live, tmp_path):
    items, pipe, posted = _alert_setup(live, tmp_path)
    (item,) = items  # the SCREENER row is the only alert source here; nothing comes from the payload
    assert item.source == "SCREENER" and item.side == "LONG" and item.timeframe == "15m"
    row = pipe.handle(item, item.bar_close)
    assert row["status"] == "UNPROVEN" and row["code"] == "SENT"
    assert row["flags"]["cost"] is False and row["flags"]["cost_stop"] is False
    assert len(posted) == 1 and posted[0]["text"].startswith("⚠️ UNPROVEN, for observation only, not for trading")


def test_fixed_candidate_alert_is_cost_filtered(live, tmp_path):
    # v3 cost check (stop distance and the screener TP1-to-stop band): a 20% round trip rejects it.
    items, pipe, posted = _alert_setup(live, tmp_path / "a", round_trip_cost=0.2)
    row = pipe.handle(items[0], items[0].bar_close)
    assert row["code"] == "SKIPPED_COST" and row["status"] == "UNPROVEN" and posted == []
    # D145 entry-to-stop filter on its own (cost check passes, entry-to-stop cutoff raised to 50%).
    items, pipe, posted = _alert_setup(live, tmp_path / "b", cost_stop={"multiple": 500.0})
    row = pipe.handle(items[0], items[0].bar_close)
    assert row["flags"]["cost"] is False and row["code"] == "SKIPPED_COST_STOP" and posted == []


# --- D204: wrong-side chart plan is not shown; shown prices use the alert tick ----------------------


def _zec_overlay() -> dict:
    """The shape the chart drew for ZECUSDT 15m: SELL above an unrounded short stop."""
    return {
        "symbol": "ZECUSDT",
        "status": "WAITING",
        "direction": None,
        "timeframe": "15m",
        "bos": {"state": "CONFIRMED", "direction": "BEARISH_BOS", "broken_level": 1216.25},
        "entry": {"entry_price": None, "direction": None},
        "stop": {"entry_price": 1216.25, "final_stop": 1209.684321, "provisional": True},
        "targets": [{"name": "TP1", "target_price": 1198.291}],
        "annotations": [
            {"kind": "SELL", "group": "trade_plan", "price": 1216.25, "direction": "SHORT", "label": "SELL"},
            {"kind": "SL", "group": "trade_plan", "price": 1209.684321, "direction": "SHORT", "label": "STOP"},
            {"kind": "TAKE_PROFIT", "group": "trade_plan", "price": 1198.291, "label": "TP1", "direction": "SHORT"},
            {"kind": "BOS", "group": "bos_choch", "price": 1216.25, "label": "BEARISH_BOS"},
        ],
    }


def test_wrong_side_plan_is_replaced_and_the_stored_payload_is_unchanged():
    from app.services.screener_display import INVALID_PLAN_TEXT, chart_annotations

    raw = _zec_overlay()
    before = copy.deepcopy(raw)
    shown = display_payload(raw, symbol="ZECUSDT", tick=0.01)
    assert raw == before
    assert shown["plan_notice"] == INVALID_PLAN_TEXT == "invalid plan: stop on wrong side"
    assert shown["stop"]["final_stop"] is None and shown["entry"]["entry_price"] is None
    assert shown["targets"] == []
    assert [a["kind"] for a in shown["annotations"]] == ["BOS"]
    anns, notice, status = chart_annotations(raw, symbol="ZECUSDT")
    assert notice == INVALID_PLAN_TEXT and status == "WAITING"
    assert [a.get("kind") for a in anns] == ["BOS"]
    assert raw == before


def test_valid_plan_is_rounded_to_the_alert_tick():
    raw = {
        "symbol": "BTCUSDT",
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "entry": {"entry_price": 100.004, "direction": "LONG"},
        "stop": {"final_stop": 98.001, "entry_price": 100.004},
        "targets": [{"name": "TP1", "target_price": 104.006}],
        "annotations": [
            {"kind": "BUY", "group": "trade_plan", "price": 100.004, "direction": "LONG", "label": "BUY"},
            {"kind": "SL", "group": "trade_plan", "price": 98.001, "direction": "LONG", "label": "STOP"},
            {"kind": "TAKE_PROFIT", "group": "trade_plan", "price": 104.006, "label": "TP1"},
        ],
    }
    before = copy.deepcopy(raw)
    shown = display_payload(raw, symbol="BTCUSDT", tick=0.01)
    assert raw == before
    assert "plan_notice" not in shown
    assert shown["entry"]["entry_price"] == 100.0
    assert shown["stop"]["final_stop"] == 98.0
    assert shown["targets"][0]["target_price"] == 104.01
    assert shown["annotations"][1]["price_text"] == "98.00"
    assert [a["kind"] for a in shown["annotations"]] == ["BUY", "SL", "TAKE_PROFIT"]


def test_display_rounding_matches_the_alert_sidecar():
    """The app does not import app.alerts. The numbers still match plan.py."""
    from app.alerts.plan import INVALID_STOP as PLAN_INVALID
    from app.alerts.plan import fmt_price as plan_fmt
    from app.alerts.plan import round_to_tick as plan_round

    from app.services.screener_display import INVALID_STOP, fmt_price, round_to_tick, stop_side_code

    assert INVALID_STOP == PLAN_INVALID
    for price, tick in ((1209.684321, 0.01), (100.004, 0.01), (98.001, 0.1), (0.000123456, 1e-05)):
        assert round_to_tick(price, tick) == plan_round(price, tick)
        assert fmt_price(price, tick) == plan_fmt(price, tick)
    assert stop_side_code(1216.25, 1209.68, "SHORT") == INVALID_STOP
    assert stop_side_code(1216.25, 1220.5, "SHORT") is None


def test_correct_short_stop_stays_on_the_plan():
    raw = {
        "symbol": "ZECUSDT",
        "direction": "SHORT",
        "entry": {"entry_price": 1216.25, "direction": "SHORT"},
        "stop": {"final_stop": 1220.5, "entry_price": 1216.25},
        "annotations": [
            {"kind": "SELL", "group": "trade_plan", "price": 1216.25, "direction": "SHORT"},
            {"kind": "SL", "group": "trade_plan", "price": 1220.5, "direction": "SHORT"},
        ],
    }
    shown = display_payload(raw, symbol="ZECUSDT", tick=0.01)
    assert "plan_notice" not in shown
    assert shown["stop"]["final_stop"] == 1220.5
    assert [a["kind"] for a in shown["annotations"]] == ["SELL", "SL"]
