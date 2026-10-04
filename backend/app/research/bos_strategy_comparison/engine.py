"""Evaluate predefined BOS strategies using production signal engines only.

No second BOS/trend/swing/impulse/pullback/retest/entry/SL/TP implementation.
Research gates select which production outputs are required.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from app.config import get_settings
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.research.bos_strategy_comparison.config import StrategyResearchConfig
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_NEUTRAL_UNAVAILABLE,
    classify_htf_alignment,
    htf_trends_for_setup_bar,
)
from app.research.bos_strategy_comparison.strategies import StrategyDefinition
from app.research.combination_engine import (
    _entry_from_existing_rules,
    _impulse_confirmed,
    _pullback_confirmed,
    _pullback_for_stop,
    _sd_confluence,
    detect_zones_as_of,
)
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.entry_engine import evaluate_entry
from app.signals.mtf_engine import align_mtf
from app.signals.risk_engine import risk_reward
from app.signals.signal_engine import SignalEngine
from app.signals.stop_engine import compute_stop
from app.signals.target_engine import compute_targets


def _clone_signal_config(
    base: SignalConfig,
    research: StrategyResearchConfig,
) -> SignalConfig:
    cfg = deepcopy(base)
    cfg.min_rr = research.min_rr
    cfg.min_rvol = research.min_rvol
    cfg.atr_multiplier = research.atr_multiplier
    cfg.require_mtf_alignment = False
    return cfg


def _bos_confirmed(bos: Mapping[str, Any] | None) -> bool:
    return bool(bos and bos.get("state") == "CONFIRMED" and bos.get("direction"))


def _retest_confirmed(retest: Mapping[str, Any] | None) -> bool:
    return bool(retest and retest.get("retest"))


def _entry_engine_pass(
    *,
    mtf: Mapping[str, Any],
    setup_trend: Mapping[str, Any],
    bos: Mapping[str, Any] | None,
    impulse: Mapping[str, Any] | None,
    pullback: Mapping[str, Any] | None,
    retest: Mapping[str, Any] | None,
    stop: Mapping[str, Any] | None,
    targets: Sequence[Mapping[str, Any]],
    rr: Mapping[str, Any] | None,
    config: SignalConfig,
    last_close: float | None,
) -> tuple[bool, dict[str, Any]]:
    """Call production evaluate_entry; research-ready if stop/targets/RR valid.

    Production entry_engine hard-requires impulse+pullback. Research strategies
    that omit those gates still use production stop/target/entry pricing.
    This does NOT change the live entry_engine.
    """
    entry = evaluate_entry(
        mtf=dict(mtf),
        setup_trend=dict(setup_trend),
        bos=bos,
        impulse=impulse,
        pullback=pullback,
        retest=retest,
        stop=stop,
        targets=list(targets),
        risk_reward=rr,
        config=config,
        last_close=last_close,
    )
    status = str(entry.get("status") or "")
    if status in ("LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE", "ENTRY_CANDIDATE"):
        return True, entry

    stop_ok = bool(stop and stop.get("final_stop") is not None)
    targets_ok = bool(targets)
    tp1_r = (rr or {}).get("TP1_R") or (rr or {}).get("first_target_R")
    rr_ok = (rr or {}).get("RISK_REWARD") == "PASS" or (
        tp1_r is not None and float(tp1_r) + 1e-12 >= float(config.min_rr)
    )
    if stop_ok and targets_ok and rr_ok:
        return True, {**entry, "research_entry_ready": True, "live_status": status}
    return False, entry


def evaluate_strategy_at_bar(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    strategy: StrategyDefinition,
    signal_config: SignalConfig,
    research_config: StrategyResearchConfig,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    signal_engine: SignalEngine | None = None,
    sd_engine: SupplyDemandEngine | None = None,
    compute_sd: bool | None = None,
    trend_cache: dict[tuple[str, int], str] | None = None,
    local_cfg: SignalConfig | None = None,
    tf_analysis: Mapping[str, Any] | None = None,
    htf: Mapping[str, Any] | None = None,
    demand_zone: tuple[float, float] | None = None,
    supply_zone: tuple[float, float] | None = None,
    structure_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one strategy at historical bar N with as_of_index = N only.

    structure_override (research lifecycle): supply frozen bos/impulse and
    lifecycle pullback/retest evaluated at as_of_index. Swings/trend still
    come from production analyze_timeframe at as_of (no look-ahead).
    """
    cfg = local_cfg or _clone_signal_config(signal_config, research_config)
    local_engine = signal_engine or SignalEngine(cfg)

    # Structure first — early exit if no BOS (skip HTF/S/D cost)
    if tf_analysis is None:
        tf_analysis = local_engine.analyze_timeframe(
            symbol,
            timeframe,
            candles,
            as_of_index=as_of_index,
            serialize_swings=False,
        )
    trend = tf_analysis.get("trend") or {}
    bos = tf_analysis.get("bos")
    impulse = tf_analysis.get("impulse")
    pullback = tf_analysis.get("pullback")
    retest = tf_analysis.get("retest")
    swings = tf_analysis.get("_swings_objs") or []

    override = dict(structure_override or {})
    if override:
        if override.get("bos") is not None:
            bos = override["bos"]
        if override.get("impulse") is not None:
            impulse = override["impulse"]
        if override.get("pullback") is not None:
            pullback = override["pullback"]
        if override.get("retest") is not None:
            retest = override["retest"]

    if not _bos_confirmed(bos):
        return {
            "status": "NO_SETUP",
            "direction": None,
            "gates": {"bos": False},
            "required": ["bos"] if strategy.require_bos else [],
            "strategy_id": strategy.strategy_id,
            "as_of_index": as_of_index,
            "htf_alignment": HTF_NEUTRAL_UNAVAILABLE,
            "reason": "No confirmed BOS at as_of_index",
            "note": "RESEARCH setup — not a live signal, not a trade command",
        }

    direction = (
        "LONG"
        if bos and bos.get("direction") == "BULLISH_BOS"
        else "SHORT"
        if bos and bos.get("direction") == "BEARISH_BOS"
        else None
    )

    end = min(as_of_index, len(candles) - 1)
    last_close = ohlc(candles, end)[3] if end >= 0 else None
    sig_time = candle_time(candles[end]) if end >= 0 else None
    signal_time = sig_time.isoformat() if sig_time else None

    need_sd = strategy.require_sd if compute_sd is None else compute_sd
    if need_sd and demand_zone is None and supply_zone is None:
        demand_zone, supply_zone = detect_zones_as_of(
            symbol,
            timeframe,
            candles,
            as_of_index,
            sd_engine=sd_engine
            or SupplyDemandEngine(get_settings().indicators_config),
        )
        if not override:
            # Same-bar path: re-run pullback with zones for S/D confluence
            tf_analysis = local_engine.analyze_timeframe(
                symbol,
                timeframe,
                candles,
                demand_zone=demand_zone,
                supply_zone=supply_zone,
                as_of_index=as_of_index,
                serialize_swings=False,
            )
            trend = tf_analysis.get("trend") or {}
            bos = tf_analysis.get("bos")
            impulse = tf_analysis.get("impulse")
            pullback = tf_analysis.get("pullback")
            retest = tf_analysis.get("retest")
            swings = tf_analysis.get("_swings_objs") or []
        # Lifecycle path: keep frozen bos/impulse/pullback/retest; zones
        # only affect _sd_confluence below (as_of truncated).

    if htf is None:
        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=candles,
            as_of_index=as_of_index,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=cfg,
            trend_15m=str((trend or {}).get("trend") or "INSUFFICIENT_DATA"),
            trend_cache=trend_cache,
            include_5m=False,
        )
    htf_alignment = classify_htf_alignment(
        bos_direction=(bos or {}).get("direction"),
        trend_4h=htf.get("trend_4h"),
        trend_1h=htf.get("trend_1h"),
    )

    sd_ok = _sd_confluence(
        pullback=pullback,
        direction=direction,
        demand_zone=demand_zone,
        supply_zone=supply_zone,
        last_close=last_close,
    )
    sd_state = "INTERACT" if sd_ok else "NONE"
    if demand_zone:
        sd_state = f"{sd_state}|DEMAND"
    if supply_zone:
        sd_state = f"{sd_state}|SUPPLY"

    gates = {
        "bos": _bos_confirmed(bos),
        "retest": _retest_confirmed(retest),
        "impulse": _impulse_confirmed(impulse),
        "pullback": _pullback_confirmed(pullback),
        "sd": sd_ok,
        "htf_alignment": htf_alignment == HTF_ALIGNED,
    }

    required: list[str] = []
    if strategy.require_bos:
        required.append("bos")
    if strategy.require_retest:
        required.append("retest")
    if strategy.require_impulse:
        required.append("impulse")
    if strategy.require_pullback:
        required.append("pullback")
    if strategy.require_sd:
        required.append("sd")
    if strategy.require_htf_alignment:
        required.append("htf_alignment")

    gates_pass = all(gates[k] for k in required)
    bos_timestamp = (
        override.get("bos_timestamp")
        or (bos or {}).get("break_timestamp")
        or signal_time
    )
    base_payload = {
        "status": "NO_SETUP",
        "direction": direction,
        "gates": gates,
        "required": required,
        "strategy_id": strategy.strategy_id,
        "signal_time": signal_time,
        "as_of_index": as_of_index,
        "htf_alignment": htf_alignment,
        "trend_4h": htf.get("trend_4h"),
        "trend_1h": htf.get("trend_1h"),
        "trend_15m": htf.get("trend_15m"),
        "trend_5m": htf.get("trend_5m"),
        "bos_direction": (bos or {}).get("direction"),
        "bos_timestamp": bos_timestamp,
        "impulse_state": (impulse or {}).get("quality")
        or ("IMPULSE" if _impulse_confirmed(impulse) else "NONE"),
        "pullback_state": (pullback or {}).get("pullback_state"),
        "retest_state": "RETEST"
        if _retest_confirmed(retest)
        else str((retest or {}).get("state") or "NONE"),
        "sd_state": sd_state,
        "regime": research_config.regime_mode,
        "lifecycle_id": override.get("lifecycle_id"),
        "impulse_timestamp": override.get("impulse_timestamp"),
        "pullback_timestamp": override.get("pullback_timestamp"),
        "retest_timestamp": override.get("retest_timestamp"),
        "note": "RESEARCH setup — not a live signal, not a trade command",
    }

    if not gates_pass or direction is None or last_close is None:
        return {**base_payload, "reason": "Strategy gates not satisfied"}

    entry_price, entry_type = _entry_from_existing_rules(
        bos=bos, retest=retest, last_close=float(last_close)
    )
    atr = (impulse or {}).get("atr") or (bos or {}).get("atr")
    pb_stop = _pullback_for_stop(pullback, impulse, swings, direction)
    stop = compute_stop(
        direction=direction,
        entry_price=float(entry_price),
        pullback=pb_stop,
        demand_zone=demand_zone,
        supply_zone=supply_zone,
        atr=float(atr) if atr else None,
        sl_buffer_atr=cfg.sl_buffer_atr,
    )
    if stop.get("final_stop") is None:
        return {
            **base_payload,
            "reason": "Existing stop engine could not compute structural stop",
        }

    targets = compute_targets(
        direction=direction,
        entry_price=float(entry_price),
        stop=stop,
        swings=swings,
        supply_zone=supply_zone,
        demand_zone=demand_zone,
        config=cfg,
    )
    if not targets:
        return {
            **base_payload,
            "reason": "Existing target engine produced no targets",
        }

    rr = risk_reward(
        float(entry_price),
        float(stop["final_stop"]),
        targets,
        min_rr=research_config.min_rr,
    )
    tp1_r = rr.get("TP1_R") or rr.get("first_target_R")
    if tp1_r is None or float(tp1_r) + 1e-12 < float(research_config.min_rr):
        return {
            **base_payload,
            "gates": {**gates, "rr": False},
            "reason": f"TP1_R {tp1_r} below minimum {research_config.min_rr}",
            "risk_reward": rr,
        }

    mtf = align_mtf(
        {
            "4h": htf.get("trend_4h"),
            "1h": htf.get("trend_1h"),
            "15m": htf.get("trend_15m"),
            "5m": htf.get("trend_5m"),
        },
        setup_bos=(bos or {}).get("direction"),
        config=cfg,
    )

    entry_ok, entry_detail = _entry_engine_pass(
        mtf=mtf,
        setup_trend=trend,
        bos=bos,
        impulse=impulse,
        pullback=pullback,
        retest=retest,
        stop=stop,
        targets=targets,
        rr=rr,
        config=cfg,
        last_close=float(last_close),
    )
    gates["entry_ready"] = entry_ok
    if strategy.require_entry_ready and not entry_ok:
        return {
            **base_payload,
            "gates": gates,
            "reason": "Entry engine not ready / invalid risk structure",
            "entry_detail": entry_detail,
        }

    tps = [float(t["target_price"]) for t in targets if t.get("target_price") is not None]
    while len(tps) < 3:
        tps.append(None)  # type: ignore[arg-type]

    status = "LONG_ENTRY_CANDIDATE" if direction == "LONG" else "SHORT_ENTRY_CANDIDATE"
    risk_per_unit = abs(float(entry_price) - float(stop["final_stop"]))
    return {
        **base_payload,
        "status": status,
        "entry_price": float(entry_price),
        "entry_type": entry_type,
        "stop_price": float(stop["final_stop"]),
        "sl": float(stop["final_stop"]),
        "stop": stop,
        "stop_reason": stop.get("reason") or stop.get("stop_source") or "STRUCTURAL",
        "structural_invalidation": stop.get("invalidation")
        or stop.get("structure_level"),
        "risk_per_unit": risk_per_unit,
        "targets": targets,
        "tp1": tps[0],
        "tp2": tps[1] if len(tps) > 1 else None,
        "tp3": tps[2] if len(tps) > 2 else None,
        "rr": rr.get("best_R"),
        "risk_reward": rr,
        "gates": gates,
        "entry_detail": {
            k: entry_detail.get(k)
            for k in ("status", "entry_type", "research_entry_ready", "live_status")
            if k in entry_detail
        },
        "condition_definition": strategy.to_dict(),
    }
