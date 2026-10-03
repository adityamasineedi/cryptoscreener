"""Schema startup must complete on plain Postgres without hanging forever."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.services.database import DatabaseManager, _SCHEMA_STATEMENTS


@pytest.mark.asyncio
async def test_timescale_failure_falls_back_and_continues_ddl():
    mgr = DatabaseManager()
    mgr.enabled = True

    calls: list[str] = []

    class _Conn:
        async def execute(self, stmt, *args, **kwargs):
            sql = str(getattr(stmt, "text", stmt))
            calls.append(sql)
            if "timescaledb" in sql.lower() and "CREATE EXTENSION" in sql.upper():
                raise OperationalError("stmt", {}, Exception("extension missing"))
            return MagicMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Engine:
        def begin(self):
            return _Conn()

        def connect(self):
            return _Conn()

    mgr.engine = _Engine()  # type: ignore[assignment]
    await mgr.ensure_schema()
    assert mgr.timescale is False
    assert mgr.schema_ready is True
    assert any("CREATE TABLE" in c.upper() for c in calls)
    stages = {s["startup_stage"]: s["status"] for s in mgr.startup_stages}
    assert stages.get("TIMESCALE_CHECK") == "FALLBACK"
    assert stages.get("SCHEMA_INIT") == "OK"


@pytest.mark.asyncio
async def test_schema_init_is_idempotent_second_pass():
    mgr = DatabaseManager()
    mgr.enabled = True

    class _Conn:
        async def execute(self, stmt, *args, **kwargs):
            return MagicMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Engine:
        def begin(self):
            return _Conn()

        def connect(self):
            return _Conn()

    mgr.engine = _Engine()  # type: ignore[assignment]
    await mgr.ensure_schema()
    first_ready = mgr.schema_ready
    await mgr.ensure_schema()
    assert first_ready is True
    assert mgr.schema_ready is True
    assert mgr.last_schema_error is None


@pytest.mark.asyncio
async def test_lock_timeout_on_index_does_not_hang_forever():
    mgr = DatabaseManager()
    mgr.enabled = True
    sleep = AsyncMock()

    class _Conn:
        async def execute(self, stmt, *args, **kwargs):
            sql = str(getattr(stmt, "text", stmt))
            if "CREATE INDEX" in sql.upper():
                raise OperationalError(
                    "stmt", {}, Exception("canceling statement due to lock timeout")
                )
            return MagicMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Engine:
        def begin(self):
            return _Conn()

        def connect(self):
            return _Conn()

    mgr.engine = _Engine()  # type: ignore[assignment]
    with patch.object(mgr, "_log_blocking_activity", new=AsyncMock(return_value=[])):
        await asyncio.wait_for(mgr.ensure_schema(), timeout=2.0)
    assert mgr.schema_ready is False
    assert mgr.last_schema_error
    assert "lock timeout" in mgr.last_schema_error.lower() or "INDEX" in mgr.last_schema_error
    # Must not sleep/retry forever
    sleep.assert_not_called()


@pytest.mark.asyncio
async def test_diagnostic_and_research_statements_included():
    joined = "\n".join(_SCHEMA_STATEMENTS).lower()
    assert "diagnostic_issues" in joined
    assert "research_runs" in joined
    assert "create table if not exists" in joined
    assert "create index if not exists" in joined


@pytest.mark.asyncio
async def test_connect_records_db_connect_stage_on_failure():
    mgr = DatabaseManager()
    settings = MagicMock()
    settings.database_enabled = True
    settings.database_url = "postgresql+asyncpg://bad:bad@127.0.0.1:1/none"
    await asyncio.wait_for(mgr.connect(settings), timeout=5.0)
    assert mgr.enabled is False
    assert any(s["startup_stage"] == "DB_CONNECT" for s in mgr.startup_stages)


@pytest.mark.asyncio
async def test_websocket_starts_after_db_in_lifespan_order():
    """Lifespan must call db connect before ingestion/ws start."""
    order: list[str] = []

    class _DB:
        schema_ready = True
        status = "ok"
        last_schema_error = None
        startup_stages: list = []
        timescale = False

        async def connect(self, settings):
            order.append("db")

        async def close(self):
            order.append("db_close")

    class _WSMon:
        async def start(self):
            order.append("ws_mon")

        async def stop(self):
            order.append("ws_mon_stop")

    class _Ingestion:
        async def start(self):
            order.append("ingestion")

        async def stop(self):
            order.append("ingestion_stop")

    fake_ws = MagicMock()
    fake_ws.event_loop = _WSMon()

    import app.ingestion.ws_forensics as ws_mod

    with patch("app.main.db_manager", _DB()), patch(
        "app.main.redis_manager"
    ) as redis, patch("app.main.market_store") as store, patch(
        "app.main.MarketDataIngestionService", return_value=_Ingestion()
    ), patch.object(
        ws_mod, "ws_forensics", fake_ws
    ), patch(
        "app.main.get_settings"
    ) as gs:
        redis.connect = AsyncMock()
        redis.close = AsyncMock()
        redis.client = None
        store.connect_redis = AsyncMock()
        settings = MagicMock()
        settings.use_real_data = True
        settings.redis_enabled = False
        settings.database_enabled = True
        settings.log_level = "INFO"
        settings.cors_origins = ["*"]
        gs.return_value = settings

        from app.main import lifespan
        from fastapi import FastAPI

        app = FastAPI()
        async with lifespan(app):
            pass

    assert order[:3] == ["db", "ws_mon", "ingestion"]
