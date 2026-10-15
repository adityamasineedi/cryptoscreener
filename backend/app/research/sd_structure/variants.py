"""Variant matrix for the supply/demand structure hypothesis.

Each id is a research question. None of them is an approval to trade.
"""

from __future__ import annotations

from dataclasses import dataclass


VOLUME_NONE = "none"
VOLUME_RVOL = "rvol"
VOLUME_DIVERGENCE = "divergence"
VOLUME_EXPANSION = "expansion"

# Entry-timeframe bars. 48 × 15m = 12h; 48 × 5m = 4h.
MAX_HOLD_BARS = 48
STRUCTURE_RULES = (
    "Body close, not a wick",
    "Inside-bar swings are ignored",
    "Swings inside the active HH-HL or LH-LL range are ignored",
    "4h or 1d is the director",
    "15m or 5m is the CHoCH trigger",
    "No entry against a clear higher-timeframe trend",
)
RVOL_WINDOW = 20
SEQUENCE_LOOKBACK = 48
ZONE_SCAN_BARS = 240


@dataclass(frozen=True)
class SdVariant:
    variant_id: str
    require_choch: bool
    require_bos: bool
    require_retest: bool
    volume_mode: str
    rvol_min: float | None = None
    fresh_zone_only: bool = False
    require_liquidity_sweep: bool = False


VARIANTS: dict[str, SdVariant] = {
    "COMBO_SD_A": SdVariant("A", True, False, False, VOLUME_NONE),
    "COMBO_SD_B": SdVariant("B", True, True, False, VOLUME_NONE),
    "COMBO_SD_C12": SdVariant("C12", True, True, False, VOLUME_RVOL, 1.2),
    "COMBO_SD_C": SdVariant("C", True, True, False, VOLUME_RVOL, 1.5),
    "COMBO_SD_C20": SdVariant("C20", True, True, False, VOLUME_RVOL, 2.0),
    "COMBO_SD_D": SdVariant("D", True, True, True, VOLUME_RVOL, 1.5),
    "COMBO_SD_E": SdVariant("E", True, False, False, VOLUME_DIVERGENCE),
    "COMBO_SD_F": SdVariant("F", False, True, False, VOLUME_EXPANSION, 1.5),
    "COMBO_SD_C_FRESH": SdVariant(
        "C_FRESH", True, True, False, VOLUME_RVOL, 1.5, True
    ),
    # Same sequence as B. The extra gate is an equal-high or equal-low sweep
    # that has already closed back inside before CHoCH confirms.
    "COMBO_SD_LIQ": SdVariant(
        "LIQ", True, True, False, VOLUME_NONE, None, False, True
    ),
}

SD_STRUCTURE_FAMILY = frozenset(VARIANTS)


def variant_for(combination_id: str) -> SdVariant:
    try:
        return VARIANTS[str(combination_id)]
    except KeyError as exc:
        raise KeyError(f"unknown supply/demand structure variant: {combination_id}") from exc
