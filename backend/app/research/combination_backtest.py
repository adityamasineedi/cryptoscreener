"""Walk-forward combination backtest with no look-ahead.

Uses as_of_index path from existing engines. Future candles are inspected
ONLY after entry for outcome / MAE / MFE. Same-candle SL+TP → AMBIGUOUS_INTRABAR.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

import numpy as np

from app.config import get_settings
from app.engines.mtf.indicators import atr_series
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.research.bos_combinations import CombinationDefinition, get_combination
from app.research.bos_strategy_comparison.htf import (
    build_htf_as_of_index_map,
    build_htf_as_of_index_map_fully_closed,
    precompute_htf_trend_cache,
)
from app.research.combination_engine import (
    _clone_signal_config,
    begin_signal_config_clone_reuse,
    end_signal_config_clone_reuse,
    evaluate_combination_at_bar,
)
from app.research.entry_diagnostics import (
    classify_bos_prefilter_skip,
    classify_direction_filter_mismatch,
    classify_from_eval_setup,
    classify_insufficient_bars_before_walk,
    classify_position_bar,
    classify_sticky_bos_skip,
    record_for_bar,
)
from app.research.backtest_timing import (
    BACKTEST_END,
    BACKTEST_HEARTBEAT,
    BACKTEST_START,
    HTF_PRECOMPUTE_END,
    HTF_PRECOMPUTE_START,
    METRICS_END,
    METRICS_START,
    SIGNAL_GENERATION_END,
    STRUCTURE_SCAN_HEARTBEAT,
    STRUCTURE_SCAN_START,
    emit_phase,
)
from app.research.config import (
    AMBIGUOUS_CONSERVATIVE,
    AMBIGUOUS_EXCLUDE,
    AMBIGUOUS_NEUTRAL,
    RESEARCH_ENGINE_VERSION,
    SIGNAL_ENGINE_VERSION,
    ResearchConfig,
    classify_asset_group,
    split_period_indices,
    walk_forward_windows,
)
from app.research.data_cache.stage_profiler import research_stage_profiler
from app.research.data_quality import verify_ohlcv
from app.research.metrics import compute_metrics
from app.research.schemas import ResearchTrade
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.schemas import TrendState
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import detect_swings, extend_swings
from app.signals.trend_engine import infer_trend


def _bos_break_levels(
    swings: Sequence[Any],
    end: int,
) -> tuple[str, float, float] | None:
    """Trend plus last confirmed swing high/low visible at ``end``.

    Same inputs as ``_bos_break_possible``. Trend uses the full swing list;
    the level prices ignore a swing that is not yet confirmed before ``end``.
    """
    if not swings or end < 1:
        return None
    trend = infer_trend(swings if isinstance(swings, list) else list(swings))
    trend_state = str(trend.get("trend") or "")
    high_px: float | None = None
    low_px: float | None = None
    for swing in swings:
        if swing.bar_index >= end:
            continue
        if swing.swing_type == "HIGH":
            high_px = float(swing.price)
        elif swing.swing_type == "LOW":
            low_px = float(swing.price)
    if high_px is None or low_px is None:
        return None
    return trend_state, high_px, low_px


def _close_breaks_level(
    trend_state: str,
    close: float,
    high_px: float,
    low_px: float,
    direction_filter: str | None,
) -> bool:
    df = (direction_filter or "").upper()
    if trend_state == TrendState.BULLISH.value and close > high_px:
        return df != "SHORT"
    if trend_state == TrendState.BEARISH.value and close < low_px:
        return df != "LONG"
    return False


def _bos_break_possible(
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    swings: Sequence[Any],
    *,
    direction_filter: str | None,
) -> bool:
    """Cheap pre-filter matching detect_bos break conditions (no full analyze)."""
    end = min(as_of_index, len(candles) - 1)
    levels = _bos_break_levels(swings, end)
    if levels is None:
        return False
    trend_state, high_px, low_px = levels
    return _close_breaks_level(
        trend_state, ohlc(candles, end)[3], high_px, low_px, direction_filter
    )


def _rolling_mean_inclusive(
    values: Sequence[float], window: int = 20
) -> np.ndarray:
    """Vectorized trailing mean including the current bar.

    This mirrors the BOS diagnostic's historical ``vols[-20:]`` mean. It is
    precomputed once for the research walk instead of rebuilding candle-prefix
    arrays for every candidate bar.
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return np.empty(0, dtype=np.float64)
    cumulative = np.cumsum(arr, dtype=np.float64)
    totals = cumulative.copy()
    if arr.size > window:
        totals[window:] = cumulative[window:] - cumulative[:-window]
    denominators = np.minimum(
        np.arange(1, arr.size + 1, dtype=np.float64), float(window)
    )
    return totals / denominators


