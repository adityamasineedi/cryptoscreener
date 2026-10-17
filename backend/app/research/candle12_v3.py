"""CANDLE12_V3_EXEC_FIX: execution-corrected CANDLE12 research engine.

CANDLE12_V2 (``candle12_v2.py``) is frozen and stays the regression baseline.
This module reuses its Candle-1 setup (``detect_c1_v2``), structural stop and
target builder read-only, and changes only execution and book rules:

- entry slippage mode (``inclusive`` default, ``additional``)
- Candle-2 gap through the entry trigger fills at the Candle-2 open
- a bar that opens beyond the stop exits at that open
- a bar that opens beyond a target exits at the target level (labelled)
- per-side position lock by default; a shared lock only for a combined book
- BOS re-fire policy (``once_per_level`` default, ``every_close`` = V2)
- target policy (``nearest_target_full_exit`` default = V2, ``tp1_runner``)
- exchange quantity step, minimum quantity and minimum notional
- intended vs actual dollar risk tolerance
- optional funding, kept apart from fees and slippage, with its coverage

Research only. No paper or live path. No parameter was tuned.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.research.ohlcv_continuity import bars_adjacent, prepare_series, step_seconds
from app.research.candle12_v2 import (
    Candle12V2Config,
    _structural_stop,
    detect_c1_v2,
)
from app.research.combination_backtest import resolve_intrabar_outcome
from app.research.config import AMBIGUOUS_CONSERVATIVE
from app.research.rolling_structure import (
    DEFAULT_LOOKBACK_BARS,
    DEFAULT_MIN_WARMUP_BARS,
    RollingSwingState,
)
from app.services.paper_sizing import floor_qty_to_step
from app.signals._candle_utils import candle_time, ohlc
from app.signals.config import SignalConfig
from app.signals.target_engine import compute_targets

STRATEGY_ID = "CANDLE12_V3_EXEC_FIX"
ENGINE_VERSION = "3.1.0"
BASELINE_STRATEGY_ID = "CANDLE12_V2"

ENTRY_SLIPPAGE_MODES = ("inclusive", "additional")
LOCK_MODES = ("per_side", "shared")
BOS_REFIRE_POLICIES = ("once_per_level", "every_close")
TARGET_POLICIES = ("nearest_target_full_exit", "tp1_runner")
BOOK_SIDES = ("LONG", "SHORT", "BOTH")
RUNNER_TARGETS = ("TP2", "TP3")
# Breakeven is judged once, at the close of the bar that booked TP1. If accepted
# it is the stop from the next bar to the end of the trade; if refused it is
# never judged again and the old stop stays.
BREAKEVEN_RULES = ("once_at_tp1_close",)

TAG_GAP_ENTRY = "GAP_ENTRY_FILL"
TAG_GAP_STOP = "GAP_STOP_FILL"
TAG_GAP_TARGET = "GAP_TARGET_FILL"
TAG_SIZE_REJECTED = "SIZE_REJECTED"
TAG_MIN_NOTIONAL_REJECTED = "MIN_NOTIONAL_REJECTED"
TAG_RISK_TOLERANCE_REJECTED = "RISK_TOLERANCE_REJECTED"
TAG_SAME_BAR_SL = "CONSERVATIVE_SAME_BAR_SL"
TAG_MAX_HOLD = "MAX_HOLD_EXIT"
TAG_TP1_PARTIAL = "TP1_PARTIAL"
TAG_TP1_SPLIT_UNAVAILABLE = "TP1_SPLIT_UNAVAILABLE"
TAG_BREAKEVEN_SET = "BREAKEVEN_STOP_SET"
TAG_BREAKEVEN_SKIPPED = "BREAKEVEN_STOP_SKIPPED"
TAG_NO_RUNNER_TARGET = "NO_RUNNER_TARGET"

FUNDING_DISABLED = "FUNDING_DISABLED"
FUNDING_FULL_HISTORY = "FULL_HISTORY_FUNDING"
FUNDING_PARTIAL_HISTORY = "PARTIAL_HISTORY_FUNDING_SENSITIVITY"

# Out-of-sample reservation (docs/candle12_v3_paper_readiness.md). Research
# scripts call check_oos_guard so no bar from this time on is read by accident.
OOS_RESERVED_FROM = datetime(2026, 10, 16, tzinfo=timezone.utc)
OOS_ALLOW_ENV = "CANDLE12_V3_ALLOW_OOS"


def check_oos_guard(candles: Sequence[Mapping[str, Any]]) -> None:
    """Raise if any bar is in the reserved window, unless the env flag says so."""
    import os

    if os.environ.get(OOS_ALLOW_ENV) == "1":
        return
    late = [c for c in candles if (candle_time(c) or OOS_RESERVED_FROM) >= OOS_RESERVED_FROM]
    if late:
        raise RuntimeError(
            f"{len(late)} bars at or after {OOS_RESERVED_FROM.isoformat()} are reserved for the "
            f"CANDLE12_V3_EXEC_FIX out-of-sample read; set {OOS_ALLOW_ENV}=1 only for that read"
        )


# Size checks in the order they are reported. The first failing one is the outcome.
_QTY_REASONS = ("ZERO_QTY", "BELOW_MIN_QTY", "ROUNDING_SHORTFALL")


@dataclass(frozen=True)
class ExchangeFilter:
    step: float
    min_qty: float
    min_notional: float


# Binance USD-M perpetual LOT_SIZE stepSize / minQty and MIN_NOTIONAL notional,
# read from GET /fapi/v1/exchangeInfo on 2026-10-15 (server time 07:20 UTC).
# A symbol missing here runs without exchange filters and says so on every trade.
EXCHANGE_FILTERS_AS_OF = "2026-10-15T07:20Z fapi/v1/exchangeInfo"
EXCHANGE_FILTERS: dict[str, ExchangeFilter] = {
    "BTCUSDT": ExchangeFilter(step=0.001, min_qty=0.001, min_notional=50.0),
    "ETHUSDT": ExchangeFilter(step=0.001, min_qty=0.001, min_notional=20.0),
}


@dataclass
class Candle12V3Config:
    """Every execution rule of the book. Defaults are the V3 book."""

    book_side: str = "LONG"
    portfolio_lock_mode: str = "per_side"
    bos_refire_policy: str = "once_per_level"
    target_policy: str = "nearest_target_full_exit"
    entry_slippage_mode: str = "inclusive"
    entry_slippage: float = 0.0002
    exit_slippage: float = 0.0002
    taker_fee: float = 0.0004
    risk_usd: float = 20.0
    account_equity: float = 1000.0
    leverage: float = 2.0
    # None: take EXCHANGE_FILTERS[symbol] when apply_exchange_filters.
    qty_step: float | None = None
    min_qty: float | None = None
    min_notional: float | None = None
    apply_exchange_filters: bool = True
    # Reject when step rounding cuts the pre-rounding quantity by more than this.
    size_rounding_tolerance: float = 0.10
    # Reject when |actual risk - risk_usd| / risk_usd exceeds this, whatever the
    # cause (rounding or the margin cap). None turns the check off.
    risk_tolerance_pct: float | None = 0.10
    # tp1_runner only
    tp1_fraction: float = 0.5
    runner_target: str = "TP2"
    min_runner_distance_r: float = 1.0
    breakeven_rule: str = "once_at_tp1_close"
    max_hold_bars: int = 48
    funding_enabled: bool = False
    funding_interval_hours: float = 8.0
    # Setup (unchanged V2 values)
    structure_lookback_bars: int = DEFAULT_LOOKBACK_BARS
    min_bars: int = DEFAULT_MIN_WARMUP_BARS
    require_impulse: bool = True
    require_close_in_bos_direction: bool = True

    def __post_init__(self) -> None:
        self.book_side = str(self.book_side).upper()
        checks = (
            ("book_side", self.book_side, BOOK_SIDES),
            ("portfolio_lock_mode", self.portfolio_lock_mode, LOCK_MODES),
            ("bos_refire_policy", self.bos_refire_policy, BOS_REFIRE_POLICIES),
            ("target_policy", self.target_policy, TARGET_POLICIES),
            ("entry_slippage_mode", self.entry_slippage_mode, ENTRY_SLIPPAGE_MODES),
            ("runner_target", self.runner_target, RUNNER_TARGETS),
            ("breakeven_rule", self.breakeven_rule, BREAKEVEN_RULES),
        )
        for name, value, allowed in checks:
            if value not in allowed:
                raise ValueError(f"{name}={value!r}; allowed {allowed}")
        if self.portfolio_lock_mode == "shared" and self.book_side != "BOTH":
            raise ValueError("portfolio_lock_mode='shared' needs book_side='BOTH'")
        if not 0.0 < self.tp1_fraction < 1.0:
            raise ValueError("tp1_fraction must be between 0 and 1")
        if self.max_hold_bars < 1:
            raise ValueError("max_hold_bars must be >= 1")
        if self.min_runner_distance_r < 0:
            raise ValueError("min_runner_distance_r must be >= 0")

    def sides(self) -> tuple[str, ...]:
        return ("LONG", "SHORT") if self.book_side == "BOTH" else (self.book_side,)

    def exchange_filter(self, symbol: str) -> tuple[float | None, float | None, float | None, str]:
        """(step, min_qty, min_notional, source). Config fields override the table."""
        base = EXCHANGE_FILTERS.get(symbol.upper()) if self.apply_exchange_filters else None
        step = self.qty_step if self.qty_step is not None else (base.step if base else None)
        min_qty = self.min_qty if self.min_qty is not None else (base.min_qty if base else None)
        min_notional = (
            self.min_notional if self.min_notional is not None else (base.min_notional if base else None)
        )
        if any(v is not None for v in (self.qty_step, self.min_qty, self.min_notional)):
            source = "config"
        elif base is not None:
            source = f"binance_usdm {EXCHANGE_FILTERS_AS_OF}"
        else:
            source = "disabled" if not self.apply_exchange_filters else "no_exchange_filter"
        return step, min_qty, min_notional, source

    def to_dict(self) -> dict[str, Any]:
        return {"strategy_id": STRATEGY_ID, "engine_version": ENGINE_VERSION, **asdict(self)}


# ---------------------------------------------------------------- prices


def _adverse(direction: str, price: float, rate: float, *, entry: bool) -> float:
    """Slippage against the trader: buys pay up, sells receive less."""
    buy = (direction == "LONG") == entry
    return price * (1.0 + rate) if buy else price * (1.0 - rate)


def entry_fill(
    *,
    direction: str,
    c1_high: float,
    c1_low: float,
    c2_open: float,
    c2_high: float,
    c2_low: float,
    cfg: Candle12V3Config,
) -> dict[str, Any] | None:
    """Candle-2 stop entry. None when Candle-2 does not trade the fill price.

    trigger      = C1 high (LONG) / C1 low (SHORT)
    stop order   = trigger                    (inclusive)
                 = trigger adjusted by s once (additional)
    normal fill  = stop order adjusted by s (adverse); Candle-2 must trade it
    gap fill     = C2 open adjusted by s, when C2 opens beyond the stop order
    """
    s = cfg.entry_slippage
    trigger = c1_high if direction == "LONG" else c1_low
    order = trigger
    if cfg.entry_slippage_mode == "additional":
        order = _adverse(direction, trigger, s, entry=True)
    if direction == "LONG":
        if c2_open > order:
            return {"trigger": trigger, "stop_order": order, "fill": c2_open * (1.0 + s), "gap": True}
        fill = order * (1.0 + s)
        if c2_high > trigger and c2_high >= fill:
            return {"trigger": trigger, "stop_order": order, "fill": fill, "gap": False}
        return None
    if c2_open < order:
        return {"trigger": trigger, "stop_order": order, "fill": c2_open * (1.0 - s), "gap": True}
    fill = order * (1.0 - s)
    if c2_low < trigger and c2_low <= fill:
        return {"trigger": trigger, "stop_order": order, "fill": fill, "gap": False}
    return None


def breakeven_stop(direction: str, entry: float, *, fee: float, exit_slippage: float) -> float:
    """Stop price whose slipped exit nets zero after entry and exit fees."""
    if direction == "LONG":
        return entry * (1.0 + fee) / ((1.0 - fee) * (1.0 - exit_slippage))
    return entry * (1.0 - fee) / ((1.0 + fee) * (1.0 + exit_slippage))


def size_position(
    *,
    entry: float,
    stop: float,
    cfg: Candle12V3Config,
    step: float | None,
    min_qty: float | None,
    min_notional: float | None = None,
) -> dict[str, Any]:
    """Quantity from the intended risk, the margin cap and the exchange filters.

    qty_target = min(risk_usd / |entry - stop|, equity * leverage / entry)
    qty        = floor(qty_target / step) * step   (no step: qty_target)
    Every failing check is listed in ``reject_reasons``:
      ZERO_QTY, BELOW_MIN_QTY            qty <= 0 or qty < min_qty
      ROUNDING_SHORTFALL                 qty < (1 - size_rounding_tolerance) * qty_target
      MIN_NOTIONAL_REJECTED              qty * entry < min_notional
      RISK_TOLERANCE_REJECTED            |qty*|entry-stop| - risk_usd| / risk_usd > risk_tolerance_pct
    """
    rpu = abs(entry - stop)
    raw = cfg.risk_usd / rpu if rpu > 0 else 0.0
    cap = cfg.account_equity * max(1.0, cfg.leverage) / entry if entry > 0 else 0.0
    target = min(raw, cap)
    qty = floor_qty_to_step(target, step) if step else target
    actual = qty * rpu
    diff = actual - cfg.risk_usd
    diff_pct = abs(diff) / cfg.risk_usd if cfg.risk_usd else 0.0
    notional = qty * entry
    reasons: list[str] = []
    if qty <= 0:
        reasons.append("ZERO_QTY")
    else:
        if min_qty and qty < min_qty - 1e-12:
            reasons.append("BELOW_MIN_QTY")
        if step and target > 0 and qty < (1.0 - cfg.size_rounding_tolerance) * target - 1e-15:
            reasons.append("ROUNDING_SHORTFALL")
        if min_notional and notional < min_notional - 1e-9:
            reasons.append(TAG_MIN_NOTIONAL_REJECTED)
        if cfg.risk_tolerance_pct is not None and diff_pct > cfg.risk_tolerance_pct + 1e-12:
            reasons.append(TAG_RISK_TOLERANCE_REJECTED)
    return {
        "risk_per_unit": rpu,
        "raw_risk_quantity": raw,
        "max_leverage_quantity": cap,
        "target_quantity": target,
        "qty": qty,
        "notional_usd": notional,
        "capped_by_leverage": bool(raw > cap + 1e-15),
        "qty_step": step,
        "min_qty": min_qty,
        "min_notional": min_notional,
        "intended_risk_usd": cfg.risk_usd,
        "actual_risk_usd": actual,
        "risk_diff_usd": diff,
        "risk_diff_pct": diff_pct,
        "reject_reasons": reasons,
        "rejected": reasons[0] if reasons else None,
    }


def rejection_outcome(reason: str) -> str:
    """Trade outcome for a size rejection reason."""
    return TAG_SIZE_REJECTED if reason in _QTY_REASONS else reason


def select_runner_target(
    direction: str,
    entry: float,
    stop: float,
    targets: Sequence[float],
    cfg: Candle12V3Config,
) -> tuple[float | None, str | None]:
    """Runner target for tp1_runner: (price, 'TP2'|'TP3') or (None, None).

    TPn is the n-th nearest target. Starting at ``runner_target``, take the first
    target at least ``min_runner_distance_r`` x initial risk beyond TP1.
    """
    levels = sorted(targets, reverse=direction == "SHORT")
    if len(levels) < 2:
        return None, None
    need = cfg.min_runner_distance_r * abs(entry - stop)
    start = 1 if cfg.runner_target == "TP2" else 2
    for slot in range(start, len(levels)):
        if abs(levels[slot] - levels[0]) >= need * (1.0 - 1e-9):
            return levels[slot], f"TP{slot + 1}"
    return None, None


# ---------------------------------------------------------------- BOS re-fire


def _broken_swing_key(setup: Mapping[str, Any], c1: int) -> tuple[str, int, float] | None:
    """(direction, absolute bar index, price) of the swing the BOS broke.

    Same pick as detect_bos: the last confirmed swing before Candle-1.
    """
    direction = str(setup["direction"])
    want = "HIGH" if direction == "LONG" else "LOW"
    offset = int(setup.get("structure_offset") or 0)
    local_c1 = c1 - offset
    same = [s for s in setup.get("swings") or [] if s.swing_type == want and s.bar_index < local_c1]
    if not same:
        return None
    s = same[-1]
    return direction, int(s.bar_index) + offset, float(s.price)


def _opposing_swing_after(setup: Mapping[str, Any], after_abs: int) -> bool:
    """True when a swing on the other side (a low for LONG) sits after ``after_abs``."""
    want = "LOW" if setup["direction"] == "LONG" else "HIGH"
    offset = int(setup.get("structure_offset") or 0)
    return any(
        s.swing_type == want and int(s.bar_index) + offset > after_abs
        for s in setup.get("swings") or []
    )


# ---------------------------------------------------------------- walk


@dataclass
class _Position:
    direction: str
    entry_index: int
    entry: float
    stop_initial: float
    stop: float
    qty: float
    targets: list[float]  # slot order TP1..TP3 (V2 slots)
    runner_level: float | None = None
    runner_name: str | None = None
    tags: list[str] = field(default_factory=list)
    legs: list[dict[str, Any]] = field(default_factory=list)
    tp1_done: bool = False
    stop_from_next_bar: float | None = None
    exit_index: int | None = None

    @property
    def open_qty(self) -> float:
        return self.qty - sum(leg["qty"] for leg in self.legs)

    def by_distance(self) -> list[float]:
        return sorted(self.targets, reverse=self.direction == "SHORT")


def _beyond(direction: str, price: float, level: float, *, favorable: bool) -> bool:
    up = (direction == "LONG") == favorable
    return price > level if up else price < level


def _touches(direction: str, high: float, low: float, level: float, *, favorable: bool) -> bool:
    up = (direction == "LONG") == favorable
    return high >= level if up else low <= level


class _Walker:
    def __init__(self, cfg: Candle12V3Config, step: float | None, min_qty: float | None) -> None:
        self.cfg = cfg
        self.step = step
        self.min_qty = min_qty

    def _exit(self, pos: _Position, k: int, raw: float, qty: float, reason: str, tag: str | None = None) -> None:
        fill = _adverse(pos.direction, raw, self.cfg.exit_slippage, entry=False)
        pos.legs.append({"bar": k, "qty": qty, "raw_exit": raw, "fill": fill, "reason": reason, "tag": tag})
        if tag and tag not in pos.tags:
            pos.tags.append(tag)

    def _close_all(self, pos: _Position, k: int, raw: float, reason: str, tag: str | None = None) -> None:
        self._exit(pos, k, raw, pos.open_qty, reason, tag)
        pos.exit_index = k

    def _split(self, pos: _Position) -> float | None:
        """TP1 quantity, or None when both parts cannot meet the exchange minimum."""
        part = pos.qty * self.cfg.tp1_fraction
        if self.step:
            part = floor_qty_to_step(part, self.step)
        rest = pos.qty - part
        floor = self.min_qty or 0.0
        if part <= 0 or rest <= 0 or part < floor - 1e-12 or rest < floor - 1e-12:
            return None
        return part

    def _take_tp1(self, pos: _Position, k: int, level: float, tag: str | None) -> bool:
        """Book TP1. Returns True when the whole position is now closed."""
        part = self._split(pos)
        if part is None:
            pos.tags.append(TAG_TP1_SPLIT_UNAVAILABLE)
            self._close_all(pos, k, level, "TP1", tag)
            return True
        self._exit(pos, k, level, part, "TP1", tag)
        pos.tags.append(TAG_TP1_PARTIAL)
        pos.tp1_done = True
        return False

    def judge_breakeven(self, pos: _Position, close: float) -> None:
        """once_at_tp1_close: BE must beat the current stop and sit on the losing
        side of the TP1 bar's close. Accepted -> stop from the next bar on."""
        be = breakeven_stop(
            pos.direction, pos.entry, fee=self.cfg.taker_fee, exit_slippage=self.cfg.exit_slippage
        )
        improves = _beyond(pos.direction, be, pos.stop, favorable=True)
        market_allows = _beyond(pos.direction, close, be, favorable=True)
        if improves and market_allows:
            pos.stop_from_next_bar = be
            pos.tags.append(TAG_BREAKEVEN_SET)
        else:
            pos.tags.append(TAG_BREAKEVEN_SKIPPED)

    def bar(self, pos: _Position, k: int, candle: Sequence[float], *, entry_bar: bool) -> None:
        o, h, l, c = candle
        d = pos.direction
        runner = self.cfg.target_policy == "tp1_runner"
        if pos.stop_from_next_bar is not None:
            pos.stop = pos.stop_from_next_bar
            pos.stop_from_next_bar = None

        # Gap rules apply from the bar after entry: the position is not open at the
        # entry bar's open.
        if not entry_bar:
            if _beyond(d, o, pos.stop, favorable=False):
                self._close_all(pos, k, o, "SL", TAG_GAP_STOP)
                return
            if self._gap_target(pos, k, o, runner):
                return

        if not runner:
            outcome, px, ambiguous = resolve_intrabar_outcome(
                direction=d, high=h, low=l, stop=pos.stop, targets=pos.targets,
                handling=AMBIGUOUS_CONSERVATIVE,
            )
            if outcome is not None:
                self._close_all(pos, k, float(px), outcome, TAG_SAME_BAR_SL if ambiguous else None)
            return
        self._runner_intrabar(pos, k, h, l, c)

    def _gap_target(self, pos: _Position, k: int, o: float, runner: bool) -> bool:
        d = pos.direction
        if not runner:
            hit = [t for t in pos.targets if _beyond(d, o, t, favorable=True)]
            if not hit:
                return False
            level = min(hit) if d == "LONG" else max(hit)
            self._close_all(pos, k, level, f"TP{pos.targets.index(level) + 1}", TAG_GAP_TARGET)
            return True
        tp1 = pos.by_distance()[0]
        if not pos.tp1_done:
            if not _beyond(d, o, tp1, favorable=True):
                return False
            if self._take_tp1(pos, k, tp1, TAG_GAP_TARGET):
                return True
        if pos.runner_level is not None and _beyond(d, o, pos.runner_level, favorable=True):
            self._close_all(pos, k, pos.runner_level, str(pos.runner_name), TAG_GAP_TARGET)
            return True
        return False

    def _runner_intrabar(self, pos: _Position, k: int, h: float, l: float, c: float) -> None:
        d = pos.direction
        tp1 = pos.by_distance()[0]
        stop_hit = _touches(d, h, l, pos.stop, favorable=False)
        pending = pos.runner_level if pos.tp1_done else tp1
        target_hit = pending is not None and _touches(d, h, l, pending, favorable=True)
        if stop_hit and target_hit:
            self._close_all(pos, k, pos.stop, "SL", TAG_SAME_BAR_SL)
            return
        if stop_hit:
            self._close_all(pos, k, pos.stop, "SL")
            return
        if target_hit and not pos.tp1_done:
            if self._take_tp1(pos, k, tp1, None):
                return
        # TP1 on this bar, intrabar or at a gap open: the stop moves from the next bar.
        tp1_now = any(leg["bar"] == k and leg["reason"] == "TP1" for leg in pos.legs)
        if pos.tp1_done and pos.runner_level is not None and _touches(d, h, l, pos.runner_level, favorable=True):
            self._close_all(pos, k, pos.runner_level, str(pos.runner_name))
            return
        if k - pos.entry_index >= self.cfg.max_hold_bars:
            self._close_all(pos, k, c, "MAX_HOLD", TAG_MAX_HOLD)
            return
        if tp1_now:
            self.judge_breakeven(pos, c)


