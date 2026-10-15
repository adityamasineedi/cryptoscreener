"""Phase 1 data audit (docs/research_instructions.md). Read-only against Postgres `ohlcv`.

Run from repo root:  backend/.venv/Scripts/python.exe research/p1_audit.py
Writes research/out/p1/*.csv|json and research/out/p1/p1_tables.md (the tables quoted in REPORT.md).
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "scripts" / "strategy_rank" / "multicap"))
from _db import _connect  # noqa: E402  read-only asyncpg session

OUT = ROOT / "research" / "out" / "p1"
UTC = timezone.utc
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
END = datetime(2026, 10, 15, tzinfo=UTC)  # audit cut: last fully closed 1d bar is 2026-10-14
# Core audit universe = the 27 coins used by earlier repo studies (16 discovery + 11 holdout).
CORE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT",
        "DOGEUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "SUIUSDT",
        "AAVEUSDT", "ATOMUSDT", "BCHUSDT", "ETCUSDT", "FILUSDT", "ICPUSDT", "INJUSDT", "RENDERUSDT", "TONUSDT",
        "TRXUSDT", "UNIUSDT"]
MAJORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
COST_RT = 2 * (0.0005 + 0.0003)  # round-trip taker + slippage assumption from the brief (0.16%)


async def fetch_all() -> tuple[pd.DataFrame, dict[tuple[str, str], pd.DataFrame], pd.DataFrame]:
    conn = await _connect()
    try:
        inv = pd.DataFrame([dict(r) for r in await conn.fetch(
            "SELECT symbol, timeframe, count(*) AS n, min(time) AS first, max(time) AS last FROM ohlcv "
            "WHERE timeframe = ANY($1::text[]) GROUP BY 1, 2", list(TF_SEC))])
        liq = pd.DataFrame([dict(r) for r in await conn.fetch(
            "SELECT symbol, date_part('year', time)::int AS year, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY quote_volume) AS med_qv, count(*) AS days "
            "FROM ohlcv WHERE timeframe='1d' AND time < $1 GROUP BY 1, 2", END)])
        data = {}
        for sym in CORE:
            for tf in TF_SEC:
                rows = await conn.fetch(
                    "SELECT time, open, high, low, close, volume, quote_volume, trade_count, source FROM ohlcv "
                    "WHERE symbol=$1 AND timeframe=$2 AND time < $3 ORDER BY time", sym, tf, END)
                df = pd.DataFrame([dict(r) for r in rows])
                if not df.empty:
                    df["time"] = pd.to_datetime(df["time"], utc=True)
                    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
                        df[c] = df[c].astype(float)
                data[(sym, tf)] = df
    finally:
        await conn.close()
    return inv, data, liq


def epoch_s(s: pd.Series) -> pd.Series:
    """UTC timestamps -> integer epoch seconds, independent of the datetime64 unit (pandas 3 uses us)."""
    return (s - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)


def big_moves(df: pd.DataFrame, top: int = 3) -> list[str]:
    lr = np.log(df["close"]).diff().abs()
    idx = lr.nlargest(top).index
    return [f"{df.at[i, 'time']:%Y-%m-%d %H:%M} {np.exp(np.log(df.at[i, 'close'] / df.at[i - 1, 'close'])) - 1:+.0%}"
            for i in idx]


def quality(sym: str, tf: str, df: pd.DataFrame) -> dict:
    sec = TF_SEC[tf]
    if df.empty:
        return dict(symbol=sym, tf=tf, n=0)
    t = (epoch_s(df["time"])).to_numpy()
    off_grid = int((t % sec != 0).sum())
    dups = int(len(t) - len(np.unique(t)))
    d = np.diff(t)
    gaps = d[d > sec] // sec - 1
    expected = (t[-1] - t[0]) // sec + 1
    o, h, l, c, v = (df[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    bad_hl = int(((h < np.maximum(o, c) - 1e-12) | (l > np.minimum(o, c) + 1e-12) | (l <= 0)).sum())
    zero_v = int((v <= 0).sum())
    qv = df["quote_volume"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        vwap = qv / v
    ok = np.isfinite(vwap) & (v > 0)
    vwap_out = int(((vwap[ok] < l[ok] * 0.999) | (vwap[ok] > h[ok] * 1.001)).sum())
    lr = np.abs(np.diff(np.log(c)))
    src = df["source"].value_counts().to_dict()
    return dict(symbol=sym, tf=tf, first=df["time"].iloc[0].isoformat()[:16], last=df["time"].iloc[-1].isoformat()[:16],
                n=len(df), expected=int(expected), missing=int(expected - len(np.unique(t))),
                gap_events=int(len(gaps)), longest_gap_bars=int(gaps.max()) if len(gaps) else 0,
                off_grid=off_grid, duplicates=dups, bad_ohlc=bad_hl, zero_volume=zero_v,
                qv_null=int(np.isnan(qv).sum()), vwap_outside_range=vwap_out,
                ret_abs_gt_20pct=int((lr > np.log(1.2)).sum()), max_abs_logret=round(float(lr.max()), 4),
                sources=json.dumps(src))


def gap_days(df: pd.DataFrame, sec: int) -> dict[str, int]:
    t = (epoch_s(df["time"])).to_numpy()
    have = set(t.tolist())
    miss = [x for x in range(int(t[0]), int(t[-1]) + 1, sec) if x not in have]
    out: dict[str, int] = {}
    for x in miss:
        k = datetime.fromtimestamp(x, UTC).date().isoformat()
        out[k] = out.get(k, 0) + 1
    return out


def align(lo: pd.DataFrame, hi: pd.DataFrame, hi_sec: int, lo_sec: int, bad_times: list | None = None) -> dict:
    """Aggregate lower-TF bars into higher-TF buckets (open-time convention) and compare.

    Mismatched bucket open times (epoch s) are appended to bad_times when given."""
    if lo.empty or hi.empty:
        return {}
    k = hi_sec // lo_sec
    g = lo.assign(b=(epoch_s(lo["time"])) // hi_sec * hi_sec).groupby("b")
    agg = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "cnt": g.size()})
    agg = agg[agg["cnt"] == k]
    h = hi.set_index((epoch_s(hi["time"])))
    j = agg.join(h[["open", "high", "low", "close", "volume"]], rsuffix="_hi", how="inner")
    out = dict(compared=len(j))
    for c in ("open", "high", "low", "close"):
        out[f"{c}_mismatch"] = int((np.abs(j[c] / j[f"{c}_hi"] - 1) > 1e-7).sum())
    out["volume_mismatch_gt_0.1pct"] = int((np.abs(j["volume"] / j["volume_hi"] - 1) > 1e-3).sum())
    out["volume_max_rel_diff"] = round(float(np.abs(j["volume"] / j["volume_hi"] - 1).max()), 6) if len(j) else None
    if bad_times is not None:
        px = np.zeros(len(j), dtype=bool)
        for c in ("open", "high", "low", "close"):
            px |= (np.abs(j[c] / j[f"{c}_hi"] - 1) > 1e-7).to_numpy()
        vol = (np.abs(j["volume"] / j["volume_hi"] - 1) > 1e-3).to_numpy()
        bad_times.extend((int(t), "price" if p else "volume") for t, p, v in zip(j.index, px, vol) if p or v)
    return out


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = np.maximum(df["high"] - df["low"], np.maximum((df["high"] - pc).abs(), (df["low"] - pc).abs()))
    return tr.rolling(n).mean() / df["close"]


def swings_per_month(df: pd.DataFrame, k: int = 3) -> float:
    """Fractal pivots with k bars each side (confirmed k bars after the pivot)."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(h)
    cnt = 0
    for i in range(k, n - k):
        if h[i] == h[i - k:i + k + 1].max() and (h[i - k:i] < h[i]).all():
            cnt += 1
        if l[i] == l[i - k:i + k + 1].min() and (l[i - k:i] > l[i]).all():
            cnt += 1
    months = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 30.44
    return cnt / months


