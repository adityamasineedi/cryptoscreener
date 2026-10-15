"""Rank discovery rows by evidence that is already on the row.

This module does not run backtests, score strategies, or compute Sharpe,
Sortino, or a profit figure. Missing metrics stay null.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


PROMOTION_STATUS = "EXPERIMENTAL"

DISCLAIMER = (
    "Research ranking only. Order uses out-of-sample fields already present "
    "on each row. A higher rank is not a profitability claim, trade "
    "recommendation, or approval for paper, live, or Telegram use."
)


def rank_discovery_results(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return rows ordered by existing out-of-sample evidence.

    Walk-forward test profit factors, when already stored on each window,
    are the primary ordering fields. Sample size and walk-forward window
    count are used only when those values are already present. Equal evidence
    keeps the input order.
    """
    prepared = [_prepare(index, row) for index, row in enumerate(rows)]
    width = max(
        (len(item["oos_profit_factors"] or []) for item in prepared),
        default=0,
    )
    ordered = sorted(prepared, key=lambda item: _sort_key(item, width))
    return [{**item, "rank": position} for position, item in enumerate(ordered, start=1)]


def _prepare(index: int, row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise TypeError("discovery result rows must be mappings")
    assessment = row.get("assessment") if isinstance(row.get("assessment"), Mapping) else {}
    oos_profit_factors = _oos_profit_factors(row)
    return {
        "source_index": index,
        "combination_id": row.get("combination_id"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "direction": row.get("direction"),
        "research_passed": _optional_bool(row, assessment, "research_passed"),
        "sample_size": _sample_size(row, assessment),
        "profit_factor": _optional_float(row.get("profit_factor"))
        if "profit_factor" in row
        else None,
        "walk_forward_windows": _walk_forward_windows(row, assessment),
        "oos_profit_factor": _single_oos_profit_factor(row, oos_profit_factors),
        "oos_profit_factors": oos_profit_factors,
        "sharpe": _passthrough_number(row, "sharpe"),
        "sortino": _passthrough_number(row, "sortino"),
        "profit": _passthrough_number(row, "profit"),
        "promotion_status": PROMOTION_STATUS,
        "operator_approved": False,
        "production_approved": False,
        "paper_eligible": False,
        "live_eligible": False,
        "telegram_eligible": False,
        "disclaimer": DISCLAIMER,
    }


def _sort_key(item: Mapping[str, Any], width: int) -> tuple[Any, ...]:
    factors = item["oos_profit_factors"] or []
    any_oos_profit_factor = any(value is not None for value in factors)
    padded: list[tuple[int, float]] = []
    for offset in range(width):
        value = factors[offset] if offset < len(factors) else None
        if value is None:
            padded.append((1, 0.0))
        else:
            padded.append((0, -float(value)))
    return (
        0 if any_oos_profit_factor else 1,
        tuple(padded),
        0 if item["sample_size"] is not None else 1,
        -(item["sample_size"] if item["sample_size"] is not None else 0),
        0 if item["walk_forward_windows"] is not None else 1,
        -(
            item["walk_forward_windows"]
            if item["walk_forward_windows"] is not None
            else 0
        ),
        item["source_index"],
    )


def _oos_profit_factors(row: Mapping[str, Any]) -> list[float | None] | None:
    walk_forward = row.get("walk_forward")
    if not isinstance(walk_forward, Mapping) or "windows" not in walk_forward:
        if "oos_profit_factor" in row:
            return [_optional_float(row.get("oos_profit_factor"))]
        return None
    windows = walk_forward.get("windows")
    if not isinstance(windows, Sequence) or isinstance(windows, (str, bytes)):
        return None
    factors: list[float | None] = []
    for window in windows:
        if not isinstance(window, Mapping):
            factors.append(None)
            continue
        result = window.get("result") if isinstance(window.get("result"), Mapping) else {}
        if "profit_factor" in result:
            factors.append(_optional_float(result.get("profit_factor")))
        elif "profit_factor" in window:
            factors.append(_optional_float(window.get("profit_factor")))
        else:
            factors.append(None)
    return factors


def _single_oos_profit_factor(
    row: Mapping[str, Any],
    factors: list[float | None] | None,
) -> float | None:
    """Copy one existing out-of-sample profit factor. Never combine windows."""
    if "oos_profit_factor" in row:
        return _optional_float(row.get("oos_profit_factor"))
    if factors is not None and len(factors) == 1:
        return factors[0]
    return None


def _sample_size(row: Mapping[str, Any], assessment: Mapping[str, Any]) -> int | None:
    if "sample_size" in assessment:
        return _optional_int(assessment.get("sample_size"))
    if "sample_size" in row:
        return _optional_int(row.get("sample_size"))
    return None


def _walk_forward_windows(
    row: Mapping[str, Any],
    assessment: Mapping[str, Any],
) -> int | None:
    if "walk_forward_windows" in assessment:
        return _optional_int(assessment.get("walk_forward_windows"))
    if "walk_forward_windows" in row:
        return _optional_int(row.get("walk_forward_windows"))
    walk_forward = row.get("walk_forward")
    if isinstance(walk_forward, Mapping):
        if "window_count" in walk_forward:
            return _optional_int(walk_forward.get("window_count"))
        windows = walk_forward.get("windows")
        if isinstance(windows, Sequence) and not isinstance(windows, (str, bytes)):
            return len(windows)
    return None


def _optional_bool(
    row: Mapping[str, Any],
    assessment: Mapping[str, Any],
    key: str,
) -> bool | None:
    if key in row and isinstance(row.get(key), bool):
        return row[key]
    if key in assessment and isinstance(assessment.get(key), bool):
        return assessment[key]
    return None


def _passthrough_number(row: Mapping[str, Any], key: str) -> float | None:
    if key not in row:
        return None
    return _optional_float(row.get(key))


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    if number is None:
        return None
    return int(number)


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    return None
