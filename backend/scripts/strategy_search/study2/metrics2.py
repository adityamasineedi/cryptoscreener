"""Metrics for sim2 results (study 2). Dollars are raw for the simulated account; *_10k fields are per $10,000
(account net x 10,000 / account base)."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from common2 import FOLDS

UTC = timezone.utc


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)


def metrics(res: dict, folds: bool = True) -> dict:
    tr = res["trades"]
    days = (res["end"] - res["start"]) / 86400
    scale = 10000.0 / res["base"]
    net = sum(t["net"] for t in tr)
    wins = [t["net"] for t in tr if t["net"] > 0]
    losses = [t["net"] for t in tr if t["net"] <= 0]
    gl = -sum(losses)
    rs = [t["r"] for t in tr]
    streak = worst = 0
    for t in sorted(tr, key=lambda x: x["exit_t"]):
        streak = streak + 1 if t["net"] <= 0 else 0
        worst = max(worst, streak)
    per_coin: dict = defaultdict(float)
    for t in tr:
        per_coin[t["coin"]] += t["net"]
    months = {k: m["net"] for k, m in res["months"].items()}
    pos_coin = sum(v for v in per_coin.values() if v > 0)
    pos_month = sum(v for v in months.values() if v > 0)
    # daily P&L from the cash-flow-adjusted curve at 00:00 UTC
    daily = []
    prev = None
    for t, eq, cf in res["curve"]:
        if t % 86400 == 0:
            if prev is not None:
                daily.append(cf - prev)
            prev = cf
    full = {}
    for k in months:
        y, mo = map(int, k.split("-"))
        full[k] = res["start"] <= int(datetime(y, mo, 1, tzinfo=UTC).timestamp()) and res["end"] >= _next_month(y, mo)
    full_vals = [v * scale for k, v in months.items() if full[k]]
    k5 = sum(1 for v in full_vals if v >= 500)
    k10 = sum(1 for v in full_vals if v >= 1000)
    m = {
        "trades": len(tr), "longs": sum(1 for t in tr if t["side"] == "LONG"),
        "shorts": sum(1 for t in tr if t["side"] == "SHORT"),
        "net": round(net, 2), "gross": round(sum(t["gross"] for t in tr), 2), "fees": round(sum(t["fees"] for t in tr), 2),
        "exec": round(sum(t["exec"] for t in tr), 2), "funding_hyp": round(sum(t["funding"] for t in tr), 2),
        "net_long": round(sum(t["net"] for t in tr if t["side"] == "LONG"), 2),
        "net_short": round(sum(t["net"] for t in tr if t["side"] == "SHORT"), 2),
        "usd30_10k": round(net * scale / days * 30, 2) if days else 0.0,
        "days": round(days, 2), "base": res["base"],
        "pf": round(sum(wins) / gl, 3) if gl > 0 else None,
        "win_rate": round(len(wins) / len(tr), 3) if tr else None,
        "exp_r": round(statistics.mean(rs), 4) if rs else None,
        "exp_r_se": round(statistics.stdev(rs) / math.sqrt(len(rs)), 4) if len(rs) > 1 else None,
        "avg_hold_h": round(statistics.mean(t["hold_h"] for t in tr), 1) if tr else None,
        "exposure": round(sum(t["hold_h"] for t in tr) / (len(res["sleeves"]) * days * 24), 4) if days else 0.0,
        "trades_per_30d": round(len(tr) / days * 30, 2) if days else 0.0,
        "avg_risk_usd": round(statistics.mean(t["risk_usd"] for t in tr), 2) if tr else None,
        "max_dd_pct": round(100 * res["max_dd_pct"], 2), "max_dd_usd": round(res["max_dd"], 2),
        "stopped": res["stopped"], "pause_hours": res["pause_hours"],
        "withdrawn": round(res["withdrawn"], 2), "final_equity": round(res["final_equity"], 2),
        "coin_share": round(max(per_coin.values()) / pos_coin, 3) if pos_coin > 0 else None,
        "month_share": round(max(months.values()) / pos_month, 3) if pos_month > 0 else None,
        "neg_coin_sum": round(sum(v for v in per_coin.values() if v < 0), 2),
        "neg_month_sum": round(sum(v for v in months.values() if v < 0), 2),
        "worst_month_10k": round(min(months.values()) * scale, 2) if months else 0.0,
        "median_daily_10k": round(statistics.median(daily) * scale, 2) if daily else 0.0,
        "losing_streak": worst,
        "full_months": len(full_vals), "months_ge_500": k5, "months_ge_1000": k10,
        "ci_ge_500": wilson(k5, len(full_vals)), "ci_ge_1000": wilson(k10, len(full_vals)),
        "skips": _skip_counts(res["skips"]),
        "per_coin": {k: round(v, 2) for k, v in sorted(per_coin.items())},
        "months": {k: round(v, 2) for k, v in months.items()},
        "months_full": full,
        "withdrawals_by_month": {k: round(mm["withdrawn"], 2) for k, mm in res["months"].items()},
    }
    by_reg: dict = defaultdict(list)
    for t in tr:
        by_reg[t.get("regime") or "?"].append(t["net"])
    m["by_regime"] = {k: {"trades": len(v), "net": round(sum(v), 2)} for k, v in by_reg.items()}
    if folds:
        fd = {}
        for fn, a, b in FOLDS:
            fd[fn] = round(sum(t["net"] for t in tr if a.timestamp() <= t["entry_t"] < b.timestamp()), 2)
        m["folds"] = fd
        m["folds_pos"] = sum(1 for x in fd.values() if x > 0)
    return m


def _next_month(y: int, mo: int) -> int:
    y2, m2 = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return int(datetime(y2, m2, 1, tzinfo=UTC).timestamp())


def _skip_counts(skips) -> dict:
    d: dict = defaultdict(int)
    for s in skips:
        d[s[3]] += 1
    return dict(d)