def daily_from_4h(h4: pd.DataFrame) -> pd.DataFrame:
    """UTC daily closes rebuilt from complete days of 4h bars (4h history is longer than stored 1d)."""
    g = h4.assign(day=h4["time"].dt.floor("D")).groupby("day")
    d = pd.DataFrame({"close": g["close"].last(), "cnt": g.size()})
    d = d[d["cnt"] == 6]
    return pd.DataFrame({"time": d.index, "close": d["close"].values})


def regime_daily(d1: pd.DataFrame, look: int = 30, vol_n: int = 90, thr: float = 1.0) -> pd.DataFrame:
    """Causal volatility-normalised trend score on closed daily bars.

    z_t = ln(C_t / C_{t-look}) / (sd(daily log ret over last vol_n days) * sqrt(look)).
    bull if z >= thr, bear if z <= -thr, else sideways. Label for day t is usable from the open of day t+1.
    """
    c = d1.set_index("time")["close"]
    r = np.log(c).diff()
    z = np.log(c / c.shift(look)) / (r.rolling(vol_n).std() * np.sqrt(look))
    lab = pd.Series(np.where(z >= thr, "bull", np.where(z <= -thr, "bear", "side")), index=c.index)
    lab[z.isna()] = "na"
    return pd.DataFrame({"close": c, "z": z, "regime": lab})


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(x) else str(x) for x in r.tolist()) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    inv, data, liq = asyncio.run(fetch_all())
    md: list[str] = []

    # ---- 1. inventory of all symbols
    inv["first"] = pd.to_datetime(inv["first"], utc=True)
    inv["last"] = pd.to_datetime(inv["last"], utc=True)
    inv.to_csv(OUT / "inventory_all.csv", index=False)
    piv = inv.pivot(index="symbol", columns="timeframe", values="first")
    lastp = inv.pivot(index="symbol", columns="timeframe", values="last")
    s = []
    for tf in TF_SEC:
        f = piv[tf].dropna()
        la = lastp[tf].dropna()
        s.append(dict(tf=tf, symbols=len(f), first_min=f.min().date(), first_median=f.median().date(),
                      symbols_from_start=int((f <= f.min() + pd.Timedelta(days=2)).sum()),
                      last_before_2026_10_01=int((la < pd.Timestamp("2026-10-01", tz="UTC")).sum())))
    md.append("### All symbols in `ohlcv` (USDT pairs)\n\n" + md_table(pd.DataFrame(s)))
    stale = lastp["1d"].dropna()
    stale = stale[stale < pd.Timestamp("2026-10-01", tz="UTC")].sort_values()
    md.append(f"Symbols whose last 1d bar is before 2026-10-01 (delisted or stopped): {len(stale)} — "
              + ", ".join(f"{k} ({v.date()})" for k, v in stale.items()))

    hist = []
    for cut in ("2022-10-20", "2023-01-05", "2024-01-05", "2024-10-10", "2025-10-01"):
        ts = pd.Timestamp(cut, tz="UTC")
        hist.append(dict(history_from_on_or_before=cut, **{f"n_{tf}": int((piv[tf].dropna() <= ts).sum()) for tf in TF_SEC}))
    md.append("### How many symbols have history from a given date (first bar on or before)\n\n"
              + md_table(pd.DataFrame(hist)))

    # ---- 2. liquidity ranking per year (median daily quote volume)
    liq.to_csv(OUT / "liquidity_by_year.csv", index=False)
    rows = []
    for y in sorted(liq["year"].unique()):
        sub = liq[(liq["year"] == y) & (liq["days"] >= 60)].sort_values("med_qv", ascending=False).head(30)
        rows.append(dict(year=int(y), top30=", ".join(f"{r.symbol.replace('USDT','')}({r.med_qv/1e6:.0f})"
                                                       for r in sub.itertuples())))
    md.append("### Top 30 by median daily quote volume, USD millions (symbols with ≥60 days that year)\n\n"
              + md_table(pd.DataFrame(rows)))

    # ---- 3. per-coin quality
    q = pd.DataFrame([quality(sym, tf, data[(sym, tf)]) for sym in CORE for tf in TF_SEC])
    q.to_csv(OUT / "quality_core.csv", index=False)
    md.append("### Core-27 quality by timeframe (window: first bar → 2026-10-14 close)\n\n" + md_table(
        q.groupby("tf", sort=False).agg(symbols=("symbol", "count"), bars=("n", "sum"), missing=("missing", "sum"),
                                        gap_events=("gap_events", "sum"), longest_gap_bars=("longest_gap_bars", "max"),
                                        off_grid=("off_grid", "sum"), duplicates=("duplicates", "sum"),
                                        bad_ohlc=("bad_ohlc", "sum"), zero_volume=("zero_volume", "sum"),
                                        qv_null=("qv_null", "sum"), vwap_outside_range=("vwap_outside_range", "sum"),
                                        ret_gt_20pct=("ret_abs_gt_20pct", "sum")).reset_index()))
    md.append("### Core-27 first bar and missing bars per coin\n\n" + md_table(
        q.pivot(index="symbol", columns="tf", values="first").join(
            q.pivot(index="symbol", columns="tf", values="missing"), rsuffix="_missing")
        .reindex(CORE).reset_index()[["symbol", "15m", "1h", "4h", "1d", "15m_missing", "1h_missing", "4h_missing",
                                      "1d_missing"]]))
    gd: dict[str, dict[str, int]] = {}
    for sym in CORE:
        for tf in ("15m", "1h"):
            for day, k in gap_days(data[(sym, tf)], TF_SEC[tf]).items():
                gd.setdefault(f"{tf} {day}", {})[sym] = k
    gdf = pd.DataFrame([dict(tf_day=k, coins=len(v), bars=sum(v.values()), examples=", ".join(list(v)[:5]))
                        for k, v in gd.items()]).sort_values("bars", ascending=False) if gd else pd.DataFrame()
    gdf.to_csv(OUT / "gap_days_core.csv", index=False)
    if not gdf.empty:
        md.append("### Largest missing-bar days (15m/1h, core-27)\n\n" + md_table(gdf.head(15)))

    # ---- 4. timeframe alignment
    al = []
    bad_rows = []
    for sym in CORE:
        for lo, hi in (("15m", "1h"), ("1h", "4h"), ("4h", "1d")):
            bt: list = []
            r = align(data[(sym, lo)], data[(sym, hi)], TF_SEC[hi], TF_SEC[lo], bt)
            bad_rows += [dict(symbol=sym, pair=f"{lo}->{hi}", day=datetime.fromtimestamp(t, UTC).date().isoformat(),
                              kind=k) for t, k in bt]
            if r:
                al.append(dict(symbol=sym, pair=f"{lo}->{hi}", **r))
    al = pd.DataFrame(al)
    al.to_csv(OUT / "alignment_core.csv", index=False)
    md.append("### Timeframe alignment: lower TF aggregated vs stored higher TF (core-27, complete buckets)\n\n"
              + md_table(al.groupby("pair", sort=False).agg(
                  compared=("compared", "sum"), open_mismatch=("open_mismatch", "sum"),
                  high_mismatch=("high_mismatch", "sum"), low_mismatch=("low_mismatch", "sum"),
                  close_mismatch=("close_mismatch", "sum"), vol_mismatch_gt_01pct=("volume_mismatch_gt_0.1pct", "sum"),
                  worst_vol_rel_diff=("volume_max_rel_diff", "max")).reset_index()))
    bad = al[(al[["open_mismatch", "high_mismatch", "low_mismatch", "close_mismatch"]].sum(axis=1) > 0)]
    if not bad.empty:
        md.append("Pairs with any price mismatch:\n\n" + md_table(bad))
    bd = pd.DataFrame(bad_rows)
    bd.to_csv(OUT / "alignment_mismatch_detail.csv", index=False)
    if not bd.empty:
        md.append("Mismatched buckets grouped by UTC day (all pairs, core-27):\n\n" + md_table(
            bd.groupby(["day", "kind"]).agg(buckets=("symbol", "size"), coins=("symbol", "nunique"),
                                            pairs=("pair", lambda x: ",".join(sorted(set(x))))).reset_index()
            .sort_values("buckets", ascending=False).head(20)))
    zv = q[q["zero_volume"] > 0][["symbol", "tf", "n", "zero_volume"]]
    if not zv.empty:
        md.append("### Zero-volume bars\n\n" + md_table(zv))
    md.append("### Largest close-to-close moves (1d and 1h, core-27), for outlier inspection\n\n" + md_table(
        pd.DataFrame([dict(symbol=sym, top_1d=" / ".join(big_moves(data[(sym, "1d")])),
                           top_1h=" / ".join(big_moves(data[(sym, "1h")]))) for sym in CORE])))

    # ---- 5. volume / perp evidence
    vol = []
    for sym in MAJORS:
        d = data[(sym, "1d")]
        d = d[d["time"] >= pd.Timestamp("2025-10-01", tz="UTC")]
        vol.append(dict(symbol=sym, median_daily_base_volume=round(float(d["volume"].median()), 0),
                        median_daily_quote_usd_m=round(float(d["quote_volume"].median()) / 1e6, 0),
                        trade_count_null_pct=round(float(data[(sym, '1h')]["trade_count"].isna().mean()) * 100, 1)))
    md.append("### Volume level, last 12 months (perp-vs-spot evidence)\n\n" + md_table(pd.DataFrame(vol)))
    thousand = sorted(x for x in inv["symbol"].unique() if x.startswith("1000") or x.startswith("1M"))
    md.append("Perp-only contract tickers present: " + (", ".join(thousand) if thousand else "none"))

    # ---- 6. volatility / cost feasibility and structure density
    fe = []
    for sym in CORE:
        row = dict(symbol=sym)
        for tf in TF_SEC:
            d = data[(sym, tf)]
            d = d[d["time"] >= pd.Timestamp("2023-01-01", tz="UTC")]
            a = float(atr_pct(d).median())
            row[f"atr%_{tf}"] = round(a * 100, 3)
            row[f"cost/ATR_{tf}"] = round(COST_RT / a, 2)
        for tf in ("15m", "1h", "4h"):
            d = data[(sym, tf)]
            row[f"swings/mo_{tf}"] = round(swings_per_month(d[d["time"] >= pd.Timestamp("2023-01-01", tz="UTC")]), 0)
        fe.append(row)
    fe = pd.DataFrame(fe)
    fe.to_csv(OUT / "feasibility_core.csv", index=False)
    md.append("### Median ATR(14) as % of price and round-trip cost (0.16%) as a fraction of 1 ATR, 2023-01→2026-10; "
              "confirmed 3-bar fractal swings per month\n\n" + md_table(fe))

    # ---- 7. regime map
    regs = {}
    for sym in MAJORS:
        regs[sym] = regime_daily(daily_from_4h(data[(sym, "4h")]))
    basket_close = np.exp(pd.concat([np.log(regs[s]["close"]) for s in MAJORS], axis=1).dropna().diff().mean(axis=1)
                          .cumsum())
    regs["BASKET5"] = regime_daily(pd.DataFrame({"time": basket_close.index, "close": basket_close.values}))
    allreg = pd.concat({k: v["regime"] for k, v in regs.items()}, axis=1)
    allreg.to_csv(OUT / "regime_daily_majors.csv")
    m = allreg.copy()
    m.index = m.index.tz_convert(None).to_period("M")
    monthly = m.groupby(level=0).agg(lambda s: s.dropna().value_counts().idxmax() if s.notna().any() else "-")
    btc = regs["BTCUSDT"]["close"]
    mret = (btc.groupby(btc.index.tz_convert(None).to_period("M")).last()
            / btc.groupby(btc.index.tz_convert(None).to_period("M")).first() - 1)
    monthly["BTC_month_ret%"] = (mret * 100).round(1)
    monthly.index = monthly.index.astype(str)
    monthly.to_csv(OUT / "regime_monthly_majors.csv")
    md.append("### Regime map — monthly majority label (UTC daily closes rebuilt from 4h; daily z = 30d log return / (90d daily σ·√30); "
              "bull z≥1, bear z≤−1, else side)\n\n" + md_table(monthly.reset_index().rename(columns={"time": "month"})))
    hy = allreg.copy()
    hy["half"] = [f"{t.year}H{1 if t.month <= 6 else 2}" for t in hy.index]
    share = []
    for h, g in hy.groupby("half"):
        for sym in ("BTCUSDT", "BASKET5"):
            vc = g[sym].value_counts(normalize=True)
            share.append(dict(half=h, series=sym, bull=round(vc.get("bull", 0) * 100), side=round(vc.get("side", 0) * 100),
                              bear=round(vc.get("bear", 0) * 100), na=round(vc.get("na", 0) * 100)))
    sh = pd.DataFrame(share)
    sh.to_csv(OUT / "regime_share_halfyear.csv", index=False)
    md.append("### Regime share by half-year, % of days\n\n" + md_table(sh))

    (OUT / "p1_tables.md").write_text("\n\n".join(md) + "\n", encoding="utf-8")
    print("\n\n".join(md))


if __name__ == "__main__":
    main()
