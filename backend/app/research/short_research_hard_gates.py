"""Hard acceptance gates for real-symbol SHORT research reports.

Research-only. Never enables paper/live/Telegram/production.
Operator override cannot clear these gates.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_research_constants import TERMINAL_PASS_STATE
from app.research.short_research_forensics import EVIDENCE_DIRECT
from app.research.short_research_prepaper_review import (
    REVIEW_BLOCKED,
    REVIEW_READY,
    RESEARCH_ONLY_CONTINUES,
    sample_class,
)

EVIDENCE_MODE_DIRECT = "DIRECT_CANDLE_REPLAY"

# OOS statuses that count as validation PASS for hard gate.
_OOS_PASS_STATUSES = frozenset(
    {
        TERMINAL_PASS_STATE,
        "PASS",
        "SHORT_RESEARCH_CANDIDATE",
        "V2_PAPER_CANDIDATE",  # mapped upstream to SHORT_RESEARCH_CANDIDATE; accept if leaked
    }
)


def evaluate_hard_acceptance_gates(
    candidate: Mapping[str, Any],
    *,
    evidence_mode: str = EVIDENCE_MODE_DIRECT,
) -> dict[str, Any]:
    """Evaluate non-overridable hard gates for REVIEW_READY eligibility."""
    forensic = candidate.get("forensic_lookahead") or {}
    audits = list(forensic.get("audits") or candidate.get("trade_forensic_audits") or [])
    evidence_classes = set(forensic.get("evidence_classes") or [])
    if not evidence_classes and audits:
        evidence_classes = {str(a.get("evidence_class") or "") for a in audits}

    trade_count = int(
        (candidate.get("base_research") or {}).get("trade_count")
        or (candidate.get("partition_counts") or {}).get("base_trades")
        or 0
    )
    # Every trade must have forensic evidence when trades exist
    trades_have_evidence = trade_count == 0 or (
        len(audits) >= 1
        and all(a.get("evidence_class") for a in audits)
    )

    direct_ok = bool(forensic.get("forensic_pass_for_promotion")) and (
        not audits or evidence_classes == {EVIDENCE_DIRECT}
    )
    if str(evidence_mode).upper() != EVIDENCE_MODE_DIRECT:
        # Only DIRECT mode is accepted for real-symbol hard gate readiness
        direct_ok = False

    health = candidate.get("data_health") or {}
    health_status = str(
        health.get("data_health_status") or health.get("health_status") or ""
    ).upper()
    data_healthy = health_status in ("HEALTHY", "OK") and bool(health.get("ok", True))
    range_ok = health.get("requested_range_available") is not False

    win_ok = str(candidate.get("window_status") or "OK") == "OK"
    oos = candidate.get("oos") or {}
    oos_status = str(oos.get("oos_status") or "")
    oos_from_val = str(oos.get("determines_status") or "oos_validation") == "oos_validation"
    oos_pass = oos_status in _OOS_PASS_STATUSES and oos_from_val

    oos_val_n = int(
        (candidate.get("oos_validation") or {}).get("trade_count")
        or (candidate.get("partition_counts") or {}).get("oos_val_trades")
        or 0
    )
    sample_ok = oos_val_n >= 30

    recon = candidate.get("reconciliation") or {}

    def _recon_pass(key: str, check_key: str | None = None) -> bool:
        if key in recon:
            return str(recon.get(key)).upper() == "PASS"
        if recon.get("ok") is True:
            return True
        if recon.get("ok") is False:
            if check_key and isinstance(recon.get("checks"), Mapping):
                return bool(recon["checks"].get(check_key))
            return False
        return trade_count == 0  # no trades → no blotter to fail

    equity_ok = _recon_pass("equity_reconciliation", "equity_matches")
    fee_ok = _recon_pass("fee_reconciliation", "fees_match")
    order_ok = _recon_pass("trade_order_reconciliation", "trade_order_ok")

    gates = {
        "direct_candle_replay": direct_ok,
        "windows_ok": win_ok,
        "oos_validation_pass": oos_pass,
        "oos_validation_sample_ge_30": sample_ok,
        "equity_reconciliation": equity_ok,
        "fee_reconciliation": fee_ok,
        "trade_order_reconciliation": order_ok,
        "data_health_healthy": data_healthy,
        "requested_range_available": range_ok,
        "all_trades_have_forensic_evidence": trades_have_evidence,
    }
    failed = [k for k, v in gates.items() if not v]
    return {
        "passed": not failed,
        "gates": gates,
        "failed_gates": failed,
        "oos_validation_trade_count": oos_val_n,
        "oos_validation_sample_class": sample_class(oos_val_n),
        "evidence_classes": sorted(evidence_classes),
        "evidence_mode": evidence_mode,
        "overridable": False,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }


def classify_review_from_hard_gates(
    candidate: Mapping[str, Any],
    *,
    evidence_mode: str = EVIDENCE_MODE_DIRECT,
) -> dict[str, Any]:
    """Map hard gates → review_status / research_quality (never enables paper)."""
    hard = evaluate_hard_acceptance_gates(candidate, evidence_mode=evidence_mode)
    blockers = list(hard["failed_gates"])
    followups: list[str] = []

    if not hard["gates"]["direct_candle_replay"]:
        followups.append("independent candle replay")
    if not hard["gates"]["oos_validation_sample_ge_30"]:
        followups.append("minimum OOS validation sample")
    followups.append("operator review")

    if hard["passed"]:
        return {
            "review_status": REVIEW_READY,
            "research_quality": "PASS_WITH_WARNINGS",
            "human_label": "Ready for separate human review",
            "hard_gates": hard,
            "blockers": [],
            "required_followups": followups,
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
        }

    # Any hard failure → REVIEW_BLOCKED (including sample < 30)
    quality = "REVIEW_REQUIRED"
    status = REVIEW_BLOCKED
    # Soft continuum only when everything else passes except we want research-only messaging
    # Spec: sample < 30 is hard REVIEW_BLOCKED.
    if (
        blockers == ["oos_validation_sample_ge_30"]
        and hard["gates"]["direct_candle_replay"]
        and hard["gates"]["windows_ok"]
    ):
        # Still blocked for separate approval, but label research-only continues
        status = REVIEW_BLOCKED
        quality = "REVIEW_REQUIRED"

    return {
        "review_status": status,
        "research_quality": quality,
        "human_label": "Review blocked — research only",
        "hard_gates": hard,
        "blockers": blockers,
        "required_followups": followups,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "alternate_status_note": RESEARCH_ONLY_CONTINUES
        if "oos_validation_sample_ge_30" in blockers
        else None,
    }


def build_batch_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """Batch summary that never hides blocked symbols."""
    candidates = list(report.get("candidates") or report.get("reports") or [])
    universe = list(report.get("universe") or [c.get("symbol") for c in candidates])
    review_blocked = 0
    review_ready = 0
    insufficient_oos = 0
    data_failures = 0
    forensic_failures = 0
    healthy = 0
    reports_out: list[dict[str, Any]] = []

    by_sym = {str(c.get("symbol") or "").upper(): c for c in candidates}
    for sym in universe:
        c = by_sym.get(str(sym).upper())
        if c is None:
            # Must appear even if missing from candidates
            row = {
                "symbol": str(sym).upper(),
                "review_status": REVIEW_BLOCKED,
                "research_quality": "REVIEW_REQUIRED",
                "data_health": "MISSING",
                "blockers": ["missing_report"],
                "paper_eligible": False,
                "production_approved": False,
                "telegram_eligible": False,
            }
            data_failures += 1
            review_blocked += 1
            reports_out.append(row)
            continue

        health = c.get("data_health") or {}
        hs = str(health.get("data_health_status") or health.get("health_status") or "")
        if health.get("ok") and hs.upper() in ("HEALTHY", "OK"):
            healthy += 1
        else:
            data_failures += 1

        review = c.get("prepaper_review") or c.get("hard_gate_review") or {}
        rs = str(review.get("review_status") or REVIEW_BLOCKED)
        if rs == REVIEW_READY:
            review_ready += 1
        else:
            review_blocked += 1

        oos_n = int(
            (c.get("oos_validation") or {}).get("trade_count")
            or (c.get("partition_counts") or {}).get("oos_val_trades")
            or 0
        )
        if oos_n < 30:
            insufficient_oos += 1

        forensic = c.get("forensic_lookahead") or {}
        if not forensic.get("forensic_pass_for_promotion"):
            forensic_failures += 1

        reports_out.append(
            {
                "symbol": str(c.get("symbol") or "").upper(),
                "timeframe": c.get("timeframe"),
                "data_health": hs or health.get("ok"),
                "actual_start": health.get("coverage_start") or health.get("actual_start"),
                "actual_end": health.get("coverage_end") or health.get("actual_end"),
                "requested_range_available": health.get("requested_range_available"),
                "base_trades": (c.get("partition_counts") or {}).get("base_trades"),
                "oos_dev_trades": (c.get("partition_counts") or {}).get("oos_dev_trades"),
                "oos_val_trades": oos_n,
                "oos_status": (c.get("oos") or {}).get("oos_status"),
                "research_quality": c.get("research_quality"),
                "forensic_evidence": (forensic.get("evidence_classes") or ["UNKNOWN"]),
                "review_status": rs,
                "blockers": review.get("blockers") or [],
                "paper_eligible": False,
                "production_approved": False,
                "telegram_eligible": False,
                "human_label": review.get("human_label"),
            }
        )

    return {
        "run_id": report.get("run_id"),
        "strategy_id": report.get("strategy_id") or "COMBO_02_SHORT_RESEARCH",
        "direction": "SHORT",
        "combo_version": report.get("combo_version"),
        "source": report.get("source"),
        "total_symbols": len(universe),
        "healthy_symbols": healthy,
        "review_blocked": review_blocked,
        "review_ready": review_ready,
        "insufficient_oos_sample": insufficient_oos,
        "data_failures": data_failures,
        "forensic_failures": forensic_failures,
        "paper_eligible": 0,
        "production_approved": 0,
        "telegram_eligible": 0,
        "evidence_mode": report.get("evidence_mode") or EVIDENCE_MODE_DIRECT,
        "research_windows": report.get("research_windows"),
        "reports": reports_out,
        "paper_eligible_flag": False,
        "production_approved_flag": False,
        "telegram_eligible_flag": False,
        "read_only": True,
        "execution_rights": False,
    }


def print_batch_console_summary(summary: Mapping[str, Any]) -> str:
    """Human-readable per-symbol table for CLI."""
    lines = [
        f"run_id={summary.get('run_id')} strategy={summary.get('strategy_id')} "
        f"direction=SHORT evidence_mode={summary.get('evidence_mode')}",
        f"total={summary.get('total_symbols')} ready={summary.get('review_ready')} "
        f"blocked={summary.get('review_blocked')} data_fail={summary.get('data_failures')} "
        f"forensic_fail={summary.get('forensic_failures')} "
        f"oos_sample_fail={summary.get('insufficient_oos_sample')}",
        "paper_eligible=0 production_approved=0 telegram_eligible=0",
        "-",
        f"{'symbol':12} {'tf':4} {'health':12} {'base':>5} {'dev':>5} {'val':>5} "
        f"{'oos':22} {'quality':22} {'evidence':22} {'review':32} paper",
    ]
    for r in summary.get("reports") or []:
        ev = r.get("forensic_evidence")
        if isinstance(ev, list):
            ev_s = ",".join(str(x) for x in ev)[:22]
        else:
            ev_s = str(ev or "UNKNOWN")[:22]
        lines.append(
            f"{str(r.get('symbol') or ''):12} "
            f"{str(r.get('timeframe') or '1h'):4} "
            f"{str(r.get('data_health') or '—'):12} "
            f"{str(r.get('base_trades') if r.get('base_trades') is not None else '—'):>5} "
            f"{str(r.get('oos_dev_trades') if r.get('oos_dev_trades') is not None else '—'):>5} "
            f"{str(r.get('oos_val_trades') if r.get('oos_val_trades') is not None else '—'):>5} "
            f"{str(r.get('oos_status') or '—')[:22]:22} "
            f"{str(r.get('research_quality') or '—')[:22]:22} "
            f"{ev_s:22} "
            f"{str(r.get('review_status') or '—')[:32]:32} "
            f"{'false'}"
        )
    return "\n".join(lines)
