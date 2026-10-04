"""Research event cache — wraps existing BOS event builders (no new logic)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.data_cache.cache_key import make_feature_cache_key
from app.research.data_cache.config import ResearchCacheConfig, load_research_cache_config
from app.research.data_cache.dataset import ResearchDataset
from app.research.data_cache.metrics import ResearchCacheMetrics


class EventCache:
    """Persist sparse BOS events produced by ``data_pipeline.event_store``."""

    def __init__(
        self,
        config: ResearchCacheConfig | None = None,
        metrics: ResearchCacheMetrics | None = None,
    ) -> None:
        self.config = config or load_research_cache_config()
        self.metrics = metrics or ResearchCacheMetrics()

    def _paths(self, key: str) -> tuple[Path, Path]:
        root = self.config.event_dir()
        return root / f"{key}.json", root / f"{key}.manifest.json"

    def ensure_bos_events(
        self,
        *,
        dataset: ResearchDataset,
        candles_by_tf: Mapping[str, Sequence[Mapping[str, Any]]],
        force: bool = False,
    ) -> dict[str, Any]:
        """Build or load BOS events using production wrappers (event_store)."""
        key = make_feature_cache_key(
            symbol=dataset.symbol,
            timeframe=dataset.timeframe,
            start_time=dataset.start_time,
            end_time=dataset.end_time,
            dataset_version=dataset.dataset_version,
            feature_version=self.config.event_version,
        )
        events_path, manifest_path = self._paths(key)
        if not force and events_path.is_file() and manifest_path.is_file():
            self.metrics.event_cache_hits += 1
            events = json.loads(events_path.read_text(encoding="utf-8"))
            return {
                "status": "CACHE_HIT",
                "cache_key": key,
                "events_path": str(events_path),
                "event_count": len(events) if isinstance(events, list) else 0,
                "events": events,
            }

        self.metrics.event_cache_misses += 1
        from app.research.bos_strategy_comparison.config import StrategyResearchConfig
        from app.research.data_pipeline.config import PipelineConfig
        from app.research.data_pipeline.event_store import build_bos_events_for_symbol

        t0 = time.monotonic()
        pipe_cfg = PipelineConfig(
            setup_timeframe=dataset.timeframe,
            period_start=dataset.start_time or "2020-10-01",
            period_end=dataset.end_time,
        )
        events = build_bos_events_for_symbol(
            symbol=dataset.symbol,
            candles_by_tf=candles_by_tf,
            config=pipe_cfg,
            dataset_version=dataset.dataset_version,
            research_cfg=StrategyResearchConfig(),
        )
        self.metrics.event_seconds += time.monotonic() - t0

        # JSON-safe (datetime → iso)
        serializable: list[dict[str, Any]] = []
        for ev in events:
            row = dict(ev)
            for k, v in list(row.items()):
                if isinstance(v, datetime):
                    row[k] = v.isoformat()
            serializable.append(row)

        events_path.parent.mkdir(parents=True, exist_ok=True)
        tw = time.monotonic()
        events_path.write_text(
            json.dumps(serializable, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        man = {
            "event_version": self.config.event_version,
            "dataset_version": dataset.dataset_version,
            "symbol": dataset.symbol,
            "timeframe": dataset.timeframe,
            "start_time": dataset.start_time,
            "end_time": dataset.end_time,
            "event_count": len(serializable),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dependencies": {
                "ohlcv_dataset_version": dataset.dataset_version,
                "builder": "data_pipeline.event_store.build_bos_events_for_symbol",
                "feature_version_note": "wraps production SignalEngine — no fork",
            },
        }
        manifest_path.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
        self.metrics.write_seconds += time.monotonic() - tw
        return {
            "status": "CACHE_WRITTEN",
            "cache_key": key,
            "events_path": str(events_path),
            "event_count": len(serializable),
            "events": serializable,
            "manifest": man,
        }
