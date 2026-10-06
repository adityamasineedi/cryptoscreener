"""Aggregation helpers for GRID_RANGE_RESEARCH reports."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def _rs(rows: Iterable[dict[str, Any]], key: str = "r_multiple") -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def bucket_metrics(rows: list[dict[str, Any]], *, r_key: str = "r_multiple") -> dict[str, Any]:
    rs = _rs(rows, r_key)
    wins = sum(1 for x in rs if x > 0)
    losses = sum(1 for x in rs if x < 0)
    gross = sum(x for x in rs if x > 0)
    loss_abs = abs(sum(x for x in rs if x < 0))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    fees = 0.0
    for r in rows:
        if r.get("fees") is not None:
            try:
                fees += float(r["fees"])
            except (TypeError, ValueError):
                pass
    invs = [int(r["maximum_inventory"]) for r in rows if r.get("maximum_inventory") is not None]
    exps = [
        float(r["maximum_exposure"])
        for r in rows
        if r.get("maximum_exposure") is not None
    ]
    return {
        "trades": len(rs),
        "winning_cycles": wins,
        "losing_cycles": losses,
        "win_rate": (wins / len(rs)) if rs else None,
        "total_r": sum(rs) if rs else 0.0,
        "avg_r": (sum(rs) / len(rs)) if rs else None,
        "profit_factor": (gross / loss_abs) if loss_abs > 0 else (None if gross == 0 else float("inf")),
        "max_dd_r": abs(max_dd),
        "fees": fees,
        "max_inventory": max(invs) if invs else 0,
        "max_exposure": max(exps) if exps else 0.0,
        "funding": "FUNDING_NOT_MODELED",
    }


def group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[str(r.get(key) or "UNKNOWN")].append(r)
    return dict(out)


def trend_escape_stats(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Sessions that saw range→breakout / regime kill after inventory."""
    breakouts = [
        s
        for s in sessions
        if s.get("breakout_seen")
        or str(s.get("reason_grid_stopped") or "").startswith("RANGE_BREAK")
        or str(s.get("reason_grid_stopped") or "").startswith("REGIME_KILL")
    ]
    # Focus on structural breakouts
    struct = [
        s
        for s in sessions
        if s.get("breakout_seen")
        or str(s.get("reason_grid_stopped") or "").startswith("RANGE_BREAK")
    ]
    losses = [float(s["R_result"]) for s in struct if s.get("R_result") is not None]
    neg = [x for x in losses if x < 0]
    invs = [int(s.get("inventory_at_breakout") or 0) for s in struct]
    kill_before_excess = sum(
        1
        for s in struct
        if int(s.get("inventory_at_breakout") or 0) <= int(s.get("maximum_inventory") or 0)
        and int(s.get("inventory_at_breakout") or 0) <= 3
    )
    return {
        "sessions_with_breakout": len(struct),
        "sessions_regime_or_breakout_end": len(breakouts),
        "max_inventory_at_breakout": max(invs) if invs else 0,
        "avg_inventory_at_breakout": (sum(invs) / len(invs)) if invs else None,
        "max_loss_r": min(losses) if losses else None,
        "avg_loss_r": (sum(neg) / len(neg)) if neg else None,
        "worst_grid_loss_r": min(losses) if losses else None,
        "mean_breakout_session_r": (sum(losses) / len(losses)) if losses else None,
        "kill_activated_with_inventory_cap_respected": kill_before_excess,
        "note": "Kill stops new entries; open inventory flattened at session end.",
    }
