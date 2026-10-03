"""System resource metrics — real measurements only; never fabricate."""

from __future__ import annotations

import os
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.diagnostics.constants import (
    DEFAULT_CPU_ERROR_PCT,
    DEFAULT_CPU_WARNING_PCT,
    DEFAULT_DISK_CRITICAL_PCT,
    DEFAULT_DISK_ERROR_PCT,
    DEFAULT_DISK_WARNING_PCT,
    DEFAULT_MEMORY_CRITICAL_PCT,
    DEFAULT_MEMORY_ERROR_PCT,
    DEFAULT_MEMORY_WARNING_PCT,
    HealthStatus,
)

try:
    import psutil  # type: ignore
except Exception:  # noqa: BLE001
    psutil = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def status_from_pct(
    pct: float | None,
    *,
    warning: float,
    error: float,
    critical: float,
) -> str:
    if pct is None:
        return HealthStatus.UNKNOWN.value
    if pct >= critical:
        return HealthStatus.ERROR.value  # CRITICAL mapped to ERROR severity tier for cards
    if pct >= error:
        return HealthStatus.ERROR.value
    if pct >= warning:
        return HealthStatus.WARNING.value
    return HealthStatus.HEALTHY.value


def disk_severity_label(
    pct: float | None,
    *,
    warning: float,
    error: float,
    critical: float,
) -> str:
    if pct is None:
        return HealthStatus.UNKNOWN.value
    if pct >= critical:
        return "CRITICAL"
    if pct >= error:
        return HealthStatus.ERROR.value
    if pct >= warning:
        return HealthStatus.WARNING.value
    return HealthStatus.HEALTHY.value


