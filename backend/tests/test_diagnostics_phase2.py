"""Phase 2 forensic collectors — real measurements, no fabricated zeros."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.diagnostics.collectors.websocket import _semantic_ws_status
from app.diagnostics.collectors.rest import _pctile
from app.diagnostics.collectors.data_health import _dataset_row, _status_from_counts
from app.diagnostics.log_buffer import DiagnosticLogBuffer
from app.diagnostics.redact import redact_value
from app.diagnostics.issue_detection import ingest_candidates
from app.diagnostics.store import DiagnosticStore
from app.diagnostics.constants import HealthStatus
from app.diagnostics.why_chain import build_why_chain
from app.diagnostics.ai_package import build_ai_fix_package
from app.diagnostics.models import DiagnosticIssue


def test_ws_connected_no_frames_is_waiting_first_frame():
    status, reason, _ = _semantic_ws_status(
        connected=True, frames=0, last_frame_at=None, stale_after=120
    )
    assert status == HealthStatus.WAITING.value
    assert reason == "WAITING_FOR_FIRST_FRAME"


def test_ws_connected_stale_frames():
    old = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc).isoformat()
    status, reason, stale = _semantic_ws_status(
        connected=True, frames=10, last_frame_at=old, stale_after=120
    )
    assert status == HealthStatus.STALE.value
    assert reason == "CONNECTED_BUT_NO_RECENT_FRAMES"
    assert stale is not None and stale > 120


def test_ws_live_recent_frames():
    now = datetime.now(timezone.utc).isoformat()
    status, reason, _ = _semantic_ws_status(
        connected=True, frames=5, last_frame_at=now, stale_after=120
    )
    assert status == "LIVE"
    assert reason is None


def test_ws_disconnected():
    status, reason, _ = _semantic_ws_status(
        connected=False, frames=0, last_frame_at=None, stale_after=120
    )
    assert status == HealthStatus.ERROR.value
    assert reason == "DISCONNECTED"


def test_p95_requires_enough_samples():
    assert _pctile([1, 2, 3], 95) is None
    assert _pctile([1, 2, 3, 4, 5, 6, 7, 8, 9, 100], 95) is not None


def test_dataset_row_null_not_zero_for_unknown():
    row = _dataset_row(
        dataset="structure",
        status=HealthStatus.UNKNOWN.value,
        provider="internal",
        universe_size=528,
        available_count=None,
        fresh_count=None,
        stale_count=None,
        missing_count=None,
        unavailable_count=None,
        last_update=None,
    )
    assert row["available_count"] is None
    assert row["fresh_count"] is None
    assert row["coverage_percent"] is None


def test_status_from_counts_waiting_not_error():
    assert (
        _status_from_counts(
            available=0, stale=0, unavailable=0, waiting=10, universe=10
        )
        == HealthStatus.WAITING.value
    )


@pytest.mark.asyncio
async def test_log_buffer_filter_and_redaction():
    buf = DiagnosticLogBuffer(maxlen=100)
    await buf.emit(
        severity="ERROR",
        message="fail api_key=supersecret",
        component="ohlcv_sync",
        diagnostic_id="DIAG-TEST-1",
        details={"token": "abc"},
    )
    res = await buf.query(diagnostic_id="DIAG-TEST-1")
    assert res["total_matched"] == 1
    entry = res["entries"][0]
    assert "supersecret" not in entry["message"]
    assert entry["details"]["token"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_log_correlation_prefers_ids():
    buf = DiagnosticLogBuffer()
    await buf.emit(severity="INFO", message="a", component="x", diagnostic_id="D1")
    await buf.emit(severity="INFO", message="b", component="x", job_id="J1")
    correlated = await buf.correlate(diagnostic_id="D1")
    assert len(correlated) == 1
    assert correlated[0]["diagnostic_id"] == "D1"


@pytest.mark.asyncio
async def test_ingest_candidates_creates_issue():
    # Use fresh store via patch
    store = DiagnosticStore()
    with patch("app.diagnostics.issue_detection.diagnostic_store", store), patch(
        "app.diagnostics.issue_detection.diagnostic_log_buffer"
    ) as lb:
        lb.emit = AsyncMock()
        created = await ingest_candidates(
            [
                {
                    "error_code": "PROVIDER_HIGH_429",
                    "severity": "WARNING",
                    "message": "binance high 429",
                    "expected": "<5",
                    "actual": "27",
                    "provider": "binance",
                }
            ],
            service="rest",
            component="providers",
            category="REST",
            file="backend/app/diagnostics/collectors/rest.py",
            function="collect_rest_forensics",
            line=1,
        )
    assert len(created) == 1
    assert created[0]["error_code"] == "PROVIDER_HIGH_429"
    assert created[0]["file"].endswith("rest.py")


def test_why_chain_uses_evidence_and_issue_link():
    chain = build_why_chain(
        "ohlcv",
        cards_by_key={},
        evidence_by_key={
            "binance_ws": {
                "status": "STALE",
                "reason": "CONNECTED_BUT_NO_RECENT_FRAMES",
                "last_checked": "t",
                "metrics": {"frames_total": 0},
            },
            "postgresql": {
                "status": "HEALTHY",
                "reason": "ok",
                "metrics": {},
            },
        },
        related_issues=[
            {
                "id": "DIAG-1",
                "diagnostic_id": "DIAG-1",
                "component": "binance_ws",
                "message": "stale",
                "location": "ws.py:1",
            }
        ],
    )
    assert chain["status"] == "STALE"
    assert chain["failure_node"]["key"] == "binance_ws"
    assert chain["failure_node"]["related_issue_id"] == "DIAG-1"
    assert chain["failure_node"]["last_checked"] == "t"


def test_ai_package_category_enrichment_and_redaction():
    issue = DiagnosticIssue(
        id="DIAG-20261003-P2TEST01",
        diagnostic_id="DIAG-20261003-P2TEST01",
        fingerprint="fp",
        severity="ERROR",
        status="OPEN",
        category="REST",
        component="providers",
        file="backend/app/diagnostics/collectors/rest.py",
        function="collect_rest_forensics",
        line=10,
        message="429 with api_key=sekrit",
        expected="<5",
        actual="27",
    )
    pkg = build_ai_fix_package(
        issue,
        category_context={
            "rest": {
                "status": "DEGRADED",
                "reason": "429s",
                "providers": [{"provider": "binance"}],
            }
        },
        related_logs=[
            {
                "timestamp": "t",
                "severity": "ERROR",
                "message": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb",
            }
        ],
    )
    assert "sekrit" not in pkg["markdown"]
    assert "Category-specific evidence" in pkg["markdown"]
    assert "eyJ" not in pkg["markdown"]
    assert "Do not modify backend/app/signals/*" in pkg["markdown"]


@pytest.mark.asyncio
async def test_database_collector_disabled():
    from app.diagnostics.collectors.database import collect_database_fast

    settings = MagicMock()
    settings.database_url = "postgresql+asyncpg://u:p@localhost/db"
    with patch("app.services.database.db_manager") as db:
        db.enabled = False
        db.engine = None
        out = await collect_database_fast(settings)
    assert out["status"] == HealthStatus.DISABLED.value
    assert out["mode"] == "fast"


@pytest.mark.asyncio
async def test_websocket_collector_no_runtime():
    from app.diagnostics.collectors.websocket import collect_websocket_forensics

    settings = MagicMock()
    settings.diag_ws_stale_seconds = 120
    with patch("app.engines.orchestrator.get_orchestrator", return_value=None), patch(
        "app.ingestion.service.get_ingestion", return_value=None
    ):
        out = await collect_websocket_forensics(settings)
    assert out["status"] == HealthStatus.WAITING.value
    assert out["connections"] == []


@pytest.mark.asyncio
async def test_rest_collector_empty_traffic():
    from app.diagnostics.collectors.rest import collect_rest_forensics

    settings = MagicMock()
    settings.providers_config = {}
    settings.diag_rest_429_warning = 5
    with patch(
        "app.core.provider_health.provider_health.snapshot_all",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.core.rate_limiter.rate_limiters.all_snapshots", return_value={}
    ):
        out = await collect_rest_forensics(settings)
    assert out["status"] in (
        HealthStatus.WAITING.value,
        HealthStatus.UNKNOWN.value,
        HealthStatus.HEALTHY.value,
    )


@pytest.mark.asyncio
async def test_jobs_collector_inprocess_only():
    from app.diagnostics.collectors.jobs import collect_jobs_forensics

    settings = MagicMock()
    settings.diag_job_stale_heartbeat_seconds = 300
    with patch("app.services.database.db_manager") as db:
        db.enabled = False
        db.engine = None
        out = await collect_jobs_forensics(settings)
    assert "jobs" in out
    assert out["status"] in (
        HealthStatus.WAITING.value,
        HealthStatus.HEALTHY.value,
        HealthStatus.UNKNOWN.value,
        HealthStatus.ERROR.value,
        HealthStatus.STALE.value,
    )


def test_redact_nested_secrets_in_context():
    raw = {
        "database": {
            "database_url": "postgresql://user:pass@localhost/db",
            "status": "HEALTHY",
        }
    }
    out = redact_value(raw)
    assert "pass" not in str(out["database"]["database_url"])
