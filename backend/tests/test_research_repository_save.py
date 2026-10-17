"""save_research_run binds TIMESTAMPTZ params the way asyncpg requires.

No database is touched: a fake engine enforces asyncpg's rule that a
parameter used in ``CAST(:x AS TIMESTAMPTZ)`` must be a datetime, not a str.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from asyncpg.exceptions import DataError

from app.research import repository, service
from app.research.repository import _MEMORY_RUNS, _as_timestamptz, memory_clear, save_research_run

_TIMESTAMPTZ_PARAM = re.compile(r"CAST\(:(\w+) AS TIMESTAMPTZ\)")


class _AsyncpgStyleConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> None:
        sql = str(stmt)
        params = params or {}
        for name in _TIMESTAMPTZ_PARAM.findall(sql):
            value = params.get(name)
            if value is not None and not isinstance(value, datetime):
                # Same failure asyncpg raises for a str bound to timestamptz.
                raise DataError(
                    f"invalid input for query argument :{name}: {value!r} "
                    f"(expected a datetime.date or datetime.datetime instance, "
                    f"got '{type(value).__name__}')"
                )
        self.calls.append((sql, params))


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _AsyncpgStyleConn()

    @asynccontextmanager
    async def begin(self):
        yield self.conn


@pytest.fixture(autouse=True)
def _clear_research_memory():
    memory_clear()
    yield
    memory_clear()


@pytest.fixture
def fake_engine(monkeypatch) -> _FakeEngine:
    engine = _FakeEngine()

    async def _schema_ok() -> bool:
        return True

    monkeypatch.setattr(repository.db_manager, "enabled", True)
    monkeypatch.setattr(repository.db_manager, "engine", engine)
    monkeypatch.setattr(repository, "ensure_research_schema", _schema_ok)
    return engine


def _params_for(engine: _FakeEngine, table: str) -> list[dict[str, Any]]:
    return [p for sql, p in engine.conn.calls if f"INSERT INTO {table}" in sql]


async def test_fake_conn_rejects_iso_string_like_asyncpg() -> None:
    conn = _AsyncpgStyleConn()
    with pytest.raises(DataError):
        await conn.execute(
            "INSERT INTO t VALUES (CAST(:created_at AS TIMESTAMPTZ))",
            {"created_at": "2026-10-17T00:00:00+00:00"},
        )


async def test_save_binds_created_at_and_signal_time_as_aware_datetimes(fake_engine) -> None:
    run_id = await save_research_run(
        {
            "run_id": "run-1",
            "created_at": "2026-10-17T12:34:56.789000+00:00",
            "results": [{"combination_id": "COMBO_01", "sample_size": 3}],
            "trades": [
                {
                    "combination_id": "COMBO_01",
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "direction": "LONG",
                    "signal_time": "2026-01-02T03:00:00Z",
                },
                {
                    "combination_id": "COMBO_01",
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "direction": "LONG",
                    "signal_time": None,
                },
            ],
        }
    )

    assert run_id == "run-1"
    (run_params,) = _params_for(fake_engine, "research_runs")
    assert run_params["created_at"] == datetime(
        2026, 10, 17, 12, 34, 56, 789000, tzinfo=timezone.utc
    )
    assert len(_params_for(fake_engine, "research_results")) == 1
    trade_times = [p["signal_time"] for p in _params_for(fake_engine, "research_trades")]
    assert trade_times == [datetime(2026, 1, 2, 3, tzinfo=timezone.utc), None]
    # Payload keeps the caller's original ISO string.
    assert _MEMORY_RUNS["run-1"]["created_at"] == "2026-10-17T12:34:56.789000+00:00"


async def test_save_without_created_at_binds_now(fake_engine) -> None:
    before = datetime.now(timezone.utc)
    await save_research_run({"run_id": "run-2"})
    (run_params,) = _params_for(fake_engine, "research_runs")
    created = run_params["created_at"]
    assert isinstance(created, datetime) and created.tzinfo is not None
    assert before <= created <= datetime.now(timezone.utc)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("not-a-date", None),
        ("2026-10-17T00:00:00+00:00", datetime(2026, 10, 17, tzinfo=timezone.utc)),
        ("2026-10-17T00:00:00Z", datetime(2026, 10, 17, tzinfo=timezone.utc)),
        ("2026-10-17T00:00:00", datetime(2026, 10, 17, tzinfo=timezone.utc)),
        (
            "2026-10-17T05:30:00+05:30",
            datetime(2026, 10, 17, tzinfo=timezone.utc),
        ),
        (datetime(2026, 10, 17), datetime(2026, 10, 17, tzinfo=timezone.utc)),
        (1_791_763_200, datetime(2026, 10, 12, tzinfo=timezone.utc)),
        (1_791_763_200_000, datetime(2026, 10, 12, tzinfo=timezone.utc)),
    ],
)
def test_as_timestamptz(raw: Any, expected: datetime | None) -> None:
    got = _as_timestamptz(raw)
    assert got == expected
    if got is not None:
        assert got.utcoffset() is not None


def test_as_timestamptz_keeps_offset() -> None:
    got = _as_timestamptz("2026-10-17T05:30:00+05:30")
    assert got is not None and got.utcoffset() == timedelta(hours=5, minutes=30)


def _stub_backtest_inputs(monkeypatch) -> None:
    monkeypatch.setattr(service, "clear_candle_cache", lambda: None)
    monkeypatch.setattr(service, "_load_candles", lambda *a, **k: [])
    monkeypatch.setattr(service, "get_combination", lambda _cid: None)
    monkeypatch.setattr(service, "_load_htf_candles_sync", lambda *a, **k: (None, None))
    monkeypatch.setattr(service, "_signal_config", lambda: None)
    monkeypatch.setattr(service, "_market_cap", lambda _s: None)
    monkeypatch.setattr(
        service,
        "run_combination_backtest",
        lambda *a, **k: {"result": None, "trades": [], "elapsed_seconds": 0.0},
    )


def test_service_db_failure_falls_back_to_memory_and_warns(monkeypatch) -> None:
    _stub_backtest_inputs(monkeypatch)
    attempts: list[str] = []

    async def _failing_save(summary: dict[str, Any]) -> str:
        attempts.append(summary["run_id"])
        raise DataError("invalid input for query argument $2")

    fake_logger = MagicMock()
    monkeypatch.setattr(service, "save_research_run", _failing_save)
    monkeypatch.setattr(service, "logger", fake_logger)

    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        out = service.BosResearchService().run_backtest(
            "COMBO_01", symbol="BTCUSDT", timeframe="1h"
        )
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    run_id = out["run_id"]
    assert attempts == [run_id], "DB save must actually be attempted"
    assert run_id in _MEMORY_RUNS
    fake_logger.warning.assert_called_once()
    event = fake_logger.warning.call_args.args[0]
    kwargs = fake_logger.warning.call_args.kwargs
    assert event == "research_run_db_save_failed_memory_fallback"
    assert kwargs["run_id"] == run_id
    assert kwargs["error_type"] == "DataError"
