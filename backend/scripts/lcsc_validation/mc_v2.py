"""Monte Carlo v2 for the LCSC archive. Reads the archived FULL-cost trade lists only.

Nothing is re-simulated: the inputs are out/trades_<SYM>.csv (written by study.py),
closed trades in exit order. Three resampling schemes, 10,000 runs each, fixed seeds:

- PERMUTATION: the observed trades in a random order (no replacement). Use it for
  drawdown uncertainty from sequencing. Final equity is the same in every run
  (sum of R and product of (1 + 0.02 R) do not depend on order) and is reported once.
- BLOCK_BOOTSTRAP: circular block bootstrap, blocks of 8 consecutive trades
  (about two months at ~4 trades/month), drawn with replacement to N trades.
  Keeps short-range clustering of wins and losses. Changes the trade sequence and
  the set of trades. Used for drawdown and final equity.
- IID_BOOTSTRAP: N trades drawn with replacement. Changes the trade sequence and the
  set of trades. Used for final-equity spread; it ignores clustering, so its
  drawdowns are a lower bound on clustering risk.

Final equity: 2% risk per trade, compounding from $1,000. Drawdown in R: additive
cumulative R (fixed $20 risk), max peak-to-trough. Drawdown %: on the compounding curve.
Writes out/monte_carlo_v2.json.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "out"
SIMS = 10_000
BLOCK = 8
RISK = 0.02
START = 1000.0
OOS_FROM = "2025-04-10"


def load(sym: str) -> list[tuple[str, str, float]]:
    with (OUT / f"trades_{sym}.csv").open() as fh:
        rows = [r for r in csv.DictReader(fh) if r["outcome"] != "OPEN"]
    rows.sort(key=lambda r: (r["exit_time"], r["signal_time"]))
    return [(r["signal_time"], r["exit_time"], float(r["R_full"])) for r in rows]


def dd_r(x: np.ndarray) -> float:
    cr = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cr]))[1:]
    return float(np.max(peak - cr))


def run(x: np.ndarray) -> tuple[float, float, float]:
    eq = START * np.cumprod(1 + RISK * x)
    peak = np.maximum.accumulate(np.concatenate([[START], eq]))[1:]
    return float(eq[-1]), dd_r(x), float(np.max(1 - eq / peak))


def block_sample(rng: np.random.Generator, x: np.ndarray) -> np.ndarray:
    n = len(x)
    starts = rng.integers(0, n, size=-(-n // BLOCK))
    idx = (starts[:, None] + np.arange(BLOCK)[None, :]).ravel() % n
    return x[idx[:n]]


def summarise(finals: np.ndarray, ddr: np.ndarray, ddp: np.ndarray, *, final_fixed: bool) -> dict:
    return {
        "median_final_equity": float(np.median(finals)),
        "p5_final_equity": float(np.percentile(finals, 5)),
        "p95_final_equity": float(np.percentile(finals, 95)),
        "final_equity_order_invariant": final_fixed,
        "median_max_dd_R": float(np.median(ddr)),
        "p95_max_dd_R": float(np.percentile(ddr, 95)),
        "p_dd_gt_10R": float((ddr > 10).mean()),
        "p_dd_gt_15R": float((ddr > 15).mean()),
        "p_dd_gt_20R": float((ddr > 20).mean()),
        "median_max_dd_pct": 100 * float(np.median(ddp)),
        "p95_max_dd_pct": 100 * float(np.percentile(ddp, 95)),
    }


def monte_carlo(x: np.ndarray, seed: int) -> dict:
    out = {}
    for name, sampler in (
        ("PERMUTATION", lambda r: r.permutation(x)),
        ("BLOCK_BOOTSTRAP", lambda r: block_sample(r, x)),
        ("IID_BOOTSTRAP", lambda r: r.choice(x, size=len(x), replace=True)),
    ):
        rng = np.random.default_rng(seed)
        res = np.array([run(sampler(rng)) for _ in range(SIMS)])
        out[name] = summarise(res[:, 0], res[:, 1], res[:, 2], final_fixed=name == "PERMUTATION")
    return out


def main() -> None:
    report = {"sims": SIMS, "block_length_trades": BLOCK, "risk_per_trade": RISK, "start_equity": START, "samples": {}}
    for sym in ("ETHUSDT", "BTCUSDT"):
        rows = load(sym)
        for label, sel in (("OOS_COMBINED", [r for r in rows if r[0] >= OOS_FROM]), ("FULL", rows)):
            x = np.array([r[2] for r in sel])
            final, obs_dd_r, obs_dd_pct = run(x)
            key = f"{sym}|{label}"
            report["samples"][key] = {
                "trades": len(x),
                "sum_R": float(x.sum()),
                "observed": {"final_equity": final, "max_dd_R": obs_dd_r, "max_dd_pct": 100 * obs_dd_pct},
                "methods": monte_carlo(x, seed=11),
            }
            print(key, len(x), round(float(x.sum()), 2), round(obs_dd_r, 2))
    (OUT / "monte_carlo_v2.json").write_text(json.dumps(report, indent=1))
    lines = ["| Sample | Method | Median final | P5 / P95 final | Median DD R | P95 DD R | P(DD>10R) | P(DD>15R) | P(DD>20R) |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, s in report["samples"].items():
        for name, m in s["methods"].items():
            p = f"${m['p5_final_equity']:,.0f} / ${m['p95_final_equity']:,.0f}" if name != "PERMUTATION" else "same every run"
            lines.append(f"| {key} ({s['trades']}) | {name} | ${m['median_final_equity']:,.0f} | {p} | "
                         f"{m['median_max_dd_R']:.1f} | {m['p95_max_dd_R']:.1f} | {100 * m['p_dd_gt_10R']:.1f}% | "
                         f"{100 * m['p_dd_gt_15R']:.1f}% | {100 * m['p_dd_gt_20R']:.1f}% |")
    (OUT / "monte_carlo_v2.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
