"""HTTP surface for the intraday BOS / supply-demand strategy.

No order placement and no external messaging.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.research.intraday_bos_sd.lifecycle import paper_policy_document
from app.research.intraday_bos_sd.runtime import load_state
from app.research.intraday_bos_sd.spec import spec_document, spec_hash

router = APIRouter(prefix="/research/intraday-bos-sd", tags=["intraday-bos-sd"])


def _public_state() -> dict:
    state = load_state()
    validation = state.get("validation") or {}
    checks = validation.get("checks") or []
    passed_display = bool(validation.get("passed")) and state.get("status") == "VALIDATED"
    if any(not c.get("passed") for c in checks):
        passed_display = False
    return {
        "strategy_id": state["strategy_id"],
        "strategy_version": state["strategy_version"],
        "spec_hash": spec_hash(),
        "status": state["status"],
        "backtest_passed": passed_display,
        "passed_means": "acceptance_policy_met_not_guaranteed_profit",
        "validation": validation,
        "paper": state.get("paper"),
        "paper_policy": paper_policy_document(),
        "approved_scope": state.get("approved_scope"),
        "signals": state.get("signals") or [],
        "suspension_reason": state.get("suspension_reason"),
    }


@router.get("/spec")
async def get_spec() -> dict:
    return {"spec_hash": spec_hash(), "spec": spec_document()}


@router.get("/status")
async def get_status() -> dict:
    return _public_state()


@router.get("/signals")
async def get_signals() -> dict:
    state = _public_state()
    return {
        "status": state["status"],
        "backtest_passed": state["backtest_passed"],
        "signals": state["signals"],
    }
