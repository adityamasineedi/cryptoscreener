"""Provisional research gates and walk-forward windows.

Gate thresholds were copied from the task's provisional policy and frozen in
``spec.py`` before this module evaluates any strategy return.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.intraday_bos_sd.spec import (
    EMBARGO_HOURS,
    GATE_POLICY_ID,
    HOLDOUT_FRACTION,
    HOLDOUT_MIN_DAYS,
    WALK_FORWARD_FOLDS,
    spec_hash,
)


def split_windows(start: datetime, end: datetime) -> dict:
    """Chronological dev folds plus one final holdout. Dates come from coverage."""
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    span = end - start
    holdout_len = max(timedelta(days=HOLDOUT_MIN_DAYS), span * HOLDOUT_FRACTION)
    if holdout_len >= span:
        return {
            "ok": False,
            "reason": "coverage_shorter_than_holdout",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    holdout_start = end - holdout_len
    dev_span = holdout_start - start
    fold_len = dev_span / WALK_FORWARD_FOLDS
    embargo = timedelta(hours=EMBARGO_HOURS)
    folds = []
    for i in range(WALK_FORWARD_FOLDS):
        test_start = start + fold_len * i
        test_end = start + fold_len * (i + 1)
        folds.append(
            {
                "name": f"fold_{i + 1}",
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "purge_before": (test_start - embargo).isoformat(),
                "embargo_after": (test_end + embargo).isoformat(),
            }
        )
    return {
        "ok": True,
        "policy": GATE_POLICY_ID,
        "spec_hash": spec_hash(),
        "folds": folds,
        "holdout": {
            "start": holdout_start.isoformat(),
            "end": end.isoformat(),
            "status": "UNTOUCHED",
        },
    }


def trade_in_window(entry_time: datetime, exit_time: datetime, start: datetime, end: datetime) -> bool:
    """Drop trades whose life overlaps the embargo at a boundary.

    A trade counts only when entry is inside [start, end) and exit is <= end.
    """
    return start <= entry_time < end and exit_time <= end


def summarize(trades: list[dict], *, initial_equity: float) -> dict:
    pnls = [float(t["net_pnl"]) for t in trades]
    rs = [float(t["r_multiple"]) for t in trades]
    n = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = initial_equity
    peak = equity
    max_dd = 0.0
    exposure_hours = 0.0
    for t in trades:
        equity += float(t["net_pnl"])
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
        exposure_hours += float(t.get("hold_hours") or 0.0)
    fees = sum(float(t.get("fees") or 0.0) for t in trades)
    funding = sum(float(t.get("funding_pnl") or 0.0) for t in trades)
    return {
        "trades": n,
        "net_pnl": sum(pnls),
        "expectancy_r": (sum(rs) / n) if n else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf")),
        "max_drawdown": max_dd,
        "win_rate": (len(wins) / n) if n else None,
        "average_win": (sum(wins) / len(wins)) if wins else None,
        "average_loss": (sum(losses) / len(losses)) if losses else None,
        "exposure_hours": exposure_hours,
        "fees": fees,
        "funding_pnl": funding,
        "ending_equity": equity,
    }


def evaluate_gates(
    *,
    correctness_ok: bool,
    oos_summary: dict,
    trades_by_tier: dict[str, int],
    enabled_tiers: list[str],
    fold_pnls: list[float],
    stress_expectancy_r: float | None,
    pnl_without_best_symbol: float | None,
) -> dict:
    checks = []

    def add(name: str, ok: bool, detail) -> None:
        checks.append({"gate": name, "passed": bool(ok), "detail": detail})

    add("correctness_causality_data_quality", correctness_ok, correctness_ok)
    n = int(oos_summary.get("trades") or 0)
    add("oos_trades_at_least_200", n >= 200, n)
    tier_ok = True
    tier_detail = {}
    for tier in enabled_tiers:
        count = int(trades_by_tier.get(tier) or 0)
        tier_detail[tier] = count
        if count < 50:
            tier_ok = False
    add("oos_trades_per_enabled_tier_at_least_50", tier_ok, tier_detail)
    exp = oos_summary.get("expectancy_r")
    add("positive_net_expectancy_r", exp is not None and exp > 0, exp)
    pf = oos_summary.get("profit_factor")
    add("profit_factor_at_least_1_20", pf is not None and pf >= 1.20, pf)
    dd = oos_summary.get("max_drawdown")
    add("max_drawdown_at_most_10pct", dd is not None and dd <= 0.10, dd)
    usable_folds = [p for p in fold_pnls if p is not None]
    fold_ok = (
        len(usable_folds) >= 3
        and sum(1 for p in usable_folds if p > 0) >= (2 / 3) * len(usable_folds)
    )
    add("positive_pnl_in_two_thirds_of_folds", fold_ok, fold_pnls)
    add(
        "positive_expectancy_under_doubled_slippage",
        stress_expectancy_r is not None and stress_expectancy_r > 0,
        stress_expectancy_r,
    )
    add(
        "positive_pnl_excluding_best_symbol",
        pnl_without_best_symbol is not None and pnl_without_best_symbol > 0,
        pnl_without_best_symbol,
    )
    failed = [c for c in checks if not c["passed"]]
    if not correctness_ok:
        status = "BACKTEST_FAILED"
    elif n < 200 or not tier_ok:
        status = "INSUFFICIENT_EVIDENCE"
    elif failed:
        status = "BACKTEST_FAILED"
    else:
        status = "VALIDATED"
    return {
        "policy": GATE_POLICY_ID,
        "status": status,
        "passed": status == "VALIDATED",
        "passed_means": "acceptance_policy_met_not_guaranteed_profit",
        "checks": checks,
    }
