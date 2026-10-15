"""One shared account. Offsetting positions do not cancel stop risk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.research.intraday_bos_sd.execution import Fill, plan_entry, simulate_trade
from app.research.intraday_bos_sd.spec import (
    ASSUMED_MIN_NOTIONAL,
    ASSUMED_QTY_STEP,
    CORRELATED_SAME_DIRECTION_RISK_PCT,
    CORRELATION_MIN_OVERLAP_HOURS,
    CORRELATION_THRESHOLD,
    DAILY_LOSS_PCT,
    INITIAL_EQUITY,
    LARGE_RISK_PCT,
    LEVERAGE_CAP,
    MID_RISK_PCT,
    TAKER_FEE_RATE,
    TOTAL_OPEN_STOP_RISK_PCT,
)


@dataclass
class OpenPosition:
    fill_plan_symbol: str
    side: str
    tier: str
    quantity: float
    entry_price: float
    stop: float
    risk_cash: float
    entry_time: datetime
    signal_key: str
    fill: Fill


@dataclass
class PortfolioResult:
    trades: list[Fill]
    rejections: list[dict]
    equity_curve: list[dict]
    ending_equity: float


def risk_pct_for_tier(tier: str) -> float | None:
    label = tier.upper()
    if label in {"LARGE", "LARGE_CAP", "LARGE_LIQUIDITY"}:
        return LARGE_RISK_PCT
    if label in {"MID", "MID_CAP", "MID_LIQUIDITY"}:
        return MID_RISK_PCT
    return None


def size_quantity(
    *,
    equity: float,
    entry: float,
    stop: float,
    tier: str,
    open_notional: float,
    step: float = ASSUMED_QTY_STEP,
    min_notional: float = ASSUMED_MIN_NOTIONAL,
) -> tuple[float, str]:
    pct = risk_pct_for_tier(tier)
    if pct is None:
        return 0.0, "tier_not_enabled"
    risk_cash = equity * pct
    per_unit = abs(entry - stop)
    fee_unit = entry * TAKER_FEE_RATE + abs(stop) * TAKER_FEE_RATE
    unit_loss = per_unit + fee_unit
    if unit_loss <= 0 or entry <= 0:
        return 0.0, "non_positive_risk"
    raw = risk_cash / unit_loss
    room = max(0.0, equity * LEVERAGE_CAP - open_notional)
    max_qty = room / entry
    qty = min(raw, max_qty)
    steps = int(qty / step)
    qty = steps * step
    if qty <= 0 or qty * entry < min_notional:
        return 0.0, "below_min_notional_or_margin"
    return qty, "ok"


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < CORRELATION_MIN_OVERLAP_HOURS:
        return None
    xs = xs[-n:]
    ys = ys[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def correlated(a: list[float] | None, b: list[float] | None) -> bool:
    """Missing history is treated as correlated."""
    if not a or not b:
        return True
    value = pearson(a, b)
    if value is None:
        return True
    return value >= CORRELATION_THRESHOLD


def _day_start(ts: datetime) -> datetime:
    ts = ts.astimezone(timezone.utc)
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


def run_portfolio(
    candidates: list[dict],
    *,
    slippage: float,
    initial_equity: float = INITIAL_EQUITY,
) -> PortfolioResult:
    """candidates items: signal, setup series, zones, execution, funding, returns.

    Each item must already be sorted by the caller. This function sorts again.
    """
    def priority(item: dict):
        signal = item["signal"]
        tier_rank = 0 if risk_pct_for_tier(signal.tier) == LARGE_RISK_PCT else 1
        side_rank = 0 if signal.side == "LONG" else 1
        return (item["setup"].bars[signal.entry_index].open_time, tier_rank, signal.symbol, side_rank)

    ordered = sorted(candidates, key=priority)
    equity = initial_equity
    cash_pnl = 0.0
    open_positions: list[dict] = []
    trades: list[Fill] = []
    rejections: list[dict] = []
    curve: list[dict] = [{"time": None, "equity": equity}]
    blocked_until: datetime | None = None
    day_anchor: datetime | None = None
    day_start_equity = equity

    def flatten(when: datetime, price_of) -> None:
        nonlocal equity, cash_pnl
        still = []
        for pos in open_positions:
            if pos["exit_time"] > when:
                # Force a mark flatten at `when` using the planned fill only if
                # the natural exit is later. Re-simulate is not repeated; the
                # natural fill is kept when it already exited.
                still.append(pos)
            else:
                trades.append(pos["fill"])
                cash_pnl += pos["fill"].net_pnl
                equity = initial_equity + cash_pnl
        open_positions[:] = [p for p in open_positions if p["exit_time"] > when]

    def mark_equity(when: datetime) -> float:
        realized = initial_equity
        for trade in trades:
            if trade.exit_time <= when:
                realized += trade.net_pnl
        unreal = 0.0
        for pos in open_positions:
            if pos["entry_time"] <= when < pos["exit_time"]:
                mark = pos["mark"](when)
                if pos["side"] == "LONG":
                    unreal += (mark - pos["entry_price"]) * pos["quantity"]
                else:
                    unreal += (pos["entry_price"] - mark) * pos["quantity"]
        return realized + unreal

    for item in ordered:
        signal = item["signal"]
        setup = item["setup"]
        entry_time = setup.bars[signal.entry_index].open_time
        # Realize exits that happen at or before this entry.
        matured = [p for p in open_positions if p["exit_time"] <= entry_time]
        for pos in matured:
            trades.append(pos["fill"])
        open_positions = [p for p in open_positions if p["exit_time"] > entry_time]
        equity = initial_equity + sum(t.net_pnl for t in trades)

        start = _day_start(entry_time)
        if day_anchor != start:
            day_anchor = start
            day_start_equity = equity
            blocked_until = None
        if blocked_until is not None and entry_time < blocked_until:
            rejections.append({"key": signal.idempotency_key, "reason": "daily_loss_block"})
            continue
        if any(p["symbol"] == signal.symbol for p in open_positions):
            rejections.append({"key": signal.idempotency_key, "reason": "already_open"})
            continue
        planned = plan_entry(signal, setup, item["zones"], slippage=slippage)
        if isinstance(planned, str):
            rejections.append({"key": signal.idempotency_key, "reason": planned})
            continue
        entry, stop, target = planned
        open_notional = sum(p["entry_price"] * p["quantity"] for p in open_positions)
        qty, reason = size_quantity(
            equity=equity,
            entry=entry,
            stop=stop,
            tier=signal.tier,
            open_notional=open_notional,
        )
        if qty <= 0:
            rejections.append({"key": signal.idempotency_key, "reason": reason})
            continue
        risk_cash = abs(entry - stop) * qty
        open_risk = sum(p["risk_cash"] for p in open_positions) + risk_cash
        if open_risk > equity * TOTAL_OPEN_STOP_RISK_PCT + 1e-9:
            rejections.append({"key": signal.idempotency_key, "reason": "total_stop_risk"})
            continue
        same = 0.0
        for pos in open_positions:
            if pos["side"] != signal.side:
                continue
            if correlated(item.get("returns"), pos.get("returns")):
                same += pos["risk_cash"]
        if same + risk_cash > equity * CORRELATED_SAME_DIRECTION_RISK_PCT + 1e-9:
            rejections.append({"key": signal.idempotency_key, "reason": "correlated_risk"})
            continue
        fill = simulate_trade(
            signal,
            setup,
            item["zones"],
            quantity=qty,
            slippage=slippage,
            execution=item.get("execution"),
            funding=item.get("funding"),
        )
        if isinstance(fill, str):
            rejections.append({"key": signal.idempotency_key, "reason": fill})
            continue
        open_positions.append(
            {
                "symbol": signal.symbol,
                "side": signal.side,
                "tier": signal.tier,
                "quantity": qty,
                "entry_price": fill.entry_price,
                "risk_cash": risk_cash,
                "entry_time": fill.entry_time,
                "exit_time": fill.exit_time,
                "returns": item.get("returns"),
                "fill": fill,
                "mark": item.get("mark") or (lambda _t, price=fill.entry_price: price),
            }
        )
        mtm = mark_equity(fill.entry_time)
        if day_start_equity > 0 and (mtm - day_start_equity) / day_start_equity <= -DAILY_LOSS_PCT:
            # Flatten everything still open, including this fill, at its exit
            # if the exit is the loss that breached. Block new entries.
            for pos in list(open_positions):
                trades.append(pos["fill"])
            open_positions.clear()
            equity = initial_equity + sum(t.net_pnl for t in trades)
            nxt = start + timedelta(days=1)
            blocked_until = nxt
            curve.append({"time": fill.exit_time.isoformat(), "equity": equity, "event": "daily_loss"})
            continue
        curve.append({"time": fill.entry_time.isoformat(), "equity": equity, "event": "entry"})

    for pos in open_positions:
        trades.append(pos["fill"])
    equity = initial_equity + sum(t.net_pnl for t in trades)
    curve.append({"time": None, "equity": equity, "event": "end"})
    trades.sort(key=lambda t: (t.entry_time, t.signal.symbol))
    return PortfolioResult(trades, rejections, curve, equity)
