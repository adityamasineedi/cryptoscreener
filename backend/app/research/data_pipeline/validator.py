"""Chunk-level OHLCV validation. Never silently repairs invalid data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe
from app.research.data_pipeline.config import GAP_DETECTED, QUALITY_FAIL, QUALITY_GAPS, QUALITY_PASS


@dataclass
class GapRecord:
    start_ms: int
    end_ms: int
    duration_ms: int
    expected_candles: int
    missing_candles: int
    kind: str = GAP_DETECTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start": datetime.fromtimestamp(self.start_ms / 1000, tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(self.end_ms / 1000, tz=timezone.utc).isoformat(),
            "duration_ms": self.duration_ms,
            "expected_candles": self.expected_candles,
            "missing_candles": self.missing_candles,
        }


@dataclass
class ValidationReport:
    symbol: str
    timeframe: str
    quality: str
    candle_count: int = 0
    duplicate_count: int = 0
    invalid_ohlc: int = 0
    future_timestamps: int = 0
    unordered: bool = False
    gaps: list[GapRecord] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    first_ms: int | None = None
    last_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "quality": self.quality,
            "candle_count": self.candle_count,
            "duplicate_count": self.duplicate_count,
            "invalid_ohlc": self.invalid_ohlc,
            "future_timestamps": self.future_timestamps,
            "unordered": self.unordered,
            "gap_count": len(self.gaps),
            "gaps": [g.to_dict() for g in self.gaps[:50]],
            "problems": self.problems,
            "first_ms": self.first_ms,
            "last_ms": self.last_ms,
            "fabricated": False,
            "interpolated": False,
        }


def _ts_ms(row: Mapping[str, Any]) -> int | None:
    if "open_time_ms" in row and row["open_time_ms"] is not None:
        return int(row["open_time_ms"])
    raw = row.get("time") or row.get("open_time") or row.get("timestamp")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v if v > 1e12 else v * 1000
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def _ohlc_ok(row: Mapping[str, Any]) -> bool:
    try:
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        v = float(row.get("volume") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    if o <= 0 or h <= 0 or l <= 0 or c <= 0 or v < 0:
        return False
    if h < max(o, c) or l > min(o, c) or h < l:
        return False
    return True


def validate_candles(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    now_ms: int | None = None,
) -> ValidationReport:
    """Validate ordering, OHLC integrity, duplicates, gaps. Does not repair."""
    tf = normalize_timeframe(timeframe)
    report = ValidationReport(symbol=symbol.upper(), timeframe=tf, quality=QUALITY_PASS)
    if not candles:
        report.quality = QUALITY_FAIL
        report.problems.append("empty_chunk")
        return report

    step = TIMEFRAME_MS.get(tf)
    if step is None:
        report.quality = QUALITY_FAIL
        report.problems.append(f"unsupported_timeframe:{tf}")
        return report

    now = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    timestamps: list[int] = []
    seen: set[int] = set()

    for row in candles:
        ts = _ts_ms(row)
        if ts is None:
            report.invalid_ohlc += 1
            report.problems.append("missing_timestamp")
            continue
        if ts in seen:
            report.duplicate_count += 1
        else:
            seen.add(ts)
            timestamps.append(ts)
        if ts > now + step:
            report.future_timestamps += 1
            report.problems.append(f"future_timestamp:{ts}")
        if not _ohlc_ok(row):
            report.invalid_ohlc += 1

    report.candle_count = len(timestamps)
    if not timestamps:
        report.quality = QUALITY_FAIL
        report.problems.append("no_valid_timestamps")
        return report

    if any(timestamps[i] > timestamps[i + 1] for i in range(len(timestamps) - 1)):
        report.unordered = True
        report.problems.append("timestamps_not_ascending")
        timestamps = sorted(timestamps)

    report.first_ms = timestamps[0]
    report.last_ms = timestamps[-1]

    for i in range(len(timestamps) - 1):
        gap = timestamps[i + 1] - timestamps[i]
        missing = int(round(gap / step)) - 1
        if missing > 0:
            report.gaps.append(
                GapRecord(
                    start_ms=timestamps[i],
                    end_ms=timestamps[i + 1],
                    duration_ms=gap,
                    expected_candles=missing + 1,
                    missing_candles=missing,
                )
            )

    if report.invalid_ohlc or report.future_timestamps or report.unordered:
        report.quality = QUALITY_FAIL
    elif report.duplicate_count:
        report.quality = QUALITY_FAIL
        report.problems.append(f"duplicates={report.duplicate_count}")
    elif report.gaps:
        report.quality = QUALITY_GAPS
        report.problems.append(f"{GAP_DETECTED}:{len(report.gaps)}")
    else:
        report.quality = QUALITY_PASS

    return report


def filter_valid_rows(
    candles: Sequence[Mapping[str, Any]],
    *,
    now_ms: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return only structurally valid rows; record rejection reasons (no repair)."""
    now = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    good: list[dict[str, Any]] = []
    reasons: list[str] = []
    seen: set[int] = set()
    for row in candles:
        ts = _ts_ms(row)
        if ts is None:
            reasons.append("reject:missing_timestamp")
            continue
        if ts in seen:
            reasons.append(f"reject:duplicate:{ts}")
            continue
        if not _ohlc_ok(row):
            reasons.append(f"reject:bad_ohlc:{ts}")
            continue
        if ts > now + 86_400_000:
            reasons.append(f"reject:future:{ts}")
            continue
        seen.add(ts)
        good.append(dict(row))
    return good, reasons
