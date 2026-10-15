"""COMBO_02 V2.1-A paper watcher — same evaluator as Backtest, PAPER ONLY.

Uses ``evaluate_combination_at_bar`` / ``evaluate_combo02_v2_at_bar`` with
``COMBO_02_V2_1_A`` (VARIANT_A CHOPPY→WAIT). Never places real orders.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import structlog

from app.research.bos_combinations import get_combination
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.combo02_v21a_paper_constants import (
    COMBINATION_ID,
    COMBO_VERSION,
    DEFAULT_SYMBOLS,
    LIVE_EXECUTION_ALLOWED,
    MODE,
    SETUP_TIMEFRAME,
    SOURCE_WATCHER,
    STRATEGY_ID,
    STREAM_ID,
)
from app.research.config import ResearchConfig
from app.research.v1_production import normalize_symbol, normalize_timeframe
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig

logger = structlog.get_logger(__name__)

_MIN_SETUP_BARS = 50
_MAX_EVAL_BARS_1H = 300
_MAX_EVAL_BARS_4H = 250
_MAX_EVAL_BARS_15M = 400

_ENTRY_STATUSES = frozenset({"LONG_ENTRY_CANDIDATE", "SHORT_ENTRY_CANDIDATE"})


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
    return f"{sym}|{tf}|{stamp}|{STRATEGY_ID}"


def signal_idempotency_key(
    *,
    symbol: str,
    direction: str,
    setup_timestamp: str,
    entry_timestamp: str | None = None,
) -> str:
    """Persistent conceptual key for signal/trade dedupe."""
    return "|".join(
        [
            STRATEGY_ID,
            normalize_symbol(symbol),
            str(direction or "").upper(),
            str(setup_timestamp or ""),
            str(entry_timestamp or setup_timestamp or ""),
        ]
    )


def is_v21a_entry(result: Mapping[str, Any] | None) -> bool:
    """True only for actionable V2.1-A LONG/SHORT entry candidates."""
    if not result or not isinstance(result, Mapping):
        return False
    status = str(result.get("status") or "")
    if status not in _ENTRY_STATUSES:
        return False
    combo = str(result.get("combination_id") or "").upper()
    if combo and combo not in (COMBINATION_ID, "COMBO_02_V2_1A"):
        return False
    entry = result.get("entry_price")
    stop = result.get("stop_price")
    try:
        if entry is None or stop is None or float(entry) <= 0:
            return False
        if status == "LONG_ENTRY_CANDIDATE" and float(stop) >= float(entry):
            return False
        if status == "SHORT_ENTRY_CANDIDATE" and float(stop) <= float(entry):
            return False
    except (TypeError, ValueError):
        return False
    return True


def is_choppy_wait(result: Mapping[str, Any] | None) -> bool:
    if not result or not isinstance(result, Mapping):
        return False
    reason = str(result.get("reason") or "")
    regime = str(result.get("regime") or "")
    playbook = str(result.get("playbook") or "")
    diag = result.get("v2_diagnostics") or {}
    if not isinstance(diag, dict):
        diag = {}
    if "WAIT_CHOPPY" in reason or "CHOPPY_RANGE_DISABLED" in reason:
        return True
    if regime == "CHOPPY" and (
        playbook == "WAIT" or str(diag.get("status") or "") == "WAIT"
    ):
        return True
    return False


def classify_v21a_snippet(
    *,
    symbol: str,
    timeframe: str,
    risk_percent: float,
    eval_result: Mapping[str, Any],
    setup_bar_time_utc: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(eval_result.get("status") or "")
    direction = "LONG" if status == "LONG_ENTRY_CANDIDATE" else "SHORT"
    snip: dict[str, Any] = {
        "strategy_id": STRATEGY_ID,
        "source": SOURCE_WATCHER,
        "combo_id": COMBINATION_ID,
        "combo_version": COMBO_VERSION,
        "path": "V21A",
        "symbol": normalize_symbol(symbol),
        "timeframe": normalize_timeframe(timeframe) or SETUP_TIMEFRAME,
        "direction": direction,
        "mode": MODE,
        "stream": STREAM_ID,
        "risk_percent": float(risk_percent),
        "telegram_eligible": True,
        "production_approved": False,
        "live_execution": False,
        "setup_bar_time_utc": setup_bar_time_utc,
        "signal_id": signal_idempotency_key(
            symbol=symbol,
            direction=direction,
            setup_timestamp=setup_bar_time_utc,
        ),
        "regime": eval_result.get("regime"),
        "playbook": eval_result.get("playbook"),
        "event": eval_result.get("event"),
        "confirmation": eval_result.get("confirmation"),
    }
    if extra:
        snip.update(extra)
    snip["strategy_id"] = STRATEGY_ID
    snip["source"] = SOURCE_WATCHER
    snip["mode"] = MODE
    snip["live_execution"] = False
    snip["telegram_eligible"] = True
    return snip


class V21APaperWatcher:
    """Closed-candle paper watcher for COMBO_02_V2_1_A (PAPER ONLY)."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        symbols: Sequence[str] | None = None,
        timeframe: str = SETUP_TIMEFRAME,
        risk_percent: float | None = None,
        paper_engine: Any | None = None,
        signal_config: SignalConfig | None = None,
        research_config: ResearchConfig | None = None,
        emit_alerts: bool = True,
        replay_mode: bool = False,
    ) -> None:
        if LIVE_EXECUTION_ALLOWED:
            raise RuntimeError("v21a_live_execution_forbidden")
        self.enabled = bool(enabled)
        self.timeframe = normalize_timeframe(timeframe) or SETUP_TIMEFRAME
        self.symbols = tuple(
            normalize_symbol(s)
            for s in (symbols if symbols is not None else DEFAULT_SYMBOLS)
            if s
        )
        self._risk_percent_override = (
            float(risk_percent) if risk_percent is not None else None
        )
        self._paper = paper_engine
        self.signal_config = signal_config or SignalConfig()
        self.research_config = research_config or ResearchConfig()
        self.emit_alerts = bool(emit_alerts)
        self.replay_mode = bool(replay_mode)
        self._combo = get_combination(COMBINATION_ID)
        self._lock = threading.RLock()
        self._processed_bars: set[str] = set()
        self._processed_signals: set[str] = set()
        self._watermarks: dict[str, str] = {}
        self._seeded: set[str] = set()
        self._last_skip: str | None = None
        self._choppy_waits: int = 0
        self._signals_created: int = 0
        self._signals_deduped: int = 0
        self._last_daily_summary_date: str | None = None
        self._last_evaluation_at: str | None = None
        self._last_signal_at: str | None = None
        self._parity_mismatch_count: int = 0

    def _paper_engine(self) -> Any:
        if self._paper is not None:
            return self._paper
        from app.services.paper_trade import get_paper_trade_engine

        return get_paper_trade_engine()

    def _risk_percent(self) -> float:
        if self._risk_percent_override is not None and self._risk_percent_override > 0:
            return float(self._risk_percent_override)
        paper = self._paper_engine()
        return float(getattr(paper, "risk_percent", 0.02) or 0.02)

    def enable(self) -> None:
        from app.services.v21a_paper_validation import assert_paper_no_real_orders

        assert_paper_no_real_orders()
        mon = self._validation_monitor()
        if mon.halted:
            logger.error(
                "v21a_enable_blocked_safety_halt",
                strategy_id=STRATEGY_ID,
                reason=mon.status().get("safety_reason"),
            )
            return
        self.enabled = True
        logger.info(
            "v21a_paper_watcher_enabled",
            strategy_id=STRATEGY_ID,
            mode=MODE,
            real_execution="DISABLED",
        )

    def disable(self) -> None:
        self.enabled = False
        logger.info("v21a_paper_watcher_disabled", strategy_id=STRATEGY_ID, mode=MODE)

    def _validation_monitor(self) -> Any:
        from app.services.v21a_paper_validation import get_v21a_validation_monitor

        return get_v21a_validation_monitor()

    def seed_watermark(
        self,
        symbol: str,
        *,
        last_closed_bar_time: datetime | str | None,
    ) -> None:
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
        from app.services.ohlcv_store import ohlcv_store

        want = set(self.symbols)
        if symbols:
            want &= {normalize_symbol(s) for s in symbols}
        n = 0
        for sym in want:
            series = ohlcv_store.get_candles_for_engine(
                sym, self.timeframe, include_open=False
            )
            if not series:
                continue
            tip = candle_time(series[-1])
            self.seed_watermark(sym, last_closed_bar_time=tip)
            n += 1
        logger.info(
            "v21a_paper_watcher_seeded",
            strategy_id=STRATEGY_ID,
            seeded=n,
            symbols=len(want),
        )
        return n

    def evaluate_at_bar(
        self,
        symbol: str,
        *,
        candles_1h: Sequence[Mapping[str, Any]],
        candles_4h: Sequence[Mapping[str, Any]],
        candles_15m: Sequence[Mapping[str, Any]] | None = None,
        as_of_index: int | None = None,
    ) -> dict[str, Any]:
        """PIT-safe V2.1-A eval on fully closed candles only."""
        sym = normalize_symbol(symbol)
        series_1h = list(candles_1h or [])
        series_4h = list(candles_4h or [])
        series_15m = list(candles_15m or []) if candles_15m is not None else None
        if not series_1h:
            return {"status": "NO_SETUP", "reason": "missing_1h", "combination_id": COMBINATION_ID}
        if not series_4h:
            return {"status": "NO_SETUP", "reason": "missing_4h", "combination_id": COMBINATION_ID}
        if len(series_1h) < _MIN_SETUP_BARS:
            return {
                "status": "NO_SETUP",
                "reason": "insufficient_1h_history",
                "combination_id": COMBINATION_ID,
            }
        # Cap windows for live latency (same closed bars, truncated history).
        if len(series_1h) > _MAX_EVAL_BARS_1H:
            series_1h = series_1h[-_MAX_EVAL_BARS_1H:]
        if len(series_4h) > _MAX_EVAL_BARS_4H:
            series_4h = series_4h[-_MAX_EVAL_BARS_4H:]
        if series_15m is not None and len(series_15m) > _MAX_EVAL_BARS_15M:
            series_15m = series_15m[-_MAX_EVAL_BARS_15M:]
        idx = len(series_1h) - 1 if as_of_index is None else int(as_of_index)
        if idx < 0 or idx >= len(series_1h):
            return {
                "status": "NO_SETUP",
                "reason": "as_of_index_out_of_range",
                "combination_id": COMBINATION_ID,
            }
        result = evaluate_combination_at_bar(
            symbol=sym,
            timeframe=self.timeframe,
            candles=series_1h,
            as_of_index=idx,
            combination=self._combo,
            signal_config=self.signal_config,
            research_config=self.research_config,
            candles_1h=series_1h,
            candles_4h=series_4h,
            candles_15m=series_15m,
            compute_sd=False,
        )
        out = dict(result or {})
        out["combination_id"] = out.get("combination_id") or COMBINATION_ID
        out["strategy_id"] = STRATEGY_ID
        out["source"] = SOURCE_WATCHER
        out["mode"] = MODE
        out["live_execution"] = False
        return out

    def on_closed_1h(
        self,
        symbol: str,
        *,
        candles_1h: Sequence[Mapping[str, Any]] | None = None,
        candles_4h: Sequence[Mapping[str, Any]] | None = None,
        candles_15m: Sequence[Mapping[str, Any]] | None = None,
        force: bool = False,
    ) -> Any | None:
        from app.services.v21a_paper_validation import (
            assert_paper_no_real_orders,
            build_evaluation_audit,
            build_signal_snapshot,
            is_candle_fully_closed,
            replay_snapshot_through_evaluator,
        )

        mon = self._validation_monitor()
        mon.note_market_update()
        if mon.halted:
            self._last_skip = "safety_halted"
            return None
        if not self.enabled:
            self._last_skip = "watcher_disabled"
            return None
        try:
            assert_paper_no_real_orders()
        except RuntimeError as exc:
            mon.safety_halt(str(exc))
            self.enabled = False
            raise
        if LIVE_EXECUTION_ALLOWED:
            mon.safety_halt("LIVE_EXECUTION_ALLOWED")
            self.enabled = False
            raise RuntimeError("v21a_live_execution_forbidden")

        sym = normalize_symbol(symbol)
        if sym not in self.symbols:
            self._last_skip = f"{sym}:not_in_universe"
            return None
        if self.timeframe != "1h":
            self._last_skip = f"unsupported_tf:{self.timeframe}"
            return None

        from app.services.ohlcv_store import ohlcv_store

        # Closed candles only — never include forming tip as confirmed input.
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
        if not self.replay_mode and not force and (
            len(series_1h) < _MIN_SETUP_BARS
            or len(series_4h) < 20
            or len(series_15m) < 20
        ):
            self._last_skip = f"{sym}:ohlcv_catchup"
            return None
        if not series_1h:
            self._last_skip = f"{sym}:missing_1h"
            return None
        tip_idx = len(series_1h) - 1
        tip_bar = series_1h[tip_idx]
        tip_ts = candle_time(tip_bar)
        tip_iso = _iso(tip_ts) or ""
        key = bar_key(sym, self.timeframe, tip_iso)

        # Forming-candle guard (explicit is_closed=False or close time in future).
        if tip_bar.get("is_closed") is False or tip_bar.get("closed") is False:
            mon.note_forming_candle_block()
            self._last_skip = f"{sym}:forming_candle"
            logger.error(
                "v21a_forming_candle_rejected",
                strategy_id=STRATEGY_ID,
                symbol=sym,
                bar=tip_iso,
            )
            return None
        if (
            not self.replay_mode
            and tip_bar.get("is_closed") is not True
            and tip_bar.get("closed") is not True
            and not is_candle_fully_closed(tip_bar, "1h")
        ):
            mon.note_forming_candle_block()
            self._last_skip = f"{sym}:forming_candle_time"
            return None

        with self._lock:
            if not force and not self.replay_mode and sym not in self._seeded:
                self._watermarks[sym] = tip_iso
                self._processed_bars.add(key)
                self._seeded.add(sym)
                self._last_skip = f"{sym}:seeded_no_backlog"
                logger.info(
                    "v21a_watcher_seeded",
                    strategy_id=STRATEGY_ID,
                    symbol=sym,
                    bar=tip_iso,
                )
                return None
            if key in self._processed_bars and not force:
                self._last_skip = f"{sym}:already_processed"
                self._signals_deduped += 1
                logger.info(
                    "signal_deduplicated",
                    strategy_id=STRATEGY_ID,
                    symbol=sym,
                    signal_id=key,
                    reason="bar_already_processed",
                )
                return None
            wm = self._watermarks.get(sym)
            if (
                not force
                and not self.replay_mode
                and wm
                and tip_iso
                and tip_iso <= wm
            ):
                self._processed_bars.add(key)
                self._last_skip = f"{sym}:not_newer_than_watermark"
                return None

        # Same gate as the v1 watcher. Do not watermark: the same bar is
        # retried once the store heals.
        from app.services.v1_paper_watcher import series_integrity_fault

        fault = series_integrity_fault(series_1h, series_4h)
        if fault:
            self._last_skip = f"{sym}:data_integrity:{fault}"
            logger.warning(
                "v21a_watcher_data_integrity",
                strategy_id=STRATEGY_ID,
                symbol=sym,
                bar=tip_iso,
                fault=fault,
            )
            return None

        result = self.evaluate_at_bar(
            symbol=sym,
            candles_1h=series_1h,
            candles_4h=series_4h,
            candles_15m=series_15m or None,
            as_of_index=tip_idx,
        )
        eval_ts = datetime.now(timezone.utc).isoformat()
        self._last_evaluation_at = eval_ts

        tip_4h = series_4h[-1] if series_4h else None
        tip_15m = series_15m[-1] if series_15m else None
        audit = build_evaluation_audit(
            symbol=sym,
            tip_1h=tip_bar,
            tip_4h=tip_4h,
            tip_15m=tip_15m,
            result=result,
            evaluation_timestamp=eval_ts,
        )
        mon.record_evaluation(audit)

        with self._lock:
            self._processed_bars.add(key)
            if tip_iso:
                self._watermarks[sym] = tip_iso
            self._seeded.add(sym)

        if is_choppy_wait(result):
            self._choppy_waits += 1
            self._last_skip = f"{sym}:CHOPPY_WAIT"
            logger.info(
                "v21a_choppy_wait",
                strategy_id=STRATEGY_ID,
                symbol=sym,
                reason=result.get("reason"),
                mode=MODE,
                decision="WAIT",
            )
            return None

        if not is_v21a_entry(result):
            self._last_skip = (
                f"{sym}:no_entry:{result.get('reason') or result.get('status')}"
            )
            return None

        direction = (
            "LONG"
            if str(result.get("status")) == "LONG_ENTRY_CANDIDATE"
            else "SHORT"
        )
        sig_id = signal_idempotency_key(
            symbol=sym,
            direction=direction,
            setup_timestamp=tip_iso,
            entry_timestamp=str(result.get("signal_time") or tip_iso),
        )
        audit["signal_id"] = sig_id
        with self._lock:
            if sig_id in self._processed_signals and not force:
                self._signals_deduped += 1
                self._last_skip = f"{sym}:signal_deduped"
                logger.info(
                    "signal_deduplicated",
                    strategy_id=STRATEGY_ID,
                    symbol=sym,
                    signal_id=sig_id,
                )
                return None
            self._processed_signals.add(sig_id)

        paper = self._paper_engine()
        if hasattr(paper, "has_open") and paper.has_open(sym, STREAM_ID):
            self._last_skip = f"{sym}:already_open"
            return None

        # Live↔backtest parity replay on the same closed-candle window.
        try:
            parity = replay_snapshot_through_evaluator(
                symbol=sym,
                candles_1h=series_1h,
                candles_4h=series_4h,
                candles_15m=series_15m or None,
                as_of_index=tip_idx,
                live_decision="SIGNAL",
                live_result=result,
                signal_id=sig_id,
            )
            mon.record_parity(parity)
            if parity.get("event") == "PARITY_MISMATCH":
                self._parity_mismatch_count += 1
                logger.error(
                    "v21a_parity_mismatch_on_signal",
                    strategy_id=STRATEGY_ID,
                    symbol=sym,
                    signal_id=sig_id,
                    classification=parity.get("classification"),
                    live_decision=parity.get("live_decision"),
                    backtest_decision=parity.get("backtest_decision"),
                )
                # Diagnostics only — do not block paper open / alter strategy.
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "v21a_parity_replay_failed",
                symbol=sym,
                signal_id=sig_id,
                error=str(exc),
            )

        snap = build_signal_snapshot(
            symbol=sym,
            tip_1h=tip_bar,
            tip_4h=tip_4h,
            tip_15m=tip_15m,
            result=result,
            signal_id=sig_id,
            direction=direction,
        )
        mon.record_snapshot(snap)
        self._last_signal_at = snap.get("signal_timestamp")

        risk_pct = self._risk_percent()
        snip = classify_v21a_snippet(
            symbol=sym,
            timeframe=self.timeframe,
            risk_percent=risk_pct,
            eval_result=result,
            setup_bar_time_utc=tip_iso,
            extra={
                "signal_id": sig_id,
                "validation_snapshot": {
                    "1h_candle_timestamp": snap.get("1h_candle_timestamp"),
                    "4h_candle_timestamp": snap.get("4h_candle_timestamp"),
                    "15m_candle_timestamp": snap.get("15m_candle_timestamp"),
                    "regime": snap.get("regime"),
                },
            },
        )
        logger.info(
            "signal_created",
            strategy_id=STRATEGY_ID,
            symbol=sym,
            signal_id=sig_id,
            direction=direction,
            mode=MODE,
        )
        self._signals_created += 1

        emit = bool(self.emit_alerts) and not self.replay_mode
        try:
            pos = paper.open_v21a_position(
                symbol=sym,
                timeframe=self.timeframe,
                eval_result=result,
                risk_percent=risk_pct,
                signal_snippet=snip,
                setup_bar_time_utc=tip_iso,
                replay=bool(self.replay_mode),
                emit_alert=emit,
            )
        except Exception as exc:  # noqa: BLE001
            # Never let alert/open side effects crash the eval loop.
            logger.error(
                "v21a_paper_open_exception",
                symbol=sym,
                signal_id=sig_id,
                error=str(exc),
            )
            mon.record_telegram_status(sig_id, "FAILED")
            return None

        if pos is None:
            self._last_skip = f"{sym}:paper_open_skipped:{getattr(paper, '_last_skip_reason', None)}"
            if emit:
                mon.record_telegram_status(sig_id, "FAILED")
            return None
        if emit:
            mon.record_telegram_status(getattr(pos, "id", sig_id), "SENT")
        return pos

    def maybe_daily_summary(self, *, force: bool = False) -> dict[str, Any] | None:
        """Optional once-per-UTC-day paper summary (Telegram if subscriber live)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if not force and self._last_daily_summary_date == today:
                return None
            self._last_daily_summary_date = today

        paper = self._paper_engine()
        if hasattr(paper, "positions"):
            snap = paper.positions(closed_limit=500)
        elif hasattr(paper, "snapshot"):
            snap = paper.snapshot()
        else:
            snap = {"open": [], "closed": []}

        def _is_v21a(row: dict[str, Any]) -> bool:
            snip = row.get("signal_snippet") or {}
            return str(snip.get("strategy_id") or "") == STRATEGY_ID

        def _side(row: dict[str, Any]) -> str:
            snip = row.get("signal_snippet") or {}
            return str(row.get("side") or snip.get("direction") or "").upper()

        open_rows = [p for p in (snap.get("open") or []) if _is_v21a(p)]
        closed_all = [p for p in (snap.get("closed") or []) if _is_v21a(p)]
        closed_today = [
            p for p in closed_all if str(p.get("closed_at") or "").startswith(today)
        ]
        opened_today = [
            p
            for p in open_rows
            if str(p.get("opened_at") or "").startswith(today)
        ] + [
            p
            for p in closed_all
            if str(p.get("opened_at") or "").startswith(today)
        ]

        wins = sum(1 for p in closed_today if float(p.get("r_multiple") or 0) > 0)
        losses = sum(1 for p in closed_today if float(p.get("r_multiple") or 0) <= 0)
        realized_r = sum(float(p.get("r_multiple") or 0) for p in closed_today)
        cumulative_r = sum(float(p.get("r_multiple") or 0) for p in closed_all)
        long_n = sum(1 for p in opened_today if _side(p) == "LONG")
        short_n = sum(1 for p in opened_today if _side(p) == "SHORT")
        # VARIANT_A: CHOPPY → WAIT — choppy must never produce a paper trade.
        choppy_trades = sum(
            1
            for p in opened_today
            if str((p.get("signal_snippet") or {}).get("regime") or "").upper()
            == "CHOPPY"
        )
        mon = self._validation_monitor()
        mon_st = mon.status()
        parity_n = int(mon_st.get("parity_mismatches") or self._parity_mismatch_count or 0)
        signals_n = int(self._signals_created)
        opened_n = len(opened_today)
        closed_n = len(closed_today)
        open_n = len(open_rows)

        from app.services.v21a_paper_validation import paper_performance_summary

        perf = paper_performance_summary(paper)
        summary = {
            "date": today,
            "signals": signals_n,
            "opened": opened_n,
            "closed": closed_n,
            "wins": wins,
            "losses": losses,
            "open": open_n,
            "realized_r": realized_r,
            "cumulative_r": cumulative_r,
            "long": long_n,
            "short": short_n,
            "parity_mismatches": parity_n,
            "choppy_trades": choppy_trades,
            "choppy_waits": self._choppy_waits,
            "mode": MODE,
            "strategy_id": STRATEGY_ID,
            "label": "OBSERVATION",
            "performance": perf,
        }
        text = (
            f"📊 V2.1-A PAPER — DAILY\n\n"
            f"Signals: {signals_n}\n"
            f"Opened: {opened_n}\n"
            f"Closed: {closed_n}\n\n"
            f"Wins: {wins}\n"
            f"Losses: {losses}\n"
            f"Open: {open_n}\n\n"
            f"Realized: {realized_r:+.1f}R\n"
            f"Cumulative: {cumulative_r:+.1f}R\n\n"
            f"LONG: {long_n}\n"
            f"SHORT: {short_n}\n\n"
            f"Parity mismatches: {parity_n}\n"
            f"CHOPPY trades: {choppy_trades}\n\n"
            f"Mode: PAPER ONLY"
        )
        try:
            from app.services.telegram_alerts import get_telegram_alert_subscriber

            sub = get_telegram_alert_subscriber()
            if sub is not None and getattr(sub, "enabled", False):
                send = getattr(sub, "send_text", None) or getattr(sub, "_send", None)
                if callable(send):
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(send(text))
                    except RuntimeError:
                        pass
        except Exception:  # noqa: BLE001
            logger.warning("v21a_daily_summary_telegram_failed", strategy_id=STRATEGY_ID)
        logger.info("v21a_daily_summary", **{k: v for k, v in summary.items() if k != "performance"})
        return {**summary, "text": text}

    def status(self) -> dict[str, Any]:
        mon = self._validation_monitor()
        mon_st = mon.status()
        paper = self._paper_engine()
        open_count = 0
        try:
            if hasattr(paper, "positions"):
                open_count = sum(
                    1
                    for p in (paper.positions(closed_limit=1).get("open") or [])
                    if str((p.get("signal_snippet") or {}).get("strategy_id") or "")
                    == STRATEGY_ID
                )
            elif hasattr(paper, "snapshot"):
                open_count = sum(
                    1
                    for p in (paper.snapshot().get("open") or [])
                    if str((p.get("signal_snippet") or {}).get("strategy_id") or "")
                    == STRATEGY_ID
                )
        except Exception:  # noqa: BLE001
            open_count = 0
        with self._lock:
            running = bool(self.enabled) and not mon.halted
            return {
                "status": "RUNNING" if running else ("HALTED" if mon.halted else "STOPPED"),
                "enabled": self.enabled,
                "strategy_id": STRATEGY_ID,
                "strategy": STRATEGY_ID,
                "combination_id": COMBINATION_ID,
                "combo_version": COMBO_VERSION,
                "mode": MODE,
                "live_execution_allowed": LIVE_EXECUTION_ALLOWED,
                "real_execution": "DISABLED",
                "timeframe": self.timeframe,
                "symbols": list(self.symbols),
                "risk_percent": self._risk_percent(),
                "processed_bars": len(self._processed_bars),
                "processed_signals": len(self._processed_signals),
                "signals_created": self._signals_created,
                "signals_deduped": self._signals_deduped,
                "choppy_waits": self._choppy_waits,
                "parity_mismatches": self._parity_mismatch_count,
                "last_skip": self._last_skip,
                "last_evaluation": self._last_evaluation_at or mon_st.get("last_evaluation"),
                "last_signal": self._last_signal_at or mon_st.get("last_signal"),
                "last_market_update": mon_st.get("last_market_update"),
                "open_paper_positions": open_count,
                "seeded": sorted(self._seeded),
                "watermarks": dict(self._watermarks),
                "validation": mon_st,
                "label": "OBSERVATION",
            }


_watcher: V21APaperWatcher | None = None
_watcher_lock = threading.Lock()


def get_v21a_paper_watcher(**kwargs: Any) -> V21APaperWatcher:
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = V21APaperWatcher(**kwargs)
        elif kwargs:
            # Allow runtime toggles without replacing the singleton mid-flight.
            if "enabled" in kwargs:
                _watcher.enabled = bool(kwargs["enabled"])
            if "emit_alerts" in kwargs:
                _watcher.emit_alerts = bool(kwargs["emit_alerts"])
            if "replay_mode" in kwargs:
                _watcher.replay_mode = bool(kwargs["replay_mode"])
            if "risk_percent" in kwargs and kwargs["risk_percent"] is not None:
                _watcher._risk_percent_override = float(kwargs["risk_percent"])
            if "symbols" in kwargs and kwargs["symbols"] is not None:
                _watcher.symbols = tuple(
                    normalize_symbol(s) for s in kwargs["symbols"] if s
                )
            if "paper_engine" in kwargs and kwargs["paper_engine"] is not None:
                _watcher._paper = kwargs["paper_engine"]
        return _watcher


def reset_v21a_paper_watcher_for_tests() -> None:
    global _watcher
    with _watcher_lock:
        _watcher = None
