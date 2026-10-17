"""Replay engine-decided trades through VectorBT and compare them with the bar loop.

VectorBT is used only as an order simulator here. Every entry, stop, target and
exit was already decided by the strategy's own engine; this module turns those
closed trades into explicit-price orders for ``vbt.Portfolio.from_orders`` and
reads the fills back. Nothing in VectorBT decides a stop or a target: no
``from_signals``, no ``sl_stop``/``tp_stop``.

Ordering inside one bar. ``from_orders`` takes one order per row, while one bar
can hold the exit of an older trade, a new entry, and the exit of a trade that
entered on that same bar. Each order therefore gets its own row, sorted by
(bar, slot): slot 0 exits trades entered on an earlier bar, slot 1 enters,
slot 2 exits trades entered on this bar. Rows map back to bar indices.

One open position per column. Entries use ``SizeType.Amount`` (signed qty) and
exits use ``SizeType.TargetAmount`` = 0, so an engine trade list that ever held
two positions at once shows up as a trade-count or price mismatch instead of
being hidden.

Research only. No paper or live path calls this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

# Fields compared per trade, in the order a first mismatch is reported.
PARITY_FIELDS: tuple[str, ...] = (
    "entry_index",
    "entry_price",
    "stop",
    "target",
    "exit_index",
    "exit_price",
    "outcome",
    "net_r",
)

# Engine fees are quantized to 1e-8 USD per leg (trade_fees._DEC_Q); VectorBT's
# are not. 1e-8 R is far below anything the screen shows and far above that
# rounding for any risk_usd >= 1.
NET_R_ABS_TOL = 1e-8
PRICE_REL_TOL = 1e-12

_SLOT_EXIT_EARLIER = 0
_SLOT_ENTRY = 1
_SLOT_EXIT_SAME_BAR = 2


@dataclass(frozen=True)
class ReplayOrder:
    """One closed trade exactly as the bar-loop engine decided and filled it."""

    trade_key: str
    direction: str  # "LONG" or "SHORT"
    entry_index: int
    exit_index: int
    entry_price: float  # entry fill the engine used (after its own slippage)
    exit_price: float  # exit fill the engine used (after its own slippage)
    qty: float  # base units, > 0
    entry_fee_rate: float  # fraction of executed notional
    exit_fee_rate: float
    risk_usd: float  # the engine's net-R denominator for this trade
    stop: float | None
    target: float | None
    outcome: str
    net_r: float | None  # the engine's own net R for this trade


@dataclass
class ReplayedTrade:
    trade_key: str | None
    entry_index: int
    exit_index: int | None
    entry_price: float
    exit_price: float | None
    size: float
    fees_usd: float
    pnl_usd: float | None
    net_r: float | None
    stop: float | None
    target: float | None
    outcome: str | None
    closed: bool


@dataclass
class ParityResult:
    bar_loop_trades: int
    vectorbt_trades: int
    mismatches: int
    first_mismatch_field: str | None
    first_mismatch_trade: str | None = None
    details: list[dict[str, Any]] = field(default_factory=list)
    replayed: list[ReplayedTrade] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.mismatches == 0

    def to_dict(self, *, max_details: int = 20) -> dict[str, Any]:
        return {
            "bar_loop_trades": self.bar_loop_trades,
            "vectorbt_trades": self.vectorbt_trades,
            "mismatches": self.mismatches,
            "first_mismatch_field": self.first_mismatch_field,
            "first_mismatch_trade": self.first_mismatch_trade,
            "verdict": "PASS" if self.passed else "FAIL",
            "error": self.error,
            "details": self.details[:max_details],
        }


def _validate(order: ReplayOrder) -> None:
    if order.direction not in ("LONG", "SHORT"):
        raise ValueError(f"{order.trade_key}: direction {order.direction!r}")
    if order.exit_index < order.entry_index:
        raise ValueError(f"{order.trade_key}: exit_index before entry_index")
    for name in ("entry_price", "exit_price", "qty"):
        v = getattr(order, name)
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise ValueError(f"{order.trade_key}: {name}={v!r}")


def _order_rows(orders: Sequence[ReplayOrder]) -> list[tuple[int, int, int, str]]:
    """(bar, slot, order position, leg) for every leg, in execution order."""
    rows: list[tuple[int, int, int, str]] = []
    for pos, o in enumerate(orders):
        rows.append((o.entry_index, _SLOT_ENTRY, pos, "entry"))
        slot = _SLOT_EXIT_SAME_BAR if o.exit_index == o.entry_index else _SLOT_EXIT_EARLIER
        rows.append((o.exit_index, slot, pos, "exit"))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return rows


def replay_orders(orders: Sequence[ReplayOrder]) -> list[ReplayedTrade]:
    """Execute the engine's orders in VectorBT and return its closed trades."""
    import vectorbt as vbt
    from vectorbt.portfolio.enums import Direction, SizeType

    if not orders:
        return []
    for o in orders:
        _validate(o)
    rows = _order_rows(orders)
    n = len(rows)
    size = np.empty(n, dtype=np.float64)
    size_type = np.empty(n, dtype=np.int64)
    price = np.empty(n, dtype=np.float64)
    fees = np.empty(n, dtype=np.float64)
    bar_of_row = np.empty(n, dtype=np.int64)
    entry_row_owner: dict[int, int] = {}
    for r, (bar, _slot, pos, leg) in enumerate(rows):
        o = orders[pos]
        bar_of_row[r] = bar
        if leg == "entry":
            size[r] = o.qty if o.direction == "LONG" else -o.qty
            size_type[r] = SizeType.Amount
            price[r] = o.entry_price
            fees[r] = o.entry_fee_rate
            entry_row_owner[r] = pos
        else:
            size[r] = 0.0
            size_type[r] = SizeType.TargetAmount
            price[r] = o.exit_price
            fees[r] = o.exit_fee_rate
    pf = vbt.Portfolio.from_orders(
        close=price.copy(),  # valuation only; fills use ``price``
        size=size,
        size_type=size_type,
        direction=Direction.Both,
        price=price,
        fees=fees,
        init_cash=1e15,  # never the binding constraint: sizing is the engine's
        lock_cash=False,
        allow_partial=False,
        raise_reject=True,
        freq="1h",
    )
    rec = pf.trades.records_arr
    out: list[ReplayedTrade] = []
    for t in rec:
        entry_row = int(t["entry_idx"])
        exit_row = int(t["exit_idx"])
        closed = int(t["status"]) == 1  # TradeStatus.Closed
        pos = entry_row_owner.get(entry_row)
        o = orders[pos] if pos is not None else None
        fees_usd = float(t["entry_fees"]) + float(t["exit_fees"])
        pnl = float(t["pnl"]) if closed else None
        net_r = (pnl / o.risk_usd) if (o is not None and pnl is not None and o.risk_usd > 0) else None
        out.append(
            ReplayedTrade(
                trade_key=o.trade_key if o else None,
                entry_index=int(bar_of_row[entry_row]),
                exit_index=int(bar_of_row[exit_row]) if closed else None,
                entry_price=float(t["entry_price"]),
                exit_price=float(t["exit_price"]) if closed else None,
                size=float(t["size"]),
                fees_usd=fees_usd,
                pnl_usd=pnl,
                net_r=net_r,
                # Labels travel with the order the engine placed; VectorBT never
                # computes a stop, a target or an outcome.
                stop=o.stop if o else None,
                target=o.target if o else None,
                outcome=o.outcome if o else None,
                closed=closed,
            )
        )
    out.sort(key=lambda t: (t.entry_index, t.exit_index if t.exit_index is not None else -1))
    return out


