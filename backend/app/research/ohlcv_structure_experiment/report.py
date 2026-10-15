"""Build the research report from in-memory candles. Does not read secrets."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.ohlcv_structure_experiment.engine import (
    SetupRecord,
    run_variant,
    scan_breaks,
)
from app.research.ohlcv_structure_experiment.config import (
    ASSUMPTIONS,
    DEVELOPMENT_FRACTION,
    RIGHT_BARS_BASELINE,
    RIGHT_BARS_FASTER,
    VARIANTS,
)
from app.research.ohlcv_structure_experiment.metrics import assign_window, summarize

REPORT_NAME = "experiment_report.json"


def _split(length: int) -> int:
    return int(length * DEVELOPMENT_FRACTION)


def _group(records: Sequence[SetupRecord], split: int, length: int) -> dict[str, list[SetupRecord]]:
    grouped: dict[str, list[SetupRecord]] = {
        "development": [],
        "later_slice": [],
        "boundary_excluded": [],
        "incomplete": [],
        "full_sample_already_inspected": [],
    }
    for record in records:
        window = assign_window(record, split, length)
        grouped[window].append(record)
        if record.status != "incomplete_window" and record.lifecycle_end < length:
            grouped["full_sample_already_inspected"].append(record)
    return grouped


def _slice_metrics(records: Sequence[SetupRecord], bar_count: int) -> dict[str, Any]:
    payload = summarize(records, bar_count=bar_count)
    by_symbol: dict[str, list[SetupRecord]] = {}
    by_call: dict[str, list[SetupRecord]] = {}
    by_direction: dict[str, list[SetupRecord]] = {}
    for record in records:
        by_symbol.setdefault(record.event.symbol, []).append(record)
        by_call.setdefault(record.event.call_type, []).append(record)
        by_direction.setdefault(record.event.direction, []).append(record)
    payload["by_symbol"] = {
        key: summarize(value, bar_count=bar_count) for key, value in sorted(by_symbol.items())
    }
    payload["by_call_type"] = {
        key: summarize(value, bar_count=bar_count) for key, value in sorted(by_call.items())
    }
    payload["by_direction"] = {
        key: summarize(value, bar_count=bar_count) for key, value in sorted(by_direction.items())
    }
    return payload


def build_report(
    series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    funding: Mapping[str, Sequence[tuple[datetime, float]]] | None = None,
    source: str = "caller",
) -> dict[str, Any]:
    """Evaluate A–D. The full sample is labeled already inspected."""
    variants: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    cached: dict[tuple[str, int], list] = {}
    for variant in VARIANTS:
        right = RIGHT_BARS_FASTER if variant == "D_faster_confirmation" else RIGHT_BARS_BASELINE
        all_records: list[SetupRecord] = []
        bar_count = 0
        for symbol, candles in series.items():
            key = (symbol, right)
            if key not in cached:
                cached[key] = scan_breaks(candles, symbol=symbol, right=right)
            records = run_variant(
                candles,
                cached[key],
                variant,
                funding=None if funding is None else funding.get(symbol),
            )
            all_records.extend(records)
            bar_count += len(candles)
        windows: dict[str, list[SetupRecord]] = {
            "development": [],
            "later_slice": [],
            "boundary_excluded": [],
            "incomplete": [],
            "full_sample_already_inspected": [],
        }
        for symbol, candles in series.items():
            symbol_records = [row for row in all_records if row.event.symbol == symbol]
            grouped = _group(symbol_records, _split(len(candles)), len(candles))
            for key, rows in grouped.items():
                windows[key].extend(rows)
        variants.append(
            {
                "id": variant,
                "windows": {
                    key: _slice_metrics(rows, bar_count)
                    for key, rows in windows.items()
                    if key != "boundary_excluded"
                },
                "boundary_excluded": len(windows["boundary_excluded"]),
                "median_bars_confirmation_to_break": _median(
                    [
                        row.event.bars_from_confirmation_to_break
                        for row in all_records
                    ]
                ),
                "event_count": len(all_records),
            }
        )
        for record in all_records:
            if len(examples) >= 24:
                break
            if record.event.symbol == next(iter(series)):
                examples.append(record.to_public())
    funding_used = bool(funding)
    return {
        "status": "ready",
        "research_only": True,
        "validation_status": "forward_evaluation_still_required",
        "source": source,
        "assumptions": {
            **ASSUMPTIONS,
            "funding_applied": funding_used,
            "funding_label": (
                "Funding included where a print falls after entry and at or before exit."
                if funding_used
                else "Results exclude funding. No funding series was supplied."
            ),
        },
        "variants": variants,
        "examples": examples,
        "legacy_reference": {
            "label": "Prior study entered at the break close, charged no costs, and left many trades unresolved at 20 bars.",
            "not_a_variant": True,
            "do_not_rank_against_corrected_fills": True,
        },
    }


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def default_report_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "research_sandbox"
        / "structure_reliability_20261009"
        / REPORT_NAME
    )


def load_saved_report() -> dict[str, Any]:
    path = default_report_path()
    if not path.is_file():
        return {
            "status": "unavailable",
            "research_only": True,
            "validation_status": "forward_evaluation_still_required",
            "reason": "No saved OHLCV structure experiment report is on disk.",
            "assumptions": ASSUMPTIONS,
        }
    return json.loads(path.read_text(encoding="utf-8"))
