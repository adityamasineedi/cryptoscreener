"""Advance dynamic registry candidates: data-health → COMBO_02 backtest → OOS.

Never promotes to PAPER_VALIDATING / APPROVED. Never enables Telegram.

Advance responses split:
- run_summary — what this invocation did
- registry_summary — durable totals across the registry
"""

from __future__ import annotations

from typing import Any

import structlog

from app.research.candidate_data_health import check_candidate_data_health
from app.research.dynamic_candidate_constants import DISCLAIMER, STRATEGY_ID
from app.research.dynamic_candidate_pipeline import (
    run_candidate_backtest_gate,
    run_candidate_oos_portfolio_gate,
)
from app.research.strategy_candidate_registry import (
    serialize_candidate,
    strategy_candidate_registry,
)

logger = structlog.get_logger(__name__)


def _empty_run_summary() -> dict[str, int]:
    return {
        "health_ready": 0,
        "backtests_started": 0,
        "oos_started": 0,
        "rejected": 0,
        "advanced": 0,
        "errors": 0,
    }


def _count_run_event(
    run_summary: dict[str, int],
    *,
    result: dict[str, Any],
    kind: str,
) -> None:
    """Update this-run counters from a single gate result."""
    status = str(result.get("status") or "").upper()
    state = str(result.get("state") or "").upper()

    if kind == "health":
        if result.get("ready"):
            run_summary["health_ready"] += 1
            if state == "DATA_READY":
                run_summary["advanced"] += 1
        else:
            # Not ready after a health check counts as a this-run reject/block.
            if status not in ("NOT_FOUND",):
                run_summary["rejected"] += 1
        if status in ("ERROR", "ENGINE_ERROR"):
            run_summary["errors"] += 1
        return

    if kind == "backtest":
        if status in ("SKIP", "NOT_FOUND"):
            return
        run_summary["backtests_started"] += 1
        if status in ("ERROR", "ENGINE_ERROR"):
            run_summary["errors"] += 1
            run_summary["rejected"] += 1
            return
        if state == "PROMISING":
            run_summary["advanced"] += 1
        elif state == "RESEARCH_REJECTED":
            run_summary["rejected"] += 1
        return

    if kind == "oos":
        if status in ("SKIP", "NOT_FOUND"):
            return
        run_summary["oos_started"] += 1
        if status in ("ERROR", "ENGINE_ERROR"):
            run_summary["errors"] += 1
            run_summary["rejected"] += 1
            return
        if state == "V2_PAPER_CANDIDATE":
            run_summary["advanced"] += 1
        elif state in ("OOS_FAILED", "RESEARCH_REJECTED"):
            run_summary["rejected"] += 1


async def advance_dynamic_candidates(
    *,
    symbols: list[str] | None = None,
    run_data_health: bool = True,
    run_backtest: bool = True,
    run_oos: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """One-shot advance for registry rows (research only)."""
    await strategy_candidate_registry.ensure_schema()

    if symbols:
        want = [s.upper().strip() for s in symbols if s]
        rows = []
        for sym in want:
            row = await strategy_candidate_registry.get_by_symbol(sym)
            if row:
                rows.append(row)
    else:
        rows = await strategy_candidate_registry.list_candidates(limit=limit)

    health_results: list[dict[str, Any]] = []
    backtest_results: list[dict[str, Any]] = []
    oos_results: list[dict[str, Any]] = []
    run_summary = _empty_run_summary()

    for row in rows:
        sym = str(row["symbol"])
        state = str(row.get("state") or "")

        if run_data_health and state in (
            "DISCOVERED",
            "DATA_PENDING",
            "DATA_READY",  # re-validate
        ):
            try:
                hr = await check_candidate_data_health(sym, queue_backfill=True)
            except Exception as exc:  # noqa: BLE001
                hr = {
                    "status": "ERROR",
                    "symbol": sym,
                    "ready": False,
                    "error": str(exc),
                }
            health_results.append(hr)
            _count_run_event(run_summary, result=hr, kind="health")
            # refresh
            row = await strategy_candidate_registry.get_by_symbol(sym) or row
            state = str(row.get("state") or "")

        if run_backtest and state in ("DATA_READY", "BACKTEST_QUEUED"):
            try:
                br = await run_candidate_backtest_gate(sym)
            except Exception as exc:  # noqa: BLE001
                br = {
                    "status": "ERROR",
                    "symbol": sym,
                    "error": str(exc),
                }
            backtest_results.append(br)
            _count_run_event(run_summary, result=br, kind="backtest")
            row = await strategy_candidate_registry.get_by_symbol(sym) or row
            state = str(row.get("state") or "")

        if run_oos and state == "PROMISING":
            try:
                oos = await run_candidate_oos_portfolio_gate(sym)
            except Exception as exc:  # noqa: BLE001
                oos = {
                    "status": "ERROR",
                    "symbol": sym,
                    "error": str(exc),
                }
            oos_results.append(oos)
            _count_run_event(run_summary, result=oos, kind="oos")

    final_rows = []
    for row in (
        await strategy_candidate_registry.list_candidates(limit=limit)
        if not symbols
        else [
            r
            for r in [
                await strategy_candidate_registry.get_by_symbol(s.upper())
                for s in (symbols or [])
            ]
            if r
        ]
    ):
        final_rows.append(serialize_candidate(row))

    registry_summary = await strategy_candidate_registry.registry_summary()

    summary = {
        "status": "OK",
        "strategy_id": STRATEGY_ID,
        "run_summary": run_summary,
        "registry_summary": registry_summary,
        "health": health_results,
        "backtests": backtest_results,
        "oos": oos_results,
        "candidates": final_rows,
        "telegram_eligible": False,
        "paper_trades_created": 0,
        "disclaimer": DISCLAIMER,
    }
    logger.info(
        "dynamic_candidates_advanced",
        run_summary=run_summary,
        registry_summary=registry_summary,
    )
    return summary
