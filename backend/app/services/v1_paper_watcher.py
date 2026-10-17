"""COMBO_02 v1 paper watcher — same gate path as validated 1h backtest.

Evaluates closed 1h bars for enabled v1 books via ``evaluate_combination_at_bar``
(COMBO_02). Does not reimplement trend/BOS/HTF. Screener / Path B / 15m setups
are out of scope and must not open or label COMBO_02 v1 trades.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import structlog

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import (
    _bos_break_levels,
    _bos_break_possible,
    _close_breaks_level,
)
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.v1_production import (
    COMBO_ID,
    COMBO_VERSION,
    V1Book,
    enabled_v1_books,
    normalize_symbol,
    normalize_timeframe,
)
from app.signals._candle_utils import candle_time, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.swing_detector import _label_swings, detect_swings, extend_swings

logger = structlog.get_logger(__name__)

V1_PATH_LABEL = "A"
DEFAULT_SETUP_TF = "1h"
_MIN_SETUP_BARS = 50
# 4h HTF needs a real swing window. Fewer bars is a delay, not a failed gate.
_MIN_HTF_BARS = 20
# Cap eval windows — full DB-hydrated histories (500+) block the asyncio loop
# when evaluate_combination_at_bar runs synchronously on paper/status polls.
_MAX_EVAL_BARS_1H = 250
_MAX_EVAL_BARS_4H = 200
# Closed paper rows read to find a book's last exit (newest first).
_EXIT_SCAN_LIMIT = 200

# Latest skip per symbol, kept for the life of the process so a re-created
# watcher still reports why a book did not open. Not persisted anywhere.
_PROCESS_SKIPS: dict[str, dict[str, Any]] = {}


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def history_ready(
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_4h: Sequence[Mapping[str, Any]] | None,
) -> bool:
    """True when both setup and HTF series are deep enough to evaluate."""
    return len(candles_1h or []) >= _MIN_SETUP_BARS and len(candles_4h or []) >= _MIN_HTF_BARS


def closed_tip_is_current(tip: datetime | None, timeframe: str = "1h") -> bool:
    """True when the closed tip is the bar that just closed (live, not a stale backlog)."""
    if tip is None:
        return False
    from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe

    step = int(TIMEFRAME_MS.get(normalize_timeframe(timeframe)) or 0)
    if step <= 0:
        return False
    if tip.tzinfo is None:
        tip = tip.replace(tzinfo=timezone.utc)
    wall_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_open = (wall_ms // step) * step
    prev_open = current_open - step
    return int(tip.timestamp() * 1000) >= prev_open


# Ledger: good opens came 51–576 s after the bar close, bad ones 1,165–3,302 s after.
MAX_OPEN_LAG_SECONDS = 900.0

# Skips after the combo eval: the bar may read V1 ELIGIBLE on screen, but the
# watcher refused to open it. Shown on the screener; they do not change any gate.
_POST_EVAL_REFUSALS = frozenset(
    {"position_open", "exit_bar_no_entry", "sticky_bos_skip", "late_tip", "paper_open_skipped"}
)


def _utc_now() -> datetime:
    """Wall clock for the open-lag bound. Tests replace this."""
    return datetime.now(timezone.utc)


def tip_open_lag_seconds(
    tip: datetime | None,
    timeframe: str = "1h",
    now: datetime | None = None,
) -> float | None:
    """Seconds since the closed tip's bar closed (tip open + timeframe). None when unknown."""
    if tip is None:
        return None
    from app.ingestion.klines import TIMEFRAME_MS, normalize_timeframe

    step = int(TIMEFRAME_MS.get(normalize_timeframe(timeframe)) or 0)
    if step <= 0:
        return None
    if tip.tzinfo is None:
        tip = tip.replace(tzinfo=timezone.utc)
    wall = now if now is not None else _utc_now()
    if wall.tzinfo is None:
        wall = wall.replace(tzinfo=timezone.utc)
    return wall.timestamp() - (tip.timestamp() + step / 1000.0)


# Closed bars rolled locally from the trade stream. They are replaced by the
# exchange kline (WS x=true or REST), so a decision waits for that copy.
_UNCONFIRMED_SOURCES = frozenset({"binance_trade_roll"})


def _series_fault(
    candles: Sequence[Mapping[str, Any]],
    step_seconds: int,
    label: str,
) -> tuple[str | None, datetime | None]:
    """First fault in one closed series, plus its tip open time."""
    prev: datetime | None = None
    for candle in candles:
        ts = candle_time(candle)
        if ts is None:
            return f"{label}_bad_timestamp", None
        if int(ts.timestamp()) % step_seconds:
            return f"{label}_misaligned", None
        if prev is not None:
            delta = (ts - prev).total_seconds()
            if delta <= 0:
                return f"{label}_out_of_order", None
            if delta != step_seconds:
                return f"{label}_gap", None
        prev = ts
    if candles and str(candles[-1].get("source") or "") in _UNCONFIRMED_SOURCES:
        return f"{label}_tip_unconfirmed", prev
    return None, prev


