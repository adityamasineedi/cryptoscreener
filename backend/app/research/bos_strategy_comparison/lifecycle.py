"""Research-only multi-bar BOS→Impulse→Pullback→Retest lifecycle.

Owns state persistence across candles. Calls production detect_pullback /
detect_retest unchanged. Does NOT modify production signal engines.

WAITING is preserved across bars (not converted to FAIL).
Research evaluation limits are execution bounds, not trading rules.
"""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.config import (
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    SIGNAL_ENGINE_VERSION,
    StrategyResearchConfig,
)
from app.research.bos_strategy_comparison.engine import _bos_confirmed, _clone_signal_config
from app.research.combination_engine import _impulse_confirmed, _pullback_confirmed
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig
from app.signals.pullback_engine import detect_pullback
from app.signals.retest_engine import detect_retest
from app.signals.signal_engine import SignalEngine

# Engine pullback states that end the pullback-waiting phase
_PULLBACK_ENGINE_TERMINAL = frozenset(
    {"ACTIVE", "CONFIRMED", "FAILED", "INVALIDATED"}
)
# Research invokes retest only after pullback ACTIVE/CONFIRMED (spec §9).
# Production detect_retest also accepts other non-WAITING states; we intentionally
# gate tighter here to match the BOS→impulse→pullback→retest lifecycle order.
_PULLBACK_RETEST_READY = frozenset({"ACTIVE", "CONFIRMED"})
# Pullback "pass" for research gates (matches combination_engine._pullback_confirmed)
_PULLBACK_PASS = frozenset({"ACTIVE", "CONFIRMED"})


def make_lifecycle_id(
    *,
    symbol: str,
    timeframe: str,
    bos_index: int,
    bos_direction: str,
    impulse_index: int,
) -> str:
    """Deterministic lifecycle identity — one BOS/impulse event, many evaluations."""
    return (
        f"{symbol.upper()}|{timeframe}|{int(bos_index)}|"
        f"{bos_direction}|{int(impulse_index)}"
    )


def _direction_label(bos: Mapping[str, Any] | None) -> str | None:
    d = (bos or {}).get("direction")
    if d == "BULLISH_BOS":
        return "LONG"
    if d == "BEARISH_BOS":
        return "SHORT"
    return None


def _data_fingerprint(candles: Sequence[Mapping[str, Any]], end_inclusive: int) -> str:
    end = min(end_inclusive, len(candles) - 1)
    if end < 0:
        return "empty"
    parts: list[str] = []
    # Compact fingerprint: first/last + count (avoid hashing entire series)
    for idx in (0, end // 2, end):
        c = candles[idx]
        parts.append(
            f"{idx}:{c.get('open')}:{c.get('high')}:{c.get('low')}:{c.get('close')}:{c.get('volume')}"
        )
    raw = f"{end}|{'|'.join(parts)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def candles_as_of(
    candles: Sequence[Mapping[str, Any]], as_of_index: int
) -> list[Mapping[str, Any]]:
    """Strict no-lookahead window: only candles[0 : as_of_index+1]."""
    from app.research.data_cache.stage_profiler import research_stage_profiler

    end = min(int(as_of_index), len(candles) - 1)
    if end < 0:
        return []
    with research_stage_profiler.time("candles_as_of_copy"):
        return list(candles[: end + 1])


@dataclass(frozen=True)
class FrozenImpulseEvent:
    """Immutable BOS+impulse snapshot for research lifecycle."""

    lifecycle_id: str
    symbol: str
    timeframe: str
    bos_index: int
    bos_direction: str
    bos_price: float | None
    broken_level: float | None
    bos_timestamp: str | None
    impulse_index: int
    impulse_timestamp: str | None
    frozen_bos: dict[str, Any]
    frozen_impulse: dict[str, Any]
    data_fingerprint: str
    signal_engine_version: str = SIGNAL_ENGINE_VERSION
    research_engine_version: str = RESEARCH_ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LifecycleEvaluation:
    as_of_index: int
    pullback_state: str | None
    pullback_reason: str | None
    pullback: dict[str, Any]
    retest_state: str | None
    retest_reason: str | None
    retest: dict[str, Any]
    phase: str  # PULLBACK | RETEST | TERMINAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_index": self.as_of_index,
            "pullback_state": self.pullback_state,
            "pullback_reason": self.pullback_reason,
            "retest_state": self.retest_state,
            "retest_reason": self.retest_reason,
            "retest_pass": bool(self.retest.get("retest")),
            "phase": self.phase,
            "retracement_percentage": self.pullback.get("retracement_percentage"),
            "structure_intact": self.pullback.get("structure_intact"),
        }