class ResourceSampler:
    """Bounded CPU trend + disk/memory snapshots. Expensive checks are throttled."""

    def __init__(self) -> None:
        self._cpu_trend: deque[tuple[float, float]] = deque(maxlen=60)  # (mono, pct)
        self._last_expensive = 0.0
        self._cached: dict[str, Any] | None = None
        self._cache_ttl = 30.0

    def configure(self, *, expensive_interval_seconds: float = 30.0) -> None:
        self._cache_ttl = max(5.0, expensive_interval_seconds)

    def sample(self, settings: Any | None = None) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached is not None and (now - self._last_expensive) < self._cache_ttl:
            # Refresh lightweight CPU if possible
            light = self._light_cpu()
            if light is not None:
                self._cpu_trend.append((now, light))
                self._cached["cpu"] = self._cpu_block(settings)
                self._cached["sampled_at"] = _utcnow_iso()
                self._cached["cached"] = True
            return self._cached

        disk_warn = float(
            getattr(settings, "diag_disk_warning_pct", DEFAULT_DISK_WARNING_PCT)
            if settings
            else DEFAULT_DISK_WARNING_PCT
        )
        disk_err = float(
            getattr(settings, "diag_disk_error_pct", DEFAULT_DISK_ERROR_PCT)
            if settings
            else DEFAULT_DISK_ERROR_PCT
        )
        disk_crit = float(
            getattr(settings, "diag_disk_critical_pct", DEFAULT_DISK_CRITICAL_PCT)
            if settings
            else DEFAULT_DISK_CRITICAL_PCT
        )
        mem_warn = float(
            getattr(settings, "diag_memory_warning_pct", DEFAULT_MEMORY_WARNING_PCT)
            if settings
            else DEFAULT_MEMORY_WARNING_PCT
        )
        mem_err = float(
            getattr(settings, "diag_memory_error_pct", DEFAULT_MEMORY_ERROR_PCT)
            if settings
            else DEFAULT_MEMORY_ERROR_PCT
        )
        mem_crit = float(
            getattr(settings, "diag_memory_critical_pct", DEFAULT_MEMORY_CRITICAL_PCT)
            if settings
            else DEFAULT_MEMORY_CRITICAL_PCT
        )

        payload: dict[str, Any] = {
            "sampled_at": _utcnow_iso(),
            "cached": False,
            "psutil_available": psutil is not None,
            "disk": self._disk_block(disk_warn, disk_err, disk_crit),
            "memory": self._memory_block(mem_warn, mem_err, mem_crit),
            "cpu": None,
            "process": self._process_block(),
            "thresholds": {
                "disk_warning_pct": disk_warn,
                "disk_error_pct": disk_err,
                "disk_critical_pct": disk_crit,
                "memory_warning_pct": mem_warn,
                "memory_error_pct": mem_err,
                "memory_critical_pct": mem_crit,
                "cpu_warning_pct": float(
                    getattr(settings, "diag_cpu_warning_pct", DEFAULT_CPU_WARNING_PCT)
                    if settings
                    else DEFAULT_CPU_WARNING_PCT
                ),
                "cpu_error_pct": float(
                    getattr(settings, "diag_cpu_error_pct", DEFAULT_CPU_ERROR_PCT)
                    if settings
                    else DEFAULT_CPU_ERROR_PCT
                ),
            },
        }
        cpu_pct = self._light_cpu(interval=0.05)
        if cpu_pct is not None:
            self._cpu_trend.append((now, cpu_pct))
        payload["cpu"] = self._cpu_block(settings)

        self._cached = payload
        self._last_expensive = now
        return payload

    def _light_cpu(self, interval: float = 0.0) -> float | None:
        if psutil is None:
            return None
        try:
            return float(psutil.cpu_percent(interval=interval))
        except Exception:  # noqa: BLE001
            return None

    def _cpu_block(self, settings: Any | None) -> dict[str, Any]:
        warn = float(
            getattr(settings, "diag_cpu_warning_pct", DEFAULT_CPU_WARNING_PCT)
            if settings
            else DEFAULT_CPU_WARNING_PCT
        )
        err = float(
            getattr(settings, "diag_cpu_error_pct", DEFAULT_CPU_ERROR_PCT)
            if settings
            else DEFAULT_CPU_ERROR_PCT
        )
        current = self._cpu_trend[-1][1] if self._cpu_trend else None
        # 5-minute trend (samples at ~30s → up to 10 points; we keep mono stamps)
        cutoff = time.monotonic() - 300
        trend = [
            {"t_offset_s": round(t - cutoff, 1), "pct": pct}
            for t, pct in self._cpu_trend
            if t >= cutoff
        ]
        load = None
        if hasattr(os, "getloadavg"):
            try:
                load = list(os.getloadavg())
            except Exception:  # noqa: BLE001
                load = None
        status = status_from_pct(current, warning=warn, error=err, critical=err + 5)
        return {
            "utilization_pct": current,
            "load": load,
            "trend_5m": trend,
            "status": status if current is not None else HealthStatus.UNKNOWN.value,
            "reason": None
            if current is not None
            else "psutil unavailable — CPU not measured",
        }

    def _disk_block(
        self, warn: float, err: float, crit: float
    ) -> dict[str, Any]:
        drives: list[dict[str, Any]] = []
        # Measure the drive hosting the project root
        roots = []
        try:
            from app.config import ROOT

            roots.append(str(ROOT))
        except Exception:  # noqa: BLE001
            roots.append(os.getcwd())
        roots.append(os.path.abspath(os.sep))

        seen: set[str] = set()
        for root in roots:
            try:
                usage = shutil.disk_usage(root)
            except Exception:  # noqa: BLE001
                continue
            # Label by drive letter on Windows, path otherwise
            if os.name == "nt":
                drive = os.path.splitdrive(os.path.abspath(root))[0] or root
            else:
                drive = os.path.abspath(root)
            if drive in seen:
                continue
            seen.add(drive)
            total = usage.total
            used = usage.used
            free = usage.free
            pct = (100.0 * used / total) if total else None
            sev = disk_severity_label(pct, warning=warn, error=err, critical=crit)
            drives.append(
                {
                    "drive": drive,
                    "path": root,
                    "total_bytes": total,
                    "used_bytes": used,
                    "free_bytes": free,
                    "usage_pct": round(pct, 2) if pct is not None else None,
                    "status": sev,
                    "reason": f"usage {pct:.1f}%" if pct is not None else "unmeasured",
                }
            )

        overall = HealthStatus.UNKNOWN.value
        if drives:
            rank = {
                HealthStatus.HEALTHY.value: 0,
                HealthStatus.WARNING.value: 1,
                HealthStatus.ERROR.value: 2,
                "CRITICAL": 3,
            }
            worst = max(drives, key=lambda d: rank.get(d["status"], 0))
            overall = worst["status"]

        return {
            "drives": drives,
            "status": overall,
            "reason": None if drives else "disk usage could not be measured",
        }

    def _memory_block(
        self, warn: float, err: float, crit: float
    ) -> dict[str, Any]:
        if psutil is None:
            return {
                "status": HealthStatus.UNKNOWN.value,
                "reason": "psutil unavailable — memory not measured",
                "total_bytes": None,
                "used_bytes": None,
                "available_bytes": None,
                "usage_pct": None,
            }
        try:
            vm = psutil.virtual_memory()
            pct = float(vm.percent)
            return {
                "total_bytes": int(vm.total),
                "used_bytes": int(vm.used),
                "available_bytes": int(vm.available),
                "free_bytes": int(getattr(vm, "free", 0) or 0),
                "usage_pct": pct,
                "status": disk_severity_label(
                    pct, warning=warn, error=err, critical=crit
                ),
                "reason": f"usage {pct:.1f}%",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": HealthStatus.UNAVAILABLE.value,
                "reason": f"memory sample failed: {exc.__class__.__name__}",
                "total_bytes": None,
                "used_bytes": None,
                "available_bytes": None,
                "usage_pct": None,
            }

    def _process_block(self) -> dict[str, Any]:
        if psutil is None:
            return {
                "pid": os.getpid(),
                "rss_bytes": None,
                "cpu_percent": None,
                "status": HealthStatus.UNKNOWN.value,
                "reason": "psutil unavailable",
            }
        try:
            proc = psutil.Process(os.getpid())
            mem = proc.memory_info()
            return {
                "pid": os.getpid(),
                "rss_bytes": int(mem.rss),
                "vms_bytes": int(mem.vms),
                "cpu_percent": float(proc.cpu_percent(interval=0.0)),
                "create_time": datetime.fromtimestamp(
                    proc.create_time(), tz=timezone.utc
                ).isoformat(),
                "status": HealthStatus.HEALTHY.value,
                "reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "pid": os.getpid(),
                "rss_bytes": None,
                "cpu_percent": None,
                "status": HealthStatus.UNAVAILABLE.value,
                "reason": f"process sample failed: {exc.__class__.__name__}",
            }


resource_sampler = ResourceSampler()
