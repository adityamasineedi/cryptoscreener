"""Orchestrate trade-plan forensics (research-only, no production side effects)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.research.trade_plan_forensics.aggregates import (
    cross_tabs,
    losing_cluster_notes,
    summarize,
    winner_loser_compare,
)
from app.research.trade_plan_forensics.hypotheses import evaluate_hypotheses, issue_table
from app.research.trade_plan_forensics.ingest import ingest_trades
from app.research.trade_plan_forensics.reconstruct import CandleCache, reconstruct_trade
from app.research.trade_plan_forensics.report import build_markdown, write_reports
from app.research.trade_plan_forensics.thresholds import (
    SAFETY_NO_PROD_CHANGE,
    SAFETY_NO_STRATEGY_SELECT,
)
from app.signals.config import SignalConfig

_LAST_REPORT: dict[str, Any] | None = None
_LAST_BY_ID: dict[str, dict[str, Any]] = {}


async def run_trade_plan_forensics(
    *,
    source: str = "auto",
    json_path: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    start: str | None = None,
    end: str | None = None,
    strategy: str | None = None,
    max_trades: int = 150,
    write_files: bool = True,
) -> dict[str, Any]:
    global _LAST_REPORT, _LAST_BY_ID

    trades, ingest_meta = await ingest_trades(
        source=source,
        json_path=json_path,
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        strategy=strategy,
    )
    if not trades:
        report = {
            "status": "STOP",
            "reason": "NO_TRADES_AVAILABLE",
            "message": (
                "No historical trades found in paper_trades, backtest job, or research JSON. "
                "Run a backtest or supply a trade JSON — do not fabricate trades."
            ),
            "ingest": ingest_meta,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "safety": [SAFETY_NO_PROD_CHANGE, SAFETY_NO_STRATEGY_SELECT],
        }
        _LAST_REPORT = report
        _LAST_BY_ID = {}
        return report

    truncated = False
    if len(trades) > max_trades:
        trades = trades[:max_trades]
        truncated = True

    cache = CandleCache()
    scfg = SignalConfig()
    records: list[dict[str, Any]] = []
    stopped: list[dict[str, Any]] = []
    for t in trades:
        try:
            rec = await reconstruct_trade(t, cache=cache, signal_config=scfg)
        except Exception as exc:  # noqa: BLE001
            stopped.append(
                {
                    "trade_id": t.trade_id,
                    "status": "STOP",
                    "reason": "RECONSTRUCT_ERROR",
                    "error": str(exc),
                    "trade": t.to_dict(),
                }
            )
            continue
        if rec.get("status") == "OK":
            records.append(rec)
        else:
            stopped.append(rec)

    if not records:
        report = {
            "status": "STOP",
            "reason": "OHLCV_OR_ENTRY_CONTEXT_UNAVAILABLE",
            "message": (
                "Trades were found but required historical candles / entry timestamps "
                "were unavailable. No synthetic data was used."
            ),
            "ingest": ingest_meta,
            "stopped": stopped[:20],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "safety": [SAFETY_NO_PROD_CHANGE, SAFETY_NO_STRATEGY_SELECT],
        }
        _LAST_REPORT = report
        _LAST_BY_ID = {}
        return report

    hyps = evaluate_hypotheses(records)
    issues = issue_table(hyps)
    tabs = cross_tabs(records)
    pop = summarize(records)

    missing = [
        "FVG engine: UNAVAILABLE in codebase (not fabricated)",
        "paper_trades.fees / balance: often UNAVAILABLE",
        "bars_from_pullback_to_entry / bars_from_retest_to_entry: UNAVAILABLE "
        "(engine does not expose start indices)",
    ]
    sample_warnings = []
    if pop.get("sample_status") != "MORE_RELIABLE_DESCRIPTIVE_SAMPLE":
        sample_warnings.append(
            f"Overall sample_status={pop.get('sample_status')} (n={pop.get('n')})"
        )
    if truncated:
        sample_warnings.append(
            f"Input truncated to max_trades={max_trades} for runtime bounds."
        )

    # Losing forensic records (phase 16)
    loss_records = []
    for r in records:
        if r.get("outcome_class") != "LOSS":
            continue
        loss_records.append(
            {
                "trade_id": r.get("trade_id"),
                "symbol": (r.get("trade") or {}).get("symbol"),
                "timeframe": (r.get("trade") or {}).get("timeframe"),
                "direction": (r.get("trade") or {}).get("direction"),
                "regime": (r.get("regime") or {}).get("primary"),
                "htf_state": r.get("htf_state"),
                "entry_timing": (r.get("entry_timing") or {}).get("class"),
                "bars_since_bos": (r.get("local_structure") or {}).get("bars_since_bos"),
                "bos_distance_atr": (r.get("local_structure") or {}).get("bos_distance_atr"),
                "atr_percent": (r.get("entry_context") or {}).get("atr_percent"),
                "rvol": (r.get("entry_context") or {}).get("rvol"),
                "sweep_state": (r.get("liquidity") or {}).get("state"),
                "fvg_state": (r.get("fvg") or {}).get("state"),
                "sd_state": (r.get("supply_demand") or {}).get("state"),
                "pullback_state": ((r.get("local_structure") or {}).get("pullback") or {}).get(
                    "state"
                ),
                "retest_state": ((r.get("local_structure") or {}).get("retest") or {}).get(
                    "state"
                ),
                "mae": (r.get("stop_forensics") or {}).get("path", {}).get("mae_r"),
                "mfe": (r.get("tp_forensics") or {}).get("mfe_r"),
                "exit_reason": (r.get("trade") or {}).get("exit_reason"),
            }
        )

    report: dict[str, Any] = {
        "status": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ingest": {**ingest_meta, "truncated": truncated, "max_trades": max_trades},
        "population": {"ok": len(records), "stopped": len(stopped)},
        "population_metrics": pop,
        "data_quality": {
            "no_lookahead": True,
            "stopped_reasons": _count_reasons(stopped),
            "missing_fields_policy": "UNKNOWN/UNAVAILABLE — no guesses",
        },
        "cross_tabs": tabs,
        "winners_vs_losers": winner_loser_compare(records),
        "losing_cluster_notes": losing_cluster_notes(records),
        "losing_trade_records": loss_records,
        "hypotheses": hyps,
        "issue_table": issues,
        "missing_data": missing,
        "sample_warnings": sample_warnings,
        "trades": records,
        "stopped_trades": stopped,
        "safety": [SAFETY_NO_PROD_CHANGE, SAFETY_NO_STRATEGY_SELECT],
        "markdown": None,
        "files": None,
    }
    report["markdown"] = build_markdown(report)
    if write_files:
        report["files"] = write_reports(report)

    _LAST_REPORT = report
    _LAST_BY_ID = {str(r.get("trade_id")): r for r in records}
    return report


def get_trade_forensic_detail(trade_id: str) -> dict[str, Any]:
    if trade_id in _LAST_BY_ID:
        return {
            "status": "OK",
            "trade": _LAST_BY_ID[trade_id],
            "safety": [SAFETY_NO_PROD_CHANGE, SAFETY_NO_STRATEGY_SELECT],
        }
    if _LAST_REPORT is None:
        return {
            "status": "NOT_FOUND",
            "reason": "NO_REPORT_YET",
            "message": "Run GET /api/research/trade-plan-forensics first.",
        }
    return {
        "status": "NOT_FOUND",
        "reason": "TRADE_ID_NOT_IN_LAST_REPORT",
        "trade_id": trade_id,
    }


def get_last_report() -> dict[str, Any] | None:
    return _LAST_REPORT


def _count_reasons(stopped: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in stopped:
        k = str(s.get("reason") or "UNKNOWN")
        out[k] = out.get(k, 0) + 1
    return out
