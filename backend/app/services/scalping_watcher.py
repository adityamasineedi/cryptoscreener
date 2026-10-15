"""Fail-closed, closed-candle scalping watcher for screener presentation."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any

from app.research.intraday_bos_sd.scalping import (
    ScalpingStrategyEngine,
    validation_registry,
)
from app.services.ohlcv_store import OHLCVStore, ohlcv_store


class ScalpingWatcher:
    """Caches setups; never places orders or sends notifications."""

    def __init__(self, store: OHLCVStore | None = None) -> None:
        self.store = store or ohlcv_store
        self.engine = ScalpingStrategyEngine()
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._last_close: dict[str, datetime] = {}
        self._lock = RLock()

    @staticmethod
    def _risk(symbol: str) -> dict[str, Any]:
        try:
            from app.services.paper_risk import evaluate_paper_entry_risk, policy_from_settings
            from app.services.paper_trade import get_paper_trade_engine

            paper = get_paper_trade_engine()
            status = paper.status()
            result = evaluate_paper_entry_risk(
                symbol,
                policy=policy_from_settings(),
                open_count=int(status.get("open_count") or 0),
                open_risk_usd=float(status.get("open_risk_usd") or 0.0),
                equity=float(status.get("equity") or 0.0),
                **paper._halt_kwargs(symbol),
            )
            return result.to_dict()
        except Exception as exc:  # fail closed
            return {"ok": False, "reason": f"risk_context_unavailable:{type(exc).__name__}"}

    def on_closed_1m(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        one = self.store.get_candles_for_engine(sym, "1m", include_open=False)
        five = self.store.get_candles_for_engine(sym, "5m", include_open=False)
        fifteen = self.store.get_candles_for_engine(sym, "15m", include_open=False)
        if not one:
            return self._store(sym, "DATA_UNAVAILABLE", [], "missing_1m")
        last_close = one[-1].get("close_time")
        if isinstance(last_close, datetime) and self._last_close.get(sym) == last_close:
            return self.snapshot(sym)
        if len(one) < 30 or len(five) < 20 or len(fifteen) < 10:
            return self._store(sym, "DATA_UNAVAILABLE", [], "insufficient_closed_history")
        try:
            candidates = self.engine.evaluate(
                symbol=sym,
                candles_1m=one,
                candles_5m=five,
                candles_15m=fifteen,
                include_pending_entry=True,
            )
        except (ValueError, KeyError) as exc:
            return self._store(sym, "DATA_UNAVAILABLE", [], f"invalid_candles:{exc}")
        if isinstance(last_close, datetime):
            self._last_close[sym] = last_close
        setups = [c for c in candidates if c.signal_index == len(one) - 1]
        if not setups:
            return self._store(sym, "WATCHING", [], "no_new_setup")
        risk = self._risk(sym)
        rows: list[dict[str, Any]] = []
        for candidate in setups:
            validated = validation_registry.is_publishable(candidate.strategy_id, candidate.variant)
            rows.append(
                {
                    **candidate.to_dict(),
                    "historical_and_forward_validated": validated,
                    "portfolio_risk": risk,
                    "actionable": bool(validated and risk.get("ok")),
                }
            )
        if any(row["actionable"] for row in rows):
            status, reason = "ACTIONABLE", "validated_and_current_risk_passed"
        elif not any(row["historical_and_forward_validated"] for row in rows):
            status, reason = "BLOCKED_VALIDATION", "validation_or_forward_paper_gate_not_passed"
        else:
            status, reason = "BLOCKED_RISK", str(risk.get("reason") or "portfolio_risk_failed")
        return self._store(sym, status, rows, reason)

    def _store(self, symbol: str, status: str, signals: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        snapshot = {
            "symbol": symbol,
            "status": status,
            "reason": reason,
            "signals": signals,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "live_orders_enabled": False,
            "external_notifications_enabled": False,
        }
        with self._lock:
            self._snapshots[symbol] = snapshot
        return dict(snapshot)

    def snapshot(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshots.get(symbol.upper(), {
                "symbol": symbol.upper(), "status": "DATA_UNAVAILABLE",
                "reason": "not_evaluated", "signals": [],
                "live_orders_enabled": False, "external_notifications_enabled": False,
            }))


_watcher: ScalpingWatcher | None = None


def get_scalping_watcher() -> ScalpingWatcher:
    global _watcher
    if _watcher is None:
        _watcher = ScalpingWatcher()
    return _watcher


def reset_scalping_watcher_for_tests() -> None:
    global _watcher
    _watcher = None
