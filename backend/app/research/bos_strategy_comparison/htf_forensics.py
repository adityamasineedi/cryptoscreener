"""Research-only HTF forensic decomposition for S3 S/D survivors.

Uses existing trend_at_as_of / classify_htf_alignment / swings_for_timeframe /
infer_trend. Does NOT modify HTF, trend, or signal engines.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_UNAVAILABLE,
    as_of_index_at_or_before,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
    trend_at_as_of,
)
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend

# Matrix keys for states actually returned by trend_at_as_of / infer_trend
_MATRIX_STATES = ("BULLISH", "BEARISH", "NEUTRAL", "INSUFFICIENT_DATA", "HTF_UNAVAILABLE")


def empty_htf_matrix() -> dict[str, int]:
    out: dict[str, int] = {}
    for a in ("BULLISH", "BEARISH", "NEUTRAL"):
        for b in ("BULLISH", "BEARISH", "NEUTRAL"):
            out[f"4H_{a}__1H_{b}"] = 0
    # Extra buckets only if engine actually returns them
    out["4H_INSUFFICIENT_DATA__1H_ANY"] = 0
    out["4H_ANY__1H_INSUFFICIENT_DATA"] = 0
    out["4H_HTF_UNAVAILABLE__1H_ANY"] = 0
    out["4H_ANY__1H_HTF_UNAVAILABLE"] = 0
    out["OTHER"] = 0
    return out


def matrix_key(state_4h: str, state_1h: str) -> str:
    s4 = (state_4h or "UNKNOWN").upper()
    s1 = (state_1h or "UNKNOWN").upper()
    if s4 in ("BULLISH", "BEARISH", "NEUTRAL") and s1 in (
        "BULLISH",
        "BEARISH",
        "NEUTRAL",
    ):
        return f"4H_{s4}__1H_{s1}"
    if s4 == "INSUFFICIENT_DATA":
        return "4H_INSUFFICIENT_DATA__1H_ANY"
    if s1 == "INSUFFICIENT_DATA":
        return "4H_ANY__1H_INSUFFICIENT_DATA"
    if s4 == "HTF_UNAVAILABLE":
        return "4H_HTF_UNAVAILABLE__1H_ANY"
    if s1 == "HTF_UNAVAILABLE":
        return "4H_ANY__1H_HTF_UNAVAILABLE"
    return "OTHER"


def _norm_state(raw: str | None) -> str:
    return str(raw or "UNKNOWN").upper()


def decompose_htf_rejection(
    *,
    direction: str | None,
    state_4h: str,
    state_1h: str,
    alignment: str,
) -> str:
    """Map underlying 4H/1H states to research rejection categories.

    Does not invent engine states — only labels combinations of existing outputs.
    """
    s4 = _norm_state(state_4h)
    s1 = _norm_state(state_1h)
    d = (direction or "").upper()

    if s4 in ("HTF_UNAVAILABLE",) or s1 in ("HTF_UNAVAILABLE",):
        return "DATA_UNAVAILABLE"
    if s4 == "INSUFFICIENT_DATA" or s1 == "INSUFFICIENT_DATA":
        # Engine returned INSUFFICIENT_DATA (not enough swings) — not missing candles
        if s4 == "INSUFFICIENT_DATA" and s1 == "INSUFFICIENT_DATA":
            return "BOTH_INSUFFICIENT_DATA"
        return (
            "4H_INSUFFICIENT_DATA"
            if s4 == "INSUFFICIENT_DATA"
            else "1H_INSUFFICIENT_DATA"
        )

    if s4 == "NEUTRAL" and s1 == "NEUTRAL":
        return "BOTH_NEUTRAL"
    if s4 == "NEUTRAL":
        return "4H_NEUTRAL"
    if s1 == "NEUTRAL":
        return "1H_NEUTRAL"

    if s4 == "BULLISH" and s1 == "BEARISH":
        return "4H_BULLISH_1H_BEARISH"
    if s4 == "BEARISH" and s1 == "BULLISH":
        return "4H_BEARISH_1H_BULLISH"

    # Both directional and same — check vs trade direction
    if s4 in ("BULLISH", "BEARISH") and s1 in ("BULLISH", "BEARISH") and s4 == s1:
        if alignment == HTF_ALIGNED:
            return "HTF_ALIGNED"
        if alignment == HTF_CONFLICT:
            return "WRONG_DIRECTION"
        # Same direction but not aligned for this trade (shouldn't happen if same)
        if d == "LONG" and s4 == "BEARISH":
            return "WRONG_DIRECTION"
        if d == "SHORT" and s4 == "BULLISH":
            return "WRONG_DIRECTION"
        return "OTHER_EXISTING_ENGINE_REASON"

    if alignment == HTF_NEUTRAL_UNAVAILABLE:
        return "OTHER_EXISTING_ENGINE_REASON"
    return "OTHER_EXISTING_ENGINE_REASON"


def inspect_tf_at_eligibility(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    eligibility_ts: datetime | None,
    config: SignalConfig,
) -> dict[str, Any]:
    """Full HTF TF snapshot at eligibility using existing engines only."""
    last_series_ts = None
    if candles:
        last_series_ts = candle_time(candles[-1])

    idx = as_of_index_at_or_before(candles, eligibility_ts)
    available = idx is not None
    as_of_time = candle_time(candles[idx]) if available and idx is not None else None

    if not available:
        return {
            "available": False,
            "as_of_index": None,
            "as_of_time": None,
            "state": "HTF_UNAVAILABLE",
            "engine_trend": None,
            "structure_sequence": [],
            "trend_reason": "No candle with timestamp <= eligibility_time",
            "last_candle_time": _iso(last_series_ts),
            "last_swing_high": None,
            "last_swing_low": None,
            "source": f"trend_at_as_of/{timeframe}/swings_for_timeframe+infer_trend",
            "asof_ok": True,  # vacuously; no future candle used
        }

    assert idx is not None
    state = trend_at_as_of(
        candles,
        idx,
        timeframe=timeframe,
        symbol=symbol,
        config=config,
    )
    swings = swings_for_timeframe(
        candles,
        config,
        timeframe,
        symbol=symbol,
        as_of_index=idx,
    )
    trend_detail = infer_trend(swings)
    # Confirm state matches helper (normalize WAITING→HTF_UNAVAILABLE path already in helper)
    engine_trend = str(trend_detail.get("trend") or "").upper()

    asof_ok = True
    if as_of_time is not None and eligibility_ts is not None:
        a = as_of_time if as_of_time.tzinfo else as_of_time.replace(tzinfo=timezone.utc)
        e = (
            eligibility_ts
            if eligibility_ts.tzinfo
            else eligibility_ts.replace(tzinfo=timezone.utc)
        )
        asof_ok = a <= e

    return {
        "available": True,
        "as_of_index": idx,
        "as_of_time": _iso(as_of_time),
        "state": state,
        "engine_trend": engine_trend,
        "structure_sequence": list(trend_detail.get("structure_sequence") or []),
        "trend_reason": trend_detail.get("reason"),
        "last_candle_time": _iso(last_series_ts),
        "last_swing_high": trend_detail.get("last_swing_high"),
        "last_swing_low": trend_detail.get("last_swing_low"),
        "source": f"trend_at_as_of/{timeframe}/swings_for_timeframe+infer_trend",
        "asof_ok": asof_ok,
        "candles_in_asof_window": idx + 1,
    }


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


HTF_CANDLE_COMPLETION_NOTE = (
    "Research htf_trends_for_setup_bar / as_of_index_at_or_before selects the "
    "largest HTF candle with candle_time <= eligibility timestamp T. Candle "
    "'time' is the bar open time in this dataset, so a currently forming HTF "
    "bar whose open <= T is included. Swing confirmation still requires "
    "swing_right_bars closed candles after a pivot (production swing_detector), "
    "so late pivots on the forming bar are not confirmed early. "
    "Behavior is not changed by this diagnostic."
)


def compare_research_vs_production_trend(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    config: SignalConfig,
    signal_engine: Any,
) -> dict[str, Any]:
    """Compare research trend_at_as_of vs production analyze_timeframe trend."""
    research_state = trend_at_as_of(
        candles,
        as_of_index,
        timeframe=timeframe,
        symbol=symbol,
        config=config,
    )
    analysis = signal_engine.analyze_timeframe(
        symbol,
        timeframe,
        candles,
        as_of_index=as_of_index,
    )
    prod_trend = str((analysis.get("trend") or {}).get("trend") or "").upper()
    # Map production WAITING/INSUFFICIENT to research labels for comparison
    if prod_trend == "WAITING":
        prod_mapped = "HTF_UNAVAILABLE"
    else:
        prod_mapped = prod_trend
    match = research_state == prod_mapped or (
        research_state == "INSUFFICIENT_DATA" and prod_trend == "INSUFFICIENT_DATA"
    ) or (research_state == prod_trend)
    return {
        "timeframe": timeframe,
        "as_of_index": as_of_index,
        "research_state": research_state,
        "production_trend": prod_trend,
        "match": match,
    }


def build_htf_survivor_trace(
    *,
    symbol: str,
    direction: str | None,
    bos_direction: str | None,
    lifecycle_id: str,
    eligibility_time: datetime | None,
    bos_time: str | None,
    pullback_time: str | None,
    retest_time: str | None,
    sd_time: str | None,
    candles_4h: Sequence[Mapping[str, Any]],
    candles_1h: Sequence[Mapping[str, Any]],
    setup_candles: Sequence[Mapping[str, Any]],
    eligibility_index: int,
    config: SignalConfig,
    signal_engine: Any | None = None,
) -> dict[str, Any]:
    """One S/D-survivor HTF forensic record."""
    elig = _as_utc(eligibility_time)
    detail_4h = inspect_tf_at_eligibility(
        symbol=symbol,
        timeframe="4h",
        candles=candles_4h,
        eligibility_ts=elig,
        config=config,
    )
    detail_1h = inspect_tf_at_eligibility(
        symbol=symbol,
        timeframe="1h",
        candles=candles_1h,
        eligibility_ts=elig,
        config=config,
    )

    # Same path as S3 runner
    htf = htf_trends_for_setup_bar(
        symbol=symbol,
        setup_candles=setup_candles,
        as_of_index=eligibility_index,
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        config=config,
        include_5m=False,
    )
    alignment = classify_htf_alignment(
        bos_direction=bos_direction,
        trend_4h=htf.get("trend_4h"),
        trend_1h=htf.get("trend_1h"),
    )
    # Prefer htf_trends states (exact S3 path); detail_* should match
    state_4h = str(htf.get("trend_4h") or detail_4h["state"])
    state_1h = str(htf.get("trend_1h") or detail_1h["state"])

    reason = decompose_htf_rejection(
        direction=direction,
        state_4h=state_4h,
        state_1h=state_1h,
        alignment=alignment,
    )
    htf_pass = alignment == HTF_ALIGNED

    data_class = "STRUCTURE_NEUTRAL"
    if not detail_4h["available"] or not detail_1h["available"]:
        data_class = "DATA_MISSING"
    elif state_4h in ("HTF_UNAVAILABLE",) or state_1h in ("HTF_UNAVAILABLE",):
        data_class = "DATA_UNAVAILABLE"
    elif "NEUTRAL" in (state_4h, state_1h) or state_4h != state_1h:
        if "NEUTRAL" in (state_4h, state_1h):
            data_class = "STRUCTURE_NEUTRAL"
        else:
            data_class = "STRUCTURE_DIRECTIONAL_MISMATCH"

    prod_match: dict[str, Any] | None = None
    if signal_engine is not None and detail_4h["as_of_index"] is not None:
        m4 = compare_research_vs_production_trend(
            symbol=symbol,
            timeframe="4h",
            candles=candles_4h,
            as_of_index=int(detail_4h["as_of_index"]),
            config=config,
            signal_engine=signal_engine,
        )
        m1 = compare_research_vs_production_trend(
            symbol=symbol,
            timeframe="1h",
            candles=candles_1h,
            as_of_index=int(detail_1h["as_of_index"]),
            config=config,
            signal_engine=signal_engine,
        )
        prod_match = {
            "4h": m4,
            "1h": m1,
            "match": bool(m4["match"] and m1["match"]),
        }

    violations: list[str] = []
    if not detail_4h.get("asof_ok", True):
        violations.append("4H_asof_time > eligibility_time")
    if not detail_1h.get("asof_ok", True):
        violations.append("1H_asof_time > eligibility_time")

    return {
        "lifecycle_id": lifecycle_id,
        "direction": direction,
        "bos_time": bos_time,
        "pullback_time": pullback_time,
        "retest_time": retest_time,
        "sd_time": sd_time,
        "eligibility_time": _iso(elig),
        "htf_evaluation_time": _iso(elig),
        "4h": {
            "as_of_index": detail_4h.get("as_of_index"),
            "as_of_time": detail_4h.get("as_of_time"),
            "state": state_4h,
            "structure": detail_4h.get("structure_sequence"),
            "structure_reason": detail_4h.get("trend_reason"),
            "last_candle_time": detail_4h.get("last_candle_time"),
            "available": detail_4h.get("available"),
            "source": detail_4h.get("source"),
            "last_swing_high": detail_4h.get("last_swing_high"),
            "last_swing_low": detail_4h.get("last_swing_low"),
        },
        "1h": {
            "as_of_index": detail_1h.get("as_of_index"),
            "as_of_time": detail_1h.get("as_of_time"),
            "state": state_1h,
            "structure": detail_1h.get("structure_sequence"),
            "structure_reason": detail_1h.get("trend_reason"),
            "last_candle_time": detail_1h.get("last_candle_time"),
            "available": detail_1h.get("available"),
            "source": detail_1h.get("source"),
            "last_swing_high": detail_1h.get("last_swing_high"),
            "last_swing_low": detail_1h.get("last_swing_low"),
        },
        "alignment": {
            "pass": htf_pass,
            "status": alignment,
            "reason": reason,
            "required": (
                "4H+1H both BULLISH"
                if direction == "LONG"
                else "4H+1H both BEARISH"
                if direction == "SHORT"
                else "unknown"
            ),
        },
        "data_classification": data_class,
        "matrix_key": matrix_key(state_4h, state_1h),
        "production_research_trend_match": prod_match,
        "lookahead_violations": violations,
    }


def classify_htf_root_cause(
    rejection_reasons: Mapping[str, int],
    *,
    data_missing: int,
    asof_violations: int,
    research_mismatch: int,
    sd_survivors: int,
    htf_pass: int,
) -> dict[str, Any]:
    evidence = [
        f"sd_survivors={sd_survivors}",
        f"htf_pass={htf_pass}",
        f"rejection_reasons={dict(rejection_reasons)}",
        f"data_missing={data_missing}",
        f"asof_violations={asof_violations}",
        f"research_mismatch={research_mismatch}",
    ]
    if sd_survivors == 0:
        return {"classification": "INSUFFICIENT_EVIDENCE", "evidence": evidence}
    if asof_violations > 0:
        return {"classification": "HTF_ASOF_BUG", "evidence": evidence}
    if research_mismatch > 0:
        return {"classification": "HTF_RESEARCH_MISMATCH", "evidence": evidence}
    if data_missing == sd_survivors:
        return {"classification": "HTF_DATA_UNAVAILABLE", "evidence": evidence}

    neutralish = sum(
        int(rejection_reasons.get(k, 0))
        for k in ("4H_NEUTRAL", "1H_NEUTRAL", "BOTH_NEUTRAL")
    )
    conflict = sum(
        int(rejection_reasons.get(k, 0))
        for k in ("4H_BULLISH_1H_BEARISH", "4H_BEARISH_1H_BULLISH")
    )
    wrong = int(rejection_reasons.get("WRONG_DIRECTION", 0))

    issues: list[str] = []
    if neutralish > 0:
        issues.append("HTF_TRUE_NEUTRAL")
    if conflict > 0:
        issues.append("HTF_DIRECTION_CONFLICT")
    if wrong > 0:
        issues.append("HTF_WRONG_DIRECTION")
    if int(rejection_reasons.get("DATA_UNAVAILABLE", 0)) > 0:
        issues.append("HTF_DATA_UNAVAILABLE")

    if len(issues) == 0:
        return {"classification": "INSUFFICIENT_EVIDENCE", "evidence": evidence}
    if len(issues) > 1:
        return {
            "classification": "MULTIPLE_ISSUES",
            "components": issues,
            "evidence": evidence,
        }
    return {"classification": issues[0], "evidence": evidence}


def aggregate_htf_forensics(traces: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matrix = empty_htf_matrix()
    reasons: Counter[str] = Counter()
    dir_buckets = {
        "LONG": {"survivors": 0, "htf_pass": 0, "htf_fail": 0, "reasons": Counter()},
        "SHORT": {"survivors": 0, "htf_pass": 0, "htf_fail": 0, "reasons": Counter()},
    }
    avail_4h = 0
    avail_1h = 0
    data_missing = 0
    asof_violations = 0
    research_mismatch = 0
    htf_pass_n = 0
    lookahead_violations: list[str] = []

    for t in traces:
        mk = str(t.get("matrix_key") or "OTHER")
        if mk in matrix:
            matrix[mk] += 1
        else:
            matrix["OTHER"] += 1
        al = t.get("alignment") or {}
        reason = str(al.get("reason") or "OTHER_EXISTING_ENGINE_REASON")
        reasons[reason] += 1
        d = str(t.get("direction") or "")
        if d in dir_buckets:
            dir_buckets[d]["survivors"] += 1
            if al.get("pass"):
                dir_buckets[d]["htf_pass"] += 1
                htf_pass_n += 1
            else:
                dir_buckets[d]["htf_fail"] += 1
                dir_buckets[d]["reasons"][reason] += 1
        if (t.get("4h") or {}).get("available"):
            avail_4h += 1
        if (t.get("1h") or {}).get("available"):
            avail_1h += 1
        if t.get("data_classification") == "DATA_MISSING":
            data_missing += 1
        viols = list(t.get("lookahead_violations") or [])
        if viols:
            asof_violations += 1
            lookahead_violations.extend(
                f"{t.get('lifecycle_id')}:{v}" for v in viols
            )
        prm = t.get("production_research_trend_match")
        if isinstance(prm, Mapping) and prm.get("match") is False:
            research_mismatch += 1

    n = len(traces)
    root = classify_htf_root_cause(
        dict(reasons),
        data_missing=data_missing,
        asof_violations=asof_violations,
        research_mismatch=research_mismatch,
        sd_survivors=n,
        htf_pass=htf_pass_n,
    )

    return {
        "sd_survivors": n,
        "htf_pass": htf_pass_n,
        "htf_fail": n - htf_pass_n,
        "htf_matrix": matrix,
        "htf_rejection_reasons": {
            k: {
                "count": v,
                "percentage": round(100.0 * v / n, 2) if n else 0.0,
            }
            for k, v in sorted(reasons.items(), key=lambda x: -x[1])
        },
        "direction": {
            d: {
                "survivors": b["survivors"],
                "htf_pass": b["htf_pass"],
                "htf_fail": b["htf_fail"],
                "rejection_reasons": dict(b["reasons"]),
            }
            for d, b in dir_buckets.items()
        },
        "htf_data_availability": {
            "4h_available_count": avail_4h,
            "1h_available_count": avail_1h,
            "4h_available_all": avail_4h == n and n > 0,
            "1h_available_all": avail_1h == n and n > 0,
            "data_missing_count": data_missing,
            "note": (
                "available=true means a candle exists with timestamp <= eligibility. "
                "NEUTRAL structure is STRUCTURE_NEUTRAL, not DATA_MISSING."
            ),
        },
        "lookahead": {
            "pass": asof_violations == 0,
            "violations": lookahead_violations,
        },
        "production_research_htf_invocation": (
            "MISMATCH" if research_mismatch > 0 else "MATCH"
        ),
        "htf_candle_completion": HTF_CANDLE_COMPLETION_NOTE,
        "htf_root_cause": root,
        "alignment_rule": {
            "LONG": "classify_htf_alignment requires 4H BULLISH and 1H BULLISH",
            "SHORT": "classify_htf_alignment requires 4H BEARISH and 1H BEARISH",
            "neutral_or_mixed": "HTF_NEUTRAL_UNAVAILABLE (not treated as ALIGNED)",
            "opposite_both": "HTF_CONFLICT → WRONG_DIRECTION",
        },
    }
