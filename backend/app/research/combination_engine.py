"""Evaluate research combinations using existing signal engines only.

Does NOT modify live thresholds, live caches, or production signals.
Does NOT re-implement BOS/trend/impulse/pullback algorithms.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.config import get_settings
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.research.bos_combinations import CombinationDefinition
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.config import ResearchConfig
from copy import deepcopy

from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.risk_engine import risk_reward
from app.signals.schemas import SwingRecord
from app.signals.signal_engine import SignalEngine
from app.signals.stop_engine import compute_stop
from app.signals.target_engine import compute_targets


def _clone_signal_config(
    base: SignalConfig,
    research: ResearchConfig,
    *,
    require_htf_alignment: bool = False,
) -> SignalConfig:
    """Copy SignalConfig for research-only overrides (live config untouched).

    Combo-level ``require_htf_alignment`` is the hard research gate (enforced in
    ``evaluate_combination_at_bar``). Align ``require_mtf_alignment`` with that
    flag so research cannot silently force MTF off while the combo requires HTF.
    Combos without HTF keep the base live default (not forced False).
    """
    cfg = deepcopy(base)
    cfg.min_rr = research.min_rr
    cfg.min_rvol = research.min_rvol
    cfg.atr_multiplier = research.atr_multiplier
    if require_htf_alignment:
        cfg.require_mtf_alignment = True
    return cfg


def htf_alignment_gate(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    bos: Mapping[str, Any] | None,
    signal_config: SignalConfig,
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_4h: Sequence[Mapping[str, Any]] | None,
    setup_trend_label: str | None = None,
    htf_trend_cache: dict[tuple[str, int], str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Hard HTF gate: require classify_htf_alignment == HTF_ALIGNED.

    Fail closed when HTF series missing, unavailable, neutral/mixed, or conflict.
    Uses only closed 1h/4h bars at-or-before the setup bar timestamp (no look-ahead).
    """
    tf = (timeframe or "").lower()
    series_1h = list(candles_1h) if candles_1h is not None else None
    series_4h = list(candles_4h) if candles_4h is not None else None
    # When the setup TF itself is an HTF role, reuse setup candles if not passed.
    if series_1h is None and tf == "1h":
        series_1h = list(candles)
    if series_4h is None and tf == "4h":
        series_4h = list(candles)

    meta: dict[str, Any] = {
        "htf_alignment": None,
        "trend_4h": None,
        "trend_1h": None,
        "reason": None,
    }
    if not series_1h or not series_4h:
        meta["reason"] = "HTF candles missing — fail closed"
        meta["htf_alignment"] = "HTF_NEUTRAL_UNAVAILABLE"
        return False, meta

    htf = htf_trends_for_setup_bar(
        symbol=symbol,
        setup_candles=candles,
        as_of_index=as_of_index,
        candles_4h=series_4h,
        candles_1h=series_1h,
        config=signal_config,
        trend_15m=setup_trend_label if tf in ("15m", "5m", "1m") else None,
        trend_cache=htf_trend_cache,
    )
    align = classify_htf_alignment(
        bos_direction=(bos or {}).get("direction"),
        trend_4h=htf.get("trend_4h"),
        trend_1h=htf.get("trend_1h"),
    )
    meta["htf_alignment"] = align
    meta["trend_4h"] = htf.get("trend_4h")
    meta["trend_1h"] = htf.get("trend_1h")
    meta["idx_4h"] = htf.get("idx_4h")
    meta["idx_1h"] = htf.get("idx_1h")
    meta["as_of_ts"] = htf.get("as_of_ts")
    ok = align == HTF_ALIGNED
    if not ok:
        meta["reason"] = (
            f"HTF gate blocked: {align} "
            f"(4h={htf.get('trend_4h')}, 1h={htf.get('trend_1h')})"
        )
    else:
        meta["reason"] = "HTF_ALIGNED"
    return ok, meta


def _entry_from_existing_rules(
    *,
    bos: Mapping[str, Any] | None,
    retest: Mapping[str, Any] | None,
    last_close: float,
) -> tuple[float, str]:
    """Same entry pricing rules as entry_engine (LIMIT_RETEST or MARKET)."""
    if retest and retest.get("retest") and bos and bos.get("broken_level") is not None:
        return float(bos["broken_level"]), "LIMIT_RETEST"
    return float(last_close), "MARKET"


