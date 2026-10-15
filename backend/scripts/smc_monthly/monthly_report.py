"""
Month-by-month results of the CHoCH/BOS + demand zone + volume long strategy.

  python monthly_report.py --csv btc_ohlcv.csv --start 2020-01-01 --end 2026-12-31
  python monthly_report.py --demo

Needs smc_long_strategy.py in the same folder. Prints a markdown table, a yearly summary,
and saves monthly_results.csv.
"""
import argparse

import pandas as pd

from smc_long_strategy import Params, demo_data, read_ohlcv, run_backtest


def _markdown(df: pd.DataFrame) -> str:
    try:
        return df.to_markdown(index=False)
    except ImportError:
        return df.to_string(index=False)


def monthly_table(df: pd.DataFrame, trades: pd.DataFrame, eq: pd.Series, p: Params) -> pd.DataFrame:
    per = pd.period_range(df.index[0], df.index[-1], freq="M")
    month_end_eq = eq.groupby(eq.index.to_period("M")).last().reindex(per).ffill()
    prev_eq = month_end_eq.shift(1).fillna(p.start_equity)
    strat_ret = (month_end_eq / prev_eq - 1) * 100

    close_m = df["close"].groupby(df.index.to_period("M")).last().reindex(per).ffill()
    prev_close = close_m.shift(1)
    prev_close.iloc[0] = df["open"].iloc[0]
    bh_ret = (close_m / prev_close - 1) * 100

    out = pd.DataFrame(index=per)
    if trades.empty:
        out["Trades"] = 0
        out["Wins"] = 0
        out["Total R"] = 0.0
    else:
        t = trades.copy()
        t["m"] = pd.to_datetime(t["exit_time"]).dt.to_period("M")
        g = t.groupby("m")
        out["Trades"] = g.size().reindex(per).fillna(0).astype(int)
        out["Wins"] = g["R"].apply(lambda x: (x > 0).sum()).reindex(per).fillna(0).astype(int)
        out["Total R"] = g["R"].sum().reindex(per).fillna(0.0)
    out["Win %"] = (100 * out["Wins"] / out["Trades"].where(out["Trades"] > 0)).round(0)
    out["Strategy %"] = strat_ret.round(2)
    out["BTC buy&hold %"] = bh_ret.round(2)
    out["Total R"] = out["Total R"].round(2)
    out.index = out.index.astype(str)
    out.index.name = "Month"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--out", default="monthly_results.csv")
    a = ap.parse_args()

    if a.demo:
        df = demo_data(n=6 * 365 * 24)
    elif a.csv:
        df = read_ohlcv(a.csv)
    else:
        ap.error("give --csv FILE or --demo")
    df = df.loc[a.start:a.end]
    if not isinstance(df.index, pd.DatetimeIndex):
        raise SystemExit("CSV needs a timestamp/date column so months can be built.")

    p = Params()
    trades, eq, _ = run_backtest(df, p)
    table = monthly_table(df, trades, eq, p)

    print(_markdown(table.reset_index()))
    table.to_csv(a.out)

    y = table.copy()
    y["Year"] = y.index.str[:4]
    ys = y.groupby("Year").agg(
        Trades=("Trades", "sum"), Wins=("Wins", "sum"), TotalR=("Total R", "sum"),
        Strategy=("Strategy %", lambda x: ((1 + x / 100).prod() - 1) * 100),
        BuyHold=("BTC buy&hold %", lambda x: ((1 + x / 100).prod() - 1) * 100),
    ).round(2)
    ys["Win %"] = (100 * ys["Wins"] / ys["Trades"].where(ys["Trades"] > 0)).round(0)
    print("\nYearly summary\n")
    print(_markdown(ys.reset_index()))
    print(f"\nSaved {a.out}")


if __name__ == "__main__":
    main()
