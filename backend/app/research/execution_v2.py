"""Corrected research execution helpers, simulator version 2 (``v2_corrected``).

Version 1 stays unchanged so archived results reproduce:
``trade_fees.enrich_trade_execution`` and ``combination_backtest`` (_simulate_trade,
evaluate_candidate_trades, resolve_intrabar_outcome). Version 2 fixes two dormant
v1 defects:

1. Fee helper: v1 slips the entry for the fee but computes gross PnL from the
   unslipped entry. v2 computes gross PnL from both slipped fills.
2. Gap through the stop: v1 exits at the stop level when a bar opens beyond it.
   v2 exits at that bar's open (slippage, when modelled, is applied on top by
   the fee helper).

With zero slippage and no gapped stop the two versions agree to the cent.
Research only; no paper or live path calls this module.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.combination_backtest import _excursions, resolve_intrabar_outcome
from app.research.config import AMBIGUOUS_CONSERVATIVE, AMBIGUOUS_EXCLUDE, AMBIGUOUS_NEUTRAL
from app.research.schemas import ResearchTrade
from app.research.trade_fees import FEE_SIGN_POSITIVE_COST, enrich_trade_execution
from app.signals._candle_utils import candle_time

EXECUTION_MODEL_V1 = "v1_archived"
EXECUTION_MODEL_V2 = "v2_corrected"


# ----------------------------------------------------------------- fix 1: fee helper


def enrich_trade_execution_v2(trade: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """v1 sizing and fees; gross and net PnL from the slipped fills.

    Quantity is still sized from the planned (unslipped) entry, as in v1, so risk
    and R stay on the planned basis. Only the PnL arithmetic changes.
    """
    row = enrich_trade_execution(trade, **kwargs)
    qty = float(row.get("qty") or 0.0)
    if qty <= 0 or row.get("notional_exit_usd") is None:
        return {**row, "execution_model": EXECUTION_MODEL_V2}
    fill_entry = float(row["notional_entry_usd"]) / qty
    fill_exit = float(row["notional_exit_usd"]) / qty
    short = str(row.get("direction") or "LONG").upper() == "SHORT"
    gross = qty * ((fill_entry - fill_exit) if short else (fill_exit - fill_entry))
    fees = float(row["fee_entry_usd"]) + float(row["fee_exit_usd"])
    positive_cost = str(row.get("fee_sign")).upper() == FEE_SIGN_POSITIVE_COST
    net = gross - fees if positive_cost else gross + fees
    risk = float(row.get("risk_usd") or 0.0)
    return {
        **row,
        "gross_pnl": gross,
        "gross_pnl_usd": gross,
        "net_pnl": net,
        "net_pnl_usd": net,
        "r_net": (net / risk) if risk > 0 else None,
        "fill_entry_price": fill_entry,
        "fill_exit_price": fill_exit,
        "execution_model": EXECUTION_MODEL_V2,
    }


def enrich_trades_v2(trades: Sequence[Mapping[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    """Closed trades only, in order, numbered like ``trade_fees.enrich_trades``."""
    out = []
    for i, t in enumerate(trades):
        if str(t.get("outcome") or "") in ("", "OPEN", "None"):
            continue
        row = enrich_trade_execution_v2(t, **kwargs)
        row["trade_no"] = len(out) + 1
        row["source_index"] = i
        if row.get("trade_id") is None:
            row["trade_id"] = f"t{row['trade_no']}"
        out.append(row)
    return out


# ----------------------------------------------------------------- fix 2: gap through the stop


def resolve_bar_exit_v2(
    *,
    direction: str,
    open_: float,
    high: float,
    low: float,
    stop: float,
    targets: list[float],
    handling: str = AMBIGUOUS_CONSERVATIVE,
) -> tuple[str | None, float | None, bool, bool]:
    """(outcome, raw exit price, ambiguous, gap) for a bar the position held from its open."""
    beyond = open_ < stop if direction == "LONG" else open_ > stop
    if beyond:
        return "SL", float(open_), False, True
    outcome, px, ambiguous = resolve_intrabar_outcome(
        direction=direction, high=high, low=low, stop=stop, targets=targets, handling=handling
    )
    return outcome, px, ambiguous, False


def simulate_trade_v2(
    trade: ResearchTrade,
    candles: Sequence[Mapping[str, Any]],
    *,
    handling: str = AMBIGUOUS_CONSERVATIVE,
) -> ResearchTrade:
    """``combination_backtest._simulate_trade`` with gap-aware stops."""
    targets = [t for t in (trade.tp1, trade.tp2, trade.tp3) if t is not None]
    highs: list[float] = []
    lows: list[float] = []
    for i in range(trade.entry_index + 1, len(candles)):
        c = candles[i]
        o = float(c.get("open") or c.get("o") or 0)
        high = float(c.get("high") or c.get("h") or 0)
        low = float(c.get("low") or c.get("l") or 0)
        highs.append(high)
        lows.append(low)
        outcome, exit_px, ambiguous, gap = resolve_bar_exit_v2(
            direction=trade.direction, open_=o, high=high, low=low, stop=trade.stop_price,
            targets=targets, handling=handling,
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
        trade.condition_snapshot = {**(trade.condition_snapshot or {}), "gap_stop": gap,
                                    "execution_model": EXECUTION_MODEL_V2}
        risk = abs(trade.entry_price - trade.stop_price)
        if outcome == "AMBIGUOUS_INTRABAR" and handling in (AMBIGUOUS_NEUTRAL, AMBIGUOUS_EXCLUDE):
            trade.r_multiple = 0.0 if handling == AMBIGUOUS_NEUTRAL else None
        elif exit_px is not None and risk > 0:
            move = (exit_px - trade.entry_price) if trade.direction == "LONG" else (trade.entry_price - exit_px)
            trade.r_multiple = move / risk
        else:
            trade.r_multiple = 0.0
        bars = trade.holding_bars
        if outcome == "SL":
            trade.time_to_sl = bars
        elif outcome == "TP1":
            trade.time_to_tp1 = bars
        elif outcome == "TP2":
            trade.time_to_tp2 = bars
        elif outcome == "TP3":
            trade.time_to_tp3 = bars
        trade.mae, trade.mfe, trade.mae_r, trade.mfe_r = _excursions(
            direction=trade.direction, entry=trade.entry_price, stop=trade.stop_price, highs=highs, lows=lows
        )
        return trade
    trade.outcome = "OPEN"
    trade.mae, trade.mfe, trade.mae_r, trade.mfe_r = _excursions(
        direction=trade.direction, entry=trade.entry_price, stop=trade.stop_price, highs=highs, lows=lows
    )
    return trade


def evaluate_candidate_trades_v2(
    candles: Sequence[Mapping[str, Any]],
    candidates: Sequence[ResearchTrade],
    *,
    handling: str = AMBIGUOUS_CONSERVATIVE,
    one_open_at_a_time: bool = True,
) -> list[ResearchTrade]:
    """``combination_backtest.evaluate_candidate_trades`` with ``simulate_trade_v2``."""
    ordered = sorted(candidates, key=lambda t: (t.entry_index, t.symbol, t.combination_id))
    out: list[ResearchTrade] = []
    next_allowed = 0
    for cand in ordered:
        if one_open_at_a_time and cand.entry_index < next_allowed:
            continue
        closed = simulate_trade_v2(cand, candles, handling=handling)
        out.append(closed)
        if one_open_at_a_time:
            next_allowed = int(closed.exit_index) + 1 if closed.exit_index is not None else len(candles)
    return out
