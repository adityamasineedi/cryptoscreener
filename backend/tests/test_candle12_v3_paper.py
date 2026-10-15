"""CANDLE12_V3_EXEC_FIX paper adapter: order flow, failures, reconciliation, guards.

Hand-made Candle-1 setups (the detector is replaced) on BTCUSDT-like bars, so
every price is exact. Book: V3 defaults on BTCUSDT (step 0.001, min notional 50,
risk tolerance 10%), $20 risk, taker 0.04%, slippage 0.02%.

LONG Candle-1: H 60,000 L 59,100, ATR 1,000 -> trigger 60,000, stop order 60,012,
stop 58,900, R 1,112, qty 0.017, nearest target 62,236.
SHORT Candle-1: H 60,900 L 60,000 -> trigger 60,000, stop order 59,988,
stop 61,100, target 57,764.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper import binance as bn
from app.research.candle12_v3_paper.adapter import PaperAdapter
from app.research.candle12_v3_paper.broker import (
    ENTRY,
    MARGIN_REJECTED,
    MIN_NOTIONAL_REJECTED,
    ORDER_REJECTED,
    QTY_STEP_REJECTED,
    BrokerRejected,
    BrokerUnavailable,
    Order,
    SimFaults,
    SimulatedBroker,
)
from app.research.candle12_v3_paper.feeds import closed_only
from app.research.candle12_v3_paper.reconcile import TOLERANCES, reconcile
from app.research.candle12_v3_paper.store import PaperStore
from app.research.candle12_v3_paper.tracker import SignalTracker

UTC = timezone.utc
T0 = datetime(2025, 5, 1, tzinfo=UTC)
S = 0.0002
FEE = 0.0004
LONG_C1 = (59_400.0, 60_000.0, 59_100.0, 59_900.0)
SHORT_C1 = (60_600.0, 60_900.0, 60_000.0, 60_100.0)
QUIET = (60_000.0, 60_000.0, 60_000.0, 60_000.0)


def bar(i, o, h, l, c):
    return {"time": T0 + timedelta(hours=i), "open": float(o), "high": float(h), "low": float(l),
            "close": float(c), "volume": 1.0}


def scenario(side, c2, after=(), lead=5):
    c1 = LONG_C1 if side == "LONG" else SHORT_C1
    rows = [QUIET] * lead + [c1, c2, *after, QUIET, QUIET]
    return [bar(i, *r) for i, r in enumerate(rows)], lead


def setup_for(side, candle):
    return {
        "direction": side,
        "c1_ohlc": {k: candle[k] for k in ("open", "high", "low", "close")},
        "pullback": {},
        "impulse": {"impulse_origin": candle["low"] if side == "LONG" else candle["high"]},
        "atr": 1000.0,
        "swings": [],
        "structure_offset": 0,
    }


@pytest.fixture
def fake(monkeypatch):
    table = {}
    monkeypatch.setattr(v3, "detect_c1_v2", lambda **kw: table.get(kw["candle1_index"]))
    return table


def cfg_for(side="LONG", **kw):
    kw.setdefault("min_bars", 2)
    return v3.Candle12V3Config(book_side=side, **kw)


def run(candles, setups, fake, *, side="LONG", faults=None, cfg=None, funding=None, tmp_path=None, first=2):
    fake.update(setups)
    cfg = cfg or cfg_for(side)
    step, _, min_notional, _ = cfg.exchange_filter("BTCUSDT")
    broker = SimulatedBroker(slippage=S, fee_rate=FEE, step=step, min_notional=min_notional, faults=faults)
    store = PaperStore(tmp_path, mode="DRY_RUN")
    adapter = PaperAdapter(broker=broker, store=store, cfg=cfg, symbol="BTCUSDT", funding_rows=funding)
    adapter.replay(candles, first)
    return adapter, store, broker


def events(store, kind=None):
    rows = store.read("events")
    return [e for e in rows if kind is None or e["event"] == kind]


def recon(store, candles, cfg, first=2, funding=None):
    return reconcile(paper_trades=store.read("trades"), paper_signals=store.read("signals"), candles=candles,
                     first_trade_index=first, cfg=cfg, symbol="BTCUSDT", timeframe="1h", funding_rows=funding)


# 1 ---------------------------------------------------------------------------


def test_signal_is_detected_only_after_candle1_closes(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 59_990, 59_900, 59_950))
    _, store, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path)
    det = events(store, "SIGNAL_DETECTED")[0]
    req = events(store, "ORDER_REQUESTED")[0]
    assert det["bar_index"] == c1 and req["bar_index"] == c1 + 1 and det["seq"] < req["seq"]
    tracker = SignalTracker("BTCUSDT", "1h", cfg_for())
    with pytest.raises(ValueError):  # a series running past bar i holds an unclosed bar
        tracker.observe(candles[: c1 + 2], c1, first_trade_index=2)
    now = candles[c1]["time"] + timedelta(minutes=30)
    assert closed_only(candles, now, timedelta(hours=1))[-1]["time"] == candles[c1 - 1]["time"]


# 2 ---------------------------------------------------------------------------


def test_stop_entry_order_is_placed_and_cancelled_after_candle2(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_005, 59_900, 59_950))  # trades past 60,000 but not 60,012
    _, store, broker = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path)
    order = events(store, "ORDER_ACCEPTED")[0]["order"]
    assert order["type"] == "STOP_MARKET" and order["side"] == "BUY"
    assert order["stop_price"] == pytest.approx(60_000 * 1.0002)
    assert order["qty"] == pytest.approx(0.017)
    cancel = events(store, "ORDER_CANCELED")[0]
    assert cancel["reason"] == "CANDLE2_CLOSED" and cancel["bar_index"] == c1 + 1 and cancel["filled_qty"] == 0
    assert store.read("signals")[0]["outcome"] == "NO_FILL" and store.read("trades") == []
    assert broker.position == 0


# 3 ---------------------------------------------------------------------------


@pytest.mark.parametrize("side,c2,fill", [
    ("LONG", (60_300, 60_400, 60_250, 60_350), 60_300 * (1 + S)),
    ("SHORT", (59_700, 59_750, 59_600, 59_650), 59_700 * (1 - S)),
])
def test_gap_through_entry_fills_at_open_with_slippage(fake, tmp_path, side, c2, fill):
    candles, c1 = scenario(side, c2)
    _, store, _ = run(candles, {c1: setup_for(side, candles[c1])}, fake, side=side, tmp_path=tmp_path)
    assert events(store, "ORDER_ACCEPTED")[0]["order"]["type"] == "MARKET"
    f = events(store, "ORDER_FILLED")[0]["fill"]
    assert f["price"] == pytest.approx(fill) and f["gap"] is True


# 4 ---------------------------------------------------------------------------


@pytest.mark.parametrize("side,c2,nxt,exit_fill", [
    ("LONG", (59_950, 60_100, 59_900, 60_050), (58_500, 58_600, 58_400, 58_450), 58_500 * (1 - S)),
    ("SHORT", (60_050, 60_080, 59_900, 59_950), (61_500, 61_600, 61_400, 61_450), 61_500 * (1 + S)),
])
def test_gap_through_stop_exits_at_open_with_slippage(fake, tmp_path, side, c2, nxt, exit_fill):
    candles, c1 = scenario(side, c2, [nxt])
    _, store, _ = run(candles, {c1: setup_for(side, candles[c1])}, fake, side=side, tmp_path=tmp_path)
    t = store.read("trades")[0]
    assert t["exit_reason"] == "SL" and t["exit_fill"] == pytest.approx(exit_fill)
    assert "GAP_STOP_FILL" in t["tags"]
    assert recon(store, candles, cfg_for(side))["status"] == "RECONCILED"


# 5 ---------------------------------------------------------------------------


def test_partial_entry_fill(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050), [(60_050, 62_300, 59_950, 62_000)])
    _, store, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path,
                      faults=SimFaults(entry_fill_fraction=0.5))
    assert events(store, "ORDER_PARTIALLY_FILLED")
    cancel = [e for e in events(store, "ORDER_CANCELED") if e["reason"] == "CANDLE2_CLOSED"][0]
    assert cancel["filled_qty"] == pytest.approx(0.008)
    placed = events(store, "EXIT_ORDERS_PLACED")[-1]
    assert placed["stop"]["qty"] == pytest.approx(0.008) and placed["target"]["qty"] == pytest.approx(0.008)
    t = store.read("trades")[0]
    assert t["qty"] == pytest.approx(0.008) and t["planned_qty"] == pytest.approx(0.017)
    assert "PARTIAL_FILL" in t["tags"]
    assert any(e["category"] == "PARTIAL_FILL" for e in store.open_exceptions())
    rec = recon(store, candles, cfg_for())
    assert any(e["category"] == "QUANTITY_MISMATCH" for e in rec["exceptions"])


# 6 ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason,code", [(MARGIN_REJECTED, -2019), (ORDER_REJECTED, -1102)])
def test_order_rejection_takes_no_position(fake, tmp_path, reason, code):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050))
    _, store, broker = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path,
                           faults=SimFaults(reject=[(ENTRY, reason, code)]))
    rej = events(store, "ORDER_REJECTED")[0]
    assert rej["reason"] == reason and rej["code"] == code
    assert not events(store, "ORDER_FILLED") and broker.position == 0
    assert store.read("signals")[0]["outcome"] == "ORDER_REJECTED"
    assert [e["category"] for e in store.open_exceptions()] == [reason]


def test_exchange_downtime_retries_then_misses(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050))
    _, ok, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path / "a",
                   faults=SimFaults(unavailable_calls=1))
    assert events(ok, "BROKER_UNAVAILABLE") and events(ok, "ORDER_FILLED")
    _, down, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path / "b",
                     faults=SimFaults(unavailable_calls=3))
    assert events(down, "SIGNAL_MISSED")[0]["reason"] == "BROKER_UNAVAILABLE"
    assert [e["category"] for e in down.open_exceptions()] == ["BROKER_UNAVAILABLE"]


def test_stale_data_and_late_placement_and_price_beyond_trigger(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050))
    fake[c1] = setup_for("LONG", candles[c1])
    cfg = cfg_for()
    step, _, mn, _ = cfg.exchange_filter("BTCUSDT")

    def fresh(root):
        store = PaperStore(root, mode="DRY_RUN")
        a = PaperAdapter(broker=SimulatedBroker(step=step, min_notional=mn), store=store, cfg=cfg, symbol="BTCUSDT")
        a.start(candles[:2], 2)
        for k in range(2, c1 + 1):
            t = candles[k]["time"]
            a.on_bar_open(k, t, candles[k]["open"], now=t)
            a.on_bar_close(candles[k], now=t + timedelta(hours=1))
        return a, store

    a, store = fresh(tmp_path / "late")
    t2 = candles[c1 + 1]["time"]
    a.on_bar_open(c1 + 1, t2, candles[c1 + 1]["open"], now=t2 + timedelta(minutes=10))
    assert events(store, "SIGNAL_MISSED")[0]["reason"] == "LATE_PLACEMENT"

    a, store = fresh(tmp_path / "moved")
    a.on_bar_open(c1 + 1, t2, candles[c1 + 1]["open"], now=t2 + timedelta(seconds=20), current_price=60_100.0)
    assert events(store, "ORDER_ACCEPTED")[0]["order"]["type"] == "MARKET"
    assert any(e["category"] == "PRICE_BEYOND_TRIGGER_AT_PLACEMENT" for e in store.open_exceptions())

    a, store = fresh(tmp_path / "stale")
    late = candles[c1 + 1]
    a.on_bar_open(c1 + 1, t2, late["open"], now=t2)
    a.on_bar_close({**late, "time": late["time"] + timedelta(hours=1)}, now=t2 + timedelta(hours=5))
    assert events(store, "DATA_GAP") and events(store, "DATA_STALE")


# 7 ---------------------------------------------------------------------------


def test_min_notional_rejection(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050))
    _, store, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path,
                      cfg=cfg_for(min_notional=5_000.0))
    rej = events(store, "SIGNAL_REJECTED")[0]
    assert rej["outcome"] == "MIN_NOTIONAL_REJECTED" and not events(store, "ORDER_REQUESTED")
    assert store.read("signals")[0]["tags"] == ["MIN_NOTIONAL_REJECTED"]
    sim = SimulatedBroker(min_notional=50.0, step=0.001)
    with pytest.raises(BrokerRejected) as exc:
        sim.place_order(Order("x", ENTRY, "BUY", "MARKET", 0.001), bar_index=0, reference_price=40_000.0)
    assert exc.value.reason == MIN_NOTIONAL_REJECTED


# 8 ---------------------------------------------------------------------------


def test_quantity_step_rejection(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050))
    _, store, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path,
                      cfg=cfg_for(qty_step=0.01, risk_tolerance_pct=None))  # 0.017985 -> 0.01: 44% short
    rej = events(store, "SIGNAL_REJECTED")[0]
    assert rej["outcome"] == "SIZE_REJECTED" and "ROUNDING_SHORTFALL" in rej["reasons"]
    sim = SimulatedBroker(step=0.001)
    with pytest.raises(BrokerRejected) as exc:
        sim.place_order(Order("y", ENTRY, "BUY", "STOP_MARKET", 0.0175, stop_price=60_012.0), bar_index=0)
    assert exc.value.reason == QTY_STEP_REJECTED and exc.value.code == -1111


# 9 ---------------------------------------------------------------------------


def test_risk_tolerance_rejection(fake, tmp_path):
    rows = [QUIET] * 5 + [(59_950, 60_000, 59_900, 59_990), (59_990, 60_100, 59_950, 60_050), QUIET, QUIET]
    candles = [bar(i, *r) for i, r in enumerate(rows)]
    setup = {**setup_for("LONG", candles[5]), "atr": 50.0}  # 0.17% stop -> margin cap -> actual risk ~$2
    _, store, _ = run(candles, {5: setup}, fake, tmp_path=tmp_path)
    rej = events(store, "SIGNAL_REJECTED")[0]
    assert rej["outcome"] == "RISK_TOLERANCE_REJECTED"
    assert rej["intended_risk_usd"] == 20.0 and rej["actual_risk_usd"] < 18.0
    assert rej["risk_diff_usd"] == pytest.approx(rej["actual_risk_usd"] - 20.0)
    assert not events(store, "ORDER_REQUESTED")


# 10 --------------------------------------------------------------------------


def test_stop_and_target_orders_after_fill(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050), [(60_050, 60_200, 59_950, 60_100)] * 2)
    _, store, broker = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path)
    placed = events(store, "EXIT_ORDERS_PLACED")[0]
    stop, target = placed["stop"], placed["target"]
    assert placed["bar_index"] == c1 + 1
    assert stop["type"] == "STOP_MARKET" and stop["side"] == "SELL" and stop["reduce_only"] is True
    assert stop["stop_price"] == pytest.approx(58_900.0) and stop["qty"] == pytest.approx(0.017)
    assert target["type"] == "TAKE_PROFIT_MARKET" and target["reduce_only"] is True
    assert target["stop_price"] == pytest.approx(60_012 + 2 * 1_112)
    params = bn.BinanceTestnetBroker.order_params(Order(**{k: stop[k] for k in (
        "client_id", "purpose", "side", "type", "qty", "stop_price", "reduce_only")}), "BTCUSDT")
    # Conditional orders use the Algo Order API since 2025-12-09 (triggerPrice, not stopPrice).
    assert bn.BinanceTestnetBroker.endpoint_for(Order(**{k: stop[k] for k in (
        "client_id", "purpose", "side", "type", "qty", "stop_price", "reduce_only")})) == "/fapi/v1/algoOrder"
    assert params["reduceOnly"] == "true" and params["triggerPrice"] == "58900"
    assert params["algoType"] == "CONDITIONAL" and "stopPrice" not in params
    assert broker.position == pytest.approx(0.017)  # still open at the end of the data


# 11 --------------------------------------------------------------------------


def test_same_bar_stop_and_target_takes_the_stop(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050), [(60_050, 62_300, 58_800, 60_000)])
    _, store, broker = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path)
    t = store.read("trades")[0]
    assert t["exit_reason"] == "SL" and "CONSERVATIVE_SAME_BAR_SL" in t["tags"]
    assert t["exit_fill"] == pytest.approx(58_900 * (1 - S))
    assert any(e["reason"] == "POSITION_CLOSED" for e in events(store, "ORDER_CANCELED"))
    assert broker.position == pytest.approx(0.0)


# 12 --------------------------------------------------------------------------


def test_reconciliation_passes_on_the_real_detector(tmp_path):
    from test_candle12_v3 import _synthetic

    candles = _synthetic()
    rows = [{"time": candles[0]["time"] + timedelta(hours=8 * j), "rate": 0.0001, "mark_price": None}
            for j in range(len(candles) // 8)]
    for side in ("LONG", "SHORT"):
        cfg = v3.Candle12V3Config(book_side=side, apply_exchange_filters=False, funding_enabled=True)
        store = PaperStore(tmp_path / side, mode="DRY_RUN")
        PaperAdapter(broker=SimulatedBroker(), store=store, cfg=cfg, symbol="SYN", funding_rows=rows).replay(candles, 300)
        rec = reconcile(paper_trades=store.read("trades"), paper_signals=store.read("signals"), candles=candles,
                        first_trade_index=300, cfg=cfg, symbol="SYN", timeframe="1h", funding_rows=rows)
        assert rec["paper_trades"] == rec["engine_trades"] >= 5
        assert rec["status"] == "RECONCILED", rec["exceptions"][:3]
        fields = {r["field"] for r in rec["reports"][0]["rows"]}
        assert {"signal_timestamp", "candle1_ohlc", "candle2_ohlc", "trigger_price", "entry_fill", "stop_price",
                "target_price", "quantity", "notional_usd", "intended_risk_usd", "actual_risk_usd",
                "fees_usd_computed_vs_exchange", "funding_usd_computed_vs_exchange", "gross_pnl_usd", "net_pnl_usd",
                "r_net_actual_risk", "r_net_intended_risk", "exit_reason", "exit_timestamp", "exit_fill",
                "rejection_tags"} <= fields


# 13 --------------------------------------------------------------------------


def test_reconciliation_flags_fill_outside_tolerance(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_200, 59_900, 60_050), [(60_050, 62_300, 59_950, 62_000)])
    _, store, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path,
                      faults=SimFaults(fill_offset={ENTRY: 0.001}))  # 0.10% worse than the engine fill
    rec = recon(store, candles, cfg_for())
    cats = {e["category"] for e in rec["exceptions"]}
    assert "ENTRY_FILL_OUTSIDE_TOLERANCE" in cats and rec["status"] == "EXCEPTIONS"
    row = next(r for r in rec["reports"][0]["rows"] if r["field"] == "entry_fill")
    assert row["within_tolerance"] is False and abs(row["difference"] / row["engine"]) > TOLERANCES["entry_fill_rel"]
    _, ok_store, _ = run(candles, {}, fake, tmp_path=tmp_path / "ok", faults=SimFaults(fill_offset={ENTRY: 0.0003}))
    assert recon(ok_store, candles, cfg_for())["status"] == "RECONCILED"


def test_funding_mismatch_is_an_exception(fake, tmp_path):
    candles, c1 = scenario("LONG", (59_950, 60_100, 59_900, 60_050),
                           [(60_050, 60_200, 59_950, 60_100)] * 10 + [(60_100, 62_300, 60_000, 62_000)])
    funding = [{"time": candles[0]["time"] + timedelta(hours=8 * j), "rate": 0.0001, "mark_price": 60_000.0}
               for j in range(4)]
    cfg = cfg_for(funding_enabled=True)
    _, store, _ = run(candles, {c1: setup_for("LONG", candles[c1])}, fake, tmp_path=tmp_path, cfg=cfg,
                      funding=funding, faults=SimFaults(funding_offset=0.01))
    rec = recon(store, candles, cfg, funding=funding)
    assert {e["category"] for e in rec["exceptions"]} == {"FUNDING_MISMATCH"}


# guards ------------------------------------------------------------------------


def test_testnet_broker_guards(monkeypatch):
    monkeypatch.delenv(bn.PAPER_ENV, raising=False)
    with pytest.raises(bn.PaperGuardError):
        bn.BinanceTestnetBroker(symbol="BTCUSDT", api_key="k", api_secret="s")
    monkeypatch.setenv(bn.PAPER_ENV, "1")
    with pytest.raises(bn.PaperGuardError):
        bn.BinanceTestnetBroker(symbol="BTCUSDT", api_key="k", api_secret="s", base_url="https://fapi.binance.com")
    with pytest.raises(bn.PaperGuardError):
        bn.BinanceTestnetBroker(symbol="BTCUSDT", api_key="", api_secret="")


def test_cli_run_refuses_paper_without_env(monkeypatch, capsys):
    from app.research.candle12_v3_paper import cli

    monkeypatch.delenv(bn.PAPER_ENV, raising=False)
    assert cli.main(["run", "--paper"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def _testnet(monkeypatch, handler):
    monkeypatch.setenv(bn.PAPER_ENV, "1")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return bn.BinanceTestnetBroker(symbol="BTCUSDT", api_key="key", api_secret="secret", client=client)


def test_testnet_order_request_is_signed_and_mapped(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-MBX-APIKEY")
        return httpx.Response(200, json={"algoId": 7, "algoStatus": "NEW"})

    broker = _testnet(monkeypatch, handler)
    order = broker.place_order(Order("c-1", "STOP", "SELL", "STOP_MARKET", 0.017, stop_price=58_900.04,
                                     reduce_only=True), bar_index=3)
    assert order.exchange_id == "7" and seen["key"] == "key"
    q = httpx.URL(seen["url"]).params
    # Conditional orders use the Algo Order API since 2025-12-09 (triggerPrice, not stopPrice).
    assert httpx.URL(seen["url"]).path == "/fapi/v1/algoOrder" and q["algoType"] == "CONDITIONAL"
    assert q["type"] == "STOP_MARKET" and q["reduceOnly"] == "true" and q["triggerPrice"] == "58900"
    assert "stopPrice" not in q
    assert q["quantity"] == "0.017" and "signature" in q and q["symbol"] == "BTCUSDT"


@pytest.mark.parametrize("status,body,expected", [
    (400, {"code": -2019, "msg": "Margin is insufficient."}, MARGIN_REJECTED),
    (400, {"code": -4164, "msg": "Order's notional must be no smaller than 50"}, MIN_NOTIONAL_REJECTED),
    (400, {"code": -1111, "msg": "Precision is over the maximum defined for this asset."}, QTY_STEP_REJECTED),
    (400, {"code": -2021, "msg": "Order would immediately trigger."}, "PRICE_BEYOND_TRIGGER"),
])
def test_testnet_error_codes(monkeypatch, status, body, expected):
    broker = _testnet(monkeypatch, lambda r: httpx.Response(status, json=body))
    with pytest.raises(BrokerRejected) as exc:
        broker.place_order(Order("c-2", ENTRY, "BUY", "STOP_MARKET", 0.017, stop_price=60_012.0), bar_index=1)
    assert exc.value.reason == expected and exc.value.code == body["code"]


def test_testnet_downtime_is_unavailable(monkeypatch):
    broker = _testnet(monkeypatch, lambda r: httpx.Response(503, text="down"))
    with pytest.raises(BrokerUnavailable):
        broker.place_order(Order("c-3", ENTRY, "BUY", "MARKET", 0.017), bar_index=1)


def test_store_keeps_exceptions_open_until_explained(tmp_path):
    store = PaperStore(tmp_path, mode="PAPER")
    store.exception("x", "PARTIAL_FILL")
    assert [e["id"] for e in store.open_exceptions()] == ["x"]
    with pytest.raises(ValueError):
        store.resolve_exception("x", " ", by="me")
    store.resolve_exception("x", "exchange partially filled; quantity difference understood", by="me")
    assert store.open_exceptions() == []
    with pytest.raises(ValueError):
        PaperStore(tmp_path, mode="SMOKE")
    assert json.loads((tmp_path / "manifest.json").read_text())["mode"] == "PAPER"
