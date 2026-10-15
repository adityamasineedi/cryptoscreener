"""Candle-only long grid on futures prices.

Fills use the bar high/low. A sell is resting only after that slot has been
bought. When one bar touches both a resting buy and a resting sell, the
configured path chooses the order. ``worst`` keeps the lower close equity.
Nothing in a bar reads a later bar.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.grid_trading.config import (
    GridConfig,
    GridConfigError,
    liquidation_price,
    maker_fee_rate,
    open_grid_cap,
    slot_quantity,
    taker_fee_rate,
    validate_config,
    worst_case_liquidation_price,
)
from app.grid_trading.funding import (
    FUNDING_EXCLUDED,
    FUNDING_QUOTES,
    FUNDING_UNAVAILABLE,
    assess_coverage,
    coarse_inventory_warning,
    distinct_settlements,
    due_settlements,
    funding_result_guards,
    normalize_funding,
    quotes_not_settlements,
    settlement_price,
    stamp,
)
from app.grid_trading.levels import build_levels

_DUST = 1e-12
_CLOSED = ("stop", "max_drawdown", "liquidation", "take_profit")


def _f(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GridConfigError("candles", f"candle {name} is not a number") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise GridConfigError("candles", f"candle {name} is not finite")
    return number


def _slip(config: GridConfig) -> float:
    return config.slippage_bps / 10_000.0


def _new_state(config: GridConfig, levels: list[float], anchor: float) -> dict[str, Any]:
    slots = []
    for i in range(config.grid_count):
        buy = levels[i]
        sell = levels[i + 1]
        slots.append(
            {
                "index": i,
                "buy": buy,
                "sell": sell,
                "qty": slot_quantity(config.capital, config.grid_count, buy, config),
                "armed": buy < anchor,
                "long": False,
                "entry_quote": 0.0,
            }
        )
    return {
        "quote": float(config.capital),
        "base": 0.0,
        "cost_basis": 0.0,
        "realized": 0.0,
        "slots": slots,
        "fills": [],
        "halted": None,
        "touches": 0,
        "dual_touch_bars": 0,
        "paths": [],
        "events": [],
        "funding_paid": 0.0,
        "blocked_buys": 0,
        "liquidation_fee": 0.0,
    }


def _equity(state: dict[str, Any], price: float) -> float:
    return state["quote"] + state["base"] * price


def _buy_slot(state: dict[str, Any], slot: dict[str, Any], config: GridConfig, time: Any) -> bool:
    fee = maker_fee_rate(config)
    px = slot["buy"]
    qty = slot["qty"]
    notional = qty * px
    fee_quote = notional * fee
    debit = notional + fee_quote
    state["touches"] += 1
    if sum(1 for other in state["slots"] if other["long"]) >= open_grid_cap(config):
        state["blocked_buys"] += 1
        return False
    # Initial margin: equity after the buy covers (base + qty) * px / leverage.
    # At 1x this is quote >= debit.
    borrow = (state["base"] + qty) * px * (1.0 - 1.0 / config.leverage)
    if state["quote"] + borrow + max(1e-6, abs(debit) * 1e-9) < debit:
        return False
    state["quote"] -= debit
    if config.leverage <= 1:
        state["quote"] = max(0.0, state["quote"])
    state["base"] += qty
    state["cost_basis"] += debit
    slot["long"] = True
    slot["armed"] = False
    slot["entry_quote"] = debit
    state["fills"].append(
        {
            "time": time,
            "side": "buy",
            "slot": slot["index"],
            "level": slot["buy"],
            "price": px,
            "qty": qty,
            "fee": fee_quote,
        }
    )
    return True


def _sell_slot(state: dict[str, Any], slot: dict[str, Any], config: GridConfig, time: Any) -> bool:
    if not slot["long"] or slot["qty"] <= _DUST:
        return False
    fee = maker_fee_rate(config)
    px = slot["sell"]
    qty = slot["qty"]
    notional = qty * px
    fee_quote = notional * fee
    credit = notional - fee_quote
    state["touches"] += 1
    state["quote"] += credit
    state["base"] -= qty
    basis = slot["entry_quote"]
    state["cost_basis"] -= basis
    state["realized"] += credit - basis
    slot["long"] = False
    slot["armed"] = True
    slot["entry_quote"] = 0.0
    if state["base"] < _DUST:
        state["base"] = 0.0
    if abs(state["cost_basis"]) < 1e-8:
        state["cost_basis"] = 0.0
    state["fills"].append(
        {
            "time": time,
            "side": "sell",
            "slot": slot["index"],
            "level": slot["sell"],
            "price": px,
            "qty": qty,
            "fee": fee_quote,
        }
    )
    return True


def _flatten(state: dict[str, Any], config: GridConfig, price: float, time: Any, reason: str) -> None:
    if state["base"] > _DUST:
        slip = _slip(config)
        fee = taker_fee_rate(config)
        px = price * (1.0 - slip)
        notional = state["base"] * px
        fee_quote = notional * fee
        credit = notional - fee_quote
        state["quote"] += credit
        state["realized"] += credit - state["cost_basis"]
        state["fills"].append(
            {
                "time": time,
                "side": "sell",
                "slot": None,
                "level": price,
                "price": px,
                "qty": state["base"],
                "fee": fee_quote,
                "reason": reason,
            }
        )
        state["base"] = 0.0
        state["cost_basis"] = 0.0
        label = "drawdown" if reason == "max_drawdown" else reason
        state["events"].append({"type": label, "time": time, "price": px})
    for slot in state["slots"]:
        slot["long"] = False
        slot["armed"] = False
        slot["entry_quote"] = 0.0
    state["halted"] = reason


def _touched(state: dict[str, Any], candle: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    low = candle["low"]
    high = candle["high"]
    buys = [
        slot
        for slot in state["slots"]
        if slot["armed"] and not slot["long"] and low <= slot["buy"]
    ]
    sells = [slot for slot in state["slots"] if slot["long"] and high >= slot["sell"]]
    buys.sort(key=lambda slot: slot["buy"], reverse=True)
    sells.sort(key=lambda slot: slot["sell"])
    return buys, sells


def _apply_orders(
    state: dict[str, Any],
    config: GridConfig,
    candle: dict[str, Any],
    *,
    buys_first: bool,
) -> None:
    buys, sells = _touched(state, candle)
    time = candle.get("time")
    if buys_first:
        for slot in buys:
            if state["halted"]:
                return
            _buy_slot(state, slot, config, time)
        _, sells_after = _touched(state, candle)
        for slot in sells_after:
            _sell_slot(state, slot, config, time)
        return
    for slot in sells:
        _sell_slot(state, slot, config, time)
    buys_after, _ = _touched(state, candle)
    for slot in buys_after:
        if state["halted"]:
            return
        _buy_slot(state, slot, config, time)


def _liquidate_book(state: dict[str, Any], config: GridConfig, price: float, time: Any) -> None:
    """Close at the liquidation price. The margin left after the close is forfeited."""
    _flatten(state, config, price, time, "liquidation")
    left = max(0.0, state["quote"])
    state["quote"] -= left
    state["realized"] -= left
    state["liquidation_fee"] += left


def _finish_bar(state: dict[str, Any], config: GridConfig, candle: dict[str, Any]) -> float:
    """Price falls from the open to the low: whichever of stop and liquidation is higher fires first."""
    time = candle.get("time")
    if state["halted"] is None and state["base"] > _DUST:
        liq = liquidation_price(state["quote"], state["base"], config)
        stop_hit = candle["low"] <= config.stop_price
        if stop_hit and (liq is None or config.stop_price > liq):
            _flatten(state, config, config.stop_price, time, "stop")
        elif liq is not None and candle["low"] <= liq:
            _liquidate_book(state, config, liq, time)
        elif stop_hit:
            _flatten(state, config, config.stop_price, time, "stop")
    if (
        state["halted"] is None
        and config.exit_above_range
        and candle["close"] > config.range_high
        and _equity(state, candle["close"]) > config.capital
    ):
        holding = state["base"] > _DUST
        _flatten(state, config, candle["close"], time, "take_profit")
        if not holding:
            state["events"].append({"type": "take_profit", "time": time, "price": candle["close"]})
    equity = _equity(state, candle["close"])
    return equity


def _bar_has_two_sides(state: dict[str, Any], candle: dict[str, Any]) -> bool:
    buys, sells = _touched(state, candle)
    if buys and sells:
        return True
    return any(candle["high"] >= slot["sell"] for slot in buys)


def _note_range(state: dict[str, Any], config: GridConfig, candle: dict[str, Any]) -> None:
    if candle["low"] < config.range_low or candle["high"] > config.range_high:
        side = "below" if candle["low"] < config.range_low else "above"
        if candle["low"] < config.range_low and candle["high"] > config.range_high:
            side = "both"
        state["events"].append(
            {"type": "range_exit", "time": candle.get("time"), "price": candle["close"], "side": side}
        )


def _run_bar(state: dict[str, Any], config: GridConfig, candle: dict[str, Any]) -> None:
    if state["halted"]:
        return
    dual = _bar_has_two_sides(state, candle)
    if dual:
        state["dual_touch_bars"] += 1
    path = config.intrabar_path
    if dual and path == "worst":
        buy_first = deepcopy(state)
        sell_first = deepcopy(state)
        _apply_orders(buy_first, config, candle, buys_first=True)
        eq_buy = _finish_bar(buy_first, config, candle)
        _apply_orders(sell_first, config, candle, buys_first=False)
        eq_sell = _finish_bar(sell_first, config, candle)
        chosen = buy_first if eq_buy <= eq_sell else sell_first
        chosen_name = "buy_then_sell" if eq_buy <= eq_sell else "sell_then_buy"
        state.clear()
        state.update(chosen)
        state["paths"].append({"time": candle.get("time"), "path": chosen_name})
    else:
        buys_first = path != "sell_then_buy"
        if dual:
            state["paths"].append(
                {
                    "time": candle.get("time"),
                    "path": "buy_then_sell" if buys_first else "sell_then_buy",
                }
            )
        _apply_orders(state, config, candle, buys_first=buys_first)
        _finish_bar(state, config, candle)


def _normalize(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candles) < 2:
        raise GridConfigError("candles", "at least two candles are required")
    out: list[dict[str, Any]] = []
    for raw in candles:
        if not isinstance(raw, dict):
            raise GridConfigError("candles", "each candle must be an object")
        bar = {
            "time": raw.get("time"),
            "open": _f(raw.get("open"), "open"),
            "high": _f(raw.get("high"), "high"),
            "low": _f(raw.get("low"), "low"),
            "close": _f(raw.get("close"), "close"),
        }
        if bar["high"] < bar["low"]:
            raise GridConfigError("candles", "high is below low")
        if not (bar["low"] <= bar["open"] <= bar["high"] and bar["low"] <= bar["close"] <= bar["high"]):
            raise GridConfigError("candles", "open or close is outside the high-low range")
        out.append(bar)
    return out


def _buy_and_hold(
    candles: list[dict[str, Any]],
    config: GridConfig,
    *,
    funding_paid: float = 0.0,
) -> dict[str, float]:
    fee = taker_fee_rate(config)
    slip = _slip(config)
    entry = candles[0]["open"] * (1.0 + slip)
    qty = _hold_qty(config, entry)
    quote = config.capital - qty * entry * (1.0 + fee)
    liq = liquidation_price(quote, qty, config)
    liquidated = liq is not None and min(bar["low"] for bar in candles) <= liq
    exit_px = candles[-1]["close"] * (1.0 - slip)
    credit = qty * exit_px * (1.0 - fee)
    equity = 0.0 if liquidated else quote + credit - funding_paid
    return {
        "qty": qty,
        "entry": entry,
        "exit": exit_px,
        "equity": equity,
        "return": equity / config.capital - 1.0,
        "funding_paid": funding_paid,
        "leverage": config.leverage,
        "liquidated": liquidated,
    }


def _hold_qty(config: GridConfig, entry: float) -> float:
    """Buy-and-hold at the grid's leverage, so the benchmark carries the same exposure."""
    return config.capital * config.leverage / (entry * (1.0 + taker_fee_rate(config)))