# ---------------------------------------------------------------- funding


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _funding_usd(
    pos: _Position,
    candles: Sequence[Mapping[str, Any]],
    funding: Sequence[Mapping[str, Any]],
) -> tuple[float, int]:
    """Funding while open: settlements t with C2 open < t <= exit-bar open.

    LONG pays rate * qty_open * mark; SHORT receives it. Mark falls back to the
    open of the candle stamped t, then to the entry fill.
    """
    if pos.exit_index is None:
        return 0.0, 0
    t_in = candle_time(candles[pos.entry_index])
    t_out = candle_time(candles[pos.exit_index])
    opens = {candle_time(candles[i]): float(candles[i]["open"]) for i in range(pos.entry_index, pos.exit_index + 1)}
    leg_times = [(candle_time(candles[leg["bar"]]), leg["qty"]) for leg in pos.legs]
    sign = -1.0 if pos.direction == "LONG" else 1.0
    total, count = 0.0, 0
    for row in funding:
        t = row["time"]
        if not (t_in < t <= t_out):
            continue
        qty_open = pos.qty - sum(q for when, q in leg_times if when < t)
        if qty_open <= 0:
            continue
        mark = row.get("mark_price") or opens.get(t) or pos.entry
        total += sign * float(row["rate"]) * qty_open * float(mark)
        count += 1
    return total, count


