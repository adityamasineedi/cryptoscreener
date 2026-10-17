from app.research.regime_backtest_job import slim_result, start


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
    import asyncio

    out = asyncio.run(start(["WIFUSDT"], 100))
    assert out["status"] == "REJECTED"


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
    import asyncio

    from app.research import regime_backtest_job as job
    from app.research.passed_cap_book import LARGE

    seen: list[tuple] = []

    async def fake_run(symbols, limit, scope=None):
        seen.append((tuple(symbols), limit, scope))

    monkeypatch.setattr(job, "_run", fake_run)
    monkeypatch.setattr(job, "_state", dict(job._state, status="idle"))

    async def go():
        out = await job.start([LARGE[0]], 200, directions)
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


def test_route_passes_the_directions_to_the_job(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.research import regime_backtest_job as job

    calls: list[tuple] = []

    async def fake_start(symbols, limit, directions=None):
        calls.append((symbols, limit, directions))
        return {"status": "STARTED"}

    monkeypatch.setattr(job, "start", fake_start)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    client = TestClient(app)
    client.post("/api/research/regime-backtest/start", json={"symbols": ["ETHUSDT"], "limit": 100, "directions": ["LONG"]})
    client.post("/api/research/regime-backtest/start", json={"symbols": ["ETHUSDT"], "limit": 100})
    assert calls == [(["ETHUSDT"], 100, ["LONG"]), (["ETHUSDT"], 100, None)]
