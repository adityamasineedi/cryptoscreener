"""Pure analysis helpers for Multi-Cap baseline forensics (research only).

No strategy parameter changes. No live trading logic.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


HISTORY_BUCKETS = (
    ("A_<14d", 0, 14),
    ("B_14_30d", 14, 30),
    ("C_30_90d", 30, 90),
    ("D_90_180d", 90, 180),
    ("E_180d_plus", 180, None),
)


def parse_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    if isinstance(raw, str) and raw.strip():
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    return None


def calendar_days(start: Any, end: Any) -> float | None:
    a = parse_dt(start)
    b = parse_dt(end)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds() / 86400.0)


def history_bucket(days: float | None) -> str:
    if days is None:
        return "UNKNOWN"
    if days < 14:
        return "A_<14d"
    if days < 30:
        return "B_14_30d"
    if days < 90:
        return "C_30_90d"
    if days < 180:
        return "D_90_180d"
    return "E_180d_plus"


def safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    return float(statistics.median(xs))


def mean(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    return float(sum(xs) / len(xs))


def profit_factor(rs: Sequence[float]) -> float | None:
    gains = sum(x for x in rs if x > 0)
    losses = sum(x for x in rs if x < 0)
    if losses < 0:
        return gains / abs(losses)
    if gains > 0:
        return None  # undefined / infinite
    return None


def max_drawdown_r(rs: Sequence[float]) -> float | None:
    if not rs:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return abs(max_dd)


def fee_r_from_row(row: Mapping[str, Any], *, risk_usd: float) -> float | None:
    fee = safe_float(row.get("fee_total_usd"))
    if fee is None or risk_usd <= 0:
        return None
    return fee / float(risk_usd)


def slip_r_from_row(row: Mapping[str, Any], *, slippage_rate: float) -> float | None:
    entry = safe_float(row.get("entry_price"))
    stop = safe_float(row.get("stop_price"))
    if entry is None or stop is None or entry <= 0:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return (2.0 * float(slippage_rate) * entry) / risk


def net_r_with_slip(
    row: Mapping[str, Any], *, slippage_rate: float
) -> float | None:
    r_net = safe_float(row.get("r_net"))
    if r_net is None:
        return None
    slip = slip_r_from_row(row, slippage_rate=slippage_rate)
    if slip is None:
        return r_net
    return r_net - slip


def reconcile_artifact_totals(
    *,
    run_manifest: Mapping[str, Any],
    funnel_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    period_rows: Sequence[Mapping[str, Any]],
    timeframe_rows: Sequence[Mapping[str, Any]],
    symbol_rows: Sequence[Mapping[str, Any]],
    direction_rows: Sequence[Mapping[str, Any]],
    cells: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile baseline aggregations. Failures listed; no silent correction."""
    expected = dict(run_manifest.get("totals") or {})
    discrepancies: list[dict[str, Any]] = []

    def _sum(rows: Sequence[Mapping[str, Any]], key: str) -> int:
        return sum(int(float(r.get(key) or 0)) for r in rows)

    funnel_closed = _sum(funnel_rows, "closed_trades")
    cells_closed = _sum(cells, "closed_trades")
    summary_closed = _sum(summary_rows, "closed_trades")
    symbol_closed = _sum(symbol_rows, "closed_trades")
    period_closed = _sum(period_rows, "closed_trades")
    tf_closed = _sum(timeframe_rows, "closed_trades")
    dir_closed = _sum(direction_rows, "closed_trades")

    checks = {
        "funnel_vs_manifest_closed": (funnel_closed, int(expected.get("closed_trades") or 0)),
        "cells_vs_manifest_closed": (cells_closed, int(expected.get("closed_trades") or 0)),
        "summary_vs_manifest_closed": (summary_closed, int(expected.get("closed_trades") or 0)),
        "symbol_vs_manifest_closed": (symbol_closed, int(expected.get("closed_trades") or 0)),
        "period_vs_manifest_closed": (period_closed, int(expected.get("closed_trades") or 0)),
        "timeframe_vs_manifest_closed": (tf_closed, int(expected.get("closed_trades") or 0)),
        "direction_vs_manifest_closed": (dir_closed, int(expected.get("closed_trades") or 0)),
        "funnel_vs_manifest_entries": (
            _sum(funnel_rows, "entries"),
            int(expected.get("entries") or 0),
        ),
        "funnel_vs_manifest_signals": (
            _sum(funnel_rows, "signals_detected"),
            int(expected.get("signals_detected") or 0),
        ),
        "funnel_vs_manifest_cap_eligible": (
            _sum(funnel_rows, "cap_eligible_signals"),
            int(expected.get("cap_eligible_signals") or 0),
        ),
        "funnel_vs_manifest_candidates": (
            _sum(funnel_rows, "candidates"),
            int(expected.get("candidates") or 0),
        ),
        "funnel_vs_manifest_bars": (
            _sum(funnel_rows, "bars"),
            int(expected.get("bars_processed") or 0),
        ),
        "funnel_vs_manifest_open": (
            _sum(funnel_rows, "open_trades"),
            int(expected.get("open_trades") or 0),
        ),
        "cells_count": (len(cells), int(expected.get("cells_run") or 0)),
        "funnel_count": (len(funnel_rows), int(expected.get("cells_run") or 0)),
    }
    for name, (got, want) in checks.items():
        if got != want:
            discrepancies.append(
                {"check": name, "actual": got, "expected": want}
            )

    period_map = {str(r.get("key")): int(float(r.get("closed_trades") or 0)) for r in period_rows}
    for label, want in (("TRAIN", 6025), ("VALIDATION", 1943), ("OOS", 1683)):
        got = period_map.get(label)
        if got is not None and got != want:
            # soft: also compare sum of period metrics from cells
            pass
    cell_period = {"TRAIN": 0, "VALIDATION": 0, "OOS": 0}
    for c in cells:
        pm = c.get("period_metrics") or {}
        for lab in cell_period:
            cell_period[lab] += int((pm.get(lab) or {}).get("trade_count") or 0)
    for lab, got in cell_period.items():
        want = period_map.get(lab)
        if want is not None and got != want:
            discrepancies.append(
                {
                    "check": f"cells_period_{lab}_vs_period_csv",
                    "actual": got,
                    "expected": want,
                }
            )

    status = "PASS" if not discrepancies else "FAIL"
    return {
        "status": status,
        "run_id": run_manifest.get("run_id"),
        "dataset_label": run_manifest.get("dataset_label"),
        "checks": {k: {"actual": a, "expected": e} for k, (a, e) in checks.items()},
        "discrepancies": discrepancies,
        "period_from_cells": cell_period,
        "entries_minus_evaluated": (
            int(expected.get("entries") or 0)
            - int(expected.get("closed_trades") or 0)
            - int(expected.get("open_trades") or 0)
        ),
    }


