"""Render out/results.json into out/tables.md (numbers only; REPORT.md carries the reading)."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def f(v, nd=2, pct=False, sign=False):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s + ("%" if pct else "")


ROWS = [
    ("Trades", "trades", 0, False, False),
    ("Net USD", "net_usd", 2, False, True),
    ("Net R (this cost model)", "net_R", 2, False, True),
    ("Gross R", "gross_R", 2, False, True),
    ("After-fee R", "after_fee_R", 2, False, True),
    ("Win rate", "win_rate", 1, True, False),
    ("Avg win R", "avg_win_R", 2, False, False),
    ("Avg loss R", "avg_loss_R", 2, False, False),
    ("Payoff", "payoff", 2, False, False),
    ("Profit factor", "pf", 2, False, False),
    ("Expectancy R", "expectancy_R", 3, False, True),
    ("Expectancy 95% CI", "expectancy_ci95", 3, False, False),
    ("t-stat", "t_stat", 2, False, False),
    ("Max DD USD", "max_dd_usd", 2, False, False),
    ("Max DD R", "max_dd_R", 2, False, False),
    ("Longest losing streak", "longest_losing_streak", 0, False, False),
    ("Avg duration h", "avg_hold_hours", 1, False, False),
    ("Exposure % of time", "exposure_pct", 1, True, False),
    ("Fees USD", "fees_usd", 2, False, False),
    ("Slippage USD", "slip_usd", 2, False, False),
    ("Funding USD (+ = received)", "funding_usd", 2, False, True),
    ("Fee drag % of gross", "fee_drag_pct", 1, True, False),
    ("All-cost drag % of gross", "cost_drag_pct", 1, True, False),
    ("TP / SL / gap stops", None, 0, False, False),
]


def cell(m: dict, key, nd, pct, sign):
    if not m or not m.get("trades"):
        return "—"
    if key is None:
        return f"{m['tp']} / {m['sl']} / {m['gaps']}"
    v = m.get(key)
    if key == "win_rate" and v is not None:
        v = 100 * v
    if key == "expectancy_ci95" and v:
        return f"[{v[0]:+.3f}, {v[1]:+.3f}]"
    return f(v, nd, pct, sign)


def metric_table(cols: list[tuple[str, dict]]) -> str:
    head = "| Metric | " + " | ".join(c for c, _ in cols) + " |\n|---|" + "---:|" * len(cols) + "\n"
    body = ""
    for label, key, nd, pct, sign in ROWS:
        body += f"| {label} | " + " | ".join(cell(m, key, nd, pct, sign) for _, m in cols) + " |\n"
    return head + body


def small_table(groups: dict, keys=("trades", "expectancy_R", "net_R", "pf", "win_rate", "max_dd_R")) -> str:
    head = "| Group | Trades | Exp R | Net R | PF | Win % | Max DD R |\n|---|---:|---:|---:|---:|---:|---:|\n"
    rows = ""
    for g, m in groups.items():
        if not m.get("trades"):
            continue
        rows += (
            f"| {g} | {m['trades']} | {f(m['expectancy_R'], 3, sign=True)} | {f(m['net_R'], 2, sign=True)} | "
            f"{f(m.get('pf'))} | {f(100 * m['win_rate'], 1)} | {f(m['max_dd_R'])} |\n"
        )
    return head + rows


def main() -> None:
    res = json.loads((OUT / "results.json").read_text())
    md = [f"# LCSC 1h LONG validation — generated tables\n\nGenerated {res['generated_utc']}. Windows: {res['windows']}\n"]
    for sym, R in res["symbols"].items():
        md.append(f"\n## {sym}\n")
        p = R["parity"]
        md.append(f"Parity with run_engine_cell: {p['closed']} closed trades, AFTER_FEE net ${p['after_fee_net_usd']:.2f}.\n")
        v1 = R["v1"]
        md.append("\n### v1 frozen, FULL costs, by window\n")
        md.append(metric_table([(w, v1[w]["FULL"]) for w in ("TRAIN", "VALIDATION", "HOLDOUT", "OOS_COMBINED", "FULL")]))
        md.append("\n### v1 frozen, cost ladder (full range | combined OOS)\n")
        md.append(
            metric_table(
                [(f"{m} full", v1["FULL"][m]) for m in ("GROSS", "AFTER_FEE", "FULL", "FULL_STRESS")]
                + [(f"{m} OOS", v1["OOS_COMBINED"][m]) for m in ("GROSS", "AFTER_FEE", "FULL", "FULL_STRESS")]
            )
        )
        md.append("\n### By calendar year (v1, FULL)\n" + small_table(R["v1_by_year"]))
        for w in ("FULL", "TRAIN", "OOS_COMBINED"):
            md.append(f"\n### Trend regime, {w} (v1, FULL)\n" + small_table(R["v1_by_trend"][w]))
            md.append(f"\n### Volatility regime, {w} (v1, FULL)\n" + small_table(R["v1_by_vol"][w]))
        md.append(f"\nBar share (eval range) trend: {R['bar_share_trend']}; vol: {R['bar_share_vol']}\n")
        for w, c in R["concentration"].items():
            md.append(f"\n### Concentration, {w} (v1, FULL)\n")
            md.append(
                f"Total {c['total_R']:+.2f}R. Excl. top 1: {c['excl_top1_R']:+.2f}R (${c['excl_top1_usd']:+.2f}); "
                f"top 3: {c['excl_top3_R']:+.2f}R (${c['excl_top3_usd']:+.2f}); top 5: {c['excl_top5_R']:+.2f}R "
                f"(${c['excl_top5_usd']:+.2f}). Fragile: {f(c['fragile'])}. Months {c['months']}, losing {c['losing_months']}, "
                f"median ${f(c['monthly_median_usd'])}, p10 ${f(c['monthly_p10_usd'])}, p90 ${f(c['monthly_p90_usd'])}, "
                f"best month share {f(c['best_month_share_pct'], 1)}%.\n\n"
            )
            md.append("| # | Signal (UTC open) | Exit | Outcome | R | USD | Trend | Vol |\n|---:|---|---|---|---:|---:|---|---|\n")
            for k, t in enumerate(c["top10"], 1):
                md.append(f"| {k} | {t['signal_time'][:16]} | {t['exit_time'][:16]} | {t['outcome']} | {t['R']:+.2f} | {t['net_usd']:+.2f} | {t['trend']} | {t['vol']} |\n")
            if w == "FULL":
                md.append("\nMonthly USD: " + ", ".join(f"{k} {v:+.0f}" for k, v in c["monthly_usd"].items()) + "\n")
        md.append("\n### Monte Carlo (FULL R, 2% risk compounding, $1,000, 10,000 runs)\n")
        md.append("| Sample | Method | Median final | P5 final | P95 final | Median DD % | P95 DD % | P(DD≥20%) | P(DD≥30%) | Median DD R | P95 DD R | P(final<start) |\n|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
        for w, mc in R["monte_carlo"].items():
            for meth, m in mc.items():
                md.append(
                    f"| {w} | {meth} | {m['median_final_equity']:.0f} | {m['p5_final_equity']:.0f} | {m['p95_final_equity']:.0f} | "
                    f"{m['median_max_dd_pct']:.1f} | {m['p95_max_dd_pct']:.1f} | {100 * m['p_dd_ge_20pct']:.1f}% | {100 * m['p_dd_ge_30pct']:.1f}% | "
                    f"{m['median_max_dd_R']:.1f} | {m['p95_max_dd_R']:.1f} | {100 * m['p_final_below_start']:.1f}% |\n"
                )
        md.append("\n### Parameter sensitivity (one at a time, FULL; exp R / trades / PF)\n")
        md.append("| Param | Value | Train | Validation | Holdout | OOS combined | Full |\n|---|---|---|---|---|---|---|\n")
        for param, vals in R["sensitivity"].items():
            for val, ws in vals.items():
                cells = []
                for w in ("TRAIN", "VALIDATION", "HOLDOUT", "OOS_COMBINED", "FULL"):
                    m = ws[w]
                    cells.append(f"{f(m['expectancy_R'], 3, sign=True)} / {m['trades']} / {f(m['pf'])}" if m.get("trades") else "—")
                md.append(f"| {param} | {val} | " + " | ".join(cells) + " |\n")
        md.append("\n### 27-config grid (FULL exp R: train | OOS combined | full)\n| Config | Train | OOS | Full |\n|---|---:|---:|---:|\n")
        for k, ws in R["grid"].items():
            md.append(f"| {k} | " + " | ".join(f"{f(ws[w].get('expectancy_R'), 3, sign=True)} ({ws[w].get('trades')})" for w in ("TRAIN", "OOS_COMBINED", "FULL")) + " |\n")
        wf = R["walk_forward"]
        md.append("\n### Walk-forward folds (FULL)\n| Train | Test | Chosen | WF trades | WF exp R | WF net R | v1 trades | v1 exp R | v1 net R |\n|---|---|---|---:|---:|---:|---:|---:|---:|\n")
        for row in wf["folds"]:
            a, b = row["test_metrics"], row["v1_same_window"]
            md.append(
                f"| {row['train'][0][:10]}→{row['train'][1][:10]} | {row['test'][0][:10]}→{row['test'][1][:10]}{' (holdout)' if row.get('holdout') else ''} | {row['chosen']} | "
                f"{a.get('trades')} | {f(a.get('expectancy_R'), 3, sign=True)} | {f(a.get('net_R'), 2, sign=True)} | {b.get('trades')} | {f(b.get('expectancy_R'), 3, sign=True)} | {f(b.get('net_R'), 2, sign=True)} |\n"
            )
        md.append("\n" + metric_table([
            ("WF tests 2024→val end", wf["wf_tests_2024_to_val_end"]["FULL"]),
            ("WF validation", wf["wf_validation_window"]),
            ("WF holdout", wf["wf_holdout"]),
            ("WF combined OOS", wf["wf_combined_oos"]),
            ("WF all tests", wf["wf_all_tests_incl_holdout"]),
            ("v1 2024→end", wf["v1_same_span_2024_to_end"]),
        ]))
        md.append("\n### Stop-reversal short re-pricing (v1 long stop-outs, full range)\n| Policy | Walk | Costs | Shorts | Win | Loss | Open | Total R | Exp R |\n|---|---|---|---:|---:|---:|---:|---:|---:|\n")
        for k, m in R["short_replay"].items():
            pol, walk, cost = k.split("|")
            md.append(f"| {pol} | {walk} | {cost} | {m['shorts']} | {m['winners']} | {m['losers']} | {m['open']} | {m['total_R']:+.2f} | {f(m['expectancy_R'], 3, sign=True)} |\n")
    (OUT / "tables.md").write_text("".join(md), encoding="utf-8")
    print("wrote", OUT / "tables.md")


if __name__ == "__main__":
    main()
