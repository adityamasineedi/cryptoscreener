"""Read-only structure labels for the research API. Not a trade input."""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.research.sd_structure.explicit_structure import (
    BASELINE_RULE_VERSION,
    CANDIDATE_PIVOT_ORDER,
    CANDIDATE_RULE_VERSION,
    audit_candles,
    label_structure_bars,
)
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings

STATES = ("bullish", "bearish", "transition", "range", "insufficient_data")
# Calculation history is the same tail the comparison script scores.
# Display coverage is only the latest 10 days of that already-labeled series.
CALCULATION_LIMITS = {"15m": 5760, "1h": 1440, "4h": 360}
DISPLAY_LIMITS = {"15m": 960, "1h": 240, "4h": 60}

BASELINE_DISCREPANCIES = (
    "Expiry counts bars of that timeframe (288 x 15m, 72 x 1h, 18 x 4h). A gap clears the designated swing, so expiry does not cross a hole.",
    "The swing detector stores confirmation at the confirmation candle's open. known_at is that candle's close.",
    "Each timeframe is labeled from its own closed bars. A 4h bar is not split into 15m bars.",
    "Baseline allows one candle to be both the new reversal high and the new reversal low. The distinct-pivot candidate does not.",
    "Range means the last two highs and last two lows match within 0.25 times ATR(14) and the close is inside them. It is not every sideways market. There is no minimum duration.",
)


def rule_settings(rule_version: str) -> dict[str, Any]:
    if rule_version == CANDIDATE_RULE_VERSION:
        return {"rule_version": rule_version, "distinct_reversal_pivots": True, "experimental": True}
    if rule_version != BASELINE_RULE_VERSION:
        raise ValueError(f"Unknown structure rule version: {rule_version}")
    return {"rule_version": rule_version, "distinct_reversal_pivots": False, "experimental": False}


