"""Phase 1: funding-data coverage. Summarises the only multi-year funding series in the repo (BTCUSDT, Binance fapi).

Run from repo root:  backend/.venv/Scripts/python.exe research/p1_funding.py
Writes research/out/p1/funding_btc.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "data" / "research_cache" / "external" / "BTCUSDT_funding_rate_binance_fapi_2022-10_2026-10.json"
OUT = ROOT / "research" / "out" / "p1"

d = pd.DataFrame(json.loads(SRC.read_text(encoding="utf-8")))
d["t"] = pd.to_datetime(d["fundingTime"], unit="ms", utc=True)
d["r"] = d["fundingRate"].astype(float)
d["year"] = d["t"].dt.year
g = d.groupby("year")["r"].agg(settlements="count", mean_pct=lambda s: round(s.mean() * 100, 4),
                               pct_positive=lambda s: round((s > 0).mean() * 100, 1),
                               p95_abs_pct=lambda s: round(s.abs().quantile(0.95) * 100, 4)).reset_index()
lines = [f"BTCUSDT funding settlements {d['t'].min():%Y-%m-%d} → {d['t'].max():%Y-%m-%d}: {len(d)} rows; "
         f"mean {d['r'].mean() * 100:.4f}% per settlement; {(d['r'] > 0).mean() * 100:.1f}% positive.", "",
         "| " + " | ".join(g.columns) + " |", "|" + "---|" * len(g.columns)]
lines += ["| " + " | ".join(str(x) for x in r) + " |" for r in g.itertuples(index=False)]
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "funding_btc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
