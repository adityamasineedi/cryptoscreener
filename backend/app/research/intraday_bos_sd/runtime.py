"""Durable cursors and signal dedupe. Historical replays do not alert."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.research.intraday_bos_sd.lifecycle import publication_kind, transition
from app.research.intraday_bos_sd.spec import STRATEGY_ID, STRATEGY_VERSION, spec_hash

STATE_PATH = (
    Path(__file__).resolve().parents[3] / "reports" / "intraday_bos_sd" / "state.json"
)


def default_state() -> dict:
    return {
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "spec_hash": spec_hash(),
        "approved_spec_hash": None,
        "status": "DRAFT",
        "approved_scope": {
            "exchange": "binance",
            "contract_type": "usdt_perpetual",
            "timeframes": ["1h", "15m", "5m", "1m"],
            "tier_kind": None,
            "tiers": [],
        },
        "validation": None,
        "paper": {
            "started_at": None,
            "candidates": 0,
            "mismatches": 0,
            "forward_validation_passed": False,
        },
        "cursors": {},
        "seen_keys": [],
        "signals": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def load_state(path: Path | None = None) -> dict:
    target = path or STATE_PATH
    if not target.exists():
        return default_state()
    data = json.loads(target.read_text(encoding="utf-8"))
    base = default_state()
    base.update(data)
    if base.get("spec_hash") != spec_hash():
        base["status"] = "SUSPENDED"
        base["suspension_reason"] = "spec_hash_changed"
        base["approved_spec_hash"] = None
    return base


def save_state(state: dict, path: Path | None = None) -> None:
    target = path or STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["spec_hash"] = spec_hash()
    target.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def set_status(state: dict, new: str, *, operator: bool = False) -> dict:
    state["status"] = transition(state.get("status") or "DRAFT", new, operator=operator)
    return state


def eligibility(state: dict, *, tier: str, data_fresh: bool, portfolio_known: bool) -> dict:
    reasons: list[str] = []
    status = state.get("status") or "DRAFT"
    if state.get("spec_hash") != spec_hash():
        reasons.append("stale_or_changed_spec")
    approved = state.get("approved_spec_hash")
    if approved and approved != spec_hash():
        reasons.append("approved_version_mismatch")
    scope = state.get("approved_scope") or {}
    tiers = scope.get("tiers") or []
    if status == "SIGNAL_ENABLED" and tier not in tiers:
        reasons.append("tier_outside_validated_scope")
    if not data_fresh:
        reasons.append("stale_or_incomplete_market_data")
    if status == "SIGNAL_ENABLED" and not portfolio_known:
        reasons.append("portfolio_state_unavailable")
    kind = publication_kind(status)
    if kind == "RESEARCH_ONLY":
        reasons.append("strategy_not_authorized_for_alerts")
    actionable = kind == "ACTIONABLE_SIGNAL" and not reasons
    return {
        "publication": "ACTIONABLE_SIGNAL" if actionable else ("PAPER_CANDIDATE" if kind == "PAPER_CANDIDATE" and data_fresh else "BLOCKED"),
        "actionable": actionable,
        "reasons": reasons,
        "portfolio_approval": "available" if portfolio_known else "unavailable_reference_sizing_only",
    }


def accept_signal(state: dict, signal: dict, *, replay: bool, data_fresh: bool, portfolio_known: bool) -> dict:
    key = signal["idempotency_key"]
    if key in state["seen_keys"]:
        return {"stored": False, "reason": "duplicate", "signal": None}
    cursor = state["cursors"].get(signal["symbol"])
    if cursor and signal["setup_time"] <= cursor and not replay:
        return {"stored": False, "reason": "cursor_already_advanced", "signal": None}
    decision = eligibility(
        state,
        tier=signal.get("tier") or "",
        data_fresh=data_fresh,
        portfolio_known=portfolio_known,
    )
    if replay:
        decision = {
            "publication": "HISTORICAL_REPLAY",
            "actionable": False,
            "reasons": ["historical_replay_does_not_alert"],
            "portfolio_approval": decision["portfolio_approval"],
        }
    record = {**signal, **decision}
    state["seen_keys"].append(key)
    state["signals"].append(record)
    if not replay:
        state["cursors"][signal["symbol"]] = signal["setup_time"]
        if decision["publication"] == "PAPER_CANDIDATE":
            state["paper"]["candidates"] = int(state["paper"].get("candidates") or 0) + 1
    return {"stored": True, "reason": "ok", "signal": record}
