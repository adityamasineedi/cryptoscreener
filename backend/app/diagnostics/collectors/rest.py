"""REST / provider forensics — measured metrics only; null if not instrumented."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.diagnostics.constants import HealthStatus, worst_status


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pctile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) < 5:
        # Not enough samples for meaningful p95 — return null (UNKNOWN)
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 2)


async def collect_rest_forensics(settings: Any) -> dict[str, Any]:
    from app.core.provider_health import provider_health
    from app.core.rate_limiter import rate_limiters
    from app.core.request_audit import request_audit

    checked = _utcnow_iso()
    out: dict[str, Any] = {
        "phase": 2,
        "last_checked": checked,
        "collector": "diagnostics.collectors.rest.collect_rest_forensics",
        "file": "backend/app/diagnostics/collectors/rest.py",
        "function": "collect_rest_forensics",
        "window_seconds": getattr(request_audit, "_window", 60),
        "providers": [],
        "endpoints": [],
        "rate_limiters": {},
        "status": HealthStatus.UNKNOWN.value,
        "reason": None,
        "issues_candidates": [],
        "errors": [],
    }

    # Provider health registry
    try:
        snapshots = await provider_health.snapshot_all()
    except Exception as exc:  # noqa: BLE001
        snapshots = []
        out["errors"].append({"code": "PROVIDER_HEALTH", "error": str(exc)})

    # Audit window forensics (extend with endpoint breakdown from live records)
    audit_by_provider: dict[str, dict[str, Any]] = {}
    endpoints: list[dict[str, Any]] = []
    try:
        async with request_audit._lock:
            request_audit._prune(__import__("time").monotonic())
            recs = list(request_audit._records)
        by_ep: dict[tuple[str, str], list[Any]] = {}
        for r in recs:
            key = (r.provider, r.endpoint)
            by_ep.setdefault(key, []).append(r)
            p = audit_by_provider.setdefault(
                r.provider,
                {
                    "request_count": 0,
                    "success_count": 0,
                    "failure_count": 0,
                    "count_4xx": 0,
                    "count_429": 0,
                    "count_5xx": 0,
                    "retry_count": 0,
                    "latencies": [],
                },
            )
            p["request_count"] += 1
            if 200 <= r.status < 400:
                p["success_count"] += 1
            else:
                p["failure_count"] += 1
            if 400 <= r.status < 500:
                p["count_4xx"] += 1
            if r.status == 429 or r.count_429:
                p["count_429"] += int(r.count_429 or (1 if r.status == 429 else 0))
            if r.status >= 500:
                p["count_5xx"] += 1
            p["retry_count"] += int(r.retry_count or 0)
            p["latencies"].append(float(r.latency_ms))

        for (prov, ep), rows in by_ep.items():
            lats = [float(x.latency_ms) for x in rows]
            endpoints.append(
                {
                    "provider": prov,
                    "endpoint": ep,
                    "request_count": len(rows),
                    "success_count": sum(1 for x in rows if 200 <= x.status < 400),
                    "failure_count": sum(1 for x in rows if x.status >= 400),
                    "count_4xx": sum(1 for x in rows if 400 <= x.status < 500),
                    "count_429": sum(
                        int(x.count_429 or (1 if x.status == 429 else 0)) for x in rows
                    ),
                    "count_5xx": sum(1 for x in rows if x.status >= 500),
                    "retry_count": sum(int(x.retry_count or 0) for x in rows),
                    "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else None,
                    "p95_latency_ms": _pctile(lats, 95),
                    "last_status": rows[-1].status if rows else None,
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "REQUEST_AUDIT", "error": str(exc)})

    # Rate limiters (registry returns a list — index by name for lookups)
    try:
        rl_rows = rate_limiters.all_snapshots()
        if isinstance(rl_rows, list):
            out["rate_limiters"] = {
                str(row.get("name") or f"limiter_{i}"): row
                for i, row in enumerate(rl_rows)
                if isinstance(row, dict)
            }
        elif isinstance(rl_rows, dict):
            out["rate_limiters"] = rl_rows
        else:
            out["rate_limiters"] = {}
            out["errors"].append(
                {
                    "code": "RATE_LIMITERS",
                    "error": f"unexpected_type:{type(rl_rows).__name__}",
                }
            )
    except Exception as exc:  # noqa: BLE001
        out["errors"].append({"code": "RATE_LIMITERS", "error": str(exc)})
        out["rate_limiters"] = {}

    # Configured providers from yaml (DISABLED if configured but unused)
    configured: set[str] = set()
    try:
        cfg = settings.providers_config or {}
        for key in ("binance", "coingecko", "defillama", "sentiment", "onchain"):
            if key in cfg or any(key in str(k).lower() for k in cfg.keys()):
                configured.add(key)
    except Exception:  # noqa: BLE001
        pass

    # Merge provider rows
    by_name: dict[str, dict[str, Any]] = {}
    if not isinstance(snapshots, list):
        out["errors"].append(
            {
                "code": "PROVIDER_HEALTH",
                "error": f"unexpected_type:{type(snapshots).__name__}",
            }
        )
        snapshots = []
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        name = str(snap.get("provider") or snap.get("name") or "").lower()
        if not name:
            continue
        audit = audit_by_provider.get(name) or audit_by_provider.get(
            name.replace("_rest", "")
        ) or {}
        lats = list(audit.get("latencies") or [])
        status = str(snap.get("status") or HealthStatus.UNKNOWN.value)
        row = {
            "provider": name,
            "status": status,
            "enabled": snap.get("enabled"),
            "request_count": audit.get("request_count", snap.get("requests")),
            "success_count": audit.get("success_count", snap.get("successful")),
            "failure_count": audit.get("failure_count", snap.get("failed")),
            "count_4xx": audit.get("count_4xx"),
            "count_429": audit.get("count_429", snap.get("429_count")),
            "count_5xx": audit.get("count_5xx"),
            "retry_count": audit.get("retry_count"),
            "backoff_count": None,  # not instrumented globally
            "avg_latency_ms": (
                round(sum(lats) / len(lats), 2)
                if lats
                else snap.get("avg_latency_ms")
            ),
            "p95_latency_ms": _pctile(lats, 95),
            "last_success": snap.get("last_success"),
            "last_failure": snap.get("last_error_at") or snap.get("last_error"),
            "last_error": snap.get("last_error"),
            "last_latency_ms": snap.get("last_latency_ms"),
            "current_concurrency": None,
            "queue_depth": None,
            "rate_limit_wait": None,
            "source": "provider_health+request_audit",
        }
        # Enrich concurrency from rate limiter if present
        rl = (out["rate_limiters"] or {}).get(name) or (out["rate_limiters"] or {}).get(
            f"{name}_rest"
        )
        if isinstance(rl, dict):
            row["current_concurrency"] = rl.get("in_flight") or rl.get("concurrency")
            row["rate_limit_wait"] = rl.get("wait_ms") or rl.get("pause_seconds")

        # Issue candidates
        c429 = int(row.get("count_429") or 0)
        c5xx = int(row.get("count_5xx") or 0)
        warn_429 = int(getattr(settings, "diag_rest_429_warning", 5) or 5)
        if c429 >= warn_429:
            out["issues_candidates"].append(
                {
                    "error_code": "PROVIDER_HIGH_429",
                    "severity": "WARNING" if c429 < warn_429 * 3 else "ERROR",
                    "message": f"{name} high 429 count in audit window",
                    "expected": f"429_count < {warn_429}",
                    "actual": str(c429),
                    "provider": name,
                    "details": {
                        "window_seconds": out["window_seconds"],
                        "retry_count": row.get("retry_count"),
                    },
                }
            )
            row["status"] = worst_status(status, HealthStatus.DEGRADED.value)
        if c5xx > 0:
            out["issues_candidates"].append(
                {
                    "error_code": "PROVIDER_5XX",
                    "severity": "ERROR",
                    "message": f"{name} returned 5xx in audit window",
                    "expected": "0 5xx",
                    "actual": str(c5xx),
                    "provider": name,
                }
            )
            row["status"] = worst_status(row["status"], HealthStatus.ERROR.value)
        if status == "UNAVAILABLE":
            out["issues_candidates"].append(
                {
                    "error_code": "PROVIDER_UNAVAILABLE",
                    "severity": "ERROR",
                    "message": f"{name} marked unavailable",
                    "expected": "HEALTHY",
                    "actual": status,
                    "provider": name,
                }
            )
        by_name[name] = row

    # Include audit-only providers
    for name, audit in audit_by_provider.items():
        if name in by_name:
            continue
        lats = list(audit.get("latencies") or [])
        by_name[name] = {
            "provider": name,
            "status": HealthStatus.UNKNOWN.value
            if audit.get("request_count")
            else HealthStatus.WAITING.value,
            "enabled": True,
            "request_count": audit.get("request_count"),
            "success_count": audit.get("success_count"),
            "failure_count": audit.get("failure_count"),
            "count_4xx": audit.get("count_4xx"),
            "count_429": audit.get("count_429"),
            "count_5xx": audit.get("count_5xx"),
            "retry_count": audit.get("retry_count"),
            "backoff_count": None,
            "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else None,
            "p95_latency_ms": _pctile(lats, 95),
            "last_success": None,
            "last_failure": None,
            "last_error": None,
            "current_concurrency": None,
            "queue_depth": None,
            "rate_limit_wait": None,
            "source": "request_audit",
        }

    out["providers"] = sorted(by_name.values(), key=lambda p: p["provider"])
    out["endpoints"] = sorted(
        endpoints, key=lambda e: (-int(e.get("request_count") or 0), e["provider"])
    )

    if out["providers"]:
        out["status"] = worst_status(*(str(p.get("status")) for p in out["providers"]))
        out["reason"] = f"{len(out['providers'])} providers measured"
    else:
        out["status"] = HealthStatus.WAITING.value
        out["reason"] = "No provider traffic recorded yet"

    return out