def _trend_agrees(trend: Mapping[str, Any] | None, bos: Mapping[str, Any] | None) -> bool:
    t = str((trend or {}).get("trend") or "")
    d = (bos or {}).get("direction")
    if d == "BULLISH_BOS" and t == "BULLISH":
        return True
    if d == "BEARISH_BOS" and t == "BEARISH":
        return True
    return False


def _bos_confirmed(bos: Mapping[str, Any] | None) -> bool:
    return bool(bos and bos.get("state") == "CONFIRMED" and bos.get("direction"))


def _pullback_confirmed(pullback: Mapping[str, Any] | None) -> bool:
    return str((pullback or {}).get("pullback_state") or "") in ("ACTIVE", "CONFIRMED")


def _impulse_confirmed(impulse: Mapping[str, Any] | None) -> bool:
    return bool(impulse and impulse.get("is_impulse"))


def _rvol_ok(
    impulse: Mapping[str, Any] | None,
    *,
    min_rvol: float,
    rvol: float | None = None,
) -> bool:
    val = rvol
    if val is None and impulse is not None:
        raw = impulse.get("rvol")
        val = float(raw) if raw is not None else None
    return val is not None and val >= min_rvol


def _sd_confluence(
    *,
    pullback: Mapping[str, Any] | None,
    direction: str | None,
    demand_zone: tuple[float, float] | None,
    supply_zone: tuple[float, float] | None,
    last_close: float | None,
) -> bool:
    zone_hit = (pullback or {}).get("zone_hit")
    if zone_hit in ("demand", "supply"):
        return True
    if direction == "LONG" and demand_zone and last_close is not None:
        lo, hi = min(demand_zone), max(demand_zone)
        return lo * 0.995 <= last_close <= hi * 1.005
    if direction == "SHORT" and supply_zone and last_close is not None:
        lo, hi = min(supply_zone), max(supply_zone)
        return lo * 0.995 <= last_close <= hi * 1.005
    return False


def _pullback_for_stop(
    pullback: Mapping[str, Any] | None,
    impulse: Mapping[str, Any] | None,
    swings: Sequence[Any],
    direction: str,
) -> dict[str, Any]:
    """Reuse engine outputs for structural stop; never invent fixed-% stops."""
    pb = dict(pullback or {})
    if pb.get("retracement_low") is not None or pb.get("retracement_high") is not None:
        return pb
    if pb.get("impulse_origin") is not None or (impulse and impulse.get("impulse_origin") is not None):
        if pb.get("impulse_origin") is None and impulse:
            pb["impulse_origin"] = impulse.get("impulse_origin")
        return pb
    # Fall back to last opposite swing already produced by swing detector
    if direction == "LONG":
        lows = [
            float(s.price if hasattr(s, "price") else s.get("price"))
            for s in swings
            if (getattr(s, "swing_type", None) or (s.get("swing_type") if isinstance(s, dict) else None))
            == "LOW"
        ]
        if lows:
            pb["impulse_origin"] = lows[-1]
    else:
        highs = [
            float(s.price if hasattr(s, "price") else s.get("price"))
            for s in swings
            if (getattr(s, "swing_type", None) or (s.get("swing_type") if isinstance(s, dict) else None))
            == "HIGH"
        ]
        if highs:
            pb["impulse_origin"] = highs[-1]
    return pb


