"""Admin-regime backtest of the passed book.

One run, one shared position per symbol, its own equity curve. At each closed
1h bar the BTC daily trend (``btc_daily_regime.daily_trend`` over the daily
bars closed at that bar's close) picks the sides that may open
(``passed_regime_router.allowed_passed_sides``). Entry, stop, target, exit,
size, leverage, and fees come from the existing backtest and fee code; this
module only decides which of those trades the shared book takes. No I/O.

How signals are produced
    Every side the trend allows on at least one bar is walked once over the
    whole series by ``run_combination_backtest``, so a strategy that builds a
    setup over several bars sees all of them. A signal on a bar where the
    trend does not allow the side opens nothing and is not listed. That walk
    does not evaluate bars while its own trade is open, so whenever the
    shared book does not take a trade, the bars that trade hid are walked
    again from the bar after its signal (``index_start``/``index_end``). A
    trade cut off by the end of such a walk is finished by repeating the same
    walk with a later end. See ``LIMITATIONS``.

Result of ``run_regime_book`` / ``build_result``
    status            "OK" or "CANCELLED"
    principal_usd, taker_fee, maker_fee, leverage
    equity_basis      "REALIZED_ONLY"
    limitations       list of str (``LIMITATIONS``)
    trades            list, in signal order. Every field of the existing trade
                      dict (symbol, timeframe, combination_id, entry_index,
                      signal_time, direction, entry_price, stop_price, tp1,
                      tp2, tp3, rr, exit_index, exit_time, exit_price,
                      outcome, r_multiple, holding_bars, mae, mfe, ...) and of
                      ``trade_fees.enrich_trade_execution`` (qty, risk_usd,
                      requested_risk_usd, account_equity, capped_by_leverage,
                      fee_entry_usd, fee_exit_usd, fee_total_usd,
                      gross_pnl_usd, net_pnl_usd, r_gross, r_net, ...), plus:
                        strategy_id       same value as combination_id
                        trend_at_entry    BULLISH or BEARISH
                        as_of_daily_bar   ISO open time of the daily bar used
                        daily_bars        closed daily bars behind that trend
                        entry_time        open time of the entry bar
                        target_price      same value as tp1
                        exit_resolution   IN_WALK or REWALK
                        rewalk_first_bar  see ``signals``
                      Times are bar open times, as in the existing trade
                      dicts. A trade still open at the last bar has outcome
                      OPEN and no fee or PnL fields.
    signals           list, every signal an allowed side emitted:
                        strategy_id, symbol, timeframe, direction,
                        signal_time, bar_index, trend, as_of_daily_bar,
                        entry_price, stop_price, tp1, taken, skip_reason
                        (None, POSITION_OPEN, or SAME_BAR_EARLIER_SIDE),
                        blocked_by ({strategy_id, direction, signal_time}),
                        rewalk_first_bar (True when the signal is on the
                        first bar of a walk over hidden bars; see the first
                        limitation)
    equity_curve      list of points, first at the first bar with
                      equity_usd == principal_usd, then one per closed trade
                      in exit order:
                        time, equity_usd, realized_pnl_usd_net, drawdown_usd,
                        drawdown_pct, open_positions
                      Realized only: equity moves when a trade exits. The
                      existing backtest prices a trade at its exit and has no
                      rule for marking an open one, so none is invented here.
    summary           trades, open_trades, wins, losses, win_rate,
                      pnl_usd_net, pnl_usd_gross, fees_usd, final_equity_usd,
                      max_drawdown_usd, max_drawdown_pct, signals,
                      signals_taken, skipped_signals {reason: count},
                      signals_on_rewalk_first_bar, trades_on_rewalk_first_bar,
                      signals_outside_trend,
                      by_trend {BULLISH/BEARISH: symbol_bars, signals, trades,
                      wins, losses, pnl_usd_net; NEUTRAL: symbol_bars,
                      short_history_symbol_bars}, by_strategy
                      {"<id>|<direction>": strategy_id, direction, timeframe,
                      signals, taken, trades, wins, losses, pnl_usd_net},
                      regime_timeline [{trend, start_daily_bar, end_daily_bar,
                      daily_bars, short_history}]
    symbols           per symbol: bars, period_start, period_end, walks,
                      signals, trades, bars_by_trend, short_history_bars,
                      short_history_until, gap_walks, exit_rewalks,
                      signals_outside_trend, errors
"""

