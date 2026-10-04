"""COMBO_02 SHORT research-only pipeline.

Signal generation / research backtest / OOS classification / candidate labels.
Never opens paper, never Telegram, never production, never v1 watcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.research.combo02_candidate_eligibility import (
    classify_eligibility,
    classify_oos,
    eligibility_report,
)
from app.research.combo02_candidate_thresholds import (
    DEFAULT_THRESHOLDS,
    EligibilityThresholds,
    ResearchWindowConfig,
)
from app.research.short_research_constants import (
    COMBO_ID,
    COMBO_VERSION,
    DIRECTION,
    DISCLAIMER,
    FORBIDDEN_PAPER_STATES,
    PAPER_ELIGIBLE,
    PRODUCTION_APPROVED,
    REJECTION_REASON,
    SETUP_TIMEFRAME,
    SHORT_RESEARCH_STATES,
    SOURCE,
    STRATEGY_FINGERPRINT_TEXT,
    STRATEGY_ID,
    TELEGRAM_ELIGIBLE,
    TERMINAL_PASS_STATE,
)
from app.research.trade_fees import enrich_trade_execution
from app.signals.schemas import Direction
from app.signals.trade_math import (
    SAME_CANDLE_PRECEDENCE_SL_FIRST,
    DirectionRequiredError,
    calculate_gross_pnl,
    require_direction,
    resolve_same_candle_exit,
    stop_distance,
    stop_triggered,
    take_profit_triggered,
    validate_trade_geometry,
)

# ---------------------------------------------------------------------------
# Integrity stamps — distinguish FULL pipeline from boundary-only stubs.
# SHORT isolation (paper/Telegram blocked) is NOT the same as a complete
# research pipeline. Never claim FULL when only helpers are present.
# ---------------------------------------------------------------------------
SHORT_RESEARCH_PIPELINE_COMPLETE = True
SHORT_RESEARCH_MODULE_KIND = "FULL_PIPELINE"
SHORT_RESEARCH_MODULE_INTEGRITY = "combo02_short_research.full.v1"
SHORT_RESEARCH_MODULE_INCOMPLETE_CODE = "short_research_module_incomplete"

REQUIRED_PUBLIC_API: tuple[str, ...] = (
    "short_research_identity",
    "is_short_research_identity",
    "assert_short_research_only_boundary",
    "assert_short_signal_geometry",
    "validate_short_research_signal",
    "classify_short_oos",
    "build_short_eligibility_output",
    "simulate_short_research_trade",
    "finalize_short_candidate",
    "ShortResearchCandidate",
    "ShortResearchRegistry",
    "ShortResearchOnlyError",
)


def assert_short_research_pipeline_complete() -> None:
    """Fail closed if this module is a boundary stub or API surface is incomplete."""
    if not SHORT_RESEARCH_PIPELINE_COMPLETE:
        raise RuntimeError(SHORT_RESEARCH_MODULE_INCOMPLETE_CODE)
    if SHORT_RESEARCH_MODULE_KIND != "FULL_PIPELINE":
        raise RuntimeError(SHORT_RESEARCH_MODULE_INCOMPLETE_CODE)
    missing = [name for name in REQUIRED_PUBLIC_API if name not in globals()]
    if missing:
        raise RuntimeError(
            f"{SHORT_RESEARCH_MODULE_INCOMPLETE_CODE}:missing={','.join(missing)}"
        )


def short_research_pipeline_status() -> dict[str, Any]:
    """Explicit status separating isolation availability from full research."""
    try:
        assert_short_research_pipeline_complete()
        pipeline_ok = True
        error = None
    except RuntimeError as exc:
        pipeline_ok = False
        error = str(exc)
    return {
        "short_isolation_available": True,
        "short_research_pipeline_available": pipeline_ok,
        "module_kind": SHORT_RESEARCH_MODULE_KIND,
        "module_integrity": SHORT_RESEARCH_MODULE_INTEGRITY,
        "pipeline_complete": SHORT_RESEARCH_PIPELINE_COMPLETE,
        "error": error,
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "source": SOURCE,
        "direction": DIRECTION,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }


# Policy A: base training ends before OOS development; OOS val after OOS dev.
# See app.research.short_research_windows.DEFAULT_SHORT_RESEARCH_WINDOWS.
SHORT_RESEARCH_WINDOW = ResearchWindowConfig(
    combination_id=COMBO_ID,
    direction=DIRECTION,
    setup_timeframe=SETUP_TIMEFRAME,
    require_htf_alignment=True,
    base_start="2025-01-01",
    base_end="2025-04-30",
    oos_dev_end="2025-06-30",
    oos_val_start="2025-07-01",
    oos_val_end="2026-01-31",
)


class ShortResearchOnlyError(PermissionError):
    """Raised when SHORT research identity reaches a paper/live boundary."""

    def __init__(self, detail: str | None = None) -> None:
        msg = REJECTION_REASON if not detail else f"{REJECTION_REASON}:{detail}"
        super().__init__(msg)


def short_research_identity(
    *,
    symbol: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical identity fields for every SHORT research candidate."""
    out: dict[str, Any] = {
        "strategy_id": STRATEGY_ID,
        "combo_version": COMBO_VERSION,
        "combo_id": COMBO_ID,
        "source": SOURCE,
        "direction": DIRECTION,
        "path": "A",
        "timeframe": SETUP_TIMEFRAME,
        "production_approved": PRODUCTION_APPROVED,
        "telegram_eligible": TELEGRAM_ELIGIBLE,
        "paper_eligible": PAPER_ELIGIBLE,
        "disclaimer": DISCLAIMER,
        "fingerprint": STRATEGY_FINGERPRINT_TEXT,
    }
    if symbol:
        out["symbol"] = str(symbol).upper()
    if extra:
        out.update(dict(extra))
    # Fail-closed overrides — never allow promotion via extra.
    out["strategy_id"] = STRATEGY_ID
    out["combo_version"] = COMBO_VERSION
    out["source"] = SOURCE
    out["direction"] = DIRECTION
    out["production_approved"] = False
    out["telegram_eligible"] = False
    out["paper_eligible"] = False
    return out


