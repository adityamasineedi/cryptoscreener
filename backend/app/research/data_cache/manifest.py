"""Cache manifests for reproducible research datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class OhlcvCacheManifest:
    symbol: str
    timeframe: str
    start_time: str | None
    end_time: str | None
    dataset_version: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    source: str = "postgresql"
    sha256: str = ""
    parquet_relpath: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    validation_status: str = "PASS"
    validation: dict[str, Any] = field(default_factory=dict)
    fabricated: bool = False
    interpolated: bool = False
    note: str = (
        "Research-only Parquet cache of real OHLCV. "
        "Gaps recorded, never filled. Not used by live trading."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OhlcvCacheManifest:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        payload = {k: v for k, v in raw.items() if k in known}
        return cls(**payload)  # type: ignore[arg-type]


def write_manifest(path: Path, manifest: OhlcvCacheManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def read_manifest(path: Path) -> OhlcvCacheManifest | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return OhlcvCacheManifest.from_dict(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
