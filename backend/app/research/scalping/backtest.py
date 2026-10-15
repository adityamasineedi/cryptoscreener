from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Mapping, Sequence

from app.research.combination_backtest import (
    AMBIGUOUS_CONSERVATIVE,
    resolve_intrabar_outcome,
)

from .engine import ScalpingStrategyEngine
from .features import build_features
from .models import ScalpCandidate, ScalpTrade


def simulate_candidate(
    candidate: ScalpCandidate,
    candles_1m: Sequence[Mapping[str, Any]],
    *,
    ambiguity_handling: str = AMBIGUOUS_CONSERVATIVE,
) -> ScalpTrade:
    trade = ScalpTrade(candidate=candidate)
    if candidate.entry_price is None or candidate.target_price is None:
        return trade
    features = build_features(candles_1m, "1m")
    last = min(
        len(candles_1m) - 1,
        candidate.entry_index + candidate.max_holding_minutes - 1,
    )
    for i in range(candidate.entry_index, last + 1):
        outcome, exit_price, ambiguous = resolve_intrabar_outcome(
            direction=candidate.direction,
            high=features.highs[i],
            low=features.lows[i],
            stop=candidate.stop_price,
            targets=[candidate.target_price],
            handling=ambiguity_handling,
        )
        if outcome is not None:
            trade.exit_index = i
            trade.exit_time = features.closes_at[i]
            trade.exit_price = exit_price
            trade.outcome = outcome
            trade.ambiguous_intrabar = ambiguous
            if outcome == "AMBIGUOUS_INTRABAR" and exit_price is None:
                trade.r_multiple = None
            elif outcome == "AMBIGUOUS_INTRABAR":
                trade.r_multiple = 0.0
            else:
                trade.r_multiple = -1.0 if outcome == "SL" else 1.5
            return trade
    if candidate.entry_index <= last:
        trade.exit_index = last
        trade.exit_time = features.closes_at[last]
        trade.exit_price = features.closes[last]
        trade.outcome = "TIME"
        risk = candidate.risk_per_unit or 0.0
        if risk > 0:
            pnl = (
                trade.exit_price - candidate.entry_price
                if candidate.direction == "LONG"
                else candidate.entry_price - trade.exit_price
            )
            trade.r_multiple = pnl / risk
    return trade


def raw_metrics(trades: Sequence[ScalpTrade]) -> dict[str, Any]:
    values = [t.r_multiple for t in trades if t.r_multiple is not None]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": len(trades),
        "scored_trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values) if values else None,
        "expectancy_r": mean(values) if values else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "net_r": sum(values),
        "max_drawdown_r": max_dd,
        "ambiguous_intrabar": sum(t.ambiguous_intrabar for t in trades),
        "fees_included": False,
    }


def run_backtest(
    *,
    symbol: str,
    candles_1m: Sequence[Mapping[str, Any]],
    candles_5m: Sequence[Mapping[str, Any]],
    candles_15m: Sequence[Mapping[str, Any]],
    one_open_per_strategy: bool = True,
) -> dict[str, Any]:
    candidates = ScalpingStrategyEngine().evaluate(
        symbol=symbol,
        candles_1m=candles_1m,
        candles_5m=candles_5m,
        candles_15m=candles_15m,
    )
    grouped: dict[str, list[ScalpCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[f"{candidate.strategy_id}:{candidate.variant}"].append(candidate)
    results: dict[str, Any] = {}
    for key, rows in grouped.items():
        trades: list[ScalpTrade] = []
        next_entry = 0
        for candidate in rows:
            if one_open_per_strategy and candidate.entry_index < next_entry:
                continue
            trade = simulate_candidate(candidate, candles_1m)
            trades.append(trade)
            if one_open_per_strategy:
                next_entry = (trade.exit_index + 1) if trade.exit_index is not None else len(candles_1m)
        results[key] = {
            "metrics": raw_metrics(trades),
            "trades": [t.to_dict() for t in trades],
        }
    return {
        "symbol": symbol.upper(),
        "execution": "next_1m_open",
        "intrabar_policy": AMBIGUOUS_CONSERVATIVE,
        "validation_status": "NOT_EVALUATED",
        "note": "Raw gross research metrics only; no missing validation gate is inferred.",
        "strategies": results,
    }
