"""Per-bar regime.

threshold:  trend by 200 SMA + one ADX threshold; range split by ATR% percentile.
hysteresis: same, but a trend starts above adx_enter and ends only below adx_exit.
vote:       ADX gives strength; close vs EMA50, close vs EMA200, EMA50 slope and the
            close's position in the 20-bar Donchian channel each vote a direction.
            Strength with 3+ agreeing votes is a trend; strength without it is TRANSITION.

Each method returns `regime` (the label), `route` (the regime used to pick
strategies) and `size` (position size multiplier). Only TRANSITION makes route
and size differ from the label.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Sequence

from app.research.regime_lab import indicators as ind
from app.research.regime_lab.config import (
    CHOPPY,
    DOWNTREND,
    SIDEWAYS,
    TRANSITION,
    UPTREND,
    WARMUP,
    RegimeParams,
    Safeguards,
)

EMA_FAST = 50
EMA_SLOW = 200
SLOPE_BARS = 10
DONCHIAN_POS_BARS = 20
MIN_VOTES = 3


def classify(
    close: float,
    sma_value: float | None,
    adx_value: float | None,
    atr_rank: float | None,
    p: RegimeParams,
) -> str:
    if sma_value is None or adx_value is None or atr_rank is None:
        return WARMUP
    if adx_value > p.adx_threshold:
        return UPTREND if close > sma_value else DOWNTREND
    return CHOPPY if atr_rank >= p.atr_rank_threshold else SIDEWAYS


def _range_label(atr_rank: float, p: RegimeParams) -> str:
    return CHOPPY if atr_rank >= p.atr_rank_threshold else SIDEWAYS


def hysteresis_labels(closes, sma_s, adx_s, rank_s, p: RegimeParams, sg: Safeguards) -> list[str]:
    out: list[str] = []
    trending = False
    for c, s, a, r in zip(closes, sma_s, adx_s, rank_s):
        if s is None or a is None or r is None:
            out.append(WARMUP)
            continue
        if not trending and a > sg.adx_enter:
            trending = True
        elif trending and a < sg.adx_exit:
            trending = False
        out.append((UPTREND if c > s else DOWNTREND) if trending else _range_label(r, p))
    return out


def vote_labels(highs, lows, closes, adx_s, rank_s, p: RegimeParams) -> tuple[list[str], list[str]]:
    """Returns (labels, leans). A lean is the trend side TRANSITION lean towards, or ''."""
    ema_f = ind.ema(closes, EMA_FAST)
    ema_s = ind.ema(closes, EMA_SLOW)
    hi = ind.prior_high(highs, DONCHIAN_POS_BARS)
    lo = ind.prior_low(lows, DONCHIAN_POS_BARS)
    labels: list[str] = []
    leans: list[str] = []
    for i, c in enumerate(closes):
        need = (ema_f[i], ema_s[i], adx_s[i], rank_s[i], hi[i], lo[i])
        if any(v is None for v in need) or i < SLOPE_BARS or ema_f[i - SLOPE_BARS] is None:
            labels.append(WARMUP)
            leans.append("")
            continue
        top = max(hi[i], c)
        bottom = min(lo[i], c)
        pos = (c - bottom) / (top - bottom) if top > bottom else 0.5
        votes = (
            (1 if c > ema_f[i] else -1)
            + (1 if c > ema_s[i] else -1)
            + (1 if ema_f[i] > ema_f[i - SLOPE_BARS] else -1)
            + (1 if pos > 0.5 else -1)
        )
        lean = UPTREND if votes > 0 else DOWNTREND if votes < 0 else ""
        if adx_s[i] > p.adx_threshold:
            if votes >= MIN_VOTES:
                labels.append(UPTREND)
            elif votes <= -MIN_VOTES:
                labels.append(DOWNTREND)
            else:
                labels.append(TRANSITION)
        else:
            labels.append(_range_label(rank_s[i], p))
        leans.append(lean)
    return labels, leans


def confirm(labels: Sequence[str], bars: int) -> list[str]:
    """A label change takes effect only after the new label has held `bars` bars in a row."""
    if bars <= 1:
        return list(labels)
    out: list[str] = []
    current = WARMUP
    candidate = None
    run = 0
    for lab in labels:
        if lab == WARMUP:
            out.append(WARMUP)
            current, candidate, run = WARMUP, None, 0
            continue
        if current == WARMUP:
            current = lab
        elif lab == current:
            candidate, run = None, 0
        elif lab == candidate:
            run += 1
            if run >= bars:
                current, candidate, run = lab, None, 0
        else:
            candidate, run = lab, 1
            if run >= bars:
                current, candidate, run = lab, None, 0
        out.append(current)
    return out


def regime_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    p: RegimeParams,
    sg: Safeguards | None = None,
) -> dict[str, list]:
    sg = sg or Safeguards()
    sma_s = ind.sma(closes, p.sma_period)
    adx_s = ind.adx(highs, lows, closes, p.adx_period)
    atr_s = ind.atr(highs, lows, closes, p.atr_period)
    atr_pct = [None if a is None else a / c for a, c in zip(atr_s, closes)]
    rank_s = ind.percentile_rank(atr_pct, p.atr_rank_window)
    leans = [""] * len(closes)
    if sg.regime_method == "hysteresis":
        labels = hysteresis_labels(closes, sma_s, adx_s, rank_s, p, sg)
    elif sg.regime_method == "vote":
        labels, leans = vote_labels(highs, lows, closes, adx_s, rank_s, p)
    elif sg.regime_method == "threshold":
        labels = [classify(closes[i], sma_s[i], adx_s[i], rank_s[i], p) for i in range(len(closes))]
    else:
        raise ValueError(f"unknown regime_method {sg.regime_method}")
    labels = confirm(labels, sg.confirm_bars)
    route, size = route_and_size(labels, leans, sg)
    return {
        "regime": labels,
        "route": route,
        "size": size,
        "sma": sma_s,
        "adx": adx_s,
        "atr": atr_s,
        "atr_rank": rank_s,
    }


def route_and_size(labels: Sequence[str], leans: Sequence[str], sg: Safeguards) -> tuple[list[str], list[float]]:
    route: list[str] = []
    size: list[float] = []
    for lab, lean in zip(labels, leans):
        if lab == TRANSITION and sg.transition_size > 0 and lean:
            route.append(lean)
            size.append(sg.transition_size)
        else:
            route.append(lab)
            size.append(1.0)
    return route, size


def map_higher_timeframe(
    times: Sequence[datetime],
    bar_delta: timedelta,
    htf_times: Sequence[datetime],
    htf_delta: timedelta,
    htf_values: Sequence,
    missing,
) -> list:
    """For each bar, the value of the last higher-timeframe bar that closed by this bar's close."""
    closes_at = [t + htf_delta for t in htf_times]
    out = []
    for t in times:
        j = bisect_right(closes_at, t + bar_delta) - 1
        out.append(htf_values[j] if j >= 0 else missing)
    return out
