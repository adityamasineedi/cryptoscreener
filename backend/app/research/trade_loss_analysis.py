"""Loss analysis for one finished Backtest-screen cell. Research only, read-only.

Explains each losing trade from the trade's own stored fields and the real
candles: one primary reason (fixed precedence), every flag that holds, the
excursion metrics, a clearly labelled hindsight look past the exit, and a
plain-English sentence built only from those numbers and the trade's
``market_context`` cell.

What-ifs re-walk every closed trade (winners too) from its real fill on the
same candles with the engine's own exit rules (same-bar stop and target ->
stop, the trade's fee rates on notional, the engine's slippage as embedded in
its fills). They never re-enter a trade the engine did not take, and they are
marked not computable where the engine's exits cannot be reproduced
faithfully (CANDLE12_V3 multi-leg exits, COMBO next-open fill models).

Nothing here changes a strategy rule, a fill, a fee, a size or a stored trade.
The CANDLE12_V2 BTCUSDT 1h LONG book is frozen: diagnostics only, no suggestions.
"""

from __future__ import annotations

import asyncio
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

DISCLAIMER = "Research only. Hindsight and what-ifs are in-sample and do not change any strategy."
FROZEN_NOTE = "Frozen BTC 1h CANDLE12_V2 book — diagnostics only; no rule change is suggested."
NO_GAIN_NOTE = "No measured what-if improved net $ on this cell; nothing is suggested."
SUGGESTION_LABEL = "research only"
DEFAULT_HINDSIGHT_BARS = 168
MAX_HINDSIGHT_BARS = 2000
ATR_PERIOD = 14
CANDLE_WARMUP_BARS = 50

REASON_ORDER = (
    "FEES_FLIPPED",
    "GAVE_BACK",
    "AGAINST_CONTEXT",
    "WRONG_RANGE_HALF",
    "LATE_STOP",
    "STOP_BEFORE_PROGRESS",
    "INSUFFICIENT_DATA",
)
# Entry-condition reasons: primary only when they separate losers from winners in
# the cell (see ``entry_reason_discrimination``).
ENTRY_REASONS = ("AGAINST_CONTEXT", "WRONG_RANGE_HALF")
DISCRIMINATION_MIN_GAP = 0.15  # losers' rate must beat winners' rate by this (absolute)
DISCRIMINATION_MAX_COVERAGE = 0.90  # and the condition must cover fewer trades than this
GAVE_BACK_FRACTION = 0.5  # of the entry -> TP1 distance
LATE_STOP_DRIFT_BARS = 10
CHASE_FRACTION_OF_STOP = 0.25
TIGHT_STOP_ATR = 1.0
WIDE_STOP_ATR = 3.0
TIME_STOPS = (12, 24, 48)
STOP_MULTIPLIERS = (1.5, 2.0)
REPLAY_TOLERANCE_USD = 0.02

_TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_STOP_OUTCOMES = {"SL", "STOP", "STOP_LOSS", "GAP_STOP", "STOPPED"}
_TREND_WORDS = {"BULLISH": "uptrend", "BEARISH": "downtrend"}


class LossAnalysisUnavailable(Exception):
    """The analysis cannot run for this request (mapped to HTTP 409)."""


# --------------------------------------------------------------------------- helpers


