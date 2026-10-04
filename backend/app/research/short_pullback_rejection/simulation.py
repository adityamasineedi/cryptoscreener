"""Trade simulation for SHORT pullback-rejection research."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.short_pullback_rejection.constants import (
    DEFAULT_THRESHOLDS,
    SAFETY_STAMPS,
)
from app.research.short_pullback_rejection.regime import candle_ohlc, candle_time
from app.research.short_pullback_rejection.signals import validate_signal_geometry
from app.research.trade_fees import enrich_trade_execution
from app.signals.schemas import Direction
from app.signals.trade_math import (
    SAME_CANDLE_PRECEDENCE_SL_FIRST,
    calculate_gross_pnl,
    resolve_same_candle_exit,
    stop_distance,
    stop_triggered,
    take_profit_triggered,
)


def simulate_signal_trade(
    signal: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float | None = None,
    max_hold_bars: int | None = None,
) -> dict[str, Any] | None:
    """Walk forward from entry using direction-neutral SHORT primitives."""
    check = validate_signal_geometry(signal)
    if not check["ok"]:
        return None

    th = DEFAULT_THRESHOLDS
    risk = float(risk_usd if risk_usd is not None else th["risk_usd"])
    hold = int(max_hold_bars if max_hold_bars is not None else th["max_hold_bars"])

    entry_i = int(signal["entry_index"])
    entry = float(signal["entry_price"])
    stop = float(signal["stop_price"])
    tp = float(signal["take_profit_price"])
    dist = stop_distance(Direction.SHORT, entry, stop)
    if dist <= 0:
        return None
    qty = risk / dist

    outcome = "OPEN"
    exit_price = None
    exit_index = None
    exit_time = None
    mfe = 0.0
    mae = 0.0

    start = entry_i + 1  # manage from next bar (no same-bar entry+exit look-ahead)
    end = min(len(candles) - 1, entry_i + hold)
    for i in range(start, end + 1):
        _, h, l, _ = candle_ohlc(candles[i])
        mfe = max(mfe, entry - l)
        mae = max(mae, h - entry)
        hit_sl = stop_triggered(Direction.SHORT, h, l, stop)
        hit_tp = take_profit_triggered(Direction.SHORT, h, l, tp)
        if hit_sl or hit_tp:
            resolution = resolve_same_candle_exit(
                Direction.SHORT,
                h,
                l,
                stop,
                tp,
                precedence=SAME_CANDLE_PRECEDENCE_SL_FIRST,
            )
            outcome = str(resolution["outcome"] or "OPEN")
            if outcome == "SL":
                outcome = "SL"
            elif outcome == "TP":
                outcome = "TP1"
            exit_price = resolution["exit_price"]
            exit_index = i
            ts = candle_time(candles[i])
            exit_time = ts.isoformat() if ts else None
            break

    if exit_price is None:
        # Time stop at last bar close.
        _, _, _, c = candle_ohlc(candles[end])
        exit_price = c
        exit_index = end
        ts = candle_time(candles[end])
        exit_time = ts.isoformat() if ts else None
        outcome = "TIME"

    gross = calculate_gross_pnl(Direction.SHORT, entry, float(exit_price), qty)
    r_gross = gross / risk if risk else None
    trade = {
        **dict(signal),
        **SAFETY_STAMPS,
        "direction": "SHORT",
        "quantity": qty,
        "outcome": outcome,
        "exit_price": float(exit_price),
        "exit_index": exit_index,
        "exit_time": exit_time,
        "holding_bars": (exit_index - entry_i) if exit_index is not None else None,
        "gross_pnl": gross,
        "r_gross": r_gross,
        "R": r_gross,
        "mfe": mfe,
        "mae": mae,
        "mfe_r": (mfe / dist) if dist else None,
        "mae_r": (mae / dist) if dist else None,
        "entry_type": signal.get("entry_type") or "MARKET",
        "condition_snapshot": {"entry_type": signal.get("entry_type") or "MARKET"},
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }
    enriched = enrich_trade_execution(trade, risk_usd=risk)
    trade["fees"] = float(enriched.get("fee_total_usd") or 0.0)
    trade["net_pnl"] = enriched.get("net_pnl_usd")
    trade["r_net"] = enriched.get("r_net")
    if trade.get("r_net") is not None:
        trade["R"] = trade["r_net"]
    trade["gross_pnl_usd"] = enriched.get("gross_pnl_usd", gross)
    trade["net_pnl_usd"] = trade["net_pnl"]
    trade["fee_total_usd"] = trade["fees"]
    return trade


def simulate_signals(
    signals: Sequence[Mapping[str, Any]],
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    risk_usd: float | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sig in signals:
        sym = str(sig.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        trade = simulate_signal_trade(sig, candles, risk_usd=risk_usd)
        if trade is not None:
            out.append(trade)
    return out
