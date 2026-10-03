"""Download plan generator — DB coverage drives what is fetched.

Cases A–F from the incremental sync spec. Deterministic for identical coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe
from app.research.data_pipeline.coverage import (
    EXPECTED_UNAVAILABLE_HISTORY,
    SeriesCoverage,
    expected_candle_count,
)

ACTION_SKIP = "SKIP"
ACTION_DOWNLOAD = "DOWNLOAD"
ACTION_REPAIR_GAPS = "REPAIR_GAPS"
ACTION_UNAVAILABLE = "UNAVAILABLE"

REASON_FULL = "FULL_RANGE"
REASON_LEFT = "LEFT_EDGE"
REASON_RIGHT = "RIGHT_EDGE"
REASON_GAP = "INTERNAL_GAP"
REASON_COVERED = "ALREADY_COVERED"
REASON_MANIFEST_STALE_DB_OK = "MANIFEST_STALE_DB_COMPLETE"
REASON_BEFORE_LISTING = "BEFORE_SYMBOL_AVAILABLE"

@dataclass(frozen=True)
class DownloadRange:
    start_ms: int  # inclusive first open_time to fetch
    end_ms: int  # exclusive end boundary
    reason: str
    estimated_candles: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "start": _iso(self.start_ms),
            "end": _iso(self.end_ms),
            "reason": self.reason,
            "estimated_candles": self.estimated_candles,
        }

@dataclass
class DownloadPlan:
    symbol: str
    timeframe: str
    action: str
    ranges: list[DownloadRange] = field(default_factory=list)
    coverage: SeriesCoverage | None = None
    notes: list[str] = field(default_factory=list)
    estimated_candles: int = 0
    estimated_requests: int = 0
    case: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "action": self.action,
            "case": self.case,
            "estimated_candles": self.estimated_candles,
            "estimated_requests": self.estimated_requests,
            "ranges": [r.to_dict() for r in self.ranges],
            "notes": self.notes,
            "coverage": self.coverage.to_dict() if self.coverage else None,
        }

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()

def _step(tf: str) -> int:
    return TIMEFRAME_MS[normalize_timeframe(tf)]

def _est_requests(candles: int, kline_limit: int = 1500) -> int:
    if candles <= 0:
        return 0
    return max(1, (candles + kline_limit - 1) // kline_limit)

def _merge_ranges(
    ranges: Sequence[DownloadRange],
    *,
    timeframe: str,
) -> list[DownloadRange]:
    if not ranges:
        return []
    out: list[DownloadRange] = []
    cur = ranges[0]
    for nxt in ranges[1:]:
        if nxt.start_ms <= cur.end_ms:
            end = max(cur.end_ms, nxt.end_ms)
            reason = cur.reason if cur.reason == nxt.reason else "MERGED"
            cur = DownloadRange(
                start_ms=cur.start_ms,
                end_ms=end,
                reason=reason,
                estimated_candles=expected_candle_count(cur.start_ms, end, timeframe),
            )
        else:
            out.append(cur)
            cur = nxt
    out.append(cur)
    return out

def plan_from_coverage(
    coverage: SeriesCoverage,
    *,
    kline_limit: int = 1500,
    scan_gaps: bool = True,
) -> DownloadPlan:
    """Deterministic download plan from a SeriesCoverage snapshot."""
    sym = coverage.symbol
    tf = coverage.timeframe
    step = _step(tf)
    req_lo = coverage.requested_start_ms
    req_hi = coverage.requested_end_ms

    # Entire window before listing (clamped start >= end) → nothing to fetch
    if req_lo >= req_hi:
        return DownloadPlan(
            symbol=sym,
            timeframe=tf,
            action=ACTION_UNAVAILABLE,
            coverage=coverage,
            notes=[REASON_BEFORE_LISTING, EXPECTED_UNAVAILABLE_HISTORY],
            case="A_UNAVAILABLE",
        )

    # CASE A — no data (start already clamped to listing when known)
    if not coverage.has_data:
        n = expected_candle_count(req_lo, req_hi, tf)
        rng = DownloadRange(
            start_ms=req_lo,
            end_ms=req_hi,
            reason=REASON_FULL,
            estimated_candles=n,
        )
        return DownloadPlan(
            symbol=sym,
            timeframe=tf,
            action=ACTION_DOWNLOAD,
            ranges=[rng],
            coverage=coverage,
            estimated_candles=n,
            estimated_requests=_est_requests(n, kline_limit),
            case="A",
            notes=["No existing data — download full requested range"],
        )

    assert coverage.existing_min_ms is not None
    assert coverage.existing_max_ms is not None
    emin = coverage.existing_min_ms
    emax = coverage.existing_max_ms
    ranges: list[DownloadRange] = []
    notes: list[str] = []
    case_parts: list[str] = []

    # CASE C — missing left edge
    if emin > req_lo:
        n = expected_candle_count(req_lo, emin, tf)
        if n > 0:
            ranges.append(
                DownloadRange(
                    start_ms=req_lo,
                    end_ms=emin,
                    reason=REASON_LEFT,
                    estimated_candles=n,
                )
            )
            case_parts.append("C")
            notes.append(f"LEFT_EDGE {n} candles")

    # CASE D — missing right edge
    next_needed = emax + step
    if next_needed < req_hi:
        n = expected_candle_count(next_needed, req_hi, tf)
        if n > 0:
            ranges.append(
                DownloadRange(
                    start_ms=next_needed,
                    end_ms=req_hi,
                    reason=REASON_RIGHT,
                    estimated_candles=n,
                )
            )
            case_parts.append("D")
            notes.append(f"RIGHT_EDGE {n} candles")

    # CASE E — internal gaps
    if scan_gaps and coverage.gaps:
        for g in coverage.gaps:
            g_start = max(g.gap_start_ms, req_lo)
            g_end_excl = min(g.gap_end_ms + step, req_hi)
            if g_end_excl <= g_start:
                continue
            n = expected_candle_count(g_start, g_end_excl, tf)
            if n <= 0:
                continue
            ranges.append(
                DownloadRange(
                    start_ms=g_start,
                    end_ms=g_end_excl,
                    reason=REASON_GAP,
                    estimated_candles=n,
                )
            )
        if any(r.reason == REASON_GAP for r in ranges):
            case_parts.append("E")
            notes.append(
                f"INTERNAL_GAPS {sum(1 for r in ranges if r.reason == REASON_GAP)}"
            )

    ranges = _merge_ranges(
        sorted(ranges, key=lambda r: (r.start_ms, r.end_ms)),
        timeframe=tf,
    )
    total = sum(r.estimated_candles for r in ranges)

    if not ranges:
        # CASE B or F
        case = "F" if coverage.manifest_stale else "B"
        if coverage.manifest_stale:
            notes.append(
                "Manifest stale/missing but DB covers requested range — SKIP"
            )
        else:
            notes.append("Existing data completely covers requested range — SKIP")
        return DownloadPlan(
            symbol=sym,
            timeframe=tf,
            action=ACTION_SKIP,
            ranges=[],
            coverage=coverage,
            case=case,
            notes=notes or [REASON_COVERED],
        )

    action = ACTION_REPAIR_GAPS if case_parts == ["E"] else ACTION_DOWNLOAD
    return DownloadPlan(
        symbol=sym,
        timeframe=tf,
        action=action,
        ranges=ranges,
        coverage=coverage,
        estimated_candles=total,
        estimated_requests=_est_requests(total, kline_limit),
        case="+".join(case_parts) if case_parts else "A",
        notes=notes,
    )

def plan_universe(
    coverages_or_plans: Sequence[SeriesCoverage | DownloadPlan],
    *,
    kline_limit: int = 1500,
) -> dict[str, Any]:
    """Aggregate plans for dry-run reporting.

    Accepts either SeriesCoverage snapshots or already-built DownloadPlan objects.
    """
    plans: list[DownloadPlan] = []
    for item in coverages_or_plans:
        if isinstance(item, DownloadPlan):
            plans.append(item)
        else:
            plans.append(plan_from_coverage(item, kline_limit=kline_limit))
    by_tf: dict[str, dict[str, Any]] = {}
    total_existing = 0
    total_missing = 0
    total_requests = 0
    for p in plans:
        tf = p.timeframe
        bucket = by_tf.setdefault(
            tf,
            {
                "already_covered": 0,
                "to_download": 0,
                "missing_ranges": 0,
                "estimated_candles": 0,
                "estimated_requests": 0,
                "symbols_skipped": 0,
                "symbols_download": 0,
            },
        )
        if p.coverage:
            total_existing += p.coverage.row_count
        if p.action == ACTION_SKIP:
            bucket["already_covered"] += 1
            bucket["symbols_skipped"] += 1
        else:
            bucket["to_download"] += 1
            bucket["symbols_download"] += 1
            bucket["missing_ranges"] += len(p.ranges)
            bucket["estimated_candles"] += p.estimated_candles
            bucket["estimated_requests"] += p.estimated_requests
            total_missing += p.estimated_candles
            total_requests += p.estimated_requests

    return {
        "plans": [p.to_dict() for p in plans],
        "by_timeframe": by_tf,
        "total_existing_candles": total_existing,
        "total_missing_candles": total_missing,
        "estimated_binance_requests": total_requests,
        "symbols": len({p.symbol for p in plans}),
        "series": len(plans),
    }

def format_console_coverage(plan: DownloadPlan) -> str:
    def fmt(ms: int | None) -> str:
        if ms is None:
            return "—"
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")

    cov = plan.coverage
    lines = [f"{plan.symbol} {plan.timeframe}", "COVERAGE"]
    if cov:
        lines.append(f"DB: {fmt(cov.existing_min_ms)} -> {fmt(cov.existing_max_ms)}")
        lines.append(
            f"Requested: {fmt(cov.requested_start_ms)} -> {fmt(cov.requested_end_ms)}"
        )
        lines.append(f"Missing: {cov.missing_count}")
    lines.append(f"Action: {plan.action}")
    if plan.estimated_candles:
        lines.append(f"Estimated candles: {plan.estimated_candles:,}")
    for r in plan.ranges[:5]:
        lines.append(
            f"  {r.reason}: {fmt(r.start_ms)} -> {fmt(r.end_ms)} ({r.estimated_candles:,})"
        )
    return "\n".join(lines)

