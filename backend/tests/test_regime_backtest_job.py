from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.research import passed_regime_backtest as book
from app.research import passed_regime_router as router
from app.research import regime_backtest_job as job_mod
from app.research.passed_cap_book import LARGE
from app.research.regime_backtest_job import slim_result, start

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = LARGE[1]


def _hourly(n: int, start: datetime = START) -> list[dict]:
    return [
        {"time": start + timedelta(hours=i), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


def _daily(n: int, start: datetime = START - timedelta(days=60)) -> list[dict]:
    return [
        {"time": start + timedelta(days=i), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _fresh_job_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(job_mod, "_state", job_mod._idle_state())
    monkeypatch.setattr(job_mod, "_task", None)
    monkeypatch.setattr(job_mod, "_lock", asyncio.Lock())
    yield


def test_slim_result_keeps_summary_and_the_latest_closed_trades():
    result = {
        "summary": {
            "trades": 2,
            "pnl_usd_net": 5,
            "by_strategy": {
                "COMBO_01|SHORT": {"strategy_id": "COMBO_01", "pnl_usd_net": 5},
                "COMBO_02|SHORT": {"strategy_id": "COMBO_02", "pnl_usd_net": -1},
            },
        },
        "trades": [
            {"symbol": "BTCUSDT", "net_pnl_usd": 1, "exit_time": "2026-01-01T00:00:00+00:00"},
            {"symbol": "ETHUSDT", "net_pnl_usd": None, "exit_time": "2026-01-02T00:00:00+00:00"},
            {"symbol": "SOLUSDT", "net_pnl_usd": 4, "exit_time": "2026-01-03T00:00:00+00:00"},
        ],
    }
    slim = slim_result(result, trade_limit=1)
    assert [row["strategy_id"] for row in slim["summary"]["by_strategy"]] == ["COMBO_01", "COMBO_02"]
    assert slim["trades"] == [
        {"symbol": "SOLUSDT", "strategy_id": None, "direction": None, "trend_at_entry": None,
         "signal_time": None, "exit_time": "2026-01-03T00:00:00+00:00", "outcome": None,
         "net_pnl_usd": 4, "r_net": None}
    ]


def test_start_rejects_symbols_outside_the_cap_book():
    out = asyncio.run(start(["WIFUSDT"], 100))
    assert out["status"] == "REJECTED"


# --- R1: window -------------------------------------------------------------


def test_dates_are_a_calendar_window():
    window = job_mod.resolve_window(limit=20000, timeframe="1h", start_date="2025-01-01", end_date="2025-03-31")
    assert window["mode"] == "dates"
    assert (window["start_date"], window["end_date"]) == ("2025-01-01", "2025-03-31")
    assert window["limit"] is None


def test_lookback_is_1h_bars_and_not_clamped_to_5000():
    window = job_mod.resolve_window(limit=5760, timeframe="1h")
    assert window == {
        "mode": "lookback",
        "timeframe": "1h",
        "start_date": None,
        "end_date": None,
        "limit": 5760,
        "max_eval_bars": job_mod.MAX_EVAL_BARS,
    }


@pytest.mark.parametrize("tf", ["15m", "4h", "1d"])
def test_other_timeframes_are_rejected(tf: str):
    with pytest.raises(job_mod.RequestError, match="1h bars only"):
        job_mod.resolve_window(limit=100, timeframe=tf)
    out = asyncio.run(start([SYMBOL], 100, timeframe=tf))
    assert out["status"] == "REJECTED"
    assert "1h" in out["error"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_date": "2025-13-01"},
        {"start_date": "2025-03-01", "end_date": "2025-02-01"},
        {"limit": 0},
        {"limit": "abc"},
    ],
)
def test_bad_windows_are_rejected(kwargs):
    out = asyncio.run(start([SYMBOL], **{"limit": kwargs.pop("limit", 100), **kwargs}))
    assert out["status"] == "REJECTED"


class _Loader:
    """Records every service loader call and serves synthetic candles."""

    def __init__(self, n_1h: int, eval_start: int) -> None:
        self.calls: list[dict[str, Any]] = []
        self.n_1h = n_1h
        self.eval_start = eval_start

    async def research(self, symbol, timeframe, **kw):
        self.calls.append({"symbol": symbol, "timeframe": timeframe, **kw})
        if timeframe == "1h":
            return _hourly(self.n_1h), self.eval_start, {"candle_source": "test"}
        return _hourly(10), 0, {}

    async def htf(self, symbol, **kw):
        self.calls.append({"symbol": symbol, "timeframe": "htf", **kw})
        return None, _hourly(10)


def _patch_loader(monkeypatch: pytest.MonkeyPatch, loader: _Loader) -> None:
    from app.research import service

    monkeypatch.setattr(service, "_load_research_candles", loader.research)
    monkeypatch.setattr(service, "_load_htf_candles_for_combo", loader.htf)


def test_dates_reach_every_loader(monkeypatch: pytest.MonkeyPatch):
    loader = _Loader(n_1h=300, eval_start=80)
    _patch_loader(monkeypatch, loader)
    window = job_mod.resolve_window(start_date="2025-01-01", end_date="2025-01-10")
    got, info = asyncio.run(job_mod._load_book(SYMBOL, window))
    asyncio.run(job_mod._load_btc_daily(window))
    assert got is not None
    assert {c["timeframe"] for c in loader.calls} == {"1h", "htf", "15m", "1d"}
    for call in loader.calls:
        assert (call["start_date"], call["end_date"]) == ("2025-01-01", "2025-01-10"), call
    # BTC daily reaches far enough back for the regime clock.
    btc = [c for c in loader.calls if c["symbol"] == "BTCUSDT" and c["timeframe"] == "1d"][-1]
    assert btc["warmup_bars"] >= 500


def test_lookback_loads_the_requested_1h_bars(monkeypatch: pytest.MonkeyPatch):
    loader = _Loader(n_1h=5760 + 80, eval_start=80)
    _patch_loader(monkeypatch, loader)
    got, info = asyncio.run(job_mod._load_book(SYMBOL, job_mod.resolve_window(limit=5760, timeframe="1h")))
    first = loader.calls[0]
    assert (first["timeframe"], first["limit"], first["start_date"], first["end_date"]) == ("1h", 5760, None, None)
    assert got["eval_start"] == 80
    assert info["bars_evaluated"] == 5760 and info["warmup_bars"] == 80 and not info["capped"]
    assert info["period_start"] == (START + timedelta(hours=80)).isoformat()


def test_cap_keeps_the_latest_bars_and_is_reported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(job_mod, "MAX_EVAL_BARS", 1000)
    loader = _Loader(n_1h=3000, eval_start=80)
    _patch_loader(monkeypatch, loader)
    got, info = asyncio.run(job_mod._load_book(SYMBOL, job_mod.resolve_window(start_date="2025-01-01")))
    assert info["capped"] is True
    assert info["bars_evaluated"] == 1000 and info["warmup_bars"] == 80
    assert len(got["candles"]) == 1080 and got["eval_start"] == 80
    assert info["period_start"] == (START + timedelta(hours=2000)).isoformat()


def test_lookback_longer_than_the_cap_is_reported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(job_mod, "MAX_EVAL_BARS", 1000)
    loader = _Loader(n_1h=1080, eval_start=80)
    _patch_loader(monkeypatch, loader)
    got, info = asyncio.run(job_mod._load_book(SYMBOL, job_mod.resolve_window(limit=5000, timeframe="1h")))
    assert loader.calls[0]["limit"] == 1000
    assert info["capped"] is True and info["bars_evaluated"] == 1000


# --- R2: the walk starts at eval_start --------------------------------------


class _FakeWalk:
    """run_combination_backtest stand-in: one signal per planned bar, 1-bar trades."""

    def __init__(self, bars: list[int]) -> None:
        self.bars = bars
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kw):
        self.calls.append(kw)
        candles = kw["candles"]
        lo = kw["index_start"] or 0
        hi = kw["index_end"] if kw["index_end"] is not None else len(candles)
        trades = []
        for i in self.bars:
            if lo <= i < hi and i + 1 < hi:
                trades.append(
                    {
                        "symbol": kw["symbol"],
                        "timeframe": kw["timeframe"],
                        "combination_id": kw["combination"],
                        "entry_index": i,
                        "signal_time": candles[i]["time"].isoformat(),
                        "direction": kw["direction_filter"],
                        "entry_price": 100.0,
                        "stop_price": 102.0,
                        "tp1": 96.0,
                        "exit_index": i + 1,
                        "exit_time": candles[i + 1]["time"].isoformat(),
                        "exit_price": 102.0,
                        "outcome": "SL",
                        "_net": -1.0,
                    }
                )
        return {"status": "OK", "trades": trades}


def _bearish(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(candles, signal_config):
        last = candles[-1]["time"] if candles else None
        return {"trend": "BEARISH", "as_of_bar": last.isoformat() if last else "", "bars": len(candles)}

    monkeypatch.setattr(book, "daily_trend", fake)


def _postprocess(data: dict) -> dict:
    trade = dict(data["backtest_out"]["trades"][0])
    return {"trades": [{**trade, "gross_pnl_usd": trade["_net"], "fee_total_usd": 0.0, "net_pnl_usd": trade["_net"]}]}


def test_walk_starts_at_eval_start_and_reports_the_evaluated_window(monkeypatch: pytest.MonkeyPatch):
    _bearish(monkeypatch)
    side = router.allowed_passed_sides("BEARISH")[0]
    fake = _FakeWalk([60, 80, 90])
    clock = book.RegimeClock(_daily(70), None)
    out = book.run_symbol(SYMBOL, {"candles": _hourly(150), "eval_start": 80}, clock, backtest_fn=fake)
    first_calls = [c for c in fake.calls if c["combination"] == side[0]]
    assert first_calls[0]["index_start"] == 80
    taken = sorted({t["entry_index"] for t in out["trades"]})
    assert 60 not in taken and 80 in taken
    assert all(not s["rewalk_first_bar"] for s in out["signals"] if s["bar_index"] == 80)
    assert out["period_start"] == (START + timedelta(hours=80)).isoformat()
    assert (out["bars_loaded"], out["warmup_bars"], out["bars_evaluated"], out["bars"]) == (150, 80, 70, 70)
    assert sum(out["bars_by_trend"].values()) == 70


def test_job_steps_match_run_regime_book(monkeypatch: pytest.MonkeyPatch):
    """The job's per-symbol child steps make the same calls as run_regime_book."""
    _bearish(monkeypatch)
    daily = _daily(70)
    books = {LARGE[0]: {"candles": _hourly(150), "eval_start": 80}, LARGE[1]: {"candles": _hourly(150), "eval_start": 80}}
    # The job charges the Backtest tab's D144 fees (owner D202); run_regime_book takes them explicitly.
    whole = book.run_regime_book(
        books, daily, backtest_fn=_FakeWalk([90, 100]), postprocess_fn=_postprocess,
        taker_fee=job_mod.TAB_TAKER_FEE, maker_fee=job_mod.TAB_MAKER_FEE,
    )
    parts = [
        job_mod._execute({"op": "symbol", "symbol": s, "book": b, "btc_daily": daily}, backtest_fn=_FakeWalk([90, 100]))
        for s, b in books.items()
    ]
    final = job_mod._execute(
        {
            "op": "finish",
            "symbol_results": parts,
            "btc_daily": daily,
            "candles_by_symbol": {s: b["candles"] for s, b in books.items()},
        },
        postprocess_fn=_postprocess,
    )
    assert final == whole
    assert final["pnl_model"] == "CAUSAL_NEXT_OPEN"


# --- R3: off the event loop, progress, cancel, single-flight ---------------


def _patch_job_io(monkeypatch: pytest.MonkeyPatch, *, runner, capped: bool = False) -> None:
    async def load_book(symbol, window):
        return {"candles": _hourly(150), "eval_start": 80}, {
            "bars_loaded": 150,
            "warmup_bars": 80,
            "bars_evaluated": 70,
            "period_start": (START + timedelta(hours=80)).isoformat(),
            "period_end": (START + timedelta(hours=149)).isoformat(),
            "capped": capped,
        }

    async def load_daily(window):
        return _daily(70)

    monkeypatch.setattr(job_mod, "_load_book", load_book)
    monkeypatch.setattr(job_mod, "_load_btc_daily", load_daily)
    monkeypatch.setattr(job_mod, "_runner", runner)


def _thread_runner(gate: threading.Event | None = None, seen: list | None = None):
    async def run(payload, should_cancel, on_progress):
        def work():
            if gate is not None:
                while not gate.is_set():
                    if should_cancel():
                        return {"status": "CANCELLED"}
                    time.sleep(0.01)
            on_progress({"symbol": payload.get("symbol"), "strategy_id": "X", "direction": "SHORT", "walks": 1})
            if seen is not None:
                seen.append(payload["op"])
            return job_mod._execute(
                payload,
                backtest_fn=_FakeWalk([90]) if payload["op"] == "symbol" else None,
                postprocess_fn=_postprocess if payload["op"] == "finish" else None,
            )

        return await asyncio.to_thread(work)

    return run


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = time.perf_counter() + timeout
    while not predicate():
        assert time.perf_counter() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def test_job_reports_progress_window_and_result(monkeypatch: pytest.MonkeyPatch):
    _bearish(monkeypatch)
    gate = threading.Event()
    seen: list[str] = []
    _patch_job_io(monkeypatch, runner=_thread_runner(gate, seen), capped=True)
    out = await start([LARGE[0], LARGE[1]], 70, timeframe="1h")
    assert out["status"] == "STARTED"
    await _until(lambda: job_mod.status()["phase"] == "running")
    running = job_mod.status()
    assert running["status"] == "running"
    assert running["total_cells"] == 2 and running["done_cells"] == 0
    assert 0 < running["pct"] < 100
    assert running["elapsed_seconds"] is not None
    gate.set()
    await _until(lambda: job_mod.status()["status"] != "running")
    done = job_mod.status()
    assert done["status"] == "done", done["error"]
    assert seen == ["symbol", "symbol", "finish"]
    assert done["pct"] == 100.0 and done["done_cells"] == 2
    window = done["window"]
    assert window["requested"] == {"mode": "lookback", "timeframe": "1h", "start_date": None, "end_date": None, "limit": 70}
    assert window["evaluated"]["period_start"] == (START + timedelta(hours=80)).isoformat()
    assert window["evaluated"]["bars_evaluated"] == 140 and window["evaluated"]["warmup_bars"] == 160
    assert window["cap"]["applied"] is True and window["cap"]["symbols"] == [LARGE[0], LARGE[1]]
    assert done["summary"]["trades"] == 2
    assert done["fixed_config"]["leverage"] == book.LEVERAGE
    # The sizing step got each symbol's candles, so the next-open book priced the run.
    assert done["pnl_model"] == "CAUSAL_NEXT_OPEN"
    assert done["execution_model_version"]
    assert done["directions"] == ["LONG", "SHORT"]


async def test_single_flight_and_cancel(monkeypatch: pytest.MonkeyPatch):
    _bearish(monkeypatch)
    gate = threading.Event()  # never set: only a cancel ends the run
    _patch_job_io(monkeypatch, runner=_thread_runner(gate))
    first = await start([SYMBOL], 70)
    assert first["status"] == "STARTED"
    await _until(lambda: job_mod.status()["phase"] == "running")
    second = await start([SYMBOL], 70)
    assert second["status"] == "ALREADY_RUNNING"
    assert second["job"]["job_id"] == first["job"]["job_id"]
    out = await job_mod.cancel()
    assert out["status"] == "CANCELLED"
    assert out["job"]["status"] == "cancelled"
    assert job_mod._task.done()
    gate.set()
    third = await start([SYMBOL], 70)
    assert third["status"] == "STARTED"
    assert third["job"]["job_id"] != first["job"]["job_id"]
    await _until(lambda: job_mod.status()["status"] != "running")


async def test_child_process_keeps_the_event_loop_free():
    """The real child interpreter runs the walk; the loop keeps ticking."""
    payload = {
        "op": "symbol",
        "symbol": SYMBOL,
        "book": {"candles": _hourly(200), "eval_start": 80, "c4h": None, "c15": None, "c1d": None},
        "btc_daily": _daily(70),
    }
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    result = await job_mod._run_child(payload, lambda: False, lambda event: None)
    stop.set()
    await tick
    assert result.get("status") == "OK", result
    assert result["warmup_bars"] == 80 and result["bars_evaluated"] == 120
    assert gaps and max(gaps) < 0.5


async def test_child_process_is_killed_on_cancel():
    payload = {"op": "symbol", "symbol": SYMBOL, "book": {"candles": _hourly(200)}, "btc_daily": _daily(70)}
    t0 = time.perf_counter()
    result = await job_mod._run_child(payload, lambda: True, lambda event: None)
    assert result == {"status": "CANCELLED"}
    assert time.perf_counter() - t0 < 5.0


# --- route -------------------------------------------------------------------


# --- next-open fill book and sides in scope ---------------------------------


def test_slim_result_reports_the_fill_book_and_the_sides_in_scope():
    result = {
        "pnl_model": "CAUSAL_NEXT_OPEN",
        "execution_model_version": "causal_next_open_v1",
        "directions": ["LONG"],
        "sides_in_scope": [{"strategy_id": "COMBO_03_TRANSITION_B", "timeframe": "1h", "direction": "LONG"}],
        "summary": {"by_strategy": {}},
        "trades": [],
    }
    slim = slim_result(result)
    assert slim["pnl_model"] == "CAUSAL_NEXT_OPEN"
    assert slim["execution_model_version"] == "causal_next_open_v1"
    assert slim["directions"] == ["LONG"]
    assert slim["sides_in_scope"] == result["sides_in_scope"]


def _start_without_running(monkeypatch, directions):
    seen: list[tuple] = []

    async def fake_run(job):
        seen.append((tuple(job["symbols"]), job["limit"], tuple(job["directions"])))

    monkeypatch.setattr(job_mod, "_run", fake_run)

    async def go():
        out = await start([LARGE[0]], 200, directions)
        await asyncio.sleep(0)
        return out

    return asyncio.run(go()), seen


def test_start_with_long_scope_runs_only_the_long_rows(monkeypatch):
    out, seen = _start_without_running(monkeypatch, ["LONG"])
    assert out["status"] == "STARTED"
    assert out["job"]["directions"] == ["LONG"]
    assert out["job"]["pnl_model"] == "CAUSAL_NEXT_OPEN"
    assert out["job"]["sides_in_scope"] == [
        {"strategy_id": "COMBO_03_TRANSITION_B", "timeframe": "1h", "direction": "LONG"}
    ]
    assert seen and seen[0][2] == ("LONG",)


def test_start_with_short_scope_lists_only_short_rows(monkeypatch):
    out, seen = _start_without_running(monkeypatch, ["SHORT"])
    assert out["status"] == "STARTED"
    assert {row["direction"] for row in out["job"]["sides_in_scope"]} == {"SHORT"}
    assert seen[0][2] == ("SHORT",)


def test_start_without_scope_keeps_both_directions(monkeypatch):
    out, seen = _start_without_running(monkeypatch, None)
    assert out["job"]["directions"] == ["LONG", "SHORT"]
    assert seen[0][2] == ("LONG", "SHORT")


def test_start_rejects_an_unknown_direction(monkeypatch):
    out, seen = _start_without_running(monkeypatch, ["SIDEWAYS"])
    assert out["status"] == "REJECTED"
    assert seen == []


def test_job_steps_match_run_regime_book_with_one_side_in_scope(monkeypatch: pytest.MonkeyPatch):
    """Child steps and run_regime_book agree on the next-open book with SHORT only."""
    _bearish(monkeypatch)
    daily = _daily(70)
    books = {LARGE[0]: {"candles": _hourly(150), "eval_start": 80}, LARGE[1]: {"candles": _hourly(150), "eval_start": 80}}
    whole = book.run_regime_book(
        books, daily, backtest_fn=_FakeWalk([90, 100]), postprocess_fn=_postprocess, directions=["SHORT"],
        taker_fee=job_mod.TAB_TAKER_FEE, maker_fee=job_mod.TAB_MAKER_FEE,
    )
    parts = [
        job_mod._execute(
            {"op": "symbol", "symbol": s, "book": b, "btc_daily": daily, "pnl_model": "CAUSAL_NEXT_OPEN", "directions": ["SHORT"]},
            backtest_fn=_FakeWalk([90, 100]),
        )
        for s, b in books.items()
    ]
    final = job_mod._execute(
        {
            "op": "finish",
            "symbol_results": parts,
            "btc_daily": daily,
            "pnl_model": "CAUSAL_NEXT_OPEN",
            "directions": ["SHORT"],
            "candles_by_symbol": {s: b["candles"] for s, b in books.items()},
        },
        postprocess_fn=_postprocess,
    )
    assert final == whole
    assert final["pnl_model"] == "CAUSAL_NEXT_OPEN"
    assert {row["direction"] for row in final["sides_in_scope"]} == {"SHORT"}
    assert final["summary"]["trades"] > 0


def test_finish_without_candles_cannot_price_the_next_open(monkeypatch: pytest.MonkeyPatch):
    """The next-open book never falls back silently to signal-close pricing."""
    _bearish(monkeypatch)
    daily = _daily(70)
    part = job_mod._execute(
        {"op": "symbol", "symbol": SYMBOL, "book": {"candles": _hourly(150), "eval_start": 80}, "btc_daily": daily},
        backtest_fn=_FakeWalk([90]),
    )
    assert part["trades"]
    with pytest.raises(ValueError, match="CAUSAL_NEXT_OPEN"):
        job_mod._execute({"op": "finish", "symbol_results": [part], "btc_daily": daily}, postprocess_fn=_postprocess)


def test_route_passes_dates_and_rejects_mixed_timeframes(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import routes

    seen: list[dict[str, Any]] = []

    async def fake_start(symbols, limit=None, **kw):
        seen.append({"symbols": symbols, "limit": limit, **kw})
        return {"status": "STARTED"}

    monkeypatch.setattr(job_mod, "start", fake_start)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)
    ok = client.post(
        "/api/research/regime-backtest/start",
        json={"symbols": [SYMBOL], "timeframes": ["1h"], "start_date": "2025-01-01", "end_date": "2025-03-31", "limit": 20000},
    ).json()
    assert ok["status"] == "STARTED"
    assert seen[-1] == {
        "symbols": [SYMBOL],
        "limit": 20000,
        "directions": None,
        "timeframe": "1h",
        "start_date": "2025-01-01",
        "end_date": "2025-03-31",
        "apply_symbol_allowlist": True,
    }
    mixed = client.post(
        "/api/research/regime-backtest/start", json={"symbols": [SYMBOL], "timeframes": ["1h", "4h"]}
    ).json()
    assert mixed["status"] == "REJECTED"
    assert len(seen) == 1
    idle = client.post("/api/research/regime-backtest/cancel").json()
    assert idle["status"] == "NOT_RUNNING"


def test_route_passes_the_directions_to_the_job(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import routes

    calls: list[tuple] = []

    async def fake_start(symbols, limit=None, directions=None, **kw):
        calls.append((symbols, limit, directions))
        return {"status": "STARTED"}

    monkeypatch.setattr(job_mod, "start", fake_start)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)
    client.post("/api/research/regime-backtest/start", json={"symbols": ["ETHUSDT"], "limit": 100, "directions": ["LONG"]})
    client.post("/api/research/regime-backtest/start", json={"symbols": ["ETHUSDT"], "limit": 100})
    client.post("/api/research/regime-backtest/start", json={"symbols": ["ETHUSDT"], "limit": 100, "direction": "SHORT"})
    assert calls == [(["ETHUSDT"], 100, ["LONG"]), (["ETHUSDT"], 100, None), (["ETHUSDT"], 100, ["SHORT"])]
