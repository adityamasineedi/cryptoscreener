"""COMBO_02 candidate eligibility — research labels only.

Does not alter v1_production.py, paper watcher universe, or Telegram eligibility.
Any promotion requires a separate v2 workflow and explicit operator confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.research.combo02_candidate_thresholds import (
    DEFAULT_THRESHOLDS,
    EligibilityThresholds,
    PORTFOLIO_HIGH_CORR,
    PORTFOLIO_HIGH_ENTRY_OVERLAP_PCT,
)

Tier = Literal["PROMISING", "WATCHLIST", "REJECT", "INSUFFICIENT_DATA"]
OosLabel = Literal[
    "V2_PAPER_CANDIDATE",
    "PROMISING_NEEDS_MORE_EVIDENCE",
    "INSUFFICIENT_OOS_DATA",
    "OOS_FAIL",
    "NOT_APPLICABLE",
]
RunStatus = Literal[
    "COMPLETED",
    "INSUFFICIENT_OHLCV",
    "DATA_GAP",
    "ENGINE_ERROR",
    "NO_TRADES",
]


def max_losing_streak(rs: list[float]) -> int:
    best = cur = 0
    for r in rs:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_winning_streak(rs: list[float]) -> int:
    best = cur = 0
    for r in rs:
        if r > 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_drawdown_r(rs: list[float]) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def fee_pct_of_gross(gross_pnl: float | None, fees: float | None) -> float | None:
    if gross_pnl is None:
        return None
    g = abs(float(gross_pnl))
    f = float(fees or 0)
    if g <= 1e-12:
        return None if f <= 0 else 1.0
    return f / g


def _rule(
    rule: str,
    actual: Any,
    required: str,
    passed: bool,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    """Machine-readable eligibility reason (PASS/FAIL per rule)."""
    row = {
        "rule": rule,
        "actual": actual,
        "required": required,
        "passed": bool(passed),
        # Backward-compatible aliases used by older tests/UI.
        "name": rule,
        "detail": detail
        if detail is not None
        else f"{rule}: actual={actual} required={required}",
    }
    return row


def evaluate_reject_conditions(
    *,
    trade_count: int,
    net_avg_r: float | None,
    net_pnl: float | None,
    profit_factor: float | None,
    max_dd_r: float | None,
    max_lose_streak: int | None,
    fee_share: float | None,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    """Floor gates — failing any of these → RESEARCH_REJECTED."""
    n = int(trade_count or 0)
    net_e = float(net_avg_r) if net_avg_r is not None else 0.0
    net_p = float(net_pnl) if net_pnl is not None else 0.0
    pf = float(profit_factor) if profit_factor is not None else 0.0
    dd = float(max_dd_r) if max_dd_r is not None else 0.0
    streak = int(max_lose_streak or 0)
    fees = float(fee_share) if fee_share is not None else 0.0
    fee_ok = fee_share is None or fees < thresholds.reject_fee_pct_of_gross
    return [
        _rule("trades_ge_10", n, f">= {thresholds.insufficient_max_trades + 1}", n > thresholds.insufficient_max_trades),
        _rule("net_avg_r_gt_0", round(net_e, 4), "> 0", net_e > 0),
        _rule("net_pnl_gt_0", round(net_p, 4), "> 0", net_p > 0),
        _rule("profit_factor_gt_1", round(pf, 4), "> 1", pf > 1.0),
        _rule(
            "max_drawdown_le_10R",
            round(dd, 4),
            f"<= {thresholds.reject_max_dd_r}",
            dd <= thresholds.reject_max_dd_r,
        ),
        _rule(
            "max_losing_streak_le_10",
            streak,
            f"<= {thresholds.reject_max_losing_streak}",
            streak <= thresholds.reject_max_losing_streak,
        ),
        _rule(
            "fees_lt_70pct_gross",
            None if fee_share is None else round(fees, 4),
            f"< {thresholds.reject_fee_pct_of_gross}",
            fee_ok,
        ),
    ]


def evaluate_promising_conditions(
    *,
    trade_count: int,
    net_avg_r: float | None,
    net_pnl: float | None,
    profit_factor: float | None,
    max_dd_r: float | None,
    max_lose_streak: int | None,
    fee_share: float | None,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> list[dict[str, Any]]:
    n = int(trade_count or 0)
    net_e = float(net_avg_r) if net_avg_r is not None else 0.0
    net_p = float(net_pnl) if net_pnl is not None else 0.0
    pf = float(profit_factor) if profit_factor is not None else 0.0
    dd = float(max_dd_r) if max_dd_r is not None else 0.0
    streak = int(max_lose_streak or 0)
    fees = float(fee_share) if fee_share is not None else 0.0
    fee_ok = fee_share is None or fees < thresholds.promising_max_fee_pct_of_gross
    return [
        _rule(
            "trades_ge_20",
            n,
            f">= {thresholds.promising_min_trades}",
            n >= thresholds.promising_min_trades,
        ),
        _rule(
            "net_avg_r_gt_0_25",
            round(net_e, 4),
            f"> {thresholds.promising_min_net_avg_r}",
            net_e > thresholds.promising_min_net_avg_r,
        ),
        _rule("net_pnl_gt_0", round(net_p, 4), "> 0", net_p > 0),
        _rule(
            "profit_factor_gt_1_25",
            round(pf, 4),
            f"> {thresholds.promising_min_profit_factor}",
            pf > thresholds.promising_min_profit_factor,
        ),
        _rule(
            "max_dd_le_6r",
            round(dd, 4),
            f"<= {thresholds.promising_max_dd_r}",
            dd <= thresholds.promising_max_dd_r,
        ),
        _rule(
            "max_losing_streak_le_6",
            streak,
            f"<= {thresholds.promising_max_losing_streak}",
            streak <= thresholds.promising_max_losing_streak,
        ),
        _rule(
            "fees_lt_70pct_gross",
            None if fee_share is None else round(fees, 4),
            f"< {thresholds.promising_max_fee_pct_of_gross}",
            fee_ok,
        ),
    ]


def classify_eligibility(
    *,
    trade_count: int,
    net_avg_r: float | None,
    net_pnl: float | None,
    profit_factor: float | None,
    max_dd_r: float | None,
    max_lose_streak: int | None,
    fee_share: float | None,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> tuple[Tier, list[str], list[dict[str, Any]]]:
    """Return (tier, reasons, condition_rows). Research labels only.

    ``condition_rows`` are machine-readable ``{rule, actual, required, passed}``
    objects. On REJECT the matrix uses reject-floor rules; otherwise promising.
    """
    n = int(trade_count or 0)
    promising = evaluate_promising_conditions(
        trade_count=trade_count,
        net_avg_r=net_avg_r,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        max_dd_r=max_dd_r,
        max_lose_streak=max_lose_streak,
        fee_share=fee_share,
        thresholds=thresholds,
    )
    reject_conds = evaluate_reject_conditions(
        trade_count=trade_count,
        net_avg_r=net_avg_r,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        max_dd_r=max_dd_r,
        max_lose_streak=max_lose_streak,
        fee_share=fee_share,
        thresholds=thresholds,
    )
    if n <= thresholds.insufficient_max_trades:
        return "INSUFFICIENT_DATA", [f"trades={n} < 10"], reject_conds

    net_e = float(net_avg_r) if net_avg_r is not None else 0.0
    net_p = float(net_pnl) if net_pnl is not None else 0.0
    pf = float(profit_factor) if profit_factor is not None else 0.0
    dd = float(max_dd_r) if max_dd_r is not None else 0.0
    streak = int(max_lose_streak or 0)
    fees = float(fee_share) if fee_share is not None else 0.0

    reasons: list[str] = []
    if net_e <= 0 or net_p <= 0:
        reasons.append("non_positive_net_expectancy_or_pnl")
    if pf <= 1.0:
        reasons.append("profit_factor_le_1")
    if dd > thresholds.reject_max_dd_r:
        reasons.append(f"max_dd={dd:.2f}R > {thresholds.reject_max_dd_r}R")
    if streak > thresholds.reject_max_losing_streak:
        reasons.append(f"lose_streak={streak} > {thresholds.reject_max_losing_streak}")
    if fee_share is not None and fees >= thresholds.reject_fee_pct_of_gross:
        reasons.append(f"fees_consume>={thresholds.reject_fee_pct_of_gross:.0%}_gross")
    if reasons:
        return "REJECT", reasons, reject_conds

    if all(c["passed"] for c in promising):
        return "PROMISING", ["meets_promising_thresholds"], promising

    if n >= thresholds.watchlist_min_trades and net_e > 0 and net_p > 0:
        fail = [c["rule"] for c in promising if not c["passed"]]
        return "WATCHLIST", fail or ["positive_but_not_promising"], promising

    return "REJECT", ["failed_watchlist_floor"], reject_conds


def eligibility_report(
    *,
    trade_count: int,
    net_avg_r: float | None,
    net_pnl: float | None,
    profit_factor: float | None,
    max_dd_r: float | None,
    max_lose_streak: int | None,
    fee_share: float | None,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Operator-facing structured eligibility (persisted for UI)."""
    tier, reason_codes, reasons = classify_eligibility(
        trade_count=trade_count,
        net_avg_r=net_avg_r,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        max_dd_r=max_dd_r,
        max_lose_streak=max_lose_streak,
        fee_share=fee_share,
        thresholds=thresholds,
    )
    display = "RESEARCH_REJECTED" if tier == "REJECT" else tier
    return {
        "tier": display,
        "passed": tier == "PROMISING",
        "reasons": reasons,
        "reason_codes": reason_codes,
    }


