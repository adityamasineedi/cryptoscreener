"""COMBO_02 entry-rejection diagnostics (observability only).

Captures why bars do not produce new entries. Never alters gate order,
signals, fills, risk, or backtest outcomes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

# Taxonomy stages (map existing code paths; do not invent strategy reasons).
STAGE_BOS = "BOS"
STAGE_STRUCTURE = "STRUCTURE"
STAGE_TREND = "TREND"
STAGE_HTF_ALIGNMENT = "HTF_ALIGNMENT"
STAGE_DIRECTION = "DIRECTION"
STAGE_RISK = "RISK"
STAGE_RR = "RR"
STAGE_POSITION_STATE = "POSITION_STATE"
STAGE_DATA = "DATA"
STAGE_OTHER = "OTHER"
STAGE_UNKNOWN = "UNKNOWN_NOT_EXPORTED"

SETUP_NO_SETUP = "NO_SETUP"
SETUP_REJECTED = "SETUP_REJECTED"
SETUP_ENTRY_READY_REJECTED = "ENTRY_READY_REJECTED"
SETUP_POSITION_BLOCKED = "POSITION_BLOCKED"
SETUP_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
SETUP_ENTRY_ACCEPTED = "ENTRY_ACCEPTED"

EVAL_NEW_ENTRY = "NEW_ENTRY"
EVAL_POSITION_OPEN = "POSITION_OPEN"
EVAL_POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
EVAL_NO_EVALUATION = "NO_EVALUATION"

# Backtest walk skip reasons (not returned by evaluate_combination_at_bar).
REASON_BOS_PREFILTER_SKIP = "BOS_PREFILTER_SKIP"
REASON_STICKY_BOS_SKIP = "STICKY_BOS_SKIP"
REASON_DIRECTION_FILTER = "DIRECTION_FILTER_MISMATCH"
REASON_POSITION_ACTIVE = "POSITION_ACTIVE_NOT_NEW_ENTRY"
REASON_ENTRY_ACCEPTED = "ENTRY_ACCEPTED"
REASON_UNKNOWN = "UNKNOWN_NOT_EXPORTED"
REASON_NO_STRATEGY_ENTRY = "NO_STRATEGY_ENTRY_AT_BAR"
# Walk-level (combination_backtest loop_start = max(min_bars, start)); not an engine gate.
REASON_INSUFFICIENT_BARS_BEFORE_WALK = "INSUFFICIENT_BARS_BEFORE_WALK"

# Required-gate walk order mirrors evaluate_combination_at_bar assembly.
_GATE_STAGE = {
    "bos": STAGE_BOS,
    "trend": STAGE_TREND,
    "hl_intact": STAGE_STRUCTURE,
    "lh_intact": STAGE_STRUCTURE,
    "impulse": STAGE_OTHER,
    "pullback": STAGE_OTHER,
    "rvol": STAGE_OTHER,
    "sd": STAGE_OTHER,
    "htf": STAGE_HTF_ALIGNMENT,
    "rr": STAGE_RR,
}

_REGIME_FUNNEL_KEYS = (
    "BEAR_TREND",
    "BULL_TREND",
    "CHOPPY",
    "HIGH_VOLATILITY_RANGE",
    "HIGH_VOLATILITY_TREND",
    "LOW_VOLATILITY_COMPRESSION",
    "RANGE",
    "TRANSITION",
    "UNKNOWN",
)

_CANDIDATE_LABELS = (
    "TREND_FOLLOWING_CANDIDATE",
    "SHORT_TREND_FOLLOWING_CANDIDATE",
    "MEAN_REVERSION_CANDIDATE",
    "BREAKOUT_CANDIDATE",
    "PULLBACK_OR_WAIT",
    "TRANSITION_WAIT",
)


def _failed_required_gates(
    gates: Mapping[str, Any] | None, required: Sequence[str] | None
) -> list[str]:
    g = gates or {}
    out: list[str] = []
    for key in required or []:
        if not g.get(key):
            out.append(str(key))
    return out


def classify_from_eval_setup(setup: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map evaluate_combination_at_bar return value → diagnostic fields.

    Preserves exact ``reason`` strings from the engine. Does not re-decide gates.
    """
    if not setup:
        return {
            "evaluation_type": EVAL_NEW_ENTRY,
            "setup_state": SETUP_NO_SETUP,
            "primary_rejection_stage": STAGE_UNKNOWN,
            "primary_rejection_reason": REASON_UNKNOWN,
            "rejection_detail_status": "NOT_EXPORTED",
            "direction_considered": None,
            "gates": {},
            "required": [],
            "failed_gates": [],
            "status": None,
            "rejection_chain": [],
        }

    status = str(setup.get("status") or "")
    direction = setup.get("direction")
    gates = dict(setup.get("gates") or {})
    required = list(setup.get("required") or [])
    reason = setup.get("reason")
    htf = setup.get("htf") or {}
    failed = _failed_required_gates(gates, required)
    chain: list[dict[str, str]] = []

    if status in ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE", "ENTRY_CANDIDATE"):
        return {
            "evaluation_type": EVAL_NEW_ENTRY,
            "setup_state": SETUP_ENTRY_ACCEPTED,
            "primary_rejection_stage": None,
            "primary_rejection_reason": REASON_ENTRY_ACCEPTED,
            "rejection_detail_status": "AVAILABLE",
            "direction_considered": direction,
            "gates": gates,
            "required": required,
            "failed_gates": [],
            "status": status,
            "htf_alignment": htf.get("htf_alignment"),
            "trend_4h": htf.get("trend_4h"),
            "trend_1h": htf.get("trend_1h"),
            "hl_intact_reason": setup.get("hl_intact_reason"),
            "lh_intact_reason": setup.get("lh_intact_reason"),
            "rejection_chain": [],
        }

    # Post-gate risk / RR reasons (exact engine strings).
    reason_s = str(reason or "")
    if reason_s.startswith("TP1_R ") or reason_s.startswith("R:R below"):
        primary_stage = STAGE_RR
        setup_state = SETUP_ENTRY_READY_REJECTED
        chain.append({"stage": STAGE_RR, "reason": reason_s})
    elif reason_s.startswith("STOP_INVALID") or reason_s in (
        "Existing stop engine could not compute structural stop",
        "Existing target engine produced no targets",
    ):
        primary_stage = STAGE_RISK
        setup_state = SETUP_ENTRY_READY_REJECTED
        chain.append({"stage": STAGE_RISK, "reason": reason_s})
    elif reason_s == "HTF candles missing — fail closed":
        primary_stage = STAGE_DATA
        setup_state = SETUP_DATA_UNAVAILABLE
        chain.append({"stage": STAGE_DATA, "reason": reason_s})
    elif reason_s.startswith("HTF gate blocked:"):
        primary_stage = STAGE_HTF_ALIGNMENT
        setup_state = (
            SETUP_REJECTED if gates.get("bos") else SETUP_NO_SETUP
        )
        chain.append({"stage": STAGE_HTF_ALIGNMENT, "reason": reason_s})
    elif reason_s.startswith("STRUCTURE_INVALID:"):
        primary_stage = STAGE_STRUCTURE
        setup_state = SETUP_REJECTED if gates.get("bos") else SETUP_NO_SETUP
        chain.append({"stage": STAGE_STRUCTURE, "reason": reason_s})
    elif reason_s == "Combination gates not satisfied":
        # Engine collapsed multiple failures; expose first failed required gate.
        if not failed and direction is None:
            primary_stage = STAGE_BOS
            setup_state = SETUP_NO_SETUP
            reason_s = "Combination gates not satisfied"
            chain.append(
                {
                    "stage": STAGE_BOS,
                    "reason": "Combination gates not satisfied",
                }
            )
        elif failed:
            first = failed[0]
            primary_stage = _GATE_STAGE.get(first, STAGE_OTHER)
            setup_state = (
                SETUP_NO_SETUP
                if first in ("bos",) or direction is None
                else SETUP_REJECTED
            )
            for fg in failed:
                chain.append(
                    {
                        "stage": _GATE_STAGE.get(fg, STAGE_OTHER),
                        "reason": f"Combination gates not satisfied:{fg}",
                    }
                )
        else:
            primary_stage = STAGE_OTHER
            setup_state = SETUP_NO_SETUP
            chain.append({"stage": STAGE_OTHER, "reason": reason_s})
    elif reason_s:
        primary_stage = STAGE_OTHER
        setup_state = SETUP_NO_SETUP
        chain.append({"stage": STAGE_OTHER, "reason": reason_s})
    else:
        primary_stage = STAGE_UNKNOWN
        setup_state = SETUP_NO_SETUP
        reason_s = REASON_UNKNOWN
        chain.append({"stage": STAGE_UNKNOWN, "reason": REASON_UNKNOWN})

    # Additional chain entries for other failed gates not already listed.
    seen_reasons = {c["reason"] for c in chain}
    for fg in failed:
        r = f"Combination gates not satisfied:{fg}"
        if r not in seen_reasons and reason_s != "Combination gates not satisfied":
            chain.append({"stage": _GATE_STAGE.get(fg, STAGE_OTHER), "reason": r})

    return {
        "evaluation_type": EVAL_NEW_ENTRY,
        "setup_state": setup_state,
        "primary_rejection_stage": primary_stage,
        "primary_rejection_reason": reason_s,
        "rejection_detail_status": "AVAILABLE",
        "direction_considered": direction,
        "gates": gates,
        "required": required,
        "failed_gates": failed,
        "status": status,
        "htf_alignment": htf.get("htf_alignment"),
        "trend_4h": htf.get("trend_4h"),
        "trend_1h": htf.get("trend_1h"),
        "hl_intact_reason": setup.get("hl_intact_reason"),
        "lh_intact_reason": setup.get("lh_intact_reason"),
        "rejection_chain": chain,
    }