def _funding_coverage(
    t_in: datetime, t_out: datetime, first: datetime | None, last: datetime | None, interval: timedelta
) -> str:
    """FULL when every settlement in (t_in, t_out] falls inside the loaded history."""
    if first is None or last is None:
        return "NONE"
    if t_out < first or t_in >= last + interval:
        return "NONE"
    if t_in >= first - interval and t_out < last + interval:
        return "FULL"
    return "PARTIAL"


def _economics(
    pos: _Position,
    sizing: Mapping[str, Any],
    cfg: Candle12V3Config,
    funding_usd: float,
) -> dict[str, Any]:
    long_ = pos.direction == "LONG"
    fee_entry = cfg.taker_fee * pos.qty * pos.entry
    gross = sum(leg["qty"] * ((leg["fill"] - pos.entry) if long_ else (pos.entry - leg["fill"])) for leg in pos.legs)
    fee_exit = sum(cfg.taker_fee * leg["qty"] * leg["fill"] for leg in pos.legs)
    slip_entry = abs(pos.entry - sizing["entry_base"]) * pos.qty
    slip_exit = sum(abs(leg["raw_exit"] - leg["fill"]) * leg["qty"] for leg in pos.legs)
    net = gross - fee_entry - fee_exit + funding_usd
    actual_risk = pos.qty * abs(pos.entry - pos.stop_initial)
    exit_qty = sum(leg["qty"] for leg in pos.legs) or 1.0
    return {
        "gross_pnl_usd": gross,
        "fee_entry_usd": -fee_entry,
        "fee_exit_usd": -fee_exit,
        "fee_total_usd": -(fee_entry + fee_exit),
        "funding_usd": funding_usd,
        "slippage_cost_usd": slip_entry + slip_exit,
        "net_pnl_usd": net,
        "pnl_usd": net,
        "risk_usd": actual_risk,
        "requested_risk_usd": cfg.risk_usd,
        "r_gross": gross / actual_risk if actual_risk else None,
        "r_net": net / actual_risk if actual_risk else None,
        "r_net_actual_risk": net / actual_risk if actual_risk else None,
        "r_net_intended_risk": net / cfg.risk_usd if cfg.risk_usd else None,
        "exit_price": sum(leg["fill"] * leg["qty"] for leg in pos.legs) / exit_qty,
        "raw_exit_price": sum(leg["raw_exit"] * leg["qty"] for leg in pos.legs) / exit_qty,
        "notional_entry_usd": pos.qty * pos.entry,
        "margin_usd": pos.qty * pos.entry / max(1.0, cfg.leverage),
    }


