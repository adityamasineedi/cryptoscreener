"""Size trades and apply exchange fees for research blotter display.

Fees are modeled as round-trip notional rates (Binance USDT-M style).
Leverage only affects margin (notional / leverage); qty is still sized from
risk_$ / |entry − stop|. Does not mutate live signal state.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


# Binance USDT-M VIP0 defaults (fraction of notional)
DEFAULT_TAKER_FEE = 0.0004  # 0.04%
DEFAULT_MAKER_FEE = 0.0002  # 0.02%
DEFAULT_LEVERAGE = 2.0


def _fee_for_entry_type(
    entry_type: str | None,
    *,
    taker_fee: float,
    maker_fee: float,
) -> float:
    if (entry_type or "").upper() == "LIMIT_RETEST":
        return maker_fee
    return taker_fee


def enrich_trade_execution(
    trade: Mapping[str, Any],
    *,
    risk_usd: float,
    taker_fee: float = DEFAULT_TAKER_FEE,
    maker_fee: float = DEFAULT_MAKER_FEE,
    exit_fee: float | None = None,
    leverage: float = DEFAULT_LEVERAGE,
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

    risk_per_unit = abs(entry - stop)
    qty = (float(risk_usd) / risk_per_unit) if risk_per_unit > 0 else 0.0
    fee_entry_rate = _fee_for_entry_type(
        str(entry_type) if entry_type else None,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
    )
    fee_exit_rate = float(exit_fee) if exit_fee is not None else taker_fee

    fee_entry = abs(qty * entry) * fee_entry_rate if qty and entry else 0.0
    fee_exit = 0.0
    gross_pnl = None
    net_pnl = None
    r_gross = trade.get("r_multiple")
    r_net = None

    if exit_px is not None and qty > 0:
        exit_f = float(exit_px)
        fee_exit = abs(qty * exit_f) * fee_exit_rate
        if direction == "SHORT":
            gross_pnl = qty * (entry - exit_f)
        else:
            gross_pnl = qty * (exit_f - entry)
        net_pnl = gross_pnl - fee_entry - fee_exit
        if float(risk_usd) > 0:
            r_net = net_pnl / float(risk_usd)

    lev = max(1.0, float(leverage or DEFAULT_LEVERAGE))
    notional_entry = abs(qty * entry) if qty else 0.0
    margin_usd = (notional_entry / lev) if lev > 0 else None
    # Isolated approx (ignores mmr): long liq ≈ entry * (1 - 1/lev)
    liquidation_price = None
    if entry > 0 and lev > 0:
        if direction == "SHORT":
            liquidation_price = entry * (1.0 + 1.0 / lev)
        else:
            liquidation_price = entry * (1.0 - 1.0 / lev)

    return {
        **dict(trade),
        "entry_type": entry_type or "MARKET",
        "qty": qty,
        "risk_usd": float(risk_usd),
        "risk_per_unit": risk_per_unit if risk_per_unit else None,
        "leverage": lev,
        "notional_entry_usd": notional_entry,
        "notional_exit_usd": abs(qty * float(exit_px)) if exit_px is not None and qty else None,
        "margin_usd": margin_usd,
        "liquidation_price": liquidation_price,
        "fee_entry_rate": fee_entry_rate,
        "fee_exit_rate": fee_exit_rate,
        "fee_entry_usd": fee_entry,
        "fee_exit_usd": fee_exit,
        "fee_total_usd": fee_entry + fee_exit,
        "gross_pnl_usd": gross_pnl,
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
        )
        row["trade_no"] = len(out) + 1
        row["source_index"] = i
        out.append(row)
    return out