def _bar_hits_levels(
    *,
    direction: str,
    high: float,
    low: float,
    stop: float,
    targets: list[float],
) -> tuple[bool, bool, int | None]:
    """Return (stop_hit, any_tp_hit, tp_index).

    ``tp_index`` is the slot of the reached target nearest the entry side
    (LONG: lowest reached price, SHORT: highest). Slots can be out of
    distance order; price passes the nearest level first. Ties keep the
    lower slot, so distance-ordered targets resolve exactly as slot order.
    """
    stop_hit = (low <= stop) if direction == "LONG" else (high >= stop)
    tp_idx = None
    for i, tp in enumerate(targets):
        if direction == "LONG" and high >= tp:
            if tp_idx is None or tp < targets[tp_idx]:
                tp_idx = i
        elif direction == "SHORT" and low <= tp:
            if tp_idx is None or tp > targets[tp_idx]:
                tp_idx = i
    return stop_hit, tp_idx is not None, tp_idx


def resolve_intrabar_outcome(
    *,
    direction: str,
    high: float,
    low: float,
    stop: float,
    targets: list[float],
    handling: str,
) -> tuple[str | None, float | None, bool]:
    """Resolve exit for one bar. Returns (outcome, exit_price, ambiguous)."""
    stop_hit, tp_hit, tp_idx = _bar_hits_levels(
        direction=direction, high=high, low=low, stop=stop, targets=targets
    )
    if stop_hit and tp_hit:
        if handling == AMBIGUOUS_EXCLUDE:
            return "AMBIGUOUS_INTRABAR", None, True
        if handling == AMBIGUOUS_NEUTRAL:
            return "AMBIGUOUS_INTRABAR", stop, True  # mark ambiguous; R forced 0 later
        # CONSERVATIVE default: SL
        return "SL", stop, True
    if stop_hit:
        return "SL", stop, False
    if tp_hit and tp_idx is not None:
        return f"TP{tp_idx + 1}", targets[tp_idx], False
    return None, None, False


def _excursions(
    *,
    direction: str,
    entry: float,
    stop: float,
    highs: Sequence[float],
    lows: Sequence[float],
) -> tuple[float, float, float | None, float | None]:
    risk = abs(entry - stop)
    if direction == "LONG":
        worst = min(lows) if lows else entry
        best = max(highs) if highs else entry
        mae = max(0.0, entry - worst)
        mfe = max(0.0, best - entry)
    else:
        worst = max(highs) if highs else entry
        best = min(lows) if lows else entry
        mae = max(0.0, worst - entry)
        mfe = max(0.0, entry - best)
    mae_r = (mae / risk) if risk > 0 else None
    mfe_r = (mfe / risk) if risk > 0 else None
    return mae, mfe, mae_r, mfe_r


def _simulate_trade(
    trade: ResearchTrade,
    candles: Sequence[Mapping[str, Any]],
    *,
    handling: str,
) -> ResearchTrade:
    """Inspect candles AFTER entry_index only."""
    targets = [t for t in (trade.tp1, trade.tp2, trade.tp3) if t is not None]
    highs: list[float] = []
    lows: list[float] = []
    for i in range(trade.entry_index + 1, len(candles)):
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
        ts = candle_time(c)
        trade.exit_time = ts.isoformat() if ts else None
        trade.exit_price = exit_px
        trade.outcome = outcome
        trade.ambiguous = ambiguous
        trade.holding_bars = i - trade.entry_index
        risk = abs(trade.entry_price - trade.stop_price)
        if outcome == "AMBIGUOUS_INTRABAR" and handling in (
            AMBIGUOUS_NEUTRAL,
            AMBIGUOUS_EXCLUDE,
        ):
            trade.r_multiple = 0.0 if handling == AMBIGUOUS_NEUTRAL else None
        elif exit_px is not None and risk > 0:
            if trade.direction == "LONG":
                trade.r_multiple = (exit_px - trade.entry_price) / risk
            else:
                trade.r_multiple = (trade.entry_price - exit_px) / risk
        else:
            trade.r_multiple = 0.0
        # time_to metrics — only the level actually exited at
        bars = trade.holding_bars
        if outcome == "SL":
            trade.time_to_sl = bars
        elif outcome == "TP1":
            trade.time_to_tp1 = bars
        elif outcome == "TP2":
            trade.time_to_tp2 = bars
        elif outcome == "TP3":
            trade.time_to_tp3 = bars
        mae, mfe, mae_r, mfe_r = _excursions(
            direction=trade.direction,
            entry=trade.entry_price,
            stop=trade.stop_price,
            highs=highs,
            lows=lows,
        )
        trade.mae, trade.mfe, trade.mae_r, trade.mfe_r = mae, mfe, mae_r, mfe_r
        return trade

    trade.outcome = "OPEN"
    mae, mfe, mae_r, mfe_r = _excursions(
        direction=trade.direction,
        entry=trade.entry_price,
        stop=trade.stop_price,
        highs=highs,
        lows=lows,
    )
    trade.mae, trade.mfe, trade.mae_r, trade.mfe_r = mae, mfe, mae_r, mfe_r
    return trade


def simulate_research_trade(
    trade: ResearchTrade,
    candles: Sequence[Mapping[str, Any]],
    *,
    handling: str = AMBIGUOUS_CONSERVATIVE,
) -> ResearchTrade:
    """Public wrapper for the existing combination_backtest trade simulator.

    Research-only. Inspects candles AFTER entry_index for SL/TP/MAE/MFE.
    Does not alter production signal or stop engines.
    """
    return _simulate_trade(trade, candles, handling=handling)