@dataclass
class LifecycleResult:
    event: FrozenImpulseEvent
    evaluations: list[LifecycleEvaluation] = field(default_factory=list)
    terminal_pullback_state: str | None = None
    terminal_pullback_reason: str | None = None
    terminal_retest_state: str | None = None
    terminal_retest_reason: str | None = None
    retest_pass: bool = False
    pullback_pass: bool = False
    completed: bool = False
    termination: str | None = None  # ENGINE_* or RESEARCH_EVAL_LIMIT
    pullback_eval_count: int = 0
    retest_eval_count: int = 0
    candles_evaluated: int = 0

    def to_dict(self, *, include_evaluations: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "lifecycle_id": self.event.lifecycle_id,
            "symbol": self.event.symbol,
            "timeframe": self.event.timeframe,
            "bos_index": self.event.bos_index,
            "impulse_index": self.event.impulse_index,
            "bos_direction": self.event.bos_direction,
            "bos_timestamp": self.event.bos_timestamp,
            "impulse_timestamp": self.event.impulse_timestamp,
            "broken_level": self.event.broken_level,
            "bos_price": self.event.bos_price,
            "frozen_impulse": {
                "bar_index": self.event.frozen_impulse.get("bar_index"),
                "direction": self.event.frozen_impulse.get("direction"),
                "impulse_origin": self.event.frozen_impulse.get("impulse_origin"),
                "impulse_end": self.event.frozen_impulse.get("impulse_end"),
                "atr": self.event.frozen_impulse.get("atr"),
                "atr_multiple": self.event.frozen_impulse.get("atr_multiple"),
                "rvol": self.event.frozen_impulse.get("rvol"),
                "quality": self.event.frozen_impulse.get("quality"),
                "is_impulse": self.event.frozen_impulse.get("is_impulse"),
            },
            "data_fingerprint": self.event.data_fingerprint,
            "terminal_pullback_state": self.terminal_pullback_state,
            "terminal_pullback_reason": self.terminal_pullback_reason,
            "terminal_retest_state": self.terminal_retest_state,
            "terminal_retest_reason": self.terminal_retest_reason,
            "pullback_pass": self.pullback_pass,
            "retest_pass": self.retest_pass,
            "completed": self.completed,
            "termination": self.termination,
            "pullback_eval_count": self.pullback_eval_count,
            "retest_eval_count": self.retest_eval_count,
            "candles_evaluated": self.candles_evaluated,
            "evaluation_count": len(self.evaluations),
        }
        if include_evaluations:
            out["evaluations"] = [e.to_dict() for e in self.evaluations]
        return out


def freeze_bos_impulse_event(
    *,
    symbol: str,
    timeframe: str,
    bos_index: int,
    bos: Mapping[str, Any],
    impulse: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
) -> FrozenImpulseEvent:
    """Freeze immutable BOS+impulse at confirmation bar (deep copy)."""
    bos_d = deepcopy(dict(bos))
    imp_d = deepcopy(dict(impulse))
    impulse_index = int(imp_d.get("bar_index") if imp_d.get("bar_index") is not None else bos_index)
    direction = str(bos_d.get("direction") or "")
    lid = make_lifecycle_id(
        symbol=symbol,
        timeframe=timeframe,
        bos_index=bos_index,
        bos_direction=direction,
        impulse_index=impulse_index,
    )
    bos_ts = bos_d.get("break_timestamp")
    if not bos_ts:
        t = candle_time(candles[bos_index]) if 0 <= bos_index < len(candles) else None
        bos_ts = t.isoformat() if t else None
    imp_t = (
        candle_time(candles[impulse_index])
        if 0 <= impulse_index < len(candles)
        else None
    )
    return FrozenImpulseEvent(
        lifecycle_id=lid,
        symbol=symbol.upper(),
        timeframe=timeframe,
        bos_index=int(bos_index),
        bos_direction=direction,
        bos_price=(
            float(bos_d["break_price"])
            if bos_d.get("break_price") is not None
            else None
        ),
        broken_level=(
            float(bos_d["broken_level"])
            if bos_d.get("broken_level") is not None
            else None
        ),
        bos_timestamp=str(bos_ts) if bos_ts is not None else None,
        impulse_index=impulse_index,
        impulse_timestamp=imp_t.isoformat() if imp_t else None,
        frozen_bos=bos_d,
        frozen_impulse=imp_d,
        data_fingerprint=_data_fingerprint(candles, bos_index),
    )


