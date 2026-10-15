"""COMBO_02 V2.1-A paper validation / live↔backtest observability.

Diagnostics only. Does not alter VARIANT_A strategy logic, risk, or execution.
PAPER mode only — real exchange orders remain forbidden.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import structlog

from app.research.combo02_v21a_paper_constants import (
    COMBINATION_ID,
    LIVE_EXECUTION_ALLOWED,
    MODE,
    SOURCE_WATCHER,
    STRATEGY_ID,
)
from app.signals._candle_utils import candle_time

logger = structlog.get_logger(__name__)

TF_MINUTES = {"15m": 15, "1h": 60, "4h": 240}

PARITY_MATCH = "PARITY_MATCH"
PARITY_MISMATCH = "PARITY_MISMATCH"

CLASS_DATA = "DATA_MISMATCH"
CLASS_TIMESTAMP = "TIMESTAMP_MISMATCH"
CLASS_CANDLE_CLOSURE = "CANDLE_CLOSURE_MISMATCH"
CLASS_HTF = "HTF_ALIGNMENT_MISMATCH"
CLASS_15M = "15M_ALIGNMENT_MISMATCH"
CLASS_STATE = "STATE_PERSISTENCE_MISMATCH"
CLASS_STRATEGY = "STRATEGY_EVALUATION_MISMATCH"
CLASS_UNKNOWN = "UNKNOWN"

_MAX_EVALS = 200
_MAX_SNAPSHOTS = 50
_MAX_MISMATCHES = 100


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def assert_paper_no_real_orders() -> None:
    """Hard safety: V2.1-A + PAPER must never allow live execution."""
    if LIVE_EXECUTION_ALLOWED:
        raise RuntimeError("v21a_safety_violation: LIVE_EXECUTION_ALLOWED is True")
    if MODE != "PAPER":
        raise RuntimeError(f"v21a_safety_violation: MODE={MODE} expected PAPER")


def candle_close_timestamp(candle: Mapping[str, Any] | None, timeframe: str) -> datetime | None:
    """Open-time + TF duration = expected close instant (display/audit only)."""
    open_ts = candle_time(candle) if candle else None
    if open_ts is None:
        return None
    mins = TF_MINUTES.get(str(timeframe or "").lower())
    if mins is None:
        return None
    return open_ts + timedelta(minutes=mins)


def is_candle_fully_closed(
    candle: Mapping[str, Any] | None,
    timeframe: str,
    *,
    now: datetime | None = None,
) -> bool:
    """True when candle is marked closed or its close time is in the past."""
    if not candle:
        return False
    if candle.get("is_closed") is True or candle.get("closed") is True:
        return True
    if candle.get("is_closed") is False or candle.get("closed") is False:
        return False
    close_ts = candle_close_timestamp(candle, timeframe)
    if close_ts is None:
        # Unknown close — fail closed for validation (caller may still use store tips).
        return False
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return close_ts <= ref


def decision_from_result(result: Mapping[str, Any] | None) -> str:
    """Map evaluator output to WAIT / SIGNAL / INVALIDATED."""
    from app.services.v21a_paper_watcher import is_choppy_wait, is_v21a_entry

    if not result:
        return "WAIT"
    if is_v21a_entry(result):
        return "SIGNAL"
    status = str(result.get("status") or "").upper()
    reason = str(result.get("reason") or "").upper()
    if "INVALID" in status or "INVALID" in reason:
        return "INVALIDATED"
    if is_choppy_wait(result):
        return "WAIT"
    return "WAIT"


def build_evaluation_audit(
    *,
    symbol: str,
    tip_1h: Mapping[str, Any] | None,
    tip_4h: Mapping[str, Any] | None,
    tip_15m: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    signal_id: str | None = None,
    evaluation_timestamp: str | None = None,
) -> dict[str, Any]:
    """Lightweight V21A_EVALUATION record (structured log + ring buffer)."""
    eval_ts = evaluation_timestamp or _now_iso()
    c1 = candle_time(tip_1h) if tip_1h else None
    c4 = candle_time(tip_4h) if tip_4h else None
    c15 = candle_time(tip_15m) if tip_15m else None
    decision = decision_from_result(result)
    regime = result.get("regime")
    reason = result.get("reason") or result.get("status")
    return {
        "event": "V21A_EVALUATION",
        "strategy_id": STRATEGY_ID,
        "mode": MODE,
        "symbol": str(symbol or "").upper(),
        "timestamp": eval_ts,
        "1h_candle": _iso(c1),
        "1h_candle_close": _iso(candle_close_timestamp(tip_1h, "1h")),
        "4h_context": _iso(c4),
        "15m_context": _iso(c15),
        "regime": regime,
        "playbook": result.get("playbook"),
        "decision": decision,
        "reason": reason,
        "signal_id": signal_id,
        "live_execution": False,
    }


def build_signal_snapshot(
    *,
    symbol: str,
    tip_1h: Mapping[str, Any] | None,
    tip_4h: Mapping[str, Any] | None,
    tip_15m: Mapping[str, Any] | None,
    result: Mapping[str, Any],
    signal_id: str,
    direction: str,
) -> dict[str, Any]:
    """Capture live evaluator fields for backtest replay — no new strategy math."""
    diag = result.get("v2_diagnostics") if isinstance(result.get("v2_diagnostics"), dict) else {}
    targets = result.get("targets") or []
    tp1 = result.get("tp1")
    if tp1 is None and targets and isinstance(targets[0], dict):
        tp1 = targets[0].get("target_price")
    return {
        "event": "V21A_SIGNAL_SNAPSHOT",
        "strategy_id": STRATEGY_ID,
        "combination_id": COMBINATION_ID,
        "source": SOURCE_WATCHER,
        "mode": MODE,
        "signal_id": signal_id,
        "symbol": str(symbol or "").upper(),
        "direction": str(direction or "").upper(),
        "signal_timestamp": _now_iso(),
        "1h_candle_timestamp": _iso(candle_time(tip_1h) if tip_1h else None),
        "4h_candle_timestamp": _iso(candle_time(tip_4h) if tip_4h else None),
        "15m_candle_timestamp": _iso(candle_time(tip_15m) if tip_15m else None),
        "regime": result.get("regime"),
        "playbook": result.get("playbook"),
        "event_detail": result.get("event") or diag.get("event"),
        "structure_state": diag.get("structure") or result.get("structure"),
        "sweep_state": diag.get("sweep") or result.get("sweep"),
        "bos_choch_state": diag.get("bos") or result.get("bos") or result.get("confirmation"),
        "entry": result.get("entry_price"),
        "stop": result.get("stop_price"),
        "target": tp1,
        "decision": "SIGNAL",
        "status": result.get("status"),
        "reason": result.get("reason"),
        "live_decision": decision_from_result(result),
    }


def classify_parity_mismatch(
    *,
    live_decision: str,
    backtest_decision: str,
    live_result: Mapping[str, Any] | None,
    backtest_result: Mapping[str, Any] | None,
    live_1h_ts: str | None,
    backtest_1h_ts: str | None,
) -> str:
    if live_decision == backtest_decision:
        return PARITY_MATCH
    if live_1h_ts and backtest_1h_ts and live_1h_ts != backtest_1h_ts:
        return CLASS_TIMESTAMP
    lr = live_result or {}
    br = backtest_result or {}
    if str(lr.get("regime") or "") != str(br.get("regime") or ""):
        return CLASS_DATA
    live_reason = str(lr.get("reason") or "")
    bt_reason = str(br.get("reason") or "")
    if "15M" in live_reason or "15M" in bt_reason:
        return CLASS_15M
    if "HTF" in live_reason or "HTF" in bt_reason:
        return CLASS_HTF
    if "CHOPPY" in live_reason or "CHOPPY" in bt_reason:
        return CLASS_STRATEGY
    if str(lr.get("status") or "") != str(br.get("status") or ""):
        return CLASS_STRATEGY
    return CLASS_UNKNOWN


def replay_snapshot_through_evaluator(
    *,
    symbol: str,
    candles_1h: Sequence[Mapping[str, Any]],
    candles_4h: Sequence[Mapping[str, Any]],
    candles_15m: Sequence[Mapping[str, Any]] | None,
    as_of_index: int | None = None,
    live_decision: str,
    live_result: Mapping[str, Any] | None,
    signal_id: str | None = None,
) -> dict[str, Any]:
    """Re-run the same closed-candle state through the shared V2.1-A evaluator."""
    from app.research.bos_combinations import get_combination
    from app.research.combination_engine import evaluate_combination_at_bar
    from app.research.config import ResearchConfig
    from app.signals.config import SignalConfig

    series_1h = list(candles_1h or [])
    series_4h = list(candles_4h or [])
    series_15m = list(candles_15m or []) if candles_15m is not None else None
    if not series_1h:
        return {
            "event": PARITY_MISMATCH,
            "classification": CLASS_DATA,
            "live_decision": live_decision,
            "backtest_decision": "WAIT",
            "reason": "missing_1h_for_replay",
        }
    idx = len(series_1h) - 1 if as_of_index is None else int(as_of_index)
    tip = series_1h[idx] if 0 <= idx < len(series_1h) else None
    tip_iso = _iso(candle_time(tip) if tip else None)

    # Closed-candle guard for replay inputs.
    if tip is not None and not is_candle_fully_closed(tip, "1h"):
        return {
            "event": PARITY_MISMATCH,
            "classification": CLASS_CANDLE_CLOSURE,
            "symbol": str(symbol).upper(),
            "timestamp": tip_iso,
            "live_decision": live_decision,
            "backtest_decision": "WAIT",
            "reason": "forming_candle_used_as_confirmation",
            "signal_id": signal_id,
        }

    combo = get_combination(COMBINATION_ID)
    bt = evaluate_combination_at_bar(
        symbol=str(symbol).upper(),
        timeframe="1h",
        candles=series_1h,
        as_of_index=idx,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=series_1h,
        candles_4h=series_4h,
        candles_15m=series_15m,
        compute_sd=False,
    )
    bt = dict(bt or {})
    bt_decision = decision_from_result(bt)
    match = live_decision == bt_decision
    classification = classify_parity_mismatch(
        live_decision=live_decision,
        backtest_decision=bt_decision,
        live_result=live_result,
        backtest_result=bt,
        live_1h_ts=tip_iso,
        backtest_1h_ts=_iso(candle_time(series_1h[idx]) if series_1h else None),
    )
    out = {
        "event": PARITY_MATCH if match else PARITY_MISMATCH,
        "classification": classification if not match else PARITY_MATCH,
        "strategy_id": STRATEGY_ID,
        "symbol": str(symbol).upper(),
        "timestamp": tip_iso,
        "live_decision": live_decision,
        "backtest_decision": bt_decision,
        "live_status": (live_result or {}).get("status"),
        "backtest_status": bt.get("status"),
        "live_reason": (live_result or {}).get("reason"),
        "backtest_reason": bt.get("reason"),
        "1h_candle_timestamp": tip_iso,
        "signal_id": signal_id,
        "mode": MODE,
        "observation": True,
    }
    if match:
        logger.info(
            "v21a_parity_match",
            symbol=out.get("symbol"),
            timestamp=out.get("timestamp"),
            live_decision=out.get("live_decision"),
            signal_id=out.get("signal_id"),
        )
    else:
        logger.error(
            "v21a_parity_mismatch",
            classification=out.get("classification"),
            symbol=out.get("symbol"),
            timestamp=out.get("timestamp"),
            live_decision=out.get("live_decision"),
            backtest_decision=out.get("backtest_decision"),
            live_status=out.get("live_status"),
            backtest_status=out.get("backtest_status"),
            signal_id=out.get("signal_id"),
        )
    return out


def _paper_rows(paper: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if hasattr(paper, "positions"):
        data = paper.positions(closed_limit=500)
        return list(data.get("open") or []), list(data.get("closed") or [])
    if hasattr(paper, "snapshot"):
        snap = paper.snapshot()
        return list(snap.get("open") or []), list(snap.get("closed") or [])
    return [], []


def paper_performance_summary(paper: Any) -> dict[str, Any]:
    """Aggregate V2.1-A paper ledger stats — observation label only."""
    open_all, closed_all = _paper_rows(paper)
    open_rows = [
        p
        for p in open_all
        if str((p.get("signal_snippet") or {}).get("strategy_id") or "") == STRATEGY_ID
    ]
    closed = [
        p
        for p in closed_all
        if str((p.get("signal_snippet") or {}).get("strategy_id") or "") == STRATEGY_ID
    ]
    wins = [p for p in closed if float(p.get("r_multiple") or 0) > 0]
    losses = [p for p in closed if float(p.get("r_multiple") or 0) <= 0]
    total_r = sum(float(p.get("r_multiple") or 0) for p in closed)
    avg_r = (total_r / len(closed)) if closed else 0.0
    gross_win = sum(float(p.get("r_multiple") or 0) for p in wins)
    gross_loss = abs(sum(float(p.get("r_multiple") or 0) for p in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (None if not wins else float("inf"))
    # Simple equity-curve DD in R
    peak = 0.0
    eq = 0.0
    max_dd = 0.0
    for p in sorted(closed, key=lambda x: str(x.get("closed_at") or "")):
        eq += float(p.get("r_multiple") or 0)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)

    by_symbol: dict[str, float] = {}
    by_regime: dict[str, float] = {}
    long_r = 0.0
    short_r = 0.0
    for p in closed:
        sym = str(p.get("symbol") or "").upper()
        r = float(p.get("r_multiple") or 0)
        by_symbol[sym] = by_symbol.get(sym, 0.0) + r
        snip = p.get("signal_snippet") or {}
        regime = str(snip.get("regime") or "UNKNOWN")
        by_regime[regime] = by_regime.get(regime, 0.0) + r
        side = str(p.get("side") or snip.get("direction") or "").upper()
        if side == "SHORT":
            short_r += r
        else:
            long_r += r

    n = len(closed)
    return {
        "label": "OBSERVATION",
        "strategy_id": STRATEGY_ID,
        "mode": MODE,
        "paper_trades": n,
        "open_positions": len(open_rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n * 100.0) if n else 0.0,
        "total_r": total_r,
        "average_r": avg_r,
        "profit_factor": pf,
        "max_drawdown_r": max_dd,
        "long_r": long_r,
        "short_r": short_r,
        "by_symbol": by_symbol,
        "by_regime": by_regime,
    }


class V21AValidationMonitor:
    """In-memory ring buffers for eval audits, snapshots, parity mismatches."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._evals: deque[dict[str, Any]] = deque(maxlen=_MAX_EVALS)
        self._snapshots: deque[dict[str, Any]] = deque(maxlen=_MAX_SNAPSHOTS)
        self._mismatches: deque[dict[str, Any]] = deque(maxlen=_MAX_MISMATCHES)
        self._telegram_status: dict[str, str] = {}  # signal/trade id -> SENT|FAILED|RETRYING
        self._last_evaluation: dict[str, Any] | None = None
        self._last_signal: dict[str, Any] | None = None
        self._last_market_update: str | None = None
        self._parity_mismatch_count = 0
        self._parity_match_count = 0
        self._safety_halted = False
        self._safety_reason: str | None = None
        self._forming_candle_blocks = 0

    def note_market_update(self) -> None:
        with self._lock:
            self._last_market_update = _now_iso()

    def record_evaluation(self, audit: dict[str, Any]) -> None:
        with self._lock:
            self._evals.append(audit)
            self._last_evaluation = audit
        # Avoid kwarg clash with structlog's positional `event` argument.
        fields = {k: v for k, v in audit.items() if k != "event"}
        if audit.get("decision") == "SIGNAL":
            logger.info("V21A_EVALUATION", **fields)
        elif audit.get("decision") == "WAIT" and "CHOPPY" in str(audit.get("reason") or ""):
            logger.info("V21A_EVALUATION_CHOPPY_WAIT", **fields)
        else:
            logger.debug("V21A_EVALUATION", **fields)

    def record_snapshot(self, snap: dict[str, Any]) -> None:
        with self._lock:
            self._snapshots.append(snap)
            self._last_signal = snap
        logger.info(
            "V21A_SIGNAL_SNAPSHOT",
            signal_id=snap.get("signal_id"),
            symbol=snap.get("symbol"),
            direction=snap.get("direction"),
            candle_1h=snap.get("1h_candle_timestamp"),
            regime=snap.get("regime"),
            entry=snap.get("entry"),
            stop=snap.get("stop"),
            target=snap.get("target"),
        )

    def record_parity(self, parity: dict[str, Any]) -> None:
        with self._lock:
            if parity.get("event") == PARITY_MISMATCH:
                self._mismatches.append(parity)
                self._parity_mismatch_count += 1
            else:
                self._parity_match_count += 1

    def record_telegram_status(self, key: str, status: str) -> None:
        with self._lock:
            self._telegram_status[str(key)] = str(status).upper()

    def note_forming_candle_block(self) -> None:
        with self._lock:
            self._forming_candle_blocks += 1
        logger.error(
            "v21a_forming_candle_blocked",
            strategy_id=STRATEGY_ID,
            mode=MODE,
        )

    def safety_halt(self, reason: str) -> None:
        with self._lock:
            self._safety_halted = True
            self._safety_reason = reason
        logger.error(
            "v21a_safety_halt",
            strategy_id=STRATEGY_ID,
            reason=reason,
            mode=MODE,
            real_execution="DISABLED",
        )
        try:
            from app.services.telegram_alerts import get_telegram_alert_subscriber

            sub = get_telegram_alert_subscriber()
            if sub is not None and getattr(sub, "enabled", False):
                text = (
                    "🚨 V2.1-A SAFETY HALT\n\n"
                    f"Reason: {reason}\n"
                    f"Strategy: {STRATEGY_ID}\n"
                    "Mode: PAPER ONLY\n"
                    "Real execution: DISABLED\n"
                    "Watcher stopped."
                )
                send = getattr(sub, "send_text", None)
                if callable(send):
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(send(text))
                    except RuntimeError:
                        pass
        except Exception:  # noqa: BLE001
            logger.warning("v21a_safety_telegram_failed")

    @property
    def halted(self) -> bool:
        with self._lock:
            return self._safety_halted

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "strategy": STRATEGY_ID,
                "mode": MODE,
                "real_execution": "DISABLED",
                "live_execution_allowed": LIVE_EXECUTION_ALLOWED,
                "safety_halted": self._safety_halted,
                "safety_reason": self._safety_reason,
                "last_market_update": self._last_market_update,
                "last_evaluation": self._last_evaluation,
                "last_signal": self._last_signal,
                "parity_matches": self._parity_match_count,
                "parity_mismatches": self._parity_mismatch_count,
                "forming_candle_blocks": self._forming_candle_blocks,
                "recent_evals": len(self._evals),
                "recent_snapshots": len(self._snapshots),
                "telegram_statuses": dict(list(self._telegram_status.items())[-20:]),
                "observation": True,
            }

    def recent_mismatches(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._mismatches)
        return rows[-limit:]


_monitor: V21AValidationMonitor | None = None
_monitor_lock = threading.Lock()


def get_v21a_validation_monitor() -> V21AValidationMonitor:
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = V21AValidationMonitor()
        return _monitor


def reset_v21a_validation_monitor_for_tests() -> None:
    global _monitor
    with _monitor_lock:
        _monitor = None
