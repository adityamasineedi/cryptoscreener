#!/usr/bin/env python3
"""GRID_RANGE_RESEARCH — compare regime-gated grid vs COMBO_02_V2 RANGE playbook.

Research-only. Does not modify COMBO_02 / COMBO_02_V2 / production engines.
Uses the same OHLCV window and symbols as combo02_v2_multi_market_validation.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "DOGEUSDT",
    "SUIUSDT",
]

DATA_START = "2022-01-01"
DATA_END = "2026-10-06"

PRIMARY_REGIMES = ("CHOPPY", "RANGE", "HIGH_VOLATILITY_RANGE")
OPTIONAL_REGIME = "LOW_VOLATILITY_COMPRESSION"
COMPARE_REGIMES = PRIMARY_REGIMES + (OPTIONAL_REGIME,)

OUT_DIR = Path("reports/grid_range_research")
V2_TRADES_CSV = Path("reports/combo02_v2_multi_market_validation/all_trades.csv")

VARIANTS = ("GRID-A", "GRID-B", "GRID-C")


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _year(ts: str | None) -> str:
    if not ts:
        return "UNKNOWN"
    s = str(ts).replace("Z", "+00:00")
    try:
        return f"{datetime.fromisoformat(s).year:04d}"
    except ValueError:
        return str(ts)[:4] if len(str(ts)) >= 4 else "UNKNOWN"


def _bucket(rows: list[dict[str, Any]], r_key: str = "r_multiple") -> dict[str, Any]:
    from app.research.grid_range_research.metrics import bucket_metrics

    return bucket_metrics(rows, r_key=r_key)


def _fee_adjust_r(
    *,
    direction: str,
    entry: float,
    stop: float,
    exit_px: float,
    r_gross: float,
) -> tuple[float, float]:
    """Return (r_net on $20 requested risk, total_fee)."""
    from app.research.grid_range_research.risk import apply_fees_and_sizing

    priced = apply_fees_and_sizing(
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        exit_price=exit_px,
        r_gross=r_gross,
        entry_type="MARKET",
    )
    return float(priced["r_net"]), float(priced.get("total_fee") or 0.0)


def _load_v2_range_baseline() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load published V2 RANGE_MEAN_REVERSION trades; attach fees + net R."""
    rows: list[dict[str, Any]] = []
    if not V2_TRADES_CSV.exists():
        return rows, {"error": f"missing {V2_TRADES_CSV}"}

    with V2_TRADES_CSV.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            if raw.get("playbook") != "RANGE_MEAN_REVERSION":
                continue
            regime = raw.get("regime") or "UNKNOWN"
            entry = _f(raw.get("entry"))
            stop = _f(raw.get("stop"))
            r_gross = _f(raw.get("r_multiple"))
            if entry is None or stop is None or r_gross is None:
                continue
            risk = abs(entry - stop)
            direction = str(raw.get("direction") or "LONG").upper()
            if direction == "LONG":
                exit_px = entry + r_gross * risk
            else:
                exit_px = entry - r_gross * risk
            r_net, fee = _fee_adjust_r(
                direction=direction,
                entry=entry,
                stop=stop,
                exit_px=exit_px,
                r_gross=r_gross,
            )
            rows.append(
                {
                    "symbol": raw.get("symbol"),
                    "timestamp": raw.get("timestamp"),
                    "exit_time": raw.get("exit_time"),
                    "direction": direction,
                    "playbook": "RANGE_MEAN_REVERSION",
                    "regime": regime,
                    "r_gross": r_gross,
                    # Primary comparison metric = net after fees (requested-risk basis)
                    "r_multiple": r_net,
                    "r_net": r_net,
                    "fees": fee,
                    "year": _year(raw.get("timestamp")),
                    "source": "COMBO_02_V2_validation_fee_adjusted",
                }
            )

    primary = [r for r in rows if r.get("regime") in PRIMARY_REGIMES]
    meta = {
        "n_range_playbook_all_regimes": len(rows),
        "n_primary_regimes": len(primary),
        "gross_total_r_all": sum(float(r["r_gross"]) for r in rows if r.get("r_gross") is not None),
        "gross_total_r_primary": sum(
            float(r["r_gross"]) for r in primary if r.get("r_gross") is not None
        ),
        "net_total_r_primary": sum(
            float(r["r_net"]) for r in primary if r.get("r_net") is not None
        ),
        "funding": "FUNDING_NOT_MODELED",
        "note": (
            "Published V2 validation R is gross. Comparison uses fee-adjusted net R "
            "on requested $20 risk units (same blotter constants as GRID)."
        ),
    }
    return rows, meta


