"""Forensic S3 (STRATEGY_3) S/D confluence diagnostic — research only.

Traces BOS→Impulse→Pullback→S/D→Retest→HTF→Entry using the same lifecycle
and `_sd_confluence` / `detect_zones_as_of` path as the strategy runner.

Does NOT modify production S/D, BOS, pullback, retest, or signal engines.
Does NOT loosen thresholds or fabricate zones.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.config import get_settings
from app.engines.supply_demand.engine import SupplyDemandEngine, SupplyDemandZone
from app.research.bos_strategy_comparison.config import (
    DISCLAIMER,
    StrategyResearchConfig,
)
from app.research.bos_strategy_comparison.engine import (
    _clone_signal_config,
    evaluate_strategy_at_bar,
)
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.bos_strategy_comparison.htf_forensics import (
    aggregate_htf_forensics,
    build_htf_survivor_trace,
)
from app.research.bos_strategy_comparison.lifecycle import (
    LifecycleResult,
    candles_as_of,
    discover_impulse_events,
    eval_at_index,
    first_pullback_pass_eval,
    first_retest_pass_eval,
    run_lifecycle,
)
from app.research.bos_strategy_comparison.strategies import get_strategy
from app.research.combination_engine import _sd_confluence
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine

# Band used by existing _sd_confluence (research/production shared helper).
_SD_CLOSE_BAND = 0.005  # ±0.5%

# Zone mapping required by existing _sd_confluence (do not change).
ZONE_MAPPING = {
    "LONG": "demand",
    "SHORT": "supply",
}

TIMEFRAME_MAPPING = {
    "sd_timeframe": "setup (same as BOS/pullback/retest; typically 15m)",
    "bos_timeframe": "setup",
    "pullback_timeframe": "setup",
    "retest_timeframe": "setup",
    "htf_timeframes": ("4h", "1h"),
    "note": (
        "detect_zones_as_of(symbol, timeframe, candles, as_of_index) uses the "
        "setup series timeframe — not a separate HTF S/D series."
    ),
}


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _direction_from_bos(bos_direction: str | None) -> str | None:
    if bos_direction == "BULLISH_BOS":
        return "LONG"
    if bos_direction == "BEARISH_BOS":
        return "SHORT"
    return None


def inspect_zones_as_of(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    sd_engine: SupplyDemandEngine | None = None,
    apply_status_update: bool = False,
) -> dict[str, Any]:
    """Research-only zone snapshot using SupplyDemandEngine on truncated series.

    Mirrors detect_zones_as_of selection (nearest non-BROKEN/EXPIRED by mid
    distance) but exposes full zone fields and rejection reasons.

    apply_status_update=False matches research detect_zones_as_of (production
    orchestrator calls update_zone_status; research adapter currently does not).
    """
    engine = sd_engine or SupplyDemandEngine(get_settings().indicators_config)
    truncated = candles_as_of(candles, as_of_index)
    eval_ts = candle_time(truncated[-1]) if truncated else None
    result: dict[str, Any] = {
        "as_of_index": int(as_of_index),
        "eval_time": _iso(eval_ts),
        "timeframe": timeframe,
        "source": "SupplyDemandEngine.detect_zones(truncated_as_of)",
        "truncated_len": len(truncated),
        "min_candles_required": 30,
        "available": False,
        "demand_zone": None,
        "supply_zone": None,
        "demand_detail": None,
        "supply_detail": None,
        "all_zones": [],
        "status_update_applied": bool(apply_status_update),
        "lookahead_future_zone": False,
        "rejection_reason": None,
    }
    if len(truncated) < 30:
        result["rejection_reason"] = "INSUFFICIENT_CANDLES"
        return result

    zones = engine.detect_zones(symbol, timeframe, list(truncated))
    if apply_status_update:
        zones = [engine.update_zone_status(z, list(truncated), now=_as_utc(eval_ts)) for z in zones]

    last_close = ohlc(truncated, len(truncated) - 1)[3]
    best_d: tuple[float, SupplyDemandZone] | None = None
    best_s: tuple[float, SupplyDemandZone] | None = None
    zone_rows: list[dict[str, Any]] = []
    future_zone = False

    for z in zones:
        created = _as_utc(z.created_at)
        dep_idx = z.meta.get("departure_index") if isinstance(z.meta, dict) else None
        impulse_idx = z.meta.get("impulse_index") if isinstance(z.meta, dict) else None
        if created is not None and eval_ts is not None and created > _as_utc(eval_ts):
            future_zone = True
        if dep_idx is not None and int(dep_idx) > as_of_index:
            future_zone = True
        if impulse_idx is not None and int(impulse_idx) > as_of_index:
            future_zone = True

        mid = (z.high + z.low) / 2.0
        dist = abs(mid - last_close)
        band_lo = min(z.low, z.high) * (1.0 - _SD_CLOSE_BAND)
        band_hi = max(z.low, z.high) * (1.0 + _SD_CLOSE_BAND)
        in_band = band_lo <= last_close <= band_hi
        row = {
            "zone_type": z.zone_type.value,
            "high": z.high,
            "low": z.low,
            "status": z.status.value,
            "created_at": _iso(created),
            "strength": z.strength,
            "freshness": z.freshness,
            "touch_count": z.touch_count,
            "distance_to_close": dist,
            "close_in_confluence_band": in_band,
            "confluence_band": [band_lo, band_hi],
            "impulse_index": impulse_idx,
            "departure_index": dep_idx,
            "filtered_broken_or_expired": z.status.value in ("BROKEN", "EXPIRED"),
        }
        zone_rows.append(row)
        if z.status.value in ("BROKEN", "EXPIRED"):
            continue
        if z.zone_type.value == "demand":
            if best_d is None or dist < best_d[0]:
                best_d = (dist, z)
        else:
            if best_s is None or dist < best_s[0]:
                best_s = (dist, z)

    result["all_zones"] = zone_rows
    result["lookahead_future_zone"] = future_zone
    result["zones_detected_raw"] = len(zones)
    result["zones_after_status_filter"] = sum(
        1 for r in zone_rows if not r["filtered_broken_or_expired"]
    )

    if best_d:
        z = best_d[1]
        result["demand_zone"] = (z.low, z.high)
        result["demand_detail"] = next(
            r for r in zone_rows if r["zone_type"] == "demand" and r["low"] == z.low and r["high"] == z.high
        )
    if best_s:
        z = best_s[1]
        result["supply_zone"] = (z.low, z.high)
        result["supply_detail"] = next(
            r for r in zone_rows if r["zone_type"] == "supply" and r["low"] == z.low and r["high"] == z.high
        )

    result["available"] = result["demand_zone"] is not None or result["supply_zone"] is not None
    if not zones:
        result["rejection_reason"] = "NO_ZONES_DETECTED"
    elif not result["available"]:
        result["rejection_reason"] = "ALL_ZONES_BROKEN_OR_EXPIRED"
    return result


def classify_sd_confluence(
    *,
    direction: str | None,
    pullback: Mapping[str, Any] | None,
    demand_zone: tuple[float, float] | None,
    supply_zone: tuple[float, float] | None,
    last_close: float | None,
    zone_snap: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify S/D outcome using the EXISTING `_sd_confluence` boolean + reasons.

    Reasons document which branch of the existing helper fired — they do not
    invent a second confluence calculator.
    """
    zone_hit = (pullback or {}).get("zone_hit")
    sd_ok = _sd_confluence(
        pullback=pullback,
        direction=direction,
        demand_zone=demand_zone,
        supply_zone=supply_zone,
        last_close=last_close,
    )

    required_type = ZONE_MAPPING.get(direction or "", None)
    required_zone = (
        demand_zone
        if required_type == "demand"
        else supply_zone
        if required_type == "supply"
        else None
    )
    opposite_zone = (
        supply_zone
        if required_type == "demand"
        else demand_zone
        if required_type == "supply"
        else None
    )

    status = "PASS" if sd_ok else "FAIL"
    reason: str
    distance: float | None = None
    zone_status: str | None = None
    zone_created_at: str | None = None
    zone_id: str | None = None

    detail_key = "demand_detail" if required_type == "demand" else "supply_detail"
    detail = (zone_snap or {}).get(detail_key) if zone_snap else None
    if isinstance(detail, Mapping):
        distance = detail.get("distance_to_close")
        zone_status = detail.get("status")
        zone_created_at = detail.get("created_at")
        if detail.get("low") is not None and detail.get("high") is not None:
            zone_id = f"{required_type}:{detail['low']}:{detail['high']}"

    if sd_ok:
        if zone_hit in ("demand", "supply"):
            reason = f"ZONE_HIT_{str(zone_hit).upper()}"
        elif direction == "LONG" and demand_zone is not None:
            reason = "CLOSE_IN_DEMAND_BAND"
        elif direction == "SHORT" and supply_zone is not None:
            reason = "CLOSE_IN_SUPPLY_BAND"
        else:
            reason = "SD_CONFLUENCE_PASS"
        availability = "AVAILABLE"
    else:
        snap_reason = (zone_snap or {}).get("rejection_reason")
        if snap_reason == "INSUFFICIENT_CANDLES":
            reason = "INSUFFICIENT_CANDLES"
            availability = "UNAVAILABLE"
            status = "UNAVAILABLE"
        elif snap_reason == "NO_ZONES_DETECTED":
            reason = "NO_ZONES_DETECTED"
            availability = "UNAVAILABLE"
            status = "UNAVAILABLE"
        elif required_zone is None and opposite_zone is not None:
            reason = (
                "NO_DEMAND_ZONE"
                if required_type == "demand"
                else "NO_SUPPLY_ZONE"
                if required_type == "supply"
                else "NO_REQUIRED_ZONE"
            )
            availability = "UNAVAILABLE_REQUIRED_TYPE"
            status = "UNAVAILABLE"
        elif required_zone is None:
            reason = (
                "NO_DEMAND_ZONE"
                if required_type == "demand"
                else "NO_SUPPLY_ZONE"
                if required_type == "supply"
                else "NO_REQUIRED_ZONE"
            )
            availability = "UNAVAILABLE"
            status = "UNAVAILABLE"
        else:
            # Zone of correct type selected, but close outside ±0.5% band and
            # pullback.zone_hit not in (demand, supply).
            reason = "CLOSE_OUTSIDE_CONFLUENCE_BAND"
            availability = "AVAILABLE"
            status = "FAIL"

    return {
        "status": status,
        "pass": bool(sd_ok),
        "reason": reason,
        "availability": availability,
        "zone_hit_from_pullback": zone_hit,
        "required_zone_type": required_type,
        "zone_type": required_type if required_zone is not None else None,
        "zone_id": zone_id,
        "zone_created_at": zone_created_at,
        "zone_status": zone_status,
        "distance": distance,
        "confluence_band_pct": _SD_CLOSE_BAND * 100.0,
        "last_close": last_close,
        "demand_zone": list(demand_zone) if demand_zone else None,
        "supply_zone": list(supply_zone) if supply_zone else None,
        "mapping_correct": (
            (direction == "LONG" and required_type == "demand")
            or (direction == "SHORT" and required_type == "supply")
            or direction is None
        ),
    }


