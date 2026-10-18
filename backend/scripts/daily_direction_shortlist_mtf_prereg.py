"""Pre-registered multi-timeframe directional screen: 114 standalone evaluations. Research only.

Scope: 19 canonical books x 2 timeframes (4h, 1h) x 3 standalone symbols (BTCUSDT, ETHUSDT, SOLUSDT) = 114
strategy-timeframe-symbol evaluations, each with three separate $10,000-flat test folds. No shared account, no
BTC-plus-shared acceptance rule, no ranking or selection of coins. Nothing here is production, tradable, paper or
live. No orders.

Commands (the run needs the user's explicit RUN APPROVED and the owning-runner designation):
  python backend/scripts/daily_direction_shortlist_mtf_prereg.py --self-test     synthetic checks only
  python backend/scripts/daily_direction_shortlist_mtf_prereg.py --emit-static   pre-run documents, no data
  python backend/scripts/daily_direction_shortlist_mtf_prereg.py                 the run (once; locked)
Writes only under backend/reports/daily_direction_shortlist_mtf_prereg/. Nothing at or after 2026-07-15 is
loaded, computed, plotted, cached or referenced.

Reuse and adapters:
- Indicator primitives come unchanged from the locked daily screen script (sha256 checked before import).
- Local rule adapters: book 6 same-timeframe SMA30 stage, book 13 completed higher-timeframe cloud, book 19
  prior-365 volatility cut with WARMUP_BLOCKED.
- Local ledger engine `simulate_ledger`: the daily engine credits funding only at exit and stamps stop exits at
  mid-bar, which cannot express settlement-time funding posting or unresolved stop-bar settlements without
  modifying it. The local engine keeps the identical bar chronology; with funding off it reproduces the frozen
  daily `simulate` trade for trade (parity test in --self-test). The daily script is never modified.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

SCRIPT_PATH = Path(__file__).resolve()
BACKEND = SCRIPT_PATH.parents[1]
OUT_DIR = BACKEND / "reports" / "daily_direction_shortlist_mtf_prereg"
DAILY_SCRIPT = BACKEND / "scripts" / "daily_direction_shortlist_prereg.py"
DAILY_SCRIPT_SHA256 = "a90b046b187e0270ed3cbadec5ec5df913f5afac0147d33b4c35b8322a1ca1e8"
ENV_FILE = Path("D:/cryptoscreener/.env")
RUN_COMMAND = "D:/cryptoscreener/backend/.venv/Scripts/python.exe backend/scripts/daily_direction_shortlist_mtf_prereg.py"


def _load_daily():
    got = hashlib.sha256(DAILY_SCRIPT.read_bytes()).hexdigest()
    if got != DAILY_SCRIPT_SHA256:
        raise SystemExit(f"daily screen script hash mismatch: {got} (expected {DAILY_SCRIPT_SHA256})")
    spec = importlib.util.spec_from_file_location("daily_screen_frozen", DAILY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load_daily()
prev, sma, ema, roll_max, atr_wilder = D.prev, D.sma, D.ema, D.roll_max, D.atr_wilder
adx_dmi, supertrend_dir, ichimoku_cloud = D.adx_dmi, D.supertrend_dir, D.ichimoku_cloud
_sign_vs, _event_regime, _donchian = D._sign_vs, D._event_regime, D._donchian
swing_structure_state = D.swing_structure_state

# ---------------------------------------------------------------------------------------------
# Frozen configuration
# ---------------------------------------------------------------------------------------------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAMES = ["4h", "1h"]
BAR = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
HTF_FOR = {"4h": "1d", "1h": "4h"}
KLINE_ROOT = D.KLINE_ROOT
HISTORY_START = pd.Timestamp("2019-01-01", tz="UTC")
SEALED_START = pd.Timestamp("2026-07-15", tz="UTC")      # no timestamp at/after this is returned
_CUT_S = int(SEALED_START.timestamp())
START_EQUITY, RISK, RISK_LOW, LEV_CAP, FEE = 10_000.0, 0.005, 0.0025, 2.0, 0.0004
STOP_ATR, ATR_N = 2.5, 14
SLIP = {"BTCUSDT": 0.0005, "ETHUSDT": 0.0005}
SLIP_OTHER = 0.0015
PF_MIN = 1.10
RV_BARS, RV_PRIOR_OBS, RV_PCT = 20, 365, 0.70
N_EVALUATIONS = 19 * 2 * 3
WARNING = ("These are 114 correlated strategy–timeframe–symbol historical screen evaluations, not 114 independent "
           "confirmations. The development history includes previously inspected daily results over the same dates. "
           "A complete-data SCREEN_HIT is a research screen hit, not a confirmed edge, coin recommendation, "
           "allocation decision or trading authorization.")

FOLDS = [
    dict(fold=1, train_end=pd.Timestamp("2021-12-31", tz="UTC"),
         test_start=pd.Timestamp("2022-01-01", tz="UTC"), test_end=pd.Timestamp("2022-12-31", tz="UTC")),
    dict(fold=2, train_end=pd.Timestamp("2022-12-31", tz="UTC"),
         test_start=pd.Timestamp("2023-01-01", tz="UTC"), test_end=pd.Timestamp("2024-12-31", tz="UTC")),
    dict(fold=3, train_end=pd.Timestamp("2024-12-31", tz="UTC"),
         test_start=pd.Timestamp("2025-01-01", tz="UTC"), test_end=pd.Timestamp("2026-07-14", tz="UTC")),
]

# Canonical registry: id, name, kind, rule reference, timeframe dependency.
BOOKS = [
    ("1", "SMA200", "state", "close[t] vs prev(SMA200)", "tested tf"),
    ("2", "EMA200", "state", "close[t] vs prev(EMA200, adjust=False)", "tested tf"),
    ("3", "SMA50", "state", "close[t] vs prev(SMA50)", "tested tf"),
    ("4", "MA slope", "state", "close[t] vs m=prev(SMA200); m vs prev(m,20)", "tested tf"),
    ("5", "Golden/death cross", "event", "cross of prev(SMA50) vs prev(SMA200)", "tested tf"),
    ("6", "Weinstein SMA30, same timeframe", "state", "LOCAL weinstein_stage_tf: close[t] vs prev(SMA30); slope vs "
     "4 bars earlier", "tested tf own bars (no weekly bar)"),
    ("7", "Donchian 20/10", "event", "close[t] vs prior 20 high/low; exit prior 10", "tested tf"),
    ("8", "Donchian 55/20", "event", "close[t] vs prior 55 high/low; exit prior 20", "tested tf"),
    ("9", "Donchian 20 gated by book 6", "event", "book 7 trigger kept only when equal to book 6 state; book 7 "
     "exits", "tested tf"),
    ("10", "Supertrend 10/3", "event", "daily supertrend_dir: flip close[t] vs fu[t-1]/fl[t-1]", "tested tf"),
    ("11", "ADX/DMI 14", "state", "prev(ADX,+DI,-DI 14 Wilder); ADX > 25", "tested tf"),
    ("12", "Ichimoku 9/26/52", "state", "close[t] vs cloud of spans at t-26", "tested tf"),
    ("13", "Ichimoku plus completed higher-timeframe cloud", "state", "book 12 state x LOCAL htf_cloud_state",
     "4h -> completed 1d; 1h -> completed 4h"),
    ("14", "ROC 90", "state", "sign(close[t]/prev(close,90)-1)", "tested tf"),
    ("15", "ROC 30", "state", "sign(close[t]/prev(close,30)-1)", "tested tf"),
    ("16", "252-bar closing-high breakout", "event", "close[t] >= prev(max 252 closes); exit close[t] < prev(SMA200)"
     "; long only", "tested tf"),
    ("17", "Available-history closing-high breakout", "event", "close[t] >= prev(cumulative max close); exit "
     "close[t] < prev(SMA200); long only", "tested tf (loaded series)"),
    ("18", "Swing structure 5", "state", "daily swing_structure_state k=5 (pivots usable from t-1)", "tested tf"),
    ("19", "Book 4 side, Donchian 20 trigger", "event", "book 4 side; Donchian20 trigger in side; exit Donchian10, "
     "stop, or loss of side; LOCAL rv_cut_prior365 with WARMUP_BLOCKED; no funding/OI veto", "tested tf"),
]
BOOK_IDS = [b[0] for b in BOOKS]
BOOK_NAME = {b[0]: b[1] for b in BOOKS}
BOOK_KIND = {b[0]: b[2] for b in BOOKS}
BOOK_RULE = {b[0]: b[3] for b in BOOKS}
BOOK_TF_DEP = {b[0]: b[4] for b in BOOKS}

RAW_STATUS = {
    "SCREEN_HIT": "pooled net > 0, pooled PF > 1.10, and at least two of three folds strictly net positive",
    "SCREEN_FAIL": "otherwise",
    "NOT_TESTED": "the evaluation cannot be performed (series absent or DATA_INCOMPLETE)"}
PROTOCOL_STATUS = {
    "COMPLETE": "eligible from the first bar of all three folds, no required price gap, funding coverage COMPLETE "
                "and funding timing RESOLVED",
    "PARTIAL": "eligibility starts after a fold has begun (incl. book 19 WARMUP_BLOCKED), funding coverage PARTIAL "
               "(missing records or unmarked settlement) or MISSING (no series), or any timing-unresolved "
               "settlement; price bars exist",
    "DATA_INCOMPLETE": "a required price gap, a missing/short/late required series (incl. the book 13 higher "
                       "timeframe), or unverified placeholder provenance for the symbol",
    "UNRESOLVED": "funding coverage UNRESOLVED: the expected settlement schedule is not documented locally (or is "
                  "inconsistent) for some exposure; or an unrecognised label"}

KLINE_SCHEMA = D.KLINE_SCHEMA
PG_COLUMNS = ["time", "open", "high", "low", "close", "volume", "quote_volume", "trade_count"]
PG_TYPES = {"time": "timestamp with time zone", "symbol": "text", "timeframe": "text", "source": "text",
            **{k: "double precision" for k in ("open", "high", "low", "close", "volume", "quote_volume")}}
PG_SOURCE = "binance_rest"          # the app's /fapi/v1/klines (Binance USDT-M perpetual) writer
ARCHIVE_EQ_RTOL = 1e-9             # float tolerance for exact equality of summed decimal archive values
EXPECTED_LAST = {"1h": pd.Timestamp("2026-07-14 23:00", tz="UTC"), "4h": pd.Timestamp("2026-07-14 20:00", tz="UTC"),
                 "1d": pd.Timestamp("2026-07-14", tz="UTC")}


def slip_for(symbol: str) -> float:
    return SLIP.get(symbol, SLIP_OTHER)


# ---------------------------------------------------------------------------------------------
# Data: local stored klines only; the sealed period is filtered out before collect.
# Logical exclusion: every price and funding scan applies time < SEALED_START before collect,
# so a sealed timestamp is not a row of the frame returned to the research process.
# Storage limitation: that predicate does not establish physical non-access. A Parquet row
# group whose column statistics overlap the cutoff can still be read, including bytes of
# excluded rows. This script does not claim those bytes were unread.
# ---------------------------------------------------------------------------------------------
def assert_sealed(index: pd.DatetimeIndex, what: str) -> None:
    if len(index) and index.max() >= SEALED_START:
        raise SystemExit(f"sealed boundary breach in {what}: {index.max()}")


def check_frame(df: pd.DataFrame, what: str) -> None:
    """Hard errors: not UTC, duplicates, unsorted, NaN prices, sealed breach. Gaps are NOT errors here: they are
    measured by missing_bars and make the affected evaluations DATA_INCOMPLETE."""
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex) or str(idx.tz) != "UTC":
        raise SystemExit(f"{what}: index is not a UTC DatetimeIndex")
    if idx.has_duplicates:
        raise SystemExit(f"{what}: duplicate timestamps")
    if not idx.is_monotonic_increasing:
        raise SystemExit(f"{what}: timestamps not sorted")
    if df[["open", "high", "low", "close"]].isna().any().any():
        raise SystemExit(f"{what}: NaN prices")
    assert_sealed(idx, what)


def missing_bars(index: pd.DatetimeIndex, step: pd.Timedelta, last_expected: pd.Timestamp) -> pd.DatetimeIndex:
    """Grid points absent from the series, from its first bar through the expected last closed bar. Compared in
    nanoseconds explicitly (pandas 3 keeps the cache's datetime64[s] unit)."""
    if not len(index):
        return pd.DatetimeIndex([], tz="UTC")
    grid = pd.date_range(index[0], last_expected, freq=step, tz="UTC").as_unit("ns")
    return grid.difference(index.as_unit("ns"))


def load_hourly_raw(symbol: str) -> pd.DataFrame | None:
    path = KLINE_ROOT / "1h" / f"{symbol}.parquet"
    if not path.exists():
        return None
    D._check_schema(path, KLINE_SCHEMA)
    lo = int(HISTORY_START.timestamp())
    df = pl.scan_parquet(path).filter((pl.col("time") >= lo) & (pl.col("time") < _CUT_S)).collect()
    if df["time"].is_duplicated().any():
        raise SystemExit(f"{symbol} 1h: duplicate timestamps in the stored file")
    if not df["time"].is_sorted():
        raise SystemExit(f"{symbol} 1h: timestamps are not sorted in the stored file")
    out = D._to_pandas(df)
    out.index = pd.to_datetime(out.pop("time"), unit="s", utc=True)
    assert_sealed(out.index, f"{symbol} 1h")
    return out


def placeholder_bars(h: pd.DataFrame) -> pd.DatetimeIndex:
    """Archive placeholder bars: open = high = low = close and zero volume."""
    m = (h["volume"] == 0) & (h["open"] == h["close"]) & (h["high"] == h["low"]) & (h["open"] == h["high"])
    return h.index[m.to_numpy()]


def fetch_postgres_bars(symbol: str, times: pd.DatetimeIndex) -> pd.DataFrame:
    """The stored Postgres 1h bars (Binance USDT-M perpetual, source binance_rest) for exactly these timestamps."""
    import asyncio

    import asyncpg

    assert_sealed(times, f"{symbol} replacement request")
    urls = [ln.split("=", 1)[1].strip() for ln in ENV_FILE.read_text(encoding="utf-8").splitlines()
            if ln.startswith("DATABASE_URL=")] if ENV_FILE.exists() else []
    if not urls:
        raise SystemExit(f"DATABASE_URL not found in {ENV_FILE}; cannot fetch replacement bars")
    url = urls[0].replace("postgresql+asyncpg", "postgresql")

    async def run():
        c = await asyncpg.connect(url)
        try:
            types = {r[0]: r[1] for r in await c.fetch(
                "select column_name, data_type from information_schema.columns "
                "where table_schema='public' and table_name='ohlcv'")}
            bad = {k: (types.get(k), v) for k, v in PG_TYPES.items() if types.get(k) != v}
            if bad or "trade_count" not in types:
                raise SystemExit(f"Postgres ohlcv schema mismatch: {bad or 'trade_count missing'}")
            return await c.fetch(
                "select time, open, high, low, close, volume, quote_volume, trade_count from public.ohlcv "
                "where symbol=$1 and timeframe='1h' and source=$3 and time = any($2::timestamptz[]) order by time",
                symbol, [t.to_pydatetime() for t in times], PG_SOURCE)
        finally:
            await c.close()

    rows = asyncio.run(run())
    df = pd.DataFrame([dict(r) for r in rows], columns=PG_COLUMNS)
    if len(df) != len(times):
        raise SystemExit(f"{symbol}: Postgres has {len(df)} of {len(times)} replacement bars (source {PG_SOURCE})")
    df.index = pd.to_datetime(df.pop("time"), utc=True)
    assert_sealed(df.index, f"{symbol} replacement result")
    if set(df.index) != set(times):
        raise SystemExit(f"{symbol}: Postgres timestamps are not the requested open times")
    return df.astype(float)


PROVENANCE_CHAIN = (
    "Postgres row: app/ingestion/binance_rest.py futures_klines -> GET {BINANCE_FUTURES_REST=https://fapi.binance.com}"
    "/fapi/v1/klines (Binance USDT-M futures); app/ingestion/klines.py normalize_rest_kline: symbol=upper(request "
    "symbol), open_time=UTC from array[0] ms, open/high/low/close=array[1..4], volume=array[5], quote_volume=array[7], "
    "source='binance_rest'; app/services/ohlcv_store.py writer: time=open_time, volume=volume, quote_volume=quote_volume, "
    "source=source. Mapping unchanged since the first commit (898e6f3, 2026-10-02); the 2026-10-03 majors 1h/4h/15m "
    "backfill (_backfill_majors_htf.json, commit e3cc5fcc, period 2020-10-01..2026-10-03, DO NOTHING inserts) used it. "
    "Parquet row: regime-book data_vision.py reads data.binance.vision USDT-M kline CSVs with columns open_time, open, "
    "high, low, close, volume, close_time, quote_volume, ... (the same Binance kline field order: volume = field 5, "
    "quote_volume = field 7). Both stores therefore hold the same Binance kline field in `volume`. Residual: a row "
    "written before 2026-10-02 would predate the repository. As additional consistency evidence (not proof that "
    "both stores are independently correct), the run requires the repaired hourly bars to aggregate exactly to the "
    "cache's native data.binance.vision 4h archive bar for that bucket (OHLC and summed volume), and records the "
    "compared values.")


def native_4h_bar(symbol: str, bucket: pd.Timestamp) -> dict | None:
    """The cache's native data.binance.vision 4h bar opening at `bucket`, only if the whole bar [bucket, bucket+4h)
    lies before the sealed start; that bound is part of the scan predicate before collect. None otherwise."""
    path = KLINE_ROOT / "4h" / f"{symbol}.parquet"
    t = int(bucket.timestamp())
    if t + 4 * 3600 > _CUT_S or not path.exists():
        return None
    D._check_schema(path, KLINE_SCHEMA)
    df = (pl.scan_parquet(path)
          .filter((pl.col("time") == t) & (pl.col("time") + 4 * 3600 <= _CUT_S))
          .collect())
    if df.height != 1:
        return None
    return {k: float(df[k][0]) for k in ("open", "high", "low", "close", "volume", "quote_volume")}


def _close_rel(a: float, b: float) -> bool:
    return abs(a - b) <= ARCHIVE_EQ_RTOL * max(abs(a), abs(b), 1.0)


def replace_placeholders(h: pd.DataFrame, symbol: str, fetch=None, archive=None):
    """In-memory repair of placeholder 1h bars from the stored Postgres bar. Gates: exact open time, row present
    for this symbol/timeframe/source, finite OHLCV, OHLC consistency, volume >= 0, and the archive cross-check:
    the repaired hourly bars of the 4h bucket must aggregate exactly to the native 4h archive bar (open, high, low,
    close, summed volume). Neighbour differences and volume x price / quote_volume are logged descriptively only.
    Neither store is written. Returns (frame, repair rows, verified, reason); when not verified, the original
    frame is returned and the symbol's evaluations become DATA_INCOMPLETE (PROVENANCE_UNVERIFIED)."""
    ph = placeholder_bars(h)
    if not len(ph):
        return h, [], True, ""
    rep_ = (fetch or fetch_postgres_bars)(symbol, ph)        # schema/type mismatch -> hard error inside
    archive = archive or native_4h_bar
    out = h.copy()
    log, problems = [], []
    for t in ph:
        rec = dict(symbol=symbol, time=str(t), source=f"Postgres public.ohlcv 1h, source {PG_SOURCE}",
                   provenance_chain=PROVENANCE_CHAIN,
                   **{f"parquet_{k}": float(h.at[t, k]) for k in ("open", "high", "low", "close", "volume")})
        if t not in rep_.index:
            problems.append(f"{t}: no Postgres bar with that exact open time")
            log.append({**rec, "status": "provenance_unverified", "reason": problems[-1]})
            continue
        r = rep_.loc[t]
        rec.update({f"postgres_{k}": float(r[k]) for k in ("open", "high", "low", "close", "volume")},
                   postgres_quote_volume=float(r["quote_volume"]))
        i = h.index.get_loc(t)
        tp = (r["high"] + r["low"] + r["close"]) / 3.0
        rec.update(prev_close=float(h["close"].iloc[i - 1]) if i > 0 else float("nan"),
                   next_open=float(h["open"].iloc[i + 1]) if i + 1 < len(h) else float("nan"),
                   volume_x_typical_over_quote=(float(r["volume"] * tp / r["quote_volume"])
                                                if r["quote_volume"] else float("nan")))
        vals = [r[k] for k in ("open", "high", "low", "close", "volume")]
        if not np.isfinite(vals).all():
            problems.append(f"{t}: non-finite replacement values")
        elif not (r["volume"] >= 0 and r["high"] >= max(r["open"], r["close"])
                  and r["low"] <= min(r["open"], r["close"]) and r["high"] >= r["low"]):
            problems.append(f"{t}: replacement fails OHLC/volume consistency")
        else:
            for k in ("open", "high", "low", "close", "volume", "quote_volume"):
                out.at[t, k] = float(r[k])
            log.append({**rec, "status": "replaced_pending_archive_check", "reason": ""})
            continue
        log.append({**rec, "status": "provenance_unverified", "reason": problems[-1]})
    compared = {}
    for bucket in sorted({t.floor("4h") for t in ph}):
        hours = out[(out.index >= bucket) & (out.index < bucket + pd.Timedelta(hours=4))]
        a = archive(symbol, bucket)
        cmp = dict(archive_bucket=str(bucket), archive_tolerance_rel=ARCHIVE_EQ_RTOL)
        if len(hours) != 4 or a is None:
            problems.append(f"{bucket}: native 4h archive bar or a full hourly bucket unavailable")
            compared[bucket] = {**cmp, "archive_check": "unavailable"}
            continue
        agg = dict(open=hours["open"].iloc[0], high=hours["high"].max(), low=hours["low"].min(),
                   close=hours["close"].iloc[-1], volume=hours["volume"].sum())
        bad = [k for k in agg if not _close_rel(float(agg[k]), a[k])]
        compared[bucket] = {**cmp, **{f"archive_{k}": a[k] for k in agg},
                            **{f"repaired_agg_{k}": float(agg[k]) for k in agg},
                            "archive_check": "agree" if not bad else f"disagree:{bad}"}
        if bad:
            problems.append(f"{bucket}: repaired hours do not aggregate to the native 4h archive bar ({bad})")
    status = "replaced" if not problems else "provenance_unverified"
    for rec in log:
        rec.update(compared.get(pd.Timestamp(rec["time"]).floor("4h"), {}))
        if rec["status"] == "replaced_pending_archive_check":
            rec["status"] = status
            rec["reason"] = "; ".join(problems)
        rec["checks"] = ("exact open time; symbol/timeframe/source; finite; OHLC consistent; volume >= 0; repaired "
                         "hours == native 4h archive bar (OHLC, summed volume)")
        rec["note"] = "in memory only; neither store written; feeds the 4h and 1d aggregates"
    if problems:
        return h, log, False, "; ".join(problems)
    if len(placeholder_bars(out)):
        return h, log, False, "placeholder bars remain after replacement"
    return out, log, True, ""


def load_funding_checked(symbol: str) -> pd.DataFrame | None:
    """Funding settlements: rate as stored (long pays when positive) and the DOCUMENTED interval only.
    - archive rows (data.binance.vision fundingRate CSV): `interval_hours` is Binance's funding_interval_hours field
      (regime-book data_vision.py); the locked daily loader treats it as the hours since the previous settlement.
    - fapi /fundingRate rows (funding_api, 2026-07): time and rate only; their interval is NOT documented (NaN here;
      never inferred from spacing).
    Fails loudly on duplicates inside a file, conflicting overlaps, non-finite rates or non-positive intervals.
    Returns None when no local series exists."""
    parts = []
    p = KLINE_ROOT / "funding" / f"{symbol}.parquet"
    if p.exists():
        D._check_schema(p, D.FUNDING_SCHEMA)
        a = pl.scan_parquet(p).filter(pl.col("time") < _CUT_S).collect()
        if a["time"].is_duplicated().any():
            raise SystemExit(f"{symbol} funding: duplicate settlements")
        parts.append(a.select("time", pl.col("interval_hours").alias("documented_interval_hours"), "rate",
                              pl.lit("archive").alias("record_source")))
    q = KLINE_ROOT / "funding_api" / f"{symbol}.parquet"
    if q.exists():
        D._check_schema(q, D.FUNDING_API_SCHEMA)
        b = pl.scan_parquet(q).filter(pl.col("time") < _CUT_S).collect()
        if b["time"].is_duplicated().any():
            raise SystemExit(f"{symbol} funding_api: duplicate settlements")
        parts.append(b.select("time", pl.lit(None, dtype=pl.Float64).alias("documented_interval_hours"), "rate",
                              pl.lit("fapi_fundingRate").alias("record_source")))
    if not parts:
        return None
    if len(parts) == 2:
        ov = parts[0].join(parts[1], on="time", how="inner")
        if ov.height and (ov["rate"] != ov["rate_right"]).any():
            raise SystemExit(f"{symbol}: funding sources disagree on overlapping settlements")
    f = pl.concat(parts).unique("time", keep="first", maintain_order=True).sort("time")
    out = D._to_pandas(f)
    out.index = pd.to_datetime(out.pop("time"), unit="s", utc=True)
    if not np.isfinite(out["rate"].to_numpy(float)).all():
        raise SystemExit(f"{symbol}: non-finite funding rate")
    iv = out["documented_interval_hours"].to_numpy(float)
    if (iv[~np.isnan(iv)] <= 0).any():
        raise SystemExit(f"{symbol}: non-positive documented funding interval")
    assert_sealed(out.index, f"{symbol} funding")
    return out


def aggregate(h: pd.DataFrame, rule: str, n: int) -> tuple[pd.DataFrame, list]:
    """Bars of `rule` (UTC buckets) from 1h bars. A bucket is emitted only with all n hourly bars; incomplete
    buckets are dropped (never invented) and returned for the coverage log."""
    g = h.groupby(h.index.floor(rule))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["open"].size()})
    dropped = [str(t) for t in out.index[out["n"] != n]]
    return out[out["n"] == n].drop(columns="n"), dropped


