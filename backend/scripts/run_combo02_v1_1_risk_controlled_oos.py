"""Formal / smoke OOS runner for COMBO_02_V1_1_RISK_CONTROLLED.

Formal acceptance window (predeclared):
  2026-10-06 00:00:00 UTC -> 2027-03-31 23:59:59 UTC

Operational smoke only (NOT acceptance):
  2026-10-01 -> 2026-10-05

Diagnostic-only (NOT acceptance): replay risk controls on failed v1 OOS
  2025-07-01 -> 2026-09-30

Signal logic: identical COMBO_02 / COMBO_02_V1. Risk overlay only.
Does not create paper or live trades.

Do not evaluate formal acceptance until the window end has passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow importing app + helpers from sibling v1 OOS script
_SCRIPTS = Path(__file__).resolve().parent
_BACKEND = _SCRIPTS.parent
for _p in (_BACKEND, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.research.combo02_v1_1_risk_controlled import (  # noqa: E402
    DIAGNOSTIC_FAILED_V1_OOS_END,
    DIAGNOSTIC_FAILED_V1_OOS_START,
    MAX_DD_R,
    MAX_LOSE_STREAK,
    MIN_COMPLETENESS,
    MIN_SAMPLE_PASS_WITH_REVIEW,
    MIN_SAMPLE_PER_SYMBOL_CLAIM,
    MIN_SAMPLE_READY_FOR_PAPER,
    OOS_END,
    OOS_START,
    RISK_PERCENT_BY_SYMBOL,
    SMOKE_OOS_END,
    SMOKE_OOS_START,
    STRATEGY_ID,
    VARIANT_VERSION,
    assert_not_failed_parent_for_acceptance,
    classify_v1_1_oos,
    profile_summary,
    replay_candidates_with_risk_controls,
    risk_percent_for,
    risk_usd_for,
    window_has_elapsed,
    window_has_started,
)
from app.research.trade_fees import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE  # noqa: E402
from app.research.v1_production import (  # noqa: E402
    COMBO_ID,
    DEFAULT_PRINCIPAL_USD,
    FROZEN_V1_SYMBOLS,
)
from app.research.service import BosResearchService, _load_research_candles, _signal_config  # noqa: E402
from app.research.config import ResearchConfig  # noqa: E402

# Reuse gate / metrics helpers from archived v1 OOS runner (behavior-preserving import).
import run_combo02_v1_oos_validation as v1oos  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "combo02_v1_1_risk_controlled_oos"
SYMBOLS = sorted(FROZEN_V1_SYMBOLS)
TF = "1h"
FAILED_V1_TRADES = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "combo02_v1_oos"
    / "oos_trades_20261005T054508Z.json"
)


def _annotate_risk(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        sym = str(row.get("symbol") or "").upper()
        rp = risk_percent_for(sym)
        row["risk_percent"] = rp
        row["risk_usd"] = risk_usd_for(sym, DEFAULT_PRINCIPAL_USD)
        r = row.get("r_multiple")
        if r is not None and float(r) != 0 and row.get("gross_pnl_usd") is not None:
            row["gross_pnl_usd"] = float(r) * row["risk_usd"]
            if row.get("r_net") is not None:
                row["net_pnl_usd"] = float(row["r_net"]) * row["risk_usd"]
        out.append(row)
    return out


def _metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    risk = float(trades[0]["risk_usd"]) if trades and trades[0].get("risk_usd") else 20.0
    return v1oos._metrics_from_trades(trades, risk_usd=risk)


async def _run_signal_window(start: str, end: str) -> dict[str, Any]:
    assert_not_failed_parent_for_acceptance(start, end)

    scfg = _signal_config()
    rcfg = ResearchConfig()
    svc = BosResearchService()

    data_health: dict[str, Any] = {}
    for sym in SYMBOLS:
        data_health[sym] = {
            "1h": await v1oos._window_coverage(sym, "1h", start, end),
            "4h": await v1oos._window_coverage(sym, "4h", start, end),
        }
    data_fail = []
    for sym, cov in data_health.items():
        for tf in ("1h", "4h"):
            c = cov[tf]
            if not c.get("pass"):
                data_fail.append(
                    f"{sym} {tf} completeness={c.get('completeness')} gaps={c.get('gap_count')}"
                )

    per_symbol_raw: dict[str, Any] = {}
    all_raw: list[dict[str, Any]] = []

    for sym in SYMBOLS:
        risk = risk_usd_for(sym, DEFAULT_PRINCIPAL_USD)
        matrix = await svc.strategy_matrix(
            combination_id=COMBO_ID,
            symbols=[sym],
            timeframes=[TF],
            direction="LONG",
            limit=50_000,
            risk_usd=risk,
            start_date=start,
            end_date=end,
            taker_fee=DEFAULT_TAKER_FEE,
            maker_fee=DEFAULT_MAKER_FEE,
            include_trades=True,
        )
        row = (matrix.get("rows") or [{}])[0]
        trades = _annotate_risk(list(row.get("trades") or []))
        candles_1h, eval_start, meta_1h = await _load_research_candles(
            sym, TF, start_date=start, end_date=end, warmup_bars=max(rcfg.min_bars, 100)
        )
        candles_4h, _, meta_4h = await _load_research_candles(
            sym, "4h", start_date=start, end_date=end, warmup_bars=max(rcfg.min_bars // 4, 50)
        )
        closed = [t for t in trades if t.get("outcome") not in (None, "OPEN")]
        gate_checks = [
            v1oos._assert_trade_gates(
                t, candles_1h=candles_1h, candles_4h=candles_4h, scfg=scfg, risk_usd=risk
            )
            for t in closed
        ]
        violation_details = [
            {"signal_time": closed[i].get("signal_time"), **g}
            for i, g in enumerate(gate_checks)
            if not g["ok"]
        ]
        lookahead = v1oos._lookahead_check(
            symbol=sym,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            trades=closed,
            scfg=scfg,
            rcfg=rcfg,
        )
        per_symbol_raw[sym] = {
            "risk_usd": risk,
            "risk_percent": risk_percent_for(sym),
            "load_meta_1h": meta_1h,
            "load_meta_4h": meta_4h,
            "eval_start": eval_start,
            "bars_1h_loaded": len(candles_1h),
            "bars_4h_loaded": len(candles_4h),
            "metrics_uncontrolled": _metrics(closed),
            "gate_integrity": {
                "trades_checked": len(gate_checks),
                "violations": len(violation_details),
                "pass": len(violation_details) == 0,
                "details": violation_details[:50],
            },
            "lookahead": lookahead,
            "trades": closed,
        }
        all_raw.extend(closed)

    per_symbol_controlled: dict[str, Any] = {}
    for sym in SYMBOLS:
        solo = replay_candidates_with_risk_controls(per_symbol_raw[sym]["trades"])
        accepted = list(solo["accepted_trades"])
        per_symbol_controlled[sym] = {
            "metrics": _metrics(accepted),
            "halted_entries": len(solo["rejected"]),
            "reject_counts": solo["reject_counts"],
            "rejected": solo["rejected"],
            "accepted_trades": accepted,
        }

    combined_replay = replay_candidates_with_risk_controls(all_raw)
    accepted_all = list(combined_replay["accepted_trades"])
    accepted_all_sorted = sorted(
        accepted_all,
        key=lambda t: (str(t.get("exit_time") or ""), str(t.get("symbol") or "")),
    )
    combined_metrics = _metrics(accepted_all_sorted)

    return {
        "data_health": data_health,
        "data_fail": data_fail,
        "per_symbol_raw": per_symbol_raw,
        "per_symbol_controlled": per_symbol_controlled,
        "combined_replay": combined_replay,
        "combined_metrics": combined_metrics,
        "all_raw_trades": all_raw,
        "accepted_trades": accepted_all_sorted,
    }


def _diagnostic_failed_v1_path() -> dict[str, Any]:
    """Non-acceptance: would v1.1 controls have altered the failed v1 OOS path?"""
    if not FAILED_V1_TRADES.exists():
        return {
            "available": False,
            "reason": f"missing {FAILED_V1_TRADES}",
            "acceptance": False,
        }
    payload = json.loads(FAILED_V1_TRADES.read_text(encoding="utf-8"))
    trades = _annotate_risk(list(payload.get("trades") or []))
    for t in trades:
        t["risk_percent"] = risk_percent_for(str(t.get("symbol") or ""))
        t["risk_usd"] = risk_usd_for(str(t.get("symbol") or ""), DEFAULT_PRINCIPAL_USD)
        if t.get("r_multiple") is not None:
            t["gross_pnl_usd"] = float(t["r_multiple"]) * t["risk_usd"]
        if t.get("r_net") is not None:
            t["net_pnl_usd"] = float(t["r_net"]) * t["risk_usd"]

    uncontrolled = _metrics(trades)
    replay = replay_candidates_with_risk_controls(trades)
    controlled = _metrics(list(replay["accepted_trades"]))
    altered = (
        int(uncontrolled.get("trades") or 0) != int(controlled.get("trades") or 0)
        or abs(
            float(uncontrolled.get("max_drawdown_R") or 0)
            - float(controlled.get("max_drawdown_R") or 0)
        )
        > 1e-9
        or dict(replay.get("reject_counts") or {}) != {}
    )
    return {
        "available": True,
        "acceptance": False,
        "window": {
            "start": DIAGNOSTIC_FAILED_V1_OOS_START,
            "end": DIAGNOSTIC_FAILED_V1_OOS_END,
        },
        "source_trades": str(FAILED_V1_TRADES),
        "uncontrolled_metrics": uncontrolled,
        "controlled_metrics": controlled,
        "reject_counts": replay.get("reject_counts"),
        "halted_entries": len(replay.get("rejected") or []),
        "would_have_altered_failed_v1_path": altered,
        "note": (
            "Diagnostic only. Not acceptance evidence. "
            "Failed parent OOS window is forbidden for v1.1 acceptance."
        ),
    }


def _classify_from_result(
    result: dict[str, Any],
    *,
    start: str,
    end: str,
    acceptance_run: bool,
) -> dict[str, Any]:
    cm = result["combined_metrics"]
    lookahead_ok = all(
        bool(result["per_symbol_raw"][s]["lookahead"].get("pass")) for s in SYMBOLS
    )
    gate_ok = all(
        bool(result["per_symbol_raw"][s]["gate_integrity"].get("pass")) for s in SYMBOLS
    )
    data_ok = len(result["data_fail"]) == 0
    per_symbol_n = {
        s: int(result["per_symbol_controlled"][s]["metrics"].get("trades") or 0)
        for s in SYMBOLS
    }
    return classify_v1_1_oos(
        combined_trades=int(cm.get("trades") or 0),
        average_net_r=cm.get("average_net_R"),
        max_drawdown_r=float(cm.get("max_drawdown_R") or 0),
        max_losing_streak=int(cm.get("max_losing_streak") or 0),
        data_completeness_ok=data_ok,
        lookahead_ok=lookahead_ok,
        gate_integrity_ok=gate_ok,
        window_start_date=start,
        window_end_date=end,
        per_symbol_trades=per_symbol_n,
        acceptance_run=acceptance_run,
    )


async def run(*, smoke: bool = False, force: bool = False) -> dict[str, Any]:
    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings())
    if not db_manager.enabled or db_manager.engine is None:
        raise SystemExit(f"DB unavailable: {db_manager.status}")

    if smoke:
        start, end = SMOKE_OOS_START, SMOKE_OOS_END
        role = "operational_smoke_only"
        acceptance_run = False
        label = "COMBO_02_V1_1_RISK_CONTROLLED_SMOKE"
    else:
        start, end = OOS_START, OOS_END
        role = "OOS_validation"
        acceptance_run = True
        label = "COMBO_02_V1_1_RISK_CONTROLLED_OOS"

    if not window_has_started(start_date=start) and not force:
        raise SystemExit(
            f"OOS window has not started ({start}T00:00:00Z). Run disabled. "
            "Wait until the start date, or pass --force only for dry wiring."
        )
    if acceptance_run and not window_has_elapsed(end_date=end) and not force:
        raise SystemExit(
            f"Formal OOS window end {end}T23:59:59Z has not passed. "
            "Do not evaluate acceptance yet. "
            "Use --smoke for the non-acceptance five-day smoke window, "
            "or --force only for dry wiring (still classified INSUFFICIENT_SAMPLE)."
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Running {'smoke' if smoke else 'formal'} OOS {start} -> {end}...")
    formal = await _run_signal_window(start, end)
    classified = _classify_from_result(
        formal, start=start, end=end, acceptance_run=acceptance_run
    )
    status = classified["status"]
    hard_fails = list(classified.get("hard_fails") or [])
    reviews = list(classified.get("reviews") or [])
    diagnostic = _diagnostic_failed_v1_path()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy_id": STRATEGY_ID,
        "variant_version": VARIANT_VERSION,
        "combo_id": COMBO_ID,
        "direction": "LONG",
        "symbols": SYMBOLS,
        "setup_timeframe": TF,
        "policy": profile_summary(),
        "oos_window": {
            "start": f"{start}T00:00:00Z",
            "end_inclusive": f"{end}T23:59:59Z",
            "mode": "calendar_dates_utc",
            "role": role,
            "acceptance": acceptance_run and not smoke,
            "disjoint_from": {
                "base_training": "2022-10-05 -> 2024-12-31",
                "oos_development": "2025-01-01 -> 2025-06-30",
                "failed_v1_oos_validation": "2025-07-01 -> 2026-09-30",
                "smoke_only": f"{SMOKE_OOS_START} -> {SMOKE_OOS_END}",
            },
        },
        "risk_profile": {
            "principal_usd": DEFAULT_PRINCIPAL_USD,
            "risk_percent_by_symbol": dict(RISK_PERCENT_BY_SYMBOL),
            "per_symbol_risk_usd": {s: risk_usd_for(s) for s in SYMBOLS},
            "note": "Frozen evidence-era profile (not flat 2%)",
            "taker_fee": DEFAULT_TAKER_FEE,
            "maker_fee": DEFAULT_MAKER_FEE,
        },
        "pass_thresholds": {
            "min_completeness": MIN_COMPLETENESS,
            "average_net_R": ">0 combined",
            "max_drawdown_R": MAX_DD_R,
            "max_losing_streak": MAX_LOSE_STREAK,
            "min_combined_trades_ready_for_paper": MIN_SAMPLE_READY_FOR_PAPER,
            "min_combined_trades_pass_with_review": MIN_SAMPLE_PASS_WITH_REVIEW,
            "min_per_symbol_claim": MIN_SAMPLE_PER_SYMBOL_CLAIM,
        },
        "data_health": formal["data_health"],
        "data_health_pass": len(formal["data_fail"]) == 0,
        "per_symbol": {
            sym: {
                "risk_percent": formal["per_symbol_raw"][sym]["risk_percent"],
                "risk_usd": formal["per_symbol_raw"][sym]["risk_usd"],
                "metrics_uncontrolled": formal["per_symbol_raw"][sym]["metrics_uncontrolled"],
                "metrics_risk_controlled_solo": formal["per_symbol_controlled"][sym]["metrics"],
                "halted_entries_solo": formal["per_symbol_controlled"][sym]["halted_entries"],
                "reject_counts_solo": formal["per_symbol_controlled"][sym]["reject_counts"],
                "gate_integrity": formal["per_symbol_raw"][sym]["gate_integrity"],
                "lookahead": formal["per_symbol_raw"][sym]["lookahead"],
            }
            for sym in SYMBOLS
        },
        "combined_metrics_uncontrolled": _metrics(formal["all_raw_trades"]),
        "combined_metrics": formal["combined_metrics"],
        "halted_entries": len(formal["combined_replay"]["rejected"]),
        "reject_counts": formal["combined_replay"]["reject_counts"],
        "rejected_entries": formal["combined_replay"]["rejected"],
        "classification": classified,
        "hard_fails": hard_fails,
        "reviews": reviews,
        "status": status,
        "diagnostic_failed_v1_oos": diagnostic,
        "parent_v1_signal_logic_changed": False,
        "parameters_tuned_during_validation": False,
        "paper_or_live_trades_created": False,
        "disclaimer": (
            "Historical OOS validation only. Not a profitability claim. "
            "No paper or live trades were created by this run. "
            "Smoke and failed-window diagnostic are not acceptance evidence."
        ),
    }

    trades_path = OUT_DIR / f"oos_trades_{stamp}.json"
    trades_path.write_text(
        json.dumps(
            {
                "window": report["oos_window"],
                "accepted_trades": formal["accepted_trades"],
                "rejected_entries": formal["combined_replay"]["rejected"],
                "raw_candidate_trades": formal["all_raw_trades"],
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    report_path = OUT_DIR / f"oos_report_{stamp}.json"
    full = dict(report)
    full["accepted_trades"] = formal["accepted_trades"]
    report_path.write_text(json.dumps(full, indent=2, default=str), encoding="utf-8")
    if acceptance_run and not smoke:
        (OUT_DIR / "oos_report_latest.json").write_text(
            report_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
    else:
        (OUT_DIR / "oos_smoke_latest.json").write_text(
            report_path.read_text(encoding="utf-8"), encoding="utf-8"
        )

    cm = formal["combined_metrics"]
    md = [
        f"# COMBO_02_V1_1_RISK_CONTROLLED OOS — {status}",
        "",
        f"**Window:** {start} -> {end} UTC  ",
        f"**Role:** {role}  ",
        f"**Acceptance:** {acceptance_run and not smoke}  ",
        f"**Symbols:** {', '.join(SYMBOLS)}  ",
        f"**Risk:** BTC 1.5% / ETH 0.5% / SOL 0.5% + cluster controls  ",
        f"**Paper/live created:** no  ",
        f"**Parent signal logic changed:** no  ",
        "",
        "## Combined (risk-controlled book)",
        "",
        f"- Trades: **{cm.get('trades')}**",
        f"- WR: {cm.get('win_rate')}",
        f"- Gross R: {cm.get('gross_R')} | Net R: {cm.get('net_R')}",
        f"- Avg net R: **{cm.get('average_net_R')}**",
        f"- PnL net: {cm.get('pnl_usd_net')} | fees: {cm.get('fees_usd')}",
        f"- Max DD: {cm.get('max_drawdown_R')}R",
        f"- Max lose streak: {cm.get('max_losing_streak')}",
        f"- PF: {cm.get('profit_factor')}",
        f"- Exits: {cm.get('exit_breakdown')}",
        f"- Halted entries: {report['halted_entries']}",
        f"- Reject reasons: {report['reject_counts']}",
        "",
        "## Classification guards",
        "",
        f"- {classified.get('guards')}",
        f"- reason: {classified.get('reason')}",
        "",
        "## Per symbol (solo risk-controlled)",
        "",
    ]
    for sym in SYMBOLS:
        m = formal["per_symbol_controlled"][sym]["metrics"]
        md.append(
            f"### {sym}\n"
            f"- n={m.get('trades')} WR={m.get('win_rate')} "
            f"avgNetR={m.get('average_net_R')} grossR={m.get('gross_R')} "
            f"netR={m.get('net_R')} PnL={m.get('pnl_usd_net')} fees={m.get('fees_usd')} "
            f"DD={m.get('max_drawdown_R')} streak={m.get('max_losing_streak')} "
            f"PF={m.get('profit_factor')} exits={m.get('exit_breakdown')} "
            f"halted={formal['per_symbol_controlled'][sym]['halted_entries']} "
            f"rejects={formal['per_symbol_controlled'][sym]['reject_counts']}"
        )
    md.extend(["", "## Hard fails", ""])
    md.extend([f"- {x}" for x in hard_fails] or ["- (none)"])
    md.extend(["", "## Reviews", ""])
    md.extend([f"- {x}" for x in reviews] or ["- (none)"])
    md.extend(
        [
            "",
            "## Diagnostic (failed v1 OOS — NOT acceptance)",
            "",
            f"- Would alter failed path: {diagnostic.get('would_have_altered_failed_v1_path')}",
            f"- Reject counts: {diagnostic.get('reject_counts')}",
            "",
            f"## Final status: `{status}`",
            "",
            "No paper or live trade was created.",
        ]
    )
    md_path = OUT_DIR / f"oos_report_{stamp}.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"\nSTATUS={status}")
    print(f"Wrote {report_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {trades_path}")
    print(
        f"combined n={cm.get('trades')} avgNetR={cm.get('average_net_R')} "
        f"DD={cm.get('max_drawdown_R')} streak={cm.get('max_losing_streak')} "
        f"halted={report['halted_entries']}"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run 2026-10-01->2026-10-05 operational smoke (NOT acceptance).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow runner before window start / formal end "
            "(still classifies INSUFFICIENT_SAMPLE when guards fail)."
        ),
    )
    args = parser.parse_args()
    asyncio.run(run(smoke=args.smoke, force=args.force))


if __name__ == "__main__":
    main()
