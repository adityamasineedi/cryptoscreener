"""Pass COMBO_02 v2 setups through existing stop/target/RR engines (unchanged)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.combination_engine import (
    _entry_from_existing_rules,
    _pullback_for_stop,
)
from app.research.config import ResearchConfig
from app.signals.config import SignalConfig
from app.signals.risk_engine import risk_reward
from app.signals.stop_engine import compute_stop
from app.signals.target_engine import compute_targets


def finalize_with_existing_risk(
    *,
    direction: str,
    last_close: float,
    bos: Mapping[str, Any] | None,
    retest: Mapping[str, Any] | None,
    pullback: Mapping[str, Any] | None,
    impulse: Mapping[str, Any] | None,
    swing_objs: Sequence[Any],
    demand_zone: tuple[float, float] | None,
    supply_zone: tuple[float, float] | None,
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    combination_id: str,
    signal_time: str | None,
    as_of_index: int,
    playbook_meta: Mapping[str, Any],
) -> dict[str, Any]:
    """Identical validation sequence as evaluate_combination_at_bar post-gates."""
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
        sl_buffer_atr=signal_config.sl_buffer_atr,
    )
    base = {
        "direction": direction,
        "combination_id": combination_id,
        "signal_time": signal_time,
        "as_of_index": as_of_index,
        "playbook": playbook_meta.get("playbook"),
        "regime": playbook_meta.get("regime"),
        "event": playbook_meta.get("event"),
        "confirmation": playbook_meta.get("confirmation"),
        "htf_state": playbook_meta.get("htf_state"),
        "sweep": playbook_meta.get("sweep"),
    }
    if stop.get("final_stop") is None:
        return {
            **base,
            "status": "NO_SETUP",
            "reason": "Existing stop engine could not compute structural stop",
        }
    try:
        final_stop = float(stop["final_stop"])
        if direction == "LONG" and final_stop >= float(entry_price):
            return {**base, "status": "NO_SETUP", "reason": "STOP_INVALID:stop_ge_entry"}
        if direction == "SHORT" and final_stop <= float(entry_price):
            return {**base, "status": "NO_SETUP", "reason": "STOP_INVALID:stop_le_entry"}
    except (TypeError, ValueError):
        return {**base, "status": "NO_SETUP", "reason": "STOP_INVALID:non_numeric"}

    targets = compute_targets(
        direction=direction,
        entry_price=float(entry_price),
        stop=stop,
        swings=swing_objs,
        supply_zone=supply_zone,
        demand_zone=demand_zone,
        config=signal_config,
    )
    if not targets:
        return {
            **base,
            "status": "NO_SETUP",
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
            **base,
            "status": "NO_SETUP",
            "reason": (
                f"TP1_R {tp1_r} below minimum {research_config.min_rr} "
                "(first-target payoff gate)"
            ),
            "risk_reward": rr,
        }
    tps = [float(t["target_price"]) for t in targets if t.get("target_price") is not None]
    while len(tps) < 3:
        tps.append(None)  # type: ignore[arg-type]
    status = "LONG_ENTRY_CANDIDATE" if direction == "LONG" else "SHORT_ENTRY_CANDIDATE"
    return {
        **base,
        "status": status,
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
        "gates": {
            "playbook": True,
            "rr": rr.get("RISK_REWARD") == "PASS",
        },
        "required": ["playbook"],
        "note": "COMBO_02_V2 RESEARCH setup — existing risk engines applied",
        "v2_diagnostics": {
            "regime": playbook_meta.get("regime"),
            "playbook": playbook_meta.get("playbook"),
            "direction": direction,
            "event": playbook_meta.get("event"),
            "confirmation": playbook_meta.get("confirmation"),
            "htf_state": playbook_meta.get("htf_state"),
            "status": "ENTRY_CANDIDATE",
        },
    }