def _fingerprint(symbol: str, timeframe: str, candles: list[dict[str, Any]]) -> str:
    first = candles[0] if candles else {}
    last = candles[-1] if candles else {}
    raw = "|".join(
        [
            symbol,
            timeframe,
            str(first.get("time") or first.get("open_time")),
            str(last.get("time") or last.get("open_time")),
            str(len(candles)),
            str(first.get("close")),
            str(last.get("close")),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def first_scored_open(labels: list[dict[str, Any]]) -> str | None:
    """First bar after the opening warm-up. Later gap resets stay in the scored set."""
    for row in labels:
        if row["initialization"] != "warming_up":
            return row["candle_open_time"]
    return None


def scored_opens(labels: list[dict[str, Any]]) -> list[str]:
    started = False
    opens: list[str] = []
    for row in labels:
        if not started:
            if row["initialization"] == "warming_up":
                continue
            started = True
        if row["candle_open_time"] is not None:
            opens.append(row["candle_open_time"])
    return opens


def holdout_scored_opens(labels: list[dict[str, Any]], recent_first_scored: str | None) -> list[str]:
    """Holdout bars strictly before the recent book's first scored timestamp."""
    opens = scored_opens(labels)
    if recent_first_scored is None:
        return opens
    return [opened for opened in opens if opened < recent_first_scored]


def assert_scored_sets_disjoint(recent: list[dict[str, Any]], holdout: list[dict[str, Any]]) -> dict[str, Any]:
    boundary = first_scored_open(recent)
    recent_scored = set(scored_opens(recent))
    hold_scored = holdout_scored_opens(holdout, boundary)
    overlap = sorted(set(hold_scored) & recent_scored)
    if overlap:
        raise AssertionError(f"Scored holdout overlaps the recent book at {overlap[:3]}")
    if boundary is not None and any(opened >= boundary for opened in hold_scored):
        raise AssertionError("Holdout scored timestamp is not before the recent first scored bar")
    return {
        "recent_first_scored_open": boundary,
        "recent_scored_bars": len(recent_scored),
        "holdout_scored_bars": len(hold_scored),
        "scored_intersection": len(overlap),
    }


async def structure_label_snapshot(
    symbol: str,
    *,
    rule_version: str = BASELINE_RULE_VERSION,
) -> dict[str, Any]:
    import time

    from app.research.service import _load_research_candles

    frames: dict[str, Any] = {}
    profile_ms: dict[str, float] = {}
    for timeframe, limit in CALCULATION_LIMITS.items():
        started = time.perf_counter()
        candles, _, _ = await _load_research_candles(
            symbol.upper(),
            timeframe,
            limit=limit,
            use_research_cache=False,
        )
        profile_ms[f"{timeframe}_load"] = round((time.perf_counter() - started) * 1000, 1)
        started = time.perf_counter()
        frames[timeframe] = await asyncio.to_thread(
            label_loaded_series,
            symbol.upper(),
            timeframe,
            candles,
            rule_version=rule_version,
            display_bars=DISPLAY_LIMITS[timeframe],
        )
        profile_ms[f"{timeframe}_label"] = round((time.perf_counter() - started) * 1000, 1)
    latest_known = [
        frames[name]["coverage"]["end_known_at"]
        for name in CALCULATION_LIMITS
        if frames[name]["coverage"]["end_known_at"]
    ]
    payload = {
        "symbol": symbol.upper(),
        "rule_version": rule_version,
        "experimental": rule_version == CANDIDATE_RULE_VERSION,
        "as_of": max(latest_known) if latest_known else None,
        "baseline_notes": list(BASELINE_DISCREPANCIES),
        "trade_connected": False,
        "timeframes": frames,
        "profile_ms": profile_ms,
    }
    started = time.perf_counter()
    import json

    encoded = json.dumps(payload, default=str)
    profile_ms["serialize"] = round((time.perf_counter() - started) * 1000, 1)
    payload["profile_ms"] = profile_ms
    payload["serialized_bytes"] = len(encoded)
    return payload


def _series_view(labels: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["state"] for row in labels)
    total = len(labels)
    warmup = 0
    for row in labels:
        if row["initialization"] == "warming_up":
            warmup += 1
        else:
            break
    return {
        "counts": {name: counts[name] for name in STATES},
        "denominator": total,
        "usable_bars": total,
        "warmup_bars": warmup,
        "scored_bars": total - warmup,
        "start_open": labels[0]["candle_open_time"] if labels else None,
        "end_open": labels[-1]["candle_open_time"] if labels else None,
        "end_known_at": labels[-1]["known_at"] if labels else None,
    }


def label_loaded_series(
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    *,
    rule_version: str = BASELINE_RULE_VERSION,
    display_bars: int | None = None,
) -> dict[str, Any]:
    settings = rule_settings(rule_version)
    as_of = datetime.now(timezone.utc)
    quality = audit_candles(candles, timeframe, as_of=as_of)
    usable = candles[:-1] if quality["unfinished_last_bar"] and len(candles) > 1 else list(candles)
    cfg = SignalConfig()
    swing_cfg = cfg.swing_for(timeframe)
    swings = detect_swings(
        usable,
        left=swing_cfg.swing_left_bars,
        right=swing_cfg.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=cfg.atr_period,
        minimum_swing_distance_atr=0.0,
        as_of_index=max(0, len(usable) - 1),
    )
    labels = label_structure_bars(
        usable,
        swings,
        right_bars=swing_cfg.swing_right_bars,
        timeframe=timeframe,
        distinct_reversal_pivots=bool(settings["distinct_reversal_pivots"]),
    )
    calculation = _series_view(labels)
    if display_bars is not None and 0 < display_bars < len(labels):
        shown = labels[-display_bars:]
    else:
        shown = labels
    display = _series_view(shown)
    # The displayed rows are the suffix of the calculation. They are not recomputed
    # from the short window, which would drop earlier swings and designated levels.
    for full_row, shown_row in zip(labels[-len(shown) :], shown):
        if full_row["candle_open_time"] != shown_row["candle_open_time"] or full_row["state"] != shown_row["state"]:
            raise RuntimeError("Display slice is not the calculation suffix")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "rule_version": settings["rule_version"],
        "experimental": settings["experimental"],
        "timezone": "UTC",
        "dataset_fingerprint": _fingerprint(symbol, timeframe, usable),
        "coverage": {
            "usable_bars": display["usable_bars"],
            "loaded_bars": len(candles),
            "calculation_bars": calculation["usable_bars"],
            "calculation_start_open": calculation["start_open"],
            "calculation_end_known_at": calculation["end_known_at"],
            "display_bars": display["usable_bars"],
            "dropped_unfinished_bar": bool(quality["unfinished_last_bar"]),
            "start_open": display["start_open"],
            "end_open": display["end_open"],
            "end_known_at": display["end_known_at"],
            "duplicates": quality["duplicates"],
            "gap_count": quality["gap_count"],
            "missing_bars": quality["missing_bars"],
            "warmup_bars": display["warmup_bars"],
            "scored_bars": display["scored_bars"],
            "calculation_warmup_bars": calculation["warmup_bars"],
            "calculation_scored_bars": calculation["scored_bars"],
        },
        "counts": display["counts"],
        "calculation_counts": calculation["counts"],
        "denominator": display["denominator"],
        "latest": shown[-1] if shown else None,
        "range_explanation": "matched swing boundaries",
        "pivot_order": CANDIDATE_PIVOT_ORDER if settings["experimental"] else "Baseline may complete a reversal from one candle's high and low.",
    }
