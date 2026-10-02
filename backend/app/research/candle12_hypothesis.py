"""Research-only Candle-1 BOS → Candle-2 break hypothesis.

Does NOT modify the live signal engine, thresholds, or production config.
Uses existing BOS / impulse / stop / target / risk engines with as_of_index.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import AMBIGUOUS_CONSERVATIVE, ResearchConfig
from app.research.data_quality import verify_ohlcv
from app.research.metrics import compute_metrics
from app.research.schemas import ResearchTrade
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.risk_engine import risk_reward
from app.signals.signal_engine import SignalEngine
from app.signals.stop_engine import compute_stop
from app.signals.target_engine import compute_targets
from app.signals.schemas import SignalStatus


HYPOTHESIS_A = "CANDLE_1_BOS_CANDLE_2_BREAK"
HYPOTHESIS_B = "EXISTING_PULLBACK_RETEST_ENTRY"


@dataclass
class Candle12Example:
    symbol: str
    timeframe: str
    candle1_index: int
    candle1_timestamp: str | None
    candle1_ohlc: dict[str, float]
    bos_level: float | None
    candle2_index: int
    candle2_timestamp: str | None
    candle2_ohlc: dict[str, float]
    entry: float
    stop: float
    tp1: float | None
    tp2: float | None
    tp3: float | None
    rr: float | None
    direction: str
    outcome: str | None
    r_multiple: float | None
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ohlc_dict(candles: Sequence[Mapping[str, Any]], idx: int) -> dict[str, float]:
    o, h, l, c = ohlc(candles, idx)
    return {"open": o, "high": h, "low": l, "close": c}


def _ts(candles: Sequence[Mapping[str, Any]], idx: int) -> str | None:
    t = candle_time(candles[idx])
    return t.isoformat() if t else None


def _closes_in_bos_direction(direction: str, o: float, c: float) -> bool:
    if direction == "LONG":
        return c > o
    if direction == "SHORT":
        return c < o
    return False


def _structural_stop_with_c1(
    *,
    direction: str,
    entry: float,
    c1_high: float,
    c1_low: float,
    pullback: Mapping[str, Any] | None,
    impulse: Mapping[str, Any] | None,
    atr: float | None,
    config: SignalConfig,
) -> dict[str, Any]:
    """Existing stop engine + Candle-1 extreme as structural candidate."""
    pb = dict(pullback or {})
    if direction == "LONG":
        # Prefer deeper invalidation among engine candidates and C1 low
        if pb.get("retracement_low") is None:
            pb["retracement_low"] = c1_low
        else:
            pb["retracement_low"] = min(float(pb["retracement_low"]), c1_low)
        if pb.get("impulse_origin") is None and impulse:
            pb["impulse_origin"] = impulse.get("impulse_origin")
    else:
        if pb.get("retracement_high") is None:
            pb["retracement_high"] = c1_high
        else:
            pb["retracement_high"] = max(float(pb["retracement_high"]), c1_high)
        if pb.get("impulse_origin") is None and impulse:
            pb["impulse_origin"] = impulse.get("impulse_origin")

    stop = compute_stop(
        direction=direction,
        entry_price=entry,
        pullback=pb,
        atr=atr,
        sl_buffer_atr=config.sl_buffer_atr,
    )
    final = stop.get("final_stop")
    if final is None:
        # Fallback: C1 extreme with ATR buffer only when engine has no structure
        buffer = (atr or 0.0) * config.sl_buffer_atr
        if direction == "LONG":
            final = c1_low - buffer
        else:
            final = c1_high + buffer
        stop = {
            **stop,
            "final_stop": final,
            "structural_stop": c1_low if direction == "LONG" else c1_high,
            "reason": "Candle-1 extreme + ATR buffer (engine had no structural stop)",
            "risk_per_unit": abs(entry - final),
        }
    else:
        # Enforce Candle-1 extreme as additional invalidation bound
        if direction == "LONG":
            bound = c1_low - (atr or 0.0) * config.sl_buffer_atr
            final = min(float(final), bound)
        else:
            bound = c1_high + (atr or 0.0) * config.sl_buffer_atr
            final = max(float(final), bound)
        stop["final_stop"] = final
        stop["risk_per_unit"] = abs(entry - final)
        stop["candle1_bound_applied"] = True
    return stop


def detect_candle1_setup(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    candle1_index: int,
    signal_config: SignalConfig,
    engine: SignalEngine | None = None,
) -> dict[str, Any] | None:
    """Candle 1 must be fully closed; uses as_of_index = candle1_index only."""
    if candle1_index < 1 or candle1_index >= len(candles):
        return None
    eng = engine or SignalEngine(signal_config)
    analysis = eng.analyze_timeframe(
        symbol,
        timeframe,
        candles,
        as_of_index=candle1_index,
    )
    bos = analysis.get("bos") or {}
    impulse = analysis.get("impulse") or {}
    pullback = analysis.get("pullback") or {}
    swings = analysis.get("_swings_objs") or []

    if bos.get("state") != "CONFIRMED":
        return None
    # Candle 1 is the BOS confirmation close
    if bos.get("confirmation_candle") is not None and int(bos["confirmation_candle"]) != candle1_index:
        return None
    if not impulse.get("is_impulse"):
        return None

    o, h, l, c = ohlc(candles, candle1_index)
    if bos.get("direction") == "BULLISH_BOS":
        direction = "LONG"
    elif bos.get("direction") == "BEARISH_BOS":
        direction = "SHORT"
    else:
        return None
    if not _closes_in_bos_direction(direction, o, c):
        return None

    return {
        "direction": direction,
        "bos": bos,
        "impulse": impulse,
        "pullback": pullback,
        "swings": swings,
        "c1_ohlc": {"open": o, "high": h, "low": l, "close": c},
        "bos_level": bos.get("broken_level"),
        "atr": impulse.get("atr") or bos.get("atr"),
        "as_of_index": candle1_index,
    }


def try_candle2_entry(
    *,
    setup: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    candle1_index: int,
    signal_config: SignalConfig,
    ambiguous_handling: str = AMBIGUOUS_CONSERVATIVE,
) -> dict[str, Any] | None:
    """Evaluate Candle 2 only after Candle 1 is closed. No earlier entry."""
    c2 = candle1_index + 1
    if c2 >= len(candles):
        return None
    direction = str(setup["direction"])
    c1 = setup["c1_ohlc"]
    c1_high, c1_low = float(c1["high"]), float(c1["low"])
    _, h2, l2, _ = ohlc(candles, c2)

    if direction == "LONG":
        breaks = h2 > c1_high
        entry = c1_high
    else:
        breaks = l2 < c1_low
        entry = c1_low
    if not breaks:
        return None

    stop = _structural_stop_with_c1(
        direction=direction,
        entry=entry,
        c1_high=c1_high,
        c1_low=c1_low,
        pullback=setup.get("pullback"),
        impulse=setup.get("impulse"),
        atr=float(setup["atr"]) if setup.get("atr") is not None else None,
        config=signal_config,
    )
    final_stop = stop.get("final_stop")
    if final_stop is None:
        return None
    # Entry/stop must be on opposite sides of entry
    if direction == "LONG" and float(final_stop) >= entry:
        return None
    if direction == "SHORT" and float(final_stop) <= entry:
        return None

    targets = compute_targets(
        direction=direction,
        entry_price=entry,
        stop=stop,
        swings=setup.get("swings") or [],
        config=signal_config,
    )
    if not targets:
        return None
    rr = risk_reward(entry, float(final_stop), targets, min_rr=signal_config.min_rr)
    tps = [float(t["target_price"]) for t in targets if t.get("target_price") is not None]
    while len(tps) < 3:
        tps.append(None)  # type: ignore[arg-type]

    # Same-candle entry+stop ambiguity on Candle 2 (conservative)
    outcome_c2, exit_px, ambiguous = resolve_intrabar_outcome(
        direction=direction,
        high=h2,
        low=l2,
        stop=float(final_stop),
        targets=[t for t in tps if t is not None],
        handling=ambiguous_handling,
    )
    # If stop hit on entry bar before we can treat breakout as valid → SL / ambiguous
    immediate_exit = None
    if ambiguous or outcome_c2 == "SL":
        immediate_exit = {
            "outcome": "SL" if ambiguous_handling == AMBIGUOUS_CONSERVATIVE else outcome_c2,
            "exit_price": exit_px if exit_px is not None else float(final_stop),
            "exit_index": c2,
            "ambiguous": True,
        }
    elif outcome_c2 and outcome_c2.startswith("TP"):
        # Favorable target on entry bar without stop — allow (breakout continuation)
        immediate_exit = {
            "outcome": outcome_c2,
            "exit_price": exit_px,
            "exit_index": c2,
            "ambiguous": False,
        }

    return {
        "entry_index": c2,
        "entry_price": float(entry),
        "stop_price": float(final_stop),
        "stop": stop,
        "targets": targets,
        "tp1": tps[0],
        "tp2": tps[1] if len(tps) > 1 else None,
        "tp3": tps[2] if len(tps) > 2 else None,
        "rr": rr.get("best_R"),
        "risk_reward": rr,
        "direction": direction,
        "immediate_exit": immediate_exit,
        "verification": {
            "candle1_closed_before_candle2": True,
            "candle1_index": candle1_index,
            "candle2_index": c2,
            "entry_not_before_candle2": True,
            "as_of_for_setup": candle1_index,
            "no_future_used_for_setup": True,
            "break_rule": (
                f"LONG requires C2.high > C1.high ({h2} > {c1_high})"
                if direction == "LONG"
                else f"SHORT requires C2.low < C1.low ({l2} < {c1_low})"
            ),
        },
    }


def _simulate_from(
    trade: ResearchTrade,
    candles: Sequence[Mapping[str, Any]],
    *,
    start_index: int,
    handling: str,
    seed_highs: list[float] | None = None,
    seed_lows: list[float] | None = None,
) -> ResearchTrade:
    targets = [t for t in (trade.tp1, trade.tp2, trade.tp3) if t is not None]
    highs = list(seed_highs or [])
    lows = list(seed_lows or [])
    for i in range(start_index, len(candles)):
        c = candles[i]
        high = float(c.get("high") or c.get("h") or 0)
        low = float(c.get("low") or c.get("l") or 0)
        highs.append(high)
        lows.append(low)
        outcome, exit_px, ambiguous = resolve_intrabar_outcome(
            direction=trade.direction,
            high=high,
            low=low,
            stop=trade.stop_price,
            targets=targets,
            handling=handling,
        )
        if outcome is None:
            continue
        trade.exit_index = i
        trade.exit_time = _ts(candles, i)
        trade.exit_price = exit_px
        trade.outcome = outcome
        trade.ambiguous = ambiguous
        trade.holding_bars = i - trade.entry_index
        risk = abs(trade.entry_price - trade.stop_price)
        if outcome == "AMBIGUOUS_INTRABAR":
            trade.r_multiple = 0.0 if handling != AMBIGUOUS_CONSERVATIVE else (
                ((exit_px or trade.stop_price) - trade.entry_price) / risk
                if trade.direction == "LONG" and risk
                else ((trade.entry_price - (exit_px or trade.stop_price)) / risk if risk else 0.0)
            )
            if handling == AMBIGUOUS_CONSERVATIVE:
                trade.outcome = "SL"
                trade.r_multiple = (
                    (trade.stop_price - trade.entry_price) / risk
                    if trade.direction == "LONG" and risk
                    else (trade.entry_price - trade.stop_price) / risk if risk else -1.0
                )
        elif exit_px is not None and risk > 0:
            if trade.direction == "LONG":
                trade.r_multiple = (exit_px - trade.entry_price) / risk
            else:
                trade.r_multiple = (trade.entry_price - exit_px) / risk
        # MAE/MFE
        if trade.direction == "LONG":
            mae = max(0.0, trade.entry_price - min(lows))
            mfe = max(0.0, max(highs) - trade.entry_price)
        else:
            mae = max(0.0, max(highs) - trade.entry_price)
            mfe = max(0.0, trade.entry_price - min(lows))
        trade.mae, trade.mfe = mae, mfe
        trade.mae_r = mae / risk if risk else None
        trade.mfe_r = mfe / risk if risk else None
        return trade
    trade.outcome = "OPEN"
    return trade


def run_hypothesis_a(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """A) CANDLE_1_BOS → CANDLE_2_BREAK."""
    t0 = time.perf_counter()
    scfg = signal_config or SignalConfig()
    rcfg = research_config or ResearchConfig()
    quality = verify_ohlcv(candles, timeframe, config=rcfg)
    if quality["status"] == "INSUFFICIENT_DATA":
        return {
            "hypothesis": HYPOTHESIS_A,
            "status": "INSUFFICIENT_DATA",
            "data_quality": quality,
            "trades": [],
            "examples": [],
            "result": compute_metrics(
                [],
                combination_id=HYPOTHESIS_A,
                description="Candle-1 BOS + impulse → Candle-2 break entry",
                symbol=symbol,
                timeframe=timeframe,
                data_quality="INSUFFICIENT_DATA",
            ).to_dict(),
        }

    engine = SignalEngine(scfg)
    trades: list[ResearchTrade] = []
    examples: list[Candle12Example] = []
    open_until: int | None = None  # last exit index; block new entries through this bar
    n = len(candles)
    min_bars = rcfg.min_bars

    for i in range(min_bars, n - 1):
        if open_until is not None and i <= open_until:
            continue

        setup = detect_candle1_setup(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            candle1_index=i,
            signal_config=scfg,
            engine=engine,
        )
        if not setup:
            continue
        entry_plan = try_candle2_entry(
            setup=setup,
            candles=candles,
            candle1_index=i,
            signal_config=scfg,
            ambiguous_handling=rcfg.ambiguous_handling,
        )
        if not entry_plan:
            continue

        trade = ResearchTrade(
            symbol=symbol.upper(),
            timeframe=timeframe,
            combination_id=HYPOTHESIS_A,
            entry_index=int(entry_plan["entry_index"]),
            signal_time=_ts(candles, i),
            direction=str(entry_plan["direction"]),
            entry_price=float(entry_plan["entry_price"]),
            stop_price=float(entry_plan["stop_price"]),
            tp1=entry_plan.get("tp1"),
            tp2=entry_plan.get("tp2"),
            tp3=entry_plan.get("tp3"),
            rr=entry_plan.get("rr"),
            condition_snapshot={
                "candle1_index": i,
                "bos_level": setup.get("bos_level"),
                "verification": entry_plan.get("verification"),
            },
        )
        imm = entry_plan.get("immediate_exit")
        if imm:
            trade.exit_index = imm["exit_index"]
            trade.exit_price = imm["exit_price"]
            trade.outcome = imm["outcome"]
            trade.ambiguous = bool(imm.get("ambiguous"))
            trade.holding_bars = 0
            risk = abs(trade.entry_price - trade.stop_price)
            if risk > 0 and trade.exit_price is not None:
                if trade.direction == "LONG":
                    trade.r_multiple = (float(trade.exit_price) - trade.entry_price) / risk
                else:
                    trade.r_multiple = (trade.entry_price - float(trade.exit_price)) / risk
            open_until = int(trade.exit_index)
        else:
            _simulate_from(
                trade,
                candles,
                start_index=trade.entry_index + 1,
                handling=rcfg.ambiguous_handling,
            )
            open_until = int(trade.exit_index) if trade.exit_index is not None else n

        trades.append(trade)

        if len(examples) < 50:
            examples.append(
                Candle12Example(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    candle1_index=i,
                    candle1_timestamp=_ts(candles, i),
                    candle1_ohlc=dict(setup["c1_ohlc"]),
                    bos_level=float(setup["bos_level"])
                    if setup.get("bos_level") is not None
                    else None,
                    candle2_index=i + 1,
                    candle2_timestamp=_ts(candles, i + 1),
                    candle2_ohlc=_ohlc_dict(candles, i + 1),
                    entry=float(entry_plan["entry_price"]),
                    stop=float(entry_plan["stop_price"]),
                    tp1=entry_plan.get("tp1"),
                    tp2=entry_plan.get("tp2"),
                    tp3=entry_plan.get("tp3"),
                    rr=entry_plan.get("rr"),
                    direction=str(entry_plan["direction"]),
                    outcome=trade.outcome,
                    r_multiple=trade.r_multiple,
                    verification=entry_plan.get("verification") or {},
                )
            )

    closed = [t for t in trades if t.outcome != "OPEN"]
    result = compute_metrics(
        closed,
        combination_id=HYPOTHESIS_A,
        description="Candle-1 BOS + impulse → Candle-2 break entry",
        symbol=symbol.upper(),
        timeframe=timeframe,
        data_coverage=quality.get("coverage_ratio"),
        data_quality=quality.get("data_quality", "OK"),
        condition_definition={
            "hypothesis": HYPOTHESIS_A,
            "candle1": ["confirmed BOS", "impulse satisfied", "close in BOS direction"],
            "candle2": ["LONG breaks C1 high", "SHORT breaks C1 low"],
            "engines": ["detect_bos", "detect_impulse", "compute_stop", "compute_targets", "risk_reward"],
        },
        period_start=_ts(candles, 0),
        period_end=_ts(candles, n - 1),
    )
    return {
        "hypothesis": HYPOTHESIS_A,
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "data_quality": quality,
        "result": result.to_dict(),
        "trades": [t.to_dict() for t in trades],
        "examples": [e.to_dict() for e in examples],
        "elapsed_seconds": time.perf_counter() - t0,
        "label": "RESEARCH_COMPARISON",
        "disclaimer": (
            "Single research hypothesis test. Not a ranking. Not a profitability claim. "
            "Live signal engine unchanged."
        ),
    }


def run_hypothesis_b(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """B) EXISTING_PULLBACK_RETEST_ENTRY via SignalEngine.analyze (as_of_index)."""
    t0 = time.perf_counter()
    from copy import deepcopy

    scfg = signal_config or SignalConfig()
    local = deepcopy(scfg)
    local.require_mtf_alignment = False
    local.mtf_setup = timeframe
    rcfg = research_config or ResearchConfig()
    quality = verify_ohlcv(candles, timeframe, config=rcfg)
    if quality["status"] == "INSUFFICIENT_DATA":
        return {
            "hypothesis": HYPOTHESIS_B,
            "status": "INSUFFICIENT_DATA",
            "data_quality": quality,
            "trades": [],
            "result": compute_metrics(
                [],
                combination_id=HYPOTHESIS_B,
                description="Existing pullback/retest entry path",
                symbol=symbol,
                timeframe=timeframe,
                data_quality="INSUFFICIENT_DATA",
            ).to_dict(),
        }

    engine = SignalEngine(local)
    trades: list[ResearchTrade] = []
    open_trade: ResearchTrade | None = None
    n = len(candles)
    min_bars = rcfg.min_bars

    for i in range(min_bars, n):
        if open_trade is not None:
            c = candles[i]
            high = float(c.get("high") or 0)
            low = float(c.get("low") or 0)
            targets = [t for t in (open_trade.tp1, open_trade.tp2, open_trade.tp3) if t is not None]
            outcome, exit_px, ambiguous = resolve_intrabar_outcome(
                direction=open_trade.direction,
                high=high,
                low=low,
                stop=open_trade.stop_price,
                targets=targets,
                handling=rcfg.ambiguous_handling,
            )
            if outcome:
                open_trade.exit_index = i
                open_trade.exit_time = _ts(candles, i)
                open_trade.exit_price = exit_px
                open_trade.outcome = (
                    "SL" if (ambiguous and rcfg.ambiguous_handling == AMBIGUOUS_CONSERVATIVE) else outcome
                )
                open_trade.ambiguous = ambiguous
                open_trade.holding_bars = i - open_trade.entry_index
                risk = abs(open_trade.entry_price - open_trade.stop_price)
                if exit_px is not None and risk > 0:
                    if open_trade.direction == "LONG":
                        open_trade.r_multiple = (exit_px - open_trade.entry_price) / risk
                    else:
                        open_trade.r_multiple = (open_trade.entry_price - exit_px) / risk
                trades.append(open_trade)
                open_trade = None
            continue

        series = list(candles[: i + 1])
        candles_by_tf = {
            timeframe: series,
            local.mtf_major: series,
            local.mtf_primary: series,
            local.mtf_setup: series,
            local.mtf_entry: series,
        }
        as_of = {tf: len(s) - 1 for tf, s in candles_by_tf.items()}
        analysis = engine.analyze(symbol, candles_by_tf, as_of_index_by_tf=as_of)
        if analysis.status not in (
            SignalStatus.LONG_ENTRY_CANDIDATE.value,
            SignalStatus.SHORT_ENTRY_CANDIDATE.value,
            SignalStatus.ENTRY_CANDIDATE.value,
        ):
            continue
        # Require pullback/retest path evidence from existing engines
        pb = analysis.pullback or {}
        retest = analysis.retest or {}
        pb_ok = str(pb.get("pullback_state") or "") in ("ACTIVE", "CONFIRMED")
        retest_ok = bool(retest.get("retest"))
        if not (pb_ok or retest_ok):
            continue
        ep = (analysis.entry or {}).get("entry_price")
        sp = (analysis.stop or {}).get("final_stop")
        tps = [float(t["target_price"]) for t in (analysis.targets or []) if t.get("target_price")]
        if ep is None or sp is None or not tps:
            continue
        while len(tps) < 3:
            tps.append(None)  # type: ignore[arg-type]
        open_trade = ResearchTrade(
            symbol=symbol.upper(),
            timeframe=timeframe,
            combination_id=HYPOTHESIS_B,
            entry_index=i,
            signal_time=_ts(candles, i),
            direction=str(analysis.direction or "LONG"),
            entry_price=float(ep),
            stop_price=float(sp),
            tp1=tps[0],
            tp2=tps[1],
            tp3=tps[2],
            rr=(analysis.risk_reward or {}).get("best_R"),
            condition_snapshot={
                "status": analysis.status,
                "pullback_state": pb.get("pullback_state"),
                "retest": retest.get("retest"),
            },
        )

    if open_trade is not None:
        open_trade.outcome = "OPEN"
        trades.append(open_trade)

    closed = [t for t in trades if t.outcome != "OPEN"]
    # Fill MAE/MFE for B trades
    for t in closed:
        if t.exit_index is None:
            continue
        highs, lows = [], []
        for j in range(t.entry_index, t.exit_index + 1):
            _, h, l, _ = ohlc(candles, j)
            highs.append(h)
            lows.append(l)
        risk = abs(t.entry_price - t.stop_price)
        if t.direction == "LONG":
            mae = max(0.0, t.entry_price - min(lows)) if lows else 0.0
            mfe = max(0.0, max(highs) - t.entry_price) if highs else 0.0
        else:
            mae = max(0.0, max(highs) - t.entry_price) if highs else 0.0
            mfe = max(0.0, t.entry_price - min(lows)) if lows else 0.0
        t.mae, t.mfe = mae, mfe
        t.mae_r = mae / risk if risk else None
        t.mfe_r = mfe / risk if risk else None

    result = compute_metrics(
        closed,
        combination_id=HYPOTHESIS_B,
        description="Existing pullback/retest entry (SignalEngine)",
        symbol=symbol.upper(),
        timeframe=timeframe,
        data_coverage=quality.get("coverage_ratio"),
        data_quality=quality.get("data_quality", "OK"),
        condition_definition={
            "hypothesis": HYPOTHESIS_B,
            "path": "SignalEngine.analyze + pullback CONFIRMED/ACTIVE or retest",
        },
        period_start=_ts(candles, 0),
        period_end=_ts(candles, n - 1),
    )
    return {
        "hypothesis": HYPOTHESIS_B,
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "data_quality": quality,
        "result": result.to_dict(),
        "trades": [t.to_dict() for t in trades],
        "elapsed_seconds": time.perf_counter() - t0,
        "label": "RESEARCH_COMPARISON",
        "disclaimer": (
            "Existing pullback/retest research path. Not a ranking. "
            "Not a profitability claim. Live thresholds unchanged."
        ),
    }


def aggregate_trade_metrics(
    trades: Sequence[ResearchTrade] | Sequence[Mapping[str, Any]],
    *,
    combination_id: str,
    description: str,
) -> dict[str, Any]:
    objs: list[ResearchTrade] = []
    for t in trades:
        if isinstance(t, ResearchTrade):
            objs.append(t)
        else:
            objs.append(
                ResearchTrade(
                    **{k: v for k, v in t.items() if k in ResearchTrade.__dataclass_fields__}
                )
            )
    closed = [t for t in objs if t.outcome != "OPEN"]
    return compute_metrics(
        closed,
        combination_id=combination_id,
        description=description,
    ).to_dict()
