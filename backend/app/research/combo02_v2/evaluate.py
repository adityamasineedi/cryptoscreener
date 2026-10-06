"""COMBO_02 v2 bar evaluator — regime router + playbooks + existing risk."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.bos_combinations import CombinationDefinition
from app.research.combo02_v2.context import Combo02V2Context
from app.research.combo02_v2.htf_policy import fetch_htf_trends
from app.research.combo02_v2.playbooks import (
    evaluate_range_playbook,
    evaluate_reversal_playbook,
    evaluate_trend_playbook,
)
from app.research.combo02_v2.research_variants import (
    VARIANT_A,
    VARIANT_B,
    choppy_range_15m_required_ok,
    route_playbook_variant,
    variant_for_combination_id,
)
from app.research.combo02_v2.risk_finalize import finalize_with_existing_risk
from app.research.combo02_v2.router import (
    PLAYBOOK_RANGE,
    PLAYBOOK_REVERSAL,
    PLAYBOOK_TREND,
    PLAYBOOK_WAIT,
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
    playbook: str | None = None,
    direction: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "NO_SETUP",
        "combination_id": combination_id,
        "as_of_index": as_of_index,
        "signal_time": signal_time,
        "reason": reason,
        "direction": direction,
        "regime": regime,
        "playbook": playbook or PLAYBOOK_WAIT,
        "gates": {},
        "required": [],
        "v2_diagnostics": {
            "regime": regime,
            "playbook": playbook or PLAYBOOK_WAIT,
            "direction": direction,
            "status": "WAIT" if reason.startswith("WAIT") or playbook == PLAYBOOK_WAIT else "REJECTED",
            "rejection_reason": reason,
        },
    }
    if extra:
        out.update(dict(extra))
        diag = dict(out["v2_diagnostics"])
        for k in ("event", "confirmation", "htf_state", "sweep"):
            if k in extra:
                diag[k] = extra[k]
        out["v2_diagnostics"] = diag
    return out


def evaluate_combo02_v2_at_bar(
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
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_15m: Sequence[Mapping[str, Any]] | None = None,
    htf_trend_cache: dict[tuple[str, int], str] | None = None,
    htf_idx_1h_map: Sequence[int | None] | None = None,
    htf_idx_4h_map: Sequence[int | None] | None = None,
    v2_context: Combo02V2Context | None = None,
    research_variant: str | None = None,
) -> dict[str, Any]:
    """Evaluate COMBO_02 v2 adaptive playbooks at historical bar N (PIT-safe).

    ``research_variant`` is None for frozen COMBO_02_V2. V2.1-A/B only alter
    CHOPPY range routing/15M policy; trend/reversal/risk paths stay identical.
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
    if research_variant is None:
        research_variant = variant_for_combination_id(cid)

    if last_close is None or end < 0:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_INSUFFICIENT_DATA",
        )

    ctx = v2_context
    if ctx is None:
        ctx = Combo02V2Context.build(
            symbol=symbol,
            setup_timeframe=timeframe,
            setup_candles=candles,
            candles_15m=candles_15m,
        )

    try:
        snap_1h = ctx.snapshot_1h(end)
    except AssertionError:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_PIT_VIOLATION",
        )

    if str(snap_1h.data_quality_state or "").startswith("UNKNOWN"):
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="WAIT_INSUFFICIENT_HISTORY",
            regime=snap_1h.market_regime,
        )

    regime = str(snap_1h.market_regime or "UNKNOWN")
    playbook = route_playbook_variant(regime, variant=research_variant)
    if playbook == PLAYBOOK_WAIT:
        wait_reason = (
            "WAIT_CHOPPY_RANGE_DISABLED_V21A"
            if research_variant == VARIANT_A and regime == "CHOPPY"
            else "WAIT_UNKNOWN_REGIME"
        )
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason=wait_reason,
            regime=regime,
            playbook=PLAYBOOK_WAIT,
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

    # Optional 15m live analysis at last closed 15m bar (PIT via map).
    analysis_15m = None
    snap_15m = ctx.snapshot_15m(end)
    if ctx.candles_15m and ctx.idx_15m_map and end < len(ctx.idx_15m_map):
        j = ctx.idx_15m_map[end]
        if j is not None and int(j) >= 20:
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
                    as_of_index=int(j),
                )
                analysis_15m = local_engine.analyze_timeframe(
                    symbol,
                    "15m",
                    ctx.candles_15m,
                    as_of_index=int(j),
                    swings=sw15,
                    serialize_swings=False,
                )
            except Exception:  # noqa: BLE001
                analysis_15m = None

    htf = fetch_htf_trends(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        as_of_index=as_of_index,
        signal_config=local_cfg,
        candles_1h=candles_1h,
        candles_4h=candles_4h,
        htf_trend_cache=htf_trend_cache,
        htf_idx_1h_map=htf_idx_1h_map,
        htf_idx_4h_map=htf_idx_4h_map,
    )

    setup: dict[str, Any] | None = None
    if playbook == PLAYBOOK_TREND:
        setup = evaluate_trend_playbook(
            candles=candles,
            as_of_index=end,
            tf_analysis=tf_analysis,
            htf=htf,
            snap_15m=snap_15m,
            analysis_15m=analysis_15m,
            market_regime=regime,
        )
    elif playbook == PLAYBOOK_RANGE:
        setup = evaluate_range_playbook(
            candles=candles,
            as_of_index=end,
            snap_15m=snap_15m,
            analysis_15m=analysis_15m,
            market_regime=regime,
        )
        # V2.1-B research only: CHOPPY range requires real 15M structure confirm.
        if (
            research_variant == VARIANT_B
            and regime == "CHOPPY"
            and setup
            and setup.get("status") == "SETUP"
        ):
            ok15, conf15 = choppy_range_15m_required_ok(
                direction=str(setup.get("direction") or ""),
                snap_15m=snap_15m,
                analysis_15m=analysis_15m,
                confirmation=str(setup.get("confirmation") or ""),
            )
            if not ok15:
                setup = {
                    "status": "NO_SETUP",
                    "playbook": PLAYBOOK_RANGE,
                    "direction": setup.get("direction"),
                    "reason": conf15,
                    "regime": regime,
                    "event": setup.get("event"),
                    "confirmation": conf15,
                }
            else:
                setup = dict(setup)
                setup["confirmation"] = conf15
    elif playbook == PLAYBOOK_REVERSAL:
        setup = evaluate_reversal_playbook(
            candles=candles,
            as_of_index=end,
            tf_analysis=tf_analysis,
            snap_15m=snap_15m,
            analysis_15m=analysis_15m,
            market_regime=regime,
        )

    if not setup or setup.get("status") != "SETUP":
        setup = setup or {
            "reason": "NO_SETUP",
            "playbook": playbook,
            "regime": regime,
        }
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason=str(setup.get("reason") or "NO_SETUP"),
            regime=regime,
            playbook=str(setup.get("playbook") or playbook),
            direction=setup.get("direction"),
            extra={
                "event": setup.get("event"),
                "confirmation": setup.get("confirmation"),
                "htf_state": setup.get("htf_state") or htf.get("reason"),
                "htf": htf,
                "sweep": setup.get("sweep"),
            },
        )

    event_key = str(setup.get("event_key") or "")
    if event_key and event_key == ctx.last_event_key:
        return _no_setup(
            combination_id=cid,
            as_of_index=as_of_index,
            signal_time=signal_time,
            reason="DUPLICATE_EVENT_STICKY",
            regime=regime,
            playbook=playbook,
            direction=setup.get("direction"),
            extra={"event": setup.get("event")},
        )

    finalized = finalize_with_existing_risk(
        direction=str(setup["direction"]),
        last_close=float(last_close),
        bos=setup.get("bos") or tf_analysis.get("bos"),
        retest=tf_analysis.get("retest"),
        pullback=tf_analysis.get("pullback"),
        impulse=tf_analysis.get("impulse"),
        swing_objs=swing_objs,
        demand_zone=None,
        supply_zone=None,
        signal_config=local_cfg,
        research_config=research_config,
        combination_id=cid,
        signal_time=signal_time,
        as_of_index=as_of_index,
        playbook_meta=setup,
    )
    if finalized.get("status") in (
        "LONG_ENTRY_CANDIDATE",
        "SHORT_ENTRY_CANDIDATE",
    ):
        ctx.last_event_key = event_key or None
        finalized["htf"] = htf
        finalized["condition_definition"] = combination.to_dict()
    else:
        finalized["htf"] = htf
        finalized["v2_diagnostics"] = {
            "regime": regime,
            "playbook": playbook,
            "direction": setup.get("direction"),
            "event": setup.get("event"),
            "confirmation": setup.get("confirmation"),
            "htf_state": setup.get("htf_state"),
            "status": "RISK_REJECTED",
            "rejection_reason": finalized.get("reason"),
        }
    return finalized
