"""Backtest seal guard (owner D202 / "D184"): no backtest reads OHLCV after 2026-07-14 23:59 UTC.

Every test here runs on a fake in-memory database of SYNTHETIC hourly bars (2025-10-01 ->
2026-10-20). The synthetic bars after the seal exist only to prove that no query asks for them
and no run receives them. Nothing touches Postgres.

V1  every backtest endpoint: a tail / 365-day / Max request is clamped, a calendar end after the
    seal is HTTP 400 with the exact message, and no loader call or SQL query has an end after
    the seal.
V2  warm-up bars come from before the seal.
V3  the run record stores its first and last bar; last_bar <= seal.
V5  a known trade is charged 0.05% taker by default.
plus the 37 CANDLE12 V3 OOS-2 frozen files are byte-identical to their lock and to 1286ee7.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services import seal

SEAL = seal.SEAL_END_EXCLUSIVE
REPO = Path(__file__).resolve().parents[2]
TF_STEP = {"5m": timedelta(minutes=5), "15m": timedelta(minutes=15), "1h": timedelta(hours=1),
           "4h": timedelta(hours=4), "1d": timedelta(days=1)}
FIRST = datetime(2025, 10, 1, tzinfo=timezone.utc)
LAST = datetime(2026, 10, 20, tzinfo=timezone.utc)  # synthetic, past the seal on purpose


# --- fake database ------------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeOhlcvDb:
    """Answers the postgres_ohlcv SELECTs from synthetic bars and records every query."""

    def __init__(self) -> None:
        self.queries: list[dict[str, Any]] = []
        self.returned_last: list[datetime] = []
        self._series: dict[tuple[str, str], list[tuple]] = {}

    def series(self, symbol: str, tf: str) -> list[tuple]:
        key = (symbol, tf)
        if key not in self._series:
            step = TF_STEP[tf]
            start = FIRST if tf != "1d" else datetime(2022, 10, 1, tzinfo=timezone.utc)
            if tf == "4h":
                start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            rows, t, px = [], start, 100.0
            n = 0
            while t < LAST:
                n += 1
                px = 100.0 + 5.0 * ((n % 48) - 24) / 24.0
                rows.append((t, px, px * 1.01, px * 0.99, px * 1.002, 10.0))
                t += step
            self._series[key] = rows
        return self._series[key]

    # engine.begin() -> async context manager yielding a connection
    def begin(self):
        db = self

        class _Ctx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(clause)
        params = dict(params or {})
        self.queries.append({"sql": sql, "params": params})
        if "FROM ohlcv" not in sql or "symbol" not in params:
            return _Result([])
        rows = list(self.series(params["symbol"], params["tf"]))
        if "start_ts" in params:
            rows = [r for r in rows if r[0] >= params["start_ts"]]
        if "end_ts" in params:
            rows = [r for r in rows if r[0] < params["end_ts"]]
        if "lim" in params:
            rows = rows[-int(params["lim"]):]
        if rows:
            self.returned_last.append(rows[-1][0])
        return _Result(rows)

    def ohlcv_queries(self) -> list[dict[str, Any]]:
        return [q for q in self.queries if "FROM ohlcv" in q["sql"] and "symbol" in q["params"]]


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeOhlcvDb:
    from app.services.database import db_manager

    db = FakeOhlcvDb()
    monkeypatch.setattr(db_manager, "engine", db)
    monkeypatch.setattr(db_manager, "enabled", True)
    monkeypatch.setattr(db_manager, "status", "ok")
    return db


class LoaderSpy:
    """Wraps the postgres_ohlcv loaders and records the end each call asks for."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.research.postgres_ohlcv as pg

        self.calls: list[dict[str, Any]] = []
        for name in ("load_ohlcv_series_tail", "load_ohlcv_series_range", "load_ohlcv_range_with_quote",
                     "load_ohlcv_series"):
            real = getattr(pg, name)
            monkeypatch.setattr(pg, name, self._wrap(name, real))

    def _wrap(self, name: str, real: Any) -> Any:
        async def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls.append({"loader": name, "args": args, "kwargs": dict(kwargs), "sealed": seal.seal_active()})
            return await real(*args, **kwargs)

        return spy


@pytest.fixture
def loader_spy(monkeypatch: pytest.MonkeyPatch) -> LoaderSpy:
    return LoaderSpy(monkeypatch)


def assert_sealed(db: FakeOhlcvDb, spy: LoaderSpy | None = None, *, expect_queries: bool = True) -> None:
    queries = db.ohlcv_queries()
    if expect_queries:
        assert queries, "the run loaded no OHLCV at all"
    for q in queries:
        assert "end_ts" in q["params"], f"OHLCV query without an end bound: {q['sql']}"
        assert q["params"]["end_ts"] <= SEAL, q
    for last in db.returned_last:
        assert last < SEAL
    if spy is not None:
        for call in spy.calls:
            assert call["sealed"], f"{call['loader']} ran outside the seal scope"
            end = call["kwargs"].get("end_exclusive")
            if call["loader"] == "load_ohlcv_series_tail":
                assert end is not None and end <= SEAL, call
            elif call["loader"] != "load_ohlcv_series":
                # a range call may pass None; the loader then closes it at the seal (SQL check above)
                assert end is None or end <= SEAL, call


