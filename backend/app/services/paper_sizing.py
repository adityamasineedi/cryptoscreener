"""Realistic paper-trade sizing: tick/lot rounding, leverage cap, fees.

Keeps paper fills closer to Binance USDT-M constraints without placing orders.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

from app.research.trade_fees import (
    DEFAULT_TAKER_FEE,
    FEE_TYPE_TAKER,
    calculate_execution_fee,
)
from app.signals.risk_engine import position_size

# Matches SignalConfig / bos research blotter default.
DEFAULT_PAPER_SLIPPAGE_RATE = 0.0002
# Keep in sync with app.research.backtest_ui_config.V1_DEFAULT_LEVERAGE (avoid
# importing backtest_ui_config here — it pulls heavy research package deps).
DEFAULT_PAPER_LEVERAGE = 2.0

# Binance USDT-M typical min notional fallback when exchange filters absent.
_DEFAULT_MIN_NOTIONAL = 5.0

# Sensible fallbacks when discovery / market_store has no filters yet.
_SYMBOL_FILTER_DEFAULTS: dict[str, tuple[float, float]] = {
    "SOLUSDT": (0.01, 0.1),
    "BTCUSDT": (0.10, 0.001),
    "ETHUSDT": (0.01, 0.001),
}
_GENERIC_TICK = 0.01
_GENERIC_STEP = 0.001


def resolve_min_notional(symbol: str) -> tuple[float, str]:
    """Return (min_notional_usd, source). Fail-closed uses exchange filter or default."""
    sym = str(symbol or "").upper()
    try:
        from app.services.market_store import market_store

        info = market_store.symbols.get(sym)
        if info is not None:
            raw = getattr(info, "min_notional", None)
            if raw is None:
                raw = getattr(info, "minNotional", None)
            if raw is not None and float(raw) > 0:
                return float(raw), "market_store"
    except Exception:  # noqa: BLE001
        pass
    return float(_DEFAULT_MIN_NOTIONAL), "default"


def _precision_to_step(precision: int | None) -> float | None:
    if precision is None:
        return None
    try:
        p = int(precision)
    except (TypeError, ValueError):
        return None
    if p < 0 or p > 12:
        return None
    return float(Decimal("1").scaleb(-p))


def resolve_symbol_filters(symbol: str) -> tuple[float, float, str]:
    """Return (tick_size, lot_step, source) for paper rounding."""
    sym = str(symbol or "").upper()
    tick: float | None = None
    step: float | None = None
    source = "default"

    try:
        from app.services.market_store import market_store

        info = market_store.symbols.get(sym)
        if info is not None:
            raw_tick = getattr(info, "tick_size", None)
            raw_step = getattr(info, "step_size", None)
            if raw_tick is not None and float(raw_tick) > 0:
                tick = float(raw_tick)
            if raw_step is not None and float(raw_step) > 0:
                step = float(raw_step)
            if tick is None:
                tick = _precision_to_step(getattr(info, "price_precision", None))
            if step is None:
                step = _precision_to_step(getattr(info, "qty_precision", None))
            if tick is not None or step is not None:
                source = "market_store"
    except Exception:  # noqa: BLE001
        pass

    defaults = _SYMBOL_FILTER_DEFAULTS.get(sym)
    if tick is None or tick <= 0:
        tick = defaults[0] if defaults else _GENERIC_TICK
        if source == "market_store":
            source = "market_store+default_tick"
        else:
            source = "symbol_default" if defaults else "generic_default"
    if step is None or step <= 0:
        step = defaults[1] if defaults else _GENERIC_STEP
        if "market_store" in source and "default" not in source:
            source = "market_store+default_step"
        elif source in ("default",):
            source = "symbol_default" if defaults else "generic_default"

    return float(tick), float(step), source


def round_price_to_tick(
    price: float,
    tick: float,
    *,
    mode: str = "nearest",
) -> float:
    """Round a price to exchange tick (nearest / up / down)."""
    if price <= 0 or tick <= 0:
        return float(price)
    px = Decimal(str(price))
    tk = Decimal(str(tick))
    units = px / tk
    mode_u = str(mode or "nearest").lower()
    if mode_u in ("up", "ceil", "ceiling"):
        rounded_units = units.to_integral_value(rounding=ROUND_CEILING)
    elif mode_u in ("down", "floor"):
        rounded_units = units.to_integral_value(rounding=ROUND_FLOOR)
    else:
        rounded_units = units.to_integral_value(rounding=ROUND_HALF_UP)
    return float(rounded_units * tk)


def floor_qty_to_step(quantity: float, step: float) -> float:
    if quantity <= 0 or step <= 0:
        return 0.0
    step_f = max(float(step), 1e-12)
    # Avoid float drift: floor via Decimal.
    q = Decimal(str(quantity))
    s = Decimal(str(step_f))
    units = (q / s).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * s)


def size_paper_long(
    *,
    symbol: str,
    account_equity: float,
    risk_percent: float,
    entry: float,
    stop: float,
    leverage: float = DEFAULT_PAPER_LEVERAGE,
    fee_rate: float = DEFAULT_TAKER_FEE,
    slippage_rate: float = DEFAULT_PAPER_SLIPPAGE_RATE,
    preferred_quantity: float | None = None,
) -> dict[str, Any]:
    """Size a paper LONG with tick/lot rounding and max-leverage notional cap.

    ``risk_usd`` is recomputed after rounding: abs(entry - stop) * qty.
    """
    tick, step, filter_source = resolve_symbol_filters(symbol)
    lev = max(1.0, float(leverage or DEFAULT_PAPER_LEVERAGE))

    # LONG adverse rounding: buy up, stop down (tighter risk distance).
    entry_r = round_price_to_tick(float(entry), tick, mode="up")
    stop_r = round_price_to_tick(float(stop), tick, mode="down")
    if stop_r >= entry_r:
        # Degenerate after rounding — keep geometry by nudging stop one tick.
        stop_r = round_price_to_tick(entry_r - tick, tick, mode="down")
    if stop_r >= entry_r or entry_r <= 0:
        return {
            "entry_price": entry_r,
            "stop_price": stop_r,
            "quantity": 0.0,
            "risk_usd": 0.0,
            "notional": 0.0,
            "leverage": lev,
            "tick_size": tick,
            "lot_step": step,
            "filter_source": filter_source,
            "capped_by_leverage": False,
            "entry_fee_usd": 0.0,
            "slippage_rate": float(slippage_rate),
            "fee_rate": float(fee_rate),
            "reason": "invalid_geometry_after_rounding",
        }

    sized = position_size(
        account_equity=float(account_equity),
        risk_percent=float(risk_percent),
        entry=entry_r,
        stop=stop_r,
        contract_quantity_step=step,
        minimum_quantity=step,
        leverage=lev,
        fee_rate=float(fee_rate),
        slippage_rate=float(slippage_rate),
        direction="LONG",
    )
    risk_qty = float(sized.get("final_quantity") or 0.0)
    if preferred_quantity is not None and float(preferred_quantity) > 0:
        risk_qty = floor_qty_to_step(float(preferred_quantity), step)

    max_notional = float(account_equity) * lev
    max_qty = floor_qty_to_step(max_notional / entry_r, step) if entry_r > 0 else 0.0
    capped = risk_qty > max_qty + 1e-15
    qty = min(risk_qty, max_qty) if max_qty > 0 else 0.0
    qty = floor_qty_to_step(qty, step)

    risk_per = entry_r - stop_r
    risk_usd = risk_per * qty if qty > 0 else 0.0
    notional = qty * entry_r
    min_notional, min_notional_source = resolve_min_notional(symbol)
    if qty > 0 and notional + 1e-12 < float(min_notional):
        # Fail closed — never upsize beyond 2% risk to meet exchange minima.
        return {
            "entry_price": entry_r,
            "stop_price": stop_r,
            "quantity": 0.0,
            "risk_usd": 0.0,
            "notional": 0.0,
            "leverage": lev,
            "tick_size": tick,
            "lot_step": step,
            "filter_source": filter_source,
            "capped_by_leverage": False,
            "entry_fee_usd": 0.0,
            "slippage_rate": float(slippage_rate),
            "fee_rate": float(fee_rate),
            "min_notional": float(min_notional),
            "min_notional_source": min_notional_source,
            "reason": "notional_below_min",
        }
    notional = qty * entry_r
    entry_fee = 0.0
    if qty > 0 and entry_r > 0:
        entry_fee = calculate_execution_fee(
            price=entry_r,
            quantity=qty,
            fee_type=FEE_TYPE_TAKER,
            taker_rate=float(fee_rate),
        )

    return {
        "entry_price": entry_r,
        "stop_price": stop_r,
        "quantity": qty,
        "risk_usd": risk_usd,
        "risk_per_unit": risk_per,
        "notional": notional,
        "leverage": lev,
        "tick_size": tick,
        "lot_step": step,
        "filter_source": filter_source,
        "capped_by_leverage": capped,
        "raw_risk_quantity": risk_qty,
        "max_leverage_quantity": max_qty,
        "entry_fee_usd": entry_fee,
        "slippage_rate": float(slippage_rate),
        "fee_rate": float(fee_rate),
        "reason": None if qty > 0 else "zero_quantity",
    }


def paper_execution_snippet(sized: dict[str, Any]) -> dict[str, Any]:
    """Compact execution metadata for signal_snippet (no DB migration)."""
    return {
        "tick_size": sized.get("tick_size"),
        "lot_step": sized.get("lot_step"),
        "filter_source": sized.get("filter_source"),
        "leverage": sized.get("leverage"),
        "capped_by_leverage": bool(sized.get("capped_by_leverage")),
        "entry_fee_usd": sized.get("entry_fee_usd"),
        "fee_rate": sized.get("fee_rate"),
        "slippage_rate": sized.get("slippage_rate"),
        "notional": sized.get("notional"),
    }


def net_paper_pnl(
    *,
    entry_price: float,
    exit_price: float,
    quantity: float,
    risk_usd: float,
    side: str = "LONG",
    entry_fee_usd: float | None = None,
    fee_rate: float = DEFAULT_TAKER_FEE,
    slippage_rate: float = DEFAULT_PAPER_SLIPPAGE_RATE,
) -> dict[str, float]:
    """Gross/net PnL and R with taker fees + round-trip slippage cost."""
    qty = float(quantity)
    entry = float(entry_price)
    exit_px = float(exit_price)
    if qty <= 0:
        return {
            "gross_pnl_usd": 0.0,
            "pnl_usd": 0.0,
            "r_multiple": 0.0,
            "entry_fee_usd": 0.0,
            "exit_fee_usd": 0.0,
            "slippage_usd": 0.0,
            "total_cost_usd": 0.0,
        }

    side_u = str(side or "LONG").upper()
    if side_u == "SHORT":
        gross = qty * (entry - exit_px)
    else:
        gross = qty * (exit_px - entry)

    if entry_fee_usd is None:
        fee_entry = calculate_execution_fee(
            price=entry,
            quantity=qty,
            fee_type=FEE_TYPE_TAKER,
            taker_rate=float(fee_rate),
        )
    else:
        fee_entry = float(entry_fee_usd)

    fee_exit = calculate_execution_fee(
        price=exit_px,
        quantity=qty,
        fee_type=FEE_TYPE_TAKER,
        taker_rate=float(fee_rate),
    )
    # Positive cost: round-trip notional * slippage (matches research blotter style).
    slip = abs(float(slippage_rate)) * (abs(entry * qty) + abs(exit_px * qty))
    total_fee = fee_entry + fee_exit  # NEGATIVE_COST
    net = float(gross) + float(total_fee) - slip
    risk = float(risk_usd)
    r_mult = (net / risk) if risk > 0 else 0.0
    return {
        "gross_pnl_usd": float(gross),
        "pnl_usd": float(net),
        "r_multiple": float(r_mult),
        "entry_fee_usd": float(fee_entry),
        "exit_fee_usd": float(fee_exit),
        "slippage_usd": float(-slip),
        "total_cost_usd": float(total_fee) - slip,
    }
