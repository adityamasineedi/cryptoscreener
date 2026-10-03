"""Public diagnostic service façade used by API routes."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from app.diagnostics.ai_package import build_ai_fix_package
from app.diagnostics.collectors.data_health import collect_data_health, drilldown_ohlcv
from app.diagnostics.collectors.database import (
    collect_database_fast,
    get_database_detail,
)
from app.diagnostics.collectors.jobs import collect_jobs_forensics
from app.diagnostics.collectors.rest import collect_rest_forensics
from app.diagnostics.collectors.websocket import collect_websocket_forensics
from app.diagnostics.git_info import collect_git_info
from app.diagnostics.health_eval import evaluate_overview
from app.diagnostics.issue_detection import ingest_candidates
from app.diagnostics.latency import latency_tracker, timed
from app.diagnostics.log_buffer import diagnostic_log_buffer
from app.diagnostics.redact import redact_value
from app.diagnostics.resources import resource_sampler
from app.diagnostics.store import diagnostic_store
from app.diagnostics.why_chain import build_why_chain


class DiagnosticService:
    def __init__(self) -> None:
        self.store = diagnostic_store

    async def overview(self, settings: Any) -> dict[str, Any]:
        expensive = float(
            getattr(settings, "diag_expensive_metrics_seconds", 30) or 30
        )
        resource_sampler.configure(expensive_interval_seconds=expensive)
        with timed("overview"):
            return await evaluate_overview(settings)

    async def resources(self, settings: Any) -> dict[str, Any]:
        expensive = float(
            getattr(settings, "diag_expensive_metrics_seconds", 30) or 30
        )
        resource_sampler.configure(expensive_interval_seconds=expensive)
        with timed("resources"):
            return resource_sampler.sample(settings)

    async def git(self) -> dict[str, Any]:
        return collect_git_info()

    async def list_issues(self, **kwargs: Any) -> dict[str, Any]:
        issues = await self.store.list_issues(**kwargs)
        return {
            "count": len(issues),
            "issues": [i.to_dict() for i in issues],
        }

    async def get_issue(self, issue_id: str) -> dict[str, Any] | None:
        issue = await self.store.get_issue(issue_id)
        if issue is None:
            return None
        events = await self.store.get_events_for_issue(issue)
        # Related logs via ID correlation
        logs = await diagnostic_log_buffer.correlate(
            diagnostic_id=issue.diagnostic_id,
            request_id=issue.request_id,
            job_id=issue.job_id,
            run_id=issue.run_id,
            component=issue.component,
            limit=50,
        )
        return {
            "issue": issue.to_dict(),
            "events": [e.to_dict() for e in events],
            "timeline": [
                {
                    "at": e.timestamp.isoformat(),
                    "severity": e.severity,
                    "message": e.message,
                    "event_id": e.id,
                }
                for e in sorted(events, key=lambda x: x.timestamp)
            ],
            "related_logs": logs,
        }

    async def acknowledge(self, issue_id: str, owner: str | None = None) -> dict | None:
        issue = await self.store.acknowledge(issue_id, owner=owner)
        return issue.to_dict() if issue else None

    async def resolve(self, issue_id: str, message: str | None = None) -> dict | None:
        issue = await self.store.resolve(issue_id, message=message)
        return issue.to_dict() if issue else None

    async def database(self, settings: Any, *, detect_issues: bool = True) -> dict[str, Any]:
        """Fast DB health only — never runs expensive catalog queries."""
        with timed("database"):
            try:
                payload = await collect_database_fast(settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "phase": 2.1,
                    "mode": "fast",
                    "status": "UNKNOWN",
                    "reason": f"collector_failed:{exc.__class__.__name__}",
                    "errors": [{"code": "COLLECTOR", "error": str(exc)}],
                }
        if detect_issues and payload.get("issues_candidates"):
            await ingest_candidates(
                payload["issues_candidates"],
                service="database",
                component="postgresql",
                category="DATABASE",
                file=str(payload.get("file") or ""),
                function=str(payload.get("function") or ""),
            )
        payload["endpoint_latency"] = latency_tracker.snapshot().get("database")
        return redact_value(payload)

    async def database_detail(
        self,
        settings: Any,
        *,
        force_refresh: bool = False,
        detect_issues: bool = True,
    ) -> dict[str, Any]:
        """Cached detailed DB forensics (table sizes, locks, long queries)."""
        with timed("database_detail"):
            try:
                payload = await get_database_detail(
                    settings, force_refresh=force_refresh
                )
            except Exception as exc:  # noqa: BLE001
                return {
                    "phase": 2.1,
                    "mode": "detail",
                    "status": "UNKNOWN",
                    "reason": f"collector_failed:{exc.__class__.__name__}",
                    "errors": [{"code": "COLLECTOR", "error": str(exc)}],
                    "cached": False,
                }
        if detect_issues and not payload.get("cached") and payload.get(
            "issues_candidates"
        ):
            await ingest_candidates(
                payload["issues_candidates"],
                service="database",
                component="postgresql",
                category="DATABASE",
                file=str(payload.get("file") or ""),
                function=str(payload.get("function") or ""),
            )
        payload["endpoint_latency"] = latency_tracker.snapshot().get("database_detail")
        return redact_value(payload)

    async def websocket(self, settings: Any, *, detect_issues: bool = True) -> dict[str, Any]:
        with timed("websocket"):
            try:
                payload = await collect_websocket_forensics(settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "phase": 2,
                    "status": "UNKNOWN",
                    "reason": f"collector_failed:{exc.__class__.__name__}",
                    "errors": [{"code": "COLLECTOR", "error": str(exc)}],
                }
        if detect_issues and payload.get("issues_candidates"):
            await ingest_candidates(
                payload["issues_candidates"],
                service="websocket",
                component="binance_ws",
                category="WEBSOCKET",
                file=str(payload.get("file") or ""),
                function=str(payload.get("function") or ""),
            )
        payload["endpoint_latency"] = latency_tracker.snapshot().get("websocket")
        return redact_value(payload)

    async def rest(self, settings: Any, *, detect_issues: bool = True) -> dict[str, Any]:
        with timed("rest"):
            try:
                payload = await collect_rest_forensics(settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "phase": 2,
                    "status": "UNKNOWN",
                    "reason": f"collector_failed:{exc.__class__.__name__}",
                    "errors": [{"code": "COLLECTOR", "error": str(exc)}],
                }
        if detect_issues and payload.get("issues_candidates"):
            await ingest_candidates(
                payload["issues_candidates"],
                service="rest",
                component="providers",
                category="REST",
                file=str(payload.get("file") or ""),
                function=str(payload.get("function") or ""),
            )
        payload["endpoint_latency"] = latency_tracker.snapshot().get("rest")
        return redact_value(payload)

    async def data_health(self, settings: Any) -> dict[str, Any]:
        with timed("data_health"):
            try:
                payload = await collect_data_health(settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "phase": 2,
                    "status": "UNKNOWN",
                    "reason": f"collector_failed:{exc.__class__.__name__}",
                    "errors": [{"code": "COLLECTOR", "error": str(exc)}],
                }
        payload["endpoint_latency"] = latency_tracker.snapshot().get("data_health")
        return redact_value(payload)

    async def data_health_drilldown(
        self,
        dataset: str,
        *,
        symbol: str,
        timeframe: str,
        settings: Any,
    ) -> dict[str, Any]:
        with timed("data_health_drilldown"):
            if dataset.lower().startswith("ohlcv"):
                payload = await drilldown_ohlcv(
                    symbol=symbol, timeframe=timeframe, settings=settings
                )
            else:
                payload = {
                    "dataset": dataset,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "UNKNOWN",
                    "reason": f"No explicit drilldown implemented for dataset '{dataset}'",
                }
        return redact_value(payload)

    async def jobs(self, settings: Any, *, detect_issues: bool = True) -> dict[str, Any]:
        with timed("jobs"):
            try:
                payload = await collect_jobs_forensics(settings)
            except Exception as exc:  # noqa: BLE001
                return {
                    "phase": 2,
                    "status": "UNKNOWN",
                    "reason": f"collector_failed:{exc.__class__.__name__}",
                    "errors": [{"code": "COLLECTOR", "error": str(exc)}],
                }
        if detect_issues and payload.get("issues_candidates"):
            await ingest_candidates(
                payload["issues_candidates"],
                service="research",
                component="jobs",
                category="JOB",
                file=str(payload.get("file") or ""),
                function=str(payload.get("function") or ""),
            )
        payload["endpoint_latency"] = latency_tracker.snapshot().get("jobs")
        return redact_value(payload)

    async def logs(self, **filters: Any) -> dict[str, Any]:
        with timed("logs"):
            # Merge store events into buffer view for ID searchability
            since = filters.pop("since", None)
            until = filters.pop("until", None)
            if isinstance(since, str) and since:
                try:
                    filters["since"] = datetime.fromisoformat(since.replace("Z", "+00:00"))
                except Exception:  # noqa: BLE001
                    pass
            if isinstance(until, str) and until:
                try:
                    filters["until"] = datetime.fromisoformat(until.replace("Z", "+00:00"))
                except Exception:  # noqa: BLE001
                    pass
            # Also seed from recent diagnostic events (best-effort)
            try:
                issues = await self.store.list_issues(limit=50)
                for issue in issues:
                    events = await self.store.get_events_for_issue(issue, limit=10)
                    for ev in events:
                        await diagnostic_log_buffer.emit(
                            severity=ev.severity,
                            message=ev.message,
                            service=ev.service,
                            component=ev.component,
                            provider=ev.provider,
                            symbol=ev.symbol,
                            timeframe=ev.timeframe,
                            diagnostic_id=ev.diagnostic_id,
                            request_id=ev.request_id,
                            job_id=ev.job_id,
                            run_id=ev.run_id,
                            details={"source": "diagnostic_event", "event_id": ev.id},
                            timestamp=ev.timestamp,
                            entry_id=ev.id,
                        )
            except Exception:  # noqa: BLE001
                pass
            payload = await diagnostic_log_buffer.query(**filters)
        payload["endpoint_latency"] = latency_tracker.snapshot().get("logs")
        return redact_value(payload)

    async def ai_package(
        self,
        issue_id: str,
        *,
        settings: Any,
        include: dict[str, bool] | None = None,
    ) -> dict[str, Any] | None:
        issue = await self.store.get_issue(issue_id)
        if issue is None:
            return None
        events = await self.store.get_events_for_issue(issue)
        overview = await self.overview(settings)
        related = [
            c
            for c in overview.get("cards") or []
            if (issue.component and issue.component.lower() in (c.get("key") or ""))
            or (issue.service and issue.service.lower() in (c.get("key") or ""))
            or c.get("status")
            in ("ERROR", "STALE", "UNAVAILABLE", "DEGRADED", "WARNING", "CRITICAL")
        ]
        category_ctx = await self._category_context(issue, settings)
        logs = await diagnostic_log_buffer.correlate(
            diagnostic_id=issue.diagnostic_id,
            request_id=issue.request_id,
            job_id=issue.job_id,
            run_id=issue.run_id,
            component=issue.component,
            limit=30,
        )
        return build_ai_fix_package(
            issue,
            events=[e.to_dict() for e in events],
            resources=overview.get("resources"),
            related_health=related,
            include=include,
            category_context=category_ctx,
            related_logs=logs,
        )

    async def _category_context(self, issue: Any, settings: Any) -> dict[str, Any]:
        cat = (issue.category or "").upper()
        comp = (issue.component or "").lower()
        ctx: dict[str, Any] = {"category": cat}
        try:
            if cat == "DATABASE" or "postgres" in comp or "database" in comp:
                ctx["database"] = await self.database(settings, detect_issues=False)
                # Prefer cached detail (no forced refresh) for AI evidence
                ctx["database_detail"] = await self.database_detail(
                    settings, force_refresh=False, detect_issues=False
                )
            elif cat == "WEBSOCKET" or "ws" in comp:
                ctx["websocket"] = await self.websocket(settings, detect_issues=False)
            elif cat == "REST" or "provider" in comp:
                ctx["rest"] = await self.rest(settings, detect_issues=False)
            elif cat in ("DATA_STALE", "DATA_MISSING", "DATA_QUALITY") or "ohlcv" in comp:
                ctx["data_health"] = await self.data_health(settings)
                if issue.symbol and issue.timeframe:
                    ctx["drilldown"] = await self.data_health_drilldown(
                        "ohlcv",
                        symbol=issue.symbol,
                        timeframe=issue.timeframe,
                        settings=settings,
                    )
            elif cat in ("JOB", "RESEARCH", "LOCK") or "job" in comp or "research" in comp:
                ctx["jobs"] = await self.jobs(settings, detect_issues=False)
            # Always attach lightweight backup recovery evidence when available
            try:
                from app.diagnostics.backup.service import backup_service

                b = await backup_service.list_backups(settings)
                ctx["backups"] = {
                    "status": b.get("status"),
                    "reason": b.get("reason"),
                    "summary": b.get("summary"),
                    "restore_capability": b.get("restore_capability"),
                }
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            ctx["enrichment_error"] = exc.__class__.__name__
        return ctx

    async def why(self, dataset: str, settings: Any) -> dict[str, Any]:
        overview = await self.overview(settings)
        cards = {c["key"]: c for c in overview.get("cards") or []}
        evidence: dict[str, dict[str, Any]] = {}
        # Enrich with Phase 2 collectors (failure-isolated)
        try:
            ws = await self.websocket(settings, detect_issues=False)
            for c in ws.get("connections") or []:
                key = "binance_ws" if c.get("stream_type") != "liquidation" else "liquidations"
                evidence[key] = {
                    "status": c.get("status"),
                    "reason": c.get("reason"),
                    "last_checked": ws.get("last_checked"),
                    "metrics": {
                        "connection_id": c.get("connection_id"),
                        "connected": c.get("connected"),
                        "stale_seconds": c.get("stale_seconds"),
                        "frames_total": c.get("frames_total"),
                    },
                }
            for st, agg in (ws.get("by_stream_type") or {}).items():
                evidence[st if st != "kline" else "binance_ws"] = {
                    "status": agg.get("status"),
                    "reason": agg.get("reason"),
                    "last_checked": ws.get("last_checked"),
                    "metrics": agg,
                }
        except Exception:  # noqa: BLE001
            pass
        try:
            db = await self.database(settings, detect_issues=False)
            evidence["postgresql"] = {
                "status": db.get("status"),
                "reason": db.get("reason"),
                "last_checked": db.get("last_checked"),
                "metrics": {
                    "latency_ms": db.get("latency_ms"),
                    "pool": db.get("pool"),
                },
            }
        except Exception:  # noqa: BLE001
            pass
        try:
            dh = await self.data_health(settings)
            for d in dh.get("datasets") or []:
                if str(d.get("dataset", "")).startswith("ohlcv"):
                    evidence["coverage"] = {
                        "status": d.get("status"),
                        "reason": d.get("note") or d.get("status"),
                        "last_checked": dh.get("last_checked"),
                        "metrics": d,
                    }
                    evidence["freshness"] = evidence["coverage"]
                    break
        except Exception:  # noqa: BLE001
            pass

        issues = await self.store.list_issues(status="OPEN", limit=100)
        related = [
            i.to_dict()
            for i in issues
            if dataset.lower() in (i.component or "").lower()
            or dataset.lower() in (i.subsystem or "").lower()
            or dataset.lower() in (i.message or "").lower()
        ]
        return build_why_chain(
            dataset,
            cards_by_key=cards,
            related_issues=related,
            evidence_by_key=evidence,
        )

    async def record(
        self,
        *,
        severity: str,
        message: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record a diagnostic event (used by tests and internal callers)."""
        event, issue = await self.store.record_event(
            severity=severity, message=message, **kwargs
        )
        await diagnostic_log_buffer.emit(
            severity=severity,
            message=message,
            service=kwargs.get("service"),
            component=kwargs.get("component"),
            provider=kwargs.get("provider"),
            symbol=kwargs.get("symbol"),
            timeframe=kwargs.get("timeframe"),
            diagnostic_id=issue.diagnostic_id,
            request_id=kwargs.get("request_id"),
            job_id=kwargs.get("job_id"),
            run_id=kwargs.get("run_id"),
            details={"event_id": event.id, "error_code": kwargs.get("error_code")},
            timestamp=event.timestamp,
            entry_id=event.id,
        )
        return {"event": event.to_dict(), "issue": issue.to_dict()}

    async def record_exception(
        self,
        *,
        severity: str,
        message: str,
        exc: BaseException,
        **kwargs: Any,
    ) -> dict[str, Any]:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        file = kwargs.pop("file", None)
        function = kwargs.pop("function", None)
        line = kwargs.pop("line", None)
        if file is None or line is None:
            extracted = traceback.extract_tb(exc.__traceback__)
            for fr in reversed(extracted):
                if "site-packages" in fr.filename:
                    continue
                file = file or fr.filename
                function = function or fr.name
                line = line if line is not None else fr.lineno
                break
        return await self.record(
            severity=severity,
            message=message,
            exception_type=type(exc).__name__,
            stack_trace=tb,
            file=file,
            function=function,
            line=line,
            **kwargs,
        )

    async def generate_handoff_package(
        self,
        *,
        settings: Any,
        issue_id: str | None = None,
        include: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        flags = include or {}
        parts: dict[str, Any] = {"generated_sections": []}
        md: list[str] = ["# AI Fix Package (Handoff)", ""]

        if issue_id:
            pkg = await self.ai_package(issue_id, settings=settings, include=flags)
            if pkg is None:
                return {"error": "issue_not_found", "issue_id": issue_id}
            parts["issue_package"] = pkg
            parts["generated_sections"].append("issue")
            md.append(pkg["markdown"])
            md.append("")
        elif flags.get("issue", True):
            issues = await self.store.list_issues(status="OPEN", limit=20)
            md.append("## Open Issues")
            for i in issues:
                md.append(
                    f"- {i.severity} `{i.diagnostic_id}` {i.component or ''} — {i.message}"
                )
            md.append("")
            parts["open_issues"] = [i.to_dict() for i in issues]
            parts["generated_sections"].append("issues")

        if flags.get("service_health", True):
            overview = await self.overview(settings)
            parts["overview"] = redact_value(overview)
            parts["generated_sections"].append("service_health")
            md.append("## Service Health")
            md.append(f"System: {overview.get('system_status')}")
            for c in overview.get("cards") or []:
                md.append(f"- {c.get('label')}: {c.get('status')} — {c.get('reason')}")
            md.append("")

        if flags.get("resources", True):
            res = await self.resources(settings)
            parts["resources"] = redact_value(res)
            parts["generated_sections"].append("resources")

        if flags.get("git", True):
            git = await self.git()
            parts["git"] = redact_value(git)
            parts["generated_sections"].append("git")
            md.append("## Git")
            md.append(f"Branch: {git.get('branch')}  Commit: {git.get('commit_short')}")
            md.append(f"Worktree: {git.get('worktree_status')}")
            md.append("")

        # Recovery context from backups (evidence only)
        try:
            from app.diagnostics.backup.service import backup_service

            backups = await backup_service.list_backups(settings)
            parts["backups"] = redact_value(
                {
                    "status": backups.get("status"),
                    "reason": backups.get("reason"),
                    "summary": backups.get("summary"),
                    "restore_capability": backups.get("restore_capability"),
                }
            )
            parts["generated_sections"].append("backups")
            md.append("## Recovery Context")
            md.append(f"Backup health: {backups.get('status')} — {backups.get('reason')}")
            last = ((backups.get("summary") or {}).get("last_verified_backup")) or {}
            md.append(f"Latest verified backup: {last.get('backup_id') or 'NONE'}")
            md.append(f"Type: {last.get('backup_type') or '—'}")
            md.append(f"Created: {last.get('started_at') or '—'}")
            md.append(
                f"Restore test: {last.get('restore_test_status') or 'NOT_TESTED'}"
            )
            md.append("")
        except Exception as exc:  # noqa: BLE001
            parts["backups_error"] = exc.__class__.__name__

        parts["markdown"] = "\n".join(md)
        parts["endpoint_latency"] = latency_tracker.snapshot()
        return parts

    async def backups(self, settings: Any) -> dict[str, Any]:
        from app.diagnostics.backup.service import backup_service

        with timed("backups"):
            return await backup_service.list_backups(settings)

    async def backup_start(self, settings: Any, *, backup_type: str) -> dict[str, Any]:
        from app.diagnostics.backup.service import backup_service

        return await backup_service.start_backup(settings, backup_type=backup_type)

    async def backup_get(self, settings: Any, backup_id: str) -> dict[str, Any] | None:
        from app.diagnostics.backup.service import backup_service

        return await backup_service.get_backup(settings, backup_id)

    async def backup_verify(self, settings: Any, backup_id: str) -> dict[str, Any]:
        from app.diagnostics.backup.service import backup_service

        return await backup_service.verify_backup(settings, backup_id)

    async def backup_restore_test(
        self, settings: Any, backup_id: str
    ) -> dict[str, Any]:
        from app.diagnostics.backup.service import backup_service

        return await backup_service.restore_test(settings, backup_id)

    async def snapshot_create(
        self, settings: Any, *, label: str | None = None
    ) -> dict[str, Any]:
        from app.diagnostics.snapshots import snapshot_service

        with timed("snapshot"):
            return await snapshot_service.create(settings, label=label)

    async def snapshot_list(self, settings: Any) -> dict[str, Any]:
        from app.diagnostics.snapshots import snapshot_service

        return await snapshot_service.list_snapshots(settings)

    async def snapshot_get(
        self, settings: Any, snapshot_id: str
    ) -> dict[str, Any] | None:
        from app.diagnostics.snapshots import snapshot_service

        return await snapshot_service.get(settings, snapshot_id)

    async def snapshot_ai_package(
        self, settings: Any, snapshot_id: str
    ) -> dict[str, Any] | None:
        from app.diagnostics.snapshots import snapshot_service

        return await snapshot_service.ai_package(settings, snapshot_id)

    async def snapshot_export(
        self, settings: Any, snapshot_id: str
    ) -> dict[str, Any] | None:
        from app.diagnostics.snapshots import snapshot_service

        return await snapshot_service.export_bundle(settings, snapshot_id)


diagnostic_service = DiagnosticService()
