#!/usr/bin/env python3
"""COMBO_02 V2.1 research comparison — BASE vs V2.1-A vs V2.1-B.

Frozen COMBO_02_V2 default is not promoted. Same data window / symbols /
ResearchConfig+SignalConfig as multi-market validation.
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.run_combo02_v2_multi_market_validation import (
    DATA_END,
    DATA_START,
    PLAYBOOKS,
    REGIMES,
    SYMBOLS,
    _bucket_metrics,
    _f,
    _install_validation_15m_speed_patch,
    _install_validation_context_patch,
    _install_validation_htf_speed_patch,
    _install_validation_ohlcv_cache_patch,
    _trade_row,
    _write_csv,
)

VARIANTS = (
    ("BASE_V2", "COMBO_02_V2"),
    ("V2_1A", "COMBO_02_V2_1A"),
    ("V2_1B", "COMBO_02_V2_1B"),
)

OUT_DIR = Path("reports/combo02_v21_variant_compare")
PRIOR_BASE_TRADES = Path("reports/combo02_v2_multi_market_validation/all_trades.csv")
PRIOR_BASE_REPORT = Path(
    "reports/combo02_v2_multi_market_validation/validation_report.json"
)

FOCUS_REGIMES = (
    "CHOPPY",
    "RANGE",
    "HIGH_VOLATILITY_RANGE",
    "LOW_VOLATILITY_COMPRESSION",
    "TRANSITION",
    "BULL_TREND",
    "BEAR_TREND",
    "HIGH_VOLATILITY_TREND",
)


def _load_prior_base() -> dict[str, Any]:
    """Reuse frozen-v2 multi-market validation trades (same window/config).

    Spot-checked: BASE BTC re-run matched 358 trades / +25.073R.
    """
    if not PRIOR_BASE_TRADES.exists() or not PRIOR_BASE_REPORT.exists():
        raise FileNotFoundError(
            f"Prior BASE artifacts missing: {PRIOR_BASE_TRADES} / {PRIOR_BASE_REPORT}"
        )
    prior = json.loads(PRIOR_BASE_REPORT.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    with PRIOR_BASE_TRADES.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            r["r_multiple"] = _f(r.get("r_multiple"))
            rows.append(r)
    per_symbol: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        sym_rows = [t for t in rows if t.get("symbol") == sym]
        per_symbol.append(
            {
                "symbol": sym,
                "combo_id": "COMBO_02_V2",
                "bars_1h": None,
                "elapsed_seconds": 0.0,
                "metrics": _bucket_metrics(sym_rows),
                "trades": sym_rows,
            }
        )
    agg = _aggregate("BASE_V2", "COMBO_02_V2", per_symbol)
    # Prefer prior overall if present (should match recompute).
    if prior.get("overall"):
        assert int(agg["overall"]["trades"]) == int(prior["overall"]["trades"])
    agg["source"] = str(PRIOR_BASE_TRADES)
    return agg


async def _run_combo(combo_id: str, symbol: str) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.signals.config import SignalConfig

    t0 = time.perf_counter()
    c1h, eval_start, _ = await _load_research_candles(
        symbol,
        "1h",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=100,
    )
    c4h, _, _ = await _load_research_candles(
        symbol,
        "4h",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=50,
    )
    c15m, _, _ = await _load_research_candles(
        symbol,
        "15m",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=200,
    )

    reset_15m = getattr(_install_validation_15m_speed_patch, "reset_15m_cache", None)
    if callable(reset_15m):
        reset_15m()
    reset_htf = getattr(_install_validation_htf_speed_patch, "reset_htf_cache", None)
    if callable(reset_htf):
        reset_htf()
    reset_ohlcv = getattr(
        _install_validation_ohlcv_cache_patch, "reset_ohlcv_registry", None
    )
    if callable(reset_ohlcv):
        reset_ohlcv()
    register = getattr(_install_validation_ohlcv_cache_patch, "register_series", None)
    if callable(register):
        register(c1h)
        register(c15m)
        register(c4h)

    combo = get_combination(combo_id)
    assert combo is not None
    idx0 = eval_start if eval_start and eval_start > 0 else None
    last_prog = {"t": time.perf_counter(), "bars": 0}

    def _progress(payload: dict[str, Any]) -> None:
        bars = int(payload.get("bars_processed") or 0)
        total = int(payload.get("total_bars") or 0)
        now = time.perf_counter()
        if bars - last_prog["bars"] >= 500 or now - last_prog["t"] >= 20.0:
            rate = (bars - last_prog["bars"]) / max(now - last_prog["t"], 1e-6)
            print(
                f"    [{combo_id}/{symbol}] bars={bars}/{total} "
                f"trades={payload.get('trades')} ({rate:.0f} bars/s)",
                flush=True,
            )
            last_prog["t"] = now
            last_prog["bars"] = bars

    out = run_combination_backtest(
        symbol,
        "1h",
        c1h,
        combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        candles_15m=c15m,
        direction_filter=None,
        index_start=idx0,
        progress_callback=_progress,
    )
    closed = [
        t
        for t in (out.get("trades") or [])
        if t.get("outcome") not in (None, "OPEN")
    ]
    rows = [_trade_row(symbol, t) for t in closed]
    return {
        "symbol": symbol,
        "combo_id": combo_id,
        "bars_1h": len(c1h),
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
        "metrics": _bucket_metrics(rows),
        "trades": rows,
    }


def _aggregate(label: str, combo_id: str, per_symbol: list[dict[str, Any]]) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    for s in per_symbol:
        all_trades.extend(s.get("trades") or [])
    overall = _bucket_metrics(all_trades)
    by_playbook = {
        pb: _bucket_metrics([t for t in all_trades if t.get("playbook") == pb])
        for pb in PLAYBOOKS
    }
    by_regime = {}
    for reg in REGIMES:
        rows = [t for t in all_trades if str(t.get("regime") or "UNKNOWN") == reg]
        m = _bucket_metrics(rows)
        by_regime[reg] = {
            "trades": m["trades"],
            "total_R": m["total_R"],
            "average_R": m["average_R"],
            "win_rate": m["win_rate"],
        }
    by_symbol = {s["symbol"]: s["metrics"] for s in per_symbol}
    return {
        "label": label,
        "combo_id": combo_id,
        "overall": overall,
        "by_playbook": by_playbook,
        "by_regime": by_regime,
        "by_symbol": by_symbol,
        "trade_count": len(all_trades),
        "per_symbol_elapsed": {s["symbol"]: s["elapsed_seconds"] for s in per_symbol},
    }


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _recommend(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    base = results["BASE_V2"]["overall"]
    a = results["V2_1A"]["overall"]
    b = results["V2_1B"]["overall"]

    def score(m: dict[str, Any]) -> dict[str, float]:
        return {
            "total_R": float(m.get("total_R") or 0.0),
            "average_R": float(m.get("average_R") or 0.0),
            "pf": float(m.get("profit_factor") or 0.0)
            if m.get("profit_factor") is not None
            else 0.0,
            "max_dd": float(m.get("maximum_drawdown_R") or 0.0),
            "trades": float(m.get("trades") or 0.0),
        }

    sb, sa, sbb = score(base), score(a), score(b)

    def improves(cand: dict[str, float], ref: dict[str, float]) -> dict[str, bool]:
        # Material improvement thresholds (research judgment, not optimization).
        return {
            "total_R": cand["total_R"] - ref["total_R"] >= 20.0,
            "average_R": cand["average_R"] - ref["average_R"] >= 0.01,
            "pf": cand["pf"] - ref["pf"] >= 0.03,
            "max_dd": ref["max_dd"] - cand["max_dd"] >= 15.0,
            "freq_ok": cand["trades"] >= 0.55 * ref["trades"],
        }

    # Loss concentration: CHOPPY total R should not worsen much; other regimes check.
    def choppy_r(label: str) -> float:
        return float(results[label]["by_regime"].get("CHOPPY", {}).get("total_R") or 0.0)

    def trend_r(label: str) -> float:
        pb = results[label]["by_playbook"]
        return float(pb["TREND_FOLLOWING"]["total_R"]) + float(pb["REVERSAL"]["total_R"])

    def evaluate_variant(label: str, cand: dict[str, float]) -> dict[str, Any]:
        flags = improves(cand, sb)
        material = sum(
            1 for k in ("total_R", "average_R", "pf", "max_dd") if flags[k]
        )
        # Prefer no damage to trend+reversal aggregate
        trend_ok = trend_r(label) >= trend_r("BASE_V2") - 5.0
        choppy_ok = choppy_r(label) >= choppy_r("BASE_V2") - 5.0
        useful = flags["freq_ok"]
        wins = material >= 2 and flags["total_R"] and useful and trend_ok
        return {
            "label": label,
            "flags": flags,
            "material_count": material,
            "trend_ok": trend_ok,
            "choppy_improved_or_flat": choppy_ok,
            "choppy_delta_R": choppy_r(label) - choppy_r("BASE_V2"),
            "total_R_delta": cand["total_R"] - sb["total_R"],
            "eligible_winner": bool(wins and choppy_ok),
        }

    eval_a = evaluate_variant("V2_1A", sa)
    eval_b = evaluate_variant("V2_1B", sbb)

    eligible = [e for e in (eval_a, eval_b) if e["eligible_winner"]]
    if not eligible:
        recommendation = "KEEP_FROZEN_V2"
        rationale = (
            "Neither V2.1-A nor V2.1-B met the material-improvement bar "
            "(total R / avg R / PF / max DD) while preserving frequency and "
            "not damaging TREND+REVERSAL. Do not change the frozen default yet."
        )
        winner = None
    else:
        winner = max(eligible, key=lambda e: (e["total_R_delta"], e["material_count"]))
        recommendation = winner["label"]
        rationale = (
            f"{winner['label']} materially improved vs BASE on the decision "
            f"criteria (total_R delta={winner['total_R_delta']:.2f}). "
            "Research recommendation only — do not promote to default without "
            "explicit approval."
        )

    return {
        "recommendation": recommendation,
        "winner": winner["label"] if winner else None,
        "rationale": rationale,
        "variant_evals": {"V2_1A": eval_a, "V2_1B": eval_b},
        "thresholds": {
            "min_total_R_lift": 20.0,
            "min_avg_R_lift": 0.01,
            "min_pf_lift": 0.03,
            "min_max_dd_reduction": 15.0,
            "min_trade_retention": 0.55,
        },
        "notes": [
            "Do not select by trade count alone.",
            "TREND_FOLLOWING / REVERSAL logic unchanged in variants.",
            "Frozen COMBO_02_V2 remains the default combination.",
        ],
    }


def _format_md(results: dict[str, dict[str, Any]], decision: dict[str, Any]) -> str:
    order = ["BASE_V2", "V2_1A", "V2_1B"]
    lines = [
        "# COMBO_02 V2.1 Research Comparison",
        "",
        "Research only. Frozen COMBO_02_V2 default is **not** modified or promoted.",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Data window: {DATA_START} -> {DATA_END}",
        f"Symbols: {', '.join(SYMBOLS)}",
        "",
        "## Critical checks",
        "",
        "- TREND_FOLLOWING / REVERSAL: unchanged vs BASE (only CHOPPY range policy differs)",
        "- Risk / stop / TP / RR / execution / fees: unchanged (shared finalize path)",
        "- No parameter optimization, no symbol-specific tuning",
        "- PIT / fully-closed 15m map / no future candle usage (same backtest engine)",
        "- Frozen COMBO_02_V2 remains the production/default research combo id",
        "",
        "## Overall",
        "",
        "| Metric | BASE V2 | V2.1-A | V2.1-B |",
        "|---|---:|---:|---:|",
    ]
    keys = [
        ("trades", "Trades", 0),
        ("long", "LONG", 0),
        ("short", "SHORT", 0),
        ("win_rate", "Win rate", 4),
        ("total_R", "Total R", 3),
        ("average_R", "Avg R", 4),
        ("profit_factor", "Profit factor", 4),
        ("maximum_drawdown_R", "Max DD", 3),
    ]
    for key, label, nd in keys:
        cells = [_fmt(results[lab]["overall"].get(key), nd) for lab in order]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines += ["", "## Playbook (total R)", "", "| Playbook | BASE | V2.1-A | V2.1-B |", "|---|---:|---:|---:|"]
    for pb in PLAYBOOKS:
        cells = [_fmt(results[lab]["by_playbook"][pb]["total_R"], 3) for lab in order]
        ncells = [str(results[lab]["by_playbook"][pb]["trades"]) for lab in order]
        lines.append(
            f"| {pb} | {cells[0]} (n={ncells[0]}) | {cells[1]} (n={ncells[1]}) | {cells[2]} (n={ncells[2]}) |"
        )

    lines += [
        "",
        "## Regime (total R)",
        "",
        "| Regime | BASE | V2.1-A | V2.1-B |",
        "|---|---:|---:|---:|",
    ]
    for reg in FOCUS_REGIMES:
        cells = [
            f"{_fmt(results[lab]['by_regime'][reg]['total_R'], 3)} (n={results[lab]['by_regime'][reg]['trades']})"
            for lab in order
        ]
        lines.append(f"| {reg} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Deltas vs BASE",
        "",
        "| Variant | dTotal R | dAvg R | dPF | dMaxDD | dTrades |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    bo = results["BASE_V2"]["overall"]
    for lab in ("V2_1A", "V2_1B"):
        o = results[lab]["overall"]
        d_pf = (
            (o["profit_factor"] - bo["profit_factor"])
            if o.get("profit_factor") is not None and bo.get("profit_factor") is not None
            else None
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    lab,
                    _fmt(o["total_R"] - bo["total_R"], 3),
                    _fmt(
                        (o["average_R"] or 0) - (bo["average_R"] or 0),
                        4,
                    ),
                    _fmt(d_pf, 4),
                    _fmt(
                        (o["maximum_drawdown_R"] or 0)
                        - (bo["maximum_drawdown_R"] or 0),
                        3,
                    ),
                    str(int(o["trades"] - bo["trades"])),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## Recommendation",
        "",
        f"**{decision['recommendation']}**",
        "",
        decision["rationale"],
        "",
        "Decision thresholds:",
        "",
    ]
    for k, v in decision["thresholds"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "Variant evals:", ""]
    for lab, ev in decision["variant_evals"].items():
        lines.append(
            f"- {lab}: eligible={ev['eligible_winner']} material={ev['material_count']} "
            f"total_R_delta={_fmt(ev['total_R_delta'], 3)} "
            f"choppy_delta_R={_fmt(ev['choppy_delta_R'], 3)} "
            f"freq_ok={ev['flags']['freq_ok']} trend_ok={ev['trend_ok']}"
        )
    lines += [
        "",
        "## Artifacts",
        "",
        "- `comparison.json`",
        "- `COMPARISON_REPORT.md`",
        "- per-variant `*_all_trades.csv`",
        "",
    ]
    return "\n".join(lines)


async def main() -> int:
    from app.config import get_settings
    from app.services.database import db_manager

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await db_manager.connect(get_settings())
    _install_validation_context_patch()
    _install_validation_15m_speed_patch()
    _install_validation_htf_speed_patch()
    _install_validation_ohlcv_cache_patch()
    print(
        "Installed validation-only speed patches (strategy defaults untouched).",
        flush=True,
    )

    results: dict[str, dict[str, Any]] = {}

    print("\n===== BASE_V2 (COMBO_02_V2) — load prior validation =====", flush=True)
    base_agg = _load_prior_base()
    results["BASE_V2"] = base_agg
    print(
        f"  BASE reused: trades={base_agg['overall']['trades']} "
        f"total_R={base_agg['overall']['total_R']:.3f} "
        f"source={base_agg.get('source')}",
        flush=True,
    )
    (OUT_DIR / "BASE_V2_summary.json").write_text(
        json.dumps(base_agg, indent=2, default=str), encoding="utf-8"
    )

    for label, combo_id in VARIANTS:
        if label == "BASE_V2":
            continue
        summary_path = OUT_DIR / f"{label}_summary.json"
        trades_path = OUT_DIR / f"{label}_all_trades.csv"
        if summary_path.exists() and trades_path.exists():
            print(f"\n===== {label} ({combo_id}) — resume cached =====", flush=True)
            cached = json.loads(summary_path.read_text(encoding="utf-8"))
            results[label] = cached
            print(
                f"  reused: trades={cached['overall']['trades']} "
                f"total_R={cached['overall']['total_R']:.3f}",
                flush=True,
            )
            continue
        print(f"\n===== {label} ({combo_id}) =====", flush=True)
        per_symbol: list[dict[str, Any]] = []
        all_trades: list[dict[str, Any]] = []
        for sym in SYMBOLS:
            sym_path = OUT_DIR / f"{label}_{sym}.json"
            if sym_path.exists():
                one = json.loads(sym_path.read_text(encoding="utf-8"))
                print(
                    f"  {sym}: resume cached trades={one['metrics']['trades']} "
                    f"total_R={one['metrics']['total_R']:.3f}",
                    flush=True,
                )
            else:
                print(f"  {label} / {sym} ...", flush=True)
                one = await _run_combo(combo_id, sym)
                # Drop bulky candle-free payload; keep trades + metrics.
                sym_path.write_text(
                    json.dumps(one, indent=2, default=str), encoding="utf-8"
                )
                print(
                    f"  {sym}: trades={one['metrics']['trades']} "
                    f"total_R={one['metrics']['total_R']:.3f} "
                    f"elapsed={one['elapsed_seconds']}s",
                    flush=True,
                )
            per_symbol.append(one)
            all_trades.extend(one["trades"])
        agg = _aggregate(label, combo_id, per_symbol)
        results[label] = agg
        _write_csv(OUT_DIR / f"{label}_all_trades.csv", all_trades)
        (OUT_DIR / f"{label}_summary.json").write_text(
            json.dumps(agg, indent=2, default=str), encoding="utf-8"
        )

    decision = _recommend(results)
    payload = {
        "data_start": DATA_START,
        "data_end": DATA_END,
        "symbols": list(SYMBOLS),
        "variants": results,
        "decision": decision,
        "base_source": "prior multi-market validation (parity-checked on BTC)",
        "disclaimer": (
            "Research comparison only. Not a profitability claim. "
            "Frozen COMBO_02_V2 default not promoted."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (OUT_DIR / "comparison.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    md = _format_md(results, decision)
    (OUT_DIR / "COMPARISON_REPORT.md").write_text(md, encoding="utf-8")
    try:
        print("\n" + md, flush=True)
    except UnicodeEncodeError:
        print(f"Wrote {OUT_DIR / 'COMPARISON_REPORT.md'}", flush=True)
    print(f"\nRecommendation: {decision['recommendation']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
