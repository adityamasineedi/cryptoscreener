"""Backtest-screen CPU work runs in child processes, not in the API process.

2026-10-17: the matrix postprocess (fee enrich, causal book, market-structure
analytics) ran on a thread inside the API process and held the GIL for
minutes; /api/health timed out and the paper watcher missed its bar. It now
runs in a child interpreter, as do the VectorBT gate and the loss-analysis
what-ifs. These tests pin: the event loop stays responsive, the child returns
the same row the in-process call returns, cancel kills the child, errors keep
their type, and no temp file is left behind.
"""

from __future__ import annotations

import asyncio
import glob
import math
import os
import pickle
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import app.research.backtest_cpu_pool as pool
from app.research.backtest_cpu_worker import apply_vectorbt_rows, build_matrix_row_postprocess
from app.research.causal_execution import clear_isolated_execution_runs
from app.research.research_child import ResearchChildCancelled, run_in_child

HERE = "tests.test_backtest_off_api_process"
SPIN_SECONDS = 3.0
MAX_GAP_SECONDS = 0.5
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------- child targets


def _gil_chunk(target_seconds: float = 0.8) -> int:
    """A ``sum(range(n))`` size that takes about ``target_seconds``.

    ``sum`` over a range is one C call: it holds the GIL for its whole run,
    like the long feature-table scans did, so a thread doing it starves the loop.
    """
    n = 2_000_000
    t = time.perf_counter()
    sum(range(n))
    dt = max(time.perf_counter() - t, 1e-6)
    return max(n, int(n * target_seconds / dt))


def _spin_holding_gil(seconds: float) -> None:
    n = _gil_chunk()
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        sum(range(n))


def spin_then_postprocess(data: dict[str, Any]) -> dict[str, Any]:
    """Child target: ~3 s of GIL-bound work, then the real postprocess."""
    _spin_holding_gil(SPIN_SECONDS)
    return build_matrix_row_postprocess(data)


