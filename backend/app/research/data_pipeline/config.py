"""Research data-pipeline configuration (isolated from live SignalConfig)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# Bump when feature extraction wrapping production engines changes.
FEATURE_VERSION = "research_features_v1"
PIPELINE_VERSION = "research_data_pipeline_v1"
SIGNAL_ENGINE_VERSION = "setup_signal_v1"

DEFAULT_PERIOD_START = "2020-10-01"
DEFAULT_TIMEFRAMES = ("5m", "15m", "1h", "4h")
BOS_MIN_TIMEFRAMES = ("5m", "15m", "1h", "4h")
ALL_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")

STATUS_PENDING = "PENDING"
STATUS_DOWNLOADING = "DOWNLOADING"
STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_NO_HISTORY = "NO_HISTORY"

GAP_DETECTED = "GAP_DETECTED"
QUALITY_PASS = "PASS"
QUALITY_FAIL = "FAIL"
QUALITY_GAPS = "GAPS"

ALIGNED_BULLISH = "ALIGNED_BULLISH"
ALIGNED_BEARISH = "ALIGNED_BEARISH"
MIXED = "MIXED"
CONFLICT = "CONFLICT"
NEUTRAL = "NEUTRAL"
UNAVAILABLE = "UNAVAILABLE"


@dataclass
class PipelineConfig:
    """Resource-bounded research pipeline settings.

    Changing these does NOT alter live trading thresholds or SignalConfig.
    """

    period_start: str = DEFAULT_PERIOD_START
    period_end: str | None = None  # None = now (UTC)
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
    max_workers: int = 4
    db_pool_size: int = 3
    chunk_days: int = 90
    max_memory_gb: float = 4.0
    batch_insert_size: int = 1000
    kline_limit: int = 1500
    page_pause_seconds: float = 0.15
    request_timeout_seconds: float = 30.0
    max_retries: int = 5
    # Soft budget — actual REST weight uses shared rate_limiters["binance_rest"]
    max_pages_per_series: int = 500
    min_sample_size: int = 30
    prune_sample_size: int = 100
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    oos_fraction: float = 0.20
    setup_timeframe: str = "15m"
    htf_timeframes: tuple[str, ...] = ("4h", "1h")
    entry_timeframe: str = "5m"
    quote_asset: str = "USDT"
    write_source_tag: str = "binance_research_pipeline"
    # Safety: never touch live memory OHLCV store
    mutate_live_ohlcv_store: bool = False
    stop_after_dataset: bool = True  # section 41 — do not auto-run optimization
    cost_multipliers: tuple[float, ...] = (1.0, 1.5, 2.0)

    def resolved_period_end(self) -> str:
        if self.period_end:
            return self.period_end[:10]
        return datetime.now(timezone.utc).date().isoformat()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timeframes"] = list(self.timeframes)
        d["htf_timeframes"] = list(self.htf_timeframes)
        d["cost_multipliers"] = list(self.cost_multipliers)
        d["feature_version"] = FEATURE_VERSION
        d["pipeline_version"] = PIPELINE_VERSION
        return d

    def configuration_hash(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> PipelineConfig:
        cfg = cls()
        if not raw:
            return cfg
        for key, value in raw.items():
            if not hasattr(cfg, key):
                continue
            if key in ("timeframes", "htf_timeframes", "cost_multipliers") and isinstance(
                value, (list, tuple)
            ):
                setattr(cfg, key, tuple(value))
            else:
                setattr(cfg, key, value)
        # Env overrides for resource limits
        if os.getenv("RESEARCH_MAX_WORKERS"):
            cfg.max_workers = max(1, min(16, int(os.environ["RESEARCH_MAX_WORKERS"])))
        if os.getenv("RESEARCH_DOWNLOAD_WORKERS"):
            cfg.max_workers = max(1, min(8, int(os.environ["RESEARCH_DOWNLOAD_WORKERS"])))
        if os.getenv("RESEARCH_CHUNK_DAYS"):
            cfg.chunk_days = max(7, int(os.environ["RESEARCH_CHUNK_DAYS"]))
        if os.getenv("RESEARCH_MAX_MEMORY_GB"):
            cfg.max_memory_gb = float(os.environ["RESEARCH_MAX_MEMORY_GB"])
        if os.getenv("MAX_DOWNLOAD_WORKERS"):
            cfg.max_workers = max(1, min(8, int(os.environ["MAX_DOWNLOAD_WORKERS"])))
        if os.getenv("MAX_BATCH_INSERT"):
            cfg.batch_insert_size = max(100, int(os.environ["MAX_BATCH_INSERT"]))
        return cfg

    def with_symbols_timeframes(
        self,
        *,
        symbols: Sequence[str] | None = None,
        timeframes: Sequence[str] | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
    ) -> PipelineConfig:
        cfg = PipelineConfig(**asdict(self))
        if timeframes is not None:
            cfg.timeframes = tuple(timeframes)
        if period_start is not None:
            cfg.period_start = period_start
        if period_end is not None:
            cfg.period_end = period_end
        return cfg
