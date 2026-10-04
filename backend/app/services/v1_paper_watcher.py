"""COMBO_02 v1 paper watcher — same gate path as validated 1h backtest.

Evaluates closed 1h bars for enabled v1 books via ``evaluate_combination_at_bar``
(COMBO_02). Does not reimplement trend/BOS/HTF. Screener / Path B / 15m setups
are out of scope and must not open or label COMBO_02 v1 trades.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import structlog

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import _bos_break_possible
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
from app.signals.swing_detector import detect_swings, extend_swings

logger = structlog.get_logger(__name__)

V1_PATH_LABEL = "A"
DEFAULT_SETUP_TF = "1h"
_MIN_SETUP_BARS = 50
# Cap eval windows — full DB-hydrated histories (500+) block the asyncio loop
# when evaluate_combination_at_bar runs synchronously on paper/status polls.
_MAX_EVAL_BARS_1H = 250
_MAX_EVAL_BARS_4H = 200


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


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
    if str(result.get("direction") or "").upper() != "LONG":
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
        # Mirror combination_backtest sticky-break skip (same broken level).
        self._prev_break: dict[str, bool] = {}
        self._prev_swing_n: dict[str, int] = {}
        self._swing_state: dict[str, list[Any]] = {}
        # Read-only UI snapshots (never open trades). symbol -> payload.
        self._presentation_snapshots: dict[str, dict[str, Any]] = {}
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
            if not closed:
                self.seed_watermark(sym, last_closed_bar_time=None)
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
    ) -> bool:
        """Same sticky-break skip as ``run_combination_backtest`` (COMBO_02)."""
        sym = normalize_symbol(symbol)
        series = list(candles)
        if reset_state or sym not in self._swing_state:
            swing_cfg = self.signal_config.swing_for(self.timeframe)
            _, highs_arr, lows_arr, closes_arr, _ = series_ohlcv(series)
            seed_idx = max(as_of_index - 1, 0)
            self._swing_state[sym] = detect_swings(
                series,
                left=swing_cfg.swing_left_bars,
                right=swing_cfg.swing_right_bars,
                symbol=sym,
                timeframe=self.timeframe,
                atr_period=self.signal_config.atr_period,
                minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
                as_of_index=seed_idx,
                highs=highs_arr,
                lows=lows_arr,
                closes=closes_arr,
            )
            self._prev_break[sym] = False
            self._prev_swing_n[sym] = len(self._swing_state[sym])

        swing_cfg = self.signal_config.swing_for(self.timeframe)
        _, highs_arr, lows_arr, closes_arr, _ = series_ohlcv(series)
        swings = extend_swings(
            self._swing_state[sym],
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
        swing_grew = len(swings) != self._prev_swing_n.get(sym, -1)
        self._prev_swing_n[sym] = len(swings)
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
        if not series_1h:
            self._last_skip = f"{book.symbol}:missing_1h"
            return None
        tip_idx = len(series_1h) - 1
        tip_ts = candle_time(series_1h[tip_idx])
        tip_iso = _iso(tip_ts)
        key = bar_key(book.symbol, self.timeframe, tip_iso)

        with self._lock:
            if not force and not self.replay_mode and book.symbol not in self._seeded:
                # First live sighting: seed only (no historical Telegram backlog).
                self._watermarks[book.symbol] = tip_iso or ""
                self._processed_bars.add(key)
                self._seeded.add(book.symbol)
                self._last_skip = f"{book.symbol}:seeded_no_backlog"
                logger.info(
                    "v1_watcher_seeded",
                    symbol=book.symbol,
                    timeframe=self.timeframe,
                    bar=tip_iso,
                )
                return None
            if key in self._processed_bars and not force:
                self._last_skip = f"{book.symbol}:already_processed"
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
                self._last_skip = f"{book.symbol}:not_newer_than_watermark"
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

        if not self._bos_break_allows_entry(book.symbol, series_1h, tip_idx):
            self._last_skip = f"{book.symbol}:sticky_bos_skip"
            return None

        if not is_v1_long_entry(result):
            self._last_skip = (
                f"{book.symbol}:gates_fail:{result.get('reason') or result.get('status')}"
            )
            return None

        paper = self._paper_engine()
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
            self._last_skip = f"{book.symbol}:paper_open_skipped"
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
        try:
            for i in range(begin, len(series)):
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
                        if before and not after:
                            # Match backtest: clear sticky break after exit
                            self._prev_break[book.symbol] = False

                tip_ts = candle_time(series[i])
                tip_iso = _iso(tip_ts)
                key = bar_key(book.symbol, self.timeframe, tip_iso)
                with self._lock:
                    if key in self._processed_bars:
                        continue

                # Advance swing/break state every bar (even while in a trade)
                allows = self._bos_break_allows_entry(book.symbol, series, i)
                with self._lock:
                    self._processed_bars.add(key)
                    if tip_iso:
                        self._watermarks[book.symbol] = tip_iso

                already_v1 = (
                    paper.has_open(book.symbol, "V1")
                    if hasattr(paper, "has_open")
                    else book.symbol in getattr(paper, "_open", {})
                )
                if already_v1:
                    continue
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
            return self.refresh_presentation_snapshots(force=False)
        with self._lock:
            return {k: dict(v) for k, v in self._presentation_snapshots.items()}

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
                waiting_next = bool(
                    seeded and tip_iso and wm and tip_iso <= wm and not self.replay_mode
                )
            if not series_1h:
                tip_status = "NO_1H_DATA"
                tip_reason = "No closed 1h candles in ohlcv_store — watcher cannot evaluate"
            elif not series_4h:
                tip_status = "NO_4H_DATA"
                tip_reason = "Missing 4h history — COMBO_02 HTF gate cannot pass"
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
