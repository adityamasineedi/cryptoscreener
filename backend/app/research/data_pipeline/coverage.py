"""DB-first OHLCV coverage audit for incremental research sync.

PostgreSQL is the source of truth. Manifest is never used to decide existence.
Uses aggregation + optional window-function gap scans — never SELECT * for coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text

from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe
from app.research.data_pipeline.paginator import align_open_ms, now_ms, parse_day_ms
from app.services.database import db_manager

# Availability classifications (section 19)
BEFORE_SYMBOL_AVAILABLE = "BEFORE_SYMBOL_AVAILABLE"
AVAILABLE_AND_PRESENT = "AVAILABLE_AND_PRESENT"
AVAILABLE_BUT_MISSING = "AVAILABLE_BUT_MISSING"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
EXPECTED_UNAVAILABLE_HISTORY = "EXPECTED_UNAVAILABLE_HISTORY"
UNKNOWN = "UNKNOWN"

@dataclass(frozen=True)
class GapRange:
    """Inclusive first-missing open_time → inclusive last-missing open_time."""

    gap_start_ms: int
    gap_end_ms: int
    missing_candle_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_start_ms": self.gap_start_ms,
            "gap_end_ms": self.gap_end_ms,
            "gap_start": _ms_iso(self.gap_start_ms),
            "gap_end": _ms_iso(self.gap_end_ms),
            "missing_candle_count": self.missing_candle_count,
        }

@dataclass
class SeriesCoverage:
    """Aggregated coverage for one (symbol, timeframe) vs a requested window."""

    symbol: str
    timeframe: str
    requested_start_ms: int
    requested_end_ms: int  # exclusive aligned boundary
    existing_min_ms: int | None = None
    existing_max_ms: int | None = None
    row_count: int = 0
    distinct_count: int = 0
    duplicate_count: int = 0
    invalid_ohlc_count: int = 0
    expected_count: int = 0
    missing_count: int = 0
    gaps: list[GapRange] = field(default_factory=list)
    availability: str = UNKNOWN
    listed_at_ms: int | None = None
    manifest_status: str | None = None
    manifest_stale: bool = False

    @property
    def has_data(self) -> bool:
        return self.existing_min_ms is not None and self.row_count > 0

    @property
    def covers_requested(self) -> bool:
        if not self.has_data:
            return False
        assert self.existing_min_ms is not None and self.existing_max_ms is not None
        return (
            self.existing_min_ms <= self.requested_start_ms
            and self.existing_max_ms + _step(self.timeframe)
            >= self.requested_end_ms
            and not self.gaps
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "requested_start": _ms_iso(self.requested_start_ms),
            "requested_end": _ms_iso(self.requested_end_ms),
            "existing_min": _ms_iso(self.existing_min_ms) if self.existing_min_ms else None,
            "existing_max": _ms_iso(self.existing_max_ms) if self.existing_max_ms else None,
            "row_count": self.row_count,
            "distinct_count": self.distinct_count,
            "duplicate_count": self.duplicate_count,
            "invalid_ohlc_count": self.invalid_ohlc_count,
            "expected_count": self.expected_count,
            "missing_count": self.missing_count,
            "gap_count": len(self.gaps),
            "gaps": [g.to_dict() for g in self.gaps[:100]],
            "availability": self.availability,
            "listed_at": _ms_iso(self.listed_at_ms) if self.listed_at_ms else None,
            "manifest_status": self.manifest_status,
            "manifest_stale": self.manifest_stale,
            "covers_requested": self.covers_requested,
        }

def _step(timeframe: str) -> int:
    return TIMEFRAME_MS[normalize_timeframe(timeframe)]

def _ms_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()

def align_requested_range(
    start_day: str,
    end_day: str,
    timeframe: str,
    *,
    clip_to_now: bool = True,
) -> tuple[int, int]:
    """Return (aligned_start_ms inclusive, aligned_end_ms exclusive)."""
    tf = normalize_timeframe(timeframe)
    start_ms = align_open_ms(parse_day_ms(start_day), tf)
    # end_day is inclusive calendar day → exclusive ms after that day
    end_raw = parse_day_ms(end_day) + 86_400_000
    if clip_to_now:
        end_raw = min(end_raw, now_ms())
    end_ms = align_open_ms(end_raw, tf)
    if end_ms <= start_ms:
        end_ms = start_ms
    return start_ms, end_ms

def expected_candle_count(start_ms: int, end_ms: int, timeframe: str) -> int:
    """Count of candle opens in [start_ms, end_ms)."""
    step = _step(timeframe)
    if end_ms <= start_ms:
        return 0
    return max(0, (end_ms - start_ms) // step)

def classify_availability(
    *,
    requested_start_ms: int,
    listed_at_ms: int | None,
    existing_min_ms: int | None,
) -> str:
    if listed_at_ms is not None and requested_start_ms < listed_at_ms:
        if existing_min_ms is None:
            return BEFORE_SYMBOL_AVAILABLE
        if existing_min_ms >= listed_at_ms:
            return AVAILABLE_AND_PRESENT
    if existing_min_ms is None:
        return AVAILABLE_BUT_MISSING if listed_at_ms is None else AVAILABLE_BUT_MISSING
    return AVAILABLE_AND_PRESENT

def effective_request_start(
    requested_start_ms: int,
    listed_at_ms: int | None,
    timeframe: str,
) -> int:
    """Clamp request start to listing date when known (avoid endless pre-listing downloads)."""
    if listed_at_ms is None:
        return requested_start_ms
    listed_aligned = align_open_ms(listed_at_ms, timeframe)
    return max(requested_start_ms, listed_aligned)

def build_coverage_from_stats(
    *,
    symbol: str,
    timeframe: str,
    requested_start_ms: int,
    requested_end_ms: int,
    existing_min_ms: int | None,
    existing_max_ms: int | None,
    row_count: int,
    distinct_count: int,
    invalid_ohlc_count: int = 0,
    gaps: Sequence[GapRange] | None = None,
    listed_at_ms: int | None = None,
    manifest_status: str | None = None,
) -> SeriesCoverage:
    """Pure constructor used by planner unit tests and DB audit."""
    tf = normalize_timeframe(timeframe)
    eff_start = effective_request_start(requested_start_ms, listed_at_ms, tf)
    dup = max(0, int(row_count) - int(distinct_count))
    expected = expected_candle_count(eff_start, requested_end_ms, tf)
    gap_list = list(gaps or [])
    gap_missing = sum(g.missing_candle_count for g in gap_list)

    # Edge missing (when we have a contiguous span assumption from min/max)
    edge_missing = 0
    if existing_min_ms is None:
        missing = expected
    else:
        if existing_min_ms > eff_start:
            edge_missing += expected_candle_count(eff_start, existing_min_ms, tf)
        next_after_max = existing_max_ms + _step(tf) if existing_max_ms is not None else eff_start
        if next_after_max < requested_end_ms:
            edge_missing += expected_candle_count(next_after_max, requested_end_ms, tf)
        missing = edge_missing + gap_missing

    availability = classify_availability(
        requested_start_ms=requested_start_ms,
        listed_at_ms=listed_at_ms,
        existing_min_ms=existing_min_ms,
    )
    # Manifest COMPLETE but DB has more history → stale optimization only
    manifest_stale = False
    if manifest_status == "COMPLETE" and existing_min_ms is not None:
        # If DB covers request, trust DB regardless of manifest dates
        if existing_min_ms <= eff_start and (
            existing_max_ms is not None
            and existing_max_ms + _step(tf) >= requested_end_ms
        ):
            manifest_stale = True

    return SeriesCoverage(
        symbol=symbol.upper(),
        timeframe=tf,
        requested_start_ms=eff_start,
        requested_end_ms=requested_end_ms,
        existing_min_ms=existing_min_ms,
        existing_max_ms=existing_max_ms,
        row_count=int(row_count),
        distinct_count=int(distinct_count),
        duplicate_count=dup,
        invalid_ohlc_count=int(invalid_ohlc_count),
        expected_count=expected,
        missing_count=int(missing),
        gaps=gap_list,
        availability=availability,
        listed_at_ms=listed_at_ms,
        manifest_status=manifest_status,
        manifest_stale=manifest_stale,
    )

def gaps_from_timestamps(
    timestamps_ms: Sequence[int],
    timeframe: str,
    *,
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> list[GapRange]:
    """Detect internal gaps from sorted open times (for tests / small samples)."""
    step = _step(timeframe)
    ts = sorted({int(t) for t in timestamps_ms})
    if window_start_ms is not None:
        ts = [t for t in ts if t >= window_start_ms]
    if window_end_ms is not None:
        ts = [t for t in ts if t < window_end_ms]
    gaps: list[GapRange] = []
    for i in range(len(ts) - 1):
        delta = ts[i + 1] - ts[i]
        missing = (delta // step) - 1
        if missing > 0:
            gaps.append(
                GapRange(
                    gap_start_ms=ts[i] + step,
                    gap_end_ms=ts[i + 1] - step,
                    missing_candle_count=int(missing),
                )
            )
    return gaps

async def audit_series_coverage(
    symbol: str,
    timeframe: str,
    *,
    start_day: str,
    end_day: str,
    listed_at_ms: int | None = None,
    detect_internal_gaps: bool = True,
    manifest_status: str | None = None,
) -> SeriesCoverage:
    """Efficient SQL aggregation coverage check for one series."""
    tf = normalize_timeframe(timeframe)
    req_start, req_end = align_requested_range(start_day, end_day, tf)
    if db_manager.engine is None:
        return build_coverage_from_stats(
            symbol=symbol,
            timeframe=tf,
            requested_start_ms=req_start,
            requested_end_ms=req_end,
            existing_min_ms=None,
            existing_max_ms=None,
            row_count=0,
            distinct_count=0,
            listed_at_ms=listed_at_ms,
            manifest_status=manifest_status,
        )

    eff_start = effective_request_start(req_start, listed_at_ms, tf)
    start_dt = datetime.fromtimestamp(eff_start / 1000.0, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(req_end / 1000.0, tz=timezone.utc)

    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        MIN(time) AS min_t,
                        MAX(time) AS max_t,
                        COUNT(*) AS n,
                        COUNT(DISTINCT time) AS n_distinct,
                        COUNT(*) FILTER (
                            WHERE high < GREATEST(open, close)
                               OR low > LEAST(open, close)
                               OR high < low
                               OR volume < 0
                        ) AS invalid_n
                    FROM ohlcv
                    WHERE symbol = :s
                      AND timeframe = :tf
                      AND time >= :start_t
                      AND time < :end_t
                    """
                ),
                {
                    "s": symbol.upper(),
                    "tf": tf,
                    "start_t": start_dt,
                    "end_t": end_dt,
                },
            )
        ).fetchone()

        # Also get global min/max (may extend outside requested window) — DB truth
        global_row = (
            await conn.execute(
                text(
                    """
                    SELECT MIN(time), MAX(time), COUNT(*), COUNT(DISTINCT time)
                    FROM ohlcv
                    WHERE symbol = :s AND timeframe = :tf
                    """
                ),
                {"s": symbol.upper(), "tf": tf},
            )
        ).fetchone()

    gmin = global_row[0] if global_row else None
    gmax = global_row[1] if global_row else None
    grow = int(global_row[2] or 0) if global_row else 0
    gdist = int(global_row[3] or 0) if global_row else 0

    # Prefer global bounds for edge decisions; window stats for invalids in range
    existing_min = int(gmin.timestamp() * 1000) if gmin else None
    existing_max = int(gmax.timestamp() * 1000) if gmax else None
    invalid_n = int(row[4] or 0) if row else 0

    gaps: list[GapRange] = []
    if detect_internal_gaps and existing_min is not None and existing_max is not None:
        # Scan gaps only inside the overlapping coverage window
        win_lo = max(eff_start, existing_min)
        win_hi = min(req_end, existing_max + _step(tf))
        if win_hi > win_lo:
            gaps = await detect_gaps_sql(
                symbol, tf, window_start_ms=win_lo, window_end_ms=win_hi
            )

    return build_coverage_from_stats(
        symbol=symbol,
        timeframe=tf,
        requested_start_ms=req_start,
        requested_end_ms=req_end,
        existing_min_ms=existing_min,
        existing_max_ms=existing_max,
        row_count=grow,
        distinct_count=gdist,
        invalid_ohlc_count=invalid_n,
        gaps=gaps,
        listed_at_ms=listed_at_ms,
        manifest_status=manifest_status,
    )