def _funding_qty(
    opening: float,
    closing: float,
    rate: float,
    *,
    at_open: bool,
    stopped: bool,
) -> float:
    """Inventory used for one settlement.

    A settlement exactly at the candle open, including the stop candle, uses
    the inventory at the end of the previous candle. A settlement strictly
    inside a candle uses the worse of the opening and closing inventory:
    the larger quantity when the rate is positive, the smaller when it is
    negative.
    """
    if stopped or at_open:
        return opening
    if rate > 0:
        return max(opening, closing)
    if rate < 0:
        return min(opening, closing)
    return opening


def _charge_funding(state: dict[str, Any], qty: float, row: dict[str, Any], close: float) -> float:
    price, source = settlement_price(row, close)
    payment = qty * price * float(row["rate"])
    state["quote"] -= payment
    state["funding_paid"] += payment
    state["events"].append(
        {
            "type": "funding",
            "time": row.get("time"),
            "price": price,
            "price_source": source,
            "rate": float(row["rate"]),
            "payment": payment,
        }
    )
    return payment


def _cycle_profits(fills: list[dict[str, Any]]) -> list[float]:
    """A cycle is one slot buy paired with that slot's later grid sell.

    Stop, drawdown, and mark sells have no slot and are not cycles.
    """
    pending: dict[int, list[float]] = {}
    profits: list[float] = []
    for fill in fills:
        slot = fill.get("slot")
        if slot is None:
            continue
        if fill["side"] == "buy":
            pending.setdefault(slot, []).append(float(fill["qty"]) * float(fill["price"]) + float(fill["fee"]))
        elif fill["side"] == "sell":
            stack = pending.get(int(slot)) or []
            if not stack:
                continue
            debit = stack.pop(0)
            credit = float(fill["qty"]) * float(fill["price"]) - float(fill["fee"])
            profits.append(credit - debit)
    return profits


