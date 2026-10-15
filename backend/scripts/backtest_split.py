"""
Multi-timeframe breakout backtest, split by year and by market regime.

Usage:
    python backtest_split.py your_15m_data.csv
CSV columns: datetime,open,high,low,close,volume   (datetime parseable, 15m bars)
Optional: set YEARS to the last N calendar years you want to see.
"""
import sys
import numpy as np
import pandas as pd
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

# ---------------- settings ----------------
YEARS = 4              # how many most recent calendar years to report
HTF = "1h"             # higher timeframe for trend / regime
EMA_N = 50             # HTF trend EMA
BREAKOUT_N = 20        # LTF breakout lookback
ATR_N = 14
K = 1.5                # stop = K * ATR
R = 2.0                # target = R * risk
RISK_PCT = 0.01        # risk per trade (fraction of capital)
CAPITAL = 100_000
COST_R = 0.05          # fees+slippage per trade, in units of R (tune for your market)
ADX_MIN = 20           # trending threshold


# ---------------- indicators ----------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"],
                      (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14):
    return true_range(df).rolling(n).mean()


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = true_range(df).ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


# ---------------- data prep ----------------
def load(path):
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    tcol = "datetime" if "datetime" in df.columns else df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol])
    return df.set_index(tcol).sort_index()[["open", "high", "low", "close", "volume"]]


def prepare(df15):
    df1h = df15.resample(HTF).agg({"open": "first", "high": "max", "low": "min",
                                   "close": "last", "volume": "sum"}).dropna()

    # HTF values shifted by 1 bar => only completed HTF bars are visible (no look-ahead)
    trend_up = (df1h["close"] > ema(df1h["close"], EMA_N)).shift(1)
    adx1h = adx(df1h).shift(1)
    atr1h = atr(df1h).shift(1)
    atr_pct = (atr1h / df1h["close"].shift(1))
    atr_rank = atr_pct.rolling(500, min_periods=100).rank(pct=True)  # volatility percentile

    d = df15.copy()
    d["trend_up"] = trend_up.reindex(d.index, method="ffill").fillna(False).astype(bool)
    d["adx1h"] = adx1h.reindex(d.index, method="ffill")
    d["atr_rank"] = atr_rank.reindex(d.index, method="ffill")
    d["atr"] = atr(d, ATR_N)
    d["hh"] = d["high"].rolling(BREAKOUT_N).max().shift(1)
    d["signal"] = d["trend_up"] & (d["close"] > d["hh"])
    return d.dropna(subset=["atr", "hh"])


# ---------------- backtest ----------------
def backtest(d):
    rows = []
    idx = d.index
    o, h, l = d["open"].values, d["high"].values, d["low"].values
    atrv, sig = d["atr"].values, d["signal"].values
    n = len(d)
    i = 0
    while i < n - 1:
        if sig[i]:
            entry = o[i + 1]
            risk = K * atrv[i]
            if risk <= 0 or np.isnan(risk):
                i += 1
                continue
            stop, target = entry - risk, entry + R * risk
            exit_p, j = None, None
            for j in range(i + 1, n):
                if l[j] <= stop:            # stop first (conservative); gap-aware fill
                    exit_p = min(stop, o[j]) if o[j] < stop else stop
                    break
                if h[j] >= target:
                    exit_p = target
                    break
            if exit_p is None:
                break
            r_mult = (exit_p - entry) / risk - COST_R
            rows.append({
                "entry_t": idx[i + 1], "exit_t": idx[j],
                "entry": entry, "exit": exit_p, "R": r_mult,
                "adx1h": d["adx1h"].iloc[i], "atr_rank": d["atr_rank"].iloc[i],
            })
            i = j
        i += 1
    t = pd.DataFrame(rows)
    if not t.empty:
        t["pnl"] = t["R"] * CAPITAL * RISK_PCT
    return t


# ---------------- reporting ----------------
def stats(g):
    if g.empty:
        return pd.Series(dtype=float)
    eq = g["R"].cumsum()
    dd = (eq - eq.cummax()).min()
    wins, losses = g.loc[g["R"] > 0, "R"], g.loc[g["R"] <= 0, "R"]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else np.inf
    return pd.Series({
        "trades": len(g),
        "win_rate_%": round(100 * (g["R"] > 0).mean(), 1),
        "avg_R": round(g["R"].mean(), 3),
        "total_R": round(g["R"].sum(), 2),
        "profit_factor": round(pf, 2),
        "max_dd_R": round(dd, 2),
    })


def report(trades, label=""):
    if trades.empty:
        print(f"{label} No trades generated.")
        return trades

    trades = trades.copy()
    trades["year"] = trades["entry_t"].dt.year
    last_years = sorted(trades["year"].unique())[-YEARS:]
    trades = trades[trades["year"].isin(last_years)].copy()

    prefix = f"[{label}] " if label else ""
    print(f"\n{prefix}=== Performance by year ===")
    print(trades.groupby("year", group_keys=False).apply(stats, include_groups=False))

    print(f"\n{prefix}=== Performance by half-year ===")
    trades["half"] = trades["entry_t"].dt.year.astype(str) + "-H" + \
        ((trades["entry_t"].dt.month > 6) + 1).astype(str)
    print(trades.groupby("half", group_keys=False).apply(stats, include_groups=False))

    trades["trend_regime"] = np.where(trades["adx1h"] >= ADX_MIN, "trending (ADX>=%d)" % ADX_MIN, "choppy (ADX<%d)" % ADX_MIN)
    print(f"\n{prefix}=== By trend regime (HTF ADX) ===")
    print(trades.groupby("trend_regime", group_keys=False).apply(stats, include_groups=False))

    trades["vol_regime"] = pd.cut(trades["atr_rank"], [0, 0.33, 0.66, 1.0],
                                  labels=["low vol", "mid vol", "high vol"], include_lowest=True)
    print(f"\n{prefix}=== By volatility regime (ATR percentile) ===")
    print(trades.groupby("vol_regime", observed=True, group_keys=False).apply(stats, include_groups=False))

    print(f"\n{prefix}=== Year x trend regime (avg R / trades) ===")
    pt = trades.pivot_table(index="year", columns="trend_regime", values="R",
                            aggfunc=["mean", "count"]).round(3)
    print(pt)
    return trades


def main(path):
    df15 = load(path)
    d = prepare(df15)
    trades = backtest(d)
    if trades.empty:
        print("No trades generated.")
        return

    trades = report(trades)
    out = "trades_with_regimes.csv"
    trades.to_csv(out, index=False)
    print(f"\nTrade list saved to {out}")
    print("Note: results are in R-multiples after COST_R per trade. Not financial advice.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