def classify_bos_prefilter_skip() -> dict[str, Any]:
    return {
        "evaluation_type": EVAL_NEW_ENTRY,
        "setup_state": SETUP_NO_SETUP,
        "primary_rejection_stage": STAGE_BOS,
        "primary_rejection_reason": REASON_BOS_PREFILTER_SKIP,
        "rejection_detail_status": "AVAILABLE",
        "direction_considered": None,
        "gates": {},
        "required": [],
        "failed_gates": ["bos"],
        "status": "NO_SETUP",
        "rejection_chain": [
            {"stage": STAGE_BOS, "reason": REASON_BOS_PREFILTER_SKIP}
        ],
    }


def classify_sticky_bos_skip() -> dict[str, Any]:
    return {
        "evaluation_type": EVAL_NEW_ENTRY,
        "setup_state": SETUP_NO_SETUP,
        "primary_rejection_stage": STAGE_BOS,
        "primary_rejection_reason": REASON_STICKY_BOS_SKIP,
        "rejection_detail_status": "AVAILABLE",
        "direction_considered": None,
        "gates": {},
        "required": [],
        "failed_gates": [],
        "status": "NO_SETUP",
        "rejection_chain": [
            {"stage": STAGE_BOS, "reason": REASON_STICKY_BOS_SKIP}
        ],
    }