from __future__ import annotations

import heapq
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from app.research import passed_regime_router as router
from app.research.btc_daily_regime import MIN_DAILY_BARS, daily_trend
from app.research.passed_cap_book import PRINCIPAL_USD, passed_risk_percent
from app.signals._candle_utils import candle_time

RUN_TIMEFRAME = "1h"
BAR = timedelta(hours=1)
DAY = timedelta(days=1)

# Same fee and leverage inputs as scripts/run_passed_cap_book.py.
TAKER_FEE = 0.0004
MAKER_FEE = 0.0002
LEVERAGE = 2.0

SKIP_POSITION_OPEN = "POSITION_OPEN"
SKIP_SAME_BAR = "SAME_BAR_EARLIER_SIDE"

# Bars added to a cut-off walk to find the trade's exit; doubles each try.
EXIT_SPAN = 48

EQUITY_BASIS = "REALIZED_ONLY"

LIMITATIONS = (
    "A walk over hidden bars starts the bar after a signal the book did not "
    "take, with no memory of that signal: the backtest's repeat-break skip "
    "(prev_break in combination_backtest) and the transition strategies' "
    "last-entered-event key start empty. The side can signal again on the "
    "same break or sweep where one unbroken single-strategy walk would not. "
    "Bias: more signals. rewalk_first_bar marks the signals the repeat-break "
    "skip could have removed.",
    "The single-strategy backtest never says what a strategy does after a "
    "signal that is not traded; it always trades it. Restarting the walk on "
    "the next bar is this run's reading of that case.",
    "The paper watcher evaluates each closed bar on its own, with no break "
    "pre-filter, no repeat-break skip, and no setup carried between bars. "
    "This run keeps all three, as the single-strategy backtest does, so the "
    "two can differ in either direction.",
    "The leverage cap uses this run's equity (principal plus net PnL of its "
    "trades closed at the signal bar), the retest's running-equity rule "
    "applied to one shared book instead of one strategy cell.",
)


