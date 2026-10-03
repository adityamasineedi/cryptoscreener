"""Frozen COMBO_02 candidate research runner core (research only).

Uses the standard BosResearchService.strategy_matrix path — no duplicated
COMBO_02 approximation. Never promotes symbols into v1 / Telegram / paper.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from app.research.bos_combinations import get_combination
from app.research.combo02_candidate_eligibility import (
    RunStatus,
    classify_eligibility,
    classify_oos,
    daily_net_r_series,
    entry_overlap_pct,
    fee_pct_of_gross,
    incremental_portfolio_stats,
    max_drawdown_r,
    max_losing_streak,
    max_winning_streak,
    portfolio_recommendation,
    trade_intervals_from_rows,
    weekly_net_r_series,
    aligned_corr,
)
from app.research.combo02_candidate_selector import manifest_symbols
from app.research.combo02_candidate_thresholds import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WINDOW,
    DISCLAIMER,
    STRATEGY_FINGERPRINT_TEXT,
    EligibilityThresholds,
    ResearchWindowConfig,
)
from app.research.config import RESEARCH_ENGINE_VERSION
from app.research.trade_fees import DEFAULT_MAKER_FEE, DEFAULT_TAKER_FEE
from app.research.v1_production import V1_SYMBOLS


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def strategy_fingerprint(window: ResearchWindowConfig = DEFAULT_WINDOW) -> dict[str, Any]:
    combo = get_combination(window.combination_id)
    return {
        "text": STRATEGY_FINGERPRINT_TEXT,
        "combination_id": window.combination_id,
        "combo_require_htf": bool(
            getattr(combo, "require_htf_alignment", True) if combo else True
        ),
        "direction": window.direction,
        "setup_timeframe": window.setup_timeframe,
        "window_start": window.base_start,
        "window_end": window.base_end,
        "oos_dev_end": window.oos_dev_end,
        "oos_val_start": window.oos_val_start,
        "oos_val_end": window.oos_val_end,
        "risk_usd_per_r": window.risk_usd,
        "principal_usd": window.principal_usd,
        "taker_fee": DEFAULT_TAKER_FEE,
        "maker_fee": DEFAULT_MAKER_FEE,
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "path": "A",
        "no_path_b": True,
        "no_combo_02_local": True,
        "no_15m": True,
        "settings_identical_across_candidates": True,
    }


def _closed_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        status = str(t.get("status") or "CLOSED").upper()
        if status not in ("CLOSED", ""):
            continue
        if t.get("exit_price") is None and t.get("r_net") is None and t.get("r_multiple") is None:
            continue
        out.append(t)
    return out


def compute_trade_metrics(
    trades: list[dict[str, Any]],
    *,
    coverage: dict[str, Any] | None = None,
    window_years: float = 13 / 12,
) -> dict[str, Any]:
    closed = _closed_trades(trades)
    rs_net: list[float] = []
    rs_gross: list[float] = []
    fees = 0.0
    gross_pnl = 0.0
    net_pnl = 0.0
    hold_bars: list[float] = []

    for t in closed:
        rn = t.get("r_net")
        rg = t.get("r_gross", t.get("r_multiple"))
        try:
            if rn is not None:
                rs_net.append(float(rn))
            elif rg is not None:
                rs_net.append(float(rg))
            if rg is not None:
                rs_gross.append(float(rg))
        except (TypeError, ValueError):
            continue

        fee = t.get("fee_total_usd")
        if fee is None:
            fee = t.get("fees_usd")
        fees += float(fee or 0.0)

        g = t.get("gross_pnl_usd")
        if g is None:
            g = t.get("pnl_usd")
        if g is not None:
            gross_pnl += float(g)

        n = t.get("net_pnl_usd")
        if n is None:
            n = t.get("pnl_usd_net")
        if n is None:
            n = g
        if n is not None:
            net_pnl += float(n)

        hb = t.get("holding_bars")
        if hb is None:
            hb = t.get("bars_held")
        if hb is not None:
            try:
                hold_bars.append(float(hb))
            except (TypeError, ValueError):
                pass

    n = len(rs_net)
    wins = sum(1 for r in rs_net if r > 0)
    wr = wins / n if n else 0.0
    avg_g = sum(rs_gross) / len(rs_gross) if rs_gross else None
    avg_n = sum(rs_net) / n if n else None
    gains = sum(r for r in rs_net if r > 0)
    losses = sum(abs(r) for r in rs_net if r < 0)
    pf = (gains / losses) if losses > 0 else (float("inf") if gains > 0 else None)
    dd = max_drawdown_r(rs_net) if rs_net else 0.0
    lose_streak = max_losing_streak(rs_net) if rs_net else 0
    win_streak = max_winning_streak(rs_net) if rs_net else 0
    fee_share = fee_pct_of_gross(gross_pnl, fees)
    largest_win = max(rs_net) if rs_net else None
    largest_loss = min(rs_net) if rs_net else None
    cov = coverage or {}

    return {
        "data_coverage_start": cov.get("ohlcv_start_utc") or cov.get("period_start"),
        "data_coverage_end": cov.get("ohlcv_end_utc") or cov.get("period_end"),
        "ohlcv_completeness": cov.get("ohlcv_completeness"),
        "trade_count": n,
        "trades_per_year": round(n / window_years, 4) if n and window_years > 0 else 0.0,
        "win_rate": round(wr, 4),
        "gross_avg_r": None if avg_g is None else round(avg_g, 4),
        "net_avg_r": None if avg_n is None else round(avg_n, 4),
        "profit_factor": None if pf is None or not math.isfinite(pf) else round(pf, 4),
        "gross_pnl": round(gross_pnl, 4),
        "net_pnl": round(net_pnl, 4),
        "total_fees": round(fees, 4),
        "fees_over_gross_pnl": None if fee_share is None else round(fee_share, 4),
        "max_drawdown_r": round(dd, 4),
        "max_losing_streak": lose_streak,
        "max_winning_streak": win_streak,
        "largest_single_winner_r": None if largest_win is None else round(largest_win, 4),
        "largest_single_loser_r": None if largest_loss is None else round(largest_loss, 4),
        "median_holding_bars": (
            None if not hold_bars else round(float(median(hold_bars)), 4)
        ),
        "maximum_holding_bars": max(hold_bars) if hold_bars else None,
        "r_series": rs_net,
        "closed_trades": closed,
    }


def map_run_status(
    *,
    engine_status: str | None,
    metrics: dict[str, Any],
    error: str | None = None,
) -> RunStatus:
    if error:
        return "ENGINE_ERROR"
    st = (engine_status or "").upper()
    if st in {"INSUFFICIENT_DATA", "INSUFFICIENT_OHLCV"}:
        return "INSUFFICIENT_OHLCV"
    if st in {"DATA_GAP", "GAP"}:
        return "DATA_GAP"
    if st in {"ERROR", "ENGINE_ERROR", "NOT_FOUND"}:
        return "ENGINE_ERROR"
    if int(metrics.get("trade_count") or 0) <= 0:
        return "NO_TRADES"
    return "COMPLETED"


async def backtest_symbol(
    service: Any,
    symbol: str,
    *,
    start: str,
    end: str,
    window: ResearchWindowConfig = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Run exact frozen COMBO_02 via strategy_matrix (identical settings)."""
    try:
        matrix = await service.strategy_matrix(
            combination_id=window.combination_id,
            symbols=[symbol],
            timeframes=[window.setup_timeframe],
            direction=window.direction,
            limit=20000,
            risk_usd=window.risk_usd,
            start_date=start,
            end_date=end,
            taker_fee=DEFAULT_TAKER_FEE,
            maker_fee=DEFAULT_MAKER_FEE,
            include_trades=True,
        )
    except Exception as exc:  # noqa: BLE001
        empty = compute_trade_metrics([])
        return {
            "symbol": symbol,
            "run_status": "ENGINE_ERROR",
            "error": str(exc),
            "window_start": start,
            "window_end": end,
            **{k: v for k, v in empty.items() if k not in ("r_series", "closed_trades")},
            "r_series": [],
            "closed_trades": [],
        }

    rows = matrix.get("rows") or []
    row = rows[0] if rows else {}
    trades = list(row.get("trades") or [])
    coverage = {
        "ohlcv_start_utc": row.get("period_start"),
        "ohlcv_end_utc": row.get("period_end"),
        "ohlcv_completeness": None,
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
    }
    metrics = compute_trade_metrics(trades, coverage=coverage)
    status = map_run_status(
        engine_status=str(row.get("status") or matrix.get("status") or ""),
        metrics=metrics,
    )
    return {
        "symbol": symbol,
        "run_status": status,
        "window_start": start,
        "window_end": end,
        "engine_status": row.get("status") or matrix.get("status"),
        "sample_size_engine": row.get("sample_size"),
        **metrics,
    }


