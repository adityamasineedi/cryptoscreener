"""Population aggregates, matrices, winner/loser comparisons (research-only)."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any, Mapping, Sequence

from app.research.trade_plan_forensics.schemas import sample_size_status


def _r(rec: Mapping[str, Any]) -> float | None:
    t = rec.get("trade") or {}
    v = t.get("R")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _is_win(rec: Mapping[str, Any]) -> bool:
    r = _r(rec)
    return r is not None and r > 0


def _is_loss(rec: Mapping[str, Any]) -> bool:
    r = _r(rec)
    return r is not None and r <= 0


def bootstrap_mean_ci(
    values: Sequence[float], *, n_boot: int = 500, alpha: float = 0.05, seed: int = 42
) -> dict[str, Any]:
    if not values:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0}
    rng = random.Random(seed)
    arr = list(values)
    means = []
    n = len(arr)
    for _ in range(n_boot):
        sample = [arr[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot) - 1]
    return {
        "mean": sum(arr) / n,
        "ci_low": lo,
        "ci_high": hi,
        "n": n,
        "note": "Descriptive bootstrap percentile interval; not a significance test.",
    }


def summarize(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closed = [r for r in recs if _r(r) is not None]
    rs = [_r(r) for r in closed if _r(r) is not None]
    rs_f = [float(x) for x in rs if x is not None]
    wins = sum(1 for x in rs_f if x > 0)
    losses = sum(1 for x in rs_f if x <= 0)
    n = len(rs_f)
    gp = sum(x for x in rs_f if x > 0)
    gl = abs(sum(x for x in rs_f if x <= 0))
    pf = (gp / gl) if gl > 0 else None
    mae = [
        float((r.get("stop_forensics") or {}).get("path", {}).get("mae_r"))
        for r in closed
        if (r.get("stop_forensics") or {}).get("path", {}).get("mae_r") is not None
    ]
    mfe = [
        float((r.get("tp_forensics") or {}).get("mfe_r"))
        for r in closed
        if (r.get("tp_forensics") or {}).get("mfe_r") is not None
    ]
    hold = [
        int((r.get("trade") or {}).get("bars_held"))
        for r in closed
        if (r.get("trade") or {}).get("bars_held") is not None
    ]
    # max DD on R equity
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in rs_f:
        eq += x
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) if n else None,
        "mean_R": (sum(rs_f) / n) if n else None,
        "median_R": (sorted(rs_f)[n // 2] if n else None),
        "profit_factor": pf,
        "max_drawdown_R": max_dd if n else None,
        "mean_MAE_R": (sum(mae) / len(mae)) if mae else None,
        "mean_MFE_R": (sum(mfe) / len(mfe)) if mfe else None,
        "avg_holding_bars": (sum(hold) / len(hold)) if hold else None,
        "sample_status": sample_size_status(n),
        "mean_R_bootstrap": bootstrap_mean_ci(rs_f),
        "win_rate_bootstrap": bootstrap_mean_ci(
            [1.0 if x > 0 else 0.0 for x in rs_f]
        ),
    }


def group_by(recs: Sequence[Mapping[str, Any]], key_fn) -> dict[str, Any]:
    buckets: dict[str, list] = defaultdict(list)
    for r in recs:
        buckets[str(key_fn(r))].append(r)
    return {k: summarize(v) for k, v in sorted(buckets.items(), key=lambda x: x[0])}


def cross_tabs(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "by_direction": group_by(
            recs, lambda r: (r.get("trade") or {}).get("direction") or "UNKNOWN"
        ),
        "by_timeframe": group_by(
            recs, lambda r: (r.get("trade") or {}).get("timeframe") or "UNKNOWN"
        ),
        "by_htf_state": group_by(recs, lambda r: r.get("htf_state") or "UNKNOWN"),
        "by_entry_timing": group_by(
            recs, lambda r: (r.get("entry_timing") or {}).get("class") or "UNKNOWN"
        ),
        "by_regime_primary": group_by(
            recs, lambda r: ((r.get("regime") or {}).get("primary") or "UNKNOWN")
        ),
        "by_session_bin": group_by(
            recs, lambda r: (r.get("session") or {}).get("bin") or "UNKNOWN"
        ),
        "by_entry_vs_bos": group_by(
            recs,
            lambda r: (r.get("local_structure") or {}).get("entry_vs_bos_class")
            or "UNKNOWN",
        ),
        "direction_x_regime": group_by(
            recs,
            lambda r: f"{(r.get('trade') or {}).get('direction')}|{(r.get('regime') or {}).get('primary')}",
        ),
        "direction_x_htf": group_by(
            recs,
            lambda r: f"{(r.get('trade') or {}).get('direction')}|{r.get('htf_state')}",
        ),
        "direction_x_timing": group_by(
            recs,
            lambda r: f"{(r.get('trade') or {}).get('direction')}|{(r.get('entry_timing') or {}).get('class')}",
        ),
        "direction_x_session": group_by(
            recs,
            lambda r: f"{(r.get('trade') or {}).get('direction')}|{(r.get('session') or {}).get('bin')}",
        ),
        "tf_x_htf": group_by(
            recs,
            lambda r: f"{(r.get('trade') or {}).get('timeframe')}|{r.get('htf_state')}",
        ),
        "regime_x_timing_x_htf": group_by(
            recs,
            lambda r: (
                f"{(r.get('regime') or {}).get('primary')}|"
                f"{(r.get('entry_timing') or {}).get('class')}|"
                f"{r.get('htf_state')}"
            ),
        ),
    }


def winner_loser_compare(recs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wins = [r for r in recs if _is_win(r)]
    losses = [r for r in recs if _is_loss(r)]

    def feat(rows: Sequence[Mapping[str, Any]], getter) -> dict[str, Any]:
        vals = []
        for r in rows:
            v = getter(r)
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                vals.append(float(v))
        return {
            "n": len(vals),
            "mean": (sum(vals) / len(vals)) if vals else None,
            "median": (sorted(vals)[len(vals) // 2] if vals else None),
        }

    getters = {
        "atr_percent": lambda r: (r.get("entry_context") or {}).get("atr_percent"),
        "rvol": lambda r: (r.get("entry_context") or {}).get("rvol"),
        "bos_distance_atr": lambda r: (r.get("local_structure") or {}).get("bos_distance_atr"),
        "bars_since_bos": lambda r: (r.get("local_structure") or {}).get("bars_since_bos"),
        "pullback_retracement_pct": lambda r: ((r.get("local_structure") or {}).get("pullback") or {}).get(
            "retracement_pct"
        ),
        "mae_r": lambda r: (r.get("stop_forensics") or {}).get("path", {}).get("mae_r"),
        "mfe_r": lambda r: (r.get("tp_forensics") or {}).get("mfe_r"),
        "sl_distance_atr": lambda r: (r.get("stop_forensics") or {}).get("sl_distance_atr"),
    }
    features = {
        k: {"winners": feat(wins, g), "losers": feat(losses, g)} for k, g in getters.items()
    }

    def rate(rows, pred):
        n = len(rows)
        if not n:
            return None
        return sum(1 for r in rows if pred(r)) / n

    cats = {
        "htf_conflict_rate": {
            "winners": rate(wins, lambda r: r.get("htf_state") == "HTF_CONFLICT"),
            "losers": rate(losses, lambda r: r.get("htf_state") == "HTF_CONFLICT"),
        },
        "htf_aligned_rate": {
            "winners": rate(wins, lambda r: r.get("htf_state") == "HTF_ALIGNED"),
            "losers": rate(losses, lambda r: r.get("htf_state") == "HTF_ALIGNED"),
        },
        "chop_label_rate": {
            "winners": rate(
                wins, lambda r: "CHOP" in ((r.get("regime") or {}).get("labels") or [])
            ),
            "losers": rate(
                losses, lambda r: "CHOP" in ((r.get("regime") or {}).get("labels") or [])
            ),
        },
        "very_late_timing_rate": {
            "winners": rate(
                wins, lambda r: (r.get("entry_timing") or {}).get("class") == "VERY_LATE"
            ),
            "losers": rate(
                losses, lambda r: (r.get("entry_timing") or {}).get("class") == "VERY_LATE"
            ),
        },
        "long_rate": {
            "winners": rate(
                wins, lambda r: (r.get("trade") or {}).get("direction") == "LONG"
            ),
            "losers": rate(
                losses, lambda r: (r.get("trade") or {}).get("direction") == "LONG"
            ),
        },
    }
    return {
        "winners_n": len(wins),
        "losers_n": len(losses),
        "features": features,
        "categorical_rates": cats,
        "note": "Descriptive comparison only — not causal and not a strategy selection.",
    }


def losing_cluster_notes(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    losses = [r for r in recs if _is_loss(r)]
    n = len(losses)
    if n == 0:
        return ["No losing trades in sample."]
    notes: list[str] = []
    htf_c = sum(1 for r in losses if r.get("htf_state") == "HTF_CONFLICT")
    notes.append(f"Losses with HTF_CONFLICT: {htf_c}/{n} ({htf_c/n:.0%}).")
    late = sum(
        1
        for r in losses
        if (r.get("entry_timing") or {}).get("class") in ("LATE", "VERY_LATE")
    )
    notes.append(f"Losses with LATE/VERY_LATE entry timing: {late}/{n} ({late/n:.0%}).")
    chop = sum(1 for r in losses if "CHOP" in ((r.get("regime") or {}).get("labels") or []))
    notes.append(f"Losses labeled CHOP: {chop}/{n} ({chop/n:.0%}).")
    short = sum(1 for r in losses if (r.get("trade") or {}).get("direction") == "SHORT")
    notes.append(f"SHORT losses: {short}/{n} ({short/n:.0%}).")
    far = sum(
        1
        for r in losses
        if (r.get("local_structure") or {}).get("entry_vs_bos_class")
        == "ENTRY_TOO_FAR_AFTER_BOS"
    )
    notes.append(f"Losses ENTRY_TOO_FAR_AFTER_BOS: {far}/{n} ({far/n:.0%}).")
    return notes
