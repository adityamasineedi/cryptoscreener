"""Walk-forward rotation over the 12 daily trend books (research only).

The rule is pre-registered in docs/daily_trend_rotation_prereg.md (sha256 24da2ebd..., 2026-10-18T07:42:09Z).
Do not change it without logging a new trial there. Selection on month start D uses only shadow trades
whose exit day is before D, so no decision sees the month it trades.
"""
from __future__ import annotations

import pandas as pd

from app.research.daily_trend.books import RISK, TAB_BOOKS, build_books, simulate_path

LOOKBACK_MONTHS = 6
TOP_K = 3
STATIC_BOOKS = len(TAB_BOOKS)


def net_r(t: dict, fee: float) -> float:
    """Net R of a shadow trade under fixed-risk sizing: price move and taker fees over the stop distance."""
    dist = abs(t["entry_price"] - t["stop_price"])
    gross = t["side"] * (t["exit_price"] - t["entry_price"])
    return (gross - fee * (t["entry_price"] + t["exit_price"])) / dist


def shadow_paths(d: pd.DataFrame, slip: float, i_end: int, books: dict) -> dict[int, list[dict]]:
    return {b: simulate_path(d, b, 1, i_end, slip, books) for b in TAB_BOOKS}


def first_rebalance(d: pd.DataFrame, books: dict) -> pd.Timestamp | None:
    firsts = []
    for b in TAB_BOOKS:
        valid = books[b]["valid"]
        if not valid.any():
            return None
        firsts.append(d.index[int(valid.argmax())])
    ready = max(firsts) + pd.DateOffset(months=LOOKBACK_MONTHS)
    month = ready.normalize().replace(day=1)
    return month if month >= ready.normalize() else month + pd.DateOffset(months=1)


def schedule(d: pd.DataFrame, shadows: dict[int, list[dict]], books: dict, fee: float, i_end: int) -> list[dict]:
    """One entry per month start D from the first rebalance through the last bar: scores and selected books."""
    start = first_rebalance(d, books)
    if start is None:
        return []
    idx = d.index
    last = idx[i_end]
    out = []
    D = start
    while D <= last:
        lo = D - pd.DateOffset(months=LOOKBACK_MONTHS)
        scores = {}
        for b in TAB_BOOKS:
            s = 0.0
            for t in shadows[b]:
                x = idx[t["exit_index"]]
                if lo <= x < D:
                    s += net_r(t, fee)
            scores[b] = s
        ranked = sorted((b for b in TAB_BOOKS if scores[b] > 0), key=lambda b: (-scores[b], b))
        out.append(dict(month=D, selected=ranked[:TOP_K], scores=scores))
        D = D + pd.DateOffset(months=1)
    return out


def rotation_items(d: pd.DataFrame, slip: float, fee: float, i_start: int, i_end: int,
                   books: dict | None = None) -> tuple[list[tuple[int, dict, float]], list[dict]]:
    """(book, shadow trade, risk scale) for every rotation trade entering inside [i_start, i_end], plus the
    monthly schedule. Risk scale = 1/TOP_K (book 19's own half-risk rule stays in the trade's risk_frac)."""
    books = books or build_books(d)
    shadows = shadow_paths(d, slip, i_end, books)
    sched = schedule(d, shadows, books, fee, i_end)
    idx = d.index
    items = []
    for k, m in enumerate(sched):
        nxt = sched[k + 1]["month"] if k + 1 < len(sched) else None
        for b in m["selected"]:
            for t in shadows[b]:
                e = idx[t["entry_index"]]
                if e >= m["month"] and (nxt is None or e < nxt) and t["entry_index"] >= i_start:
                    items.append((b, t, 1.0 / TOP_K))
    items.sort(key=lambda it: (it[1]["entry_index"], it[0]))
    return items, sched


def static_items(d: pd.DataFrame, slip: float, i_start: int, i_end: int,
                 books: dict | None = None) -> list[tuple[int, dict, float]]:
    """Benchmark: every book's shadow trades entering inside the window, each at 1/12 risk, no selection.
    Uses the same shadow paths as the rotation so the two differ only by selection."""
    books = books or build_books(d)
    shadows = shadow_paths(d, slip, i_end, books)
    items = [(b, t, 1.0 / STATIC_BOOKS) for b in TAB_BOOKS for t in shadows[b] if t["entry_index"] >= i_start]
    items.sort(key=lambda it: (it[1]["entry_index"], it[0]))
    return items


__all__ = ["LOOKBACK_MONTHS", "TOP_K", "RISK", "net_r", "schedule", "rotation_items", "static_items",
           "first_rebalance", "shadow_paths"]
