"""Mutually exclusive SHORT entry classification with documented precedence.

Precedence (primary):
  1. RETEST — valid retest fill (limit after BOS)
  2. CHASING — extension_atr > chase_threshold
  3. LATE — delay_bars > late_delay_threshold
  4. IMMEDIATE_BREAK — entry on/near BOS without retest
  5. EARLY — otherwise near broken level without chase/late

A trade has exactly one primary label. Secondary is optional context only.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.research.short_entry_research.constants import (
    DEFAULT_FILTER_THRESHOLDS,
    ENTRY_CHASING,
    ENTRY_EARLY,
    ENTRY_IMMEDIATE_BREAK,
    ENTRY_LATE,
    ENTRY_RETEST,
)
from app.research.short_research_diagnostics.metrics import entry_extension_atr


def classify_entry_quality_exclusive(
    *,
    valid_retest_fill: bool = False,
    extension_atr: float | None = None,
    delay_bars: int | None = None,
    entry_price: float | None = None,
    bos_level: float | None = None,
    atr: float | None = None,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    """Return primary/secondary labels using configurable thresholds."""
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    chase_th = float(th["chase_extension_atr"])
    late_delay_th = int(th["late_delay_bars"])
    late_ext_th = float(th["late_extension_atr"])
    early_ext_th = float(th["early_extension_atr"])

    ext = extension_atr
    if ext is None and entry_price is not None:
        ext = entry_extension_atr(float(entry_price), bos_level, atr)

    delay = int(delay_bars) if delay_bars is not None else None

    secondary: str | None = None
    if valid_retest_fill:
        primary = ENTRY_RETEST
        if ext is not None and ext > chase_th:
            secondary = ENTRY_CHASING
        elif delay is not None and delay > late_delay_th:
            secondary = ENTRY_LATE
        return {
            "entry_quality_primary": primary,
            "entry_quality_secondary": secondary,
            "extension_atr": ext,
            "delay_bars": delay,
            "filter_thresholds": {
                "chase_extension_atr": chase_th,
                "late_delay_bars": late_delay_th,
                "late_extension_atr": late_ext_th,
                "early_extension_atr": early_ext_th,
            },
            "precedence": "RETEST > CHASING > LATE > IMMEDIATE_BREAK > EARLY",
        }

    if ext is not None and ext > chase_th:
        primary = ENTRY_CHASING
        if delay is not None and delay > late_delay_th:
            secondary = ENTRY_LATE
        return {
            "entry_quality_primary": primary,
            "entry_quality_secondary": secondary,
            "extension_atr": ext,
            "delay_bars": delay,
            "filter_thresholds": {
                "chase_extension_atr": chase_th,
                "late_delay_bars": late_delay_th,
                "late_extension_atr": late_ext_th,
                "early_extension_atr": early_ext_th,
            },
            "precedence": "RETEST > CHASING > LATE > IMMEDIATE_BREAK > EARLY",
        }

    if delay is not None and delay > late_delay_th:
        primary = ENTRY_LATE
        if ext is not None and ext >= late_ext_th:
            secondary = ENTRY_CHASING
        return {
            "entry_quality_primary": primary,
            "entry_quality_secondary": secondary,
            "extension_atr": ext,
            "delay_bars": delay,
            "filter_thresholds": {
                "chase_extension_atr": chase_th,
                "late_delay_bars": late_delay_th,
                "late_extension_atr": late_ext_th,
                "early_extension_atr": early_ext_th,
            },
            "precedence": "RETEST > CHASING > LATE > IMMEDIATE_BREAK > EARLY",
        }

    # Immediate after BOS: delay 0/1 and not a retest
    if delay is None or delay <= 1:
        if ext is not None and ext <= early_ext_th:
            primary = ENTRY_EARLY
        else:
            primary = ENTRY_IMMEDIATE_BREAK
    else:
        primary = ENTRY_EARLY if (ext is not None and ext <= early_ext_th) else ENTRY_IMMEDIATE_BREAK

    return {
        "entry_quality_primary": primary,
        "entry_quality_secondary": secondary,
        "extension_atr": ext,
        "delay_bars": delay,
        "filter_thresholds": {
            "chase_extension_atr": chase_th,
            "late_delay_bars": late_delay_th,
            "late_extension_atr": late_ext_th,
            "early_extension_atr": early_ext_th,
        },
        "precedence": "RETEST > CHASING > LATE > IMMEDIATE_BREAK > EARLY",
    }


def extension_filter_passed(
    *,
    extension_atr: float | None,
    delay_bars: int | None,
    thresholds: Mapping[str, float | int] | None = None,
) -> bool:
    th = {**DEFAULT_FILTER_THRESHOLDS, **dict(thresholds or {})}
    max_ext = float(th["extension_filter_max_atr"])
    max_delay = int(th["extension_filter_max_delay_bars"])
    if extension_atr is None or delay_bars is None:
        return False
    return float(extension_atr) <= max_ext and int(delay_bars) <= max_delay