def _utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _r(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _sgn(direction: str) -> int:
    return -1 if str(direction).upper() == "SHORT" else 1


def is_frozen_book(strategy_id: Any, symbol: Any, timeframe: Any, direction: Any) -> bool:
    """The frozen CANDLE12_V2 BTCUSDT 1h LONG book (docs/candle12_v2_btc_1h_freeze.md)."""
    return (
        str(strategy_id or "").strip().upper() == "CANDLE12_V2"
        and str(symbol or "").strip().upper() == "BTCUSDT"
        and str(timeframe or "").strip().lower() == "1h"
        and str(direction or "").strip().upper() == "LONG"
    )


class _Series:
    """Candles as parallel arrays with a robust time -> index map (candle-open UTC)."""

    def __init__(self, candles: Sequence[Mapping[str, Any]]) -> None:
        rows: list[tuple[datetime, float, float, float, float]] = []
        for c in candles:
            ts = _utc(c.get("time") if c.get("time") is not None else c.get("timestamp") or c.get("open_time"))
            o = _f(c.get("open", c.get("o")))
            h = _f(c.get("high", c.get("h")))
            lo = _f(c.get("low", c.get("l")))
            cl = _f(c.get("close", c.get("c")))
            if ts is None or None in (o, h, lo, cl):
                continue
            rows.append((ts, o, h, lo, cl))  # type: ignore[arg-type]
        rows.sort(key=lambda r: r[0])
        dedup: list[tuple[datetime, float, float, float, float]] = []
        for r in rows:
            if dedup and dedup[-1][0] == r[0]:
                dedup[-1] = r
            else:
                dedup.append(r)
        self.times = [r[0] for r in dedup]
        self.open = [r[1] for r in dedup]
        self.high = [r[2] for r in dedup]
        self.low = [r[3] for r in dedup]
        self.close = [r[4] for r in dedup]
        self.n = len(dedup)
        self._exact = {t: i for i, t in enumerate(self.times)}
        steps = sorted(
            (self.times[i + 1] - self.times[i]).total_seconds() for i in range(min(self.n - 1, 500))
        )
        self.bar_seconds = steps[len(steps) // 2] if steps else 0.0
        self.atr = _wilder_atr(self.high, self.low, self.close, ATR_PERIOD)

    def index_of(self, value: Any) -> int | None:
        """Bar whose open is ``value``; else the bar containing it (open <= t < open + bar)."""
        ts = _utc(value)
        if ts is None or not self.n:
            return None
        hit = self._exact.get(ts)
        if hit is not None:
            return hit
        i = bisect_right(self.times, ts) - 1
        if i < 0:
            return None
        if self.bar_seconds and (ts - self.times[i]).total_seconds() < self.bar_seconds:
            return i
        return None

    def index_at_or_before(self, value: Any) -> int | None:
        ts = _utc(value)
        if ts is None or not self.n:
            return None
        i = bisect_right(self.times, ts) - 1
        return i if i >= 0 else None


def _wilder_atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int) -> list[float | None]:
    n = len(high)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    tr = [high[0] - low[0]]
    for i in range(1, n):
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    atr = sum(tr[1 : period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


# --------------------------------------------------------------------------- exit models


@dataclass(frozen=True)
class ExitModel:
    """How one engine family exits a single-leg position, for the re-walks."""

    family: str
    computable: bool
    reason: str | None = None
    # True: the fill happens inside (or at the open of) the entry bar and that bar's
    # whole range is checked; False: entry at the entry bar's close, exits from the next.
    entry_bar_inclusive: bool = True
    # A bar (after the entry bar) opening beyond the stop exits at its open.
    gap_stop_at_open: bool = False
    # Stop / target exit fill slippage: "trade" = derived from the trade's own
    # raw_exit_price vs exit_price; "none" = exits fill at the level.
    level_slippage: str = "none"
    # The engine's own hold limit (bars walked, entry bar included when inclusive).
    max_hold_bars: int | None = None
    # Slippage on a close exit (engine timeout or what-if time stop). None -> the trade's.
    close_slippage: float | None = None
    # Signal bar for CHASED_ENTRY when signal_time is the entry bar itself.
    signal_offset_same_bar: int = -1


def _ose_constants() -> tuple[int, float]:
    try:
        from app.research.ohlcv_structure_experiment.config import HOLD_BARS, SLIPPAGE_RATE

        return int(HOLD_BARS), float(SLIPPAGE_RATE)
    except Exception:  # noqa: BLE001
        return 20, 0.0002


def exit_model_for(strategy_id: Any, row: Mapping[str, Any] | None = None) -> ExitModel:
    family = str((row or {}).get("engine_family") or "").strip()
    if not family:
        try:
            from app.research.engine_backtest import ENGINE_STRATEGIES, engine_strategy_id

            sid = engine_strategy_id(str(strategy_id or ""))
            family = str(ENGINE_STRATEGIES[sid]["family"]) if sid else ""
        except Exception:  # noqa: BLE001
            family = ""
    if family == "candle12":
        # CANDLE12_V2: Candle-2 range checked (immediate exit possible); nearest touched
        # target exits full size; stop exits at the stop price; engine _exit_fill slippage.
        return ExitModel(
            family=family,
            computable=True,
            entry_bar_inclusive=True,
            gap_stop_at_open=False,
            level_slippage="trade",
            signal_offset_same_bar=-1,
        )
    if family == "multicap":
        return ExitModel(
            family=family,
            computable=True,
            entry_bar_inclusive=False,
            gap_stop_at_open=False,
            level_slippage="none",
            close_slippage=0.0,
            signal_offset_same_bar=0,
        )
    if family == "ohlcv_structure_experiment":
        hold, slip = _ose_constants()
        return ExitModel(
            family=family,
            computable=True,
            entry_bar_inclusive=True,
            gap_stop_at_open=True,
            level_slippage="none",
            max_hold_bars=hold,
            close_slippage=slip,
            signal_offset_same_bar=-1,
        )
    if family == "candle12_v3":
        return ExitModel(
            family=family,
            computable=False,
            reason=(
                "CANDLE12_V3 exits are multi-leg (TP1 split, runner, breakeven stop move, "
                "exchange quantity step); stop and time what-ifs are not re-simulated."
            ),
        )
    return ExitModel(
        family=family or "combo",
        computable=False,
        reason=(
            "This strategy family's fill model (next-open fills, HTF gates) is not "
            "re-simulated here; only the skip what-ifs are measured."
        ),
    )


# --------------------------------------------------------------------------- per-trade frame


@dataclass
class _Frame:
    trade: Mapping[str, Any]
    no: int
    direction: str
    sgn: int
    net: float
    gross: float | None
    fees: float | None
    entry: float | None
    stop: float | None
    risk_unit: float | None
    tp1: float | None
    targets: list[float]
    entry_idx: int | None
    walk_start: int | None
    exit_idx: int | None
    signal_idx: int | None
    exit_slip: float
    is_stop: bool
    outcome: str
    context: Mapping[str, Any]


def _targets(trade: Mapping[str, Any]) -> list[float]:
    out = [v for v in (_f(trade.get("tp1")), _f(trade.get("tp2")), _f(trade.get("tp3"))) if v is not None]
    if not out:
        tp = _f(trade.get("target_price"))
        if tp is not None:
            out.append(tp)
    return out


def _outcome_is_stop(outcome: str) -> bool:
    u = outcome.upper()
    return u in _STOP_OUTCOMES or u.startswith("SL") or "STOP" in u


def _trade_exit_slip(trade: Mapping[str, Any], sgn: int, model: ExitModel) -> float:
    if model.level_slippage != "trade":
        return 0.0
    raw = _f(trade.get("raw_exit_price"))
    fill = _f(trade.get("exit_price"))
    if not raw or fill is None:
        return 0.0
    # LONG fill = raw * (1 - s); SHORT fill = raw * (1 + s)
    s = (1.0 - fill / raw) if sgn > 0 else (fill / raw - 1.0)
    return max(0.0, s)


def _frame(trade: Mapping[str, Any], no: int, series: _Series, model: ExitModel) -> _Frame:
    direction = str(trade.get("direction") or "LONG").upper()
    sgn = _sgn(direction)
    entry = _f(trade.get("entry_price"))
    stop = _f(trade.get("stop_price"))
    risk_unit = abs(entry - stop) if entry is not None and stop is not None and entry != stop else None
    targets = _targets(trade)
    entry_time = trade.get("fill_time") or trade.get("entry_time") or trade.get("signal_time")
    entry_idx = series.index_of(entry_time)
    exit_idx = series.index_of(trade.get("exit_time"))
    walk_start = None
    if entry_idx is not None:
        walk_start = entry_idx if model.entry_bar_inclusive else entry_idx + 1
    signal_idx = None
    sig = series.index_of(trade.get("signal_time"))
    if sig is not None and entry_idx is not None:
        signal_idx = sig if sig < entry_idx else entry_idx + model.signal_offset_same_bar
        if signal_idx < 0:
            signal_idx = None
    elif entry_idx is not None:
        signal_idx = entry_idx + model.signal_offset_same_bar if entry_idx + model.signal_offset_same_bar >= 0 else None
    outcome = str(trade.get("outcome") or "")
    ctx = trade.get("market_context") if isinstance(trade.get("market_context"), Mapping) else {}
    return _Frame(
        trade=trade,
        no=no,
        direction=direction,
        sgn=sgn,
        net=float(trade.get("net_pnl_usd") or 0.0),
        gross=_f(trade.get("gross_pnl_usd")),
        fees=_f(trade.get("fee_total_usd")),
        entry=entry,
        stop=stop,
        risk_unit=risk_unit,
        tp1=_f(trade.get("tp1")) if _f(trade.get("tp1")) is not None else (targets[0] if targets else None),
        targets=targets,
        entry_idx=entry_idx,
        walk_start=walk_start,
        exit_idx=exit_idx,
        signal_idx=signal_idx,
        exit_slip=_trade_exit_slip(trade, sgn, model),
        is_stop=_outcome_is_stop(outcome),
        outcome=outcome,
        context=ctx or {},
    )


# --------------------------------------------------------------------------- re-walk


def _walk(
    series: _Series,
    fr: _Frame,
    model: ExitModel,
    *,
    stop: float,
    hold_limit: int | None,
    last_index: int,
) -> tuple[int, float, str] | None:
    """(exit_index, raw exit price, kind) or None while still open at ``last_index``.

    kind: stop | gap_stop | target | close (hold limit reached, exit at that bar's close).
    """
    assert fr.walk_start is not None and fr.entry_idx is not None
    sgn = fr.sgn
    targets = fr.targets
    end = min(last_index, series.n - 1)
    hold_last = fr.walk_start + hold_limit - 1 if hold_limit is not None else None
    for i in range(fr.walk_start, end + 1):
        o, h, lo, c = series.open[i], series.high[i], series.low[i], series.close[i]
        if model.gap_stop_at_open and i > fr.entry_idx:
            if (sgn > 0 and o <= stop) or (sgn < 0 and o >= stop):
                return i, o, "gap_stop"
        stop_hit = lo <= stop if sgn > 0 else h >= stop
        if stop_hit:  # same-bar stop and target -> stop
            return i, stop, "stop"
        reached = [t for t in targets if (h >= t if sgn > 0 else lo <= t)]
        if reached:
            return i, (min(reached) if sgn > 0 else max(reached)), "target"
        if hold_last is not None and i >= hold_last:
            return i, c, "close"
    return None


def _fill(raw: float, kind: str, fr: _Frame, model: ExitModel) -> float:
    if kind == "close":
        slip = model.close_slippage if model.close_slippage is not None else fr.exit_slip
    elif kind == "gap_stop":
        slip = 0.0 if model.level_slippage == "none" else fr.exit_slip
    else:
        slip = fr.exit_slip
    return raw * (1.0 - slip) if fr.sgn > 0 else raw * (1.0 + slip)


def _net_for(fr: _Frame, qty: float, exit_fill: float) -> float:
    t = fr.trade
    entry = float(fr.entry or 0.0)
    slip_in = float(_f(t.get("slippage_rate")) or 0.0)
    entry_fill = entry * (1.0 + slip_in) if fr.sgn > 0 else entry * (1.0 - slip_in)
    rate_in = _f(t.get("fee_entry_rate"))
    rate_out = _f(t.get("fee_exit_rate"))
    rate_in = 0.0 if rate_in is None else rate_in
    rate_out = rate_in if rate_out is None else rate_out
    gross = qty * (exit_fill - entry_fill) * fr.sgn
    fees = qty * entry_fill * rate_in + qty * exit_fill * rate_out
    return gross - fees


def _qty(fr: _Frame) -> float | None:
    return _f(fr.trade.get("qty")) or _f(fr.trade.get("quantity"))


def _resized_qty(fr: _Frame, new_risk_unit: float) -> float | None:
    t = fr.trade
    risk = _f(t.get("requested_risk_usd")) or _f(t.get("risk_usd"))
    if not risk or new_risk_unit <= 0:
        return None
    qty = risk / new_risk_unit
    cap = _f(t.get("max_leverage_quantity"))
    if cap and cap > 0:
        qty = min(qty, cap)
    return qty


# --------------------------------------------------------------------------- diagnostics


def _bar_fav(series: _Series, fr: _Frame, i: int) -> float:
    return (series.high[i] - fr.entry) if fr.sgn > 0 else (fr.entry - series.low[i])  # type: ignore[operator]


def _bar_adv(series: _Series, fr: _Frame, i: int) -> float:
    return (fr.entry - series.low[i]) if fr.sgn > 0 else (series.high[i] - fr.entry)  # type: ignore[operator]


def _context_flags(fr: _Frame) -> list[str]:
    flags: list[str] = []
    ctx = fr.context
    structure = str(ctx.get("structure") or "").upper()
    trend = str(ctx.get("trend") or "").upper()
    opposes = (fr.sgn > 0 and trend == "BEARISH") or (fr.sgn < 0 and trend == "BULLISH")
    if structure == "WRONG" or opposes:
        flags.append("AGAINST_CONTEXT")
    pos = _f(ctx.get("range_position"))
    if pos is not None and ((fr.sgn > 0 and pos > 0.5) or (fr.sgn < 0 and pos < 0.5)):
        flags.append("WRONG_RANGE_HALF")
    return flags


def pick_primary(flags: Sequence[str], *, allowed_entry_reasons: Sequence[str]) -> str:
    """First reason in REASON_ORDER that holds; entry reasons only when allowed."""
    have = set(flags)
    for reason in REASON_ORDER[:-1]:
        if reason in ENTRY_REASONS and reason not in allowed_entry_reasons:
            continue
        if reason in have:
            return reason
    return "INSUFFICIENT_DATA"


def _pct(x: float) -> str:
    return f"{100.0 * x:.0f}%"


def entry_reason_discrimination(
    frames: Sequence[_Frame],
    diags: Mapping[int, Mapping[str, Any]],
    *,
    post_fill: bool,
) -> tuple[list[str], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Which entry-condition reasons may be primary in this cell.

    A reason qualifies only when its rate among losers is at least the winners'
    rate + 0.15, it covers fewer than 90% of all trades, and the cell's
    market_context was not read after the fill. Returns (allowed reasons, the
    non-discriminating list for the summary, per-flag stats).
    """
    losers = [fr for fr in frames if round(fr.net, 2) < 0]
    winners = [fr for fr in frames if round(fr.net, 2) > 0]
    allowed: list[str] = []
    non_disc: list[dict[str, Any]] = []
    stats: dict[str, dict[str, Any]] = {}
    for flag in ENTRY_REASONS:
        on = {fr.no for fr in frames if flag in diags[fr.no]["flags"]}
        l_rate = (sum(1 for fr in losers if fr.no in on) / len(losers)) if losers else 0.0
        w_rate = (sum(1 for fr in winners if fr.no in on) / len(winners)) if winners else 0.0
        coverage = (len(on) / len(frames)) if frames else 0.0
        why: list[str] = []
        if not on:
            stats[flag] = {
                "flag": flag,
                "losers_rate": 0.0,
                "winners_rate": 0.0,
                "coverage": 0.0,
                "discriminating": False,
                "reason": "never present in this cell",
            }
            continue
        if post_fill:
            why.append("context read after the fill")
        if coverage >= DISCRIMINATION_MAX_COVERAGE:
            why.append(f"on {_pct(coverage)} of all trades")
        if l_rate < w_rate + DISCRIMINATION_MIN_GAP:
            why.append(
                f"also present on {_pct(w_rate)} of winners (losers {_pct(l_rate)}; "
                f"needs a {_pct(DISCRIMINATION_MIN_GAP)} gap)"
            )
        ok = not why
        stats[flag] = {
            "flag": flag,
            "losers_rate": _r(l_rate),
            "winners_rate": _r(w_rate),
            "coverage": _r(coverage),
            "discriminating": ok,
            "reason": "; ".join(why) if why else None,
        }
        if ok:
            allowed.append(flag)
        elif on:
            non_disc.append(
                {
                    "flag": flag,
                    "losers_rate": _r(l_rate),
                    "winners_rate": _r(w_rate),
                    "coverage": _r(coverage),
                    "reason": "; ".join(why),
                }
            )
    return allowed, non_disc, stats


def _diagnose(fr: _Frame, series: _Series, *, hindsight_bars: int) -> dict[str, Any]:
    """Metrics, flags, primary reason and hindsight for one trade (losers in full)."""
    t = fr.trade
    loser = fr.net < 0
    risk_usd = _f(t.get("risk_usd"))
    metrics: dict[str, Any] = {
        "stop_distance_pct": None,
        "stop_distance_atr": None,
        "tp1_distance_r": None,
        "mfe_r_before_exit": None,
        "mfe_pct_to_tp1": None,
        "bars_to_mfe": None,
        "hold_bars": None,
        "mae_r": None,
        "fees_r": _r(abs(fr.fees) / risk_usd) if fr.fees is not None and risk_usd else None,
    }
    flags: list[str] = []
    hindsight: dict[str, Any] = {
        "reached_tp1_after_exit": None,
        "bars_after_exit": None,
        "stop_needed_r": None,
        "max_favourable_after_exit_r": None,
    }
    extra: dict[str, Any] = {"mfe_index": None}

    fee_flipped = loser and fr.gross is not None and fr.gross > 0
    if fee_flipped:
        flags.append("FEES_FLIPPED")

    have_path = (
        fr.entry is not None
        and fr.risk_unit
        and fr.entry_idx is not None
        and fr.walk_start is not None
        and fr.exit_idx is not None
        and fr.exit_idx >= fr.entry_idx
        and fr.exit_idx < series.n
    )
    R = fr.risk_unit or 0.0
    if fr.entry and R:
        metrics["stop_distance_pct"] = _r(100.0 * R / fr.entry)
    if fr.tp1 is not None and fr.entry is not None and R:
        metrics["tp1_distance_r"] = _r(abs(fr.tp1 - fr.entry) / R)
    atr = series.atr[fr.signal_idx] if fr.signal_idx is not None and fr.signal_idx < series.n else None
    if atr and R:
        metrics["stop_distance_atr"] = _r(R / atr)
        if R < TIGHT_STOP_ATR * atr:
            flags.append("TIGHT_STOP")
        elif R > WIDE_STOP_ATR * atr:
            flags.append("WIDE_STOP")
    if fr.signal_idx is not None and fr.entry is not None and R and fr.signal_idx < series.n:
        chase = (fr.entry - series.close[fr.signal_idx]) * fr.sgn
        if chase > CHASE_FRACTION_OF_STOP * R:
            flags.append("CHASED_ENTRY")
    flags.extend(_context_flags(fr))

    tags = " ".join(str(x) for x in (t.get("tags") or []))
    if "GAP" in fr.outcome.upper() or "GAP" in tags.upper():
        flags.append("GAP_EXIT")

    reached_half = None
    late = False
    if have_path:
        assert fr.entry_idx is not None and fr.walk_start is not None and fr.exit_idx is not None
        metrics["hold_bars"] = fr.exit_idx - fr.entry_idx
        before = range(fr.walk_start, fr.exit_idx)
        best = 0.0
        best_i: int | None = None
        for i in before:
            fav = _bar_fav(series, fr, i)
            if fav > best:
                best, best_i = fav, i
        metrics["mfe_r_before_exit"] = _r(best / R)
        metrics["bars_to_mfe"] = (best_i - fr.entry_idx) if best_i is not None else None
        extra["mfe_index"] = best_i
        worst = 0.0
        for i in range(fr.walk_start, fr.exit_idx + 1):
            worst = max(worst, _bar_adv(series, fr, i))
        metrics["mae_r"] = _r(worst / R)
        if fr.tp1 is not None and abs(fr.tp1 - fr.entry) > 0:  # type: ignore[operator]
            dist = abs(fr.tp1 - fr.entry)  # type: ignore[operator]
            metrics["mfe_pct_to_tp1"] = _r(100.0 * best / dist, 2)
            reached_half = best >= GAVE_BACK_FRACTION * dist
        if fr.is_stop and fr.exit_idx > fr.entry_idx:
            o = series.open[fr.exit_idx]
            if fr.stop is not None and ((fr.sgn > 0 and o < fr.stop) or (fr.sgn < 0 and o > fr.stop)):
                flags.append("GAP_THROUGH_STOP")
        hold = fr.exit_idx - fr.entry_idx
        if (
            fr.is_stop
            and best_i is not None
            and best > 0
            and (best_i - fr.entry_idx) <= hold / 3.0
            and (fr.exit_idx - best_i) >= LATE_STOP_DRIFT_BARS
        ):
            late = True
    elif fr.is_stop and "GAP_STOP" in tags.upper():
        flags.append("GAP_THROUGH_STOP")

    primary = None
    if loser:
        if reached_half:
            flags.append("GAVE_BACK")
        if late:
            flags.append("LATE_STOP")
        if reached_half is False:
            flags.append("STOP_BEFORE_PROGRESS")
        # Provisional; analyze_row re-picks it once the cell-level discrimination
        # test has decided which entry-condition reasons may be primary.
        primary = pick_primary(flags, allowed_entry_reasons=ENTRY_REASONS)

        # Hindsight: TP1 within ``hindsight_bars`` bars after the exit.
        if have_path and fr.tp1 is not None:
            assert fr.exit_idx is not None and fr.walk_start is not None
            first = fr.exit_idx + 1
            last = min(series.n - 1, fr.exit_idx + hindsight_bars)
            if first <= last:
                hit = None
                best_after = None
                for j in range(first, last + 1):
                    fav = _bar_fav(series, fr, j)
                    best_after = fav if best_after is None else max(best_after, fav)
                    if hit is None and ((fr.sgn > 0 and series.high[j] >= fr.tp1) or (fr.sgn < 0 and series.low[j] <= fr.tp1)):
                        hit = j
                hindsight["reached_tp1_after_exit"] = hit is not None
                hindsight["max_favourable_after_exit_r"] = _r(best_after / R) if best_after is not None else None
                if hit is not None:
                    hindsight["bars_after_exit"] = hit - fr.exit_idx
                    worst = max(_bar_adv(series, fr, i) for i in range(fr.walk_start, hit + 1))
                    hindsight["stop_needed_r"] = _r(worst / R)

    # Stable order, no duplicates.
    order = list(REASON_ORDER[:-1]) + ["CHASED_ENTRY", "TIGHT_STOP", "WIDE_STOP", "GAP_EXIT", "GAP_THROUGH_STOP"]
    flags = [f for f in order if f in set(flags)]
    return {"metrics": metrics, "flags": flags, "primary": primary, "hindsight": hindsight, "extra": extra}


def _exit_phrase(fr: _Frame) -> str:
    u = fr.outcome.upper()
    if "GAP" in u and fr.is_stop:
        return "Gapped through the stop"
    if fr.is_stop:
        return "Stopped"
    if u in ("TIMEOUT", "TIME", "TIME_STOP"):
        return "Timed out"
    return f"Exited ({fr.outcome or 'unknown'})"


def _bars_in(hold: int | None) -> str:
    if hold is None:
        return ""
    if hold == 0:
        return " on the entry bar"
    return f" {hold} bar{'s' if hold != 1 else ''} in"


def _explain(fr: _Frame, d: Mapping[str, Any], hindsight_bars: int) -> str:
    m = d["metrics"]
    h = d["hindsight"]
    reason = d["primary"]
    verb = _exit_phrase(fr)
    hold = m.get("hold_bars")
    pct = m.get("mfe_pct_to_tp1")
    mfe_r = m.get("mfe_r_before_exit")
    progress = (
        f"after reaching {pct:.0f}% of the way to TP1 (+{mfe_r:.1f}R)" if pct is not None and mfe_r is not None else None
    )
    ctx = fr.context
    if reason == "FEES_FLIPPED":
        s1 = (
            f"Made +${(fr.gross or 0):.2f} before fees, but ${abs(fr.fees or 0):.2f} in fees"
            + (f" ({m['fees_r']:.2f}R)" if m.get("fees_r") is not None else "")
            + f" turned it into -${abs(fr.net):.2f}."
        )
    elif reason == "GAVE_BACK":
        s1 = f"{verb}{_bars_in(hold)} {progress}; the open gain was given back."
    elif reason == "AGAINST_CONTEXT":
        trend = _TREND_WORDS.get(str(ctx.get("trend") or "").upper(), "trend")
        s1 = f"Entered {fr.direction} against the {trend} (structure {str(ctx.get('structure') or 'WRONG').upper()})"
        s1 += f"; {verb.lower()}{_bars_in(hold)} {progress}." if progress else f"; {verb.lower()}{_bars_in(hold)}."
    elif reason == "WRONG_RANGE_HALF":
        pos = _f(ctx.get("range_position")) or 0.0
        half = "upper" if pos > 0.5 else "lower"
        s1 = f"Entered {fr.direction} at {pos * 100:.0f}% of the prior 24-bar range ({half} half)"
        s1 += f"; {verb.lower()}{_bars_in(hold)} {progress}." if progress else f"; {verb.lower()}{_bars_in(hold)}."
    elif reason == "LATE_STOP":
        drift = (fr.exit_idx or 0) - (d["extra"].get("mfe_index") or 0)
        best_txt = f"+{mfe_r:.1f}R" + (f", {pct:.0f}% of the way to TP1" if pct is not None else "")
        s1 = (
            f"Best price ({best_txt}) came {m.get('bars_to_mfe')} bars in; "
            f"it then drifted {drift} bars to the stop."
        )
    elif reason == "STOP_BEFORE_PROGRESS":
        s1 = (
            f"{verb}{_bars_in(hold)} after reaching only {pct:.0f}% of the way to TP1 (+{mfe_r:.1f}R)."
            if pct is not None and mfe_r is not None
            else f"{verb}{_bars_in(hold)}."
        )
    else:
        if hold is None:
            s1 = "The trade's bars could not be matched to the loaded candles, so the loss is not explained."
        else:
            s1 = f"{verb}{_bars_in(hold)}; the trade has no TP1 to measure progress against."
    reached = h.get("reached_tp1_after_exit")
    if reached is True:
        s2 = (
            f" After the exit, price reached TP1 {h['bars_after_exit']} bars later; "
            f"a stop wider than {h['stop_needed_r']:.1f}R would have held (hindsight)."
        )
    elif reached is False:
        best = h.get("max_favourable_after_exit_r")
        s2 = f" Price did not reach TP1 within {hindsight_bars} bars after the exit" + (
            f" (best {best:+.1f}R from entry)." if best is not None else "."
        )
    else:
        s2 = ""
    return s1 + s2


# --------------------------------------------------------------------------- what-ifs


def _what_if_stats(
    frames: Sequence[_Frame], new_nets: Mapping[int, float | None], *, unreplayed: int, open_at_end: int
) -> dict[str, Any]:
    kept = [fr for fr in frames if new_nets.get(fr.no, fr.net) is not None]
    net = sum(float(new_nets.get(fr.no, fr.net)) for fr in kept)  # type: ignore[arg-type]
    actual = sum(fr.net for fr in frames)
    winners_lost = 0
    changed = 0
    to_win = 0
    for fr in frames:
        new = new_nets.get(fr.no, fr.net)
        if new is None or abs(new - fr.net) > REPLAY_TOLERANCE_USD:
            changed += 1
        if fr.net > 0 and (new is None or new <= 0):
            winners_lost += 1
        if fr.net < 0 and new is not None and new > 0:
            to_win += 1
    return {
        "trades": len(kept),
        "net_usd": _r(net, 2),
        "delta_usd": _r(net - actual, 2),
        "winners_lost": winners_lost,
        "trades_changed": changed,
        "losers_to_winners": to_win,
        "unreplayed_trades": unreplayed,
        "open_at_end": open_at_end,
    }


def _replay_ok(series: _Series, fr: _Frame, model: ExitModel, last_index: int) -> bool:
    if fr.walk_start is None or fr.stop is None or fr.entry is None or fr.exit_idx is None:
        return False
    qty = _qty(fr)
    if not qty:
        return False
    res = _walk(series, fr, model, stop=fr.stop, hold_limit=model.max_hold_bars, last_index=max(last_index, fr.exit_idx))
    if res is None or res[0] != fr.exit_idx:
        return False
    net = _net_for(fr, qty, _fill(res[1], res[2], fr, model))
    return abs(net - fr.net) <= max(REPLAY_TOLERANCE_USD, 1e-6 * abs(qty * fr.entry))


def _rewalk_what_if(
    series: _Series,
    frames: Sequence[_Frame],
    model: ExitModel,
    replayable: Mapping[int, bool],
    last_index: int,
    *,
    stop_mult: float | None = None,
    time_stop: int | None = None,
) -> dict[str, Any]:
    new_nets: dict[int, float | None] = {}
    open_at_end = 0
    for fr in frames:
        if not replayable.get(fr.no):
            continue  # keeps the actual net
        assert fr.entry is not None and fr.stop is not None and fr.risk_unit
        stop = fr.stop
        qty = _qty(fr)
        if stop_mult is not None:
            stop = fr.entry - fr.sgn * stop_mult * fr.risk_unit
            qty = _resized_qty(fr, stop_mult * fr.risk_unit)
        if not qty:
            continue
        hold = model.max_hold_bars
        if time_stop is not None:
            hold = time_stop if hold is None else min(hold, time_stop)
        res = _walk(series, fr, model, stop=stop, hold_limit=hold, last_index=last_index)
        if res is None:
            # Still open at the backtest's last bar: marked at that bar's close.
            open_at_end += 1
            idx = min(last_index, series.n - 1)
            res = (idx, series.close[idx], "close")
        new_nets[fr.no] = _net_for(fr, qty, _fill(res[1], res[2], fr, model))
    unreplayed = sum(1 for fr in frames if not replayable.get(fr.no))
    return _what_if_stats(frames, new_nets, unreplayed=unreplayed, open_at_end=open_at_end)


def _context_after_fill(frames: Sequence[_Frame], series: _Series, model: ExitModel) -> int:
    """Trades whose market_context decision bar is the (intrabar-filled) entry bar or later."""
    if not model.entry_bar_inclusive:
        return 0
    count = 0
    for fr in frames:
        idx = series.index_of(fr.context.get("decision_bar")) if fr.context else None
        if idx is not None and fr.entry_idx is not None and idx >= fr.entry_idx:
            count += 1
    return count


def context_read_after_fill(frames: Sequence[_Frame], series: _Series, model: ExitModel) -> bool:
    """True when this cell's market_context is stamped after the fill.

    CANDLE12_V2 (engine family ``candle12``) stamps it on Candle-2, the bar the
    stop entry fills inside, so it reads that bar's close. Any other engine
    counts when a trade's decision bar is its intrabar-filled entry bar or later.
    """
    if not any(fr.context for fr in frames):
        return False
    if model.family == "candle12":
        return True
    return _context_after_fill(frames, series, model) > 0


def _build_what_ifs(
    series: _Series,
    frames: Sequence[_Frame],
    diags: Mapping[int, Mapping[str, Any]],
    model: ExitModel,
    last_index: int,
    *,
    entry_stats: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_caveat = (
        "In-sample, one cell; each closed trade re-walked on the same candles from its real fill "
        "(same entry, fee rates and slippage; same-bar stop and target -> stop); the walk ignores "
        "the one-position lock, so trades the engine skipped are never added."
    )
    replayable: dict[int, bool] = {}
    if model.computable:
        for fr in frames:
            replayable[fr.no] = _replay_ok(series, fr, model, last_index)
    n_ok = sum(1 for v in replayable.values() if v)
    replay = {
        "trades": len(frames),
        "replayed_exactly": n_ok if model.computable else None,
        "computable": model.computable,
        "note": (
            "Re-walk of each trade with its actual stop reproduces the stored exit bar and net $."
            if model.computable
            else model.reason
        ),
    }
    rewalk_ok = model.computable and frames and n_ok >= 0.9 * len(frames)
    not_ok_reason = model.reason if not model.computable else (
        f"Only {n_ok} of {len(frames)} trades re-walk to their stored exit on these candles; "
        "the candles may have changed since the backtest ran."
    )
    out: list[dict[str, Any]] = []

    def _unavailable(wid: str, label: str) -> dict[str, Any]:
        return {
            "id": wid,
            "label": label,
            "computable": False,
            "trades": None,
            "net_usd": None,
            "delta_usd": None,
            "winners_lost": None,
            "caveat": not_ok_reason,
        }

    unreplayed_note = ""
    if rewalk_ok and n_ok < len(frames):
        unreplayed_note = f" {len(frames) - n_ok} trade(s) that did not re-walk exactly keep their actual net."

    for mult in STOP_MULTIPLIERS:
        wid = f"stop_x{mult:g}".replace(".", "_")
        label = f"Stop {mult:g}x wider (target unchanged, same $ risk)"
        if not rewalk_ok:
            out.append(_unavailable(wid, label))
            continue
        stats = _rewalk_what_if(series, frames, model, replayable, last_index, stop_mult=mult)
        caveat = (
            base_caveat
            + " Size is reset to qty = risk $ / new stop distance (leverage cap kept), so $ risk stays the trade's."
            + unreplayed_note
        )
        if stats["open_at_end"]:
            caveat += f" {stats['open_at_end']} trade(s) still open at the last bar were marked at its close."
        out.append({"id": wid, "label": label, "computable": True, **stats, "caveat": caveat})

    for n in TIME_STOPS:
        wid = f"time_stop_{n}"
        label = f"Time stop: exit at the close after {n} bars"
        if not rewalk_ok:
            out.append(_unavailable(wid, label))
            continue
        stats = _rewalk_what_if(series, frames, model, replayable, last_index, time_stop=n)
        caveat = base_caveat + " Same size and stop; a close exit pays the engine's exit slippage." + unreplayed_note
        if model.max_hold_bars is not None and n >= model.max_hold_bars:
            caveat += f" The engine already closes after {model.max_hold_bars} bars, so this matches the actual hold."
        if stats["open_at_end"]:
            caveat += f" {stats['open_at_end']} trade(s) still open at the last bar were marked at its close."
        out.append({"id": wid, "label": label, "computable": True, **stats, "caveat": caveat})

    has_ctx = any(fr.context for fr in frames)
    for flag, wid, label in (
        ("AGAINST_CONTEXT", "skip_against_context", "Skip trades against the context (structure WRONG / trend opposes)"),
        ("WRONG_RANGE_HALF", "skip_wrong_range_half", "Skip trades in the wrong half of the prior 24-bar range"),
    ):
        if not has_ctx:
            out.append(
                {
                    "id": wid,
                    "label": label,
                    "computable": False,
                    "trades": None,
                    "net_usd": None,
                    "delta_usd": None,
                    "winners_lost": None,
                    "caveat": "No trade carries market_context, so this filter cannot be measured.",
                }
            )
            continue
        new_nets = {fr.no: (None if flag in diags[fr.no]["flags"] else fr.net) for fr in frames}
        stats = _what_if_stats(frames, new_nets, unreplayed=0, open_at_end=0)
        skipped = sum(1 for v in new_nets.values() if v is None)
        stats["trades_skipped"] = skipped
        caveat = (
            "In-sample, one cell; removes the flagged trades only. It never adds the signals the "
            "engine skipped while those trades were open (one-position lock)."
        )
        st = dict((entry_stats or {}).get(flag) or {})
        if st:
            caveat += (
                f" On losers {_pct(st['losers_rate'] or 0)}, on winners {_pct(st['winners_rate'] or 0)}, "
                f"on {_pct(st['coverage'] or 0)} of all trades."
            )
            if st.get("discriminating") is False:
                caveat += f" Not a discriminating filter here ({st['reason']}), so it is never suggested."
        late = _context_after_fill(frames, series, model)
        if late:
            caveat += (
                f" On {late} trade(s) market_context is read at the entry bar's close, after the fill, "
                "so a live filter could not have seen it."
            )
        out.append(
            {
                "id": wid,
                "label": label,
                "computable": True,
                **stats,
                "discriminating": st.get("discriminating") if st else None,
                "losers_rate": st.get("losers_rate") if st else None,
                "winners_rate": st.get("winners_rate") if st else None,
                "caveat": caveat,
            }
        )
    return out, replay


_BUCKETS = {
    "stop_x1_5": ("Widen the stop to 1.5x (same $ risk)", "STOP_BEFORE_PROGRESS"),
    "stop_x2": ("Widen the stop to 2x (same $ risk)", "STOP_BEFORE_PROGRESS"),
    "time_stop_12": ("Exit at the close after 12 bars", "LATE_STOP"),
    "time_stop_24": ("Exit at the close after 24 bars", "LATE_STOP"),
    "time_stop_48": ("Exit at the close after 48 bars", "LATE_STOP"),
    "skip_against_context": ("Skip entries against the context", "AGAINST_CONTEXT"),
    "skip_wrong_range_half": ("Skip entries in the wrong range half", "WRONG_RANGE_HALF"),
}


def _suggestions(what_ifs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for w in what_ifs:
        if not w.get("computable") or (w.get("delta_usd") or 0) <= 0:
            continue
        if w.get("discriminating") is False:  # never propose a non-discriminating / post-fill filter
            continue
        rule, bucket = _BUCKETS.get(str(w["id"]), (str(w.get("label")), "OTHER"))
        out.append(
            {
                "rule": rule,
                "reason_bucket": bucket,
                "trades_affected": w.get("trades_changed"),
                "net_change_usd": w.get("delta_usd"),
                "winners_lost": w.get("winners_lost"),
                "label": SUGGESTION_LABEL,
                "what_if_id": w["id"],
            }
        )
    out.sort(key=lambda s: -(s["net_change_usd"] or 0))
    return out


# --------------------------------------------------------------------------- main entry


def _loss_record(fr: _Frame, d: Mapping[str, Any], hindsight_bars: int) -> dict[str, Any]:
    t = fr.trade
    return {
        "trade_no": t.get("trade_no", fr.no),
        "trade_id": t.get("trade_id"),
        "entry_time": t.get("fill_time") or t.get("entry_time"),
        "exit_time": t.get("exit_time"),
        "direction": fr.direction,
        "outcome": fr.outcome or None,
        "net_usd": _r(fr.net, 2),
        "gross_usd": _r(fr.gross, 2),
        "fees_usd": _r(fr.fees, 2),
        "r_net": _r(_f(t.get("r_net"))),
        "primary_reason": d["primary"],
        "flags": list(d["flags"]),
        "metrics": dict(d["metrics"]),
        "hindsight": dict(d["hindsight"]),
        "hindsight_label": "hindsight",
        "explanation": _explain(fr, d, hindsight_bars),
        "market_context": dict(fr.context) if fr.context else None,
    }


def _iso_week(value: Any) -> str | None:
    ts = _utc(value)
    if ts is None:
        return None
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def analyze_row(
    row: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    hindsight_bars: int = DEFAULT_HINDSIGHT_BARS,
    strategy_id: str | None = None,
) -> dict[str, Any]:
    """Loss analysis for one Backtest row (pure: row + candles in, JSON-ready dict out)."""
    hindsight_bars = max(1, min(int(hindsight_bars), MAX_HINDSIGHT_BARS))
    trades = [t for t in (row.get("trades") or []) if isinstance(t, Mapping) and t.get("net_pnl_usd") is not None]
    sid = strategy_id or row.get("combination_id") or row.get("strategy_id")
    symbol = str(row.get("symbol") or "")
    timeframe = str(row.get("timeframe") or "")
    direction = str(row.get("direction") or (trades[0].get("direction") if trades else "") or "").upper() or None
    frozen = is_frozen_book(sid, symbol, timeframe, direction)
    model = exit_model_for(sid, row)
    series = _Series(candles)

    frames = [_frame(t, int(t.get("trade_no") or i), series, model) for i, t in enumerate(trades, 1)]
    diags = {fr.no: _diagnose(fr, series, hindsight_bars=hindsight_bars) for fr in frames}
    post_fill = context_read_after_fill(frames, series, model)
    allowed_entry, non_discriminating, entry_stats = entry_reason_discrimination(
        frames, diags, post_fill=post_fill
    )
    for fr in frames:
        if diags[fr.no]["primary"] is not None:
            diags[fr.no]["primary"] = pick_primary(diags[fr.no]["flags"], allowed_entry_reasons=allowed_entry)

    period_end_idx = series.index_at_or_before(row.get("period_end"))
    last_index = period_end_idx if period_end_idx is not None else series.n - 1

    losers = [fr for fr in frames if round(fr.net, 2) < 0]
    winners = [fr for fr in frames if round(fr.net, 2) > 0]
    scratches = len(frames) - len(losers) - len(winners)
    loss_records = [_loss_record(fr, diags[fr.no], hindsight_bars) for fr in losers]
    fee_flipped = [rec for rec in loss_records if rec["primary_reason"] == "FEES_FLIPPED"]

    total_loss = sum(fr.net for fr in losers)
    by_reason = []
    for reason in REASON_ORDER:
        group = [fr for fr in losers if diags[fr.no]["primary"] == reason]
        if not group:
            continue
        net = sum(fr.net for fr in group)
        by_reason.append(
            {
                "reason": reason,
                "count": len(group),
                "net_usd": _r(net, 2),
                "share_of_loss": _r(net / total_loss) if total_loss else None,
            }
        )
    ctx_groups: dict[str, list[_Frame]] = {}
    for fr in losers:
        key = str(fr.context.get("structure") or "UNKNOWN").upper()
        ctx_groups.setdefault(key, []).append(fr)
    by_context = [
        {"structure": k, "count": len(v), "net_usd": _r(sum(fr.net for fr in v), 2)}
        for k, v in sorted(ctx_groups.items(), key=lambda kv: sum(fr.net for fr in kv[1]))
    ]
    largest = sorted(losers, key=lambda fr: fr.net)[:5]
    largest_losses = [
        {
            "trade_no": fr.trade.get("trade_no", fr.no),
            "entry_time": fr.trade.get("fill_time") or fr.trade.get("entry_time"),
            "net_usd": _r(fr.net, 2),
            "reason": diags[fr.no]["primary"],
        }
        for fr in largest
    ]
    weeks: dict[str, list[_Frame]] = {}
    for fr in losers:
        wk = _iso_week(fr.trade.get("exit_time") or fr.trade.get("entry_time"))
        if wk:
            weeks.setdefault(wk, []).append(fr)
    loss_weeks = [
        {"week": wk, "count": len(v), "net_usd": _r(sum(fr.net for fr in v), 2)}
        for wk, v in sorted(weeks.items(), key=lambda kv: (sum(fr.net for fr in kv[1]), -len(kv[1])))
    ][:8]
    held = [fr for fr in losers if diags[fr.no]["hindsight"].get("reached_tp1_after_exit") is True]

    what_ifs, replay = (
        _build_what_ifs(series, frames, diags, model, last_index, entry_stats=entry_stats)
        if frames
        else ([], {})
    )
    if frozen:
        suggestions: list[dict[str, Any]] = []
        note: str | None = FROZEN_NOTE
    else:
        suggestions = _suggestions(what_ifs)
        note = None if suggestions else NO_GAIN_NOTE

    gross_vals = [fr.gross for fr in frames if fr.gross is not None]
    fee_vals = [fr.fees for fr in frames if fr.fees is not None]
    unmatched = sum(1 for fr in frames if fr.entry_idx is None or fr.exit_idx is None)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_id": sid,
        "direction": direction,
        "frozen_book": frozen,
        "suggestions_note": note,
        "period": {"start": row.get("period_start"), "end": row.get("period_end")},
        "hindsight_bars": hindsight_bars,
        "summary": {
            "trades": len(frames),
            "winners": len(winners),
            "losers": len(losers),
            "scratches": scratches,
            "net_usd": _r(sum(fr.net for fr in frames), 2),
            "gross_usd": _r(sum(gross_vals), 2) if gross_vals else None,
            "fees_usd": _r(sum(fee_vals), 2) if fee_vals else None,
            "fees_sign": "negative = cost (net = gross + fees)",
            "loss_usd": _r(total_loss, 2),
            "by_reason": by_reason,
            "by_context": by_context,
            "largest_losses": largest_losses,
            "loss_weeks": loss_weeks,
            "held_would_have_won": {"count": len(held), "net_usd": _r(sum(fr.net for fr in held), 2)},
            "trades_unmatched_to_candles": unmatched,
            "non_discriminating_flags": non_discriminating,
            "context_read_after_fill": post_fill,
        },
        "losses": loss_records,
        "fee_flipped": fee_flipped,
        "what_ifs": what_ifs,
        "suggestions": suggestions,
        "replay_check": replay,
        "engine_family": model.family,
        "candles_used": series.n,
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------- job glue

CandleLoader = Callable[..., Awaitable[tuple[list[dict[str, Any]], int, dict[str, Any]]]]


def _find_row(job: Any, symbol: str, timeframe: str) -> dict[str, Any]:
    from app.research.query_utils import normalize_research_symbol, normalize_research_timeframe

    sym = normalize_research_symbol(symbol)
    tf = normalize_research_timeframe(timeframe)
    for row in getattr(job, "rows", None) or []:
        if not isinstance(row, Mapping):
            continue
        try:
            rsym = normalize_research_symbol(str(row.get("symbol") or ""))
            rtf = normalize_research_timeframe(str(row.get("timeframe") or ""))
        except Exception:  # noqa: BLE001
            continue
        if rsym == sym and rtf == tf:
            return dict(row)
    raise LossAnalysisUnavailable(f"The finished backtest job has no row for {sym} {tf}.")


async def loss_analysis_for_current_job(
    symbol: str,
    timeframe: str,
    *,
    max_hold_after_exit: int = DEFAULT_HINDSIGHT_BARS,
    service: Any = None,
    candle_loader: CandleLoader | None = None,
) -> dict[str, Any]:
    """Analyse the current finished backtest job's row for ``symbol``/``timeframe``.

    Raises ``LossAnalysisUnavailable`` (HTTP 409) when there is no finished job,
    no matching row, or the row carries no trades.
    """
    if service is None:
        from app.research.backtest_job import backtest_job_service as service
    job = getattr(service, "_job", None)
    if job is None:
        raise LossAnalysisUnavailable("No backtest job has run yet; run a backtest first.")
    status = str(getattr(job, "status", "") or "")
    if status != "done":
        raise LossAnalysisUnavailable(
            f"The backtest job is {status or 'not finished'}; loss analysis needs a finished job."
        )
    row = _find_row(job, symbol, timeframe)
    trades = [t for t in (row.get("trades") or []) if isinstance(t, Mapping)]
    if not trades:
        if not bool(getattr(job, "include_trades", True)):
            raise LossAnalysisUnavailable(
                "This backtest ran without trades (include_trades was off); rerun it with trades."
            )
        raise LossAnalysisUnavailable("This cell has no closed trades to analyse.")

    hindsight = max(1, min(int(max_hold_after_exit), MAX_HINDSIGHT_BARS))
    tf = str(row.get("timeframe") or timeframe)
    bar_s = _TF_SECONDS.get(tf.lower(), 3600)
    start = _utc(row.get("period_start")) or min(
        (_utc(t.get("signal_time") or t.get("entry_time")) for t in trades if _utc(t.get("signal_time") or t.get("entry_time"))),
        default=None,
    )
    last_exit = max((_utc(t.get("exit_time")) for t in trades if _utc(t.get("exit_time"))), default=None)
    end_candidates = [x for x in (_utc(row.get("period_end")), last_exit) if x is not None]
    if start is None or not end_candidates:
        raise LossAnalysisUnavailable("The row has no period or trade times to load candles for.")
    end = max(end_candidates)
    if last_exit is not None:
        end = max(end, last_exit + timedelta(seconds=bar_s * (hindsight + 1)))

    if candle_loader is None:
        from app.research.service import _load_research_candles as candle_loader
    candles, _eval_start, _meta = await candle_loader(
        str(row.get("symbol") or symbol),
        tf,
        limit=1,
        start_date=start.date().isoformat(),
        end_date=end.date().isoformat(),
        warmup_bars=CANDLE_WARMUP_BARS,
        use_research_cache=False,
        tail_warmup=True,
    )
    if not candles:
        raise LossAnalysisUnavailable("No candles could be loaded for this cell.")
    sid = row.get("combination_id") or getattr(job, "combination_id", None)
    out = await asyncio.to_thread(analyze_row, row, candles, hindsight_bars=hindsight, strategy_id=sid)
    out["job_id"] = getattr(job, "job_id", None)
    return out