def _empty_dir_bucket() -> dict[str, int]:
    return {
        "bos_lifecycles": 0,
        "pullback_pass": 0,
        "retest_pass": 0,
        "sd_evaluated": 0,
        "sd_available": 0,
        "sd_confluence_pass": 0,
        "htf_pass": 0,
        "entry_ready": 0,
        "rr_pass": 0,
        "final_s3_trades": 0,
    }


def _lifecycle_structure_override(
    lc: LifecycleResult,
    *,
    entry_index: int,
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ev = eval_at_index(lc, entry_index)
    if ev is None:
        prior = [e for e in lc.evaluations if e.as_of_index <= entry_index]
        ev = prior[-1] if prior else None
    pb_first = first_pullback_pass_eval(lc)
    rt_first = first_retest_pass_eval(lc)
    pb_ts = None
    rt_ts = None
    if pb_first is not None and 0 <= pb_first.as_of_index < len(candles):
        t = candle_time(candles[pb_first.as_of_index])
        pb_ts = t.isoformat() if t else None
    if rt_first is not None and 0 <= rt_first.as_of_index < len(candles):
        t = candle_time(candles[rt_first.as_of_index])
        rt_ts = t.isoformat() if t else None
    return {
        "bos": lc.event.frozen_bos,
        "impulse": lc.event.frozen_impulse,
        "pullback": (ev.pullback if ev else {}),
        "retest": (ev.retest if ev else {}),
        "lifecycle_id": lc.event.lifecycle_id,
        "bos_timestamp": lc.event.bos_timestamp,
        "impulse_timestamp": lc.event.impulse_timestamp,
        "pullback_timestamp": pb_ts,
        "retest_timestamp": rt_ts,
    }


def evaluate_sd_at_index(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    direction: str | None,
    pullback: Mapping[str, Any] | None,
    sd_engine: SupplyDemandEngine,
) -> dict[str, Any]:
    """S/D evaluation at a single as_of bar (no look-ahead)."""
    snap = inspect_zones_as_of(
        symbol, timeframe, candles, as_of_index, sd_engine=sd_engine
    )
    end = min(as_of_index, len(candles) - 1)
    last_close = ohlc(candles, end)[3] if end >= 0 else None
    classified = classify_sd_confluence(
        direction=direction,
        pullback=pullback,
        demand_zone=tuple(snap["demand_zone"]) if snap.get("demand_zone") else None,
        supply_zone=tuple(snap["supply_zone"]) if snap.get("supply_zone") else None,
        last_close=last_close,
        zone_snap=snap,
    )
    return {
        "sd_time": snap.get("eval_time"),
        "as_of_index": as_of_index,
        **classified,
        "zone_snap": {
            "source": snap.get("source"),
            "timeframe": timeframe,
            "truncated_len": snap.get("truncated_len"),
            "zones_detected_raw": snap.get("zones_detected_raw"),
            "status_update_applied": snap.get("status_update_applied"),
            "lookahead_future_zone": snap.get("lookahead_future_zone"),
            "demand_detail": snap.get("demand_detail"),
            "supply_detail": snap.get("supply_detail"),
            "rejection_reason": snap.get("rejection_reason"),
        },
    }


def run_s3_forensic_diagnostic(
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
    direction_filter: str | None = None,
    trace: bool = False,
    htf_trace: bool = False,
    limit: int = 100,
    max_follow_bars: int | None = None,
) -> dict[str, Any]:
    """True sequential S3 lifecycle funnel + S/D rejection forensics.

    htf_trace=True: decompose HTF failure for every S/D PASS survivor.
    """
    t0 = time.perf_counter()
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    engine = SignalEngine(local_cfg)
    sd_engine = SupplyDemandEngine(get_settings().indicators_config)
    trend_cache: dict[tuple[str, int], str] = {}
    s3 = get_strategy("STRATEGY_3")
    c3 = get_strategy("CONTROL_C")
    assert s3 is not None and c3 is not None

    follow = (
        int(max_follow_bars)
        if max_follow_bars is not None
        else int(getattr(rcfg, "research_max_lifecycle_bars", 40) or 40)
    )
    start = max(0, int(index_start))
    dfilt = (direction_filter or "ALL").upper()

    events, bos_candidates = discover_impulse_events(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        index_start=start,
        signal_config=scfg,
        research_config=rcfg,
    )

    # Funnel counters (true subsets along S3 eligibility path)
    funnel = {
        "bos_lifecycles": 0,
        "lifecycles_started": 0,
        "pullback_pass": 0,
        "retest_pass": 0,
        "sd_evaluated": 0,
        "sd_available": 0,
        "sd_confluence_pass": 0,
        "htf_pass": 0,
        "entry_ready": 0,
        "rr_pass": 0,
        "final_s3_trades": 0,
    }
    direction_funnel = {"LONG": _empty_dir_bucket(), "SHORT": _empty_dir_bucket()}
    sd_statuses: Counter[str] = Counter()
    sd_rejection_reasons: Counter[str] = Counter()
    availability_split = Counter()
    zones_counts = {"supply_evaluated": 0, "demand_evaluated": 0}
    future_zone_detected = False
    broken_after_break_qualifying = 0
    traces: list[dict[str, Any]] = []
    htf_survivor_traces: list[dict[str, Any]] = []
    c3_rows: list[dict[str, Any]] = []
    s1_s2_context: list[dict[str, Any]] = []

    lifecycles: list[LifecycleResult] = []
    for ev in events:
        lc = run_lifecycle(
            candles=candles,
            event=ev,
            signal_config=local_cfg,
            max_follow_bars=follow,
        )
        lifecycles.append(lc)

    for lc in lifecycles:
        direction = _direction_from_bos(lc.event.bos_direction)
        if dfilt in ("LONG", "SHORT") and direction != dfilt:
            continue

        funnel["bos_lifecycles"] += 1
        funnel["lifecycles_started"] += 1
        if direction in direction_funnel:
            direction_funnel[direction]["bos_lifecycles"] += 1

        pb_ev = first_pullback_pass_eval(lc)
        rt_ev = first_retest_pass_eval(lc)

        if not lc.pullback_pass or pb_ev is None:
            continue
        funnel["pullback_pass"] += 1
        if direction in direction_funnel:
            direction_funnel[direction]["pullback_pass"] += 1

        if not lc.retest_pass or rt_ev is None:
            # Still record S/D at pullback for context (not in sequential S3 funnel after retest)
            continue

        # --- Sequential S3 path: retest PASS is eligibility (matches runner) ---
        funnel["retest_pass"] += 1
        if direction in direction_funnel:
            direction_funnel[direction]["retest_pass"] += 1

        eval_i = rt_ev.as_of_index
        pullback = rt_ev.pullback
        sd = evaluate_sd_at_index(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            as_of_index=eval_i,
            direction=direction,
            pullback=pullback,
            sd_engine=sd_engine,
        )
        funnel["sd_evaluated"] += 1
        if direction in direction_funnel:
            direction_funnel[direction]["sd_evaluated"] += 1

        if sd.get("zone_snap", {}).get("lookahead_future_zone"):
            future_zone_detected = True
        if sd.get("demand_zone"):
            zones_counts["demand_evaluated"] += 1
        if sd.get("supply_zone"):
            zones_counts["supply_evaluated"] += 1

        avail = str(sd.get("availability") or "")
        availability_split[avail] += 1
        sd_statuses[str(sd.get("status"))] += 1

        # "available" for funnel = required zone type present OR pass via zone_hit
        required_zone_present = (
            (direction == "LONG" and sd.get("demand_zone") is not None)
            or (direction == "SHORT" and sd.get("supply_zone") is not None)
            or (sd.get("zone_hit_from_pullback") in ("demand", "supply"))
        )
        if required_zone_present or sd.get("pass"):
            funnel["sd_available"] += 1
            if direction in direction_funnel:
                direction_funnel[direction]["sd_available"] += 1
        else:
            sd_rejection_reasons[str(sd.get("reason"))] += 1

        if not sd.get("pass"):
            if required_zone_present:
                sd_rejection_reasons[str(sd.get("reason"))] += 1
            c3_rows.append(
                {
                    "lifecycle_id": lc.event.lifecycle_id,
                    "direction": direction,
                    "retest_index": eval_i,
                    "sd_status": sd.get("status"),
                    "sd_reason": sd.get("reason"),
                    "sd_availability": sd.get("availability"),
                    "zone_type": sd.get("zone_type"),
                    "distance": sd.get("distance"),
                }
            )
            if trace and len(traces) < limit:
                traces.append(
                    _build_trace(
                        lc=lc,
                        candles=candles,
                        pb_ev=pb_ev,
                        rt_ev=rt_ev,
                        sd=sd,
                        htf_status=None,
                        entry_status=None,
                        rr_status=None,
                        final=False,
                    )
                )
            continue

        funnel["sd_confluence_pass"] += 1
        if direction in direction_funnel:
            direction_funnel[direction]["sd_confluence_pass"] += 1

        # HTF / entry / RR via same evaluate_strategy_at_bar path as runner
        override = _lifecycle_structure_override(lc, entry_index=eval_i, candles=candles)
        demand_zone = tuple(sd["demand_zone"]) if sd.get("demand_zone") else None
        supply_zone = tuple(sd["supply_zone"]) if sd.get("supply_zone") else None
        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=candles,
            as_of_index=eval_i,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=local_cfg,
            trend_cache=trend_cache,
            include_5m=False,
        )
        htf_alignment = classify_htf_alignment(
            bos_direction=lc.event.bos_direction,
            trend_4h=htf.get("trend_4h"),
            trend_1h=htf.get("trend_1h"),
        )
        htf_ok = htf_alignment == HTF_ALIGNED
        htf_detail = {
            "status": htf_alignment,
            "trend_4h": htf.get("trend_4h"),
            "trend_1h": htf.get("trend_1h"),
            "trend_15m": htf.get("trend_15m"),
        }

        if htf_trace:
            elig_ts = candle_time(candles[eval_i]) if 0 <= eval_i < len(candles) else None
            pb_ts = (
                _iso(candle_time(candles[pb_ev.as_of_index]))
                if pb_ev is not None and 0 <= pb_ev.as_of_index < len(candles)
                else None
            )
            rt_ts = (
                _iso(candle_time(candles[rt_ev.as_of_index]))
                if rt_ev is not None and 0 <= rt_ev.as_of_index < len(candles)
                else None
            )
            htf_survivor_traces.append(
                build_htf_survivor_trace(
                    symbol=symbol,
                    direction=direction,
                    bos_direction=lc.event.bos_direction,
                    lifecycle_id=lc.event.lifecycle_id,
                    eligibility_time=elig_ts,
                    bos_time=lc.event.bos_timestamp,
                    pullback_time=pb_ts,
                    retest_time=rt_ts,
                    sd_time=sd.get("sd_time"),
                    candles_4h=candles_4h or [],
                    candles_1h=candles_1h or [],
                    setup_candles=candles,
                    eligibility_index=eval_i,
                    config=local_cfg,
                    signal_engine=engine,
                )
            )

        setup = evaluate_strategy_at_bar(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            as_of_index=eval_i,
            strategy=s3,
            signal_config=scfg,
            research_config=rcfg,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            signal_engine=engine,
            sd_engine=sd_engine,
            compute_sd=False,
            trend_cache=trend_cache,
            local_cfg=local_cfg,
            htf=htf,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            structure_override=override,
        )
        gates = dict(setup.get("gates") or {})
        entry_ok = bool(gates.get("entry_ready")) or str(setup.get("status") or "") in (
            "LONG_ENTRY_CANDIDATE",
            "SHORT_ENTRY_CANDIDATE",
        )
        rr_ok = bool(gates.get("rr")) or (
            str(setup.get("status") or "")
            in ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE")
        )
        final_ok = str(setup.get("status") or "") in (
            "LONG_ENTRY_CANDIDATE",
            "SHORT_ENTRY_CANDIDATE",
        )
        if final_ok:
            entry_ok = True
            rr_ok = True

        if htf_ok:
            funnel["htf_pass"] += 1
            if direction in direction_funnel:
                direction_funnel[direction]["htf_pass"] += 1
        else:
            if trace and len(traces) < limit:
                traces.append(
                    _build_trace(
                        lc=lc,
                        candles=candles,
                        pb_ev=pb_ev,
                        rt_ev=rt_ev,
                        sd=sd,
                        htf_status=htf_detail,
                        entry_status="NOT_EVALUATED_HTF_FAIL",
                        rr_status="NOT_EVALUATED_HTF_FAIL",
                        final=False,
                    )
                )
            c3_rows.append(
                {
                    "lifecycle_id": lc.event.lifecycle_id,
                    "direction": direction,
                    "retest_index": eval_i,
                    "sd_status": "PASS",
                    "sd_reason": sd.get("reason"),
                    "sd_availability": "AVAILABLE",
                    "rejected_after_sd": htf_detail,
                }
            )
            continue

        if entry_ok:
            funnel["entry_ready"] += 1
            if direction in direction_funnel:
                direction_funnel[direction]["entry_ready"] += 1
        if rr_ok:
            funnel["rr_pass"] += 1
            if direction in direction_funnel:
                direction_funnel[direction]["rr_pass"] += 1
        if final_ok:
            funnel["final_s3_trades"] += 1
            if direction in direction_funnel:
                direction_funnel[direction]["final_s3_trades"] += 1

        if trace and len(traces) < limit:
            traces.append(
                _build_trace(
                    lc=lc,
                    candles=candles,
                    pb_ev=pb_ev,
                    rt_ev=rt_ev,
                    sd=sd,
                    htf_status=htf_detail,
                    entry_status="READY" if entry_ok else str(setup.get("reason")),
                    rr_status="PASS" if rr_ok else str(setup.get("reason")),
                    final=final_ok,
                )
            )

        # Lookahead / broken-zone checks on selected zone detail
        detail = (sd.get("zone_snap") or {}).get(
            "demand_detail" if direction == "LONG" else "supply_detail"
        )
        if isinstance(detail, Mapping) and detail.get("filtered_broken_or_expired"):
            broken_after_break_qualifying += 1

    # C3 reconciliation summary
    c3_retest_n = funnel["retest_pass"]
    c3_sd_available = funnel["sd_available"]
    c3_sd_pass = funnel["sd_confluence_pass"]
    c3_reject_reasons = dict(sd_rejection_reasons)

    # S1/S2 context: any lifecycle that would be S1/S2-eligible — show S/D state
    for lc in lifecycles[: max(0, min(10, limit))]:
        direction = _direction_from_bos(lc.event.bos_direction)
        rt_ev = first_retest_pass_eval(lc)
        if rt_ev is None:
            continue
        sd = evaluate_sd_at_index(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            as_of_index=rt_ev.as_of_index,
            direction=direction,
            pullback=rt_ev.pullback,
            sd_engine=sd_engine,
        )
        s1_s2_context.append(
            {
                "lifecycle_id": lc.event.lifecycle_id,
                "direction": direction,
                "note": "Context only — S1/S2 tiny-sample; not for performance conclusions",
                "s1_requires": {
                    "retest": True,
                    "htf": True,
                    "sd": False,
                    "pullback": False,
                    "impulse": False,
                },
                "s2_requires": {
                    "impulse": True,
                    "pullback": True,
                    "retest": True,
                    "htf": True,
                    "sd": False,
                },
                "sd_status": sd.get("status"),
                "sd_reason": sd.get("reason"),
            }
        )

    # Root-cause classification (evidence-based)
    root = _classify_root_cause(
        funnel=funnel,
        sd_rejection_reasons=c3_reject_reasons,
        future_zone_detected=future_zone_detected,
        availability_split=dict(availability_split),
        timeframe=timeframe,
    )

    # Determinism fingerprint
    fingerprint = {
        "lifecycle_count": len(lifecycles),
        "pullback_pass": funnel["pullback_pass"],
        "retest_pass": funnel["retest_pass"],
        "sd_confluence_pass": funnel["sd_confluence_pass"],
        "final_s3_trades": funnel["final_s3_trades"],
        "rejection_reasons": dict(sorted(c3_reject_reasons.items())),
    }

    elapsed = time.perf_counter() - t0
    window_start = candle_time(candles[start]) if start < len(candles) else None
    window_end = candle_time(candles[-1]) if candles else None

    htf_forensic: dict[str, Any] | None = None
    if htf_trace:
        htf_forensic = aggregate_htf_forensics(htf_survivor_traces)
        # Cap traces in response
        htf_forensic["htf_traces"] = list(htf_survivor_traces)[: max(0, int(limit))]

    out: dict[str, Any] = {
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "window": {
            "index_start": start,
            "candles": len(candles),
            "start": _iso(window_start),
            "end": _iso(window_end),
        },
        "funnel": funnel,
        "s3_funnel": funnel,
        "direction": direction_funnel,
        "sd_statuses": dict(sd_statuses),
        "sd_rejection_reasons": {
            k: {
                "count": v,
                "percentage": (
                    round(100.0 * v / c3_retest_n, 2) if c3_retest_n else 0.0
                ),
            }
            for k, v in sorted(c3_reject_reasons.items(), key=lambda x: -x[1])
        },
        "sd_availability_split": dict(availability_split),
        "zones": zones_counts,
        "zone_mapping": {
            "LONG": "demand confluence (existing _sd_confluence)",
            "SHORT": "supply confluence (existing _sd_confluence)",
            "mapping_correct": True,
            "note": (
                "LONG uses demand_zone close band; SHORT uses supply_zone close band. "
                "pullback.zone_hit only counts if 'demand' or 'supply' (not 'structure')."
            ),
        },
        "sd_implementation": {
            "helper": "app.research.combination_engine._sd_confluence",
            "zone_source": "detect_zones_as_of → SupplyDemandEngine.detect_zones(truncated)",
            "status_update": (
                "Research adapter does NOT call update_zone_status "
                "(production orchestrator does). Zones from detect_zones are FRESH."
            ),
            "confluence_band_pct": _SD_CLOSE_BAND * 100.0,
            "distance_threshold": (
                "No max distance/ATR gate. Nearest non-BROKEN/EXPIRED zone is "
                "selected; confluence requires close within zone ±0.5% OR "
                "pullback.zone_hit in (demand, supply)."
            ),
            "lifecycle_pullback_zones": (
                "run_lifecycle evaluates pullback WITHOUT demand/supply zones; "
                "zone_hit is typically 'structure' or None — does not satisfy "
                "_sd_confluence zone_hit branch."
            ),
            "evaluation_bar": (
                "S3 eligibility = first retest PASS (runner); S/D evaluated at "
                "that as_of_index."
            ),
        },
        "timeframes": TIMEFRAME_MAPPING,
        "lookahead": {
            "future_zone_detected": future_zone_detected,
            "broken_zone_qualified_count": broken_after_break_qualifying,
            "candles_as_of_truncation": True,
            "htf_uses_as_of_index_at_or_before": True,
            "check": "FAIL" if future_zone_detected else "PASS",
        },
        "c3_reconciliation": {
            "c3_retest_pass": c3_retest_n,
            "sd_available": c3_sd_available,
            "sd_pass": c3_sd_pass,
            "s3_eligible_after_sd": c3_sd_pass,
            "final_s3_trades": funnel["final_s3_trades"],
            "rejected_by_sd": c3_retest_n - c3_sd_pass,
            "rejection_reasons": dict(c3_reject_reasons),
            "samples": c3_rows[: min(limit, 50)],
        },
        "s1_s2_sd_context": s1_s2_context,
        "root_cause_classification": root,
        "data_quality": {
            "fabricated_candles": False,
            "candle_source": "caller_provided (postgresql expected)",
            "bos_candidates_same_bar": bos_candidates,
            "impulse_lifecycles": len(lifecycles),
            "research_max_lifecycle_bars": follow,
            "deterministic_fingerprint": fingerprint,
            "no_lookahead_window": "candles_as_of truncates to as_of_index inclusive",
        },
        "elapsed_seconds": elapsed,
        "live_engines_unchanged": True,
        "sd_logic_unchanged": True,
        "htf_logic_unchanged": True,
        "disclaimer": DISCLAIMER,
        "note": (
            "Forensic only — S3 not optimized. "
            "Sequential funnel: lifecycle → pullback PASS → retest PASS → "
            "S/D eval → HTF → entry/RR (matches runner eligibility)."
        ),
    }
    if htf_forensic is not None:
        out["htf_matrix"] = htf_forensic.get("htf_matrix")
        out["htf_rejection_reasons"] = htf_forensic.get("htf_rejection_reasons")
        out["htf_direction"] = htf_forensic.get("direction")
        out["htf_data_availability"] = htf_forensic.get("htf_data_availability")
        out["htf_lookahead"] = htf_forensic.get("lookahead")
        out["production_research_htf_invocation"] = htf_forensic.get(
            "production_research_htf_invocation"
        )
        out["htf_candle_completion"] = htf_forensic.get("htf_candle_completion")
        out["htf_root_cause"] = htf_forensic.get("htf_root_cause")
        out["htf_alignment_rule"] = htf_forensic.get("alignment_rule")
        out["htf_survivors"] = {
            "sd_survivors": htf_forensic.get("sd_survivors"),
            "htf_pass": htf_forensic.get("htf_pass"),
            "htf_fail": htf_forensic.get("htf_fail"),
        }
        out["htf_traces"] = htf_forensic.get("htf_traces")
        # Merge HTF lookahead into top-level lookahead for API shape
        hl = htf_forensic.get("lookahead") or {}
        out["lookahead"] = {
            **out["lookahead"],
            "pass": bool(hl.get("pass", True)) and out["lookahead"]["check"] == "PASS",
            "violations": list(hl.get("violations") or []),
        }
    if trace:
        out["traces"] = traces
    return out


