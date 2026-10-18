"""Funding for the 4h trend book on the Backtest tab (research only; no paper or live path).

Postgres ``funding_rates`` has no rows before 2026-10, so settlements come from the committed bundle
``funding_btc_eth_to_2026-07-14.csv`` (scripts/build_trend4h_funding_bundle.py): BTCUSDT and ETHUSDT,
strictly before the sealed holdout (2026-07-15). Rules are the pre-registered 4h/1h screen's
``FundingSeries`` / ``simulate_ledger`` (backend/scripts/daily_direction_shortlist_mtf_prereg.py,
sha256 263ecc52), applied to one fixed-$ trade at a time:

- a settlement is posted when entry open < ts <= bound: none at the entry open; the one at an exit open
  is posted before the exit; a window_end close posts through the bar's close;
- a settlement strictly inside the bar where an intrabar stop fills is TIMING_UNRESOLVED (not posted);
- mark = the 1h open at the settlement. On 4h bars that is the open of the bar opening at that instant;
  a settlement that does not fall on a loaded bar open has no mark here (unmarked, not posted -> PARTIAL).
  That includes one exactly at the close of the window's last bar (window_end): the screen read the
  next 1h open, which the tab does not load (and at 2026-07-15 00:00 is sealed);
- amount = -side x qty x rate x mark (a long pays when the rate is positive);
- coverage checks every settlement after the entry up to and including the first one after the bound
  against the DOCUMENTED interval; fapi rows (July 2026) have none, so exposure there is UNRESOLVED.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

BUNDLE = Path(__file__).with_name("funding_btc_eth_to_2026-07-14.csv")
BUNDLE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
HOLDOUT_START = pd.Timestamp("2026-07-15", tz="UTC")
COVERAGE_ORDER = ("COMPLETE", "PARTIAL", "UNRESOLVED", "MISSING")


@lru_cache(maxsize=None)
def bundled_funding(symbol: str) -> pd.DataFrame | None:
    """Settlements for ``symbol`` (index UTC ts; rate, documented_interval_hours), or None."""
    if symbol not in BUNDLE_SYMBOLS:
        return None
    f = pd.read_csv(BUNDLE, dtype={"symbol": str, "record_source": str})
    f = f[f["symbol"] == symbol]
    idx = pd.DatetimeIndex(pd.to_datetime(f["time"], utc=True)).as_unit("ns")
    out = pd.DataFrame({"rate": f["rate"].to_numpy(float),
                        "documented_interval_hours": f["documented_interval_hours"].to_numpy(float)}, index=idx)
    if len(out) and out.index.max() >= HOLDOUT_START:
        raise RuntimeError(f"{symbol}: funding bundle reaches the sealed holdout")
    if not out.index.is_monotonic_increasing or out.index.has_duplicates:
        raise RuntimeError(f"{symbol}: funding bundle not strictly increasing")
    return out


class FundingSchedule:
    """The screen's FundingSeries with the mark taken from the tested bars (see module docstring)."""

    def __init__(self, f: pd.DataFrame | None, bar_index: pd.DatetimeIndex, bar_open: np.ndarray):
        self.missing = f is None
        if self.missing:
            self.ts = np.zeros(0, dtype=np.int64)
            self.cpu = np.zeros(0)
            return
        self.ts = f.index.as_unit("ns").asi8
        b_ts = bar_index.as_unit("ns").asi8
        mark = np.full(len(self.ts), np.nan)
        if len(b_ts):
            j = np.searchsorted(b_ts, self.ts, side="left")
            jj = np.minimum(j, len(b_ts) - 1)
            on_open = (j < len(b_ts)) & (b_ts[jj] == self.ts)
            mark[on_open] = np.asarray(bar_open, dtype=float)[jj[on_open]]
        self.cpu = f["rate"].to_numpy(float) * mark           # cash per unit qty for a long (pays when > 0)
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
        """(status, missing_records, unknown_schedule_checks) for exposure (t0, t1]."""
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

    def trade(self, side: int, qty: float, entry_open_ns: int, bound_ns: int,
              stop_bar: tuple[int, int] | None = None) -> dict:
        """Funding of one trade held from the entry open through ``bound_ns`` (inclusive).

        ``stop_bar`` = (open_ns, close_ns) of the bar where an intrabar stop filled: settlements strictly
        between them are TIMING_UNRESOLVED (the stop time inside the bar is unknown)."""
        if self.missing:
            return dict(funding_usd=0.0, funding_settlements=0, funding_unmarked_settlements=0,
                        funding_coverage_status="MISSING", funding_missing_records=0,
                        funding_unknown_schedule_checks=0, funding_timing_status="NO_SERIES",
                        funding_unresolved_settlements=0)
        a = int(np.searchsorted(self.ts, entry_open_ns, side="right"))
        b = int(np.searchsorted(self.ts, bound_ns, side="right"))
        v = self.cpu[a:b]
        ok = np.isfinite(v)
        amount = float(-side * qty * v[ok].sum())
        unresolved = 0
        if stop_bar is not None:
            k0 = int(np.searchsorted(self.ts, stop_bar[0], side="right"))
            k1 = int(np.searchsorted(self.ts, stop_bar[1], side="left"))
            unresolved = max(0, k1 - k0)
        cov, n_miss, n_unknown = self.coverage(entry_open_ns, bound_ns)
        n_unmarked = int((~ok).sum())
        if n_unmarked and cov == "COMPLETE":
            cov = "PARTIAL"
        return dict(funding_usd=amount, funding_settlements=int(ok.sum()), funding_unmarked_settlements=n_unmarked,
                    funding_coverage_status=cov, funding_missing_records=n_miss,
                    funding_unknown_schedule_checks=n_unknown,
                    funding_timing_status="TIMING_UNRESOLVED" if unresolved else "RESOLVED",
                    funding_unresolved_settlements=unresolved)


def worst_coverage(trades: list[dict]) -> str | None:
    """Row-level funding status: the most severe trade status; a timing-unresolved trade counts as
    UNRESOLVED. None when no trade carries funding."""
    seen = [t for t in trades if t.get("funding_coverage_status") is not None]
    if not seen:
        return None
    worst = 0
    for t in seen:
        s = str(t["funding_coverage_status"])
        if t.get("funding_timing_status") == "TIMING_UNRESOLVED" and s in ("COMPLETE", "PARTIAL"):
            s = "UNRESOLVED"
        worst = max(worst, COVERAGE_ORDER.index(s))
    return COVERAGE_ORDER[worst]
