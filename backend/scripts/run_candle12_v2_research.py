"""Run Candle-1/Candle-2 research V2 on persisted real OHLCV from PostgreSQL.

Loads full history per series (bypasses in-memory 500-bar store).
Processes symbol × timeframe one series at a time (releases memory).
Does not modify live signal engine or production thresholds.
Does not optimize parameters on OOS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.candle12_v2 import (
    HYPOTHESIS_V2,
    Candle12V2Config,
    aggregate_v2_trades,
    run_candle12_v2,
)
from app.research.config import split_period_indices
from app.research.postgres_ohlcv import list_symbols_with_ohlcv, load_ohlcv_series
from app.research.rolling_structure import lookback_documentation
from app.services.database import db_manager
from app.signals.config import SignalConfig

TIMEFRAMES = ["5m", "15m", "1h"]
MIN_SAMPLE_WARN = 30
BENCHMARK_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _row(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_size": m.get("sample_size", 0),
        "sample_size_warning": m.get("sample_size_warning"),
        "sample_label": m.get("sample_label"),
        "expectancy_R": m.get("expectancy_R"),
        "average_R": m.get("average_R"),
        "median_R": m.get("median_R"),
        "tp1_rate": m.get("tp1_hit_rate"),
        "tp2_rate": m.get("tp2_hit_rate"),
        "tp3_rate": m.get("tp3_hit_rate"),
        "sl_rate": m.get("sl_rate"),
        "profit_factor": m.get("profit_factor"),
        "max_drawdown_R": m.get("max_drawdown_R"),
        "average_MAE_R": m.get("average_MAE_R"),
        "average_MFE_R": m.get("average_MFE_R"),
        "average_holding_time": m.get("average_holding_time"),
        "median_holding_time": m.get("median_holding_time"),
        "long_count": m.get("long_setups"),
        "short_count": m.get("short_setups"),
        "gross_R": m.get("gross_R"),
        "net_R": m.get("net_R"),
        "fees": m.get("fees"),
        "slippage": m.get("slippage"),
        "ambiguous_trades": m.get("ambiguous_count") or m.get("ambiguous_trades"),
        "data_quality": m.get("data_quality"),
        "gap_count": m.get("gap_count"),
        "duplicate_count": m.get("duplicate_count"),
        "calendar_start": m.get("calendar_start"),
        "calendar_end": m.get("calendar_end"),
    }


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
        if x == float("inf"):
            return "inf"
        return f"{x*100:.1f}%" if pct else f"{x:.4f}"
    except (TypeError, ValueError):
        return str(v)


def print_metrics(title: str, m: dict[str, Any]) -> None:
    r = _row(m)
    print(f"\n=== {title} ===")
    print(f"  sample_size        : {r['sample_size']}")
    if r["sample_size_warning"]:
        print(f"  WARNING            : {r['sample_size_warning'].get('message')}")
    print(f"  LONG / SHORT       : {r['long_count']} / {r['short_count']}")
    print(f"  expectancy R       : {_fmt(r['expectancy_R'])}")
    print(f"  average / median R : {_fmt(r['average_R'])} / {_fmt(r['median_R'])}")
    print(
        f"  TP1 / TP2 / TP3    : {_fmt(r['tp1_rate'], True)} / "
        f"{_fmt(r['tp2_rate'], True)} / {_fmt(r['tp3_rate'], True)}"
    )
    print(f"  SL rate            : {_fmt(r['sl_rate'], True)}")
    print(f"  profit factor      : {_fmt(r['profit_factor'])}")
    print(f"  max drawdown R     : {_fmt(r['max_drawdown_R'])}")
    print(f"  MAE_R / MFE_R      : {_fmt(r['average_MAE_R'])} / {_fmt(r['average_MFE_R'])}")
    print(
        f"  holding bars       : avg={_fmt(r['average_holding_time'])} "
        f"med={_fmt(r['median_holding_time'])}"
    )
    print(f"  gross_R / net_R    : {_fmt(r['gross_R'])} / {_fmt(r['net_R'])}")
    print(f"  ambiguous          : {r['ambiguous_trades']}")


def _progress(info: dict[str, Any]) -> None:
    processed = info.get("processed", 0)
    total = info.get("candles", 0)
    elapsed = float(info.get("elapsed") or 0)
    rate = (processed / elapsed) if elapsed > 0 else None
    eta = None
    if rate and total and processed < total:
        eta = (total - processed) / rate
    msg = (
        f"  {info.get('symbol')} {info.get('timeframe')}  "
        f"{total} candles  processed {processed}/{total}  "
        f"trades {info.get('trades')}  elapsed {elapsed:.1f}s"
    )
    if eta is not None:
        msg += f"  ETA {eta:.1f}s"
    print(msg, flush=True)


async def _connect() -> None:
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled or db_manager.engine is None:
        raise RuntimeError("DATABASE unavailable")


async def resolve_symbols(mode: str) -> list[str]:
    if mode == "benchmark":
        return list(BENCHMARK_SYMBOLS)
    # Full universe: data-driven from Postgres (no hardcoded coin list)
    symbols = await list_symbols_with_ohlcv()
    return symbols


async def run_research(mode: str) -> dict[str, Any]:
    await _connect()
    symbols = await resolve_symbols(mode)
    scfg = SignalConfig()
    v2 = Candle12V2Config(
        trading_fee=scfg.fee_rate,
        entry_slippage=scfg.slippage_rate,
        exit_slippage=scfg.slippage_rate,
        tick_size=0.0,
        require_impulse=True,
        min_bars=50,
        min_coverage_ratio=0.80,
        min_sample_size_warning=MIN_SAMPLE_WARN,
        structure_lookback_bars=300,
    )
    frozen_config = v2.to_dict()  # freeze before any OOS inspection

    print("\n" + "=" * 72)
    print(f"CANDLE 1 -> CANDLE 2 RESEARCH V2  mode={mode}")
    print("label=RESEARCH_COMPARISON | live engine UNCHANGED | no OOS optimization")
    print(
        f"fee={v2.trading_fee} entry_slip={v2.entry_slippage} "
        f"exit_slip={v2.exit_slippage} lookback={v2.structure_lookback_bars}"
    )
    print(f"lookback docs: {lookback_documentation()}")
    print(f"symbols={len(symbols)} timeframes={TIMEFRAMES}")
    print("=" * 72)

    all_trades: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    period_store: dict[str, list[dict[str, Any]]] = {
        "TRAINING_PERIOD": [],
        "VALIDATION_PERIOD": [],
        "OUT_OF_SAMPLE_PERIOD": [],
    }
    series_reports: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    eligible = 0
    total_candles = 0
    total_setups = 0
    total_trades = 0
    symbols_processed: set[str] = set()
    t_run = time.perf_counter()

    for tf in TIMEFRAMES:
        for sym in symbols:
            try:
                candles = await load_ohlcv_series(sym, tf)
            except Exception as exc:  # noqa: BLE001 — continue universe
                excluded.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "reason": f"load_error:{exc}",
                        "status": "INSUFFICIENT_DATA",
                    }
                )
                print(f"SKIP {sym} {tf}: load_error {exc}", flush=True)
                continue

            n = len(candles)
            total_candles += n
            if n < v2.min_bars:
                excluded.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "reason": f"n={n} < min_bars={v2.min_bars}",
                        "status": "INSUFFICIENT_DATA",
                        "candle_count": n,
                    }
                )
                print(f"SKIP {sym} {tf}: INSUFFICIENT_DATA n={n}", flush=True)
                del candles
                continue

            print(
                f"\n{sym} {tf}: loading done n={n}  "
                f"{candles[0]['time']} -> {candles[-1]['time']}",
                flush=True,
            )

            full = run_candle12_v2(
                sym,
                tf,
                candles,
                signal_config=scfg,
                v2_config=v2,
                period_label="FULL",
                progress_every=max(2000, n // 5),
                progress_callback=_progress,
            )
            instr = full.get("instrumentation") or {}
            dq = full.get("data_quality") or {}
            print(
                f"{sym} {tf}: status={full.get('status')} "
                f"sample={full.get('sample_size')} "
                f"trades={instr.get('trades_generated')} "
                f"setups_eval={instr.get('setups_evaluated')} "
                f"dq={dq.get('data_quality')} "
                f"gaps={dq.get('gap_count')} "
                f"{full.get('elapsed_seconds', 0):.2f}s",
                flush=True,
            )

            if full.get("status") == "INSUFFICIENT_DATA":
                excluded.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "reason": dq.get("reason"),
                        "status": "INSUFFICIENT_DATA",
                        "data_quality": dq,
                    }
                )
                del candles
                continue

            eligible += 1
            symbols_processed.add(sym.upper())
            total_setups += int(instr.get("setups_evaluated") or 0)
            total_trades += int(instr.get("trades_generated") or 0)

            result = full.get("result") or {}
            series_reports.append(
                {
                    "symbol": sym.upper(),
                    "timeframe": tf,
                    "split": "FULL",
                    "configuration_id": frozen_config,
                    "sample_size": full.get("sample_size", 0),
                    "metrics": _row(result),
                    "data_quality": dq,
                    "calendar_start": dq.get("calendar_start"),
                    "calendar_end": dq.get("calendar_end"),
                    "instrumentation": instr,
                }
            )

            for t in full.get("trades") or []:
                if t.get("outcome") != "OPEN":
                    all_trades.append({**t, "symbol": sym.upper(), "timeframe": tf})
            for ex in full.get("examples") or []:
                examples.append(ex)

            splits = split_period_indices(
                n,
                train_fraction=v2.train_fraction,
                validation_fraction=v2.validation_fraction,
                oos_fraction=v2.oos_fraction,
            )
            for label, (a, b) in splits.items():
                for t in full.get("trades") or []:
                    if t.get("outcome") == "OPEN":
                        continue
                    ei = int(t.get("entry_index", -1))
                    if a <= ei < b:
                        period_store[label].append(
                            {
                                **t,
                                "symbol": sym.upper(),
                                "timeframe": tf,
                                "period_label": label,
                            }
                        )

            # Release series memory before next symbol/tf
            del candles
            del full

    run_elapsed = time.perf_counter() - t_run

    overall = aggregate_v2_trades(
        all_trades, description="V2 ALL", min_sample_warning=MIN_SAMPLE_WARN
    )
    print_metrics("V2 ALL (FULL history)", overall)

    by_tf = {}
    for tf in TIMEFRAMES:
        m = aggregate_v2_trades(
            [t for t in all_trades if t["timeframe"] == tf],
            description=f"V2 {tf}",
            min_sample_warning=MIN_SAMPLE_WARN,
        )
        by_tf[tf] = _row(m)
        print_metrics(f"V2 timeframe {tf}", m)

    by_dir = {}
    for direction in ("LONG", "SHORT"):
        m = aggregate_v2_trades(
            [t for t in all_trades if t.get("direction") == direction],
            description=f"V2 {direction}",
            min_sample_warning=MIN_SAMPLE_WARN,
        )
        by_dir[direction] = _row(m)
        print_metrics(f"V2 {direction}", m)

    # Per-symbol aggregates (objective metrics only — no ranking)
    by_symbol: dict[str, Any] = {}
    for sym in sorted({t["symbol"] for t in all_trades}):
        m = aggregate_v2_trades(
            [t for t in all_trades if t["symbol"] == sym],
            description=f"V2 {sym}",
            min_sample_warning=MIN_SAMPLE_WARN,
        )
        by_symbol[sym] = _row(m)

    print("\n--- Chronological periods (evaluation; OOS not used for optimization) ---")
    period_metrics = {}
    for label, trades in period_store.items():
        m = aggregate_v2_trades(
            trades, description=label, min_sample_warning=MIN_SAMPLE_WARN
        )
        period_metrics[label] = _row(m)
        print_metrics(label, m)

    print("\n" + "=" * 72)
    print("MANUALLY INSPECTABLE EXAMPLES — Hypothesis V2")
    print("=" * 72)
    shown = examples[:25]
    verified = 0
    for i, ex in enumerate(shown, 1):
        v = ex.get("verification") or {}
        ok = bool(v.get("no_lookahead_ok"))
        if ok:
            verified += 1
        print(f"\n--- Example {i} ---")
        print(f"  symbol / tf           : {ex.get('symbol')} / {ex.get('timeframe')}")
        print(f"  C1 / C2 idx           : {ex.get('candle1_index')} / {ex.get('candle2_index')}")
        print(
            f"  setup/confirm/entry   : {ex.get('setup_candle_index')} / "
            f"{ex.get('confirmation_candle_index')} / {ex.get('entry_candle_index')}"
        )
        print(f"  entry                 : {ex.get('entry_price')}")
        print(f"  outcome / net_R       : {ex.get('outcome')} / {ex.get('net_R')}")
        print(f"  outcome_resolution    : {ex.get('outcome_resolution')}")
        print(f"  no_lookahead_ok       : {ok}")
        assert ex.get("candle2_index") == (ex.get("candle1_index") or 0) + 1
        assert v.get("entry_is_not_c2_close") is True

    summary_txt_lines = [
        "Candle-1/Candle-2 Research V2 Summary",
        f"mode={mode}",
        f"created_at={datetime.now(timezone.utc).isoformat()}",
        f"symbols_processed={len(symbols_processed)}",
        f"series_eligible={eligible}",
        f"series_excluded={len(excluded)}",
        f"total_candles={total_candles}",
        f"total_setups_evaluated={total_setups}",
        f"total_trades={total_trades}",
        f"runtime_seconds={run_elapsed:.2f}",
        f"lookback={v2.structure_lookback_bars}",
        "live_signal_engine_unchanged=true",
        "oos_optimization=false",
        f"train_sample={period_metrics.get('TRAINING_PERIOD', {}).get('sample_size')}",
        f"validation_sample={period_metrics.get('VALIDATION_PERIOD', {}).get('sample_size')}",
        f"oos_sample={period_metrics.get('OUT_OF_SAMPLE_PERIOD', {}).get('sample_size')}",
    ]

    out = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": f"candle12_v2_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "hypothesis": HYPOTHESIS_V2,
        "label": "RESEARCH_COMPARISON",
        "mode": mode,
        "disclaimer": (
            "Candle-1/Candle-2 research V2. Not a ranking. Not a profitability claim. "
            "Live engine unchanged. OOS evaluation-only. No parameter optimization."
        ),
        "configuration_id": frozen_config,
        "v2_config": frozen_config,
        "lookback_documentation": lookback_documentation(),
        "universe": {
            "requested_symbols": symbols,
            "symbols_processed": sorted(symbols_processed),
            "timeframes": TIMEFRAMES,
            "eligible_series": eligible,
            "excluded_series": excluded,
            "excluded_count": len(excluded),
        },
        "totals": {
            "symbols_processed": len(symbols_processed),
            "series_processed": eligible,
            "candles_processed": total_candles,
            "setups_evaluated": total_setups,
            "trades_generated": total_trades,
            "runtime_seconds": run_elapsed,
        },
        "overall": _row(overall),
        "by_timeframe": by_tf,
        "by_symbol": by_symbol,
        "by_direction": by_dir,
        "by_split": period_metrics,
        "series": series_reports,
        "examples": shown,
        "examples_no_lookahead_verified": verified,
        "confirmations": {
            "live_signal_logic_unchanged": True,
            "production_thresholds_unchanged": True,
            "oos_optimization": False,
            "postgres_source": True,
            "memory_500_bar_bypassed": True,
            "no_on2_full_history_scan": True,
            "no_fabricated_data": True,
        },
    }

    tag = "benchmark" if mode == "benchmark" else "full"
    json_path = ROOT / "scripts" / f"candle12_v2_research_result_{tag}.json"
    txt_path = ROOT / "scripts" / f"candle12_v2_research_summary_{tag}.txt"
    json_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    txt_path.write_text("\n".join(summary_txt_lines) + "\n", encoding="utf-8")
    # Also keep untagged path for compatibility
    if mode == "full":
        (ROOT / "scripts" / "candle12_v2_research_result.json").write_text(
            json.dumps(out, indent=2, default=str), encoding="utf-8"
        )
    print(f"\nWrote {json_path}")
    print(f"Wrote {txt_path}")
    print("\n".join(summary_txt_lines))
    return out


async def _amain(mode: str) -> None:
    try:
        await run_research(mode)
    finally:
        try:
            await db_manager.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Candle-12 research V2 runner")
    parser.add_argument(
        "--mode",
        choices=("benchmark", "full"),
        default="benchmark",
        help="benchmark=BTC/ETH/SOL x 5m/15m/1h; full=all eligible symbols",
    )
    args = parser.parse_args()
    asyncio.run(_amain(args.mode))


if __name__ == "__main__":
    main()