def _build_trace(
    *,
    lc: LifecycleResult,
    candles: Sequence[Mapping[str, Any]],
    pb_ev: Any,
    rt_ev: Any,
    sd: Mapping[str, Any],
    htf_status: str | None,
    entry_status: str | None,
    rr_status: str | None,
    final: bool,
) -> dict[str, Any]:
    direction = _direction_from_bos(lc.event.bos_direction)
    return {
        "lifecycle_id": lc.event.lifecycle_id,
        "direction": direction,
        "bos_time": lc.event.bos_timestamp,
        "impulse_time": lc.event.impulse_timestamp,
        "pullback_time": (
            _iso(candle_time(candles[pb_ev.as_of_index]))
            if pb_ev is not None and 0 <= pb_ev.as_of_index < len(candles)
            else None
        ),
        "sd_time": sd.get("sd_time"),
        "retest_time": (
            _iso(candle_time(candles[rt_ev.as_of_index]))
            if rt_ev is not None and 0 <= rt_ev.as_of_index < len(candles)
            else None
        ),
        "pullback": {
            "status": pb_ev.pullback_state if pb_ev else None,
            "reason": pb_ev.pullback_reason if pb_ev else None,
            "as_of_index": pb_ev.as_of_index if pb_ev else None,
        },
        "sd": {
            "status": sd.get("status"),
            "reason": sd.get("reason"),
            "availability": sd.get("availability"),
            "zone_type": sd.get("zone_type"),
            "zone_id": sd.get("zone_id"),
            "zone_created_at": sd.get("zone_created_at"),
            "zone_status": sd.get("zone_status"),
            "distance": sd.get("distance"),
            "zone_hit_from_pullback": sd.get("zone_hit_from_pullback"),
            "confluence_band_pct": sd.get("confluence_band_pct"),
            "last_close": sd.get("last_close"),
            "demand_zone": sd.get("demand_zone"),
            "supply_zone": sd.get("supply_zone"),
        },
        "retest": {
            "status": rt_ev.retest_state if rt_ev else None,
            "reason": rt_ev.retest_reason if rt_ev else None,
            "as_of_index": rt_ev.as_of_index if rt_ev else None,
        },
        "htf": (
            htf_status
            if isinstance(htf_status, dict)
            else {"status": htf_status}
        ),
        "entry": {"status": entry_status},
        "rr": {"status": rr_status},
        "final_s3": final,
        "timing_ok": _timing_ok(lc, pb_ev, rt_ev, sd),
    }


