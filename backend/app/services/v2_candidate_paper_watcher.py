"""V2CandidatePaperWatcher — separate from V1PaperWatcher, disabled by default.

Reads only operator-approved PAPER_VALIDATING registry rows. Never Telegram-
eligible. Uses the same closed 1h + closed 4h COMBO_02 evaluation path.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import structlog

from app.research.bos_combinations import get_combination
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.config import ResearchConfig
from app.research.dynamic_candidate_constants import (
    BLOCKED_PORTFOLIO_RISK_CAP,
    COMBO_ID,
    COMBO_VERSION,
    EXPERIMENTAL_LABEL,
    MAX_OVERRIDE_RISK,
    SOURCE_WATCHER,
    STRATEGY_ID,
)
from app.research.strategy_candidate_registry import strategy_candidate_registry
from app.research.v1_production import normalize_symbol, normalize_timeframe
from app.services.v1_paper_watcher import is_v1_long_entry
from app.signals._candle_utils import candle_time
from app.signals.config import SignalConfig

logger = structlog.get_logger(__name__)

DEFAULT_SETUP_TF = "1h"
_MIN_SETUP_BARS = 50


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def classify_v2_candidate(
    *,
    symbol: str,
    timeframe: str,
    risk_percent: float,
    htf_alignment: str | None,
    trend_1h: str | None = None,
    trend_4h: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Snippet fields for experimental v2 paper — always telegram_eligible=false."""
    snip: dict[str, Any] = {
        "strategy_id": STRATEGY_ID,
        "source": SOURCE_WATCHER,
        "combo_id": COMBO_ID,
        "combo_version": COMBO_VERSION,
        "path": "A",
        "symbol": str(symbol or "").upper(),
        "timeframe": str(timeframe or "1h").lower(),
        "htf_alignment": str(htf_alignment or "").upper() or None,
        "trend_1h": str(trend_1h or "").upper() or None,
        "trend_4h": str(trend_4h or "").upper() or None,
        "risk_percent": float(risk_percent),
        "telegram_eligible": False,
        "production_approved": False,
        "experimental_label": EXPERIMENTAL_LABEL,
        "v1": False,
    }
    if extra:
        snip.update(extra)
    snip["telegram_eligible"] = False
    snip["production_approved"] = False
    snip["strategy_id"] = STRATEGY_ID
    snip["source"] = SOURCE_WATCHER
    return snip


