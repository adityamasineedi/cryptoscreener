"""Owner D188 part 2: My Trades journal maths, stats, bootstrap CI, append-only manual_trades, API and CSV.

Storage tests run on the stdlib sqlite3 backend (no live database, P13); the PostgreSQL DDL is checked as text.
"""

from __future__ import annotations

import asyncio
import csv
import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.screener_tool import costs, journal_stats as js
from app.screener_tool import journal_store as store
from app.screener_tool.api import router

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def journal() -> store.ManualTradeJournal:
    return store.ManualTradeJournal(store.SqliteBackend(), clock=Clock())


# --- maths (golden) --------------------------------------------------------------------------------
def test_position_levels_from_risk_or_quantity():
    by_risk = costs.position_levels("LONG", 100.0, 98.0, risk_usd=20.0, targets=[104.0, 106.0], symbol="BTCUSDT")
    assert by_risk["quantity"] == pytest.approx(10.0) and by_risk["risk_usd"] == pytest.approx(20.0)
    assert by_risk["targets_r"] == [pytest.approx(2.0), pytest.approx(3.0)]
    assert by_risk["stop_distance_pct"] == pytest.approx(2.0) and by_risk["cost_r"] == pytest.approx(0.07)
    by_qty = costs.position_levels("SHORT", 2.0, 2.1, quantity=100.0, symbol="OPUSDT")
    assert by_qty["risk_usd"] == pytest.approx(10.0)
    with pytest.raises(ValueError):
        costs.position_levels("LONG", 100.0, 101.0, risk_usd=10.0)  # stop above a long entry
    with pytest.raises(ValueError):
        costs.position_levels("LONG", 100.0, 98.0, risk_usd=10.0, quantity=1.0)
    with pytest.raises(ValueError):
        costs.position_levels("LONG", 100.0, 98.0, risk_usd=10.0, targets=[99.0])


def test_close_math_d144_golden_long_btc():
    # qty 10, entry 100, stop 98, exit 104: gross 40; fees 10 x 204 x 0.05% = 1.02; slippage 10 x 204 x 0.02% = 0.408
    m = costs.close_math("LONG", 100.0, 98.0, 10.0, 104.0, "BTCUSDT")
    assert m["gross_pnl_usd"] == pytest.approx(40.0)
    assert m["fees_usd"] == pytest.approx(1.02) and m["slippage_usd"] == pytest.approx(0.408)
    assert m["net_pnl_usd"] == pytest.approx(38.572) and m["risk_usd"] == pytest.approx(20.0)
    assert m["gross_r"] == pytest.approx(2.0) and m["r_multiple"] == pytest.approx(1.9286)
    assert "0.05%" in m["formula"] and "0.02%" in m["formula"]


def test_close_math_d144_golden_short_thin_coin_and_stop_loss():
    # OP is a thin coin: 5 bp slippage per side. qty 100, entry 2.0, stop 2.1, exit 1.8.
    m = costs.close_math("SHORT", 2.0, 2.1, 100.0, 1.8, "OPUSDT")
    assert m["gross_pnl_usd"] == pytest.approx(20.0)
    assert m["fees_usd"] == pytest.approx(0.19) and m["slippage_usd"] == pytest.approx(0.19)
    assert m["net_pnl_usd"] == pytest.approx(19.62) and m["r_multiple"] == pytest.approx(1.962)
    stopped = costs.close_math("LONG", 100.0, 98.0, 10.0, 98.0, "BTCUSDT")  # a full stop costs more than 1 R
    assert stopped["r_multiple"] == pytest.approx((-20.0 - 10 * 198 * 0.0007) / 20.0)
    assert stopped["r_multiple"] < -1.0


# --- stats (golden) ---------------------------------------------------------------------------------
def _trades(rs, *, usd_per_r=10.0, tags=None, coins=None):
    out = []
    for i, r in enumerate(rs):
        out.append({
            "trade_id": f"t{i:02d}", "r_multiple": r, "net_pnl_usd": r * usd_per_r,
            "exit_at": T0 + timedelta(days=i * 4), "opened_at": T0 + timedelta(days=i * 4, hours=-2),
            "setup_tag": (tags or ["A"])[i % len(tags or ["A"])], "symbol": (coins or ["ETHUSDT"])[i % len(coins or ["X"])],
        })
    return out


