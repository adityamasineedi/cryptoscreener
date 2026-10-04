"""Entry triad, deviation metrics, and ENTRY_PRICE_CHECK (research-only)."""

from __future__ import annotations

import math
import os
from typing import Any, Sequence

from app.research.live_backtest_parity.constants import (
    DEFAULT_ENTRY_DEVIATION_THRESHOLD_PCT,
    DEVIATION_BUCKETS,
    ENTRY_PRICE_NOT_CHECKED,
    ENTRY_PRICE_STALE,
    ENTRY_PRICE_UNAVAILABLE,
    ENTRY_PRICE_VALID,
    PRICE_SOURCE_CLOSE,
    PRICE_SOURCE_MARK,
    PRICE_SOURCE_TICKER,
    PRICE_SOURCE_UNAVAILABLE,
    STALE_ENTRY,
)
from app.research.live_backtest_parity.models import EntryTriad


def research_entry_deviation_threshold_pct(
    override: float | None = None,
) -> float:
    """Configurable research-only threshold — never hard-codes a trade rule."""
    if override is not None:
        return float(override)
    try:
        from app.config import get_settings

        settings = get_settings()
        val = getattr(settings, "research_only_entry_deviation_threshold", None)
        if val is not None:
            return float(val)
    except Exception:  # noqa: BLE001
        pass
    env = os.environ.get("RESEARCH_ONLY_ENTRY_DEVIATION_THRESHOLD")
    if env is not None and str(env).strip() != "":
        return float(env)
    return float(DEFAULT_ENTRY_DEVIATION_THRESHOLD_PCT)


def difference_pct(live_signal_price: float, backtest_entry: float) -> float:
    """abs(live - backtest) / backtest × 100."""
    if backtest_entry == 0 or not math.isfinite(backtest_entry):
        raise ValueError("backtest_entry must be non-zero finite")
    return abs(float(live_signal_price) - float(backtest_entry)) / float(
        backtest_entry
    ) * 100.0


def live_vs_backtest_bps(live_signal_price: float, backtest_entry: float) -> float:
    """Signed basis points: (live - backtest) / backtest × 10000."""
    if backtest_entry == 0 or not math.isfinite(backtest_entry):
        raise ValueError("backtest_entry must be non-zero finite")
    return (
        (float(live_signal_price) - float(backtest_entry))
        / float(backtest_entry)
        * 10_000.0
    )


def build_entry_triad(
    *,
    backtest_entry: float | None,
    live_signal_price: float | None,
    paper_entry: float | None,
    price_source: str = PRICE_SOURCE_UNAVAILABLE,
) -> EntryTriad:
    """Preserve three distinct values — never overwrite one with another."""
    return EntryTriad(
        backtest_entry=float(backtest_entry) if backtest_entry is not None else None,
        live_signal_price=(
            float(live_signal_price) if live_signal_price is not None else None
        ),
        paper_entry=float(paper_entry) if paper_entry is not None else None,
        price_source=str(price_source or PRICE_SOURCE_UNAVAILABLE),
    )


def compute_entry_deviation(
    triad: EntryTriad,
) -> dict[str, float | None]:
    bt = triad.backtest_entry
    live = triad.live_signal_price
    if bt is None or live is None or bt == 0:
        return {
            "absolute_difference": None,
            "difference_pct": None,
            "live_vs_backtest_bps": None,
        }
    abs_diff = abs(float(live) - float(bt))
    return {
        "absolute_difference": abs_diff,
        "difference_pct": difference_pct(float(live), float(bt)),
        "live_vs_backtest_bps": live_vs_backtest_bps(float(live), float(bt)),
    }


def check_entry_price(
    triad: EntryTriad,
    *,
    threshold_pct: float | None = None,
) -> dict[str, Any]:
    """ENTRY_PRICE_CHECK — descriptive only; does not change strategy entry."""
    thr = research_entry_deviation_threshold_pct(threshold_pct)
    if triad.backtest_entry is None or triad.live_signal_price is None:
        return {
            "entry_price_status": ENTRY_PRICE_UNAVAILABLE,
            "entry_price_label": None,
            "threshold_pct": thr,
            "difference_pct": None,
        }
    dev = compute_entry_deviation(triad)
    pct = dev["difference_pct"]
    assert pct is not None
    if pct > thr:
        return {
            "entry_price_status": ENTRY_PRICE_STALE,
            "entry_price_label": STALE_ENTRY,
            "threshold_pct": thr,
            "difference_pct": pct,
        }
    return {
        "entry_price_status": ENTRY_PRICE_VALID,
        "entry_price_label": None,
        "threshold_pct": thr,
        "difference_pct": pct,
    }


def deviation_bucket(difference_pct_value: float | None) -> str | None:
    if difference_pct_value is None:
        return None
    v = float(difference_pct_value)
    for name, lo, hi in DEVIATION_BUCKETS:
        if hi is None:
            if v >= float(lo or 0.0):
                return name
        elif float(lo or 0.0) <= v < float(hi):
            return name
    return DEVIATION_BUCKETS[-1][0]


def percentile(sorted_vals: Sequence[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(sorted_vals[lo])
    w = rank - lo
    return float(sorted_vals[lo]) * (1.0 - w) + float(sorted_vals[hi]) * w


def deviation_distribution(values: Sequence[float | None]) -> dict[str, float | None]:
    cleaned = sorted(float(v) for v in values if v is not None and math.isfinite(v))
    if not cleaned:
        return {
            "P50": None,
            "P75": None,
            "P90": None,
            "P95": None,
            "P99": None,
            "MAX": None,
            "n": 0,
        }
    return {
        "P50": percentile(cleaned, 50),
        "P75": percentile(cleaned, 75),
        "P90": percentile(cleaned, 90),
        "P95": percentile(cleaned, 95),
        "P99": percentile(cleaned, 99),
        "MAX": float(cleaned[-1]),
        "n": len(cleaned),
    }


def resolve_live_price(
    symbol: str,
    *,
    fallback_close: float | None = None,
    injected: float | None = None,
    injected_source: str | None = None,
) -> tuple[float | None, str]:
    """Resolve live price with explicit source — never silent mix."""
    if injected is not None:
        return float(injected), str(injected_source or PRICE_SOURCE_CLOSE)
    try:
        from app.services.market_store import market_store

        mark = market_store.mark_prices.get(symbol.upper())
        if mark is not None and getattr(mark, "mark_price", None):
            return float(mark.mark_price), PRICE_SOURCE_MARK
        tick = market_store.get_ticker(symbol)
        if tick is not None and tick.price:
            return float(tick.price), PRICE_SOURCE_TICKER
    except Exception:  # noqa: BLE001
        pass
    if fallback_close is not None:
        return float(fallback_close), PRICE_SOURCE_CLOSE
    return None, PRICE_SOURCE_UNAVAILABLE


def entry_triad_snippet_fields(triad: EntryTriad, check: dict[str, Any]) -> dict[str, Any]:
    """Fields to attach on paper/alert snippets without overwriting strategy entry."""
    return {
        "backtest_entry": triad.backtest_entry,
        "live_signal_price": triad.live_signal_price,
        "paper_entry": triad.paper_entry,
        "price_source": triad.price_source,
        "entry_deviation_pct": check.get("difference_pct"),
        "entry_price_status": check.get(
            "entry_price_status", ENTRY_PRICE_NOT_CHECKED
        ),
        "entry_price_label": check.get("entry_price_label"),
        "research_only_entry_deviation_threshold": check.get("threshold_pct"),
    }
