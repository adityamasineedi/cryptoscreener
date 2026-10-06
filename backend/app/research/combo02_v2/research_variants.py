"""COMBO_02 V2.1 research variants — CHOPPY range only; frozen v2 default untouched.

VARIANT_A: CHOPPY → WAIT (no RANGE_MEAN_REVERSION in CHOPPY)
VARIANT_B: CHOPPY range keeps sweep logic but requires valid 15M confirm
           (no 15M_UNAVAILABLE_OPTIONAL_PASS)
"""

from __future__ import annotations

from typing import Any, Mapping

from app.research.combo02_v2.playbooks import (
    _15m_bearish_confirm,
    _15m_bullish_confirm,
)
from app.research.combo02_v2.router import (
    PLAYBOOK_RANGE,
    PLAYBOOK_REVERSAL,
    PLAYBOOK_TREND,
    PLAYBOOK_WAIT,
    RANGE_REGIMES,
    REVERSAL_REGIMES,
    TREND_REGIMES,
    route_playbook,
)

VARIANT_A = "V2_1A"
VARIANT_B = "V2_1B"

# UI/backtest id COMBO_02_V2_1_A aliases research id COMBO_02_V2_1A (same VARIANT_A).
COMBO_V2_FAMILY = frozenset(
    {
        "COMBO_02_V2",
        "COMBO_02_V2_1A",
        "COMBO_02_V2_1_A",
        "COMBO_02_V2_1B",
    }
)

_OPTIONAL_PASS = "15M_UNAVAILABLE_OPTIONAL_PASS"


def variant_for_combination_id(combination_id: str | None) -> str | None:
    cid = str(combination_id or "")
    if cid in {"COMBO_02_V2_1A", "COMBO_02_V2_1_A"}:
        return VARIANT_A
    if cid == "COMBO_02_V2_1B":
        return VARIANT_B
    return None


def route_playbook_variant(market_regime: str | None, *, variant: str | None) -> str:
    """Router with optional V2.1-A CHOPPY disable. Default == frozen v2."""
    if variant != VARIANT_A:
        return route_playbook(market_regime)
    regime = str(market_regime or "UNKNOWN").upper()
    if regime == "CHOPPY":
        return PLAYBOOK_WAIT
    if regime in REVERSAL_REGIMES:
        return PLAYBOOK_REVERSAL
    if regime in TREND_REGIMES:
        return PLAYBOOK_TREND
    if regime in RANGE_REGIMES:
        return PLAYBOOK_RANGE
    return PLAYBOOK_WAIT


def choppy_range_15m_required_ok(
    *,
    direction: str,
    snap_15m: Any | None,
    analysis_15m: Mapping[str, Any] | None,
    confirmation: str | None,
) -> tuple[bool, str]:
    """V2.1-B CHOPPY gate: reject optional/unavailable 15M pass; keep real confirms."""
    conf = str(confirmation or "")
    if conf == _OPTIONAL_PASS or (snap_15m is None and not analysis_15m):
        return False, "15M_REQUIRED_CHOPPY_UNAVAILABLE"
    if direction == "LONG":
        ok, conf2 = _15m_bullish_confirm(snap_15m, analysis_15m)
    else:
        ok, conf2 = _15m_bearish_confirm(snap_15m, analysis_15m)
    if conf2 == _OPTIONAL_PASS:
        return False, "15M_REQUIRED_CHOPPY_UNAVAILABLE"
    if not ok:
        return False, conf2 or "15M_REQUIRED_CHOPPY_NO_CONFIRM"
    return True, conf2