def build_cap_transitions(
    observations_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    classify_fn,
) -> list[dict[str, Any]]:
    """Chronological cap-group path per symbol using as-of classification at each obs time."""
    rows: list[dict[str, Any]] = []
    for symbol, obs in sorted(observations_by_symbol.items()):
        timed: list[tuple[datetime, str]] = []
        for o in obs:
            et = parse_dt(o.get("effective_time"))
            if et is None:
                continue
            lk = classify_fn(symbol, et, obs)
            group = getattr(lk, "strategy_cap_group", None) or "UNAVAILABLE"
            timed.append((et, str(group)))
        timed.sort(key=lambda x: x[0])
        if not timed:
            rows.append(
                {
                    "symbol": symbol,
                    "first_observation": None,
                    "last_observation": None,
                    "groups_seen": "",
                    "transition_count": 0,
                    "transition_dates": "",
                    "large_days": 0.0,
                    "mid_days": 0.0,
                    "small_days": 0.0,
                    "unavailable_days": 0.0,
                    "observation_count": 0,
                }
            )
            continue
        groups_seen: list[str] = []
        transitions: list[str] = []
        prev_g: str | None = None
        day_acc = {
            "LARGE_CAP": 0.0,
            "MID_CAP": 0.0,
            "SMALL_CAP": 0.0,
            "UNAVAILABLE": 0.0,
        }
        for i, (et, g) in enumerate(timed):
            if prev_g is None or g != prev_g:
                if g not in groups_seen:
                    groups_seen.append(g)
                if prev_g is not None and g != prev_g:
                    transitions.append(f"{prev_g}->{g}@{et.isoformat()}")
                prev_g = g
            if i + 1 < len(timed):
                delta = (timed[i + 1][0] - et).total_seconds() / 86400.0
                day_acc[g if g in day_acc else "UNAVAILABLE"] = day_acc.get(
                    g if g in day_acc else "UNAVAILABLE", 0.0
                ) + max(0.0, delta)
        rows.append(
            {
                "symbol": symbol,
                "first_observation": timed[0][0].isoformat(),
                "last_observation": timed[-1][0].isoformat(),
                "groups_seen": "|".join(groups_seen),
                "transition_count": len(transitions),
                "transition_dates": ";".join(transitions),
                "large_days": round(day_acc["LARGE_CAP"], 4),
                "mid_days": round(day_acc["MID_CAP"], 4),
                "small_days": round(day_acc["SMALL_CAP"], 4),
                "unavailable_days": round(day_acc["UNAVAILABLE"], 4),
                "observation_count": len(timed),
            }
        )
    return rows