def _assert_frozen_combo(window: ResearchWindowConfig) -> None:
    combo = get_combination(window.combination_id)
    if combo is None:
        raise RuntimeError(f"Unknown combination {window.combination_id}")
    if window.combination_id != "COMBO_02":
        raise RuntimeError("Candidate research must use COMBO_02 only")
    if window.setup_timeframe != "1h":
        raise RuntimeError("Candidate research must use 1h setup TF")
    if window.direction.upper() != "LONG":
        raise RuntimeError("Candidate research must use LONG only")
    if not bool(getattr(combo, "require_htf_alignment", False)):
        raise RuntimeError("COMBO_02 must require HTF alignment")


async def run_candidate_research(
    *,
    manifest: dict[str, Any],
    run_oos: bool = True,
    window: ResearchWindowConfig = DEFAULT_WINDOW,
    thresholds: EligibilityThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    from app.research.service import get_bos_research_service

    _assert_frozen_combo(window)
    service = get_bos_research_service()
    fingerprint = strategy_fingerprint(window)
    symbols = manifest_symbols(manifest)
    sym_meta = {
        str(r.get("symbol")).upper(): r
        for r in (manifest.get("symbols") or manifest.get("candidates") or [])
        if r.get("symbol")
    }

    # v1 reference books (same fixed $20/R for comparability)
    ref_runs: dict[str, dict[str, Any]] = {}
    ref_intervals: dict[str, list] = {}
    for ref in sorted(V1_SYMBOLS):
        ref_runs[ref] = await backtest_symbol(
            service, ref, start=window.base_start, end=window.base_end, window=window
        )
        ref_intervals[ref] = trade_intervals_from_rows(
            ref, list(ref_runs[ref].get("closed_trades") or [])
        )
    v1_all = [iv for ref in sorted(V1_SYMBOLS) for iv in ref_intervals[ref]]
    btc_daily = daily_net_r_series(ref_intervals.get("BTCUSDT") or [])
    btc_weekly = weekly_net_r_series(ref_intervals.get("BTCUSDT") or [])

    results: list[dict[str, Any]] = []
    portfolio_rows: list[dict[str, Any]] = []
    oos_rows: list[dict[str, Any]] = []

    for sym in symbols:
        run = await backtest_symbol(
            service, sym, start=window.base_start, end=window.base_end, window=window
        )
        meta = sym_meta.get(sym, {})
        if run.get("ohlcv_completeness") is None:
            run["ohlcv_completeness"] = meta.get("ohlcv_completeness")
        if not run.get("data_coverage_start"):
            run["data_coverage_start"] = meta.get("ohlcv_start_utc")
        if not run.get("data_coverage_end"):
            run["data_coverage_end"] = meta.get("ohlcv_end_utc")

        closed = list(run.get("closed_trades") or [])
        cand_iv = trade_intervals_from_rows(sym, closed)
        cand_entries = [iv.entry for iv in cand_iv]

        overlaps = {
            ref: entry_overlap_pct(cand_entries, [iv.entry for iv in ref_intervals[ref]])
            for ref in sorted(V1_SYMBOLS)
        }
        cand_daily = daily_net_r_series(cand_iv)
        cand_weekly = weekly_net_r_series(cand_iv)
        corr_daily = aligned_corr(cand_daily, btc_daily)
        corr_weekly = aligned_corr(cand_weekly, btc_weekly)
        incremental = incremental_portfolio_stats(cand_iv, v1_all)
        concurrent_vs_refs = {
            ref: incremental_portfolio_stats(cand_iv, ref_intervals[ref]).get(
                "peak_concurrent_with_v1"
            )
            for ref in sorted(V1_SYMBOLS)
        }

        if run["run_status"] != "COMPLETED":
            tier, reasons, conditions = (
                "INSUFFICIENT_DATA",
                [f"run_status={run['run_status']}"],
                [],
            )
        else:
            tier, reasons, conditions = classify_eligibility(
                trade_count=int(run.get("trade_count") or 0),
                net_avg_r=run.get("net_avg_r"),
                net_pnl=run.get("net_pnl"),
                profit_factor=run.get("profit_factor"),
                max_dd_r=run.get("max_drawdown_r"),
                max_lose_streak=run.get("max_losing_streak"),
                fee_share=run.get("fees_over_gross_pnl"),
                thresholds=thresholds,
            )

        row = {
            "symbol": sym,
            "run_status": run["run_status"],
            "data_coverage_start": run.get("data_coverage_start"),
            "data_coverage_end": run.get("data_coverage_end"),
            "ohlcv_completeness": run.get("ohlcv_completeness"),
            "trade_count": run.get("trade_count"),
            "trades_per_year": run.get("trades_per_year"),
            "win_rate": run.get("win_rate"),
            "gross_avg_r": run.get("gross_avg_r"),
            "net_avg_r": run.get("net_avg_r"),
            "profit_factor": run.get("profit_factor"),
            "gross_pnl": run.get("gross_pnl"),
            "net_pnl": run.get("net_pnl"),
            "total_fees": run.get("total_fees"),
            "fees_over_gross_pnl": run.get("fees_over_gross_pnl"),
            "max_drawdown_r": run.get("max_drawdown_r"),
            "max_losing_streak": run.get("max_losing_streak"),
            "max_winning_streak": run.get("max_winning_streak"),
            "largest_single_winner_r": run.get("largest_single_winner_r"),
            "largest_single_loser_r": run.get("largest_single_loser_r"),
            "median_holding_bars": run.get("median_holding_bars"),
            "maximum_holding_bars": run.get("maximum_holding_bars"),
            "concurrent_vs_btc": concurrent_vs_refs.get("BTCUSDT"),
            "concurrent_vs_eth": concurrent_vs_refs.get("ETHUSDT"),
            "concurrent_vs_sol": concurrent_vs_refs.get("SOLUSDT"),
            "peak_concurrent_with_v1_book": incremental.get("peak_concurrent_with_v1"),
            "overlap_pct_BTCUSDT": (
                None if overlaps.get("BTCUSDT") is None else round(overlaps["BTCUSDT"], 4)
            ),
            "overlap_pct_ETHUSDT": (
                None if overlaps.get("ETHUSDT") is None else round(overlaps["ETHUSDT"], 4)
            ),
            "overlap_pct_SOLUSDT": (
                None if overlaps.get("SOLUSDT") is None else round(overlaps["SOLUSDT"], 4)
            ),
            "corr_daily_net_r_vs_btc": None if corr_daily is None else round(corr_daily, 4),
            "corr_weekly_net_r_vs_btc": (
                None if corr_weekly is None else round(corr_weekly, 4)
            ),
            "eligibility_tier": tier,
            "eligibility_reasons": reasons,
            "eligibility_conditions": conditions,
            "incremental_portfolio": incremental,
        }
        results.append(row)

        positive_base = tier in ("PROMISING", "WATCHLIST") or (
            (run.get("net_avg_r") or 0) > 0 and (run.get("net_pnl") or 0) > 0
        )
        if positive_base:
            portfolio_rows.append(
                {
                    "symbol": sym,
                    **portfolio_recommendation(
                        symbol=sym,
                        base_tier=tier,  # type: ignore[arg-type]
                        oos_label="NOT_APPLICABLE",
                        overlap_btc=row.get("overlap_pct_BTCUSDT"),
                        overlap_eth=row.get("overlap_pct_ETHUSDT"),
                        overlap_sol=row.get("overlap_pct_SOLUSDT"),
                        corr_daily_btc=row.get("corr_daily_net_r_vs_btc"),
                        incremental=incremental,
                    ),
                }
            )

    # OOS for PROMISING only
    if run_oos:
        for r in results:
            if r.get("eligibility_tier") != "PROMISING":
                continue
            sym = r["symbol"]
            meta = sym_meta.get(sym, {})
            earliest = meta.get("ohlcv_start_utc") or r.get("data_coverage_start")
            # Dev window: earliest usable → oos_dev_end
            if not earliest:
                oos_label, oos_reasons, oos_conds = classify_oos(
                    base_tier="PROMISING",
                    oos_trade_count=0,
                    oos_net_avg_r=None,
                    oos_net_pnl=None,
                    oos_profit_factor=None,
                    oos_max_dd_r=None,
                    oos_max_lose_streak=None,
                    oos_usable=False,
                    thresholds=thresholds,
                )
                oos_rows.append(
                    {
                        "symbol": sym,
                        "dev_window": f"earliest→{window.oos_dev_end}",
                        "oos_window": f"{window.oos_val_start}→{window.oos_val_end}",
                        "oos_label": oos_label,
                        "oos_reasons": oos_reasons,
                        "oos_conditions": oos_conds,
                        "oos_pass": False,
                    }
                )
                r["oos_label"] = oos_label
                continue

            dev = await backtest_symbol(
                service,
                sym,
                start=str(earliest)[:10],
                end=window.oos_dev_end,
                window=window,
            )
            val = await backtest_symbol(
                service,
                sym,
                start=window.oos_val_start,
                end=window.oos_val_end,
                window=window,
            )
            usable = (
                val.get("run_status") in ("COMPLETED", "NO_TRADES")
                and dev.get("run_status") in ("COMPLETED", "NO_TRADES", "INSUFFICIENT_OHLCV")
            )
            # Prefer COMPLETED with trades for usability of OOS partition
            if val.get("run_status") not in ("COMPLETED", "NO_TRADES"):
                usable = False

            oos_label, oos_reasons, oos_conds = classify_oos(
                base_tier="PROMISING",
                oos_trade_count=int(val.get("trade_count") or 0),
                oos_net_avg_r=val.get("net_avg_r"),
                oos_net_pnl=val.get("net_pnl"),
                oos_profit_factor=val.get("profit_factor"),
                oos_max_dd_r=val.get("max_drawdown_r"),
                oos_max_lose_streak=val.get("max_losing_streak"),
                oos_usable=usable,
                thresholds=thresholds,
            )
            oos_entry = {
                "symbol": sym,
                "dev_window": f"{str(earliest)[:10]}→{window.oos_dev_end}",
                "oos_window": f"{window.oos_val_start}→{window.oos_val_end}",
                "dev_trade_count": dev.get("trade_count"),
                "dev_net_avg_r": dev.get("net_avg_r"),
                "dev_net_pnl": dev.get("net_pnl"),
                "dev_profit_factor": dev.get("profit_factor"),
                "dev_max_drawdown_r": dev.get("max_drawdown_r"),
                "dev_max_losing_streak": dev.get("max_losing_streak"),
                "oos_trade_count": val.get("trade_count"),
                "oos_net_avg_r": val.get("net_avg_r"),
                "oos_net_pnl": val.get("net_pnl"),
                "oos_profit_factor": val.get("profit_factor"),
                "oos_max_drawdown_r": val.get("max_drawdown_r"),
                "oos_max_losing_streak": val.get("max_losing_streak"),
                "oos_label": oos_label,
                "oos_reasons": oos_reasons,
                "oos_conditions": oos_conds,
                "oos_pass": oos_label == "V2_PAPER_CANDIDATE",
            }
            oos_rows.append(oos_entry)
            r["oos_label"] = oos_label
            r["oos_pass"] = oos_entry["oos_pass"]

            # Refresh portfolio recommendation with OOS label
            for p in portfolio_rows:
                if p.get("symbol") == sym:
                    p.update(
                        portfolio_recommendation(
                            symbol=sym,
                            base_tier=r["eligibility_tier"],
                            oos_label=oos_label,
                            overlap_btc=r.get("overlap_pct_BTCUSDT"),
                            overlap_eth=r.get("overlap_pct_ETHUSDT"),
                            overlap_sol=r.get("overlap_pct_SOLUSDT"),
                            corr_daily_btc=r.get("corr_daily_net_r_vs_btc"),
                            incremental=r.get("incremental_portfolio") or {},
                        )
                    )

    # Final lists
    by_tier: dict[str, list[str]] = {
        "V2_PAPER_CANDIDATE": [],
        "PROMISING_NEEDS_MORE_EVIDENCE": [],
        "WATCHLIST": [],
        "REJECT": [],
        "INSUFFICIENT_DATA": [],
        "PROMISING": [],
    }
    for r in results:
        tier = r.get("eligibility_tier")
        oos_lab = r.get("oos_label")
        if oos_lab == "V2_PAPER_CANDIDATE":
            by_tier["V2_PAPER_CANDIDATE"].append(r["symbol"])
        elif oos_lab in ("PROMISING_NEEDS_MORE_EVIDENCE", "INSUFFICIENT_OOS_DATA", "OOS_FAIL"):
            by_tier["PROMISING_NEEDS_MORE_EVIDENCE"].append(r["symbol"])
        elif tier == "PROMISING":
            by_tier["PROMISING_NEEDS_MORE_EVIDENCE"].append(r["symbol"])
            by_tier["PROMISING"].append(r["symbol"])
        elif tier in by_tier:
            by_tier[tier].append(r["symbol"])

    # Strip bulky trade payloads from persisted results
    slim_results = []
    for r in results:
        slim_results.append(dict(r))

    v1_ref_slim = {}
    for sym, m in ref_runs.items():
        v1_ref_slim[sym] = {
            k: v
            for k, v in m.items()
            if k not in ("r_series", "closed_trades")
        }

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fingerprint,
        "thresholds": thresholds.to_dict(),
        "window": window.to_dict(),
        "manifest_ref": {
            "generated_at_utc": manifest.get("generated_at_utc")
            or manifest.get("selected_at_utc"),
            "selector_version": manifest.get("selector_version"),
            "criteria": manifest.get("criteria") or manifest.get("selection_rules"),
            "symbols": symbols,
        },
        "v1_reference": v1_ref_slim,
        "results": slim_results,
        "oos": oos_rows,
        "portfolio": portfolio_rows,
        "final_lists": by_tier,
        "disclaimer": DISCLAIMER,
        "v1_unchanged": True,
        "telegram_eligible_changed": False,
        "paper_universe_changed": False,
    }


