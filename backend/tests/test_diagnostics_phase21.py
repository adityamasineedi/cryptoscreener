"""Phase 2.1 — DB diagnostics performance hardening."""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.diagnostics.constants import HealthStatus
from app.diagnostics.collectors import database as db_mod


@pytest.fixture(autouse=True)
def _clear_detail_cache():
    db_mod.reset_database_detail_cache()
    yield
    db_mod.reset_database_detail_cache()


def test_fast_collector_source_has_no_heavy_sql():
    src = inspect.getsource(db_mod.collect_database_fast)
    for marker in db_mod.HEAVY_SQL_MARKERS:
        assert marker not in src, f"fast collector must not contain {marker}"


def test_overview_service_uses_fast_collector():
    src = inspect.getsource(db_mod)
    # service.database must call collect_database_fast — verified via import wiring
    from app.diagnostics import service as svc_mod

    svc_src = inspect.getsource(svc_mod.DiagnosticService.database)
    assert "collect_database_fast" in svc_src
    assert "collect_database_detail_uncached" not in svc_src
    assert "get_database_detail" not in svc_src


@pytest.mark.asyncio
async def test_fast_collector_disabled():
    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"
    with patch("app.services.database.db_manager") as db:
        db.enabled = False
        db.engine = None
        out = await db_mod.collect_database_fast(settings)
    assert out["mode"] == "fast"
    assert out["status"] == HealthStatus.DISABLED.value
    assert out["tables"] if "tables" in out else True  # tables absent on fast is OK
    assert "tables" not in out or out.get("tables") in (None, [])


@pytest.mark.asyncio
async def test_detail_cache_avoids_repeated_heavy_collection():
    settings = MagicMock()
    settings.diagnostics_db_detail_cache_seconds = 60
    settings.diag_db_long_query_seconds = 30
    settings.diag_db_pool_warning_pct = 80
    settings.diag_db_pool_error_pct = 95
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"

    call_count = {"n": 0}

    async def fake_uncached(s):
        call_count["n"] += 1
        return {
            "phase": 2.1,
            "mode": "detail",
            "status": "HEALTHY",
            "reason": "fake",
            "collection_duration_ms": 12000.0,
            "tables": [{"table": "ohlcv"}],
            "issues_candidates": [],
            "timings": {"table_sizes_ms": 11000.0},
            "slowest_queries": [{"name": "table_sizes_ms", "duration_ms": 11000.0}],
        }

    with patch.object(db_mod, "collect_database_detail_uncached", side_effect=fake_uncached):
        a = await db_mod.get_database_detail(settings, force_refresh=False)
        b = await db_mod.get_database_detail(settings, force_refresh=False)
        c = await db_mod.get_database_detail(settings, force_refresh=True)

    assert call_count["n"] == 2  # first + force refresh
    assert a["cached"] is False
    assert b["cached"] is True
    assert b["cache_age_seconds"] is not None
    assert b["cache_age_seconds"] >= 0
    assert c["cached"] is False
    assert c["force_refresh"] is True


@pytest.mark.asyncio
async def test_detail_cache_expiry_triggers_refresh():
    settings = MagicMock()
    settings.diagnostics_db_detail_cache_seconds = 1
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"
    call_count = {"n": 0}

    async def fake_uncached(s):
        call_count["n"] += 1
        return {
            "status": "HEALTHY",
            "collection_duration_ms": 100.0,
            "issues_candidates": [],
        }

    with patch.object(db_mod, "collect_database_detail_uncached", side_effect=fake_uncached):
        await db_mod.get_database_detail(settings)
        # Expire cache artificially
        assert db_mod._detail_cache._collected_at is not None
        db_mod._detail_cache._collected_at = datetime.now(timezone.utc) - timedelta(
            seconds=5
        )
        out = await db_mod.get_database_detail(settings, force_refresh=False)

    assert call_count["n"] == 2
    assert out["cached"] is False


@pytest.mark.asyncio
async def test_table_sizes_failure_isolated():
    """One failed section must not fail the whole detail collector."""
    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"
    settings.diag_db_long_query_seconds = 30
    settings.diag_db_pool_warning_pct = 80
    settings.diag_db_pool_error_pct = 95

    class FakeResult:
        def __init__(self, value):
            self._value = value

        def scalar(self):
            return self._value

        def one(self):
            return self._value

        def fetchall(self):
            return self._value

        def one_or_none(self):
            return self._value

    class FakeConn:
        def __init__(self):
            self.calls = 0

        async def execute(self, statement, params=None):
            sql = str(statement)
            self.calls += 1
            if "SELECT 1" in sql:
                return FakeResult(1)
            if "pg_stat_activity" in sql and "FILTER" in sql:
                return FakeResult((1, 2, 0, 3))
            if "max_connections" in sql:
                return FakeResult("100")
            if "pg_database_size" in sql:
                return FakeResult(12345)
            if "pg_locks" in sql and "NOT granted" in sql:
                return FakeResult(0)
            if "make_interval" in sql:
                return FakeResult([])
            if "blocked.pid" in sql or "JOIN pg_locks" in sql:
                return FakeResult([])
            if "xact_start" in sql:
                return FakeResult(1.5)
            if "pg_total_relation_size" in sql or "pg_stat_user_tables" in sql:
                raise RuntimeError("permission denied for pg_class")
            if "pg_tables" in sql:
                raise RuntimeError("permission denied for pg_tables")
            return FakeResult([])

    class FakeBegin:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *args):
            return False

    class FakeEngine:
        def __init__(self):
            self.pool = MagicMock()
            self.pool.size.return_value = 5
            self.pool.checkedout.return_value = 1
            self.pool.checkedin.return_value = 4
            self.pool.overflow.return_value = 0
            self._conn = FakeConn()

        def begin(self):
            return FakeBegin(self._conn)

    with patch("app.services.database.db_manager") as db:
        db.enabled = True
        db.engine = FakeEngine()
        out = await db_mod.collect_database_detail_uncached(settings)

    assert out["status"] == HealthStatus.HEALTHY.value  # connection still OK
    assert out["table_sizes"]["status"] == HealthStatus.UNKNOWN.value
    assert "INSUFFICIENT_PERMISSION" in (out["table_sizes"].get("reason") or "")
    assert out["latency_ms"] is not None
    assert isinstance(out["latency_ms"], (int, float))
    assert out["timings"]["table_sizes_ms"] is not None
    # Unknown metrics stay null, not fabricated zeros for missing table list
    assert out["tables"] == []


