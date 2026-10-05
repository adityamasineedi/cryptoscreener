"""Attach market-structure analytics to a completed strategy_matrix row.

Isolation contract: never mutates trades, balances, or configuration fingerprints.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from app.research.market_structure.artifacts import write_market_structure_artifacts
from app.research.market_structure.config import (
    ANALYTICS_VERSION,
    assert_regime_filtering_safe,
    default_feature_config,
    resolve_analytics_flags,
)
from app.research.market_structure.engine import compute_market_structure_analytics


def attach_market_structure_to_row(
    row: dict[str, Any],
    *,
    setup_candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None,
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_15m: Sequence[Mapping[str, Any]] | None,
    index_start: int | None = None,
    strategy_runtime_seconds: float | None = None,
    combination_id: str | None = None,
    strategy_id: str | None = None,
    run_id: str | None = None,
    write_artifacts: bool = True,
    analytics_enabled: bool | None = None,
    enable_regime_filtering: bool | None = None,
    research_only: bool = True,
    closed_htf_policy: bool = True,
) -> dict[str, Any]:
    """Return a shallow-copied row with ``market_structure`` payload attached."""
    out = dict(row)
    try:
        from app.config import get_settings

        settings = get_settings()
    except Exception:  # noqa: BLE001
        settings = None

    default_on, default_filter = resolve_analytics_flags(settings)
    enabled = default_on if analytics_enabled is None else bool(analytics_enabled)
    filtering = (
        default_filter if enable_regime_filtering is None else bool(enable_regime_filtering)
    )

    assert_regime_filtering_safe(
        enable_regime_filtering=filtering, research_only=research_only
    )

    if not enabled:
        out["market_structure"] = {
            "analytics_enabled": False,
            "analytics_version": ANALYTICS_VERSION,
            "analytics_runtime_seconds": 0.0,
            "strategy_runtime_seconds": strategy_runtime_seconds,
            "feature_config_fingerprint": default_feature_config().fingerprint(),
            "status": "DISABLED",
            "disclaimer": "Analytics only — does not affect strategy decisions",
        }
        return out

    t0 = time.perf_counter()
    analytics = compute_market_structure_analytics(
        symbol=str(row.get("symbol") or ""),
        setup_timeframe=str(row.get("timeframe") or "1h"),
        setup_candles=setup_candles,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        trades=list(row.get("trades") or []),
        index_start=index_start,
        combination_id=combination_id or str(row.get("combination_id") or ""),
        strategy_id=strategy_id or str(row.get("strategy_id") or ""),
        closed_htf_policy=closed_htf_policy,
        enable_regime_filtering=filtering,
        research_only=research_only,
        run_id=run_id,
        dataset_fingerprint=row.get("dataset_fingerprint"),
        configuration_fingerprint=row.get("configuration_fingerprint"),
        requested_range=row.get("requested_range"),
        actual_range={
            "period_start": row.get("period_start"),
            "period_end": row.get("period_end"),
            "bars_loaded": row.get("bars_loaded"),
        },
    )
    analytics["strategy_runtime_seconds"] = strategy_runtime_seconds
    analytics["attach_runtime_seconds"] = round(time.perf_counter() - t0, 6)

    if write_artifacts and run_id and analytics.get("status") in {"OK", "PIT_VIOLATION"}:
        try:
            paths = write_market_structure_artifacts(analytics, run_id=run_id)
            analytics["artifact_paths"] = paths
        except Exception as exc:  # noqa: BLE001
            analytics["artifact_error"] = str(exc)

    out["market_structure"] = analytics
    return out
