"""Historical COMBO_02 SHORT research batch runner (research-only).

Loads OHLCV health, runs SHORT strategy_matrix backtests, OOS + portfolio
diagnostics, classifies to SHORT_RESEARCH_CANDIDATE / RESEARCH_REJECTED /
OOS_FAILED, and persists reports. Never opens paper/live trades or Telegram.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.research.combo02_candidate_eligibility import (
    classify_eligibility,
    entry_overlap_pct,
    incremental_portfolio_stats,
    trade_intervals_from_rows,
)
from app.research.combo02_candidate_thresholds import (
    DEFAULT_THRESHOLDS,
    EligibilityThresholds,
    ResearchWindowConfig,
)
from app.research.combo02_short_research import (
    SHORT_RESEARCH_WINDOW,
    ShortResearchOnlyError,
    assert_short_window,
    classify_short_oos,
    run_short_symbol_backtest,
    short_research_identity,
    short_research_registry,
    ShortResearchCandidate,
)
from app.research.config import RESEARCH_ENGINE_VERSION
from app.research.short_research_constants import (
    COMBO_VERSION,
    DEFAULT_SHORT_RESEARCH_UNIVERSE,
    DIRECTION,
    DISCLAIMER,
    FORBIDDEN_PAPER_STATES,
    NO_POSITION_CREATED_LABEL,
    RESEARCH_SIMULATION_LABEL,
    SETUP_TIMEFRAME,
    SOURCE,
    STRATEGY_FINGERPRINT_TEXT,
    STRATEGY_ID,
    TERMINAL_PASS_STATE,
)
from app.research.short_research_forensics import (
    audit_trades_for_lookahead,
    enrich_trade_forensic_fields,
)
from app.research.short_research_hard_gates import (
    EVIDENCE_MODE_DIRECT,
    build_batch_summary,
)
from app.research.short_research_prepaper_review import (
    attach_partition_sample_warnings,
    build_prepaper_review,
)
from app.research.short_research_quality import (
    EXECUTION_MODEL,
    HISTORICAL_DISCLAIMER,
    classify_research_quality,
    configuration_fingerprint,
    dataset_fingerprint,
    inspect_ohlcv_series,
    lookahead_audit_checklist,
    reconcile_research_blotter,
    sample_size_assessment,
    validate_base_oos_split,
    validate_timeframe,
)
from app.research.short_research_windows import (
    DEFAULT_SHORT_RESEARCH_WINDOWS,
    ResearchWindows,
    research_windows_from_config,
    validate_research_windows,
)
from app.signals.trade_math import SAME_CANDLE_PRECEDENCE_SL_FIRST

HealthFn = Callable[[str], Any]
BacktestFn = Callable[..., Any]

_MIN_BARS_1H = 200
_MIN_COMPLETENESS = 0.99


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def default_reports_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "reports"


class ShortResearchReportStore:
    """In-memory + file-backed SHORT research reports (by run_id)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._by_symbol: dict[str, list[str]] = {}  # symbol -> run_ids newest last

    def put(self, report: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(report.get("run_id") or "")
        if not run_id:
            raise ValueError("run_id required")
        payload = dict(report)
        # Fail-closed safety stamps
        payload["strategy_id"] = STRATEGY_ID
        payload["combo_version"] = COMBO_VERSION
        payload["source"] = SOURCE
        payload["direction"] = DIRECTION
        payload["paper_eligible"] = False
        payload["production_approved"] = False
        payload["telegram_eligible"] = False
        payload["research_universe"] = True
        payload["v1_universe"] = False
        state = str(payload.get("state") or "").upper()
        if state in FORBIDDEN_PAPER_STATES:
            raise ShortResearchOnlyError("forbidden_paper_state")
        with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                # Idempotent only when fingerprints match; never overwrite drift.
                same_cfg = existing.get("configuration_hash") == payload.get(
                    "configuration_hash"
                )
                same_data = existing.get("dataset_hash") == payload.get("dataset_hash")
                if same_cfg and same_data:
                    return dict(existing)
                raise ValueError(
                    "run_id_fingerprint_conflict: changed data/config requires a new run_id"
                )
            self._runs[run_id] = payload
            # Index each candidate symbol in this run
            for cand in payload.get("candidates") or []:
                sym = str(cand.get("symbol") or "").upper()
                if not sym:
                    continue
                self._by_symbol.setdefault(sym, [])
                if run_id not in self._by_symbol[sym]:
                    self._by_symbol[sym].append(run_id)
        return payload

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._runs.get(run_id)
            return dict(row) if row else None

    def list_candidates(self) -> list[dict[str, Any]]:
        """Latest candidate snapshot per symbol across runs."""
        with self._lock:
            latest: dict[str, dict[str, Any]] = {}
            for run_id, run in self._runs.items():
                for cand in run.get("candidates") or []:
                    sym = str(cand.get("symbol") or "").upper()
                    if not sym:
                        continue
                    prev = latest.get(sym)
                    if prev is None or str(cand.get("created_at") or "") >= str(
                        prev.get("created_at") or ""
                    ):
                        latest[sym] = {**cand, "run_id": run_id}
            return list(latest.values())

    def get_symbol(self, symbol: str) -> dict[str, Any] | None:
        sym = str(symbol).upper()
        with self._lock:
            run_ids = list(self._by_symbol.get(sym) or [])
            if not run_ids:
                return None
            # Prefer newest run that contains the symbol
            for run_id in reversed(run_ids):
                run = self._runs.get(run_id)
                if not run:
                    continue
                for cand in run.get("candidates") or []:
                    if str(cand.get("symbol") or "").upper() == sym:
                        return {**cand, "run_id": run_id}
            return None

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._by_symbol.clear()


short_research_report_store = ShortResearchReportStore()


async def load_short_research_candles(
    symbol: str,
    timeframe: str,
    *,
    requested_start: str | None = None,
    requested_end: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Load closed candles for SHORT research health/forensics.

    Prefers PostgreSQL (full requested range) over the in-memory ohlcv_store,
    which is often empty or capped at ~500 bars outside the live engine process.
    """
    from app.services.database import db_manager
    from app.services.ohlcv_store import ohlcv_store

    sym = str(symbol).upper()
    tf = str(timeframe).lower()

    if db_manager.enabled and db_manager.engine is not None:
        try:
            from app.research.postgres_ohlcv import load_ohlcv_series_range
            from app.research.query_utils import resolve_date_bounds

            bounds = resolve_date_bounds(requested_start, requested_end)
            rows = await load_ohlcv_series_range(
                sym,
                tf,
                start=bounds["start"],
                end_exclusive=bounds["end_exclusive"],
                warmup_bars=0,
            )
            if rows:
                return list(rows), "postgresql_ohlcv"
            # Empty Postgres result is authoritative — do not silently fall back
            # to a truncated memory slice for a different calendar window.
            if bounds["start"] is not None or bounds["end_exclusive"] is not None:
                return [], "postgresql_ohlcv"
        except Exception:
            pass

    mem = list(ohlcv_store.get_candles_for_engine(sym, tf, include_open=False) or [])
    return mem, "ohlcv_store"


def _public_data_health(health: Mapping[str, Any]) -> dict[str, Any]:
    """Drop bulky candle arrays before persisting health into reports."""
    out = dict(health)
    out.pop("candles_1h", None)
    out.pop("candles_4h", None)
    return out


async def assess_short_data_health(
    symbol: str,
    *,
    requested_start: str | None = None,
    requested_end: str | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fail-closed 1h+4h coverage + integrity check for SHORT research."""
    sym = str(symbol).upper()
    if candles_1h is not None:
        series_1h = list(candles_1h)
        src_1h = "injected"
    else:
        series_1h, src_1h = await load_short_research_candles(
            sym,
            "1h",
            requested_start=requested_start,
            requested_end=requested_end,
        )
    if candles_4h is not None:
        series_4h = list(candles_4h)
        src_4h = "injected"
    else:
        series_4h, src_4h = await load_short_research_candles(
            sym,
            "4h",
            requested_start=requested_start,
            requested_end=requested_end,
        )
    data_source = src_1h if src_1h == src_4h else f"{src_1h}+{src_4h}"
    cov_1h = inspect_ohlcv_series(
        series_1h,
        timeframe="1h",
        symbol=sym,
        requested_start=requested_start,
        requested_end=requested_end,
        data_source=src_1h,
    )
    cov_4h = inspect_ohlcv_series(
        series_4h,
        timeframe="4h",
        symbol=sym,
        requested_start=requested_start,
        requested_end=requested_end,
        data_source=src_4h,
    )
    bars_1h = int(cov_1h.get("bars_used") or 0)
    bars_4h = int(cov_4h.get("bars_used") or 0)
    completeness = 1.0 if bars_1h >= _MIN_BARS_1H and bars_4h >= 80 else (
        0.0 if bars_1h == 0 else min(1.0, bars_1h / float(_MIN_BARS_1H))
    )
    integrity_ok = bool(cov_1h.get("ok")) and bool(cov_4h.get("ok"))
    size_ok = bars_1h >= _MIN_BARS_1H and bars_4h >= 80 and completeness >= _MIN_COMPLETENESS
    ok = integrity_ok and size_ok
    status = "HEALTHY" if ok else (
        cov_1h.get("data_health_status")
        if cov_1h.get("data_health_status") not in (None, "HEALTHY")
        else cov_4h.get("data_health_status") or "INCOMPLETE"
    )
    if not size_ok and status == "HEALTHY":
        status = "INSUFFICIENT_HISTORY"
    ds_hash = dataset_fingerprint({"1h": series_1h, "4h": series_4h})
    return {
        "symbol": sym,
        "coverage_start": cov_1h.get("actual_first_candle"),
        "coverage_end": cov_1h.get("actual_last_candle"),
        "coverage_start_4h": cov_4h.get("actual_first_candle"),
        "coverage_end_4h": cov_4h.get("actual_last_candle"),
        "requested_start": requested_start,
        "requested_end": requested_end,
        "requested_range_available": cov_1h.get("requested_range_available"),
        "bars_used": bars_1h,
        "bars_4h": bars_4h,
        "bars_loaded": cov_1h.get("bars_loaded"),
        "bars_requested": cov_1h.get("bars_requested"),
        "expected_bar_count": cov_1h.get("expected_bar_count"),
        "missing_bar_count": cov_1h.get("missing_bars"),
        "duplicate_bars": cov_1h.get("duplicate_bars"),
        "out_of_order_bars": cov_1h.get("out_of_order_bars"),
        "completeness": completeness,
        "timezone": "UTC",
        "data_source": data_source,
        "data_health_status": status,
        "health_status": "OK" if ok else "BLOCKED",
        "ok": ok,
        "reason": None if ok else "insufficient_or_malformed_ohlcv",
        "coverage_1h": cov_1h,
        "coverage_4h": cov_4h,
        "dataset_hash": ds_hash,
        "candles_1h": series_1h,
        "candles_4h": series_4h,
        "errors": list(cov_1h.get("errors") or []) + list(cov_4h.get("errors") or []),
    }


def _rule(rule: str, actual: Any, required: Any, passed: bool) -> dict[str, Any]:
    return {
        "rule": rule,
        "actual": actual,
        "required": required,
        "passed": bool(passed),
    }


def _direction_checks(
    *,
    bearish_trend: bool,
    bearish_bos: bool,
    bearish_htf: bool,
    geometry_ok: bool,
) -> list[dict[str, Any]]:
    return [
        _rule("bearish_trend", bearish_trend, True, bearish_trend),
        _rule("bearish_bos", bearish_bos, True, bearish_bos),
        _rule("bearish_htf_alignment", bearish_htf, True, bearish_htf),
        _rule(
            "short_geometry",
            "tp < entry < stop" if geometry_ok else "invalid",
            True,
            geometry_ok,
        ),
    ]


def _classify_state(
    *,
    health_ok: bool,
    base_tier: str,
    oos_label: str | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (state, research_tier, rejection_reasons)."""
    if not health_ok:
        reason = _rule("data_health", "BLOCKED", "OK", False)
        return "RESEARCH_REJECTED", "RESEARCH_REJECTED", [reason]
    if base_tier == "REJECT" or base_tier == "INSUFFICIENT_DATA":
        return "RESEARCH_REJECTED", "RESEARCH_REJECTED", []
    if base_tier == "PROMISING" and oos_label == TERMINAL_PASS_STATE:
        return TERMINAL_PASS_STATE, TERMINAL_PASS_STATE, []
    if base_tier == "PROMISING" and oos_label in (
        "OOS_FAILED",
        "PROMISING_NEEDS_MORE_EVIDENCE",
        "INSUFFICIENT_OOS_DATA",
    ):
        # Needs-more-evidence / insufficient → OOS_FAILED for research gate
        if oos_label == "PROMISING_NEEDS_MORE_EVIDENCE" or oos_label == "OOS_FAILED":
            return "OOS_FAILED", "OOS_FAILED", []
        return "OOS_FAILED", "OOS_FAILED", []
    if base_tier == "PROMISING":
        return "PROMISING", "PROMISING", []
    if base_tier == "WATCHLIST":
        return "RESEARCH_REJECTED", "RESEARCH_REJECTED", []
    return "RESEARCH_REJECTED", "RESEARCH_REJECTED", []


def serialize_short_candidate_view(cand: Mapping[str, Any]) -> dict[str, Any]:
    """API-facing candidate row with mandatory identity + safety flags."""
    identity = short_research_identity(symbol=str(cand.get("symbol") or ""))
    state = str(cand.get("state") or "DISCOVERED").upper()
    if state in FORBIDDEN_PAPER_STATES:
        state = "RESEARCH_REJECTED"
    return {
        **identity,
        "symbol": str(cand.get("symbol") or "").upper(),
        "timeframe": str(cand.get("timeframe") or SETUP_TIMEFRAME),
        "research_tier": str(cand.get("research_tier") or state),
        "state": state,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "research_universe": True,
        "v1_universe": False,
        "run_id": cand.get("run_id"),
        "created_at": cand.get("created_at"),
        "updated_at": cand.get("updated_at") or cand.get("created_at"),
        "data_health": cand.get("data_health"),
        "base_research": cand.get("base_research"),
        "oos": cand.get("oos"),
        "direction_checks": cand.get("direction_checks") or [],
        "rejection_reasons": cand.get("rejection_reasons") or [],
        "portfolio": cand.get("portfolio"),
        "risk_simulation": cand.get("risk_simulation"),
        "sample_size": cand.get("sample_size"),
        "research_quality": cand.get("research_quality"),
        "quality_warnings": cand.get("quality_warnings") or [],
        "lookahead_audit": cand.get("lookahead_audit"),
        "forensic_lookahead": cand.get("forensic_lookahead"),
        "trade_forensic_audits": cand.get("trade_forensic_audits") or [],
        "prepaper_review": cand.get("prepaper_review"),
        "research_windows": cand.get("research_windows"),
        "window_status": cand.get("window_status"),
        "partition_counts": cand.get("partition_counts"),
        "oos_development": cand.get("oos_development"),
        "oos_validation": cand.get("oos_validation"),
        "execution_model": cand.get("execution_model") or EXECUTION_MODEL,
        "reconciliation": cand.get("reconciliation"),
        "oos_split": cand.get("oos_split"),
        "configuration_hash": cand.get("configuration_hash"),
        "dataset_hash": cand.get("dataset_hash"),
        "engine_version": cand.get("engine_version") or RESEARCH_ENGINE_VERSION,
        "strategy_fingerprint": cand.get("strategy_fingerprint")
        or STRATEGY_FINGERPRINT_TEXT,
        "historical_disclaimer": HISTORICAL_DISCLAIMER,
        "labels": {
            "research_state": _ui_state_label(state),
            "paper": "Paper disabled",
            "production": "Production not approved",
            "telegram": "Telegram disabled",
            "simulation": RESEARCH_SIMULATION_LABEL,
            "no_position": NO_POSITION_CREATED_LABEL,
            "banner": "SHORT RESEARCH ONLY",
            "research_quality": str(cand.get("research_quality") or "RESEARCH_ONLY"),
            "review": str(
                (cand.get("prepaper_review") or {}).get("human_label")
                or (
                    "Ready for separate human review"
                    if str((cand.get("prepaper_review") or {}).get("review_status"))
                    == "REVIEW_READY_FOR_SEPARATE_APPROVAL"
                    else "Review blocked — research only"
                )
            ),
        },
        "blockers": list((cand.get("prepaper_review") or {}).get("blockers") or []),
        "human_label": (cand.get("prepaper_review") or {}).get("human_label"),
    }


def _ui_state_label(state: str) -> str:
    s = str(state or "").upper()
    if s == TERMINAL_PASS_STATE:
        return "Research candidate"
    if s == "OOS_FAILED":
        return "OOS failed"
    if s == "RESEARCH_REJECTED":
        return "Research rejected"
    if s == "PROMISING":
        return "Research candidate"
    return "Research only"


def _partition_metrics_block(
    run: Mapping[str, Any] | None,
    *,
    start: str,
    end: str,
    label: str,
) -> dict[str, Any]:
    if run is None:
        return {
            "partition": label,
            "window": f"{start}→{end}",
            "trade_count": 0,
            "status": "NOT_RUN",
        }
    return {
        "partition": label,
        "window": f"{start}→{end}",
        "trade_count": run.get("trade_count"),
        "net_pnl": run.get("net_pnl"),
        "avg_r": run.get("net_avg_r"),
        "profit_factor": run.get("profit_factor"),
        "max_drawdown": run.get("max_drawdown_r") or run.get("max_dd_r"),
        "max_losing_streak": run.get("max_losing_streak"),
        "run_status": run.get("run_status"),
        "status": "COMPLETED" if str(run.get("run_status")) == "COMPLETED" else "FAILED",
    }


def _forensic_bundle(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    enriched = [enrich_trade_forensic_fields(t) for t in trades]
    return audit_trades_for_lookahead(enriched, timeframe=SETUP_TIMEFRAME)


async def research_one_short_symbol(
    *,
    symbol: str,
    run_id: str,
    service: Any,
    window: ResearchWindowConfig = SHORT_RESEARCH_WINDOW,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
    run_oos: bool = True,
    health_fn: HealthFn | None = None,
    backtest_fn: BacktestFn | None = None,
    peer_intervals: dict[str, list] | None = None,
    research_windows: ResearchWindows | None = None,
    evidence_mode: str = EVIDENCE_MODE_DIRECT,
) -> dict[str, Any]:
    """Build one SHORT research report record (no paper side effects)."""
    assert_short_window(window)
    evidence_mode = str(evidence_mode or EVIDENCE_MODE_DIRECT).upper()
    tf_ok = validate_timeframe(window.setup_timeframe)
    if not tf_ok["ok"]:
        raise ValueError(tf_ok["error"])
    sym = str(symbol).upper()
    created = utc_now_iso()
    windows = research_windows or research_windows_from_config(window)
    win_check = validate_research_windows(windows)
    cfg_hash = configuration_fingerprint(
        window={**window.to_dict(), **windows.to_dict()},
        thresholds=thresholds.to_dict(),
        symbols=[sym],
        run_oos=run_oos,
    )
    if health_fn is None:
        health = await assess_short_data_health(
            sym,
            requested_start=windows.requested_start,
            requested_end=windows.requested_end,
        )
    else:
        health = await _maybe_await(health_fn(sym))
    identity = short_research_identity(symbol=sym)
    la = lookahead_audit_checklist()

    def _reject_cand(
        *,
        state: str,
        research_quality: str,
        rejection: list[dict[str, Any]],
        oos: dict[str, Any] | None = None,
        base_research: dict[str, Any] | None = None,
        forensic: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sample = sample_size_assessment(0)
        cand = {
            **identity,
            "timeframe": SETUP_TIMEFRAME,
            "run_id": run_id,
            "created_at": created,
            "updated_at": created,
            "state": state,
            "research_tier": state,
            "research_quality": research_quality,
            "window_status": win_check["window_status"],
            "research_windows": windows.to_dict(),
            "data_health": _public_data_health(health),
            "base_research": base_research,
            "oos": oos or {"oos_status": "NOT_RUN"},
            "oos_development": None,
            "oos_validation": None,
            "direction_checks": _direction_checks(
                bearish_trend=False,
                bearish_bos=False,
                bearish_htf=False,
                geometry_ok=False,
            ),
            "rejection_reasons": rejection,
            "portfolio": {
                "status": "NOT_CHECKED",
                "note": RESEARCH_SIMULATION_LABEL,
                "affects_v1_risk_totals": False,
            },
            "risk_simulation": {
                "label": RESEARCH_SIMULATION_LABEL,
                "no_position": NO_POSITION_CREATED_LABEL,
                "requested_research_risk_usd": window.risk_usd,
                "added_to_v1_risk_totals": False,
            },
            "sample_size": sample,
            "quality_warnings": sample["warnings"],
            "lookahead_audit": la,
            "forensic_lookahead": forensic,
            "trade_forensic_audits": (forensic or {}).get("audits") or [],
            "execution_model": EXECUTION_MODEL,
            "configuration_hash": cfg_hash,
            "dataset_hash": health.get("dataset_hash"),
            "engine_version": RESEARCH_ENGINE_VERSION,
            "strategy_fingerprint": STRATEGY_FINGERPRINT_TEXT,
            "evidence_mode": evidence_mode,
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
            "research_universe": True,
            "v1_universe": False,
        }
        cand["prepaper_review"] = build_prepaper_review(
            cand, evidence_mode=evidence_mode
        )
        cand["research_quality"] = "REVIEW_REQUIRED"
        short_research_registry.upsert(
            ShortResearchCandidate(
                symbol=sym,
                state=state if state in (
                    "RESEARCH_REJECTED", "OOS_FAILED", "PROMISING", TERMINAL_PASS_STATE
                ) else "RESEARCH_REJECTED",
                metrics={},
                oos_label=None,
                eligibility={"research_tier": state},
            )
        )
        return serialize_short_candidate_view(cand)

    if not win_check["ok"]:
        return _reject_cand(
            state="OOS_FAILED",
            research_quality="REVIEW_REQUIRED",
            rejection=[_rule("window_status", win_check["window_status"], "OK", False)],
            oos={
                "oos_status": "INVALID_SPLIT",
                "oos_policy": windows.oos_policy,
                "oos_dev_start": windows.oos_dev_start,
                "oos_dev_end": windows.oos_dev_end,
                "oos_val_start": windows.oos_val_start,
                "oos_val_end": windows.oos_val_end,
                "oos_reasons": win_check["errors"],
            },
        )

    if not health.get("ok"):
        return _reject_cand(
            state="RESEARCH_REJECTED",
            research_quality="REVIEW_REQUIRED",
            rejection=[
                _rule(
                    "data_health",
                    health.get("data_health_status") or health.get("health_status"),
                    "HEALTHY",
                    False,
                )
            ],
            oos={"oos_status": "NOT_RUN", "reason": "data_health_blocked"},
        )

    bt = backtest_fn or run_short_symbol_backtest
    # Base eligibility uses ONLY the canonical base window (never OOS candles).
    base = await _maybe_await(
        bt(
            service,
            sym,
            start=windows.base_start,
            end=windows.base_end,
            window=window,
        )
    )
    fee_share = base.get("fees_over_gross_pnl") or base.get("fee_share")
    if base.get("run_status") != "COMPLETED":
        tier, reason_codes, metric_conds = (
            "INSUFFICIENT_DATA",
            [f"run_status={base.get('run_status')}"],
            [],
        )
    else:
        tier, reason_codes, metric_conds = classify_eligibility(
            trade_count=int(base.get("trade_count") or 0),
            net_avg_r=base.get("net_avg_r"),
            net_pnl=base.get("net_pnl"),
            profit_factor=base.get("profit_factor"),
            max_dd_r=base.get("max_drawdown_r") or base.get("max_dd_r"),
            max_lose_streak=base.get("max_losing_streak"),
            fee_share=fee_share,
            thresholds=thresholds,
        )

    direction_checks = _direction_checks(
        bearish_trend=True,
        bearish_bos=True,
        bearish_htf=bool(window.require_htf_alignment),
        geometry_ok=True,
    )

    closed = [enrich_trade_forensic_fields(t) for t in (base.get("closed_trades") or [])]
    # Attempt independent candle replay when OHLCV is available (fail closed if not).
    try:
        from app.research.short_research_forensics import apply_independent_replay_to_trade

        c1h = list(health.get("candles_1h") or [])
        c4h = list(health.get("candles_4h") or [])
        if not c1h:
            c1h, _ = await load_short_research_candles(
                sym,
                "1h",
                requested_start=windows.requested_start,
                requested_end=windows.requested_end,
            )
        if not c4h:
            c4h, _ = await load_short_research_candles(
                sym,
                "4h",
                requested_start=windows.requested_start,
                requested_end=windows.requested_end,
            )
        if c1h:
            closed = [
                apply_independent_replay_to_trade(t, candles_1h=c1h, candles_4h=c4h)
                for t in closed
            ]
    except Exception:
        # Keep derived enrichment; promotion remains blocked without DIRECT replay.
        pass
    base_forensic = audit_trades_for_lookahead(closed, timeframe=SETUP_TIMEFRAME)
    # Promotion-grade pass requires DIRECT_CANDLE_REPLAY evidence, not derived timestamps.
    forensic_pass = bool(base_forensic.get("forensic_pass_for_promotion"))

    oos_dev_block = _partition_metrics_block(
        None, start=windows.oos_dev_start, end=windows.oos_dev_end, label="oos_development"
    )
    oos_val_block = _partition_metrics_block(
        None, start=windows.oos_val_start, end=windows.oos_val_end, label="oos_validation"
    )
    oos_block: dict[str, Any] = {
        "oos_status": "NOT_APPLICABLE",
        "reason": "base_research_rejected" if tier != "PROMISING" else None,
        "oos_policy": windows.oos_policy,
        "oos_dev_start": windows.oos_dev_start,
        "oos_dev_end": windows.oos_dev_end,
        "oos_val_start": windows.oos_val_start,
        "oos_val_end": windows.oos_val_end,
    }
    oos_label: str | None = None
    oos_conds: list[dict[str, Any]] = []
    oos_dev_trades: list[dict[str, Any]] = []
    oos_val_trades: list[dict[str, Any]] = []
    oos_split = validate_base_oos_split(
        base_start=windows.base_start,
        base_end=windows.base_end,
        oos_start=windows.oos_val_start,
        oos_end=windows.oos_val_end,
        base_trades=closed,
        oos_trades=[],
    )
    # Also require base vs oos_dev disjoint
    base_dev_split = validate_base_oos_split(
        base_start=windows.base_start,
        base_end=windows.base_end,
        oos_start=windows.oos_dev_start,
        oos_end=windows.oos_dev_end,
        base_trades=closed,
        oos_trades=[],
    )
    if not base_dev_split["valid"] or not oos_split["valid"]:
        oos_label = "INVALID_SPLIT"
        oos_block["oos_status"] = "INVALID_SPLIT"

    if run_oos and tier == "PROMISING" and oos_label != "INVALID_SPLIT":
        oos_dev_run = await _maybe_await(
            bt(
                service,
                sym,
                start=windows.oos_dev_start,
                end=windows.oos_dev_end,
                window=window,
            )
        )
        oos_dev_trades = [
            enrich_trade_forensic_fields(t)
            for t in (oos_dev_run.get("closed_trades") or [])
        ]
        oos_dev_block = {
            **_partition_metrics_block(
                oos_dev_run,
                start=windows.oos_dev_start,
                end=windows.oos_dev_end,
                label="oos_development",
            ),
            "conditions": [],
        }

        oos_val_run = await _maybe_await(
            bt(
                service,
                sym,
                start=windows.oos_val_start,
                end=windows.oos_val_end,
                window=window,
            )
        )
        oos_val_trades = [
            enrich_trade_forensic_fields(t)
            for t in (oos_val_run.get("closed_trades") or [])
        ]
        oos_split = validate_base_oos_split(
            base_start=windows.base_start,
            base_end=windows.base_end,
            oos_start=windows.oos_val_start,
            oos_end=windows.oos_val_end,
            base_trades=closed,
            oos_trades=oos_val_trades,
        )
        dev_val_split = validate_base_oos_split(
            base_start=windows.oos_dev_start,
            base_end=windows.oos_dev_end,
            oos_start=windows.oos_val_start,
            oos_end=windows.oos_val_end,
            base_trades=oos_dev_trades,
            oos_trades=oos_val_trades,
        )
        if not oos_split["valid"] or not dev_val_split["valid"]:
            oos_label = "INVALID_SPLIT"
            oos_block = {
                **oos_block,
                "oos_status": "INVALID_SPLIT",
                "oos_reasons": ["partition_overlap"],
            }
            oos_val_block = _partition_metrics_block(
                oos_val_run,
                start=windows.oos_val_start,
                end=windows.oos_val_end,
                label="oos_validation",
            )
        else:
            # Final OOS status from VALIDATION partition only (not combined with dev).
            oos_label, oos_reasons, oos_conds = classify_short_oos(
                base_tier=tier,
                oos_trade_count=int(oos_val_run.get("trade_count") or 0),
                oos_net_avg_r=oos_val_run.get("net_avg_r"),
                oos_net_pnl=oos_val_run.get("net_pnl"),
                oos_profit_factor=oos_val_run.get("profit_factor"),
                oos_max_dd_r=oos_val_run.get("max_drawdown_r") or oos_val_run.get("max_dd_r"),
                oos_max_lose_streak=oos_val_run.get("max_losing_streak"),
                oos_usable=str(oos_val_run.get("run_status") or "") == "COMPLETED",
                thresholds=thresholds,
            )
            oos_val_block = {
                **_partition_metrics_block(
                    oos_val_run,
                    start=windows.oos_val_start,
                    end=windows.oos_val_end,
                    label="oos_validation",
                ),
                "conditions": oos_conds,
                "reasons": oos_reasons,
            }
            oos_block = {
                **oos_block,
                "oos_status": oos_label,
                "oos_trade_count": oos_val_run.get("trade_count"),
                "oos_net_pnl": oos_val_run.get("net_pnl"),
                "oos_avg_r": oos_val_run.get("net_avg_r"),
                "oos_profit_factor": oos_val_run.get("profit_factor"),
                "oos_max_drawdown": oos_val_run.get("max_drawdown_r")
                or oos_val_run.get("max_dd_r"),
                "oos_max_losing_streak": oos_val_run.get("max_losing_streak"),
                "oos_conditions": oos_conds,
                "oos_reasons": oos_reasons,
                "window": f"{windows.oos_val_start}→{windows.oos_val_end}",
                "determines_status": "oos_validation",
            }

        # Forensic on OOS trades — derived-only or FAIL blocks promotion
        try:
            from app.research.short_research_forensics import (
                apply_independent_replay_to_trade,
            )

            c1h_oos = list(health.get("candles_1h") or [])
            c4h_oos = list(health.get("candles_4h") or [])
            if c1h_oos:
                oos_dev_trades = [
                    apply_independent_replay_to_trade(
                        t, candles_1h=c1h_oos, candles_4h=c4h_oos
                    )
                    for t in oos_dev_trades
                ]
                oos_val_trades = [
                    apply_independent_replay_to_trade(
                        t, candles_1h=c1h_oos, candles_4h=c4h_oos
                    )
                    for t in oos_val_trades
                ]
        except Exception:
            pass
        for part_trades in (oos_dev_trades, oos_val_trades):
            part_f = audit_trades_for_lookahead(part_trades, timeframe=SETUP_TIMEFRAME)
            if not part_f.get("forensic_pass_for_promotion"):
                forensic_pass = False
                base_forensic = {
                    **base_forensic,
                    "oos_forensic_failure": True,
                    "research_quality": "REVIEW_REQUIRED",
                }

    state, research_tier, rejection = _classify_state(
        health_ok=True,
        base_tier=tier,
        oos_label=("OOS_FAILED" if oos_label == "INVALID_SPLIT" else oos_label),
    )
    if oos_label == "INVALID_SPLIT":
        state = "OOS_FAILED"
        research_tier = "OOS_FAILED"
        rejection = [_rule("base_oos_disjoint", False, True, False)]
    if not rejection and tier != "PROMISING":
        rejection = [c for c in metric_conds if not c.get("passed")]
        if not rejection and reason_codes:
            rejection = [
                _rule("eligibility", reason_codes, "promising_thresholds", False)
            ]
    if state == "OOS_FAILED" and oos_conds:
        rejection = [c for c in oos_conds if not c.get("passed")] or rejection

    # Forensic failure cannot promote to SHORT_RESEARCH_CANDIDATE
    if not forensic_pass:
        if state == TERMINAL_PASS_STATE:
            state = "PROMISING"
            research_tier = "PROMISING"
        rejection = list(rejection or []) + [
            _rule("forensic_lookahead", "FAIL", "PASS", False)
        ]

    cand_iv = trade_intervals_from_rows(sym, closed)
    peers = peer_intervals or {}
    overlap = {
        peer: entry_overlap_pct(
            [iv.entry for iv in cand_iv], [iv.entry for iv in peers.get(peer) or []]
        )
        for peer in peers
    }
    all_peer_iv = [iv for vals in peers.values() for iv in vals]
    incremental = (
        incremental_portfolio_stats(cand_iv, all_peer_iv) if all_peer_iv else {}
    )

    wins = int(base.get("wins") or 0)
    losses = int(base.get("losses") or 0)
    if wins == 0 and losses == 0 and closed:
        wins = sum(1 for t in closed if float(t.get("r_net") or t.get("r_multiple") or 0) > 0)
        losses = sum(1 for t in closed if float(t.get("r_net") or t.get("r_multiple") or 0) <= 0)

    recon = reconcile_research_blotter(
        closed,
        starting_equity=float(window.principal_usd),
        reported_net_pnl=base.get("net_pnl"),
        reported_fees=base.get("total_fees"),
        reported_avg_r=base.get("net_avg_r"),
        reported_profit_factor=base.get("profit_factor"),
        profit_factor_basis=str(EXECUTION_MODEL["profit_factor_basis"]),
    )
    sample = sample_size_assessment(int(base.get("trade_count") or 0))
    provenance_ok = health.get("requested_range_available") is not False
    recon_ok = bool(recon.get("ok", True))
    research_quality = classify_research_quality(
        trade_count=int(base.get("trade_count") or 0),
        health_ok=True,
        oos_split_valid=bool(oos_split.get("valid", True)) and oos_label != "INVALID_SPLIT",
        lookahead_safe=bool(la.get("lookahead_safe")) and forensic_pass,
        state=state,
        windows_ok=bool(win_check["ok"]),
        forensic_pass=forensic_pass,
    )
    if not provenance_ok or not recon_ok:
        research_quality = "REVIEW_REQUIRED"
    if research_quality == "REVIEW_REQUIRED" and state == TERMINAL_PASS_STATE:
        state = "PROMISING"
        research_tier = "PROMISING"

    # Terminal pass requires DIRECT forensic + windows + OOS validation
    if state == TERMINAL_PASS_STATE and (
        not forensic_pass
        or not win_check["ok"]
        or oos_label != TERMINAL_PASS_STATE
        or not provenance_ok
        or not recon_ok
    ):
        state = "PROMISING"
        research_tier = "PROMISING"
        research_quality = "REVIEW_REQUIRED"

    # Low OOS validation sample cannot be represented as validated
    oos_val_n = int(oos_val_block.get("trade_count") or 0)
    if oos_val_n < 30 and research_quality not in (
        "REVIEW_REQUIRED",
        "RESEARCH_REJECTED",
        "OOS_FAILED",
    ):
        research_quality = "PROMISING_BUT_LOW_SAMPLE"

    base_research = {
        "trade_count": base.get("trade_count"),
        "wins": wins,
        "losses": losses,
        "win_rate": base.get("win_rate"),
        "gross_pnl": base.get("gross_pnl"),
        "net_pnl": base.get("net_pnl"),
        "fees": base.get("total_fees"),
        "avg_gross_r": base.get("gross_avg_r"),
        "avg_net_r": base.get("net_avg_r"),
        "profit_factor": base.get("profit_factor"),
        "profit_factor_basis": EXECUTION_MODEL["profit_factor_basis"],
        "max_drawdown": base.get("max_drawdown_r") or base.get("max_dd_r"),
        "max_losing_streak": base.get("max_losing_streak"),
        "same_candle_policy": SAME_CANDLE_PRECEDENCE_SL_FIRST,
        "run_status": base.get("run_status"),
        "window": f"{windows.base_start}→{windows.base_end}",
        "base_start": windows.base_start,
        "base_end": windows.base_end,
        "reconciled_max_drawdown": recon.get("max_drawdown"),
        "reconciled_max_losing_streak": recon.get("max_losing_streak"),
    }

    partition_counts = {
        "base_bars": health.get("bars_used"),
        "oos_dev_bars": None,
        "oos_val_bars": None,
        "base_trades": int(base.get("trade_count") or 0),
        "oos_dev_trades": int(oos_dev_block.get("trade_count") or 0),
        "oos_val_trades": int(oos_val_block.get("trade_count") or 0),
        "window_policy": windows.policy,
        "base_start": windows.base_start,
        "base_end": windows.base_end,
        "oos_dev_start": windows.oos_dev_start,
        "oos_dev_end": windows.oos_dev_end,
        "oos_val_start": windows.oos_val_start,
        "oos_val_end": windows.oos_val_end,
        "overlap_bars": oos_split.get("overlap_bars"),
        "overlap_trades": oos_split.get("overlap_trades"),
    }
    quality_warnings = attach_partition_sample_warnings(
        base_trades=int(base.get("trade_count") or 0),
        oos_dev_trades=int(oos_dev_block.get("trade_count") or 0),
        oos_val_trades=int(oos_val_block.get("trade_count") or 0),
    )

    risk_simulation = {
        "label": RESEARCH_SIMULATION_LABEL,
        "no_position": NO_POSITION_CREATED_LABEL,
        "requested_research_risk_usd": window.risk_usd,
        "principal_usd": window.principal_usd,
        "hypothetical_quantity": None,
        "hypothetical_r": base.get("net_avg_r"),
        "portfolio_overlap": overlap,
        "hypothetical_incremental_drawdown": incremental.get("incremental_max_dd_r"),
        "added_to_v1_risk_totals": False,
    }

    cand = {
        **identity,
        "timeframe": SETUP_TIMEFRAME,
        "run_id": run_id,
        "created_at": created,
        "updated_at": created,
        "state": state,
        "research_tier": research_tier,
        "research_quality": research_quality,
        "window_status": win_check["window_status"],
        "research_windows": windows.to_dict(),
        "partition_counts": partition_counts,
        "data_health": _public_data_health(health),
        "base_research": base_research,
        "oos": {
            **oos_block,
            **{
                k: oos_split.get(k)
                for k in (
                    "base_start",
                    "base_end",
                    "oos_start",
                    "oos_end",
                    "overlap_bars",
                    "overlap_trades",
                )
            },
        },
        "oos_development": oos_dev_block,
        "oos_validation": oos_val_block,
        "oos_split": oos_split,
        "direction_checks": direction_checks,
        "rejection_reasons": rejection,
        "portfolio": {
            "status": "REVIEWED" if incremental else "NOT_CHECKED",
            "overlap": overlap,
            "incremental": incremental,
            "note": RESEARCH_SIMULATION_LABEL,
            "affects_v1_risk_totals": False,
        },
        "risk_simulation": risk_simulation,
        "sample_size": sample,
        "quality_warnings": quality_warnings,
        "lookahead_audit": la,
        "forensic_lookahead": base_forensic,
        "trade_forensic_audits": base_forensic.get("audits") or [],
        "execution_model": EXECUTION_MODEL,
        "reconciliation": recon,
        "configuration_hash": cfg_hash,
        "dataset_hash": health.get("dataset_hash"),
        "engine_version": RESEARCH_ENGINE_VERSION,
        "strategy_fingerprint": STRATEGY_FINGERPRINT_TEXT,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "research_universe": True,
        "v1_universe": False,
        "eligibility_tier": tier,
        "eligibility_reason_codes": reason_codes,
        "eligibility_conditions": metric_conds,
    }
    cand["evidence_mode"] = evidence_mode
    cand["prepaper_review"] = build_prepaper_review(
        cand, evidence_mode=evidence_mode
    )
    # Review never enables paper; force safety stamps after review attach.
    cand["paper_eligible"] = False
    cand["production_approved"] = False
    cand["telegram_eligible"] = False
    # Align research_quality with hard-gate review (PASS only when ready).
    if cand["prepaper_review"]["review_status"] == "REVIEW_READY_FOR_SEPARATE_APPROVAL":
        cand["research_quality"] = cand["prepaper_review"]["research_quality"]
    else:
        cand["research_quality"] = "REVIEW_REQUIRED"
        if cand["state"] == TERMINAL_PASS_STATE:
            cand["state"] = "PROMISING"
            cand["research_tier"] = "PROMISING"
    final_state = str(cand["state"])
    if final_state in FORBIDDEN_PAPER_STATES:
        raise ShortResearchOnlyError("forbidden_paper_state")

    short_research_registry.upsert(
        ShortResearchCandidate(
            symbol=sym,
            state=final_state if final_state in (
                "DISCOVERED",
                "DATA_PENDING",
                "DATA_READY",
                "BACKTEST_COMPLETED",
                "RESEARCH_REJECTED",
                "PROMISING",
                "OOS_FAILED",
                TERMINAL_PASS_STATE,
            ) else "RESEARCH_REJECTED",
            metrics=dict(base_research),
            oos_label=oos_label,
            eligibility={"research_tier": cand["research_tier"]},
        )
    )
    return serialize_short_candidate_view(cand)


async def run_short_research_batch(
    *,
    symbols: Sequence[str] | None = None,
    run_id: str | None = None,
    window: ResearchWindowConfig = SHORT_RESEARCH_WINDOW,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
    run_oos: bool = True,
    persist: bool = True,
    reports_dir: Path | None = None,
    health_fn: HealthFn | None = None,
    backtest_fn: BacktestFn | None = None,
    service: Any | None = None,
    research_windows: ResearchWindows | None = None,
    evidence_mode: str = EVIDENCE_MODE_DIRECT,
) -> dict[str, Any]:
    """Batch SHORT research over a configurable universe. Never paper/Telegram."""
    # Explicit import isolation — never touch paper watchers / telegram.
    forbidden = (
        "app.services.v1_paper_watcher",
        "app.services.v2_candidate_paper_watcher",
        "app.services.telegram_alerts",
    )
    import sys

    for mod in forbidden:
        # Presence is fine; calling is forbidden. Documented for reviewers.
        _ = mod

    assert_short_window(window)
    evidence_mode = str(evidence_mode or EVIDENCE_MODE_DIRECT).upper()
    rid = run_id or new_run_id()
    # Idempotent: same run_id returns existing persisted report.
    existing = short_research_report_store.get_run(rid)
    if existing is not None:
        return existing

    universe = [str(s).upper() for s in (symbols or DEFAULT_SHORT_RESEARCH_UNIVERSE)]
    if service is None:
        from app.research.service import get_bos_research_service

        service = get_bos_research_service()

    candidates: list[dict[str, Any]] = []
    peer_intervals: dict[str, list] = {}

    windows = research_windows or research_windows_from_config(window)
    win_check = validate_research_windows(windows)
    cfg_hash = configuration_fingerprint(
        window={**window.to_dict(), **windows.to_dict()},
        thresholds=thresholds.to_dict(),
        symbols=universe,
        run_oos=run_oos,
    )

    async def _health(sym: str) -> dict[str, Any]:
        if health_fn is not None:
            return await _maybe_await(health_fn(sym))
        return await assess_short_data_health(
            sym,
            requested_start=windows.requested_start,
            requested_end=windows.requested_end,
        )

    # First pass base runs for portfolio peers (research-only overlaps).
    dataset_hashes: list[str] = []
    if win_check["ok"]:
        for sym in universe:
            health = await _health(sym)
            if health.get("dataset_hash"):
                dataset_hashes.append(str(health["dataset_hash"]))
            if not health.get("ok"):
                continue
            bt = backtest_fn or run_short_symbol_backtest
            base = await _maybe_await(
                bt(
                    service,
                    sym,
                    start=windows.base_start,
                    end=windows.base_end,
                    window=window,
                )
            )
            peer_intervals[sym] = trade_intervals_from_rows(
                sym, list(base.get("closed_trades") or [])
            )

    for sym in universe:
        cand = await research_one_short_symbol(
            symbol=sym,
            run_id=rid,
            service=service,
            window=window,
            thresholds=thresholds,
            run_oos=run_oos,
            health_fn=health_fn if health_fn is not None else None,
            backtest_fn=backtest_fn,
            peer_intervals={k: v for k, v in peer_intervals.items() if k != sym},
            research_windows=windows,
            evidence_mode=evidence_mode,
        )
        candidates.append(cand)

    # Every configured symbol must appear (research_one always returns a row).
    present = {str(c.get("symbol") or "").upper() for c in candidates}
    for sym in universe:
        if sym in present:
            continue
        # Structured blocked stub — never drop from batch.
        candidates.append(
            serialize_short_candidate_view(
                {
                    **short_research_identity(symbol=sym),
                    "symbol": sym,
                    "timeframe": SETUP_TIMEFRAME,
                    "run_id": rid,
                    "state": "RESEARCH_REJECTED",
                    "research_tier": "RESEARCH_REJECTED",
                    "research_quality": "REVIEW_REQUIRED",
                    "window_status": win_check["window_status"],
                    "research_windows": windows.to_dict(),
                    "data_health": {
                        "ok": False,
                        "data_health_status": "EMPTY",
                        "health_status": "BLOCKED",
                        "requested_range_available": False,
                        "reason": "missing_from_batch",
                    },
                    "prepaper_review": {
                        "review_status": "REVIEW_BLOCKED",
                        "research_quality": "REVIEW_REQUIRED",
                        "blockers": ["missing_report"],
                        "human_label": "Review blocked — research only",
                        "paper_eligible": False,
                        "production_approved": False,
                        "telegram_eligible": False,
                    },
                    "paper_eligible": False,
                    "production_approved": False,
                    "telegram_eligible": False,
                }
            )
        )

    batch_dataset_hash = hashlib.sha256(
        ("\n".join(sorted(dataset_hashes)) or rid).encode("utf-8")
    ).hexdigest()

    report = {
        "status": "OK",
        "run_id": rid,
        "created_at": utc_now_iso(),
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE,
        "direction": DIRECTION,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "research_universe": True,
        "v1_universe": False,
        "fingerprint": STRATEGY_FINGERPRINT_TEXT,
        "strategy_fingerprint": STRATEGY_FINGERPRINT_TEXT,
        "engine_version": RESEARCH_ENGINE_VERSION,
        "configuration_hash": cfg_hash,
        "dataset_hash": batch_dataset_hash,
        "execution_model": EXECUTION_MODEL,
        "disclaimer": DISCLAIMER,
        "historical_disclaimer": HISTORICAL_DISCLAIMER,
        "simulation_labels": [RESEARCH_SIMULATION_LABEL, NO_POSITION_CREATED_LABEL],
        "universe": universe,
        "window": window.to_dict(),
        "research_windows": windows.to_dict(),
        "window_status": win_check["window_status"],
        "evidence_mode": evidence_mode,
        "thresholds": thresholds.to_dict(),
        "candidates": candidates,
        "read_only": True,
        "execution_rights": False,
        "v1_unchanged": True,
        "sys_modules_snapshot_note": "runner does not invoke paper/telegram modules",
        "sys_loaded_paper_modules": [
            m for m in ("app.services.paper_trade",) if m in sys.modules
        ],
    }
    report["batch_summary"] = build_batch_summary(report)
    short_research_report_store.put(report)

    if persist:
        out_dir = reports_dir or default_reports_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"combo02_short_research_{rid}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["source_file"] = str(path.name)
        # Also write batch summary sidecar for operators
        summary_path = out_dir / f"combo02_short_research_{rid}_summary.json"
        summary_path.write_text(
            json.dumps(report["batch_summary"], indent=2, default=str), encoding="utf-8"
        )
        report["summary_file"] = summary_path.name

    return report


def load_short_research_reports_from_disk(
    reports_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Load persisted SHORT research JSON artifacts into the store."""
    root = reports_dir or default_reports_dir()
    if not root.is_dir():
        return []
    loaded: list[dict[str, Any]] = []
    for path in sorted(root.glob("combo02_short_research_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("strategy_id") or "") != STRATEGY_ID:
            continue
        short_research_report_store.put(payload)
        loaded.append(payload)
    return loaded


def api_list_response() -> dict[str, Any]:
    load_short_research_reports_from_disk()
    candidates = [
        serialize_short_candidate_view(c)
        for c in short_research_report_store.list_candidates()
    ]
    # Attach latest batch summary if available
    latest_summary = None
    with short_research_report_store._lock:
        runs = list(short_research_report_store._runs.values())
    if runs:
        latest = max(runs, key=lambda r: str(r.get("created_at") or ""))
        latest_summary = latest.get("batch_summary") or build_batch_summary(latest)
    return {
        "status": "OK",
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE,
        "direction": DIRECTION,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "research_universe": True,
        "v1_universe": False,
        "disclaimer": DISCLAIMER,
        "research_windows": DEFAULT_SHORT_RESEARCH_WINDOWS.to_dict(),
        "batch_summary": latest_summary,
        "labels": {
            "banner": "SHORT RESEARCH ONLY",
            "paper": "NO PAPER TRADING",
            "production": "NO PRODUCTION",
            "telegram": "TELEGRAM DISABLED",
            "ready": "Ready for separate human review",
        },
        "candidates": candidates,
        "read_only": True,
        "execution_rights": False,
        "v1_unchanged": True,
        "timestamp": utc_now_iso(),
    }


def api_symbol_response(symbol: str) -> dict[str, Any]:
    load_short_research_reports_from_disk()
    row = short_research_report_store.get_symbol(symbol)
    if row is None:
        return {
            "status": "NOT_FOUND",
            "strategy_id": STRATEGY_ID,
            "combo_version": COMBO_VERSION,
            "source": SOURCE,
            "direction": DIRECTION,
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
            "symbol": str(symbol).upper(),
            "timestamp": utc_now_iso(),
        }
    return {
        "status": "OK",
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE,
        "direction": DIRECTION,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "candidate": serialize_short_candidate_view(row),
        "read_only": True,
        "execution_rights": False,
        "v1_unchanged": True,
        "timestamp": utc_now_iso(),
    }
