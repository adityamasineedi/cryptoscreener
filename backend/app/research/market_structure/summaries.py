"""Aggregate regime / opportunity / MTF summaries from bar analytics."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _month_year(ts: str | None) -> tuple[str | None, str | None]:
    if not ts:
        return None, None
    try:
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return f"{dt.year:04d}-{dt.month:02d}", str(dt.year)
    except ValueError:
        return None, None


def _group_metrics(
    rows: list[Mapping[str, Any]], *, regime: str, timeframe: str
) -> dict[str, Any]:
    rs = [_f(r.get("r_multiple")) for r in rows]
    rs_ok = [x for x in rs if x is not None]
    wins = sum(1 for x in rs_ok if x > 0)
    losses = sum(1 for x in rs_ok if x < 0)
    gross = sum(x for x in rs_ok if x > 0)
    loss_abs = abs(sum(x for x in rs_ok if x < 0))
    pf = (gross / loss_abs) if loss_abs > 0 else (None if not rs_ok else None)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs_ok:
        equity += x
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    holds = [_f(r.get("holding_bars")) for r in rows]
    holds_ok = [h for h in holds if h is not None]
    return {
        "regime": regime,
        "timeframe": timeframe,
        "total_trades": len(rows),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(rs_ok)) if rs_ok else None,
        "sum_R": sum(rs_ok) if rs_ok else 0.0,
        "average_R": (sum(rs_ok) / len(rs_ok)) if rs_ok else None,
        "median_R": statistics.median(rs_ok) if rs_ok else None,
        "profit_factor": pf,
        "gross_pnl": None,
        "fees": None,
        "net_pnl": None,
        "max_drawdown": abs(max_dd) if rs_ok else None,
        "average_holding_time": (sum(holds_ok) / len(holds_ok)) if holds_ok else None,
    }


def build_trade_regime_summary(
    by_bar: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    # Authoritative: one row per unique executed trade (not raw entry-marked bars).
    accepted = [
        r for r in by_bar if r.get("entry_attribution_type") == "EXECUTION_BAR"
    ]
    by_id: dict[Any, Mapping[str, Any]] = {}
    for t in trades:
        key = t.get("trade_no") or t.get("trade_id") or t.get("entry_index")
        if key is not None:
            by_id[key] = t
    enriched: list[dict[str, Any]] = []
    for r in accepted:
        row = dict(r)
        tid = r.get("trade_id")
        t = by_id.get(tid) if tid is not None else None
        if t is None and tid is not None:
            # try int key
            try:
                t = by_id.get(int(tid))
            except (TypeError, ValueError):
                t = None
        if t:
            row["holding_bars"] = t.get("holding_bars")
            row["gross_pnl_usd"] = t.get("gross_pnl_usd")
            row["fee_total_usd"] = t.get("fee_total_usd")
            row["net_pnl_usd"] = t.get("net_pnl_usd")
            if row.get("r_multiple") is None:
                row["r_multiple"] = (
                    t.get("r_net") if t.get("r_net") is not None else t.get("r_multiple")
                )
        month, year = _month_year(str(r.get("decision_time") or r.get("entry_time") or ""))
        row["month"] = month
        row["year"] = year
        enriched.append(row)

    summaries: list[dict[str, Any]] = []

    def add_groups(key: str, timeframe_label: str) -> None:
        buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for r in enriched:
            buckets[str(r.get(key) or "UNKNOWN")].append(r)
        for regime, rows in sorted(buckets.items()):
            m = _group_metrics(list(rows), regime=regime, timeframe=timeframe_label)
            gross = [_f(x.get("gross_pnl_usd")) for x in rows]
            fees = [_f(x.get("fee_total_usd")) for x in rows]
            nets = [_f(x.get("net_pnl_usd")) for x in rows]
            g_ok = [x for x in gross if x is not None]
            f_ok = [x for x in fees if x is not None]
            n_ok = [x for x in nets if x is not None]
            m["gross_pnl"] = sum(g_ok) if g_ok else None
            m["fees"] = sum(f_ok) if f_ok else None
            m["net_pnl"] = sum(n_ok) if n_ok else None
            m["group_key"] = key
            summaries.append(m)

    add_groups("regime_4h", "4h")
    add_groups("regime_1h", "1h")
    add_groups("regime_15m", "15m")
    add_groups("mtf_alignment", "mtf")
    add_groups("volatility_state", "volatility")
    add_groups("bos_1h", "bos_1h")
    add_groups("win_loss", "outcome")
    add_groups("signal_stage", "signal_stage")

    buckets_t: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in enriched:
        buckets_t[
            "TRANSITION" if r.get("trend_1h") == "TRANSITION" else "NON_TRANSITION"
        ].append(r)
    for regime, rows in buckets_t.items():
        m = _group_metrics(list(rows), regime=regime, timeframe="transition")
        m["group_key"] = "transition_state"
        summaries.append(m)

    buckets_m: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    buckets_y: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in enriched:
        if r.get("month"):
            buckets_m[str(r["month"])].append(r)
        if r.get("year"):
            buckets_y[str(r["year"])].append(r)
    for regime, rows in sorted(buckets_m.items()):
        m = _group_metrics(list(rows), regime=regime, timeframe="month")
        m["group_key"] = "month"
        summaries.append(m)
    for regime, rows in sorted(buckets_y.items()):
        m = _group_metrics(list(rows), regime=regime, timeframe="year")
        m["group_key"] = "year"
        summaries.append(m)

    return summaries


def build_opportunity_summary(by_bar: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in by_bar:
        buckets[str(r.get("regime_1h") or "UNKNOWN")].append(r)
    out: list[dict[str, Any]] = []
    for regime, rows in sorted(buckets.items()):
        accepted = sum(
            1 for r in rows if r.get("entry_attribution_type") == "EXECUTION_BAR"
        )
        rejected = len(rows) - accepted
        executed = accepted
        n = len(rows)
        out.append(
            {
                "regime": regime,
                "bars_in_regime": n,
                "accepted_signals": accepted,
                "rejected_signals": rejected,
                "trades_executed": executed,
                "signal_rate": (accepted / n) if n else 0.0,
                "trade_rate": (executed / n) if n else 0.0,
            }
        )
    return out


def build_mtf_alignment_summary(
    by_bar: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in by_bar:
        buckets[str(r.get("mtf_alignment") or "UNKNOWN")].append(r)
    out: list[dict[str, Any]] = []
    for label, rows in sorted(buckets.items()):
        accepted = [
            r for r in rows if r.get("entry_attribution_type") == "EXECUTION_BAR"
        ]
        rs = [_f(r.get("r_multiple")) for r in accepted]
        rs_ok = [x for x in rs if x is not None]
        out.append(
            {
                "mtf_alignment": label,
                "bars": len(rows),
                "accepted_signals": len(accepted),
                "avg_mtf_score": (
                    sum(float(r.get("mtf_score") or 0) for r in rows) / len(rows)
                    if rows
                    else 0.0
                ),
                "avg_R_accepted": (sum(rs_ok) / len(rs_ok)) if rs_ok else None,
                "win_rate_accepted": (
                    sum(1 for x in rs_ok if x > 0) / len(rs_ok) if rs_ok else None
                ),
            }
        )
    return out