async def detect_gaps_sql(
    symbol: str,
    timeframe: str,
    *,
    window_start_ms: int,
    window_end_ms: int,
) -> list[GapRange]:
    """PostgreSQL LAG-based gap detection within a bounded window."""
    if db_manager.engine is None:
        return []
    tf = normalize_timeframe(timeframe)
    step = _step(tf)
    start_dt = datetime.fromtimestamp(window_start_ms / 1000.0, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(window_end_ms / 1000.0, tz=timezone.utc)

    async with db_manager.engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    WITH ordered AS (
                        SELECT
                            time,
                            LAG(time) OVER (
                                PARTITION BY symbol, timeframe
                                ORDER BY time
                            ) AS prev_time
                        FROM ohlcv
                        WHERE symbol = :s
                          AND timeframe = :tf
                          AND time >= :start_t
                          AND time < :end_t
                    )
                    SELECT prev_time, time
                    FROM ordered
                    WHERE prev_time IS NOT NULL
                      AND EXTRACT(EPOCH FROM (time - prev_time)) * 1000.0 > :step_ms
                    ORDER BY prev_time
                    """
                ),
                {
                    "s": symbol.upper(),
                    "tf": tf,
                    "start_t": start_dt,
                    "end_t": end_dt,
                    "step_ms": float(step),
                },
            )
        ).fetchall()

    gaps: list[GapRange] = []
    for prev_t, cur_t in rows:
        prev_ms = int(prev_t.timestamp() * 1000)
        cur_ms = int(cur_t.timestamp() * 1000)
        missing = ((cur_ms - prev_ms) // step) - 1
        if missing > 0:
            gaps.append(
                GapRange(
                    gap_start_ms=prev_ms + step,
                    gap_end_ms=cur_ms - step,
                    missing_candle_count=int(missing),
                )
            )
    return gaps

async def recheck_range_covered(
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> bool:
    """True if every expected candle open in [start_ms, end_ms) exists in DB."""
    if db_manager.engine is None:
        return False
    tf = normalize_timeframe(timeframe)
    step = _step(tf)
    expected = expected_candle_count(start_ms, end_ms, tf)
    if expected <= 0:
        return True
    start_dt = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc)
    async with db_manager.engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT time)
                    FROM ohlcv
                    WHERE symbol = :s AND timeframe = :tf
                      AND time >= :start_t AND time < :end_t
                    """
                ),
                {
                    "s": symbol.upper(),
                    "tf": tf,
                    "start_t": start_dt,
                    "end_t": end_dt,
                },
            )
        ).fetchone()
    have = int(row[0] or 0) if row else 0
    # Allow tiny boundary skew of 0; require full distinct count
    return have >= expected and step > 0

