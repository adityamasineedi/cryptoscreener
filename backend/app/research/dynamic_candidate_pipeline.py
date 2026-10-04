"""Research / OOS / portfolio gate for dynamic registry candidates.

Uses frozen COMBO_02 via existing candidate research helpers — no parameter
tuning, no v1 promotion, no Telegram, risk_percent stays 0 until operator API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.research.candidate_state_machine import OperatorOnlyTransition
from app.research.combo02_candidate_eligibility import (
    classify_eligibility,
    classify_oos,
    eligibility_report,
    fee_pct_of_gross,
    portfolio_recommendation,
)
from app.research.combo02_candidate_research import (
    backtest_symbol,
    strategy_fingerprint,
)
from app.research.combo02_candidate_thresholds import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WINDOW,
    PORTFOLIO_HIGH_CORR,
    PORTFOLIO_HIGH_ENTRY_OVERLAP_PCT,
    EligibilityThresholds,
    ResearchWindowConfig,
)
from app.research.dynamic_candidate_constants import DISCLAIMER, STRATEGY_ID
from app.research.strategy_candidate_registry import strategy_candidate_registry

logger = structlog.get_logger(__name__)


def _map_tier_to_state(tier: str) -> str:
    t = str(tier or "").upper()
    if t == "PROMISING":
        return "PROMISING"
    # WATCHLIST is display-only → store as RESEARCH_REJECTED with reason
    return "RESEARCH_REJECTED"


async def _get_research_service() -> Any:
    from app.research.service import BosResearchService

    return BosResearchService()


async def run_candidate_backtest_gate(
    symbol: str,
    *,
    window: ResearchWindowConfig | None = None,
    thresholds: EligibilityThresholds | None = None,
) -> dict[str, Any]:
    """DATA_READY → BACKTEST_* → PROMISING | RESEARCH_REJECTED.

    Never transitions to PAPER_VALIDATING / APPROVED.
    """
    sym = str(symbol).upper().strip()
    row = await strategy_candidate_registry.get_by_symbol(sym)
    if row is None:
        return {"status": "NOT_FOUND", "symbol": sym}
    if str(row.get("state") or "") not in ("DATA_READY", "BACKTEST_QUEUED"):
        return {
            "status": "SKIP",
            "symbol": sym,
            "reason": f"state={row.get('state')} not ready for backtest",
        }

    win = window or DEFAULT_WINDOW
    thr = thresholds or DEFAULT_THRESHOLDS
    fp = strategy_fingerprint(win)

    if str(row.get("state")) == "DATA_READY":
        await strategy_candidate_registry.transition(
            sym,
            "BACKTEST_QUEUED",
            reason="queued_frozen_combo02_research",
            actor="dynamic_candidate_pipeline",
        )
    await strategy_candidate_registry.transition(
        sym,
        "BACKTEST_RUNNING",
        reason="running_frozen_combo02_research",
        actor="dynamic_candidate_pipeline",
    )

    service = await _get_research_service()
    try:
        run = await backtest_symbol(
            service,
            sym,
            start=win.base_start,
            end=win.base_end,
            window=win,
        )
    except Exception as exc:  # noqa: BLE001
        await strategy_candidate_registry.transition(
            sym,
            "RESEARCH_REJECTED",
            reason=f"ENGINE_ERROR:{exc}",
            actor="dynamic_candidate_pipeline",
            extra_fields={
                "backtest_status": "ENGINE_ERROR",
                "backtest_engine_fingerprint": fp,
                "risk_percent": 0.0,
            },
        )
        return {"status": "ENGINE_ERROR", "symbol": sym, "error": str(exc)}

    fee_share = fee_pct_of_gross(run.get("gross_pnl"), run.get("fees"))
    metrics_kwargs = dict(
        trade_count=int(run.get("trade_count") or 0),
        net_avg_r=run.get("net_avg_r"),
        net_pnl=run.get("net_pnl"),
        profit_factor=run.get("profit_factor"),
        max_dd_r=run.get("max_drawdown_r"),
        max_lose_streak=run.get("max_losing_streak"),
        fee_share=fee_share,
        thresholds=thr,
    )
    tier, reasons, conditions = classify_eligibility(**metrics_kwargs)
    elig = eligibility_report(**metrics_kwargs)
    # Display label only — WATCHLIST is not an executable registry state.
    display_tier = tier
    target_state = _map_tier_to_state(tier)
    reason = ",".join(reasons) if reasons else display_tier
    prev_port = row.get("portfolio_report") if isinstance(row.get("portfolio_report"), dict) else {}

    fields = {
        "backtest_window_start_utc": win.base_start,
        "backtest_window_end_utc": win.base_end,
        "backtest_engine_fingerprint": fp,
        "backtest_status": run.get("run_status") or "COMPLETED",
        "backtest_tier": display_tier,
        "backtest_trade_count": int(run.get("trade_count") or 0),
        "backtest_win_rate": run.get("win_rate"),
        "backtest_net_avg_r": run.get("net_avg_r"),
        "backtest_profit_factor": run.get("profit_factor"),
        "backtest_net_pnl": run.get("net_pnl"),
        "backtest_fees": run.get("fees"),
        "backtest_max_dd_r": run.get("max_drawdown_r"),
        "backtest_max_losing_streak": run.get("max_losing_streak"),
        "portfolio_report": {
            **prev_port,
            "base_eligibility": elig,
        },
        "risk_percent": 0.0,
    }
    await strategy_candidate_registry.transition(
        sym,
        target_state,
        reason=f"{display_tier}:{reason}",
        actor="dynamic_candidate_pipeline",
        extra_fields=fields,
    )

    # Hard assert: jobs cannot promote
    final = await strategy_candidate_registry.get_by_symbol(sym)
    assert final is not None
    if str(final.get("state")) in ("PAPER_VALIDATING", "APPROVED"):
        raise OperatorOnlyTransition("pipeline illegally reached operator-only state")
    assert float(final.get("risk_percent") or 0) == 0.0
    assert final.get("telegram_eligible") is False

    return {
        "status": "OK",
        "symbol": sym,
        "state": final.get("state"),
        "tier": display_tier,
        "conditions": conditions,
        "strategy_id": STRATEGY_ID,
        "telegram_eligible": False,
        "risk_percent": 0.0,
        "disclaimer": DISCLAIMER,
    }


async def run_candidate_oos_portfolio_gate(
    symbol: str,
    *,
    window: ResearchWindowConfig | None = None,
    thresholds: EligibilityThresholds | None = None,
    portfolio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PROMISING → OOS_PENDING → V2_PAPER_CANDIDATE | OOS_FAILED.

    Portfolio overlap must also pass documented thresholds for V2_PAPER_CANDIDATE.
    """
    sym = str(symbol).upper().strip()
    row = await strategy_candidate_registry.get_by_symbol(sym)
    if row is None:
        return {"status": "NOT_FOUND", "symbol": sym}
    if str(row.get("state") or "") != "PROMISING":
        return {
            "status": "SKIP",
            "symbol": sym,
            "reason": f"state={row.get('state')} not PROMISING",
        }

    win = window or DEFAULT_WINDOW
    thr = thresholds or DEFAULT_THRESHOLDS

    await strategy_candidate_registry.transition(
        sym,
        "OOS_PENDING",
        reason="oos_and_portfolio_gate",
        actor="dynamic_candidate_pipeline",
    )

    service = await _get_research_service()
    earliest = row.get("ohlcv_1h_start_utc")
    earliest_s = None
    if earliest is not None:
        earliest_s = str(earliest)[:10] if not hasattr(earliest, "strftime") else earliest.strftime("%Y-%m-%d")

    if not earliest_s:
        earliest_s = win.base_start

    try:
        val = await backtest_symbol(
            service,
            sym,
            start=win.oos_val_start,
            end=win.oos_val_end,
            window=win,
        )
        usable = val.get("run_status") in ("COMPLETED", "NO_TRADES")
    except Exception as exc:  # noqa: BLE001
        await strategy_candidate_registry.transition(
            sym,
            "OOS_FAILED",
            reason=f"OOS_ENGINE_ERROR:{exc}",
            actor="dynamic_candidate_pipeline",
            extra_fields={"oos_status": "ENGINE_ERROR", "risk_percent": 0.0},
        )
        return {"status": "ENGINE_ERROR", "symbol": sym, "error": str(exc)}

    oos_label, oos_reasons, oos_conds = classify_oos(
        base_tier="PROMISING",
        oos_trade_count=int(val.get("trade_count") or 0),
        oos_net_avg_r=val.get("net_avg_r"),
        oos_net_pnl=val.get("net_pnl"),
        oos_profit_factor=val.get("profit_factor"),
        oos_max_dd_r=val.get("max_drawdown_r"),
        oos_max_lose_streak=val.get("max_losing_streak"),
        oos_usable=usable,
        thresholds=thr,
    )

    # Portfolio: accept injected research metrics or defaults from row/backtest fields.
    port = portfolio or {}
    overlap_btc = port.get("portfolio_overlap_btc", row.get("portfolio_overlap_btc"))
    overlap_eth = port.get("portfolio_overlap_eth", row.get("portfolio_overlap_eth"))
    overlap_sol = port.get("portfolio_overlap_sol", row.get("portfolio_overlap_sol"))
    peak = port.get("peak_concurrent_positions", row.get("peak_concurrent_positions"))
    inc_dd = port.get(
        "portfolio_incremental_dd_r", row.get("portfolio_incremental_dd_r")
    )
    corr_btc = port.get("corr_daily_btc")
    incremental = port.get("incremental_portfolio") or {
        "peak_concurrent_with_v1": peak,
        "incremental_max_dd_r": inc_dd,
    }
    rec = portfolio_recommendation(
        symbol=sym,
        base_tier="PROMISING",
        oos_label=oos_label,  # type: ignore[arg-type]
        overlap_btc=float(overlap_btc) if overlap_btc is not None else None,
        overlap_eth=float(overlap_eth) if overlap_eth is not None else None,
        overlap_sol=float(overlap_sol) if overlap_sol is not None else None,
        corr_daily_btc=float(corr_btc) if corr_btc is not None else None,
        incremental=incremental,
    )

    portfolio_fail = False
    portfolio_reasons: list[str] = []
    for name, val_ov in (
        ("BTC", overlap_btc),
        ("ETH", overlap_eth),
        ("SOL", overlap_sol),
    ):
        if val_ov is not None and float(val_ov) >= PORTFOLIO_HIGH_ENTRY_OVERLAP_PCT:
            portfolio_fail = True
            portfolio_reasons.append(f"high_overlap_{name}={val_ov}")
    if corr_btc is not None and float(corr_btc) >= PORTFOLIO_HIGH_CORR:
        portfolio_fail = True
        portfolio_reasons.append(f"high_corr_btc={corr_btc}")

    prev_port = row.get("portfolio_report") if isinstance(row.get("portfolio_report"), dict) else {}
    oos_fields = {
        "oos_status": oos_label,
        "oos_window_start_utc": win.oos_val_start,
        "oos_window_end_utc": win.oos_val_end,
        "oos_trade_count": int(val.get("trade_count") or 0),
        "oos_net_avg_r": val.get("net_avg_r"),
        "oos_profit_factor": val.get("profit_factor"),
        "oos_net_pnl": val.get("net_pnl"),
        "oos_max_dd_r": val.get("max_drawdown_r"),
        "oos_max_losing_streak": val.get("max_losing_streak"),
        "portfolio_overlap_btc": overlap_btc,
        "portfolio_overlap_eth": overlap_eth,
        "portfolio_overlap_sol": overlap_sol,
        "peak_concurrent_positions": peak,
        "portfolio_incremental_dd_r": inc_dd,
        "portfolio_report": {
            **prev_port,
            **rec,
            "base_eligibility": prev_port.get("base_eligibility"),
            "oos_conditions": oos_conds,
            "oos_reasons": oos_reasons,
            "oos_eligibility": {
                "tier": oos_label,
                "passed": oos_label == "V2_PAPER_CANDIDATE" and not portfolio_fail,
                "reasons": oos_conds,
                "reason_codes": oos_reasons,
            },
            "portfolio_fail": portfolio_fail,
            "portfolio_status": "FAIL" if portfolio_fail else "PASS",
            "portfolio_reasons": portfolio_reasons,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "risk_percent": 0.0,
    }

    pass_oos = oos_label == "V2_PAPER_CANDIDATE" and not portfolio_fail
    if pass_oos:
        await strategy_candidate_registry.transition(
            sym,
            "V2_PAPER_CANDIDATE",
            reason="oos_and_portfolio_passed",
            actor="dynamic_candidate_pipeline",
            extra_fields=oos_fields,
        )
    else:
        reason = ",".join(oos_reasons + portfolio_reasons) or oos_label
        await strategy_candidate_registry.transition(
            sym,
            "OOS_FAILED",
            reason=reason,
            actor="dynamic_candidate_pipeline",
            extra_fields=oos_fields,
        )

    final = await strategy_candidate_registry.get_by_symbol(sym)
    assert final is not None
    assert final.get("telegram_eligible") is False
    assert float(final.get("risk_percent") or 0) == 0.0
    assert final.get("operator_approved") is False
    if str(final.get("state")) in ("PAPER_VALIDATING", "APPROVED"):
        raise OperatorOnlyTransition("oos gate illegally reached operator-only state")

    return {
        "status": "OK",
        "symbol": sym,
        "state": final.get("state"),
        "oos_label": oos_label,
        "portfolio_fail": portfolio_fail,
        "telegram_eligible": False,
        "risk_percent": 0.0,
        "operator_approved": False,
        "disclaimer": DISCLAIMER,
    }


async def background_promote_forbidden(symbol: str, target: str) -> None:
    """Helper used by tests — must raise for PAPER_VALIDATING / APPROVED."""
    await strategy_candidate_registry.transition(
        symbol,
        target,
        reason="background_attempt",
        actor="background_job",
        operator_approved_action=False,
        allow_background=False,
    )
