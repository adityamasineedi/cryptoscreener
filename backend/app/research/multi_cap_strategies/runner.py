"""Orchestrate multi-cap research runs — reuses existing infra.

Reused:
  - postgres_ohlcv (via service)
  - classify_asset_group / cap_filter
  - combination_backtest.evaluate_candidate_trades
  - trade_fees.enrich_trades
  - metrics.compute_metrics
  - config.split_period_indices
  - data_quality.verify_ohlcv
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence

from app.research.combination_backtest import evaluate_candidate_trades
from app.research.config import split_period_indices
from app.research.data_quality import verify_ohlcv
from app.research.metrics import compute_metrics
from app.research.multi_cap_strategies.adapter import MultiCapStrategyAdapter, get_strategy
from app.research.multi_cap_strategies.cap_filter import resolve_cap_label
from app.research.multi_cap_strategies.config import (
    DISCLAIMER,
    RESEARCH_ENGINE_VERSION,
    MultiCapResearchConfig,
)
from app.research.schemas import ResearchTrade
from app.research.trade_fees import enrich_trades
from app.signals._candle_utils import candle_time


def _parse_year(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(datetime.fromisoformat(raw.replace("Z", "+00:00")).year)
    except ValueError:
        return None


def _metrics_for_trades(
    trades: Sequence[ResearchTrade],
    *,
    strategy_id: str,
    description: str,
    symbol: str | None,
    timeframe: str | None,
    period_label: str,
    data_quality: str,
    data_coverage: float | None,
    condition_definition: dict[str, Any],
) -> dict[str, Any]:
    closed = [t for t in trades if t.outcome and t.outcome != "OPEN"]
    m = compute_metrics(
        closed,
        combination_id=strategy_id,
        description=description,
        symbol=symbol,
        timeframe=timeframe,
        direction="LONG",
        period_label=period_label,
        data_coverage=data_coverage,
        data_quality=data_quality,
        condition_definition=condition_definition,
    )
    d = m.to_dict()
    wins = sum(1 for t in closed if (t.r_multiple or 0) > 0)
    d["trade_count"] = len(closed)
    d["win_rate"] = (wins / len(closed)) if closed else None
    d["mean_R"] = d.get("average_R")
    d["median_R"] = d.get("median_R")
    d["max_drawdown"] = d.get("max_drawdown_R")
    d["total_return"] = d.get("gross_profit_R", 0) + d.get("gross_loss_R", 0)
    d["MAE"] = d.get("average_MAE_R")
    d["MFE"] = d.get("average_MFE_R")
    return d


def _breakdown(
    trades: Sequence[ResearchTrade],
    *,
    strategy_id: str,
    description: str,
    key_fn,
) -> dict[str, Any]:
    groups: dict[str, list[ResearchTrade]] = defaultdict(list)
    for t in trades:
        k = key_fn(t)
        if k is None:
            continue
        groups[str(k)].append(t)
    out: dict[str, Any] = {}
    for k, rows in sorted(groups.items()):
        out[k] = _metrics_for_trades(
            rows,
            strategy_id=strategy_id,
            description=description,
            symbol=None,
            timeframe=None,
            period_label="FULL",
            data_quality="OK",
            data_coverage=None,
            condition_definition={},
        )
    return out


def run_strategy_on_series(
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    market_cap: float | None,
    config: MultiCapResearchConfig | None = None,
    adapter: MultiCapStrategyAdapter | None = None,
) -> dict[str, Any]:
    """Full series run with TRAIN/VALIDATION/OOS metrics."""
    t0 = time.perf_counter()
    cfg = config or MultiCapResearchConfig()
    ad = adapter or MultiCapStrategyAdapter(cfg)
    definition = get_strategy(strategy_id)
    if definition is None:
        return {"status": "ERROR", "reason": f"Unknown strategy {strategy_id}"}

    rcfg = cfg.to_research_config()
    quality = verify_ohlcv(candles, timeframe, config=rcfg)
    asset_group = resolve_cap_label(symbol, market_cap, config=cfg)

    base = {
        "strategy_id": definition.strategy_id,
        "strategy": definition.to_dict(),
        "cap_group": definition.asset_group,
        "resolved_cap_group": asset_group,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "signal_logic": definition.strategy_id,
        "trade_evaluation_logic": cfg.trade_evaluation_mechanics,
        "fvg_mitigation_rule": cfg.fvg_mitigation_rule,
        "deep_retrace_rule": cfg.deep_retrace_rule,
        "deep_retrace_threshold": cfg.small_cap_deep_retrace_threshold,
        "configuration_hash": cfg.configuration_hash(),
        "configuration": cfg.configuration_dict(),
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "data_quality": quality,
        "disclaimer": DISCLAIMER,
        "label": "Historical Result",
        "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
    }

    if quality.get("status") == "INSUFFICIENT_DATA":
        return {
            **base,
            "status": "INSUFFICIENT_DATA",
            "signal_count": 0,
            "trade_count": 0,
            "signal_to_trade_conversion": None,
            "trades": [],
            "elapsed_seconds": time.perf_counter() - t0,
        }

    candidates, gen_meta = ad.generate_candidates(
        strategy_id, symbol, timeframe, candles, market_cap=market_cap
    )
    if gen_meta.get("status") == "EXCLUDED":
        return {
            **base,
            "status": "EXCLUDED",
            "exclusion": gen_meta,
            "signal_count": 0,
            "trade_count": 0,
            "signal_to_trade_conversion": None,
            "trades": [],
            "elapsed_seconds": time.perf_counter() - t0,
        }

    open_trades = ad.candidates_to_trades(
        candidates, asset_group=asset_group, period_label="FULL"
    )
    evaluated = evaluate_candidate_trades(
        candles,
        open_trades,
        handling=cfg.ambiguous_handling,
        one_open_at_a_time=cfg.one_open_at_a_time,
    )

    # Fees / slippage enrichment for blotter
    fee_rows = enrich_trades(
        [t.to_dict() for t in evaluated if t.outcome != "OPEN"],
        risk_usd=cfg.risk_usd,
        taker_fee=cfg.taker_fee,
        maker_fee=cfg.maker_fee,
    )
    # Approximate net_R adjustment for slippage (round-trip)
    for row in fee_rows:
        r_net = row.get("r_net")
        if r_net is not None:
            entry = float(row.get("entry_price") or 0)
            stop = float(row.get("stop_price") or 0)
            risk = abs(entry - stop)
            if risk > 0 and entry > 0:
                slip_r = (2.0 * cfg.slippage_rate * entry) / risk
                row["r_net_with_slippage"] = float(r_net) - slip_r
            else:
                row["r_net_with_slippage"] = r_net

    # Period labels on trades by entry index
    n = len(candles)
    splits = split_period_indices(
        n,
        train_fraction=cfg.train_fraction,
        validation_fraction=cfg.validation_fraction,
        oos_fraction=cfg.oos_fraction,
    )
    for t in evaluated:
        for label, (a, b) in splits.items():
            if a <= t.entry_index < b:
                t.period_label = label
                break

    period_metrics: dict[str, Any] = {}
    for label in ("TRAINING_PERIOD", "VALIDATION_PERIOD", "OUT_OF_SAMPLE_PERIOD"):
        a, b = splits[label]
        subset = [t for t in evaluated if a <= t.entry_index < b]
        period_metrics[label] = _metrics_for_trades(
            subset,
            strategy_id=definition.strategy_id,
            description=definition.description,
            symbol=symbol.upper(),
            timeframe=timeframe,
            period_label=label,
            data_quality=str(quality.get("data_quality") or "OK"),
            data_coverage=quality.get("coverage_ratio"),
            condition_definition=definition.to_dict(),
        )

    full_metrics = _metrics_for_trades(
        evaluated,
        strategy_id=definition.strategy_id,
        description=definition.description,
        symbol=symbol.upper(),
        timeframe=timeframe,
        period_label="FULL",
        data_quality=str(quality.get("data_quality") or "OK"),
        data_coverage=quality.get("coverage_ratio"),
        condition_definition=definition.to_dict(),
    )

    closed = [t for t in evaluated if t.outcome and t.outcome != "OPEN"]
    signal_count = len(candidates)
    trade_count = len(closed)
    conversion = (trade_count / signal_count) if signal_count else None

    long_tr = [t for t in closed if t.direction == "LONG"]
    short_tr = [t for t in closed if t.direction == "SHORT"]

    period_start = None
    period_end = None
    if candles:
        ts0 = candle_time(candles[0])
        ts1 = candle_time(candles[-1])
        period_start = ts0.isoformat() if ts0 else None
        period_end = ts1.isoformat() if ts1 else None

    events = []
    for c in candidates:
        events.extend(e.to_dict() for e in c.events)

    return {
        **base,
        "status": "OK",
        "definition": definition.to_dict(),
        "data_range": {"start": period_start, "end": period_end},
        "splits": splits,
        "signal_count": signal_count,
        "trade_count": trade_count,
        "signal_to_trade_conversion": conversion,
        "metrics": full_metrics,
        "TRAIN": period_metrics["TRAINING_PERIOD"],
        "VALIDATION": period_metrics["VALIDATION_PERIOD"],
        "OOS": period_metrics["OUT_OF_SAMPLE_PERIOD"],
        "LONG": _metrics_for_trades(
            long_tr,
            strategy_id=definition.strategy_id,
            description=definition.description,
            symbol=symbol.upper(),
            timeframe=timeframe,
            period_label="FULL",
            data_quality="OK",
            data_coverage=None,
            condition_definition=definition.to_dict(),
        ),
        "SHORT": _metrics_for_trades(
            short_tr,
            strategy_id=definition.strategy_id,
            description=definition.description,
            symbol=symbol.upper(),
            timeframe=timeframe,
            period_label="FULL",
            data_quality="OK",
            data_coverage=None,
            condition_definition=definition.to_dict(),
        ),
        "by_year": _breakdown(
            closed,
            strategy_id=definition.strategy_id,
            description=definition.description,
            key_fn=lambda t: _parse_year(t.signal_time),
        ),
        "by_symbol": _breakdown(
            closed,
            strategy_id=definition.strategy_id,
            description=definition.description,
            key_fn=lambda t: t.symbol,
        ),
        "by_timeframe": _breakdown(
            closed,
            strategy_id=definition.strategy_id,
            description=definition.description,
            key_fn=lambda t: t.timeframe,
        ),
        "fee_assumptions": {
            "taker_fee": cfg.taker_fee,
            "maker_fee": cfg.maker_fee,
            "slippage_rate": cfg.slippage_rate,
            "risk_usd": cfg.risk_usd,
        },
        "fee_enriched_trades": fee_rows,
        "trades": [t.to_dict() for t in evaluated],
        "events": events,
        "candles_processed": len(candles),
        "elapsed_seconds": time.perf_counter() - t0,
    }


def aggregate_runs(runs: Sequence[Mapping[str, Any]], *, strategy_id: str) -> dict[str, Any]:
    """Aggregate per-symbol runs for one strategy (no ranking)."""
    definition = get_strategy(strategy_id)
    all_trades: list[ResearchTrade] = []
    signal_count = 0
    eligible = 0
    excluded: list[dict[str, Any]] = []
    dq: list[dict[str, Any]] = []

    for run in runs:
        if run.get("status") == "EXCLUDED":
            excluded.append(run.get("exclusion") or {"symbol": run.get("symbol")})
            continue
        if run.get("status") == "INSUFFICIENT_DATA":
            dq.append(
                {
                    "symbol": run.get("symbol"),
                    "timeframe": run.get("timeframe"),
                    "reason": "INSUFFICIENT_DATA",
                    "data_quality": run.get("data_quality"),
                }
            )
            continue
        if run.get("status") != "OK":
            continue
        eligible += 1
        signal_count += int(run.get("signal_count") or 0)
        for td in run.get("trades") or []:
            fields = {k: v for k, v in td.items() if k in ResearchTrade.__dataclass_fields__}
            try:
                all_trades.append(ResearchTrade(**fields))
            except TypeError:
                continue

    closed = [t for t in all_trades if t.outcome and t.outcome != "OPEN"]
    desc = definition.description if definition else strategy_id
    sid = definition.strategy_id if definition else strategy_id
    full = _metrics_for_trades(
        closed,
        strategy_id=sid,
        description=desc,
        symbol=None,
        timeframe=None,
        period_label="FULL",
        data_quality="OK",
        data_coverage=None,
        condition_definition=definition.to_dict() if definition else {},
    )
    period_buckets = {
        "TRAIN": [t for t in closed if t.period_label == "TRAINING_PERIOD"],
        "VALIDATION": [t for t in closed if t.period_label == "VALIDATION_PERIOD"],
        "OOS": [t for t in closed if t.period_label == "OUT_OF_SAMPLE_PERIOD"],
    }
    return {
        "strategy_id": sid,
        "strategy": definition.to_dict() if definition else {},
        "cap_group": definition.asset_group if definition else None,
        "definition": definition.to_dict() if definition else {},
        "symbol_count": eligible,
        "eligible_series": eligible,
        "excluded_symbols": excluded,
        "data_quality_exclusions": dq,
        "signal_count": signal_count,
        "trade_count": len(closed),
        "signal_to_trade_conversion": (
            len(closed) / signal_count if signal_count else None
        ),
        "metrics": full,
        "TRAIN": _metrics_for_trades(
            period_buckets["TRAIN"],
            strategy_id=sid,
            description=desc,
            symbol=None,
            timeframe=None,
            period_label="TRAINING_PERIOD",
            data_quality="OK",
            data_coverage=None,
            condition_definition={},
        ),
        "VALIDATION": _metrics_for_trades(
            period_buckets["VALIDATION"],
            strategy_id=sid,
            description=desc,
            symbol=None,
            timeframe=None,
            period_label="VALIDATION_PERIOD",
            data_quality="OK",
            data_coverage=None,
            condition_definition={},
        ),
        "OOS": _metrics_for_trades(
            period_buckets["OOS"],
            strategy_id=sid,
            description=desc,
            symbol=None,
            timeframe=None,
            period_label="OUT_OF_SAMPLE_PERIOD",
            data_quality="OK",
            data_coverage=None,
            condition_definition={},
        ),
        "by_year": _breakdown(
            closed, strategy_id=sid, description=desc, key_fn=lambda t: _parse_year(t.signal_time)
        ),
        "by_symbol": _breakdown(
            closed, strategy_id=sid, description=desc, key_fn=lambda t: t.symbol
        ),
        "by_timeframe": _breakdown(
            closed, strategy_id=sid, description=desc, key_fn=lambda t: t.timeframe
        ),
        "LONG": _metrics_for_trades(
            [t for t in closed if t.direction == "LONG"],
            strategy_id=sid,
            description=desc,
            symbol=None,
            timeframe=None,
            period_label="FULL",
            data_quality="OK",
            data_coverage=None,
            condition_definition={},
        ),
        "SHORT": _metrics_for_trades(
            [t for t in closed if t.direction == "SHORT"],
            strategy_id=sid,
            description=desc,
            symbol=None,
            timeframe=None,
            period_label="FULL",
            data_quality="OK",
            data_coverage=None,
            condition_definition={},
        ),
        "disclaimer": DISCLAIMER,
        "label": "Historical Result",
        "NO_LIVE_TRADING_LOGIC_WAS_CHANGED": True,
        "note": "No strategy ranking. Observational report only.",
    }