def load_all():
    """(bars[tf][symbol], hourly[symbol], funding[symbol], repairs, coverage rows, data_status[tf][symbol])."""
    bars = {"1h": {}, "4h": {}, "1d": {}}
    hourly, funding, repairs, coverage = {}, {}, [], []
    status = {tf: {} for tf in ("1h", "4h", "1d")}
    for s in SYMBOLS:
        raw = load_hourly_raw(s)
        if raw is None:
            for tf in status:
                status[tf][s] = "MISSING_SERIES"
            funding[s] = None
            for tf in ("1h", "4h", "1d"):
                coverage.append(dict(series="ohlcv", timeframe=tf, symbol=s, rows=0, status="MISSING_SERIES",
                                     reason="no local 1h cache file", source="regime-book 1h parquet"))
            continue
        h, rlog, verified, why = replace_placeholders(raw, s)
        repairs.extend(rlog)
        check_frame(h, f"{s} 1h")
        hourly[s] = h
        bars["1h"][s] = h[["open", "high", "low", "close", "volume"]].copy()
        dropped = {}
        bars["4h"][s], dropped["4h"] = aggregate(h, "4h", 4)
        bars["1d"][s], dropped["1d"] = aggregate(h, "1D", 24)
        for tf in ("1h", "4h", "1d"):
            b = bars[tf][s]
            check_frame(b, f"{s} {tf}")
            miss = missing_bars(b.index, BAR[tf], EXPECTED_LAST[tf])
            short = b.index[-1] < EXPECTED_LAST[tf]
            late = b.index[0] >= FOLDS[0]["test_start"]
            reasons = ([f"{len(miss)} missing bar(s)"] if len(miss) else []) + (["ends early"] if short else []) \
                + (["starts inside fold 1"] if late else []) + ([f"PROVENANCE_UNVERIFIED: {why}"] if not verified else [])
            status[tf][s] = "COMPLETE" if not reasons else "DATA_INCOMPLETE"
            coverage.append(dict(series="ohlcv", timeframe=tf, symbol=s, rows=len(b), first=str(b.index[0]),
                                 last=str(b.index[-1]), expected_last=str(EXPECTED_LAST[tf]),
                                 missing_bars=len(miss), missing_timestamps=";".join(str(t) for t in miss),
                                 dropped_incomplete_buckets=";".join(dropped.get(tf, [])),
                                 status=status[tf][s], reason="; ".join(reasons),
                                 source="regime-book 1h parquet"
                                 + ("" if tf == "1h" else " (aggregated, complete buckets only)")))
        funding[s] = load_funding_checked(s)
        f = funding[s]
        undocumented = 0 if f is None else int(f["documented_interval_hours"].isna().sum())
        coverage.append(dict(series="funding", timeframe="settlement", symbol=s, rows=0 if f is None else len(f),
                             first="" if f is None else str(f.index[0]), last="" if f is None else str(f.index[-1]),
                             status="MISSING" if f is None else "PRESENT",
                             reason=f"{undocumented} record(s) without a documented interval",
                             source="regime-book funding (archive) + funding_api (fapi /fundingRate) parquet"))
    return bars, hourly, funding, repairs, coverage, status


def affected_evaluation_ids(tf: str, symbol: str) -> list[str]:
    """Evaluations that depend on a series: every book on that timeframe and symbol, plus book 13 on the timeframe
    that uses it as its higher timeframe (4h -> 1h book 13; 1d -> 4h book 13)."""
    ids = [f"{tf}|{b}|{symbol}" for b in BOOK_IDS] if tf in TIMEFRAMES else []
    ids += [f"{lo}|13|{symbol}" for lo, hi in HTF_FOR.items() if hi == tf]
    return ids


# ---------------------------------------------------------------------------------------------
# Local rule adapters
# ---------------------------------------------------------------------------------------------
def weinstein_stage_tf(c: np.ndarray) -> np.ndarray:
    """Book 6: m = prev(SMA30) (window ends at t-1), m4 = prev(m, 4). +1 if close[t] > m and m > m4; -1 if
    close[t] < m and m < m4; else 0; NaN until computable. No weekly bar is read."""
    m = prev(sma(c, 30))
    m4 = prev(m, 4)
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(m4), np.nan,
                        np.where((c > m) & (m > m4), 1.0, np.where((c < m) & (m < m4), -1.0, 0.0)))


def htf_cloud_state(ltf_index: pd.DatetimeIndex, htf: pd.DataFrame, htf_bar: pd.Timedelta) -> np.ndarray:
    """Book 13 permission: completed higher-timeframe close versus its own Ichimoku 9/26/52 cloud (+1 above,
    -1 below, 0 inside/on an edge, NaN unknown). A higher-timeframe bar opening at H applies only to tested bars
    opening at or after H + its length, and carries forward until the next completed one."""
    top, bot, ok = ichimoku_cloud(htf["high"].to_numpy(float), htf["low"].to_numpy(float))
    hc = htf["close"].to_numpy(float)
    st = np.where(~ok, np.nan, np.where(hc > top, 1.0, np.where(hc < bot, -1.0, 0.0)))
    s = pd.Series(st, index=htf.index + htf_bar)
    return s.reindex(ltf_index, method="ffill").to_numpy(dtype=float)


