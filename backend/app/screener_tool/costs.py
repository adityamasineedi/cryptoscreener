"""D144 real costs: cost as R for a setup, and the journal's fee, P&L and R maths.

Owner D144 (GATES E32): taker 0.05% per side, slippage 2 bp per side, 5 bp per side on the thin coins
AAVE, APT, ARB, DOT, ETC, OP and FIL. Pure functions, no I/O.

Cost as R (owner D188 part 1a)::

    round trip  = 2 x (taker + slippage)            = 2 x (0.05% + 0.02%) = 0.14% of entry
                                                      (thin coin: 2 x (0.05% + 0.05%) = 0.20%)
    stop dist   = |entry - stop| / entry
    cost as R   = round trip / stop dist            = 0.0014 x entry / |entry - stop|
    COST TOO HIGH when cost as R > 0.25

Journal P&L (owner D188 part 2b), qty in coins, side = +1 LONG / -1 SHORT::

    gross $     = qty x (exit - entry) x side
    fees $      = qty x (entry + exit) x 0.05%                (taker, each side)
    slippage $  = qty x (entry + exit) x 0.02%                (0.05% on a thin coin, each side)
    net $       = gross $ - fees $ - slippage $
    risk $      = qty x |entry - stop|
    R (net)     = net $ / risk $
"""

from __future__ import annotations

import math
from typing import Any, Iterable

COST_MODEL = "D144"
TAKER_FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0002
THIN_COIN_SLIPPAGE_RATE = 0.0005
THIN_COINS = frozenset({"AAVE", "APT", "ARB", "DOT", "ETC", "OP", "FIL"})
FEE_SIDES = 2
COST_R_MAX = 0.25
_EPS = 1e-12

COST_AS_R_FORMULA = (
    "cost as R = 2 x (taker 0.05% + slippage 0.02%) x entry / |entry - stop| "
    "(slippage 0.05% on AAVE/APT/ARB/DOT/ETC/OP/FIL, D144); COST TOO HIGH above 0.25 R"
)
JOURNAL_FORMULA = (
    "net $ = qty x (exit - entry) x side - qty x (entry + exit) x (taker 0.05% + slippage 0.02%); "
    "R = net $ / (qty x |entry - stop|); slippage 0.05% per side on AAVE/APT/ARB/DOT/ETC/OP/FIL (D144)"
)

_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD")


def base_asset(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    for quote in _QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


def is_thin_coin(symbol: str) -> bool:
    return base_asset(symbol) in THIN_COINS


def slippage_rate(symbol: str) -> float:
    return THIN_COIN_SLIPPAGE_RATE if is_thin_coin(symbol) else SLIPPAGE_RATE


def round_trip_rate(symbol: str) -> float:
    return FEE_SIDES * (TAKER_FEE_RATE + slippage_rate(symbol))


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def side_sign(side: str) -> int:
    s = str(side or "").strip().upper()
    if s == "LONG":
        return 1
    if s == "SHORT":
        return -1
    raise ValueError(f"side must be LONG or SHORT, not {side!r}")


def cost_as_r(entry: Any, stop: Any, symbol: str, *, side: str | None = None) -> dict[str, Any]:
    """Stop distance and D144 round-trip cost in R for one setup. ``status`` is OK unless levels are unusable."""
    slip = slippage_rate(symbol)
    rt = round_trip_rate(symbol)
    out: dict[str, Any] = {
        "status": "OK",
        "cost_model": COST_MODEL,
        "taker_fee_rate": TAKER_FEE_RATE,
        "slippage_rate": slip,
        "slippage_bps": round(slip * 10_000, 6),
        "thin_coin": is_thin_coin(symbol),
        "round_trip_rate": rt,
        "round_trip_pct": rt * 100.0,
        "stop_distance_pct": None,
        "cost_r": None,
        "cost_r_max": COST_R_MAX,
        "cost_ok": None,
        "cost_too_high": None,
    }
    e, s = _num(entry), _num(stop)
    if e is None or s is None or e <= 0 or s <= 0:
        out["status"] = "NO_LEVELS"
        return out
    dist = abs(e - s)
    if dist <= 0:
        out["status"] = "INVALID_STOP"
        return out
    if side:
        try:
            sgn = side_sign(side)
        except ValueError:
            sgn = 0
        if sgn and (e - s) * sgn <= 0:
            out["status"] = "STOP_WRONG_SIDE"
            return out
    cost_r = rt * e / dist
    too_high = cost_r > COST_R_MAX + _EPS
    out.update(
        stop_distance_pct=dist / e * 100.0,
        cost_r=cost_r,
        cost_ok=not too_high,
        cost_too_high=too_high,
    )
    return out


# --- journal ----------------------------------------------------------------------------------
def position_levels(
    side: str,
    entry: float,
    stop: float,
    *,
    quantity: float | None = None,
    risk_usd: float | None = None,
    targets: Iterable[float] = (),
    symbol: str = "",
) -> dict[str, Any]:
    """Quantity, risk $ and planned target R from entry and stop. Give exactly one of quantity or risk $."""
    sgn = side_sign(side)
    e, s = float(entry), float(stop)
    if not (e > 0 and s > 0):
        raise ValueError("entry and stop must be positive")
    if (e - s) * sgn <= 0:
        raise ValueError("stop must be below entry for a LONG and above entry for a SHORT")
    per_unit = abs(e - s)
    if (quantity is None) == (risk_usd is None):
        raise ValueError("give exactly one of quantity or risk_usd")
    if quantity is not None:
        qty = float(quantity)
        if not qty > 0:
            raise ValueError("quantity must be positive")
        risk = qty * per_unit
    else:
        risk = float(risk_usd)  # type: ignore[arg-type]
        if not risk > 0:
            raise ValueError("risk_usd must be positive")
        qty = risk / per_unit
    t_list = [float(t) for t in targets]
    for t in t_list:
        if not t > 0 or (t - e) * sgn <= 0:
            raise ValueError("each target must be on the profit side of entry")
    cost = cost_as_r(e, s, symbol, side=side) if symbol else None
    return {
        "quantity": qty,
        "risk_usd": risk,
        "risk_per_unit": per_unit,
        "stop_distance_pct": per_unit / e * 100.0,
        "targets": t_list,
        "targets_r": [(t - e) * sgn / per_unit for t in t_list],
        "notional_usd": qty * e,
        "cost_r": cost["cost_r"] if cost else None,
    }


def close_math(side: str, entry: float, stop: float, quantity: float, exit_price: float, symbol: str) -> dict[str, Any]:
    """D144 net result of a closed trade (see the module docstring for the formula)."""
    sgn = side_sign(side)
    e, s, q, x = float(entry), float(stop), float(quantity), float(exit_price)
    if not (e > 0 and s > 0 and q > 0 and x > 0):
        raise ValueError("entry, stop, quantity and exit must be positive")
    slip = slippage_rate(symbol)
    gross = q * (x - e) * sgn
    fees = q * (e + x) * TAKER_FEE_RATE
    slippage = q * (e + x) * slip
    net = gross - fees - slippage
    risk = q * abs(e - s)
    return {
        "gross_pnl_usd": gross,
        "fees_usd": fees,
        "slippage_usd": slippage,
        "costs_usd": fees + slippage,
        "net_pnl_usd": net,
        "risk_usd": risk,
        "gross_r": gross / risk if risk > 0 else None,
        "r_multiple": net / risk if risk > 0 else None,
        "taker_fee_rate": TAKER_FEE_RATE,
        "slippage_rate": slip,
        "cost_model": COST_MODEL,
        "formula": JOURNAL_FORMULA,
    }
