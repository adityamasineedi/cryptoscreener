"""Phase 1 diagnostics: events, dedupe, health, redaction, AI package."""

from __future__ import annotations

import pytest

from app.diagnostics.constants import HealthStatus, IssueStatus, worst_status
from app.diagnostics.fingerprint import issue_fingerprint, normalize_message
from app.diagnostics.ids import new_diagnostic_id
from app.diagnostics.redact import contains_secret, redact_string, redact_value
from app.diagnostics.resources import disk_severity_label, status_from_pct
from app.diagnostics.store import DiagnosticStore
from app.diagnostics.ai_package import build_ai_fix_package
from app.diagnostics.why_chain import build_why_chain
from app.diagnostics.health_eval import _map_provider_status


def test_diagnostic_id_format():
    did = new_diagnostic_id()
    assert did.startswith("DIAG-")
    parts = did.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8
    assert len(parts[2]) == 8


def test_fingerprint_stable_and_normalizes_numbers():
    a = issue_fingerprint(
        service="research",
        component="ohlcv_sync",
        error_code="HTTP_429",
        exception_type="HTTPError",
        message="429 Too Many Requests after 5 retries on BTCUSDT",
        symbol="BTCUSDT",
        timeframe="5m",
    )
    b = issue_fingerprint(
        service="research",
        component="ohlcv_sync",
        error_code="HTTP_429",
        exception_type="HTTPError",
        message="429 Too Many Requests after 9 retries on BTCUSDT",
        symbol="BTCUSDT",
        timeframe="5m",
    )
    assert a == b
    assert len(a) == 32


def test_normalize_message_strips_ids():
    n = normalize_message("Request abcdef0123456789 failed with 429")
    assert "<hex>" in n
    assert "<n>" in n


@pytest.mark.asyncio
async def test_record_event_and_dedupe_increments_occurrence():
    store = DiagnosticStore()
    e1, i1 = await store.record_event(
        severity="ERROR",
        message="Binance REST returned HTTP 429",
        category="REST",
        service="research",
        component="ohlcv_sync",
        error_code="HTTP_429",
        exception_type="HTTPError",
        file="backend/app/research/data_pipeline/sync.py",
        function="sync_range",
        line=482,
        symbol="BTCUSDT",
        timeframe="5m",
        expected="Request succeeds within retry policy",
        actual="HTTP 429 after 5 retries",
    )
    assert i1.occurrence_count == 1
    assert i1.file.endswith("sync.py")
    assert i1.line == 482
    assert i1.diagnostic_id.startswith("DIAG-")

    e2, i2 = await store.record_event(
        severity="ERROR",
        message="Binance REST returned HTTP 429",
        category="REST",
        service="research",
        component="ohlcv_sync",
        error_code="HTTP_429",
        exception_type="HTTPError",
        file="backend/app/research/data_pipeline/sync.py",
        function="sync_range",
        line=482,
        symbol="BTCUSDT",
        timeframe="5m",
    )
    assert i2.fingerprint == i1.fingerprint
    assert i2.occurrence_count == 2
    assert i2.id == i1.id
    assert e1.id != e2.id


@pytest.mark.asyncio
async def test_resolved_issue_reopens_on_same_fingerprint():
    store = DiagnosticStore()
    _, issue = await store.record_event(
        severity="ERROR",
        message="insert failed",
        service="research",
        component="postgres",
        error_code="DB_WRITE",
        exception_type="IntegrityError",
    )
    await store.resolve(issue.id, message="fixed")
    resolved = await store.get_issue(issue.id)
    assert resolved is not None
    assert resolved.status == IssueStatus.RESOLVED.value

    _, again = await store.record_event(
        severity="ERROR",
        message="insert failed",
        service="research",
        component="postgres",
        error_code="DB_WRITE",
        exception_type="IntegrityError",
    )
    assert again.status == IssueStatus.OPEN.value
    assert again.occurrence_count == 2
    assert again.resolved_at is None


