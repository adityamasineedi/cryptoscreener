"""
Loss analysis for the CHoCH/BOS + demand zone + volume long strategy.

Two modes (use either or both):

  python loss_analysis.py --monthly monthly_results.csv
      Works on the month-by-month table. Shows losing months, streaks, worst months,
      and how losses relate to what BTC did that month.

  python loss_analysis.py --trades trades.csv
      Works on the trade log written by smc_long_strategy.py. Shows WHERE the losing
      trades come from (setup, exit reason, hour, weekday, holding time, stop size, zone
      width) and tests simple loss-limiting rules (monthly loss limit, cooldown after a
      losing streak) so you can see what they would have done to total R.

Requires: pandas, numpy, tabulate.
"""
import argparse

import numpy as np
import pandas as pd


def longest_run(flags) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def show(title: str, df: pd.DataFrame, index=False):
    print(f"\n### {title}\n")
    print(df.to_markdown(index=index))


# ----------------------------------------------------------------------------- monthly
def monthly_loss_report(path: str):
    m = pd.read_csv(path)
    m.columns = [c.strip() for c in m.columns]
    m["Month"] = m["Month"].astype(str)
    m["Year"] = m["Month"].str[:4]
    ret, btc = m["Strategy %"], m["BTC buy&hold %"]
    lose = ret < 0

    print(f"\n=== MONTHLY LOSS ANALYSIS ({m['Month'].iloc[0]} to {m['Month'].iloc[-1]}) ===")
    ov = pd.DataFrame({
        "Metric": [
            "Months", "Losing months", "Losing months %", "Avg losing month %", "Avg winning month %",
            "Sum of losing months %", "Sum of winning months %", "Longest losing-month streak",
            "Total trades", "Total R",
        ],
        "Value": [
            len(m), int(lose.sum()), round(100 * lose.mean(), 1), round(ret[lose].mean(), 2),
            round(ret[~lose].mean(), 2), round(ret[lose].sum(), 2), round(ret[~lose].sum(), 2),
            longest_run(lose), int(m["Trades"].sum()), round(m["Total R"].sum(), 2),
        ],
    })
    show("Overview", ov)

    worst = m.nsmallest(7, "Strategy %")[["Month", "Trades", "Wins", "Win %", "Total R", "Strategy %", "BTC buy&hold %"]]
    show("Worst 7 months", worst)

    by_year = m.groupby("Year").agg(
        Months=("Month", "count"), Losing=("Strategy %", lambda x: int((x < 0).sum())),
        Trades=("Trades", "sum"), TotalR=("Total R", "sum"),
        Strategy=("Strategy %", lambda x: ((1 + x / 100).prod() - 1) * 100),
        BTC=("BTC buy&hold %", lambda x: ((1 + x / 100).prod() - 1) * 100),
    ).round(2)
    show("By year (compounded %)", by_year.reset_index())

    rows = []
    for name, mask in [("BTC up >10%", btc > 10), ("BTC up 0-10%", (btc > 0) & (btc <= 10)),
                       ("BTC down 0-10%", (btc <= 0) & (btc > -10)), ("BTC down >10%", btc <= -10)]:
        s = ret[mask]
        rows.append({"BTC month type": name, "Months": int(mask.sum()),
                     "Losing": int((s < 0).sum()),
                     "Losing %": round(100 * (s < 0).mean(), 0) if len(s) else np.nan,
                     "Avg strategy %": round(s.mean(), 2) if len(s) else np.nan})
    show("Losses vs what BTC did that month", pd.DataFrame(rows))

    up_miss = m[(btc > 10) & lose][["Month", "Strategy %", "BTC buy&hold %"]]
    if len(up_miss):
        show("Months BTC rallied >10% but the strategy still lost (missed trend / whipsaw)", up_miss)

    busy = m[(m["Trades"] >= m["Trades"].median()) & (m["Win %"] <= 25)][["Month", "Trades", "Win %", "Total R", "Strategy %"]]
    if len(busy):
        show("Busy-but-bad months (above-median trades, win rate <= 25%): choppy-market signature", busy)

    m["cumR"] = m["Total R"].cumsum()
    dd = (m["cumR"] - m["cumR"].cummax())
    print(f"\nWorst drawdown in cumulative R: {dd.min():.2f} R, trough at {m.loc[dd.idxmin(), 'Month']}")
    print(f"Correlation of strategy monthly % with BTC monthly %: {ret.corr(btc):.2f}")


# ----------------------------------------------------------------------------- trades
def breakdown(t: pd.DataFrame, col: str) -> pd.DataFrame:
    g = t.groupby(col, observed=True)
    out = pd.DataFrame({
        "Trades": g.size(),
        "Losers": g["R"].apply(lambda x: int((x < 0).sum())),
        "Loss %": g["R"].apply(lambda x: round(100 * (x < 0).mean(), 0)),
        "Avg R": g["R"].mean().round(2),
        "Total R": g["R"].sum().round(2),
    })
    return out.reset_index()


