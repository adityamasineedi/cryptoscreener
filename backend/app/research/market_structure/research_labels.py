"""Research-only opportunity labels (hypotheses — never trade filters)."""

from __future__ import annotations

from typing import Any, Mapping

from app.research.market_structure.structure import StructureSnapshot


def suggest_research_opportunity(
    *,
    structure_4h: StructureSnapshot | Mapping[str, Any] | None,
    structure_1h: StructureSnapshot | Mapping[str, Any] | None,
    structure_15m: StructureSnapshot | Mapping[str, Any] | None,
    mtf_alignment: str | None,
) -> dict[str, Any]:
    """Return hypothesis labels only — do not execute alternate strategies."""

    def g(obj: Any, key: str, default: str = "UNKNOWN") -> str:
        if obj is None:
            return default
        if isinstance(obj, StructureSnapshot):
            return str(getattr(obj, key, default) or default)
        return str(obj.get(key) or default)

    r4 = g(structure_4h, "market_regime")
    r1 = g(structure_1h, "market_regime")
    bos1 = g(structure_1h, "bos_state")
    bos4 = g(structure_4h, "bos_state")
    vol1 = g(structure_1h, "volatility_state")
    d4 = g(structure_4h, "direction")
    d1 = g(structure_1h, "direction")
    d15 = g(structure_15m, "direction")
    pb15 = g(structure_15m, "pullback_state")
    mtf = (mtf_alignment or "").upper()

    labels: list[str] = []
    notes: list[str] = []

    if r1 == "BULL_TREND" and bos1 == "BULLISH_BOS":
        labels.append("TREND_FOLLOWING_CANDIDATE")
        notes.append("hypothesis_only")
    if r1 == "BEAR_TREND" and bos1 == "BEARISH_BOS":
        labels.append("SHORT_TREND_FOLLOWING_CANDIDATE")
        notes.append("short_paused_do_not_execute")
    if r4 == "BULL_TREND" and bos4 == "BULLISH_BOS" and "TREND_FOLLOWING_CANDIDATE" not in labels:
        labels.append("TREND_FOLLOWING_CANDIDATE")

    if r1 in {"RANGE", "LOW_VOLATILITY_COMPRESSION"} and vol1 == "LOW":
        labels.append("MEAN_REVERSION_CANDIDATE")
    if r1 == "RANGE" and vol1 == "HIGH":
        labels.append("NO_TRADE_CHOPPY")
        labels.append("HIGH_VOLATILITY_RISK_REDUCTION")
    if r1 == "LOW_VOLATILITY_COMPRESSION" or r4 == "LOW_VOLATILITY_COMPRESSION":
        labels.append("BREAKOUT_CANDIDATE")
        labels.append("LOW_VOLATILITY_COMPRESSION")
        notes.append("breakout_direction_unknown")
    if r1 == "CHOPPY" or r4 == "CHOPPY":
        labels.append("NO_TRADE_CHOPPY")
    if r1 == "TRANSITION" or r4 == "TRANSITION" or mtf == "TRANSITION_ALIGNED":
        labels.append("TRANSITION_WAIT")
    if mtf in {
        "TIMEFRAME_CONFLICT",
        "1H_15M_BULLISH_AGAINST_4H",
        "1H_15M_BEARISH_AGAINST_4H",
    }:
        labels.append("TIMEFRAME_CONFLICT_AVOID")
    if d4 == "BULLISH" and d1 == "BULLISH" and d15 == "BEARISH":
        labels.append("PULLBACK_OR_WAIT")
        if pb15 in {"BULLISH_PULLBACK", "DEEP_PULLBACK"}:
            labels.append("PULLBACK_CANDIDATE")
    if d4 == "BULLISH" and d1 == "BEARISH":
        labels.append("TIMEFRAME_CONFLICT_AVOID")

    if not labels:
        labels = ["NONE"]

    # Deduplicate preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for lab in labels:
        if lab not in seen:
            seen.add(lab)
            uniq.append(lab)

    return {
        "research_opportunity_labels": uniq,
        "primary_research_label": uniq[0],
        "notes": notes,
        "disclaimer": (
            "Hypotheses only — not profitable strategies. "
            "Do not activate alternate strategies from these labels."
        ),
    }