def test_secret_redaction():
    raw = {
        "api_key": "sk-secret-123",
        "Authorization": "Bearer abc.def.ghi",
        "database_url": "postgresql://user:pass@localhost/db",
        "safe": "ok",
        "nested": {"token": "xyz", "count": 1},
    }
    out = redact_value(raw)
    assert out["api_key"] == "***REDACTED***"
    assert out["Authorization"] == "***REDACTED***"
    assert "***" in out["database_url"]
    assert "pass" not in out["database_url"]
    assert out["safe"] == "ok"
    assert out["nested"]["token"] == "***REDACTED***"
    assert out["nested"]["count"] == 1
    assert contains_secret("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.aaa.bbb")


def test_ai_package_redacts_secrets_and_includes_location():
    store_issue_msg = "HTTP 429 with api_key=supersecret"
    from app.diagnostics.models import DiagnosticIssue
    from datetime import datetime, timezone

    issue = DiagnosticIssue(
        id="DIAG-20261003-TEST0001",
        diagnostic_id="DIAG-20261003-TEST0001",
        fingerprint="abc",
        severity="ERROR",
        status="OPEN",
        category="REST",
        component="ohlcv_sync",
        file="backend/app/research/data_pipeline/sync.py",
        function="sync_range",
        line=482,
        message=store_issue_msg,
        expected="success",
        actual="429",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    pkg = build_ai_fix_package(issue)
    assert "sync_range" in pkg["markdown"]
    assert "482" in pkg["markdown"]
    assert "supersecret" not in pkg["markdown"]
    assert "api_key=***REDACTED***" in pkg["markdown"] or "***REDACTED***" in pkg["markdown"]
    assert "Do not modify backend/app/signals/*" in pkg["markdown"]


def test_disk_thresholds():
    assert disk_severity_label(50, warning=70, error=85, critical=95) == HealthStatus.HEALTHY.value
    assert disk_severity_label(75, warning=70, error=85, critical=95) == HealthStatus.WARNING.value
    assert disk_severity_label(90, warning=70, error=85, critical=95) == HealthStatus.ERROR.value
    assert disk_severity_label(96, warning=70, error=85, critical=95) == "CRITICAL"
    assert status_from_pct(None, warning=70, error=85, critical=95) == HealthStatus.UNKNOWN.value


def test_worst_status_and_waiting_not_error():
    assert worst_status(HealthStatus.HEALTHY, HealthStatus.WAITING) == HealthStatus.WAITING.value
    assert worst_status(HealthStatus.WAITING, HealthStatus.ERROR) == HealthStatus.ERROR.value
    # Mapping: WAITING stays WAITING
    assert _map_provider_status("WAITING") == HealthStatus.WAITING.value


def test_ws_stale_logic_via_why_chain():
    cards = {
        "binance_ws": {
            "key": "binance_ws",
            "status": "STALE",
            "reason": "Connected but no frames received",
            "metrics": {"active": 7, "messages_total": 0},
        },
        "postgresql": {
            "key": "postgresql",
            "status": "HEALTHY",
            "reason": "ok",
            "metrics": {},
        },
    }
    chain = build_why_chain("ohlcv", cards_by_key=cards)
    assert chain["status"] == "STALE"
    assert chain["failure_node"]["key"] == "binance_ws"
    assert chain["failure_node"]["ok"] is False


def test_redact_string_jwt():
    s = redact_string("token=eyJhbGciOiJIUzI1NiJ9.aaa.bbb")
    assert s is not None
    assert "eyJ" not in s


@pytest.mark.asyncio
async def test_acknowledge_and_resolve():
    store = DiagnosticStore()
    _, issue = await store.record_event(
        severity="WARNING",
        message="disk high",
        service="system",
        component="disk",
        error_code="DISK_WARN",
    )
    ack = await store.acknowledge(issue.id, owner="dev")
    assert ack is not None
    assert ack.status == IssueStatus.ACKNOWLEDGED.value
    assert ack.owner == "dev"
    done = await store.resolve(issue.id, message="cleaned")
    assert done is not None
    assert done.status == IssueStatus.RESOLVED.value
