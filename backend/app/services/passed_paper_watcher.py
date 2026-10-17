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

Fills follow the backtest regime book's ``CAUSAL_NEXT_OPEN`` model
(``app.research.causal_execution``): a signal on a closed bar fills at the
next bar's open with the causal entry slippage, and the paper engine walks
the exit on closed bars with the same rules. A signal whose next bar is
missing is not opened (``FILL_BAR_GAP``); one whose next bar has not arrived
yet is retried.

A coin with an entry in the per-coin allowlist
(``app.research.passed_symbol_allowlist``) opens only the sides that passed
that coin's test. A signal from any other passed side on that coin is
recorded as not opened with reason ``symbol_side_not_allowed``, ahead of the
open-position check, as the backtest regime book does. Coins with no entry
run every passed side.

Every closed 1h bar is evaluated once, oldest first (``catch_up``). A
per-symbol watermark (the newest bar with every bar up to it processed) is
kept in a local state file, so a restart or a stalled loop resumes from it
instead of jumping to the latest bar. Only the closed tip can still be inside
the late-open bound; a signal on any bar evaluated after that bound is
journaled as ``missed`` with how late it was and counted in
``missed_signals``. Nothing late is opened.
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.config import ROOT
from app.core.logging import get_logger
from app.research.bos_combinations import get_combination
from app.research.btc_daily_regime import (
    MIN_DAILY_BARS,
    REGIME_SYMBOL,
    REGIME_TIMEFRAME,
    daily_trend,
)
from app.research.causal_execution import SKIP_FILL_BAR_GAP, causal_entry_fill
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.ohlcv_continuity import bars_adjacent, step_seconds
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
from app.research.passed_symbol_allowlist import (
    PAPER_SKIP_REASON as SYMBOL_SIDE_NOT_ALLOWED,
    SymbolAllowlist,
    get_allowlist,
)
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

# Next-open fill states (see ``PassedPaperWatcher._next_open_fill``).
FILL_OK = "ok"
FILL_PENDING = "fill_bar_pending"
FILL_GAP = SKIP_FILL_BAR_GAP

# Closed 1h bars back from the closed tip that a restart or a stalled loop
# still evaluates. Unprocessed bars older than that are journaled as not
# evaluated; a bar inside it that cannot be evaluated yet is retried.
MAX_CATCHUP_BARS = 24

# Journal outcomes for bars the watcher reached after the late-open bound.
MISSED_OUTCOME = "missed"
NOT_EVALUATED_OUTCOME = "not_evaluated"
MAX_RECENT_MISSED = 20

# Watermarks, the missed counters and the latest missed rows survive a restart
# here. A local file, not the paper ledger. Without a path they stay in memory.
DEFAULT_STATE_PATH = ROOT / "data" / "paper" / "passed_paper_watermark.json"
STATE_VERSION = 1

# One bar closes every hour: no evaluation for an hour plus the late-open
# bound means at least one bar was not evaluated in time.
HEARTBEAT_STALE_SECONDS = 3600.0 + 900.0

_watcher: PassedPaperWatcher | None = None
_watcher_lock = threading.Lock()


HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
H4 = timedelta(hours=4)
M15 = timedelta(minutes=15)


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _iso(ts: datetime | None) -> str:
    if ts is None:
        return ""
    return _utc(ts).isoformat()


def _parse_iso(raw: Any) -> datetime | None:
    try:
        return _utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _wall_now() -> datetime:
    """Same clock as the late-open bound (``v1_paper_watcher._utc_now``)."""
    from app.services import v1_paper_watcher

    return _utc(v1_paper_watcher._utc_now())


def _closed_by(
    candles: Sequence[Mapping[str, Any]], close: datetime, step: timedelta
) -> list[Mapping[str, Any]]:
    """Bars of ``candles`` already closed at ``close``."""
    out = []
    for candle in candles:
        ts = candle_time(candle)
        if ts is not None and _utc(ts) + step <= close:
            out.append(candle)
    return out