def rv_cut_prior365(c: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Book 19 volatility cut. rv[t] = std (ddof 1) of the 20 one-bar log returns whose last return is into bar
    t-1 (a 20-bar std, then one prev). Threshold q[t] = 70th percentile (linear) of rv[t-365] .. rv[t-1]: 365
    positional values, rv[t] excluded, all of them finite; no reaching back across gaps. Eligible at t only when
    rv[t] and those 365 values are finite (else WARMUP_BLOCKED). Returns (rv, q, eligible)."""
    lr = np.log(c / prev(c))
    rv = prev(pd.Series(lr).rolling(RV_BARS, min_periods=RV_BARS).std().to_numpy())
    window = pd.Series(prev(rv))                              # rv[t-1] ... back
    q = window.rolling(RV_PRIOR_OBS, min_periods=RV_PRIOR_OBS).quantile(RV_PCT).to_numpy()
    eligible = np.isfinite(rv) & np.isfinite(q)
    return rv, q, eligible


# ---------------------------------------------------------------------------------------------
# Canonical books on the tested timeframe (bar counts, never calendar conversions)
# ---------------------------------------------------------------------------------------------
def build_books(d: pd.DataFrame, htf: pd.DataFrame | None, htf_bar: pd.Timedelta) -> dict:
    o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    N = len(c)
    no = np.zeros(N, dtype=bool)
    books = {}
    s200 = prev(sma(c, 200))
    books["1"] = dict(zip(("state", "valid"), _sign_vs(c, s200)))
    books["2"] = dict(zip(("state", "valid"), _sign_vs(c, prev(ema(c, 200)))))
    books["3"] = dict(zip(("state", "valid"), _sign_vs(c, prev(sma(c, 50)))))
    s200_20 = prev(s200, 20)
    v4 = ~(np.isnan(s200) | np.isnan(s200_20))
    with np.errstate(invalid="ignore"):
        st4 = np.where(v4 & (c > s200) & (s200 > s200_20), 1.0, np.where(v4 & (c < s200) & (s200 < s200_20), -1.0, 0.0))
    books["4"] = dict(state=st4, valid=v4)
    s50 = prev(sma(c, 50))
    v5 = ~(np.isnan(s50) | np.isnan(s200) | np.isnan(prev(s50)) | np.isnan(prev(s200)))
    with np.errstate(invalid="ignore"):
        up = v5 & (s50 > s200) & (prev(s50) <= prev(s200))
        dn = v5 & (s50 < s200) & (prev(s50) >= prev(s200))
    trig5 = np.where(up, 1.0, np.where(dn, -1.0, 0.0))
    books["5"] = dict(trig=trig5, state=_event_regime(trig5, no, no), valid=v5)
    stage = weinstein_stage_tf(c)
    v6 = ~np.isnan(stage)
    stage0 = np.where(v6, stage, 0.0)
    books["6"] = dict(state=stage0, valid=v6)
    trig7, xl7, xs7, v7 = _donchian(c, h, l, 20, 10)
    books["7"] = dict(trig=trig7, state=_event_regime(trig7, xl7, xs7), valid=v7)
    trig8, xl8, xs8, v8 = _donchian(c, h, l, 55, 20)
    books["8"] = dict(trig=trig8, state=_event_regime(trig8, xl8, xs8), valid=v8)
    trig9 = np.where((trig7 != 0) & (trig7 == stage0), trig7, 0.0)
    books["9"] = dict(trig=trig9, state=_event_regime(trig9, xl7, xs7), valid=v7 & v6)
    sdir = supertrend_dir(h, l, c, 10, 3.0)
    trig10 = np.where((sdir != 0) & (prev(sdir) == -sdir), sdir, 0.0)
    trig10[0] = 0.0
    books["10"] = dict(trig=trig10, state=_event_regime(trig10, no, no), valid=sdir != 0)
    adx, pdi, ndi = (prev(x) for x in adx_dmi(h, l, c, 14))
    v11 = ~(np.isnan(adx) | np.isnan(pdi) | np.isnan(ndi))
    with np.errstate(invalid="ignore"):
        st11 = np.where(v11 & (adx > 25) & (pdi > ndi), 1.0, np.where(v11 & (adx > 25) & (ndi > pdi), -1.0, 0.0))
    books["11"] = dict(state=st11, valid=v11)
    top, bot, v12 = ichimoku_cloud(h, l)
    with np.errstate(invalid="ignore"):
        st12 = np.where(v12 & (c > top), 1.0, np.where(v12 & (c < bot), -1.0, 0.0))
    books["12"] = dict(state=st12, valid=v12)
    hcl = htf_cloud_state(d.index, htf, htf_bar) if htf is not None else np.full(N, np.nan)
    v13 = v12 & ~np.isnan(hcl)
    st13 = np.where(v13 & (st12 != 0) & (st12 == np.where(v13, hcl, 0.0)), st12, 0.0)
    books["13"] = dict(state=st13, valid=v13)
    for b, n in (("14", 90), ("15", 30)):
        base = prev(c, n)
        v = ~np.isnan(base)
        books[b] = dict(state=np.where(v, np.sign(c / np.where(v, base, 1.0) - 1.0), 0.0), valid=v)
    hi252 = prev(roll_max(c, 252))
    v16 = ~(np.isnan(hi252) | np.isnan(s200))
    with np.errstate(invalid="ignore"):
        trig16 = np.where(v16 & (c >= hi252), 1.0, 0.0)
        below16 = v16 & (c < s200)
    books["16"] = dict(trig=trig16, state=_event_regime(trig16, below16, no), valid=v16)
    ath = prev(np.maximum.accumulate(c))
    v17 = ~(np.isnan(ath) | np.isnan(s200))
    with np.errstate(invalid="ignore"):
        trig17 = np.where(v17 & (c >= ath), 1.0, 0.0)
        below17 = v17 & (c < s200)
    books["17"] = dict(trig=trig17, state=_event_regime(trig17, below17, no), valid=v17)
    st18, v18 = swing_structure_state(h, l, c, 5)
    books["18"] = dict(state=st18, valid=v18)
    rv, q, rv_ok = rv_cut_prior365(c)
    v19 = v4 & v7 & rv_ok                                       # WARMUP_BLOCKED before this
    trig19 = np.where(v19 & (trig7 != 0) & (trig7 == st4), trig7, 0.0)
    st19 = np.where(v19, _event_regime(trig19, xl7 | (st4 != 1), xs7 | (st4 != -1)), 0.0)
    with np.errstate(invalid="ignore"):
        risk19 = np.where(v19, np.where(rv > q, RISK_LOW, RISK), np.nan)   # no fallback size while blocked
    books["19"] = dict(trig=trig19, state=st19, valid=v19, risk=risk19)
    for b in BOOK_IDS:
        books[b]["kind"] = BOOK_KIND[b]
        books[b].setdefault("trig", np.zeros(N))
        bad = (~books[b]["valid"]) & ((books[b]["state"] != 0) | (books[b]["trig"] != 0))
        if bad.any():
            raise SystemExit(f"book {b}: signal emitted before eligibility (bar {int(np.argmax(bad))})")
    if sorted(books, key=int) != sorted(BOOK_IDS, key=int):
        raise SystemExit("book registry mismatch")
    return books


# ---------------------------------------------------------------------------------------------
# Funding series and the local ledger engine (one symbol, one account)
# ---------------------------------------------------------------------------------------------
class FundingSeries:
    """Settlements with mark = the 1h open at or before the settlement (forward-filled; none -> unmarked).
    Expected schedule, per record k, from its DOCUMENTED interval I_k (hours since the previous settlement):
      OK       ts[k] - ts[k-1] == I_k exactly;
      MISSING  ts[k] - ts[k-1] == m * I_k (integer m >= 2) and I_{k-1} == I_k: m - 1 records missing;
      UNKNOWN  no documented interval (fapi rows), no previous record, or any other spacing (an undocumented
               schedule change cannot be told apart from missing records)."""

    def __init__(self, f: pd.DataFrame | None, hourly: pd.DataFrame | None):
        self.missing = f is None
        if self.missing:
            self.ts = np.zeros(0, dtype=np.int64)
            self.cpu = np.zeros(0)
            return
        self.ts = f.index.as_unit("ns").asi8
        h_ts = hourly.index.as_unit("ns").asi8
        j = np.searchsorted(h_ts, self.ts, side="right") - 1
        mark = np.where(j >= 0, hourly["open"].to_numpy(float)[np.clip(j, 0, None)], np.nan)
        self.cpu = f["rate"].to_numpy(float) * mark            # cash per unit qty for a long (pays when > 0)
        ivh = f["documented_interval_hours"].to_numpy(float)
        iv = np.where(np.isnan(ivh), -1, np.round(ivh * 3.6e12)).astype(np.int64)
        n = len(self.ts)
        self.check = np.array(["UNKNOWN"] * n, dtype=object)
        self.missing_before = np.zeros(n, dtype=np.int64)
        for k in range(1, n):
            if iv[k] <= 0:
                continue
            sp = int(self.ts[k] - self.ts[k - 1])
            if sp == iv[k]:
                self.check[k] = "OK"
            elif sp > iv[k] and sp % iv[k] == 0 and iv[k - 1] == iv[k]:
                self.check[k] = "MISSING"
                self.missing_before[k] = sp // iv[k] - 1

    def coverage(self, t0: int, t1: int) -> tuple[str, int, int]:
        """(status, missing_records, unknown_schedule_checks) for exposure (t0, t1]. Every settlement after t0 up
        to and including the first one after t1 (the bracket) is checked against the documented schedule; no
        bracket (exposure reaches past the last local record) is an unknown-schedule check."""
        if self.missing:
            return "MISSING", 0, 0
        a = int(np.searchsorted(self.ts, t0, side="right"))
        b1 = int(np.searchsorted(self.ts, t1, side="right"))
        unknown = 1 if b1 >= len(self.ts) else 0
        rng = range(a, min(b1, len(self.ts) - 1) + 1)
        unknown += sum(1 for k in rng if self.check[k] == "UNKNOWN")
        missing = int(sum(self.missing_before[k] for k in rng if self.check[k] == "MISSING"))
        unmarked = int((~np.isfinite(self.cpu[a:b1])).sum())
        if unknown:
            return "UNRESOLVED", missing, unknown
        if missing or unmarked:
            return "PARTIAL", missing, unknown
        return "COMPLETE", missing, unknown


def simulate_ledger(sym: str, d: pd.DataFrame, bar: pd.Timedelta, sig: dict, risk: np.ndarray, i0: int, i1: int,
                    fs: FundingSeries | None, eval_id: str = "", fold: int = 0):
    """One symbol, one account, $10,000 flat at i0. Bar order at bar i:
      open:  (a) post settlements s <= open_i while a position is held (funding before any exit at that instant);
             (b) gap through the stop -> stop_gap at the open; else (c) scheduled indicator exit -> signal at open;
             else (d) a scheduled entry fills (sized on equity = cash, the account being flat);
      intra: (e) stop test on low/high (also on the entry bar) -> stop at the stop price; settlements strictly
             between this bar's open and close are TIMING_UNRESOLVED (not posted, not zero); one exactly at the
             close is outside the position (not posted, not unresolved);
      close: (f) last fold bar: post settlements s <= close_i, then close at the close (window_end);
             (g) otherwise, if still held, post settlements s <= close_i; then mark equity; then signals schedule
             the next open (no same-bar reversal; event books need a fresh trigger).
    Funding is posted to cash at its settlement time and attributed to the open trade; it is never added again at
    exit. Returns (trades, ledger rows, skipped_by_cap)."""
    o, h, l, c = (d[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    atr = prev(atr_wilder(h, l, c, ATR_N))
    t_ns = d.index.as_unit("ns").asi8
    bar_ns = int(bar.value)
    slip = slip_for(sym)
    cash, cum_f = START_EQUITY, 0.0
    pos, pending, armed = None, None, 0.0
    trades, rows, skipped, ntr = [], [], 0, 0
    has_f = fs is not None and not fs.missing

    def post(upto: int):
        nonlocal cash, cum_f
        if not has_f:
            return
        while pos["fptr"] < len(fs.ts) and fs.ts[pos["fptr"]] <= upto:
            k = pos["fptr"]
            v = fs.cpu[k]
            if np.isfinite(v):
                amt = -pos["side"] * pos["qty"] * v
                cash += amt
                cum_f += amt
                pos["funding"] += amt
                pos["n_settle"] += 1
            else:
                pos["no_mark"] += 1
            pos["fptr"] += 1

    def close(px_raw: float, i: int, reason: str, bound: int, unresolved: list):
        nonlocal cash, pos
        side, qty = pos["side"], pos["qty"]
        px = px_raw * (1 - slip * side)
        price_pnl = side * qty * (px - pos["entry"])
        fee_out = FEE * qty * px
        cash += price_pnl - fee_out
        if has_f:
            cov, n_miss, n_unknown = fs.coverage(pos["entry_ns"], bound)
            if pos["no_mark"] and cov == "COMPLETE":
                cov = "PARTIAL"
        else:
            cov, n_miss, n_unknown = "MISSING", 0, 0
        trades.append(dict(
            trade_id=pos["trade_id"], evaluation_id=eval_id, fold=fold, symbol=sym, side=side,
            signal_time=str(d.index[pos["signal_i"]]), entry_time=str(d.index[pos["entry_i"]]),
            entry_price=pos["entry"], exit_time=str(d.index[i]), exit_reason=reason, exit_price=px, qty=qty,
            notional=qty * pos["entry"], stop_price=pos["stop"], atr=pos["atr"], risk_fraction=pos["risk"],
            price_pnl=price_pnl, fees=pos["fee_in"] + fee_out, funding=pos["funding"],
            funding_settlements_included=pos["n_settle"], funding_coverage_status=cov,
            funding_missing_records=n_miss, funding_unknown_schedule_checks=n_unknown,
            funding_unmarked_settlements=pos["no_mark"],
            funding_timing_status=("NO_SERIES" if not has_f else
                                   "TIMING_UNRESOLVED" if unresolved else "RESOLVED"),
            unresolved_settlement_count=len(unresolved),
            unresolved_settlement_timestamps=";".join(str(pd.Timestamp(x, tz="UTC")) for x in unresolved),
            net_pnl=price_pnl - pos["fee_in"] - fee_out + pos["funding"]))
        pos = None

    for i in range(max(i0 - 1, 0), i1 + 1):
        t_open, t_close = int(t_ns[i]), int(t_ns[i]) + bar_ns
        if i >= i0:
            act, pending = pending, None
            if pos is not None:
                post(t_open)                                                   # (a)
                gap = (pos["side"] == 1 and o[i] <= pos["stop"]) or (pos["side"] == -1 and o[i] >= pos["stop"])
                if gap:
                    close(o[i], i, "stop_gap", t_open, [])                     # (b)
                elif act and act[0] == "exit":
                    close(o[i], i, "signal", t_open, [])                       # (c)
            elif act and act[0] == "enter":                                    # (d)
                side = act[1]
                if sig["kind"] == "event":
                    armed = 0.0
                r = float(risk[i - 1])
                if not np.isfinite(r):
                    raise SystemExit(f"{eval_id}: entry scheduled while the risk rule is WARMUP_BLOCKED")
                equity = cash                                                  # flat: no open position
                fill = o[i] * (1 + slip * side)
                dist = STOP_ATR * atr[i - 1]
                qty = r * equity / dist
                if qty * fill > LEV_CAP * equity:
                    skipped += 1
                else:
                    fee = FEE * qty * fill
                    cash -= fee
                    ntr += 1
                    pos = dict(side=side, qty=qty, entry=fill, stop=fill - side * dist, atr=atr[i - 1], fee_in=fee,
                               entry_ns=t_open, entry_i=i, signal_i=i - 1, risk=r, funding=0.0, n_settle=0,
                               no_mark=0, trade_id=f"{eval_id}|f{fold}|t{ntr}",
                               fptr=int(np.searchsorted(fs.ts, t_open, side="right")) if has_f else 0)
            if pos is not None:                                                # (e)
                hit = (pos["side"] == 1 and l[i] <= pos["stop"]) or (pos["side"] == -1 and h[i] >= pos["stop"])
                if hit:
                    unresolved = []
                    if has_f:
                        k0 = np.searchsorted(fs.ts, t_open, side="right")
                        k1 = np.searchsorted(fs.ts, t_close, side="left")
                        unresolved = [int(x) for x in fs.ts[k0:k1]]
                    close(pos["stop"], i, "stop", t_open, unresolved)
        if i == i1:
            if pos is not None:                                                # (f)
                post(t_close)
                close(c[i], i, "window_end", t_close, [])
            rows.append(_ledger_row(eval_id, fold, d.index[i], cash, 0.0, cum_f, 0.0, ""))
            continue
        if i >= i0 and pos is not None:                                        # (g)
            post(t_close)
        if i >= i0:
            unreal = pos["side"] * pos["qty"] * (c[i] - pos["entry"]) if pos is not None else 0.0
            rows.append(_ledger_row(eval_id, fold, d.index[i], cash, unreal, cum_f,
                                    pos["side"] * pos["qty"] if pos is not None else 0.0,
                                    pos["trade_id"] if pos is not None else ""))
        st, tg = sig["state"][i], sig["trig"][i]
        if sig["kind"] == "event":
            if tg != 0 and st == tg and (pos is None or pos["side"] != tg):
                armed = tg
            if armed != 0 and st != armed:
                armed = 0.0
        if pos is not None:
            if st != pos["side"]:
                pending = ("exit",)
        elif st != 0 and not np.isnan(atr[i]) and (sig["kind"] == "state" or armed == st):
            pending = ("enter", st)
    return trades, rows, skipped


def _ledger_row(eval_id, fold, t, cash, unreal, cum_f, qty, trade_id) -> dict:
    return dict(evaluation_id=eval_id, fold=fold, bar_open_time=str(t), cash=cash, unrealized_price_pnl=unreal,
                cumulative_funding_posted=cum_f, equity=cash + unreal, position_quantity=qty, trade_id=trade_id)


# ---------------------------------------------------------------------------------------------
# Metrics and statuses
# ---------------------------------------------------------------------------------------------
def window_bounds(index: pd.DatetimeIndex, start_day: pd.Timestamp, end_day: pd.Timestamp) -> tuple[int, int]:
    """First bar opening on/after start_day; last bar opening before end_day + 1 day."""
    i0 = int(index.searchsorted(start_day))
    i1 = int(index.searchsorted(end_day + pd.Timedelta(days=1))) - 1
    if i0 >= len(index) or i1 < i0:
        raise SystemExit(f"window {start_day.date()}..{end_day.date()} outside the data")
    return i0, i1


def fold_metrics(trades: list, rows: list, skipped: int) -> dict:
    net = np.array([t["net_pnl"] for t in trades]) if trades else np.zeros(0)
    gp, gl = float(net[net > 0].sum()), float(-net[net < 0].sum())
    eq = np.r_[START_EQUITY, [r["equity"] for r in rows]]          # full resolution, starting at 10,000
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return dict(trades=len(trades), net_pnl=float(net.sum()), gross_profit=gp, gross_loss=gl,
                pf=gp / gl if gl > 0 else (math.inf if gp > 0 else float("nan")),
                max_drawdown=float(dd.max()), max_drawdown_pct=float((dd / peak).max() * 100),
                avg_trade=float(net.mean()) if len(net) else float("nan"),
                exposure=float(np.mean([r["position_quantity"] != 0 for r in rows])) if rows else float("nan"),
                funding_pnl=float(sum(t["funding"] for t in trades)), fees=float(sum(t["fees"] for t in trades)),
                win_count=int((net > 0).sum()), skipped_lev_cap=skipped,
                final_equity=float(eq[-1]))


def pooled(folds: list, fold_trades: list) -> dict:
    allt = [t for ts in fold_trades for t in ts]
    net = np.array([t["net_pnl"] for t in allt]) if allt else np.zeros(0)
    gp, gl = float(net[net > 0].sum()), float(-net[net < 0].sum())
    return dict(trades=len(allt), net_pnl=float(net.sum()),
                pf=gp / gl if gl > 0 else (math.inf if gp > 0 else float("nan")),
                worst_fold_drawdown_pct=max([f["max_drawdown_pct"] for f in folds], default=float("nan")),
                positive_fold_count=sum(1 for f in folds if f["net_pnl"] > 0),
                funding_pnl=float(sum(t["funding"] for t in allt)))


def raw_status(p: dict) -> str:
    return "SCREEN_HIT" if (p["net_pnl"] > 0 and p["pf"] > PF_MIN and p["positive_fold_count"] >= 2) else "SCREEN_FAIL"


STATUS_PRECEDENCE = [
    ("DATA_INCOMPLETE", "a required price gap, a missing/short/late series (incl. the book 13 higher timeframe), or "
                        "unverified placeholder provenance for the symbol"),
    ("UNRESOLVED", "funding coverage UNRESOLVED (expected settlement schedule not documented or inconsistent for "
                   "some exposure), or any unrecognised label"),
    ("PARTIAL", "eligibility starts after a fold's first bar (incl. book 19 WARMUP_BLOCKED); funding coverage PARTIAL "
                "(missing records or unmarked settlement) or MISSING (no series); or any TIMING_UNRESOLVED settlement"),
    ("COMPLETE", "none of the above"),
]
_COVERAGE = ("COMPLETE", "PARTIAL", "MISSING", "UNRESOLVED")
_TIMING = ("RESOLVED", "TIMING_UNRESOLVED", "NO_SERIES")


def protocol_status(eligible_from_start: list, data_ok: bool, coverage: str, timing: str) -> str:
    """Precedence: DATA_INCOMPLETE > UNRESOLVED > PARTIAL > COMPLETE (STATUS_PRECEDENCE)."""
    if not data_ok:
        return "DATA_INCOMPLETE"
    if coverage == "UNRESOLVED" or coverage not in _COVERAGE or timing not in _TIMING:
        return "UNRESOLVED"
    if (not all(eligible_from_start)) or coverage in ("PARTIAL", "MISSING") or timing in ("TIMING_UNRESOLVED",
                                                                                          "NO_SERIES"):
        return "PARTIAL"
    return "COMPLETE"


def interpretation(raw: str, proto: str) -> str:
    if raw == "SCREEN_HIT" and proto == "COMPLETE":
        return "complete-data research screen hit (not a confirmed edge, recommendation or authorization)"
    if raw == "SCREEN_HIT":
        return f"raw hit on {proto} protocol: shown, not promoted"
    if raw == "NOT_TESTED":
        return f"not evaluated ({proto})"
    return "screen fail"


PNL_LABEL_COMPLETE = "After fees, slippage and funding."
PNL_LABEL_INCOMPLETE = "After fees, slippage and included funding; funding incomplete/unresolved."


def pnl_label(coverage: str, timing: str) -> str:
    return PNL_LABEL_COMPLETE if (coverage == "COMPLETE" and timing == "RESOLVED") else PNL_LABEL_INCOMPLETE


def evaluation_keys() -> list[tuple[str, str, str]]:
    return [(tf, b, s) for tf in TIMEFRAMES for b in BOOK_IDS for s in SYMBOLS]


def evaluate(tf, bars, hourly, funding, data_status, trade_sink, equity_writer):
    """All 19 x 3 standalone evaluations on one timeframe, order book -> symbol -> fold."""
    htf_tf = HTF_FOR[tf]
    rows, train = [], []
    built, fseries = {}, {}
    for s in SYMBOLS:
        if data_status[tf].get(s) == "COMPLETE":
            htf = bars[htf_tf][s] if data_status[htf_tf].get(s) == "COMPLETE" else None
            built[s] = build_books(bars[tf][s], htf, BAR[htf_tf])
            fseries[s] = FundingSeries(funding.get(s), hourly.get(s))
    for b in BOOK_IDS:
        for s in SYMBOLS:
            eid = f"{tf}|{b}|{s}"
            row = dict(evaluation_id=eid, timeframe=tf, book_id=b, book_name=BOOK_NAME[b], symbol=s,
                       kind=BOOK_KIND[b], rule_reference=BOOK_RULE[b], timeframe_dependency=BOOK_TF_DEP[b])
            ds = data_status[tf].get(s, "MISSING_SERIES")
            data_ok = ds == "COMPLETE" and (b != "13" or data_status[htf_tf].get(s) == "COMPLETE")
            row["data_status"] = ds if b != "13" else f"{ds}; htf {data_status[htf_tf].get(s, 'MISSING_SERIES')}"
            if not data_ok:
                row.update(raw_screen_status="NOT_TESTED", protocol_status="DATA_INCOMPLETE",
                           interpretation=interpretation("NOT_TESTED", "DATA_INCOMPLETE"))
                rows.append(row)
                continue
            d = bars[tf][s]
            bk = built[s][b]
            idx = d.index
            first_valid = idx[int(np.argmax(bk["valid"]))] if bk["valid"].any() else None
            row["first_valid_signal_timestamp"] = str(first_valid) if first_valid is not None else ""
            sig = dict(state=bk["state"], trig=bk["trig"], kind=BOOK_KIND[b])
            risk = bk.get("risk", np.full(len(idx), RISK))
            folds, fold_trades, elig, covs, timings, unresolved = [], [], [], [], [], []
            for f in FOLDS:
                i0, i1 = window_bounds(idx, f["test_start"], f["test_end"])
                elig.append(bool(bk["valid"][i0 - 1]) if i0 > 0 else False)
                tr, led, sk = simulate_ledger(s, d, BAR[tf], sig, risk, i0, i1, fseries[s], eid, f["fold"])
                for t in tr:
                    t.update(timeframe=tf, book_id=b)
                trade_sink.extend(tr)
                equity_writer(tf, b, s, led)
                m = fold_metrics(tr, led, sk)
                m.update(fold=f["fold"], window_start=str(idx[i0]), window_end=str(idx[i1]),
                         eligible_from_first_bar=elig[-1])
                folds.append(m)
                fold_trades.append(tr)
                covs += [t["funding_coverage_status"] for t in tr]
                timings += [t["funding_timing_status"] for t in tr]
                unresolved += [x for t in tr for x in t["unresolved_settlement_timestamps"].split(";") if x]
            fs = fseries[s]
            if fs.missing:
                cov = "MISSING"
            elif "UNRESOLVED" in covs:
                cov = "UNRESOLVED"
            elif "PARTIAL" in covs:
                cov = "PARTIAL"
            else:
                cov = "COMPLETE"
            timing = ("NO_SERIES" if fs.missing else
                      "TIMING_UNRESOLVED" if "TIMING_UNRESOLVED" in timings else "RESOLVED")
            alltr = [t for ts in fold_trades for t in ts]
            p = pooled(folds, fold_trades)
            raw = raw_status(p)
            proto = protocol_status(elig, True, cov, timing)
            for f in folds:
                for k in FOLD_FIELDS:
                    row[f"fold{f['fold']}_{k}"] = f[k]
            row.update(pooled_trades=p["trades"], pooled_net_pnl=p["net_pnl"], pooled_pf=p["pf"],
                       worst_fold_drawdown_pct=p["worst_fold_drawdown_pct"],
                       positive_fold_count=p["positive_fold_count"], funding_pnl=p["funding_pnl"],
                       funding_coverage_status=cov,
                       funding_missing_records=sum(t["funding_missing_records"] for t in alltr),
                       funding_unknown_schedule_checks=sum(t["funding_unknown_schedule_checks"] for t in alltr),
                       funding_unmarked_settlements=sum(t["funding_unmarked_settlements"] for t in alltr),
                       funding_timing_status=timing,
                       unresolved_settlement_count=len(unresolved),
                       unresolved_settlement_timestamps=";".join(unresolved),
                       pnl_label=pnl_label(cov, timing),
                       raw_screen_status=raw, protocol_status=proto, interpretation=interpretation(raw, proto))
            rows.append(row)
            for f in FOLDS:   # train windows: code-run check only, never part of any status
                j0, j1 = window_bounds(idx, idx[0].normalize() + pd.Timedelta(days=1), f["train_end"])
                tr, _, _ = simulate_ledger(s, d, BAR[tf], sig, risk, j0, j1, fseries[s], eid, 0)
                train.append(dict(evaluation_id=eid, fold=f["fold"], trades=len(tr)))
    return rows, train


# ---------------------------------------------------------------------------------------------
# Output schema (emitted before the run) and writers
# ---------------------------------------------------------------------------------------------
FOLD_FIELDS = ["window_start", "window_end", "eligible_from_first_bar", "trades", "net_pnl", "gross_profit",
               "gross_loss", "pf", "max_drawdown", "max_drawdown_pct", "avg_trade", "exposure", "funding_pnl", "fees",
               "win_count", "skipped_lev_cap", "final_equity"]
MATRIX_COLS = (["evaluation_id", "timeframe", "book_id", "book_name", "symbol", "kind", "rule_reference",
                "timeframe_dependency", "data_status", "first_valid_signal_timestamp"]
               + [f"fold{i}_{k}" for i in (1, 2, 3) for k in FOLD_FIELDS]
               + ["pooled_trades", "pooled_net_pnl", "pooled_pf", "worst_fold_drawdown_pct", "positive_fold_count",
                  "funding_pnl", "funding_coverage_status", "funding_missing_records",
                  "funding_unknown_schedule_checks", "funding_unmarked_settlements", "funding_timing_status",
                  "unresolved_settlement_count", "unresolved_settlement_timestamps", "pnl_label",
                  "raw_screen_status", "protocol_status", "interpretation"])
TRADE_COLS = ["trade_id", "evaluation_id", "timeframe", "book_id", "symbol", "fold", "side", "signal_time",
              "entry_time", "entry_price", "exit_time", "exit_reason", "exit_price", "qty", "notional", "stop_price",
              "atr", "risk_fraction", "price_pnl", "fees", "funding", "funding_settlements_included",
              "funding_coverage_status", "funding_missing_records", "funding_unknown_schedule_checks",
              "funding_unmarked_settlements", "funding_timing_status", "unresolved_settlement_count",
              "unresolved_settlement_timestamps", "net_pnl"]
EQUITY_COLS = ["evaluation_id", "timeframe", "book_id", "symbol", "fold", "bar_open_time", "cash",
               "unrealized_price_pnl", "cumulative_funding_posted", "equity", "position_quantity", "trade_id"]
COVERAGE_COLS = ["series", "timeframe", "symbol", "rows", "first", "last", "expected_last", "missing_bars",
                 "missing_timestamps", "dropped_incomplete_buckets", "status", "reason", "affected_evaluation_ids",
                 "source"]
REPAIR_COLS = (["symbol", "time", "status", "reason", "source"]
               + [f"parquet_{k}" for k in ("open", "high", "low", "close", "volume")]
               + [f"postgres_{k}" for k in ("open", "high", "low", "close", "volume")]
               + ["postgres_quote_volume", "prev_close", "next_open", "volume_x_typical_over_quote",
                  "archive_bucket", "archive_check", "archive_tolerance_rel"]
               + [f"archive_{k}" for k in ("open", "high", "low", "close", "volume")]
               + [f"repaired_agg_{k}" for k in ("open", "high", "low", "close", "volume")]
               + ["checks", "provenance_chain", "note"])
STATIC_FILES = ["preregistration.md", "screener_rules.md", "screener_output_schema.json", "screener_template.csv"]
RUN_FILES = ["strategy_coin_matrix.csv", "strategy_coin_suitability.md", "trades.csv", "fold_equity.csv.gz",
             "data_coverage.csv", "data_repairs.csv", "run_manifest.json", "verdict.json"]
EQUITY_IDENTITY = ("equity = cash + unrealized_price_pnl. cash already contains every posted funding amount "
                   "(cumulative_funding_posted is shown for audit only and is NOT added again), all fees and all "
                   "realised price PnL. A trade's net_pnl = price_pnl - fees + funding, where funding is the sum of "
                   "the amounts posted to cash during that trade.")
SUPERTREND_DOC = (
    "Book 10 Supertrend (daily supertrend_dir, unchanged): ATR10[t] = Wilder ATR through bar t; hl2[t] = (high[t] + "
    "low[t]) / 2; bu[t] = hl2[t] + 3*ATR10[t], bl[t] = hl2[t] - 3*ATR10[t]. Final bands: fu[t] = bu[t] if bu[t] < "
    "fu[t-1] or close[t-1] > fu[t-1] else fu[t-1]; fl[t] = bl[t] if bl[t] > fl[t-1] or close[t-1] < fl[t-1] else "
    "fl[t-1]. Direction at t: +1 if the previous direction is not +1 and close[t] > fu[t-1]; -1 if the previous "
    "direction is not -1 and close[t] < fl[t-1]; else carried. The reference band for the decision at t is "
    "fu[t-1]/fl[t-1], whose inputs end at bar t-1, so the decision at t does not use bar t's high, low or ATR. "
    "Documented exception to a blanket 'every series shifted by prev': the band stored at t (fu[t], fl[t]) uses bar "
    "t's high/low/ATR and close[t-1]; it is used only as the reference for the decision at t+1. Trigger timestamp = "
    "close of bar t where the direction changes sign; fill = open of bar t+1. The first established direction is "
    "not a trigger.")


def schema_doc() -> dict:
    return {"version": 2, "evaluations": N_EVALUATIONS, "warning": WARNING,
            "ordering": "timeframe (4h, 1h), book_id (1..19), symbol (BTCUSDT, ETHUSDT, SOLUSDT); never by performance",
            "raw_screen_status": RAW_STATUS, "protocol_status": PROTOCOL_STATUS,
            "protocol_precedence": [k for k, _ in STATUS_PRECEDENCE],
            "files": {
                "strategy_coin_matrix.csv": {"rows": N_EVALUATIONS, "columns": MATRIX_COLS},
                "trades.csv": {"columns": TRADE_COLS, "keys": ["evaluation_id", "fold", "trade_id"]},
                "fold_equity.csv.gz": {"columns": EQUITY_COLS, "compression": "gzip",
                                       "resolution": "every closed bar of every tested fold", "identity": EQUITY_IDENTITY},
                "data_coverage.csv": {"columns": COVERAGE_COLS}, "data_repairs.csv": {"columns": REPAIR_COLS},
                "screener_template.csv": {"columns": MATRIX_COLS, "rows": 0}}}


def screener_rules_md() -> str:
    return "\n".join([
        "# Screener rules — MTF directional matrix", "", f"> {WARNING}", "",
        f"Source: `strategy_coin_matrix.csv` ({N_EVALUATIONS} rows, schema `screener_output_schema.json`).", "",
        "- One row = one standalone strategy-timeframe-symbol evaluation with three $10,000-flat folds.",
        "- `raw_screen_status` and `protocol_status` are separate columns and are always shown together.",
        "- Only `protocol_status = COMPLETE` with `raw_screen_status = SCREEN_HIT` may be called an unqualified "
        "historical screen hit. A raw SCREEN_HIT on PARTIAL, DATA_INCOMPLETE or UNRESOLVED stays visible and is not "
        "promoted.",
        "- Fixed order (timeframe, book, symbol). No ranking, sorting, colour scale or selection by any performance "
        "column; no 'best coin' or 'best book'; no shared-account aggregation; 4h and 1h are separate.",
        f"- Every PnL figure carries `pnl_label`: \"{PNL_LABEL_COMPLETE}\" only when funding coverage is COMPLETE and "
        f"timing RESOLVED; otherwise \"{PNL_LABEL_INCOMPLETE}\"",
        "- Every display carries the warning above. Nothing is tradable.", ""])


def preregistration_md() -> str:
    L = ["# Pre-registration — MTF directional screen, 114 standalone evaluations", "",
         "Status: written before the run and frozen by hash in run_manifest.json. NOT RUN. Research only; no orders.",
         "", f"> {WARNING}", "",
         "## Ownership", "",
         "Two implementations exist. This one: `backend/scripts/daily_direction_shortlist_mtf_prereg.py` in worktree "
         "`D:/cryptoscreener-worktrees/daily-direction-prereg` (branch research/daily-direction-prereg), output "
         "`backend/reports/daily_direction_shortlist_mtf_prereg/`, dependency the locked daily script "
         f"(sha256 {DAILY_SCRIPT_SHA256}). The other: `backend/reports/mtf_4h_1h_strategy_coin_screen/` "
         "(run_mtf_strategy_coin_screen.py + mtf_rule_lock.py + mtf_engine.py + preregistration.md) in "
         "D:/cryptoscreener. Owner designated by the user on 2026-10-18: THIS runner and this output directory. The "
         "other runner is REVIEW_ONLY: not executed, merged, deleted or developed for this experiment.", "",
         "## Scope", "",
         "19 canonical books × 4h and 1h × BTCUSDT, ETHUSDT, SOLUSDT = 114 standalone evaluations, each with three "
         "$10,000-flat folds. No shared account; nothing is split from a shared trade list; no ranking.", "",
         "## Canonical registry", "", "| ID | Name | Kind | Rule | Timeframe dependency |", "|---|---|---|---|---|"]
    L += [f"| {b} | {n} | {k} | {r} | {t} |" for b, n, k, r, t in BOOKS]
    L += ["", "Lookbacks are bar counts of the tested timeframe. `prev` is the single one-bar shift.", "",
          "## Adaptations", "",
          "- Book 6: m = prev(SMA30), m4 = prev(m, 4); long close[t] > m and m > m4; short close[t] < m and m < m4.",
          "- Book 9: book 7's Donchian-20 trigger kept only when it equals book 6's state on that bar; book 7 exits.",
          "- Book 13: the completed higher-timeframe close versus its own cloud permits the side (4h uses complete "
          "UTC days, 1h uses complete 4h bars); available only to tested bars opening at or after the higher bar's "
          "close; a missing higher-timeframe series makes book 13 DATA_INCOMPLETE.",
          f"- Book 19: book 4 side; Donchian-20 trigger in that side; exit on Donchian-10, the stop, or loss of side. "
          f"rv[t] = 20-bar std of log returns ending with the return into t-1. Threshold = {RV_PCT:.0%} percentile "
          f"(linear) of rv[t-365]..rv[t-1] — rv[t] excluded. Eligible only when rv[t] and all 365 prior values are "
          "finite and book 4 and Donchian 20/10 are valid; before that WARMUP_BLOCKED: no entry and no fallback "
          f"size. Risk {RISK_LOW:.2%} when rv[t] > threshold, else {RISK:.2%}. No funding/OI veto; funding costs apply.",
          f"- {SUPERTREND_DOC}", "",
          "## Folds", "", "| Fold | Test |", "|---|---|"]
    L += [f"| {f['fold']} | {f['test_start'].date()} → {f['test_end'].date()} (last closed bar) |" for f in FOLDS]
    L += ["", "Train windows (first bar → train end) are a code-run check only.", "",
          "## Bar chronology", "",
          "Bars are half-open intervals [open, close). Signal on the closed bar t; fill at open t+1 with adverse "
          "slippage. At each bar: (a) post funding "
          "settlements at or before the open while a position is held; (b) a gap through the stop closes at the open "
          "(stop_gap); else (c) an indicator exit scheduled at the previous close fills at the open (signal); else "
          "(d) a scheduled entry fills; (e) the stop is tested on the bar's low/high, also on the entry bar (stop at "
          "the stop price); (f) on a fold's last bar, settlements at or before the close are posted and the position "
          "closes at the close (window_end); (g) otherwise settlements at or before the close are posted while held, "
          "equity is marked, and signals schedule the next open. One position, no pyramiding, no same-bar reversal; "
          "state books re-enter after a stop if the rule holds; event books need a fresh trigger.", "",
          "## Sizing", "",
          "- cash: starts at $10,000 per fold; changes by entry fee (at entry), funding (at each settlement), and "
          "price PnL minus exit fee (at exit). reserved margin: not modelled (0).",
          "- equity: cash + unrealized price PnL. " + EQUITY_IDENTITY,
          "- Sizing timestamp: the fill bar's open, after (a)–(c). An entry only happens when flat, so equity = cash "
          "there.",
          f"- dist = {STOP_ATR} × prev(Wilder ATR14) at the signal bar; fill = open × (1 + slip × side); qty = "
          "risk_fraction × equity / dist; stop = fill − side × dist.",
          f"- Skip (and count) the entry if qty × fill > {LEV_CAP:g} × equity (before the entry fee). Taker fee "
          f"{FEE:.2%} of fill notional on each leg; slippage {SLIP['BTCUSDT']:.2%} BTC/ETH, {SLIP_OTHER:.2%} SOL.", "",
          "## Funding", "",
          "- Records: archive rows (data.binance.vision fundingRate CSV) carry the Binance field "
          "funding_interval_hours, used as the DOCUMENTED interval since the previous settlement (the locked daily "
          "loader's convention); fapi /fundingRate rows (2026-07) carry time and rate only, so their expected schedule "
          "is NOT documented locally and is never inferred from spacing.",
          "- Mark = the 1h open at or before the settlement (forward-filled); rate as stored; funding = -side x qty x "
          "rate x mark. A settlement without such an open is unmarked.",
          "- Expected-schedule check for an exposure (t0, t1]: every settlement after t0 up to and including the "
          "first one after t1 must satisfy spacing == its documented interval (OK); spacing == m x interval with an "
          "unchanged interval means m - 1 MISSING records; anything else, an undocumented interval, no previous "
          "record, or no record after t1 is UNKNOWN. No tolerance factor is used.",
          "- funding_coverage_status: COMPLETE (all checks OK, all included settlements marked); PARTIAL (missing "
          "records or an unmarked settlement); MISSING (no local series); UNRESOLVED (any UNKNOWN check). Kept "
          "separately: funding_missing_records, funding_unknown_schedule_checks, funding_unmarked_settlements and "
          "funding_timing_status (RESOLVED / TIMING_UNRESOLVED / NO_SERIES).",
          "- Inclusion (bars [open, close)): settlements with entry_fill_time < s <= exit bound. Exit bound = the bar "
          "open for stop_gap and signal exits (a settlement exactly at that open is posted before the exit); = the "
          "bar close for the fold-end close exit (a settlement exactly at that close is posted before the close "
          "exit; a separate convention for this forced close). A settlement at an entry's open is not included.",
          "- Intrabar stop, a stated assumption and not exact accounting: the stop executes before the bar's closing "
          "boundary. A settlement at the stop bar's open follows the open-posting convention (posted); settlements "
          "strictly inside (open, close) are TIMING_UNRESOLVED: not posted, not treated as zero, count and "
          "timestamps recorded, protocol PARTIAL; a settlement exactly at the close is excluded under this "
          "assumption. No stop time inside the bar is invented.",
          "- Ledger: funding is posted to cash at the settlement time, included in marked equity and drawdown, "
          "attributed to the open trade's net, and never added again at exit.",
          "- Consequence stated before the run: the archive ends at 2026-06-30 16:00, later settlements come from "
          "fapi rows without a documented interval, and the fold-3 close bound (2026-07-15 00:00) has no local "
          "record after it (sealed). Any exposure after 2026-06-30 16:00 therefore checks UNKNOWN, and such "
          "evaluations are funding UNRESOLVED and protocol UNRESOLVED.",
          "", "## Statuses", ""]
    L += [f"- raw_screen_status {k}: {v}" for k, v in RAW_STATUS.items()]
    L += [f"- protocol_status {k}: {v}" for k, v in PROTOCOL_STATUS.items()]
    L += ["- Only COMPLETE + SCREEN_HIT may be called an unqualified historical screen hit; a fold with zero trades "
          "is not positive; no completeness percentage cutoff is used.", "",
          "## Data", "",
          "- Local regime-book Binance USDT-M 1h parquet (open time < 2026-07-15) and funding/funding_api parquet. 4h "
          "and 1d bars only from complete UTC buckets (4 / 24 hourly bars); incomplete buckets are dropped and "
          "logged, never invented.",
          "- Price-gap policy (conservative): a missing bar anywhere from the series start through its last expected "
          "closed bar (2026-07-14: 1h 23:00, 4h 20:00, 1d 00:00), a series ending early, or a series starting inside "
          "fold 1 makes every evaluation on that symbol x timeframe DATA_INCOMPLETE (raw NOT_TESTED), propagated to "
          "dependants (book 13 on 1h depends on 4h; book 13 on 4h depends on 1d; 1h gaps drop 4h/1d buckets). "
          "Recursive indicators carry state through the whole history, so no partial-history evaluation is "
          "attempted. data_coverage.csv keeps every missing timestamp and the affected evaluation IDs. No gap "
          "repair, forward-fill or indicator reset.",
          "- Placeholder repair (in memory; neither store written): 1h bars with open = high = low = close and zero "
          "volume are replaced by the stored Postgres bar (public.ohlcv, timeframe 1h, source binance_rest, exact "
          "open time) after finite / OHLC / volume >= 0 checks and an archive cross-check: the repaired hours of the "
          "4h bucket must aggregate exactly to the cache's native data.binance.vision 4h bar (open, high, low, close, "
          "summed volume; float tolerance 1e-9 relative). Neighbour differences and volume x typical price / "
          "quote_volume are logged descriptively only. If any gate fails, provenance is UNVERIFIED and every "
          "evaluation on that symbol (all timeframes) is DATA_INCOMPLETE.",
          "- Provenance chain: " + PROVENANCE_CHAIN, "",
          "## Status precedence", "", "| Order | protocol_status | When |", "|---|---|---|"]
    L += [f"| {i} | {k} | {v} |" for i, (k, v) in enumerate(STATUS_PRECEDENCE, 1)]
    L += ["", "raw_screen_status is NOT_TESTED when protocol_status is DATA_INCOMPLETE; otherwise it is computed.",
          f"PnL labels: '{PNL_LABEL_COMPLETE}' only with funding coverage COMPLETE and timing RESOLVED; otherwise "
          f"'{PNL_LABEL_INCOMPLETE}'",
          "Read boundary: every price and funding read is a lazy Parquet scan whose predicate (time < 2026-07-15 "
          "00:00 UTC; for the native 4h check, time == bucket and time + 4h <= that instant) is pushed into the scan "
          "before collect; no full-file read followed by clipping. The Postgres repair query selects only the "
          "placeholder timestamps (checked < the cutoff before connecting), timeframe 1h and source binance_rest.",
          "", "## Approximations (disclosed)", "",
          "- Funding mark = 1h open at or before the settlement, not the exchange mark price.",
          "- Intrabar stop: filled exactly at the stop price plus adverse slippage; the intrabar path is unknown; "
          "stop-bar funding strictly inside the bar is TIMING_UNRESOLVED.",
          "- Fills at bar opens and closes; constant adverse slippage; taker fees on both legs; no partial fills, "
          "queue, latency, liquidation or margin model; reserved margin not modelled.",
          "- Sizing on equity = cash at the fill (flat account); the 2x cap is checked before the entry fee.",
          "- Fold-end positions are closed at the last bar's close; the fold-3 close at 2026-07-15 00:00 cannot see "
          "a settlement at that sealed instant.",
          "- 4h / 1d bars are UTC aggregates of 1h bars; book 13's higher-timeframe cloud uses these aggregates.",
          "",
          "## Data access before approval (loader-only access; no performance evaluation)", "",
          "On 2026-10-18, all reads filtered to timestamps < 2026-07-15: (1) metadata check of the native 1h and 4h "
          "parquet and funding files (counts, first/last, gaps, duplicates, NaN, zero-volume/flat counts; no prices "
          "printed); (2) zero-volume 1h timestamps and Postgres boolean checks at 2024-10-28 20:00; (3) Postgres row "
          "counts by source and information_schema types; (4) loader attempt 1 stopped on a false gap (timestamp-unit "
          "bug); (5) loader attempt 2: full load of 1h, 4h/1d aggregation, funding and three Postgres replacement bars "
          "(printed counts, ranges and replacement volumes). No book state, trade, return, PnL or status was "
          "computed on real data. Earlier versions of this script (7eb2d235 shared-account; 527de03b, 0f40f70b "
          "intermediate 114-evaluation versions) were never run on real data. After the owner designation only "
          "source code, git history and the 2026-10-03 backfill run log (_backfill_majors_htf.json, run metadata) "
          "were read; no market data.", "",
          "## Bugs fixed before any evaluation", "",
          "- Timestamp-unit: pandas 3 keeps datetime64[s]; the gap check compared second integers with a nanosecond "
          "step and flagged every bar. Fixed (nanoseconds explicitly); regression test in --self-test.", "",
          "## Sealed holdout", "", "2026-07-15 onward: SEALED, not accessed.", ""]
    return "\n".join(L)


def static_contents() -> dict:
    return {"preregistration.md": preregistration_md(), "screener_rules.md": screener_rules_md(),
            "screener_output_schema.json": json.dumps(schema_doc(), indent=2, ensure_ascii=False),
            "screener_template.csv": ",".join(MATRIX_COLS) + "\n"}


def emit_static() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in static_contents().items():
        (OUT_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {name} sha256 {hashlib.sha256(text.encode('utf-8')).hexdigest()}")
    return 0


def _fmt(x):
    return "inf" if isinstance(x, float) and math.isinf(x) else x


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        o = float(o)
        return None if math.isnan(o) else ("inf" if math.isinf(o) else o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def write_csv(path: Path, rows, cols):
    df = pd.DataFrame(rows, columns=cols)
    for c in df.columns:
        if c.endswith("pf"):
            df[c] = [_fmt(x) for x in df[c]]
    df.to_csv(path, index=False, lineterminator="\n")


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def suitability_md(rows: list) -> str:
    L = ["# Strategy × coin suitability (descriptive, fixed order, no ranking)", "", f"> {WARNING}", "",
         "Each cell: raw_screen_status / protocol_status. Not a recommendation or an order of preference.", ""]
    for tf in TIMEFRAMES:
        L += [f"## {tf}", "", "| Book | Name | BTCUSDT | ETHUSDT | SOLUSDT |", "|---|---|---|---|---|"]
        for b in BOOK_IDS:
            cells = [next(f"{r['raw_screen_status']} / {r['protocol_status']}" for r in rows
                          if r["timeframe"] == tf and r["book_id"] == b and r["symbol"] == s) for s in SYMBOLS]
            L.append(f"| {b} | {BOOK_NAME[b]} | " + " | ".join(cells) + " |")
        L.append("")
    return "\n".join(L)


def main() -> int:
    script_sha = sha_file(SCRIPT_PATH)
    if (OUT_DIR / "verdict.json").exists():
        print(f"REFUSING TO RUN: {OUT_DIR / 'verdict.json'} already exists. Nothing was changed.")
        return 2
    leftovers = [n for n in RUN_FILES if (OUT_DIR / n).exists()]
    if leftovers:
        print(f"REFUSING TO RUN: run outputs already exist without verdict.json: {leftovers}. Ask the user.")
        return 2
    for name, text in static_contents().items():
        p = OUT_DIR / name
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            print(f"REFUSING TO RUN: {name} missing or differs from this script's pre-registration.")
            return 2
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"script sha256 {script_sha}")
    bars, hourly, funding, repairs, coverage, data_status = load_all()
    trades, rows, train = [], [], []
    with gzip.open(OUT_DIR / "fold_equity.csv.gz", "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(EQUITY_COLS) + "\n")

        def equity_writer(tf, b, s, led):
            if led:
                df = pd.DataFrame(led)
                df.insert(1, "timeframe", tf)
                df.insert(2, "book_id", b)
                df.insert(3, "symbol", s)
                df[EQUITY_COLS].to_csv(fh, header=False, index=False, lineterminator="\n")

        for tf in TIMEFRAMES:
            r, t = evaluate(tf, bars, hourly, funding, data_status, trades, equity_writer)
            rows += r
            train += t
    keys = [(r["timeframe"], r["book_id"], r["symbol"]) for r in rows]
    if keys != evaluation_keys():
        raise SystemExit("evaluation keys differ from the 114 pre-registered keys")
    write_csv(OUT_DIR / "strategy_coin_matrix.csv", rows, MATRIX_COLS)
    write_csv(OUT_DIR / "trades.csv", trades, TRADE_COLS)
    for c in coverage:
        if c["series"] == "ohlcv" and c.get("status") != "COMPLETE":
            c["affected_evaluation_ids"] = ";".join(affected_evaluation_ids(c["timeframe"], c["symbol"]))
    write_csv(OUT_DIR / "data_coverage.csv", coverage, COVERAGE_COLS)
    write_csv(OUT_DIR / "data_repairs.csv", repairs, REPAIR_COLS)
    (OUT_DIR / "strategy_coin_suitability.md").write_text(suitability_md(rows), encoding="utf-8")
    counts = {tf: {"raw_screen_status": {k: sum(1 for r in rows if r["timeframe"] == tf and r["raw_screen_status"] == k)
                                         for k in RAW_STATUS},
                   "protocol_status": {k: sum(1 for r in rows if r["timeframe"] == tf and r["protocol_status"] == k)
                                       for k in PROTOCOL_STATUS}} for tf in TIMEFRAMES}
    manifest = dict(script="backend/scripts/daily_direction_shortlist_mtf_prereg.py", script_sha256=script_sha,
                    daily_dependency_sha256=DAILY_SCRIPT_SHA256, command=RUN_COMMAND, started_utc=started,
                    finished_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__,
                    polars=pl.__version__, folds=[{k: str(v) for k, v in f.items()} for f in FOLDS],
                    sealed_start=str(SEALED_START), no_bar_at_or_after_sealed_start_read=True,
                    preregistration_files={n: sha_file(OUT_DIR / n) for n in STATIC_FILES},
                    data_status={tf: data_status[tf] for tf in data_status}, repairs=repairs,
                    train_checks=dict(windows=len(train), trades=sum(t["trades"] for t in train)),
                    outputs={n: sha_file(OUT_DIR / n) for n in RUN_FILES[:6]})
    (OUT_DIR / "run_manifest.json").write_text(json.dumps(_jsonable(manifest), indent=2), encoding="utf-8")
    verdict = dict(evaluations=N_EVALUATIONS, status_counts=counts, script_sha256=script_sha,
                   daily_dependency_sha256=DAILY_SCRIPT_SHA256,
                   rows=[{k: r[k] for k in ("evaluation_id", "raw_screen_status", "protocol_status")} for r in rows],
                   warning=WARNING, holdout_status="SEALED_NOT_ACCESSED", trading_recommendation="NONE_RESEARCH_ONLY",
                   shared_account_analysis="OUT_OF_SCOPE", ranking="NONE")
    (OUT_DIR / "verdict.json").write_text(json.dumps(_jsonable(verdict), indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    print("done:", json.dumps(counts))
    return 0


# ---------------------------------------------------------------------------------------------
# Self-test (synthetic data only; never loads real data, never writes outputs)
# ---------------------------------------------------------------------------------------------
def _synthetic_hourly(n_hours: int, seed: int = 0, start: str = "2021-06-01") -> pd.DataFrame:
    r = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n_hours, freq="h", tz="UTC")
    c = 30000 * np.exp(np.cumsum(r.normal(0.00002, 0.006, n_hours)))
    o = np.r_[c[0], c[:-1]] * np.exp(r.normal(0, 0.0008, n_hours))
    h = np.maximum(o, c) * np.exp(np.abs(r.normal(0, 0.002, n_hours)))
    l = np.minimum(o, c) * np.exp(-np.abs(r.normal(0, 0.002, n_hours)))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=1.0, quote_volume=1.0), index=idx)


def _flat_frame(n: int, freq: str = "4h", start: str = "2024-01-01") -> pd.DataFrame:
    ti = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    base = np.full(n, 100.0)
    return pd.DataFrame(dict(open=base, high=base + 1, low=base - 1, close=base, volume=1.0), index=ti)


def _funding(index_start, periods, rate=0.001, freq="8h", interval=8.0, start_offset="0h"):
    fset = pd.date_range(pd.Timestamp(index_start) + pd.Timedelta(start_offset), periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame(dict(rate=np.full(len(fset), rate), documented_interval_hours=interval,
                             record_source="archive"), index=fset)


def _hourly_open(df: pd.DataFrame, extra_hours: int = 8) -> pd.DataFrame:
    hix = pd.date_range(df.index[0], df.index[-1] + pd.Timedelta(hours=extra_hours), freq="h", tz="UTC")
    return pd.DataFrame(dict(open=100.0), index=hix)


def _ledger(df, state, trig=None, kind="state", i0=10, atr=2.0, fs=None, risk=None, bar=pd.Timedelta(hours=4)):
    """simulate_ledger with a constant ATR (stop distance 5)."""
    sig = dict(state=state, trig=np.zeros(len(df)) if trig is None else trig, kind=kind)
    rk = np.full(len(df), RISK) if risk is None else risk
    global atr_wilder
    orig = atr_wilder
    atr_wilder = lambda h, l, c, n: np.full(len(c), atr)         # noqa: E731 (synthetic fixed ATR)
    try:
        return simulate_ledger("BTCUSDT", df, bar, sig, rk, i0, len(df) - 1, fs, "probe", 1)
    finally:
        atr_wilder = orig


def self_test() -> int:
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))

    slip = slip_for("BTCUSDT")
    H = _synthetic_hourly(24 * 700)
    bars4, _ = aggregate(H, "4h", 4)
    bars1d, _ = aggregate(H, "1D", 24)
    bars1 = H[["open", "high", "low", "close", "volume"]]
    b4 = build_books(bars4, bars1d, BAR["1d"])

    # --- scope, keys, folds
    keys = evaluation_keys()
    ok("114 evaluation keys (4h/1h x books 1..19 x BTC/ETH/SOL), unique, fixed order; 3 folds each",
       len(keys) == 114 and len(set(keys)) == 114 and keys[0] == ("4h", "1", "BTCUSDT")
       and keys[-1] == ("1h", "19", "SOLUSDT") and [f["fold"] for f in FOLDS] == [1, 2, 3])
    ok("canonical IDs: 14 ROC 90, 15 ROC 30, 16 252-bar, 17 available-history, 18 swing, 19 book-4/Donchian",
       BOOK_IDS == [str(i) for i in range(1, 20)] and BOOK_NAME["14"] == "ROC 90" and BOOK_NAME["15"] == "ROC 30"
       and BOOK_NAME["16"].startswith("252-bar") and BOOK_NAME["17"].startswith("Available-history")
       and BOOK_NAME["18"].startswith("Swing") and BOOK_NAME["19"].startswith("Book 4"))
    ok("warning text is the exact contract text",
       WARNING.startswith("These are 114 correlated strategy–timeframe–symbol historical screen evaluations")
       and WARNING.endswith("allocation decision or trading authorization."))

    # --- no lookahead and signal-bar exclusion
    for tf, d, htf, hb in (("4h", bars4, bars1d, BAR["1d"]), ("1h", bars1, bars4, BAR["4h"])):
        k = len(d) * 2 // 3
        d2 = d.copy()
        shock = np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.02, len(d) - k - 1)))
        for col in ("open", "high", "low", "close"):
            d2.iloc[k + 1:, d2.columns.get_loc(col)] = d2[col].to_numpy()[k + 1:] * shock
        d2["high"] = d2[["open", "high", "low", "close"]].max(axis=1)
        d2["low"] = d2[["open", "high", "low", "close"]].min(axis=1)
        htf2 = htf.copy()
        htf2.loc[htf2.index + hb > d.index[k] + BAR[tf], ["open", "high", "low", "close"]] *= 1.37
        a_, b_ = build_books(d, htf, hb), build_books(d2, htf2, hb)
        same = all(np.array_equal(a_[b]["state"][:k + 1], b_[b]["state"][:k + 1]) and
                   np.array_equal(a_[b]["trig"][:k + 1], b_[b]["trig"][:k + 1]) for b in BOOK_IDS)
        same &= np.array_equal(np.nan_to_num(a_["19"]["risk"][:k + 1], nan=-1),
                               np.nan_to_num(b_["19"]["risk"][:k + 1], nan=-1))
        ok(f"no lookahead ({tf}): later bars / later HTF bars change no state, trigger or book-19 risk up to k", same)
    t = len(bars4) - 50
    d3 = bars4.copy()
    d3.iloc[t, d3.columns.get_loc("high")] *= 1.5
    d3.iloc[t, d3.columns.get_loc("low")] *= 0.5
    b3 = build_books(d3, bars1d, BAR["1d"])
    ok("signal-bar exclusion: bar t's own high/low never change a signal at t (all 19 books)",
       all(b3[b]["state"][t] == b4[b]["state"][t] and b3[b]["trig"][t] == b4[b]["trig"][t] for b in BOOK_IDS))

    # --- adapters
    c4 = bars4["close"].to_numpy()
    m30 = pd.Series(c4).shift(1).rolling(30).mean().to_numpy()
    m30_4 = np.r_[np.full(4, np.nan), m30[:-4]]
    with np.errstate(invalid="ignore"):
        want6 = np.where(np.isnan(m30_4), np.nan, np.where((c4 > m30) & (m30 > m30_4), 1.0,
                                                           np.where((c4 < m30) & (m30 < m30_4), -1.0, 0.0)))
    ok("book 6: close[t] vs prev(SMA30), slope vs 4 bars earlier (manual recompute)",
       np.array_equal(np.nan_to_num(weinstein_stage_tf(c4), nan=9), np.nan_to_num(want6, nan=9)))
    trig7 = _donchian(c4, bars4["high"].to_numpy(), bars4["low"].to_numpy(), 20, 10)[0]
    ok("book 9: Donchian 20 trigger kept only when equal to the book 6 state",
       np.array_equal(b4["9"]["trig"], np.where((trig7 != 0) & (trig7 == b4["6"]["state"]), trig7, 0.0)))
    got = htf_cloud_state(bars1.index, bars4, BAR["4h"])
    top, bot, okc = ichimoku_cloud(bars4["high"].to_numpy(float), bars4["low"].to_numpy(float))
    c4h = bars4["close"].to_numpy(float)
    st4h = np.where(~okc, np.nan, np.where(c4h > top, 1.0, np.where(c4h < bot, -1.0, 0.0)))
    j = np.searchsorted((bars4.index + BAR["4h"]).as_unit("ns").asi8, bars1.index.as_unit("ns").asi8, side="right") - 1
    want13 = np.where(j >= 0, st4h[np.clip(j, 0, None)], np.nan)
    ok("book 13 (1h): completed 4h close-vs-own-cloud applies only from 1h bars opening at its close",
       np.array_equal(np.nan_to_num(got, nan=9), np.nan_to_num(want13, nan=9)) and np.isnan(got[:4]).all())
    got1d = htf_cloud_state(bars4.index, bars1d, BAR["1d"])
    ok("book 13 (4h): completed 1d cloud first appears at a 00:00 4h bar",
       bars4.index[int(np.argmax(~np.isnan(got1d)))].hour == 0)

    # --- book 19 warm-up blocking
    rv, q, el = rv_cut_prior365(c4)
    fin = np.flatnonzero(np.isfinite(rv))
    first_el = fin[0] + 365
    probe = [first_el, first_el + 37, len(c4) - 1]
    manual = [np.quantile(rv[p - 365:p], 0.70) for p in probe]
    ok("book 19: threshold = 70th pct of rv[t-365..t-1] (rv[t] excluded); eligible exactly from the bar after 365 "
       "finite prior values", np.allclose([q[p] for p in probe], manual, rtol=0, atol=1e-15) and not el[first_el - 1]
       and el[first_el] and el[first_el:].all())
    ok("book 19: WARMUP_BLOCKED -> no state, no trigger, no risk value (no 0.5% fallback) before eligibility",
       not (b4["19"]["state"][~b4["19"]["valid"]] != 0).any() and np.isnan(b4["19"]["risk"][~b4["19"]["valid"]]).all()
       and np.array_equal(b4["19"]["valid"], b4["4"]["valid"] & b4["7"]["valid"] & el)
       and set(np.unique(b4["19"]["risk"][b4["19"]["valid"]])) <= {RISK, RISK_LOW})
    rv_gap = rv.copy()
    rv_gap[first_el + 100] = np.nan
    q_gap = pd.Series(prev(rv_gap)).rolling(365, min_periods=365).quantile(0.7).to_numpy()
    ok("book 19: a missing rv observation blocks the next 365 bars (no reaching back across the gap)",
       np.isnan(q_gap[first_el + 101: first_el + 466]).all() and np.isfinite(q_gap[first_el + 466]))

    # --- Supertrend recurrence
    hh, ll, cc = bars4["high"].to_numpy(), bars4["low"].to_numpy(), c4
    a10 = atr_wilder(hh, ll, cc, 10)
    bu, bl = (hh + ll) / 2 + 3 * a10, (hh + ll) / 2 - 3 * a10
    fu, fl, dd_ = np.full(len(cc), np.nan), np.full(len(cc), np.nan), np.zeros(len(cc))
    for t_ in range(len(cc)):
        if np.isnan(a10[t_]):
            continue
        if t_ == 0 or np.isnan(fu[t_ - 1]):
            fu[t_], fl[t_] = bu[t_], bl[t_]
            continue
        fu[t_] = bu[t_] if (bu[t_] < fu[t_ - 1] or cc[t_ - 1] > fu[t_ - 1]) else fu[t_ - 1]
        fl[t_] = bl[t_] if (bl[t_] > fl[t_ - 1] or cc[t_ - 1] < fl[t_ - 1]) else fl[t_ - 1]
        dd_[t_] = 1 if (dd_[t_ - 1] <= 0 and cc[t_] > fu[t_ - 1]) else (
            -1 if (dd_[t_ - 1] >= 0 and cc[t_] < fl[t_ - 1]) else dd_[t_ - 1])
    flips = np.flatnonzero(b4["10"]["trig"] != 0)
    ok("Supertrend: independent recurrence equals the daily one; each trigger compares close[t] with fu/fl[t-1]",
       np.array_equal(dd_, supertrend_dir(hh, ll, cc, 10, 3.0)) and len(flips) > 5
       and all((cc[t_] > fu[t_ - 1]) if b4["10"]["trig"][t_] > 0 else (cc[t_] < fl[t_ - 1]) for t_ in flips))
    tf_ = flips[len(flips) // 2]
    d5 = bars4.copy()
    d5.iloc[tf_, d5.columns.get_loc("high")] *= 1.3
    d5.iloc[tf_, d5.columns.get_loc("low")] *= 0.7
    sd5 = supertrend_dir(d5["high"].to_numpy(), d5["low"].to_numpy(), d5["close"].to_numpy(), 10, 3.0)
    ok("Supertrend: bar t's high/low change the band stored at t but not the direction at t (trigger at t's close, "
       "fill at t+1 open)", sd5[tf_] == supertrend_dir(hh, ll, cc, 10, 3.0)[tf_])

    # --- engine parity with the frozen daily simulate (funding off)
    sd = D.SymbolData("BTCUSDT", bars4, None, None)
    par = True
    for b in ("1", "7", "10", "17", "18"):
        sig = dict(state=b4[b]["state"], trig=b4[b]["trig"], kind=BOOK_KIND[b])
        want, eqw, _, _ = D.simulate([sd], [sig], bars4.index[400], bars4.index[len(bars4) - 1],
                                     [np.full(len(bars4), RISK)])
        gotr, led, _ = simulate_ledger("BTCUSDT", bars4, BAR["4h"], sig, np.full(len(bars4), RISK), 400,
                                       len(bars4) - 1, None, "p", 1)
        par &= len(want) == len(gotr) and all(
            str(w["entry_time"]) == g_["entry_time"] and str(w["exit_time"]) == g_["exit_time"]
            and w["exit_reason"] == g_["exit_reason"] and abs(w["qty"] - g_["qty"]) < 1e-9
            and abs(w["net_pnl"] - g_["net_pnl"]) < 1e-6 for w, g_ in zip(want, gotr))
        par &= np.allclose(eqw.to_numpy(), [r["equity"] for r in led], atol=1e-6)
    ok("local ledger engine == frozen daily simulate (funding off): trades, qty, net and bar-close equity", par)

    # --- next-open fill, ATR timeframe, sizing ledger
    sig1 = dict(state=b4["1"]["state"], trig=b4["1"]["trig"], kind="state")
    trs, led, _ = simulate_ledger("BTCUSDT", bars4, BAR["4h"], sig1, np.full(len(bars4), RISK), 400, len(bars4) - 1,
                                  None, "p", 1)
    o4 = bars4["open"].to_numpy()
    idx4 = bars4.index
    atr4 = prev(atr_wilder(bars4["high"].to_numpy(), bars4["low"].to_numpy(), c4, 14))
    atr1 = prev(atr_wilder(bars1["high"].to_numpy(), bars1["low"].to_numpy(), bars1["close"].to_numpy(), 14))
    ok("next-open fill with slippage; stop = 2.5 x ATR14 of the tested timeframe",
       len(trs) > 5 and all(
           abs(x["entry_price"] - o4[idx4.get_loc(pd.Timestamp(x["entry_time"]))] * (1 + slip * x["side"])) < 1e-9
           and abs(abs(x["entry_price"] - x["stop_price"])
                   - 2.5 * atr4[idx4.get_loc(pd.Timestamp(x["signal_time"]))]) < 1e-6 for x in trs)
       and abs(np.nanmedian(atr4) / np.nanmedian(atr1) - 1) > 0.3)
    cash, ledger_ok = START_EQUITY, True
    for x in trs:
        dist = abs(x["entry_price"] - x["stop_price"])
        ledger_ok &= abs(x["qty"] - RISK * cash / dist) < 1e-9 * max(1.0, x["qty"])
        cash += x["net_pnl"]
    ident = all(abs(r["equity"] - (r["cash"] + r["unrealized_price_pnl"])) < 1e-9 for r in led)
    flat_ok = all(abs(r["unrealized_price_pnl"]) < 1e-12 for r in led if r["position_quantity"] == 0)
    ok("sizing ledger: at every entry qty = 0.5% x equity / dist with equity = cash (flat); equity identity holds; "
       "final equity = 10,000 + sum(net)", ledger_ok and ident and flat_ok and abs(led[-1]["equity"] - cash) < 1e-6)

    # --- funding ledger: posting time, trade attribution, no double count, drawdown
    n = 40
    df = _flat_frame(n)
    st = np.zeros(n)
    st[20:30] = 1.0                          # long fills at bar 21 open; exit scheduled at bar 30 close -> bar 31 open
    fs = FundingSeries(_funding(df.index[0], 200, rate=0.01), _hourly_open(df))
    trf, ledf, _ = _ledger(df, st, fs=fs)
    tr0 = trf[0]
    ent, ex_ = pd.Timestamp(tr0["entry_time"]).value, pd.Timestamp(tr0["exit_time"]).value
    expect_n = int(((fs.ts > ent) & (fs.ts <= ex_)).sum())
    ok("funding ledger: settlements entry < s <= exit-open posted at their time, attributed to the trade, not double "
       "counted (final cash = 10,000 + trade net)", tr0["exit_reason"] == "signal" and expect_n > 2
       and tr0["funding_settlements_included"] == expect_n
       and abs(tr0["funding"] + expect_n * tr0["qty"] * 100 * 0.01) < 1e-9
       and abs(ledf[-1]["cash"] - (START_EQUITY + tr0["net_pnl"])) < 1e-9
       and abs(ledf[-1]["cumulative_funding_posted"] - tr0["funding"]) < 1e-12)
    held = [r for r in ledf if r["trade_id"]]
    eqs = np.r_[START_EQUITY, [r["equity"] for r in ledf]]
    fm = fold_metrics(trf, ledf, 0)
    ok("funding enters marked equity at settlement time (equity steps down while price is flat); full-resolution "
       "drawdown includes it", len(held) > 3 and held[-1]["cumulative_funding_posted"] < held[0]["cumulative_funding_posted"]
       and all(abs(r["equity"] - (START_EQUITY - tr0["fees"] + (r["cumulative_funding_posted"])
                                  + r["unrealized_price_pnl"]) - (tr0["fees"] - FEE * tr0["qty"] * tr0["entry_price"]))
               < 1e-6 for r in held)
       and fm["max_drawdown"] >= -tr0["funding"] - 1e-9 and abs(fm["max_drawdown"] - (START_EQUITY - eqs.min())) < 1e-9)
    st_eq = np.zeros(n)
    st_eq[19:27] = 1.0                       # entry at bar 20 open (08:00), exit at bar 28 open (16:00)
    teq = _ledger(df, st_eq, fs=fs)[0][0]
    e_ns, x_ns = pd.Timestamp(teq["entry_time"]).value, pd.Timestamp(teq["exit_time"]).value
    fset_ = set(fs.ts.tolist())
    ok("equal timestamps: a settlement at the entry open (08:00) is excluded, one at the exit open (16:00) is "
       "included", pd.Timestamp(teq["entry_time"]).hour == 8 and pd.Timestamp(teq["exit_time"]).hour == 16
       and e_ns in fset_ and x_ns in fset_
       and teq["funding_settlements_included"] == int(((fs.ts > e_ns) & (fs.ts <= x_ns)).sum())
       and teq["funding_settlements_included"] == int(((fs.ts >= e_ns) & (fs.ts <= x_ns)).sum()) - 1)
    st_end = np.zeros(n)
    st_end[30:] = 1.0                        # held into the fold end
    trw = _ledger(df, st_end, fs=fs)[0][-1]
    last_close = df.index[-1].value + BAR["4h"].value
    ok("fold-end close: settlements up to and including the last bar's close are posted before the close exit",
       trw["exit_reason"] == "window_end" and last_close in set(fs.ts.tolist())
       and trw["funding_settlements_included"] == int(((fs.ts > pd.Timestamp(trw["entry_time"]).value)
                                                       & (fs.ts <= last_close)).sum()))

    # --- unresolved stop-bar funding (2h settlement grid inside 4h bars)
    df_s = _flat_frame(n)
    i_stop = 26
    df_s.iloc[i_stop, df_s.columns.get_loc("low")] = 90.0
    fs2 = FundingSeries(_funding(df_s.index[0], 400, rate=0.001, freq="2h", interval=2.0), _hourly_open(df_s))
    x = _ledger(df_s, st, fs=fs2)[0][0]
    so = df_s.index[i_stop].value
    sc = so + BAR["4h"].value
    want_unres = [int(v) for v in fs2.ts if so < v < sc]
    ok("unresolved stop-bar funding: the settlement strictly inside the stop bar is recorded (count, timestamp) and "
       "not posted; the one at the bar open is posted; the one at the close is excluded",
       x["exit_reason"] == "stop" and x["funding_timing_status"] == "TIMING_UNRESOLVED"
       and x["unresolved_settlement_count"] == len(want_unres) == 1
       and x["unresolved_settlement_timestamps"] == str(pd.Timestamp(want_unres[0], tz="UTC"))
       and x["funding_settlements_included"] == int(((fs2.ts > pd.Timestamp(x["entry_time"]).value)
                                                     & (fs2.ts <= so)).sum())
       and so in set(fs2.ts.tolist()) and sc in set(fs2.ts.tolist())
       and str(pd.Timestamp(sc, tz="UTC")) not in x["unresolved_settlement_timestamps"])

    # --- exit chronology, re-entry
    df = _flat_frame(n)
    st = np.zeros(n)
    st[20:25] = 1.0
    df.iloc[26, df.columns.get_loc("low")] = 90.0
    e = _ledger(df, st)[0][0]
    ok("chronology: exit scheduled at the previous close fills at the next open, not at the later intrabar stop",
       e["exit_reason"] == "signal" and e["exit_time"] == str(df.index[26])
       and abs(e["exit_price"] - 100 * (1 - slip)) < 1e-9)
    df = _flat_frame(n)
    df.iloc[26, df.columns.get_loc("open")] = 80.0
    df.iloc[26, df.columns.get_loc("low")] = 79.0
    e = _ledger(df, st)[0][0]
    ok("chronology: opening gap through the stop has priority (stop_gap at the open)",
       e["exit_reason"] == "stop_gap" and abs(e["exit_price"] - 80.0 * (1 - slip)) < 1e-9)
    df = _flat_frame(n)
    df.iloc[21, df.columns.get_loc("low")] = 90.0
    e = _ledger(df, st)[0][0]
    ok("chronology: a fresh entry is stopped during its own entry bar",
       e["exit_reason"] == "stop" and e["entry_time"] == e["exit_time"] == str(df.index[21]))
    st_rev = np.zeros(n)
    st_rev[20:25] = 1.0
    st_rev[25:] = -1.0
    trv = _ledger(_flat_frame(n), st_rev)[0]
    ok("chronology: no same-bar reversal (exit at bar 26 open, short entry at bar 27 open)",
       len(trv) >= 2 and trv[0]["exit_time"] == str(_flat_frame(n).index[26]) and trv[1]["side"] == -1
       and trv[1]["entry_time"] == str(_flat_frame(n).index[27]))
    df = _flat_frame(n)
    df.iloc[23, df.columns.get_loc("low")] = 90.0
    st_l = np.zeros(n)
    st_l[20:30] = 1.0
    trr = _ledger(df, st_l)[0]
    ok("re-entry (state book): rule still holds after a stop -> re-entry at the next open",
       len(trr) >= 2 and trr[0]["exit_reason"] == "stop" and trr[1]["entry_time"] == str(df.index[24]))
    trig = np.zeros(n)
    trig[20] = 1.0
    st_e = np.zeros(n)
    st_e[20:35] = 1.0
    ta = _ledger(df, st_e, trig, "event")[0]
    trig2 = trig.copy()
    trig2[29] = 1.0
    tb = _ledger(df, st_e, trig2, "event")[0]
    ok("re-entry (event book): no re-entry after a stop until a fresh trigger; then the next open",
       len(ta) == 1 and ta[0]["exit_reason"] == "stop" and len(tb) == 2 and tb[1]["entry_time"] == str(df.index[30]))
    try:
        _ledger(_flat_frame(n), st_l, risk=np.full(n, np.nan))
        blocked_ok = False
    except SystemExit:
        blocked_ok = True
    ok("engine refuses an entry whose risk is WARMUP_BLOCKED (no fallback size)", blocked_ok)

    # --- statuses
    F = lambda net: dict(net_pnl=net, max_drawdown_pct=1.0)                     # noqa: E731
    T = lambda *nets: [dict(net_pnl=v, funding=0.0) for v in nets]             # noqa: E731
    p_hit = pooled([F(100.0), F(-30.0), F(5.0)], [T(100.0), T(-40.0, 10.0), T(5.0)])
    p_fail = pooled([F(100.0), F(-30.0), F(0.0)], [T(100.0), T(-40.0, 10.0), []])
    ok("raw status: SCREEN_HIT needs pooled net > 0, PF > 1.10 and two folds strictly positive; a zero-trade fold "
       "is not positive", raw_status(p_hit) == "SCREEN_HIT" and raw_status(p_fail) == "SCREEN_FAIL")
    combos = {
        ("COMPLETE", "all eligible, funding complete and resolved"): protocol_status([True] * 3, True, "COMPLETE",
                                                                                     "RESOLVED"),
        ("PARTIAL", "late eligibility"): protocol_status([False, True, True], True, "COMPLETE", "RESOLVED"),
        ("PARTIAL", "funding series missing"): protocol_status([True] * 3, True, "MISSING", "NO_SERIES"),
        ("PARTIAL", "funding partial"): protocol_status([True] * 3, True, "PARTIAL", "RESOLVED"),
        ("PARTIAL", "timing unresolved"): protocol_status([True] * 3, True, "COMPLETE", "TIMING_UNRESOLVED"),
        ("DATA_INCOMPLETE", "gap"): protocol_status([True] * 3, False, "COMPLETE", "RESOLVED"),
        ("UNRESOLVED", "unknown timing label"): protocol_status([True] * 3, True, "COMPLETE", "SOMETHING_ELSE"),
        ("UNRESOLVED", "schedule unknown beats PARTIAL causes"): protocol_status([False, True, True], True,
                                                                                 "UNRESOLVED", "TIMING_UNRESOLVED"),
        ("DATA_INCOMPLETE", "beats UNRESOLVED"): protocol_status([True] * 3, False, "UNRESOLVED", "RESOLVED"),
    }
    ok("protocol status combinations and precedence DATA_INCOMPLETE > UNRESOLVED > PARTIAL > COMPLETE; no % cutoff",
       all(v == k[0] for k, v in combos.items()))
    ok("interpretation: only COMPLETE + SCREEN_HIT is an unqualified hit; partial raw hits are not promoted",
       interpretation("SCREEN_HIT", "COMPLETE").startswith("complete-data")
       and "not promoted" in interpretation("SCREEN_HIT", "PARTIAL")
       and interpretation("NOT_TESTED", "DATA_INCOMPLETE").startswith("not evaluated"))

    # --- data guards
    a4, dropped = aggregate(H.drop(H.index[100]), "4h", 4)
    ids4 = affected_evaluation_ids("4h", "SOLUSDT")
    ids1d = affected_evaluation_ids("1d", "SOLUSDT")
    ok("gap propagation: a 4h series gap blocks all 19 4h evaluations and 1h book 13; a 1d gap blocks 4h book 13",
       len(ids4) == 20 and "1h|13|SOLUSDT" in ids4 and ids1d == ["4h|13|SOLUSDT"])
    ok("incomplete 4h bucket dropped (not invented) and detected as a missing 4h bar",
       len(dropped) == 1 and len(missing_bars(a4.index, BAR["4h"], a4.index[-1])) == 1)
    Hs = H.iloc[:200].copy()
    Hs.index = Hs.index.as_unit("s")
    ok("gap detection works on second-resolution timestamps (timestamp-unit regression)",
       len(missing_bars(Hs.index, BAR["1h"], Hs.index[-1])) == 0
       and len(missing_bars(Hs.drop(Hs.index[50]).index, BAR["1h"], Hs.index[-1])) == 1)
    Hp = H.iloc[:500].copy()
    tp = Hp.index[202]                                   # 2021-06-09 10:00 -> 4h bucket 08:00
    pr = float(Hp["close"].iloc[201])
    true_bar = Hp.loc[tp].copy()
    Hp.loc[tp, ["open", "high", "low", "close", "volume"]] = [pr, pr, pr, pr, 0.0]
    pg = pd.DataFrame(dict(open=[true_bar["open"]], high=[true_bar["high"]], low=[true_bar["low"]],
                           close=[true_bar["close"]], volume=[7.0], quote_volume=[123.0], trade_count=[3.0]),
                      index=pd.DatetimeIndex([tp]))
    bucket = tp.floor("4h")
    hb = H[(H.index >= bucket) & (H.index < bucket + pd.Timedelta(hours=4))].copy()
    hb.loc[tp, "volume"] = 7.0
    arch_ok = dict(open=hb["open"].iloc[0], high=hb["high"].max(), low=hb["low"].min(), close=hb["close"].iloc[-1],
                   volume=hb["volume"].sum(), quote_volume=0.0)
    Hr, lg, ver, why = replace_placeholders(Hp, "BTCUSDT", fetch=lambda s_, t_: pg, archive=lambda s_, b_: arch_ok)
    ok("repair: placeholder replaced in memory when the repaired hours aggregate exactly to the native 4h archive "
       "bar; neighbour gaps and volume/quote ratio only logged (no continuity or ratio gate); input unchanged",
       ver and why == "" and lg[0]["status"] == "replaced" and Hr.at[tp, "volume"] == 7.0 and Hp.at[tp, "volume"] == 0
       and {"prev_close", "next_open", "volume_x_typical_over_quote", "provenance_chain"} <= set(lg[0]))
    arch_bad = dict(arch_ok, volume=arch_ok["volume"] * 1.5)
    Hr2, lg2, ver2, why2 = replace_placeholders(Hp, "BTCUSDT", fetch=lambda s_, t_: pg, archive=lambda s_, b_: arch_bad)
    Hr3, lg3, ver3, why3 = replace_placeholders(Hp, "BTCUSDT", fetch=lambda s_, t_: pg, archive=lambda s_, b_: None)
    ok("repair: archive mismatch or missing archive bar -> PROVENANCE_UNVERIFIED (no exception, original frame "
       "returned, reason logged)", not ver2 and "volume" in why2 and Hr2.at[tp, "volume"] == 0.0
       and lg2[0]["status"] == "provenance_unverified" and not ver3 and "unavailable" in why3)
    pg_jump = pg.assign(open=pr * 1.5, high=pr * 1.6, low=pr * 1.4, close=pr * 1.45)
    hj = hb.copy()
    hj.loc[tp, ["open", "high", "low", "close"]] = [pr * 1.5, pr * 1.6, pr * 1.4, pr * 1.45]
    arch_j = dict(open=hj["open"].iloc[0], high=hj["high"].max(), low=hj["low"].min(), close=hj["close"].iloc[-1],
                  volume=hj["volume"].sum(), quote_volume=0.0)
    _, lgj, verj, _ = replace_placeholders(Hp, "BTCUSDT", fetch=lambda s_, t_: pg_jump, archive=lambda s_, b_: arch_j)
    ok("repair: a large neighbour jump is not a gate (accepted when the archive agrees; jump logged)",
       verj and abs(lgj[0]["prev_close"] - pr) < 1e-9)

    # funding schedule coverage
    base_t = pd.Timestamp("2024-01-01", tz="UTC")
    hrs_f = pd.DataFrame(dict(open=100.0), index=pd.date_range(base_t, periods=24 * 40, freq="h", tz="UTC"))
    f_ok = _funding(base_t, 100)
    ns = lambda x: pd.Timestamp(x).value                                               # noqa: E731
    t0, t1 = ns(base_t + pd.Timedelta(hours=20)), ns(base_t + pd.Timedelta(days=5))
    c_ok = FundingSeries(f_ok, hrs_f).coverage(t0, t1)
    c_miss = FundingSeries(f_ok.drop(f_ok.index[6]), hrs_f).coverage(t0, t1)
    f_api = f_ok.copy()
    f_api.loc[f_api.index[8:], "documented_interval_hours"] = np.nan
    c_api = FundingSeries(f_api, hrs_f).coverage(t0, t1)
    f_chg = pd.concat([_funding(base_t, 10), _funding(base_t + pd.Timedelta(hours=76), 30, freq="4h", interval=4.0)])
    c_chg = FundingSeries(f_chg, hrs_f).coverage(t0, t1)
    f_odd = f_ok.copy()
    f_odd.index = f_odd.index.where(np.arange(len(f_odd)) != 6, f_odd.index[6] - pd.Timedelta(hours=2))
    c_odd = FundingSeries(f_odd, hrs_f).coverage(t0, t1)
    c_end = FundingSeries(f_ok, hrs_f).coverage(t0, ns(f_ok.index[-1] + pd.Timedelta(hours=1)))
    ok("funding schedule: documented spacing -> COMPLETE; a dropped record -> PARTIAL (1 missing); undocumented "
       "fapi-style rows -> UNRESOLVED; documented 8h->4h change -> COMPLETE; inconsistent spacing -> UNRESOLVED; "
       "exposure past the last record -> UNRESOLVED; no 1.01 tolerance",
       c_ok == ("COMPLETE", 0, 0) and c_miss[0] == "PARTIAL" and c_miss[1] == 1 and c_api[0] == "UNRESOLVED"
       and c_chg[0] == "COMPLETE" and c_odd[0] == "UNRESOLVED" and c_end[0] == "UNRESOLVED")

    try:
        assert_sealed(pd.DatetimeIndex([pd.Timestamp("2026-07-15", tz="UTC")]), "probe")
        sealed = False
    except SystemExit:
        sealed = True
    try:
        check_frame(_synthetic_hourly(48, start="2026-07-14"), "probe")
        sealed2 = False
    except SystemExit:
        sealed2 = True
    ok("sealed boundary: any bar at/after 2026-07-15 is a hard error; loader filters before collect",
       sealed and sealed2 and "_CUT_S" in load_hourly_raw.__code__.co_names)
    ok("folds end at the last bar of 2026-07-14", window_bounds(
        pd.date_range("2026-07-13", "2026-07-14 23:00", freq="h", tz="UTC"),
        pd.Timestamp("2026-07-13", tz="UTC"), pd.Timestamp("2026-07-14", tz="UTC"))[1] == 47)
    ok("schema: matrix has fold metrics x3, both status columns and funding timing columns; fold_equity has the "
       "full audit columns", all(f"fold{i}_net_pnl" in MATRIX_COLS for i in (1, 2, 3))
       and {"raw_screen_status", "protocol_status", "funding_timing_status", "unresolved_settlement_count"}
       <= set(MATRIX_COLS)
       and {"cash", "unrealized_price_pnl", "cumulative_funding_posted", "equity", "position_quantity", "trade_id",
            "evaluation_id", "fold"} <= set(EQUITY_COLS))

    _boundary_tests(ok)

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} self-checks passed")
    return 1 if failed else 0


def _boundary_tests(ok) -> None:
    """Source-read boundary on synthetic files that DO contain rows at/after the cutoff: every reader must push the
    cutoff predicate into the Parquet scan (checked on the lazy plan before collect), never call a full read, and
    return no sealed row; the Postgres repair query must refuse sealed timestamps before connecting and select only
    the requested rows."""
    import tempfile
    import types

    global KLINE_ROOT, ENV_FILE
    cut = _CUT_S
    plans, full_reads = [], []
    orig_collect, orig_read = pl.LazyFrame.collect, pl.read_parquet

    def rec_collect(self, *a, **k):
        plans.append(self.explain())
        return orig_collect(self, *a, **k)

    def no_full_read(*a, **k):
        full_reads.append(a)
        raise AssertionError("full parquet read attempted")

    old_root, old_env = KLINE_ROOT, ENV_FILE
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for sub in ("1h", "4h", "funding", "funding_api"):
            (root / sub).mkdir()
        t1h = np.arange(cut - 6 * 3600, cut + 6 * 3600, 3600, dtype=np.int64)
        t4h = np.array([cut - 8 * 3600, cut - 4 * 3600, cut, cut + 4 * 3600], dtype=np.int64)

        def kl(ts):
            n_ = len(ts)
            return pl.DataFrame({"time": ts, "open": np.full(n_, 1.0), "high": np.full(n_, 1.0),
                                 "low": np.full(n_, 1.0), "close": np.full(n_, 1.0), "volume": np.full(n_, 1.0),
                                 "quote_volume": np.full(n_, 1.0), "trade_count": np.ones(n_, dtype=np.int64)},
                                schema=KLINE_SCHEMA)
        kl(t1h).write_parquet(root / "1h" / "BTCUSDT.parquet")
        kl(t4h).write_parquet(root / "4h" / "BTCUSDT.parquet")
        tf_ = np.arange(cut - 24 * 3600, cut + 24 * 3600, 8 * 3600, dtype=np.int64)
        pl.DataFrame({"time": tf_, "interval_hours": np.full(len(tf_), 8.0), "rate": np.full(len(tf_), 1e-4)},
                     schema=D.FUNDING_SCHEMA).write_parquet(root / "funding" / "BTCUSDT.parquet")
        pl.DataFrame({"time": tf_ + 8 * 3600, "rate": np.full(len(tf_), 1e-4)},
                     schema=D.FUNDING_API_SCHEMA).write_parquet(root / "funding_api" / "BTCUSDT.parquet")
        KLINE_ROOT = root
        pl.LazyFrame.collect, pl.read_parquet = rec_collect, no_full_read
        try:
            h = load_hourly_raw("BTCUSDT")
            n_h = len(plans)
            f = load_funding_checked("BTCUSDT")
            n_f = len(plans)
            a_ok = native_4h_bar("BTCUSDT", pd.Timestamp(cut - 4 * 3600, unit="s", tz="UTC"))
            n_a = len(plans)
            a_cut = native_4h_bar("BTCUSDT", pd.Timestamp(cut, unit="s", tz="UTC"))
            a_straddle = native_4h_bar("BTCUSDT", pd.Timestamp(cut - 2 * 3600, unit="s", tz="UTC"))
            n_after = len(plans)
        finally:
            pl.LazyFrame.collect, pl.read_parquet = orig_collect, orig_read
            KLINE_ROOT = old_root
        # Only plans that read a file matter (in-memory frame operations also go through LazyFrame.collect).
        def scans(lo_, hi_):
            return [x for x in plans[lo_:hi_] if "Parquet SCAN" in x]

        def bounded(x, needle):
            sel = x[x.index("SELECTION"):] if "SELECTION" in x else ""
            return needle in sel

        s_h, s_f, s_a, s_r = scans(0, n_h), scans(n_h, n_f), scans(n_f, n_a), scans(n_a, n_after)
        lt_cut = f'(col("time")) < ({cut})'
        ok("read boundary (1h price): the single Parquet scan carries time < cutoff in its SELECTION before collect; "
           "pl.read_parquet is not used; no row at/after 2026-07-15 is returned. This is logical exclusion of rows, "
           "not a claim that row-group bytes containing excluded rows were unread",
           len(s_h) == 1 and bounded(s_h[0], lt_cut) and not full_reads and h.index.max() < SEALED_START
           and len(h) == 6)
        ok("read boundary (funding archive + fapi): both Parquet scans carry time < cutoff; no sealed settlement "
           "(incl. 2026-07-15 00:00) returned", len(s_f) == 2 and all(bounded(x, lt_cut) for x in s_f)
           and f.index.max() < SEALED_START and pd.Timestamp(cut, unit="s", tz="UTC") not in f.index)
        ok("read boundary (native 4h check): one scan for the requested bucket, its SELECTION bounds time + 4h <= "
           "cutoff; buckets at or straddling the cutoff are refused without any scan",
           a_ok is not None and len(s_a) == 1 and bounded(s_a[0], str(cut)) and bounded(s_a[0], "14400")
           and a_cut is None and a_straddle is None and not s_r and not full_reads)

        # Postgres repair query with a mocked asyncpg
        calls = {"connect": 0, "sql": [], "args": []}

        class FakeConn:
            async def fetch(self, sql, *args):
                calls["sql"].append(sql)
                calls["args"].append(args)
                if "information_schema" in sql:
                    return [(k, v) for k, v in {**PG_TYPES, "trade_count": "integer"}.items()]
                return [dict(time=t, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0,
                             quote_volume=1.0, trade_count=1) for t in args[1]]

            async def close(self):
                return None

        async def fake_connect(url):
            calls["connect"] += 1
            return FakeConn()

        env = root / ".env"
        env.write_text("DATABASE_URL=postgresql+asyncpg://u:p@localhost:5432/db\n", encoding="utf-8")
        old_mod = sys.modules.get("asyncpg")
        sys.modules["asyncpg"] = types.SimpleNamespace(connect=fake_connect)
        ENV_FILE = env
        try:
            try:
                fetch_postgres_bars("BTCUSDT", pd.DatetimeIndex([pd.Timestamp(cut, unit="s", tz="UTC")]))
                sealed_refused = False
            except SystemExit:
                sealed_refused = True
            refused_before_connect = calls["connect"] == 0
            want = pd.DatetimeIndex([pd.Timestamp("2024-10-28 20:00", tz="UTC")])
            got = fetch_postgres_bars("BTCUSDT", want)
        finally:
            ENV_FILE = old_env
            if old_mod is not None:
                sys.modules["asyncpg"] = old_mod
            else:
                sys.modules.pop("asyncpg", None)
        q = [x for x in calls["sql"] if "from public.ohlcv" in x]
        ok("read boundary (Postgres repair): a sealed timestamp is refused before connecting; the query selects only "
           "the requested open times, symbol, timeframe 1h and source binance_rest",
           sealed_refused and refused_before_connect and len(q) == 1 and "time = any($2" in q[0]
           and "timeframe='1h'" in q[0] and "source=$3" in q[0]
           and calls["args"][-1][0] == "BTCUSDT" and calls["args"][-1][2] == PG_SOURCE
           and [pd.Timestamp(x) for x in calls["args"][-1][1]] == list(want) and list(got.index) == list(want))

        class LeakConn(FakeConn):
            async def fetch(self, sql, *args):
                if "information_schema" in sql:
                    return [(k, v) for k, v in {**PG_TYPES, "trade_count": "integer"}.items()]
                sealed_t = pd.Timestamp("2026-07-15", tz="UTC").to_pydatetime()
                return [dict(time=sealed_t, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0,
                             quote_volume=1.0, trade_count=1)]

        async def leak_connect(url):
            return LeakConn()

        sys.modules["asyncpg"] = types.SimpleNamespace(connect=leak_connect)
        ENV_FILE = env
        leaked = None
        try:
            leaked = fetch_postgres_bars("BTCUSDT", want)
        except SystemExit:
            leaked = "refused"
        finally:
            ENV_FILE = old_env
            if old_mod is not None:
                sys.modules["asyncpg"] = old_mod
            else:
                sys.modules.pop("asyncpg", None)
        ok("read boundary (Postgres repair result): a returned timestamp at/after 2026-07-15 is rejected before the "
           "frame is returned, even when the request itself was before the seal", leaked == "refused")

        bad_root = root / "bad_schema"
        (bad_root / "1h").mkdir(parents=True)
        bad = kl(t1h[:3]).with_columns(pl.col("close").cast(pl.Utf8))
        bad.write_parquet(bad_root / "1h" / "ETHUSDT.parquet")
        KLINE_ROOT = bad_root
        try:
            load_hourly_raw("ETHUSDT")
            schema_refused = False
        except SystemExit:
            schema_refused = True
        finally:
            KLINE_ROOT = old_root
        ok("schema: correct column names with a non-numeric close are rejected; names do not substitute for dtypes",
           schema_refused)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true", help="synthetic-data checks only")
    ap.add_argument("--emit-static", action="store_true", help="write the pre-run documents (no data)")
    args = ap.parse_args()
    sys.exit(self_test() if args.self_test else emit_static() if args.emit_static else main())
