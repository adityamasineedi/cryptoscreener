"""Deterministic AI Fix Package generation — secrets redacted."""

from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from typing import Any

from app.diagnostics.git_info import collect_git_info
from app.diagnostics.models import DiagnosticIssue
from app.diagnostics.redact import redact_string, redact_value
from app.diagnostics.resources import resource_sampler


SAFETY_CONSTRAINTS = [
    "Do not modify backend/app/signals/*",
    "Do not modify production trading logic",
    "Preserve other agents' changes",
    "Do not delete data",
    "Do not fabricate data",
    "Do not run destructive git commands (reset/restore/clean)",
    "Never include or request secrets/API keys",
]


def build_ai_fix_package(
    issue: DiagnosticIssue,
    *,
    events: list[dict[str, Any]] | None = None,
    resources: dict[str, Any] | None = None,
    related_health: list[dict[str, Any]] | None = None,
    include: dict[str, bool] | None = None,
    category_context: dict[str, Any] | None = None,
    related_logs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return structured package + markdown suitable for Cursor/Claude."""
    flags = {
        "issue": True,
        "logs": True,
        "stack_trace": True,
        "service_health": True,
        "resources": True,
        "git": True,
        "tests": False,
        "configuration": True,
        **(include or {}),
    }
    git = collect_git_info() if flags.get("git") else {}
    res = resources if resources is not None else (
        resource_sampler.sample(None) if flags.get("resources") else {}
    )
    safe_issue = redact_value(issue.to_dict())
    safe_events = redact_value(events or [])
    safe_health = redact_value(related_health or [])
    safe_res = redact_value(res)
    safe_git = redact_value(git)
    safe_ctx = redact_value(category_context or {})
    safe_logs = redact_value(related_logs or [])

    location = issue.location_str() or "unknown"
    env = {
        "os": platform.system(),
        "os_release": platform.release(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }

    markdown = _render_markdown(
        issue=safe_issue if isinstance(safe_issue, dict) else {},
        location=location,
        env=env,
        git=safe_git if isinstance(safe_git, dict) else {},
        events=safe_events if isinstance(safe_events, list) else [],
        health=safe_health if isinstance(safe_health, list) else [],
        resources=safe_res if isinstance(safe_res, dict) else {},
        flags=flags,
        category_context=safe_ctx if isinstance(safe_ctx, dict) else {},
        related_logs=safe_logs if isinstance(safe_logs, list) else [],
    )

    return {
        "diagnostic_id": issue.diagnostic_id,
        "issue_id": issue.id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "format": "markdown",
        "markdown": markdown,
        "json": {
            "issue": safe_issue,
            "environment": env,
            "git": safe_git,
            "events": safe_events,
            "related_health": safe_health,
            "related_logs": safe_logs,
            "category_context": safe_ctx,
            "resources": {
                "disk": (safe_res or {}).get("disk") if isinstance(safe_res, dict) else None,
                "memory": (safe_res or {}).get("memory") if isinstance(safe_res, dict) else None,
                "cpu": (safe_res or {}).get("cpu") if isinstance(safe_res, dict) else None,
            },
            "safety_constraints": SAFETY_CONSTRAINTS,
        },
    }


def _render_markdown(
    *,
    issue: dict[str, Any],
    location: str,
    env: dict[str, Any],
    git: dict[str, Any],
    events: list[Any],
    health: list[Any],
    resources: dict[str, Any],
    flags: dict[str, bool],
    category_context: dict[str, Any] | None = None,
    related_logs: list[Any] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("=" * 50)
    lines.append("AI DIAGNOSTIC PACKAGE")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Issue ID:\n{issue.get('diagnostic_id') or issue.get('id')}")
    lines.append("")
    lines.append(f"Severity:\n{issue.get('severity')}")
    lines.append("")
    lines.append(f"Status:\n{issue.get('status')}")
    lines.append("")
    lines.append(f"Component:\n{issue.get('component') or issue.get('service') or '—'}")
    lines.append("")
    lines.append(f"Exact location:\n{location}")
    lines.append("")
    if issue.get("function"):
        lines.append(f"Function:\n{issue.get('function')}")
        lines.append("")
    if issue.get("line") is not None:
        lines.append(f"Line:\n{issue.get('line')}")
        lines.append("")
    lines.append("Environment:")
    lines.append(f"- OS: {env.get('os')} {env.get('os_release')}")
    lines.append(f"- Python: {env.get('python')}")
    if git.get("available"):
        lines.append(f"- Git branch: {git.get('branch')}")
        lines.append(f"- Git commit: {git.get('commit_short') or git.get('commit')}")
        lines.append(f"- Worktree: {git.get('worktree_status')}")
    lines.append("")
    lines.append("Problem:")
    lines.append(redact_string(issue.get("message")) or "—")
    lines.append("")
    lines.append("Expected:")
    lines.append(redact_string(issue.get("expected")) or "—")
    lines.append("")
    lines.append("Actual:")
    lines.append(redact_string(issue.get("actual")) or "—")
    lines.append("")
    if issue.get("symbol"):
        lines.append(f"Symbol:\n{issue.get('symbol')}")
        lines.append("")
    if issue.get("timeframe"):
        lines.append(f"Timeframe:\n{issue.get('timeframe')}")
        lines.append("")
    if issue.get("job_id"):
        lines.append(f"Job:\n{issue.get('job_id')}")
        lines.append("")
    if issue.get("request_id"):
        lines.append(f"Request ID:\n{issue.get('request_id')}")
        lines.append("")
    lines.append("Timeline:")
    lines.append(f"- First seen: {issue.get('first_seen')}")
    lines.append(f"- Last seen: {issue.get('last_seen')}")
    lines.append(f"- Occurrences: {issue.get('occurrence_count')}")
    lines.append("")

    if flags.get("logs") and events:
        lines.append("Recent logs / events:")
        for ev in events[:10]:
            if not isinstance(ev, dict):
                continue
            lines.append(
                f"- [{ev.get('timestamp')}] {ev.get('severity')} "
                f"{redact_string(ev.get('message'))}"
            )
        lines.append("")

    if flags.get("logs") and related_logs:
        lines.append("Correlated logs:")
        for ev in related_logs[:15]:
            if not isinstance(ev, dict):
                continue
            lines.append(
                f"- [{ev.get('timestamp')}] {ev.get('severity')} "
                f"{redact_string(ev.get('message'))}"
            )
        lines.append("")

    if category_context:
        lines.append("Category-specific evidence:")
        for key in (
            "database",
            "database_detail",
            "websocket",
            "rest",
            "data_health",
            "drilldown",
            "jobs",
            "backups",
        ):
            block = category_context.get(key)
            if not isinstance(block, dict):
                continue
            lines.append(f"- {key}: status={block.get('status')} reason={block.get('reason')}")
            # Keep compact — do not dump entire system
            if key == "database":
                lines.append(
                    f"  latency_ms={block.get('latency_ms')} "
                    f"pool={((block.get('pool') or {}).get('utilization_pct'))}"
                )
            if key == "database_detail":
                slow = (block.get("slowest_queries") or [])[:3]
                lines.append(
                    f"  cached={block.get('cached')} "
                    f"collection_ms={block.get('collection_duration_ms')} "
                    f"tables={len(block.get('tables') or [])} "
                    f"slowest={[s.get('name') for s in slow if isinstance(s, dict)]}"
                )
            if key == "backups":
                last = ((block.get("summary") or {}).get("last_verified_backup")) or {}
                lines.append(
                    f"  latest_verified={last.get('backup_id') or 'NONE'} "
                    f"restore={(block.get('restore_capability') or {}).get('status')}"
                )
            if key == "websocket":
                lines.append(
                    f"  connections={len(block.get('connections') or [])}"
                )
            if key == "rest":
                lines.append(
                    f"  providers={len(block.get('providers') or [])}"
                )
            if key == "drilldown":
                lines.append(
                    f"  symbol={block.get('symbol')} tf={block.get('timeframe')} "
                    f"gaps={block.get('gap_count')} candles={block.get('candle_count')}"
                )
            if key == "jobs":
                lines.append(
                    f"  jobs={len(block.get('jobs') or [])} "
                    f"locks={len(block.get('locks') or [])}"
                )
        lines.append("")

    if flags.get("stack_trace") and issue.get("stack_trace"):
        lines.append("Stack trace:")
        lines.append("```")
        lines.append(redact_string(issue.get("stack_trace")) or "")
        lines.append("```")
        lines.append("")

    if flags.get("service_health") and health:
        lines.append("Related service health:")
        for h in health[:12]:
            if not isinstance(h, dict):
                continue
            lines.append(
                f"- {h.get('label') or h.get('key')}: {h.get('status')} "
                f"— {h.get('reason') or ''}"
            )
        lines.append("")

    if flags.get("resources") and resources:
        disk = resources.get("disk") or {}
        mem = resources.get("memory") or {}
        cpu = resources.get("cpu") or {}
        lines.append("Disk:")
        for d in disk.get("drives") or []:
            lines.append(
                f"- {d.get('drive')}: {d.get('usage_pct')}% used "
                f"(free {d.get('free_bytes')} bytes) [{d.get('status')}]"
            )
        lines.append("")
        lines.append("Memory:")
        lines.append(
            f"- usage_pct={mem.get('usage_pct')} status={mem.get('status')}"
        )
        lines.append("")
        lines.append("CPU:")
        lines.append(
            f"- utilization_pct={cpu.get('utilization_pct')} status={cpu.get('status')}"
        )
        lines.append("")

    if flags.get("git") and git.get("dirty"):
        lines.append("Git worktree DIRTY — modified files:")
        for f in (git.get("modified_files") or [])[:40]:
            lines.append(f"- {f}")
        for f in (git.get("untracked_files") or [])[:20]:
            lines.append(f"- ?? {f}")
        lines.append("")

    lines.append("Files likely involved:")
    if issue.get("file"):
        lines.append(f"- {issue.get('file')}")
    else:
        lines.append("- (exact file unknown — do not invent line numbers)")
    lines.append("")

    lines.append("Safety constraints:")
    for c in SAFETY_CONSTRAINTS:
        lines.append(f"- {c}")
    lines.append("")
    lines.append("TASK FOR AI AGENT:")
    lines.append("1. Reproduce the issue.")
    lines.append("2. Trace the failure.")
    lines.append("3. Identify root cause.")
    lines.append("4. Make the smallest safe fix.")
    lines.append("5. Add regression tests.")
    lines.append("6. Run relevant tests.")
    lines.append("7. Verify no production signal changes.")
    lines.append("8. Report changed files.")
    lines.append("9. Report test results.")
    lines.append("10. Report remaining risks.")
    lines.append("")
    return "\n".join(lines)
