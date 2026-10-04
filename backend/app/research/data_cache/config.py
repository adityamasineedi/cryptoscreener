"""Research Parquet cache configuration (env-driven, isolated from live Settings)."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Bump when on-disk schema / validation rules change.
DATASET_VERSION = "ohlcv_v1"
FEATURE_CACHE_VERSION = "research_features_cache_v1"
EVENT_CACHE_VERSION = "research_events_bos_v1"


def _default_cache_root() -> Path:
    env = os.getenv("RESEARCH_CACHE_PATH")
    if env:
        return Path(env).expanduser().resolve()
    # backend/app/research/data_cache → parents[3] = repo root
    root = Path(__file__).resolve().parents[4]
    return (root / "backend" / "data" / "research_cache").resolve()


@dataclass
class ResearchCacheConfig:
    enabled: bool = False
    cache_root: Path = None  # type: ignore[assignment]
    dataset_version: str = DATASET_VERSION
    feature_version: str = FEATURE_CACHE_VERSION
    event_version: str = EVENT_CACHE_VERSION
    db_max_concurrency: int = 2
    cpu_workers: int = 4
    memory_limit_mb: int = 2048
    batch_size: int = 1000
    allow_partial_extend: bool = True

    def __post_init__(self) -> None:
        if self.cache_root is None:
            self.cache_root = _default_cache_root()
        elif not isinstance(self.cache_root, Path):
            self.cache_root = Path(self.cache_root).expanduser().resolve()
        self.enabled = _env_bool("RESEARCH_CACHE_ENABLED", self.enabled)
        if os.getenv("RESEARCH_CACHE_PATH"):
            self.cache_root = Path(os.environ["RESEARCH_CACHE_PATH"]).expanduser().resolve()
        if os.getenv("RESEARCH_DB_MAX_CONCURRENCY"):
            self.db_max_concurrency = max(
                1, min(8, int(os.environ["RESEARCH_DB_MAX_CONCURRENCY"]))
            )
        if os.getenv("RESEARCH_CPU_WORKERS"):
            self.cpu_workers = max(1, min(8, int(os.environ["RESEARCH_CPU_WORKERS"])))
        if os.getenv("RESEARCH_MEMORY_LIMIT_MB"):
            self.memory_limit_mb = max(
                256, int(os.environ["RESEARCH_MEMORY_LIMIT_MB"])
            )
        if os.getenv("RESEARCH_BATCH_SIZE"):
            self.batch_size = max(100, int(os.environ["RESEARCH_BATCH_SIZE"]))

    def ohlcv_dir(self) -> Path:
        p = self.cache_root / "ohlcv" / self.dataset_version
        p.mkdir(parents=True, exist_ok=True)
        return p

    def feature_dir(self) -> Path:
        p = self.cache_root / "features" / self.feature_version
        p.mkdir(parents=True, exist_ok=True)
        return p

    def event_dir(self) -> Path:
        p = self.cache_root / "events" / self.event_version
        p.mkdir(parents=True, exist_ok=True)
        return p

    def runs_dir(self) -> Path:
        p = self.cache_root / "runs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cache_root"] = str(self.cache_root)
        return d


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_research_cache_config() -> ResearchCacheConfig:
    return ResearchCacheConfig()