def evaluate_pullback_at(
    *,
    candles: Sequence[Mapping[str, Any]],
    event: FrozenImpulseEvent,
    as_of_index: int,
    signal_config: SignalConfig,
    demand_zone: tuple[float, float] | None = None,
    supply_zone: tuple[float, float] | None = None,
    vwap: float | None = None,
    ema: float | None = None,
) -> dict[str, Any]:
    """Call production detect_pullback with frozen impulse; no future candles."""
    from app.research.data_cache.stage_profiler import research_stage_profiler

    window = candles_as_of(candles, as_of_index)
    # as_of within truncated window is last index
    with research_stage_profiler.time("detect_pullback"):
        return detect_pullback(
            window,
            event.frozen_bos,
            event.frozen_impulse,
            signal_config,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
            vwap=vwap,
            ema=ema,
            as_of_index=len(window) - 1,
        )


def evaluate_retest_at(
    *,
    candles: Sequence[Mapping[str, Any]],
    event: FrozenImpulseEvent,
    pullback: Mapping[str, Any],
    as_of_index: int,
    signal_config: SignalConfig,
) -> dict[str, Any]:
    """Call production detect_retest with frozen BOS + current pullback output."""
    from app.research.data_cache.stage_profiler import research_stage_profiler

    window = candles_as_of(candles, as_of_index)
    direction = _direction_label(event.frozen_bos)
    with research_stage_profiler.time("detect_retest"):
        return detect_retest(
            window,
            event.frozen_bos,
            dict(pullback),
            signal_config,
            direction=direction,
            as_of_index=len(window) - 1,
        )


