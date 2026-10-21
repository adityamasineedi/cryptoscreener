"""D128d account kill switches: golden tests for each rule. Paper only; no database, no network.

Every test builds an in-memory PaperTradeEngine with stubbed market inputs, attaches a KillSwitch with a fixed
clock, and checks the exact decision (refusal reason, flatten rows, ledger queue, journal lines).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.kill_switch as ks_mod
import app.services.paper_risk as pr
import app.services.paper_trade as paper_mod
from app.services.kill_switch import (
    EXIT_REASON_FLATTEN,
    TRIP_DAILY_LOSS,
    TRIP_HEARTBEAT,
    TRIP_MANUAL,
    TRIP_MISMATCH,
    KillSwitch,
    KillSwitchConfig,
    config_from_settings,
)
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperTradeEngine

NOW = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
BAR = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _inputs(monkeypatch):
    monkeypatch.setattr(pr, "_mcap_and_group", lambda s: ("BTC", None, "WAITING") if s == "BTCUSDT" else ("ETH", None, "WAITING"))
    monkeypatch.setattr(pr, "_quote_volume_24h", lambda _s: (1e9, "LIVE"))
    monkeypatch.setattr(paper_mod, "_live_price_with_source", lambda _s: (None, "unavailable"))


def _engine(clock: list[datetime]) -> PaperTradeEngine:
    policy = PaperRiskPolicy(enabled=True, max_open_positions=50, max_open_risk_pct=0.90, liq_gate_enabled=False,
                             daily_loss_halt_r=99.0, consecutive_loss_halt=99, consecutive_loss_symbol_halt=99,
                             peak_drawdown_halt_pct=0.99, strategy_drawdown_halt_r=99.0)
    eng = PaperTradeEngine(starting_equity=1000.0, enabled=True, risk_policy=policy)
    eng._now = lambda: clock[0]  # noqa: SLF001
    return eng


def _switch(eng: PaperTradeEngine, clock: list[datetime], tmp_path: Path | None = None, **cfg: Any) -> KillSwitch:
    base = dict(enabled=True, daily_loss_limit_pct=0.05, max_open_risk_pct=0.10, max_position_notional_usd=0.0,
                max_position_notional_pct=2.0)
    base.update(cfg)
    ks = KillSwitch(KillSwitchConfig(**base), journal_path=(tmp_path / "ks.jsonl") if tmp_path else None,
                    now=lambda: clock[0])
    ks.attach(eng, price_fn=lambda sym: {"BTCUSDT": 60500.0, "ETHUSDT": 2950.0}.get(sym))
    eng.kill_switch = ks
    return ks


def _open(eng: PaperTradeEngine, sym: str = "BTCUSDT", *, bar: datetime = BAR, risk: float = 0.01,
          entry: float = 60000.0, stop: float = 59000.0, tp: float = 62000.0):
    return eng.open_passed_position(
        symbol=sym, combination_id="COMBO_03_TRANSITION_B", timeframe="1h", direction="LONG",
        eval_result={"status": "LONG_ENTRY_CANDIDATE", "direction": "LONG", "entry_price": entry,
                     "stop_price": stop, "tp1": tp},
        risk_percent=risk, setup_bar_time_utc=bar.isoformat())


def _journal(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "ks.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# --- off by default ------------------------------------------------------------------------------------------


def test_off_by_default_everywhere():
    from app.config import Settings

    assert Settings.model_fields["kill_switches_enabled"].default is False
    assert config_from_settings(SimpleNamespace()).enabled is False
    assert PaperTradeEngine().kill_switch is None
    assert ks_mod.get_kill_switch() is None
    eng = _engine([NOW])
    disabled = KillSwitch(KillSwitchConfig(enabled=False))
    assert disabled.check_entry(eng, "BTCUSDT", risk_usd=1e9, notional_usd=1e12) is None
    assert disabled.monitor_tick() == {"enabled": False}
    assert disabled.on_position_mismatch({"symbol": "BTCUSDT"}) == {"enabled": False, "action": None}


# --- daily loss limit ----------------------------------------------------------------------------------------


def test_daily_loss_limit_realized_blocks_and_resets_next_utc_day(tmp_path):
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, tmp_path, daily_loss_limit_pct=0.01)  # 1% of 1000 = 10 USD
    pos = _open(eng)
    assert pos is not None
    # Close at the stop: -1R = about -10 USD after fees -> at or past the 1% limit.
    eng._close_locked(pos, 59000.0, "STOP")  # noqa: SLF001
    assert eng.realized_pnl < -10.0
    blocked = _open(eng, "ETHUSDT", entry=3000.0, stop=2950.0, tp=3100.0)
    assert blocked is None
    assert eng._last_skip_reason.startswith("ETHUSDT:kill_switch:daily_loss_limit=")  # noqa: SLF001
    assert TRIP_DAILY_LOSS in ks.tripped()
    # Latched for the day, even if the loss is recovered on paper.
    assert ks.check_entry(eng, "ETHUSDT", risk_usd=1.0, notional_usd=10.0) == "kill_switch:daily_loss_limit:latched"
    # Next UTC day: cleared by itself, and the new day starts at 0 P&L.
    clock[0] = NOW.replace(hour=0) + timedelta(days=1, minutes=5)
    assert ks.check_entry(eng, "ETHUSDT", risk_usd=1.0, notional_usd=10.0) is None
    events = [(r["event"], r.get("trip")) for r in _journal(tmp_path)]
    assert ("trip", TRIP_DAILY_LOSS) in events and ("clear", TRIP_DAILY_LOSS) in events


def test_daily_loss_limit_counts_unrealized_loss():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, daily_loss_limit_pct=0.008)  # 8 USD
    assert _open(eng) is not None
    eng.tick({"BTCUSDT": 59100.0})  # marked down, not stopped: unrealized -9 USD on a 1000 account
    acct = ks.account_day_pnl(eng)
    assert acct["realized_today"] == 0.0 and acct["unrealized"] == pytest.approx(-9.0)
    assert _open(eng, "ETHUSDT", entry=3000.0, stop=2950.0, tp=3100.0) is None
    assert "kill_switch:daily_loss_limit=" in eng._last_skip_reason  # noqa: SLF001


def test_daily_loss_ignores_closes_from_earlier_days():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, daily_loss_limit_pct=0.01)
    pos = _open(eng)
    eng._close_locked(pos, 59000.0, "STOP")  # noqa: SLF001
    clock[0] = NOW + timedelta(days=1)
    assert ks.account_day_pnl(eng)["realized_today"] == 0.0
    assert ks.check_entry(eng, "ETHUSDT", risk_usd=1.0, notional_usd=10.0) is None


# --- max open risk, max position size ---------------------------------------------------------------------


def test_max_open_risk_counts_every_book_plus_the_new_trade():
    clock = [NOW]
    eng = _engine(clock)
    _switch(eng, clock, max_open_risk_pct=0.03)  # 30 USD of open risk at 1000 equity
    assert _open(eng, risk=0.02) is not None  # ~20 USD
    refused = _open(eng, "ETHUSDT", risk=0.02, entry=3000.0, stop=2950.0, tp=3100.0)
    assert refused is None
    assert eng._last_skip_reason.startswith("ETHUSDT:kill_switch:max_open_risk=")  # noqa: SLF001
    assert _open(eng, "ETHUSDT", risk=0.005, entry=3000.0, stop=2950.0, tp=3100.0) is not None


def test_max_position_notional_usd_and_pct():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, max_position_notional_usd=500.0)
    # 1% risk on a 1.67% stop is about 600 USD notional: over the 500 USD cap.
    assert _open(eng, risk=0.01) is None
    assert eng._last_skip_reason.startswith("BTCUSDT:kill_switch:max_position_notional=")  # noqa: SLF001
    assert ks.check_entry(eng, "X", risk_usd=1.0, notional_usd=499.0) is None
    pct = KillSwitch(KillSwitchConfig(enabled=True, max_position_notional_pct=0.5), now=lambda: clock[0])
    assert pct.check_entry(eng, "X", risk_usd=1.0, notional_usd=501.0).startswith("kill_switch:max_position_notional=")
    assert pct.check_entry(eng, "X", risk_usd=1.0, notional_usd=499.0) is None


# --- manual flatten ------------------------------------------------------------------------------------------


def test_manual_flatten_closes_everything_at_the_current_price_in_the_ledger(tmp_path):
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, tmp_path)
    a = _open(eng, risk=0.01)
    b = _open(eng, "ETHUSDT", risk=0.01, entry=3000.0, stop=2950.0, tp=3100.0)
    assert a is not None and b is not None
    eng.drain_persist_queue()
    result = ks.manual_flatten(by="owner", note="test")
    assert result["ok"] and result["closed_count"] == 2
    assert not eng._open  # noqa: SLF001
    rows = {r["symbol"]: r for r in result["closed"]}
    assert rows["BTCUSDT"]["exit_reason"] == EXIT_REASON_FLATTEN and rows["BTCUSDT"]["status"] == "CLOSED"
    assert rows["BTCUSDT"]["exit_price"] == 60500.0 and rows["ETHUSDT"]["exit_price"] == 2950.0
    assert rows["BTCUSDT"]["pnl_usd"] > 0 > rows["ETHUSDT"]["pnl_usd"]
    assert rows["BTCUSDT"]["signal_snippet"]["kill_switch"] == {"reason": TRIP_MANUAL, "by": "owner", "price_source": "live"}
    # The ledger write path: both closes are queued for Postgres (flush_paper_trade_persists).
    queued = eng.drain_persist_queue()
    assert sorted(r["symbol"] for r in queued) == ["BTCUSDT", "ETHUSDT"]
    assert all(r["exit_reason"] == EXIT_REASON_FLATTEN for r in queued)
    # Entries blocked until the owner clears it.
    later = BAR + timedelta(hours=3)  # after the flatten's exit bar (re-entry rule)
    assert _open(eng, bar=later) is None
    assert eng._last_skip_reason == "BTCUSDT:kill_switch:tripped:manual_flatten"  # noqa: SLF001
    assert ks.clear(TRIP_MANUAL, by="owner")
    assert _open(eng, bar=later) is not None
    events = [r["event"] for r in _journal(tmp_path)]
    assert events[:2] == ["trip", "flatten"] and "entry_blocked" in events and "clear" in events


def test_flatten_price_falls_back_to_last_mark_then_entry():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock)
    ks.attach(eng, price_fn=lambda _s: None)
    _open(eng)
    _open(eng, "ETHUSDT", entry=3000.0, stop=2950.0, tp=3100.0)
    eng.tick({"BTCUSDT": 60100.0})
    rows = {r["symbol"]: r for r in ks.flatten_all(reason="test", by="owner")["closed"]}
    assert (rows["BTCUSDT"]["exit_price"], rows["BTCUSDT"]["price_source"]) == (60100.0, "last_mark")
    assert (rows["ETHUSDT"]["exit_price"], rows["ETHUSDT"]["price_source"]) == (3000.0, "entry")


# --- heartbeat -----------------------------------------------------------------------------------------------


def test_stale_heartbeat_trips_and_auto_flattens_once(tmp_path):
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, tmp_path, heartbeat_timeout_minutes=90.0)
    last_bar = [NOW - timedelta(minutes=30)]
    ks.add_heartbeat_source("passed_book_last_eval", lambda: last_bar[0].isoformat())
    _open(eng)
    assert ks.monitor_tick()["action"] is None  # 30 min: fine
    clock[0] = NOW + timedelta(minutes=61)  # 91 min since the last closed-bar evaluation
    out = ks.monitor_tick()
    assert out["heartbeat"]["stale"] is True
    assert out["action"]["closed_count"] == 1 and out["action"]["closed"][0]["exit_reason"] == EXIT_REASON_FLATTEN
    assert TRIP_HEARTBEAT in ks.tripped()
    assert ks.monitor_tick()["action"] is None  # latched: no second flatten
    assert ks.check_entry(eng, "ETHUSDT", risk_usd=1.0, notional_usd=10.0) == "kill_switch:tripped:heartbeat"


def test_heartbeat_newest_source_wins_and_broken_source_is_ignored():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, heartbeat_timeout_minutes=90.0)
    ks.add_heartbeat_source("old", lambda: (NOW - timedelta(hours=5)).isoformat())
    ks.add_heartbeat_source("broken", lambda: 1 / 0)
    ks.add_heartbeat_source("fresh", lambda: NOW - timedelta(minutes=10))
    hb = ks.heartbeat()
    assert hb["stale"] is False and hb["age_seconds"] == 600.0 and hb["sources"]["broken"] is None


def test_no_heartbeat_source_means_no_heartbeat_trip():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock)
    clock[0] = NOW + timedelta(days=3)
    assert ks.monitor_tick()["action"] is None and ks.tripped() == {}


def test_heartbeat_auto_flatten_can_be_turned_off():
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, auto_flatten_on_heartbeat=False)
    ks.add_heartbeat_source("x", lambda: NOW - timedelta(hours=3))
    _open(eng)
    assert ks.monitor_tick()["action"] is None
    assert TRIP_HEARTBEAT in ks.tripped() and len(eng._open) == 1  # noqa: SLF001


# --- reconciliation mismatch hook ---------------------------------------------------------------------------


def test_position_mismatch_hook_trips_and_flattens_once(tmp_path):
    clock = [NOW]
    eng = _engine(clock)
    ks = _switch(eng, clock, tmp_path)
    _open(eng)
    out = ks.on_position_mismatch({"symbol": "BTCUSDT", "exchange_qty": 0.0, "ledger_qty": 0.01})
    assert out["first"] is True and out["action"]["closed_count"] == 1
    assert ks.tripped()[TRIP_MISMATCH]["detail"]["symbol"] == "BTCUSDT"
    again = ks.on_position_mismatch({"symbol": "BTCUSDT"})
    assert again["first"] is False and again["action"] is None
    assert ks.check_entry(eng, "ETHUSDT", risk_usd=1.0, notional_usd=10.0) == "kill_switch:tripped:mismatch"


# --- API and CLI ---------------------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    from app.api import routes

    clock = [NOW]
    eng = _engine(clock)
    monkeypatch.setattr(paper_mod, "get_paper_trade_engine", lambda *a, **k: eng)

    async def _flush(_eng: Any) -> int:
        return len(_eng.drain_persist_queue())

    monkeypatch.setattr(paper_mod, "flush_paper_trade_persists", _flush)
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(paper_admin_token=None))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def _loopback(scope, receive, send):
        await app({**scope, "client": ("127.0.0.1", 50000)}, receive, send)

    yield SimpleNamespace(engine=eng, clock=clock, client=TestClient(_loopback))
    ks_mod.set_kill_switch(None)


def test_api_refuses_when_flag_off(api):
    ks_mod.set_kill_switch(None)
    assert api.client.get("/api/paper/kill-switch").json()["enabled"] is False
    r = api.client.post("/api/paper/kill-switch/flatten-all", params={"confirm": "FLATTEN ALL"})
    assert r.status_code == 409


def test_api_flatten_needs_typed_confirmation_and_books_the_closes(api):
    ks = _switch(api.engine, api.clock)
    ks_mod.set_kill_switch(ks)
    _open(api.engine)
    api.engine.drain_persist_queue()  # the open row; only the flatten close is left to persist
    assert api.client.post("/api/paper/kill-switch/flatten-all", params={"confirm": "yes"}).status_code == 400
    assert len(api.engine._open) == 1  # noqa: SLF001
    r = api.client.post("/api/paper/kill-switch/flatten-all", params={"confirm": "FLATTEN ALL", "note": "drill"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["closed_count"] == 1 and body["persisted"] == 1
    assert api.client.get("/api/paper/kill-switch").json()["tripped"][TRIP_MANUAL]["detail"]["note"] == "drill"
    assert api.client.post("/api/paper/kill-switch/clear", params={"trip": TRIP_MANUAL}).status_code == 400
    r = api.client.post("/api/paper/kill-switch/clear", params={"trip": TRIP_MANUAL, "confirm": "CLEAR"})
    assert r.json()["cleared"] is True and r.json()["tripped"] == {}


def test_api_flatten_refused_without_writer_lock(api):
    ks_mod.set_kill_switch(_switch(api.engine, api.clock))
    api.engine.writer_lock_held = False
    r = api.client.post("/api/paper/kill-switch/flatten-all", params={"confirm": "FLATTEN ALL"})
    assert r.status_code == 409


def test_api_flatten_from_remote_peer_needs_admin(api, monkeypatch):
    from app.api import routes

    ks_mod.set_kill_switch(_switch(api.engine, api.clock))
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    async def _remote(scope, receive, send):
        await app({**scope, "client": ("203.0.113.5", 50000)}, receive, send)

    r = TestClient(_remote).post("/api/paper/kill-switch/flatten-all", params={"confirm": "FLATTEN ALL"})
    assert r.status_code == 403


def test_cli_without_confirmation_sends_nothing(monkeypatch, capsys):
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "flatten_all.py"
    spec = importlib.util.spec_from_file_location("flatten_all_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    sent: list[Any] = []
    monkeypatch.setattr(cli, "_call", lambda *a, **k: sent.append(a) or (200, {}))
    assert cli.main(["flatten", "--confirm", "yes"]) == 2
    assert cli.main(["clear", "manual_flatten", "--confirm", "nope"]) == 2
    assert sent == []
    assert cli.main(["flatten", "--confirm", "FLATTEN ALL"]) == 0
    assert sent and sent[0][2] == "/api/paper/kill-switch/flatten-all"
