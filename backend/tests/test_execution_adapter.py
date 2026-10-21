"""D128c execution adapter against the in-repo mock exchange. No network, no keys (C1 v2.5)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import app.services.paper_risk as pr
import app.services.paper_trade as paper_mod
from app.execution.adapter import ExecutionAdapter, ReconciliationLoop, ledger_positions_from_engine
from app.execution.ids import CLIENT_ID_RE, client_order_id
from app.execution.interface import DuplicateClientOrderId, ReduceOnlyRejected, TransientExchangeError
from app.execution.mock_exchange import MockBinanceFutures
from app.execution.models import (
    BUY,
    CANCELED,
    EXPIRED,
    FILLED,
    LIMIT,
    MARKET,
    NEW,
    PARTIALLY_FILLED,
    SELL,
    STOP_MARKET,
    TAKE_PROFIT_MARKET,
    OrderRequest,
)
from app.services.kill_switch import EXIT_REASON_FLATTEN, TRIP_MISMATCH, KillSwitch, KillSwitchConfig
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine

SYM = "BTCUSDT"


def _ex(mark: float = 100.0) -> MockBinanceFutures:
    return MockBinanceFutures({SYM: mark})


def _long(ad: ExecutionAdapter, **kw):
    args = dict(strategy="BOOK16", signal_key="BTCUSDT|4h|2026-10-21T08:00Z", symbol=SYM, side="LONG",
                quantity=1.0, stop_price=95.0, tp_price=110.0)
    args.update(kw)
    return ad.open_bracket(**args)


# --- ids ---------------------------------------------------------------------------------------------------


def test_client_order_ids_are_deterministic_per_leg_and_version():
    a = client_order_id("BOOK16", "BTCUSDT|4h|t", "EN")
    assert a == client_order_id("BOOK16", "BTCUSDT|4h|t", "EN")
    ids = {client_order_id("BOOK16", "BTCUSDT|4h|t", leg, v) for leg in ("EN", "SL", "TP", "XT", "FL") for v in (1, 2)}
    assert len(ids) == 10
    assert client_order_id("BOOK16", "BTCUSDT|4h|u", "EN") != a
    assert all(len(i) <= 36 and CLIENT_ID_RE.match(i) for i in ids)
    with pytest.raises(ValueError):
        client_order_id("BOOK16", "x", "ZZ")


# --- bracket and protection --------------------------------------------------------------------------------


def test_market_entry_places_reduce_only_stop_and_take_profit_for_the_filled_qty():
    ex = _ex()
    b = _long(ExecutionAdapter(ex))
    assert b.entry.status == FILLED and b.entry.executed_qty == 1.0
    assert ex.positions()[SYM].qty == 1.0
    opens = {o.type: o for o in ex.open_orders(SYM)}
    assert opens[STOP_MARKET].reduce_only and opens[STOP_MARKET].side == SELL and opens[STOP_MARKET].quantity == 1.0
    assert opens[STOP_MARKET].stop_price == 95.0
    assert opens[TAKE_PROFIT_MARKET].reduce_only and opens[TAKE_PROFIT_MARKET].stop_price == 110.0


def test_stop_triggers_on_the_exchange_and_the_take_profit_is_cancelled():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    b = _long(ad)
    fills = ex.set_mark(SYM, 94.0)
    assert [f.client_order_id for f in fills] == [b.stop.client_order_id] and fills[0].price == 94.0
    assert SYM not in ex.positions()
    ad.poll()
    assert ex.get_order(SYM, b.take_profit.client_order_id).status == CANCELED
    assert ex.open_orders() == []
    assert ad.reconcile({}).ok


def test_take_profit_triggers_and_stop_is_cancelled():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    b = _long(ad)
    ex.set_mark(SYM, 111.0)
    ad.poll()
    assert ex.get_order(SYM, b.take_profit.client_order_id).status == FILLED
    assert ex.get_order(SYM, b.stop.client_order_id).status == CANCELED
    assert ex.positions() == {}
    assert ex.fills[-1].price == 111.0


def test_short_bracket_mirrors_the_long():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    b = _long(ad, side="SHORT", stop_price=105.0, tp_price=90.0)
    assert ex.positions()[SYM].qty == -1.0
    assert b.stop.side == BUY and b.stop.reduce_only
    ex.set_mark(SYM, 106.0)
    ad.poll()
    assert ex.positions() == {} and ex.get_order(SYM, b.take_profit.client_order_id).status == CANCELED


# --- idempotent retries ------------------------------------------------------------------------------------


def test_lost_acknowledgement_retry_returns_the_original_and_creates_no_duplicate():
    ex = _ex()
    ex.fail_next_place(1, applied=True)  # the exchange accepts the entry, then the response is lost
    b = _long(ExecutionAdapter(ex))
    assert b.entry.status == FILLED
    assert ex.positions()[SYM].qty == 1.0  # one entry, not two
    entries = [f for f in ex.fills if f.client_order_id == b.entry.client_order_id]
    assert len(entries) == 1
    assert ex.accepted == 3  # entry + stop + take-profit


def test_timeout_before_the_exchange_saw_it_is_retried_once():
    ex = _ex()
    ex.fail_next_place(2, applied=False)
    b = _long(ExecutionAdapter(ex, max_retries=3))
    assert b.entry.status == FILLED and ex.positions()[SYM].qty == 1.0 and ex.accepted == 3


def test_retries_give_up_without_placing_anything():
    ex = _ex()
    ex.fail_next_place(10, applied=False)
    with pytest.raises(TransientExchangeError):
        _long(ExecutionAdapter(ex, max_retries=2))
    assert ex.accepted == 0 and ex.positions() == {}


def test_restart_re_running_the_same_signal_creates_nothing_new():
    ex = _ex()
    b1 = _long(ExecutionAdapter(ex))
    before = (ex.accepted, len(ex.fills), ex.positions()[SYM].qty)
    b2 = _long(ExecutionAdapter(ex))  # a new process, same strategy + signal
    assert (ex.accepted, len(ex.fills), ex.positions()[SYM].qty) == before
    assert b2.entry.order_id == b1.entry.order_id and b2.stop.order_id == b1.stop.order_id


def test_mock_refuses_a_reused_client_order_id():
    ex = _ex()
    req = OrderRequest(symbol=SYM, side=BUY, type=MARKET, quantity=1.0, client_order_id="cs-EN-abc")
    ex.place_order(req)
    with pytest.raises(DuplicateClientOrderId):
        ex.place_order(req)


# --- partial fills -----------------------------------------------------------------------------------------


def test_partial_fills_resize_the_protection_by_cancel_and_replace():
    ex = _ex(101.0)
    ad = ExecutionAdapter(ex)
    b = _long(ad, entry_type=LIMIT, limit_price=100.0)
    assert b.entry.status == NEW and b.stop is None and ex.open_orders(SYM)[0].type == LIMIT
    ex.fill_partial(SYM, b.entry.client_order_id, 0.4)
    ad.sync_protection(b)
    assert b.entry.status == PARTIALLY_FILLED
    assert (b.stop.quantity, b.stop_version, b.take_profit.quantity) == (0.4, 1, 0.4)
    first_stop = b.stop.client_order_id
    ex.fill_partial(SYM, b.entry.client_order_id, 0.3)
    ad.sync_protection(b)
    assert ex.get_order(SYM, first_stop).status == CANCELED
    assert (b.stop.quantity, b.stop_version) == (pytest.approx(0.7), 2)
    assert b.stop.client_order_id == client_order_id("BOOK16", b.signal_key, "SL", 2)
    ad.cancel_entry(b)
    assert ex.get_order(SYM, b.entry.client_order_id).status == CANCELED
    assert ex.positions()[SYM].qty == pytest.approx(0.7) and b.stop.quantity == pytest.approx(0.7)
    assert ad.reconcile({SYM: 0.7}).ok


def test_triggered_stop_after_a_manual_close_expires_instead_of_opening_a_short():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    b = _long(ad)
    ex.force_position(SYM, 0.0)  # closed by hand in the exchange UI
    ex.set_mark(SYM, 94.0)
    assert ex.get_order(SYM, b.stop.client_order_id).status == EXPIRED
    assert ex.positions() == {}
    with pytest.raises(ReduceOnlyRejected):
        ex.place_order(OrderRequest(symbol=SYM, side=SELL, type=MARKET, quantity=1.0, reduce_only=True,
                                    client_order_id="cs-XT-x"))
    assert ad.close_bracket(b) is None


# --- amend -------------------------------------------------------------------------------------------------


def test_amend_stop_is_cancel_and_replace_and_the_new_stop_triggers():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    b = _long(ad)
    old = b.stop.client_order_id
    ad.amend_stop(b, 98.0)
    assert ex.get_order(SYM, old).status == CANCELED
    assert (b.stop.stop_price, b.stop_version, b.stop.status) == (98.0, 2, NEW)
    assert [o.stop_price for o in ex.open_orders(SYM) if o.type == STOP_MARKET] == [98.0]
    ex.set_mark(SYM, 97.5)
    ad.poll()
    assert ex.get_order(SYM, b.stop.client_order_id).status == FILLED and ex.positions() == {}


def test_amend_stop_after_it_already_filled_replaces_nothing():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    b = _long(ad)
    ex.set_mark(SYM, 94.0)
    n = ex.accepted
    ad.amend_stop(b, 98.0)
    assert ex.accepted == n and b.stop.status == FILLED and ex.positions() == {}


def test_amend_limit_entry_in_place():
    ex = _ex(101.0)
    ad = ExecutionAdapter(ex)
    b = _long(ad, entry_type=LIMIT, limit_price=99.0)
    oid = b.entry.order_id
    ad.amend_entry(b, quantity=2.0, price=100.5)
    assert (b.entry.order_id, b.entry.quantity, b.entry.price) == (oid, 2.0, 100.5)
    ex.set_mark(SYM, 100.4)
    ad.poll()
    assert b.entry.status == FILLED and b.stop.quantity == 2.0 and ex.positions()[SYM].qty == 2.0


# --- reconciliation and the kill-switch hook ---------------------------------------------------------------


def test_reconcile_ok_and_qty_mismatch_fires_the_hook():
    ex = _ex()
    seen: list[dict] = []
    ad = ExecutionAdapter(ex, mismatch_hook=seen.append)
    _long(ad)
    assert ad.reconcile({SYM: 1.0}).ok and seen == []
    res = ad.reconcile({SYM: 0.5})
    assert not res.ok and res.mismatches[0].kind == "qty_mismatch"
    assert seen and seen[0]["mismatches"][0]["exchange_qty"] == 1.0 and seen[0]["mismatches"][0]["ledger_qty"] == 0.5


def test_position_without_a_covering_stop_is_a_mismatch():
    ex = _ex()
    seen: list[dict] = []
    ad = ExecutionAdapter(ex, mismatch_hook=seen.append)
    ex.force_position(SYM, 1.0, 100.0)  # e.g. opened outside the adapter
    res = ad.reconcile({SYM: 1.0})
    assert [m.kind for m in res.mismatches] == ["missing_stop"] and seen


def test_mismatch_trips_the_d128d_kill_switch_and_flattens_paper(monkeypatch):
    monkeypatch.setattr(pr, "_mcap_and_group", lambda _s: ("BTC", None, "WAITING"))
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (1e9, "LIVE"))
    monkeypatch.setattr(paper_mod, "_live_price_with_source", lambda _s: (None, "unavailable"))
    now = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    eng = PaperTradeEngine(starting_equity=1000.0, risk_policy=PaperRiskPolicy(
        enabled=True, max_open_positions=50, max_open_risk_pct=0.9, liq_gate_enabled=False))
    eng._now = lambda: now  # noqa: SLF001
    ks = KillSwitch(KillSwitchConfig(enabled=True), now=lambda: now)
    ks.attach(eng, price_fn=lambda _s: 60100.0)
    eng.kill_switch = ks
    pos = eng.open_passed_position(symbol=SYM, combination_id="COMBO_03_TRANSITION_B", timeframe="1h",
                                   direction="LONG", eval_result={"status": "LONG_ENTRY_CANDIDATE",
                                   "entry_price": 60000.0, "stop_price": 59000.0, "tp1": 62000.0},
                                   risk_percent=0.01, setup_bar_time_utc="2026-03-02T10:00:00+00:00")
    assert pos is not None
    ex = MockBinanceFutures({SYM: 60000.0})  # the exchange holds nothing: the ledger says 0.01 BTC
    ad = ExecutionAdapter(ex, mismatch_hook=ks.on_position_mismatch)
    loop = ReconciliationLoop(ad, lambda: ledger_positions_from_engine(eng))
    res = loop.run_once()
    assert not res.ok and res.mismatches[0].ledger_qty == pytest.approx(pos.quantity)
    assert res.hook_result["first"] is True and TRIP_MISMATCH in ks.tripped()
    assert res.hook_result["action"]["closed"][0]["exit_reason"] == EXIT_REASON_FLATTEN
    assert eng._open == {}  # noqa: SLF001
    assert loop.run_once().ok  # ledger now flat too


async def test_reconciliation_loop_runs_on_its_interval():
    ex = _ex()
    ad = ExecutionAdapter(ex)
    loop = ReconciliationLoop(ad, lambda: {}, interval_s=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(loop.run(stop))
    await asyncio.sleep(0.08)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert loop.runs >= 2 and loop.last.ok


def test_default_interval_is_sixty_seconds():
    assert ReconciliationLoop(ExecutionAdapter(_ex()), dict).interval_s == 60.0


def test_live_code_does_not_import_the_execution_package():
    """The adapter is a library only: no live module imports it (wiring it in is a separate owner decision)."""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    users = [p for p in app_dir.rglob("*.py") if "execution" not in p.parts and "app.execution" in p.read_text(encoding="utf-8")]
    assert users == []
