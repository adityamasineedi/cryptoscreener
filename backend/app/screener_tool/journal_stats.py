"""My Trades statistics and the honesty panel (owner D188 parts 2c and 2d). Pure.

Input: the latest version of each closed, non-void trade, with its D144 net ``r_multiple`` and
``net_pnl_usd``. Trades are ordered by exit time (then open time, then trade id).

- win rate: share of trades with net $ > 0;
- average R = expectancy R = mean net R (equal to win% x avg win R - loss% x avg loss R);
- expectancy $ = mean net $;
- profit factor: sum of winning net $ / |sum of losing net $| (None when there is no loss), also in R;
- max drawdown: the largest drop of the cumulative curve from its running peak (the peak starts at 0), in R and $;
- longest losing streak: most consecutive trades with net $ < 0.

Honesty panel: fewer than 30 closed trades -> "too few trades to judge". From 30: a 90% percentile
bootstrap confidence interval of the mean net R (10,000 resamples, fixed seed), and whether its lower
bound is above 0 after costs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

MIN_TRADES_TO_JUDGE = 30
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261022
BOOTSTRAP_CONFIDENCE = 0.90
_CHUNK = 1_000
TOO_FEW_TEXT = "too few trades to judge"


def _sort_key(t: Mapping[str, Any]) -> tuple:
    def ts(v: Any) -> str:
        return v.isoformat() if isinstance(v, datetime) else str(v or "")

    return (ts(t.get("exit_at")), ts(t.get("opened_at")), str(t.get("trade_id") or ""))


def _max_drawdown(values: Sequence[float]) -> float:
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def _profit_factor(values: Sequence[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None
    return wins / losses


def _longest_losing_streak(values: Sequence[float]) -> int:
    best = run = 0
    for v in values:
        run = run + 1 if v < 0 else 0
        best = max(best, run)
    return best


def summarize(trades: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = sorted(trades, key=_sort_key)
    r = [float(t["r_multiple"]) for t in rows]
    usd = [float(t["net_pnl_usd"]) for t in rows]
    n = len(rows)
    if n == 0:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "avg_r": None, "expectancy_r": None,
                "expectancy_usd": None, "total_r": 0.0, "net_pnl_usd": 0.0, "profit_factor": None,
                "profit_factor_r": None, "max_drawdown_r": 0.0, "max_drawdown_usd": 0.0,
                "longest_losing_streak": 0, "avg_win_r": None, "avg_loss_r": None}
    wins = [x for x, d in zip(r, usd) if d > 0]
    losses = [x for x, d in zip(r, usd) if d < 0]
    mean_r = sum(r) / n
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n,
        "avg_r": mean_r,
        "expectancy_r": mean_r,
        "expectancy_usd": sum(usd) / n,
        "total_r": sum(r),
        "net_pnl_usd": sum(usd),
        "profit_factor": _profit_factor(usd),
        "profit_factor_r": _profit_factor(r),
        "max_drawdown_r": _max_drawdown(r),
        "max_drawdown_usd": _max_drawdown(usd),
        "longest_losing_streak": _longest_losing_streak(usd),
        "avg_win_r": sum(wins) / len(wins) if wins else None,
        "avg_loss_r": sum(losses) / len(losses) if losses else None,
    }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = BOOTSTRAP_CONFIDENCE,
) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean. Resamples are drawn in fixed chunks from one seeded generator."""
    x = np.asarray(list(values), dtype=float)
    n = x.size
    if n == 0:
        raise ValueError("no values")
    rng = np.random.default_rng(seed)
    means = np.empty(resamples, dtype=float)
    done = 0
    while done < resamples:
        k = min(_CHUNK, resamples - done)
        idx = rng.integers(0, n, size=(k, n))
        means[done : done + k] = x[idx].mean(axis=1)
        done += k
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(means, [alpha, 1.0 - alpha])
    return float(lo), float(hi)


def honesty(trades: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    r = [float(t["r_multiple"]) for t in trades]
    n = len(r)
    base = {"trades": n, "min_trades": MIN_TRADES_TO_JUDGE, "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED, "confidence": BOOTSTRAP_CONFIDENCE, "metric": "mean net R per trade (D144 costs)"}
    if n < MIN_TRADES_TO_JUDGE:
        return {**base, "judgeable": False, "text": TOO_FEW_TEXT, "expectancy_r": (sum(r) / n) if n else None,
                "ci_low": None, "ci_high": None, "lower_bound_above_zero": None}
    lo, hi = bootstrap_mean_ci(r)
    above = lo > 0
    text = (
        f"Expectancy {sum(r) / n:+.3f} R per trade, 90% CI {lo:+.3f} to {hi:+.3f} R: "
        + ("the lower bound is above 0 after costs." if above else "the lower bound is not above 0 after costs.")
    )
    return {**base, "judgeable": True, "text": text, "expectancy_r": sum(r) / n, "ci_low": lo, "ci_high": hi,
            "lower_bound_above_zero": above}


def _month(t: Mapping[str, Any]) -> str:
    v = t.get("exit_at")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m")
    return str(v or "")[:7] or "unknown"


def grouped(trades: Sequence[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], str]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for t in trades:
        groups.setdefault(key(t), []).append(t)
    return [{"key": k, **summarize(v)} for k, v in sorted(groups.items())]


def full_stats(closed: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "overall": summarize(closed),
        "by_setup": grouped(closed, lambda t: str(t.get("setup_tag") or "").strip() or "(no tag)"),
        "by_coin": grouped(closed, lambda t: str(t.get("symbol") or "")),
        "by_month": grouped(closed, _month),
        "honesty": honesty(closed),
    }
