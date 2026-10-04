"""Canonical SHORT research windows — Policy A strict disjoint partitions.

Policy A (default):
    base_end < oos_dev_start
    oos_dev_end < oos_val_start

Each partition uses inclusive calendar endpoints:
    partition_start <= candle_date <= partition_end

Adjacent-day boundaries are valid (e.g. base_end=2025-04-30,
oos_dev_start=2025-05-01). Candle-time assertions use strict ``<`` across
partition series maxima/minima.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.research.combo02_candidate_thresholds import ResearchWindowConfig
from app.research.short_research_constants import DIRECTION, SETUP_TIMEFRAME

WINDOW_POLICY = "POLICY_A_STRICT_DISJOINT"
BOUNDARY_RULE = (
    "inclusive within partition; cross-partition requires "
    "base_end < oos_dev_start and oos_dev_end < oos_val_start "
    "(calendar dates). Series assertion: max(earlier) < min(later)."
)


@dataclass(frozen=True)
class ResearchWindows:
    """Canonical SHORT research calendar windows (UTC dates)."""

    requested_start: str
    requested_end: str
    base_start: str
    base_end: str
    oos_dev_start: str
    oos_dev_end: str
    oos_val_start: str
    oos_val_end: str
    policy: str = WINDOW_POLICY
    boundary_rule: str = BOUNDARY_RULE
    oos_policy: str = "VALIDATION_DETERMINES_STATUS"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Training / development / validation — no shared candles.
DEFAULT_SHORT_RESEARCH_WINDOWS = ResearchWindows(
    requested_start="2025-01-01",
    requested_end="2026-01-31",
    base_start="2025-01-01",
    base_end="2025-04-30",
    oos_dev_start="2025-05-01",
    oos_dev_end="2025-06-30",
    oos_val_start="2025-07-01",
    oos_val_end="2026-01-31",
)


def _day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10] + "T00:00:00+00:00")
    except ValueError:
        return None


def _next_day_str(value: str) -> str:
    d = _day(value)
    if d is None:
        return value
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def research_windows_from_config(
    window: ResearchWindowConfig,
    *,
    oos_dev_start: str | None = None,
) -> ResearchWindows:
    """Map ResearchWindowConfig → ResearchWindows (Policy A).

    Derives ``oos_dev_start`` as the calendar day after ``base_end`` when not
    supplied. Does **not** silently repair overlapping windows — callers must
    run ``validate_research_windows`` and fail closed on INVALID_OVERLAP.

    When ``base_end == oos_dev_end`` (legacy two-partition shape), returns
    DEFAULT_SHORT_RESEARCH_WINDOWS so SHORT research uses the canonical
    three-way train/dev/val split.
    """
    if str(window.base_end) == str(window.oos_dev_end):
        return DEFAULT_SHORT_RESEARCH_WINDOWS

    base_start = window.base_start
    base_end = window.base_end
    oos_val_start = window.oos_val_start
    oos_val_end = window.oos_val_end
    oos_dev_end = window.oos_dev_end
    dev_start = oos_dev_start or _next_day_str(base_end)

    return ResearchWindows(
        requested_start=min(base_start, oos_val_start),
        requested_end=max(oos_val_end, oos_dev_end),
        base_start=base_start,
        base_end=base_end,
        oos_dev_start=dev_start,
        oos_dev_end=oos_dev_end,
        oos_val_start=oos_val_start,
        oos_val_end=oos_val_end,
    )


def short_research_window_config(
    windows: ResearchWindows = DEFAULT_SHORT_RESEARCH_WINDOWS,
    *,
    risk_usd: float = 20.0,
    principal_usd: float = 1000.0,
) -> ResearchWindowConfig:
    """ResearchWindowConfig aligned to canonical SHORT Policy A windows."""
    return ResearchWindowConfig(
        base_start=windows.base_start,
        base_end=windows.base_end,
        oos_dev_end=windows.oos_dev_end,
        oos_val_start=windows.oos_val_start,
        oos_val_end=windows.oos_val_end,
        risk_usd=risk_usd,
        principal_usd=principal_usd,
        combination_id="COMBO_02",
        direction=DIRECTION,
        setup_timeframe=SETUP_TIMEFRAME,
        require_htf_alignment=True,
    )


def validate_research_windows(windows: ResearchWindows) -> dict[str, Any]:
    """Fail closed on overlapping partitions."""
    b0, b1 = _day(windows.base_start), _day(windows.base_end)
    d0, d1 = _day(windows.oos_dev_start), _day(windows.oos_dev_end)
    v0, v1 = _day(windows.oos_val_start), _day(windows.oos_val_end)
    errors: list[str] = []
    if not all([b0, b1, d0, d1, v0, v1]):
        errors.append("unparseable_window_dates")
    else:
        assert b0 and b1 and d0 and d1 and v0 and v1
        if b1 < b0:
            errors.append("base_inverted")
        if d1 < d0:
            errors.append("oos_dev_inverted")
        if v1 < v0:
            errors.append("oos_val_inverted")
        # Policy A: strict disjoint by calendar date
        if not (b1 < d0):
            errors.append("base_overlaps_oos_dev")
        if not (d1 < v0):
            errors.append("oos_dev_overlaps_oos_val")
        if not (b1 < v0):
            errors.append("base_overlaps_oos_val")

    ok = not errors
    return {
        "ok": ok,
        "window_status": "OK" if ok else "INVALID_OVERLAP",
        "oos_status": "OK" if ok else "INVALID_SPLIT",
        "research_quality": None if ok else "REVIEW_REQUIRED",
        "policy": windows.policy,
        "boundary_rule": windows.boundary_rule,
        "oos_policy": windows.oos_policy,
        "errors": errors,
        "windows": windows.to_dict(),
        "paper_eligible": False,
    }


def _parse_candle_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.fromisoformat(str(value)[:10] + "T00:00:00+00:00")
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def filter_candles_to_window(
    candles: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """Inclusive calendar filter: start <= candle_date <= end."""
    s, e = _day(start), _day(end)
    out: list[dict[str, Any]] = []
    for c in candles:
        ts = _parse_candle_time(c.get("time") if isinstance(c, Mapping) else None)
        if ts is None or s is None or e is None:
            continue
        day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        if s <= day <= e:
            out.append(dict(c))
    return out


def partition_candles(
    candles: Sequence[Mapping[str, Any]],
    windows: ResearchWindows,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "base": filter_candles_to_window(
            candles, start=windows.base_start, end=windows.base_end
        ),
        "oos_dev": filter_candles_to_window(
            candles, start=windows.oos_dev_start, end=windows.oos_dev_end
        ),
        "oos_val": filter_candles_to_window(
            candles, start=windows.oos_val_start, end=windows.oos_val_end
        ),
    }


def assert_partition_time_order(
    base: Sequence[Mapping[str, Any]],
    oos_dev: Sequence[Mapping[str, Any]],
    oos_val: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assert max(earlier) < min(later). Empty partitions skip comparisons."""

    def _times(rows: Sequence[Mapping[str, Any]]) -> list[datetime]:
        out: list[datetime] = []
        for c in rows:
            ts = _parse_candle_time(c.get("time"))
            if ts is not None:
                out.append(ts)
        return out

    bt, dt, vt = _times(base), _times(oos_dev), _times(oos_val)
    errors: list[str] = []
    if bt and dt and max(bt) >= min(dt):
        errors.append("base_not_before_oos_dev")
    if dt and vt and max(dt) >= min(vt):
        errors.append("oos_dev_not_before_oos_val")
    if bt and vt and max(bt) >= min(vt):
        errors.append("base_not_before_oos_val")
    return {
        "ok": not errors,
        "errors": errors,
        "base_bars": len(bt),
        "oos_dev_bars": len(dt),
        "oos_val_bars": len(vt),
        "max_base": max(bt).isoformat() if bt else None,
        "min_oos_dev": min(dt).isoformat() if dt else None,
        "max_oos_dev": max(dt).isoformat() if dt else None,
        "min_oos_val": min(vt).isoformat() if vt else None,
    }
