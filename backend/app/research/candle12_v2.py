"""Candle-1 → Candle-2 research V2 (research-only).

Extends candle12_hypothesis with:
- bounded rolling lookback (default 300) — never O(n²) full-history scans
- PostgreSQL-backed series (runner); bypasses in-memory 500-bar store
- standardized trigger entry + configurable tick/slippage/fees
- TRAIN / VALIDATION / OUT-OF-SAMPLE chronological splits
- data-quality labels, sample-size warnings, instrumentation

Does NOT change the live signal engine or production thresholds.
Does NOT optimize parameters on OOS.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import (
    AMBIGUOUS_CONSERVATIVE,
    ResearchConfig,
    split_period_indices,
)
from app.research.data_quality import verify_ohlcv
from app.research.metrics import compute_metrics
from app.research.rolling_structure import (
    DEFAULT_LOOKBACK_BARS,
    DEFAULT_MIN_WARMUP_BARS,
    MAX_LOOKBACK_BARS,
    RollingSwingState,
    analyze_structure_at_bar,
    clamp_lookback,
    lookback_documentation,
)
from app.research.schemas import ResearchTrade
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.risk_engine import risk_reward
from app.signals.stop_engine import compute_stop
from app.signals.target_engine import compute_targets

HYPOTHESIS_V2 = "CANDLE_1_BOS_CANDLE_2_BREAK_V2"
DEFAULT_MIN_SAMPLE_WARNING = 30
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
OUTCOME_CONSERVATIVE_SAME_BAR_SL = "CONSERVATIVE_SAME_BAR_SL"


@dataclass
class Candle12V2Config:
    """Research-only execution assumptions — not live production thresholds.

    Lookback defaults: 300 bars; min warmup 50; max 2000.
    See lookback_documentation().
    """

    # Fees / slippage (aliases kept for older callers)
    trading_fee: float = 0.0004
    entry_slippage: float = 0.0002
    exit_slippage: float = 0.0002
    fee_rate: float | None = None  # alias → trading_fee
    slippage_rate: float | None = None  # alias → both entry/exit if set
    tick_size: float = 0.0
    require_impulse: bool = True
    require_close_in_bos_direction: bool = True
    ambiguous_handling: str = AMBIGUOUS_CONSERVATIVE
    min_bars: int = DEFAULT_MIN_WARMUP_BARS
    min_coverage_ratio: float = 0.80
    min_sample_size_warning: int = DEFAULT_MIN_SAMPLE_WARNING
    train_fraction: float = 0.60
    validation_fraction: float = 0.20
    oos_fraction: float = 0.20
    # Bounded rolling lookback for structure engines (NOT full history).
    structure_lookback_bars: int = DEFAULT_LOOKBACK_BARS
    lookback_bars: int | None = None  # alias

    def __post_init__(self) -> None:
        if self.fee_rate is not None:
            self.trading_fee = float(self.fee_rate)
        else:
            self.fee_rate = float(self.trading_fee)
        if self.slippage_rate is not None:
            self.entry_slippage = float(self.slippage_rate)
            self.exit_slippage = float(self.slippage_rate)
        else:
            self.slippage_rate = float(self.entry_slippage)
        if self.lookback_bars is not None:
            self.structure_lookback_bars = int(self.lookback_bars)
        else:
            self.lookback_bars = int(self.structure_lookback_bars)
        self.structure_lookback_bars = clamp_lookback(int(self.structure_lookback_bars))
        self.lookback_bars = self.structure_lookback_bars

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["lookback_documentation"] = lookback_documentation()
        return d


@dataclass
class Candle12V2Example:
    symbol: str
    timeframe: str
    period_label: str
    candle1_index: int
    setup_candle_index: int
    confirmation_candle_index: int
    entry_candle_index: int
    candle1_timestamp: str | None
    candle1_ohlc: dict[str, float]
    bos_level: float | None
    candle2_index: int
    candle2_timestamp: str | None
    candle2_ohlc: dict[str, float]
    trigger_level: float
    actual_entry: float
    entry_price: float
    entry_trigger_timestamp: str | None
    entry_trigger_timestamp_note: str
    stop: float
    tp1: float | None
    tp2: float | None
    tp3: float | None
    initial_risk: float
    rr: float | None
    direction: str
    outcome: str | None
    outcome_resolution: str | None
    realized_R: float | None
    gross_R: float | None
    net_R: float | None
    fees_R: float | None
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ohlc_dict(candles: Sequence[Mapping[str, Any]], idx: int) -> dict[str, float]:
    o, h, l, c = ohlc(candles, idx)
    return {"open": o, "high": h, "low": l, "close": c}


def _ts(candles: Sequence[Mapping[str, Any]], idx: int) -> str | None:
    t = candle_time(candles[idx])
    return t.isoformat() if t else None


def _trigger_and_entry(
    *,
    direction: str,
    c1_high: float,
    c1_low: float,
    cfg: Candle12V2Config,
) -> tuple[float, float]:
    """Return (trigger_level, actual_entry) with entry slippage/tick — not C2 close."""
    if direction == "LONG":
        trigger = c1_high
        entry = trigger * (1.0 + cfg.entry_slippage) + cfg.tick_size
    else:
        trigger = c1_low
        entry = trigger * (1.0 - cfg.entry_slippage) - cfg.tick_size
    return float(trigger), float(entry)


def _exit_fill(direction: str, raw_exit: float, cfg: Candle12V2Config) -> float:
    """Adverse exit slippage for research fills."""
    if direction == "LONG":
        return raw_exit * (1.0 - cfg.exit_slippage)
    return raw_exit * (1.0 + cfg.exit_slippage)


def _realized_r(
    *,
    direction: str,
    entry: float,
    stop: float,
    exit_price: float,
    cfg: Candle12V2Config,
) -> tuple[float, float, float, float]:
    """Return (net_R, fees_absolute, initial_risk, gross_R)."""
    initial_risk = abs(entry - stop)
    if initial_risk <= 0:
        return 0.0, 0.0, 0.0, 0.0
    fill_exit = _exit_fill(direction, exit_price, cfg)
    if direction == "LONG":
        gross = fill_exit - entry
    else:
        gross = entry - fill_exit
    fees = cfg.trading_fee * (entry + fill_exit)
    net = gross - fees
    return net / initial_risk, fees, initial_risk, gross / initial_risk


def _structural_stop(
    *,
    direction: str,
    entry: float,
    c1_high: float,
    c1_low: float,
    pullback: Mapping[str, Any] | None,
    impulse: Mapping[str, Any] | None,
    atr: float | None,
    signal_config: SignalConfig,
) -> dict[str, Any]:
    pb = dict(pullback or {})
    if direction == "LONG":
        prev = pb.get("retracement_low")
        pb["retracement_low"] = min(float(prev), c1_low) if prev is not None else c1_low
        if pb.get("impulse_origin") is None and impulse:
            pb["impulse_origin"] = impulse.get("impulse_origin")
    else:
        prev = pb.get("retracement_high")
        pb["retracement_high"] = max(float(prev), c1_high) if prev is not None else c1_high
        if pb.get("impulse_origin") is None and impulse:
            pb["impulse_origin"] = impulse.get("impulse_origin")

    stop = compute_stop(
        direction=direction,
        entry_price=entry,
        pullback=pb,
        atr=atr,
        sl_buffer_atr=signal_config.sl_buffer_atr,
    )
    final = stop.get("final_stop")
    buffer = (atr or 0.0) * signal_config.sl_buffer_atr
    if final is None:
        final = (c1_low - buffer) if direction == "LONG" else (c1_high + buffer)
        stop = {
            **stop,
            "final_stop": final,
            "structural_stop": c1_low if direction == "LONG" else c1_high,
            "risk_per_unit": abs(entry - final),
            "reason": "Candle-1 extreme + ATR buffer",
        }
    else:
        if direction == "LONG":
            final = min(float(final), c1_low - buffer)
        else:
            final = max(float(final), c1_high + buffer)
        stop["final_stop"] = final
        stop["risk_per_unit"] = abs(entry - final)
        stop["candle1_bound_applied"] = True
    return stop


def detect_c1_v2(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    candle1_index: int,
    signal_config: SignalConfig,
    v2: Candle12V2Config,
    swing_state: RollingSwingState | None = None,
    engine: Any = None,  # accepted for API compat; unused (research path)
) -> dict[str, Any] | None:
    """Candle-1 setup using bounded lookback + as_of_index = candle1_index only."""
    del engine  # research path uses rolling structure helpers only
    if candle1_index < 1 or candle1_index >= len(candles):
        return None

    sc = signal_config.swing_for(timeframe)
    state = swing_state or RollingSwingState(
        left=sc.swing_left_bars,
        right=sc.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
    )
    structure = analyze_structure_at_bar(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        as_of_index=candle1_index,
        lookback=v2.structure_lookback_bars,
        signal_config=signal_config,
        swing_state=state,
        require_impulse=v2.require_impulse,
    )
    if not structure:
        return None

    bos = structure["bos"]
    impulse = structure["impulse"]
    pullback = structure["pullback"]
    swings = structure["swings"]

    o, h, l, c = ohlc(candles, candle1_index)
    if bos.get("direction") == "BULLISH_BOS":
        direction = "LONG"
    elif bos.get("direction") == "BEARISH_BOS":
        direction = "SHORT"
    else:
        return None
    if v2.require_close_in_bos_direction:
        if direction == "LONG" and not (c > o):
            return None
        if direction == "SHORT" and not (c < o):
            return None

    # Look-ahead assertions
    assert structure["as_of_index"] <= candle1_index
    assert structure["as_of_index"] == candle1_index

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
        "setup_as_of_index": candle1_index,
        "setup_candle_index": candle1_index,
        "structure_offset": structure["structure_offset"],
        "structure_lookback": v2.structure_lookback_bars,
    }


def try_c2_entry_v2(
    *,
    setup: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    candle1_index: int,
    signal_config: SignalConfig,
    v2: Candle12V2Config,
) -> dict[str, Any] | None:
    c2 = candle1_index + 1
    if c2 >= len(candles):
        return None
    direction = str(setup["direction"])
    c1 = setup["c1_ohlc"]
    c1_high, c1_low = float(c1["high"]), float(c1["low"])
    o2, h2, l2, c2_close = ohlc(candles, c2)
    trigger, entry = _trigger_and_entry(
        direction=direction, c1_high=c1_high, c1_low=c1_low, cfg=v2
    )

    if direction == "LONG":
        breaks = h2 > trigger
        fills = h2 >= entry or o2 >= entry
    else:
        breaks = l2 < trigger
        fills = l2 <= entry or o2 <= entry
    if not (breaks and fills):
        return None

    gap_through = (direction == "LONG" and o2 >= entry) or (
        direction == "SHORT" and o2 <= entry
    )
    entry_trigger_timestamp = _ts(candles, c2)
    entry_trigger_note = (
        "GAP_THROUGH_AT_C2_OPEN"
        if gap_through
        else (
            "OHLCV_BAR_RESOLUTION: trigger crossed during Candle-2; "
            "exact tick time unavailable — timestamp is Candle-2 open_time "
            "(earliest bound). Entry price is trigger+/-slippage/tick, NOT C2 close."
        )
    )

    stop = _structural_stop(
        direction=direction,
        entry=entry,
        c1_high=c1_high,
        c1_low=c1_low,
        pullback=setup.get("pullback"),
        impulse=setup.get("impulse"),
        atr=float(setup["atr"]) if setup.get("atr") is not None else None,
        signal_config=signal_config,
    )
    final_stop = stop.get("final_stop")
    if final_stop is None:
        return None
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

    outcome_c2, exit_px, ambiguous = resolve_intrabar_outcome(
        direction=direction,
        high=h2,
        low=l2,
        stop=float(final_stop),
        targets=[t for t in tps if t is not None],
        handling=v2.ambiguous_handling,
    )
    if gap_through:
        stop_also = (direction == "LONG" and l2 <= float(final_stop)) or (
            direction == "SHORT" and h2 >= float(final_stop)
        )
        if stop_also:
            ambiguous = True
            outcome_c2 = "AMBIGUOUS_INTRABAR"
            exit_px = float(final_stop)

    immediate_exit = None
    outcome_resolution = None
    if ambiguous:
        if v2.ambiguous_handling == AMBIGUOUS_CONSERVATIVE:
            immediate_exit = {
                "outcome": "SL",
                "raw_outcome": "AMBIGUOUS_INTRABAR",
                "exit_price": float(final_stop),
                "exit_index": c2,
                "ambiguous": True,
            }
            outcome_resolution = OUTCOME_CONSERVATIVE_SAME_BAR_SL
        else:
            immediate_exit = {
                "outcome": "AMBIGUOUS_INTRABAR",
                "raw_outcome": "AMBIGUOUS_INTRABAR",
                "exit_price": float(final_stop),
                "exit_index": c2,
                "ambiguous": True,
            }
            outcome_resolution = "AMBIGUOUS_INTRABAR"
    elif outcome_c2 == "SL":
        immediate_exit = {
            "outcome": "SL",
            "raw_outcome": "SL",
            "exit_price": float(final_stop),
            "exit_index": c2,
            "ambiguous": False,
        }
    elif outcome_c2 and str(outcome_c2).startswith("TP"):
        immediate_exit = {
            "outcome": outcome_c2,
            "raw_outcome": outcome_c2,
            "exit_price": exit_px,
            "exit_index": c2,
            "ambiguous": False,
        }

    initial_risk = abs(entry - float(final_stop))
    confirmation_index = c2
    entry_index = c2
    assert confirmation_index == candle1_index + 1
    assert entry_index >= confirmation_index
    assert setup.get("setup_as_of_index", candle1_index) <= candle1_index

    return {
        "entry_index": entry_index,
        "setup_candle_index": candle1_index,
        "confirmation_candle_index": confirmation_index,
        "entry_candle_index": entry_index,
        "trigger_level": trigger,
        "entry_price": entry,
        "entry_trigger_timestamp": entry_trigger_timestamp,
        "entry_trigger_timestamp_note": entry_trigger_note,
        "stop_price": float(final_stop),
        "stop": stop,
        "targets": targets,
        "tp1": tps[0],
        "tp2": tps[1] if len(tps) > 1 else None,
        "tp3": tps[2] if len(tps) > 2 else None,
        "rr": rr.get("best_R"),
        "risk_reward": rr,
        "initial_risk": initial_risk,
        "direction": direction,
        "immediate_exit": immediate_exit,
        "outcome_resolution": outcome_resolution,
        "c2_close_not_used_as_entry": True,
        "c2_close": c2_close,
        "verification": {
            "candle1_closed_before_candle2": True,
            "candle1_index": candle1_index,
            "setup_candle_index": candle1_index,
            "candle2_index": c2,
            "confirmation_candle_index": confirmation_index,
            "entry_candle_index": entry_index,
            "confirmation_index_is_setup_plus_1": confirmation_index == candle1_index + 1,
            "entry_index_gte_confirmation": entry_index >= confirmation_index,
            "entry_not_before_candle2": True,
            "as_of_for_setup": candle1_index,
            "setup_as_of_index": candle1_index,
            "no_future_used_for_setup": True,
            "entry_is_not_c2_close": True,
            "break_rule": (
                f"LONG: C2.high > trigger {trigger} and reaches entry {entry}"
                if direction == "LONG"
                else f"SHORT: C2.low < trigger {trigger} and reaches entry {entry}"
            ),
        },
    }


def _apply_exit_economics(
    trade: ResearchTrade,
    *,
    raw_exit: float,
    v2: Candle12V2Config,
) -> None:
    net_r, fees, risk, gross_r = _realized_r(
        direction=trade.direction,
        entry=trade.entry_price,
        stop=trade.stop_price,
        exit_price=raw_exit,
        cfg=v2,
    )
    trade.r_multiple = net_r
    trade.condition_snapshot = {
        **(trade.condition_snapshot or {}),
        "initial_risk": risk,
        "fees_absolute": fees,
        "fees_R": (fees / risk) if risk else None,
        "raw_exit": raw_exit,
        "gross_R": gross_r,
        "net_R": net_r,
        "realized_R": net_r,
        "trading_fee": v2.trading_fee,
        "entry_slippage": v2.entry_slippage,
        "exit_slippage": v2.exit_slippage,
    }


def _simulate_forward(
    trade: ResearchTrade,
    candles: Sequence[Mapping[str, Any]],
    *,
    start_index: int,
    v2: Candle12V2Config,
) -> ResearchTrade:
    targets = [t for t in (trade.tp1, trade.tp2, trade.tp3) if t is not None]
    highs: list[float] = []
    lows: list[float] = []
    for i in range(start_index, len(candles)):
        high = float(candles[i].get("high") or 0)
        low = float(candles[i].get("low") or 0)
        highs.append(high)
        lows.append(low)
        outcome, exit_px, ambiguous = resolve_intrabar_outcome(
            direction=trade.direction,
            high=high,
            low=low,
            stop=trade.stop_price,
            targets=targets,
            handling=v2.ambiguous_handling,
        )
        if outcome is None:
            continue
        trade.exit_index = i
        trade.exit_time = _ts(candles, i)
        trade.ambiguous = ambiguous
        trade.holding_bars = i - trade.entry_index
        if ambiguous and v2.ambiguous_handling == AMBIGUOUS_CONSERVATIVE:
            trade.outcome = "SL"
            raw_exit = trade.stop_price
            trade.condition_snapshot = {
                **(trade.condition_snapshot or {}),
                "outcome_resolution": OUTCOME_CONSERVATIVE_SAME_BAR_SL,
            }
        else:
            trade.outcome = outcome
            raw_exit = float(exit_px if exit_px is not None else trade.stop_price)
        trade.exit_price = raw_exit
        _apply_exit_economics(trade, raw_exit=raw_exit, v2=v2)
        risk = abs(trade.entry_price - trade.stop_price)
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


def run_candle12_v2(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    signal_config: SignalConfig | None = None,
    v2_config: Candle12V2Config | None = None,
    period_label: str = "FULL",
    index_start: int | None = None,
    index_end: int | None = None,
    progress_every: int = 2000,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()

    scfg = signal_config or SignalConfig()
    v2 = v2_config or Candle12V2Config(
        trading_fee=scfg.fee_rate,
        entry_slippage=scfg.slippage_rate,
        exit_slippage=scfg.slippage_rate,
    )
    rcfg = ResearchConfig(
        min_bars=v2.min_bars,
        min_coverage_ratio=v2.min_coverage_ratio,
        ambiguous_handling=v2.ambiguous_handling,
    )
    quality = verify_ohlcv(candles, timeframe, config=rcfg)
    if quality["status"] == "INSUFFICIENT_DATA":
        return {
            "hypothesis": HYPOTHESIS_V2,
            "status": "INSUFFICIENT_DATA",
            "data_quality": quality,
            "trades": [],
            "examples": [],
            "result": compute_metrics(
                [],
                combination_id=HYPOTHESIS_V2,
                description="C1 BOS -> C2 break V2",
                symbol=symbol,
                timeframe=timeframe,
                data_quality="INSUFFICIENT_DATA",
                period_label=period_label,
            ).to_dict(),
            "sample_size": 0,
            "sample_size_warning": _sample_warning(0, v2.min_sample_size_warning),
            "instrumentation": {
                "series_processing_ms": (time.perf_counter() - t0) * 1000.0,
                "candles_processed": 0,
                "setups_evaluated": 0,
                "trades_generated": 0,
            },
        }

    sc = scfg.swing_for(timeframe)
    swing_state = RollingSwingState(
        left=sc.swing_left_bars,
        right=sc.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
    )
    swing_state.bind(candles)

    trades: list[ResearchTrade] = []
    examples: list[Candle12V2Example] = []
    open_until: int | None = None
    n = len(candles)
    start = max(v2.min_bars, index_start or 0)
    hard_end = min(n - 1, index_end if index_end is not None else n)
    setups_evaluated = 0
    candles_processed = 0

    for i in range(start, hard_end):
        candles_processed += 1
        if i + 1 >= n:
            break
        if index_end is not None and i + 1 >= index_end:
            break
        if open_until is not None and i <= open_until:
            # Still advance swing state chronologically so lookback stays consistent
            swing_state.update(
                candles, end=i, lookback=v2.structure_lookback_bars
            )
            continue
        if index_start is not None and i < index_start:
            swing_state.update(
                candles, end=i, lookback=v2.structure_lookback_bars
            )
            continue

        if progress_callback and progress_every and candles_processed % progress_every == 0:
            elapsed = time.perf_counter() - t0
            progress_callback(
                {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "candles": n,
                    "processed": start + candles_processed,
                    "trades": len(trades),
                    "elapsed": elapsed,
                }
            )

        setup = detect_c1_v2(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            candle1_index=i,
            signal_config=scfg,
            v2=v2,
            swing_state=swing_state,
        )
        setups_evaluated += 1
        if not setup:
            continue
        plan = try_c2_entry_v2(
            setup=setup,
            candles=candles,
            candle1_index=i,
            signal_config=scfg,
            v2=v2,
        )
        if not plan:
            continue
        if index_start is not None and int(plan["entry_index"]) < index_start:
            continue
        if index_end is not None and int(plan["entry_index"]) >= index_end:
            continue

        assert plan["confirmation_candle_index"] == i + 1
        assert plan["entry_candle_index"] >= plan["confirmation_candle_index"]
        assert setup["setup_as_of_index"] <= i

        trade = ResearchTrade(
            symbol=symbol.upper(),
            timeframe=timeframe,
            combination_id=HYPOTHESIS_V2,
            entry_index=int(plan["entry_index"]),
            signal_time=plan.get("entry_trigger_timestamp"),
            direction=str(plan["direction"]),
            entry_price=float(plan["entry_price"]),
            stop_price=float(plan["stop_price"]),
            tp1=plan.get("tp1"),
            tp2=plan.get("tp2"),
            tp3=plan.get("tp3"),
            rr=plan.get("rr"),
            period_label=period_label,
            condition_snapshot={
                "candle1_index": i,
                "setup_candle_index": i,
                "confirmation_candle_index": plan["confirmation_candle_index"],
                "entry_candle_index": plan["entry_candle_index"],
                "trigger_level": plan["trigger_level"],
                "entry_trigger_timestamp": plan.get("entry_trigger_timestamp"),
                "entry_trigger_timestamp_note": plan.get("entry_trigger_timestamp_note"),
                "initial_risk": plan.get("initial_risk"),
                "bos_level": setup.get("bos_level"),
                "trading_fee": v2.trading_fee,
                "entry_slippage": v2.entry_slippage,
                "exit_slippage": v2.exit_slippage,
                "tick_size": v2.tick_size,
                "structure_lookback_bars": v2.structure_lookback_bars,
                "c2_close_not_used_as_entry": True,
                "outcome_resolution": plan.get("outcome_resolution"),
                "verification": plan.get("verification"),
            },
        )
        imm = plan.get("immediate_exit")
        if imm:
            trade.exit_index = imm["exit_index"]
            trade.exit_time = _ts(candles, int(imm["exit_index"]))
            trade.outcome = imm["outcome"]
            trade.ambiguous = bool(imm.get("ambiguous"))
            trade.holding_bars = 0
            trade.exit_price = float(imm["exit_price"])
            if plan.get("outcome_resolution"):
                trade.condition_snapshot["outcome_resolution"] = plan[
                    "outcome_resolution"
                ]
            _apply_exit_economics(trade, raw_exit=float(imm["exit_price"]), v2=v2)
            open_until = int(trade.exit_index)
        else:
            _simulate_forward(
                trade, candles, start_index=trade.entry_index + 1, v2=v2
            )
            open_until = int(trade.exit_index) if trade.exit_index is not None else n
        trades.append(trade)

        if len(examples) < 100:
            snap = trade.condition_snapshot or {}
            examples.append(
                Candle12V2Example(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    period_label=period_label,
                    candle1_index=i,
                    setup_candle_index=i,
                    confirmation_candle_index=int(plan["confirmation_candle_index"]),
                    entry_candle_index=int(plan["entry_candle_index"]),
                    candle1_timestamp=_ts(candles, i),
                    candle1_ohlc=dict(setup["c1_ohlc"]),
                    bos_level=float(setup["bos_level"])
                    if setup.get("bos_level") is not None
                    else None,
                    candle2_index=i + 1,
                    candle2_timestamp=_ts(candles, i + 1),
                    candle2_ohlc=_ohlc_dict(candles, i + 1),
                    trigger_level=float(plan["trigger_level"]),
                    actual_entry=float(plan["entry_price"]),
                    entry_price=float(plan["entry_price"]),
                    entry_trigger_timestamp=plan.get("entry_trigger_timestamp"),
                    entry_trigger_timestamp_note=str(
                        plan.get("entry_trigger_timestamp_note") or ""
                    ),
                    stop=float(plan["stop_price"]),
                    tp1=plan.get("tp1"),
                    tp2=plan.get("tp2"),
                    tp3=plan.get("tp3"),
                    initial_risk=float(plan.get("initial_risk") or 0),
                    rr=plan.get("rr"),
                    direction=str(plan["direction"]),
                    outcome=trade.outcome,
                    outcome_resolution=snap.get("outcome_resolution"),
                    realized_R=trade.r_multiple,
                    gross_R=snap.get("gross_R"),
                    net_R=snap.get("net_R"),
                    fees_R=snap.get("fees_R"),
                    verification={
                        **(plan.get("verification") or {}),
                        "no_lookahead_ok": (
                            plan.get("verification", {}).get("as_of_for_setup") == i
                            and plan.get("verification", {}).get("candle2_index")
                            == i + 1
                            and plan.get("verification", {}).get("entry_is_not_c2_close")
                        ),
                    },
                )
            )

    closed = [t for t in trades if t.outcome != "OPEN"]
    result = compute_metrics(
        closed,
        combination_id=HYPOTHESIS_V2,
        description="C1 BOS confirm -> C2 break entry V2 (fees+slippage)",
        symbol=symbol.upper(),
        timeframe=timeframe,
        period_label=period_label,
        data_coverage=quality.get("coverage_ratio"),
        data_quality=quality.get("data_quality", "DATA_OK"),
        condition_definition={
            "hypothesis": HYPOTHESIS_V2,
            "entry": "C1 extreme +/- entry_slippage/tick (not C2 close)",
            "v2_config": v2.to_dict(),
        },
        period_start=quality.get("calendar_start") or _ts(candles, 0),
        period_end=quality.get("calendar_end") or _ts(candles, n - 1),
    )
    # Attach gross/net aggregates
    result_dict = result.to_dict()
    gross_vals = [
        float((t.condition_snapshot or {}).get("gross_R"))
        for t in closed
        if (t.condition_snapshot or {}).get("gross_R") is not None
    ]
    net_vals = [
        float((t.condition_snapshot or {}).get("net_R"))
        for t in closed
        if (t.condition_snapshot or {}).get("net_R") is not None
    ]
    result_dict["gross_R"] = (sum(gross_vals) / len(gross_vals)) if gross_vals else None
    result_dict["net_R"] = (sum(net_vals) / len(net_vals)) if net_vals else None
    result_dict["fees"] = v2.trading_fee
    result_dict["slippage"] = {
        "entry_slippage": v2.entry_slippage,
        "exit_slippage": v2.exit_slippage,
    }
    result_dict["gap_count"] = quality.get("gap_count")
    result_dict["duplicate_count"] = quality.get("duplicate_count")
    result_dict["calendar_start"] = quality.get("calendar_start")
    result_dict["calendar_end"] = quality.get("calendar_end")

    sample = result.sample_size
    elapsed = time.perf_counter() - t0

    return {
        "hypothesis": HYPOTHESIS_V2,
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "period_label": period_label,
        "data_quality": quality,
        "v2_config": v2.to_dict(),
        "result": result_dict,
        "trades": [t.to_dict() for t in trades],
        "examples": [e.to_dict() for e in examples],
        "sample_size": sample,
        "sample_size_warning": _sample_warning(sample, v2.min_sample_size_warning),
        "elapsed_seconds": elapsed,
        "instrumentation": {
            "series_processing_ms": elapsed * 1000.0,
            "candles_processed": candles_processed,
            "setups_evaluated": setups_evaluated,
            "trades_generated": len(trades),
            "lookback_bars": v2.structure_lookback_bars,
        },
        "label": "RESEARCH_COMPARISON",
        "disclaimer": (
            "Candle-1/Candle-2 research V2 only. Not a ranking. "
            "Not a profitability claim. Live engine unchanged. "
            "OOS not used for parameter optimization."
        ),
    }


def run_candle12_v2_with_periods(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    signal_config: SignalConfig | None = None,
    v2_config: Candle12V2Config | None = None,
) -> dict[str, Any]:
    """FULL + TRAIN / VALIDATION / OUT_OF_SAMPLE. OOS is evaluation-only.

    Parameters are fixed in v2_config before any OOS metrics are inspected.
    Configuration is never derived from OOS outcomes.
    """
    v2 = v2_config or Candle12V2Config()
    # Freeze configuration identity before any period metrics
    frozen_config = v2.to_dict()
    n = len(candles)
    splits = split_period_indices(
        n,
        train_fraction=v2.train_fraction,
        validation_fraction=v2.validation_fraction,
        oos_fraction=v2.oos_fraction,
    )
    # Single FULL pass — chronological bucket into periods (no re-optimization)
    full = run_candle12_v2(
        symbol,
        timeframe,
        candles,
        signal_config=signal_config,
        v2_config=v2,
        period_label="FULL",
    )
    periods: dict[str, Any] = {}
    split_bounds: dict[str, Any] = {}
    for label, (a, b) in splits.items():
        split_bounds[label] = {
            "index_start": a,
            "index_end": b,
            "start_time": _ts(candles, a) if a < n else None,
            "end_time": _ts(candles, b - 1) if 0 < b <= n else None,
        }
        bucket = []
        for t in full.get("trades") or []:
            if t.get("outcome") == "OPEN":
                continue
            ei = int(t.get("entry_index", -1))
            if a <= ei < b:
                bucket.append({**t, "period_label": label})
        periods[label] = {
            "index_range": [a, b],
            "sample_size": len(bucket),
            "sample_size_warning": _sample_warning(
                len(bucket), v2.min_sample_size_warning
            ),
            "result": aggregate_v2_trades(
                bucket,
                description=label,
                min_sample_warning=v2.min_sample_size_warning,
            ),
            "status": "OK",
        }

    return {
        "hypothesis": HYPOTHESIS_V2,
        "label": "RESEARCH_COMPARISON",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "splits": splits,
        "split_bounds": {
            "train_start": split_bounds.get("TRAINING_PERIOD", {}).get("start_time"),
            "train_end": split_bounds.get("TRAINING_PERIOD", {}).get("end_time"),
            "validation_start": split_bounds.get("VALIDATION_PERIOD", {}).get(
                "start_time"
            ),
            "validation_end": split_bounds.get("VALIDATION_PERIOD", {}).get("end_time"),
            "oos_start": split_bounds.get("OUT_OF_SAMPLE_PERIOD", {}).get("start_time"),
            "oos_end": split_bounds.get("OUT_OF_SAMPLE_PERIOD", {}).get("end_time"),
            "indices": split_bounds,
        },
        "configuration_id": frozen_config,
        "full": full,
        "periods": periods,
        "oos_note": (
            "OUT_OF_SAMPLE_PERIOD is evaluation-only. "
            "Parameters were not optimized using OOS."
        ),
        "disclaimer": full.get("disclaimer"),
    }


def _sample_warning(sample_size: int, minimum: int) -> dict[str, Any] | None:
    if sample_size >= minimum:
        return None
    return {
        "flag": "MINIMUM_SAMPLE_SIZE_WARNING",
        "label": INSUFFICIENT_SAMPLE,
        "sample_size": sample_size,
        "minimum_sample_size": minimum,
        "message": (
            f"INSUFFICIENT_SAMPLE: sample_size={sample_size} below "
            f"minimum_sample_size={minimum}. Do not draw strategy conclusions."
        ),
    }


def aggregate_v2_trades(
    trades: Sequence[Mapping[str, Any]] | Sequence[ResearchTrade],
    *,
    description: str = HYPOTHESIS_V2,
    min_sample_warning: int = DEFAULT_MIN_SAMPLE_WARNING,
) -> dict[str, Any]:
    objs: list[ResearchTrade] = []
    for t in trades:
        if isinstance(t, ResearchTrade):
            objs.append(t)
        else:
            objs.append(
                ResearchTrade(
                    **{
                        k: v
                        for k, v in t.items()
                        if k in ResearchTrade.__dataclass_fields__
                    }
                )
            )
    closed = [t for t in objs if t.outcome != "OPEN"]
    result = compute_metrics(
        closed,
        combination_id=HYPOTHESIS_V2,
        description=description,
    )
    d = result.to_dict()
    d["sample_size_warning"] = _sample_warning(result.sample_size, min_sample_warning)
    if d["sample_size_warning"]:
        d["sample_label"] = INSUFFICIENT_SAMPLE
    gross_vals = []
    net_vals = []
    for t in closed:
        snap = t.condition_snapshot or {}
        if snap.get("gross_R") is not None:
            gross_vals.append(float(snap["gross_R"]))
        if snap.get("net_R") is not None:
            net_vals.append(float(snap["net_R"]))
    d["gross_R"] = (sum(gross_vals) / len(gross_vals)) if gross_vals else None
    d["net_R"] = (sum(net_vals) / len(net_vals)) if net_vals else None
    return d
