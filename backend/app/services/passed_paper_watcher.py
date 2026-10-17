"""Paper watcher for every strategy side that passed the cap book.

Evaluates the same combination function as the backtest on closed 1h bars.
Does not place exchange orders.

The BTC daily trend picks which sides run: BULLISH the LONG rows, BEARISH the
SHORT rows, NEUTRAL none. One open paper position per symbol.

The regime book is open (``REGIME_BOOK_OPEN``): a signal from a side that
passed the cap-book backtest opens a paper position. Setting the flag false
records that signal as not opened with reason ``regime_book_closed``.

The trend for a 1h bar is read at that bar's close with the backtest's
``RegimeClock``, from BTC daily bars read once per tick. A failed daily read
leaves the bar unprocessed so the next tick retries it.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.core.logging import get_logger
from app.research.bos_combinations import get_combination
from app.research.btc_daily_regime import (
    MIN_DAILY_BARS,
    REGIME_SYMBOL,
    REGIME_TIMEFRAME,
    daily_trend,
)
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.passed_cap_book import (
    LARGE,
    MID,
    PASSED_SIDES,
    cap_symbols,
    passed_risk_percent,
)
from app.research.passed_regime_router import (
    allowed_passed_sides,
    normalize_trend,
    route_passed_book,
)
from app.research.passed_regime_backtest import RegimeClock
from app.research.v1_production import normalize_symbol
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig

logger = get_logger("passed_paper_watcher")

# Closed bars required before a bar is evaluated. Mirrors the REST catch-up
# targets in app/engines/orchestrator.py ``_paper_history_catchup``.
MIN_CLOSED_1H_BARS = 250
MIN_CLOSED_4H_BARS = 80
MIN_CLOSED_15M_BARS = 100

# Signals kept in memory for the admin status.
MAX_LATEST_SIGNALS = 50

# Cap-book sides that passed the backtest may open paper positions.
# False records each signal as regime_book_closed and opens nothing.
REGIME_BOOK_OPEN = True
REGIME_BOOK_CLOSED_REASON = "regime_book_closed"
REGIME_BOOK_CLOSED_NOTE = (
    "New passed-regime entries are off because no side passed its regime window."
)

_watcher: PassedPaperWatcher | None = None
_watcher_lock = threading.Lock()


HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _iso(ts: datetime | None) -> str:
    if ts is None:
        return ""
    return _utc(ts).isoformat()


@dataclass(frozen=True)
class DailySnapshot:
    """BTC closed daily bars read once per tick. ``clock`` is None when the read failed."""

    clock: RegimeClock | None
    error: str = ""
    read_at: str = ""


class PassedPaperWatcher:
    def __init__(self, *, enabled: bool = True, paper_engine: Any | None = None) -> None:
        self.enabled = bool(enabled)
        self.symbols = cap_symbols()
        self.sides = PASSED_SIDES
        self._paper = paper_engine
        self._lock = threading.RLock()
        self._seeded: set[str] = set()
        self._processed: set[str] = set()
        self._inflight: set[str] = set()
        self._combos: dict[str, Any] = {}
        self.signal_config = SignalConfig()
        self.research_config = ResearchConfig()
        self.last_skip: str | None = None
        self.opened_count = 0
        self.last_evaluated_bar: dict[str, Any] | None = None
        self.last_regime: dict[str, Any] | None = None
        self._signals: deque[dict[str, Any]] = deque(maxlen=MAX_LATEST_SIGNALS)

    def _paper_engine(self) -> Any:
        if self._paper is not None:
            return self._paper
        from app.services.paper_trade import get_paper_trade_engine

        return get_paper_trade_engine()

    def _combo(self, combination_id: str) -> Any | None:
        if combination_id not in self._combos:
            self._combos[combination_id] = get_combination(combination_id)
        return self._combos[combination_id]

    def _market_regime(self) -> dict[str, Any]:
        """BTC daily trend with the closed daily bar it was read from."""
        try:
            from app.services.ohlcv_store import ohlcv_store

            candles = list(
                ohlcv_store.get_candles_for_engine("BTCUSDT", "1d", include_open=False) or []
            )
            return daily_trend(candles, self.signal_config)
        except Exception:  # noqa: BLE001
            return {"trend": "NEUTRAL", "as_of_bar": "", "bars": 0}

    def _market_direction(self) -> str:
        return str(self._market_regime()["trend"])

    def read_daily_snapshot(self) -> DailySnapshot:
        """Read BTC closed daily bars once. Pass the result to every symbol in the tick."""
        read_at = datetime.now(timezone.utc).isoformat()
        try:
            from app.services.ohlcv_store import ohlcv_store

            candles = list(
                ohlcv_store.get_candles_for_engine(
                    REGIME_SYMBOL, REGIME_TIMEFRAME, include_open=False
                )
                or []
            )
            return DailySnapshot(RegimeClock(candles, self.signal_config), read_at=read_at)
        except Exception as exc:  # noqa: BLE001
            logger.warning("passed_paper_daily_read_failed", error=str(exc))
            return DailySnapshot(None, error="daily_read_failed", read_at=read_at)

    def _bar_regime(self, daily: DailySnapshot, bar_open: datetime) -> dict[str, Any]:
        """Trend for the 1h bar opened at ``bar_open``, read at its close like the backtest.

        ``ok`` is False when the read failed, gave no daily bar, or the daily bar
        that closed by then is not in the snapshot yet. The caller retries the bar.
        """
        close = _utc(bar_open) + HOUR
        out: dict[str, Any] = {
            "ok": False,
            "error": "",
            "trend": "NEUTRAL",
            "as_of_daily_bar": "",
            "daily_bars": 0,
            "short_history": False,
            "bar_close": close.isoformat(),
            "read_at": daily.read_at,
        }
        if daily.clock is None:
            out["error"] = daily.error or "daily_read_failed"
            return out
        try:
            regime = daily.clock.at(close)
        except Exception as exc:  # noqa: BLE001
            logger.warning("passed_paper_daily_read_failed", error=str(exc))
            out["error"] = "daily_read_failed"
            return out
        as_of = str(regime.get("as_of_bar") or "")
        bars = int(regime.get("bars") or 0)
        out.update(
            trend=normalize_trend(regime.get("trend")),
            as_of_daily_bar=as_of,
            daily_bars=bars,
            short_history=bars < MIN_DAILY_BARS,
        )
        if not as_of:
            out["error"] = "no_daily_bar"
            return out
        # The last daily bar closed at ``close`` opened the day before close's day.
        due = close.replace(hour=0, minute=0, second=0, microsecond=0) - DAY
        try:
            got = _utc(datetime.fromisoformat(as_of.replace("Z", "+00:00")))
        except ValueError:
            out["error"] = "no_daily_bar"
            return out
        if got < due:
            out["error"] = "daily_bar_pending"
            return out
        out["ok"] = True
        return out

    def _symbol_has_open_position(self, paper: Any, sym: str) -> bool:
        """Any open paper position on the symbol, in any stream."""
        return sym.upper() in {str(s or "").upper() for s in paper.open_symbols()}

    def _engine_refusal(self, paper: Any, sym: str) -> str:
        """The engine's last skip reason for this symbol, when it gave one."""
        try:
            detail = str(paper.status().get("last_skip_reason") or "")
        except Exception:  # noqa: BLE001
            detail = ""
        prefix = f"{sym}:"
        if detail.startswith(prefix) and len(detail) > len(prefix):
            return f"engine_refused:{detail[len(prefix):]}"
        return "engine_refused"

    def _record_signal(self, **fields: Any) -> None:
        row = dict(fields)
        row["recorded_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._signals.appendleft(row)

    def regime_status(self) -> dict[str, Any]:
        """Read-only view for the admin screen. Opens and evaluates nothing.

        Shows the regime the watcher last used for a bar. Before any bar, reads
        it for the last closed 1h bar.
        """
        with self._lock:
            regime = dict(self.last_regime) if self.last_regime else None
        if regime is None:
            hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            regime = self._bar_regime(self.read_daily_snapshot(), hour - HOUR)
            regime["bar"] = _iso(hour - HOUR)
        ok = bool(regime.get("ok"))
        book = route_passed_book(regime.get("trend") if ok else None)
        with self._lock:
            return {
                "enabled": self.enabled,
                "trend": book["trend"],
                "daily_read_ok": ok,
                "daily_read_error": str(regime.get("error") or ""),
                "regime_bar": str(regime.get("bar") or ""),
                "regime_read_at": str(regime.get("read_at") or ""),
                "as_of_daily_bar": str(regime.get("as_of_daily_bar") or ""),
                "daily_bars": int(regime.get("daily_bars") or 0),
                "min_daily_bars": MIN_DAILY_BARS,
                "short_history": bool(regime.get("short_history")),
                "allowed": [dict(row) for row in book["allowed"]],
                "skipped": [dict(row) for row in book["skipped"]],
                "allowed_strategy_ids": [row["strategy_id"] for row in book["allowed"]],
                "skipped_strategy_ids": [row["strategy_id"] for row in book["skipped"]],
                "latest_signals": [dict(row) for row in self._signals],
                "last_evaluated_bar": (
                    dict(self.last_evaluated_bar) if self.last_evaluated_bar else None
                ),
                "last_skip": self.last_skip,
                "opened_count": self.opened_count,
                "book_open": REGIME_BOOK_OPEN,
                "book_note": "" if REGIME_BOOK_OPEN else REGIME_BOOK_CLOSED_NOTE,
                "symbols": list(cap_symbols()),
                "symbol_risk": {
                    "large_symbols": list(LARGE),
                    "mid_symbols": list(MID),
                    "large_risk_percent": 0.02,
                    "mid_risk_percent": 0.01,
                },
            }

    def on_closed_1h(
        self,
        symbol: str,
        *,
        candles_1h: Sequence[Mapping[str, Any]] | None = None,
        candles_4h: Sequence[Mapping[str, Any]] | None = None,
        candles_15m: Sequence[Mapping[str, Any]] | None = None,
        market_direction: str | None = None,
        daily: DailySnapshot | None = None,
        force: bool = False,
    ) -> list[Any]:
        if not self.enabled:
            self.last_skip = "watcher_disabled"
            return []
        sym = normalize_symbol(symbol)
        if sym not in self.symbols:
            self.last_skip = f"{sym}:not_in_universe"
            return []

        from app.services.ohlcv_store import ohlcv_store

        series_1h = list(
            candles_1h
            if candles_1h is not None
            else ohlcv_store.get_candles_for_engine(sym, "1h", include_open=False)
        )
        series_4h = list(
            candles_4h
            if candles_4h is not None
            else ohlcv_store.get_candles_for_engine(sym, "4h", include_open=False)
        )
        series_15m = list(
            candles_15m
            if candles_15m is not None
            else ohlcv_store.get_candles_for_engine(sym, "15m", include_open=False)
        )
        # Wait for the full catch-up window: a shorter 4h series cannot settle
        # the HTF trend. Do not watermark while any of them is short.
        if not force and (
            len(series_1h) < MIN_CLOSED_1H_BARS
            or len(series_4h) < MIN_CLOSED_4H_BARS
            or len(series_15m) < MIN_CLOSED_15M_BARS
        ):
            self.last_skip = f"{sym}:ohlcv_catchup"
            return []
        if len(series_1h) < MIN_CLOSED_1H_BARS:
            self.last_skip = f"{sym}:missing_1h"
            return []
        tip = series_1h[-1]
        tip_ts = candle_time(tip)
        tip_iso = _iso(tip_ts) if tip_ts is not None else str(tip.get("time") or "")
        if not tip_iso:
            self.last_skip = f"{sym}:missing_bar_time"
            return []
        if not force:
            from app.services.v21a_paper_validation import is_candle_fully_closed

            if tip.get("is_closed") is False or tip.get("closed") is False:
                self.last_skip = f"{sym}:forming_candle"
                return []
            if (
                tip.get("is_closed") is not True
                and tip.get("closed") is not True
                and not is_candle_fully_closed(tip, "1h")
            ):
                self.last_skip = f"{sym}:forming_candle"
                return []
        with self._lock:
            if not force and sym not in self._seeded:
                from app.services.v1_paper_watcher import closed_tip_is_current

                if closed_tip_is_current(tip_ts, "1h"):
                    # Same closed hour the signal is for. Evaluate it.
                    self._seeded.add(sym)
                else:
                    # Old tip on first sight: do not replay a backlog.
                    self._seeded.add(sym)
                    self._processed.add(f"{sym}|{tip_iso}")
                    self.last_skip = f"{sym}:seeded_no_backlog"
                    return []
            bar_key = f"{sym}|{tip_iso}"
            if not force and (
                bar_key in self._processed or bar_key in self._inflight
            ):
                self.last_skip = f"{sym}:already_processed"
                return []
            self._inflight.add(bar_key)

        # Same gate as the v1 watcher. Do not mark the bar processed: it is
        # retried once the store heals, up to the open-lag bound below.
        from app.services.v1_paper_watcher import series_integrity_fault

        fault = series_integrity_fault(series_1h, series_4h)
        if fault:
            self.last_skip = f"{sym}:data_integrity:{fault}"
            logger.warning(
                "passed_paper_data_integrity",
                symbol=sym,
                bar=tip_iso,
                fault=fault,
            )
            with self._lock:
                self._inflight.discard(bar_key)
            return []

        # A closed bar is not opened long after it closed, and is not retried.
        late = False
        if not force:
            from app.services.v1_paper_watcher import (
                MAX_OPEN_LAG_SECONDS,
                tip_open_lag_seconds,
            )

            lag = tip_open_lag_seconds(tip_ts, "1h")
            late = lag is not None and lag > MAX_OPEN_LAG_SECONDS
        late_refused = False

        # The daily trend at this bar's close picks the sides that may open.
        if market_direction:
            market = market_direction
            as_of_daily = ""
        else:
            snapshot = daily if daily is not None else self.read_daily_snapshot()
            if tip_ts is None:
                regime = {"ok": False, "error": "no_bar_close", "read_at": snapshot.read_at}
            else:
                regime = self._bar_regime(snapshot, tip_ts)
            with self._lock:
                self.last_regime = dict(regime, symbol=sym, bar=tip_iso)
            if not regime["ok"]:
                # Same as a series fault: not processed, retried next tick.
                self.last_skip = f"{sym}:{regime['error']}"
                logger.warning(
                    "passed_paper_daily_regime_unavailable",
                    symbol=sym,
                    bar=tip_iso,
                    reason=regime["error"],
                )
                with self._lock:
                    self._inflight.discard(bar_key)
                return []
            market = str(regime["trend"])
            as_of_daily = str(regime["as_of_daily_bar"])
        trend = normalize_trend(market)
        sides = allowed_passed_sides(trend)
        bar_info = {
            "symbol": sym,
            "bar": tip_iso,
            "trend": trend,
            "as_of_daily_bar": as_of_daily,
            "sides_evaluated": 0,
        }
        if trend == "NEUTRAL":
            # No new signal. Open positions keep their own stops and targets.
            self.last_skip = f"{sym}:neutral_trend"
            with self._lock:
                self.last_evaluated_bar = bar_info
                self._inflight.discard(bar_key)
                self._processed.add(bar_key)
            return []

        paper = self._paper_engine()
        opened: list[Any] = []
        failed = False
        blocked = False
        book_closed = False
        evaluated = 0
        idx = len(series_1h) - 1
        for combination_id, timeframe, direction in sides:
            if timeframe != "1h":
                continue
            combo = self._combo(combination_id)
            if combo is None:
                continue
            evaluated += 1
            try:
                result = evaluate_combination_at_bar(
                    symbol=sym,
                    timeframe="1h",
                    candles=series_1h,
                    as_of_index=idx,
                    combination=combo,
                    signal_config=self.signal_config,
                    research_config=self.research_config,
                    candles_1h=series_1h,
                    candles_4h=series_4h or None,
                    candles_15m=series_15m or None,
                    compute_sd=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "passed_paper_eval_failed",
                    symbol=sym,
                    combination_id=combination_id,
                    direction=direction,
                    error=str(exc),
                )
                failed = True
                continue
            status = str((result or {}).get("status") or "")
            wanted = (
                "LONG_ENTRY_CANDIDATE" if direction == "LONG" else "SHORT_ENTRY_CANDIDATE"
            )
            if status != wanted:
                continue
            signal = {
                "strategy_id": combination_id,
                "direction": direction,
                "timeframe": timeframe,
                "symbol": sym,
                "setup_bar_time": tip_iso,
                "trend": trend,
                "as_of_daily_bar": as_of_daily,
            }
            if late:
                late_refused = True
                self._record_signal(**signal, outcome="not_opened", reason="late_tip")
                continue
            # One open paper position per symbol, whichever stream holds it.
            try:
                has_open = self._symbol_has_open_position(paper, sym)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "passed_paper_open_check_failed",
                    symbol=sym,
                    combination_id=combination_id,
                    error=str(exc),
                )
                failed = True
                self._record_signal(
                    **signal, outcome="not_opened", reason="open_position_check_failed"
                )
                continue
            if has_open:
                blocked = True
                self._record_signal(
                    **signal, outcome="not_opened", reason="symbol_has_open_position"
                )
                continue
            if not REGIME_BOOK_OPEN:
                book_closed = True
                self._record_signal(
                    **signal, outcome="not_opened", reason=REGIME_BOOK_CLOSED_REASON
                )
                continue
            eval_result = dict(result or {})
            if not force:
                from app.research.ohlcv_continuity import bars_adjacent, step_seconds
                from app.services.ohlcv_store import ohlcv_store
                from app.services.paper_sizing import DEFAULT_PAPER_SLIPPAGE_RATE

                forming = ohlcv_store.get_open(sym, "1h")
                if (
                    forming is not None
                    and tip_ts is not None
                    and bars_adjacent(
                        tip, {"time": forming.open_time}, step_seconds("1h")
                    )
                ):
                    raw = float(forming.open)
                    slip = DEFAULT_PAPER_SLIPPAGE_RATE
                    eval_result["entry_price"] = (
                        raw * (1.0 - slip) if direction == "SHORT" else raw * (1.0 + slip)
                    )
                    eval_result["entry_type"] = "NEXT_OPEN"
            pos = paper.open_passed_position(
                symbol=sym,
                combination_id=combination_id,
                timeframe="1h",
                direction=direction,
                eval_result=eval_result,
                risk_percent=passed_risk_percent(sym, direction, market),
                setup_bar_time_utc=tip_iso,
                market_direction=market,
            )
            if pos is not None:
                opened.append(pos)
                self.opened_count += 1
                self._record_signal(
                    **signal,
                    outcome="opened",
                    reason="",
                    position_id=str(getattr(pos, "id", "") or ""),
                )
            else:
                self._record_signal(
                    **signal,
                    outcome="not_opened",
                    reason=self._engine_refusal(paper, sym),
                )
        if late_refused:
            self.last_skip = f"{sym}:late_tip"
        elif book_closed:
            self.last_skip = f"{sym}:{REGIME_BOOK_CLOSED_REASON}"
        elif blocked and not opened:
            self.last_skip = f"{sym}:symbol_has_open_position"
        elif not opened:
            self.last_skip = f"{sym}:no_passed_entry"
        bar_info["sides_evaluated"] = evaluated
        with self._lock:
            self.last_evaluated_bar = bar_info
            self._inflight.discard(bar_key)
            if not failed or late:
                self._processed.add(bar_key)
        return opened


def get_passed_paper_watcher(**kwargs: Any) -> PassedPaperWatcher:
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = PassedPaperWatcher(**kwargs)
        elif kwargs:
            if "enabled" in kwargs:
                _watcher.enabled = bool(kwargs["enabled"])
            if "paper_engine" in kwargs:
                _watcher._paper = kwargs["paper_engine"]
        return _watcher


def reset_passed_paper_watcher_for_tests() -> None:
    global _watcher
    with _watcher_lock:
        _watcher = None
