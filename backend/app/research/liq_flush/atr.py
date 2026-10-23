"""Wilder ATR(14), the same recursion as research daily_trend.books.atr_wilder.

ATR at index t uses true range through bar t (E34: "as of bar t"). The first
13 values are undefined. The seed is the pandas ewm start (y0 = TR0), not an
SMA of the first 14 true ranges.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from app.research.liq_flush.costs import ATR_PERIOD


def atr_wilder(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = ATR_PERIOD) -> np.ndarray:
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    if not (len(h) == len(l) == len(c)):
        raise ValueError("high, low and close must be the same length")
    pc = np.full(len(c), np.nan)
    if len(c):
        pc[1:] = c[:-1]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    if len(tr):
        tr[0] = h[0] - l[0]
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def atr_at(high: Sequence[float], low: Sequence[float], close: Sequence[float], index: int, n: int = ATR_PERIOD) -> float | None:
    if index < 0:
        return None
    series = atr_wilder(high, low, close, n)
    if index >= len(series):
        return None
    value = float(series[index])
    if not math.isfinite(value):
        return None
    return value