def _fast_fake_engine(*, checked_out: int = 0, pool_size: int = 5):
    class FakeResult:
        def __init__(self, value):
            self._value = value

        def one(self):
            return self._value

    class FakeConn:
        async def execute(self, statement, params=None):
            # ok, active, idle, waiting, total, max_conn, db_size, locks
            return FakeResult((1, 1, 2, 0, 3, 100, 999, 0))

    class FakeBegin:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, *args):
            return False

    class FakeEngine:
        def __init__(self):
            self.pool = MagicMock()
            self.pool.size.return_value = pool_size
            self.pool.checkedout.return_value = checked_out
            self.pool.checkedin.return_value = max(pool_size - checked_out, 0)
            self.pool.overflow.return_value = 0

        def begin(self):
            return FakeBegin(FakeConn())

    return FakeEngine()


@pytest.mark.asyncio
async def test_timings_are_measured_not_faked():
    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"
    settings.diag_db_pool_warning_pct = 80
    settings.diag_db_pool_error_pct = 95

    with patch("app.services.database.db_manager") as db:
        db.enabled = True
        db.engine = _fast_fake_engine()
        out = await db_mod.collect_database_fast(settings)

    assert out["collection_duration_ms"] is not None
    assert out["collection_duration_ms"] >= 0
    assert out["timings"]["connection_check_ms"] is not None
    assert out["timings"]["connection_check_ms"] >= 0
    assert out["collection_duration_ms"] != 500
    assert out["latency_ms"] == out["timings"]["connection_check_ms"]
    assert out["database_size_bytes"] == 999


@pytest.mark.asyncio
async def test_issue_detection_still_works_on_fast_pool():
    from app.diagnostics.issue_detection import ingest_candidates
    from app.diagnostics.store import diagnostic_store

    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"
    settings.diag_db_pool_warning_pct = 50
    settings.diag_db_pool_error_pct = 60

    with patch("app.services.database.db_manager") as db:
        db.enabled = True
        db.engine = _fast_fake_engine(checked_out=5, pool_size=5)
        out = await db_mod.collect_database_fast(settings)

    assert any(
        c.get("error_code") == "DB_POOL_EXHAUSTED" for c in out["issues_candidates"]
    )
    await ingest_candidates(
        out["issues_candidates"],
        service="database",
        component="postgresql",
        category="DATABASE",
        file="database.py",
        function="collect_database_fast",
    )
    issues = await diagnostic_store.list_issues(status="OPEN")
    assert any(i.error_code == "DB_POOL_EXHAUSTED" for i in issues)


@pytest.mark.asyncio
async def test_ai_package_includes_db_evidence():
    from app.diagnostics.ai_package import build_ai_fix_package
    from app.diagnostics.models import DiagnosticIssue

    issue = DiagnosticIssue(
        id="DIAG-20261003-TESTDB01",
        diagnostic_id="DIAG-20261003-TESTDB01",
        fingerprint="fp-db",
        severity="ERROR",
        status="OPEN",
        category="DATABASE",
        service="database",
        component="postgresql",
        error_code="DB_POOL_EXHAUSTED",
        message="pool exhausted",
        expected="utilization < 95%",
        actual="100%",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
        occurrence_count=1,
    )
    pkg = build_ai_fix_package(
        issue,
        resources={"status": "UNKNOWN"},
        category_context={
            "database": {
                "status": "ERROR",
                "reason": "DB_POOL_EXHAUSTED",
                "latency_ms": 12.3,
                "pool": {"utilization_pct": 100.0},
            },
            "database_detail": {
                "status": "HEALTHY",
                "reason": "cached",
                "cached": True,
                "collection_duration_ms": 12400,
                "tables": [{"table": "ohlcv"}],
                "slowest_queries": [{"name": "table_sizes_ms", "duration_ms": 11000}],
            },
        },
        related_logs=[],
        include={"git": False, "resources": False, "service_health": False},
    )
    assert "Category-specific evidence" in pkg["markdown"]
    assert "database_detail" in pkg["markdown"]
    assert "table_sizes_ms" in pkg["markdown"]
    assert "Do not modify backend/app/signals/*" in pkg["markdown"]


def test_fast_collector_ast_no_heavy_calls():
    """Parse AST of fast function body for heavy SQL string literals."""
    src = inspect.getsource(db_mod.collect_database_fast)
    # Strip def line indent for parse
    tree = ast.parse(inspect.getsource(db_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "collect_database_fast":
            body_src = ast.get_source_segment(inspect.getsource(db_mod), node) or src
            for marker in ("pg_total_relation_size", "pg_stat_user_tables", "n_dead_tup"):
                assert marker not in body_src
            return
    # Fallback already covered by source test
    assert "pg_total_relation_size" not in src
