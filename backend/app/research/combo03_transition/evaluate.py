"""COMBO_03_TRANSITION bar evaluator — research-only, existing risk engines."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.bos_combinations import CombinationDefinition
from app.research.combo02_v2.risk_finalize import finalize_with_existing_risk
from app.research.combo03_transition.context import Combo03Context
from app.research.combo03_transition.params import (
    ELIGIBLE_REGIMES,
    MAX_BARS_STRUCTURE_TO_15M,
    MAX_BARS_SWEEP_TO_STRUCTURE,
    PLAYBOOK_TRANSITION,
    SWEEP_LOOKBACK,
)
from app.research.combo03_transition.setup import (
    confirm_15m_mandatory,
    displacement_from_impulse,
    displacement_passes,
    opposite_boundary_invalidated,
    record_sweep_from_existing,
    regime_eligible,
    structure_shift_for_direction,
)
from app.research.combo03_transition.state_machine import (
    STATE_ENTRY_ELIGIBLE,
    STATE_IDLE,
    STATE_REJECTION_CONFIRMED,
    STATE_STRUCTURE_SHIFT_CONFIRMED,
    STATE_SWEEP_DETECTED,
    STATE_WAITING_FOR_15M_CONFIRMATION,
    SetupState,
)
from app.research.combo03_transition.variants import (
    require_15m_confirmation,
    require_displacement,
    variant_for_combination_id,
)
from app.research.combination_engine import _clone_signal_config
from app.research.config import ResearchConfig
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import detect_swings


def _no_setup(
    *,
    combination_id: str,
    as_of_index: int,
    signal_time: str | None,
    reason: str,
    regime: str | None = None,
    direction: str | None = None,
    setup: SetupState | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    diag = setup.to_diagnostics() if setup is not None else {}
    out: dict[str, Any] = {
        "status": "NO_SETUP",
        "combination_id": combination_id,
        "as_of_index": as_of_index,
        "signal_time": signal_time,
        "reason": reason,
        "direction": direction or (setup.direction if setup else None),
        "regime": regime or (setup.regime_at_setup if setup else None),
        "playbook": PLAYBOOK_TRANSITION,
        "gates": {},
        "required": [],
        "combo03_diagnostics": {
            **diag,
            "status": "WAIT" if reason.startswith("WAIT") else "REJECTED",
            "rejection_reason": reason,
            "playbook": PLAYBOOK_TRANSITION,
        },
    }
    if extra:
        out.update(dict(extra))
    return out


def evaluate_combo03_transition_at_bar(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    combination: CombinationDefinition,
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    signal_engine: SignalEngine | None = None,
    swings: Sequence[Any] | None = None,
    atr_value: float | None = None,
    volumes: Sequence[float] | None = None,
    candles_15m: Sequence[Mapping[str, Any]] | None = None,
    v2_context: Any | None = None,
    **_unused: Any,
) -> dict[str, Any]:
    """Evaluate COMBO_03_TRANSITION at historical bar N (PIT-safe).

    Does not alter COMBO_02 v1/v2. Uses existing stop/target/RR finalize path.
    """
    local_cfg = _clone_signal_config(
        signal_config, research_config, require_htf_alignment=False
    )
    local_engine = signal_engine if signal_engine is not None else SignalEngine(local_cfg)

    end = min(as_of_index, len(candles) - 1)
    last_close = ohlc(candles, end)[3] if end >= 0 else None
    sig_time = candle_time(candles[end]) if end >= 0 else None
    signal_time = sig_time.isoformat() if sig_time else None
    cid = combination.combination_id
    variant = variant_for_combination_id(cid)

    if last_close is None or end < 0:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_INSUFFICIENT_DATA",
        )

    ctx: Combo03Context
    if isinstance(v2_context, Combo03Context):
        ctx = v2_context
    else:
        ctx = Combo03Context.build(
            symbol=symbol,
            setup_timeframe=timeframe,
            setup_candles=candles,
            candles_15m=candles_15m,
        )

    regime = ctx.regime_at(end)
    setup = ctx.setup
    if regime == "UNKNOWN" and end < 50:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_INSUFFICIENT_HISTORY",
            regime=regime,
            setup=setup,
        )

    swing_list = list(swings) if swings is not None else None
    tf_analysis = local_engine.analyze_timeframe(
        symbol,
        timeframe,
        candles,
        as_of_index=as_of_index,
        swings=swing_list,
        atr_value=atr_value,
        volumes=volumes,
        serialize_swings=False,
    )
    swing_objs = tf_analysis.get("_swings_objs") or []
    impulse = tf_analysis.get("impulse")

    j15 = ctx.closed_15m_index(end)
    candles_15m_available = bool(ctx.candles_15m)

    def _load_15m() -> tuple[Any | None, Mapping[str, Any] | None]:
        snap = ctx.snapshot_15m(end)
        analysis = None
        if candles_15m_available and j15 is not None and int(j15) >= 20:
            try:
                sw15 = detect_swings(
                    ctx.candles_15m,
                    left=local_cfg.swing_for("15m").swing_left_bars,
                    right=local_cfg.swing_for("15m").swing_right_bars,
                    symbol=symbol,
                    timeframe="15m",
                    atr_period=local_cfg.atr_period,
                    minimum_swing_distance_atr=local_cfg.swing_for(
                        "15m"
                    ).minimum_swing_distance_atr,
                    as_of_index=int(j15),
                )
                analysis = local_engine.analyze_timeframe(
                    symbol,
                    "15m",
                    ctx.candles_15m,
                    as_of_index=int(j15),
                    swings=sw15,
                    serialize_swings=False,
                )
            except Exception:  # noqa: BLE001
                analysis = None
        return snap, analysis

    # --- Active setup: expiry / invalidation / advance ---
    if setup.state not in (STATE_IDLE,):
        snap_15m, analysis_15m = (None, None)
        if require_15m_confirmation(variant) and setup.state in (
            STATE_STRUCTURE_SHIFT_CONFIRMED,
            STATE_WAITING_FOR_15M_CONFIRMATION,
            STATE_REJECTION_CONFIRMED,
        ):
            snap_15m, analysis_15m = _load_15m()
        advanced = _advance_active_setup(
            setup=setup,
            candles=candles,
            end=end,
            signal_time=signal_time,
            regime=regime,
            tf_analysis=tf_analysis,
            impulse=impulse,
            analysis_15m=analysis_15m,
            snap_15m=snap_15m,
            candles_15m_available=candles_15m_available,
            j15=j15,
            variant=variant,
        )
        if advanced.get("terminal") == "EXPIRED":
            setup.reset(reason=str(advanced.get("reason") or "EXPIRED"))
            return _no_setup(
                combination_id=cid,
                as_of_index=as_of_index,
                signal_time=signal_time,
                reason=str(advanced.get("reason") or "EXPIRED"),
                regime=regime,
                setup=setup,
            )
        if advanced.get("terminal") == "INVALIDATED":
            setup.reset(reason=str(advanced.get("reason") or "INVALIDATED"))
            return _no_setup(
                combination_id=cid,
                as_of_index=as_of_index,
                signal_time=signal_time,
                reason=str(advanced.get("reason") or "INVALIDATED"),
                regime=regime,
                setup=setup,
            )
        if advanced.get("status") == "ENTRY_ELIGIBLE":
            return _finalize_entry(
                cid=cid,
                setup=setup,
                last_close=float(last_close),
                tf_analysis=tf_analysis,
                swing_objs=swing_objs,
                local_cfg=local_cfg,
                research_config=research_config,
                signal_time=signal_time,
                as_of_index=as_of_index,
                ctx=ctx,
            )
        # Still waiting — do not start a competing setup on this bar.
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason=str(advanced.get("reason") or f"WAIT_{setup.state}"),
            regime=regime,
            setup=setup,
        )

    # --- IDLE: only start a new setup in an eligible regime ---
    if not regime_eligible(regime, ELIGIBLE_REGIMES):
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_REGIME_NOT_ELIGIBLE",
            regime=regime,
            setup=setup,
        )

    sweep = record_sweep_from_existing(
        candles, end, lookback=SWEEP_LOOKBACK
    )
    if not sweep:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_NO_SWEEP",
            regime=regime,
            setup=setup,
        )

    # Existing detector encodes sweep + rejection on the same bar.
    setup.reset()
    setup.state = STATE_REJECTION_CONFIRMED
    setup.direction = str(sweep["direction"])
    setup.regime_at_setup = regime
    setup.sweep_timestamp = sweep["timestamp"]
    setup.sweep_bar_index = int(sweep["bar_index"])
    setup.sweep_direction = str(sweep["sweep_direction"])
    setup.sweep_level = float(sweep["level"])
    setup.sweep_high = sweep.get("high")
    setup.sweep_low = sweep.get("low")
    setup.sweep_close = sweep.get("close")
    setup.sweep_source = sweep.get("source")
    setup.sweep_event = sweep.get("event")
    setup.rejection_timestamp = sweep["timestamp"]
    setup.rejection_bar_index = int(sweep["bar_index"])
    setup.labels = list(sweep.get("labels") or [])
    setup.event_key = (
        f"C03:{setup.direction}:{setup.sweep_level}:{setup.sweep_bar_index}"
    )

    # Same-bar structure shift allowed (after sweep+rejection).
    shift = structure_shift_for_direction(
        candles=candles,
        as_of_index=end,
        direction=str(setup.direction),
        tf_analysis=tf_analysis,
    )
    if shift:
        _apply_structure_shift(setup, shift, signal_time, end, impulse)
        if require_displacement(variant) and not displacement_passes(impulse):
            setup.reset(reason="NO_DISPLACEMENT_ON_STRUCTURE_SHIFT")
            return _no_setup(
                combination_id=cid,
                as_of_index=as_of_index,
                signal_time=signal_time,
                reason="NO_DISPLACEMENT_ON_STRUCTURE_SHIFT",
                regime=regime,
                setup=setup,
            )
        if not require_15m_confirmation(variant):
            setup.state = STATE_ENTRY_ELIGIBLE
            setup.confirmation_15m = False
            setup.confirmation_type = "VARIANT_B_15M_NOT_REQUIRED"
            return _finalize_entry(
                cid=cid,
                setup=setup,
                last_close=float(last_close),
                tf_analysis=tf_analysis,
                swing_objs=swing_objs,
                local_cfg=local_cfg,
                research_config=research_config,
                signal_time=signal_time,
                as_of_index=as_of_index,
                ctx=ctx,
            )
        setup.state = STATE_WAITING_FOR_15M_CONFIRMATION
        snap_15m, analysis_15m = _load_15m()
        ok15, conf_type, conf_close = confirm_15m_mandatory(
            direction=str(setup.direction),
            analysis_15m=analysis_15m,
            snap_15m=snap_15m,
            candles_15m_available=candles_15m_available,
            as_of_15m_index=j15,
        )
        if ok15:
            # Confirmation must be after structure shift — same 1h bar's closed
            # 15m as-of is allowed (fully closed map); forming 15m never used.
            setup.confirmation_15m = True
            setup.confirmation_type = conf_type
            setup.confirmation_timestamp = signal_time
            setup.confirmation_candle_close = conf_close
            if "bullish_15m_confirmation" in conf_type or "bearish_15m_confirmation" in conf_type:
                label = (
                    "bullish_15m_confirmation"
                    if setup.direction == "LONG"
                    else "bearish_15m_confirmation"
                )
                if label not in setup.labels:
                    setup.labels.append(label)
            setup.state = STATE_ENTRY_ELIGIBLE
            return _finalize_entry(
                cid=cid,
                setup=setup,
                last_close=float(last_close),
                tf_analysis=tf_analysis,
                swing_objs=swing_objs,
                local_cfg=local_cfg,
                research_config=research_config,
                signal_time=signal_time,
                as_of_index=as_of_index,
                ctx=ctx,
            )
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason=conf_type,
            regime=regime,
            setup=setup,
        )

    return _no_setup(
        combination_id=cid,
        as_of_index=as_of_index,
        signal_time=signal_time,
        reason="WAIT_REJECTION_CONFIRMED_NEED_STRUCTURE_SHIFT",
        regime=regime,
        setup=setup,
    )


def _apply_structure_shift(
    setup: SetupState,
    shift: Mapping[str, Any],
    signal_time: str | None,
    end: int,
    impulse: Mapping[str, Any] | None,
) -> None:
    setup.structure_shift = str(shift["structure_shift"])
    setup.bos_or_choch = str(shift["bos_or_choch"])
    setup.structure_shift_timestamp = signal_time
    setup.structure_shift_bar_index = end
    bos = shift.get("bos")
    choch = shift.get("choch")
    setup.structure_bos = dict(bos) if isinstance(bos, Mapping) else None
    setup.structure_choch = dict(choch) if isinstance(choch, Mapping) else None
    setup.structure_impulse = dict(impulse) if isinstance(impulse, Mapping) else None
    setup.displacement = displacement_from_impulse(impulse)
    if setup.structure_shift not in setup.labels:
        setup.labels.append(setup.structure_shift)
    setup.state = STATE_STRUCTURE_SHIFT_CONFIRMED


def _advance_active_setup(
    *,
    setup: SetupState,
    candles: Sequence[Mapping[str, Any]],
    end: int,
    signal_time: str | None,
    regime: str,
    tf_analysis: Mapping[str, Any],
    impulse: Mapping[str, Any] | None,
    analysis_15m: Mapping[str, Any] | None,
    snap_15m: Any | None,
    candles_15m_available: bool,
    j15: int | None,
    variant: str,
) -> dict[str, Any]:
    # Opposite-direction sweep invalidates the active setup.
    new_sweep = record_sweep_from_existing(candles, end, lookback=SWEEP_LOOKBACK)
    if (
        new_sweep
        and setup.direction
        and str(new_sweep["direction"]) != str(setup.direction)
    ):
        return {
            "terminal": "INVALIDATED",
            "reason": "INVALIDATED_OPPOSITE_SETUP",
        }

    if setup.sweep_bar_index is not None and setup.state in (
        STATE_SWEEP_DETECTED,
        STATE_REJECTION_CONFIRMED,
    ):
        if end - int(setup.sweep_bar_index) > MAX_BARS_SWEEP_TO_STRUCTURE:
            return {
                "terminal": "EXPIRED",
                "reason": "EXPIRED_SWEEP_TO_STRUCTURE",
            }
        if end > int(setup.rejection_bar_index or setup.sweep_bar_index):
            if opposite_boundary_invalidated(
                direction=str(setup.direction),
                candles=candles,
                as_of_index=end,
                sweep_level=setup.sweep_level,
            ):
                return {
                    "terminal": "INVALIDATED",
                    "reason": "INVALIDATED_OPPOSITE_BOUNDARY",
                }

    if setup.state == STATE_REJECTION_CONFIRMED:
        shift = structure_shift_for_direction(
            candles=candles,
            as_of_index=end,
            direction=str(setup.direction),
            tf_analysis=tf_analysis,
        )
        if not shift:
            return {
                "reason": "WAIT_REJECTION_CONFIRMED_NEED_STRUCTURE_SHIFT",
            }
        _apply_structure_shift(setup, shift, signal_time, end, impulse)
        if require_displacement(variant) and not displacement_passes(impulse):
            return {
                "terminal": "INVALIDATED",
                "reason": "NO_DISPLACEMENT_ON_STRUCTURE_SHIFT",
            }
        if not require_15m_confirmation(variant):
            setup.state = STATE_ENTRY_ELIGIBLE
            setup.confirmation_15m = False
            setup.confirmation_type = "VARIANT_B_15M_NOT_REQUIRED"
            return {"status": "ENTRY_ELIGIBLE"}
        setup.state = STATE_WAITING_FOR_15M_CONFIRMATION

    if setup.state in (
        STATE_STRUCTURE_SHIFT_CONFIRMED,
        STATE_WAITING_FOR_15M_CONFIRMATION,
    ):
        if setup.structure_shift_bar_index is not None:
            if end - int(setup.structure_shift_bar_index) > MAX_BARS_STRUCTURE_TO_15M:
                return {
                    "terminal": "EXPIRED",
                    "reason": "EXPIRED_STRUCTURE_TO_15M",
                }
        if not require_15m_confirmation(variant):
            setup.state = STATE_ENTRY_ELIGIBLE
            setup.confirmation_type = "VARIANT_B_15M_NOT_REQUIRED"
            return {"status": "ENTRY_ELIGIBLE"}

        # Reject confirmation that cannot be after the 1H structure shift.
        if (
            setup.structure_shift_bar_index is not None
            and end < int(setup.structure_shift_bar_index)
        ):
            return {"reason": "WAIT_15M_BEFORE_STRUCTURE_SHIFT"}

        ok15, conf_type, conf_close = confirm_15m_mandatory(
            direction=str(setup.direction),
            analysis_15m=analysis_15m,
            snap_15m=snap_15m,
            candles_15m_available=candles_15m_available,
            as_of_15m_index=j15,
        )
        if not ok15:
            return {"reason": conf_type}
        setup.confirmation_15m = True
        setup.confirmation_type = conf_type
        setup.confirmation_timestamp = signal_time
        setup.confirmation_candle_close = conf_close
        label = (
            "bullish_15m_confirmation"
            if setup.direction == "LONG"
            else "bearish_15m_confirmation"
        )
        if label not in setup.labels:
            setup.labels.append(label)
        setup.state = STATE_ENTRY_ELIGIBLE
        return {"status": "ENTRY_ELIGIBLE"}

    if setup.state == STATE_ENTRY_ELIGIBLE:
        return {"status": "ENTRY_ELIGIBLE"}

    return {"reason": f"WAIT_{setup.state}"}


def _finalize_entry(
    *,
    cid: str,
    setup: SetupState,
    last_close: float,
    tf_analysis: Mapping[str, Any],
    swing_objs: Sequence[Any],
    local_cfg: SignalConfig,
    research_config: ResearchConfig,
    signal_time: str | None,
    as_of_index: int,
    ctx: Combo03Context,
) -> dict[str, Any]:
    event_key = setup.event_key or ""
    if event_key and event_key == ctx.last_entered_event_key:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="DUPLICATE_SETUP_ONE_ENTRY",
            regime=setup.regime_at_setup,
            setup=setup,
        )

    # Prefer structure-shift-time BOS/impulse so delayed 15M confirmation
    # does not lose the structural context needed by existing stop/entry engines.
    bos = setup.structure_bos or tf_analysis.get("bos")
    choch = setup.structure_choch or tf_analysis.get("choch")
    impulse = setup.structure_impulse or tf_analysis.get("impulse")
    playbook_meta = {
        "playbook": PLAYBOOK_TRANSITION,
        "regime": setup.regime_at_setup,
        "event": setup.sweep_event,
        "confirmation": setup.confirmation_type,
        "htf_state": "HTF_NOT_REQUIRED_COMBO03",
        "sweep": {
            "event": setup.sweep_event,
            "direction": setup.direction,
            "level": setup.sweep_level,
            "timestamp": setup.sweep_timestamp,
        },
        "structure_shift": setup.structure_shift,
        "BOS_or_CHoCH": setup.bos_or_choch,
    }
    finalized = finalize_with_existing_risk(
        direction=str(setup.direction),
        last_close=float(last_close),
        bos=bos,
        retest=tf_analysis.get("retest"),
        pullback=tf_analysis.get("pullback"),
        impulse=impulse,
        swing_objs=swing_objs,
        demand_zone=None,
        supply_zone=None,
        signal_config=local_cfg,
        research_config=research_config,
        combination_id=cid,
        signal_time=signal_time,
        as_of_index=as_of_index,
        playbook_meta=playbook_meta,
    )
    diag = setup.to_diagnostics()
    if finalized.get("status") in (
        "LONG_ENTRY_CANDIDATE",
        "SHORT_ENTRY_CANDIDATE",
    ):
        ctx.last_entered_event_key = event_key or None
        setup.state = STATE_IDLE  # consumed; one entry per setup
        setup.reset()
        finalized["playbook"] = PLAYBOOK_TRANSITION
        finalized["regime"] = playbook_meta["regime"]
        finalized["event"] = playbook_meta["event"]
        finalized["confirmation"] = playbook_meta["confirmation"]
        finalized["htf_state"] = playbook_meta["htf_state"]
        finalized["sweep"] = playbook_meta["sweep"]
        finalized["combo03_diagnostics"] = {
            **diag,
            "status": "ENTRY_CANDIDATE",
            "playbook": PLAYBOOK_TRANSITION,
        }
        finalized["note"] = (
            "COMBO_03_TRANSITION RESEARCH setup — existing risk engines applied"
        )
        # Keep choch available for snapshots when BOS absent.
        if isinstance(choch, dict):
            finalized["choch"] = choch
        return finalized

    finalized["combo03_diagnostics"] = {
        **diag,
        "status": "RISK_REJECTED",
        "rejection_reason": finalized.get("reason"),
        "playbook": PLAYBOOK_TRANSITION,
    }
    # Keep setup alive only if risk rejected transiently? Spec: one entry attempt
    # per setup — reset so we do not retry the same sweep.
    setup.reset(reason=str(finalized.get("reason") or "RISK_REJECTED"))
    return finalized