def _timing_ok(
    lc: LifecycleResult,
    pb_ev: Any,
    rt_ev: Any,
    sd: Mapping[str, Any],
) -> dict[str, Any]:
    pb_i = pb_ev.as_of_index if pb_ev else None
    rt_i = rt_ev.as_of_index if rt_ev else None
    sd_i = sd.get("as_of_index")
    created = sd.get("zone_created_at")
    checks = {
        "bos_index_le_impulse": lc.event.bos_index <= lc.event.impulse_index,
        "impulse_lt_pullback": (
            pb_i is None or lc.event.impulse_index < pb_i
        ),
        "pullback_le_sd": pb_i is None or sd_i is None or pb_i <= sd_i,
        "sd_le_retest_or_eq": (
            # S3 evaluates SD at retest eligibility bar (same index)
            sd_i is None or rt_i is None or sd_i == rt_i or sd_i <= rt_i
        ),
        "zone_created_present": created is not None or not sd.get("pass"),
    }
    return {
        **checks,
        "all_pass": all(checks.values()),
    }


def _classify_root_cause(
    *,
    funnel: Mapping[str, int],
    sd_rejection_reasons: Mapping[str, int],
    future_zone_detected: bool,
    availability_split: Mapping[str, int],
    timeframe: str,
) -> dict[str, Any]:
    retest_n = int(funnel.get("retest_pass") or 0)
    sd_pass = int(funnel.get("sd_confluence_pass") or 0)
    final = int(funnel.get("final_s3_trades") or 0)
    unavailable = sum(
        v
        for k, v in availability_split.items()
        if "UNAVAILABLE" in str(k).upper()
    )
    available_fail = int(availability_split.get("AVAILABLE", 0)) - sd_pass
    if available_fail < 0:
        available_fail = int(sd_rejection_reasons.get("CLOSE_OUTSIDE_CONFLUENCE_BAND", 0))

    evidence: list[str] = [
        f"retest_pass={retest_n}",
        f"sd_confluence_pass={sd_pass}",
        f"final_s3_trades={final}",
        f"rejection_reasons={dict(sd_rejection_reasons)}",
        f"availability_split={dict(availability_split)}",
        f"sd_timeframe={timeframe} (setup)",
        f"future_zone_detected={future_zone_detected}",
    ]

    if retest_n == 0:
        return {
            "classification": "INSUFFICIENT_EVIDENCE",
            "evidence": evidence,
            "note": "No C3 retest PASS lifecycles in window — cannot attribute S3 wipeout to S/D.",
        }
    if future_zone_detected:
        return {
            "classification": "SD_LOOKAHEAD_BUG",
            "evidence": evidence,
        }

    issues: list[str] = []
    if unavailable == retest_n and sd_pass == 0:
        issues.append("SD_DATA_UNAVAILABLE")
    elif sd_pass == 0 and available_fail + unavailable >= retest_n:
        # All eliminated at S/D
        close_out = int(sd_rejection_reasons.get("CLOSE_OUTSIDE_CONFLUENCE_BAND", 0))
        no_zone = sum(
            int(sd_rejection_reasons.get(k, 0))
            for k in (
                "NO_DEMAND_ZONE",
                "NO_SUPPLY_ZONE",
                "NO_ZONES_DETECTED",
                "INSUFFICIENT_CANDLES",
                "NO_REQUIRED_ZONE",
                "ALL_ZONES_BROKEN_OR_EXPIRED",
            )
        )
        if close_out > 0 and no_zone > 0:
            issues.append("MULTIPLE_ISSUES")
        elif close_out >= retest_n * 0.5:
            issues.append("SD_CONFLUENCE_GENUINELY_RARE")
        elif no_zone >= retest_n * 0.5:
            issues.append("SD_DATA_UNAVAILABLE")
        else:
            issues.append("SD_CONFLUENCE_GENUINELY_RARE")

    # Research adapter note is informational; only elevate if it changes outcomes
    # (status always FRESH) — flag as secondary when confluence fails with zones present
    if sd_pass == 0 and int(sd_rejection_reasons.get("CLOSE_OUTSIDE_CONFLUENCE_BAND", 0)) > 0:
        evidence.append(
            "Research detect_zones_as_of skips update_zone_status; zones remain FRESH. "
            "This does not invent confluence — close still must be inside ±0.5% band."
        )

    if len(issues) == 0:
        if sd_pass > 0 and final == 0:
            return {
                "classification": "MULTIPLE_ISSUES",
                "evidence": evidence + ["S/D passed some; later HTF/entry/RR wiped remainder"],
            }
        if sd_pass == 0:
            return {
                "classification": "SD_CONFLUENCE_GENUINELY_RARE",
                "evidence": evidence,
            }
        return {
            "classification": "INSUFFICIENT_EVIDENCE",
            "evidence": evidence,
        }
    if len(issues) > 1 or issues[0] == "MULTIPLE_ISSUES":
        return {"classification": "MULTIPLE_ISSUES", "evidence": evidence, "components": issues}
    return {"classification": issues[0], "evidence": evidence}


def assert_no_future_zone(
    zone_created_at: datetime | None,
    eval_time: datetime | None,
) -> bool:
    """True when zone timestamp is not after evaluation time."""
    if zone_created_at is None or eval_time is None:
        return True
    return _as_utc(zone_created_at) <= _as_utc(eval_time)


def assert_broken_zone_not_selected(
    zones: Sequence[SupplyDemandZone],
) -> bool:
    """Research selection skips BROKEN/EXPIRED (existing detect_zones_as_of rule)."""
    return all(z.status.value not in ("BROKEN", "EXPIRED") for z in zones)