def classify_oos(
    *,
    base_tier: Tier,
    oos_trade_count: int | None,
    oos_net_avg_r: float | None,
    oos_net_pnl: float | None,
    oos_profit_factor: float | None,
    oos_max_dd_r: float | None,
    oos_max_lose_streak: int | None,
    oos_usable: bool,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> tuple[OosLabel, list[str], list[dict[str, Any]]]:
    """OOS label for PROMISING candidates. Never writes to v1."""
    if base_tier != "PROMISING":
        return "NOT_APPLICABLE", ["base_tier_not_promising"], []

    if not oos_usable:
        return (
            "INSUFFICIENT_OOS_DATA",
            ["earlier_data_unavailable_or_unusable"],
            [],
        )

    n = int(oos_trade_count or 0)
    net_e = float(oos_net_avg_r) if oos_net_avg_r is not None else 0.0
    net_p = float(oos_net_pnl) if oos_net_pnl is not None else 0.0
    pf = float(oos_profit_factor) if oos_profit_factor is not None else 0.0
    dd = float(oos_max_dd_r) if oos_max_dd_r is not None else 0.0
    streak = int(oos_max_lose_streak or 0)

    conditions = [
        _rule(
            "oos_trades_ge_10",
            n,
            f">= {thresholds.oos_min_trades}",
            n >= thresholds.oos_min_trades,
        ),
        _rule(
            "oos_net_avg_r_gt_0",
            round(net_e, 4),
            f"> {thresholds.oos_min_net_avg_r}",
            net_e > thresholds.oos_min_net_avg_r,
        ),
        _rule("oos_net_pnl_gt_0", round(net_p, 4), "> 0", net_p > 0),
        _rule(
            "oos_pf_gt_1",
            round(pf, 4),
            f"> {thresholds.oos_min_profit_factor}",
            pf > thresholds.oos_min_profit_factor,
        ),
        _rule(
            "oos_no_severe_dd",
            round(dd, 4),
            f"<= {thresholds.oos_severe_max_dd_r}",
            dd <= thresholds.oos_severe_max_dd_r,
        ),
        _rule(
            "oos_no_severe_streak",
            streak,
            f"<= {thresholds.oos_severe_max_losing_streak}",
            streak <= thresholds.oos_severe_max_losing_streak,
        ),
    ]
    fails = [c["rule"] for c in conditions if not c["passed"]]
    if n < thresholds.oos_min_trades:
        return "INSUFFICIENT_OOS_DATA", fails, conditions
    if fails:
        return "PROMISING_NEEDS_MORE_EVIDENCE", fails, conditions
    return "V2_PAPER_CANDIDATE", ["oos_gates_passed"], conditions


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class TradeInterval:
    symbol: str
    entry: datetime
    exit: datetime
    r_net: float


def trade_intervals_from_rows(symbol: str, trades: list[dict[str, Any]]) -> list[TradeInterval]:
    out: list[TradeInterval] = []
    for t in trades:
        entry = _parse_ts(t.get("entry_time") or t.get("signal_time"))
        exit_ = _parse_ts(t.get("exit_time"))
        if entry is None or exit_ is None:
            continue
        if exit_ < entry:
            continue
        try:
            rn = float(t.get("r_net") if t.get("r_net") is not None else t.get("r_multiple") or 0)
        except (TypeError, ValueError):
            rn = 0.0
        out.append(TradeInterval(symbol=symbol, entry=entry, exit=exit_, r_net=rn))
    return out


def entry_overlap_pct(
    candidate_entries: list[datetime],
    reference_entries: list[datetime],
    *,
    tolerance_hours: float = 1.0,
) -> float | None:
    """Fraction of candidate entries that coincide with a reference entry (±tolerance)."""
    if not candidate_entries:
        return None
    if not reference_entries:
        return 0.0
    tol = tolerance_hours * 3600.0
    hits = 0
    for c in candidate_entries:
        for r in reference_entries:
            if abs((c - r).total_seconds()) <= tol:
                hits += 1
                break
    return hits / len(candidate_entries)


def peak_concurrent_positions(intervals: list[TradeInterval]) -> int:
    if not intervals:
        return 0
    events: list[tuple[datetime, int]] = []
    for iv in intervals:
        events.append((iv.entry, 1))
        events.append((iv.exit, -1))
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def daily_net_r_series(intervals: list[TradeInterval]) -> dict[str, float]:
    """Attribute each trade's net R to its exit UTC date."""
    out: dict[str, float] = {}
    for iv in intervals:
        key = iv.exit.astimezone(timezone.utc).strftime("%Y-%m-%d")
        out[key] = out.get(key, 0.0) + iv.r_net
    return out


def weekly_net_r_series(intervals: list[TradeInterval]) -> dict[str, float]:
    out: dict[str, float] = {}
    for iv in intervals:
        d = iv.exit.astimezone(timezone.utc).date()
        iso = d.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        out[key] = out.get(key, 0.0) + iv.r_net
    return out


def aligned_corr(a: dict[str, float], b: dict[str, float]) -> float | None:
    keys = sorted(set(a) & set(b))
    if len(keys) < 3:
        return None
    xa = [a[k] for k in keys]
    xb = [b[k] for k in keys]
    ma = sum(xa) / len(xa)
    mb = sum(xb) / len(xb)
    num = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    da = sum((x - ma) ** 2 for x in xa) ** 0.5
    db = sum((y - mb) ** 2 for y in xb) ** 0.5
    if da <= 1e-12 or db <= 1e-12:
        return None
    return num / (da * db)


def incremental_portfolio_stats(
    candidate: list[TradeInterval],
    v1_book: list[TradeInterval],
) -> dict[str, Any]:
    """Combined candidate+v1 book stats vs v1 alone (trade-order by exit)."""
    v1_rs = [t.r_net for t in sorted(v1_book, key=lambda x: x.exit)]
    combined = sorted(v1_book + candidate, key=lambda x: x.exit)
    comb_rs = [t.r_net for t in combined]
    v1_dd = max_drawdown_r(v1_rs) if v1_rs else 0.0
    comb_dd = max_drawdown_r(comb_rs) if comb_rs else 0.0
    v1_net = sum(v1_rs)
    comb_net = sum(comb_rs)
    v1_streak = max_losing_streak(v1_rs)
    comb_streak = max_losing_streak(comb_rs)
    v1_ret_dd = (v1_net / v1_dd) if v1_dd > 1e-12 else None
    comb_ret_dd = (comb_net / comb_dd) if comb_dd > 1e-12 else None
    improves = None
    if v1_ret_dd is not None and comb_ret_dd is not None:
        improves = comb_ret_dd > v1_ret_dd
    return {
        "v1_net_r": round(v1_net, 4),
        "combined_net_r": round(comb_net, 4),
        "incremental_net_r": round(comb_net - v1_net, 4),
        "v1_max_dd_r": round(v1_dd, 4),
        "combined_max_dd_r": round(comb_dd, 4),
        "incremental_max_dd_r": round(comb_dd - v1_dd, 4),
        "v1_max_losing_streak": v1_streak,
        "combined_max_losing_streak": comb_streak,
        "v1_return_per_dd": None if v1_ret_dd is None else round(v1_ret_dd, 4),
        "combined_return_per_dd": None if comb_ret_dd is None else round(comb_ret_dd, 4),
        "improves_return_per_dd": improves,
        "peak_concurrent_with_v1": peak_concurrent_positions(combined),
    }


def portfolio_recommendation(
    *,
    symbol: str,
    base_tier: Tier,
    oos_label: OosLabel,
    overlap_btc: float | None,
    overlap_eth: float | None,
    overlap_sol: float | None,
    corr_daily_btc: float | None,
    incremental: dict[str, Any],
) -> dict[str, Any]:
    """Research-only correlation / concurrency guidance."""
    overlaps = {
        "BTCUSDT": overlap_btc,
        "ETHUSDT": overlap_eth,
        "SOLUSDT": overlap_sol,
    }
    high_overlap = {
        k: v
        for k, v in overlaps.items()
        if v is not None and v >= PORTFOLIO_HIGH_ENTRY_OVERLAP_PCT
    }
    correlated = bool(high_overlap) or (
        corr_daily_btc is not None and corr_daily_btc >= PORTFOLIO_HIGH_CORR
    )
    peak = int(incremental.get("peak_concurrent_with_v1") or 0)
    inc_dd = incremental.get("incremental_max_dd_r")
    if base_tier in ("REJECT", "INSUFFICIENT_DATA"):
        rec = "SKIP"
        note = "Base results do not support further portfolio consideration."
    elif correlated:
        rec = "CORRELATED"
        note = (
            "Do not add without a portfolio exposure cap — "
            "entries largely duplicate BTC/ETH/SOL risk."
        )
    elif oos_label == "V2_PAPER_CANDIDATE":
        rec = "V2_PAPER_ONLY"
        note = "Research label only — requires separate v2 workflow before any paper trial."
    elif base_tier == "PROMISING":
        rec = "NEEDS_MORE_EVIDENCE"
        note = "Promising in-sample; OOS/portfolio evidence incomplete."
    else:
        rec = "WATCH"
        note = "Positive but below promising thresholds."

    return {
        "symbol": symbol,
        "base_tier": base_tier,
        "oos_label": oos_label,
        "btc_overlap_pct": overlap_btc,
        "eth_overlap_pct": overlap_eth,
        "sol_overlap_pct": overlap_sol,
        "high_overlap_refs": high_overlap,
        "corr_daily_vs_btc": corr_daily_btc,
        "peak_concurrent_positions": peak,
        "incremental_portfolio_dd_r": inc_dd,
        "recommendation": rec,
        "recommendation_note": note,
    }
