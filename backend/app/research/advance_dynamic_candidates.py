"""Advance dynamic registry candidates: data-health → COMBO_02 backtest → OOS.

Never promotes to PAPER_VALIDATING / APPROVED. Never enables Telegram.
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

    for row in rows:
        sym = str(row["symbol"])
        state = str(row.get("state") or "")

        if run_data_health and state in (
            "DISCOVERED",
            "DATA_PENDING",
            "DATA_READY",  # re-validate
        ):
            hr = await check_candidate_data_health(sym, queue_backfill=True)
            health_results.append(hr)
            # refresh
            row = await strategy_candidate_registry.get_by_symbol(sym) or row
            state = str(row.get("state") or "")

        if run_backtest and state in ("DATA_READY", "BACKTEST_QUEUED"):
            br = await run_candidate_backtest_gate(sym)
            backtest_results.append(br)
            row = await strategy_candidate_registry.get_by_symbol(sym) or row
            state = str(row.get("state") or "")

        if run_oos and state == "PROMISING":
            oos = await run_candidate_oos_portfolio_gate(sym)
            oos_results.append(oos)

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

    summary = {
        "status": "OK",
        "strategy_id": STRATEGY_ID,
        "health_checked": len(health_results),
        "health_ready": sum(1 for h in health_results if h.get("ready")),
        "health_blocked": sum(1 for h in health_results if not h.get("ready")),
        "backtests_run": len(backtest_results),
        "oos_run": len(oos_results),
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
        health_checked=summary["health_checked"],
        health_ready=summary["health_ready"],
        backtests_run=summary["backtests_run"],
        oos_run=summary["oos_run"],
    )
    return summary