def classify_direction_filter_mismatch(
    direction: str | None, direction_filter: str | None
) -> dict[str, Any]:
    reason = (
        f"{REASON_DIRECTION_FILTER}:{direction}->{direction_filter}"
    )
    return {
        "evaluation_type": EVAL_NEW_ENTRY,
        "setup_state": SETUP_REJECTED,
        "primary_rejection_stage": STAGE_DIRECTION,
        "primary_rejection_reason": reason,
        "rejection_detail_status": "AVAILABLE",
        "direction_considered": direction,
        "gates": {},
        "required": [],
        "failed_gates": [],
        "status": "FILTERED",
        "rejection_chain": [
            {"stage": STAGE_DIRECTION, "reason": reason}
        ],
    }


def classify_position_bar(*, exited: bool) -> dict[str, Any]:
    return {
        "evaluation_type": (
            EVAL_POSITION_MANAGEMENT if exited else EVAL_POSITION_OPEN
        ),
        "setup_state": SETUP_POSITION_BLOCKED,
        "primary_rejection_stage": STAGE_POSITION_STATE,
        "primary_rejection_reason": REASON_POSITION_ACTIVE,
        "rejection_detail_status": "AVAILABLE",
        "direction_considered": None,
        "gates": {},
        "required": [],
        "failed_gates": [],
        "status": "POSITION_ACTIVE",
        "rejection_chain": [
            {
                "stage": STAGE_POSITION_STATE,
                "reason": REASON_POSITION_ACTIVE,
            }
        ],
    }


