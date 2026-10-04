"""Research-only OHLCV Parquet cache (PostgreSQL → Parquet → Polars/memory).

Isolated from live signals, WebSocket/REST ingestion, Trade Plan, and
production strategy thresholds. Additive layer above ``postgres_ohlcv``.
"""

from __future__ import annotations

from app.research.data_cache.cache_manager import ResearchCacheManager, get_research_cache
from app.research.data_cache.config import DATASET_VERSION, ResearchCacheConfig
from app.research.data_cache.dataset import PreparedResearchBundle, ResearchDataset
from app.research.data_cache.prepare import prepare_research_dataset

__all__ = [
    "DATASET_VERSION",
    "ResearchCacheConfig",
    "ResearchCacheManager",
    "ResearchDataset",
    "PreparedResearchBundle",
    "get_research_cache",
    "prepare_research_dataset",
]
