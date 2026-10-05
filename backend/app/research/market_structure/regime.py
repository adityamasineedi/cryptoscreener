"""Deterministic market-regime classifier (analytics only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from app.research.market_structure.config import MarketStructureFeatureConfig
from app.research.market_structure.labels import PRIMARY_REGIMES


@dataclass(frozen=True)
class RegimeClassification:
    market_regime: str
    confidence_score: float
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    votes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason_codes"] = list(self.reason_codes)
        return d


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def classify_market_regime(
    snapshot: Mapping[str, Any],
    config: MarketStructureFeatureConfig | None = None,
) -> RegimeClassification:
    """Pure, deterministic regime classifier from a structure/indicator snapshot.

    Uses a transparent multi-vote scheme so one noisy indicator cannot flip the
    regime alone (``trend_vote_required``, default 2).
    """
    cfg = config or MarketStructureFeatureConfig()
    reasons: list[str] = []

    data_q = str(snapshot.get("data_quality_state") or "")
    if data_q == "UNKNOWN_DATA_MISSING":
        return RegimeClassification(
            "UNKNOWN",
            0.0,
            ("DATA_MISSING",),
            {"votes": {}},
        )
    if data_q == "UNKNOWN_INSUFFICIENT_HISTORY":
        return RegimeClassification(
            "UNKNOWN",
            0.0,
            ("INSUFFICIENT_HISTORY",),
            {"votes": {}},
        )

    trend = str(snapshot.get("trend_state") or "UNKNOWN").upper()
    structure = str(snapshot.get("structure_state") or "UNKNOWN").upper()
    direction = str(snapshot.get("direction") or "UNKNOWN").upper()
    bos = str(snapshot.get("bos_state") or "UNKNOWN").upper()
    choch = str(snapshot.get("choch_state") or "UNKNOWN").upper()
    vol_state = str(snapshot.get("volatility_state") or "UNKNOWN").upper()
    momentum = str(snapshot.get("momentum_state") or "UNKNOWN").upper()

    adx = _num(snapshot.get("adx"))
    er = _num(snapshot.get("efficiency_ratio"))
    chop = _num(snapshot.get("choppiness_index"))
    atr_pct = _num(snapshot.get("atr_percentile"))
    ema_align = str(snapshot.get("ema_alignment") or "UNKNOWN").upper()
    range_state = str(snapshot.get("range_state") or "UNKNOWN").upper()
    alternations = snapshot.get("direction_changes")
    try:
        alt_n = int(alternations) if alternations is not None else None
    except (TypeError, ValueError):
        alt_n = None

    # --- votes for bull / bear / range / chop ---
    bull_votes = 0
    bear_votes = 0
    range_votes = 0
    chop_votes = 0
    transition_votes = 0
    vote_detail: dict[str, Any] = {}

    # 1) Structure direction
    if structure in {"UPTREND_HH_HL", "BULLISH_STRUCTURE"} or trend == "BULLISH":
        bull_votes += 1
        vote_detail["structure"] = "BULL"
        reasons.append("STRUCTURE_BULLISH")
    elif structure in {"DOWNTREND_LH_LL", "BEARISH_STRUCTURE"} or trend == "BEARISH":
        bear_votes += 1
        vote_detail["structure"] = "BEAR"
        reasons.append("STRUCTURE_BEARISH")
    elif structure in {"RANGE_STRUCTURE", "MIXED_STRUCTURE"} or trend == "NEUTRAL":
        range_votes += 1
        vote_detail["structure"] = "RANGE"
        reasons.append("STRUCTURE_RANGE_OR_MIXED")
    elif trend == "TRANSITION" or structure == "TRANSITION_STRUCTURE":
        transition_votes += 1
        vote_detail["structure"] = "TRANSITION"
        reasons.append("STRUCTURE_TRANSITION")
    else:
        vote_detail["structure"] = "UNKNOWN"

    # 2) EMA alignment
    if ema_align == "BULLISH":
        bull_votes += 1
        vote_detail["ema"] = "BULL"
        reasons.append("EMA_BULL_ALIGN")
    elif ema_align == "BEARISH":
        bear_votes += 1
        vote_detail["ema"] = "BEAR"
        reasons.append("EMA_BEAR_ALIGN")
    elif ema_align == "MIXED":
        transition_votes += 1
        vote_detail["ema"] = "MIXED"
        reasons.append("EMA_MIXED")
    else:
        vote_detail["ema"] = ema_align or "UNKNOWN"

    # 3) ADX / directional strength
    if adx is not None:
        if adx >= cfg.adx_trending_threshold:
            if direction == "BULLISH" or (adx and snapshot.get("di_plus", 0) or 0) > (
                snapshot.get("di_minus") or 0
            ):
                # Prefer DI for side when ADX strong
                di_p = _num(snapshot.get("di_plus")) or 0.0
                di_m = _num(snapshot.get("di_minus")) or 0.0
                if di_p > di_m:
                    bull_votes += 1
                    vote_detail["adx"] = "BULL_STRONG"
                    reasons.append("ADX_TRENDING_BULL")
                elif di_m > di_p:
                    bear_votes += 1
                    vote_detail["adx"] = "BEAR_STRONG"
                    reasons.append("ADX_TRENDING_BEAR")
                else:
                    vote_detail["adx"] = "STRONG_NEUTRAL"
            else:
                vote_detail["adx"] = "STRONG"
                reasons.append("ADX_TRENDING")
        elif adx <= cfg.adx_ranging_threshold:
            range_votes += 1
            vote_detail["adx"] = "RANGING"
            reasons.append("ADX_RANGING")
        else:
            vote_detail["adx"] = "MID"
            reasons.append("ADX_MID")
    else:
        vote_detail["adx"] = "MISSING"

    # 4) Efficiency / choppiness
    if er is not None:
        if er >= cfg.efficiency_trending_threshold:
            vote_detail["efficiency"] = "TRENDING"
            reasons.append("EFFICIENCY_TRENDING")
            if direction == "BULLISH":
                bull_votes += 1
            elif direction == "BEARISH":
                bear_votes += 1
        elif er <= cfg.efficiency_choppy_threshold:
            chop_votes += 1
            vote_detail["efficiency"] = "CHOPPY"
            reasons.append("EFFICIENCY_CHOPPY")
        else:
            vote_detail["efficiency"] = "MID"
    else:
        vote_detail["efficiency"] = "MISSING"

    if chop is not None:
        if chop >= cfg.choppiness_high_threshold:
            chop_votes += 1
            vote_detail["choppiness"] = "HIGH"
            reasons.append("CHOPPINESS_HIGH")
        elif chop <= cfg.choppiness_low_threshold:
            vote_detail["choppiness"] = "LOW"
            reasons.append("CHOPPINESS_LOW")
        else:
            vote_detail["choppiness"] = "MID"
    else:
        vote_detail["choppiness"] = "MISSING"

    if alt_n is not None and alt_n >= max(6, cfg.direction_change_lookback // 3):
        chop_votes += 1
        reasons.append("HIGH_DIRECTION_ALTERNATION")
        vote_detail["alternation"] = alt_n

    # BOS / CHoCH conflict → transition
    if bos.startswith("BULLISH") and choch.startswith("BEARISH"):
        transition_votes += 1
        reasons.append("BOS_CHOCH_CONFLICT")
    elif bos.startswith("BEARISH") and choch.startswith("BULLISH"):
        transition_votes += 1
        reasons.append("BOS_CHOCH_CONFLICT")
    if trend == "TRANSITION" or structure == "TRANSITION_STRUCTURE":
        transition_votes += 1

    need = int(cfg.trend_vote_required)
    high_vol = vol_state == "HIGH" or (
        atr_pct is not None and atr_pct >= cfg.atr_high_percentile
    )
    low_vol = vol_state == "LOW" or (
        atr_pct is not None and atr_pct <= cfg.atr_low_percentile
    )

    # Disagreement between structure and EMA → transition preference
    sides = {vote_detail.get("structure"), vote_detail.get("ema")}
    if "BULL" in sides and "BEAR" in sides:
        transition_votes += 1
        reasons.append("STRUCTURE_EMA_DISAGREE")

    regime = "UNKNOWN"
    confidence = 0.0

    if transition_votes >= 2 and bull_votes < need and bear_votes < need:
        regime = "TRANSITION"
        confidence = min(1.0, 0.4 + 0.15 * transition_votes)
        reasons.append("CLASSIFIED_TRANSITION")
    elif bull_votes >= need and bull_votes > bear_votes and bull_votes >= range_votes:
        if high_vol:
            regime = "HIGH_VOLATILITY_TREND"
            reasons.append("CLASSIFIED_HIGH_VOL_BULL_TREND")
        else:
            regime = "BULL_TREND"
            reasons.append("CLASSIFIED_BULL_TREND")
        confidence = min(1.0, 0.45 + 0.15 * bull_votes)
    elif bear_votes >= need and bear_votes > bull_votes and bear_votes >= range_votes:
        if high_vol:
            regime = "HIGH_VOLATILITY_TREND"
            reasons.append("CLASSIFIED_HIGH_VOL_BEAR_TREND")
        else:
            regime = "BEAR_TREND"
            reasons.append("CLASSIFIED_BEAR_TREND")
        confidence = min(1.0, 0.45 + 0.15 * bear_votes)
    elif chop_votes >= need and chop_votes >= range_votes:
        if high_vol:
            regime = "HIGH_VOLATILITY_RANGE"
            reasons.append("CLASSIFIED_HIGH_VOL_CHOP")
        else:
            regime = "CHOPPY"
            reasons.append("CLASSIFIED_CHOPPY")
        confidence = min(1.0, 0.4 + 0.12 * chop_votes)
    elif range_votes >= need or (
        adx is not None
        and adx <= cfg.adx_ranging_threshold
        and range_state in {"INSIDE_RANGE", "RANGE_COMPRESSION"}
        and bos in {"NO_CONFIRMED_BOS", "UNKNOWN"}
    ):
        if low_vol or range_state == "RANGE_COMPRESSION":
            regime = "LOW_VOLATILITY_COMPRESSION"
            reasons.append("CLASSIFIED_LOW_VOL_COMPRESSION")
        elif high_vol:
            regime = "HIGH_VOLATILITY_RANGE"
            reasons.append("CLASSIFIED_HIGH_VOL_RANGE")
        else:
            regime = "RANGE"
            reasons.append("CLASSIFIED_RANGE")
        confidence = min(1.0, 0.4 + 0.12 * max(range_votes, 1))
    elif low_vol and bull_votes < need and bear_votes < need:
        regime = "LOW_VOLATILITY_COMPRESSION"
        reasons.append("CLASSIFIED_LOW_VOL_FALLBACK")
        confidence = 0.35
    elif transition_votes >= 1:
        regime = "TRANSITION"
        reasons.append("CLASSIFIED_TRANSITION_WEAK")
        confidence = 0.3
    else:
        regime = "UNKNOWN"
        reasons.append("CLASSIFIED_UNKNOWN")
        confidence = 0.1

    # Momentum soft confirmation (does not own the class alone)
    if regime in {"BULL_TREND", "HIGH_VOLATILITY_TREND"} and momentum == "BEARISH":
        reasons.append("MOMENTUM_DIVERGES_BEAR")
        confidence = max(0.15, confidence - 0.1)
    if regime in {"BEAR_TREND", "HIGH_VOLATILITY_TREND"} and momentum == "BULLISH":
        reasons.append("MOMENTUM_DIVERGES_BULL")
        confidence = max(0.15, confidence - 0.1)

    if regime not in PRIMARY_REGIMES:
        regime = "UNKNOWN"

    return RegimeClassification(
        market_regime=regime,
        confidence_score=round(confidence, 4),
        reason_codes=tuple(reasons),
        votes={
            "bull_votes": bull_votes,
            "bear_votes": bear_votes,
            "range_votes": range_votes,
            "chop_votes": chop_votes,
            "transition_votes": transition_votes,
            "detail": vote_detail,
            "high_vol": high_vol,
            "low_vol": low_vol,
            "thresholds": {
                "adx_trending_threshold": cfg.adx_trending_threshold,
                "adx_ranging_threshold": cfg.adx_ranging_threshold,
                "atr_high_percentile": cfg.atr_high_percentile,
                "atr_low_percentile": cfg.atr_low_percentile,
                "efficiency_trending_threshold": cfg.efficiency_trending_threshold,
                "efficiency_choppy_threshold": cfg.efficiency_choppy_threshold,
                "trend_vote_required": cfg.trend_vote_required,
            },
        },
    )