def _ts(candles: Sequence[Mapping[str, Any]], i: int) -> str | None:
    t = candle_time(candles[i])
    return t.isoformat() if t else None


def run_candle12_v3(
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    *,
    config: Candle12V3Config | None = None,
    signal_config: SignalConfig | None = None,
    index_start: int | None = None,
    funding: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Walk the series once. Setups come from the frozen V2 detector."""
    t0 = time.perf_counter()
    cfg = config or Candle12V3Config()
    scfg = signal_config or SignalConfig()
    if cfg.funding_enabled and funding is None:
        raise ValueError("funding_enabled needs funding rows")
    fund_rows = sorted(
        ({**row, "time": _as_utc(row["time"])} for row in (funding or [])), key=lambda r: r["time"]
    )
    fund_first = fund_rows[0]["time"] if fund_rows else None
    fund_last = fund_rows[-1]["time"] if fund_rows else None
    fund_interval = timedelta(hours=cfg.funding_interval_hours)
    setup_cfg = Candle12V2Config(
        structure_lookback_bars=cfg.structure_lookback_bars,
        min_bars=cfg.min_bars,
        require_impulse=cfg.require_impulse,
        require_close_in_bos_direction=cfg.require_close_in_bos_direction,
    )
    step, min_qty, min_notional, filter_source = cfg.exchange_filter(symbol)
    walker = _Walker(cfg, step, min_qty)
    sym = symbol.upper()
    candles = prepare_series(candles)
    bar_step = step_seconds(timeframe)
    n = len(candles)
    sc = scfg.swing_for(timeframe)
    swing_state = RollingSwingState(left=sc.swing_left_bars, right=sc.swing_right_bars, symbol=sym, timeframe=timeframe)
    swing_state.bind(candles)

    first_trade_c1 = max(cfg.min_bars, index_start or 0)
    sides = cfg.sides()
    open_until: dict[str, tuple[int, str]] = {}  # lock key -> (last bar held, side)
    consumed: dict[tuple[str, int, float], int] = {}  # broken swing -> Candle-1 that used it
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counts = {
        "setups_detected": 0,
        "refire_suppressed": 0,
        "blocked_same_side": 0,
        "blocked_by_opposite": 0,
        "no_fill": 0,
        "size_rejected": 0,
        "min_notional_rejected": 0,
        "risk_tolerance_rejected": 0,
    }

    for i in range(cfg.min_bars, n - 1):
        setup = detect_c1_v2(
            symbol=sym, timeframe=timeframe, candles=candles, candle1_index=i,
            signal_config=scfg, v2=setup_cfg, swing_state=swing_state,
        )
        if not setup:
            continue
        counts["setups_detected"] += 1
        direction = str(setup["direction"])
        key = _broken_swing_key(setup, i)
        if cfg.bos_refire_policy == "once_per_level" and key is not None:
            prior = consumed.get(key)
            if prior is not None and not _opposing_swing_after(setup, prior):
                if i >= first_trade_c1 and direction in sides:
                    counts["refire_suppressed"] += 1
                continue
            consumed[key] = i
        if i < first_trade_c1 or direction not in sides:
            continue
        lock_key = "ALL" if cfg.portfolio_lock_mode == "shared" else direction
        held = open_until.get(lock_key)
        if held is not None and i <= held[0]:
            if held[1] != direction:
                if bars_adjacent(candles[i], candles[i + 1], bar_step) and _plan(
                    setup, candles, i, cfg, scfg
                ) is not None:
                    counts["blocked_by_opposite"] += 1
            else:
                counts["blocked_same_side"] += 1
            continue

        if not bars_adjacent(candles[i], candles[i + 1], bar_step):
            counts["no_fill"] += 1
            continue
        plan = _plan(setup, candles, i, cfg, scfg)
        if plan is None:
            counts["no_fill"] += 1
            continue
        sizing = size_position(
            entry=plan["fill"], stop=plan["stop"], cfg=cfg, step=step, min_qty=min_qty, min_notional=min_notional
        )
        sizing["entry_base"] = plan["entry_base"]
        base = {
            "strategy_id": STRATEGY_ID,
            "symbol": sym,
            "timeframe": timeframe,
            "direction": direction,
            "candle1_index": i,
            "entry_index": i + 1,
            "candle1_time": _ts(candles, i),
            "signal_time": _ts(candles, i + 1),  # entry bar, as V2 (UI "Entry time")
            "entry_time": _ts(candles, i + 1),
            "trigger_level": plan["trigger"],
            "stop_order_price": plan["stop_order"],
            "gap_entry": plan["gap"],
            "entry_price": plan["fill"],
            "stop_price": plan["stop"],
            "tp1": plan["targets"][0] if plan["targets"] else None,
            "tp2": plan["targets"][1] if len(plan["targets"]) > 1 else None,
            "tp3": plan["targets"][2] if len(plan["targets"]) > 2 else None,
            "bos_level": setup.get("bos_level"),
            "broken_swing_index": key[1] if key else None,
            "broken_swing_price": key[2] if key else None,
            "exchange_filter_source": filter_source,
            **{k: v for k, v in sizing.items() if k != "entry_base"},
        }
        if sizing["rejected"]:
            outcome = rejection_outcome(sizing["rejected"])
            tags = [rejection_outcome(r) for r in sizing["reject_reasons"]]
            counts["size_rejected"] += outcome == TAG_SIZE_REJECTED
            counts["min_notional_rejected"] += TAG_MIN_NOTIONAL_REJECTED in sizing["reject_reasons"]
            counts["risk_tolerance_rejected"] += TAG_RISK_TOLERANCE_REJECTED in sizing["reject_reasons"]
            rejected.append({
                **base,
                "outcome": outcome,
                "rejection_reason": sizing["rejected"],
                "tags": list(dict.fromkeys(tags + sizing["reject_reasons"])),
            })
            continue

        pos = _Position(
            direction=direction, entry_index=i + 1, entry=plan["fill"], stop_initial=plan["stop"],
            stop=plan["stop"], qty=sizing["qty"], targets=list(plan["targets"]),
        )
        if plan["gap"]:
            pos.tags.append(TAG_GAP_ENTRY)
        if cfg.target_policy == "tp1_runner":
            pos.runner_level, pos.runner_name = select_runner_target(
                direction, plan["fill"], plan["stop"], plan["targets"], cfg
            )
            pos.tags.append(f"RUNNER_TARGET_{pos.runner_name}" if pos.runner_name else TAG_NO_RUNNER_TARGET)
        for k in range(i + 1, n):
            if k > i + 1 and not bars_adjacent(candles[k - 1], candles[k], bar_step):
                break
            walker.bar(pos, k, ohlc(candles, k), entry_bar=k == i + 1)
            if pos.exit_index is not None:
                break
        if pos.exit_index is None:
            trades.append({**base, "outcome": "OPEN", "tags": pos.tags, "legs": pos.legs})
            open_until[lock_key] = (n, direction)
            continue
        if cfg.funding_enabled:
            fund, fund_n = _funding_usd(pos, candles, fund_rows)
            coverage = _funding_coverage(
                candle_time(candles[pos.entry_index]), candle_time(candles[pos.exit_index]),
                fund_first, fund_last, fund_interval,
            )
        else:
            fund, fund_n, coverage = 0.0, 0, None
        reasons = [leg["reason"] for leg in pos.legs]
        outcome = reasons[-1] if len(reasons) == 1 else "+".join(reasons)
        trades.append({
            **base,
            "outcome": outcome,
            "exit_index": pos.exit_index,
            "exit_time": _ts(candles, pos.exit_index),
            "holding_bars": pos.exit_index - pos.entry_index,
            "ambiguous": TAG_SAME_BAR_SL in pos.tags,
            "tags": pos.tags,
            "legs": pos.legs,
            "runner_target_price": pos.runner_level,
            "runner_target_slot": pos.runner_name,
            "final_stop_price": pos.stop,
            "funding_events": fund_n,
            "funding_coverage": coverage,
            **_economics(pos, sizing, cfg, fund),
        })
        open_until[lock_key] = (pos.exit_index, direction)

    closed = [t for t in trades if t["outcome"] != "OPEN"]
    counts.update({
        "trades_closed": len(closed),
        "trades_open_at_end": len(trades) - len(closed),
        "gap_entry_fills": sum(TAG_GAP_ENTRY in t["tags"] for t in trades),
        "gap_stop_exits": sum(TAG_GAP_STOP in t["tags"] for t in trades),
        "gap_target_exits": sum(TAG_GAP_TARGET in t["tags"] for t in trades),
        "same_bar_sl": sum(TAG_SAME_BAR_SL in t["tags"] for t in trades),
        "capped_by_leverage": sum(bool(t["capped_by_leverage"]) for t in trades),
    })
    if not cfg.funding_enabled:
        funding_summary: dict[str, Any] = {"label": FUNDING_DISABLED}
    else:
        cover = [t["funding_coverage"] for t in closed]
        funding_summary = {
            "label": FUNDING_FULL_HISTORY if cover and all(c == "FULL" for c in cover) else FUNDING_PARTIAL_HISTORY,
            "rows": len(fund_rows),
            "first_settlement": fund_first.isoformat() if fund_first else None,
            "last_settlement": fund_last.isoformat() if fund_last else None,
            "interval_hours": cfg.funding_interval_hours,
            "trades_full": cover.count("FULL"),
            "trades_partial": cover.count("PARTIAL"),
            "trades_none": cover.count("NONE"),
        }
    return {
        "strategy_id": STRATEGY_ID,
        "engine_version": ENGINE_VERSION,
        "baseline": BASELINE_STRATEGY_ID,
        "symbol": sym,
        "timeframe": timeframe,
        "config": cfg.to_dict(),
        "exchange_filter": {
            "step": step, "min_qty": min_qty, "min_notional": min_notional, "source": filter_source,
        },
        "funding": funding_summary,
        "trades": trades,
        "rejected": rejected,
        "counts": counts,
        "elapsed_seconds": time.perf_counter() - t0,
        "label": "RESEARCH_ONLY",
    }


def _plan(
    setup: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    c1: int,
    cfg: Candle12V3Config,
    scfg: SignalConfig,
) -> dict[str, Any] | None:
    """Fill, stop and targets for a Candle-1 setup, or None when nothing fills."""
    direction = str(setup["direction"])
    c1_ohlc = setup["c1_ohlc"]
    o2, h2, l2, _ = ohlc(candles, c1 + 1)
    fill = entry_fill(
        direction=direction, c1_high=float(c1_ohlc["high"]), c1_low=float(c1_ohlc["low"]),
        c2_open=o2, c2_high=h2, c2_low=l2, cfg=cfg,
    )
    if fill is None:
        return None
    entry = fill["fill"]
    stop = _structural_stop(
        direction=direction, entry=entry, c1_high=float(c1_ohlc["high"]), c1_low=float(c1_ohlc["low"]),
        pullback=setup.get("pullback"), impulse=setup.get("impulse"),
        atr=float(setup["atr"]) if setup.get("atr") is not None else None, signal_config=scfg,
    )
    final = stop.get("final_stop")
    if final is None or not _beyond(direction, entry, float(final), favorable=True):
        return None
    targets = compute_targets(
        direction=direction, entry_price=entry, stop=stop, swings=setup.get("swings") or [], config=scfg
    )
    levels = [float(t["target_price"]) for t in targets if t.get("target_price") is not None]
    if not levels:
        return None
    return {
        **fill,
        "entry_base": o2 if fill["gap"] else fill["trigger"],
        "stop": float(final),
        "targets": levels,
    }