class V2CandidatePaperWatcher:
    """Disabled-by-default experimental paper watcher for dynamic candidates."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        timeframe: str = DEFAULT_SETUP_TF,
        max_open_positions: int = 1,
        max_total_risk_percent: float = MAX_OVERRIDE_RISK,
        max_v1_book_risk_percent: float = 0.05,
        paper_engine: Any | None = None,
        signal_config: SignalConfig | None = None,
        research_config: ResearchConfig | None = None,
        emit_alerts: bool = False,
        replay_mode: bool = False,
    ) -> None:
        self.enabled = bool(enabled)
        self.timeframe = normalize_timeframe(timeframe) or DEFAULT_SETUP_TF
        self.max_open_positions = int(max_open_positions)
        self.max_total_risk_percent = float(max_total_risk_percent)
        self.max_v1_book_risk_percent = float(max_v1_book_risk_percent)
        self._paper = paper_engine
        self.signal_config = signal_config or SignalConfig()
        self.research_config = research_config or ResearchConfig()
        self.emit_alerts = bool(emit_alerts)
        self.replay_mode = bool(replay_mode)
        self._combo = get_combination(COMBO_ID)
        self._lock = threading.RLock()
        self._processed_bars: set[str] = set()
        self._last_skip: str | None = None

    def _paper_engine(self) -> Any:
        if self._paper is not None:
            return self._paper
        from app.services.paper_trade import get_paper_trade_engine

        return get_paper_trade_engine()

    async def eligible_symbols(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        return await strategy_candidate_registry.list_paper_validating()

    def _v2_open_risk(self, paper: Any) -> tuple[int, float]:
        """Count open v2 positions and sum risk."""
        status = paper.status() if hasattr(paper, "status") else {}
        open_rows = []
        if hasattr(paper, "snapshot"):
            snap = paper.snapshot()
            open_rows = snap.get("open") or []
        elif isinstance(status, dict):
            open_rows = status.get("open") or []
        count = 0
        risk = 0.0
        for p in open_rows:
            snip = p.get("signal_snippet") or {}
            if str(snip.get("source") or "") != SOURCE_WATCHER:
                continue
            if str(snip.get("strategy_id") or "") != STRATEGY_ID:
                continue
            count += 1
            try:
                risk += float(snip.get("risk_percent") or p.get("risk_percent") or 0)
            except (TypeError, ValueError):
                pass
        return count, risk

    def _v1_open_risk(self, paper: Any) -> float:
        from app.services.paper_classification import (
            SOURCE_V1_PAPER_WATCHER,
            STRATEGY_COMBO_02_V1,
        )

        snap = paper.snapshot() if hasattr(paper, "snapshot") else {"open": []}
        total = 0.0
        for p in snap.get("open") or []:
            snip = p.get("signal_snippet") or {}
            if str(snip.get("source") or "") != SOURCE_V1_PAPER_WATCHER:
                continue
            if str(snip.get("strategy_id") or "") != STRATEGY_COMBO_02_V1:
                continue
            try:
                total += float(snip.get("risk_percent") or 0)
            except (TypeError, ValueError):
                pass
        return total

    def evaluate_closed_bar(
        self,
        symbol: str,
        *,
        candles_1h: Sequence[Mapping[str, Any]] | None,
        candles_4h: Sequence[Mapping[str, Any]] | None,
        risk_percent: float,
        as_of_index: int | None = None,
    ) -> dict[str, Any]:
        """Fail-closed COMBO_02 eval on closed 1h + closed 4h only."""
        sym = normalize_symbol(symbol)
        series_1h = list(candles_1h or [])
        series_4h = list(candles_4h or [])
        if not series_1h:
            return {"status": "NO_SETUP", "reason": "missing_1h"}
        if not series_4h:
            return {"status": "NO_SETUP", "reason": "missing_4h"}
        if len(series_1h) < _MIN_SETUP_BARS:
            return {"status": "NO_SETUP", "reason": "insufficient_1h_history"}
        idx = len(series_1h) - 1 if as_of_index is None else int(as_of_index)
        if idx < 0 or idx >= len(series_1h):
            return {"status": "NO_SETUP", "reason": "as_of_index out of range"}
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
            compute_sd=False,
        )
        result = dict(result or {})
        result["risk_percent"] = float(risk_percent)
        result["telegram_eligible"] = False
        result["strategy_id"] = STRATEGY_ID
        result["source"] = SOURCE_WATCHER
        return result

    async def on_closed_1h(
        self,
        symbol: str,
        *,
        candles_1h: Sequence[Mapping[str, Any]] | None = None,
        candles_4h: Sequence[Mapping[str, Any]] | None = None,
        force: bool = False,
    ) -> Any | None:
        if not self.enabled:
            self._last_skip = "watcher_disabled"
            return None

        sym = normalize_symbol(symbol)
        rows = await self.eligible_symbols()
        book = next((r for r in rows if str(r.get("symbol")) == sym), None)
        if book is None:
            self._last_skip = f"{sym}:not_paper_validating"
            return None

        risk = float(book.get("risk_percent") or 0)
        if risk <= 0 or risk > MAX_OVERRIDE_RISK + 1e-15:
            self._last_skip = f"{sym}:risk_out_of_bounds"
            return None
        if bool(book.get("telegram_eligible")):
            self._last_skip = f"{sym}:telegram_eligible_forbidden"
            return None
        if bool(book.get("production_approved")):
            self._last_skip = f"{sym}:production_approved_forbidden"
            return None
        if not bool(book.get("operator_approved")):
            self._last_skip = f"{sym}:not_operator_approved"
            return None

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
        if not series_1h or not series_4h:
            self._last_skip = f"{sym}:missing_closed_ohlcv"
            return None

        tip_idx = len(series_1h) - 1
        tip_ts = candle_time(series_1h[tip_idx])
        tip_iso = _iso(tip_ts)
        key = f"{sym}|{self.timeframe}|{tip_iso}"
        with self._lock:
            if key in self._processed_bars and not force:
                self._last_skip = f"{sym}:already_processed"
                return None
            self._processed_bars.add(key)

        eval_result = self.evaluate_closed_bar(
            sym,
            candles_1h=series_1h,
            candles_4h=series_4h,
            risk_percent=risk,
            as_of_index=tip_idx,
        )
        if not is_v1_long_entry(eval_result):
            # Same gate shape as v1 LONG entry; identity remains v2.
            self._last_skip = f"{sym}:no_long_entry:{eval_result.get('status')}"
            return None

        paper = self._paper_engine()
        open_n, open_risk = self._v2_open_risk(paper)
        peak_concurrent = open_n + 1  # would-be peak if this trade opened
        if open_n >= self.max_open_positions:
            self._last_skip = f"{sym}:max_v2_positions"
            return None
        if open_risk + risk > self.max_total_risk_percent + 1e-12:
            # Internal reason max_v2_risk; public reason is blocked_portfolio_risk_cap.
            self._last_skip = f"{sym}:max_v2_risk"
            await self._persist_block_reason(
                sym,
                reason=BLOCKED_PORTFOLIO_RISK_CAP,
                detail={
                    "symbol": sym,
                    "decision": "BLOCKED",
                    "reason": BLOCKED_PORTFOLIO_RISK_CAP,
                    "blocked_reason": BLOCKED_PORTFOLIO_RISK_CAP,
                    "internal_reason": "max_v2_risk",
                    "v1_open_risk_percent": 0.0,
                    "dynamic_open_risk_percent": open_risk,
                    "dynamic_requested_risk_percent": risk,
                    "max_total_risk_percent": self.max_total_risk_percent,
                    "peak_concurrent_positions": peak_concurrent,
                },
            )
            return None
        v1_risk = self._v1_open_risk(paper)
        combined = v1_risk + open_risk + risk
        if combined > self.max_v1_book_risk_percent + 1e-12:
            self._last_skip = f"{sym}:{BLOCKED_PORTFOLIO_RISK_CAP}"
            await self._persist_block_reason(
                sym,
                reason=BLOCKED_PORTFOLIO_RISK_CAP,
                detail={
                    "symbol": sym,
                    "decision": "BLOCKED",
                    "reason": BLOCKED_PORTFOLIO_RISK_CAP,
                    "blocked_reason": BLOCKED_PORTFOLIO_RISK_CAP,
                    "internal_reason": "v1_book_risk_cap",
                    "v1_open_risk_percent": v1_risk,
                    "dynamic_open_risk_percent": open_risk,
                    "dynamic_requested_risk_percent": risk,
                    "combined_risk_percent": combined,
                    "max_total_risk_percent": self.max_v1_book_risk_percent,
                    "peak_concurrent_positions": peak_concurrent,
                    # Legacy aliases retained for existing tests/UI.
                    "v1_open_risk": v1_risk,
                    "v2_open_risk": open_risk,
                    "requested_risk": risk,
                    "combined_risk": combined,
                    "cap": self.max_v1_book_risk_percent,
                },
            )
            return None

        # Open via generic paper machinery with v2 classification — never v1 path.
        htf = eval_result.get("htf") or {}
        snip = classify_v2_candidate(
            symbol=sym,
            timeframe=self.timeframe,
            risk_percent=risk,
            htf_alignment=htf.get("htf_alignment"),
            trend_1h=htf.get("trend_1h"),
            trend_4h=htf.get("trend_4h"),
            extra={
                "setup_bar_time_utc": tip_iso,
                "experimental": True,
                "production_approved": False,
            },
        )
        assert snip.get("strategy_id") != "COMBO_02_V1"
        assert snip.get("telegram_eligible") is False
        assert snip.get("source") == SOURCE_WATCHER
        assert snip.get("production_approved") is False

        open_fn = getattr(paper, "open_experimental_position", None)
        if callable(open_fn):
            pos = open_fn(
                symbol=sym,
                timeframe=self.timeframe,
                eval_result=eval_result,
                risk_percent=risk,
                signal_snippet=snip,
                setup_bar_time_utc=tip_iso or "",
                replay=self.replay_mode,
                emit_alert=self.emit_alerts,
            )
        else:
            # Fail closed if paper engine has no experimental open path.
            self._last_skip = f"{sym}:no_experimental_open_path"
            logger.warning(
                "v2_watcher_no_open_path",
                symbol=sym,
                note="PaperTradeEngine.open_experimental_position missing",
            )
            return None

        if pos is not None:
            # Hard isolation asserts on the opened trade record when available.
            snip_out = getattr(pos, "signal_snippet", None) or (
                pos.get("signal_snippet") if isinstance(pos, dict) else None
            )
            if isinstance(snip_out, dict):
                assert snip_out.get("strategy_id") != "COMBO_02_V1"
                assert snip_out.get("telegram_eligible") is False
                assert snip_out.get("source") == SOURCE_WATCHER
                assert snip_out.get("production_approved") is False

        logger.info(
            "v2_candidate_paper_open",
            symbol=sym,
            risk_percent=risk,
            telegram_eligible=False,
            label=EXPERIMENTAL_LABEL,
        )
        return pos

    async def _persist_block_reason(
        self,
        symbol: str,
        *,
        reason: str,
        detail: dict[str, Any],
    ) -> None:
        """Persist exposure block on the candidate for operator detail view."""
        try:
            row = await strategy_candidate_registry.get_by_symbol(symbol)
            if row is None:
                return
            port = row.get("portfolio_report") if isinstance(row.get("portfolio_report"), dict) else {}
            block_payload = {
                **detail,
                "reason": reason,
                "decision": detail.get("decision") or "BLOCKED",
            }
            await strategy_candidate_registry.update_fields(
                symbol,
                {
                    "portfolio_report": {
                        **port,
                        "last_block_reason": reason,
                        "blocked_reason": reason,
                        "last_block_detail": block_payload,
                        "last_block_at_utc": datetime.now(timezone.utc).isoformat(),
                        "last_trade_decision": block_payload,
                    }
                },
                actor="v2_candidate_paper_watcher",
                note=reason,
            )
            await strategy_candidate_registry.append_audit(
                candidate_id=str(row["id"]),
                symbol=str(row["symbol"]),
                action="TRADE_DECISION_BLOCKED",
                actor="v2_candidate_paper_watcher",
                previous_state=str(row.get("state")),
                new_state=str(row.get("state")),
                previous_risk=float(row.get("risk_percent") or 0),
                new_risk=float(row.get("risk_percent") or 0),
                note=reason,
                payload=block_payload,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "v2_watcher_block_persist_failed",
                symbol=symbol,
                reason=reason,
                error=str(exc),
            )


_v2_watcher: V2CandidatePaperWatcher | None = None


def get_v2_candidate_paper_watcher(**kwargs: Any) -> V2CandidatePaperWatcher:
    global _v2_watcher
    if _v2_watcher is None:
        _v2_watcher = V2CandidatePaperWatcher(**kwargs)
    return _v2_watcher


def reset_v2_candidate_paper_watcher() -> None:
    global _v2_watcher
    _v2_watcher = None
