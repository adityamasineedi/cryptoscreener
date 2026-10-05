"""Multi-timeframe structure alignment (analytics only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from app.research.market_structure.labels import MTF_ALIGNMENT_LABELS
from app.research.market_structure.structure import StructureSnapshot


def _dir_score(direction: str | None, weight: int) -> int:
    d = (direction or "").upper()
    if d == "BULLISH":
        return weight
    if d == "BEARISH":
        return -weight
    return 0


def _side(direction: str | None) -> str | None:
    d = (direction or "").upper()
    if d == "BULLISH":
        return "BULL"
    if d == "BEARISH":
        return "BEAR"
    return None


@dataclass(frozen=True)
class MtfAlignment:
    mtf_alignment: str
    mtf_score: int
    mtf_confidence: float
    mtf_conflict_count: int
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason_codes"] = list(self.reason_codes)
        return d


def classify_mtf_alignment(
    structure_4h: StructureSnapshot | Mapping[str, Any] | None,
    structure_1h: StructureSnapshot | Mapping[str, Any] | None,
    structure_15m: StructureSnapshot | Mapping[str, Any] | None,
) -> MtfAlignment:
    def _get(obj: Any, key: str, default: str = "UNKNOWN") -> str:
        if obj is None:
            return default
        if isinstance(obj, StructureSnapshot):
            return str(getattr(obj, key, default) or default)
        return str(obj.get(key) or default)

    d4 = _get(structure_4h, "direction")
    d1 = _get(structure_1h, "direction")
    d15 = _get(structure_15m, "direction")
    r4 = _get(structure_4h, "market_regime")
    r1 = _get(structure_1h, "market_regime")
    r15 = _get(structure_15m, "market_regime")
    q15 = _get(structure_15m, "data_quality_state", "UNKNOWN_DATA_MISSING")
    q4 = _get(structure_4h, "data_quality_state")
    q1 = _get(structure_1h, "data_quality_state")

    reasons: list[str] = []
    if (
        q4.startswith("UNKNOWN")
        or q1.startswith("UNKNOWN")
        or structure_4h is None
        or structure_1h is None
    ):
        return MtfAlignment(
            "INSUFFICIENT_DATA",
            0,
            0.0,
            0,
            ("INSUFFICIENT_HTF_OR_1H",),
        )

    score = _dir_score(d4, 2) + _dir_score(d1, 1)
    if q15 not in {"UNKNOWN_DATA_MISSING", "UNKNOWN_INSUFFICIENT_HISTORY"}:
        score += _dir_score(d15, 1)
    else:
        reasons.append("15M_UNAVAILABLE")

    s4, s1, s15 = _side(d4), _side(d1), _side(d15)
    conflict = 0
    sides = [s for s in (s4, s1, s15) if s is not None]
    if "BULL" in sides and "BEAR" in sides:
        conflict = sum(1 for a, b in ((s4, s1), (s4, s15), (s1, s15)) if a and b and a != b)

    label = "TIMEFRAME_CONFLICT"
    conf = 0.4

    # Range / transition / vol alignment shortcuts
    regimes = {r4, r1, r15}
    if all(r in {"RANGE", "LOW_VOLATILITY_COMPRESSION", "CHOPPY"} for r in (r4, r1)):
        label = "RANGE_ALIGNED"
        conf = 0.55
        reasons.append("RANGE_ALIGNED")
    elif all(
        r in {"TRANSITION", "UNKNOWN"} or r.endswith("TRANSITION") for r in (r4, r1)
    ):
        label = "TRANSITION_ALIGNED"
        conf = 0.45
        reasons.append("TRANSITION_ALIGNED")
    elif all(
        r in {"HIGH_VOLATILITY_TREND", "HIGH_VOLATILITY_RANGE"} for r in (r4, r1)
    ):
        label = "VOLATILITY_ALIGNED"
        conf = 0.5
        reasons.append("VOLATILITY_ALIGNED")
    elif s4 == "BULL" and s1 == "BULL" and (s15 == "BULL" or s15 is None):
        if s15 == "BULL":
            label = "FULL_BULL_ALIGNMENT"
            conf = 0.9
        else:
            label = "BULLISH_HIGHER_TIMEFRAME_BUT_15M_WEAK"
            conf = 0.7
            reasons.append("15M_WEAK_OR_MISSING")
    elif s4 == "BEAR" and s1 == "BEAR" and (s15 == "BEAR" or s15 is None):
        if s15 == "BEAR":
            label = "FULL_BEAR_ALIGNMENT"
            conf = 0.9
        else:
            label = "BEARISH_HIGHER_TIMEFRAME_BUT_15M_WEAK"
            conf = 0.7
            reasons.append("15M_WEAK_OR_MISSING")
    elif s4 == "BULL" and s1 == "BULL" and s15 != "BULL":
        label = "BULLISH_HIGHER_TIMEFRAME_BUT_15M_WEAK"
        conf = 0.65
        reasons.append("15M_NOT_BULL")
    elif s4 == "BEAR" and s1 == "BEAR" and s15 != "BEAR":
        label = "BEARISH_HIGHER_TIMEFRAME_BUT_15M_WEAK"
        conf = 0.65
        reasons.append("15M_NOT_BEAR")
    elif s4 != "BULL" and s1 == "BULL" and s15 == "BULL":
        label = "1H_15M_BULLISH_AGAINST_4H"
        conf = 0.6
        reasons.append("LOWER_TFS_BULL_VS_4H")
    elif s4 != "BEAR" and s1 == "BEAR" and s15 == "BEAR":
        label = "1H_15M_BEARISH_AGAINST_4H"
        conf = 0.6
        reasons.append("LOWER_TFS_BEAR_VS_4H")
    else:
        label = "TIMEFRAME_CONFLICT"
        conf = 0.35
        reasons.append("DIRECTION_CONFLICT")

    if label not in MTF_ALIGNMENT_LABELS:
        label = "TIMEFRAME_CONFLICT"

    return MtfAlignment(
        mtf_alignment=label,
        mtf_score=int(score),
        mtf_confidence=round(conf, 4),
        mtf_conflict_count=int(conflict),
        reason_codes=tuple(reasons),
    )
