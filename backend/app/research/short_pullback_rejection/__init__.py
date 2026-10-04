"""SHORT pullback-rejection research package (research-only)."""

from app.research.short_pullback_rejection.constants import SAFETY_STAMPS, STRATEGY_ID

__all__ = [
    "STRATEGY_ID",
    "SAFETY_STAMPS",
    "run_short_pullback_rejection_research",
    "run_short_pullback_rejection_research_async",
]


def __getattr__(name: str):
    if name in (
        "run_short_pullback_rejection_research",
        "run_short_pullback_rejection_research_async",
    ):
        from app.research.short_pullback_rejection.runner import (
            run_short_pullback_rejection_research,
            run_short_pullback_rejection_research_async,
        )

        return {
            "run_short_pullback_rejection_research": run_short_pullback_rejection_research,
            "run_short_pullback_rejection_research_async": run_short_pullback_rejection_research_async,
        }[name]
    raise AttributeError(name)
