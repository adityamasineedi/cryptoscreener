"""Authoritative research-data inventory and dataset versioning."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from app.research.data_pipeline.config import PIPELINE_VERSION, QUALITY_PASS
from app.research.data_pipeline.checkpoint import SeriesCheckpoint


@dataclass
class SeriesManifestEntry:
    symbol: str
    timeframe: str
    first: str | None
    last: str | None
    candles: int
    gaps: int
    duplicates: int
    invalid_rows: int
    quality: str
    status: str
    available_days: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "first": self.first,
            "last": self.last,
            "candles": self.candles,
            "gaps": self.gaps,
            "duplicates": self.duplicates,
            "invalid_rows": self.invalid_rows,
            "quality": self.quality,
            "status": self.status,
            "available_days": self.available_days,
        }


@dataclass
class DatasetQualityReport:
    dataset_version: str
    symbols_requested: list[str]
    symbols_eligible: list[str]
    symbols_excluded: list[str]
    symbols_failed: list[str]
    timeframes: list[str]
    first_date: str | None
    last_date: str | None
    total_candles: int
    total_gaps: int
    total_duplicates: int
    total_invalid: int
    series: list[SeriesManifestEntry] = field(default_factory=list)
    per_timeframe: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    pipeline_version: str = PIPELINE_VERSION
    note: str = (
        "Real Binance USDⓈ-M Futures klines only. "
        "Gaps recorded, never fabricated. Research-only inventory."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": self.dataset_version,
            "symbols_requested": self.symbols_requested,
            "eligible_symbols": len(self.symbols_eligible),
            "excluded_symbols": len(self.symbols_excluded),
            "failed_symbols": self.symbols_failed,
            "symbols_eligible": self.symbols_eligible,
            "symbols_excluded": self.symbols_excluded,
            "timeframes": self.timeframes,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "total_candles": self.total_candles,
            "total_gaps": self.total_gaps,
            "total_duplicates": self.total_duplicates,
            "total_invalid": self.total_invalid,
            "per_timeframe": self.per_timeframe,
            "series": [s.to_dict() for s in self.series],
            "created_at": self.created_at,
            "pipeline_version": self.pipeline_version,
            "note": self.note,
            "fabricated": False,
            "interpolated": False,
        }


def make_dataset_version(label: str | None = None) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = label or "v1"
    return f"{day}_{suffix}"


def entry_from_checkpoint(cp: SeriesCheckpoint, bars: int | None = None) -> SeriesManifestEntry:
    candles = bars if bars is not None else cp.candles_downloaded
    available_days = None
    if cp.actual_first and cp.actual_last:
        try:
            a = datetime.fromisoformat(cp.actual_first[:10]).replace(tzinfo=timezone.utc)
            b = datetime.fromisoformat(cp.actual_last[:10]).replace(tzinfo=timezone.utc)
            available_days = max(0.0, (b - a).total_seconds() / 86400.0)
        except ValueError:
            available_days = None
    return SeriesManifestEntry(
        symbol=cp.symbol,
        timeframe=cp.timeframe,
        first=cp.actual_first,
        last=cp.actual_last,
        candles=candles,
        gaps=cp.gap_count,
        duplicates=cp.duplicate_count,
        invalid_rows=cp.invalid_rows,
        quality=cp.quality or ("FAIL" if cp.status == "FAILED" else QUALITY_PASS),
        status=cp.status,
        available_days=available_days,
    )


def build_quality_report(
    *,
    dataset_version: str,
    checkpoints: Sequence[SeriesCheckpoint],
    symbols_requested: Sequence[str],
    timeframes: Sequence[str],
    bars_by_key: dict[tuple[str, str], int] | None = None,
) -> DatasetQualityReport:
    bars_by_key = bars_by_key or {}
    series = [
        entry_from_checkpoint(cp, bars_by_key.get((cp.symbol, cp.timeframe)))
        for cp in checkpoints
    ]
    eligible = sorted(
        {
            e.symbol
            for e in series
            if e.candles > 0 and e.status not in ("FAILED", "NO_HISTORY")
        }
    )
    failed = sorted({e.symbol for e in series if e.status == "FAILED"})
    excluded = sorted(set(symbols_requested) - set(eligible))
    firsts = [e.first for e in series if e.first]
    lasts = [e.last for e in series if e.last]
    per_tf: dict[str, Any] = {}
    for tf in timeframes:
        rows = [e for e in series if e.timeframe == tf]
        hist = [e.available_days for e in rows if e.available_days is not None]
        hist_sorted = sorted(hist)
        median = (
            hist_sorted[len(hist_sorted) // 2] if hist_sorted else None
        )
        avg = (sum(hist_sorted) / len(hist_sorted)) if hist_sorted else None
        per_tf[tf] = {
            "series": len(rows),
            "eligible": sum(1 for e in rows if e.candles > 0),
            "candles": sum(e.candles for e in rows),
            "gaps": sum(e.gaps for e in rows),
            "duplicates": sum(e.duplicates for e in rows),
            "invalid_rows": sum(e.invalid_rows for e in rows),
            "average_history_days": avg,
            "median_history_days": median,
        }
    return DatasetQualityReport(
        dataset_version=dataset_version,
        symbols_requested=list(symbols_requested),
        symbols_eligible=eligible,
        symbols_excluded=excluded,
        symbols_failed=failed,
        timeframes=list(timeframes),
        first_date=min(firsts) if firsts else None,
        last_date=max(lasts) if lasts else None,
        total_candles=sum(e.candles for e in series),
        total_gaps=sum(e.gaps for e in series),
        total_duplicates=sum(e.duplicates for e in series),
        total_invalid=sum(e.invalid_rows for e in series),
        series=series,
        per_timeframe=per_tf,
    )


def data_fingerprint(report: DatasetQualityReport) -> str:
    payload = {
        "dataset_version": report.dataset_version,
        "total_candles": report.total_candles,
        "timeframes": report.timeframes,
        "eligible": report.symbols_eligible,
        "first": report.first_date,
        "last": report.last_date,
        "per_timeframe": {
            k: {"candles": v.get("candles"), "eligible": v.get("eligible")}
            for k, v in report.per_timeframe.items()
        },
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