def run_lifecycle(
    *,
    candles: Sequence[Mapping[str, Any]],
    event: FrozenImpulseEvent,
    signal_config: SignalConfig,
    max_follow_bars: int = 40,
    demand_zone: tuple[float, float] | None = None,
    supply_zone: tuple[float, float] | None = None,
    record_waiting: bool = True,
) -> LifecycleResult:
    """Advance as_of from impulse_index+1 until engine terminal or research limit.

    max_follow_bars is a RESEARCH EVALUATION LIMIT (not a trading rule).
    Production engines have no pullback expiry — only WAITING/ACTIVE/CONFIRMED/FAILED/INVALIDATED.
    """
    result = LifecycleResult(event=event)
    n = len(candles)
    impulse_i = event.impulse_index
    start = impulse_i + 1
    if start >= n:
        result.completed = True
        result.termination = "RESEARCH_EVAL_LIMIT"
        result.terminal_pullback_state = "WAITING"
        result.terminal_pullback_reason = "No bars after impulse in loaded series"
        return result

    end = min(n - 1, impulse_i + max(1, int(max_follow_bars)))
    pullback_terminal_reached = False
    latest_pb: dict[str, Any] = {}
    latest_rt: dict[str, Any] = {}

    for j in range(start, end + 1):
        result.candles_evaluated += 1
        pb = evaluate_pullback_at(
            candles=candles,
            event=event,
            as_of_index=j,
            signal_config=signal_config,
            demand_zone=demand_zone,
            supply_zone=supply_zone,
        )
        result.pullback_eval_count += 1
        pb_state = str(pb.get("pullback_state") or "")
        latest_pb = pb

        rt: dict[str, Any] = {
            "retest": False,
            "state": "WAITING",
            "reason": "Waiting for pullback",
        }
        phase = "PULLBACK"
        if pb_state in _PULLBACK_RETEST_READY:
            rt = evaluate_retest_at(
                candles=candles,
                event=event,
                pullback=pb,
                as_of_index=j,
                signal_config=signal_config,
            )
            result.retest_eval_count += 1
            latest_rt = rt
            phase = "RETEST" if pb_state in _PULLBACK_PASS else "PULLBACK_TERMINAL"

        if pb_state == "WAITING" and not record_waiting and not pullback_terminal_reached:
            # Still track first/last waiting sparsely — record every eval for traces
            pass

        ev = LifecycleEvaluation(
            as_of_index=j,
            pullback_state=pb_state or None,
            pullback_reason=pb.get("reason"),
            pullback=pb,
            retest_state=str(rt.get("state") or None),
            retest_reason=rt.get("reason"),
            retest=rt,
            phase=phase,
        )
        result.evaluations.append(ev)

        if pb_state in _PULLBACK_PASS:
            result.pullback_pass = True

        if pb_state in _PULLBACK_ENGINE_TERMINAL and not pullback_terminal_reached:
            pullback_terminal_reached = True
            result.terminal_pullback_state = pb_state
            result.terminal_pullback_reason = pb.get("reason")
            if pb_state in ("FAILED", "INVALIDATED"):
                result.completed = True
                result.termination = f"ENGINE_PULLBACK_{pb_state}"
                result.terminal_retest_state = str(rt.get("state") or "WAITING")
                result.terminal_retest_reason = rt.get("reason")
                result.retest_pass = bool(rt.get("retest"))
                break

        # After pullback pass: continue until retest confirmed or research limit
        if result.pullback_pass and bool(rt.get("retest")):
            result.retest_pass = True
            result.terminal_retest_state = str(rt.get("state") or "CONFIRMED")
            result.terminal_retest_reason = rt.get("reason")
            if result.terminal_pullback_state is None:
                result.terminal_pullback_state = pb_state
                result.terminal_pullback_reason = pb.get("reason")
            result.completed = True
            result.termination = "ENGINE_RETEST_CONFIRMED"
            break

        # If pullback already terminal ACTIVE/CONFIRMED but retest not yet — keep going
        # until end of research window.

    else:
        # Loop exhausted
        if latest_pb:
            result.terminal_pullback_state = str(
                latest_pb.get("pullback_state") or result.terminal_pullback_state
            )
            result.terminal_pullback_reason = latest_pb.get("reason")
            result.pullback_pass = result.pullback_pass or _pullback_confirmed(latest_pb)
        if latest_rt:
            result.terminal_retest_state = str(latest_rt.get("state") or "WAITING")
            result.terminal_retest_reason = latest_rt.get("reason")
            result.retest_pass = bool(latest_rt.get("retest"))
        result.completed = True
        if result.pullback_pass and result.retest_pass:
            result.termination = "ENGINE_RETEST_CONFIRMED"
        elif result.pullback_pass:
            result.termination = "RESEARCH_EVAL_LIMIT_AFTER_PULLBACK"
        elif result.terminal_pullback_state in _PULLBACK_ENGINE_TERMINAL:
            result.termination = f"ENGINE_PULLBACK_{result.terminal_pullback_state}"
        else:
            result.termination = "RESEARCH_EVAL_LIMIT"

    # Ensure terminal pullback populated
    if result.terminal_pullback_state is None and result.evaluations:
        last = result.evaluations[-1]
        result.terminal_pullback_state = last.pullback_state
        result.terminal_pullback_reason = last.pullback_reason
    if result.terminal_retest_state is None and result.evaluations:
        last = result.evaluations[-1]
        result.terminal_retest_state = last.retest_state
        result.terminal_retest_reason = last.retest_reason

    return result