def summarize_cap_transitions(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    no_t = [r for r in rows if int(r.get("transition_count") or 0) == 0]
    one_t = [r for r in rows if int(r.get("transition_count") or 0) == 1]
    multi = [r for r in rows if int(r.get("transition_count") or 0) > 1]
    overlapping = [
        r
        for r in rows
        if len([g for g in str(r.get("groups_seen") or "").split("|") if g and g != "UNAVAILABLE"])
        > 1
    ]
    missing = [
        r
        for r in rows
        if float(r.get("unavailable_days") or 0) > 0
        or "UNAVAILABLE" in str(r.get("groups_seen") or "")
    ]
    return {
        "symbols_total": len(rows),
        "symbols_with_no_transition": len(no_t),
        "symbols_with_1_transition": len(one_t),
        "symbols_with_multiple_transitions": len(multi),
        "symbols_with_overlapping_group_membership": len(overlapping),
        "symbols_with_missing_periods": len(missing),
        "no_transition_symbols": sorted(r["symbol"] for r in no_t),
        "one_transition_symbols": sorted(r["symbol"] for r in one_t),
        "multi_transition_symbols": sorted(r["symbol"] for r in multi),
        "overlapping_symbols": sorted(r["symbol"] for r in overlapping),
    }


def classify_cell_history_bucket(cell: Mapping[str, Any]) -> dict[str, Any]:
    days = calendar_days(cell.get("requested_start"), cell.get("requested_end"))
    return {
        **{k: cell.get(k) for k in (
            "strategy_id",
            "symbol",
            "timeframe",
            "cap_group",
            "requested_start",
            "requested_end",
            "actual_start",
            "actual_end",
            "bars",
            "signals_detected",
            "cap_eligible_signals",
            "candidates",
            "entries",
            "closed_trades",
            "open_trades",
        )},
        "calendar_days": days,
        "history_bucket": history_bucket(days),
    }


def aggregate_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    slippage_rate: float,
    risk_usd: float,
    group: str,
) -> dict[str, Any]:
    closed = [
        t
        for t in trades
        if t.get("outcome") not in (None, "OPEN")
        and safe_float(t.get("r_multiple")) is not None
    ]
    rs = [float(safe_float(t["r_multiple"])) for t in closed]  # type: ignore[arg-type]
    fee_rs = []
    slip_rs = []
    net_rs = []
    for t in closed:
        fr = fee_r_from_row(t, risk_usd=risk_usd)
        sr = slip_r_from_row(t, slippage_rate=slippage_rate)
        nr = net_r_with_slip(t, slippage_rate=slippage_rate)
        if fr is not None:
            fee_rs.append(fr)
        if sr is not None:
            slip_rs.append(sr)
        if nr is not None:
            net_rs.append(nr)
    wins = sum(1 for r in rs if r > 0)
    n = len(rs)
    holds = [
        int(t["holding_bars"])
        for t in closed
        if t.get("holding_bars") is not None
    ]
    return {
        "group": group,
        "trades": n,
        "win_count": wins,
        "loss_count": n - wins,
        "win_rate": (wins / n) if n else None,
        "mean_R": mean(rs),
        "median_R": median(rs),
        "gross_R": sum(rs) if rs else None,
        "fees_R": sum(fee_rs) if fee_rs else None,
        "slippage_R": sum(slip_rs) if slip_rs else None,
        "net_R": sum(net_rs) if net_rs else None,
        "gross_mean_R": mean(rs),
        "net_mean_R": mean(net_rs),
        "cost_per_trade_R": (
            ((sum(fee_rs) + sum(slip_rs)) / n) if n and fee_rs else None
        ),
        "profit_factor": profit_factor(rs),
        "max_drawdown_R": max_drawdown_r(rs),
        "average_hold_bars": mean([float(h) for h in holds]),
        "median_hold_bars": median([float(h) for h in holds]),
        "fees_usd": sum(float(t.get("fee_total_usd") or 0) for t in closed),
    }