def _load_candles_from_parquet(
    symbol: str, timeframe: str
) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    """Load validation-window OHLCV directly from research Parquet cache (no DB)."""
    from app.research.data_cache.cache_key import make_ohlcv_cache_key
    from app.research.data_cache.config import DATASET_VERSION, load_research_cache_config
    from app.research.data_cache.parquet_store import frame_to_candles, read_ohlcv_parquet
    from app.research.query_utils import resolve_date_bounds, slice_candles_for_research

    cfg = load_research_cache_config()
    key = make_ohlcv_cache_key(
        symbol=symbol,
        timeframe=timeframe,
        start_time=DATA_START,
        end_time=DATA_END,
        dataset_version=DATASET_VERSION,
    )
    path = cfg.ohlcv_dir() / f"{key}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"missing_research_parquet:{path}")
    df = read_ohlcv_parquet(path)
    raw = frame_to_candles(df)
    bounds = resolve_date_bounds(DATA_START, DATA_END)
    warmup = 100 if timeframe == "1h" else 200
    window, eval_start, slice_meta = slice_candles_for_research(
        raw,
        start=bounds["start"],
        end_exclusive=bounds["end_exclusive"],
        limit=None,
        warmup_bars=warmup,
    )
    meta = {
        "source": f"research_parquet_direct:{path.name}",
        "rows_raw": len(raw),
        "rows_window": len(window),
        "eval_start": eval_start,
        **slice_meta,
    }
    return window, int(eval_start or 0), meta


