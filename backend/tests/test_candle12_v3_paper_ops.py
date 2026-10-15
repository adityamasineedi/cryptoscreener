"""CANDLE12_V3 paper operations: execution modes, restart recovery, broker check,
conditional-order endpoints, backfill, freeze, drift guard, safety limits, status.

Hand-made Candle-1 setups (the detector is replaced), as in test_candle12_v3_paper:
LONG C1 H 60,000 L 59,100, ATR 1,000 -> stop order 60,012, stop 58,900, qty 0.017,
target 62,236. Nothing here touches the network or the database.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper import backfill as BF
from app.research.candle12_v3_paper import binance as bn
from app.research.candle12_v3_paper import cli
from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper import guard
from app.research.candle12_v3_paper import modes as M
from app.research.candle12_v3_paper import oos
from app.research.candle12_v3_paper.adapter import PaperAdapter
from app.research.candle12_v3_paper.broker import (
    DUPLICATE_CLIENT_ID,
    ENTRY,
    STOP,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    TARGET,
    UNSUPPORTED_ENDPOINT,
    BrokerRejected,
    BrokerUnavailable,
    Order,
    SimFaults,
    SimulatedBroker,
)
from app.research.candle12_v3_paper.broker_check import BROKER_CHECK_FAIL, BROKER_CHECK_PASS, run_broker_check
from app.research.candle12_v3_paper.capabilities import UnsupportedOrderType, capabilities, require_supported
from app.research.candle12_v3_paper import preflight as PF
from app.research.candle12_v3_paper.freeze import (
    DIRTY_WORKING_TREE,
    FREEZE_AFTER_OOS_START,
    LOCK_ALREADY_FROZEN,
    SUPERSEDED_LOCK_UNVERIFIED,
    UNKNOWN_EXISTING_LOCK,
    FreezeError,
    freeze_oos,
)
from app.research.candle12_v3_paper.recovery import (
    UNRECONCILED_EXTERNAL_ORDER,
    UNRECONCILED_EXTERNAL_POSITION,
    RecoveryHalted,
    run_recovery,
)
from app.research.candle12_v3_paper.safety import SafetyLimits, engage_kill_switch
from app.research.candle12_v3_paper.status import build_status
from app.research.candle12_v3_paper.store import PaperStore
from app.signals._candle_utils import candle_time

UTC = timezone.utc
T0 = datetime(2025, 5, 1, tzinfo=UTC)
LONG_C1 = (59_400.0, 60_000.0, 59_100.0, 59_900.0)
C2_FILL = (59_950.0, 60_100.0, 59_900.0, 60_050.0)
QUIET = (60_000.0, 60_000.0, 60_000.0, 60_000.0)
TP_BAR = (60_000.0, 62_300.0, 59_950.0, 62_000.0)
RECOVERY_FIELDS = ("recovery_id", "prior_state", "observed_broker_state", "action_taken", "order_ids", "timestamp",
                   "reason_code")


def bar(i, o, h, l, c):
    return {"time": T0 + timedelta(hours=i), "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": 1.0}


def setup_for(candle):
    return {"direction": "LONG", "c1_ohlc": {k: candle[k] for k in ("open", "high", "low", "close")},
            "pullback": {}, "impulse": {"impulse_origin": candle["low"]}, "atr": 1000.0, "swings": [],
            "structure_offset": 0}


@pytest.fixture
def fake(monkeypatch):
    table = {}
    monkeypatch.setattr(v3, "detect_c1_v2", lambda **kw: table.get(kw["candle1_index"]))
    return table


def cfg_for(**kw):
    kw.setdefault("min_bars", 2)
    return v3.Candle12V3Config(book_side="LONG", **kw)


def make_broker(cfg, faults=None):
    step, _, min_notional, _ = cfg.exchange_filter("BTCUSDT")
    return SimulatedBroker(slippage=0.0002, fee_rate=0.0004, step=step, min_notional=min_notional, faults=faults)


def candles_for(rows):
    return [bar(i, *r) for i, r in enumerate(rows)]


def drive(adapter, candles, k):
    t = candle_time(candles[k])
    adapter.on_bar_open(k, t, candles[k]["open"], now=t)
    adapter.on_bar_close(candles[k], now=t + timedelta(hours=1))


def build(fake, tmp_path, rows, *, upto, faults=None, mode="VIRTUAL_PAPER_MAINNET", **adapter_kw):
    candles = candles_for(rows)
    fake.update({5: setup_for(candles[5])})
    cfg = cfg_for(**adapter_kw.pop("cfg_kw", {}))
    broker = make_broker(cfg, faults)
    store = PaperStore(tmp_path, mode=mode)
    adapter = PaperAdapter(broker=broker, store=store, cfg=cfg, symbol="BTCUSDT", **adapter_kw)
    adapter.start(candles[:2], 2)
    for k in range(2, upto + 1):
        drive(adapter, candles, k)
    return candles, cfg, store, adapter, broker


def restart(candles, cfg, store, **adapter_kw):
    state = store.load_state()
    broker = make_broker(cfg)
    store2 = PaperStore(store.root, mode=store.mode)
    adapter = PaperAdapter(broker=broker, store=store2, cfg=cfg, symbol="BTCUSDT", **adapter_kw)
    adapter.resume(candles, int(state["first_trade_index"]), state)
    return adapter, broker, store2


def events(store, kind=None):
    return [e for e in store.read("events") if kind is None or e["event"] == kind]


OPEN_ROWS = [QUIET] * 5 + [LONG_C1, C2_FILL, QUIET, QUIET, QUIET, QUIET, QUIET]


# ----------------------------------------------------------------- 1. modes


def test_modes_are_separate_and_only_virtual_counts():
    v, t = M.spec(M.VIRTUAL_PAPER_MAINNET), M.spec(M.TESTNET_BROKER_CHECK)
    assert (v.data_venue, v.broker, v.counts_toward_oos, v.strategy_validation) == ("MAINNET", "SIMULATED", True, True)
    assert (t.data_venue, t.broker, t.counts_toward_oos, t.strategy_validation, t.engine_comparison) == (
        "TESTNET", "BINANCE_USDM_TESTNET", False, False, False)
    with pytest.raises(M.ModeError) as exc:
        M.spec("paper")
    assert exc.value.code == M.UNKNOWN_MODE


@pytest.mark.parametrize("data,broker", [
    ("https://fapi.binance.com", M.TESTNET_BROKER),  # mainnet candles + testnet fills
    ("https://demo-fapi.binance.com", M.SIMULATED),  # testnet candles + virtual fills
])
def test_mixed_data_broker_pairs_are_rejected(data, broker):
    with pytest.raises(M.ModeError) as exc:
        M.check_pair(data, broker)
    assert exc.value.code == M.MIXED_DATA_BROKER_MODE


def test_resolve_rejects_a_data_host_from_the_other_venue():
    with pytest.raises(M.ModeError) as exc:
        M.resolve(M.VIRTUAL_PAPER_MAINNET, data_base="https://demo-fapi.binance.com")
    assert exc.value.code == M.MIXED_DATA_BROKER_MODE
    with pytest.raises(M.ModeError) as exc:
        M.resolve(M.TESTNET_BROKER_CHECK, data_base="https://fapi.binance.com")
    assert exc.value.code == M.MIXED_DATA_BROKER_MODE
    assert M.resolve(M.TESTNET_BROKER_CHECK).broker == M.TESTNET_BROKER


def test_cli_refuses_mixed_mode(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(bn.PAPER_ENV, "1")
    assert cli.main(["run", "--paper", "--store", str(tmp_path)]) == 2  # old mainnet-candles + testnet-fills run
    assert "MIXED_DATA_BROKER_MODE" in capsys.readouterr().out
    assert cli.main(["run", "--mode", "virtual_paper_mainnet", "--data-url", "https://demo-fapi.binance.com",
                     "--store", str(tmp_path)]) == 2
    assert "MIXED_DATA_BROKER_MODE" in capsys.readouterr().out
    assert cli.main(["run", "--store", str(tmp_path)]) == 2
    assert "UNKNOWN_MODE" in capsys.readouterr().out
    assert not (tmp_path / "manifest.json").exists()


def test_run_live_refuses_a_mixed_feed(fake, tmp_path):
    class TestnetFeed:
        base_url = "https://demo-fapi.binance.com"

    cfg = cfg_for()
    adapter = PaperAdapter(broker=make_broker(cfg), store=PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET"),
                           cfg=cfg, symbol="BTCUSDT")
    with pytest.raises(M.ModeError) as exc:
        cli.run_live(adapter, TestnetFeed(), funding_source=None, poll_s=0, max_loops=1)
    assert exc.value.code == M.MIXED_DATA_BROKER_MODE


def test_testnet_store_never_counts_and_is_never_reconciled(tmp_path, capsys):
    store = PaperStore(tmp_path, mode="TESTNET_BROKER_CHECK", manifest={"counts_toward_oos": True,
                                                                        "strategy_validation": True})
    assert store.manifest["counts_toward_oos"] is False and store.manifest["strategy_validation"] is False
    assert store.manifest["label"] == "NON_OOS"
    assert cli.main(["reconcile", "--store", str(tmp_path)]) == 2
    assert "TESTNET_NOT_COMPARABLE" in capsys.readouterr().out
    with pytest.raises(oos.OOSRefused) as exc:
        oos.evaluate_oos(candles=[], funding_rows=None, now=C.earliest_evaluation() + timedelta(days=1), lock={},
                         open_exceptions=[], store_mode="TESTNET_BROKER_CHECK")
    assert exc.value.reason == "NOT_A_PAPER_STORE"


# ----------------------------------------------------------------- 2. restart recovery


def assert_recovery_fields(row):
    for f in RECOVERY_FIELDS:
        assert f in row, f


def test_resume_rebuilds_the_book_and_both_exits_resume(fake, tmp_path):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    slot = a1.slots["LONG"]
    assert slot.state == "POSITION_OPEN" and slot.entry_qty == pytest.approx(0.017)
    a2, b2, store2 = restart(candles[:8], cfg, store)
    assert a2.slots["LONG"].stop_id == slot.stop_id and b2.position_qty() == pytest.approx(0.017)
    result = run_recovery(a2, now=T0 + timedelta(hours=9))
    assert result["status"] == "RESUMED" and "RECOVERY_RESUMED_POSITION" in result["actions"]
    row = events(store2, "RECOVERY_RESUMED_POSITION")[-1]
    assert_recovery_fields(row)
    assert any(x["alert"] == "PROCESS_RESTART" for x in store2.read("alerts"))
    # the resumed adapter keeps trading the same position: the target fills and the trade closes once
    tp = bar(8, *TP_BAR)
    a2.on_bar_open(8, tp["time"], tp["open"], now=tp["time"])
    a2.on_bar_close(tp, now=tp["time"] + timedelta(hours=1))
    trades = store2.read("trades")
    assert len(trades) == 1 and trades[0]["exit_reason"].startswith("TP")


@pytest.mark.parametrize("missing", [("stop_id",), ("target_id",), ("stop_id", "target_id")])
def test_missing_exits_are_replaced_at_frozen_levels(fake, tmp_path, missing):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    a2, b2, store2 = restart(candles[:8], cfg, store)
    slot = a2.slots["LONG"]
    for attr in missing:
        b2.orders[getattr(slot, attr)].status = "CANCELED"  # gone at the venue
    run_recovery(a2, now=T0 + timedelta(hours=9))
    replaced = events(store2, "RECOVERY_EXIT_REPLACED")
    assert len(replaced) == len(missing)
    for row in replaced:
        assert_recovery_fields(row)
        assert row["reason_code"] == "EXIT_MISSING" and row["qty"] == pytest.approx(0.017)
    live = {o.purpose: o for o in b2.open_orders()}
    assert set(live) == {STOP, TARGET}
    assert live[STOP].stop_price == pytest.approx(58_900.0) and live[STOP].reduce_only
    assert live[TARGET].stop_price == pytest.approx(slot.plan["target"]) and live[TARGET].qty == pytest.approx(0.017)
    assert any(x["alert"] == "RECOVERY_ACTION" for x in store2.read("alerts"))


def test_exit_sized_for_another_quantity_is_resized(fake, tmp_path):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    a2, b2, store2 = restart(candles[:8], cfg, store)
    b2.orders[a2.slots["LONG"].stop_id].qty = 0.010
    run_recovery(a2, now=T0 + timedelta(hours=9))
    row = events(store2, "RECOVERY_EXIT_RESIZED")[-1]
    assert row["reason_code"] == "EXIT_QTY_MISMATCH"
    assert {o.purpose: o.qty for o in b2.open_orders()} == {STOP: pytest.approx(0.017), TARGET: pytest.approx(0.017)}


def test_order_requested_but_never_placed_is_lost_not_reentered(fake, tmp_path):
    candles = candles_for(OPEN_ROWS)
    fake.update({5: setup_for(candles[5])})
    cfg = cfg_for()
    broker = make_broker(cfg)
    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    a1 = PaperAdapter(broker=broker, store=store, cfg=cfg, symbol="BTCUSDT")
    a1.start(candles[:2], 2)
    for k in range(2, 6):
        drive(a1, candles, k)

    def crash(*_a, **_k):
        raise KeyboardInterrupt("process killed between request and acknowledgement")

    broker.place_order = crash
    with pytest.raises(KeyboardInterrupt):
        a1.on_bar_open(6, candles[6]["time"], candles[6]["open"], now=candles[6]["time"])
    state = store.load_state()
    assert list(state["inflight"]) == ["BTCUSDT-LONG-2025-05-01T05:00:00+00:00-E"]
    a2, b2, store2 = restart(candles[:6], cfg, store)
    requested_before = len(events(store2, "ORDER_REQUESTED"))
    result = run_recovery(a2, now=T0 + timedelta(hours=7))
    assert result["status"] == "RESUMED"
    lost = events(store2, "ORDER_LOST")[-1]
    assert_recovery_fields(lost)
    assert lost["action_taken"] == "RECORD_LOST_NO_REENTRY"
    assert "LONG" not in a2.slots and not a2.inflight and b2.open_orders() == []
    assert any(e["category"] == "ORDER_LOST" for e in store2.open_exceptions())
    assert len(events(store2, "ORDER_REQUESTED")) == requested_before  # nothing re-sent


def test_request_that_reached_the_venue_is_adopted(fake, tmp_path):
    candles = candles_for(OPEN_ROWS)
    fake.update({5: setup_for(candles[5])})
    cfg = cfg_for()
    broker = make_broker(cfg)
    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    a1 = PaperAdapter(broker=broker, store=store, cfg=cfg, symbol="BTCUSDT")
    a1.start(candles[:2], 2)
    for k in range(2, 6):
        drive(a1, candles, k)
    real = broker.place_order

    def place_then_crash(order, **kw):
        real(order, **kw)
        store.save_state({**a1.snapshot(), "broker": broker.snapshot()})  # the venue has it
        raise KeyboardInterrupt("killed before the acknowledgement was recorded")

    broker.place_order = place_then_crash
    with pytest.raises(KeyboardInterrupt):
        a1.on_bar_open(6, candles[6]["time"], candles[6]["open"], now=candles[6]["time"])
    a2, b2, store2 = restart(candles[:6], cfg, store)
    run_recovery(a2, now=T0 + timedelta(hours=6, minutes=1))
    assert events(store2, "RECOVERY_ORDER_ADOPTED")
    assert a2.slots["LONG"].state == "ORDER_ACCEPTED" and a2.slots["LONG"].entry_id in {o.client_id for o in b2.open_orders()}


def test_venue_position_without_local_trade_halts(fake, tmp_path):
    rows = [QUIET] * 8
    candles, cfg, store, a1, _ = build(fake, tmp_path, rows[:5] + [LONG_C1] + rows[:2], upto=4)
    a2, b2, store2 = restart(candles[:5], cfg, store)
    b2.position = 0.05  # someone opened a position on the venue
    with pytest.raises(RecoveryHalted) as exc:
        run_recovery(a2, now=T0 + timedelta(hours=6))
    assert exc.value.code == UNRECONCILED_EXTERNAL_POSITION and exc.value.detail["cause"] == "NO_LOCAL_TRADE_RECORD"
    assert a2.halted == UNRECONCILED_EXTERNAL_POSITION
    assert_recovery_fields(events(store2, "RECOVERY_HALTED")[-1])
    assert any(x["alert"] == "UNEXPECTED_POSITION" for x in store2.read("alerts"))
    # halted: the next Candle-1 is missed, no order goes out
    a2.on_bar_close(candles[5], now=candles[5]["time"] + timedelta(hours=1))
    a2.on_bar_open(6, candles[6]["time"], candles[6]["open"], now=candles[6]["time"])
    assert events(store2, "ORDER_REQUESTED") == [] and events(store2, "SIGNAL_MISSED")[-1]["reason"] == "HALTED"


def test_local_closed_but_venue_still_open_halts(fake, tmp_path):
    rows = [QUIET] * 5 + [LONG_C1, C2_FILL, TP_BAR, QUIET, QUIET]
    candles, cfg, store, a1, _ = build(fake, tmp_path, rows, upto=8)
    assert len(store.read("trades")) == 1 and not a1.slots
    a2, b2, store2 = restart(candles[:9], cfg, store)
    b2.position = 0.017
    with pytest.raises(RecoveryHalted) as exc:
        run_recovery(a2, now=T0 + timedelta(hours=10))
    assert exc.value.code == UNRECONCILED_EXTERNAL_POSITION and exc.value.detail["cause"] == "LOCAL_POSITION_CLOSED"


def test_unknown_open_order_halts(fake, tmp_path):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    a2, b2, store2 = restart(candles[:8], cfg, store)
    b2.orders["stranger"] = Order("stranger", STOP, "SELL", STOP_MARKET, 0.017, stop_price=50_000, reduce_only=True,
                                  status="NEW")
    with pytest.raises(RecoveryHalted) as exc:
        run_recovery(a2, now=T0 + timedelta(hours=9))
    assert exc.value.code == UNRECONCILED_EXTERNAL_ORDER


def test_partial_fill_during_downtime_restores_quantity_and_resizes_exits(fake, tmp_path):
    candles, cfg, store, a1, b1 = build(fake, tmp_path, OPEN_ROWS, upto=5)
    a1.on_bar_open(6, candles[6]["time"], candles[6]["open"], now=candles[6]["time"])  # entry resting; then crash
    state_slot = store.load_state()["slots"]["LONG"]
    assert state_slot["state"] == "ORDER_ACCEPTED"
    a2, b2, store2 = restart(candles[:6], cfg, store)

    class Null:
        def on_fill(self, *a):
            pass

    b2.faults.entry_fill_fraction = 0.5  # the venue partially filled while the process was down
    b2.sync(6, (C2_FILL[0], C2_FILL[1], C2_FILL[2], C2_FILL[3]), Null())
    assert b2.position_qty() == pytest.approx(0.008)
    result = run_recovery(a2, now=T0 + timedelta(hours=6, minutes=30))
    assert "RECOVERY_FILL_APPLIED" in result["actions"]
    slot = a2.slots["LONG"]
    assert slot.entry_qty == pytest.approx(0.008)
    exits = {o.purpose: o for o in b2.open_orders() if o.reduce_only}
    assert exits[STOP].qty == pytest.approx(0.008) and exits[TARGET].qty == pytest.approx(0.008)


def test_testnet_restart_applies_a_fill_from_downtime_and_places_exits(fake, tmp_path, monkeypatch):
    ex = FakeTestnet(drift=0.0)
    candles = candles_for(OPEN_ROWS)
    fake.update({5: setup_for(candles[5])})
    cfg = cfg_for()
    store = PaperStore(tmp_path, mode="TESTNET_BROKER_CHECK")
    a1 = PaperAdapter(broker=make_testnet_broker(monkeypatch, ex), store=store, cfg=cfg, symbol="BTCUSDT")
    a1.start(candles[:2], 2)
    for k in range(2, 6):
        drive(a1, candles, k)
    a1.on_bar_open(6, candles[6]["time"], candles[6]["open"], now=candles[6]["time"])
    assert a1.slots["LONG"].state == "ORDER_ACCEPTED"
    # process dies; the Candle-2 stop entry triggers on the exchange while it is down
    ex.price = 60_050.0
    ex._step()
    assert ex.position == pytest.approx(0.017)
    state = store.load_state()
    a2 = PaperAdapter(broker=make_testnet_broker(monkeypatch, ex), store=PaperStore(tmp_path, mode=store.mode), cfg=cfg,
                      symbol="BTCUSDT")
    a2.resume(candles[:6], int(state["first_trade_index"]), state)
    result = run_recovery(a2, now=T0 + timedelta(hours=6, minutes=20))
    assert result["status"] == "RESUMED" and "RECOVERY_FILL_APPLIED" in result["actions"]
    slot = a2.slots["LONG"]
    assert slot.state == "POSITION_OPEN" and slot.entry_qty == pytest.approx(0.017)
    live = [x for x in ex.algos.values() if x["algoStatus"] == "NEW"]
    assert sorted((x["orderType"], x["side"], x["reduceOnly"]) for x in live) == [
        ("STOP_MARKET", "SELL", True), ("TAKE_PROFIT_MARKET", "SELL", True)]
    triggers = {x["orderType"]: float(x["triggerPrice"]) for x in live}
    assert triggers["STOP_MARKET"] == pytest.approx(58_900.0)  # frozen stop; SELL triggers round down to the tick
    assert triggers["TAKE_PROFIT_MARKET"] == pytest.approx(math.floor(slot.plan["target"] * 10 + 1e-9) / 10)


def test_still_halted_store_does_not_resume(fake, tmp_path):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    a1.halt("MAX_CONSECUTIVE_BROKER_ERRORS")
    a2, _, store2 = restart(candles[:8], cfg, store)
    with pytest.raises(RecoveryHalted) as exc:
        run_recovery(a2)
    assert exc.value.code == "STILL_HALTED"
    store2.resolve_exception(a1.halt_exception_id, "broker errors were a testnet outage; checked", by="op")
    a3, _, _ = restart(candles[:8], cfg, store2)
    assert a3.halted is None and run_recovery(a3)["status"] == "RESUMED"


def test_cli_requires_recover_flag_for_a_saved_store(fake, tmp_path, capsys):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    store.update_manifest(counts_toward_oos=False)
    assert cli.main(["run", "--mode", "virtual_paper_mainnet", "--allow-non-oos", "--store", str(tmp_path)]) == 2
    assert "RECOVERY_REQUIRED" in capsys.readouterr().out


# ----------------------------------------------------------------- 3. broker check (fake testnet)


class FakeTestnet:
    """A small USD-M testnet: MARKET orders fill at once, conditional orders trigger as the price drifts."""

    def __init__(self, *, price=60_000.0, partial=False, accept_duplicate_algo=False, drift=-0.0004):
        self.price, self.partial, self.accept_dup, self.drift = price, partial, accept_duplicate_algo, drift
        self.position = 0.0
        self.orders: dict[int, dict] = {}
        self.algos: dict[int, dict] = {}
        self.trades: list[dict] = []
        self.next_id = 100

    def _id(self):
        self.next_id += 1
        return self.next_id

    @staticmethod
    def err(code, msg="", status=400):
        return httpx.Response(status, json={"code": code, "msg": msg})

    def _fill_market(self, side, qty, price, client_id, parts=1):
        oid = self._id()
        signed = qty if side == "BUY" else -qty
        self.position = round(self.position + signed, 10)
        each = round(qty / parts, 10)
        for j in range(parts):
            q = each if j < parts - 1 else round(qty - each * (parts - 1), 10)
            self.trades.append({"id": self._id(), "orderId": oid, "qty": str(q), "price": str(price),
                                "commission": str(round(q * price * 0.0004, 8))})
        self.orders[oid] = {"orderId": oid, "clientOrderId": client_id, "status": "FILLED", "type": "MARKET",
                            "side": side, "origQty": str(qty), "executedQty": str(qty)}
        return self.orders[oid]

    @staticmethod
    def would_trigger(order_type, side, trig, price):
        rising = (order_type == "STOP_MARKET" and side == "BUY") or (order_type == "TAKE_PROFIT_MARKET" and side == "SELL")
        return price >= trig if rising else price <= trig

    def _step(self):
        self.price *= 1 + self.drift
        for a in self.algos.values():
            if a["algoStatus"] != "NEW":
                continue
            trig = float(a["triggerPrice"])
            if self.would_trigger(a["orderType"], a["side"], trig, self.price):
                o = self._fill_market(a["side"], float(a["quantity"]), trig, a["clientAlgoId"])
                a["algoStatus"], a["actualOrderId"] = "FINISHED", str(o["orderId"])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        t = request.extensions.get("timeout") or {}
        if t.get("read") is not None and t["read"] < 0.01:
            raise httpx.ReadTimeout("timed out", request=request)
        path, q, m = request.url.path, dict(request.url.params), request.method
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(200, json={"symbols": [{"symbol": "BTCUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "100"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"}]}]})
        if path == "/fapi/v1/ticker/price":
            return httpx.Response(200, json={"price": str(self.price)})
        if path == "/fapi/v2/balance":
            return httpx.Response(200, json=[{"asset": "USDT", "balance": "5000"}])
        if path == "/fapi/v3/positionRisk":
            return httpx.Response(200, json=[{"symbol": "BTCUSDT", "positionAmt": str(self.position)}])
        if path == "/fapi/v1/order" and m == "POST":
            if q["type"] != "MARKET":
                return self.err(-4120, "Order type not supported for this endpoint.")
            if any(o["clientOrderId"] == q["newClientOrderId"] for o in self.orders.values()):
                return self.err(-4116, "clientOrderId is duplicated")
            qty = float(q["quantity"])
            if q.get("reduceOnly") == "true":
                closing = (q["side"] == "SELL" and self.position >= qty) or (q["side"] == "BUY" and -self.position >= qty)
                if not closing:
                    return self.err(-2022, "ReduceOnly Order is rejected.")
            return httpx.Response(200, json=self._fill_market(q["side"], qty, self.price, q["newClientOrderId"],
                                                              parts=2 if self.partial else 1))
        if path == "/fapi/v1/order" and m == "GET":
            for o in self.orders.values():
                if str(o["orderId"]) == q.get("orderId") or o["clientOrderId"] == q.get("origClientOrderId"):
                    return httpx.Response(200, json=o)
            return self.err(-2013, "Order does not exist.")
        if path == "/fapi/v1/algoOrder" and m == "POST":
            assert q["algoType"] == "CONDITIONAL" and "triggerPrice" in q and "stopPrice" not in q
            dup = any(a["clientAlgoId"] == q["clientAlgoId"] and a["algoStatus"] == "NEW" for a in self.algos.values())
            if dup and not self.accept_dup:
                return self.err(-4116, "clientOrderId is duplicated")
            trig = float(q["triggerPrice"])
            if self.would_trigger(q["type"], q["side"], trig, self.price):
                return self.err(-2021, "Order would immediately trigger.")
            aid = self._id()
            self.algos[aid] = {"algoId": aid, "clientAlgoId": q["clientAlgoId"], "algoType": "CONDITIONAL",
                               "orderType": q["type"], "side": q["side"], "quantity": q["quantity"],
                               "triggerPrice": q["triggerPrice"], "algoStatus": "NEW", "actualOrderId": "",
                               "reduceOnly": q.get("reduceOnly") == "true"}
            return httpx.Response(200, json=self.algos[aid])
        if path == "/fapi/v1/algoOrder" and m == "GET":
            self._step()
            for a in self.algos.values():
                if a["clientAlgoId"] == q.get("clientAlgoId"):
                    return httpx.Response(200, json=a)
            return self.err(-2013, "Order does not exist.")
        if path == "/fapi/v1/algoOrder" and m == "DELETE":
            for a in self.algos.values():
                if a["clientAlgoId"] == q.get("clientAlgoId") and a["algoStatus"] == "NEW":
                    a["algoStatus"] = "CANCELED"
                    return httpx.Response(200, json={"algoId": a["algoId"], "code": "200", "msg": "success"})
            return self.err(-2011, "Unknown order sent.")
        if path == "/fapi/v1/openOrders":
            return httpx.Response(200, json=[])
        if path == "/fapi/v1/openAlgoOrders":
            return httpx.Response(200, json=[a for a in self.algos.values() if a["algoStatus"] == "NEW"])
        if path == "/fapi/v1/userTrades":
            return httpx.Response(200, json=[t for t in self.trades if str(t["orderId"]) == q.get("orderId")])
        return self.err(-1, f"unhandled {m} {path}", status=404)


def make_testnet_broker(monkeypatch, exchange):
    monkeypatch.setenv(bn.PAPER_ENV, "1")
    client = httpx.Client(transport=httpx.MockTransport(exchange))
    return bn.BinanceTestnetBroker(symbol="BTCUSDT", api_key="k", api_secret="s", client=client)


def check_store(tmp_path):
    return PaperStore(tmp_path, mode="TESTNET_BROKER_CHECK")


def test_broker_check_passes_on_a_well_behaved_testnet(monkeypatch, tmp_path):
    ex = FakeTestnet(partial=True)
    result = run_broker_check(make_testnet_broker(monkeypatch, ex), check_store(tmp_path), exit_wait_s=30, poll_s=0,
                              sleep=lambda s: None, run_tag="t1")
    assert result["result"] == BROKER_CHECK_PASS, {k: v for k, v in result["checks"].items() if v["status"] != "PASS"}
    assert result["counts_toward_oos"] is False and result["strategy_validation"] is False
    assert result["checks"]["partial_fill_handling"]["status"] == "PASS"
    assert result["checks"]["api_timeout_handling"]["unknown"] is True
    assert ex.position == 0 and not [a for a in ex.algos.values() if a["algoStatus"] == "NEW"]
    kinds = [e["event"] for e in PaperStore(tmp_path, mode="TESTNET_BROKER_CHECK").read("events")]
    assert {"ORDER_REQUESTED", "ORDER_ACCEPTED", "ORDER_REJECTED", "ORDER_FILLED", "ORDER_CANCELED",
            "SESSION_TERMINAL", "CLEANUP"} <= set(kinds)


def test_broker_check_partial_fill_is_skipped_when_the_testnet_fills_at_once(monkeypatch, tmp_path):
    result = run_broker_check(make_testnet_broker(monkeypatch, FakeTestnet()), check_store(tmp_path), exit_wait_s=30,
                              poll_s=0, sleep=lambda s: None, run_tag="t2")
    assert result["checks"]["partial_fill_handling"]["status"] == "SKIPPED"
    assert result["result"] == BROKER_CHECK_PASS


def test_broker_check_fails_when_duplicates_are_accepted(monkeypatch, tmp_path):
    ex = FakeTestnet(accept_duplicate_algo=True)
    result = run_broker_check(make_testnet_broker(monkeypatch, ex), check_store(tmp_path), exit_wait_s=30, poll_s=0,
                              sleep=lambda s: None, run_tag="t3")
    assert result["result"] == BROKER_CHECK_FAIL
    assert result["checks"]["duplicate_client_id"]["status"] == "FAIL"
    assert ex.position == 0  # cleanup still flattens


def test_broker_check_fails_when_no_exit_triggers(monkeypatch, tmp_path):
    ex = FakeTestnet(drift=0.0)
    result = run_broker_check(make_testnet_broker(monkeypatch, ex), check_store(tmp_path), exit_wait_s=0, poll_s=0,
                              sleep=lambda s: None, run_tag="t4")
    assert result["result"] == BROKER_CHECK_FAIL
    assert result["checks"]["exit_fill_cancels_other"]["status"] == "FAIL"
    assert ex.position == 0 and not [a for a in ex.algos.values() if a["algoStatus"] == "NEW"]


def test_broker_check_refuses_a_non_flat_account(monkeypatch, tmp_path):
    ex = FakeTestnet()
    ex.position = 0.5
    result = run_broker_check(make_testnet_broker(monkeypatch, ex), check_store(tmp_path), exit_wait_s=0, poll_s=0,
                              sleep=lambda s: None, run_tag="t5")
    assert result["result"] == BROKER_CHECK_FAIL and "not flat" in result["checks"]["order_rejection"]["detail"]


def test_cli_broker_check_needs_env_and_keys(monkeypatch, capsys):
    monkeypatch.delenv(bn.PAPER_ENV, raising=False)
    assert cli.main(["broker-check", "--symbol", "BTCUSDT", "--max-trades", "1"]) == 2
    monkeypatch.setenv(bn.PAPER_ENV, "1")
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    assert cli.main(["broker-check", "--symbol", "BTCUSDT", "--max-trades", "1"]) == 2
    assert cli.main(["broker-check", "--base-url", "https://fapi.binance.com"]) == 2
    assert "REFUSED" in capsys.readouterr().out


# ----------------------------------------------------------------- 4. conditional-order endpoints


@pytest.mark.parametrize("otype,reduce,path", [
    ("MARKET", False, "/fapi/v1/order"),
    (STOP_MARKET, False, "/fapi/v1/algoOrder"),
    (STOP_MARKET, True, "/fapi/v1/algoOrder"),
    (TAKE_PROFIT_MARKET, True, "/fapi/v1/algoOrder"),
])
def test_order_types_use_their_current_endpoint(otype, reduce, path):
    order = Order("x", STOP, "SELL", otype, 0.017, stop_price=None if otype == "MARKET" else 58_900.0,
                  reduce_only=reduce)
    assert bn.BinanceTestnetBroker.endpoint_for(order) == path
    params = bn.BinanceTestnetBroker.order_params(order, "BTCUSDT")
    if path.endswith("algoOrder"):
        assert params["algoType"] == "CONDITIONAL" and params["triggerPrice"] == "58900" and "clientAlgoId" in params
    else:
        assert "newClientOrderId" in params and "triggerPrice" not in params


@pytest.mark.parametrize("otype", ["LIMIT", "STOP", "TAKE_PROFIT", "TRAILING_STOP_MARKET", "OCO"])
def test_unsupported_order_types_fail_before_the_exchange(otype, monkeypatch):
    order = Order("x", STOP, "SELL", otype, 0.017, stop_price=58_900.0)
    with pytest.raises(UnsupportedOrderType):
        require_supported(otype)
    with pytest.raises(UnsupportedOrderType):
        bn.BinanceTestnetBroker.order_params(order, "BTCUSDT")
    with pytest.raises(UnsupportedOrderType):
        SimulatedBroker().place_order(order, bar_index=0)


def test_capabilities_file_records_the_algo_migration():
    caps = capabilities()
    assert caps["order_types"][STOP_MARKET]["endpoint"] == "POST /fapi/v1/algoOrder"
    assert caps["order_types"][TAKE_PROFIT_MARKET]["client_id_param"] == "clientAlgoId"
    assert caps["order_types"]["MARKET"]["endpoint"] == "POST /fapi/v1/order"
    assert all(caps["order_types"][t]["reduce_only"] for t in caps["order_types"])
    assert caps["migration"]["effective"] == "2025-12-09" and caps["hosts"]["testnet_rest"].endswith("demo-fapi.binance.com")
    assert caps["known_limitations"]


def test_exchange_client_ids_are_valid_and_stable():
    long_id = "BTCUSDT-LONG-2026-10-16T05:00:00+00:00-S12"
    ex = bn.exchange_client_id(long_id)
    assert re.match(capabilities()["client_id"]["pattern"], ex) and ex == bn.exchange_client_id(long_id)
    assert bn.exchange_client_id("c12chk_x_S1") == "c12chk_x_S1"


@pytest.mark.parametrize("code,reason", [(-4120, UNSUPPORTED_ENDPOINT), (-4116, DUPLICATE_CLIENT_ID)])
def test_new_error_codes_map(monkeypatch, code, reason):
    broker = make_testnet_broker(monkeypatch, lambda r: httpx.Response(400, json={"code": code, "msg": "x"}))
    with pytest.raises(BrokerRejected) as exc:
        broker.place_order(Order("c", ENTRY, "BUY", "MARKET", 0.017), bar_index=0)
    assert exc.value.reason == reason


def test_timeout_after_send_is_an_unknown_state(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    broker = make_testnet_broker(monkeypatch, handler)
    with pytest.raises(BrokerUnavailable) as exc:
        broker.place_order(Order("c", ENTRY, "BUY", STOP_MARKET, 0.017, stop_price=60_012.0), bar_index=0)
    assert exc.value.unknown is True


def test_adapter_looks_up_an_order_after_a_timeout_instead_of_resending(fake, tmp_path):
    candles = candles_for(OPEN_ROWS)
    fake.update({5: setup_for(candles[5])})
    cfg = cfg_for()
    broker = make_broker(cfg)
    real = broker.place_order

    def placed_but_timed_out(order, **kw):
        real(order, **kw)
        raise BrokerUnavailable("timeout after send", unknown=True)

    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    adapter = PaperAdapter(broker=broker, store=store, cfg=cfg, symbol="BTCUSDT")
    adapter.start(candles[:2], 2)
    for k in range(2, 6):
        drive(adapter, candles, k)
    broker.place_order = placed_but_timed_out
    adapter.on_bar_open(6, candles[6]["time"], candles[6]["open"], now=candles[6]["time"])
    assert len(events(store, "ORDER_REQUESTED")) == 1 and events(store, "ORDER_FOUND_AFTER_TIMEOUT")
    assert adapter.slots["LONG"].state == "ORDER_ACCEPTED" and not adapter.inflight


# ----------------------------------------------------------------- 5. data backfill and gaps


class FakeKlines:
    def __init__(self, bars):
        self.bars = bars

    def history(self, start, now):
        return [b for b in self.bars if start <= b["time"] and b["time"] + timedelta(hours=1) <= now]


class FakeFunding:
    def __init__(self, rows):
        self.rows = rows

    def settlements(self, start, end):
        return [r for r in self.rows if start <= r["time"] < end]


START = datetime(2026, 10, 12, tzinfo=UTC)


def hourly(n, skip=()):
    return [{"time": START + timedelta(hours=i), "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i, "close": 1.5 + i,
             "volume": 10.0} for i in range(n) if i not in skip]


def settlements(n, skip=()):
    return [{"time": START + timedelta(hours=8 * j, milliseconds=8), "rate": 0.0001, "mark_price": 100.0}
            for j in range(n) if j not in skip]


def test_gap_detection_reports_and_never_fabricates(tmp_path):
    repo = BF.InMemoryRepository()
    now = START + timedelta(hours=24, minutes=5)
    report = BF.backfill(["BTCUSDT"], START, now, repo=repo, state_path=tmp_path / "wm.json", now=now,
                         kline_source=lambda s: FakeKlines(hourly(24, skip={5, 6})),
                         funding_source=lambda s: FakeFunding(settlements(4, skip={1})))
    c = report["symbols"]["BTCUSDT"]["candles_1h"]
    f = report["symbols"]["BTCUSDT"]["funding"]
    assert c["status"] == "GAPS" and c["gaps"] == [{"from": (START + timedelta(hours=5)).isoformat(),
                                                    "to": (START + timedelta(hours=6)).isoformat(), "bars": 2}]
    assert f["status"] == "GAPS" and f["gaps"] == [{"settlement": (START + timedelta(hours=8)).isoformat()}]
    assert ("BTCUSDT", START + timedelta(hours=5)) not in repo.candles and len(repo.funding) == 3
    assert BF.candle_gaps([], START, START + timedelta(hours=2))[0]["bars"] == 2


def test_backfill_is_idempotent_and_keeps_watermarks(tmp_path):
    repo = BF.InMemoryRepository()
    now = START + timedelta(hours=10, minutes=1)
    kw = dict(repo=repo, state_path=tmp_path / "wm.json", now=now,
              kline_source=lambda s: FakeKlines(hourly(10)), funding_source=lambda s: FakeFunding(settlements(2)))
    first = BF.backfill(["BTCUSDT", "ETHUSDT"], START, now, **kw)
    second = BF.backfill(["BTCUSDT", "ETHUSDT"], START, now, **kw)
    for sym in ("BTCUSDT", "ETHUSDT"):
        assert first["symbols"][sym]["candles_1h"]["inserted"] == 10
        assert second["symbols"][sym]["candles_1h"]["inserted"] == 0
        assert second["symbols"][sym]["candles_1h"]["existing"] == 10
        assert second["symbols"][sym]["funding"]["inserted"] == 0
    assert second["ok"] and len(repo.candles) == 20
    marks = json.loads((tmp_path / "wm.json").read_text())
    assert marks["BTCUSDT"]["candles_1h"]["last_bar_open"] == (START + timedelta(hours=9)).isoformat()
    assert marks["ETHUSDT"]["funding"]["last_success_at"] == now.isoformat()


def test_backfill_reports_conflicts_without_overwriting(tmp_path):
    repo = BF.InMemoryRepository()
    now = START + timedelta(hours=3, minutes=1)
    BF.backfill(["BTCUSDT"], START, now, repo=repo, state_path=tmp_path / "wm.json", now=now,
                kline_source=lambda s: FakeKlines(hourly(3)), funding_source=lambda s: FakeFunding([]))
    changed = hourly(3)
    changed[1]["close"] = 999.0
    report = BF.backfill(["BTCUSDT"], START, now, repo=repo, state_path=tmp_path / "wm.json", now=now,
                         kline_source=lambda s: FakeKlines(changed), funding_source=lambda s: FakeFunding([]))
    assert not report["ok"] and report["symbols"]["BTCUSDT"]["candles_1h"]["conflicts"]
    assert repo.candles[("BTCUSDT", START + timedelta(hours=1))]["close"] == 2.5


def test_failed_fetch_keeps_the_watermark(tmp_path):
    repo = BF.InMemoryRepository()
    now = START + timedelta(hours=3, minutes=1)
    BF.backfill(["BTCUSDT"], START, now, repo=repo, state_path=tmp_path / "wm.json", now=now,
                kline_source=lambda s: FakeKlines(hourly(3)), funding_source=lambda s: FakeFunding(settlements(1)))
    before = json.loads((tmp_path / "wm.json").read_text())

    class Down:
        def history(self, *a):
            raise httpx.ConnectError("down")

        def settlements(self, *a):
            raise httpx.ConnectError("down")

    report = BF.backfill(["BTCUSDT"], START, now + timedelta(hours=5), repo=repo, state_path=tmp_path / "wm.json",
                         now=now + timedelta(hours=5), kline_source=lambda s: Down(), funding_source=lambda s: Down())
    assert not report["ok"] and report["symbols"]["BTCUSDT"]["candles_1h"]["status"] == "ERROR"
    assert json.loads((tmp_path / "wm.json").read_text()) == before


def test_continuous_runner_repeats_and_counts_failures():
    reports = iter([{"ok": True, "symbols": {}}, {"ok": False, "symbols": {}}, {"ok": True, "symbols": {}}])
    slept, seen = [], []
    failures = BF.run_forever(lambda: next(reports), interval_s=60, max_loops=3, sleep=slept.append, emit=seen.append)
    assert failures == 1 and slept == [60, 60] and [r["pass"] for r in seen] == [1, 2, 3]


# ----------------------------------------------------------------- 6. missing funding blocks OOS


def funding_rows_for(hours):
    return [{"time": T0 + timedelta(hours=h), "rate": 0.0001, "mark_price": None} for h in hours]


def test_missing_funding_for_an_open_position_raises_and_blocks_oos(fake, tmp_path, monkeypatch):
    rows = [QUIET] * 5 + [LONG_C1, C2_FILL] + [QUIET] * 6
    candles, cfg, store, a, _ = build(fake, tmp_path, rows, upto=11, require_funding=True,
                                      funding_rows=funding_rows_for([0]), cfg_kw={"funding_enabled": True})
    missing = [e for e in store.open_exceptions() if e["category"] == "MISSING_FUNDING_DATA"]
    assert [e["settlement"] for e in missing] == [(T0 + timedelta(hours=8)).isoformat()]
    assert any(x["alert"] == "MISSING_FUNDING" for x in store.read("alerts"))

    winners = [{"outcome": "TP1" if i % 3 else "SL", "entry_time": (C.OOS_START + timedelta(days=5 * i)).isoformat(),
                "exit_time": (C.OOS_START + timedelta(days=5 * i, hours=10)).isoformat(),
                "net_pnl_usd": 30.0 if i % 3 else -20.0, "funding_usd": 0.0, "funding_coverage": "FULL"}
               for i in range(30)]
    monkeypatch.setattr(oos.v3, "run_candle12_v3", lambda *a, **k: {"trades": winners})
    start = C.OOS_START - timedelta(hours=C.WARMUP_BARS)
    quiet = [{"time": start + timedelta(hours=i), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
             for i in range(C.WARMUP_BARS + 48)]
    read = oos.evaluate_oos(candles=quiet, funding_rows=None, now=C.earliest_evaluation() + timedelta(days=1),
                            lock=C.build_lock(), open_exceptions=store.open_exceptions(),
                            store_mode="VIRTUAL_PAPER_MAINNET")
    assert read["acceptance"] is False and "EXECUTION_MISMATCH" in read["failures"]


def test_covered_funding_raises_nothing(fake, tmp_path):
    rows = [QUIET] * 5 + [LONG_C1, C2_FILL] + [QUIET] * 6
    _, _, store, _, _ = build(fake, tmp_path, rows, upto=11, require_funding=True,
                              funding_rows=funding_rows_for([0, 8, 16]), cfg_kw={"funding_enabled": True})
    assert not [e for e in store.open_exceptions() if e["category"] == "MISSING_FUNDING_DATA"]


# ----------------------------------------------------------------- 7. freeze and drift


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "SUPERSEDES", {})  # supersession has its own tests below
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "docs" / C.CONTRACT_DOC.name).write_text("contract v1\n", encoding="utf-8")
    from app.research.candle12_v3_paper.freeze import contract_paths

    for rel in contract_paths():  # every file the contract depends on must exist to freeze
        path = root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"placeholder {rel}\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


@pytest.mark.parametrize("dirt", ["untracked", "contract_edit", "contract_missing"])
def test_freeze_refuses_a_dirty_tree(repo, dirt):
    if dirt == "untracked":
        (repo / "scratch.txt").write_text("x")
    elif dirt == "contract_edit":
        (repo / C.CODE_FILES[0]).write_text("edited, not committed")
    else:
        (repo / C.CODE_FILES[0]).unlink()
    with pytest.raises(FreezeError) as exc:
        freeze_oos(root=repo, now=datetime(2026, 10, 15, 20, tzinfo=UTC))
    assert exc.value.code == DIRTY_WORKING_TREE
    assert not (repo / "docs" / C.LOCK_PATH.name).exists()


def test_freeze_writes_commits_and_tags_the_lock(repo, capsys):
    now = datetime(2026, 10, 15, 20, tzinfo=UTC)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    out = freeze_oos(root=repo, now=now)
    lock = json.loads((repo / "docs" / C.LOCK_PATH.name).read_text())
    fz = lock["freeze"]
    assert fz["git_commit"] == head and fz["git_branch"] == "main" and fz["tag"] == "candle12-v3-oos-2-freeze-2026-10-15"
    assert fz["contract_id"] == C.CONTRACT_ID == "CANDLE12_V3_EXEC_FIX/OOS-2"
    assert fz["oos_start"] == "2026-10-16T00:00:00+00:00" and fz["earliest_evaluation"] == "2027-10-16T00:00:00+00:00"
    assert fz["allowed_modes"] == ["virtual_paper_mainnet"] and fz["allowed_symbols"] == ["BTCUSDT"]
    assert fz["config_fingerprint"] == C.config_fingerprint() and fz["frozen_after_oos_start"] is False
    assert lock["lock_hash"] == C.lock_hash(lock) and out["lock_hash"] == lock["lock_hash"]
    tags = subprocess.run(["git", "tag", "--list"], cwd=repo, capture_output=True, text=True).stdout.split()
    assert tags == ["candle12-v3-oos-2-freeze-2026-10-15"]
    clean = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert clean == ""
    with pytest.raises(FreezeError) as exc:  # never re-frozen in place, on any day
        freeze_oos(root=repo, now=now + timedelta(hours=1))
    assert exc.value.code == LOCK_ALREADY_FROZEN


def test_freeze_refuses_at_or_after_the_oos_start(repo):
    with pytest.raises(FreezeError) as exc:
        freeze_oos(root=repo, now=C.OOS_START)
    assert exc.value.code == FREEZE_AFTER_OOS_START
    assert not (repo / "docs" / C.LOCK_PATH.name).exists()


def test_freeze_refuses_a_lock_from_an_unrelated_contract(repo):
    (repo / "docs" / C.LOCK_PATH.name).write_text(json.dumps({"contract_id": "OTHER/OOS-9"}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "other lock")
    with pytest.raises(FreezeError) as exc:
        freeze_oos(root=repo, now=datetime(2026, 10, 15, 20, tzinfo=UTC))
    assert exc.value.code == UNKNOWN_EXISTING_LOCK


def _supersession_repo(repo, monkeypatch, *, tamper=False):
    old_lock = repo / "docs" / "review" / "lock.OOS-1.json"
    old_doc = repo / "docs" / "review" / "contract.OOS-1.md"
    old_lock.parent.mkdir(parents=True)
    old_lock.write_bytes(b'{"contract_id": "CANDLE12_V3_EXEC_FIX/OOS-1"}')
    old_doc.write_bytes(b"OOS-1 text\n")
    # the in-place rewrite at the canonical path, as it was found on 2026-10-15
    (repo / "docs" / C.LOCK_PATH.name).write_text(json.dumps({"contract_id": "CANDLE12_V3_EXEC_FIX/OOS-1",
                                                               "freeze": None}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "drift review")
    review = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    import hashlib

    sup = {"contract_id": "CANDLE12_V3_EXEC_FIX/OOS-1", "status": "SUPERSEDED",
           "lock_path": "docs/review/lock.OOS-1.json", "contract_doc_path": "docs/review/contract.OOS-1.md",
           "lock_sha256": hashlib.sha256(old_lock.read_bytes()).hexdigest(),
           "contract_doc_sha256": "0" * 64 if tamper else hashlib.sha256(old_doc.read_bytes()).hexdigest(),
           "drift_review_commit": review}
    monkeypatch.setattr(C, "SUPERSEDES", sup)
    return sup


def test_freeze_records_a_verified_supersession(repo, monkeypatch):
    sup = _supersession_repo(repo, monkeypatch)
    out = freeze_oos(root=repo, now=datetime(2026, 10, 15, 20, tzinfo=UTC))
    fz = out["freeze"]
    assert fz["supersedes"]["verified"] is True and fz["supersedes"]["lock_sha256"] == sup["lock_sha256"]
    assert fz["replaced_file_at_lock_path"]["contract_id"] == "CANDLE12_V3_EXEC_FIX/OOS-1"
    lock = json.loads((repo / "docs" / C.LOCK_PATH.name).read_text())
    assert lock["contract_id"] == C.CONTRACT_ID and lock["lock_hash"] == C.lock_hash(lock)
    assert (repo / sup["lock_path"]).read_bytes() == b'{"contract_id": "CANDLE12_V3_EXEC_FIX/OOS-1"}'  # untouched


def test_freeze_refuses_an_unverified_supersession(repo, monkeypatch):
    _supersession_repo(repo, monkeypatch, tamper=True)
    with pytest.raises(FreezeError) as exc:
        freeze_oos(root=repo, now=datetime(2026, 10, 15, 20, tzinfo=UTC))
    assert exc.value.code == SUPERSEDED_LOCK_UNVERIFIED


def test_guard_passes_a_clean_freeze_and_blocks_drift(repo, monkeypatch):
    freeze_oos(root=repo, now=datetime(2026, 10, 15, 20, tzinfo=UTC))
    lock_path = repo / "docs" / C.LOCK_PATH.name
    assert guard.fingerprint_status(lock_path, repo)["status"] == guard.OK
    assert guard.preflight(M.VIRTUAL_PAPER_MAINNET, lock_path=lock_path, root=repo)["eligible"] is True
    # code drift: a fingerprinted file changes
    path = repo / C.CODE_FILES[0]
    original = path.read_bytes()
    path.write_text("changed")
    pre = guard.preflight(M.VIRTUAL_PAPER_MAINNET, lock_path=lock_path, root=repo)
    assert pre["eligible"] is False and pre["status"] == guard.CODE_DRIFT
    with pytest.raises(guard.DriftError) as exc:
        guard.require_oos_eligible(M.VIRTUAL_PAPER_MAINNET, lock_path=lock_path, root=repo)
    assert exc.value.code == guard.CODE_DRIFT
    path.write_bytes(original)
    # config drift: a contract term changes
    monkeypatch.setattr(C, "MIN_CLOSED_TRADES", 29)
    assert guard.preflight(M.VIRTUAL_PAPER_MAINNET, lock_path=lock_path, root=repo)["status"] == guard.CONFIG_DRIFT
    # testnet and smoke are never blocked, and never eligible
    t = guard.preflight(M.TESTNET_BROKER_CHECK, lock_path=lock_path, root=repo)
    assert t["label"] == M.NON_OOS and t["eligible"] is False


def test_guard_flags_an_unfrozen_or_tampered_lock(repo):
    lock_path = repo / "docs" / C.LOCK_PATH.name
    assert guard.fingerprint_status(lock_path, repo)["status"] == guard.LOCK_MISSING
    C.write_lock(lock_path, repo)  # the old freeze-contract lock has no freeze block
    assert guard.fingerprint_status(lock_path, repo)["status"] == guard.NOT_FROZEN
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "old lock")
    freeze_oos(root=repo, now=datetime(2026, 10, 15, 20, tzinfo=UTC))
    lock = json.loads(lock_path.read_text())
    lock["freeze"]["allowed_symbols"] = ["BTCUSDT", "ETHUSDT"]  # edited by hand after the freeze
    lock_path.write_text(json.dumps(lock))
    assert guard.fingerprint_status(lock_path, repo)["status"] == guard.LOCK_TAMPERED


def test_cli_refuses_an_oos_run_without_a_clean_freeze(tmp_path, capsys):
    # The repo's lock is not frozen by freeze-oos yet (or drifted): the OOS-eligible run must not start.
    assert cli.main(["run", "--mode", "virtual_paper_mainnet", "--store", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and any(c in out for c in ("NOT_FROZEN", "CODE_DRIFT", "CONFIG_DRIFT", "LOCK_MISSING",
                                                       "LOCK_TAMPERED"))
    assert not (tmp_path / "manifest.json").exists()


def test_oos_reads_refuse_non_eligible_virtual_stores_and_early_dates():
    assert C.earliest_evaluation() == datetime(2027, 10, 16, tzinfo=UTC)
    with pytest.raises(oos.OOSRefused) as exc:
        oos.evaluate_oos(candles=[], funding_rows=None, now=datetime(2027, 10, 15, 23, 59, tzinfo=UTC), lock={},
                         open_exceptions=[], store_mode="VIRTUAL_PAPER_MAINNET", manifest={"counts_toward_oos": True})
    assert exc.value.reason == "TOO_EARLY"
    with pytest.raises(oos.OOSRefused) as exc:
        oos.evaluate_oos(candles=[], funding_rows=None, now=datetime(2027, 10, 17, tzinfo=UTC), lock={},
                         open_exceptions=[], store_mode="VIRTUAL_PAPER_MAINNET", manifest={"counts_toward_oos": False})
    assert exc.value.reason == "NOT_OOS_ELIGIBLE"


# ----------------------------------------------------------------- 8. safety


def test_kill_switch_blocks_entries_but_keeps_exits(fake, tmp_path):
    kill = tmp_path / "KILL"
    rows = [QUIET] * 5 + [LONG_C1, C2_FILL, QUIET, TP_BAR, QUIET]
    candles, cfg, store, a, _ = build(fake, tmp_path / "s", rows, upto=7, safety=SafetyLimits(kill_switch_path=kill))
    engage_kill_switch(kill, by="test", reason="drill")
    drive(a, candles, 8)  # the open position's target still fills
    assert len(store.read("trades")) == 1
    # a new Candle-1 while the switch is engaged: missed, no order
    fake[9] = setup_for(candles[5])
    a.on_bar_close(bar(9, *LONG_C1), now=T0 + timedelta(hours=10))
    requested = len(events(store, "ORDER_REQUESTED"))
    a.on_bar_open(10, T0 + timedelta(hours=10), 59_950.0, now=T0 + timedelta(hours=10))
    assert len(events(store, "ORDER_REQUESTED")) == requested
    assert events(store, "SIGNAL_MISSED")[-1]["reason"] == "KILL_SWITCH"
    assert any(x["alert"] == "KILL_SWITCH" for x in store.read("alerts"))


def test_cli_kill_switch(tmp_path, capsys):
    path = tmp_path / "KILL"
    assert cli.main(["kill-switch", "engage", "--path", str(path), "--reason", "drill"]) == 0 and path.exists()
    assert cli.main(["kill-switch", "release", "--path", str(path)]) == 0 and not path.exists()


def test_consecutive_broker_errors_halt(fake, tmp_path):
    rows = [QUIET] * 5 + [LONG_C1, C2_FILL, QUIET]
    candles, cfg, store, a, _ = build(fake, tmp_path, rows, upto=6, faults=SimFaults(unavailable_calls=5),
                                      safety=SafetyLimits(max_consecutive_broker_errors=2))
    assert a.halted == "MAX_CONSECUTIVE_BROKER_ERRORS"
    assert len(events(store, "ORDER_REQUESTED")) == 2  # stopped retrying at the limit
    assert any(e["category"] == "HALTED" for e in store.open_exceptions())


def test_daily_order_attempt_limit(fake, tmp_path):
    rows = [QUIET] * 5 + [LONG_C1, C2_FILL, QUIET]
    _, _, store, _, _ = build(fake, tmp_path, rows, upto=6, safety=SafetyLimits(max_daily_order_attempts=0))
    assert events(store, "ORDER_REQUESTED") == []
    assert events(store, "SIGNAL_MISSED")[-1]["reason"] == "MAX_DAILY_ORDER_ATTEMPTS"


def test_heartbeat_and_status(fake, tmp_path):
    class MainnetFeed:
        base_url = "https://fapi.binance.com"

        def __init__(self, bars):
            self.bars = bars

        def history(self, start, now):
            return [b for b in self.bars if start <= b["time"] and b["time"] + timedelta(hours=1) <= now]

        def forming_bar(self, now):
            live = [b for b in self.bars if b["time"] <= now < b["time"] + timedelta(hours=1)]
            return live[-1] if live else None

        def last_price(self):
            return 60_000.0

    candles = candles_for(OPEN_ROWS)
    fake.update({5: setup_for(candles[5])})
    cfg = cfg_for()
    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    adapter = PaperAdapter(broker=make_broker(cfg), store=store, cfg=cfg, symbol="BTCUSDT")
    # live clock: started at 05:01, then one poll at 06:01 (Candle-2 forming) and one at 07:01
    ticks = iter([T0 + timedelta(hours=h, minutes=1) for h in (5, 5, 6, 7)])
    cli.run_live(adapter, MainnetFeed(candles), funding_source=None, poll_s=0, clock=lambda: next(ticks),
                 max_loops=2, first_trade_at=T0 + timedelta(hours=2), history_start=T0, sleep=lambda s: None)
    beats = store.read("heartbeats")
    assert len(beats) == 2 and beats[-1]["slots"] == ["LONG"] and beats[-1]["halted"] is None
    status = build_status(tmp_path, kill_switch_path=tmp_path / "KILL")
    assert status["mode"] == "virtual_paper_mainnet" and status["current_state"] == "LONG:POSITION_OPEN"
    assert status["open_position"]["LONG"]["qty"] == pytest.approx(0.017)
    assert {o["purpose"] for o in status["open_orders"]} == {STOP, TARGET}
    assert status["last_processed_candle"] == (T0 + timedelta(hours=6)).isoformat()
    assert status["oos_eligibility"]["eligible_store"] is False  # created without the drift guard
    assert status["oos_eligibility"]["earliest_read"] == "2027-10-16T00:00:00+00:00"
    assert "fingerprint_status" in status and status["kill_switch"]["engaged"] is False


def test_trades_are_written_once(tmp_path):
    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    assert store.trade({"trade_id": "t1"}) is True and store.trade({"trade_id": "t1"}) is False
    assert len(store.read("trades")) == 1 and events(store, "DUPLICATE_TRADE_SUPPRESSED")


# ----------------------------------------------------------------- 9. preflight


FROZEN_NOW = datetime(2026, 10, 15, 20, tzinfo=UTC)


def _full_data_repo(now, *, candle_skip=(), funding_skip=()):
    repo = BF.InMemoryRepository()
    start = C.OOS_START - timedelta(hours=C.WARMUP_BARS)
    t, i = start, 0
    while t < now:
        if i not in candle_skip:
            repo.candles[("BTCUSDT", t)] = {"time": t}
        t += timedelta(hours=1)
        i += 1
    t, j = start, 0
    while t < now:
        if j not in funding_skip:
            repo.funding[("BTCUSDT", t + timedelta(milliseconds=5))] = {"time": t}
        t += timedelta(hours=8)
        j += 1
    return repo


@pytest.fixture
def frozen(repo):
    freeze_oos(root=repo, now=FROZEN_NOW)
    return repo


def env_for(root, tmp_path, *, now=None, data=None, skew=0, free_gb=50.0, **kw):
    now = now or datetime(2026, 10, 15, 22, 30, tzinfo=UTC)
    return PF.PreflightEnv(root=root, lock_path=root / "docs" / C.LOCK_PATH.name, kill_switch_path=tmp_path / "KILL",
                           repo=data or _full_data_repo(now), now=lambda: now,
                           server_time_ms=lambda: (1_000_000, 1_000_000 + skew),
                           disk_free=lambda p: int(free_gb * 1024 ** 3), **kw)


def names(result, status="FAIL"):
    return {c["name"] for c in result["checks"] if c["status"] == status}


def test_preflight_passes_on_a_clean_frozen_launch(frozen, tmp_path):
    res = PF.run_preflight("virtual_paper_mainnet", ["BTCUSDT"], tmp_path / "store", env=env_for(frozen, tmp_path))
    assert res["result"] == PF.PREFLIGHT_PASS, res["failures"]
    assert len(res["checks"]) == 12 and not res["failures"]


@pytest.mark.parametrize("breakage,check", [
    ("dirty", "git_tree_clean"),
    ("drift", "fingerprint_match"),
    ("candle_gap", "candle_continuity"),
    ("funding_gap", "funding_continuity"),
    ("kill", "kill_switch_released"),
    ("skew", "clock_skew"),
    ("disk", "disk_and_permissions"),
    ("late", "oos_window"),
    ("symbol", "mode_and_symbols"),
])
def test_preflight_fails_each_check(frozen, tmp_path, breakage, check):
    now = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)
    kw: dict = {}
    symbols = ["BTCUSDT"]
    if breakage == "dirty":
        (frozen / C.CODE_FILES[0]).write_text("edited, not committed")  # a contract file
    elif breakage == "drift":
        p = frozen / C.CODE_FILES[0]
        p.write_text("changed")
        git(frozen, "add", "-A")
        git(frozen, "commit", "-q", "-m", "drift")
    elif breakage == "candle_gap":
        kw["data"] = _full_data_repo(now, candle_skip={500})
    elif breakage == "funding_gap":
        kw["data"] = _full_data_repo(now, funding_skip={10})
    elif breakage == "kill":
        engage_kill_switch(tmp_path / "KILL", by="t", reason="drill")
    elif breakage == "skew":
        kw["skew"] = 2_500
    elif breakage == "disk":
        kw["free_gb"] = 0.2
    elif breakage == "late":
        now = C.OOS_START + timedelta(hours=3)
    elif breakage == "symbol":
        symbols = ["BTCUSDT", "ETHUSDT"]
    res = PF.run_preflight("virtual_paper_mainnet", symbols, tmp_path / "store",
                           env=env_for(frozen, tmp_path, now=now, **kw))
    assert res["result"] == PF.PREFLIGHT_FAIL and check in names(res)
    assert all(f["action"] for f in res["failures"])


def test_preflight_fails_without_a_freeze(repo, tmp_path):
    res = PF.run_preflight("virtual_paper_mainnet", ["BTCUSDT"], tmp_path / "store", env=env_for(repo, tmp_path))
    assert {"freeze_lock", "fingerprint_match", "mode_and_symbols"} <= names(res)


def test_preflight_refuses_testnet_mode(frozen, tmp_path):
    res = PF.run_preflight("testnet_broker_check", ["BTCUSDT"], tmp_path / "s", env=env_for(frozen, tmp_path))
    assert res["result"] == PF.PREFLIGHT_FAIL and names(res) == {"mode_and_symbols"}


def _resume_store(root, *, created, history_start, first_index, state_extra=None, counts=True):
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "mode": "VIRTUAL_PAPER_MAINNET", "contract_id": C.CONTRACT_ID, "counts_toward_oos": counts,
        "created_at": created.isoformat(), "history_start": history_start.isoformat()}))
    (root / "state.json").write_text(json.dumps({"first_trade_index": first_index, "slots": {}, "inflight": {},
                                                 "orders": {}, "broker": {"position": 0.0, "orders": {}},
                                                 **(state_extra or {})}))


def test_preflight_resume_checks(frozen, tmp_path):
    hs = C.OOS_START - timedelta(hours=C.WARMUP_BARS)
    store = tmp_path / "store"
    _resume_store(store, created=FROZEN_NOW + timedelta(hours=1), history_start=hs, first_index=C.WARMUP_BARS)
    later = C.OOS_START + timedelta(hours=30)
    env = env_for(frozen, tmp_path, now=later)
    assert PF.run_preflight("virtual_paper_mainnet", ["BTCUSDT"], store, env=env)["result"] == PF.PREFLIGHT_FAIL
    ok = PF.run_preflight("virtual_paper_mainnet", ["BTCUSDT"], store, recover=True, env=env)
    assert ok["result"] == PF.PREFLIGHT_PASS, ok["failures"]
    # an open exception or an engaged kill switch is a warning on a restart, never a silent pass
    (store / "exceptions.jsonl").write_text(json.dumps({"id": "x", "category": "DATA_GAP", "status": "OPEN"}) + "\n")
    engage_kill_switch(tmp_path / "KILL", by="t", reason="drill")
    warned = PF.run_preflight("virtual_paper_mainnet", ["BTCUSDT"], store, recover=True, env=env)
    assert warned["result"] == PF.PREFLIGHT_PASS
    assert {w["name"] for w in warned["warnings"]} == {"no_open_exceptions", "kill_switch_released"}


@pytest.mark.parametrize("problem", ["created_after_start", "wrong_first_bar", "inflight", "position", "unknown_order"])
def test_preflight_resume_failures(frozen, tmp_path, problem):
    hs = C.OOS_START - timedelta(hours=C.WARMUP_BARS)
    created, first, extra = FROZEN_NOW + timedelta(hours=1), C.WARMUP_BARS, {}
    if problem == "created_after_start":
        created = C.OOS_START + timedelta(hours=2)
    elif problem == "wrong_first_bar":
        first = C.WARMUP_BARS + 5
    elif problem == "inflight":
        extra = {"inflight": {"x-E": {}}}
    elif problem == "position":
        extra = {"broker": {"position": 0.017, "orders": {}}}
    elif problem == "unknown_order":
        extra = {"broker": {"position": 0.0, "orders": {"zz": {"status": "NEW"}}}}
    store = tmp_path / "store"
    _resume_store(store, created=created, history_start=hs, first_index=first, state_extra=extra)
    res = PF.run_preflight("virtual_paper_mainnet", ["BTCUSDT"], store, recover=True,
                           env=env_for(frozen, tmp_path, now=C.OOS_START + timedelta(hours=30)))
    expected = "oos_window" if problem in ("created_after_start", "wrong_first_bar") else "broker_state_reconciled"
    assert res["result"] == PF.PREFLIGHT_FAIL and expected in names(res)



# ----------------------------------------------------------------- 10. store integrity and crash safety


def test_run_lock_refuses_a_second_runner_and_records_a_crashed_one(tmp_path):
    from app.research.candle12_v3_paper.store import StoreBusy

    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    t = datetime(2026, 10, 16, 1, tzinfo=UTC)
    store.acquire_run_lock(now=t)
    with pytest.raises(StoreBusy):
        PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET").acquire_run_lock(now=t + timedelta(minutes=5))
    later = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    later.acquire_run_lock(now=t + timedelta(minutes=20))  # never refreshed: the first runner died
    assert any(e["category"] == "RUNNER_CRASH" for e in later.open_exceptions())
    assert events(later, "RUNNER_CRASH_RECOVERED")
    later.release_run_lock()
    assert not (tmp_path / "runner.lock").exists()


def test_torn_or_corrupt_records_stop_the_store(tmp_path):
    from app.research.candle12_v3_paper.store import StoreCorrupt

    store = PaperStore(tmp_path, mode="VIRTUAL_PAPER_MAINNET")
    store.event("X")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "event": "TORN"')  # the process died mid-write
    with pytest.raises(StoreCorrupt):
        store.event("Y")
    with pytest.raises(StoreCorrupt):
        store.read("events")


def test_a_bar_cut_off_by_a_crash_halts_on_resume(fake, tmp_path):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    state = store.load_state()
    state["completed_bar_index"] = state["last_closed_index"] - 1  # bar 7 appended, not fully processed
    store.save_state(state)
    a2, _, _ = restart(candles[:8], cfg, store)
    assert a2.halted == "BAR_PROCESSING_INCOMPLETE"
    with pytest.raises(RecoveryHalted):
        run_recovery(a2)


def test_recovery_cancels_a_duplicate_exit_left_by_a_crash(fake, tmp_path):
    candles, cfg, store, a1, _ = build(fake, tmp_path, OPEN_ROWS, upto=7)
    a2, b2, store2 = restart(candles[:8], cfg, store)
    slot = a2.slots["LONG"]
    dup = Order(f"{slot.trade_id}-S9", STOP, "SELL", STOP_MARKET, 0.017, stop_price=slot.plan["stop"], reduce_only=True)
    a2._place(dup, slot, STOP, 7)  # placed but never recorded on the slot before the crash
    run_recovery(a2, now=T0 + timedelta(hours=9))
    assert events(store2, "RECOVERY_DUPLICATE_EXIT_CANCELED")
    stops = [o for o in b2.open_orders() if o.purpose == STOP]
    assert len(stops) == 1 and stops[0].qty == pytest.approx(0.017)
