"""Rolling walk-forward: choose grid params on train, apply untouched to the next test window.

Test windows run back to back and carry equity forward; open trades are closed
at each test window's end. Only the stitched test windows are out-of-sample.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from app.research.regime_lab.config import RunConfig, walk_forward_grid
from app.research.regime_lab.engine import Trade, breakdowns, downsample, monte_carlo, run_backtest, summarize
from app.research.regime_lab.strategies import SymbolData

MIN_TRAIN_TRADES = 10


def add_months(t: datetime, months: int) -> datetime:
    m = t.month - 1 + months
    year = t.year + m // 12
    month = m % 12 + 1
    day = min(t.day, 28)
    return t.replace(year=year, month=month, day=day)


def folds(start: datetime, end: datetime, train_months: int, test_months: int) -> list[dict[str, datetime]]:
    out = []
    train_start = start
    while True:
        test_start = add_months(train_start, train_months)
        test_end = min(add_months(test_start, test_months), end)
        if test_start >= end or test_end <= test_start:
            break
        out.append({"train_start": train_start, "train_end": test_start, "test_start": test_start, "test_end": test_end})
        if test_end >= end:
            break
        train_start = add_months(train_start, test_months)
    return out


def train_score(summary: dict[str, Any]) -> float | None:
    """Return over drawdown; None when the train window has too few trades to judge."""
    if summary["trades"] < MIN_TRAIN_TRADES:
        return None
    return summary["return_pct"] / max(summary["max_drawdown_pct"], 1.0)


def plateau_scores(grid: list[dict[str, Any]], scores: list[float | None]) -> list[float | None]:
    """Mean score of each point and its neighbours (points differing in exactly one setting).

    A point whose neighbours fail scores low, so a lone spike loses to a broad good zone.
    Neighbours below the train trade minimum count as 0.
    """
    out: list[float | None] = []
    for k, point in enumerate(grid):
        if scores[k] is None:
            out.append(None)
            continue
        vals = [scores[k]]
        for j, other in enumerate(grid):
            if j != k and sum(1 for key in point if point[key] != other[key]) == 1:
                vals.append(scores[j] if scores[j] is not None else 0.0)
        out.append(sum(vals) / len(vals))
    return out


def run_walk_forward(
    datas: dict[str, SymbolData],
    cfg: RunConfig,
    start: datetime,
    end: datetime,
    *,
    btc: SymbolData | None,
    train_months: int,
    test_months: int,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    plan = folds(start, end, train_months, test_months)
    if not plan:
        raise ValueError("range too short for one train + test fold")
    grid = walk_forward_grid(cfg)
    total_runs = len(plan) * (len(grid) + 1)
    done = 0
    equity = cfg.initial_equity
    peak = equity
    halted = False
    kill_at = None
    fold_rows: list[dict[str, Any]] = []
    oos_trades: list[dict[str, Any]] = []
    oos_curve: list = []
    exposed = 0
    bars = 0
    counters: dict[str, int] = {}

    for k, f in enumerate(plan):
        summaries: list[dict[str, Any]] = []
        for point in grid:
            res = run_backtest(
                datas, cfg.with_overrides(**point), f["train_start"], f["train_end"],
                btc=btc, include_trades=False,
            )
            summaries.append(res["summary"])
            done += 1
            if progress:
                progress(done / total_runs, f"fold {k + 1}/{len(plan)} train")
        raw = [train_score(s) for s in summaries]
        scores = plateau_scores(grid, raw) if cfg.safeguards.wf_selection == "plateau" else raw
        best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        for point, score, s in zip(grid, scores, summaries):
            if score is not None and (best is None or score > best[0]):
                best = (score, point, s)
        chosen = best[1] if best else {}
        test_cfg = cfg.with_overrides(**chosen)
        res = run_backtest(
            datas, test_cfg, f["test_start"], f["test_end"], btc=btc, initial_equity=equity,
            with_monte_carlo=False, peak_equity=peak, halted=halted,
        )
        peak, halted = res["peak_equity"], res["halted"]
        kill_at = kill_at or res["kill_switch_at"]
        done += 1
        if progress:
            progress(done / total_runs, f"fold {k + 1}/{len(plan)} test")
        equity = res["end_equity"]
        oos_trades.extend(res["trades"])
        oos_curve.extend(res["_curve"])
        s = res["summary"]
        exposed += round(s["exposure_pct"] / 100 * len(res["_curve"]))
        bars += len(res["_curve"])
        for key, v in res["counters"].items():
            counters[key] = counters.get(key, 0) + v
        fold_rows.append(
            {
                "fold": k + 1,
                **{key: v.isoformat() for key, v in f.items()},
                "params": chosen or "defaults (no grid point reached the train trade minimum)",
                "train_score": None if best is None else best[0],
                "train_return_pct": None if best is None else best[2]["return_pct"],
                "train_trades": None if best is None else best[2]["trades"],
                "test_return_pct": s["return_pct"],
                "test_max_drawdown_pct": s["max_drawdown_pct"],
                "test_trades": s["trades"],
                "test_net_pnl": s["net_pnl"],
                "test_win_rate": s["win_rate"],
            }
        )

    trades = [Trade(**t) for t in oos_trades]
    return {
        "summary": summarize(trades, oos_curve, cfg.initial_equity, cfg.timeframe, exposed, bars),
        "counters": counters,
        "breakdowns": breakdowns(trades),
        "folds": fold_rows,
        "grid": grid,
        "equity_curve": downsample(oos_curve),
        "trades": oos_trades,
        "trade_count": len(oos_trades),
        "positive_folds": sum(1 for r in fold_rows if r["test_net_pnl"] > 0),
        "monte_carlo": monte_carlo(trades, cfg.initial_equity),
        "selection": cfg.safeguards.wf_selection,
        "kill_switch_at": kill_at,
    }