CSV_FIELDS = [
    "symbol",
    "run_status",
    "eligibility_tier",
    "oos_label",
    "trade_count",
    "trades_per_year",
    "win_rate",
    "gross_avg_r",
    "net_avg_r",
    "profit_factor",
    "gross_pnl",
    "net_pnl",
    "total_fees",
    "fees_over_gross_pnl",
    "max_drawdown_r",
    "max_losing_streak",
    "max_winning_streak",
    "largest_single_winner_r",
    "largest_single_loser_r",
    "median_holding_bars",
    "maximum_holding_bars",
    "peak_concurrent_with_v1_book",
    "overlap_pct_BTCUSDT",
    "overlap_pct_ETHUSDT",
    "overlap_pct_SOLUSDT",
    "corr_daily_net_r_vs_btc",
    "data_coverage_start",
    "data_coverage_end",
    "ohlcv_completeness",
    "eligibility_reasons",
]


def write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k) for k in CSV_FIELDS}
            if isinstance(row.get("eligibility_reasons"), list):
                row["eligibility_reasons"] = ";".join(row["eligibility_reasons"])
            w.writerow(row)


def write_results_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_markdown_report(path: Path, payload: dict[str, Any], manifest_path: str | None = None) -> None:
    fp = payload.get("fingerprint") or {}
    mref = payload.get("manifest_ref") or {}
    finals = payload.get("final_lists") or {}
    lines = [
        "# COMBO_02 candidate eligibility research report",
        "",
        f"Generated: `{payload.get('generated_at_utc')}`",
        "",
        "## Mandatory disclaimer",
        "",
        str(payload.get("disclaimer") or DISCLAIMER),
        "",
        "## Frozen strategy fingerprint",
        "",
        f"> {fp.get('text') or STRATEGY_FINGERPRINT_TEXT}",
        "",
        "```json",
        json.dumps(fp, indent=2),
        "```",
        "",
        "## Selection criteria and immutable candidate manifest",
        "",
    ]
    if manifest_path:
        lines.append(f"Manifest file: `{manifest_path}`")
        lines.append("")
    lines += [
        "```json",
        json.dumps(mref.get("criteria") or {}, indent=2),
        "```",
        "",
        f"Candidates ({len(mref.get('symbols') or [])}): "
        + ", ".join(mref.get("symbols") or []),
        "",
        "## Complete results table",
        "",
        "| symbol | status | tier | n | win% | net avg R | PF | net PnL | maxDD R | lose streak | fees/gross | BTC overlap |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in payload.get("results") or []:
        lines.append(
            "| {symbol} | {run_status} | {eligibility_tier} | {trade_count} | {win_rate} | "
            "{net_avg_r} | {profit_factor} | {net_pnl} | {max_drawdown_r} | "
            "{max_losing_streak} | {fees_over_gross_pnl} | {overlap_pct_BTCUSDT} |".format(
                **{k: r.get(k) for k in (
                    "symbol",
                    "run_status",
                    "eligibility_tier",
                    "trade_count",
                    "win_rate",
                    "net_avg_r",
                    "profit_factor",
                    "net_pnl",
                    "max_drawdown_r",
                    "max_losing_streak",
                    "fees_over_gross_pnl",
                    "overlap_pct_BTCUSDT",
                )}
            )
        )

    lines += ["", "## Tier pass/fail conditions", ""]
    for r in payload.get("results") or []:
        lines.append(f"### {r.get('symbol')} — `{r.get('eligibility_tier')}`")
        lines.append("")
        lines.append(f"Reasons: `{'; '.join(r.get('eligibility_reasons') or [])}`")
        lines.append("")
        conds = r.get("eligibility_conditions") or []
        if conds:
            lines.append("| condition | passed | detail |")
            lines.append("|---|---|---|")
            for c in conds:
                lines.append(
                    f"| {c.get('name')} | {c.get('passed')} | {c.get('detail')} |"
                )
            lines.append("")
        else:
            lines.append("_No promising-condition matrix (non-completed / insufficient)._")
            lines.append("")

    lines += ["", "## Out-of-sample (PROMISING only)", ""]
    oos = payload.get("oos") or []
    if not oos:
        lines.append("_No PROMISING coins in this run._")
    else:
        lines.append(
            "| symbol | OOS n | net avg R | net PnL | PF | maxDD | lose streak | label |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|---|")
        for o in oos:
            lines.append(
                f"| {o.get('symbol')} | {o.get('oos_trade_count')} | {o.get('oos_net_avg_r')} | "
                f"{o.get('oos_net_pnl')} | {o.get('oos_profit_factor')} | "
                f"{o.get('oos_max_drawdown_r')} | {o.get('oos_max_losing_streak')} | "
                f"{o.get('oos_label')} |"
            )
    lines += ["", "## Portfolio overlap / concurrency", ""]
    port = payload.get("portfolio") or []
    if not port:
        lines.append("_No positive-base candidates for portfolio analysis._")
    else:
        for p in port:
            lines.append(f"### {p.get('symbol')}")
            lines.append("")
            lines.append(f"- Base result: `{p.get('base_tier')}`")
            lines.append(f"- OOS result: `{p.get('oos_label')}`")
            btc_o = p.get("btc_overlap_pct")
            lines.append(
                f"- BTC overlap: "
                f"{'n/a' if btc_o is None else f'{100 * float(btc_o):.0f}% of entries'}"
            )
            lines.append(
                f"- Peak concurrent positions: `{p.get('peak_concurrent_positions')}`"
            )
            lines.append(
                f"- Incremental portfolio DD: `{p.get('incremental_portfolio_dd_r')}R`"
            )
            lines.append(
                f"- Recommendation: **{p.get('recommendation')}** — {p.get('recommendation_note')}"
            )
            lines.append("")

    lines += [
        "",
        "## Final lists",
        "",
        f"- **V2_PAPER_CANDIDATE**: {', '.join(finals.get('V2_PAPER_CANDIDATE') or []) or 'none'}",
        f"- **PROMISING_NEEDS_MORE_EVIDENCE**: "
        f"{', '.join(finals.get('PROMISING_NEEDS_MORE_EVIDENCE') or []) or 'none'}",
        f"- **WATCHLIST**: {', '.join(finals.get('WATCHLIST') or []) or 'none'}",
        f"- **REJECT**: {', '.join(finals.get('REJECT') or []) or 'none'}",
        f"- **INSUFFICIENT_DATA**: {', '.join(finals.get('INSUFFICIENT_DATA') or []) or 'none'}",
        "",
        "## Hard boundary confirmation",
        "",
        "- Frozen v1 remains BTC/ETH/SOL only.",
        "- No screener candidate was automatically promoted, paper-traded, or made Telegram eligible.",
        "",
        str(payload.get("disclaimer") or DISCLAIMER),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def persist_research_artifacts(
    payload: dict[str, Any],
    *,
    reports_dir: Path,
    stamp: str | None = None,
    manifest: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Path]:
    """Write manifest copy + results JSON/CSV + markdown report."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = stamp or utc_stamp()
    paths: dict[str, Path] = {}

    if manifest is not None:
        man_path = reports_dir / f"combo02_candidate_manifest_{ts}.json"
        man_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        paths["manifest"] = man_path
        # Also write the spec name combo02_candidates_<ts>.json
        alt = reports_dir / f"combo02_candidates_{ts}.json"
        alt.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        paths["candidates"] = alt

    json_path = reports_dir / f"combo02_candidate_results_{ts}.json"
    csv_path = reports_dir / f"combo02_candidate_results_{ts}.csv"
    md_path = reports_dir / f"combo02_candidate_report_{ts}.md"
    write_results_json(json_path, payload)
    write_results_csv(csv_path, list(payload.get("results") or []))
    write_markdown_report(
        md_path,
        payload,
        manifest_path=str(manifest_path or paths.get("manifest") or ""),
    )
    paths.update({"json": json_path, "csv": csv_path, "md": md_path})
    return paths
