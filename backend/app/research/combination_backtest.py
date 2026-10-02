"""Walk-forward combination backtest with no look-ahead.

Uses as_of_index path from existing engines. Future candles are inspected
ONLY after entry for outcome / MAE / MFE. Same-candle SL+TP → AMBIGUOUS_INTRABAR.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import CombinationDefinition, get_combination
from app.research.combination_engine import evaluate_combination_at_bar
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
from app.research.data_quality import verify_ohlcv
from app.research.metrics import compute_metrics
from app.research.schemas import ResearchTrade
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig


def _bar_hits_levels(
    *,
    direction: str,
    high: float,
    low: float,
    stop: float,
    targets: list[float],
) -> tuple[bool, bool, int | None]:
    """Return (stop_hit, any_tp_hit, first_tp_index)."""
    stop_hit = (low <= stop) if direction == "LONG" else (high >= stop)
    tp_idx = None
    for i, tp in enumerate(targets):
        if direction == "LONG" and high >= tp:
            tp_idx = i
            break
        if direction == "SHORT" and low <= tp:
            tp_idx = i
            break
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
        # time_to metrics
        bars = trade.holding_bars
        if outcome == "SL":
            trade.time_to_sl = bars
        elif outcome == "TP1":
            trade.time_to_tp1 = bars
        elif outcome == "TP2":
            trade.time_to_tp1 = bars  # must have passed TP1 zone conceptually
            trade.time_to_tp2 = bars
        elif outcome == "TP3":
            trade.time_to_tp1 = bars
            trade.time_to_tp2 = bars
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


def run_combination_backtest(
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
) -> dict[str, Any]:
    """Run one combination on one symbol/timeframe series.

    Read-only: does not write to live signal store/cache.
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

    series = list(candles)
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

    for i in range(loop_start, end):
        candles_processed += 1
        if open_trade is not None:
            # Manage with current bar (after entry)
            _simulate_partial = True
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
                    n = int(outcome[-1])
                    open_trade.time_to_tp1 = open_trade.holding_bars
                    if n >= 2:
                        open_trade.time_to_tp2 = open_trade.holding_bars
                    if n >= 3:
                        open_trade.time_to_tp3 = open_trade.holding_bars
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
            continue

        setup = evaluate_combination_at_bar(
            symbol=symbol,
            timeframe=timeframe,
            candles=series,
            as_of_index=i,
            combination=combo,
            signal_config=scfg,
            research_config=rcfg,
        )
        if setup.get("status") not in (
            "LONG_ENTRY_CANDIDATE",
            "SHORT_ENTRY_CANDIDATE",
            "ENTRY_CANDIDATE",
        ):
            continue
        direction = str(setup["direction"])
        if direction_filter and direction_filter.upper() in ("LONG", "SHORT"):
            if direction != direction_filter.upper():
                continue
        setups += 1
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
            },
        )

    # Drop still-open trades from closed metrics (record as OPEN for transparency)
    if open_trade is not None:
        open_trade.outcome = "OPEN"
        trades.append(open_trade)

    closed_for_metrics = [t for t in trades if t.outcome != "OPEN"]
    period_start = None
    period_end = None
    if series:
        ts0 = candle_time(series[start] if start < len(series) else series[0])
        ts1 = candle_time(series[end - 1] if end > 0 else series[-1])
        period_start = ts0.isoformat() if ts0 else None
        period_end = ts1.isoformat() if ts1 else None

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
        "elapsed_seconds": time.perf_counter() - t0,
        "candles_processed": candles_processed,
        "setups_processed": setups,
        "sample_size": result.sample_size,
        "label": "RESEARCH_COMPARISON",
        "disclaimer": result.disclaimer,
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