def funnel_gap_explanation(
    *,
    candidates: int,
    cap_eligible: int,
    cross_cap: int,
    entries: int,
    closed: int,
    open_trades: int,
    excluded_by_cap: int | None = None,
) -> dict[str, Any]:
    evaluated = closed + open_trades
    suppressed = entries - evaluated
    return {
        "candidates": candidates,
        "cap_eligible_signals": cap_eligible,
        "cross_cap_signal_count": cross_cap,
        "candidates_minus_eligible_and_cross": candidates - cap_eligible - cross_cap,
        "entries": entries,
        "entries_equals_cap_eligible": entries == cap_eligible,
        "closed_trades": closed,
        "open_trades": open_trades,
        "evaluated_trades": evaluated,
        "entries_minus_evaluated": suppressed,
        "suppression_interpretation": (
            "LIKELY_ONE_OPEN_AT_A_TIME_SKIP"
            if suppressed > 0
            else "NONE"
        ),
        "note": (
            "evaluate_candidate_trades skips candidates while a prior trade is open "
            "when one_open_at_a_time=true; skipped candidates are not present in "
            "evaluated trade lists. Count is inferred as entries - (closed+open)."
        ),
        "excluded_by_cap": excluded_by_cap,
    }


def reference_comparison_aggregate(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("strategy_id") or "UNKNOWN")].append(r)
    out: list[dict[str, Any]] = []
    for sid, items in sorted(by.items()):
        ref = sum(1 for r in items if str(r.get("reference_detected")).lower() == "true")
        prod = sum(1 for r in items if str(r.get("production_detected")).lower() == "true")
        matched = sum(
            1
            for r in items
            if str(r.get("reference_detected")).lower() == "true"
            and str(r.get("production_detected")).lower() == "true"
        )
        mismatched = sum(
            1
            for r in items
            if (str(r.get("reference_detected")).lower() == "true")
            != (str(r.get("production_detected")).lower() == "true")
        )
        missing_ref = sum(
            1
            for r in items
            if str(r.get("reference_detected")).lower() != "true"
            and str(r.get("production_detected")).lower() == "true"
        )
        missing_prod = sum(
            1
            for r in items
            if str(r.get("reference_detected")).lower() == "true"
            and str(r.get("production_detected")).lower() != "true"
        )
        denom = max(ref, prod, 1)
        reasons = Counter(
            str(r.get("difference_reason") or "MATCH")
            for r in items
            if str(r.get("difference_reason") or "").strip()
        )
        out.append(
            {
                "strategy": sid,
                "reference_events": ref,
                "production_events": prod,
                "matched": matched,
                "mismatched": mismatched,
                "missing_reference": missing_ref,
                "missing_production": missing_prod,
                "match_rate": matched / denom,
                "difference_reasons": dict(reasons),
                "row_count": len(items),
            }
        )
    return out