def evaluate_candidate_trades(
    candles: Sequence[Mapping[str, Any]],
    candidates: Sequence[ResearchTrade],
    *,
    handling: str = AMBIGUOUS_CONSERVATIVE,
    one_open_at_a_time: bool = True,
) -> list[ResearchTrade]:
    """Evaluate pre-built research entry candidates with existing trade mechanics.

    SIGNAL LOGIC lives in the caller (strategy adapter). This function is
    TRADE EVALUATION LOGIC only: same-bar SL handling, exits, MAE/MFE, R.
    Candidates must already have entry_index / entry_price / stop / targets.
    """
    ordered = sorted(candidates, key=lambda t: (t.entry_index, t.symbol, t.combination_id))
    out: list[ResearchTrade] = []
    next_allowed = 0
    for cand in ordered:
        if one_open_at_a_time and cand.entry_index < next_allowed:
            continue
        closed = _simulate_trade(cand, candles, handling=handling)
        out.append(closed)
        if one_open_at_a_time:
            if closed.exit_index is not None:
                next_allowed = int(closed.exit_index) + 1
            else:
                # Still open — block further entries for this series
                next_allowed = len(candles)
    return out


def run_combination_backtest(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run one combination. Clone reuse is scoped to this call only."""
    token = begin_signal_config_clone_reuse()
    try:
        return _run_combination_backtest_impl(*args, **kwargs)
    finally:
        end_signal_config_clone_reuse(token)


def _run_combination_backtest_impl(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    combination: CombinationDefinition | str,
    *,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
    market_cap: float | None = None,
    period_label: str = "FULL",
    index_start: int | None = None,
    index_end: int | None = None,
    direction_filter: str | None = None,
    should_cancel: Any | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_15m: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    candles_1d: Sequence[Mapping[str, Any]] | None = None,
    progress_callback: Any | None = None,
    job_id: str | None = None,
    prebuilt_context: Any | None = None,
) -> dict[str, Any]:
    """Run one combination on one symbol/timeframe series.

    Read-only: does not write to live signal store/cache.
    ``should_cancel`` is an optional zero-arg callable; when it returns True the
    walk aborts early (used by background UI jobs).

    When the combination requires HTF alignment, pass ``candles_1h`` /
    ``candles_4h`` (closed bars only). Missing HTF series fail closed (no entries).

    ``candles_15m`` is optional confirmation input for COMBO_02_V2 and
    COMBO_03_TRANSITION research families.

    ``prebuilt_context`` optionally supplies a precomputed COMBO_02_V2 /
    COMBO_03 context (validation memory control). When omitted, context is
    built inside the walk as before.

    ``progress_callback`` receives dict heartbeats (phase / bars / trades) for
    UI job polling — does not affect trade results.
    """
    t0 = time.perf_counter()
    combo = (
        get_combination(combination)
        if isinstance(combination, str)
        else combination
    )
    if combo is None:
        return {"status": "ERROR", "reason": f"Unknown combination {combination}"}

    rcfg = research_config or ResearchConfig()
    scfg = signal_config or SignalConfig()
    from app.research.sd_structure.evaluate import clear_structure_cache

    clear_structure_cache()
    # Keep HTF references (no deep copy) — gate must not mutate series.
    from app.research.ohlcv_continuity import prepare_series

    htf_1h = prepare_series(candles_1h) if candles_1h is not None else None
    htf_4h = prepare_series(candles_4h) if candles_4h is not None else None
    series_15m = prepare_series(candles_15m) if candles_15m is not None else None
    series_5m = prepare_series(candles_5m) if candles_5m is not None else None
    series_1d = prepare_series(candles_1d) if candles_1d is not None else None
    # Setup TF may itself be 1h/4h — reuse when HTF series not supplied.
    tf_l = (timeframe or "").lower()
    if combo.require_htf_alignment:
        if htf_1h is None and tf_l == "1h":
            htf_1h = prepare_series(candles)
        if htf_4h is None and tf_l == "4h":
            htf_4h = prepare_series(candles)

    def _progress(phase: str, *, bars: int = 0, total: int = 0, trades_n: int = 0, **extra: Any) -> None:
        payload = {
            "phase": phase,
            "bars_processed": bars,
            "total_bars": total,
            "trades": trades_n,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "elapsed_seconds": time.perf_counter() - t0,
            **extra,
        }
        emit_phase(
            phase,
            job_id=job_id,
            symbol=symbol.upper(),
            timeframe=timeframe,
            elapsed_seconds=payload["elapsed_seconds"],
            bars_processed=bars,
            total_bars=total,
            trades=trades_n,
        )
        if progress_callback is not None:
            try:
                progress_callback(payload)
            except Exception:  # noqa: BLE001
                pass
    quality = verify_ohlcv(candles, timeframe, config=rcfg)
    if quality["status"] == "INSUFFICIENT_DATA":
        return {
            "status": "INSUFFICIENT_DATA",
            "data_quality": quality,
            "combination_id": combo.combination_id,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "sample_size": 0,
            "result": compute_metrics(
                [],
                combination_id=combo.combination_id,
                description=combo.description,
                symbol=symbol.upper(),
                timeframe=timeframe,
                data_coverage=quality.get("coverage_ratio"),
                data_quality="INSUFFICIENT_DATA",
                condition_definition=combo.to_dict(),
                period_label=period_label,
            ).to_dict(),
            "trades": [],
            "elapsed_seconds": time.perf_counter() - t0,
            "candles_processed": 0,
            "setups_processed": 0,
            "disclaimer": "INSUFFICIENT_DATA — no silent fill, no TF substitution",
        }

    from app.research.ohlcv_continuity import bars_adjacent, prepare_series, step_seconds

    series = prepare_series(candles)
    bar_step = step_seconds(timeframe)
    start = index_start if index_start is not None else 0
    end = index_end if index_end is not None else len(series)
    start = max(0, start)
    end = min(len(series), end)
    asset_group = classify_asset_group(symbol, market_cap, config=rcfg)

    trades: list[ResearchTrade] = []
    open_trade: ResearchTrade | None = None
    setups = 0
    candles_processed = 0
    min_bars = rcfg.min_bars
    loop_start = max(min_bars, start)
    walk_total = max(0, end - loop_start)

    # Hot-path caches: reuse engines + incremental swings (O(n) not O(n²)).
    local_cfg = _clone_signal_config(
        scfg,
        rcfg,
        require_htf_alignment=bool(combo.require_htf_alignment),
    )
    signal_engine = SignalEngine(local_cfg)
    sd_engine = SupplyDemandEngine(get_settings().indicators_config)
    swing_cfg = local_cfg.swing_for(timeframe)
    _, highs_arr, lows_arr, closes_arr, vols_arr = series_ohlcv(series)
    atr_arr = atr_series(highs_arr, lows_arr, closes_arr, local_cfg.atr_period)
    volume_avg_ref_arr = _rolling_mean_inclusive(vols_arr, 20)
    swings = detect_swings(
        series,
        left=swing_cfg.swing_left_bars,
        right=swing_cfg.swing_right_bars,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=local_cfg.atr_period,
        minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
        as_of_index=max(loop_start - 1, 0),
        highs=highs_arr,
        lows=lows_arr,
        closes=closes_arr,
    ) if loop_start > 0 else []
    prev_break = False
    prev_swing_n = len(swings)
    htf_trend_cache: dict[tuple[str, int], str] = {}
    htf_idx_1h_map: list[int | None] | None = None
    htf_idx_4h_map: list[int | None] | None = None
    htf_precompute_seconds = 0.0
    # Observability side-channel only — never consulted for entry decisions.
    entry_diagnostics: dict[int, dict[str, Any]] = {}
    for _pre_i in range(start, loop_start):
        entry_diagnostics[_pre_i] = record_for_bar(
            _pre_i, classified=classify_insufficient_bars_before_walk()
        )

    v2_context = prebuilt_context
    from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
    from app.research.combo03_transition.variants import COMBO_03_FAMILY
    from app.research.sd_structure.variants import MAX_HOLD_BARS, SD_STRUCTURE_FAMILY

    if v2_context is None and combo.combination_id in COMBO_V2_FAMILY:
        from app.research.combo02_v2.context import Combo02V2Context

        v2_context = Combo02V2Context.build(
            symbol=symbol,
            setup_timeframe=timeframe,
            setup_candles=series,
            candles_15m=series_15m,
        )
    elif v2_context is None and combo.combination_id in COMBO_03_FAMILY:
        from app.research.combo03_transition.context import Combo03Context

        v2_context = Combo03Context.build(
            symbol=symbol,
            setup_timeframe=timeframe,
            setup_candles=series,
            candles_15m=series_15m,
        )

    _progress(BACKTEST_START, bars=0, total=walk_total, trades_n=0)
    _progress(STRUCTURE_SCAN_START, bars=0, total=walk_total, trades_n=0)

    # Precompute closed-bar HTF trends once (same labels as on-demand trend_at_as_of).
    if combo.require_htf_alignment and (htf_1h is not None or htf_4h is not None):
        t_htf = time.perf_counter()
        _progress(HTF_PRECOMPUTE_START, bars=0, total=walk_total, trades_n=0)
        closed_htf = bool(getattr(combo, "htf_require_fully_closed", False))
        with research_stage_profiler.time("htf_precompute"):
            if htf_1h:
                precompute_htf_trend_cache(
                    htf_1h,
                    timeframe="1h",
                    symbol=symbol,
                    config=local_cfg,
                    cache=htf_trend_cache,
                )
                if closed_htf:
                    htf_idx_1h_map = build_htf_as_of_index_map_fully_closed(
                        series,
                        htf_1h,
                        setup_timeframe=timeframe,
                        htf_timeframe="1h",
                    )
                else:
                    htf_idx_1h_map = build_htf_as_of_index_map(series, htf_1h)
            if htf_4h:
                precompute_htf_trend_cache(
                    htf_4h,
                    timeframe="4h",
                    symbol=symbol,
                    config=local_cfg,
                    cache=htf_trend_cache,
                )
                if closed_htf:
                    htf_idx_4h_map = build_htf_as_of_index_map_fully_closed(
                        series,
                        htf_4h,
                        setup_timeframe=timeframe,
                        htf_timeframe="4h",
                    )
                else:
                    htf_idx_4h_map = build_htf_as_of_index_map(series, htf_4h)
        htf_precompute_seconds = time.perf_counter() - t_htf
        _progress(
            HTF_PRECOMPUTE_END,
            bars=0,
            total=walk_total,
            trades_n=0,
            htf_cache_entries=len(htf_trend_cache),
            htf_precompute_seconds=round(htf_precompute_seconds, 6),
            htf_require_fully_closed=closed_htf,
        )

    last_hb = time.perf_counter()
    hb_every_bars = 500
    # Break levels change only when a new swing confirms. Reuse them across
    # the bars in between instead of re-inferring trend on every candle.
    bos_level_n = -1
    bos_levels: tuple[str, float, float] | None = None

    def _manage_open_trade(i: int) -> bool:
        """Test bar ``i`` against the open trade; True when it closed there."""
        nonlocal open_trade, prev_break
        _simulate_partial = True
        with research_stage_profiler.time("trade_simulation"):
            c = series[i]
            high = float(c.get("high") or c.get("h") or 0)
            low = float(c.get("low") or c.get("l") or 0)
            targets = [t for t in (open_trade.tp1, open_trade.tp2, open_trade.tp3) if t is not None]
            # accumulate excursions
            if not hasattr(open_trade, "_highs"):
                open_trade._highs = []  # type: ignore[attr-defined]
                open_trade._lows = []  # type: ignore[attr-defined]
            open_trade._highs.append(high)  # type: ignore[attr-defined]
            open_trade._lows.append(low)  # type: ignore[attr-defined]
            outcome, exit_px, ambiguous = resolve_intrabar_outcome(
                direction=open_trade.direction,
                high=high,
                low=low,
                stop=open_trade.stop_price,
                targets=targets,
                handling=rcfg.ambiguous_handling,
            )
            if outcome is None and combo.combination_id in SD_STRUCTURE_FAMILY:
                if i - open_trade.entry_index >= MAX_HOLD_BARS:
                    outcome = "TIME_STOP"
                    exit_px = float(c.get("close") or c.get("c") or 0)
                    ambiguous = False
        if outcome:
            open_trade.exit_index = i
            ts = candle_time(c)
            open_trade.exit_time = ts.isoformat() if ts else None
            open_trade.exit_price = exit_px
            open_trade.outcome = outcome
            open_trade.ambiguous = ambiguous
            open_trade.holding_bars = i - open_trade.entry_index
            risk = abs(open_trade.entry_price - open_trade.stop_price)
            if outcome == "AMBIGUOUS_INTRABAR" and rcfg.ambiguous_handling == AMBIGUOUS_EXCLUDE:
                open_trade.r_multiple = None
            elif outcome == "AMBIGUOUS_INTRABAR" and rcfg.ambiguous_handling == AMBIGUOUS_NEUTRAL:
                open_trade.r_multiple = 0.0
            elif exit_px is not None and risk > 0:
                if open_trade.direction == "LONG":
                    open_trade.r_multiple = (exit_px - open_trade.entry_price) / risk
                else:
                    open_trade.r_multiple = (open_trade.entry_price - exit_px) / risk
            if outcome == "SL":
                open_trade.time_to_sl = open_trade.holding_bars
            elif outcome.startswith("TP"):
                # Only the level actually exited at.
                setattr(open_trade, f"time_to_tp{int(outcome[-1])}", open_trade.holding_bars)
            mae, mfe, mae_r, mfe_r = _excursions(
                direction=open_trade.direction,
                entry=open_trade.entry_price,
                stop=open_trade.stop_price,
                highs=open_trade._highs,  # type: ignore[attr-defined]
                lows=open_trade._lows,  # type: ignore[attr-defined]
            )
            open_trade.mae, open_trade.mfe = mae, mfe
            open_trade.mae_r, open_trade.mfe_r = mae_r, mfe_r
            if not (
                open_trade.outcome == "AMBIGUOUS_INTRABAR"
                and rcfg.ambiguous_handling == AMBIGUOUS_EXCLUDE
            ):
                trades.append(open_trade)
            open_trade = None
            prev_break = False
            return True
        return False

    for i in range(loop_start, end):
        if should_cancel is not None and i % 64 == 0 and should_cancel():
            return {
                "status": "CANCELLED",
                "combination_id": combo.combination_id,
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "sample_size": 0,
                "result": {},
                "trades": [],
                "entry_diagnostics": entry_diagnostics,
                "elapsed_seconds": time.perf_counter() - t0,
                "candles_processed": candles_processed,
                "setups_processed": setups,
                "htf_precompute_seconds": htf_precompute_seconds,
                "htf_cache_entries": len(htf_trend_cache),
                "disclaimer": "CANCELLED — walk aborted",
            }
        candles_processed += 1
        now = time.perf_counter()
        if (
            candles_processed == 1
            or candles_processed % hb_every_bars == 0
            or (now - last_hb) >= 1.0
        ):
            last_hb = now
            closed_n = sum(1 for t in trades if t.outcome != "OPEN")
            if open_trade is not None:
                closed_n += 0
            _progress(
                STRUCTURE_SCAN_HEARTBEAT
                if candles_processed < walk_total
                else BACKTEST_HEARTBEAT,
                bars=candles_processed,
                total=walk_total,
                trades_n=len(trades),
            )
        with research_stage_profiler.time("swing_extend"):
            swings = extend_swings(
                swings,
                series,
                left=swing_cfg.swing_left_bars,
                right=swing_cfg.swing_right_bars,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=local_cfg.atr_period,
                minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
                as_of_index=i,
                highs=highs_arr,
                lows=lows_arr,
                closes=closes_arr,
            )
        swing_grew = len(swings) != prev_swing_n
        prev_swing_n = len(swings)
        if open_trade is not None:
            # A jumped timestamp is not the next bar. Leave the trade open
            # and do not read this bar's high or low as the exit.
            if i > 0 and not bars_adjacent(series[i - 1], series[i], bar_step):
                open_trade.outcome = "OPEN"
                open_trade.exit_price = None
                trades.append(open_trade)
                open_trade = None
                prev_break = False
            else:
                entry_diagnostics[i] = record_for_bar(
                    i, classified=classify_position_bar(exited=_manage_open_trade(i))
                )
                continue

        # COMBO_* that require BOS: skip full structure scan when break impossible.
        # Also skip sticky same-level breaks until structure (swings) updates —
        # otherwise uptrends re-evaluate thousands of identical BOS bars.
        # Pullback combinations confirm on later bars of that same break, so
        # they must keep evaluating until the close is back inside the level.
        if combo.require_bos:
            with research_stage_profiler.time("bos_prefilter"):
                if len(swings) != bos_level_n:
                    bos_level_n = len(swings)
                    bos_levels = _bos_break_levels(swings, i)
                if bos_levels is None:
                    brk = False
                else:
                    trend_state, high_px, low_px = bos_levels
                    brk = _close_breaks_level(
                        trend_state,
                        float(closes_arr[i]),
                        high_px,
                        low_px,
                        direction_filter,
                    )
            if not brk:
                prev_break = False
                entry_diagnostics[i] = record_for_bar(
                    i, classified=classify_bos_prefilter_skip()
                )
                continue
            if prev_break and not swing_grew and not combo.require_pullback:
                entry_diagnostics[i] = record_for_bar(
                    i, classified=classify_sticky_bos_skip()
                )
                continue
            prev_break = True

        with research_stage_profiler.time("evaluate_combination_at_bar"):
            setup = evaluate_combination_at_bar(
                symbol=symbol,
                timeframe=timeframe,
                candles=series,
                as_of_index=i,
                combination=combo,
                signal_config=scfg,
                research_config=rcfg,
                signal_engine=signal_engine,
                sd_engine=sd_engine,
                swings=swings,
                compute_sd=bool(combo.require_sd),
                atr_value=atr_arr[i],
                volumes=vols_arr,
                avg_volume_ref=float(volume_avg_ref_arr[i]),
                candles_1h=htf_1h,
                candles_4h=htf_4h,
                candles_15m=series_15m,
                candles_5m=series_5m,
                candles_1d=series_1d,
                htf_trend_cache=htf_trend_cache,
                htf_idx_1h_map=htf_idx_1h_map,
                htf_idx_4h_map=htf_idx_4h_map,
                v2_context=v2_context,
            )
        if setup.get("status") not in (
            "LONG_ENTRY_CANDIDATE",
            "SHORT_ENTRY_CANDIDATE",
            "ENTRY_CANDIDATE",
        ):
            entry_diagnostics[i] = record_for_bar(
                i,
                classified=classify_from_eval_setup(setup),
                signal_time=setup.get("signal_time"),
            )
            continue
        direction = str(setup["direction"])
        if direction_filter and direction_filter.upper() in ("LONG", "SHORT"):
            if direction != direction_filter.upper():
                entry_diagnostics[i] = record_for_bar(
                    i,
                    classified=classify_direction_filter_mismatch(
                        direction, direction_filter
                    ),
                    signal_time=setup.get("signal_time"),
                )
                continue
        setups += 1
        entry_diagnostics[i] = record_for_bar(
            i,
            classified=classify_from_eval_setup(setup),
            signal_time=setup.get("signal_time"),
        )
        open_trade = ResearchTrade(
            symbol=symbol.upper(),
            timeframe=timeframe,
            combination_id=combo.combination_id,
            entry_index=i,
            signal_time=setup.get("signal_time"),
            direction=direction,
            entry_price=float(setup["entry_price"]),
            stop_price=float(setup["stop_price"]),
            tp1=setup.get("tp1"),
            tp2=setup.get("tp2"),
            tp3=setup.get("tp3"),
            rr=setup.get("rr"),
            period_label=period_label,
            asset_group=asset_group,
            condition_snapshot={
                **(setup.get("gates") or {}),
                "entry_type": setup.get("entry_type"),
                **(
                    {
                        "htf_alignment": (setup.get("htf") or {}).get("htf_alignment"),
                        "trend_4h": (setup.get("htf") or {}).get("trend_4h"),
                        "trend_1h": (setup.get("htf") or {}).get("trend_1h"),
                        "htf_require_fully_closed": bool(
                            getattr(combo, "htf_require_fully_closed", False)
                        ),
                    }
                    if combo.require_htf_alignment
                    else {}
                ),
                **(
                    {
                        "bos_level": (setup.get("bos") or {}).get("broken_level"),
                        "bos_direction": (setup.get("bos") or {}).get("direction"),
                    }
                    if isinstance(setup.get("bos"), dict)
                    else {}
                ),
                **(
                    {
                        "playbook": setup.get("playbook"),
                        "regime": setup.get("regime"),
                        "event": setup.get("event"),
                        "confirmation": setup.get("confirmation"),
                        "htf_state": setup.get("htf_state"),
                        "v2_diagnostics": setup.get("v2_diagnostics"),
                    }
                    if combo.combination_id in COMBO_V2_FAMILY
                    else {}
                ),
                **(
                    {
                        "playbook": setup.get("playbook"),
                        "regime": setup.get("regime"),
                        "event": setup.get("event"),
                        "confirmation": setup.get("confirmation"),
                        "htf_state": setup.get("htf_state"),
                        "sweep": setup.get("sweep"),
                        "combo03_diagnostics": setup.get("combo03_diagnostics"),
                    }
                    if combo.combination_id in COMBO_03_FAMILY
                    else {}
                ),
                **(
                    {
                        "playbook": setup.get("playbook"),
                        "sd_structure": setup.get("sd_structure"),
                    }
                    if combo.combination_id in SD_STRUCTURE_FAMILY
                    else {}
                ),
            },
        )
        if setup.get("entry_type") == "NEXT_OPEN":
            # Filled at this bar's open, so its high/low print after the
            # fill: manage the trade on its own fill bar. A same-bar exit has
            # holding_bars == 0; the bar keeps its entry diagnostics record.
            _manage_open_trade(i)

    # Drop still-open trades from closed metrics (record as OPEN for transparency)
    if open_trade is not None:
        open_trade.outcome = "OPEN"
        trades.append(open_trade)

    _progress(
        SIGNAL_GENERATION_END,
        bars=candles_processed,
        total=walk_total,
        trades_n=len(trades),
    )

    closed_for_metrics = [t for t in trades if t.outcome != "OPEN"]
    period_start = None
    period_end = None
    if series:
        ts0 = candle_time(series[start] if start < len(series) else series[0])
        ts1 = candle_time(series[end - 1] if end > 0 else series[-1])
        period_start = ts0.isoformat() if ts0 else None
        period_end = ts1.isoformat() if ts1 else None

    _progress(METRICS_START, bars=candles_processed, total=walk_total, trades_n=len(trades))
    result = compute_metrics(
        closed_for_metrics,
        combination_id=combo.combination_id,
        description=combo.description,
        symbol=symbol.upper(),
        timeframe=timeframe,
        direction=direction_filter or "ALL",
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        data_coverage=quality.get("coverage_ratio"),
        data_quality=quality.get("data_quality", "OK"),
        condition_definition=combo.to_dict(),
    )
    result.by_timeframe = {
        timeframe: {
            "sample_size": result.sample_size,
            "average_R": result.average_R,
            "expectancy_R": result.expectancy_R,
            "profit_factor": result.profit_factor,
        }
    }
    _progress(METRICS_END, bars=candles_processed, total=walk_total, trades_n=len(trades))
    _progress(BACKTEST_END, bars=candles_processed, total=walk_total, trades_n=len(trades))

    return {
        "status": "OK",
        "combination_id": combo.combination_id,
        "combination": combo.to_dict(),
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "signal_engine_version": SIGNAL_ENGINE_VERSION,
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "configuration_hash": rcfg.configuration_hash(),
        "configuration": rcfg.configuration_dict(),
        "data_quality": quality,
        "result": result.to_dict(),
        "trades": [t.to_dict() for t in trades],
        # Observability only — never used for signal/trade decisions.
        "entry_diagnostics": entry_diagnostics,
        "elapsed_seconds": time.perf_counter() - t0,
        "candles_processed": candles_processed,
        "setups_processed": setups,
        "sample_size": result.sample_size,
        "htf_precompute_seconds": htf_precompute_seconds,
        "htf_cache_entries": len(htf_trend_cache),
        "label": "RESEARCH_COMPARISON",
        "disclaimer": result.disclaimer,
        "execution_model": rcfg.execution_model,
        "htf_fully_closed": bool(rcfg.htf_fully_closed),
    }


def run_oos_split_backtest(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    combination: CombinationDefinition | str,
    *,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
    market_cap: float | None = None,
    direction_filter: str | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Train / validation / out-of-sample separation (chronological).

    Parameter testing must NOT use OUT_OF_SAMPLE_PERIOD for optimization.
    """
    rcfg = research_config or ResearchConfig()
    n = len(candles)
    splits = split_period_indices(
        n,
        train_fraction=rcfg.train_fraction,
        validation_fraction=rcfg.validation_fraction,
        oos_fraction=rcfg.oos_fraction,
    )
    out: dict[str, Any] = {
        "splits": splits,
        "periods": {},
        "note": (
            "OUT_OF_SAMPLE_PERIOD is evaluation-only. "
            "Do not optimize parameters using this period."
        ),
    }
    for label, (a, b) in splits.items():
        # Each period still needs lookback history before `a` for indicators,
        # but entries are only accepted when entry_index is in [a, b).
        period_candles = list(candles[:b])
        run = run_combination_backtest(
            symbol,
            timeframe,
            period_candles,
            combination,
            signal_config=signal_config,
            research_config=rcfg,
            market_cap=market_cap,
            period_label=label,
            index_start=a,
            index_end=b,
            direction_filter=direction_filter,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
        )
        # Filter trades to those with entry inside the period window
        trades = [
            t
            for t in run.get("trades") or []
            if a <= int(t.get("entry_index", -1)) < b
        ]
        combo_id = run.get("combination_id") or (
            combination if isinstance(combination, str) else combination.combination_id
        )
        desc = (run.get("combination") or {}).get("description") or ""
        from app.research.schemas import ResearchTrade as RT

        trade_objs = [RT(**{k: v for k, v in t.items() if k in RT.__dataclass_fields__}) for t in trades]
        metrics = compute_metrics(
            [t for t in trade_objs if t.outcome != "OPEN"],
            combination_id=str(combo_id),
            description=desc,
            symbol=symbol.upper(),
            timeframe=timeframe,
            period_label=label,
            condition_definition=run.get("combination") or {},
        )
        out["periods"][label] = {
            "index_range": [a, b],
            "sample_size": metrics.sample_size,
            "result": metrics.to_dict(),
            "status": run.get("status"),
        }
    return out


def run_walk_forward(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    combination: CombinationDefinition | str,
    *,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
    market_cap: float | None = None,
    direction_filter: str | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Optional walk-forward. Windows stored separately — not pooled incorrectly."""
    rcfg = research_config or ResearchConfig()
    windows = walk_forward_windows(
        len(candles),
        train_bars=rcfg.walk_forward_train_bars,
        test_bars=rcfg.walk_forward_test_bars,
    )
    results = []
    for w in windows:
        # Test window only for evaluation metrics
        test_run = run_combination_backtest(
            symbol,
            timeframe,
            list(candles[: w["test_end"]]),
            combination,
            signal_config=signal_config,
            research_config=rcfg,
            market_cap=market_cap,
            period_label="WALK_FORWARD_TEST",
            index_start=w["test_start"],
            index_end=w["test_end"],
            direction_filter=direction_filter,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
        )
        results.append(
            {
                **w,
                "sample_size": test_run.get("sample_size", 0),
                "result": test_run.get("result"),
                "status": test_run.get("status"),
            }
        )
    return {
        "windows": results,
        "window_count": len(results),
        "note": "Each walk-forward window reported separately; do not merge incorrectly.",
        "label": "RESEARCH_COMPARISON",
    }


def compare_combinations(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    combination_ids: Sequence[str] | None = None,
    *,
    signal_config: SignalConfig | None = None,
    research_config: ResearchConfig | None = None,
    market_cap: float | None = None,
    direction_filter: str | None = None,
    index_start: int | None = None,
    index_end: int | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare combinations. Never ranks or labels a winner."""
    from app.research.bos_combinations import COMBINATIONS
    from app.research.query_utils import (
        DATASET_ID,
        DATASET_LABEL,
        OTHER_DATASET_ID,
        normalize_research_symbol,
        normalize_research_timeframe,
    )

    ids = list(combination_ids) if combination_ids else list(COMBINATIONS.keys())
    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    rows = []
    for cid in ids:
        run = run_combination_backtest(
            sym,
            tf,
            candles,
            cid,
            signal_config=signal_config,
            research_config=research_config,
            market_cap=market_cap,
            direction_filter=direction_filter,
            index_start=index_start,
            index_end=index_end,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
        )
        res = run.get("result") or {}
        sample = int(res.get("sample_size") or 0)
        rows.append(
            {
                "combination_id": cid,
                "name": (run.get("combination") or {}).get("name"),
                "description": (run.get("combination") or {}).get("description"),
                "sample_size": sample,
                "tp1_hit_rate": res.get("tp1_hit_rate") if sample else None,
                "tp2_hit_rate": res.get("tp2_hit_rate") if sample else None,
                "tp3_hit_rate": res.get("tp3_hit_rate") if sample else None,
                "sl_rate": res.get("sl_rate") if sample else None,
                "average_R": res.get("average_R") if sample else None,
                "expectancy_R": res.get("expectancy_R") if sample else None,
                "profit_factor": res.get("profit_factor") if sample else None,
                "max_drawdown_R": res.get("max_drawdown_R") if sample else None,
                "average_MAE_R": res.get("average_MAE_R") if sample else None,
                "average_MFE_R": res.get("average_MFE_R") if sample else None,
                "status": run.get("status"),
            }
        )
    return {
        "label": "RESEARCH_COMPARISON",
        "title": "Combination Comparison",
        "dataset": DATASET_LABEL,
        "dataset_id": DATASET_ID,
        "not_dataset": OTHER_DATASET_ID,
        "symbol": sym,
        "timeframe": tf,
        "combinations_tested": len(rows),
        "rows": rows,
        "disclaimer": (
            "Research comparison only. No ranking, no best/winner label, "
            "no future profitability claim. "
            "Dataset is BOS Combination Research — not Candle-1/Candle-2 V2."
        ),
    }