def first_pullback_pass_eval(
    result: LifecycleResult,
) -> LifecycleEvaluation | None:
    """First evaluation where production pullback is ACTIVE/CONFIRMED."""
    for ev in result.evaluations:
        if ev.pullback_state in _PULLBACK_PASS:
            return ev
    return None


def first_retest_pass_eval(
    result: LifecycleResult,
) -> LifecycleEvaluation | None:
    """First evaluation where production retest is confirmed (after pullback pass)."""
    for ev in result.evaluations:
        if ev.pullback_state in _PULLBACK_PASS and bool(ev.retest.get("retest")):
            return ev
    return None


def strategy_needs_lifecycle(strategy: Any) -> bool:
    """True when strategy gates require pullback and/or retest (multi-bar path)."""
    return bool(
        getattr(strategy, "require_pullback", False)
        or getattr(strategy, "require_retest", False)
    )


def eligibility_index_for_strategy(
    result: LifecycleResult,
    *,
    require_pullback: bool,
    require_retest: bool,
) -> int | None:
    """Bar index when strategy gates first become reachable via lifecycle.

    Retest-required strategies become eligible at first retest PASS.
    Pullback-only (no retest) become eligible at first ACTIVE/CONFIRMED.
    """
    if require_retest:
        ev = first_retest_pass_eval(result)
        return ev.as_of_index if ev is not None else None
    if require_pullback:
        ev = first_pullback_pass_eval(result)
        return ev.as_of_index if ev is not None else None
    return None


def eval_at_index(
    result: LifecycleResult, as_of_index: int
) -> LifecycleEvaluation | None:
    for ev in result.evaluations:
        if ev.as_of_index == as_of_index:
            return ev
    return None


def discover_impulse_events(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    index_start: int = 0,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
) -> tuple[list[FrozenImpulseEvent], int]:
    """Scan bars once; emit unique BOS+impulse events and total BOS count."""
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    engine = SignalEngine(local_cfg)
    start = max(0, int(index_start))
    seen: set[str] = set()
    events: list[FrozenImpulseEvent] = []
    bos_candidates = 0

    from app.research.data_cache.stage_profiler import research_stage_profiler

    for i in range(start, len(candles)):
        with research_stage_profiler.time("lifecycle_discover_analyze_timeframe"):
            # Research-only: skip SwingRecord API serialization (use _swings_objs).
            tf = engine.analyze_timeframe(
                symbol,
                timeframe,
                candles,
                as_of_index=i,
                serialize_swings=False,
            )
        bos = tf.get("bos")
        impulse = tf.get("impulse")
        if not _bos_confirmed(bos if isinstance(bos, Mapping) else None):
            continue
        bos_candidates += 1
        if not _impulse_confirmed(impulse if isinstance(impulse, Mapping) else None):
            continue
        assert isinstance(bos, Mapping) and isinstance(impulse, Mapping)
        ev = freeze_bos_impulse_event(
            symbol=symbol,
            timeframe=timeframe,
            bos_index=i,
            bos=bos,
            impulse=impulse,
            candles=candles,
        )
        if ev.lifecycle_id in seen:
            continue
        seen.add(ev.lifecycle_id)
        events.append(ev)
    return events, bos_candidates


