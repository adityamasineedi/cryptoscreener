"""Research OHLCV cache manager: PostgreSQL bulk load → Parquet → memory."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.research.data_cache.cache_key import make_ohlcv_cache_key
from app.research.data_cache.config import ResearchCacheConfig, load_research_cache_config
from app.research.data_cache.dataset import ResearchDataset
from app.research.data_cache.manifest import (
    OhlcvCacheManifest,
    read_manifest,
    write_manifest,
)
from app.research.data_cache.metrics import ResearchCacheMetrics, sample_rss_mb
from app.research.data_cache.parquet_store import (
    file_sha256,
    frame_to_candles,
    read_ohlcv_parquet,
    write_ohlcv_parquet,
)
from app.research.data_cache.postgres_loader import bulk_load_ohlcv_from_postgres
from app.research.data_cache.validation import (
    CACHE_INVALID,
    CACHE_PASS,
    manifests_compatible,
    validate_ohlcv_candles,
)
from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe

_mgr: ResearchCacheManager | None = None


def _parse_day(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    return datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc)


class ResearchCacheManager:
    def __init__(self, config: ResearchCacheConfig | None = None) -> None:
        self.config = config or load_research_cache_config()
        self.metrics = ResearchCacheMetrics()

    def reset_metrics(self) -> None:
        self.metrics = ResearchCacheMetrics()

    def _paths(self, cache_key: str) -> tuple[Path, Path]:
        root = self.config.ohlcv_dir()
        return root / f"{cache_key}.parquet", root / f"{cache_key}.manifest.json"

    async def load_ohlcv_to_cache(
        self,
        symbol: str,
        timeframe: str,
        start_time: str | datetime | None,
        end_time: str | datetime | None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Ensure exact-range Parquet cache exists; return metadata + location."""
        sym = normalize_research_symbol(symbol)
        tf = normalize_research_timeframe(timeframe)
        start_s = None if start_time is None else str(start_time)[:10]
        end_s = None if end_time is None else str(end_time)[:10]
        key = make_ohlcv_cache_key(
            symbol=sym,
            timeframe=tf,
            start_time=start_s,
            end_time=end_s,
            dataset_version=self.config.dataset_version,
        )
        parquet_path, manifest_path = self._paths(key)

        if not force_refresh and parquet_path.is_file() and manifest_path.is_file():
            man = read_manifest(manifest_path)
            if man is not None:
                ok, reason = manifests_compatible(
                    manifest=man.to_dict(),
                    symbol=sym,
                    timeframe=tf,
                    start_time=start_s,
                    end_time=end_s,
                    dataset_version=self.config.dataset_version,
                )
                if ok:
                    try:
                        digest = file_sha256(parquet_path)
                        if man.sha256 and digest != man.sha256:
                            ok = False
                            reason = "sha256_mismatch"
                        else:
                            # Light schema check
                            df = read_ohlcv_parquet(parquet_path)
                            if df.height != int(man.row_count):
                                ok = False
                                reason = "row_count_mismatch"
                    except Exception as exc:  # noqa: BLE001
                        ok = False
                        reason = f"cache_read_failed:{exc}"
                if ok:
                    self.metrics.cache_hits += 1
                    return {
                        "status": "CACHE_HIT",
                        "cache_key": key,
                        "parquet_path": str(parquet_path),
                        "manifest_path": str(manifest_path),
                        "manifest": man.to_dict(),
                        "reason": reason,
                    }

        self.metrics.cache_misses += 1
        start_dt = _parse_day(start_time)
        end_dt = _parse_day(end_time)
        # end is exclusive calendar day → next day 00:00 if date-only string
        end_exclusive = None
        if end_dt is not None:
            end_exclusive = end_dt
            # If caller passed a date string, treat as exclusive end-of-day boundary
            # via end_exclusive = that UTC midnight (same as research bounds).
            if isinstance(end_time, str) and len(str(end_time).strip()) <= 10:
                from datetime import timedelta

                end_exclusive = end_dt + timedelta(days=1)

        candles = await bulk_load_ohlcv_from_postgres(
            symbol=sym,
            timeframe=tf,
            start_time=start_dt,
            end_time=end_exclusive,
            metrics=self.metrics,
        )
        report = validate_ohlcv_candles(
            candles,
            symbol=sym,
            timeframe=tf,
            start_time=start_dt,
            end_time=end_exclusive,
        )
        if report["validation_status"] != CACHE_PASS and not candles:
            return {
                "status": CACHE_INVALID,
                "cache_key": key,
                "reason": report.get("reason") or "validation_failed",
                "validation": report,
            }

        # Allow PASS with gap notes from verify_ohlcv; hard-fail only on structural issues
        hard = {
            "unordered_timestamps",
            "duplicate_timestamps",
            "empty_series",
        }
        if any(p in hard or p.startswith("invalid_ohlc") for p in report.get("problems") or []):
            if report["validation_status"] == CACHE_INVALID and not candles:
                return {
                    "status": CACHE_INVALID,
                    "cache_key": key,
                    "validation": report,
                }

        t_write = time.monotonic()
        digest = write_ohlcv_parquet(parquet_path, candles)
        self.metrics.write_seconds += time.monotonic() - t_write

        # Re-validate persisted frame
        df = read_ohlcv_parquet(parquet_path)
        persisted = frame_to_candles(df)
        report2 = validate_ohlcv_candles(
            persisted, symbol=sym, timeframe=tf, start_time=start_dt, end_time=end_exclusive
        )
        status = (
            CACHE_PASS
            if report2["validation_status"] == CACHE_PASS
            or (
                persisted
                and not any(
                    p in {"unordered_timestamps", "duplicate_timestamps"}
                    for p in (report2.get("problems") or [])
                )
            )
            else CACHE_INVALID
        )
        if status == CACHE_INVALID:
            return {
                "status": CACHE_INVALID,
                "cache_key": key,
                "validation": report2,
                "parquet_path": str(parquet_path),
            }

        first = report2.get("first_timestamp")
        last = report2.get("last_timestamp")
        man = OhlcvCacheManifest(
            symbol=sym,
            timeframe=tf,
            start_time=start_s,
            end_time=end_s,
            dataset_version=self.config.dataset_version,
            row_count=len(persisted),
            first_timestamp=first,
            last_timestamp=last,
            source="postgresql",
            sha256=digest,
            parquet_relpath=str(parquet_path.relative_to(self.config.cache_root)),
            validation_status=CACHE_PASS,
            validation=report2,
        )
        write_manifest(manifest_path, man)
        peak = sample_rss_mb()
        if peak is not None:
            self.metrics.peak_memory_mb = max(self.metrics.peak_memory_mb or 0.0, peak)
        return {
            "status": "CACHE_WRITTEN",
            "cache_key": key,
            "parquet_path": str(parquet_path),
            "manifest_path": str(manifest_path),
            "manifest": man.to_dict(),
            "validation": report2,
        }

    async def load(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
        force_refresh: bool = False,
    ) -> ResearchDataset:
        """Load (or build) cache and return an in-memory ResearchDataset."""
        t0 = time.monotonic()
        meta = await self.load_ohlcv_to_cache(
            symbol, timeframe, start, end, force_refresh=force_refresh
        )
        if meta.get("status") == CACHE_INVALID:
            raise RuntimeError(
                f"research_cache_invalid:{meta.get('reason') or meta.get('validation')}"
            )
        parquet_path = Path(meta["parquet_path"])
        t_read = time.monotonic()
        df = read_ohlcv_parquet(parquet_path)
        self.metrics.cache_load_seconds += time.monotonic() - t_read
        self.metrics.parquet_rows_loaded += int(df.height)
        est = round(df.height * 56 / (1024 * 1024), 3)
        if est > float(self.config.memory_limit_mb):
            raise MemoryError(
                f"dataset_exceeds_RESEARCH_MEMORY_LIMIT_MB "
                f"est={est}mb limit={self.config.memory_limit_mb}"
            )
        start_s = None if start is None else str(start)[:10]
        end_s = None if end is None else str(end)[:10]
        man = meta.get("manifest") or {}
        ds = ResearchDataset(
            symbol=normalize_research_symbol(symbol),
            timeframe=normalize_research_timeframe(timeframe),
            start_time=start_s,
            end_time=end_s,
            dataset_version=self.config.dataset_version,
            frame=df,
            source="parquet_cache" if meta.get("status") == "CACHE_HIT" else "postgresql+parquet",
            cache_key=meta.get("cache_key"),
            manifest=dict(man),
            metrics_snapshot=self.metrics.to_dict(),
        )
        self.metrics.cache_load_seconds += time.monotonic() - t0
        return ds


def get_research_cache(config: ResearchCacheConfig | None = None) -> ResearchCacheManager:
    global _mgr
    if config is not None:
        return ResearchCacheManager(config)
    if _mgr is None:
        _mgr = ResearchCacheManager()
    return _mgr
