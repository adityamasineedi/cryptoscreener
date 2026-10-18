"""Build the funding settlement bundle for the 4h trend book on the Backtest tab (research only).

Postgres ``funding_rates`` has no rows before 2026-10, so the 4h book charges funding from a small
CSV committed next to the books: BTCUSDT and ETHUSDT settlements strictly before 2026-07-15 00:00 UTC
(the sealed holdout is never read; the Parquet scans carry the time predicate).

Source and merge policy are the ones of the pre-registered 4h/1h screen's ``load_funding_checked``
(backend/scripts/daily_direction_shortlist_mtf_prereg.py, sha256 263ecc52):
- archive rows (data.binance.vision fundingRate, regime-book ``funding``): ``interval_hours`` is the
  documented hours since the previous settlement;
- fapi /fundingRate rows (regime-book ``funding_api``, 2026-07): time and rate only, interval NOT
  documented (left empty, never inferred from spacing);
- overlapping settlements must agree on the rate; the archive row is kept.

Usage: python scripts/build_trend4h_funding_bundle.py [--root <regime_book_klines dir>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

SYMBOLS = ("BTCUSDT", "ETHUSDT")
CUT_S = 1784073600  # 2026-07-15 00:00:00 UTC; nothing at or after it is read
DEFAULT_ROOT = Path("D:/cryptoscreener-worktrees/regime-book/backend/data/regime_book_klines")
OUT = Path(__file__).resolve().parents[1] / "app" / "research" / "daily_trend" / "funding_btc_eth_to_2026-07-14.csv"


def load(root: Path, symbol: str) -> pl.DataFrame:
    parts = []
    a = pl.scan_parquet(root / "funding" / f"{symbol}.parquet").filter(pl.col("time") < CUT_S).collect()
    if a["time"].is_duplicated().any():
        raise SystemExit(f"{symbol} funding: duplicate settlements")
    parts.append(a.select("time", pl.col("interval_hours").alias("documented_interval_hours"), "rate",
                          pl.lit("archive").alias("record_source")))
    q = root / "funding_api" / f"{symbol}.parquet"
    if q.exists():
        b = pl.scan_parquet(q).filter(pl.col("time") < CUT_S).collect()
        if b["time"].is_duplicated().any():
            raise SystemExit(f"{symbol} funding_api: duplicate settlements")
        parts.append(b.select("time", pl.lit(None, dtype=pl.Float64).alias("documented_interval_hours"), "rate",
                              pl.lit("fapi_fundingRate").alias("record_source")))
    if len(parts) == 2:
        ov = parts[0].join(parts[1], on="time", how="inner")
        if ov.height and (ov["rate"] != ov["rate_right"]).any():
            raise SystemExit(f"{symbol}: funding sources disagree on overlapping settlements")
    f = pl.concat(parts).unique("time", keep="first", maintain_order=True).sort("time")
    if f.height == 0 or int(f["time"].max()) >= CUT_S:
        raise SystemExit(f"{symbol}: empty or sealed funding rows")
    return f.with_columns(pl.lit(symbol).alias("symbol"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args(argv)
    f = pl.concat([load(args.root, s) for s in SYMBOLS])
    out = f.select(
        pl.from_epoch("time", time_unit="s").dt.strftime("%Y-%m-%dT%H:%M:%SZ").alias("time"),
        "symbol",
        pl.col("rate").map_elements(repr, return_dtype=pl.String).alias("rate"),
        pl.col("documented_interval_hours").map_elements(repr, return_dtype=pl.String),
        "record_source",
    )
    out.write_csv(OUT)
    print(f"wrote {OUT} ({out.height} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
