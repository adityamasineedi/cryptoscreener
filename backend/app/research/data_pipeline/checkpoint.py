"""Resumable per-series download checkpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.research.data_pipeline.config import (
    STATUS_COMPLETE,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_NO_HISTORY,
    STATUS_PARTIAL,
    STATUS_PENDING,
)


@dataclass
class SeriesCheckpoint:
    symbol: str
    timeframe: str
    requested_start: str
    requested_end: str
    actual_first: str | None = None
    actual_last: str | None = None
    candles_downloaded: int = 0
    chunks_completed: int = 0
    last_successful_chunk: int | None = None
    last_successful_chunk_end_ms: int | None = None
    status: str = STATUS_PENDING
    last_error: str | None = None
    quality: str | None = None
    gap_count: int = 0
    duplicate_count: int = 0
    invalid_rows: int = 0
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_downloading(self) -> None:
        self.status = STATUS_DOWNLOADING
        self.touch()

    def mark_chunk_done(self, chunk_index: int, end_ms: int, written: int) -> None:
        self.chunks_completed += 1
        self.last_successful_chunk = chunk_index
        self.last_successful_chunk_end_ms = end_ms
        self.candles_downloaded += written
        self.touch()

    def mark_complete(self) -> None:
        self.status = STATUS_COMPLETE
        self.last_error = None
        self.touch()

    def mark_partial(self, error: str | None = None) -> None:
        self.status = STATUS_PARTIAL
        if error:
            self.last_error = error
        self.touch()

    def mark_failed(self, error: str) -> None:
        self.status = STATUS_FAILED
        self.last_error = error
        self.touch()

    def mark_no_history(self) -> None:
        self.status = STATUS_NO_HISTORY
        self.touch()

    def resume_after_ms(self) -> int | None:
        if self.status in (STATUS_COMPLETE, STATUS_NO_HISTORY):
            return None
        return self.last_successful_chunk_end_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "actual_first": self.actual_first,
            "actual_last": self.actual_last,
            "candles_downloaded": self.candles_downloaded,
            "chunks_completed": self.chunks_completed,
            "last_successful_chunk": self.last_successful_chunk,
            "last_successful_chunk_end_ms": self.last_successful_chunk_end_ms,
            "status": self.status,
            "last_error": self.last_error,
            "quality": self.quality,
            "gap_count": self.gap_count,
            "duplicate_count": self.duplicate_count,
            "invalid_rows": self.invalid_rows,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> SeriesCheckpoint:
        return cls(
            symbol=str(row["symbol"]),
            timeframe=str(row["timeframe"]),
            requested_start=str(row["requested_start"]),
            requested_end=str(row["requested_end"]),
            actual_first=row.get("actual_first"),
            actual_last=row.get("actual_last"),
            candles_downloaded=int(row.get("candles_downloaded") or 0),
            chunks_completed=int(row.get("chunks_completed") or 0),
            last_successful_chunk=row.get("last_successful_chunk"),
            last_successful_chunk_end_ms=row.get("last_successful_chunk_end_ms"),
            status=str(row.get("status") or STATUS_PENDING),
            last_error=row.get("last_error"),
            quality=row.get("quality"),
            gap_count=int(row.get("gap_count") or 0),
            duplicate_count=int(row.get("duplicate_count") or 0),
            invalid_rows=int(row.get("invalid_rows") or 0),
            updated_at=str(row.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )
