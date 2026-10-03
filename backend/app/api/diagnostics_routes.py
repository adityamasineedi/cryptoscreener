"""System Diagnostics / Issue Center API — /api/diagnostics/*"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from app.config import get_settings
from app.diagnostics.service import diagnostic_service

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("/overview")
async def diagnostics_overview() -> dict[str, Any]:
    return await diagnostic_service.overview(get_settings())


@router.get("/issues")
async def diagnostics_issues(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    component: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return await diagnostic_service.list_issues(
        status=status,
        severity=severity,
        component=component,
        limit=limit,
        offset=offset,
    )


@router.get("/issues/{issue_id}")
async def diagnostics_issue_detail(issue_id: str) -> dict[str, Any]:
    payload = await diagnostic_service.get_issue(issue_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="issue_not_found")
    return payload


@router.get("/issues/{issue_id}/ai-package")
async def diagnostics_ai_package(issue_id: str) -> dict[str, Any]:
    pkg = await diagnostic_service.ai_package(issue_id, settings=get_settings())
    if pkg is None:
        raise HTTPException(status_code=404, detail="issue_not_found")
    return pkg


@router.post("/issues/{issue_id}/acknowledge")
async def diagnostics_acknowledge(
    issue_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    owner = (body or {}).get("owner")
    issue = await diagnostic_service.acknowledge(issue_id, owner=owner)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue_not_found")
    return {"issue": issue}


@router.post("/issues/{issue_id}/resolve")
async def diagnostics_resolve(
    issue_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    message = (body or {}).get("message")
    issue = await diagnostic_service.resolve(issue_id, message=message)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue_not_found")
    return {"issue": issue}


@router.get("/resources")
async def diagnostics_resources() -> dict[str, Any]:
    return await diagnostic_service.resources(get_settings())


@router.get("/git")
async def diagnostics_git() -> dict[str, Any]:
    return await diagnostic_service.git()


@router.get("/why/{dataset}")
async def diagnostics_why(dataset: str) -> dict[str, Any]:
    return await diagnostic_service.why(dataset, get_settings())


@router.post("/events")
async def diagnostics_record_event(
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Controlled event ingest for tests / internal tooling. No secrets."""
    message = body.get("message")
    severity = body.get("severity") or "ERROR"
    if not message:
        raise HTTPException(status_code=400, detail="message_required")
    allowed = {
        "category",
        "service",
        "subsystem",
        "component",
        "module",
        "file",
        "function",
        "line",
        "event_type",
        "error_code",
        "exception_type",
        "stack_trace",
        "symbol",
        "timeframe",
        "provider",
        "endpoint",
        "stream",
        "request_id",
        "job_id",
        "run_id",
        "expected",
        "actual",
        "details",
        "metadata",
    }
    kwargs = {k: body[k] for k in allowed if k in body}
    return await diagnostic_service.record(
        severity=str(severity), message=str(message), **kwargs
    )


@router.post("/ai-handoff")
async def diagnostics_ai_handoff(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = body or {}
    return await diagnostic_service.generate_handoff_package(
        settings=get_settings(),
        issue_id=body.get("issue_id"),
        include=body.get("include"),
    )


@router.get("/services")
async def diagnostics_services() -> dict[str, Any]:
    overview = await diagnostic_service.overview(get_settings())
    return {
        "phase": 2,
        "note": "Service cards from overview + Phase 2 collectors",
        "services": overview.get("cards") or [],
    }


@router.get("/database")
async def diagnostics_database() -> dict[str, Any]:
    """Fast DB health only (<500ms target). Expensive catalog → /database/detail."""
    return await diagnostic_service.database(get_settings())


@router.get("/database/detail")
async def diagnostics_database_detail(
    refresh: bool = Query(default=False, description="Force refresh cache"),
) -> dict[str, Any]:
    """Detailed DB forensics (table sizes, vacuum, long/blocked queries). Cached."""
    return await diagnostic_service.database_detail(
        get_settings(), force_refresh=refresh
    )


@router.get("/websocket")
async def diagnostics_websocket() -> dict[str, Any]:
    return await diagnostic_service.websocket(get_settings())


@router.get("/rest")
async def diagnostics_rest() -> dict[str, Any]:
    return await diagnostic_service.rest(get_settings())


@router.get("/data-health")
async def diagnostics_data_health() -> dict[str, Any]:
    return await diagnostic_service.data_health(get_settings())


@router.get("/data-health/{dataset}")
async def diagnostics_data_health_drilldown(
    dataset: str,
    symbol: str = Query(...),
    timeframe: str = Query(...),
) -> dict[str, Any]:
    return await diagnostic_service.data_health_drilldown(
        dataset,
        symbol=symbol,
        timeframe=timeframe,
        settings=get_settings(),
    )


@router.get("/jobs")
async def diagnostics_jobs() -> dict[str, Any]:
    return await diagnostic_service.jobs(get_settings())


@router.get("/backups")
async def diagnostics_backups() -> dict[str, Any]:
    return await diagnostic_service.backups(get_settings())


@router.post("/backups")
async def diagnostics_backup_create(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = body or {}
    backup_type = str(body.get("type") or body.get("backup_type") or "CONFIG")
    return await diagnostic_service.backup_start(
        get_settings(), backup_type=backup_type
    )


@router.get("/backups/{backup_id}")
async def diagnostics_backup_get(backup_id: str) -> dict[str, Any]:
    row = await diagnostic_service.backup_get(get_settings(), backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="backup_not_found")
    return row


@router.post("/backups/{backup_id}/verify")
async def diagnostics_backup_verify(backup_id: str) -> dict[str, Any]:
    return await diagnostic_service.backup_verify(get_settings(), backup_id)


@router.post("/backups/{backup_id}/restore-test")
async def diagnostics_backup_restore_test(backup_id: str) -> dict[str, Any]:
    return await diagnostic_service.backup_restore_test(get_settings(), backup_id)


@router.get("/logs")
async def diagnostics_logs(
    severity: str | None = Query(default=None),
    service: str | None = Query(default=None),
    component: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    diagnostic_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    job_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    text: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return await diagnostic_service.logs(
        severity=severity,
        service=service,
        component=component,
        provider=provider,
        symbol=symbol,
        timeframe=timeframe,
        diagnostic_id=diagnostic_id,
        request_id=request_id,
        job_id=job_id,
        run_id=run_id,
        text=text,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )


@router.get("/latency")
async def diagnostics_latency() -> dict[str, Any]:
    from app.diagnostics.latency import latency_tracker

    return {"phase": 2, "endpoints": latency_tracker.snapshot()}


@router.get("/snapshot")
async def diagnostics_snapshot_list() -> dict[str, Any]:
    return await diagnostic_service.snapshot_list(get_settings())


@router.post("/snapshot")
async def diagnostics_snapshot_post(
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    body = body or {}
    return await diagnostic_service.snapshot_create(
        get_settings(), label=body.get("label")
    )


@router.get("/snapshot/{snapshot_id}")
async def diagnostics_snapshot_get(snapshot_id: str) -> dict[str, Any]:
    row = await diagnostic_service.snapshot_get(get_settings(), snapshot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    return row


@router.get("/snapshot/{snapshot_id}/ai-package")
async def diagnostics_snapshot_ai_package(snapshot_id: str) -> dict[str, Any]:
    row = await diagnostic_service.snapshot_ai_package(get_settings(), snapshot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    return row


@router.get("/snapshot/{snapshot_id}/export")
async def diagnostics_snapshot_export(snapshot_id: str) -> dict[str, Any]:
    row = await diagnostic_service.snapshot_export(get_settings(), snapshot_id)
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot_not_found")
    return row
