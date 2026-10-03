"""Walk-forward strategy backtest runner — no look-ahead.

Reuses combination_backtest.resolve_intrabar_outcome (CONSERVATIVE SL-first).
Processes one symbol at a time; does not load the full universe into memory.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.config import (
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    SIGNAL_ENGINE_VERSION,
    StrategyResearchConfig,
)
from app.config import get_settings
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.research.bos_strategy_comparison.data_quality import series_quality_report
from app.research.bos_strategy_comparison.engine import (
    _clone_signal_config,
    evaluate_strategy_at_bar,
)
from app.research.bos_strategy_comparison.metrics import (
    apply_net_r,
    by_htf_breakdown,
    by_symbol_breakdown,
    by_year_breakdown,
    chronological_splits,
    compute_direction_metrics,
    cost_sensitivity,
    metrics_summary_dict,
)
from app.research.bos_strategy_comparison.schemas import StrategyResult, StrategyTrade
from app.research.bos_strategy_comparison.strategies import (
    CONTRIBUTION_LADDER,
    STRATEGIES,
    StrategyDefinition,
    get_strategy,
)
from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import (
    AMBIGUOUS_EXCLUDE,
    AMBIGUOUS_NEUTRAL,
    walk_forward_windows,
)
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine


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


def _manage_open_trade(
    open_trade: StrategyTrade,
    *,
    i: int,
    candle: Mapping[str, Any],
    handling: str,
) -> StrategyTrade | None:
    """Update open trade with bar i. Returns closed trade or None if still open."""
    high = float(candle.get("high") or candle.get("h") or 0)
    low = float(candle.get("low") or candle.get("l") or 0)
    targets = [t for t in (open_trade.tp1, open_trade.tp2, open_trade.tp3) if t is not None]
    if not hasattr(open_trade, "_highs"):
        open_trade._highs = []  # type: ignore[attr-defined]
        open_trade._lows = []  # type: ignore[attr-defined]
    open_trade._highs.append(high)  # type: ignore[attr-defined]
    open_trade._lows.append(low)  # type: ignore[attr-defined]
    outcome, exit_px, ambiguous = resolve_intrabar_outcome(
        direction=open_trade.direction,
        high=high,
        low=low,
        stop=open_trade.sl,
        targets=targets,
        handling=handling,
    )
    if not outcome:
        return None
    open_trade.exit_index = i
    ts = candle_time(candle)
    open_trade.exit_time = ts.isoformat() if ts else None
    open_trade.exit_price = exit_px
    open_trade.exit_reason = outcome
    open_trade.ambiguous = ambiguous
    open_trade.holding_period = i - open_trade.entry_index
    risk = abs(open_trade.entry_price - open_trade.sl)
    if outcome == "AMBIGUOUS_INTRABAR" and handling == AMBIGUOUS_EXCLUDE:
        open_trade.gross_R = None
    elif outcome == "AMBIGUOUS_INTRABAR" and handling == AMBIGUOUS_NEUTRAL:
        open_trade.gross_R = 0.0
    elif exit_px is not None and risk > 0:
        if open_trade.direction == "LONG":
            open_trade.gross_R = (exit_px - open_trade.entry_price) / risk
        else:
            open_trade.gross_R = (open_trade.entry_price - exit_px) / risk
    mae, mfe, mae_r, mfe_r = _excursions(
        direction=open_trade.direction,
        entry=open_trade.entry_price,
        stop=open_trade.sl,
        highs=open_trade._highs,  # type: ignore[attr-defined]
        lows=open_trade._lows,  # type: ignore[attr-defined]
    )
    open_trade.MAE, open_trade.MFE = mae, mfe
    open_trade.MAE_R, open_trade.MFE_R = mae_r, mfe_r
    if (
        open_trade.exit_reason == "AMBIGUOUS_INTRABAR"
        and handling == AMBIGUOUS_EXCLUDE
    ):
        return open_trade  # caller may drop
    return open_trade


def _trade_from_setup(
    setup: Mapping[str, Any],
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    entry_index: int,
    period_label: str,
) -> StrategyTrade:
    return StrategyTrade(
        strategy_id=strategy_id,
        symbol=symbol.upper(),
        direction=str(setup["direction"]),
        timeframe=timeframe,
        entry_index=entry_index,
        entry_time=setup.get("signal_time"),
        entry_price=float(setup["entry_price"]),
        sl=float(setup["sl"]),
        tp1=setup.get("tp1"),
        tp2=setup.get("tp2"),
        tp3=setup.get("tp3"),
        trend_4h=setup.get("trend_4h"),
        trend_1h=setup.get("trend_1h"),
        trend_15m=setup.get("trend_15m"),
        trend_5m=setup.get("trend_5m"),
        bos_direction=setup.get("bos_direction"),
        bos_timestamp=setup.get("bos_timestamp"),
        impulse_state=setup.get("impulse_state"),
        pullback_state=setup.get("pullback_state"),
        retest_state=setup.get("retest_state"),
        sd_state=setup.get("sd_state"),
        htf_alignment=setup.get("htf_alignment"),
        regime=setup.get("regime") or "REGIME_UNAVAILABLE",
        stop_reason=setup.get("stop_reason"),
        risk_per_unit=setup.get("risk_per_unit"),
        structural_invalidation=str(setup.get("structural_invalidation"))
        if setup.get("structural_invalidation") is not None
        else None,
        entry_type=setup.get("entry_type"),
        period_label=period_label,
        condition_snapshot=dict(setup.get("gates") or {}),
    )


def _finalize_strategy_run(
    *,
    strat: StrategyDefinition,
    symbol: str,
    timeframe: str,
    trades: list[StrategyTrade],
    quality: dict[str, Any],
    rcfg: StrategyResearchConfig,
    period_start: str | None,
    period_end: str | None,
    candles_processed: int,
    setups: int,
    elapsed: float,
    status: str = "OK",
) -> dict[str, Any]:
    apply_net_r(
        trades,
        taker_fee=rcfg.taker_fee,
        maker_fee=rcfg.maker_fee,
        slippage_rate=rcfg.slippage_rate,
        cost_multiplier=1.0,
    )
    closed = [t for t in trades if t.exit_reason != "OPEN"]
    all_m = compute_direction_metrics(
        closed, direction="ALL", bootstrap_samples=rcfg.bootstrap_samples
    )
    long_m = compute_direction_metrics(closed, direction="LONG")
    short_m = compute_direction_metrics(closed, direction="SHORT")
    return {
        "status": status,
        "strategy_id": strat.strategy_id,
        "strategy": strat.to_dict(),
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "signal_engine_version": SIGNAL_ENGINE_VERSION,
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "configuration_hash": rcfg.configuration_hash(),
        "configuration": rcfg.configuration_dict(),
        "fee_assumptions": {
            "taker_fee": rcfg.taker_fee,
            "maker_fee": rcfg.maker_fee,
            "slippage_rate": rcfg.slippage_rate,
            "same_bar_rule": rcfg.same_bar_rule,
            "leverage": "NOT_APPLIED_IN_R_METRICS",
        },
        "data_quality": quality,
        "period_start": period_start,
        "period_end": period_end,
        "sample_size": all_m.sample_size,
        "result": metrics_summary_dict(all_m),
        "long": metrics_summary_dict(long_m),
        "short": metrics_summary_dict(short_m),
        "trades": [t.to_dict() for t in trades],
        "elapsed_seconds": elapsed,
        "candles_processed": candles_processed,
        "setups_processed": setups,
        "label": "Historical Result",
        "disclaimer": DISCLAIMER,
    }


def run_multi_strategy_backtest(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    strategies: Sequence[StrategyDefinition | str] | None = None,
    *,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
    period_label: str = "FULL",
    index_start: int | None = None,
    index_end: int | None = None,
    direction_filter: str | None = None,
) -> dict[str, Any]:
    """Walk the series once; evaluate all strategies independently (shared structure)."""
    t0 = time.perf_counter()
    rcfg = research_config or StrategyResearchConfig()
    scfg = signal_config or SignalConfig()
    local_cfg = _clone_signal_config(scfg, rcfg)
    engine = SignalEngine(local_cfg)
    sd_engine = SupplyDemandEngine(get_settings().indicators_config)
    trend_cache: dict[tuple[str, int], str] = {}

    strat_list: list[StrategyDefinition] = []
    raw = list(strategies) if strategies is not None else list(STRATEGIES.keys())
    for s in raw:
        st = get_strategy(s) if isinstance(s, str) else s
        if st is not None:
            strat_list.append(st)
    if not strat_list:
        return {"status": "ERROR", "reason": "No strategies", "strategies": {}}

    quality = series_quality_report(candles, timeframe, config=rcfg)
    if quality.get("status") == "INSUFFICIENT_DATA":
        empty = compute_direction_metrics([], direction="ALL")
        return {
            "status": "INSUFFICIENT_DATA",
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "data_quality": quality,
            "strategies": {
                st.strategy_id: {
                    "status": "INSUFFICIENT_DATA",
                    "strategy_id": st.strategy_id,
                    "sample_size": 0,
                    "trades": [],
                    "result": metrics_summary_dict(empty),
                    "disclaimer": DISCLAIMER,
                }
                for st in strat_list
            },
            "elapsed_seconds": time.perf_counter() - t0,
        }

    series = list(candles)
    start = max(0, index_start if index_start is not None else 0)
    end = min(len(series), index_end if index_end is not None else len(series))
    loop_start = max(rcfg.min_bars, start)

    trades_by: dict[str, list[StrategyTrade]] = {st.strategy_id: [] for st in strat_list}
    open_by: dict[str, StrategyTrade | None] = {st.strategy_id: None for st in strat_list}
    setups_by: dict[str, int] = {st.strategy_id: 0 for st in strat_list}
    candles_processed = 0
    needs_sd = any(st.require_sd for st in strat_list)

    for i in range(loop_start, end):
        candles_processed += 1
        c = series[i]

        # Manage open trades first (independent per strategy)
        for sid, ot in list(open_by.items()):
            if ot is None:
                continue
            closed = _manage_open_trade(
                ot, i=i, candle=c, handling=rcfg.ambiguous_handling
            )
            if closed is not None:
                if not (
                    closed.exit_reason == "AMBIGUOUS_INTRABAR"
                    and rcfg.ambiguous_handling == AMBIGUOUS_EXCLUDE
                ):
                    trades_by[sid].append(closed)
                open_by[sid] = None

        # Shared structure once per bar for all flat strategies
        flat = [st for st in strat_list if open_by[st.strategy_id] is None]
        if not flat:
            continue

        tf_analysis = engine.analyze_timeframe(
            symbol, timeframe, series, as_of_index=i
        )
        bos = tf_analysis.get("bos")
        if not (bos and bos.get("state") == "CONFIRMED" and bos.get("direction")):
            continue

        from app.research.bos_strategy_comparison.htf import htf_trends_for_setup_bar

        htf = htf_trends_for_setup_bar(
            symbol=symbol,
            setup_candles=series,
            as_of_index=i,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            config=local_cfg,
            trend_15m=str((tf_analysis.get("trend") or {}).get("trend") or ""),
            trend_cache=trend_cache,
            include_5m=False,
        )

        demand_zone = supply_zone = None
        tf_sd = None
        if needs_sd and any(st.require_sd for st in flat):
            from app.research.combination_engine import detect_zones_as_of

            demand_zone, supply_zone = detect_zones_as_of(
                symbol, timeframe, series, i, sd_engine=sd_engine
            )
            tf_sd = engine.analyze_timeframe(
                symbol,
                timeframe,
                series,
                demand_zone=demand_zone,
                supply_zone=supply_zone,
                as_of_index=i,
            )

        for st in flat:
            setup = evaluate_strategy_at_bar(
                symbol=symbol,
                timeframe=timeframe,
                candles=series,
                as_of_index=i,
                strategy=st,
                signal_config=scfg,
                research_config=rcfg,
                candles_4h=candles_4h,
                candles_1h=candles_1h,
                candles_5m=candles_5m,
                signal_engine=engine,
                sd_engine=sd_engine,
                compute_sd=False,
                trend_cache=trend_cache,
                local_cfg=local_cfg,
                tf_analysis=tf_sd if st.require_sd and tf_sd is not None else tf_analysis,
                htf=htf,
                demand_zone=demand_zone if st.require_sd else None,
                supply_zone=supply_zone if st.require_sd else None,
            )
            if setup.get("status") not in (
                "LONG_ENTRY_CANDIDATE",
                "SHORT_ENTRY_CANDIDATE",
            ):
                continue
            direction = str(setup["direction"])
            if direction_filter and direction_filter.upper() in ("LONG", "SHORT"):
                if direction != direction_filter.upper():
                    continue
            setups_by[st.strategy_id] += 1
            open_by[st.strategy_id] = _trade_from_setup(
                setup,
                strategy_id=st.strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                entry_index=i,
                period_label=period_label,
            )

    for sid, ot in open_by.items():
        if ot is not None:
            ot.exit_reason = "OPEN"
            trades_by[sid].append(ot)

    period_start = period_end = None
    if series:
        ts0 = candle_time(series[start] if start < len(series) else series[0])
        ts1 = candle_time(series[end - 1] if end > 0 else series[-1])
        period_start = ts0.isoformat() if ts0 else None
        period_end = ts1.isoformat() if ts1 else None

    elapsed = time.perf_counter() - t0
    out_strategies = {
        st.strategy_id: _finalize_strategy_run(
            strat=st,
            symbol=symbol,
            timeframe=timeframe,
            trades=trades_by[st.strategy_id],
            quality=quality,
            rcfg=rcfg,
            period_start=period_start,
            period_end=period_end,
            candles_processed=candles_processed,
            setups=setups_by[st.strategy_id],
            elapsed=elapsed,
        )
        for st in strat_list
    }
    return {
        "status": "OK",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "strategies": out_strategies,
        "elapsed_seconds": elapsed,
        "candles_processed": candles_processed,
        "disclaimer": DISCLAIMER,
    }


def run_strategy_backtest(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    strategy: StrategyDefinition | str,
    *,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    signal_config: SignalConfig | None = None,
    research_config: StrategyResearchConfig | None = None,
    period_label: str = "FULL",
    index_start: int | None = None,
    index_end: int | None = None,
    direction_filter: str | None = None,
) -> dict[str, Any]:
    """Run one strategy on one symbol/setup-TF series with HTF context."""
    multi = run_multi_strategy_backtest(
        symbol,
        timeframe,
        candles,
        [strategy],
        candles_4h=candles_4h,
        candles_1h=candles_1h,
        candles_5m=candles_5m,
        signal_config=signal_config,
        research_config=research_config,
        period_label=period_label,
        index_start=index_start,
        index_end=index_end,
        direction_filter=direction_filter,
    )
    if multi.get("status") == "ERROR":
        return multi
    strat = get_strategy(strategy) if isinstance(strategy, str) else strategy
    sid = strat.strategy_id if strat else ""
    payload = (multi.get("strategies") or {}).get(sid)
    if payload is None:
        return {"status": "ERROR", "reason": f"Unknown strategy {strategy}"}
    return payload


def aggregate_strategy_results(
    strategy: StrategyDefinition,
    trade_dicts: Sequence[Mapping[str, Any]],
    *,
    research_config: StrategyResearchConfig | None = None,
    timeframe: str = "15m",
) -> StrategyResult:
    """Aggregate trades across symbols into StrategyResult with breakdowns."""
    rcfg = research_config or StrategyResearchConfig()
    trades = [StrategyTrade(**dict(t)) for t in trade_dicts]  # type: ignore[arg-type]
    closed = [t for t in trades if t.exit_reason and t.exit_reason != "OPEN"]

    all_m = compute_direction_metrics(
        closed, direction="ALL", bootstrap_samples=rcfg.bootstrap_samples
    )
    long_m = compute_direction_metrics(closed, direction="LONG")
    short_m = compute_direction_metrics(closed, direction="SHORT")
    splits = chronological_splits(
        closed,
        train_fraction=rcfg.train_fraction,
        validation_fraction=rcfg.validation_fraction,
        oos_fraction=rcfg.oos_fraction,
    )

    return StrategyResult(
        strategy_id=strategy.strategy_id,
        strategy_name=strategy.strategy_name,
        direction="ALL",
        sample_size=all_m.sample_size,
        win_rate=all_m.win_rate,
        expectancy_R=all_m.expectancy_R,
        profit_factor=all_m.profit_factor,
        max_drawdown_R=all_m.max_drawdown_R,
        metrics=metrics_summary_dict(all_m),
        long=metrics_summary_dict(long_m),
        short=metrics_summary_dict(short_m),
        by_year=by_year_breakdown(closed),
        by_symbol=by_symbol_breakdown(closed, min_sample=rcfg.min_symbol_sample),
        by_timeframe={
            timeframe: {
                "n": all_m.sample_size,
                "expectancy": all_m.expectancy_R,
                "PF": all_m.profit_factor,
                "DD": all_m.max_drawdown_R,
                "win_rate": all_m.win_rate,
                "label": "Historical Result",
            }
        },
        by_htf_alignment=by_htf_breakdown(closed),
        train=metrics_summary_dict(compute_direction_metrics(splits["train"])),
        validation=metrics_summary_dict(
            compute_direction_metrics(splits["validation"])
        ),
        oos=metrics_summary_dict(compute_direction_metrics(splits["oos"])),
        cost_sensitivity=cost_sensitivity(
            closed,
            taker_fee=rcfg.taker_fee,
            maker_fee=rcfg.maker_fee,
            slippage_rate=rcfg.slippage_rate,
            multipliers=rcfg.cost_multipliers,
        ),
        condition_definition=strategy.to_dict(),
    )


def condition_contribution(
    results_by_id: Mapping[str, StrategyResult],
) -> list[dict[str, Any]]:
    """Observational ladder — does not auto-select a live strategy."""
    rows: list[dict[str, Any]] = []
    for sid in CONTRIBUTION_LADDER:
        r = results_by_id.get(sid)
        if r is None:
            continue
        rows.append(
            {
                "strategy_id": sid,
                "strategy_name": r.strategy_name,
                "sample_size": r.sample_size,
                "expectancy": r.expectancy_R,
                "profit_factor": r.profit_factor,
                "max_drawdown_R": r.max_drawdown_R,
                "win_rate": r.win_rate,
                "label": "Historical Result",
                "note": "Observational contribution step — not a deployment decision",
            }
        )
    return rows


def run_walk_forward_strategy(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    strategy: StrategyDefinition | str,
    *,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    research_config: StrategyResearchConfig | None = None,
    signal_config: SignalConfig | None = None,
) -> dict[str, Any]:
    """Walk-forward windows — predefined strategies only, no parameter optimization."""
    rcfg = research_config or StrategyResearchConfig()
    n = len(candles)
    windows = walk_forward_windows(
        n,
        train_bars=rcfg.walk_forward_train_bars,
        test_bars=rcfg.walk_forward_test_bars,
    )
    # Shrink if history short
    if not windows and n >= rcfg.min_bars * 3:
        train_bars = max(rcfg.min_bars * 2, n // 3)
        test_bars = max(rcfg.min_bars, n // 6)
        windows = walk_forward_windows(n, train_bars=train_bars, test_bars=test_bars)

    out_windows: list[dict[str, Any]] = []
    for w in windows:
        test_run = run_strategy_backtest(
            symbol,
            timeframe,
            candles,
            strategy,
            candles_4h=candles_4h,
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            signal_config=signal_config,
            research_config=rcfg,
            period_label="WALK_FORWARD_TEST",
            index_start=w["test_start"],
            index_end=w["test_end"],
        )
        out_windows.append(
            {
                "strategy": get_strategy(strategy).strategy_id  # type: ignore[union-attr]
                if isinstance(strategy, str)
                else strategy.strategy_id,
                "train_period": {"start": w["train_start"], "end": w["train_end"]},
                "test_period": {"start": w["test_start"], "end": w["test_end"]},
                "trade_count": test_run.get("sample_size"),
                "expectancy": (test_run.get("result") or {}).get("expectancy_R"),
                "PF": (test_run.get("result") or {}).get("profit_factor"),
                "DD": (test_run.get("result") or {}).get("max_drawdown_R"),
                "label": "Historical Result",
            }
        )
    return {
        "windows": out_windows,
        "note": "Walk-forward evaluates predefined strategies; OOS not used for optimization.",
        "disclaimer": DISCLAIMER,
    }


def compare_all_strategies(
    *,
    symbol_runs: Mapping[str, Mapping[str, Any]],
    research_config: StrategyResearchConfig | None = None,
    timeframe: str = "15m",
) -> dict[str, Any]:
    """symbol_runs: strategy_id -> {trades: [...], ...} aggregated later via service."""
    rcfg = research_config or StrategyResearchConfig()
    results: dict[str, StrategyResult] = {}
    for sid, payload in symbol_runs.items():
        strat = get_strategy(sid)
        if strat is None:
            continue
        trades = payload.get("trades") or []
        results[sid] = aggregate_strategy_results(
            strat, trades, research_config=rcfg, timeframe=timeframe
        )
    return {
        "strategies": {k: v.to_dict() for k, v in results.items()},
        "condition_contribution": condition_contribution(results),
        "disclaimer": DISCLAIMER,
        "label": "Historical Result",
    }