def _liquidate(state: dict[str, Any], config: GridConfig, candle: dict[str, Any]) -> float:
    if state["halted"] or state["base"] <= _DUST:
        return _equity(state, candle["close"])
    end = deepcopy(state)
    _flatten(end, config, candle["close"], candle.get("time"), "mark")
    return end["quote"]


def _window_funding(rows: list[dict[str, Any]], bars: list[dict[str, Any]], qty_at) -> float:
    """Sum funding for a constant or per-candle quantity. Does not touch grid state."""
    if not rows or not bars:
        return 0.0
    window_end = stamp(bars[-1]["time"])
    paid = 0.0
    for index, bar in enumerate(bars):
        nxt = stamp(bars[index + 1]["time"]) if index + 1 < len(bars) else None
        for row in due_settlements(
            rows, bar_time=stamp(bar["time"]), next_time=nxt, window_end=window_end
        ):
            price, _source = settlement_price(row, bar["close"])
            paid += qty_at(index) * price * float(row["rate"])
    return paid


def run_grid(
    candles: list[dict[str, Any]],
    config: GridConfig,
    funding: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    levels = build_levels(config)
    validate_config(config, levels)
    bars = _normalize(candles)
    state = _new_state(config, levels, bars[0]["open"])
    if config.paused:
        state["halted"] = "paused"
        state["events"].append({"type": "paused", "time": bars[0].get("time"), "price": bars[0]["open"]})
    open_orders = sum(1 for slot in state["slots"] if slot["armed"])
    if open_orders > config.max_open_orders:
        raise GridConfigError("max_open_orders", "initial resting buys exceed max_open_orders")

    funding_status = "excluded" if config.funding_mode == "exclude" else "charged"
    unavailable_reason: str | None = None
    if config.funding_mode == "exclude":
        rows = []
    else:
        rows = distinct_settlements(normalize_funding(funding))
        if not rows:
            funding_status = "unavailable"
            unavailable_reason = FUNDING_UNAVAILABLE
            rows = []
        elif quotes_not_settlements(rows):
            funding_status = "unavailable"
            unavailable_reason = FUNDING_QUOTES
            rows = []
    window_start = stamp(bars[0]["time"])
    window_end = stamp(bars[-1]["time"])
    equity_curve: list[dict[str, Any]] = []
    peak = float(config.capital)
    max_dd = 0.0
    accepted = True
    error: str | None = None

    for index, bar in enumerate(bars):
        opening_base = float(state["base"])
        already_closed = state["halted"] in _CLOSED
        _run_bar(state, config, bar)
        _note_range(state, config, bar)
        nxt = stamp(bars[index + 1]["time"]) if index + 1 < len(bars) else None
        bar_time = stamp(bar["time"])
        if rows and not already_closed:
            stopped = state["halted"] in ("stop", "liquidation", "take_profit")
            for row in due_settlements(
                rows, bar_time=bar_time, next_time=nxt, window_end=window_end
            ):
                when = float(row["stamp"])
                qty = _funding_qty(
                    opening_base,
                    float(state["base"]),
                    float(row["rate"]),
                    at_open=when == bar_time,
                    stopped=stopped,
                )
                _charge_funding(state, qty, row, bar["close"])
        equity = _equity(state, bar["close"])
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > config.max_drawdown + 1e-12:
            accepted = False
            error = "max_drawdown"
            if state["halted"] is None:
                _flatten(state, config, bar["close"], bar.get("time"), "max_drawdown")
            equity = state["quote"]
            dd = (peak - equity) / peak if peak > 0 else dd
        if dd > max_dd:
            max_dd = dd
        equity_curve.append({"time": bar.get("time"), "equity": equity, "drawdown": dd})
        if not accepted:
            break

    last = bars[min(len(equity_curve), len(bars)) - 1]
    liquidated = _liquidate(state, config, last)
    seen = bars[: len(equity_curve)]
    if funding_status == "excluded":
        coverage = {
            "funding_coverage_pct": None,
            "funding_included": False,
            "warnings": [FUNDING_EXCLUDED],
        }
        hold_funding = 0.0
    elif funding_status == "unavailable":
        coverage = {
            "funding_coverage_pct": None,
            "funding_included": False,
            "warnings": [unavailable_reason or FUNDING_UNAVAILABLE],
        }
        hold_funding = 0.0
    else:
        coverage = assess_coverage(rows, window_start=window_start, window_end=stamp(seen[-1]["time"]))
        coarse = coarse_inventory_warning(config.timeframe, seen, rows)
        if coarse:
            coverage["warnings"] = [*coverage["warnings"], coarse]
        entry = seen[0]["open"] * (1.0 + _slip(config))
        hold_qty = _hold_qty(config, entry)
        hold_funding = _window_funding(rows, seen, lambda _index: hold_qty)
        payments = [
            (float(event["payment"]) / float(event["rate"]), float(event["rate"]))
            for event in state["events"]
            if event.get("type") == "funding" and float(event.get("rate") or 0.0) != 0.0
        ]
        guarded = funding_result_guards(rows, coverage, payments=payments)
        if guarded["funding_status"] == "unavailable":
            funding_status = "unavailable"
            unavailable_reason = guarded["warnings"][0]
            coverage = {
                "funding_coverage_pct": None,
                "funding_included": False,
                "warnings": guarded["warnings"],
            }
        else:
            funding_status = guarded["funding_status"]
            coverage = {
                "funding_coverage_pct": guarded["funding_coverage_pct"],
                "funding_included": guarded["funding_included"],
                "warnings": guarded["warnings"],
            }
    hold = _buy_and_hold(seen, config, funding_paid=hold_funding)
    fills = state["fills"]
    touches = state["touches"]
    inside = sum(1 for bar in seen if config.range_low <= bar["close"] <= config.range_high)
    mark = last["close"]
    unrealized = state["base"] * mark - state["cost_basis"]
    cycles = _cycle_profits(fills)
    pnl_before_funding = state["realized"] + unrealized
    funded_net = (
        None
        if funding_status in ("unavailable", "incomplete")
        else pnl_before_funding - state["funding_paid"]
    )
    funding_events = [event for event in state["events"] if event.get("type") == "funding"]
    return {
        "accepted": accepted,
        "error": error,
        "halted": state["halted"],
        "market": config.market,
        "mode": config.mode,
        "symbol": config.symbol,
        "timeframe": config.timeframe,
        "grid_type": config.grid_type,
        "levels": levels,
        "fills": fills,
        "fill_count": len(fills),
        "touch_count": touches,
        "fill_rate": (len(fills) / touches) if touches else 0.0,
        "round_trips": sum(1 for fill in fills if fill["side"] == "sell" and fill.get("slot") is not None),
        "dual_touch_bars": state["dual_touch_bars"],
        "paths": state["paths"],
        "fees_paid": sum(float(fill["fee"]) for fill in fills),
        "realized_pnl": state["realized"],
        "unrealized_pnl": unrealized,
        "net_pnl": funded_net,
        "pnl_before_funding": pnl_before_funding,
        "funding_status": funding_status,
        "funding_paid": state["funding_paid"],
        "funding_mark_prices": sum(1 for event in funding_events if event.get("price_source") == "mark_price"),
        "funding_closes": sum(1 for event in funding_events if event.get("price_source") == "close"),
        "funding_coverage_pct": coverage["funding_coverage_pct"],
        "pct_time_in_range": (inside / len(seen)) if seen else 0.0,
        "avg_profit_per_grid_cycle": (sum(cycles) / len(cycles)) if cycles else None,
        "cycle_count": len(cycles),
        "events": state["events"],
        "warnings": coverage["warnings"],
        "funding_included": coverage["funding_included"],
        "leverage": config.leverage,
        "maintenance_margin_rate": config.maintenance_margin_rate,
        "max_open_grids": open_grid_cap(config),
        "worst_case_liquidation_price": worst_case_liquidation_price(config, levels),
        "stop_hits": sum(1 for event in state["events"] if event.get("type") == "stop"),
        "liquidations": sum(1 for event in state["events"] if event.get("type") == "liquidation"),
        "take_profit_exits": sum(1 for event in state["events"] if event.get("type") == "take_profit"),
        "drawdown_kills": sum(1 for event in state["events"] if event.get("type") == "drawdown"),
        "blocked_buys": state["blocked_buys"],
        "liquidation_fee": state["liquidation_fee"],
        "quote": state["quote"],
        "base": state["base"],
        "equity": _equity(state, last["close"]) if state["base"] > _DUST else state["quote"],
        "liquidated_equity": liquidated,
        "return": liquidated / config.capital - 1.0,
        "max_drawdown": max_dd,
        "equity_curve": equity_curve,
        "buy_and_hold": hold,
        "candles": bars,
        "bar_count": len(bars),
    }
