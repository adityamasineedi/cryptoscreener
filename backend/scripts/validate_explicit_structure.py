"""Validate the frozen structure rules. Does not retune them and does not trade."""

from __future__ import annotations

import asyncio
import json
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.research.sd_structure.explicit_structure import label_structure_bars
from app.research.service import _load_research_candles
from app.services.database import db_manager
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings

OUT = Path(__file__).resolve().parents[2] / "research_sandbox" / "explicit_structure_validation_20261010"
FOCUS = ("BTCUSDT", "LINKUSDT", "SOLUSDT", "TRXUSDT")
STATES = ("bullish", "bearish", "range", "transition", "insufficient_data")


def _stamp(candle: dict) -> str:
    value = candle.get("time") or candle.get("open_time")
    return str(value)


def _runs(labels: list[dict], predicate) -> list[int]:
    lengths: list[int] = []
    current = 0
    for row in labels:
        if predicate(row):
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    return lengths


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _transition_class(row: dict) -> str | None:
    if row["state"] != "transition":
        return None
    reason = row["reason"]
    if reason in {"support_broken", "resistance_broken"}:
        return "post_choch"
    if reason in {"support_expired", "resistance_expired"}:
        return "expired_level"
    if reason == "mixed_structure" and not row["last_event"]:
        return "initial_mixed"
    return "other_unresolved"


def _summarize(candles: list, labels: list[dict]) -> dict:
    counts = Counter(row["state"] for row in labels)
    total = len(labels)
    classes = ("initial_mixed", "post_choch", "expired_level", "other_unresolved")
    transition = {}
    for name in classes:
        lengths = _runs(labels, lambda row, name=name: _transition_class(row) == name)
        transition[name] = {
            "bars": sum(lengths),
            "runs": len(lengths),
            "median_bars": _median(lengths),
            "longest_bars": max(lengths) if lengths else 0,
        }
    range_lengths = _runs(labels, lambda row: row["state"] == "range")
    first_ready = next((i for i, row in enumerate(labels) if row["state"] != "insufficient_data"), None)
    return {
        "bars": total,
        "start": _stamp(candles[0]) if candles else None,
        "end": _stamp(candles[-1]) if candles else None,
        "counts": {name: counts[name] for name in STATES},
        "sum_counts": sum(counts.values()),
        "first_bar_past_insufficient": first_ready,
        "state_at_bar_8": labels[8]["state"] if len(labels) > 8 else None,
        "reason_at_bar_8": labels[8]["reason"] if len(labels) > 8 else None,
        "transition": transition,
        "range_runs": len(range_lengths),
        "range_median_bars": _median(range_lengths),
        "range_longest_bars": max(range_lengths) if range_lengths else 0,
        "range_one_bar_runs": sum(1 for length in range_lengths if length == 1),
    }


def _label(symbol: str, timeframe: str, candles: list, *, left: int = 3, right: int = 3, min_atr: float = 0.0, max_age: int | None = None) -> list[dict]:
    swings = detect_swings(
        candles,
        left=left,
        right=right,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=14,
        minimum_swing_distance_atr=0.0,
        as_of_index=len(candles) - 1,
    )
    return label_structure_bars(
        candles,
        swings,
        right_bars=right,
        timeframe=timeframe,
        max_age_bars=max_age,
        min_swing_atr=min_atr,
    )


def _agreement(left: list[dict], right: list[dict]) -> float:
    n = min(len(left), len(right))
    if not n:
        return 0.0
    same = sum(1 for i in range(n) if left[i]["state"] == right[i]["state"])
    return round(100.0 * same / n, 2)


def _changes(labels: list[dict]) -> int:
    return sum(1 for i in range(1, len(labels)) if labels[i]["state"] != labels[i - 1]["state"])


def _reversals(candles: list, labels: list[dict]) -> list[dict]:
    found = []
    for index, row in enumerate(labels):
        if row["state"] not in {"bullish", "bearish"} or row["last_event"] != "reversal_confirmed":
            continue
        if index and labels[index - 1]["state"] == row["state"]:
            continue
        pair = row["pair"] or {}
        found.append(
            {
                "knowable_at": _stamp(candles[index]),
                "state": row["state"],
                "previous_high": pair.get("previous_high"),
                "latest_high": pair.get("latest_high"),
                "previous_low": pair.get("previous_low"),
                "latest_low": pair.get("latest_low"),
                "support": row["supporting_swing"],
            }
        )
        if len(found) >= 4:
            break
    return found