def test_summary_golden():
    rs = [2.0, -1.0, -1.0, 1.5, -1.0, -1.0, -1.0, 3.0]
    s = js.summarize(_trades(rs))
    assert s["trades"] == 8 and s["wins"] == 3 and s["losses"] == 5
    assert s["win_rate"] == pytest.approx(0.375)
    assert s["avg_r"] == pytest.approx(0.1875) and s["expectancy_r"] == pytest.approx(0.1875)
    assert s["expectancy_usd"] == pytest.approx(1.875)
    assert s["profit_factor"] == pytest.approx(6.5 / 5.0) and s["profit_factor_r"] == pytest.approx(1.3)
    # cumulative R: 2, 1, 0, 1.5, 0.5, -0.5, -1.5, 1.5 -> peak 2, trough -1.5
    assert s["max_drawdown_r"] == pytest.approx(3.5) and s["max_drawdown_usd"] == pytest.approx(35.0)
    assert s["longest_losing_streak"] == 3
    # expectancy = win% x avg win - loss% x avg loss
    assert s["expectancy_r"] == pytest.approx(0.375 * s["avg_win_r"] + 0.625 * s["avg_loss_r"])


def test_summary_orders_by_exit_time_not_input_order():
    t = _trades([-1.0, -1.0, 2.0])
    s1 = js.summarize(t)
    s2 = js.summarize(list(reversed(t)))
    assert s1 == s2 and s1["longest_losing_streak"] == 2 and s1["max_drawdown_r"] == pytest.approx(2.0)


def test_summary_no_loss_and_empty():
    assert js.summarize(_trades([1.0, 0.5]))["profit_factor"] is None
    assert js.summarize([])["trades"] == 0


def test_groups_by_tag_coin_month():
    t = _trades([1.0, -1.0, 2.0, -1.0], tags=["breakout", "pullback"], coins=["ETHUSDT", "SOLUSDT"])
    full = js.full_stats(t)
    assert [g["key"] for g in full["by_setup"]] == ["breakout", "pullback"]
    assert full["by_setup"][0]["total_r"] == pytest.approx(3.0) and full["by_setup"][1]["total_r"] == pytest.approx(-2.0)
    assert [g["key"] for g in full["by_coin"]] == ["ETHUSDT", "SOLUSDT"]
    assert [g["key"] for g in full["by_month"]] == ["2026-10"]
    assert full["overall"]["trades"] == 4


# --- honesty panel ------------------------------------------------------------------------------------
GOLDEN_R = [0.8, -1.0, 1.9, -1.0, -0.4, 2.5, -1.0, 0.3, -1.0, 1.2] * 3


def test_honesty_too_few_below_30():
    h = js.honesty(_trades(GOLDEN_R[:29]))
    assert h["judgeable"] is False and h["text"] == "too few trades to judge" and h["ci_low"] is None


def test_bootstrap_ci_golden_fixed_seed():
    assert js.BOOTSTRAP_RESAMPLES == 10_000 and js.BOOTSTRAP_SEED == 20261022
    lo, hi = js.bootstrap_mean_ci(GOLDEN_R)
    assert lo == pytest.approx(-0.13, abs=1e-9) and hi == pytest.approx(0.6166666666666667, abs=1e-9)
    assert js.bootstrap_mean_ci(GOLDEN_R) == (lo, hi)  # same seed, same answer
    assert js.bootstrap_mean_ci([0.5] * 30) == (pytest.approx(0.5), pytest.approx(0.5))
    h = js.honesty(_trades(GOLDEN_R))
    assert h["judgeable"] is True and h["expectancy_r"] == pytest.approx(0.23)
    assert h["lower_bound_above_zero"] is False and "not above 0 after costs" in h["text"]
    good = js.honesty(_trades([1.0, 0.5, 0.2] * 10))
    assert good["lower_bound_above_zero"] is True and good["ci_low"] > 0


