"""Isolated historical research data pipeline.

READ-ONLY against production trading logic. Does not modify live signal engines,
WebSocket ingestion, screener behavior, Trade Plan, or production thresholds.

Candle writes use conflict-safe INSERT ... ON CONFLICT DO NOTHING into the shared
``ohlcv`` table (additive real Binance history only — never overwrites valid rows).
Manifest, feature cache, BOS events, and dataset versions live in research-only tables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.research.data_pipeline.config import FEATURE_VERSION, PipelineConfig

if TYPE_CHECKING:
    from app.research.data_pipeline.runner import ResearchDataPipeline as ResearchDataPipeline

__all__ = [
    "PipelineConfig",
    "FEATURE_VERSION",
    "ResearchDataPipeline",
    "audit_symbol_mtf_coverage",
    "research_data_readiness_gate",
    "plan_from_coverage",
    "build_coverage_from_stats",
]


def __getattr__(name: str) -> Any:
    if name == "ResearchDataPipeline":
        from app.research.data_pipeline.runner import ResearchDataPipeline

        return ResearchDataPipeline
    if name == "audit_symbol_mtf_coverage":
        from app.research.data_pipeline.history_coverage import audit_symbol_mtf_coverage

        return audit_symbol_mtf_coverage
    if name == "research_data_readiness_gate":
        from app.research.data_pipeline.history_coverage import (
            research_data_readiness_gate,
        )

        return research_data_readiness_gate
    if name == "plan_from_coverage":
        from app.research.data_pipeline.planner import plan_from_coverage

        return plan_from_coverage
    if name == "build_coverage_from_stats":
        from app.research.data_pipeline.coverage import build_coverage_from_stats

        return build_coverage_from_stats
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