async def _load(symbol: str, timeframe: str, limit: int, start: str | None = None, end: str | None = None) -> list:
    candles, _, _ = await _load_research_candles(
        symbol,
        timeframe,
        limit=limit,
        start_date=start,
        end_date=end,
        warmup_bars=0,
        use_research_cache=False,
    )
    return candles


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        books: dict[tuple[str, str], list] = {}
        for symbol in FOCUS:
            for timeframe, limit in (("15m", 5760), ("1h", 1440), ("4h", 360)):
                books[(symbol, timeframe)] = await _load(symbol, timeframe, limit)
        report: dict = {"range_rule": {
            "boundary": "Last two confirmed highs differ by at most 0.25 times ATR(14), and the last two confirmed lows do too. ATR must exist.",
            "containment": "The bar close is inside the inclusive band from the lower of those two lows to the higher of those two highs.",
            "minimum_duration_bars": 1,
            "note": "There is no extra persistence rule. One bar can be a range. A CHoCH or expiry pending state is not relabeled as a range.",
        }, "expiry_rule": {
            "default_bars": {"15m": 288, "1h": 72, "4h": 18},
            "behavior": "On the first bar where the designated swing was confirmed more than that many bars ago, the state becomes transition. That same swing cannot restore the trend. A swing confirmed after the expiry bar can start a new pair.",
        }}
        report["series"] = {}
        labels_by = {}
        for key, candles in books.items():
            symbol, timeframe = key
            labels = _label(symbol, timeframe, candles)
            labels_by[key] = labels
            report["series"][f"{symbol}:{timeframe}"] = _summarize(candles, labels)
            print(symbol, timeframe, report["series"][f"{symbol}:{timeframe}"]["counts"], "first", report["series"][f"{symbol}:{timeframe}"]["first_bar_past_insufficient"], flush=True)

        btc4 = labels_by[("BTCUSDT", "4h")]
        report["btc_4h_reversals"] = _reversals(books[("BTCUSDT", "4h")], btc4)

        base = labels_by[("BTCUSDT", "15m")]
        btc = books[("BTCUSDT", "15m")]
        sweep = []
        for age, name in ((96, "1d"), (288, "3d"), (672, "7d"), (10**9, "no_expiry")):
            labels = _label("BTCUSDT", "15m", btc, max_age=age)
            sweep.append({"expiry": name, "agreement_with_3d_pct": _agreement(base, labels), "state_changes": _changes(labels), "counts": _summarize(btc, labels)["counts"]})
        for left in (2, 3, 4):
            labels = _label("BTCUSDT", "15m", btc, left=left, right=left)
            sweep.append({"pivot": left, "agreement_with_3_pct": _agreement(base, labels), "state_changes": _changes(labels), "counts": _summarize(btc, labels)["counts"]})
        for min_atr in (0.0, 0.25, 0.5):
            labels = _label("BTCUSDT", "15m", btc, min_atr=min_atr)
            sweep.append({"min_swing_atr": min_atr, "agreement_with_0_pct": _agreement(base, labels), "state_changes": _changes(labels), "counts": _summarize(btc, labels)["counts"]})
        report["btc_15m_sweep"] = sweep

        gap_notes = []
        for symbol in ("SOLUSDT", "WLFIUSDT"):
            candles = await _load(symbol, "15m", 5760)
            labels = _label(symbol, "15m", candles)
            gaps = [i for i, row in enumerate(labels) if row["data_quality"] == "gap_before"]
            stitched = []
            origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for index, candle in enumerate(candles):
                copy = dict(candle)
                copy["time"] = origin + timedelta(minutes=15 * index)
                stitched.append(copy)
            silent = _label(symbol, "15m", stitched)
            gap_notes.append({
                "symbol": symbol,
                "gap_bars": len(gaps),
                "gap_times": [_stamp(candles[i]) for i in gaps[:6]],
                "state_on_gap": [labels[i]["state"] for i in gaps[:6]],
                "reason_on_gap": [labels[i]["reason"] for i in gaps[:6]],
                "bars_disagreeing_with_stitched_clock": sum(1 for i in range(len(labels)) if labels[i]["state"] != silent[i]["state"]),
            })
        report["gaps"] = gap_notes

        earlier = {}
        for symbol in FOCUS:
            candles = await _load(symbol, "15m", 7000, "2026-06-12", "2026-08-11")
            labels = _label(symbol, "15m", candles)
            earlier[symbol] = _summarize(candles, labels)
            print("earlier", symbol, earlier[symbol]["counts"], earlier[symbol]["start"], earlier[symbol]["end"], flush=True)
        report["earlier_untouched_15m"] = earlier

        rng = random.Random(20261010)
        windows = []
        labels = labels_by[("BTCUSDT", "15m")]
        candles = books[("BTCUSDT", "15m")]
        picks = [("random", index) for index in rng.sample(range(40, len(labels) - 40), 3)]
        for index, row in enumerate(labels):
            if row["last_event"] == "choch" and (not index or labels[index - 1]["last_event"] != "choch"):
                picks.append(("choch", index))
                break
        for index, row in enumerate(labels):
            if row["last_event"] == "reversal_confirmed" and (not index or labels[index - 1]["state"] != row["state"]):
                picks.append(("reversal", index))
                break
        for index, row in enumerate(labels):
            if row["reason"] in {"support_expired", "resistance_expired"} and (not index or labels[index - 1]["reason"] != row["reason"]):
                picks.append(("expiry", index))
                break
        sol = await _load("SOLUSDT", "15m", 5760)
        sol_labels = _label("SOLUSDT", "15m", sol)
        for index, row in enumerate(sol_labels):
            if row["data_quality"] == "gap_before":
                picks.append(("sol_gap", index))
                break
        candle_windows = []
        judged = []
        for kind, index in picks:
            source = sol if kind == "sol_gap" else candles
            source_labels = sol_labels if kind == "sol_gap" else labels
            start = max(0, index - 12)
            end = min(len(source), index + 6)
            candle_windows.append({
                "kind": kind,
                "focus_offset": index - start,
                "bars": [
                    {"t": _stamp(source[i]), "o": source[i].get("open"), "h": source[i].get("high"), "l": source[i].get("low"), "c": source[i].get("close")}
                    for i in range(start, end)
                ],
            })
            judged.append({
                "kind": kind,
                "time": _stamp(source[index]),
                "state": source_labels[index]["state"],
                "reason": source_labels[index]["reason"],
                "event": source_labels[index]["last_event"],
                "pair": source_labels[index]["pair"],
            })
        (OUT / "windows.json").write_text(json.dumps(candle_windows, indent=2), encoding="utf-8")
        (OUT / "window_labels.json").write_text(json.dumps(judged, indent=2), encoding="utf-8")
        (OUT / "summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("wrote", OUT, flush=True)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