def classify_insufficient_bars_before_walk() -> dict[str, Any]:
    return {
        "evaluation_type": EVAL_NO_EVALUATION,
        "setup_state": SETUP_DATA_UNAVAILABLE,
        "primary_rejection_stage": STAGE_DATA,
        "primary_rejection_reason": REASON_INSUFFICIENT_BARS_BEFORE_WALK,
        "rejection_detail_status": "AVAILABLE",
        "direction_considered": None,
        "gates": {},
        "required": [],
        "failed_gates": [],
        "status": "NO_EVALUATION",
        "rejection_chain": [
            {
                "stage": STAGE_DATA,
                "reason": REASON_INSUFFICIENT_BARS_BEFORE_WALK,
            }
        ],
    }


def flatten_rejection_chain(
    chain: Sequence[Mapping[str, str]] | None, *, max_n: int = 8
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, item in enumerate(list(chain or [])[:max_n], start=1):
        out[f"rejection_stage_{i}"] = item.get("stage")
        out[f"rejection_reason_{i}"] = item.get("reason")
    return out


def record_for_bar(
    bar_index: int,
    *,
    classified: Mapping[str, Any],
    signal_time: str | None = None,
) -> dict[str, Any]:
    """Compact per-bar diagnostic record for the backtest side-channel."""
    chain = list(classified.get("rejection_chain") or [])
    row = {
        "bar_index": int(bar_index),
        "signal_time": signal_time,
        "evaluation_type": classified.get("evaluation_type"),
        "setup_state": classified.get("setup_state"),
        "primary_rejection_stage": classified.get("primary_rejection_stage"),
        "primary_rejection_reason": classified.get("primary_rejection_reason"),
        "rejection_detail_status": classified.get(
            "rejection_detail_status", "AVAILABLE"
        ),
        "direction_considered": classified.get("direction_considered"),
        "status": classified.get("status"),
        "gates": dict(classified.get("gates") or {}),
        "required": list(classified.get("required") or []),
        "failed_gates": list(classified.get("failed_gates") or []),
        "htf_alignment": classified.get("htf_alignment"),
        "trend_4h": classified.get("trend_4h"),
        "trend_1h": classified.get("trend_1h"),
        "hl_intact_reason": classified.get("hl_intact_reason"),
        "lh_intact_reason": classified.get("lh_intact_reason"),
        "rejection_chain": chain,
    }
    row.update(flatten_rejection_chain(chain))
    return row


def merge_diag_into_bar_row(
    bar_row: dict[str, Any],
    diag: Mapping[str, Any] | None,
    *,
    entry_attr: str,
) -> None:
    """Mutate analytics bar row with strategy entry diagnostics (observability)."""
    if entry_attr == "EXECUTION_BAR":
        bar_row["evaluation_type"] = EVAL_NEW_ENTRY
        bar_row["setup_state"] = SETUP_ENTRY_ACCEPTED
        # Keep acceptance fields; do not invent rejection.
        if diag:
            bar_row["direction_considered"] = diag.get("direction_considered") or bar_row.get(
                "direction"
            )
            for k in (
                "gates",
                "required",
                "failed_gates",
                "htf_alignment",
                "rejection_stage_1",
                "rejection_reason_1",
            ):
                if k in diag and diag[k] is not None:
                    bar_row[k] = diag[k]
        return

    if entry_attr == "POSITION_ACTIVE":
        # Ledger attribution already sets POSITION_ACTIVE_NOT_NEW_ENTRY.
        bar_row["evaluation_type"] = (
            (diag or {}).get("evaluation_type") or EVAL_POSITION_OPEN
        )
        bar_row["setup_state"] = SETUP_POSITION_BLOCKED
        return

    if diag and diag.get("evaluation_type") == EVAL_NO_EVALUATION:
        bar_row["evaluation_type"] = EVAL_NO_EVALUATION
        bar_row["setup_state"] = diag.get("setup_state") or SETUP_DATA_UNAVAILABLE
        reason = diag.get("primary_rejection_reason") or REASON_INSUFFICIENT_BARS_BEFORE_WALK
        bar_row["primary_rejection_stage"] = diag.get("primary_rejection_stage") or STAGE_DATA
        bar_row["primary_rejection_reason"] = reason
        bar_row["rejection_reason"] = reason
        bar_row["rejection_detail_status"] = "AVAILABLE"
        return

    if not diag:
        bar_row["evaluation_type"] = EVAL_NEW_ENTRY
        bar_row["setup_state"] = SETUP_NO_SETUP
        bar_row["primary_rejection_stage"] = STAGE_UNKNOWN
        bar_row["primary_rejection_reason"] = REASON_NO_STRATEGY_ENTRY
        bar_row["rejection_reason"] = REASON_NO_STRATEGY_ENTRY
        bar_row["rejection_detail_status"] = "NOT_EXPORTED"
        return

    bar_row["evaluation_type"] = diag.get("evaluation_type") or EVAL_NEW_ENTRY
    bar_row["setup_state"] = diag.get("setup_state") or SETUP_NO_SETUP
    bar_row["primary_rejection_stage"] = diag.get("primary_rejection_stage")
    reason = diag.get("primary_rejection_reason")
    bar_row["primary_rejection_reason"] = reason
    bar_row["rejection_reason"] = reason
    bar_row["rejection_detail_status"] = diag.get(
        "rejection_detail_status", "AVAILABLE"
    )
    bar_row["direction_considered"] = diag.get("direction_considered")
    for k in (
        "gates",
        "required",
        "failed_gates",
        "htf_alignment",
        "status",
        "hl_intact_reason",
        "lh_intact_reason",
    ):
        if diag.get(k) is not None:
            bar_row[k] = diag.get(k)
    for i in range(1, 9):
        sk = f"rejection_stage_{i}"
        rk = f"rejection_reason_{i}"
        if diag.get(sk) is not None:
            bar_row[sk] = diag.get(sk)
        if diag.get(rk) is not None:
            bar_row[rk] = diag.get(rk)


def _pct(n: int, d: int) -> float | None:
    if d <= 0:
        return None
    return n / d


def _funnel_rows(counts: Mapping[str, int]) -> list[dict[str, Any]]:
    order = [
        ("NEW_ENTRY_EVALUATION", "NEW_ENTRY_EVALUATION"),
        ("SETUP_DETECTED", "SETUP_DETECTED"),
        ("BOS_CONFIRMED", "BOS_CONFIRMED"),
        ("HTF_ALIGNED", "HTF_ALIGNED"),
        ("ENTRY_CONFIRMATION", "ENTRY_CONFIRMATION"),
        ("RISK_VALID", "RISK_VALID"),
        ("RR_VALID", "RR_VALID"),
        ("ENTRY_ACCEPTED", "ENTRY_ACCEPTED"),
    ]
    rows: list[dict[str, Any]] = []
    prev: int | None = None
    for label, key in order:
        c = int(counts.get(key, 0))
        rows.append(
            {
                "stage": label,
                "count": c,
                "pct_of_previous": _pct(c, prev) if prev is not None else 1.0,
                "pct_of_new_entry": _pct(
                    c, int(counts.get("NEW_ENTRY_EVALUATION", 0))
                ),
            }
        )
        prev = c
    return rows


def _stage_flags(row: Mapping[str, Any]) -> dict[str, bool]:
    """Infer funnel stage membership from exported diagnostic + structure fields."""
    eval_t = str(row.get("evaluation_type") or "")
    if eval_t != EVAL_NEW_ENTRY:
        return {
            "NEW_ENTRY_EVALUATION": False,
            "SETUP_DETECTED": False,
            "BOS_CONFIRMED": False,
            "HTF_ALIGNED": False,
            "ENTRY_CONFIRMATION": False,
            "RISK_VALID": False,
            "RR_VALID": False,
            "ENTRY_ACCEPTED": False,
        }

    setup_state = str(row.get("setup_state") or "")
    reason = str(row.get("primary_rejection_reason") or "")
    stage = str(row.get("primary_rejection_stage") or "")
    gates = row.get("gates") if isinstance(row.get("gates"), Mapping) else {}
    accepted = setup_state == SETUP_ENTRY_ACCEPTED or row.get("entry") == "YES"

    bos_confirmed = bool(gates.get("bos")) if gates else False
    if not gates:
        # Prefilter / sticky skips are not BOS-confirmed setups.
        if reason in (REASON_BOS_PREFILTER_SKIP, REASON_STICKY_BOS_SKIP):
            bos_confirmed = False
        elif stage == STAGE_HTF_ALIGNMENT or reason.startswith("HTF gate"):
            # Engine only emits HTF reason when HTF required failed; BOS may
            # still be true — unknown without gates → do not guess True.
            bos_confirmed = False
        elif accepted:
            bos_confirmed = True

    setup_detected = bos_confirmed or reason in (
        REASON_STICKY_BOS_SKIP,
    ) or setup_state in (
        SETUP_REJECTED,
        SETUP_ENTRY_READY_REJECTED,
        SETUP_ENTRY_ACCEPTED,
    ) or (gates.get("bos") is True)

    htf_aligned = bool(gates.get("htf")) if gates else False
    if accepted:
        htf_aligned = True
    if reason.startswith("HTF gate") or reason == "HTF candles missing — fail closed":
        htf_aligned = False

    # Entry confirmation = trend + structure intact (HL/LH) when gates known.
    if gates:
        entry_conf = bool(gates.get("trend", True)) and bool(
            gates.get("hl_intact", True)
        ) and bool(gates.get("lh_intact", True))
        if bos_confirmed and not entry_conf:
            entry_conf = False
        elif not bos_confirmed:
            entry_conf = False
    else:
        entry_conf = False
        if accepted:
            entry_conf = True
        elif setup_state == SETUP_ENTRY_READY_REJECTED:
            entry_conf = True
        elif stage in (STAGE_RISK, STAGE_RR):
            entry_conf = True

    risk_valid = False
    rr_valid = False
    if accepted:
        risk_valid = True
        rr_valid = True
    elif setup_state == SETUP_ENTRY_READY_REJECTED:
        if stage == STAGE_RR or reason.startswith("TP1_R") or reason.startswith("R:R"):
            risk_valid = True
            rr_valid = False
        elif stage == STAGE_RISK:
            risk_valid = False
            rr_valid = False
    elif entry_conf and htf_aligned and bos_confirmed:
        # Passed confirmation but rejected earlier than risk — not risk-valid.
        risk_valid = False

    if gates and bos_confirmed and htf_aligned and entry_conf:
        # If we reached risk engines, gates may include rr after failure.
        if setup_state == SETUP_ENTRY_READY_REJECTED:
            pass
        elif accepted:
            pass

    return {
        "NEW_ENTRY_EVALUATION": True,
        "SETUP_DETECTED": bool(setup_detected),
        "BOS_CONFIRMED": bool(bos_confirmed),
        "HTF_ALIGNED": bool(htf_aligned and bos_confirmed),
        "ENTRY_CONFIRMATION": bool(entry_conf and htf_aligned and bos_confirmed),
        "RISK_VALID": bool(
            risk_valid and entry_conf and htf_aligned and bos_confirmed
        ),
        "RR_VALID": bool(
            rr_valid and risk_valid and entry_conf and htf_aligned and bos_confirmed
        ),
        "ENTRY_ACCEPTED": bool(accepted),
    }


def build_entry_funnel_reports(
    by_bar: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate entry funnel / rejection rankings from annotated by_bar rows."""
    new_entry_rows = [
        r for r in by_bar if str(r.get("evaluation_type") or "") == EVAL_NEW_ENTRY
    ]
    totals = Counter()
    for r in new_entry_rows:
        flags = _stage_flags(r)
        for k, v in flags.items():
            if v:
                totals[k] += 1

    overall = _funnel_rows(totals)

    # By regime
    by_regime: dict[str, Any] = {}
    for regime in _REGIME_FUNNEL_KEYS:
        rows = [
            r
            for r in new_entry_rows
            if str(r.get("market_regime") or "UNKNOWN") == regime
        ]
        c = Counter()
        for r in rows:
            for k, v in _stage_flags(r).items():
                if v:
                    c[k] += 1
        rej = Counter(
            str(r.get("primary_rejection_reason") or REASON_UNKNOWN)
            for r in rows
            if str(r.get("setup_state") or "") != SETUP_ENTRY_ACCEPTED
        )
        by_regime[regime] = {
            "bars": len(rows),
            "setup_candidates": int(c.get("SETUP_DETECTED", 0)),
            "bos_confirmed": int(c.get("BOS_CONFIRMED", 0)),
            "htf_aligned": int(c.get("HTF_ALIGNED", 0)),
            "entry_ready": int(c.get("ENTRY_CONFIRMATION", 0)),
            "risk_valid": int(c.get("RISK_VALID", 0)),
            "rr_valid": int(c.get("RR_VALID", 0)),
            "accepted": int(c.get("ENTRY_ACCEPTED", 0)),
            "rejection_reasons": [
                {"reason": k, "count": v} for k, v in rej.most_common()
            ],
            "funnel": _funnel_rows(c),
        }

    # By direction considered
    by_direction: dict[str, Any] = {}
    for direction in ("LONG", "SHORT", "NONE"):
        if direction == "NONE":
            rows = [
                r
                for r in new_entry_rows
                if not r.get("direction_considered")
                and str(r.get("setup_state") or "") != SETUP_ENTRY_ACCEPTED
            ]
            # Include accepted with trade direction
            rows = rows + [
                r
                for r in new_entry_rows
                if str(r.get("setup_state") or "") == SETUP_ENTRY_ACCEPTED
                and not r.get("direction_considered")
            ]
        else:
            rows = [
                r
                for r in new_entry_rows
                if str(r.get("direction_considered") or r.get("direction") or "")
                == direction
            ]
        c = Counter()
        for r in rows:
            for k, v in _stage_flags(r).items():
                if v:
                    c[k] += 1
        rej = Counter(
            str(r.get("primary_rejection_reason") or REASON_UNKNOWN)
            for r in rows
            if str(r.get("setup_state") or "") != SETUP_ENTRY_ACCEPTED
        )
        by_direction[direction] = {
            "bars": len(rows),
            "bos_confirmed": int(c.get("BOS_CONFIRMED", 0)),
            "setup_candidates": int(c.get("SETUP_DETECTED", 0)),
            "htf_aligned": int(c.get("HTF_ALIGNED", 0)),
            "entry_ready": int(c.get("ENTRY_CONFIRMATION", 0)),
            "accepted": int(c.get("ENTRY_ACCEPTED", 0)),
            "rejection_reasons": [
                {"reason": k, "count": v} for k, v in rej.most_common()
            ],
            "funnel": _funnel_rows(c),
        }

    # By research candidate label
    by_candidate: dict[str, Any] = {}
    for label in _CANDIDATE_LABELS:
        rows = [
            r for r in new_entry_rows if str(r.get("research_label") or "") == label
        ]
        c = Counter()
        for r in rows:
            for k, v in _stage_flags(r).items():
                if v:
                    c[k] += 1
        rej = Counter(
            str(r.get("primary_rejection_reason") or REASON_UNKNOWN)
            for r in rows
            if str(r.get("setup_state") or "") != SETUP_ENTRY_ACCEPTED
        )
        top = rej.most_common(1)
        by_candidate[label] = {
            "count": len(rows),
            "bos_confirmed": int(c.get("BOS_CONFIRMED", 0)),
            "htf_aligned": int(c.get("HTF_ALIGNED", 0)),
            "entry_ready": int(c.get("ENTRY_CONFIRMATION", 0)),
            "accepted": int(c.get("ENTRY_ACCEPTED", 0)),
            "primary_rejection_reason": top[0][0] if top else None,
            "rejection_reasons": [
                {"reason": k, "count": v} for k, v in rej.most_common()
            ],
        }

    # Rejection ranking among NEW_ENTRY non-accepted
    setup_like = [
        r
        for r in new_entry_rows
        if str(r.get("setup_state") or "")
        in (SETUP_REJECTED, SETUP_ENTRY_READY_REJECTED, SETUP_NO_SETUP, SETUP_DATA_UNAVAILABLE)
    ]
    rej_all = Counter(
        str(r.get("primary_rejection_reason") or REASON_UNKNOWN) for r in setup_like
    )
    n_eval = len(setup_like) or 1
    rejection_ranking = [
        {
            "rejection_reason": reason,
            "count": cnt,
            "pct_of_evaluated": cnt / n_eval,
        }
        for reason, cnt in rej_all.most_common()
    ]

    unknown_rows = [
        {
            "bar_index": r.get("bar_index"),
            "decision_time": r.get("decision_time"),
            "evaluation_type": r.get("evaluation_type"),
            "setup_state": r.get("setup_state"),
            "primary_rejection_stage": r.get("primary_rejection_stage"),
            "primary_rejection_reason": r.get("primary_rejection_reason"),
            "research_label": r.get("research_label"),
            "market_regime": r.get("market_regime"),
        }
        for r in by_bar
        if str(r.get("primary_rejection_stage") or "") == STAGE_UNKNOWN
        or str(r.get("primary_rejection_reason") or "")
        in (REASON_UNKNOWN, REASON_NO_STRATEGY_ENTRY)
        and str(r.get("evaluation_type") or "") == EVAL_NEW_ENTRY
        and str(r.get("setup_state") or "") != SETUP_ENTRY_ACCEPTED
    ]

    return {
        "total_bars": len(by_bar),
        "new_entry_evaluations": len(new_entry_rows),
        "no_evaluation_bars": sum(
            1
            for r in by_bar
            if str(r.get("evaluation_type") or "") == EVAL_NO_EVALUATION
        ),
        "position_open_bars": sum(
            1
            for r in by_bar
            if str(r.get("evaluation_type") or "") == EVAL_POSITION_OPEN
        ),
        "position_management_bars": sum(
            1
            for r in by_bar
            if str(r.get("evaluation_type") or "") == EVAL_POSITION_MANAGEMENT
        ),
        "overall_funnel": overall,
        "by_regime": by_regime,
        "by_direction": by_direction,
        "by_research_candidate": by_candidate,
        "rejection_ranking": rejection_ranking,
        "unknown_not_exported_rows": unknown_rows,
        "unknown_not_exported_count": len(unknown_rows),
        "disclaimer": (
            "Observability only — does not alter COMBO_02 entry decisions"
        ),
    }


def format_funnel_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# COMBO_02 Entry Rejection Funnel",
        "",
        report.get("disclaimer") or "",
        "",
        f"Total bars: {report.get('total_bars')}",
        f"New-entry evaluations: {report.get('new_entry_evaluations')}",
        f"No-evaluation bars (pre-walk): {report.get('no_evaluation_bars')}",
        f"Position-open bars: {report.get('position_open_bars')}",
        f"Position-management bars: {report.get('position_management_bars')}",
        "",
        "## Overall funnel",
        "",
        f"{'Stage':<28} {'Count':>8} {'% prev':>10}",
        "-" * 50,
    ]
    for row in report.get("overall_funnel") or []:
        pct = row.get("pct_of_previous")
        pct_s = f"{pct*100:.1f}%" if isinstance(pct, float) else "—"
        lines.append(
            f"{str(row.get('stage')):<28} {int(row.get('count') or 0):>8} {pct_s:>10}"
        )
    lines.extend(["", "## Top rejection reasons", ""])
    for row in (report.get("rejection_ranking") or [])[:25]:
        lines.append(
            f"- {row.get('rejection_reason')}: {row.get('count')} "
            f"({(row.get('pct_of_evaluated') or 0)*100:.1f}%)"
        )
    lines.extend(
        [
            "",
            f"UNKNOWN_NOT_EXPORTED remaining: "
            f"{report.get('unknown_not_exported_count')}",
            "",
        ]
    )
    return "\n".join(lines)