def apply_rules(t: pd.DataFrame, month_limit=None, cooldown_after=None, cooldown_trades=0) -> float:
    """Replay trades in order, skipping some according to a loss-limiting rule. Returns total R."""
    total, month_r, cur_month, streak, skip = 0.0, 0.0, None, 0, 0
    for _, r in t.iterrows():
        mth = r["exit_month"]
        if mth != cur_month:
            cur_month, month_r = mth, 0.0
        if skip > 0:
            skip -= 1
            continue
        if month_limit is not None and month_r <= -month_limit:
            continue
        total += r["R"]
        month_r += r["R"]
        streak = streak + 1 if r["R"] < 0 else 0
        if cooldown_after and streak >= cooldown_after:
            skip, streak = cooldown_trades, 0
    return total


def trade_loss_report(path: str):
    t = pd.read_csv(path, parse_dates=["entry_time", "exit_time"]).sort_values("exit_time").reset_index(drop=True)
    t["exit_month"] = t["exit_time"].dt.to_period("M").astype(str)
    losers = t[t["R"] < 0]
    print(f"\n=== TRADE-LEVEL LOSS ANALYSIS ({len(t)} trades) ===")
    if t.empty or losers.empty:
        print("Nothing to analyse.")
        return
    wins = t[t["R"] > 0]
    ov = pd.DataFrame({
        "Metric": ["Trades", "Losers", "Loss rate %", "Avg loss R", "Avg win R", "Gross loss R", "Gross win R",
                   "Profit factor", "Longest losing streak (trades)", "Total R"],
        "Value": [len(t), len(losers), round(100 * len(losers) / len(t), 1), round(losers["R"].mean(), 2),
                  round(wins["R"].mean(), 2) if len(wins) else 0, round(losers["R"].sum(), 2),
                  round(wins["R"].sum(), 2),
                  round(wins["R"].sum() / -losers["R"].sum(), 2), longest_run(t["R"] < 0), round(t["R"].sum(), 2)],
    })
    show("Overview", ov)

    show("By setup", breakdown(t, "setup"))
    show("By exit reason", breakdown(t, "reason"))
    t["entry_hour"] = t["entry_time"].dt.hour
    show("By entry hour", breakdown(t, "entry_hour"))
    t["weekday"] = t["entry_time"].dt.day_name()
    show("By entry weekday", breakdown(t, "weekday"))
    t["held"] = pd.cut(t["bars"], [-1, 2, 8, 24, 72, np.inf], labels=["0-2 bars", "3-8", "9-24", "25-72", "73+"])
    show("By holding time", breakdown(t, "held"))
    t["stop_pct"] = (t["entry"] - t["stop0"]) / t["entry"] * 100
    t["zone_pct"] = (t["zone_high"] - t["zone_low"]) / t["entry"] * 100
    for col, label in [("stop_pct", "By stop distance (% of price, quartiles)"), ("zone_pct", "By zone width (% of price, quartiles)")]:
        try:
            t[col + "_q"] = pd.qcut(t[col], 4, duplicates="drop").astype(str)
            show(label, breakdown(t, col + "_q"))
        except ValueError:
            pass

    mm = t.groupby("exit_month")["R"].sum().round(2).sort_values().head(6).reset_index()
    show("Worst 6 months by R", mm)

    base = t["R"].sum()
    rows = [{"Rule": "No rule (baseline)", "Total R": round(base, 2), "Change": 0.0}]
    for lim in (2, 3, 4, 5):
        r = apply_rules(t, month_limit=lim)
        rows.append({"Rule": f"Stop trading for the month at -{lim}R", "Total R": round(r, 2), "Change": round(r - base, 2)})
    for n, k in ((3, 2), (3, 5), (4, 5)):
        r = apply_rules(t, cooldown_after=n, cooldown_trades=k)
        rows.append({"Rule": f"After {n} losses in a row, skip next {k} trades", "Total R": round(r, 2), "Change": round(r - base, 2)})
    show("What-if: loss-limiting rules (replayed on this trade log)", pd.DataFrame(rows))
    print("\nNote: these replays assume skipped trades simply do not happen. Check any rule that helps on a")
    print("different period (e.g. tune on 2022-2024, confirm on 2025-2026) before trusting it.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--monthly", help="monthly results CSV")
    ap.add_argument("--trades", help="trades.csv from smc_long_strategy.py")
    a = ap.parse_args()
    if not (a.monthly or a.trades):
        ap.error("give --monthly FILE and/or --trades FILE")
    if a.monthly:
        monthly_loss_report(a.monthly)
    if a.trades:
        trade_loss_report(a.trades)


if __name__ == "__main__":
    main()
