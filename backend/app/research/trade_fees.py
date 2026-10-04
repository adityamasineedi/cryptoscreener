"""Size trades and apply exchange fees for research blotter display.

Canonical fee model (research):
- fee_sign = NEGATIVE_COST (fees are negative quote-currency costs)
- fee_basis = EXECUTED_NOTIONAL (abs(price * quantity))
- entry: TAKER for MARKET, MAKER for LIMIT_RETEST
- exit: TAKER (MARKET)
- leverage affects margin only; never multiplies fee notional

net_pnl = gross_pnl + total_fee  (total_fee <= 0)
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Sequence


# Binance USDT-M VIP0 defaults (fraction of notional)
DEFAULT_TAKER_FEE = 0.0004  # 0.04%
DEFAULT_MAKER_FEE = 0.0002  # 0.02%
DEFAULT_LEVERAGE = 2.0

FEE_SIGN_NEGATIVE_COST = "NEGATIVE_COST"
FEE_SIGN_POSITIVE_COST = "POSITIVE_COST"
FEE_BASIS_EXECUTED_NOTIONAL = "EXECUTED_NOTIONAL"
FEE_CURRENCY_QUOTE = "QUOTE"

FEE_TYPE_TAKER = "TAKER"
FEE_TYPE_MAKER = "MAKER"

_DEC_Q = Decimal("0.00000001")


class FeeCalculationError(ValueError):
    """Invalid inputs for research fee calculation."""


def _d(value: float | int | str | Decimal) -> Decimal:
    return Decimal(str(value))


def normalize_fee_type(fee_type: str | None) -> str:
    raw = str(fee_type or "").strip().upper()
    if raw in ("", "NONE", "NULL"):
        raise FeeCalculationError("missing_fee_type")
    if raw in (FEE_TYPE_TAKER, "MARKET", "TAKER_FEE"):
        return FEE_TYPE_TAKER
    if raw in (FEE_TYPE_MAKER, "LIMIT", "LIMIT_RETEST", "MAKER_FEE"):
        return FEE_TYPE_MAKER
    raise FeeCalculationError(f"unknown_fee_type:{raw}")


def fee_type_for_entry(entry_type: str | None) -> str:
    if (entry_type or "").upper() == "LIMIT_RETEST":
        return FEE_TYPE_MAKER
    return FEE_TYPE_TAKER


def applicable_fee_rate(
    fee_type: str,
    *,
    maker_rate: float,
    taker_rate: float,
) -> float:
    ft = normalize_fee_type(fee_type)
    if ft == FEE_TYPE_MAKER:
        return float(maker_rate)
    return float(taker_rate)


def calculate_execution_fee(
    *,
    price: float,
    quantity: float,
    fee_type: str,
    maker_rate: float = DEFAULT_MAKER_FEE,
    taker_rate: float = DEFAULT_TAKER_FEE,
    fee_sign: str = FEE_SIGN_NEGATIVE_COST,
) -> float:
    """Canonical per-leg execution fee in quote currency.

    executed_notional = abs(price * quantity)
    fee_magnitude = executed_notional * applicable_rate
    NEGATIVE_COST → returns -fee_magnitude
    POSITIVE_COST → returns +fee_magnitude
    """
    qty = _d(quantity)
    px = _d(price)
    if qty == 0:
        raise FeeCalculationError("zero_quantity")
    if qty < 0:
        raise FeeCalculationError("negative_quantity")
    if px < 0:
        raise FeeCalculationError("negative_price")

    rate = _d(applicable_fee_rate(fee_type, maker_rate=maker_rate, taker_rate=taker_rate))
    if rate < 0:
        raise FeeCalculationError("negative_fee_rate")

    notional = abs(px * qty)
    magnitude = (notional * rate).quantize(_DEC_Q, rounding=ROUND_HALF_UP)
    sign = str(fee_sign or FEE_SIGN_NEGATIVE_COST).upper()
    if sign == FEE_SIGN_POSITIVE_COST:
        return float(magnitude)
    if sign == FEE_SIGN_NEGATIVE_COST:
        return float(-magnitude)
    raise FeeCalculationError(f"unknown_fee_sign:{fee_sign}")


def _fee_for_entry_type(
    entry_type: str | None,
    *,
    taker_fee: float,
    maker_fee: float,
) -> float:
    return applicable_fee_rate(
        fee_type_for_entry(entry_type),
        maker_rate=maker_fee,
        taker_rate=taker_fee,
    )


def enrich_trade_execution(
    trade: Mapping[str, Any],
    *,
    risk_usd: float,
    taker_fee: float = DEFAULT_TAKER_FEE,
    maker_fee: float = DEFAULT_MAKER_FEE,
    exit_fee: float | None = None,
    leverage: float = DEFAULT_LEVERAGE,
    fee_sign: str = FEE_SIGN_NEGATIVE_COST,
) -> dict[str, Any]:
    """Attach qty, gross/net PnL, fee, and margin fields for one research trade."""
    entry = float(trade.get("entry_price") or 0.0)
    stop = float(trade.get("stop_price") or 0.0)
    exit_px = trade.get("exit_price")
    direction = str(trade.get("direction") or "LONG").upper()
    snap = trade.get("condition_snapshot") or {}
    entry_type = snap.get("entry_type") if isinstance(snap, Mapping) else None
    if entry_type is None:
        entry_type = trade.get("entry_type")
    entry_type_s = str(entry_type or "MARKET").upper()
    if entry_type_s not in ("MARKET", "LIMIT_RETEST"):
        entry_type_s = "MARKET"

    risk_per_unit = abs(entry - stop)
    qty = (float(risk_usd) / risk_per_unit) if risk_per_unit > 0 else 0.0

    entry_fee_type = fee_type_for_entry(entry_type_s)
    exit_fee_type = FEE_TYPE_TAKER
    fee_entry_rate = applicable_fee_rate(
        entry_fee_type, maker_rate=maker_fee, taker_rate=taker_fee
    )
    fee_exit_rate = (
        float(exit_fee) if exit_fee is not None else float(taker_fee)
    )

    fee_entry = 0.0
    fee_exit = 0.0
    gross_pnl = None
    net_pnl = None
    r_gross = trade.get("r_multiple")
    r_net = None
    notional_entry = abs(qty * entry) if qty else 0.0
    notional_exit = None

    if qty > 0 and entry > 0:
        fee_entry = calculate_execution_fee(
            price=entry,
            quantity=qty,
            fee_type=entry_fee_type,
            maker_rate=maker_fee,
            taker_rate=taker_fee,
            fee_sign=fee_sign,
        )

    if exit_px is not None and qty > 0:
        exit_f = float(exit_px)
        notional_exit = abs(qty * exit_f)
        # Exit rate may be overridden via exit_fee argument (rate, not dollars).
        if exit_fee is not None:
            # Treat exit_fee as an explicit rate with TAKER/MAKER inferred by magnitude
            # relative to maker/taker defaults when possible; otherwise TAKER.
            exit_fee_type = (
                FEE_TYPE_MAKER
                if abs(float(exit_fee) - float(maker_fee)) < 1e-12
                else FEE_TYPE_TAKER
            )
            fee_exit = calculate_execution_fee(
                price=exit_f,
                quantity=qty,
                fee_type=exit_fee_type,
                maker_rate=float(exit_fee),
                taker_rate=float(exit_fee),
                fee_sign=fee_sign,
            )
            fee_exit_rate = float(exit_fee)
        else:
            fee_exit = calculate_execution_fee(
                price=exit_f,
                quantity=qty,
                fee_type=exit_fee_type,
                maker_rate=maker_fee,
                taker_rate=taker_fee,
                fee_sign=fee_sign,
            )
        if direction == "SHORT":
            gross_pnl = qty * (entry - exit_f)
        else:
            gross_pnl = qty * (exit_f - entry)
        total_fee = fee_entry + fee_exit
        if str(fee_sign).upper() == FEE_SIGN_POSITIVE_COST:
            net_pnl = float(gross_pnl) - total_fee
        else:
            net_pnl = float(gross_pnl) + total_fee
        if float(risk_usd) > 0:
            r_net = net_pnl / float(risk_usd)

    total_fee = fee_entry + fee_exit
    lev = max(1.0, float(leverage or DEFAULT_LEVERAGE))
    margin_usd = (notional_entry / lev) if lev > 0 else None
    liquidation_price = None
    if entry > 0 and lev > 0:
        if direction == "SHORT":
            liquidation_price = entry * (1.0 + 1.0 / lev)
        else:
            liquidation_price = entry * (1.0 - 1.0 / lev)

    trade_id = (
        trade.get("trade_id")
        or trade.get("id")
        or trade.get("trade_no")
        or trade.get("source_index")
    )

    return {
        **dict(trade),
        "trade_id": trade_id,
        "entry_type": entry_type_s,
        "qty": qty,
        "quantity": qty,
        "risk_usd": float(risk_usd),
        "risk_per_unit": risk_per_unit if risk_per_unit else None,
        "leverage": lev,
        "notional": notional_entry,
        "notional_entry_usd": notional_entry,
        "notional_exit_usd": notional_exit,
        "margin_usd": margin_usd,
        "liquidation_price": liquidation_price,
        "fee_currency": FEE_CURRENCY_QUOTE,
        "fee_sign": str(fee_sign).upper(),
        "fee_basis": FEE_BASIS_EXECUTED_NOTIONAL,
        "fee_entry_rate": fee_entry_rate,
        "fee_exit_rate": fee_exit_rate,
        "fee_rate": fee_entry_rate,
        "entry_fee_type": entry_fee_type,
        "exit_fee_type": exit_fee_type,
        "fee_type": f"{entry_fee_type}/{exit_fee_type}",
        "entry_fee": fee_entry,
        "exit_fee": fee_exit,
        "total_fee": total_fee,
        "fees": total_fee,
        "fee_entry_usd": fee_entry,
        "fee_exit_usd": fee_exit,
        "fee_total_usd": total_fee,
        "gross_pnl": gross_pnl,
        "gross_pnl_usd": gross_pnl,
        "net_pnl": net_pnl,
        "net_pnl_usd": net_pnl,
        "r_gross": r_gross,
        "r_net": r_net,
    }


def enrich_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    risk_usd: float,
    taker_fee: float = DEFAULT_TAKER_FEE,
    maker_fee: float = DEFAULT_MAKER_FEE,
    exit_fee: float | None = None,
    leverage: float = DEFAULT_LEVERAGE,
    closed_only: bool = True,
    fee_sign: str = FEE_SIGN_NEGATIVE_COST,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, t in enumerate(trades):
        outcome = str(t.get("outcome") or "")
        if closed_only and outcome in ("", "OPEN", "None"):
            continue
        row = enrich_trade_execution(
            t,
            risk_usd=risk_usd,
            taker_fee=taker_fee,
            maker_fee=maker_fee,
            exit_fee=exit_fee,
            leverage=leverage,
            fee_sign=fee_sign,
        )
        row["trade_no"] = len(out) + 1
        row["source_index"] = i
        if row.get("trade_id") is None:
            row["trade_id"] = f"t{row['trade_no']}"
        out.append(row)
    return out