def is_short_research_identity(payload: Mapping[str, Any] | None) -> bool:
    if not payload or not isinstance(payload, Mapping):
        return False
    # Include sibling SHORT research identities (pullback-rejection, etc.).
    _SHORT_RESEARCH_IDS = {
        STRATEGY_ID,
        "SHORT_PULLBACK_REJECTION_RESEARCH",
        "COMBO_02_SHORT_ENTRY_RESEARCH",
        "COMBO_02_SHORT_DIAGNOSTICS",
    }
    _SHORT_RESEARCH_VERSIONS = {
        COMBO_VERSION,
        "v1-short-pullback-rejection",
        "v2-short-entry-research",
    }
    snip = payload.get("signal_snippet") if isinstance(payload.get("signal_snippet"), Mapping) else {}
    for src in (payload, snip):
        sid = str(src.get("strategy_id") or "")
        source = str(src.get("source") or "")
        version = str(src.get("combo_version") or "").lower()
        if (
            sid in _SHORT_RESEARCH_IDS
            or source == SOURCE
            or source == "SHORT_ENTRY_DIAGNOSTIC_PIPELINE"
            or source == "SHORT_RESEARCH_DIAGNOSTICS"
            or version in _SHORT_RESEARCH_VERSIONS
        ):
            return True
    return False