def run_lifecycles_for_series(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    index_start: int = 0,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
    max_follow_bars: int | None = None,
    max_trace_lifecycles: int = 20,
) -> dict[str, Any]:
    """Discover BOS+impulse events and run multi-bar lifecycle for each (once)."""
    t0 = time.perf_counter()
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    follow = (
        int(max_follow_bars)
        if max_follow_bars is not None
        else int(getattr(rcfg, "research_max_lifecycle_bars", 40) or 40)
    )

    events, bos_candidates = discover_impulse_events(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        index_start=index_start,
        signal_config=scfg,
        research_config=rcfg,
    )

    pullback_status_counter: dict[str, int] = {}
    retest_status_counter: dict[str, int] = {}
    termination_counter: dict[str, int] = {}
    results: list[LifecycleResult] = []
    pullback_evals = 0
    retest_evals = 0
    candles_eval = 0
    lifecycles_completed = 0
    terminal_pullbacks = 0
    terminal_retests = 0
    pullback_pass_n = 0
    retest_pass_n = 0

    for ev in events:
        lc = run_lifecycle(
            candles=candles,
            event=ev,
            signal_config=local_cfg,
            max_follow_bars=follow,
        )
        results.append(lc)
        pullback_evals += lc.pullback_eval_count
        retest_evals += lc.retest_eval_count
        candles_eval += lc.candles_evaluated
        if lc.completed:
            lifecycles_completed += 1
        pb_term = lc.terminal_pullback_state or "WAITING"
        pullback_status_counter[pb_term] = pullback_status_counter.get(pb_term, 0) + 1
        if pb_term in _PULLBACK_ENGINE_TERMINAL:
            terminal_pullbacks += 1
        if lc.pullback_pass:
            pullback_pass_n += 1
        rt_term = lc.terminal_retest_state or "WAITING"
        retest_status_counter[rt_term] = retest_status_counter.get(rt_term, 0) + 1
        if lc.retest_pass:
            retest_pass_n += 1
            terminal_retests += 1
        term = lc.termination or "UNKNOWN"
        termination_counter[term] = termination_counter.get(term, 0) + 1

    # Verify frozen impulse immutability sample
    freeze_ok = True
    for lc in results[:5]:
        # Re-run should not mutate frozen dicts
        before = json.dumps(lc.event.frozen_impulse, sort_keys=True, default=str)
        _ = run_lifecycle(
            candles=candles,
            event=lc.event,
            signal_config=local_cfg,
            max_follow_bars=min(5, follow),
            record_waiting=True,
        )
        after = json.dumps(lc.event.frozen_impulse, sort_keys=True, default=str)
        if before != after:
            freeze_ok = False
            break

    elapsed = time.perf_counter() - t0
    traces = [
        r.to_dict(include_evaluations=True)
        for r in results[: max(0, int(max_trace_lifecycles))]
    ]

    return {
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "lifecycle_mode": True,
        "bos_candidates": bos_candidates,
        "impulse_candidates": len(events),
        "lifecycles_started": len(events),
        "lifecycles_completed": lifecycles_completed,
        "pullback_statuses": pullback_status_counter,
        "retest_statuses": retest_status_counter,
        "terminations": termination_counter,
        "terminal_pullbacks": terminal_pullbacks,
        "terminal_retests": terminal_retests,
        "pullback_pass": pullback_pass_n,
        "retest_pass": retest_pass_n,
        "pullback_ACTIVE": pullback_status_counter.get("ACTIVE", 0),
        "pullback_CONFIRMED": pullback_status_counter.get("CONFIRMED", 0),
        "pullback_FAILED": pullback_status_counter.get("FAILED", 0),
        "pullback_INVALIDATED": pullback_status_counter.get("INVALIDATED", 0),
        "pullback_WAITING": pullback_status_counter.get("WAITING", 0),
        "performance": {
            "elapsed_seconds": elapsed,
            "db_queries": 0,  # series provided by caller
            "candles_in_series": len(candles),
            "lifecycle_count": len(events),
            "candles_evaluated_across_lifecycles": candles_eval,
            "pullback_evaluations": pullback_evals,
            "retest_evaluations": retest_evals,
            "research_max_lifecycle_bars": follow,
            "note": (
                "research_max_lifecycle_bars is an execution bound, not a trading rule. "
                "Production pullback engine has no expiry."
            ),
        },
        "data_quality": {
            "fabricated_candles": False,
            "frozen_impulse_immutable": freeze_ok,
            "no_lookahead_window": "candles_as_of truncates to as_of_index inclusive",
            "waiting_not_converted_to_fail": True,
            "one_lifecycle_per_bos_impulse": True,
        },
        "lifecycle_traces": traces,
        "live_engines_unchanged": True,
        "pullback_logic_unchanged": True,
        "retest_logic_unchanged": True,
        "disclaimer": DISCLAIMER,
        "note": (
            "Research lifecycle freezes BOS+impulse and re-calls production "
            "detect_pullback/detect_retest on subsequent candles. "
            "WAITING evaluations are not separate opportunities."
        ),
    }
