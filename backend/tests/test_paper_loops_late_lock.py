"""P11 (owner ruling D41): a process that gets the paper writer lock late starts its paper loops then.

Before P11 the paper loops started only when the lock was held at startup. A copy that took the lock
later (retry_paper_writer_lock) held it, so no other copy could write, yet ran no paper loop.

The replay test from the proposal: two processes; the holder dies; the second takes the lock and runs
the same paper loops and journal as a single fresh process. The decision code is untouched by P11, so
the same loops on the same stored bars give the same decisions.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import app.engines.orchestrator as orch
from app.engines.orchestrator import (
    CalculationOrchestrator,
    acquire_paper_writer_lock,
    release_paper_writer_lock,
    retry_paper_writer_lock,
)

LOOPS = (
    "_paper_trade_loop",
    "_v1_paper_watcher_loop",
    "_v21a_paper_watcher_loop",
    "_passed_paper_watcher_loop",
    "_candle12_v2_paper_loop",
    "_book16_paper_loop",
)


class _Lock:
    """One Postgres advisory lock shared by several fake processes."""

    def __init__(self) -> None:
        self.holder: Any = None


class _Conn:
    def __init__(self, lock: _Lock) -> None:
        self.lock = lock
        self.closed = False

    async def execute(self, statement: Any, params: dict | None = None) -> Any:
        free = self.lock.holder is None or self.lock.holder is self
        if free:
            self.lock.holder = self
        return SimpleNamespace(scalar=lambda: free)

    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        if self.lock.holder is self:
            self.lock.holder = None


class _Db:
    enabled = True

    def __init__(self, lock: _Lock) -> None:
        self.lock = lock
        self.engine = self

    async def connect(self) -> _Conn:
        return _Conn(self.lock)


def _process(monkeypatch: pytest.MonkeyPatch, journal: list, **flags: bool) -> CalculationOrchestrator:
    """An orchestrator with only what the paper-loop start uses; each loop just waits."""
    o = object.__new__(CalculationOrchestrator)
    o.settings = SimpleNamespace(
        paper_v1_watcher_enabled=flags.get("v1", True),
        paper_v21a_watcher_enabled=flags.get("v21a", False),
        paper_passed_watcher_enabled=flags.get("passed", True),
        paper_candle12_v2_btc_long_enabled=flags.get("v2", True),
        paper_book16_4h_enabled=flags.get("book16", False),
    )
    o._tasks = []
    o._running = True
    o._paper_writer_lock_conn = None
    o._paper_loops_started = False
    o._paper_loop_tasks = {}
    for name in LOOPS:
        async def _loop() -> None:
            await asyncio.sleep(3600)

        setattr(o, name, _loop)
    from app.services import decision_journal

    monkeypatch.setattr(decision_journal, "configure", lambda path: journal.append(str(path)))
    return o


def _stop(o: CalculationOrchestrator) -> None:
    o._running = False
    for t in o._tasks:
        t.cancel()


async def test_late_lock_starts_the_same_loops_and_journal_as_a_fresh_process(monkeypatch):
    lock = _Lock()
    db = _Db(lock)
    paper_a, paper_b, paper_c = (SimpleNamespace(writer_lock_held=False) for _ in range(3))

    # Process A holds the lock and runs its loops.
    journal_a: list = []
    a = _process(monkeypatch, journal_a)
    held, conn_a = await acquire_paper_writer_lock(db)
    assert held is True
    paper_a.writer_lock_held = True
    a._paper_writer_lock_conn = conn_a
    started_a = a._start_paper_loops(paper_a)

    # Process B starts while A holds it: no lock, no loops, retry running.
    journal_b: list = []
    b = _process(monkeypatch, journal_b)
    held, _ = await acquire_paper_writer_lock(db)
    assert held is False
    assert b._paper_loop_tasks == {} and journal_b == []
    retry = asyncio.create_task(
        retry_paper_writer_lock(paper_b, db, interval=0, on_acquired=lambda c: b._on_paper_writer_lock(paper_b, c))
    )
    await asyncio.sleep(0.01)
    assert paper_b.writer_lock_held is False and b._paper_loop_tasks == {}

    # A dies: its session closes and the lock is free. B takes it and starts its loops.
    _stop(a)
    await conn_a.close()
    await asyncio.wait_for(retry, 2)
    assert paper_b.writer_lock_held is True
    assert lock.holder is b._paper_writer_lock_conn

    # A single fresh process with the same settings (the reference).
    journal_c: list = []
    c = _process(monkeypatch, journal_c)
    paper_c.writer_lock_held = True
    started_c = c._start_paper_loops(paper_c)

    assert sorted(b._paper_loop_tasks) == sorted(started_c) == sorted(started_a)
    assert journal_b == journal_c and len(journal_b) == 1
    assert all(not t.done() for t in b._paper_loop_tasks.values())
    assert set(started_c) == {"paper_trade_loop", "v1_paper_watcher_loop", "passed_paper_watcher_loop", "candle12_v2_paper_loop"}
    for o in (b, c):
        _stop(o)


async def test_loops_start_once_only(monkeypatch):
    journal: list = []
    o = _process(monkeypatch, journal)
    paper = SimpleNamespace(writer_lock_held=True)
    first = o._start_paper_loops(paper)
    assert first and o._start_paper_loops(paper) == []
    assert len(o._paper_loop_tasks) == len(first) and len(journal) == 1
    _stop(o)


async def test_a_failed_loop_start_gives_the_lock_back_and_retries(monkeypatch):
    lock = _Lock()
    db = _Db(lock)
    paper = SimpleNamespace(writer_lock_held=False)
    conns: list = []
    calls = {"n": 0}

    async def on_acquired(conn: Any) -> None:
        conns.append(conn)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("loop start failed")

    await asyncio.wait_for(retry_paper_writer_lock(paper, db, interval=0, on_acquired=on_acquired), 2)
    # First grant refused: the lock was released (session closed), not held without loops.
    assert calls["n"] == 2
    assert conns[0].closed is True
    assert conns[1].closed is False and lock.holder is conns[1]
    assert paper.writer_lock_held is True


async def test_release_clears_the_flag_and_frees_the_lock():
    lock = _Lock()
    held, conn = await acquire_paper_writer_lock(_Db(lock))
    paper = SimpleNamespace(writer_lock_held=bool(held))
    await release_paper_writer_lock(paper, conn, reason="test")
    assert paper.writer_lock_held is False and lock.holder is None


async def test_sync_on_acquired_still_works():
    lock = _Lock()
    paper = SimpleNamespace(writer_lock_held=False)
    saved: dict = {}
    await asyncio.wait_for(
        retry_paper_writer_lock(paper, _Db(lock), interval=0, on_acquired=lambda c: saved.setdefault("conn", c)), 2
    )
    assert paper.writer_lock_held is True and saved["conn"].closed is False


async def test_hourly_paper_loops_running_log(monkeypatch):
    journal: list = []
    o = _process(monkeypatch, journal)
    lines: list = []
    monkeypatch.setattr(orch.logger, "info", lambda event, **kw: lines.append((event, kw)))
    monkeypatch.setattr(orch, "PAPER_LOOPS_HEARTBEAT_SECONDS", 0.01)
    paper = SimpleNamespace(writer_lock_held=True)
    o._start_paper_loops(paper)
    await asyncio.sleep(0.05)
    beats = [kw for event, kw in lines if event == "paper_loops_running"]
    assert len(beats) >= 2
    assert beats[0]["writer_lock_held"] is True
    assert set(beats[0]["loops"]) == set(o._paper_loop_tasks) and all(beats[0]["loops"].values())
    assert orch.PAPER_LOOPS_HEARTBEAT_SECONDS == 0.01
    _stop(o)
