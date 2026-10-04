"""Direction-neutral trade geometry, PnL, triggers, and stop-distance helpers.

Shared math only. Does not enable SHORT trading, paper opens, Telegram, or
v1 production approval. Missing direction fails closed — never silently LONG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.signals.schemas import Direction

# Same-candle SL/TP precedence used by research/live R simulators historically.
# CONSERVATIVE / SL_FIRST preserves existing LONG backtest semantics.
SAME_CANDLE_PRECEDENCE_SL_FIRST = "SL_FIRST"


class DirectionRequiredError(ValueError):
    """Raised when shared math requires an explicit trade direction."""


class TradeGeometryError(ValueError):
    """Raised when entry/stop/TP geometry is invalid for the given direction."""


def require_direction(direction: Direction | str | None) -> Direction:
    """Parse and require LONG/SHORT. Never defaults missing values to LONG."""
    if direction is None:
        raise DirectionRequiredError("direction is required")
    if isinstance(direction, Direction):
        return direction
    text = str(direction).strip().upper()
    if not text:
        raise DirectionRequiredError("direction is required")
    try:
        return Direction(text)
    except ValueError as exc:
        raise DirectionRequiredError(
            f"direction must be LONG or SHORT, got {direction!r}"
        ) from exc


def parse_direction_optional(direction: Direction | str | None) -> Direction | None:
    """Parse LONG/SHORT when present; return None when absent (no LONG default)."""
    if direction is None:
        return None
    if isinstance(direction, Direction):
        return direction
    text = str(direction).strip().upper()
    if not text:
        return None
    try:
        return Direction(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class GeometryValidation:
    ok: bool
    direction: Direction | None
    entry_price: float | None
    stop_price: float | None
    take_profit_price: float | None
    reason: str | None = None

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise TradeGeometryError(self.reason or "Invalid trade geometry")


def validate_trade_geometry(
    direction: Direction | str | None,
    entry_price: float | None,
    stop_price: float | None,
    take_profit_price: float | None = None,
    *,
    require_take_profit: bool = False,
) -> GeometryValidation:
    """Validate directional entry/stop/(optional) TP geometry.

    LONG:  stop < entry (< TP when provided)
    SHORT: stop > entry (> TP when provided)
    """
    try:
        d = require_direction(direction)
    except DirectionRequiredError as exc:
        return GeometryValidation(
            ok=False,
            direction=None,
            entry_price=None,
            stop_price=None,
            take_profit_price=None,
            reason=str(exc),
        )

    try:
        entry = float(entry_price) if entry_price is not None else None
        stop = float(stop_price) if stop_price is not None else None
        tp = float(take_profit_price) if take_profit_price is not None else None
    except (TypeError, ValueError):
        return GeometryValidation(
            ok=False,
            direction=d,
            entry_price=None,
            stop_price=None,
            take_profit_price=None,
            reason="Prices must be numeric",
        )

    if entry is None or stop is None:
        return GeometryValidation(
            ok=False,
            direction=d,
            entry_price=entry,
            stop_price=stop,
            take_profit_price=tp,
            reason="entry_price and stop_price are required",
        )
    if entry <= 0 or stop <= 0:
        return GeometryValidation(
            ok=False,
            direction=d,
            entry_price=entry,
            stop_price=stop,
            take_profit_price=tp,
            reason="Prices must be positive",
        )
    if stop == entry:
        return GeometryValidation(
            ok=False,
            direction=d,
            entry_price=entry,
            stop_price=stop,
            take_profit_price=tp,
            reason="stop_price must not equal entry_price",
        )

    if d == Direction.LONG:
        if not (stop < entry):
            return GeometryValidation(
                ok=False,
                direction=d,
                entry_price=entry,
                stop_price=stop,
                take_profit_price=tp,
                reason="LONG requires stop_price < entry_price",
            )
    else:
        if not (stop > entry):
            return GeometryValidation(
                ok=False,
                direction=d,
                entry_price=entry,
                stop_price=stop,
                take_profit_price=tp,
                reason="SHORT requires stop_price > entry_price",
            )

    if tp is None:
        if require_take_profit:
            return GeometryValidation(
                ok=False,
                direction=d,
                entry_price=entry,
                stop_price=stop,
                take_profit_price=None,
                reason="take_profit_price is required",
            )
        return GeometryValidation(
            ok=True,
            direction=d,
            entry_price=entry,
            stop_price=stop,
            take_profit_price=None,
            reason=None,
        )

    if tp <= 0:
        return GeometryValidation(
            ok=False,
            direction=d,
            entry_price=entry,
            stop_price=stop,
            take_profit_price=tp,
            reason="take_profit_price must be positive",
        )
    if tp == entry:
        return GeometryValidation(
            ok=False,
            direction=d,
            entry_price=entry,
            stop_price=stop,
            take_profit_price=tp,
            reason="take_profit_price must not equal entry_price",
        )

    if d == Direction.LONG:
        if not (tp > entry):
            return GeometryValidation(
                ok=False,
                direction=d,
                entry_price=entry,
                stop_price=stop,
                take_profit_price=tp,
                reason="LONG requires take_profit_price > entry_price",
            )
    else:
        if not (tp < entry):
            return GeometryValidation(
                ok=False,
                direction=d,
                entry_price=entry,
                stop_price=stop,
                take_profit_price=tp,
                reason="SHORT requires take_profit_price < entry_price",
            )

    return GeometryValidation(
        ok=True,
        direction=d,
        entry_price=entry,
        stop_price=stop,
        take_profit_price=tp,
        reason=None,
    )


def stop_distance(
    direction: Direction | str | None,
    entry_price: float,
    stop_price: float,
) -> float:
    """Directional stop distance. Rejects invalid geometry / missing direction."""
    geom = validate_trade_geometry(direction, entry_price, stop_price)
    geom.raise_if_invalid()
    assert geom.direction is not None
    assert geom.entry_price is not None and geom.stop_price is not None
    if geom.direction == Direction.LONG:
        return geom.entry_price - geom.stop_price
    return geom.stop_price - geom.entry_price


def calculate_gross_pnl(
    direction: Direction | str | None,
    entry_price: float,
    exit_price: float,
    quantity: float,
) -> float:
    """Directional gross PnL. Missing direction fails closed."""
    d = require_direction(direction)
    entry = float(entry_price)
    exit_px = float(exit_price)
    qty = float(quantity)
    if qty <= 0:
        raise ValueError("quantity must be positive")
    if entry <= 0 or exit_px <= 0:
        raise ValueError("entry_price and exit_price must be positive")
    if d == Direction.LONG:
        return (exit_px - entry) * qty
    return (entry - exit_px) * qty


def stop_triggered(
    direction: Direction | str | None,
    candle_high: float,
    candle_low: float,
    stop_price: float,
) -> bool:
    d = require_direction(direction)
    high = float(candle_high)
    low = float(candle_low)
    stop = float(stop_price)
    if d == Direction.LONG:
        return low <= stop
    return high >= stop


def take_profit_triggered(
    direction: Direction | str | None,
    candle_high: float,
    candle_low: float,
    take_profit_price: float,
) -> bool:
    d = require_direction(direction)
    high = float(candle_high)
    low = float(candle_low)
    tp = float(take_profit_price)
    if d == Direction.LONG:
        return high >= tp
    return low <= tp


def resolve_same_candle_exit(
    direction: Direction | str | None,
    candle_high: float,
    candle_low: float,
    stop_price: float,
    take_profit_price: float,
    *,
    precedence: str = SAME_CANDLE_PRECEDENCE_SL_FIRST,
) -> dict[str, Any]:
    """Resolve SL/TP when both could hit on one candle.

    Default precedence is SL_FIRST (conservative), matching historical LONG
    research/live R simulators. Do not change without a dedicated migration.
    """
    d = require_direction(direction)
    hit_stop = stop_triggered(d, candle_high, candle_low, stop_price)
    hit_tp = take_profit_triggered(d, candle_high, candle_low, take_profit_price)
    ambiguous = hit_stop and hit_tp
    if not hit_stop and not hit_tp:
        return {
            "outcome": None,
            "exit_price": None,
            "ambiguous": False,
            "precedence": precedence,
            "direction": d.value,
        }
    if ambiguous:
        if precedence != SAME_CANDLE_PRECEDENCE_SL_FIRST:
            raise ValueError(f"Unsupported same-candle precedence: {precedence}")
        return {
            "outcome": "STOP",
            "exit_price": float(stop_price),
            "ambiguous": True,
            "precedence": precedence,
            "direction": d.value,
        }
    if hit_stop:
        return {
            "outcome": "STOP",
            "exit_price": float(stop_price),
            "ambiguous": False,
            "precedence": precedence,
            "direction": d.value,
        }
    return {
        "outcome": "TP",
        "exit_price": float(take_profit_price),
        "ambiguous": False,
        "precedence": precedence,
        "direction": d.value,
    }
