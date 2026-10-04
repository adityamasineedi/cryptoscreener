"""Validation, reconciliation, and OOS classification for pullback-rejection research."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import (
    DEFAULT_PULLBACK_REJECTION_WINDOWS,
    SAFETY_STAMPS,
)
from app.research.short_research_forensics import (
    apply_independent_replay_to_trade,
    audit_trades_for_lookahead,
    enrich_trade_forensic_fields,
)
from app.research.short_research_quality import reconcile_research_blotter
from app.research.short_research_windows import (
    ResearchWindows,
    assert_partition_time_order,
    filter_candles_to_window,
    partition_candles,
    validate_research_windows,
)


def windows_from_dict(d: Mapping[str, Any] | None = None) -> ResearchWindows:
    src = dict(DEFAULT_PULLBACK_REJECTION_WINDOWS)
    if d:
        src.update({k: v for k, v in d.items() if k in ResearchWindows.__dataclass_fields__})
    return ResearchWindows(
        requested_start=str(src["requested_start"]),
        requested_end=str(src["requested_end"]),
        base_start=str(src["base_start"]),
        base_end=str(src["base_end"]),
        oos_dev_start=str(src["oos_dev_start"]),
        oos_dev_end=str(src["oos_dev_end"]),
        oos_val_start=str(src["oos_val_start"]),
        oos_val_end=str(src["oos_val_end"]),
        policy=str(src.get("policy") or "POLICY_A_STRICT_DISJOINT"),
        oos_policy=str(src.get("oos_policy") or "VALIDATION_DETERMINES_STATUS"),
    )


def assert_disjoint(windows: ResearchWindows) -> dict[str, Any]:
    return validate_research_windows(windows)


def summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(t) for t in trades]
    if not rows:
        return {
            "trade_count": 0,
            "win_rate": None,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "net_pnl": 0.0,
            "average_gross_r": None,
            "average_net_r": None,
            "profit_factor": None,
            "maximum_drawdown": 0.0,
            "maximum_losing_streak": 0,
            "avg_mfe_r": None,
            "avg_mae_r": None,
            "avg_planned_rr": None,
            "closed_trades": [],
            **SAFETY_STAMPS,
        }

    rs = [float(t.get("R") if t.get("R") is not None else t.get("r_net") or 0.0) for t in rows]
    gross_rs = [
        float(t["r_gross"]) if t.get("r_gross") is not None else float(t.get("R") or 0.0)
        for t in rows
    ]
    nets = [float(t.get("net_pnl") or 0.0) for t in rows]
    grosses = [float(t.get("gross_pnl") or 0.0) for t in rows]
    fees = [float(t.get("fees") or 0.0) for t in rows]
    wins = sum(1 for r in rs if r > 0)
    gains = sum(r for r in rs if r > 0)
    losses = sum(abs(r) for r in rs if r < 0)
    peak = eq = dd = 0.0
    streak = max_streak = 0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        if r < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    def _avg(vals: list[float | None]) -> float | None:
        clean = [float(v) for v in vals if v is not None]
        return (sum(clean) / len(clean)) if clean else None

    return {
        "trade_count": len(rows),
        "win_rate": wins / len(rs) if rs else None,
        "gross_pnl": sum(grosses),
        "fees": sum(fees),
        "net_pnl": sum(nets),
        "average_gross_r": (sum(gross_rs) / len(gross_rs)) if gross_rs else None,
        "average_net_r": (sum(rs) / len(rs)) if rs else None,
        "profit_factor": (gains / losses) if losses > 0 else (float("inf") if gains > 0 else None),
        "maximum_drawdown": dd,
        "maximum_losing_streak": max_streak,
        "avg_mfe_r": _avg([t.get("mfe_r") for t in rows]),
        "avg_mae_r": _avg([t.get("mae_r") for t in rows]),
        "avg_planned_rr": _avg([t.get("planned_rr") for t in rows]),
        "closed_trades": rows,
        **SAFETY_STAMPS,
    }


def by_rejection_type(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        key = str(t.get("rejection_type") or "UNKNOWN")
        groups.setdefault(key, []).append(dict(t))
    return {k: summarize_trades(v) for k, v in groups.items()}


def by_regime(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        key = str(t.get("htf_regime") or "UNKNOWN")
        groups.setdefault(key, []).append(dict(t))
    return {k: summarize_trades(v) for k, v in groups.items()}


def distribution(trades: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    vals = [float(t[field]) for t in trades if t.get(field) is not None]
    if not vals:
        return {"count": 0, "mean": None, "p50": None, "min": None, "max": None}
    vals_sorted = sorted(vals)
    mid = len(vals_sorted) // 2
    p50 = (
        vals_sorted[mid]
        if len(vals_sorted) % 2 == 1
        else 0.5 * (vals_sorted[mid - 1] + vals_sorted[mid])
    )
    return {
        "count": len(vals),
        "mean": sum(vals) / len(vals),
        "p50": p50,
        "min": vals_sorted[0],
        "max": vals_sorted[-1],
    }


def reconcile_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = summarize_trades(trades)
    closed = list(summary.get("closed_trades") or [])
    closed.sort(
        key=lambda t: str(t.get("entry_time") or t.get("signal_time") or "")
    )
    recon = reconcile_research_blotter(
        closed,
        reported_net_pnl=summary.get("net_pnl"),
        reported_fees=summary.get("fees"),
        reported_avg_r=summary.get("average_net_r"),
        reported_profit_factor=summary.get("profit_factor"),
    )
    return {
        "equity_reconciliation": recon.get("equity_reconciliation"),
        "fee_reconciliation": recon.get("fee_reconciliation"),
        "trade_order_reconciliation": recon.get("trade_order_reconciliation"),
        "ok": bool(recon.get("ok")),
        "details": recon,
    }


def forensic_direct_replay(
    trades: Sequence[Mapping[str, Any]],
    *,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    candles_4h_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    c4 = candles_4h_by_symbol or {}
    enriched = []
    for t in trades:
        row = enrich_trade_forensic_fields({**t, "direction": "SHORT"})
        sym = str(t.get("symbol") or "").upper()
        try:
            row = apply_independent_replay_to_trade(
                row,
                candles_1h=list(candles_by_symbol.get(sym) or []),
                candles_4h=list(c4.get(sym) or []),
            )
        except Exception:
            pass
        enriched.append(row)
    audit = audit_trades_for_lookahead(enriched, timeframe="1h")
    return {
        "forensic": audit,
        "direct_candle_replay": bool(audit.get("forensic_pass_for_promotion")),
        "evidence_classes": audit.get("evidence_classes"),
        "trades": enriched,
    }


def partition_trades(
    trades: Sequence[Mapping[str, Any]],
    windows: ResearchWindows,
) -> dict[str, list[dict[str, Any]]]:
    """Partition by entry_time calendar date."""
    fake_candles = [
        {"time": t.get("entry_time") or t.get("signal_time"), **t} for t in trades
    ]
    parts = partition_candles(fake_candles, windows)
    return {
        "base": [dict(c) for c in parts["base"]],
        "oos_dev": [dict(c) for c in parts["oos_dev"]],
        "oos_val": [dict(c) for c in parts["oos_val"]],
    }


def classify_research(
    *,
    base_summary: Mapping[str, Any],
    oos_dev_summary: Mapping[str, Any] | None,
    oos_val_summary: Mapping[str, Any] | None,
    oos_val_trades: int,
    recon_ok: bool,
    direct_replay: bool,
    windows_ok: bool,
) -> dict[str, Any]:
    blockers: list[str] = []
    if not windows_ok:
        blockers.append("invalid_windows")
    if not recon_ok:
        blockers.append("reconciliation")
    if not direct_replay and oos_val_trades > 0:
        blockers.append("direct_candle_replay")
    if oos_val_trades < 30:
        blockers.append("oos_validation_sample_ge_30")

    def _pass(summary: Mapping[str, Any] | None) -> bool:
        if not summary:
            return False
        net = summary.get("net_pnl")
        avg = summary.get("average_net_r")
        pf = summary.get("profit_factor")
        return (
            net is not None
            and float(net) > 0
            and avg is not None
            and float(avg) > 0
            and pf is not None
            and float(pf) > 1
        )

    base_pass = _pass(base_summary)
    if not base_pass:
        return {
            "oos_status": "NOT_APPLICABLE",
            "research_classification": "RESEARCH_REJECTED",
            "reason": "base_research_rejected",
            "blockers": blockers + ["base_research_rejected"],
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
            **SAFETY_STAMPS,
        }

    # Validation partition determines final OOS status.
    if oos_val_summary is None or oos_val_trades == 0:
        return {
            "oos_status": "NOT_RUN",
            "research_classification": "RESEARCH_REJECTED",
            "reason": "oos_validation_missing",
            "blockers": blockers + ["oos_validation_missing"],
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
            **SAFETY_STAMPS,
        }

    val_pass = _pass(oos_val_summary) and oos_val_trades >= 30 and recon_ok and windows_ok
    # Direct replay required when trades exist; soft-note if forensic path incomplete.
    if blockers:
        val_pass = False

    if val_pass:
        return {
            "oos_status": "PASS",
            "research_classification": "SHORT_RESEARCH_CANDIDATE",
            "reason": "oos_validation_pass",
            "blockers": [],
            "oos_dev_pass": _pass(oos_dev_summary),
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
            **SAFETY_STAMPS,
        }

    return {
        "oos_status": "FAIL",
        "research_classification": "RESEARCH_REJECTED",
        "reason": "oos_validation_fail",
        "blockers": blockers
        + (
            []
            if _pass(oos_val_summary)
            else ["oos_validation_metrics"]
        ),
        "oos_dev_pass": _pass(oos_dev_summary),
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        **SAFETY_STAMPS,
    }


def filter_trades_to_window(
    trades: Sequence[Mapping[str, Any]],
    *,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    fake = [{"time": t.get("entry_time") or t.get("signal_time"), **t} for t in trades]
    return [dict(c) for c in filter_candles_to_window(fake, start=start, end=end)]


def rejection_type_counts(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(t.get("rejection_type") or "UNKNOWN") for t in trades))


__all__ = [
    "windows_from_dict",
    "assert_disjoint",
    "summarize_trades",
    "by_rejection_type",
    "by_regime",
    "distribution",
    "reconcile_trades",
    "forensic_direct_replay",
    "partition_trades",
    "classify_research",
    "filter_trades_to_window",
    "rejection_type_counts",
    "assert_partition_time_order",
]