# --- app and CPU stubs --------------------------------------------------------------------------


def _client() -> httpx.AsyncClient:
    from app.main import create_app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


@pytest.fixture
def stub_cpu(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Strategy walks and engines replaced by stubs: only the data path runs for real."""
    import app.research.backtest_cpu_pool as pool
    import app.research.engine_backtest as eng

    seen: dict[str, list[Any]] = {"walk": [], "post": [], "engine": []}

    async def walk(**kwargs: Any) -> dict[str, Any]:
        seen["walk"].append(kwargs)
        return {"status": "OK", "trades": [], "result": {}, "sample_size": 0, "elapsed_seconds": 0.0}

    async def post(data: dict[str, Any], **_: Any) -> dict[str, Any]:
        seen["post"].append(data)
        meta = data["load_meta"]
        return {
            "combination_id": data["combination_id"],
            "symbol": meta["symbol"],
            "timeframe": meta["timeframe"],
            "direction": data["direction"],
            "sample_size": 0,
            "trades": [],
            "bars_loaded": len(data["candles"]),
            "warmup_bars": int(data.get("index_start") or 0),
            "period_start": meta.get("period_start"),
            "period_end": meta.get("period_end"),
            "status": "SUCCESS_EMPTY",
        }

    async def engine(payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        seen["engine"].append(payload)
        return {"status": "OK", "trades": [], "execution_model": "stub"}

    monkeypatch.setattr(pool, "run_combination_backtest_isolated", walk)
    monkeypatch.setattr(pool, "run_matrix_postprocess_isolated", post)
    monkeypatch.setattr(eng, "run_engine_cell_isolated", engine)
    return seen


@pytest.fixture
def fresh_job_service(monkeypatch: pytest.MonkeyPatch):
    import app.research.backtest_job as bj

    svc = bj.BacktestJobService()
    monkeypatch.setattr(bj, "backtest_job_service", svc)
    return svc


async def _wait_job(client: httpx.AsyncClient) -> dict[str, Any]:
    for _ in range(400):
        st = (await client.get("/api/research/long-strategy/backtest/job")).json()
        if st.get("status") in ("done", "error", "cancelled", "stalled"):
            return st
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


START_BODY = {
    "symbols": ["BTCUSDT"],
    "timeframes": ["1h"],
    "direction": "SHORT",
    "combination_id": "COMBO_02",
    "strategy_id": "COMBO_02",
    "research_risk_override": True,
    "risk_mode": "RESEARCH_OVERRIDE",
    "principal_usd": 1000,
    "risk_usd": 20,
}


# --- the seal module ----------------------------------------------------------------------------


def test_seal_constants_and_message():
    assert seal.SEAL_LAST_DAY == "2026-07-14"
    assert seal.SEAL_END_EXCLUSIVE == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert seal.SEAL_LAST_MINUTE == datetime(2026, 7, 14, 23, 59, tzinfo=timezone.utc)
    assert seal.SEALED_MESSAGE == "Sealed: data after 2026-07-14 is reserved for the holdout (SEALS.md)"
    with pytest.raises(seal.SealedRangeError):
        seal.check_request_dates(None, "2026-07-15")
    with pytest.raises(seal.SealedRangeError):
        seal.check_request_dates("2026-08-01", None)
    seal.check_request_dates("2025-01-01", "2026-07-14")
    seal.check_request_dates(None, None)


def test_every_existing_seal_date_points_at_the_module():
    from app.research import engine_backtest
    from app.research.daily_trend import funding
    from app.research.regime_lab import config as regime_lab_config

    assert engine_backtest.DAILY_TREND_HOLDOUT_START is seal.SEAL_END_EXCLUSIVE
    assert engine_backtest.DAILY_TREND_LAST_END_DATE == seal.SEAL_LAST_DAY
    assert funding.HOLDOUT_START.to_pydatetime() == seal.SEAL_END_EXCLUSIVE
    assert regime_lab_config.HOLDOUT_START is seal.SEAL_END_EXCLUSIVE


@pytest.mark.parametrize(
    "path,sealed",
    [
        ("/api/research/long-strategy/backtest", True),
        ("/api/research/long-strategy/backtest/start", True),
        ("/api/research/long-strategy/backtest/loss-analysis", True),
        ("/api/research/regime-backtest/start", True),
        ("/api/research/ohlcv-candles", True),
        ("/api/research/cap-book-agents/rerun", True),
        ("/api/research/bos-combinations/COMBO_02/backtest", True),
        ("/api/research/multi-cap-strategies/run", True),
        ("/api/grid-trading/backtest", True),
        ("/api/grid-trading/validate", True),
        ("/api/grid-trading/scan", True),
        ("/api/signals/BTCUSDT/backtest", True),
        ("/api/grid-trading/live", False),
        ("/api/paper/passed-regime", False),
        ("/api/research/ohlcv-range", False),
        ("/api/screener", False),
    ],
)
def test_sealed_paths(path: str, sealed: bool):
    assert seal.is_sealed_path(path) is sealed


@pytest.mark.asyncio
async def test_loaders_unchanged_outside_the_seal(fake_db: FakeOhlcvDb):
    """Paper and the watchers call the loaders outside the scope: same SQL, no end bound."""
    from app.research.postgres_ohlcv import load_ohlcv_series_tail

    rows = await load_ohlcv_series_tail("BTCUSDT", "1h", limit=5)
    q = fake_db.ohlcv_queries()[-1]
    assert "end_ts" not in q["params"]
    assert rows[-1]["time"] > SEAL  # the synthetic DB tail; unsealed callers still see it


@pytest.mark.asyncio
async def test_tail_counts_back_from_the_seal(fake_db: FakeOhlcvDb):
    from app.research.postgres_ohlcv import load_ohlcv_series_tail

    with seal.sealed_scope():
        rows = await load_ohlcv_series_tail("BTCUSDT", "1h", limit=24)
    assert len(rows) == 24
    assert rows[-1]["time"] == datetime(2026, 7, 14, 23, tzinfo=timezone.utc)
    assert fake_db.ohlcv_queries()[-1]["params"]["end_ts"] == SEAL


# --- V1: single strategy (COMBO), hedge, daily books, book 16 ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [5760, 20000], ids=["db_tail_5760", "max"])
async def test_v1_single_strategy_tail_and_max_end_at_the_seal(
    fake_db, loader_spy, stub_cpu, fresh_job_service, limit
):
    async with _client() as client:
        r = await client.post("/api/research/long-strategy/backtest/start", json={**START_BODY, "limit": limit})
        assert r.status_code == 200 and r.json()["status"] == "STARTED", r.text
        final = await _wait_job(client)
    assert final["status"] == "done", final.get("error")
    assert_sealed(fake_db, loader_spy)
    # V3: the run record carries its first and last bar, last_bar <= seal.
    window = final["data_window"]
    assert window["sealed_through"] == "2026-07-14"
    assert datetime.fromisoformat(window["last_bar"]) <= seal.SEAL_LAST_MINUTE
    assert datetime.fromisoformat(window["last_bar"]) == datetime(2026, 7, 14, 23, tzinfo=timezone.utc)
    row = final["rows"][0]
    assert datetime.fromisoformat(row["data_last_bar"]) < SEAL
    assert datetime.fromisoformat(row["data_first_bar"]) < datetime.fromisoformat(row["data_last_bar"])


@pytest.mark.asyncio
async def test_v1_last_365_days_to_the_seal_runs(fake_db, loader_spy, stub_cpu, fresh_job_service):
    body = {**START_BODY, "start_date": "2025-07-15", "end_date": "2026-07-14"}
    async with _client() as client:
        r = await client.post("/api/research/long-strategy/backtest/start", json=body)
        assert r.status_code == 200, r.text
        final = await _wait_job(client)
    assert final["status"] == "done", final.get("error")
    assert_sealed(fake_db, loader_spy)
    assert datetime.fromisoformat(final["data_window"]["last_bar"]) < SEAL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": "2026-01-01", "end_date": "2026-10-01"},  # calendar range ending 2026-10-01
        {"start_date": "2025-10-22", "end_date": "2026-10-22"},  # "last 365 days" ending today
        {"start_date": "2026-08-01"},  # a start inside the holdout
        {"end_date": "2026-07-15"},  # one day past the seal
    ],
)
async def test_v1_single_strategy_calendar_past_the_seal_is_400(
    fake_db, loader_spy, stub_cpu, fresh_job_service, dates
):
    async with _client() as client:
        r = await client.post("/api/research/long-strategy/backtest/start", json={**START_BODY, **dates})
        sync = await client.get(
            "/api/research/long-strategy/backtest",
            params={"symbols": "BTCUSDT", "timeframes": "1h", "direction": "SHORT", **dates},
        )
    for resp in (r, sync):
        assert resp.status_code == 400
        assert resp.json()["detail"] == seal.SEALED_MESSAGE
    assert fake_db.ohlcv_queries() == [] and loader_spy.calls == []
    assert fresh_job_service.status()["status"] == "idle"


@pytest.mark.asyncio
async def test_v1_year_to_date_without_end_is_closed_at_the_seal(fake_db, loader_spy, stub_cpu, fresh_job_service):
    async with _client() as client:
        r = await client.post("/api/research/long-strategy/backtest/start", json={**START_BODY, "start_date": "2026-01-01"})
        assert r.status_code == 200, r.text
        final = await _wait_job(client)
    assert final["status"] == "done", final.get("error")
    assert_sealed(fake_db, loader_spy)


@pytest.mark.asyncio
async def test_v1_hedge_two_sided_tail_ends_at_the_seal(fake_db, loader_spy, stub_cpu, fresh_job_service):
    body = {**START_BODY, "combination_id": "COMBO_02_V2_1_A", "strategy_id": "COMBO_02_V2_1_A",
            "direction": "LONG", "limit": 1200}
    async with _client() as client:
        r = await client.post("/api/research/long-strategy/backtest/start", json=body)
        assert r.status_code == 200 and r.json()["status"] == "STARTED", r.text
        final = await _wait_job(client)
    assert final["status"] == "done", final.get("error")
    assert_sealed(fake_db, loader_spy)
    assert {c["kwargs"].get("end_exclusive") for c in loader_spy.calls if c["loader"] == "load_ohlcv_series_tail"} == {SEAL}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy,tf,direction,limit",
    [
        ("DAILY_TREND_01_SMA200", "1d", "BOTH", 365),
        ("DAILY_TREND_ROTATION", "1d", "BOTH", 20000),
        ("MTF_4H_16_BREAKOUT252", "4h", "LONG", 2190),
        ("CANDLE12_V2", "1h", "LONG", 1200),
        ("OSE_A_corrected_baseline", "1h", "SHORT", 1200),
        ("LARGE_CAP_SWEEP_CHOCH", "1h", "LONG", 1200),
    ],
)
async def test_v1_engine_strategies_end_at_the_seal(
    fake_db, loader_spy, stub_cpu, fresh_job_service, strategy, tf, direction, limit
):
    body = {**START_BODY, "combination_id": strategy, "strategy_id": strategy, "timeframes": [tf],
            "direction": direction, "limit": limit}
    async with _client() as client:
        r = await client.post("/api/research/long-strategy/backtest/start", json=body)
        assert r.status_code == 200 and r.json()["status"] == "STARTED", r.text
        final = await _wait_job(client)
        bad = await client.post(
            "/api/research/long-strategy/backtest/start",
            json={**body, "start_date": "2026-01-01", "end_date": "2026-10-01"},
        )
    assert final["status"] == "done", final.get("error")
    assert_sealed(fake_db, loader_spy)
    payload = stub_cpu["engine"][0]
    assert max(c["time"] for c in payload["candles"]) < SEAL
    assert datetime.fromisoformat(final["data_window"]["last_bar"]) < SEAL
    assert bad.status_code == 400 and bad.json()["detail"] == seal.SEALED_MESSAGE


# --- V1: passed / auto regime book --------------------------------------------------------------


@pytest.fixture
def regime_job(monkeypatch: pytest.MonkeyPatch):
    import app.research.regime_backtest_job as job_mod

    monkeypatch.setattr(job_mod, "_state", job_mod._idle_state())
    monkeypatch.setattr(job_mod, "_task", None)
    monkeypatch.setattr(job_mod, "_lock", asyncio.Lock())
    payloads: list[dict[str, Any]] = []

    async def runner(payload: dict[str, Any], _cancel: Any, _progress: Any) -> dict[str, Any]:
        payloads.append(payload)
        if payload["op"] == "symbol":
            return {"status": "OK", "symbol": payload["symbol"], "walks": 0, "trades": [], "signals": []}
        return {"status": "OK", "summary": {}, "trades": [], "pnl_model": "CAUSAL_NEXT_OPEN",
                "directions": payload.get("directions"), "sides_in_scope": []}

    monkeypatch.setattr(job_mod, "_runner", runner)
    return job_mod, payloads


async def _wait_regime(client: httpx.AsyncClient) -> dict[str, Any]:
    for _ in range(400):
        st = (await client.get("/api/research/regime-backtest")).json()
        if st.get("status") in ("done", "error", "cancelled"):
            return st
        await asyncio.sleep(0.02)
    raise AssertionError("regime run did not finish")


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [{"limit": 1440}, {"limit": 20000}, {"start_date": "2025-07-15", "end_date": "2026-07-14"}])
async def test_v1_regime_book_ends_at_the_seal(fake_db, loader_spy, regime_job, window):
    job_mod, payloads = regime_job
    async with _client() as client:
        r = await client.post("/api/research/regime-backtest/start",
                              json={"symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "directions": ["SHORT"], **window})
        assert r.status_code == 200 and r.json()["status"] == "STARTED", r.text
        final = await _wait_regime(client)
    assert final["status"] == "done", final.get("error")
    assert_sealed(fake_db, loader_spy)
    for p in payloads:
        if p["op"] == "symbol":
            book = p["book"]
            for key in ("candles", "c4h", "c15", "c1d"):
                assert max(c["time"] for c in book[key]) < SEAL, key
        assert max(c["time"] for c in p["btc_daily"]) < SEAL
    evaluated = final["window"]["evaluated"]
    # V3 for the regime run record.
    assert datetime.fromisoformat(evaluated["data_last_bar"]) <= seal.SEAL_LAST_MINUTE
    assert datetime.fromisoformat(evaluated["period_end"]) < SEAL
    assert datetime.fromisoformat(final["window"]["btc_daily"]["last_bar"]) < SEAL
    # V5 for the regime book: the sizing step charges the D144 taker fee.
    finish = [p for p in payloads if p["op"] == "finish"][0]
    assert finish["taker_fee"] == pytest.approx(0.0005)
    assert finish["maker_fee"] == pytest.approx(0.0002)
    assert final["fixed_config"]["taker_fee_pct"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_v1_regime_book_calendar_past_the_seal_is_400(fake_db, loader_spy, regime_job):
    async with _client() as client:
        r = await client.post("/api/research/regime-backtest/start",
                              json={"symbols": ["BTCUSDT"], "timeframe": "1h", "start_date": "2026-01-01", "end_date": "2026-10-01"})
    assert r.status_code == 400 and r.json()["detail"] == seal.SEALED_MESSAGE
    assert fake_db.ohlcv_queries() == []


@pytest.mark.asyncio
async def test_regime_plan_lists_every_side_the_run_walks():
    from app.research.passed_regime_backtest import sides_in_scope

    async with _client() as client:
        short = (await client.get("/api/research/regime-backtest/plan", params={"directions": "SHORT"})).json()
        long_ = (await client.get("/api/research/regime-backtest/plan", params={"directions": "LONG"})).json()
    ids = [row["strategy_id"] for row in short["sides_in_scope"]]
    assert ids == [cid for cid, _tf, _side in sides_in_scope(["SHORT"])]
    assert "COMBO_02_V2" in ids
    assert [row["strategy_id"] for row in long_["sides_in_scope"]] == ["COMBO_03_TRANSITION_B"]
    assert short["fixed_config"]["taker_fee_pct"] == pytest.approx(0.05)


# --- V1: grid, charts, loss analysis, profit checker, BOS, multi-cap, signals ------------------


@pytest.mark.asyncio
async def test_v1_grid_scan_and_backtest_tail_end_at_the_seal(fake_db, loader_spy, monkeypatch):
    import app.grid_trading.api as grid_api

    monkeypatch.setattr(grid_api, "grid_trading_enabled", lambda: True)
    async with _client() as client:
        scan = await client.post("/api/grid-trading/scan", json={"symbols": ["BTCUSDT"], "timeframe": "1h", "limit": 400})
        bt = await client.post(
            "/api/grid-trading/backtest",
            json={"symbol": "BTCUSDT", "timeframe": "1h", "range_low": 95, "range_high": 105, "grid_count": 8,
                  "capital": 1000, "stop_price": 90, "limit": 1500, "funding_mode": "exclude"},
        )
    assert scan.status_code == 200, scan.text
    assert bt.status_code in (200, 422), bt.text
    assert_sealed(fake_db)
    tails = [c for c in loader_spy.calls if c["loader"] == "load_ohlcv_series_tail"]
    assert tails and all(c["sealed"] for c in tails)
    if bt.status_code == 200:
        assert max(datetime.fromisoformat(str(c["time"])) for c in bt.json()["candles"]) < SEAL


@pytest.mark.asyncio
async def test_v1_trade_chart_padding_stops_at_the_seal(fake_db, loader_spy):
    async with _client() as client:
        r = await client.get("/api/research/ohlcv-candles",
                             params={"symbol": "BTCUSDT", "timeframe": "1h", "start": "2026-07-13", "end": "2026-07-14", "pad_bars": 64})
    body = r.json()
    assert body["status"] == "OK"
    assert max(datetime.fromisoformat(c["open_time"]) for c in body["candles"]) < SEAL
    assert datetime.fromisoformat(body["window_end_exclusive"]) <= SEAL
    assert_sealed(fake_db, loader_spy)


@pytest.mark.asyncio
async def test_v1_loss_analysis_hindsight_stops_at_the_seal(monkeypatch):
    import app.research.trade_loss_analysis as tla

    calls: list[dict[str, Any]] = []

    async def loader(symbol: str, tf: str, **kwargs: Any):
        calls.append({**kwargs, "sealed": seal.seal_active()})
        return [{"time": SEAL - timedelta(hours=1), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}], 0, {}

    monkeypatch.setattr(tla, "analyze_row", lambda row, candles, **_: {"status": "OK", "bars": len(candles)})

    class Job:
        status = "done"
        include_trades = True
        job_id = "j"
        combination_id = "COMBO_02"
        rows = [{"symbol": "BTCUSDT", "timeframe": "1h", "period_start": "2026-06-01T00:00:00+00:00",
                 "period_end": "2026-07-14T23:00:00+00:00",
                 "trades": [{"signal_time": "2026-07-10T00:00:00+00:00", "exit_time": "2026-07-14T20:00:00+00:00"}]}]

    class Svc:
        _job = Job()

    out = await tla.loss_analysis_for_current_job("BTCUSDT", "1h", max_hold_after_exit=168, service=Svc(), candle_loader=loader)
    assert out["status"] == "OK"
    assert calls[0]["sealed"] is True
    assert calls[0]["end_date"] <= "2026-07-14"


@pytest.mark.asyncio
async def test_profit_checker_is_labelled_and_its_rerun_refused(fake_db):
    async with _client() as client:
        snap = (await client.get("/api/research/cap-book-agents")).json()
        rerun = await client.post("/api/research/cap-book-agents/rerun")
    assert snap["sealed"] is True and snap["rerun_allowed"] is False
    assert snap["sealed_label"] == (
        "Inside the sealed holdout window; already used before the seal (SEALS.md). Not evidence."
    )
    assert snap["window"]["1h"] == {"start": "2026-09-20", "end": "2026-10-10"}
    assert rerun.status_code == 400 and rerun.json()["detail"] == seal.SEALED_MESSAGE
    assert fake_db.ohlcv_queries() == []


@pytest.mark.asyncio
async def test_bos_and_multicap_calendar_past_the_seal_is_400(fake_db):
    async with _client() as client:
        a = await client.get("/api/research/bos-combinations/compare",
                             params={"symbol": "BTCUSDT", "timeframe": "1h", "end_date": "2026-10-01"})
        b = await client.get("/api/research/bos-combinations/COMBO_02/backtest",
                             params={"symbol": "BTCUSDT", "timeframe": "1h", "end_date": "2026-10-01"})
        c = await client.get("/api/research/bos-combinations/COMBO_02",
                             params={"symbol": "BTCUSDT", "timeframe": "1h", "end_date": "2026-10-01"})
        d = await client.post("/api/research/multi-cap-strategies/run", json={"end": "2026-10-01"})
    for resp in (a, b, c, d):
        assert resp.status_code == 400 and resp.json()["detail"] == seal.SEALED_MESSAGE
    assert fake_db.ohlcv_queries() == []


@pytest.mark.asyncio
async def test_signal_backtest_ignores_live_bars_after_the_seal(monkeypatch):
    from app.services.ohlcv_store import ohlcv_store

    live = [{"time": SEAL + timedelta(hours=i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(300)]
    monkeypatch.setattr(ohlcv_store, "get_candles_for_engine", lambda *a, **k: list(live))
    async with _client() as client:
        r = await client.get("/api/signals/BTCUSDT/backtest", params={"timeframe": "1h", "limit": 300})
    assert r.json()["status"] == "WAITING"


# --- V2: warm-up --------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 500, "tail_warmup": True, "warmup_bars": 100},
        {"limit": 500, "start_date": "2026-07-01", "end_date": "2026-07-14", "warmup_bars": 200},
        {"limit": 500, "start_date": "2026-07-01", "warmup_bars": 200},
    ],
    ids=["tail", "calendar", "start_only"],
)
async def test_v2_warmup_bars_come_from_before_the_seal(fake_db, loader_spy, kwargs):
    from app.research.service import _load_research_candles

    with seal.sealed_scope():
        candles, eval_start, meta = await _load_research_candles("BTCUSDT", "1h", use_research_cache=False, **kwargs)
    assert eval_start > 0
    warm = candles[:eval_start]
    assert warm and max(c["time"] for c in warm) < candles[eval_start]["time"] < SEAL
    assert max(c["time"] for c in candles) < SEAL
    assert datetime.fromisoformat(meta["data_last_bar"]) < SEAL
    assert datetime.fromisoformat(meta["data_first_bar"]) == candles[0]["time"]
    assert_sealed(fake_db, loader_spy)


@pytest.mark.asyncio
async def test_v2_memory_fallback_keeps_no_live_bar(monkeypatch):
    """No database: the in-memory live store is the fallback; a sealed run keeps none of it."""
    from app.research import service
    from app.services.ohlcv_store import ohlcv_store

    live = [{"time": SEAL + timedelta(hours=i), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(50)]
    monkeypatch.setattr(ohlcv_store, "get_candles_for_engine", lambda *a, **k: list(live))
    service.clear_candle_cache()
    with seal.sealed_scope():
        candles, _, _ = await service._load_research_candles("BTCUSDT", "1h", limit=50, use_research_cache=False)
    assert candles == []
    service.clear_candle_cache()


# --- V5: fees -----------------------------------------------------------------------------------


def test_v5_known_trade_is_charged_0_05_pct_taker():
    from app.research.backtest_ui_config import BACKTEST_TAB_MAKER_FEE_PCT, BACKTEST_TAB_TAKER_FEE_PCT
    from app.research.trade_fees import enrich_trades

    assert BACKTEST_TAB_TAKER_FEE_PCT == 0.05 and BACKTEST_TAB_MAKER_FEE_PCT == 0.02
    trade = {
        "symbol": "BTCUSDT", "direction": "LONG", "entry_price": 100.0, "stop_price": 99.0,
        "exit_price": 102.0, "outcome": "TP1", "entry_type": "MARKET",
        "signal_time": "2026-07-01T00:00:00+00:00", "entry_time": "2026-07-01T01:00:00+00:00",
        "exit_time": "2026-07-01T05:00:00+00:00", "R": 2.0,
    }
    [row] = enrich_trades([trade], risk_usd=20.0, taker_fee=BACKTEST_TAB_TAKER_FEE_PCT / 100.0,
                          maker_fee=BACKTEST_TAB_MAKER_FEE_PCT / 100.0, leverage=2.0, account_equity=1000.0,
                          closed_only=True)
    qty = float(row["qty"])
    assert qty == pytest.approx(20.0)  # $20 risk over a $1 stop
    # Fees are a negative cost: entry $1.00 and exit $1.02 at 0.05% of executed notional.
    assert row["fee_entry_rate"] == pytest.approx(0.0005) and row["fee_exit_rate"] == pytest.approx(0.0005)
    assert abs(float(row["fee_entry_usd"])) == pytest.approx(qty * 100.0 * 0.0005)
    assert abs(float(row["fee_exit_usd"])) == pytest.approx(qty * 102.0 * 0.0005)
    assert abs(float(row["fee_total_usd"])) == pytest.approx(2.02)
    assert float(row["net_pnl_usd"]) == pytest.approx(40.0 - 2.02)


@pytest.mark.asyncio
async def test_v5_route_default_fees_reach_the_cell(fake_db, stub_cpu, fresh_job_service):
    async with _client() as client:
        started = (await client.post("/api/research/long-strategy/backtest/start", json={**START_BODY, "limit": 300})).json()
        await _wait_job(client)
    assert started["job"]["taker_fee_pct"] == pytest.approx(0.05)
    assert started["job"]["maker_fee_pct"] == pytest.approx(0.02)
    assert stub_cpu["post"][0]["taker_fee"] == pytest.approx(0.0005)
    assert stub_cpu["post"][0]["maker_fee"] == pytest.approx(0.0002)


# --- frozen files -------------------------------------------------------------------------------


def _lock_files() -> dict[str, str]:
    lock = json.loads((REPO / "docs" / "candle12_v3_oos_contract.lock.json").read_text(encoding="utf-8"))
    return lock["code_fingerprint"]["files"]


def test_frozen_oos2_files_match_their_lock():
    files = _lock_files()
    assert len(files) == 37
    for rel, sha in files.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == sha, rel


def test_frozen_oos2_files_unchanged_since_1286ee7():
    try:
        subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", "1286ee7^{commit}"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("1286ee7 not in this checkout")
    for rel in _lock_files():
        base = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"1286ee7:{rel}"],
                              check=True, capture_output=True, text=True).stdout.strip()
        now = subprocess.run(["git", "-C", str(REPO), "hash-object", rel],
                             check=True, capture_output=True, text=True).stdout.strip()
        assert now == base, rel


# --- owner-only dev mode (D212) -----------------------------------------------------------------


def test_dev_mode_setting_defaults_false_and_is_read_from_settings(monkeypatch):
    from app.config import Settings

    assert Settings.model_fields["backtest_dev_mode_allowed"].default is False
    assert Settings.model_fields["backtest_dev_mode_allowed"].alias == "BACKTEST_DEV_MODE_ALLOWED"

    class S:
        backtest_dev_mode_allowed = True

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    assert seal.dev_mode_allowed() is True
    assert seal.dev_mode_granted("true") is True
    assert seal.dev_mode_granted(False) is False
    S.backtest_dev_mode_allowed = False
    assert seal.dev_mode_granted(True) is False


@pytest.mark.asyncio
async def test_dev_mode_refused_while_the_setting_is_off(fake_db, loader_spy, stub_cpu, fresh_job_service, tmp_path, monkeypatch):
    monkeypatch.setattr(seal, "dev_mode_allowed", lambda: False)
    monkeypatch.setattr(seal, "HOLDOUT_USAGE_PATH", tmp_path / "HOLDOUT_USAGE.md")
    async with _client() as client:
        flag = (await client.get("/api/research/backtest-dev-mode")).json()
        bad = await client.post("/api/research/long-strategy/backtest/start",
                                json={**START_BODY, "start_date": "2026-07-01", "end_date": "2026-10-01", "dev_mode": True})
        sync = await client.get("/api/research/long-strategy/backtest",
                                params={"symbols": "BTCUSDT", "timeframes": "1h", "direction": "SHORT",
                                        "end_date": "2026-10-01", "dev_mode": "true"})
        regime = await client.post("/api/research/regime-backtest/start",
                                   json={"symbols": ["BTCUSDT"], "timeframe": "1h", "end_date": "2026-10-01", "dev_mode": True})
        tail = await client.post("/api/research/long-strategy/backtest/start", json={**START_BODY, "limit": 500, "dev_mode": True})
        assert tail.status_code == 200
        final = await _wait_job(client)
    assert flag["allowed"] is False and flag["hint"] == "owner-only: set BACKTEST_DEV_MODE_ALLOWED=true in .env"
    for resp in (bad, sync, regime):
        assert resp.status_code == 400 and resp.json()["detail"] == seal.SEALED_MESSAGE
    assert final["status"] == "done" and final["dev_mode"] is False and final["run_label"] is None
    assert_sealed(fake_db, loader_spy)  # the tail still counts back from the seal
    assert not (tmp_path / "HOLDOUT_USAGE.md").exists()


@pytest.mark.asyncio
async def test_dev_mode_needs_the_request_flag_too(fake_db, loader_spy, stub_cpu, fresh_job_service, monkeypatch):
    monkeypatch.setattr(seal, "dev_mode_allowed", lambda: True)
    async with _client() as client:
        bad = await client.post("/api/research/long-strategy/backtest/start",
                                json={**START_BODY, "start_date": "2026-07-01", "end_date": "2026-10-01"})
    assert bad.status_code == 400 and bad.json()["detail"] == seal.SEALED_MESSAGE
    assert fake_db.ohlcv_queries() == []


@pytest.mark.asyncio
async def test_dev_mode_allows_post_seal_data_labels_and_logs_the_run(
    fake_db, loader_spy, stub_cpu, fresh_job_service, tmp_path, monkeypatch, regime_job
):
    log = tmp_path / "research" / "HOLDOUT_USAGE.md"
    monkeypatch.setattr(seal, "dev_mode_allowed", lambda: True)
    monkeypatch.setattr(seal, "HOLDOUT_USAGE_PATH", log)
    body = {**START_BODY, "start_date": "2026-07-01", "end_date": "2026-10-01", "dev_mode": True}
    async with _client() as client:
        flag = (await client.get("/api/research/backtest-dev-mode")).json()
        r = await client.post("/api/research/long-strategy/backtest/start", json=body)
        assert r.status_code == 200 and r.json()["status"] == "STARTED", r.text
        final = await _wait_job(client)
        rg = await client.post("/api/research/regime-backtest/start",
                               json={"symbols": ["BTCUSDT"], "timeframe": "1h", "directions": ["SHORT"],
                                     "start_date": "2026-07-01", "end_date": "2026-10-01", "dev_mode": True})
        assert rg.status_code == 200 and rg.json()["status"] == "STARTED", rg.text
        regime = await _wait_regime(client)
        chart = (await client.get("/api/research/ohlcv-candles",
                                  params={"symbol": "BTCUSDT", "timeframe": "1h", "start": "2026-09-01",
                                          "end": "2026-09-02", "dev_mode": "true"})).json()
    assert flag["allowed"] is True and flag["banner"] == "Uses holdout data; results are not evidence"
    assert final["status"] == "done", final.get("error")
    assert final["dev_mode"] is True and final["run_label"] == "DEV MODE (holdout data)"
    assert final["data_window"]["sealed_through"] == "DEV MODE (holdout data)"
    assert datetime.fromisoformat(final["data_window"]["last_bar"]) > SEAL
    ranges = [c for c in loader_spy.calls if c["loader"] == "load_ohlcv_series_range"]
    assert any(c["kwargs"]["end_exclusive"] > SEAL for c in ranges)
    assert regime["status"] == "done" and regime["dev_mode"] is True
    assert max(datetime.fromisoformat(c["open_time"]) for c in chart["candles"]) > SEAL
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Holdout usage (Backtest tab dev mode)"
    runs = [ln for ln in lines if ln.startswith("| 20")]
    assert len(runs) == 2
    assert "POST /api/research/long-strategy/backtest/start" in runs[0] and "COMBO_02 SHORT (done)" in runs[0]
    assert "| BTCUSDT | 1h | 2026-07-01 | 2026-10-01 |" in runs[0]
    assert final["data_window"]["last_bar"] in runs[0]
    assert "POST /api/research/regime-backtest/start" in runs[1]