# --- storage: append-only ---------------------------------------------------------------------------
def test_ddl_postgres_append_only_and_key():
    ddl = "\n".join(store.POSTGRES_DDL)
    assert "CREATE TABLE IF NOT EXISTS manual_trades" in ddl
    assert "UNIQUE (trade_id, version)" in ddl and "row_id BIGSERIAL PRIMARY KEY" in ddl
    assert "BEFORE UPDATE OR DELETE ON manual_trades" in ddl and "BEFORE TRUNCATE ON manual_trades" in ddl
    assert "RAISE EXCEPTION" in ddl
    assert all(c in ddl for c in ("valid_from TIMESTAMPTZ", "voided BOOLEAN", "trade_id TEXT NOT NULL"))
    for stmt in (store.INSERT_SQL, store.LATEST_SQL, store.HISTORY_SQL, store.ALL_SQL):
        assert "UPDATE " not in stmt.upper() and "DELETE " not in stmt.upper()


def _log(j, **over):
    data = {"symbol": "ETHUSDT", "side": "LONG", "entry_price": 2500.0, "stop_price": 2450.0, "risk_usd": 25.0,
            "targets": [2600.0, 2700.0], "setup_tag": "breakout", "timeframe": "1h", "notes": "test",
            "opened_at": "2026-10-20T10:00:00Z"}
    data.update(over)
    return run(j.create(data))


def test_create_computes_size_and_r():
    j = journal()
    t = _log(j)
    assert t["version"] == 1 and t["action"] == "OPEN" and t["status"] == "OPEN" and t["voided"] is False
    assert t["quantity"] == pytest.approx(0.5) and t["risk_usd"] == pytest.approx(25.0)
    assert t["targets"] == [2600.0, 2700.0] and t["targets_r"] == [pytest.approx(2.0), pytest.approx(4.0)]
    assert t["cost_model"] == "D144" and t["slippage_rate"] == pytest.approx(0.0002)
    assert t["r_multiple"] is None


def test_edit_close_void_insert_versions_and_never_delete():
    j = journal()
    t = _log(j)
    tid = t["trade_id"]
    e = run(j.edit(tid, {"stop_price": 2475.0, "notes": "moved"}, edit_note="tighter stop"))
    assert (e["version"], e["action"], e["status"]) == (2, "EDIT", "OPEN")
    assert e["quantity"] == pytest.approx(0.5) and e["risk_usd"] == pytest.approx(12.5)  # size kept, risk follows
    c = run(j.close(tid, exit_price=2550.0, exit_at="2026-10-21T10:00:00Z", exit_reason="target"))
    assert (c["version"], c["action"], c["status"]) == (3, "CLOSE", "CLOSED")
    m = costs.close_math("LONG", 2500.0, 2475.0, 0.5, 2550.0, "ETHUSDT")
    assert c["net_pnl_usd"] == pytest.approx(m["net_pnl_usd"]) and c["r_multiple"] == pytest.approx(m["r_multiple"])
    fix = run(j.edit(tid, {"exit_price": 2560.0}, edit_note="fill was better"))
    assert fix["version"] == 4 and fix["status"] == "CLOSED" and fix["net_pnl_usd"] > c["net_pnl_usd"]
    v = run(j.void(tid, reason="duplicate entry"))
    assert (v["version"], v["action"], v["status"], v["voided"]) == (5, "VOID", "VOID", True)
    hist = run(j.history(tid))
    assert [h["version"] for h in hist] == [1, 2, 3, 4, 5]
    assert hist[0]["stop_price"] == 2450.0 and hist[0]["notes"] == "test"  # v1 untouched
    assert hist[2]["exit_price"] == 2550.0  # the first exit is still on record
    latest = run(j.latest())
    assert len(latest) == 1 and latest[0]["version"] == 5
    assert run(j.latest(include_void=False)) == []
    with pytest.raises(store.JournalError):
        run(j.edit(tid, {"notes": "after void"}))