@dataclass(frozen=True)
class DailySnapshot:
    """BTC closed daily bars read once per tick. ``clock`` is None when the read failed."""

    clock: RegimeClock | None
    error: str = ""
    read_at: str = ""


class PassedPaperWatcher:
    def __init__(
        self,
        *,
        enabled: bool = True,
        paper_engine: Any | None = None,
        symbol_allowlist: SymbolAllowlist | None = None,
        state_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.symbols = cap_symbols()
        self.sides = PASSED_SIDES
        # Loaded and validated once; a bad file fails here, not mid-tick.
        self.symbol_allowlist = (
            symbol_allowlist if symbol_allowlist is not None else get_allowlist()
        )
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
        # Newest bar per symbol with every bar up to it processed (ISO open time).
        self._watermarks: dict[str, str] = {}
        self._pending_bars: dict[str, int] = {}
        self.missed_signals = 0
        self.unevaluated_bars = 0
        self._recent_missed: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_MISSED)
        self.last_eval: dict[str, Any] | None = None
        self.last_catchup_at = ""
        self._state_dirty = False
        self.state_path: Path | None = None
        if state_path is not None:
            self.attach_state(state_path)

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

    def _record_signal(self, **fields: Any) -> dict[str, Any]:
        row = dict(fields)
        row["recorded_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._signals.appendleft(row)
        return row

    def _record_missed(self, signal: Mapping[str, Any], lag: float | None) -> None:
        """A permitted signal on a bar evaluated after the late-open bound. Nothing opens."""
        lag_s = float(lag or 0.0)
        row = self._record_signal(
            **signal,
            outcome=MISSED_OUTCOME,
            reason="late_tip",
            note=f"missed: evaluated late ({int(lag_s // 60)}m)",
            lag_seconds=round(lag_s, 1),
        )
        logger.warning(
            "passed_paper_signal_missed",
            symbol=row.get("symbol"),
            strategy_id=row.get("strategy_id"),
            direction=row.get("direction"),
            bar=row.get("setup_bar_time"),
            lag_seconds=row["lag_seconds"],
        )
        with self._lock:
            self.missed_signals += 1
            self._recent_missed.appendleft(dict(row))
            self._state_dirty = True

    def _record_not_evaluated(
        self, sym: str, bar: str, reason: str, *, bars: int = 1, last_bar: str = ""
    ) -> None:
        """Due bars never evaluated: a signal on them is unknown, not absent."""
        first = _parse_iso(bar)
        lag = (_wall_now() - (first + HOUR)).total_seconds() if first is not None else 0.0
        span = f"{bars} bars" if bars != 1 else "bar"
        row = self._record_signal(
            symbol=sym,
            timeframe="1h",
            setup_bar_time=bar,
            last_bar_time=last_bar or bar,
            bars=bars,
            outcome=NOT_EVALUATED_OUTCOME,
            reason=reason,
            note=f"missed: {span} not evaluated ({reason}, {int(max(lag, 0.0) // 60)}m late)",
        )
        logger.warning(
            "passed_paper_bar_not_evaluated",
            symbol=sym,
            bar=bar,
            last_bar=row["last_bar_time"],
            bars=bars,
            reason=reason,
        )
        with self._lock:
            self.unevaluated_bars += bars
            self._recent_missed.appendleft(dict(row))
            self._state_dirty = True

    def _note_evaluated(self, sym: str, bar_iso: str, bar_ts: datetime | None) -> None:
        """Heartbeat: the last bar the watcher finished, when, and how long after its close."""
        now = _wall_now()
        lag = (now - (_utc(bar_ts) + HOUR)).total_seconds() if bar_ts is not None else None
        with self._lock:
            self.last_eval = {
                "at": now.isoformat(),
                "symbol": sym,
                "bar": bar_iso,
                "lag_seconds": round(lag, 1) if lag is not None else None,
            }

    # --- watermark state ---------------------------------------------------------

    def attach_state(self, path: str | os.PathLike[str]) -> None:
        """Keep watermarks in ``path`` and resume from what it holds."""
        with self._lock:
            self.state_path = Path(path)
        self._load_state()

    def _load_state(self) -> None:
        path = self.state_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Unreadable: no watermarks, so the tip only, as on a first run.
            logger.warning("passed_paper_state_unreadable", path=str(path), error=str(exc))
            return
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            logger.warning("passed_paper_state_unreadable", path=str(path), error="version")
            return
        marks: dict[str, str] = {}
        for sym, iso in dict(raw.get("watermarks") or {}).items():
            ts = _parse_iso(iso)
            if ts is not None and normalize_symbol(sym) in self.symbols:
                marks[normalize_symbol(sym)] = _iso(ts)
        with self._lock:
            self._watermarks.update(marks)
            self.missed_signals = int(raw.get("missed_signals") or 0)
            self.unevaluated_bars = int(raw.get("unevaluated_bars") or 0)
            self._recent_missed.clear()
            for row in list(raw.get("recent_missed") or [])[:MAX_RECENT_MISSED]:
                if isinstance(row, dict):
                    self._recent_missed.append(dict(row))
        logger.info("passed_paper_state_loaded", path=str(path), symbols=len(marks))

    def save_state(self) -> None:
        """Write the state file when something changed. A failed write is retried next call."""
        with self._lock:
            path = self.state_path
            if path is None or not self._state_dirty:
                return
            payload = {
                "version": STATE_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "watermarks": dict(sorted(self._watermarks.items())),
                "missed_signals": self.missed_signals,
                "unevaluated_bars": self.unevaluated_bars,
                "recent_missed": [dict(row) for row in self._recent_missed],
            }
            self._state_dirty = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("passed_paper_state_write_failed", path=str(path), error=str(exc))
            with self._lock:
                self._state_dirty = True

    def _set_watermark(self, sym: str, ts: datetime) -> None:
        iso = _iso(ts)
        with self._lock:
            if self._watermarks.get(sym) != iso:
                self._watermarks[sym] = iso
                self._state_dirty = True

    def heartbeat(self) -> dict[str, Any]:
        """Last finished bar and how far behind the watcher is. Reads only."""
        from app.services.v1_paper_watcher import MAX_OPEN_LAG_SECONDS

        now = _wall_now()
        with self._lock:
            last = dict(self.last_eval) if self.last_eval else {}
            pending = {sym: n for sym, n in sorted(self._pending_bars.items()) if n}
            out: dict[str, Any] = {
                "last_eval_at": str(last.get("at") or ""),
                "last_eval_symbol": str(last.get("symbol") or ""),
                "last_eval_bar": str(last.get("bar") or ""),
                "last_eval_lag_seconds": last.get("lag_seconds"),
                "last_catchup_at": self.last_catchup_at,
                "pending_bars": sum(pending.values()),
                "pending_by_symbol": pending,
                "watermarks": dict(sorted(self._watermarks.items())),
                "state_file": str(self.state_path or ""),
                "max_catchup_bars": MAX_CATCHUP_BARS,
                "max_open_lag_seconds": MAX_OPEN_LAG_SECONDS,
            }
        at = _parse_iso(out["last_eval_at"]) if out["last_eval_at"] else None
        since = (now - at).total_seconds() if at is not None else None
        out["seconds_since_last_eval"] = round(since, 1) if since is not None else None
        out["stale"] = since is not None and since > HEARTBEAT_STALE_SECONDS
        return out

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
                # Signals on bars evaluated after the late-open bound (never opened)
                # and due bars never evaluated. Kept across restarts with a state file.
                "missed_signals": self.missed_signals,
                "unevaluated_bars": self.unevaluated_bars,
                "recent_missed": [dict(row) for row in self._recent_missed],
                "heartbeat": self.heartbeat(),
                "book_open": REGIME_BOOK_OPEN,
                "book_note": "" if REGIME_BOOK_OPEN else REGIME_BOOK_CLOSED_NOTE,
                "symbols": list(cap_symbols()),
                # Per coin: sides that passed / failed that coin's test.
                # Coins not listed run every passed side.
                "symbol_allowlist": self.symbol_allowlist.describe(),
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
        next_bar: Mapping[str, Any] | None = None,
    ) -> list[Any]:
        """Evaluate the closed tip of ``symbol`` and open the passed sides that signal.

        A signal fills at the open of the bar after the tip (``next_bar``, or
        the store's forming bar) with the causal model's entry slippage. When
        that bar is not there yet the bar is retried on the next tick; when the
        bar after the tip is missing the signal is not opened (``FILL_BAR_GAP``).
        """
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
        lag: float | None = None
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
            "signals_not_allowed": 0,
        }
        if trend == "NEUTRAL":
            # No new signal. Open positions keep their own stops and targets.
            self.last_skip = f"{sym}:neutral_trend"
            with self._lock:
                self.last_evaluated_bar = bar_info
                self._inflight.discard(bar_key)
                self._processed.add(bar_key)
            self._note_evaluated(sym, tip_iso, tip_ts)
            return []

        paper = self._paper_engine()
        opened: list[Any] = []
        failed = False
        blocked = False
        book_closed = False
        pending = False
        not_allowed = False
        evaluated = 0
        idx = len(series_1h) - 1
        # None: no entry for this coin, every passed side may open.
        permitted = self.symbol_allowlist.allowed_sides_for(sym)
        for combination_id, timeframe, direction in sides:
            if timeframe != "1h":
                continue
            combo = self._combo(combination_id)
            if combo is None:
                continue
            evaluated += 1
            side_permitted = permitted is None or (combination_id, timeframe, direction) in permitted
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
                # A side that cannot open on this coin does not hold the bar for a retry.
                failed = failed or side_permitted
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
            if not side_permitted:
                # Failed this coin's test: never opens here, whatever else holds.
                not_allowed = True
                bar_info["signals_not_allowed"] += 1
                self._record_signal(
                    **signal, outcome="not_opened", reason=SYMBOL_SIDE_NOT_ALLOWED
                )
                continue
            if late:
                # Past the late-open bound: journaled and counted, never opened.
                late_refused = True
                self._record_missed(signal, lag)
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
            # Fill at the next bar's open with the backtest's causal slippage
            # (causal_execution, CAUSAL_NEXT_OPEN). No next bar, no fill.
            fill = self._next_open_fill(sym, tip, direction, next_bar)
            if fill["status"] == FILL_PENDING:
                pending = True
                continue
            if fill["status"] == FILL_GAP:
                logger.info(
                    "passed_paper_fill_bar_gap",
                    symbol=sym,
                    combination_id=combination_id,
                    direction=direction,
                    bar=tip_iso,
                    next_bar=fill.get("fill_bar_time"),
                )
                self._record_signal(**signal, outcome="not_opened", reason=SKIP_FILL_BAR_GAP)
                continue
            eval_result = dict(result or {})
            eval_result["signal_entry_price"] = eval_result.get("entry_price")
            eval_result["entry_price"] = fill["entry_price"]
            eval_result["next_open"] = fill["next_open"]
            eval_result["fill_bar_time"] = fill["fill_bar_time"]
            eval_result["entry_type"] = "NEXT_OPEN"
            eval_result["entry_slippage_rate"] = fill["entry_slippage_rate"]
            eval_result["exit_slippage_rate"] = float(self.research_config.exit_slippage_rate)
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
        elif pending and not opened:
            self.last_skip = f"{sym}:{FILL_PENDING}"
        elif book_closed:
            self.last_skip = f"{sym}:{REGIME_BOOK_CLOSED_REASON}"
        elif blocked and not opened:
            self.last_skip = f"{sym}:symbol_has_open_position"
        elif not_allowed and not opened:
            self.last_skip = f"{sym}:{SYMBOL_SIDE_NOT_ALLOWED}"
        elif not opened:
            self.last_skip = f"{sym}:no_passed_entry"
        bar_info["sides_evaluated"] = evaluated
        if late and failed:
            # Not retried past the bound: a side that could not be evaluated is unknown.
            self._record_not_evaluated(sym, tip_iso, "eval_failed")
        done = (not failed and not pending) or late
        with self._lock:
            self.last_evaluated_bar = bar_info
            self._inflight.discard(bar_key)
            # A signal waiting for its fill bar is retried, up to the late bound.
            if done:
                self._processed.add(bar_key)
        if done:
            self._note_evaluated(sym, tip_iso, tip_ts)
        return opened

    def catch_up(self, symbol: str, *, daily: DailySnapshot | None = None) -> list[Any]:
        """Evaluate every closed 1h bar of ``symbol`` after its watermark, oldest first.

        The closed tip goes through ``on_closed_1h`` exactly as before. A bar
        before the tip is evaluated on the 1h/4h/15m bars closed at its close,
        with the bar after it as the fill bar. It closed over an hour ago, so
        it is past the late-open bound: a signal on it is journaled as missed
        and never opened. A bar that cannot be evaluated yet (series fault,
        daily bar pending) stays unprocessed and is retried on the next call
        while it is within ``MAX_CATCHUP_BARS`` of the tip; older unprocessed
        bars are journaled as not evaluated. The watermark then moves to the
        newest bar with every bar up to it processed.

        A symbol with no watermark (first run, no state file) evaluates only
        the tip, with the first-sight rule in ``on_closed_1h``.
        """
        if not self.enabled:
            self.last_skip = "watcher_disabled"
            return []
        sym = normalize_symbol(symbol)
        if sym not in self.symbols:
            self.last_skip = f"{sym}:not_in_universe"
            return []

        from app.services.ohlcv_store import ohlcv_store

        series_1h = list(ohlcv_store.get_candles_for_engine(sym, "1h", include_open=False) or [])
        series_4h = list(ohlcv_store.get_candles_for_engine(sym, "4h", include_open=False) or [])
        series_15m = list(
            ohlcv_store.get_candles_for_engine(sym, "15m", include_open=False) or []
        )
        with self._lock:
            mark = _parse_iso(self._watermarks[sym]) if sym in self._watermarks else None
        tip_ts = candle_time(series_1h[-1]) if series_1h else None
        try:
            if mark is None or tip_ts is None:
                opened = self.on_closed_1h(
                    sym,
                    candles_1h=series_1h,
                    candles_4h=series_4h,
                    candles_15m=series_15m,
                    daily=daily,
                )
                self._start_watermark(sym, tip_ts)
                return opened
            return self._catch_up_from(sym, mark, series_1h, series_4h, series_15m, daily)
        finally:
            with self._lock:
                self.last_catchup_at = _wall_now().isoformat()
            self.save_state()

    def _start_watermark(self, sym: str, tip_ts: datetime | None) -> None:
        """First sight: start at the tip, or just before it while the tip is retried."""
        if tip_ts is None:
            return
        with self._lock:
            if sym not in self._seeded:
                return
            done = f"{sym}|{_iso(tip_ts)}" in self._processed
            self._pending_bars[sym] = 0 if done else 1
        self._set_watermark(sym, _utc(tip_ts) if done else _utc(tip_ts) - HOUR)

    def _catch_up_from(
        self,
        sym: str,
        mark: datetime,
        series_1h: list[Mapping[str, Any]],
        series_4h: list[Mapping[str, Any]],
        series_15m: list[Mapping[str, Any]],
        daily: DailySnapshot | None,
    ) -> list[Any]:
        last = len(series_1h) - 1
        tip_ts = _utc(candle_time(series_1h[last]))
        # The watermark covers every bar up to it: the first-sight rule does not apply.
        with self._lock:
            self._seeded.add(sym)
        window_start = tip_ts - HOUR * (MAX_CATCHUP_BARS - 1)

        # Due bars older than the window, whether or not the store still has them.
        beyond: list[str] = []
        slot = mark + HOUR
        with self._lock:
            while slot < window_start:
                key = f"{sym}|{_iso(slot)}"
                if key not in self._processed:
                    beyond.append(_iso(slot))
                    self._processed.add(key)
                slot += HOUR
        if beyond:
            self._record_not_evaluated(
                sym, beyond[0], "beyond_catchup_window", bars=len(beyond), last_bar=beyond[-1]
            )

        opened: list[Any] = []
        for idx, bar in enumerate(series_1h):
            ts = candle_time(bar)
            if ts is None or _utc(ts) <= mark or _utc(ts) < window_start:
                continue
            if idx == last:
                # The tip: the live path, unchanged (late bound, next-open fill, retries).
                opened.extend(
                    self.on_closed_1h(
                        sym,
                        candles_1h=series_1h,
                        candles_4h=series_4h,
                        candles_15m=series_15m,
                        daily=daily,
                    )
                )
                break
            bar_iso = _iso(ts)
            key = f"{sym}|{bar_iso}"
            with self._lock:
                if key in self._processed or key in self._inflight:
                    continue
            close = _utc(ts) + HOUR
            s1 = series_1h[: idx + 1]
            s4 = _closed_by(series_4h, close, H4)
            s15 = _closed_by(series_15m, close, M15)
            if (
                len(s1) < MIN_CLOSED_1H_BARS
                or len(s4) < MIN_CLOSED_4H_BARS
                or len(s15) < MIN_CLOSED_15M_BARS
            ):
                # The store does not reach back far enough; that does not heal.
                self._record_not_evaluated(sym, bar_iso, "history_short")
                with self._lock:
                    self._processed.add(key)
                continue
            opened.extend(
                self.on_closed_1h(
                    sym,
                    candles_1h=s1,
                    candles_4h=s4,
                    candles_15m=s15,
                    daily=daily,
                    next_bar=series_1h[idx + 1],
                )
            )
            with self._lock:
                retry = key not in self._processed
            if retry:
                logger.info(
                    "passed_paper_catchup_bar_retry", symbol=sym, bar=bar_iso, reason=self.last_skip
                )

        # Move the watermark over the processed prefix and count what is still owed.
        new_mark = mark
        owed = 0
        slot = mark + HOUR
        with self._lock:
            while slot <= tip_ts:
                if f"{sym}|{_iso(slot)}" not in self._processed:
                    owed += 1
                elif owed == 0:
                    new_mark = slot
                slot += HOUR
            self._pending_bars[sym] = owed
        self._set_watermark(sym, new_mark)
        return opened

    def _next_open_fill(
        self,
        sym: str,
        tip: Mapping[str, Any],
        direction: str,
        next_bar: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Causal next-open fill for a signal on ``tip``.

        ``status`` is ``FILL_OK`` with ``entry_price`` (the bar's open slipped
        by the causal entry rate), ``FILL_PENDING`` when the next bar is not in
        yet, or ``FILL_GAP`` when the bar after ``tip`` is missing.
        """
        bar = next_bar
        if bar is None:
            from app.services.ohlcv_store import ohlcv_store

            forming = ohlcv_store.get_open(sym, "1h")
            if forming is not None:
                bar = {"time": forming.open_time, "open": forming.open}
        tip_ts = candle_time(tip)
        bar_ts = candle_time(bar) if bar is not None else None
        rate = float(self.research_config.entry_slippage_rate)
        out: dict[str, Any] = {
            "status": FILL_PENDING,
            "entry_slippage_rate": rate,
            "fill_bar_time": _iso(bar_ts) if bar_ts is not None else "",
        }
        if bar is None or tip_ts is None or bar_ts is None or _utc(bar_ts) <= _utc(tip_ts):
            return out
        if not bars_adjacent(tip, bar, step_seconds("1h")):
            out["status"] = FILL_GAP
            return out
        raw = float(bar.get("open") or 0.0)
        if raw <= 0:
            return out
        out.update(
            status=FILL_OK,
            next_open=raw,
            entry_price=causal_entry_fill(raw, direction, rate=rate),
        )
        return out


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
            path = kwargs.get("state_path")
            if path is not None and _watcher.state_path != Path(path):
                _watcher.attach_state(path)
        return _watcher


def reset_passed_paper_watcher_for_tests() -> None:
    global _watcher
    with _watcher_lock:
        _watcher = None