def series_integrity_fault(
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
) -> str | None:
    """Reason the evaluated 1h/4h windows are not safe to decide on, else None.

    Checks only the bars the evaluation reads (the last ``_MAX_EVAL_BARS_*``):
    aligned and contiguous open times, an exchange-confirmed tip, and a 4h tip
    that is exactly the last 4h bar fully closed at the 1h tip's close. An
    older 4h tip is missing its latest bar; a newer one is still forming.
    """
    window_1h = list(candles_1h)[-_MAX_EVAL_BARS_1H:]
    window_4h = list(candles_4h)[-_MAX_EVAL_BARS_4H:]
    fault, tip_1h = _series_fault(window_1h, 3600, "1h")
    if fault:
        return fault
    fault, tip_4h = _series_fault(window_4h, 14400, "4h")
    if fault:
        return fault
    if tip_1h is None or tip_4h is None:
        return "missing_tip"
    decision = int(tip_1h.timestamp()) + 3600
    expected_4h = (decision // 14400) * 14400 - 14400
    have_4h = int(tip_4h.timestamp())
    if have_4h < expected_4h:
        return "4h_tip_behind"
    if have_4h > expected_4h:
        return "4h_tip_not_closed"
    return None


def swings_on_window(
    swings: list[Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    left: int,
) -> list[Any]:
    """Re-anchor swings to ``candles`` by candle open time.

    The live store hands over a sliding window whose tip stays in the last
    slot, so ``bar_index`` alone does not identify a candle. A swing whose
    candle left the window, or now sits in the first ``left`` bars where a
    fresh detection cannot confirm it, is dropped.
    """
    if not swings:
        return swings
    slot_of: dict[datetime, int] = {}
    for i, candle in enumerate(candles):
        ts = candle_time(candle)
        if ts is not None:
            slot_of[ts] = i
    if not slot_of:
        return swings
    out: list[Any] = []
    changed = False
    for swing in swings:
        if swing.timestamp is None:
            out.append(swing)
            continue
        slot = slot_of.get(swing.timestamp)
        if slot is None or slot < left:
            changed = True
            continue
        if slot != swing.bar_index:
            swing = replace(swing, bar_index=slot)
            changed = True
        out.append(swing)
    return _label_swings(out) if changed else swings


def bar_key(symbol: str, timeframe: str, bar_time: str | datetime | None) -> str:
    sym = normalize_symbol(symbol)
    tf = normalize_timeframe(timeframe)
    if isinstance(bar_time, datetime):
        stamp = _iso(bar_time) or ""
    else:
        stamp = str(bar_time or "")
    return f"{sym}|{tf}|{stamp}"


def is_v1_long_entry(result: Mapping[str, Any] | None) -> bool:
    """Hard checks on combination-engine output for COMBO_02 v1 LONG."""
    if not result or not isinstance(result, Mapping):
        return False
    # SHORT research identity / direction can never satisfy v1 LONG entry.
    if str(result.get("strategy_id") or "") in (
        "COMBO_02_SHORT_RESEARCH",
        "SHORT_PULLBACK_REJECTION_RESEARCH",
        "COMBO_02_SHORT_ENTRY_RESEARCH",
    ):
        return False
    if str(result.get("source") or "") == "SHORT_RESEARCH_PIPELINE":
        return False
    if str(result.get("combo_version") or "").lower() in (
        "v2-short-research",
        "v1-short-pullback-rejection",
        "v2-short-entry-research",
    ):
        return False
    if str(result.get("direction") or "").upper() == "SHORT":
        return False
    if str(result.get("status") or "") != "LONG_ENTRY_CANDIDATE":
        return False
    # LONG_ENTRY_CANDIDATE implies LONG; only reject an explicit non-LONG direction.
    direction = str(result.get("direction") or "").upper()
    if direction and direction != "LONG":
        return False
    if str(result.get("combination_id") or "").upper() != COMBO_ID:
        return False
    gates = result.get("gates") or {}
    required = list(result.get("required") or [])
    if required and not all(bool(gates.get(k)) for k in required):
        return False
    if not bool(gates.get("bos")) or not bool(gates.get("trend")) or not bool(
        gates.get("htf")
    ):
        return False
    htf = result.get("htf") or {}
    if str(htf.get("htf_alignment") or "").upper() != "HTF_ALIGNED":
        return False
    if str(htf.get("trend_1h") or "").upper() != "BULLISH":
        return False
    if str(htf.get("trend_4h") or "").upper() != "BULLISH":
        return False
    entry = result.get("entry_price")
    stop = result.get("stop_price")
    try:
        if entry is None or stop is None or float(entry) <= 0 or float(stop) >= float(entry):
            return False
    except (TypeError, ValueError):
        return False
    return True


class V1PaperWatcher:
    """Watches frozen COMBO_02 v1 LONG books on closed 1h candles."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeframe: str = DEFAULT_SETUP_TF,
        secondary_enabled: bool = True,
        replay_mode: bool = False,
        emit_alerts: bool = True,
        paper_engine: Any | None = None,
        signal_config: SignalConfig | None = None,
        research_config: ResearchConfig | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.timeframe = normalize_timeframe(timeframe) or DEFAULT_SETUP_TF
        self.secondary_enabled = bool(secondary_enabled)
        self.replay_mode = bool(replay_mode)
        self.emit_alerts = bool(emit_alerts)
        self._paper = paper_engine
        self.signal_config = signal_config or SignalConfig()
        self.research_config = research_config or ResearchConfig()
        self._combo = get_combination(COMBO_ID)
        self._lock = threading.RLock()
        # Keys already evaluated (opened or rejected) — no backlog spam on restart.
        self._processed_bars: set[str] = set()
        self._watermarks: dict[str, str] = {}  # symbol -> last closed bar iso
        self._seeded: set[str] = set()
        self._last_skip: str | None = None
        # symbol -> {"reason", "bar"}: latest skip and the closed 1h bar it applied to.
        self._skip_by_symbol: dict[str, dict[str, Any]] = {
            sym: dict(skip) for sym, skip in _PROCESS_SKIPS.items()
        }
        # Mirror combination_backtest sticky-break skip (same broken level).
        self._prev_break: dict[str, bool] = {}
        self._prev_swing_n: dict[str, int] = {}
        self._swing_state: dict[str, list[Any]] = {}
        # symbol -> V1 position was open when its previous closed bar was handled.
        self._in_trade: dict[str, bool] = {}
        # Read-only UI snapshots (never open trades). symbol -> payload.
        self._presentation_snapshots: dict[str, dict[str, Any]] = {}
        # symbol -> {"reason", "bar", "detail"}: a bar the combo eval passed or
        # could pass but the watcher refused. Display only; a later
        # already_processed note does not overwrite it.
        self._bar_refusals: dict[str, dict[str, Any]] = {}
        self._presentation_refreshed_at: float = 0.0
        self._presentation_ttl_sec: float = 20.0

    @property
    def books(self) -> list[V1Book]:
        return enabled_v1_books(
            secondary_enabled=self.secondary_enabled,
            timeframes={self.timeframe},
        )

    def book_for(self, symbol: str) -> V1Book | None:
        sym = normalize_symbol(symbol)
        for book in self.books:
            if book.symbol == sym:
                return book
        return None

    def _paper_engine(self) -> Any:
        if self._paper is not None:
            return self._paper
        from app.services.paper_trade import get_paper_trade_engine

        return get_paper_trade_engine()

    def _note_skip(
        self, symbol: str, reason: str, bar: str | None, *, detail: str | None = None
    ) -> None:
        """Record a per-symbol skip; a later book's skip does not overwrite it."""
        sym = normalize_symbol(symbol)
        entry = {"reason": reason, "bar": bar}
        kind = reason.split(":", 1)[1] if ":" in reason else reason
        with self._lock:
            self._last_skip = reason
            self._skip_by_symbol[sym] = entry
            _PROCESS_SKIPS[sym] = dict(entry)
            if kind in _POST_EVAL_REFUSALS:
                self._bar_refusals[sym] = {"reason": kind, "bar": bar, "detail": detail}

    def _with_refusals(self, snaps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Attach the watcher's refusal to a snapshot of the same closed bar."""
        with self._lock:
            refusals = {k: dict(v) for k, v in self._bar_refusals.items()}
        for sym, snap in snaps.items():
            refusal = refusals.get(sym)
            if refusal and refusal.get("bar") and refusal.get("bar") == snap.get("tip_bar"):
                snap["watcher_refusal"] = refusal
        return snaps

    def _clear_skip(self, symbol: str) -> None:
        """Drop one symbol's skip after its position opened. Display only."""
        sym = normalize_symbol(symbol)
        with self._lock:
            self._skip_by_symbol.pop(sym, None)
            _PROCESS_SKIPS.pop(sym, None)
            self._bar_refusals.pop(sym, None)

    def seed_watermark(
        self,
        symbol: str,
        *,
        last_closed_bar_time: datetime | str | None,
    ) -> None:
        """Mark current tip as already seen so startup does not replay history."""
        sym = normalize_symbol(symbol)
        stamp = (
            _iso(last_closed_bar_time)
            if isinstance(last_closed_bar_time, datetime)
            else str(last_closed_bar_time or "")
        )
        with self._lock:
            if stamp:
                self._watermarks[sym] = stamp
                self._processed_bars.add(bar_key(sym, self.timeframe, stamp))
            self._seeded.add(sym)

    def seed_from_store(self, symbols: Sequence[str] | None = None) -> int:
        """Seed watermarks from ohlcv_store tips (no evaluation)."""
        from app.services.ohlcv_store import ohlcv_store

        books = self.books
        want = {b.symbol for b in books}
        if symbols:
            want &= {normalize_symbol(s) for s in symbols}
        n = 0
        for sym in sorted(want):
            closed = ohlcv_store.get_candles_for_engine(
                sym, self.timeframe, include_open=False
            )
            h4 = ohlcv_store.get_candles_for_engine(sym, "4h", include_open=False)
            # Thin history is a delay. Leave the book unseeded so the live tip
            # can still open once 1h and 4h catch up.
            if not history_ready(closed, h4):
                continue
            tip = candle_time(closed[-1])
            self.seed_watermark(sym, last_closed_bar_time=tip)
            n += 1
        return n

    def evaluate_at_bar(
        self,
        *,
        symbol: str,
        candles_1h: Sequence[Mapping[str, Any]],
        candles_4h: Sequence[Mapping[str, Any]],
        as_of_index: int,
    ) -> dict[str, Any]:
        """Call the same combination engine as the validated backtest."""
        if self._combo is None:
            return {"status": "NO_SETUP", "reason": "COMBO_02 missing"}
        series = list(candles_1h)
        if as_of_index < 0 or as_of_index >= len(series):
            return {"status": "NO_SETUP", "reason": "as_of_index out of range"}
        if len(series) < _MIN_SETUP_BARS:
            return {"status": "NO_SETUP", "reason": "insufficient_1h_history"}
        if not candles_4h:
            return {"status": "NO_SETUP", "reason": "missing_4h", "htf": {}}
        # Keep tip-aligned windows — drop ancient history that only slows eval.
        if len(series) > _MAX_EVAL_BARS_1H:
            drop = len(series) - _MAX_EVAL_BARS_1H
            series = series[drop:]
            as_of_index = as_of_index - drop
        htf = list(candles_4h)
        if len(htf) > _MAX_EVAL_BARS_4H:
            htf = htf[-_MAX_EVAL_BARS_4H:]
        return evaluate_combination_at_bar(
            symbol=normalize_symbol(symbol),
            timeframe=self.timeframe,
            candles=series,
            as_of_index=int(as_of_index),
            combination=self._combo,
            signal_config=self.signal_config,
            research_config=self.research_config,
            candles_1h=series,
            candles_4h=htf,
            compute_sd=False,
        )

    def _bos_break_allows_entry(
        self,
        symbol: str,
        candles: Sequence[Mapping[str, Any]],
        as_of_index: int,
        *,
        reset_state: bool = False,
        last_exit_bar: datetime | None = None,
    ) -> bool:
        """Same sticky-break skip as ``run_combination_backtest`` (COMBO_02).

        First sight of a symbol replays the break state across ``candles``
        up to the bar before ``as_of_index``. ``reset_state`` starts with no
        prior break, as the backtest walk does on its first bar.
        """
        sym = normalize_symbol(symbol)
        series = list(candles)
        if reset_state or sym not in self._swing_state:
            self._seed_sticky_state(
                sym,
                series,
                as_of_index,
                fresh=reset_state,
                last_exit_bar=last_exit_bar,
            )
        swings, swing_grew = self._advance_swings(sym, series, as_of_index)
        brk = _bos_break_possible(
            series, as_of_index, swings, direction_filter="LONG"
        )
        if not brk:
            self._prev_break[sym] = False
            return False
        if self._prev_break.get(sym) and not swing_grew:
            return False
        self._prev_break[sym] = True
        return True

    def _seed_sticky_state(
        self,
        sym: str,
        series: list[Mapping[str, Any]],
        as_of_index: int,
        *,
        fresh: bool,
        last_exit_bar: datetime | None = None,
    ) -> None:
        """Build swing and break state as of the bar before ``as_of_index``.

        Missing memory is not "no prior break": unless ``fresh``, the break
        state is replayed over the loaded window. Nothing is evaluated or
        opened. A bar the book exited on leaves the break cleared.
        """
        swing_cfg = self.signal_config.swing_for(self.timeframe)
        _, highs_arr, lows_arr, closes_arr, _ = series_ohlcv(series)
        kwargs: dict[str, Any] = {
            "left": swing_cfg.swing_left_bars,
            "right": swing_cfg.swing_right_bars,
            "symbol": sym,
            "timeframe": self.timeframe,
            "atr_period": self.signal_config.atr_period,
            "minimum_swing_distance_atr": swing_cfg.minimum_swing_distance_atr,
            "highs": highs_arr,
            "lows": lows_arr,
            "closes": closes_arr,
        }
        seed_idx = max(as_of_index - 1, 0)
        start = as_of_index if fresh else min(as_of_index, _MIN_SETUP_BARS)
        swings = detect_swings(
            series, as_of_index=seed_idx if fresh else max(start - 1, 0), **kwargs
        )
        prev_break = False
        level_n = -1
        levels: tuple[str, float, float] | None = None
        for t in range(start, as_of_index):
            before_n = len(swings)
            swings = extend_swings(swings, series, as_of_index=t, **kwargs)
            swing_grew = len(swings) != before_n
            if last_exit_bar is not None and _utc(candle_time(series[t])) == last_exit_bar:
                prev_break = False
                continue
            if len(swings) != level_n:
                level_n = len(swings)
                levels = _bos_break_levels(swings, t)
            brk = levels is not None and _close_breaks_level(
                levels[0], float(closes_arr[t]), levels[1], levels[2], "LONG"
            )
            if not brk:
                prev_break = False
            elif not (prev_break and not swing_grew):
                prev_break = True
        self._swing_state[sym] = swings
        self._prev_break[sym] = prev_break
        self._prev_swing_n[sym] = len(swings)

    def _advance_swings(
        self,
        sym: str,
        series: list[Mapping[str, Any]],
        as_of_index: int,
    ) -> tuple[list[Any], bool]:
        """Extend the symbol's swings to ``as_of_index``; True when one confirmed."""
        swing_cfg = self.signal_config.swing_for(self.timeframe)
        _, highs_arr, lows_arr, closes_arr, _ = series_ohlcv(series)
        # Swing identity follows the candle, not the slot: a full store window
        # slides under a fixed tip index.
        anchored = swings_on_window(
            self._swing_state[sym], series, left=swing_cfg.swing_left_bars
        )
        anchored_n = len(anchored)
        swings = extend_swings(
            anchored,
            series,
            as_of_index=as_of_index,
            left=swing_cfg.swing_left_bars,
            right=swing_cfg.swing_right_bars,
            symbol=sym,
            timeframe=self.timeframe,
            atr_period=self.signal_config.atr_period,
            minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
            highs=highs_arr,
            lows=lows_arr,
            closes=closes_arr,
        )
        self._swing_state[sym] = swings
        # "Grew" means this bar confirmed a new swing. Swings that slid out of
        # the window do not count.
        self._prev_swing_n[sym] = len(swings)
        return swings, len(swings) != anchored_n

    def _advance_without_entry(
        self,
        symbol: str,
        candles: Sequence[Mapping[str, Any]],
        as_of_index: int,
        *,
        exited: bool,
        reset_state: bool = False,
        last_exit_bar: datetime | None = None,
    ) -> None:
        """A bar spent in a trade, or the bar it exited on: no entry is considered.

        Same as ``run_combination_backtest``: swings advance, the break state
        is left alone while the trade is open, and the exit bar clears it so
        the next closed bar may enter the same broken level.
        """
        sym = normalize_symbol(symbol)
        series = list(candles)
        if reset_state or sym not in self._swing_state:
            self._seed_sticky_state(
                sym,
                series,
                as_of_index,
                fresh=reset_state,
                last_exit_bar=last_exit_bar,
            )
        self._advance_swings(sym, series, as_of_index)
        if exited:
            self._prev_break[sym] = False

    @staticmethod
    def _v1_open(paper: Any, symbol: str) -> bool:
        has_open = getattr(paper, "has_open", None)
        return bool(has_open(symbol, "V1")) if callable(has_open) else False

    def _last_exit_bar(self, paper: Any, symbol: str) -> datetime | None:
        """Open time of the bar the book's last V1 stop/target exit fell in."""
        if self.replay_mode:
            # Replay closes carry the wall clock, not the bar being walked.
            return None
        positions = getattr(paper, "positions", None)
        if not callable(positions):
            return None
        from app.services.paper_trade import PaperTradeEngine, exit_bar_open

        try:
            closed = positions(closed_limit=_EXIT_SCAN_LIMIT).get("closed") or []
        except Exception:  # noqa: BLE001
            return None
        sym = normalize_symbol(symbol)
        last: datetime | None = None
        for row in closed:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("symbol") or "").upper() != sym:
                continue
            # Real exits only. A CANCELLED row is not a strategy exit.
            if str(row.get("status") or "").upper() != "CLOSED":
                continue
            if PaperTradeEngine.stream_of(dict(row)) != "V1":
                continue
            bar = exit_bar_open(row.get("closed_at"), self.timeframe)
            if bar is not None and (last is None or bar > last):
                last = bar
        return last

    def _trade_phase(
        self,
        paper: Any,
        symbol: str,
        tip_ts: datetime | None,
        last_exit_bar: datetime | None,
    ) -> str | None:
        """Return "open" while the V1 position is live, "exit" on the bar it closed in."""
        sym = normalize_symbol(symbol)
        open_now = self._v1_open(paper, sym)
        with self._lock:
            was_open = bool(self._in_trade.get(sym))
            self._in_trade[sym] = open_now
        if open_now:
            return "open"
        if was_open:
            return "exit"
        tip = _utc(tip_ts)
        # Also true after a restart, and for an exit that printed in the new
        # bar before this one was handled.
        if last_exit_bar is not None and tip is not None and last_exit_bar >= tip:
            return "exit"
        return None

    def on_closed_1h(
        self,
        symbol: str,
        *,
        candles_1h: Sequence[Mapping[str, Any]] | None = None,
        candles_4h: Sequence[Mapping[str, Any]] | None = None,
        force: bool = False,
    ) -> Any | None:
        """Evaluate the latest fully closed 1h tip for a v1 book."""
        if not self.enabled:
            self._last_skip = "watcher_disabled"
            return None
        book = self.book_for(symbol)
        if book is None:
            self._last_skip = f"{normalize_symbol(symbol)}:not_v1_book"
            return None
        if self.timeframe != "1h":
            self._last_skip = f"unsupported_tf:{self.timeframe}"
            return None
        # SHORT research never enters the frozen v1 watcher path.
        from app.research.short_research_constants import STRATEGY_ID as SHORT_STRATEGY_ID

        if str(getattr(book, "strategy_id", "") or "") == SHORT_STRATEGY_ID:
            self._last_skip = f"{normalize_symbol(symbol)}:short_research_only"
            raise PermissionError("short_research_only")

        from app.services.ohlcv_store import ohlcv_store

        from_live_store = candles_1h is None and candles_4h is None
        series_1h = list(
            candles_1h
            if candles_1h is not None
            else ohlcv_store.get_candles_for_engine(
                book.symbol, "1h", include_open=False
            )
        )
        series_4h = list(
            candles_4h
            if candles_4h is not None
            else ohlcv_store.get_candles_for_engine(
                book.symbol, "4h", include_open=False
            )
        )
        if not history_ready(series_1h, series_4h):
            # Do not watermark. The same closed 1h bar is retried after REST
            # catch-up so a late 4h/1h download can still open the trade.
            catchup_bar = _iso(candle_time(series_1h[-1])) if series_1h else None
            self._note_skip(book.symbol, f"{book.symbol}:ohlcv_catchup", catchup_bar)
            return None
        tip_idx = len(series_1h) - 1
        tip_bar = series_1h[tip_idx]
        tip_ts = candle_time(tip_bar)
        tip_iso = _iso(tip_ts)
        key = bar_key(book.symbol, self.timeframe, tip_iso)
        if from_live_store and not self.replay_mode:
            from app.services.v21a_paper_validation import is_candle_fully_closed

            if tip_bar.get("is_closed") is False or tip_bar.get("closed") is False:
                self._note_skip(book.symbol, f"{book.symbol}:forming_candle", tip_iso)
                return None
            if (
                tip_bar.get("is_closed") is not True
                and tip_bar.get("closed") is not True
                and not is_candle_fully_closed(tip_bar, "1h")
            ):
                self._note_skip(book.symbol, f"{book.symbol}:forming_candle", tip_iso)
                return None

        with self._lock:
            if not force and not self.replay_mode and book.symbol not in self._seeded:
                if closed_tip_is_current(tip_ts, self.timeframe):
                    # History just caught up to the live closed bar — evaluate it.
                    self._seeded.add(book.symbol)
                else:
                    # Stale tip on first sight: do not replay an old backlog.
                    self._watermarks[book.symbol] = tip_iso or ""
                    self._processed_bars.add(key)
                    self._seeded.add(book.symbol)
                    self._note_skip(
                        book.symbol, f"{book.symbol}:seeded_no_backlog", tip_iso
                    )
                    logger.info(
                        "v1_watcher_seeded",
                        symbol=book.symbol,
                        timeframe=self.timeframe,
                        bar=tip_iso,
                    )
                    return None
            if key in self._processed_bars and not force:
                self._note_skip(
                    book.symbol, f"{book.symbol}:already_processed", tip_iso
                )
                return None
            wm = self._watermarks.get(book.symbol)
            if (
                not force
                and not self.replay_mode
                and wm
                and tip_iso
                and tip_iso <= wm
            ):
                self._processed_bars.add(key)
                self._note_skip(
                    book.symbol, f"{book.symbol}:not_newer_than_watermark", tip_iso
                )
                return None

        if from_live_store:
            # Do not watermark. The same bar is retried once the store heals,
            # up to the open-lag bound.
            fault = series_integrity_fault(series_1h, series_4h)
            if fault:
                self._note_skip(
                    book.symbol, f"{book.symbol}:data_integrity:{fault}", tip_iso
                )
                logger.warning(
                    "v1_watcher_data_integrity",
                    symbol=book.symbol,
                    bar=tip_iso,
                    fault=fault,
                )
                return None

        result = self.evaluate_at_bar(
            symbol=book.symbol,
            candles_1h=series_1h,
            candles_4h=series_4h,
            as_of_index=tip_idx,
        )
        self._store_presentation_snapshot(
            book.symbol,
            eval_result=result,
            tip_bar=tip_iso,
        )
        with self._lock:
            self._processed_bars.add(key)
            if tip_iso:
                self._watermarks[book.symbol] = tip_iso
            self._seeded.add(book.symbol)

        paper = self._paper_engine()
        last_exit = (
            None
            if self._v1_open(paper, book.symbol)
            else self._last_exit_bar(paper, book.symbol)
        )
        phase = self._trade_phase(paper, book.symbol, tip_ts, last_exit)
        if phase is not None:
            # Backtest rule: no entry while the trade is open or on its exit bar.
            self._advance_without_entry(
                book.symbol,
                series_1h,
                tip_idx,
                exited=phase == "exit",
                last_exit_bar=last_exit,
            )
            reason = "exit_bar_no_entry" if phase == "exit" else "position_open"
            self._note_skip(book.symbol, f"{book.symbol}:{reason}", tip_iso)
            return None

        if not self._bos_break_allows_entry(
            book.symbol, series_1h, tip_idx, last_exit_bar=last_exit
        ):
            self._note_skip(book.symbol, f"{book.symbol}:sticky_bos_skip", tip_iso)
            return None

        if not is_v1_long_entry(result):
            self._note_skip(
                book.symbol,
                f"{book.symbol}:gates_fail:{result.get('reason') or result.get('status')}",
                tip_iso,
            )
            return None

        if not force and not self.replay_mode:
            # A closed bar is not opened long after it closed.
            lag = tip_open_lag_seconds(tip_ts, self.timeframe)
            if lag is not None and lag > MAX_OPEN_LAG_SECONDS:
                self._note_skip(book.symbol, f"{book.symbol}:late_tip", tip_iso)
                return None

        if not self.replay_mode:
            from app.research.ohlcv_continuity import bars_adjacent, step_seconds
            from app.services.ohlcv_store import ohlcv_store
            from app.services.paper_sizing import DEFAULT_PAPER_SLIPPAGE_RATE

            forming = ohlcv_store.get_open(book.symbol, "1h")
            if (
                forming is not None
                and tip_ts is not None
                and bars_adjacent(tip_bar, {"time": forming.open_time}, step_seconds("1h"))
            ):
                result = dict(result)
                result["entry_price"] = float(forming.open) * (
                    1.0 + DEFAULT_PAPER_SLIPPAGE_RATE
                )
                result["entry_type"] = "NEXT_OPEN"

        pos = paper.open_v1_combo_position(
            symbol=book.symbol,
            timeframe=self.timeframe,
            book=book,
            eval_result=result,
            setup_bar_time_utc=tip_iso or "",
            replay=bool(self.replay_mode),
            emit_alert=bool(self.emit_alerts) and not self.replay_mode,
        )
        if pos is None:
            detail = None
            try:
                engine_skip = str(paper.status().get("last_skip_reason") or "")
                if engine_skip.startswith(f"{book.symbol}:"):
                    detail = engine_skip[len(book.symbol) + 1 :]
            except Exception:  # noqa: BLE001
                detail = None
            self._note_skip(
                book.symbol, f"{book.symbol}:paper_open_skipped", tip_iso, detail=detail
            )
        else:
            # An earlier bar's skip no longer describes this book.
            self._clear_skip(book.symbol)
            with self._lock:
                self._in_trade[book.symbol] = self._v1_open(paper, book.symbol)
        return pos

    def replay(
        self,
        symbol: str,
        candles_1h: Sequence[Mapping[str, Any]],
        candles_4h: Sequence[Mapping[str, Any]],
        *,
        start_index: int | None = None,
        simulate_exits: bool = True,
    ) -> list[Any]:
        """Historical mode: walk each closed 1h bar (parity tests)."""
        book = self.book_for(symbol)
        if book is None:
            return []
        series = list(candles_1h)
        h4 = list(candles_4h)
        if not series:
            return []
        begin = (
            int(start_index)
            if start_index is not None
            else max(int(self.research_config.min_bars), _MIN_SETUP_BARS)
        )
        begin = max(begin, 0)
        opened: list[Any] = []
        paper = self._paper_engine()
        prev_replay = self.replay_mode
        self.replay_mode = True
        # Fresh sticky-break / swing state for this walk
        self._prev_break.pop(book.symbol, None)
        self._prev_swing_n.pop(book.symbol, None)
        self._swing_state.pop(book.symbol, None)
        self._in_trade.pop(book.symbol, None)
        fresh = True
        try:
            for i in range(begin, len(series)):
                exited = False
                # Manage open exit against this bar before considering a new entry.
                if simulate_exits:
                    bar = series[i]
                    px = float(bar.get("close") or 0)
                    hi = float(bar.get("high") or px)
                    lo = float(bar.get("low") or px)
                    open_pos = None
                    if hasattr(paper, "get_open"):
                        open_pos = paper.get_open(book.symbol, "V1")
                    else:
                        with getattr(paper, "_lock", threading.RLock()):
                            open_pos = getattr(paper, "_open", {}).get(book.symbol)
                    if open_pos is not None:
                        stop = float(open_pos.stop_price or 0)
                        tp1 = open_pos.tp1_price
                        mark = px
                        if stop > 0 and lo <= stop:
                            mark = stop
                        elif tp1 is not None and hi >= float(tp1):
                            mark = float(tp1)
                        before = (
                            paper.has_open(book.symbol, "V1")
                            if hasattr(paper, "has_open")
                            else book.symbol in getattr(paper, "_open", {})
                        )
                        paper.tick({book.symbol: mark})
                        after = (
                            paper.has_open(book.symbol, "V1")
                            if hasattr(paper, "has_open")
                            else book.symbol in getattr(paper, "_open", {})
                        )
                        exited = bool(before and not after)

                tip_ts = candle_time(series[i])
                tip_iso = _iso(tip_ts)
                key = bar_key(book.symbol, self.timeframe, tip_iso)
                with self._lock:
                    seen = key in self._processed_bars
                if seen:
                    if exited:
                        self._prev_break[book.symbol] = False
                    continue

                already_v1 = (
                    paper.has_open(book.symbol, "V1")
                    if hasattr(paper, "has_open")
                    else book.symbol in getattr(paper, "_open", {})
                )
                # Backtest rule: swings advance every bar; a bar in a trade is
                # not evaluated, and the exit bar clears the break and is
                # skipped, so the next bar may enter the same broken level.
                if already_v1 or exited:
                    self._advance_without_entry(
                        book.symbol, series, i, exited=exited, reset_state=fresh
                    )
                    allows = False
                else:
                    allows = self._bos_break_allows_entry(
                        book.symbol, series, i, reset_state=fresh
                    )
                fresh = False
                with self._lock:
                    self._processed_bars.add(key)
                    if tip_iso:
                        self._watermarks[book.symbol] = tip_iso
                if not allows:
                    continue

                result = self.evaluate_at_bar(
                    symbol=book.symbol,
                    candles_1h=series,
                    candles_4h=h4,
                    as_of_index=i,
                )
                if not is_v1_long_entry(result):
                    continue
                pos = paper.open_v1_combo_position(
                    symbol=book.symbol,
                    timeframe=self.timeframe,
                    book=book,
                    eval_result=result,
                    setup_bar_time_utc=tip_iso or "",
                    replay=True,
                    emit_alert=False,
                )
                if pos is not None:
                    opened.append(pos)
        finally:
            self.replay_mode = prev_replay
        return opened

    def _store_presentation_snapshot(
        self,
        symbol: str,
        *,
        eval_result: Mapping[str, Any] | None,
        tip_bar: str | None,
    ) -> None:
        """Cache a read-only evaluate_combination_at_bar tip snapshot for UI."""
        sym = normalize_symbol(symbol)
        htf = (eval_result or {}).get("htf") if isinstance(eval_result, Mapping) else None
        htf = htf if isinstance(htf, Mapping) else {}
        payload = {
            "symbol": sym,
            "tip_bar": tip_bar,
            "evaluated_at_utc": _iso(datetime.now(timezone.utc)),
            "eval": dict(eval_result) if isinstance(eval_result, Mapping) else None,
            "trend_1h": htf.get("trend_1h"),
            "trend_4h": htf.get("trend_4h"),
            "htf_alignment": htf.get("htf_alignment"),
            "read_only": True,
            "opens_trades": False,
        }
        with self._lock:
            self._presentation_snapshots[sym] = payload

    def refresh_presentation_snapshots(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        """Evaluate tip for BTC/ETH/SOL only; never opens paper or emits alerts.

        Cached with a short TTL so screener refresh does not re-run combo eval
        on every poll. Does not touch non-v1 symbols.
        """
        import time

        now = time.monotonic()
        with self._lock:
            age = now - float(self._presentation_refreshed_at or 0.0)
            if (
                not force
                and self._presentation_snapshots
                and age < float(self._presentation_ttl_sec)
            ):
                return {
                    k: dict(v) for k, v in self._presentation_snapshots.items()
                }

        from app.services.ohlcv_store import ohlcv_store

        for book in self.books:
            try:
                series_1h = ohlcv_store.get_candles_for_engine(
                    book.symbol, "1h", include_open=False
                )
                series_4h = ohlcv_store.get_candles_for_engine(
                    book.symbol, "4h", include_open=False
                )
                if not series_1h:
                    self._store_presentation_snapshot(
                        book.symbol,
                        eval_result={"status": "NO_SETUP", "reason": "missing_1h", "htf": {}},
                        tip_bar=None,
                    )
                    continue
                tip_idx = len(series_1h) - 1
                tip_iso = _iso(candle_time(series_1h[tip_idx]))
                if not series_4h:
                    self._store_presentation_snapshot(
                        book.symbol,
                        eval_result={
                            "status": "NO_SETUP",
                            "reason": "missing_4h",
                            "htf": {},
                        },
                        tip_bar=tip_iso,
                    )
                    continue
                result = self.evaluate_at_bar(
                    symbol=book.symbol,
                    candles_1h=series_1h,
                    candles_4h=series_4h,
                    as_of_index=tip_idx,
                )
                self._store_presentation_snapshot(
                    book.symbol,
                    eval_result=result,
                    tip_bar=tip_iso,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "v1_presentation_snapshot_failed",
                    symbol=book.symbol,
                    error=str(exc),
                )
                # Fail closed — leave prior cache or mark unavailable via absence.
                continue

        with self._lock:
            self._presentation_refreshed_at = now
            return {k: dict(v) for k, v in self._presentation_snapshots.items()}

    def get_presentation_snapshots(self, *, refresh: bool = True) -> dict[str, dict[str, Any]]:
        """Return cached tip eval snapshots for v1 books (presentation only)."""
        if refresh:
            return self._with_refusals(self.refresh_presentation_snapshots(force=False))
        with self._lock:
            snaps = {k: dict(v) for k, v in self._presentation_snapshots.items()}
        return self._with_refusals(snaps)

    def peek_books(self) -> list[dict[str, Any]]:
        """Cheap tip snapshot per v1 book; merges cached combo eval when present."""
        from app.services.ohlcv_store import ohlcv_store

        snaps = self.get_presentation_snapshots(refresh=False)
        rows: list[dict[str, Any]] = []
        for book in self.books:
            series_1h = ohlcv_store.get_candles_for_engine(
                book.symbol, "1h", include_open=False
            )
            series_4h = ohlcv_store.get_candles_for_engine(
                book.symbol, "4h", include_open=False
            )
            tip_iso = None
            if series_1h:
                tip_iso = _iso(candle_time(series_1h[-1]))
            with self._lock:
                wm = self._watermarks.get(book.symbol)
                seeded = book.symbol in self._seeded
                skip = self._skip_by_symbol.get(book.symbol) or {}
                waiting_next = bool(
                    seeded and tip_iso and wm and tip_iso <= wm and not self.replay_mode
                )
            if not history_ready(series_1h, series_4h):
                if len(series_1h or []) < _MIN_SETUP_BARS:
                    tip_status = "SYNCING_LIVE"
                    tip_reason = (
                        "1h history still catching up to the live tip — "
                        "this bar stays open and retries automatically"
                    )
                else:
                    tip_status = "SYNCING_LIVE"
                    tip_reason = (
                        "4h history still catching up to the live tip — "
                        "HTF delay does not skip the entry; it retries automatically"
                    )
            elif waiting_next:
                tip_status = "WAITING_NEXT_BAR"
                tip_reason = (
                    "Current 1h tip already watermarked — opens only on a newer closed bar"
                )
            elif seeded:
                tip_status = "WATCHING"
                tip_reason = "Watcher armed for next COMBO_02 LONG_ENTRY_CANDIDATE"
            else:
                tip_status = "SEEDING"
                tip_reason = "Waiting for first 1h tip seed"
            snap = snaps.get(book.symbol) or {}
            eval_result = snap.get("eval") if isinstance(snap.get("eval"), Mapping) else None
            htf = (eval_result or {}).get("htf") if isinstance(eval_result, Mapping) else {}
            htf = htf if isinstance(htf, Mapping) else {}
            rows.append(
                {
                    "symbol": book.symbol,
                    "timeframe": book.timeframe,
                    "tier": book.tier,
                    "risk_percent": book.risk_percent,
                    "seeded": seeded,
                    "watermark": wm,
                    "tip_bar": tip_iso,
                    "waiting_next_closed_bar": waiting_next,
                    "tip_status": tip_status,
                    "tip_reason": tip_reason,
                    "last_skip": skip.get("reason"),
                    "last_skip_bar": skip.get("bar"),
                    "gates": (eval_result or {}).get("gates") if eval_result else None,
                    "htf_alignment": htf.get("htf_alignment") or snap.get("htf_alignment"),
                    "trend_1h": htf.get("trend_1h") or snap.get("trend_1h"),
                    "trend_4h": htf.get("trend_4h") or snap.get("trend_4h"),
                    "eval_status": (eval_result or {}).get("status") if eval_result else None,
                    "last_evaluated_at_utc": snap.get("evaluated_at_utc"),
                    "would_open_on_new_bar": False,
                    "bars_1h": len(series_1h or []),
                    "bars_4h": len(series_4h or []),
                    "presentation_only": True,
                }
            )
        return rows

    def status(self, *, include_live: bool = False) -> dict[str, Any]:
        with self._lock:
            base = {
                "enabled": self.enabled,
                "timeframe": self.timeframe,
                "replay_mode": self.replay_mode,
                "secondary_enabled": self.secondary_enabled,
                "books": [
                    {
                        "symbol": b.symbol,
                        "timeframe": b.timeframe,
                        "tier": b.tier,
                        "risk_percent": b.risk_percent,
                    }
                    for b in self.books
                ],
                "seeded": sorted(self._seeded),
                "watermarks": dict(self._watermarks),
                "processed_bars": len(self._processed_bars),
                "last_skip": self._last_skip,
                "last_skip_by_symbol": {
                    sym: dict(skip) for sym, skip in self._skip_by_symbol.items()
                },
                "combo_id": COMBO_ID,
                "combo_version": COMBO_VERSION,
                "path": V1_PATH_LABEL,
                "label": "COMBO_02 v1 Paper Watcher / Telegram-eligible",
                "auto_executes_from": (
                    "Closed 1h bars on BTCUSDT / ETHUSDT / SOLUSDT via "
                    "evaluate_combination_at_bar (COMBO_02). "
                    "15m RESEARCH_15M may also open when legacy auto-entry is on "
                    "and PAPER_V1_WATCHER_OWNS_ENTRIES=false (separate labels)."
                ),
            }
        if include_live:
            try:
                base["live"] = self.peek_books()
            except Exception as exc:  # noqa: BLE001
                base["live"] = []
                base["live_error"] = str(exc)
        return base


_watcher: V1PaperWatcher | None = None


def get_v1_paper_watcher(**kwargs: Any) -> V1PaperWatcher:
    global _watcher
    if _watcher is None:
        _watcher = V1PaperWatcher(**kwargs)
    else:
        for key, value in kwargs.items():
            if hasattr(_watcher, key) and value is not None:
                setattr(_watcher, key, value)
    return _watcher


def reset_v1_paper_watcher_for_tests() -> None:
    global _watcher
    _watcher = None
    _PROCESS_SKIPS.clear()
