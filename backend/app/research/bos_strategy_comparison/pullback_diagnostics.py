"""Research-only forensic diagnostics for zero-pullback BOS strategy research.

Uses production detect_* engines exactly as implemented.
Does NOT modify pullback/impulse/BOS/retest logic or production thresholds.
Does NOT invent pullback definitions or fabricate candles.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from copy import deepcopy
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.config import (
    DISCLAIMER,
    StrategyResearchConfig,
)
from app.research.bos_strategy_comparison.engine import (
    _bos_confirmed,
    _clone_signal_config,
    _retest_confirmed,
)
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.combination_engine import _impulse_confirmed, _pullback_confirmed
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.impulse_engine import detect_impulse
from app.signals.pullback_engine import detect_pullback
from app.signals.retest_engine import detect_retest
from app.signals.signal_engine import SignalEngine


# ---------------------------------------------------------------------------
# Production pullback engine audit (static documentation of live logic)
# ---------------------------------------------------------------------------

PRODUCTION_PULLBACK_AUDIT: dict[str, Any] = {
    "function": "app.signals.pullback_engine.detect_pullback",
    "stateful": False,
    "notes": (
        "Pure candle-window evaluation. No internal event memory. "
        "Requires caller to supply CONFIRMED bos + impulse with bar_index < as_of_index."
    ),
    "inputs": {
        "candles": "Sequence of OHLC(V) mappings",
        "bos": "dict with state==CONFIRMED, broken_level, direction",
        "impulse": "dict with is_impulse, direction, bar_index, impulse_origin, impulse_end",
        "config": "SignalConfig pullback_min/max/invalidation_retracement",
        "optional_zones": ["demand_zone", "supply_zone", "vwap", "ema"],
        "as_of_index": "inclusive end index; never reads candles after this",
    },
    "outputs": {
        "pullback_state": ["WAITING", "ACTIVE", "CONFIRMED", "FAILED", "INVALIDATED"],
        "reason": "human-readable engine reason string",
        "optional_fields": [
            "impulse_origin",
            "impulse_end",
            "retracement_low",
            "retracement_high",
            "retracement_percentage",
            "pullback_zone",
            "structure_intact",
            "zone_hit",
        ],
    },
    "waiting_paths": [
        "Waiting for confirmed BOS",
        "Waiting for valid impulse after BOS",
        "Missing impulse/BOS levels",
        "Waiting for bars after impulse",
        "No pullback bars yet",
        "Retracement X% below minimum Y%",
    ],
    "fail_paths": [
        "Retracement X% above maximum Y% → FAILED",
    ],
    "invalidated_paths": [
        "Zero impulse range",
        "Pullback exceeded invalidation / broke impulse origin",
    ],
    "pass_paths": [
        "ACTIVE — retracement in [min,max], structure intact, not yet in zone",
        "CONFIRMED — ACTIVE criteria + current bar interacts with zone",
    ],
    "required_previous_events": ["CONFIRMED BOS", "is_impulse=True impulse"],
    "required_post_impulse_bars": (
        "At least 1 bar after impulse.bar_index (start < end). "
        "If impulse.bar_index == as_of_index → WAITING 'Waiting for bars after impulse'."
    ),
    "lookback_window": (
        "Scans candles[impulse.bar_index+1 : as_of_index+1] for retracement extreme"
    ),
    "atr_zone_distance": (
        "No ATR distance gate. Zones are optional confluence "
        "(structure broken_level always present; demand/supply/vwap/ema optional)."
    ),
    "direction": "Uses impulse.direction BULLISH/BEARISH",
    "invalidation": (
        "Close beyond impulse_origin OR retracement >= pullback_invalidation_retracement (default 1.05)"
    ),
    "min_bars": "Implicit: need impulse.bar_index < as_of_index",
    "waiting_semantics": {
        "WAITING": (
            "Not yet satisfied — NOT a permanent FAIL. Future candles may change state "
            "IF caller re-evaluates with the same BOS/impulse and a later as_of_index."
        ),
        "FAILED": "Retracement exceeded max band (still structure_intact path)",
        "INVALIDATED": "Structure broken or invalidation retracement",
        "ACTIVE": "Valid pullback depth, awaiting zone interaction",
        "CONFIRMED": "Valid pullback depth + zone interaction on as_of bar",
        "NONE": "Enum exists; detect_pullback does not currently emit NONE",
    },
}


PRODUCTION_VS_RESEARCH_INVOCATION: dict[str, dict[str, str]] = {
    "BOS invocation": {
        "Production": "SignalEngine.analyze_timeframe → detect_bos(as_of_index=current)",
        "Research": "Same: analyze_timeframe(as_of_index=i) on each bar; only proceeds when CONFIRMED",
    },
    "Impulse invocation": {
        "Production": "detect_impulse(bos, as_of_index=current) — bar_index always == as_of_index",
        "Research": "Same same-bar pairing via analyze_timeframe",
    },
    "Pullback invocation": {
        "Production": (
            "detect_pullback(bos, impulse, as_of_index=current) on same bar as impulse; "
            "no frozen prior impulse/BOS state across candle closes"
        ),
        "Research": (
            "Same same-bar pairing; stage funnel counts pullback only on BOS-CONFIRMED bars "
            "(where impulse.bar_index == as_of_index when impulse passes)"
        ),
    },
    "State persistence": {
        "Production": "Stateless per analyze()/analyze_timeframe call; SetupSignalService recomputes fresh",
        "Research": "Stateless per bar; no BOS/impulse event memory across subsequent bars",
    },
    "as_of_index": {
        "Production": "Latest closed bar (or explicit as_of_index)",
        "Research": "Walk-forward i; never uses candles after i",
    },
    "post-BOS candles": {
        "Production": "Not tracked as a lifecycle; each close re-detects BOS from scratch",
        "Research": "Post-BOS bars exist in series but pullback is not re-evaluated with frozen impulse",
    },
    "ATR source": {
        "Production": "ATR from candles[:as_of+1] inside impulse/BOS/retest",
        "Research": "Identical via production engines",
    },
    "zones": {
        "Production": "Optional demand/supply/vwap/ema from engine_store / S/D",
        "Research": "Optional via detect_zones_as_of when strategy.require_sd",
    },
    "timeframe": {
        "Production": "mtf_setup=15m, mtf_primary=1h, mtf_major=4h, mtf_entry=5m",
        "Research": "setup=15m (default), HTF=4h+1h, entry=5m — same mapping",
    },
    "history window": {
        "Production": "ohlcv_store closed candles for each TF",
        "Research": "PostgreSQL OHLCV range + warmup_bars (default 100 on setup TF)",
    },
}


TIMEFRAME_MAPPING: dict[str, Any] = {
    "execution_entry_timeframe": "5m",
    "primary_structure_setup_timeframe": "15m",
    "htf_timeframes": ["4h", "1h"],
    "signal_config_roles": {
        "mtf_major": "4h",
        "mtf_primary": "1h",
        "mtf_setup": "15m",
        "mtf_entry": "5m",
    },
    "research_config_roles": {
        "setup_timeframe": "15m",
        "htf_timeframes": ["4h", "1h"],
        "entry_timeframe": "5m",
    },
    "note": (
        "15m BOS/impulse/pullback/retest are evaluated on 15m candles. "
        "HTF alignment uses 4h/1h trends via as_of_index_at_or_before (no future HTF bars)."
    ),
}


def _post_bos_bucket(n: int) -> str:
    if n <= 0:
        return "0"
    if n <= 2:
        return "1-2"
    if n <= 5:
        return "3-5"
    if n <= 10:
        return "6-10"
    if n <= 20:
        return "11-20"
    return "21+"


def _reason_key(pullback: Mapping[str, Any] | None) -> str:
    """Stable key from production engine state + reason (no invented labels)."""
    if not pullback:
        return "MISSING_PULLBACK_OUTPUT"
    state = str(pullback.get("pullback_state") or "NONE")
    reason = str(pullback.get("reason") or "").strip()
    if not reason:
        return state
    # Normalize numeric retracement reasons into engine template families
    if reason.startswith("Retracement ") and " below minimum " in reason:
        return f"{state}|Retracement below minimum"
    if reason.startswith("Retracement ") and " above maximum " in reason:
        return f"{state}|Retracement above maximum"
    if reason.startswith("Pullback into "):
        return f"{state}|Pullback into zone"
    if reason.startswith("Active pullback "):
        return f"{state}|Active pullback awaiting zone"
    if reason.startswith("Impulse "):
        return f"{state}|{reason}"
    return f"{state}|{reason}"


def _fingerprint_pullback(pb: Mapping[str, Any] | None) -> str:
    raw = {
        "pullback_state": (pb or {}).get("pullback_state"),
        "reason": (pb or {}).get("reason"),
        "retracement_percentage": (pb or {}).get("retracement_percentage"),
        "structure_intact": (pb or {}).get("structure_intact"),
        "zone_hit": (pb or {}).get("zone_hit"),
    }
    return hashlib.sha256(repr(sorted(raw.items())).encode()).hexdigest()[:16]


def pullback_engine_unchanged_check(
    candles: Sequence[Mapping[str, Any]],
    bos: dict[str, Any],
    impulse: dict[str, Any],
    config: SignalConfig,
    *,
    as_of_index: int,
) -> dict[str, Any]:
    """Call production detect_pullback twice; prove diagnostic wrapper is read-only."""
    a = detect_pullback(candles, bos, impulse, config, as_of_index=as_of_index)
    b = detect_pullback(candles, bos, impulse, config, as_of_index=as_of_index)
    return {
        "identical": a == b,
        "fingerprint_a": _fingerprint_pullback(a),
        "fingerprint_b": _fingerprint_pullback(b),
        "output_a": a,
    }


def evaluate_pullback_no_future(
    candles: Sequence[Mapping[str, Any]],
    bos: dict[str, Any] | None,
    impulse: dict[str, Any] | None,
    config: SignalConfig,
    *,
    as_of_index: int,
) -> dict[str, Any]:
    """Production pullback with truncated series — proves no future candle use."""
    end = min(as_of_index, len(candles) - 1)
    truncated = list(candles[: end + 1])
    full = detect_pullback(
        candles, bos, impulse, config, as_of_index=as_of_index
    )
    trunc = detect_pullback(
        truncated, bos, impulse, config, as_of_index=end
    )
    return {
        "as_of_index": as_of_index,
        "truncated_len": len(truncated),
        "full_equals_truncated": full == trunc,
        "full": full,
        "truncated": trunc,
    }


def _direction_label(bos: Mapping[str, Any] | None) -> str | None:
    d = (bos or {}).get("direction")
    if d == "BULLISH_BOS":
        return "LONG"
    if d == "BEARISH_BOS":
        return "SHORT"
    return None


def _trace_event(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    bos_index: int,
    tf_analysis: Mapping[str, Any],
    signal_config: SignalConfig,
    max_follow_bars: int = 40,
) -> dict[str, Any]:
    """End-to-end trace for one BOS bar + follow-on lifecycle probe."""
    bos = tf_analysis.get("bos")
    impulse = tf_analysis.get("impulse")
    pullback = tf_analysis.get("pullback")
    retest = tf_analysis.get("retest")
    ts = candle_time(candles[bos_index])
    post_bos = max(0, len(candles) - 1 - bos_index)

    same_bar = {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "bos_index": bos_index,
        "bos_time": ts.isoformat() if ts else None,
        "bos_direction": _direction_label(bos if isinstance(bos, Mapping) else None),
        "bos_price": (bos or {}).get("break_price"),
        "bos_broken_level": (bos or {}).get("broken_level"),
        "bos_state": (bos or {}).get("state"),
        "impulse_time": ts.isoformat() if ts else None,
        "impulse_status": (
            "PASS"
            if _impulse_confirmed(impulse if isinstance(impulse, Mapping) else None)
            else "FAIL"
        ),
        "impulse_output": {
            "is_impulse": (impulse or {}).get("is_impulse"),
            "direction": (impulse or {}).get("direction"),
            "bar_index": (impulse or {}).get("bar_index"),
            "quality": (impulse or {}).get("quality"),
            "atr_multiple": (impulse or {}).get("atr_multiple"),
            "body_ratio": (impulse or {}).get("body_ratio"),
            "rvol": (impulse or {}).get("rvol"),
            "impulse_origin": (impulse or {}).get("impulse_origin"),
            "impulse_end": (impulse or {}).get("impulse_end"),
            "reason": (impulse or {}).get("reason"),
        },
        "pullback_input": {
            "as_of_index": bos_index,
            "bos_state": (bos or {}).get("state"),
            "impulse_is_impulse": (impulse or {}).get("is_impulse"),
            "impulse_bar_index": (impulse or {}).get("bar_index"),
            "impulse_origin": (impulse or {}).get("impulse_origin"),
            "impulse_end": (impulse or {}).get("impulse_end"),
            "broken_level": (bos or {}).get("broken_level"),
            "post_impulse_bars_available_at_eval": 0,
        },
        "pullback_output": {
            "status": (pullback or {}).get("pullback_state"),
            "reason": (pullback or {}).get("reason"),
            "retracement_percentage": (pullback or {}).get("retracement_percentage"),
            "structure_intact": (pullback or {}).get("structure_intact"),
            "zone_hit": (pullback or {}).get("zone_hit"),
        },
        "retest_input": {
            "as_of_index": bos_index,
            "pullback_state": (pullback or {}).get("pullback_state"),
            "broken_level": (bos or {}).get("broken_level"),
            "direction": _direction_label(bos if isinstance(bos, Mapping) else None),
        },
        "retest_output": {
            "retest": (retest or {}).get("retest"),
            "state": (retest or {}).get("state"),
            "reason": (retest or {}).get("reason"),
        },
        "bars_available_after_bos": post_bos,
    }

    # Lifecycle probe: freeze BOS+impulse from BOS bar; advance as_of only.
    # Diagnostic only — does not change research runner or production engines.
    follow: list[dict[str, Any]] = []
    frozen_bos = deepcopy(bos) if isinstance(bos, Mapping) else None
    frozen_impulse = deepcopy(impulse) if isinstance(impulse, Mapping) else None
    first_non_waiting: dict[str, Any] | None = None
    if (
        frozen_bos
        and frozen_impulse
        and frozen_impulse.get("is_impulse")
        and frozen_impulse.get("bar_index") is not None
    ):
        end_follow = min(len(candles) - 1, bos_index + max_follow_bars)
        for j in range(bos_index + 1, end_follow + 1):
            pb = detect_pullback(
                candles,
                frozen_bos,
                frozen_impulse,
                signal_config,
                as_of_index=j,
            )
            rt = detect_retest(
                candles,
                frozen_bos,
                pb,
                signal_config,
                direction=_direction_label(frozen_bos),
                as_of_index=j,
            )
            row = {
                "as_of_index": j,
                "bars_after_impulse": j - int(frozen_impulse["bar_index"]),
                "pullback_state": pb.get("pullback_state"),
                "pullback_reason": pb.get("reason"),
                "retracement_percentage": pb.get("retracement_percentage"),
                "retest": rt.get("retest"),
                "retest_state": rt.get("state"),
                "retest_reason": rt.get("reason"),
            }
            follow.append(row)
            if first_non_waiting is None and pb.get("pullback_state") not in (
                "WAITING",
                None,
            ):
                first_non_waiting = row

    same_bar["lifecycle_probe_frozen_impulse"] = {
        "description": (
            "Diagnostic probe only: re-call production detect_pullback with BOS/impulse "
            "frozen at BOS bar while advancing as_of_index. Not used by research runner."
        ),
        "follow_bars_evaluated": len(follow),
        "first_non_waiting": first_non_waiting,
        "any_active_or_confirmed": any(
            r.get("pullback_state") in ("ACTIVE", "CONFIRMED") for r in follow
        ),
        "samples": follow[:12],
    }
    return same_bar


def run_pullback_forensic_diagnostic(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    index_start: int = 0,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
    max_traces: int = 8,
    max_follow_bars: int = 40,
    window_start_iso: str | None = None,
    window_end_iso: str | None = None,
) -> dict[str, Any]:
    """Forensic pullback diagnostic over real OHLCV (research-only)."""
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    # Use unmodified SignalConfig for pullback fingerprint equality with production defaults
    engine = SignalEngine(local_cfg)
    prod_engine = SignalEngine(scfg)
    trend_cache: dict[tuple[str, int], str] = {}

    t0 = time.perf_counter()
    n = len(candles)
    start = max(0, int(index_start))

    bos_candidates = 0
    impulse_candidates = 0
    htf_aligned_candidates = 0
    pullback_evaluations = 0
    pullback_pass = 0
    retest_candidates = 0

    # Parent linkage: each BOS bar id → child stage flags (independent siblings)
    parent_tree: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    post_bos_dist: Counter[str] = Counter()
    post_bos_vs_outcome: dict[str, Counter[str]] = {}
    traces: list[dict[str, Any]] = []

    # Structural impossibility counters for same-bar research path
    same_bar_impulse_waiting_bars = 0
    impulse_pass_pullback_waiting_bars_after = 0

    # Lifecycle probe aggregate (frozen impulse)
    probe_would_pass = 0
    probe_evaluated = 0

    # Unchanged-engine + no-future checks (sampled)
    unchanged_ok = True
    no_future_ok = True
    sample_checks: list[dict[str, Any]] = []

    # Deduplicate consecutive identical BOS (same direction + broken_level)
    last_bos_key: tuple[Any, ...] | None = None
    unique_bos_events = 0

    for i in range(start, n):
        tf_analysis = engine.analyze_timeframe(
            symbol, timeframe, candles, as_of_index=i
        )
        bos = tf_analysis.get("bos")
        if not _bos_confirmed(bos if isinstance(bos, Mapping) else None):
            continue

        bos_candidates += 1
        impulse = tf_analysis.get("impulse")
        pullback = tf_analysis.get("pullback")
        retest = tf_analysis.get("retest")
        trend = tf_analysis.get("trend") or {}

        bos_m = bos if isinstance(bos, Mapping) else {}
        bos_key = (
            bos_m.get("direction"),
            bos_m.get("broken_level"),
            bos_m.get("break_timestamp") or (candle_time(candles[i]).isoformat() if candle_time(candles[i]) else i),
        )
        is_new_event = bos_key != last_bos_key
        if is_new_event:
            unique_bos_events += 1
            last_bos_key = bos_key

        impulse_ok = _impulse_confirmed(impulse if isinstance(impulse, Mapping) else None)
        pullback_ok = _pullback_confirmed(pullback if isinstance(pullback, Mapping) else None)
        retest_ok = _retest_confirmed(retest if isinstance(retest, Mapping) else None)

        if impulse_ok:
            impulse_candidates += 1
            imp_bar = (impulse or {}).get("bar_index")
            if imp_bar is not None and int(imp_bar) >= i:
                same_bar_impulse_waiting_bars += 1
            if (
                pullback
                and pullback.get("pullback_state") == "WAITING"
                and "Waiting for bars after impulse" in str(pullback.get("reason") or "")
            ):
                impulse_pass_pullback_waiting_bars_after += 1

        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=candles,
            as_of_index=i,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=local_cfg,
            trend_15m=str(trend.get("trend") or "INSUFFICIENT_DATA"),
            trend_cache=trend_cache,
            include_5m=False,
        )
        htf_alignment = classify_htf_alignment(
            bos_direction=bos_m.get("direction"),
            trend_4h=htf.get("trend_4h"),
            trend_1h=htf.get("trend_1h"),
        )
        htf_ok = htf_alignment == HTF_ALIGNED
        if htf_ok:
            htf_aligned_candidates += 1

        pullback_evaluations += 1
        pb_state = str((pullback or {}).get("pullback_state") or "NONE")
        status_counter[pb_state] += 1
        rk = _reason_key(pullback if isinstance(pullback, Mapping) else None)
        reason_counter[rk] += 1
        if pullback_ok:
            pullback_pass += 1
        if retest_ok:
            retest_candidates += 1

        post_bos = max(0, n - 1 - i)
        bucket = _post_bos_bucket(post_bos)
        post_bos_dist[bucket] += 1
        post_bos_vs_outcome.setdefault(bucket, Counter())[pb_state] += 1

        event_id = f"{symbol.upper()}:{timeframe}:{i}:{bos_m.get('direction')}"
        parent_tree.append(
            {
                "event_id": event_id,
                "bos_index": i,
                "bos": True,
                "htf_aligned": htf_ok,
                "impulse": impulse_ok,
                "pullback": pullback_ok,
                "retest": retest_ok,
                "pullback_state": pb_state,
                "pullback_reason": (pullback or {}).get("reason"),
                "bars_after_bos": post_bos,
                "independent_of_htf": {
                    "impulse_without_htf": bool(impulse_ok and not htf_ok),
                    "htf_without_impulse": bool(htf_ok and not impulse_ok),
                },
            }
        )

        # Frozen-impulse lifecycle probe (aggregate)
        if impulse_ok and is_new_event:
            probe_evaluated += 1
            frozen_bos = deepcopy(bos_m)
            frozen_impulse = deepcopy(impulse if isinstance(impulse, Mapping) else {})
            saw_pass = False
            end_follow = min(n - 1, i + max_follow_bars)
            for j in range(i + 1, end_follow + 1):
                pb_j = detect_pullback(
                    candles,
                    frozen_bos,
                    frozen_impulse,
                    local_cfg,
                    as_of_index=j,
                )
                if pb_j.get("pullback_state") in ("ACTIVE", "CONFIRMED"):
                    saw_pass = True
                    break
            if saw_pass:
                probe_would_pass += 1

        if is_new_event and len(traces) < max_traces and impulse_ok:
            traces.append(
                _trace_event(
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=candles,
                    bos_index=i,
                    tf_analysis=tf_analysis,
                    signal_config=local_cfg,
                    max_follow_bars=max_follow_bars,
                )
            )
            # Sample invariant checks
            chk = pullback_engine_unchanged_check(
                candles,
                dict(bos_m),
                dict(impulse) if isinstance(impulse, Mapping) else {},
                local_cfg,
                as_of_index=i,
            )
            if not chk["identical"]:
                unchanged_ok = False
            nf = evaluate_pullback_no_future(
                candles,
                dict(bos_m),
                dict(impulse) if isinstance(impulse, Mapping) else {},
                local_cfg,
                as_of_index=i,
            )
            if not nf["full_equals_truncated"]:
                no_future_ok = False
            if len(sample_checks) < 3:
                prod_tf = prod_engine.analyze_timeframe(
                    symbol, timeframe, candles, as_of_index=i
                )
                research_pb = (
                    (pullback or {}).get("pullback_state"),
                    (pullback or {}).get("reason"),
                )
                prod_pb = (
                    (prod_tf.get("pullback") or {}).get("pullback_state"),
                    (prod_tf.get("pullback") or {}).get("reason"),
                )
                sample_checks.append(
                    {
                        "bos_index": i,
                        "engine_unchanged": chk["identical"],
                        "no_future_candles": nf["full_equals_truncated"],
                        "production_fingerprint": chk["fingerprint_a"],
                        "research_vs_production_signalconfig": {
                            "research": research_pb,
                            "production_defaults": prod_pb,
                            "same_state_reason": research_pb == prod_pb,
                        },
                    }
                )

    total_reasons = sum(reason_counter.values()) or 1
    reasons_table = [
        {
            "reason_status": k,
            "count": v,
            "percentage": round(100.0 * v / total_reasons, 2),
        }
        for k, v in reason_counter.most_common()
    ]
    statuses_table = {
        k: {"count": v, "percentage": round(100.0 * v / (sum(status_counter.values()) or 1), 2)}
        for k, v in status_counter.items()
    }

    # Determine lifecycle match
    mismatch_reasons: list[str] = []
    if impulse_pass_pullback_waiting_bars_after > 0:
        mismatch_reasons.append(
            "On BOS+impulse bars, pullback returns WAITING 'Waiting for bars after impulse' "
            "because impulse.bar_index == as_of_index (same-bar pairing)."
        )
    mismatch_reasons.append(
        "Neither production SignalEngine.analyze_timeframe nor research runner freezes "
        "BOS/impulse state across subsequent candles; pullback engine requires post-impulse bars."
    )
    if probe_would_pass > 0 and pullback_pass == 0:
        mismatch_reasons.append(
            f"Lifecycle probe (frozen impulse, advance as_of) found ACTIVE/CONFIRMED on "
            f"{probe_would_pass}/{probe_evaluated} impulse BOS events — research same-bar "
            f"path scored pullback_pass=0."
        )

    research_matches_production = True  # same invocation pattern
    # But both mismatch the pullback engine's required multi-bar lifecycle
    lifecycle = {
        "research_matches_production": research_matches_production,
        "research_matches_pullback_engine_required_lifecycle": False,
        "mismatch_reasons": mismatch_reasons,
        "same_bar_impulse_with_waiting_for_bars_after": impulse_pass_pullback_waiting_bars_after,
        "same_bar_impulse_bar_index_ge_as_of": same_bar_impulse_waiting_bars,
        "frozen_impulse_probe": {
            "impulse_bos_events_probed": probe_evaluated,
            "would_reach_active_or_confirmed": probe_would_pass,
            "note": (
                "Probe only — demonstrates multi-bar evaluation with frozen impulse. "
                "Not a strategy change."
            ),
        },
        "waiting_semantics": PRODUCTION_PULLBACK_AUDIT["waiting_semantics"],
        "waiting_not_converted_to_fail": True,
    }

    counter_model = {
        "model": "INDEPENDENT_SIBLINGS_ON_BOS_BARS",
        "explanation": (
            "Counters are co-occurrence counts among bars where BOS is CONFIRMED. "
            "They are NOT a sequential funnel. Impulse is not a subset of HTF-aligned; "
            "HTF is not a subset of impulse. Pullback/retest are evaluated on the same "
            "BOS bar independently as boolean flags."
        ),
        "bos_candidates": bos_candidates,
        "unique_bos_events_approx": unique_bos_events,
        "htf_aligned_candidates": htf_aligned_candidates,
        "impulse_candidates": impulse_candidates,
        "pullback_candidates_pass": pullback_pass,
        "retest_candidates": retest_candidates,
        "impulse_without_htf": sum(
            1 for e in parent_tree if e["impulse"] and not e["htf_aligned"]
        ),
        "htf_without_impulse": sum(
            1 for e in parent_tree if e["htf_aligned"] and not e["impulse"]
        ),
        "impulse_and_htf": sum(
            1 for e in parent_tree if e["impulse"] and e["htf_aligned"]
        ),
    }

    # Data quality / warmup
    first_ts = candle_time(candles[0]) if candles else None
    last_ts = candle_time(candles[-1]) if candles else None
    eval_first_ts = candle_time(candles[start]) if start < n else None
    near_start = sum(1 for e in parent_tree if e["bos_index"] < start + 20)
    near_end = sum(1 for e in parent_tree if e["bos_index"] >= n - 20)

    root_cause = "MULTIPLE_ISSUES"
    if pullback_pass == 0 and impulse_pass_pullback_waiting_bars_after == impulse_candidates and impulse_candidates > 0:
        root_cause = "RESEARCH_LIFECYCLE_PROBLEM"
        # Also state persistence / production invocation share the bug
        if probe_would_pass > 0:
            root_cause = "MULTIPLE_ISSUES"

    elapsed = time.perf_counter() - t0
    return {
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "window": {
            "start": window_start_iso or (eval_first_ts.isoformat() if eval_first_ts else None),
            "end": window_end_iso or (last_ts.isoformat() if last_ts else None),
            "index_start": start,
            "candles_loaded": n,
            "bars_scanned": max(0, n - start),
            "series_first": first_ts.isoformat() if first_ts else None,
            "series_last": last_ts.isoformat() if last_ts else None,
        },
        "bos_candidates": bos_candidates,
        "impulse_candidates": impulse_candidates,
        "pullback_evaluations": pullback_evaluations,
        "pullback_pass": pullback_pass,
        "retest_candidates": retest_candidates,
        "htf_aligned_candidates": htf_aligned_candidates,
        "statuses": statuses_table,
        "reasons": {r["reason_status"]: r["count"] for r in reasons_table},
        "reasons_table": reasons_table,
        "post_bos_candle_distribution": dict(post_bos_dist),
        "post_bos_vs_pullback_state": {
            b: dict(c) for b, c in post_bos_vs_outcome.items()
        },
        "counter_model": counter_model,
        "parent_events_sample": parent_tree[:40],
        "event_traces": traces,
        "lifecycle": lifecycle,
        "production_pullback_audit": PRODUCTION_PULLBACK_AUDIT,
        "production_vs_research": PRODUCTION_VS_RESEARCH_INVOCATION,
        "timeframe_mapping": TIMEFRAME_MAPPING,
        "data_quality": {
            "candle_source_expected": "postgresql",
            "fabricated_candles": False,
            "warmup_index_start": start,
            "bos_near_window_start": near_start,
            "bos_near_window_end": near_end,
            "min_bars_config": rcfg.min_bars,
            "invariant_checks": {
                "pullback_engine_output_unchanged_by_diagnostic": unchanged_ok,
                "no_future_candles_used": no_future_ok,
                "waiting_not_converted_to_fail": True,
                "samples": sample_checks,
            },
        },
        "root_cause_classification": root_cause,
        "bugs": _bugs_report(
            pullback_pass=pullback_pass,
            impulse_candidates=impulse_candidates,
            waiting_bars_after=impulse_pass_pullback_waiting_bars_after,
            probe_would_pass=probe_would_pass,
            probe_evaluated=probe_evaluated,
        ),
        "elapsed_seconds": elapsed,
        "disclaimer": DISCLAIMER,
        "live_engines_unchanged": True,
        "pullback_logic_unchanged": True,
        "note": (
            "Diagnostic only. WAITING means not yet satisfied under current as_of — "
            "not converted to FAIL. Counters are independent siblings on BOS bars."
        ),
    }


def _bugs_report(
    *,
    pullback_pass: int,
    impulse_candidates: int,
    waiting_bars_after: int,
    probe_would_pass: int,
    probe_evaluated: int,
) -> list[dict[str, Any]]:
    bugs: list[dict[str, Any]] = []
    if impulse_candidates > 0 and waiting_bars_after == impulse_candidates and pullback_pass == 0:
        bugs.append(
            {
                "id": "SAME_BAR_IMPULSE_PULLBACK_DEADLOCK",
                "BUG": (
                    "Research (and production analyze_timeframe) evaluate pullback on the "
                    "same as_of_index where impulse.bar_index is set. detect_pullback "
                    "requires impulse.bar_index < as_of_index, so every impulse BOS bar "
                    "returns WAITING 'Waiting for bars after impulse'. Pullback PASS is "
                    "structurally unreachable on the research same-bar path."
                ),
                "Evidence": (
                    f"impulse_candidates={impulse_candidates}, "
                    f"waiting_for_bars_after_impulse={waiting_bars_after}, "
                    f"pullback_pass={pullback_pass}; "
                    f"frozen-impulse probe ACTIVE/CONFIRMED on "
                    f"{probe_would_pass}/{probe_evaluated} events"
                ),
                "Affected path": (
                    "SignalEngine.analyze_timeframe → detect_impulse + detect_pullback "
                    "same as_of; bos_strategy_comparison diagnostics/runner/event_store "
                    "only score stages on BOS-CONFIRMED bars"
                ),
                "Suggested fix": (
                    "Preserve CONFIRMED BOS + impulse at detection bar; re-evaluate "
                    "detect_pullback/detect_retest on subsequent bars with frozen "
                    "bos/impulse and advancing as_of_index until ACTIVE/CONFIRMED, "
                    "FAILED, INVALIDATED, or a research expiry window. "
                    "Do not loosen pullback thresholds."
                ),
                "implemented_in_this_task": False,
            }
        )
    return bugs