def test_table_refuses_update_and_delete():
    j = journal()
    _log(j)
    conn = j.backend.conn
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with conn:
            conn.execute("UPDATE manual_trades SET notes = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        with conn:
            conn.execute("DELETE FROM manual_trades")
    assert conn.execute("SELECT COUNT(*) FROM manual_trades").fetchone()[0] == 1


def test_duplicate_version_is_refused_by_the_key():
    j = journal()
    t = _log(j)
    row = run(j.history(t["trade_id"]))[0]
    params = {c: row.get(c) for c in store.COLUMN_NAMES}
    with pytest.raises(sqlite3.IntegrityError):
        run(j.backend.execute(store.INSERT_SQL, params))


def test_validation_errors():
    j = journal()
    for bad in ({"stop_price": 2600.0}, {"risk_usd": None}, {"quantity": 1.0}, {"side": "BUY"},
                {"symbol": "ETH/USDT"}, {"targets": [2400.0]}):
        with pytest.raises(store.JournalError):
            _log(j, **bad)
    t = _log(j)
    with pytest.raises(store.JournalError):
        run(j.close(t["trade_id"], exit_price=2550.0, exit_at="2026-10-19T00:00:00Z"))  # before entry
    with pytest.raises(store.NotFound):
        run(j.close("nope", exit_price=1.0))
    run(j.close(t["trade_id"], exit_price=2550.0, exit_at="2026-10-21T00:00:00Z"))
    with pytest.raises(store.JournalError):
        run(j.close(t["trade_id"], exit_price=2560.0))  # already closed: edit instead


# --- API and CSV ----------------------------------------------------------------------------------------
@pytest.fixture
def client():
    j = journal()
    store.set_journal_for_tests(j)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    try:
        yield TestClient(app)
    finally:
        store.set_journal_for_tests(None)


def test_api_log_exit_stats_export(client):
    r = client.post("/api/my-trades", json={"symbol": "solusdt", "side": "SHORT", "entry_price": 150.0,
                                             "stop_price": 153.0, "quantity": 2.0, "setup_tag": "range top",
                                             "timeframe": "4h", "targets": "144, 141",
                                             "opened_at": "2026-10-20T08:00:00Z"})
    assert r.status_code == 200, r.text
    t = r.json()["trade"]
    assert t["symbol"] == "SOLUSDT" and t["risk_usd"] == pytest.approx(6.0) and t["targets"] == [144.0, 141.0]
    tid = t["trade_id"]
    r = client.post(f"/api/my-trades/{tid}/close", json={"exit_price": 147.0, "exit_at": "2026-10-21T08:00:00Z",
                                                         "exit_reason": "manual"})
    assert r.status_code == 200, r.text
    closed = r.json()["trade"]
    assert closed["net_pnl_usd"] == pytest.approx(6.0 - 2 * 297 * 0.0007)
    body = client.get("/api/my-trades").json()
    assert body["counts"] == {"open": 0, "closed": 1, "void": 0}
    assert body["stats"]["overall"]["trades"] == 1
    assert body["stats"]["honesty"]["text"] == "too few trades to judge"
    assert body["cost_model"]["name"] == "D144" and "0.05%" in body["cost_model"]["formula"]
    assert body["storage"]["table"] == "manual_trades" and body["storage"]["key"] == ["trade_id", "version"]
    assert body["storage"]["append_only_guard"] is True
    cur = client.get("/api/my-trades/export.csv")
    assert cur.status_code == 200 and cur.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(cur.text)))
    assert len(rows) == 1 and rows[0]["version"] == "2" and rows[0]["status"] == "CLOSED"
    full = list(csv.DictReader(io.StringIO(client.get("/api/my-trades/export.csv?history=1").text)))
    assert [x["version"] for x in full] == ["1", "2"] and [x["action"] for x in full] == ["OPEN", "CLOSE"]
    assert client.get(f"/api/my-trades/{tid}/history").json()["versions"][0]["action"] == "OPEN"


def test_api_errors(client):
    assert client.post("/api/my-trades", json={"symbol": "BTCUSDT", "side": "LONG", "entry_price": 100,
                                                "stop_price": 101, "risk_usd": 5}).status_code == 422
    assert client.post("/api/my-trades/missing/close", json={"exit_price": 1}).status_code == 404
    assert client.get("/api/my-trades/missing/history").status_code == 404


def test_api_needs_the_database():
    store.set_journal_for_tests(None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    # conftest (P13) disables the database for tests, so there is no journal.
    assert TestClient(app).get("/api/my-trades").status_code == 503


def test_schema_description_for_backups():
    d = store.schema_description()
    assert d["table"] == "manual_trades" and d["key"] == ["trade_id", "version"]
    assert d["columns"][0] == "row_id" and "valid_from" in d["columns"] and "voided" in d["columns"]