class RunCancelled(Exception):
    pass


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse(stamp: Any) -> datetime | None:
    if isinstance(stamp, datetime):
        return _utc(stamp)
    if not stamp:
        return None
    try:
        return _utc(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")))
    except ValueError:
        return None


def _bar_iso(candle: Mapping[str, Any]) -> str | None:
    ts = candle_time(candle)
    return _utc(ts).isoformat() if ts is not None else None


class RegimeClock:
    """BTC daily trend as of any moment, one ``daily_trend`` call per daily bar."""

    def __init__(self, btc_daily: Sequence[Mapping[str, Any]], signal_config: Any) -> None:
        rows = []
        for candle in btc_daily or []:
            ts = candle_time(candle)
            if ts is not None:
                rows.append((_utc(ts), candle))
        rows.sort(key=lambda row: row[0])
        self._bars = [candle for _, candle in rows]
        # A daily bar opened at T is closed from T + 24h.
        self._closes = [ts + DAY for ts, _ in rows]
        self._signal_config = signal_config
        self._cache: dict[int, dict[str, Any]] = {}

    def closed_count(self, moment: datetime) -> int:
        return bisect_right(self._closes, _utc(moment))

    def by_count(self, count: int) -> dict[str, Any]:
        """Trend over the first ``count`` daily bars."""
        if count not in self._cache:
            out = dict(daily_trend(self._bars[:count], self._signal_config))
            self._cache[count] = {
                "trend": router.normalize_trend(out.get("trend")),
                "as_of_bar": str(out.get("as_of_bar") or ""),
                "bars": count,
                "short_history": count < MIN_DAILY_BARS,
            }
        return self._cache[count]

    def at(self, moment: datetime) -> dict[str, Any]:
        """Trend over the daily bars fully closed at ``moment``."""
        return self.by_count(self.closed_count(moment))

    def timeline(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for count in range(self.closed_count(start), self.closed_count(end) + 1):
            info = self.by_count(count)
            last = segments[-1] if segments else None
            if (
                last is not None
                and last["trend"] == info["trend"]
                and last["short_history"] == info["short_history"]
            ):
                last["end_daily_bar"] = info["as_of_bar"]
                last["daily_bars"] += 1
                continue
            segments.append(
                {
                    "trend": info["trend"],
                    "start_daily_bar": info["as_of_bar"],
                    "end_daily_bar": info["as_of_bar"],
                    "daily_bars": 1,
                    "short_history": info["short_history"],
                }
            )
        return segments


def _default_backtest() -> Callable[..., dict[str, Any]]:
    from app.research.combination_backtest import run_combination_backtest

    return run_combination_backtest


def _default_postprocess() -> Callable[[dict[str, Any]], dict[str, Any]]:
    from app.research.backtest_cpu_worker import build_matrix_row_postprocess

    return build_matrix_row_postprocess


def _same_setup(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return all(
        a.get(key) == b.get(key)
        for key in ("entry_index", "direction", "entry_price", "stop_price", "tp1")
    )


def run_symbol(
    symbol: str,
    book: Mapping[str, Any],
    clock: RegimeClock,
    *,
    signal_config: Any = None,
    research_config: Any = None,
    market_cap: float | None = None,
    backtest_fn: Callable[..., dict[str, Any]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Pick the shared book's trades for one symbol. JSON-ready, no sizing.

    ``book`` is the dict ``run_cap_mtf_risk._load`` returns: ``candles`` (1h,
    closed, oldest first), ``c1h``, ``c4h``, ``c15``, ``c1d``.
    """
    backtest = backtest_fn or _default_backtest()
    sym = str(symbol).upper()
    series = list(book.get("candles") or [])
    n = len(series)

    regimes: list[dict[str, Any]] = []
    allowed_at: list[tuple[tuple[str, str, str], ...]] = []
    bars_by_trend = {trend: 0 for trend in router.TRENDS}
    short_history_bars = 0
    short_history_until: str | None = None
    # Sides the trend allows on at least one bar.
    sides: list[tuple[str, str, str]] = []
    for i, candle in enumerate(series):
        ts = candle_time(candle)
        # No bar time means no trend can be dated: nothing may open.
        regime = clock.at(_utc(ts) + BAR) if ts is not None else clock.by_count(0)
        regimes.append(regime)
        bars_by_trend[regime["trend"]] += 1
        if regime["short_history"]:
            short_history_bars += 1
            short_history_until = _bar_iso(candle)
        allowed = tuple(
            side
            for side in router.allowed_passed_sides(regime["trend"])
            if side[1] == RUN_TIMEFRAME
        )
        allowed_at.append(allowed)
        for side in allowed:
            if side not in sides:
                sides.append(side)

    heap: list[tuple[int, int, int, dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    stats = {"walks": 0, "gap_walks": 0, "exit_rewalks": 0, "outside_trend": 0}
    seq = 0

    def walk(side: tuple[str, str, str], start: int | None, end: int | None) -> list[dict[str, Any]]:
        """Existing backtest over bars [start, end) for one side. None is the series edge."""
        if should_cancel is not None and should_cancel():
            raise RunCancelled()
        combination_id, timeframe, direction = side
        stats["walks"] += 1
        out = backtest(
            symbol=sym,
            timeframe=timeframe,
            candles=series,
            combination=combination_id,
            signal_config=signal_config,
            research_config=research_config,
            market_cap=market_cap,
            direction_filter=direction,
            index_start=start,
            index_end=end,
            should_cancel=should_cancel,
            candles_1h=book.get("c1h"),
            candles_4h=book.get("c4h"),
            candles_15m=book.get("c15"),
            candles_1d=book.get("c1d"),
        )
        status = str((out or {}).get("status") or "")
        if status == "CANCELLED":
            raise RunCancelled()
        if progress is not None:
            progress(
                {
                    "symbol": sym,
                    "strategy_id": combination_id,
                    "direction": direction,
                    "index_start": start,
                    "index_end": end,
                    "status": status,
                    "walks": stats["walks"],
                }
            )
        if status != "OK":
            errors.append(
                {
                    "strategy_id": combination_id,
                    "direction": direction,
                    "index_start": start,
                    "index_end": end,
                    "status": status,
                    "reason": (out or {}).get("reason") or (out or {}).get("disclaimer"),
                }
            )
            return []
        return [
            dict(trade)
            for trade in (out.get("trades") or [])
            if str(trade.get("direction") or "").upper() == direction
        ]

    def push(side: tuple[str, str, str], start: int | None, keep_to: int) -> None:
        """Walk bars ``start`` to ``keep_to`` and queue the signals found."""
        nonlocal seq
        end = min(n, keep_to + 1)
        for trade in walk(side, start, None if end >= n else end):
            i = int(trade["entry_index"])
            if not ((start or 0) <= i <= keep_to):
                continue
            seq += 1
            item = {"side": side, "trade": trade, "anchor": start, "end": end, "keep_to": keep_to}
            rank = allowed_at[i].index(side) if side in allowed_at[i] else len(allowed_at[i])
            heapq.heappush(heap, (i, rank, seq, item))

    def resolve_exit(item: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Repeat a cut-off walk with a later end until the trade exits."""
        trade = item["trade"]
        end = item["end"]
        span = EXIT_SPAN
        while end < n:
            end = min(n, end + span)
            span *= 2
            stats["exit_rewalks"] += 1
            again = walk(item["side"], item["anchor"], None if end >= n else end)
            found = next((t for t in again if _same_setup(t, trade)), None)
            if found is None:
                return trade, "UNRESOLVED"
            if found.get("outcome") != "OPEN":
                return found, "REWALK"
            trade = found
        return trade, "REWALK"

    def walk_hidden(item: dict[str, Any], i: int, open_here: bool) -> None:
        """Walk the bars a trade the book did not take hid from its own walk."""
        side = item["side"]
        hidden_to = item["end"] - 1 if open_here else int(item["trade"]["exit_index"])
        hidden_to = min(hidden_to, item["keep_to"])
        if any(side in allowed_at[k] for k in range(i + 1, hidden_to + 1)):
            stats["gap_walks"] += 1
            push(side, i + 1, hidden_to)

    signals: list[dict[str, Any]] = []
    taken: list[dict[str, Any]] = []
    cancelled = False
    holder: dict[str, Any] | None = None
    busy_until = -1  # last bar index the symbol is held on
    try:
        for side in sides:
            push(side, None, n - 1)
        while heap:
            i, _, _, item = heapq.heappop(heap)
            side = item["side"]
            combination_id, timeframe, direction = side
            trade = item["trade"]
            regime = regimes[i]
            open_here = trade.get("outcome") == "OPEN" or trade.get("exit_index") is None
            if not router.is_side_allowed(regime["trend"], combination_id, timeframe, direction):
                # The side keeps watching on bars it may not trade; nothing opens.
                stats["outside_trend"] += 1
                walk_hidden(item, i, open_here)
                continue
            signal = {
                "strategy_id": combination_id,
                "symbol": sym,
                "timeframe": timeframe,
                "direction": direction,
                "signal_time": trade.get("signal_time"),
                "bar_index": i,
                "trend": regime["trend"],
                "as_of_daily_bar": regime["as_of_bar"],
                "entry_price": trade.get("entry_price"),
                "stop_price": trade.get("stop_price"),
                "tp1": trade.get("tp1"),
                "taken": False,
                "skip_reason": None,
                "blocked_by": None,
                "rewalk_first_bar": item["anchor"] is not None and i == item["anchor"],
            }
            signals.append(signal)
            if holder is not None and i <= busy_until:
                same_bar = int(holder["entry_index"]) == i
                signal["skip_reason"] = SKIP_SAME_BAR if same_bar else SKIP_POSITION_OPEN
                signal["blocked_by"] = {
                    "strategy_id": holder["combination_id"],
                    "direction": holder["direction"],
                    "signal_time": holder.get("signal_time"),
                }
                walk_hidden(item, i, open_here)
                continue
            resolution = "IN_WALK"
            if open_here and item["end"] < n:
                trade, resolution = resolve_exit(item)
            signal["taken"] = True
            closed = trade.get("outcome") != "OPEN" and trade.get("exit_index") is not None
            busy_until = int(trade["exit_index"]) if closed else n
            holder = trade
            row = {key: value for key, value in trade.items() if key != "condition_snapshot"}
            snapshot = trade.get("condition_snapshot")
            if isinstance(snapshot, Mapping) and snapshot.get("entry_type") is not None:
                row["entry_type"] = snapshot.get("entry_type")
            row.update(
                {
                    "strategy_id": combination_id,
                    "trend_at_entry": regime["trend"],
                    "as_of_daily_bar": regime["as_of_bar"],
                    "daily_bars": regime["bars"],
                    "entry_time": _bar_iso(series[i]),
                    "target_price": trade.get("tp1"),
                    "exit_resolution": resolution,
                    "rewalk_first_bar": signal["rewalk_first_bar"],
                }
            )
            taken.append(row)
    except RunCancelled:
        cancelled = True

    return {
        "symbol": sym,
        "status": "CANCELLED" if cancelled else "OK",
        "bars": n,
        "period_start": _bar_iso(series[0]) if series else None,
        "period_end": _bar_iso(series[-1]) if series else None,
        "bars_by_trend": bars_by_trend,
        "short_history_bars": short_history_bars,
        "short_history_until": short_history_until,
        "walks": stats["walks"],
        "gap_walks": stats["gap_walks"],
        "exit_rewalks": stats["exit_rewalks"],
        "signals_outside_trend": stats["outside_trend"],
        "errors": errors,
        "signals": signals,
        "trades": taken,
    }


def _size_trade(
    trade: Mapping[str, Any],
    *,
    equity: float,
    principal: float,
    taker_fee: float,
    maker_fee: float,
    leverage: float,
    postprocess: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    """Existing fee and leverage step for one trade. None when it is still open."""
    symbol = str(trade.get("symbol") or "")
    direction = str(trade.get("direction") or "")
    risk_usd = passed_risk_percent(symbol, direction, str(trade.get("trend_at_entry") or "")) * principal
    row = postprocess(
        {
            "backtest_out": {"status": "OK", "trades": [dict(trade)], "result": {}, "sample_size": 1},
            "risk_usd": risk_usd,
            "taker_fee": taker_fee,
            "maker_fee": maker_fee,
            "leverage": leverage,
            "principal": equity,
            "include_trades": True,
            "direction": direction,
            "combination_id": trade.get("combination_id"),
            "combination_name": trade.get("combination_id"),
            "load_meta": {"symbol": symbol, "timeframe": trade.get("timeframe")},
            "candles": [],
            "analytics_on": False,
        }
    )
    sized = list(row.get("trades") or [])
    return dict(sized[0]) if sized else None


def build_result(
    symbol_results: Sequence[Mapping[str, Any]],
    clock: RegimeClock,
    *,
    principal: float = PRINCIPAL_USD,
    taker_fee: float = TAKER_FEE,
    maker_fee: float = MAKER_FEE,
    leverage: float = LEVERAGE,
    postprocess_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Size the taken trades on one account and build the curve and summary."""
    postprocess = postprocess_fn or _default_postprocess()
    principal = float(principal)
    order = {str(res.get("symbol")): n for n, res in enumerate(symbol_results)}
    far = datetime.max.replace(tzinfo=timezone.utc)

    def by_signal(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return (_parse(row.get("signal_time")) or far, order.get(str(row.get("symbol")), 0))

    raw = sorted((t for res in symbol_results for t in (res.get("trades") or [])), key=by_signal)
    signals = sorted((s for res in symbol_results for s in (res.get("signals") or [])), key=by_signal)

    trades: list[dict[str, Any]] = []
    closed: list[tuple[datetime | None, float]] = []
    for trade in raw:
        signal_ts = _parse(trade.get("signal_time"))
        # Same rule as trade_fees.enrich_trades(running_equity=True).
        realized = sum(
            pnl
            for exit_ts, pnl in closed
            if signal_ts is None or exit_ts is None or exit_ts <= signal_ts
        )
        sized = None
        if trade.get("outcome") not in (None, "", "OPEN"):
            sized = _size_trade(
                trade,
                equity=max(1e-6, principal + realized),
                principal=principal,
                taker_fee=taker_fee,
                maker_fee=maker_fee,
                leverage=leverage,
                postprocess=postprocess,
            )
        row = dict(sized) if sized is not None else dict(trade)
        row.pop("condition_snapshot", None)
        # The fee step numbers trades per call; number them across the book.
        row.pop("source_index", None)
        row["trade_no"] = len(trades) + 1
        row["trade_id"] = f"t{row['trade_no']}"
        trades.append(row)
        if row.get("net_pnl_usd") is not None:
            closed.append((_parse(row.get("exit_time")), float(row["net_pnl_usd"])))

    done = [t for t in trades if t.get("net_pnl_usd") is not None]
    done.sort(
        key=lambda t: (
            _parse(t.get("exit_time")) or far,
            _parse(t.get("signal_time")) or far,
            order.get(str(t.get("symbol")), 0),
        )
    )
    starts = [s for s in (_parse(res.get("period_start")) for res in symbol_results) if s]
    ends = [e for e in (_parse(res.get("period_end")) for res in symbol_results) if e]
    opened_at = sorted(_parse(t.get("signal_time")) or far for t in trades)
    curve = [
        {
            "time": min(starts).isoformat() if starts else None,
            "equity_usd": principal,
            "realized_pnl_usd_net": 0.0,
            "drawdown_usd": 0.0,
            "drawdown_pct": 0.0,
            "open_positions": 0,
        }
    ]
    realized = 0.0
    peak = principal
    max_dd = 0.0
    max_dd_pct = 0.0
    for count, trade in enumerate(done, start=1):
        realized += float(trade["net_pnl_usd"])
        equity = principal + realized
        peak = max(peak, equity)
        drawdown = peak - equity
        drawdown_pct = (drawdown / peak * 100.0) if peak > 0 else 0.0
        max_dd = max(max_dd, drawdown)
        max_dd_pct = max(max_dd_pct, drawdown_pct)
        exit_ts = _parse(trade.get("exit_time"))
        opened = bisect_right(opened_at, exit_ts) if exit_ts is not None else count
        curve.append(
            {
                "time": trade.get("exit_time"),
                "equity_usd": equity,
                "realized_pnl_usd_net": realized,
                "drawdown_usd": drawdown,
                "drawdown_pct": drawdown_pct,
                "open_positions": max(0, opened - count),
            }
        )

    def tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        pnls = [float(t["net_pnl_usd"]) for t in rows if t.get("net_pnl_usd") is not None]
        return {
            "trades": len(pnls),
            "wins": sum(1 for p in pnls if p > 0),
            "losses": sum(1 for p in pnls if p < 0),
            "pnl_usd_net": sum(pnls),
        }

    by_trend: dict[str, dict[str, Any]] = {}
    for trend in router.TRENDS:
        symbol_bars = sum(int((res.get("bars_by_trend") or {}).get(trend) or 0) for res in symbol_results)
        if trend == "NEUTRAL":
            by_trend[trend] = {
                "symbol_bars": symbol_bars,
                "short_history_symbol_bars": sum(
                    int(res.get("short_history_bars") or 0) for res in symbol_results
                ),
            }
            continue
        by_trend[trend] = {
            "symbol_bars": symbol_bars,
            "signals": sum(1 for s in signals if s.get("trend") == trend),
            **tally([t for t in trades if t.get("trend_at_entry") == trend]),
        }

    by_strategy: dict[str, dict[str, Any]] = {}
    for signal in signals:
        key = f"{signal.get('strategy_id')}|{signal.get('direction')}"
        cell = by_strategy.setdefault(
            key,
            {
                "strategy_id": signal.get("strategy_id"),
                "direction": signal.get("direction"),
                "timeframe": signal.get("timeframe"),
                "signals": 0,
                "taken": 0,
            },
        )
        cell["signals"] += 1
        cell["taken"] += 1 if signal.get("taken") else 0
    for key, cell in by_strategy.items():
        cell.update(
            tally([t for t in trades if f"{t.get('strategy_id')}|{t.get('direction')}" == key])
        )

    skipped: dict[str, int] = {}
    for signal in signals:
        if not signal.get("taken"):
            reason = str(signal.get("skip_reason") or "UNKNOWN")
            skipped[reason] = skipped.get(reason, 0) + 1

    total = tally(trades)
    cancelled = any(res.get("status") == "CANCELLED" for res in symbol_results)
    summary = {
        **total,
        "open_trades": len(trades) - len(done),
        "win_rate": (total["wins"] / total["trades"]) if total["trades"] else None,
        "pnl_usd_gross": sum(float(t.get("gross_pnl_usd") or 0.0) for t in done),
        "fees_usd": sum(float(t.get("fee_total_usd") or 0.0) for t in done),
        "final_equity_usd": principal + realized,
        "max_drawdown_usd": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "signals": len(signals),
        "signals_taken": sum(1 for s in signals if s.get("taken")),
        "signals_on_rewalk_first_bar": sum(1 for s in signals if s.get("rewalk_first_bar")),
        "trades_on_rewalk_first_bar": sum(1 for t in trades if t.get("rewalk_first_bar")),
        "signals_outside_trend": sum(
            int(res.get("signals_outside_trend") or 0) for res in symbol_results
        ),
        "skipped_signals": skipped,
        "by_trend": by_trend,
        "by_strategy": by_strategy,
        "regime_timeline": clock.timeline(min(starts) + BAR, max(ends) + BAR) if starts and ends else [],
    }
    return {
        "status": "CANCELLED" if cancelled else "OK",
        "principal_usd": principal,
        "taker_fee": taker_fee,
        "maker_fee": maker_fee,
        "leverage": leverage,
        "equity_basis": EQUITY_BASIS,
        "limitations": list(LIMITATIONS),
        "summary": summary,
        "equity_curve": curve,
        "trades": trades,
        "signals": signals,
        "symbols": {
            str(res.get("symbol")): {
                key: res.get(key)
                for key in (
                    "status",
                    "bars",
                    "period_start",
                    "period_end",
                    "walks",
                    "gap_walks",
                    "exit_rewalks",
                    "signals_outside_trend",
                    "bars_by_trend",
                    "short_history_bars",
                    "short_history_until",
                    "errors",
                )
            }
            | {
                "signals": len(res.get("signals") or []),
                "trades": len(res.get("trades") or []),
            }
            for res in symbol_results
        },
    }


def run_regime_book(
    books: Mapping[str, Mapping[str, Any]],
    btc_daily: Sequence[Mapping[str, Any]],
    *,
    signal_config: Any = None,
    research_config: Any = None,
    regime_signal_config: Any = None,
    principal: float = PRINCIPAL_USD,
    taker_fee: float = TAKER_FEE,
    maker_fee: float = MAKER_FEE,
    leverage: float = LEVERAGE,
    market_caps: Mapping[str, float | None] | None = None,
    backtest_fn: Callable[..., dict[str, Any]] | None = None,
    postprocess_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the regime book over loaded candles. See the module docstring.

    ``books`` maps symbol -> the dict ``run_cap_mtf_risk._load`` returns.
    ``btc_daily`` is BTCUSDT 1d, oldest first; an unclosed last bar is ignored.
    ``signal_config`` and ``research_config`` go to the strategy walks as the
    retest passes them. ``regime_signal_config`` is for the daily trend and
    defaults to ``SignalConfig()``, which is what the paper watcher uses.
    """
    if regime_signal_config is None:
        from app.signals.config import SignalConfig

        regime_signal_config = SignalConfig()
    clock = RegimeClock(btc_daily, regime_signal_config)
    results = []
    for symbol, book in books.items():
        result = run_symbol(
            symbol,
            book,
            clock,
            signal_config=signal_config,
            research_config=research_config,
            market_cap=(market_caps or {}).get(symbol),
            backtest_fn=backtest_fn,
            should_cancel=should_cancel,
            progress=progress,
        )
        results.append(result)
        if result["status"] == "CANCELLED":
            break
    return build_result(
        results,
        clock,
        principal=principal,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
        leverage=leverage,
        postprocess_fn=postprocess_fn,
    )
