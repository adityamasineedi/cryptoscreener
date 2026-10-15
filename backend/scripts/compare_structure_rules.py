"""Baseline versus distinct-pivot candidate. Research report only. Not a trade result."""

from __future__ import annotations

import asyncio
import json
import random
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.research.sd_structure.explicit_structure import BASELINE_RULE_VERSION, CANDIDATE_RULE_VERSION
from app.research.sd_structure.labels_api import (
    assert_scored_sets_disjoint,
    first_scored_open,
    holdout_scored_opens,
    label_loaded_series,
)
from app.research.service import _load_research_candles
from app.research.sd_structure.explicit_structure import label_structure_bars
from app.services.database import db_manager
from app.signals.config import SignalConfig
from app.signals.swing_detector import detect_swings

OUT = Path(__file__).resolve().parents[2] / "research_sandbox" / "structure_rule_comparison_20261010"
SYMBOLS = ("BTCUSDT", "LINKUSDT", "SOLUSDT", "TRXUSDT")
STATES = ("bullish", "bearish", "transition", "range", "insufficient_data")


def _both(symbol: str, timeframe: str, candles: list) -> tuple[list[dict], list[dict]]:
    cfg = SignalConfig()
    swing = cfg.swing_for(timeframe)
    detected = detect_swings(
        candles,
        left=swing.swing_left_bars,
        right=swing.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=cfg.atr_period,
        minimum_swing_distance_atr=0.0,
        as_of_index=len(candles) - 1,
    )
    baseline = label_structure_bars(candles, detected, right_bars=swing.swing_right_bars, timeframe=timeframe)
    candidate = label_structure_bars(
        candles,
        detected,
        right_bars=swing.swing_right_bars,
        timeframe=timeframe,
        distinct_reversal_pivots=True,
    )
    return baseline, candidate