def detect_zones_as_of(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    *,
    sd_engine: SupplyDemandEngine | None = None,
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """S/D zones using only candles through as_of_index (no future leakage)."""
    engine = sd_engine or SupplyDemandEngine(get_settings().indicators_config)
    truncated = list(candles[: as_of_index + 1])
    if len(truncated) < 30:
        return None, None
    zones = engine.detect_zones(symbol, timeframe, truncated)
    demand = None
    supply = None
    last_close = ohlc(truncated, len(truncated) - 1)[3]
    best_d = None
    best_s = None
    for z in zones:
        if z.status.value in ("BROKEN", "EXPIRED"):
            continue
        mid = (z.high + z.low) / 2.0
        dist = abs(mid - last_close)
        if z.zone_type.value == "demand":
            if best_d is None or dist < best_d[0]:
                best_d = (dist, (z.low, z.high))
        else:
            if best_s is None or dist < best_s[0]:
                best_s = (dist, (z.low, z.high))
    if best_d:
        demand = best_d[1]
    if best_s:
        supply = best_s[1]
    return demand, supply


def evaluate_combination_at_bar(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    combination: CombinationDefinition,
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    signal_engine: SignalEngine | None = None,
    demand_zone: tuple[float, float] | None = None,
    supply_zone: tuple[float, float] | None = None,
    rvol: float | None = None,
    compute_sd: bool = True,
    sd_engine: SupplyDemandEngine | None = None,
    swings: Sequence[SwingRecord] | None = None,
    atr_value: float | None = None,
    volumes: Sequence[float] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    htf_trend_cache: dict[tuple[str, int], str] | None = None,
) -> dict[str, Any]:
    """Evaluate one combination at historical bar N with as_of_index = N.

    Returns setup dict. status NO_SETUP when combination gates fail or
    existing engines cannot produce entry/stop/targets.

    When ``combination.require_htf_alignment`` is True, 1h/4h trends must be
    HTF_ALIGNED with BOS direction (missing/mixed/conflict → fail closed).
    """
    local_cfg = _clone_signal_config(
        signal_config,
        research_config,
        require_htf_alignment=bool(combination.require_htf_alignment),
    )
    # Reuse caller-provided engine when present (research backtest hot path).
    # Otherwise build a local engine so live shared instances stay untouched.
    local_engine = signal_engine if signal_engine is not None else SignalEngine(local_cfg)

    # S/D zone scan is O(bars) and dominates date-window backtests. Only run it
    # when the combination actually gates on S/D (or the caller forced zones).
    if (
        compute_sd
        and combination.require_sd
        and demand_zone is None
        and supply_zone is None
    ):
        demand_zone, supply_zone = detect_zones_as_of(
            symbol, timeframe, candles, as_of_index, sd_engine=sd_engine
        )

    swing_list = list(swings) if swings is not None and not isinstance(swings, list) else swings
    tf_analysis = local_engine.analyze_timeframe(
        symbol,
        timeframe,
        candles,
        rvol=rvol,
        demand_zone=demand_zone,
        supply_zone=supply_zone,
        as_of_index=as_of_index,
        swings=swing_list,
        atr_value=atr_value,
        volumes=volumes,
    )
    trend = tf_analysis.get("trend") or {}
    bos = tf_analysis.get("bos")
    impulse = tf_analysis.get("impulse")
    pullback = tf_analysis.get("pullback")
    retest = tf_analysis.get("retest")
    swing_objs = tf_analysis.get("_swings_objs") or []

    direction = None
    if bos and bos.get("direction") == "BULLISH_BOS":
        direction = "LONG"
    elif bos and bos.get("direction") == "BEARISH_BOS":
        direction = "SHORT"

    end = min(as_of_index, len(candles) - 1)
    last_close = ohlc(candles, end)[3] if end >= 0 else None
    sig_time = candle_time(candles[end])
    signal_time = sig_time.isoformat() if sig_time else None

    htf_meta: dict[str, Any] = {}
    if combination.require_htf_alignment:
        htf_ok, htf_meta = htf_alignment_gate(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            as_of_index=as_of_index,
            bos=bos,
            signal_config=local_cfg,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            setup_trend_label=str(trend.get("trend") or "") or None,
            htf_trend_cache=htf_trend_cache,
        )
    else:
        htf_ok = True

    gates = {
        "bos": _bos_confirmed(bos),
        "trend": _trend_agrees(trend, bos),
        "impulse": _impulse_confirmed(impulse),
        "pullback": _pullback_confirmed(pullback),
        "rvol": _rvol_ok(impulse, min_rvol=research_config.min_rvol, rvol=rvol),
        "sd": _sd_confluence(
            pullback=pullback,
            direction=direction,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            last_close=last_close,
        ),
        "htf": htf_ok,
    }

    required = []
    if combination.require_bos:
        required.append("bos")
    if combination.require_trend:
        required.append("trend")
    if combination.require_impulse:
        required.append("impulse")
    if combination.require_pullback:
        required.append("pullback")
    if combination.require_rvol:
        required.append("rvol")
    if combination.require_sd:
        required.append("sd")
    if combination.require_htf_alignment:
        required.append("htf")

    gates_pass = all(gates[k] for k in required)
    if not gates_pass or direction is None or last_close is None:
        reason = "Combination gates not satisfied"
        if combination.require_htf_alignment and not htf_ok:
            reason = htf_meta.get("reason") or reason
        return {
            "status": "NO_SETUP",
            "direction": direction,
            "gates": gates,
            "required": required,
            "combination_id": combination.combination_id,
            "signal_time": signal_time,
            "as_of_index": as_of_index,
            "reason": reason,
            "htf": htf_meta,
            "tf_analysis": {k: v for k, v in tf_analysis.items() if k != "_swings_objs"},
        }

    entry_price, entry_type = _entry_from_existing_rules(
        bos=bos, retest=retest, last_close=float(last_close)
    )
    atr = (impulse or {}).get("atr") or (bos or {}).get("atr")
    pb_stop = _pullback_for_stop(pullback, impulse, swing_objs, direction)
    stop = compute_stop(
        direction=direction,
        entry_price=float(entry_price),
        pullback=pb_stop,
        demand_zone=demand_zone,
        supply_zone=supply_zone,
        atr=float(atr) if atr else None,
        sl_buffer_atr=local_cfg.sl_buffer_atr,
    )
    if stop.get("final_stop") is None:
        return {
            "status": "NO_SETUP",
            "direction": direction,
            "gates": gates,
            "required": required,
            "combination_id": combination.combination_id,
            "signal_time": signal_time,
            "as_of_index": as_of_index,
            "reason": "Existing stop engine could not compute structural stop",
        }
    targets = compute_targets(
        direction=direction,
        entry_price=float(entry_price),
        stop=stop,
        swings=swing_objs,
        supply_zone=supply_zone,
        demand_zone=demand_zone,
        config=local_cfg,
    )
    if not targets:
        return {
            "status": "NO_SETUP",
            "direction": direction,
            "gates": gates,
            "reason": "Existing target engine produced no targets",
            "combination_id": combination.combination_id,
            "as_of_index": as_of_index,
            "signal_time": signal_time,
        }
    rr = risk_reward(
        float(entry_price),
        float(stop["final_stop"]),
        targets,
        min_rr=research_config.min_rr,
    )
    gates["rr"] = rr.get("RISK_REWARD") == "PASS"
    tp1_r = rr.get("TP1_R") or rr.get("first_target_R")
    # Always reject micro-TP1 setups (even if combo does not require R:R gate).
    # Backtests exit at TP1 first — a 0.1R TP1 vs -1R stop is structurally -EV.
    if tp1_r is None or float(tp1_r) + 1e-12 < float(research_config.min_rr):
        return {
            "status": "NO_SETUP",
            "direction": direction,
            "gates": gates,
            "required": required + ["rr"],
            "combination_id": combination.combination_id,
            "signal_time": signal_time,
            "as_of_index": as_of_index,
            "reason": (
                f"TP1_R {tp1_r} below minimum {research_config.min_rr} "
                "(first-target payoff gate)"
            ),
            "risk_reward": rr,
        }
    if combination.require_rr and not gates["rr"]:
        return {
            "status": "NO_SETUP",
            "direction": direction,
            "gates": gates,
            "required": required + ["rr"],
            "combination_id": combination.combination_id,
            "signal_time": signal_time,
            "as_of_index": as_of_index,
            "reason": f"R:R below minimum {research_config.min_rr}",
            "risk_reward": rr,
        }

    tps = [float(t["target_price"]) for t in targets if t.get("target_price") is not None]
    while len(tps) < 3:
        tps.append(None)  # type: ignore[arg-type]

    status = "LONG_ENTRY_CANDIDATE" if direction == "LONG" else "SHORT_ENTRY_CANDIDATE"
    return {
        "status": status,
        "direction": direction,
        "entry_price": float(entry_price),
        "entry_type": entry_type,
        "stop_price": float(stop["final_stop"]),
        "stop": stop,
        "targets": targets,
        "tp1": tps[0],
        "tp2": tps[1] if len(tps) > 1 else None,
        "tp3": tps[2] if len(tps) > 2 else None,
        "rr": rr.get("best_R"),
        "risk_reward": rr,
        "gates": gates,
        "required": required + (["rr"] if combination.require_rr else []),
        "combination_id": combination.combination_id,
        "signal_time": signal_time,
        "as_of_index": as_of_index,
        "htf": htf_meta,
        "condition_definition": combination.to_dict(),
        "note": "RESEARCH setup — not a live signal, not a trade command",
    }