async def _run_symbol(symbol: str) -> dict[str, Any]:
    from app.research.grid_range_research.config import get_variant
    from app.research.grid_range_research.engine import run_grid_backtest

    t0 = time.perf_counter()
    print(f"  loading {symbol} from parquet cache...", flush=True)
    c1h, eval_start, meta1 = _load_candles_from_parquet(symbol, "1h")
    c15m, _, meta15 = _load_candles_from_parquet(symbol, "15m")
    idx0 = int(eval_start or 0)
    print(
        f"  [{symbol}] bars 1h={len(c1h)} 15m={len(c15m)} eval_start={idx0}",
        flush=True,
    )
    print(f"  [{symbol}] precomputing regimes/bounds...", flush=True)
    from app.research.grid_range_research.engine import (
        _build_1h_to_15m_map,
        _precompute_regimes_and_bounds,
    )

    pt0 = time.perf_counter()
    regimes, bounds = _precompute_regimes_and_bounds(
        c1h, symbol=symbol, index_start=idx0
    )
    map_15 = _build_1h_to_15m_map(c1h, c15m)
    print(
        f"  [{symbol}] precompute done in {time.perf_counter() - pt0:.1f}s",
        flush=True,
    )
    variant_out: dict[str, Any] = {}
    for vid in list(VARIANTS) + ["GRID-A-LVC"]:
        print(f"  [{symbol}] running {vid}...", flush=True)
        vt0 = time.perf_counter()
        variant = get_variant(vid)
        result = run_grid_backtest(
            symbol,
            c1h,
            c15m,
            variant,
            index_start=idx0,
            precomputed_regimes=regimes,
            precomputed_bounds=bounds,
            precomputed_map_15=map_15,
        )
        # Attach year on cycles if missing
        for c in result.cycles:
            if not c.get("year"):
                c["year"] = _year(c.get("exit_time") or c.get("entry_time"))
        variant_out[vid] = {
            "cycles": result.cycles,
            "sessions": result.sessions,
            "risk_summary": result.risk_summary,
            "funding_status": result.funding_status,
            "elapsed_seconds": round(time.perf_counter() - vt0, 3),
            "metrics": _bucket(result.cycles),
        }
        print(
            f"  [{symbol}] {vid} cycles={len(result.cycles)} "
            f"sessions={len(result.sessions)} "
            f"total_R={variant_out[vid]['metrics'].get('total_r')} "
            f"({variant_out[vid]['elapsed_seconds']}s)",
            flush=True,
        )
    return {
        "symbol": symbol,
        "bars_1h": len(c1h),
        "bars_15m": len(c15m),
        "eval_start": idx0,
        "source_1h": meta1.get("source"),
        "source_15m": meta15.get("source"),
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
        "variants": variant_out,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _fmt(v: Any, nd: int = 4) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        if v != v:  # nan
            return "n/a"
        if v == float("inf"):
            return "inf"
        return f"{v:.{nd}f}"
    return str(v)


def _build_report(
    symbol_results: list[dict[str, Any]],
    v2_rows: list[dict[str, Any]],
    v2_meta: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    from app.research.grid_range_research.metrics import trend_escape_stats

    # Aggregate per variant
    all_cycles: dict[str, list[dict[str, Any]]] = {v: [] for v in VARIANTS}
    all_sessions: dict[str, list[dict[str, Any]]] = {v: [] for v in VARIANTS}
    lvc_cycles: list[dict[str, Any]] = []
    lvc_sessions: list[dict[str, Any]] = []

    for sr in symbol_results:
        for vid, payload in (sr.get("variants") or {}).items():
            cycles = payload.get("cycles") or []
            sessions = payload.get("sessions") or []
            if vid == "GRID-A-LVC":
                lvc_cycles.extend(cycles)
                lvc_sessions.extend(sessions)
            elif vid in all_cycles:
                all_cycles[vid].extend(cycles)
                all_sessions[vid].extend(sessions)

    v2_primary = [r for r in v2_rows if r.get("regime") in PRIMARY_REGIMES]
    v2_m = _bucket(v2_primary)
    # Also gross reference
    v2_gross_total = sum(float(r["r_gross"]) for r in v2_primary if r.get("r_gross") is not None)

    compare_rows = {
        "V2_Range_net": v2_m,
        **{vid: _bucket(all_cycles[vid]) for vid in VARIANTS},
    }

    # By regime (primary variants + V2)
    by_regime: dict[str, dict[str, Any]] = {}
    for reg in COMPARE_REGIMES:
        by_regime[reg] = {
            "V2_Range": _bucket([r for r in v2_rows if r.get("regime") == reg]),
        }
        for vid in VARIANTS:
            by_regime[reg][vid] = _bucket(
                [r for r in all_cycles[vid] if r.get("regime") == reg]
            )
        # LVC optional from GRID-A-LVC only for that regime
        if reg == OPTIONAL_REGIME:
            by_regime[reg]["GRID-A-LVC"] = _bucket(
                [r for r in lvc_cycles if r.get("regime") == reg]
            )

    # By symbol
    by_symbol: dict[str, dict[str, Any]] = {}
    for sym in SYMBOLS:
        by_symbol[sym] = {
            "V2_Range": _bucket(
                [r for r in v2_primary if r.get("symbol") == sym]
            ),
        }
        for vid in VARIANTS:
            m = _bucket([r for r in all_cycles[vid] if r.get("symbol") == sym])
            sess = [s for s in all_sessions[vid] if s.get("symbol") == sym]
            m["max_inventory"] = max(
                (int(s.get("maximum_inventory") or 0) for s in sess), default=0
            )
            by_symbol[sym][vid] = m

    # By year
    years = ("2022", "2023", "2024", "2025", "2026")
    by_year: dict[str, dict[str, Any]] = {}
    for y in years:
        by_year[y] = {
            "V2_Range": _bucket([r for r in v2_primary if r.get("year") == y]),
        }
        for vid in VARIANTS:
            by_year[y][vid] = _bucket(
                [r for r in all_cycles[vid] if r.get("year") == y]
            )

    # Escape / best / worst — use GRID-A as primary research lens
    escape = {vid: trend_escape_stats(all_sessions[vid]) for vid in VARIANTS}
    worst = sorted(all_sessions["GRID-A"], key=lambda s: float(s.get("R_result") or 0))[:20]
    best = sorted(
        all_sessions["GRID-A"], key=lambda s: float(s.get("R_result") or 0), reverse=True
    )[:20]

    # Decision helpers
    grid_totals = {vid: compare_rows[vid]["total_r"] for vid in VARIANTS}
    best_variant = max(VARIANTS, key=lambda v: grid_totals[v])
    v2_total = compare_rows["V2_Range_net"]["total_r"]
    outperforms = grid_totals[best_variant] > v2_total
    positive = grid_totals[best_variant] > 0

    year_signs = {
        y: {
            vid: (by_year[y][vid]["total_r"] > 0)
            for vid in VARIANTS
        }
        for y in years
    }

    regime_benefit = {}
    for reg in PRIMARY_REGIMES:
        regime_benefit[reg] = {
            vid: by_regime[reg][vid]["total_r"] for vid in VARIANTS
        }
        regime_benefit[reg]["V2_Range"] = by_regime[reg]["V2_Range"]["total_r"]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_window": {"start": DATA_START, "end": DATA_END},
        "symbols": SYMBOLS,
        "funding": "FUNDING_NOT_MODELED",
        "v2_baseline_meta": v2_meta,
        "v2_gross_total_r_primary_regimes": v2_gross_total,
        "comparison": compare_rows,
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "by_year": by_year,
        "trend_escape": escape,
        "best_sessions_grid_a": best,
        "worst_sessions_grid_a": worst,
        "lvc_optional": {
            "metrics": _bucket([r for r in lvc_cycles if r.get("regime") == OPTIONAL_REGIME]),
            "n_cycles_all_regimes": len(lvc_cycles),
            "n_sessions": len(lvc_sessions),
        },
        "decisions": {
            "best_variant": best_variant,
            "grid_outperforms_v2_range_net": outperforms,
            "best_variant_positive_expectancy_net": positive,
            "year_positive_flags": year_signs,
            "regime_total_r": regime_benefit,
            "replace_range_mean_reversion": False,  # never auto-replace
        },
        "symbol_run_meta": [
            {
                "symbol": s["symbol"],
                "bars_1h": s["bars_1h"],
                "bars_15m": s["bars_15m"],
                "elapsed_seconds": s["elapsed_seconds"],
            }
            for s in symbol_results
        ],
    }

    # Markdown
    lines: list[str] = []
    lines.append("# GRID_RANGE_RESEARCH — Range Replacement Test")
    lines.append("")
    lines.append(
        "Research-only. COMBO_02_V2 remains frozen. "
        "Not a profitability claim. Fees applied; funding not modeled."
    )
    lines.append("")
    lines.append(f"Generated: {payload['generated_at']}")
    lines.append(f"Data window: {DATA_START} → {DATA_END}")
    lines.append(f"Symbols: {', '.join(SYMBOLS)}")
    lines.append("")
    lines.append("## Fee / funding assumptions")
    lines.append("")
    lines.append("- Taker fee: 0.04% (DEFAULT_TAKER_FEE)")
    lines.append("- Maker fee: 0.02% (unused — grid entries modeled as MARKET/taker)")
    lines.append("- Slippage: 0.02% (`SignalConfig.slippage_rate`)")
    lines.append("- Risk per grid position: $20 on $1000 equity, 2x leverage cap")
    lines.append("- **FUNDING_NOT_MODELED**")
    lines.append(
        "- V2 Range column = fee-adjusted net R from published validation trades "
        f"(gross primary-regime total R was {v2_gross_total:.4f})"
    )
    lines.append("")
    lines.append("## 1. Comparison (primary regimes: CHOPPY / RANGE / HIGH_VOLATILITY_RANGE)")
    lines.append("")
    lines.append(
        "| Metric | V2 Range | GRID-A | GRID-B | GRID-C |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    metrics_order = [
        ("trades", "Trades / completed grid cycles"),
        ("winning_cycles", "Winning cycles"),
        ("win_rate", "Win rate"),
        ("total_r", "Total R"),
        ("avg_r", "Avg R"),
        ("profit_factor", "Profit factor"),
        ("max_dd_r", "Max DD R"),
        ("max_inventory", "Max inventory"),
        ("max_exposure", "Max exposure"),
        ("fees", "Fees"),
        ("funding", "Funding"),
    ]
    # Attach session max inventory/exposure onto cycle metrics for grids
    for vid in VARIANTS:
        sess = all_sessions[vid]
        compare_rows[vid]["max_inventory"] = max(
            (int(s.get("maximum_inventory") or 0) for s in sess), default=0
        )
        compare_rows[vid]["max_exposure"] = max(
            (float(s.get("maximum_exposure") or 0) for s in sess), default=0.0
        )
    compare_rows["V2_Range_net"]["max_inventory"] = "n/a"
    compare_rows["V2_Range_net"]["max_exposure"] = "n/a"
    compare_rows["V2_Range_net"]["funding"] = "FUNDING_NOT_MODELED"
    for vid in VARIANTS:
        compare_rows[vid]["funding"] = "FUNDING_NOT_MODELED"

    for key, label in metrics_order:
        cells = [label]
        for col in ("V2_Range_net", "GRID-A", "GRID-B", "GRID-C"):
            cells.append(_fmt(compare_rows[col].get(key)))
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## 2. By regime")
    lines.append("")
    for reg in COMPARE_REGIMES:
        lines.append(f"### {reg}")
        lines.append("")
        lines.append("| Strategy | Trades | Total R | Avg R | PF | Max DD |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        keys = ["V2_Range", "GRID-A", "GRID-B", "GRID-C"]
        if reg == OPTIONAL_REGIME:
            keys.append("GRID-A-LVC")
        for k in keys:
            m = by_regime[reg].get(k) or {}
            lines.append(
                f"| {k} | {_fmt(m.get('trades'), 0)} | {_fmt(m.get('total_r'))} | "
                f"{_fmt(m.get('avg_r'))} | {_fmt(m.get('profit_factor'))} | "
                f"{_fmt(m.get('max_dd_r'))} |"
            )
        lines.append("")

    lines.append("## 3. By symbol (GRID-A focus + V2)")
    lines.append("")
    lines.append(
        "| Symbol | V2 trades | V2 R | A cycles | A R | A avg | A PF | A maxDD | A maxInv |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for sym in SYMBOLS:
        v2 = by_symbol[sym]["V2_Range"]
        a = by_symbol[sym]["GRID-A"]
        lines.append(
            f"| {sym} | {_fmt(v2.get('trades'), 0)} | {_fmt(v2.get('total_r'))} | "
            f"{_fmt(a.get('trades'), 0)} | {_fmt(a.get('total_r'))} | {_fmt(a.get('avg_r'))} | "
            f"{_fmt(a.get('profit_factor'))} | {_fmt(a.get('max_dd_r'))} | "
            f"{_fmt(a.get('max_inventory'), 0)} |"
        )
    lines.append("")
    lines.append("### Full variant symbol table")
    lines.append("")
    for vid in VARIANTS:
        lines.append(f"#### {vid}")
        lines.append("")
        lines.append("| Symbol | Cycles | Total R | Avg R | PF | Max DD | Max Inv |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for sym in SYMBOLS:
            m = by_symbol[sym][vid]
            lines.append(
                f"| {sym} | {_fmt(m.get('trades'), 0)} | {_fmt(m.get('total_r'))} | "
                f"{_fmt(m.get('avg_r'))} | {_fmt(m.get('profit_factor'))} | "
                f"{_fmt(m.get('max_dd_r'))} | {_fmt(m.get('max_inventory'), 0)} |"
            )
        lines.append("")

    lines.append("## 4. By year")
    lines.append("")
    lines.append("| Year | V2 R | GRID-A R | GRID-B R | GRID-C R | A trades |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for y in years:
        lines.append(
            f"| {y} | {_fmt(by_year[y]['V2_Range'].get('total_r'))} | "
            f"{_fmt(by_year[y]['GRID-A'].get('total_r'))} | "
            f"{_fmt(by_year[y]['GRID-B'].get('total_r'))} | "
            f"{_fmt(by_year[y]['GRID-C'].get('total_r'))} | "
            f"{_fmt(by_year[y]['GRID-A'].get('trades'), 0)} |"
        )
    lines.append("")

    lines.append("## 5. Trend escape test (RANGE → TREND / breakout)")
    lines.append("")
    for vid in VARIANTS:
        e = escape[vid]
        lines.append(f"### {vid}")
        lines.append("")
        for k, v in e.items():
            lines.append(f"- {k}: `{_fmt(v) if not isinstance(v, str) else v}`")
        lines.append("")

    lines.append("## 6. Worst 20 GRID-A sessions")
    lines.append("")
    lines.append(
        "| symbol | start | end | regime | hi | lo | spacing | levels | positions | maxInv | maxExp | fees | R | stop |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in worst:
        lines.append(
            f"| {s.get('symbol')} | {s.get('start_timestamp')} | {s.get('end_timestamp')} | "
            f"{s.get('regime')} | {_fmt(s.get('range_high'))} | {_fmt(s.get('range_low'))} | "
            f"{_fmt(s.get('grid_spacing'))} | {_fmt(s.get('number_of_levels'), 0)} | "
            f"{_fmt(s.get('number_of_positions'), 0)} | {_fmt(s.get('maximum_inventory'), 0)} | "
            f"{_fmt(s.get('maximum_exposure'))} | {_fmt(s.get('total_fees'))} | "
            f"{_fmt(s.get('R_result'))} | {s.get('reason_grid_stopped')} |"
        )
    lines.append("")

    lines.append("## 7. Best 20 GRID-A sessions")
    lines.append("")
    lines.append(
        "| symbol | start | end | regime | hi | lo | spacing | levels | positions | maxInv | maxExp | fees | R | stop |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in best:
        lines.append(
            f"| {s.get('symbol')} | {s.get('start_timestamp')} | {s.get('end_timestamp')} | "
            f"{s.get('regime')} | {_fmt(s.get('range_high'))} | {_fmt(s.get('range_low'))} | "
            f"{_fmt(s.get('grid_spacing'))} | {_fmt(s.get('number_of_levels'), 0)} | "
            f"{_fmt(s.get('number_of_positions'), 0)} | {_fmt(s.get('maximum_inventory'), 0)} | "
            f"{_fmt(s.get('maximum_exposure'))} | {_fmt(s.get('total_fees'))} | "
            f"{_fmt(s.get('R_result'))} | {s.get('reason_grid_stopped')} |"
        )
    lines.append("")

    lines.append("## 8. Final decision answers")
    lines.append("")
    lines.append(
        f"### Question 1 — Does GRID outperform V2 RANGE_MEAN_REVERSION (net R, primary regimes)?\n"
        f"**{'YES' if outperforms else 'NO'}** — best variant `{best_variant}` "
        f"total R `{_fmt(grid_totals[best_variant])}` vs V2 net `{_fmt(v2_total)}`."
    )
    lines.append("")
    lines.append(
        f"### Question 2 — Positive expectancy after fees?\n"
        f"**{'YES' if positive else 'NO'}** — best variant avg R "
        f"`{_fmt(compare_rows[best_variant].get('avg_r'))}` "
        f"(total R `{_fmt(grid_totals[best_variant])}`)."
    )
    lines.append("")
    lines.append(
        f"### Question 3 — Strongest variant?\n"
        f"**{best_variant}** by total net R among GRID-A/B/C."
    )
    lines.append("")
    lines.append("### Question 4 — Consistent across years?")
    lines.append("")
    for y in years:
        flags = ", ".join(
            f"{vid}={'+' if year_signs[y][vid] else '-'}" for vid in VARIANTS
        )
        lines.append(f"- {y}: {flags}")
    consistent = all(
        any(year_signs[y][vid] for vid in VARIANTS) for y in years if by_year[y]["GRID-A"]["trades"]
    )
    lines.append("")
    lines.append(
        f"Verdict: **{'mixed/partial' if not all(year_signs[y][best_variant] for y in years) else 'positive in all years with trades'}** "
        f"(consistent_any_positive_year_flag={consistent})."
    )
    lines.append("")
    lines.append("### Question 5 — Which regimes benefit?")
    lines.append("")
    for reg in PRIMARY_REGIMES:
        v2r = regime_benefit[reg]["V2_Range"]
        ar = regime_benefit[reg]["GRID-A"]
        lines.append(f"- {reg}: V2 `{_fmt(v2r)}` vs GRID-A `{_fmt(ar)}`")
    lvc_m = payload["lvc_optional"]["metrics"]
    lines.append(
        f"- LOW_VOLATILITY_COMPRESSION (GRID-A-LVC only): `{_fmt(lvc_m.get('total_r'))}` "
        f"on `{_fmt(lvc_m.get('trades'), 0)}` cycles"
    )
    lines.append("")
    lines.append("### Question 6 — What happens when the range breaks?")
    lines.append("")
    ea = escape["GRID-A"]
    lines.append(
        f"- Breakout sessions: `{ea.get('sessions_with_breakout')}`; "
        f"max inventory at breakout `{ea.get('max_inventory_at_breakout')}`; "
        f"worst loss `{_fmt(ea.get('worst_grid_loss_r'))}`; "
        f"avg loss `{_fmt(ea.get('avg_loss_r'))}`."
    )
    lines.append(f"- Note: {ea.get('note')}")
    lines.append("")
    lines.append("### Question 7 — Unacceptable inventory / drawdown?")
    lines.append("")
    max_inv = compare_rows[best_variant].get("max_inventory")
    max_dd = compare_rows[best_variant].get("max_dd_r")
    unacceptable = (isinstance(max_inv, (int, float)) and max_inv > 3) or (
        isinstance(max_dd, (int, float)) and max_dd > abs(v2_total) and max_dd > 50
    )
    lines.append(
        f"- Best variant max inventory `{_fmt(max_inv, 0)}` (cap={get_variant_cap(best_variant)}), "
        f"max DD R `{_fmt(max_dd)}`. "
        f"**{'YES — concerning' if unacceptable else 'Inventory cap held; DD still research-judgement'}**."
    )
    lines.append("")
    lines.append("### Question 8 — Should GRID replace RANGE_MEAN_REVERSION?")
    lines.append("")
    replace = bool(outperforms and positive and not unacceptable)
    lines.append(
        f"**{'CANDIDATE — do not implement yet; needs review' if replace else 'NO — do not replace'}**. "
        "Architecture sketch must not be implemented until explicitly approved."
    )
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append("- `research_report.json`")
    lines.append("- `all_cycles.csv` / `all_sessions.csv`")
    lines.append("- `best_sessions_grid_a.csv` / `worst_sessions_grid_a.csv`")
    lines.append("- `by_year.csv` / `by_symbol.csv` / `by_regime.csv`")
    lines.append("- per-symbol `*_cycles.csv`")
    lines.append("")
    lines.append("## Safety")
    lines.append("")
    lines.append("- COMBO_02 v1 / COMBO_02_V2 packages were not modified.")
    lines.append("- New code lives under `app/research/grid_range_research/` + this script + tests.")
    lines.append("")

    return "\n".join(lines), payload


def get_variant_cap(vid: str) -> int:
    from app.research.grid_range_research.config import get_variant

    return get_variant(vid).max_simultaneous


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading V2 RANGE baseline...", flush=True)
    v2_rows, v2_meta = _load_v2_range_baseline()
    print(f"  V2 range rows={len(v2_rows)} meta={v2_meta}", flush=True)

    results: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        print(f"=== {sym} ===", flush=True)
        results.append(await _run_symbol(sym))

    md, payload = _build_report(results, v2_rows, v2_meta)

    # Flatten exports
    all_cycles: list[dict[str, Any]] = []
    all_sessions: list[dict[str, Any]] = []
    for sr in results:
        for vid, p in (sr.get("variants") or {}).items():
            all_cycles.extend(p.get("cycles") or [])
            all_sessions.extend(p.get("sessions") or [])
            _write_csv(OUT_DIR / f"{sr['symbol']}_{vid}_cycles.csv", p.get("cycles") or [])
            _write_csv(
                OUT_DIR / f"{sr['symbol']}_{vid}_sessions.csv", p.get("sessions") or []
            )

    _write_csv(OUT_DIR / "all_cycles.csv", all_cycles)
    _write_csv(OUT_DIR / "all_sessions.csv", all_sessions)
    _write_csv(OUT_DIR / "best_sessions_grid_a.csv", payload["best_sessions_grid_a"])
    _write_csv(OUT_DIR / "worst_sessions_grid_a.csv", payload["worst_sessions_grid_a"])

    # Compact tables
    year_rows = []
    for y, cols in payload["by_year"].items():
        row = {"year": y}
        for k, m in cols.items():
            row[f"{k}_trades"] = m.get("trades")
            row[f"{k}_total_r"] = m.get("total_r")
        year_rows.append(row)
    _write_csv(OUT_DIR / "by_year.csv", year_rows)

    sym_rows = []
    for sym, cols in payload["by_symbol"].items():
        row = {"symbol": sym}
        for k, m in cols.items():
            row[f"{k}_trades"] = m.get("trades")
            row[f"{k}_total_r"] = m.get("total_r")
            row[f"{k}_max_inventory"] = m.get("max_inventory")
        sym_rows.append(row)
    _write_csv(OUT_DIR / "by_symbol.csv", sym_rows)

    reg_rows = []
    for reg, cols in payload["by_regime"].items():
        row = {"regime": reg}
        for k, m in cols.items():
            row[f"{k}_trades"] = m.get("trades")
            row[f"{k}_total_r"] = m.get("total_r")
            row[f"{k}_pf"] = m.get("profit_factor")
        reg_rows.append(row)
    _write_csv(OUT_DIR / "by_regime.csv", reg_rows)

    (OUT_DIR / "research_report.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    (OUT_DIR / "RESEARCH_REPORT.md").write_text(md, encoding="utf-8")
    print(f"Wrote report -> {OUT_DIR / 'RESEARCH_REPORT.md'}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