def write_pid_then_sleep(data: dict[str, Any]) -> None:
    with open(data["pid_path"], "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    time.sleep(120)


def raise_value_error(data: dict[str, Any]) -> None:
    raise ValueError(f"bad payload {data['tag']}")


# --------------------------------------------------------------------- fixtures


def _bar(t: datetime, o: float, h: float, l: float, c: float, v: float) -> dict[str, Any]:
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _candles_1h(n: int = 720) -> list[dict[str, Any]]:
    """Deterministic swings (two sine waves + drift): real-shaped OHLCV."""
    out = []
    prev = 100.0
    for i in range(n):
        mid = 100.0 + 0.01 * i + 6.0 * math.sin(i / 17.0) + 2.5 * math.sin(i / 5.3)
        o, c = prev, mid
        h = max(o, c) + 0.4 + 0.3 * abs(math.sin(i / 3.1))
        l = min(o, c) - 0.4 - 0.3 * abs(math.cos(i / 2.9))
        out.append(_bar(T0 + timedelta(hours=i), o, h, l, c, 1000.0 + 40.0 * math.sin(i / 7.0)))
        prev = c
    return out


def _resample(bars: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    out = []
    for j in range(0, len(bars) - k + 1, k):
        grp = bars[j : j + k]
        out.append(
            _bar(
                grp[0]["time"],
                grp[0]["open"],
                max(b["high"] for b in grp),
                min(b["low"] for b in grp),
                grp[-1]["close"],
                sum(b["volume"] for b in grp),
            )
        )
    return out


def _split_15m(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bars:
        step = (b["close"] - b["open"]) / 4.0
        for q in range(4):
            o = b["open"] + step * q
            c = o + step
            out.append(
                _bar(
                    b["time"] + timedelta(minutes=15 * q),
                    o,
                    max(o, c) + (b["high"] - max(b["open"], b["close"])) * (q == 1),
                    min(o, c) - (b["low"] - min(b["open"], b["close"])) * (q == 2),
                    c,
                    b["volume"] / 4.0,
                )
            )
    return out


def _trade(candles: list[dict[str, Any]], i: int, x: int, stop: float, tp1: float, outcome: str) -> dict[str, Any]:
    entry = candles[i]["close"]
    exit_px = tp1 if outcome.startswith("TP") else stop
    return {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "combination_id": "COMBO_02_V2",
        "entry_index": i,
        "signal_time": candles[i]["time"].isoformat(),
        "direction": "LONG",
        "entry_price": entry,
        "stop_price": stop,
        "tp1": tp1,
        "tp2": None,
        "tp3": None,
        "rr": abs(tp1 - entry) / abs(entry - stop),
        "exit_index": x,
        "exit_time": candles[x]["time"].isoformat(),
        "exit_price": exit_px,
        "outcome": outcome,
        "r_multiple": (exit_px - entry) / (entry - stop),
        "holding_bars": x - i,
        "ambiguous": False,
        "condition_snapshot": {"entry_type": "MARKET"},
    }


def _postprocess_payload(job_id: str, *, analytics_on: bool = True) -> dict[str, Any]:
    candles = _candles_1h()
    trades = []
    for i in range(260, 700, 55):
        entry = candles[i]["close"]
        hit_tp = i % 2 == 0
        trades.append(
            _trade(candles, i, i + 6, entry - 1.5, entry + 3.0, "TP1" if hit_tp else "SL")
        )
    r_values = [t["r_multiple"] for t in trades]
    return {
        "backtest_out": {
            "sample_size": len(trades),
            "status": "OK",
            "execution_model": "DUAL_LEGACY_AND_CAUSAL",
            "htf_fully_closed": True,
            "elapsed_seconds": 1.25,
            "result": {"sample_size": len(trades), "average_R": sum(r_values) / len(r_values), "r_values": r_values},
            "trades": trades,
        },
        "risk_usd": 20.0,
        "taker_fee": 0.0004,
        "maker_fee": 0.0002,
        "leverage": 2.0,
        "principal": 1000.0,
        "include_trades": True,
        "direction": "LONG",
        "combination_id": "COMBO_02_V2",
        "combination_name": "COMBO_02 V2",
        "load_meta": {"symbol": "BTCUSDT", "timeframe": "1h", "candle_source": "fixture"},
        "candles": candles,
        "candles_1h": None,
        "candles_4h": _resample(candles, 4),
        "candles_15m": _split_15m(candles),
        "index_start": 200,
        "job_id": job_id,
        "analytics_on": analytics_on,
        "write_artifacts": False,
    }


async def _max_gap_while(awaitable: Any) -> tuple[Any, float, float]:
    """Await ``awaitable`` while a 50 ms ticker records the longest loop gap."""
    gaps: list[float] = []
    done = False

    async def ticker() -> None:
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.1)
    t0 = time.perf_counter()
    try:
        result = await awaitable
    finally:
        elapsed = time.perf_counter() - t0
        done = True
        await task
    return result, max(gaps), elapsed


def _leftover_temp_files() -> set[str]:
    tmp = tempfile.gettempdir()
    return set(glob.glob(os.path.join(tmp, "research_child_*")))


# Wall-clock stopwatches inside the analytics payload differ between any two runs.
_STOPWATCH_KEYS = frozenset({"analytics_runtime_seconds", "attach_runtime_seconds"})


def _assert_identical(a: Any, b: Any, path: str = "row") -> None:
    """Same types, same key order, bit-identical leaves (NaN-safe), stopwatches aside.

    Pickle bytes of the two objects are not comparable: the child's strings are
    separate objects where in-process ones are shared, which changes the memo.
    """
    assert type(a) is type(b), f"{path}: {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, dict):
        assert list(a) == list(b), f"{path}: keys {list(a)} != {list(b)}"
        for key in a:
            if key in _STOPWATCH_KEYS:
                assert isinstance(b[key], type(a[key])), f"{path}.{key}"
                continue
            _assert_identical(a[key], b[key], f"{path}.{key}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            _assert_identical(x, y, f"{path}[{i}]")
    else:
        assert pickle.dumps(a) == pickle.dumps(b), f"{path}: {a!r} != {b!r}"


# --------------------------------------------------------------------- tests


def test_event_loop_stays_responsive_while_postprocess_spins_in_child(monkeypatch):
    monkeypatch.setattr(pool, "_POSTPROCESS_TARGET", f"{HERE}:spin_then_postprocess")
    payload = _postprocess_payload("offapi-spin", analytics_on=False)
    beats: list[dict[str, Any]] = []
    before = _leftover_temp_files()

    row, max_gap, elapsed = asyncio.run(
        _max_gap_while(pool.run_matrix_postprocess_isolated(payload, progress_callback=beats.append))
    )

    assert elapsed >= SPIN_SECONDS  # the spin really ran (in the child)
    assert max_gap < MAX_GAP_SECONDS, f"event loop stalled {max_gap:.3f}s"
    # The job watchdog is fed with phase-only heartbeats while the child runs.
    assert len(beats) >= 2 and {b["phase"] for b in beats} == {"ANALYTICS_ATTACH"}
    clear_isolated_execution_runs()
    _assert_identical(row, build_matrix_row_postprocess(payload))
    assert _leftover_temp_files() <= before


def test_the_same_spin_on_a_thread_does_stall_the_loop():
    """Control: the ticker catches GIL-bound work on a thread (the old path)."""
    _, max_gap, _ = asyncio.run(_max_gap_while(asyncio.to_thread(_spin_holding_gil, 1.6)))
    assert max_gap > MAX_GAP_SECONDS


@pytest.mark.parametrize("analytics_on", [True, False])
def test_isolated_postprocess_row_is_byte_identical_to_in_process(analytics_on):
    payload = _postprocess_payload(f"offapi-parity-{analytics_on}", analytics_on=analytics_on)
    clear_isolated_execution_runs()
    direct = build_matrix_row_postprocess(payload)
    isolated = asyncio.run(pool.run_matrix_postprocess_isolated(payload))
    assert direct["sample_size"] == 8
    assert direct["causal_trades"] and direct["trades"]
    if analytics_on:
        assert direct["market_structure"]["analytics_enabled"] is True
    _assert_identical(isolated, direct)


def test_vectorbt_gate_in_child_matches_in_process():
    from tests.test_vectorbt_execution import _combo_row

    data = {
        "rows": [_combo_row(), "not-a-row"],
        "strategy_id": "COMBO_02",
        "family": "combo",
        "adapter_kwargs": {},
        "include_trades": False,
    }
    direct = apply_vectorbt_rows(data)
    isolated = asyncio.run(run_in_child("app.research.backtest_cpu_worker:apply_vectorbt_rows", data))
    assert isolated[0]["trades"] == [] and isolated[1] == "not-a-row"
    assert "execution_engine" in isolated[0]
    _assert_identical(isolated, direct)


def test_cancel_kills_the_child_interpreter(tmp_path):
    psutil = pytest.importorskip("psutil")
    pid_path = tmp_path / "child.pid"
    t0 = time.perf_counter()

    def should_cancel() -> bool:
        return pid_path.exists() and pid_path.stat().st_size > 0

    with pytest.raises(ResearchChildCancelled):
        asyncio.run(
            run_in_child(f"{HERE}:write_pid_then_sleep", {"pid_path": str(pid_path)}, should_cancel=should_cancel)
        )
    assert time.perf_counter() - t0 < 60
    pid = int(pid_path.read_text(encoding="utf-8"))
    # The interpreter itself (not just a venv launcher) is gone.
    deadline = time.perf_counter() + 10
    while psutil.pid_exists(pid) and time.perf_counter() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_child_error_keeps_its_type():
    # strategy_matrix re-raises ValueError and falls back on any other error.
    with pytest.raises(ValueError, match="bad payload x1"):
        asyncio.run(run_in_child(f"{HERE}:raise_value_error", {"tag": "x1"}))


def test_strategy_matrix_reports_cancelled_when_postprocess_is_cancelled(monkeypatch):
    import app.research.market_structure as ms
    import app.research.service as service

    async def fake_load(symbol, timeframe, **kwargs):
        return _candles_1h(300), 0, {"symbol": symbol, "timeframe": timeframe}

    async def fake_htf(symbol, **kwargs):
        return None, None

    async def fake_walk(**kwargs):
        return {"trades": [], "result": {}, "status": "SUCCESS_EMPTY", "sample_size": 0}

    seen: dict[str, Any] = {}

    async def cancelled_post(data, **kwargs):
        seen.update(kwargs)
        raise ResearchChildCancelled("postprocess")

    monkeypatch.setattr(service, "_load_research_candles", fake_load)
    monkeypatch.setattr(service, "_load_htf_candles_for_combo", fake_htf)
    monkeypatch.setattr(pool, "run_combination_backtest_isolated", fake_walk)
    monkeypatch.setattr(pool, "run_matrix_postprocess_isolated", cancelled_post)
    monkeypatch.setattr(ms, "resolve_analytics_flags", lambda settings: (False, False))
    flag = lambda: False  # noqa: E731
    out = asyncio.run(
        service.BosResearchService().strategy_matrix(
            combination_id="COMBO_02", symbols=["BTCUSDT"], timeframes=["1h"], direction="LONG",
            should_cancel=flag,
        )
    )
    assert out["status"] == "CANCELLED"
    assert seen["should_cancel"] is flag


def test_job_vectorbt_gate_uses_the_child_and_survives_its_failure(monkeypatch):
    import app.research.backtest_job as bj

    assert bj._vectorbt_runner is bj._vectorbt_rows_in_child

    async def broken_child(data, *, should_cancel=None, heartbeat=None):
        raise RuntimeError("child died")

    monkeypatch.setattr(bj, "_vectorbt_runner", broken_child)
    job = bj.BacktestJob(job_id="vbt1", status="running", include_trades=False)
    rows = [{"symbol": "BTCUSDT", "trades": [{"trade_id": "t1"}]}]
    out = asyncio.run(
        bj.BacktestJobService()._apply_vectorbt(job, rows, strategy_id="COMBO_02", family="combo")
    )
    assert out[0]["execution_engine"] == "bar_loop"
    assert out[0]["vectorbt_parity"]["verdict"] == "ERROR"
    assert "child died" in out[0]["vectorbt_parity"]["reason"]
    assert out[0]["trades"] == []
    assert job.phase == bj.VECTORBT_REPLAY


def test_a_child_windows_could_not_start_is_started_again(monkeypatch):
    import app.research.research_child as rc

    calls: list[str] = []
    outcomes = [rc._ChildDidNotStart("0xC0000142"), rc._ChildDidNotStart("0xC0000142"), "row"]

    def once(target, payload_path, **kwargs):
        calls.append(target)
        out = outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(rc, "_run_child_once", once)
    monkeypatch.setattr(rc.time, "sleep", lambda s: None)
    assert rc.run_child_sync("m:f", "p.pkl") == "row"
    assert len(calls) == 3

    # A child that ran and failed is not retried; three failed starts give up.
    calls.clear()
    outcomes[:] = [rc.ResearchChildError("crashed")]
    with pytest.raises(rc.ResearchChildError, match="crashed"):
        rc.run_child_sync("m:f", "p.pkl")
    assert len(calls) == 1
    calls.clear()
    outcomes[:] = [rc._ChildDidNotStart("x")] * 3
    with pytest.raises(rc._ChildDidNotStart):
        rc.run_child_sync("m:f", "p.pkl")
    assert len(calls) == 3
