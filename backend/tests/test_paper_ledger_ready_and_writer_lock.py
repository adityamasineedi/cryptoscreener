"""No paper open before the ledger has loaded, and one paper writer per database.

No real database: the persistence object and the connection are fakes.
Standalone engines only. Nothing touches the live book.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import app.services.paper_trade as paper_mod
from app.engines.orchestrator import (
    PAPER_LEDGER_RETRY_SECONDS,
    PAPER_WRITER_LOCK_KEY,
    acquire_paper_writer_lock,
    load_paper_ledger,
    retry_paper_ledger_load,
    retry_paper_writer_lock,
)
from app.services.paper_risk import PaperRiskPolicy
from app.services.paper_trade import PaperPosition, PaperTradeEngine, ledger_dedupe_key
from app.services.persistence import PersistenceService

SYM = "BTCUSDT"
BAR = "2026-10-12T10:00:00+00:00"
# Tick-aligned plan (BTC default tick 0.1).
LONG = {"entry": 60000.0, "stop": 59000.0, "tp1": 62000.0}
PATHS = ["legacy", "v1", "passed", "v21a", "experimental"]


def _engine() -> PaperTradeEngine:
    eng = PaperTradeEngine(
        starting_equity=1000.0,
        enabled=True,
        entry_mode="path_b",
        risk_policy=PaperRiskPolicy(enabled=False),
    )
    eng.legacy_auto_entry_enabled = True
    return eng


def _long_eval() -> dict:
    return {
        "status": "LONG_ENTRY_CANDIDATE",
        "direction": "LONG",
        "entry_price": LONG["entry"],
        "stop_price": LONG["stop"],
        "tp1": LONG["tp1"],
        "htf": {"htf_alignment": "HTF_ALIGNED", "trend_1h": "BULLISH", "trend_4h": "BULLISH"},
    }


def _open(eng: PaperTradeEngine, path: str) -> PaperPosition | None:
    if path == "legacy":
        return eng.on_setup_signal(
            SYM,
            {
                "symbol": SYM,
                "status": "LONG_ENTRY_CANDIDATE",
                "direction": "LONG",
                "timeframe": "15m",
                "entry": {"entry_price": LONG["entry"]},
                "stop": {"final_stop": LONG["stop"]},
                "targets": [{"target_price": LONG["tp1"]}],
                "risk_reward": {"RISK_REWARD": "PASS"},
                "source_candle_timestamps": {"15m": BAR},
                "calculated_at": BAR,
                "ohlcv_freshness": "OK",
            },
        )
    if path == "v1":
        return eng.open_v1_combo_position(
            symbol=SYM,
            timeframe="1h",
            book=SimpleNamespace(tier="core", risk_percent=0.02),
            eval_result=_long_eval(),
            setup_bar_time_utc=BAR,
            emit_alert=False,
        )
    if path == "passed":
        return eng.open_passed_position(
            symbol=SYM,
            combination_id="COMBO_03_TRANSITION_B",
            timeframe="1h",
            direction="LONG",
            eval_result=_long_eval(),
            risk_percent=0.02,
            setup_bar_time_utc=BAR,
            market_direction="NEUTRAL",
        )
    if path == "v21a":
        return eng.open_v21a_position(
            symbol=SYM,
            timeframe="1h",
            eval_result=_long_eval(),
            risk_percent=0.02,
            signal_snippet={
                "strategy_id": "COMBO_02_V2_1_A",
                "source": "V21A_PAPER_WATCHER",
            },
            setup_bar_time_utc=BAR,
            emit_alert=False,
        )
    return eng.open_experimental_position(
        symbol=SYM,
        timeframe="1h",
        eval_result=_long_eval(),
        risk_percent=0.0025,
        signal_snippet={
            "strategy_id": "COMBO_02_V2_RESEARCH",
            "source": "V2_CANDIDATE_PAPER_WATCHER",
        },
        setup_bar_time_utc=BAR,
        emit_alert=False,
    )


@pytest.fixture(autouse=True)
def _no_live_price(monkeypatch):
    monkeypatch.setattr(paper_mod, "_live_price_with_source", lambda _s: (None, "unavailable"))


# --- engine flags -------------------------------------------------------------


def test_standalone_engine_defaults_to_ready_and_lock_held():
    eng = _engine()
    assert eng.ledger_ready is True
    assert eng.writer_lock_held is True
    status = eng.status()
    assert status["ledger_ready"] is True
    assert status["writer_lock_held"] is True


@pytest.mark.parametrize("path", PATHS)
def test_every_open_path_waits_for_the_ledger(path):
    eng = _engine()
    eng.ledger_ready = False
    assert _open(eng, path) is None
    assert eng._last_skip_reason == f"{SYM}:ledger_not_ready"
    status = eng.status()
    assert status["ledger_ready"] is False
    assert status["open_count"] == 0
    assert eng.pending_persist_count() == 0

    eng.ledger_ready = True
    pos = _open(eng, path)
    assert pos is not None
    assert (pos.entry_price, pos.stop_price, pos.tp1_price) == (
        LONG["entry"],
        LONG["stop"],
        LONG["tp1"],
    )
    assert eng.status()["ledger_ready"] is True


@pytest.mark.parametrize("path", PATHS)
def test_every_open_path_refuses_without_the_writer_lock(path):
    eng = _engine()
    eng.writer_lock_held = False
    assert _open(eng, path) is None
    assert eng._last_skip_reason == f"{SYM}:paper_writer_lock_not_held"
    status = eng.status()
    assert status["writer_lock_held"] is False
    assert status["open_count"] == 0
    assert eng.pending_persist_count() == 0

    eng.writer_lock_held = True
    assert _open(eng, path) is not None


# --- ledger load ----------------------------------------------------------------

LEDGER_KEY = ledger_dedupe_key(SYM, BAR, "V1", "1h")
OLD_KEY = ledger_dedupe_key(SYM, "2026-09-01T00:00:00+00:00", "V1", "1h")


def _open_row() -> dict:
    return {
        "id": "ledger-open-1",
        "symbol": SYM,
        "side": "LONG",
        "status": "OPEN",
        "entry_price": LONG["entry"],
        "stop_price": LONG["stop"],
        "tp1_price": LONG["tp1"],
        "quantity": 0.001,
        "risk_usd": 1.0,
        "opened_at": "2026-10-12T11:01:00+00:00",
        "source_candle_ts": BAR,
        "timeframe": "1h",
        "signal_snippet": {"source": "V1_PAPER_WATCHER", "strategy_id": "COMBO_02_V1"},
    }


class _FakeStore:
    """Persistence stand-in. ``fail`` maps a method name to how many calls raise first."""

    def __init__(self, **fail: int) -> None:
        self.fail = dict(fail)
        self.calls: list[str] = []

    def _step(self, name: str, raise_on_error: bool) -> None:
        # The hydrate path must ask for errors, or a failed query looks like an empty ledger.
        assert raise_on_error is True
        self.calls.append(name)
        if self.fail.get(name, 0) > 0:
            self.fail[name] -= 1
            raise RuntimeError(f"{name} failed")

    async def load_paper_trades(self, *, closed_limit: int = 200, raise_on_error: bool = False):
        self._step("trades", raise_on_error)
        return [_open_row()]

    async def sum_closed_paper_pnl(self, *, raise_on_error: bool = False):
        self._step("pnl", raise_on_error)
        return 12.5

    async def load_paper_dedupe_keys(self, *, raise_on_error: bool = False):
        self._step("keys", raise_on_error)
        return [OLD_KEY]


async def test_successful_load_hydrates_the_book_and_marks_it_ready():
    eng = _engine()
    eng.ledger_ready = False
    assert await load_paper_ledger(eng, _FakeStore()) is True
    assert eng.ledger_ready is True
    assert eng.has_open(SYM, "V1")
    assert eng.realized_pnl == 12.5
    assert {LEDGER_KEY, OLD_KEY} <= eng._opened_keys


@pytest.mark.parametrize("failing", ["trades", "pnl", "keys"])
async def test_failed_load_leaves_the_book_not_ready_and_untouched(failing):
    eng = _engine()
    eng.ledger_ready = False
    store = _FakeStore(**{failing: 1})
    assert await load_paper_ledger(eng, store) is False
    assert eng.ledger_ready is False
    # Nothing half-loaded: the next attempt still hydrates into an empty book.
    assert eng.status()["open_count"] == 0
    assert eng._opened_keys == set()
    assert _open(eng, "v1") is None
    assert eng._last_skip_reason == f"{SYM}:ledger_not_ready"

    assert await load_paper_ledger(eng, store) is True
    assert eng.ledger_ready is True
    assert eng.has_open(SYM, "V1")
    assert {LEDGER_KEY, OLD_KEY} <= eng._opened_keys


async def test_retry_reloads_until_the_ledger_is_ready():
    assert PAPER_LEDGER_RETRY_SECONDS == 30.0
    eng = _engine()
    eng.ledger_ready = False
    # Fails on the first attempt and on the first retry.
    store = _FakeStore(trades=2)
    assert await load_paper_ledger(eng, store) is False
    assert eng.ledger_ready is False

    await retry_paper_ledger_load(eng, store, interval=0)
    assert eng.ledger_ready is True
    assert eng.has_open(SYM, "V1")
    assert store.calls == ["trades", "trades", "trades", "pnl", "keys"]
    # The bar the ledger already holds stays refused once opens are allowed.
    assert _open(eng, "v1") is None


async def test_retry_does_nothing_once_ready_and_stops_with_the_orchestrator():
    eng = _engine()
    store = _FakeStore()
    await retry_paper_ledger_load(eng, store, interval=0)
    assert store.calls == []

    eng.ledger_ready = False
    await retry_paper_ledger_load(eng, store, interval=0, keep_running=lambda: False)
    assert store.calls == []
    assert eng.ledger_ready is False


class _BrokenEngine:
    def begin(self):
        raise RuntimeError("connection refused")


async def test_persistence_loads_raise_only_when_asked(monkeypatch):
    import app.services.persistence as persistence_mod

    store = PersistenceService()
    # Database off: empty answers, as before.
    monkeypatch.setattr(persistence_mod.db_manager, "enabled", False)
    monkeypatch.setattr(persistence_mod.db_manager, "engine", None)
    assert await store.load_paper_trades(raise_on_error=True) == []
    assert await store.sum_closed_paper_pnl(raise_on_error=True) is None
    assert await store.load_paper_dedupe_keys(raise_on_error=True) == []

    # Database on but the query fails.
    monkeypatch.setattr(persistence_mod.db_manager, "enabled", True)
    monkeypatch.setattr(persistence_mod.db_manager, "engine", _BrokenEngine())
    # Existing callers keep the old return values.
    assert await store.load_paper_trades() == []
    assert await store.sum_closed_paper_pnl() is None
    assert await store.load_paper_dedupe_keys() == []
    with pytest.raises(RuntimeError):
        await store.load_paper_trades(raise_on_error=True)
    with pytest.raises(RuntimeError):
        await store.sum_closed_paper_pnl(raise_on_error=True)
    with pytest.raises(RuntimeError):
        await store.load_paper_dedupe_keys(raise_on_error=True)


# --- single-writer lock -----------------------------------------------------------


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeConn:
    def __init__(self, *, lock_free: bool, fail: bool = False) -> None:
        self.lock_free = lock_free
        self.fail = fail
        self.statements: list[tuple[str, dict]] = []
        self.committed = False
        self.closed = False

    async def execute(self, statement: Any, params: dict | None = None) -> _FakeResult:
        if self.fail:
            raise RuntimeError("server closed the connection")
        self.statements.append((str(statement), dict(params or {})))
        return _FakeResult(self.lock_free)

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        self.closed = True


class _FakeDb:
    def __init__(self, conn: _FakeConn | None, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.conn = conn
        self.engine = self if conn is not None else None
        self.connects = 0

    async def connect(self) -> _FakeConn:
        self.connects += 1
        return self.conn


async def test_no_lock_is_needed_when_the_database_is_off():
    held, conn = await acquire_paper_writer_lock(_FakeDb(None, enabled=False))
    assert held is None and conn is None
    # Enabled flag without an engine is the same: nothing to lock on.
    held, conn = await acquire_paper_writer_lock(_FakeDb(None, enabled=True))
    assert held is None and conn is None
    # Database off even though an engine object exists: no connection is opened.
    db = _FakeDb(_FakeConn(lock_free=True), enabled=False)
    held, conn = await acquire_paper_writer_lock(db)
    assert held is None and conn is None
    assert db.connects == 0


async def test_free_lock_is_taken_on_a_connection_that_stays_open():
    fake = _FakeConn(lock_free=True)
    held, conn = await acquire_paper_writer_lock(_FakeDb(fake))
    assert held is True
    assert conn is fake
    assert fake.closed is False
    assert fake.committed is True
    assert fake.statements == [
        ("SELECT pg_try_advisory_lock(:key)", {"key": PAPER_WRITER_LOCK_KEY})
    ]
    assert isinstance(PAPER_WRITER_LOCK_KEY, int)
    assert -(2**63) <= PAPER_WRITER_LOCK_KEY < 2**63


async def test_taken_lock_reports_not_held_and_releases_the_connection():
    fake = _FakeConn(lock_free=False)
    held, conn = await acquire_paper_writer_lock(_FakeDb(fake))
    assert held is False
    assert conn is None
    assert fake.closed is True


async def test_lock_error_with_the_database_on_fails_closed():
    fake = _FakeConn(lock_free=True, fail=True)
    held, conn = await acquire_paper_writer_lock(_FakeDb(fake))
    assert held is False
    assert conn is None
    assert fake.closed is True

    class _NoConnect:
        enabled = True

        def __init__(self) -> None:
            self.engine = self

        async def connect(self):
            raise RuntimeError("too many connections")

    held, conn = await acquire_paper_writer_lock(_NoConnect())
    assert held is False and conn is None


def test_engine_flag_follows_the_lock_outcome():
    # The orchestrator sets: writer_lock_held = (held is not False).
    for held, expected in ((None, True), (True, True), (False, False)):
        eng = _engine()
        eng.writer_lock_held = held is not False
        assert eng.writer_lock_held is expected


async def test_lock_is_retried_on_the_same_connection_until_the_old_holder_lets_go():
    class _FreesOnThird(_FakeConn):
        async def execute(self, statement: Any, params: dict | None = None) -> _FakeResult:
            self.statements.append((str(statement), dict(params or {})))
            return _FakeResult(len(self.statements) >= 3)

    fake = _FreesOnThird(lock_free=False)
    db = _FakeDb(fake)
    held, conn = await acquire_paper_writer_lock(db, attempts=5, interval=0.0)
    assert held is True
    assert conn is fake
    assert len(fake.statements) == 3
    assert fake.closed is False


async def test_retry_takes_the_lock_after_the_holder_leaves():
    calls = {"n": 0}

    class _Db:
        enabled = True
        engine = True

        async def connect(self) -> _FakeConn:
            calls["n"] += 1
            return _FakeConn(lock_free=calls["n"] >= 2)

    eng = _engine()
    eng.writer_lock_held = False
    saved: dict[str, Any] = {}
    await retry_paper_writer_lock(
        eng,
        _Db(),
        interval=0,
        on_acquired=lambda conn: saved.__setitem__("conn", conn),
    )
    assert eng.writer_lock_held is True
    assert calls["n"] == 2
    assert saved["conn"].closed is False


async def test_retry_stops_when_the_process_stops():
    eng = _engine()
    eng.writer_lock_held = False
    db = _FakeDb(_FakeConn(lock_free=True))
    await retry_paper_writer_lock(
        eng, db, interval=0, keep_running=lambda: False
    )
    assert eng.writer_lock_held is False
    assert db.connects == 0


async def test_lock_gives_up_after_the_last_attempt():
    fake = _FakeConn(lock_free=False)
    held, conn = await acquire_paper_writer_lock(_FakeDb(fake), attempts=3, interval=0.0)
    assert held is False and conn is None
    assert len(fake.statements) == 3
    assert fake.closed is True