def payload_direction(payload: Mapping[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in ("direction", "side"):
        raw = payload.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip().upper()
    snip = payload.get("signal_snippet")
    if isinstance(snip, Mapping):
        raw = snip.get("direction") or snip.get("side")
        if raw is not None and str(raw).strip():
            return str(raw).strip().upper()
    return None


def assert_short_research_only_boundary(
    payload: Mapping[str, Any] | None = None,
    *,
    direction: str | None = None,
    detail: str | None = None,
) -> None:
    """Fail closed at paper/live boundaries for SHORT / SHORT research identity."""
    if is_short_research_identity(payload):
        raise ShortResearchOnlyError(detail or "identity")
    d = (direction or payload_direction(payload) or "").upper()
    if d == "SHORT":
        raise ShortResearchOnlyError(detail or "direction")


def assert_short_signal_geometry(
    *,
    direction: Direction | str | None,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
) -> None:
    """Reject malformed SHORT signals; never auto-correct prices."""
    d = require_direction(direction)
    assert d is Direction.SHORT
    geom = validate_trade_geometry(
        d, entry_price, stop_price, take_profit_price, require_take_profit=True
    )
    if not geom.ok:
        raise ValueError(geom.reason or "Invalid SHORT geometry")
    assert float(stop_price) > float(entry_price)
    assert float(take_profit_price) < float(entry_price)


def validate_short_research_signal(eval_result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate combination-engine / entry-engine SHORT research output."""
    if not eval_result or not isinstance(eval_result, Mapping):
        return {"ok": False, "reason": "missing_eval", "conditions": []}

    direction = str(eval_result.get("direction") or "").upper()
    status = str(eval_result.get("status") or "").upper()
    htf = eval_result.get("htf") or {}
    if not isinstance(htf, Mapping):
        htf = {}
    trend = eval_result.get("trend") or {}
    if not isinstance(trend, Mapping):
        trend = {}
    bos = eval_result.get("bos") or {}
    if not isinstance(bos, Mapping):
        bos = {}

    entry = eval_result.get("entry_price")
    if entry is None and isinstance(eval_result.get("entry"), Mapping):
        entry = (eval_result.get("entry") or {}).get("entry_price")
    stop = eval_result.get("stop_price")
    if stop is None and isinstance(eval_result.get("stop"), Mapping):
        stop = (eval_result.get("stop") or {}).get("final_stop")
    tp = eval_result.get("tp1")
    if tp is None:
        targets = eval_result.get("targets") or []
        if targets and isinstance(targets[0], Mapping):
            tp = targets[0].get("target_price")

    conditions = [
        {
            "rule": "direction_short",
            "actual": direction,
            "required": "SHORT",
            "passed": direction == "SHORT",
        },
        {
            "rule": "short_entry_status",
            "actual": status,
            "required": "SHORT_ENTRY_CANDIDATE",
            "passed": status == "SHORT_ENTRY_CANDIDATE",
        },
        {
            "rule": "bearish_structure",
            "actual": str(trend.get("trend") or ""),
            "required": "BEARISH",
            "passed": str(trend.get("trend") or "").upper() == "BEARISH",
        },
        {
            "rule": "bearish_bos",
            "actual": str(bos.get("direction") or ""),
            "required": "BEARISH_BOS",
            "passed": str(bos.get("direction") or "").upper() == "BEARISH_BOS"
            and str(bos.get("state") or "").upper() == "CONFIRMED",
        },
        {
            "rule": "bearish_htf_alignment",
            "actual": {
                "htf_alignment": htf.get("htf_alignment"),
                "trend_1h": htf.get("trend_1h"),
                "trend_4h": htf.get("trend_4h"),
            },
            "required": True,
            "passed": (
                str(htf.get("htf_alignment") or "").upper() == "HTF_ALIGNED"
                and str(htf.get("trend_1h") or "").upper() == "BEARISH"
                and str(htf.get("trend_4h") or "").upper() == "BEARISH"
            ),
        },
    ]

    geometry_ok = False
    geometry_actual: Any = None
    try:
        if entry is None or stop is None or tp is None:
            raise ValueError("missing prices")
        assert_short_signal_geometry(
            direction=direction if direction == "SHORT" else None,
            entry_price=float(entry),
            stop_price=float(stop),
            take_profit_price=float(tp),
        )
        geometry_ok = True
        geometry_actual = "tp < entry < stop"
    except (AssertionError, DirectionRequiredError, TypeError, ValueError) as exc:
        geometry_actual = str(exc)

    conditions.append(
        {
            "rule": "short_geometry",
            "actual": geometry_actual,
            "required": True,
            "passed": geometry_ok,
        }
    )
    ok = all(bool(c["passed"]) for c in conditions)
    return {
        "ok": ok,
        "reason": None if ok else "short_signal_validation_failed",
        "conditions": conditions,
        "direction": DIRECTION,
        "strategy_id": STRATEGY_ID,
    }


def classify_short_oos(
    *,
    base_tier: str,
    oos_trade_count: int | None,
    oos_net_avg_r: float | None,
    oos_net_pnl: float | None,
    oos_profit_factor: float | None,
    oos_max_dd_r: float | None,
    oos_max_lose_streak: int | None,
    oos_usable: bool,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """OOS classification for SHORT research — never returns V2_PAPER_CANDIDATE."""
    label, reasons, conditions = classify_oos(
        base_tier=base_tier,  # type: ignore[arg-type]
        oos_trade_count=oos_trade_count,
        oos_net_avg_r=oos_net_avg_r,
        oos_net_pnl=oos_net_pnl,
        oos_profit_factor=oos_profit_factor,
        oos_max_dd_r=oos_max_dd_r,
        oos_max_lose_streak=oos_max_lose_streak,
        oos_usable=oos_usable,
        thresholds=thresholds,
    )
    if label == "V2_PAPER_CANDIDATE":
        return TERMINAL_PASS_STATE, reasons, conditions
    if label == "OOS_FAIL":
        return "OOS_FAILED", reasons, conditions
    return str(label), reasons, conditions


def build_short_eligibility_output(
    *,
    symbol: str,
    trade_count: int,
    net_avg_r: float | None,
    net_pnl: float | None,
    profit_factor: float | None,
    max_dd_r: float | None,
    max_lose_streak: int | None,
    fee_share: float | None,
    bearish_htf_aligned: bool,
    entry_price: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    oos_label: str | None = None,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Direction-explicit eligibility payload for SHORT research."""
    tier, reason_codes, metric_conditions = classify_eligibility(
        trade_count=trade_count,
        net_avg_r=net_avg_r,
        net_pnl=net_pnl,
        profit_factor=profit_factor,
        max_dd_r=max_dd_r,
        max_lose_streak=max_lose_streak,
        fee_share=fee_share,
        thresholds=thresholds,
    )
    geometry_passed = False
    geometry_actual: Any = None
    if entry_price is not None and stop_price is not None and take_profit_price is not None:
        geom = validate_trade_geometry(
            Direction.SHORT, entry_price, stop_price, take_profit_price, require_take_profit=True
        )
        geometry_passed = geom.ok
        geometry_actual = "tp < entry < stop" if geom.ok else geom.reason
    else:
        geometry_actual = "prices_unavailable"

    conditions = [
        {
            "rule": "bearish_htf_alignment",
            "actual": bearish_htf_aligned,
            "required": True,
            "passed": bool(bearish_htf_aligned),
        },
        {
            "rule": "short_geometry",
            "actual": geometry_actual,
            "required": True,
            "passed": geometry_passed,
        },
        *metric_conditions,
    ]

    research_tier = "RESEARCH_REJECTED" if tier == "REJECT" else tier
    if oos_label == TERMINAL_PASS_STATE and tier == "PROMISING":
        research_tier = TERMINAL_PASS_STATE
    elif oos_label == "OOS_FAILED":
        research_tier = "OOS_FAILED"

    if research_tier in FORBIDDEN_PAPER_STATES:
        research_tier = "RESEARCH_REJECTED"

    identity = short_research_identity(symbol=symbol)
    return {
        **identity,
        "symbol": str(symbol).upper(),
        "research_tier": research_tier,
        "base_tier": tier,
        "oos_label": oos_label,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "reason_codes": reason_codes,
        "conditions": conditions,
        "metrics": {
            "trade_count": trade_count,
            "net_avg_r": net_avg_r,
            "net_pnl": net_pnl,
            "profit_factor": profit_factor,
            "max_dd_r": max_dd_r,
            "max_losing_streak": max_lose_streak,
            "fee_share": fee_share,
        },
    }


@dataclass
class ShortResearchCandidate:
    symbol: str
    state: str = "DISCOVERED"
    metrics: dict[str, Any] = field(default_factory=dict)
    oos_label: str | None = None
    eligibility: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        identity = short_research_identity(symbol=self.symbol)
        return {
            **identity,
            "state": self.state,
            "research_tier": self.eligibility.get("research_tier") or self.state,
            "oos_label": self.oos_label,
            "metrics": self.metrics,
            "eligibility": self.eligibility,
            "paper_eligible": False,
            "production_approved": False,
            "telegram_eligible": False,
        }


class ShortResearchRegistry:
    """In-memory SHORT research registry — never promotes to paper states."""

    def __init__(self) -> None:
        self._rows: dict[str, ShortResearchCandidate] = {}

    def upsert(self, candidate: ShortResearchCandidate) -> dict[str, Any]:
        state = str(candidate.state or "").upper()
        if state not in SHORT_RESEARCH_STATES:
            raise ValueError(f"illegal short research state: {state}")
        if state in FORBIDDEN_PAPER_STATES:
            raise ShortResearchOnlyError("forbidden_paper_state")
        candidate.state = state
        self._rows[candidate.symbol.upper()] = candidate
        return candidate.to_dict()

    def get(self, symbol: str) -> dict[str, Any] | None:
        row = self._rows.get(str(symbol).upper())
        return row.to_dict() if row else None

    def list_all(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._rows.values()]

    def promote_to_paper(self, symbol: str) -> None:
        raise ShortResearchOnlyError("promote_to_paper")

    def set_production_approved(self, symbol: str, value: bool = True) -> None:
        _ = symbol
        if value:
            raise ShortResearchOnlyError("production_approved")

    def set_telegram_eligible(self, symbol: str, value: bool = True) -> None:
        _ = symbol
        if value:
            raise ShortResearchOnlyError("telegram_eligible")


short_research_registry = ShortResearchRegistry()


def simulate_short_research_trade(
    *,
    entry_price: float,
    stop_price: float,
    take_profit_price: float,
    quantity: float | None = None,
    risk_usd: float = 20.0,
    candle_high: float,
    candle_low: float,
    entry_type: str = "MARKET",
    taker_fee: float = 0.0004,
    maker_fee: float = 0.0002,
) -> dict[str, Any]:
    """One-bar SHORT research simulator using Phase 1 primitives + fees."""
    assert_short_signal_geometry(
        direction=Direction.SHORT,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )
    dist = stop_distance(Direction.SHORT, entry_price, stop_price)
    qty = float(quantity) if quantity is not None else float(risk_usd) / dist
    if qty <= 0:
        raise ValueError("non-positive quantity")

    resolution = resolve_same_candle_exit(
        Direction.SHORT,
        candle_high,
        candle_low,
        stop_price,
        take_profit_price,
        precedence=SAME_CANDLE_PRECEDENCE_SL_FIRST,
    )
    hit_stop = stop_triggered(Direction.SHORT, candle_high, candle_low, stop_price)
    hit_tp = take_profit_triggered(
        Direction.SHORT, candle_high, candle_low, take_profit_price
    )
    outcome = resolution["outcome"]
    exit_price = resolution["exit_price"]
    gross = None
    r_gross = None
    if exit_price is not None:
        gross = calculate_gross_pnl(Direction.SHORT, entry_price, float(exit_price), qty)
        r_gross = gross / float(risk_usd) if risk_usd else None

    trade = {
        "direction": "SHORT",
        "entry_price": entry_price,
        "stop_price": stop_price,
        "exit_price": exit_price,
        "r_multiple": r_gross,
        "entry_type": entry_type,
        "condition_snapshot": {"entry_type": entry_type},
        **short_research_identity(),
    }
    enriched = enrich_trade_execution(
        trade,
        risk_usd=risk_usd,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
    )
    net_pnl = enriched.get("net_pnl_usd")
    return {
        "direction": "SHORT",
        "strategy_id": STRATEGY_ID,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "quantity": qty,
        "stop_distance": dist,
        "hit_stop": hit_stop,
        "hit_tp": hit_tp,
        "outcome": outcome,
        "exit_price": exit_price,
        "ambiguous": resolution["ambiguous"],
        "precedence": resolution["precedence"],
        "gross_pnl": enriched.get("gross_pnl_usd", gross),
        "r_multiple": enriched.get("r_net") if enriched.get("r_net") is not None else r_gross,
        "r_gross": enriched.get("r_gross", r_gross),
        "fees": float(enriched.get("fee_total_usd") or 0.0),
        "net_pnl": net_pnl,
        "equity_delta": net_pnl,
        "production_approved": False,
        "telegram_eligible": False,
        "paper_eligible": False,
    }


def assert_short_window(window: ResearchWindowConfig) -> None:
    """SHORT research window freeze — separate from LONG candidate runner."""
    if window.combination_id != COMBO_ID:
        raise RuntimeError("SHORT research must use COMBO_02")
    if window.setup_timeframe != SETUP_TIMEFRAME:
        raise RuntimeError("SHORT research must use 1h setup TF")
    if str(window.direction or "").upper() != "SHORT":
        raise RuntimeError("SHORT research window must be SHORT")
    if not window.require_htf_alignment:
        raise RuntimeError("SHORT research must require HTF alignment")


async def run_short_symbol_backtest(
    service: Any,
    symbol: str,
    *,
    start: str,
    end: str,
    window: ResearchWindowConfig = SHORT_RESEARCH_WINDOW,
    risk_usd: float | None = None,
) -> dict[str, Any]:
    """Research backtest via strategy_matrix(direction=SHORT). Never paper."""
    from app.research.combo02_candidate_research import (
        compute_trade_metrics,
        map_run_status,
    )
    from app.research.trade_fees import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE

    assert_short_window(window)
    matrix = await service.strategy_matrix(
        combination_id=window.combination_id,
        symbols=[symbol],
        timeframes=[window.setup_timeframe],
        direction="SHORT",
        limit=20000,
        risk_usd=float(risk_usd if risk_usd is not None else window.risk_usd),
        start_date=start,
        end_date=end,
        taker_fee=DEFAULT_TAKER_FEE,
        maker_fee=DEFAULT_MAKER_FEE,
        include_trades=True,
    )
    rows = matrix.get("rows") or []
    row = rows[0] if rows else {}
    from app.research.short_research_forensics import enrich_trade_forensic_fields

    trades = list(row.get("trades") or [])
    for t in trades:
        if str(t.get("direction") or "").upper() not in ("", "SHORT"):
            raise RuntimeError("SHORT research backtest received non-SHORT trade")
    trades = [enrich_trade_forensic_fields({**t, "direction": "SHORT"}) for t in trades]
    coverage = {
        "ohlcv_start_utc": row.get("period_start"),
        "ohlcv_end_utc": row.get("period_end"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
    }
    metrics = compute_trade_metrics(trades, coverage=coverage)
    status = map_run_status(
        engine_status=str(row.get("status") or matrix.get("status") or ""),
        metrics=metrics,
    )
    return {
        **short_research_identity(symbol=symbol),
        "run_status": status,
        "window_start": start,
        "window_end": end,
        "engine_status": row.get("status") or matrix.get("status"),
        **metrics,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }


def finalize_short_candidate(
    *,
    symbol: str,
    base_metrics: Mapping[str, Any],
    oos_metrics: Mapping[str, Any] | None = None,
    oos_usable: bool = False,
    bearish_htf_aligned: bool = True,
    entry_price: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    """Classify SHORT research candidate; terminal pass = SHORT_RESEARCH_CANDIDATE."""
    elig = eligibility_report(
        trade_count=int(base_metrics.get("trade_count") or 0),
        net_avg_r=base_metrics.get("net_avg_r"),
        net_pnl=base_metrics.get("net_pnl"),
        profit_factor=base_metrics.get("profit_factor"),
        max_dd_r=base_metrics.get("max_dd_r"),
        max_lose_streak=base_metrics.get("max_losing_streak"),
        fee_share=base_metrics.get("fee_share"),
        thresholds=thresholds,
    )
    base_tier = "PROMISING" if elig.get("passed") else (
        "INSUFFICIENT_DATA"
        if str(elig.get("tier")) == "INSUFFICIENT_DATA"
        else ("WATCHLIST" if str(elig.get("tier")) == "WATCHLIST" else "REJECT")
    )
    # eligibility_report maps REJECT -> RESEARCH_REJECTED display
    raw_tier = str(elig.get("tier") or "")
    if raw_tier == "RESEARCH_REJECTED":
        base_tier = "REJECT"
    elif raw_tier in ("PROMISING", "WATCHLIST", "INSUFFICIENT_DATA"):
        base_tier = raw_tier

    oos_label = None
    oos_reasons: list[str] = []
    if oos_metrics is not None or oos_usable:
        oos_label, oos_reasons, _ = classify_short_oos(
            base_tier=base_tier,
            oos_trade_count=(oos_metrics or {}).get("trade_count"),
            oos_net_avg_r=(oos_metrics or {}).get("net_avg_r"),
            oos_net_pnl=(oos_metrics or {}).get("net_pnl"),
            oos_profit_factor=(oos_metrics or {}).get("profit_factor"),
            oos_max_dd_r=(oos_metrics or {}).get("max_dd_r"),
            oos_max_lose_streak=(oos_metrics or {}).get("max_losing_streak"),
            oos_usable=oos_usable,
            thresholds=thresholds,
        )

    state = "BACKTEST_COMPLETED"
    if base_tier == "REJECT":
        state = "RESEARCH_REJECTED"
    elif base_tier == "PROMISING" and oos_label == TERMINAL_PASS_STATE:
        state = TERMINAL_PASS_STATE
    elif base_tier == "PROMISING" and oos_label in ("OOS_FAILED", "PROMISING_NEEDS_MORE_EVIDENCE"):
        state = "OOS_FAILED" if oos_label == "OOS_FAILED" else "PROMISING"
    elif base_tier == "PROMISING":
        state = "PROMISING"

    report = build_short_eligibility_output(
        symbol=symbol,
        trade_count=int(base_metrics.get("trade_count") or 0),
        net_avg_r=base_metrics.get("net_avg_r"),
        net_pnl=base_metrics.get("net_pnl"),
        profit_factor=base_metrics.get("profit_factor"),
        max_dd_r=base_metrics.get("max_dd_r"),
        max_lose_streak=base_metrics.get("max_losing_streak"),
        fee_share=base_metrics.get("fee_share"),
        bearish_htf_aligned=bearish_htf_aligned,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        oos_label=oos_label,
        thresholds=thresholds,
    )
    cand = ShortResearchCandidate(
        symbol=str(symbol).upper(),
        state=state,
        metrics=dict(base_metrics),
        oos_label=oos_label,
        eligibility=report,
    )
    stored = short_research_registry.upsert(cand)
    stored["oos_reasons"] = oos_reasons
    return stored