def _runs(labels: list[dict], name: str) -> dict:
    lengths: list[int] = []
    current = 0
    for row in labels:
        if row["reason"] == name and row["state"] == "transition":
            current += 1
        elif current:
            lengths.append(current)
            current = 0
    if current:
        lengths.append(current)
    ordered = sorted(lengths)
    median = None if not ordered else float(ordered[len(ordered) // 2])
    return {"runs": len(lengths), "bars": sum(lengths), "median_bars": median, "longest_bars": max(lengths) if lengths else 0}


def _delay(labels: list[dict]) -> dict:
    waits: list[int] = []
    open_breaks = 0
    index = 0
    while index < len(labels):
        row = labels[index]
        if row["last_event"] == "choch" and row["bars_since_break"] == 0:
            end = index
            resolved = False
            while end < len(labels) and labels[end]["bars_since_break"] is not None and labels[end]["break_open_time"] == row["break_open_time"]:
                if labels[end]["last_event"] == "reversal_confirmed" and labels[end]["state"] in {"bullish", "bearish"}:
                    waits.append(end - index)
                    resolved = True
                    break
                end += 1
            if not resolved:
                open_breaks += 1
            index = max(end, index + 1)
        else:
            index += 1
    ordered = sorted(waits)
    return {
        "resolved": len(waits),
        "unresolved_at_window_end": open_breaks,
        "median_bars": None if not ordered else float(ordered[len(ordered) // 2]),
        "longest_bars": max(waits) if waits else 0,
    }


def _compare(baseline: list[dict], candidate: list[dict]) -> dict:
    matrix = {left: {right: 0 for right in STATES} for left in STATES}
    changed = Counter()
    same_candle = 0
    candidate_rejected = 0
    candidate_promoted = 0
    for left, right in zip(baseline, candidate):
        matrix[left["state"]][right["state"]] += 1
        if left["state"] != right["state"]:
            changed[f"{left['state']}->{right['state']}"] += 1
        if "same_candle_reversal_pivots" in left["ambiguity_flags"]:
            same_candle += 1
            if right["state"] == "transition" and left["state"] in {"bullish", "bearish"}:
                candidate_rejected += 1
            elif right["state"] == left["state"]:
                candidate_promoted += 0
    agree = {name: matrix[name][name] for name in STATES}
    return {
        "denominator": len(baseline),
        "confusion_baseline_rows_candidate_columns": matrix,
        "per_state_agreement": agree,
        "changed_bars": dict(changed),
        "same_candle_bars": same_candle,
        "same_candle_rejected_by_candidate": candidate_rejected,
    }


async def _load(symbol: str, timeframe: str, limit: int, start: str | None = None, end: str | None = None) -> list:
    candles, _, _ = await _load_research_candles(
        symbol, timeframe, limit=limit, start_date=start, end_date=end, warmup_bars=0, use_research_cache=False
    )
    return candles


def _trace_row(index: int, left: dict, right: dict) -> dict:
    pair = left.get("pair") or {}
    latest_high = pair.get("latest_high") or {}
    latest_low = pair.get("latest_low") or {}
    return {
        "index": index,
        "open": left["candle_open_time"],
        "known_at": left["known_at"],
        "baseline_state": left["state"],
        "baseline_reason": left["reason"],
        "baseline_event": left["last_event"],
        "candidate_state": right["state"],
        "candidate_reason": right["reason"],
        "candidate_event": right["last_event"],
        "baseline_flags": left["ambiguity_flags"],
        "candidate_flags": right["ambiguity_flags"],
        "same_pivot_timestamps": latest_high.get("pivot_time") == latest_low.get("pivot_time"),
        "high_pivot": latest_high.get("pivot_time"),
        "low_pivot": latest_low.get("pivot_time"),
        "pair_swing_ids_match": left.get("pair") == right.get("pair"),
    }


def _path_traces(baseline: list[dict], candidate: list[dict]) -> dict:
    """Show that state differences start at a same-candle acceptance and then carry forward."""
    rejections = []
    episodes = []
    first = None
    pair_mismatches = 0
    for index, (left, right) in enumerate(zip(baseline, candidate)):
        if left.get("pair") != right.get("pair"):
            pair_mismatches += 1
        row = _trace_row(index, left, right)
        rejected = (
            "candidate_rejected_same_candle_reversal" in right["ambiguity_flags"]
            and left["state"] != right["state"]
        )
        if rejected:
            rejections.append(row)
        if left["state"] == right["state"]:
            continue
        if first is None:
            first = row
        if left["state"] == "transition" and right["state"] in {"bullish", "bearish"}:
            if (
                episodes
                and episodes[-1]["end_index"] == index - 1
                and episodes[-1]["candidate_state"] == right["state"]
            ):
                episodes[-1]["end_index"] = index
                episodes[-1]["bars"] += 1
                episodes[-1]["end_open"] = left["candle_open_time"]
            else:
                prior = [item for item in rejections if item["index"] < index]
                episodes.append(
                    {
                        **row,
                        "end_index": index,
                        "end_open": left["candle_open_time"],
                        "bars": 1,
                        "preceding_same_candle_rejection": prior[-1] if prior else None,
                    }
                )
    carried = []
    if first is not None:
        for index, (left, right) in enumerate(zip(baseline, candidate)):
            if index < first["index"]:
                continue
            previous_left = baseline[index - 1]["state"] if index else None
            previous_right = candidate[index - 1]["state"] if index else None
            if index == first["index"] or left["state"] != previous_left or right["state"] != previous_right:
                carried.append(_trace_row(index, left, right))
            if len(carried) >= 8:
                break
    return {
        "rule_difference": "candidate sets reversal false when the new high and new low share a pivot timestamp",
        "pair_mismatches": pair_mismatches,
        "first_divergence": first,
        "first_divergence_is_same_candle_rejection": bool(
            first and "candidate_rejected_same_candle_reversal" in first["candidate_flags"]
        ),
        "same_candle_rejections": rejections,
        "baseline_transition_candidate_trend": episodes,
        "path_after_first_divergence": carried,
    }


def _hidden_window(candles: list, index: int) -> dict:
    start = max(0, index - 16)
    return {
        "review_cutoff_open": str(candles[index].get("time")),
        "bars": [
            {
                "t": str(candles[i].get("time")),
                "o": candles[i].get("open"),
                "h": candles[i].get("high"),
                "l": candles[i].get("low"),
                "c": candles[i].get("close"),
            }
            for i in range(start, index + 1)
        ],
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        report: dict = {
            "baseline_rule_version": BASELINE_RULE_VERSION,
            "candidate_rule_version": CANDIDATE_RULE_VERSION,
            "timezone": "UTC",
            "trade_connected": False,
            "forward_validation": "pending. Stored bars end on the current date, so there is no later unseen period.",
            "holdout_note": (
                "12 Jun 2026 through 11 Aug 2026 is an earlier holdout, not forward validation. "
                "Scored holdout timestamps are strictly before that symbol's first scored bar "
                "in the recent book. Warm-up bars are excluded from both scored sets."
            ),
            "candidate_note": (
                "On this sample the candidate is a same-candle ambiguity refinement. "
                "It does not shorten CHoCH confirmation delay and it is not an accuracy result."
            ),
            "series": [],
        }
        hidden = []
        rng = random.Random(20261010)
        for symbol in SYMBOLS:
            candles = await _load(symbol, "15m", 5760)
            baseline, candidate = _both(symbol, "15m", candles)
            identity = label_loaded_series(symbol, "15m", candles)
            scored_from = 0
            for index, row in enumerate(baseline):
                if row["initialization"] != "warming_up":
                    scored_from = index
                    break
            holdout = await _load(symbol, "15m", 7000, "2026-06-12", "2026-08-11")
            hold_base, hold_cand = _both(symbol, "15m", holdout)
            separation = assert_scored_sets_disjoint(baseline, hold_base)
            allowed = set(holdout_scored_opens(hold_base, first_scored_open(baseline)))
            scored_hold = [index for index, row in enumerate(hold_base) if row["candle_open_time"] in allowed]
            report_comparison = _compare(baseline, candidate)
            changed_bars = sum(report_comparison["changed_bars"].values())
            report["series"].append(
                {
                    "symbol": symbol,
                    "timeframe": "15m",
                    "fingerprint": identity["dataset_fingerprint"],
                    "start_open": identity["coverage"]["end_open"] and baseline[0]["candle_open_time"],
                    "end_known_at": baseline[-1]["known_at"],
                    "usable_bars": len(baseline),
                    "warmup_bars": identity["coverage"]["warmup_bars"],
                    "scored_bars": len(baseline) - scored_from,
                    "baseline_counts": identity["counts"],
                    "candidate_counts": Counter(row["state"] for row in candidate),
                    "denominator": len(baseline),
                    "comparison": report_comparison,
                    "changed_bar_share": changed_bars / len(baseline) if baseline else None,
                    "candidate_path": _path_traces(baseline, candidate),
                    "baseline_choch_delay": _delay(baseline),
                    "candidate_choch_delay": _delay(candidate),
                    "baseline_transition_reasons": {
                        name: _runs(baseline, name)
                        for name in ("support_broken", "resistance_broken", "support_expired", "resistance_expired", "mixed_structure", "gap_in_history")
                    },
                    "gap_resets": sum(1 for row in baseline if row["initialization"] == "gap_reset"),
                    "expiry_events": sum(
                        1
                        for row in baseline
                        if row["expiry_status"] == "expired" and row["state_age"] == 0
                    ),
                    "recent_first_scored_open": separation["recent_first_scored_open"],
                    "holdout_scored_bars": len(scored_hold),
                    "holdout_warmup_bars": next(
                        (index for index, row in enumerate(hold_base) if row["initialization"] != "warming_up"),
                        len(hold_base),
                    ),
                    "holdout_file_end_open": hold_base[-1]["candle_open_time"] if hold_base else None,
                    "holdout_scored_end_open": hold_base[scored_hold[-1]]["candle_open_time"] if scored_hold else None,
                    "scored_intersection": separation["scored_intersection"],
                    "holdout_baseline_counts": dict(Counter(hold_base[i]["state"] for i in scored_hold)),
                    "holdout_candidate_counts": dict(Counter(hold_cand[i]["state"] for i in scored_hold)),
                }
            )
            print(symbol, "changed", report["series"][-1]["comparison"]["changed_bars"], flush=True)
            kinds = {}
            for index, row in enumerate(baseline):
                kinds.setdefault(row["state"], index)
                if row["bars_since_break"] == 0 and row["last_event"] == "choch":
                    kinds.setdefault("choch", index)
                if row["last_event"] == "reversal_confirmed" and row["state"] in {"bullish", "bearish"} and row["state_age"] == 0:
                    kinds.setdefault("reversal", index)
                if "same_candle_reversal_pivots" in row["ambiguity_flags"]:
                    kinds.setdefault("same_candle", index)
                if row["initialization"] == "gap_reset":
                    kinds.setdefault("gap", index)
                if row["expiry_status"] == "expired" and row["state_age"] == 0:
                    kinds.setdefault("expiry", index)
            if "bullish" in kinds:
                spare = [i for i in rng.sample(range(len(baseline)), min(40, len(baseline))) if baseline[i]["state"] == "transition"]
                if spare:
                    kinds["random_transition"] = spare[0]
            for index in kinds.values():
                case_id = f"{symbol}-{len(hidden) + 1:02d}"
                hidden.append({"case_id": case_id, "symbol": symbol, "timeframe": "15m", **_hidden_window(candles, index)})
        report["review_instructions"] = (
            "Read only review_hidden.json. Save classification, ambiguity, and reasoning in review_answers.json. "
            "Do not create or open review_reveal.json before those answers exist. "
            "After answers are saved, run this script with the argument reveal. "
            "Disagreements are written to review_disagreements.json and are separate from the blind answers. "
            "An empty answers file means no review has been recorded."
        )
        (OUT / "comparison.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        (OUT / "review_hidden.json").write_text(json.dumps(hidden, indent=2), encoding="utf-8")
        reveal_path = OUT / "review_reveal.json"
        if reveal_path.exists():
            reveal_path.unlink()
        answers_path = OUT / "review_answers.json"
        if not answers_path.exists():
            answers_path.write_text("[]\n", encoding="utf-8")
        print("wrote", OUT, "hidden cases", len(hidden), "reveal withheld", flush=True)
    finally:
        await db_manager.close()


async def reveal() -> None:
    """Attach labels only after review_answers.json has classifications."""
    answers_path = OUT / "review_answers.json"
    hidden_path = OUT / "review_hidden.json"
    if not answers_path.exists() or not hidden_path.exists():
        raise SystemExit("Missing hidden cases or answers. Record answers before reveal.")
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    if not isinstance(answers, list) or not answers:
        raise SystemExit("review_answers.json has no classifications. Reveal stays closed.")
    hidden = json.loads(hidden_path.read_text(encoding="utf-8"))
    by_id = {row["case_id"]: row for row in answers}
    await db_manager.connect(get_settings(), ensure_schema=False)
    try:
        cached: dict[str, tuple[list, list, list]] = {}
        revealed = []
        disagreements = []
        for case in hidden:
            symbol = case["symbol"]
            if symbol not in cached:
                candles = await _load(symbol, "15m", 5760)
                cached[symbol] = (candles, *_both(symbol, "15m", candles))
            candles, baseline, candidate = cached[symbol]
            cutoff = case["review_cutoff_open"]
            match = next(
                (
                    index
                    for index, candle in enumerate(candles)
                    if str(candle.get("time")) == cutoff
                ),
                None,
            )
            if match is None:
                revealed.append({"case_id": case["case_id"], "error": "cutoff left the loaded window"})
                continue
            row = baseline[match]
            other = candidate[match]
            revealed.append(
                {
                    "case_id": case["case_id"],
                    "baseline_state": row["state"],
                    "baseline_reason": row["reason"],
                    "baseline_explanation": row["explanation"],
                    "candidate_state": other["state"],
                    "ambiguity_flags": row["ambiguity_flags"],
                    "known_at": row["known_at"],
                }
            )
            answer = by_id.get(case["case_id"])
            if answer is None:
                disagreements.append({"case_id": case["case_id"], "missing_answer": True})
                continue
            if answer.get("classification") != row["state"]:
                disagreements.append(
                    {
                        "case_id": case["case_id"],
                        "classification": answer.get("classification"),
                        "baseline_state": row["state"],
                        "reasoning": answer.get("reasoning"),
                    }
                )
        (OUT / "review_reveal.json").write_text(json.dumps(revealed, indent=2), encoding="utf-8")
        (OUT / "review_disagreements.json").write_text(json.dumps(disagreements, indent=2), encoding="utf-8")
        print("revealed", len(revealed), "disagreements", len(disagreements), flush=True)
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(reveal() if len(sys.argv) > 1 and sys.argv[1] == "reveal" else main())
