"""Validation helpers for SHORT entry research variants."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from app.research.combo02_candidate_research import compute_trade_metrics
from app.research.short_entry_research.constants import SAFETY_STAMPS
from app.research.short_research_forensics import (
    apply_independent_replay_to_trade,
    audit_trades_for_lookahead,
    enrich_trade_forensic_fields,
)
from app.research.short_research_quality import (
    reconcile_research_blotter,
    validate_base_oos_split,
)
from app.research.short_research_windows import ResearchWindows, validate_research_windows
from app.research.trade_fees import enrich_trades


def summarize_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float = 20.0,
) -> dict[str, Any]:
    prepared = []
    for t in trades:
        row = dict(t)
        if row.get("exit_price") is None:
            oc = str(row.get("outcome") or "").upper()
            if oc in ("SL", "STOP"):
                row["exit_price"] = row.get("stop_price")
            elif oc.startswith("TP"):
                row["exit_price"] = row.get("tp1") or row.get("TP1")
        if row.get("r_net") is None and row.get("R") is not None:
            row["r_net"] = row["R"]
        if row.get("gross_pnl_usd") is None and row.get("gross_pnl") is not None:
            row["gross_pnl_usd"] = row["gross_pnl"]
        if row.get("net_pnl_usd") is None and row.get("net_pnl") is not None:
            row["net_pnl_usd"] = row["net_pnl"]
        if row.get("fee_total_usd") is None and row.get("fees") is not None:
            row["fee_total_usd"] = row["fees"]
        prepared.append(row)

    if prepared and all(p.get("net_pnl") is not None for p in prepared):
        # Aggregate stored blotter fields
        rs = [
            float(p["R"] if p.get("R") is not None else p.get("r_net") or 0.0)
            for p in prepared
        ]
        nets = [float(p["net_pnl"]) for p in prepared]
        grosses = [float(p.get("gross_pnl") or 0.0) for p in prepared]
        fees = [float(p.get("fees") or 0.0) for p in prepared]
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
        return {
            "trade_count": len(prepared),
            "win_rate": (wins / len(rs)) if rs else None,
            "gross_pnl": sum(grosses),
            "fees": sum(fees),
            "net_pnl": sum(nets),
            "average_net_r": (sum(rs) / len(rs)) if rs else None,
            "profit_factor": (gains / losses) if losses > 0 else (float("inf") if gains > 0 else None),
            "maximum_drawdown": dd,
            "maximum_losing_streak": max_streak,
            "closed_trades": prepared,
            **SAFETY_STAMPS,
        }

    enriched = enrich_trades(prepared, risk_usd=risk_usd, closed_only=True)
    metrics = compute_trade_metrics(enriched)
    return {
        "trade_count": metrics.get("trade_count"),
        "win_rate": metrics.get("win_rate"),
        "gross_pnl": metrics.get("gross_pnl"),
        "fees": metrics.get("total_fees"),
        "net_pnl": metrics.get("net_pnl"),
        "average_net_r": metrics.get("net_avg_r"),
        "profit_factor": metrics.get("profit_factor"),
        "maximum_drawdown": metrics.get("max_drawdown_r"),
        "maximum_losing_streak": metrics.get("max_losing_streak"),
        "closed_trades": enriched,
        **SAFETY_STAMPS,
    }


def reconcile_variant(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = summarize_trades(trades)
    closed = list(summary.get("closed_trades") or [])
    # Chronological order required for trade-order reconciliation across symbols.
    closed.sort(
        key=lambda t: str(
            t.get("entry_time") or t.get("fill_time") or t.get("signal_time") or ""
        )
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


def classify_oos_status(
    *,
    base_summary: Mapping[str, Any],
    oos_val_summary: Mapping[str, Any] | None,
    oos_val_trades: int,
    recon_ok: bool,
    direct_replay: bool,
    windows_ok: bool,
    oos_consumed: bool = False,
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
    if oos_consumed:
        blockers.append("oos_window_consumed_needs_new_holdout")

    base_net = base_summary.get("net_pnl")
    base_avg = base_summary.get("average_net_r")
    base_pf = base_summary.get("profit_factor")
    base_pass = (
        base_net is not None
        and float(base_net) > 0
        and base_avg is not None
        and float(base_avg) > 0
        and base_pf is not None
        and float(base_pf) > 1
    )
    if not base_pass:
        return {
            "oos_status": "NOT_APPLICABLE",
            "review_status": "REVIEW_BLOCKED",
            "research_classification": "RESEARCH_REJECTED",
            "reason": "base_research_rejected",
            "blockers": blockers + ["base_research_rejected"],
            "paper_eligible": False,
        }

    if oos_val_summary is None or oos_val_trades == 0:
        return {
            "oos_status": "NOT_RUN",
            "review_status": "REVIEW_BLOCKED",
            "research_classification": "RESEARCH_REJECTED",
            "reason": "oos_not_available",
            "blockers": blockers,
            "paper_eligible": False,
        }

    oos_net = oos_val_summary.get("net_pnl")
    oos_avg = oos_val_summary.get("average_net_r")
    oos_pf = oos_val_summary.get("profit_factor")
    metrics_pass = (
        oos_val_trades >= 30
        and oos_net is not None
        and float(oos_net) > 0
        and oos_avg is not None
        and float(oos_avg) > 0
        and oos_pf is not None
        and float(oos_pf) > 1
        and recon_ok
        and direct_replay
        and windows_ok
    )
    if metrics_pass and oos_consumed:
        return {
            "oos_status": "PASS_CONSUMED",
            "review_status": "REVIEW_BLOCKED",
            "research_classification": "EXPLORATORY_NEEDS_FRESH_HOLDOUT",
            "reason": "oos_window_consumed_after_post_observation_change",
            "blockers": blockers,
            "paper_eligible": False,
            "metrics_would_pass": True,
        }
    if metrics_pass:
        return {
            "oos_status": "PASS",
            "review_status": "REVIEW_READY_FOR_SEPARATE_APPROVAL",
            "research_classification": "PROMISING_NEEDS_HUMAN_REVIEW",
            "reason": None,
            "blockers": [],
            "paper_eligible": False,  # hard rule this phase
        }
    return {
        "oos_status": "FAIL",
        "review_status": "REVIEW_BLOCKED",
        "research_classification": "RESEARCH_REJECTED",
        "reason": "oos_validation_failed",
        "blockers": blockers
        + (
            ["oos_metrics"]
            if not (
                oos_net is not None
                and float(oos_net) > 0
                and oos_avg is not None
                and float(oos_avg) > 0
                and oos_pf is not None
                and float(oos_pf) > 1
            )
            else []
        ),
        "paper_eligible": False,
    }


def count_labels(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    return dict(Counter(str(r.get(key) or "UNKNOWN") for r in rows))


def windows_from_dict(d: Mapping[str, Any]) -> ResearchWindows:
    return ResearchWindows(
        requested_start=str(d["requested_start"]),
        requested_end=str(d["requested_end"]),
        base_start=str(d["base_start"]),
        base_end=str(d["base_end"]),
        oos_dev_start=str(d["oos_dev_start"]),
        oos_dev_end=str(d["oos_dev_end"]),
        oos_val_start=str(d["oos_val_start"]),
        oos_val_end=str(d["oos_val_end"]),
    )


def assert_disjoint(windows: ResearchWindows) -> dict[str, Any]:
    check = validate_research_windows(windows)
    split = validate_base_oos_split(
        base_start=windows.base_start,
        base_end=windows.base_end,
        oos_start=windows.oos_val_start,
        oos_end=windows.oos_val_end,
    )
    split_ok = bool(split.get("ok") if "ok" in split else split.get("valid"))
    return {
        "ok": bool(check.get("ok")) and split_ok,
        "window_status": check.get("window_status"),
        "errors": list(check.get("errors") or []) + list(split.get("errors") or []),
        "split": split,
    }