def _price_eq(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=PRICE_REL_TOL, abs_tol=0.0)


def _field_mismatch(o: ReplayOrder, v: ReplayedTrade) -> str | None:
    for name in PARITY_FIELDS:
        a = getattr(o, name)
        b = getattr(v, name)
        if name in ("entry_index", "exit_index", "outcome"):
            ok = a == b
        elif name == "net_r":
            ok = (a is None and b is None) or (
                a is not None and b is not None and abs(float(a) - float(b)) <= NET_R_ABS_TOL
            )
        else:
            ok = _price_eq(a, b)
        if not ok:
            return name
    return None


def compare(orders: Sequence[ReplayOrder], replayed: Sequence[ReplayedTrade]) -> ParityResult:
    """Pair bar-loop and VectorBT trades in execution order; count trades that differ."""
    ordered = sorted(orders, key=lambda o: (o.entry_index, o.exit_index))
    res = ParityResult(
        bar_loop_trades=len(ordered),
        vectorbt_trades=len(replayed),
        mismatches=0,
        first_mismatch_field=None,
        replayed=list(replayed),
    )
    for i in range(max(len(ordered), len(replayed))):
        o = ordered[i] if i < len(ordered) else None
        v = replayed[i] if i < len(replayed) else None
        if o is None or v is None:
            fld = "trade_count"
        else:
            fld = _field_mismatch(o, v)
        if fld is None:
            continue
        res.mismatches += 1
        key = (o.trade_key if o else None) or (v.trade_key if v else None)
        if res.first_mismatch_field is None:
            res.first_mismatch_field = fld
            res.first_mismatch_trade = key
        res.details.append(
            {
                "position": i,
                "trade_key": key,
                "field": fld,
                "bar_loop": getattr(o, fld, None) if o and fld != "trade_count" else None,
                "vectorbt": getattr(v, fld, None) if v and fld != "trade_count" else None,
            }
        )
    return res


def check_parity(orders: Sequence[ReplayOrder]) -> ParityResult:
    """Replay and compare. A VectorBT error is a FAIL with the error recorded."""
    try:
        replayed = replay_orders(orders)
    except Exception as exc:  # noqa: BLE001
        return ParityResult(
            bar_loop_trades=len(orders),
            vectorbt_trades=0,
            mismatches=max(1, len(orders)),
            first_mismatch_field="vectorbt_error",
            error=f"{type(exc).__name__}: {exc}",
        )
    return compare(orders, replayed)
