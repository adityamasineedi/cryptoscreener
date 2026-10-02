"""Run Candle-1/Candle-2 research hypothesis vs existing pullback/retest entry.

Uses real persisted OHLCV from Postgres only. Does not modify live engines.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.candle12_hypothesis import (
    HYPOTHESIS_A,
    HYPOTHESIS_B,
    aggregate_trade_metrics,
    run_hypothesis_a,
    run_hypothesis_b,
)
from app.research.config import ResearchConfig
from app.research.schemas import ResearchTrade
from app.services.database import db_manager
from app.services.ohlcv_store import ohlcv_store
from app.signals.config import SignalConfig

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
TIMEFRAMES = ["5m", "15m", "1h"]
BTC_ETH = {"BTCUSDT", "ETHUSDT"}


def _metric_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_size": result.get("sample_size", 0),
        "total_setups": result.get("sample_size", 0),
        "long_setups": result.get("long_setups", 0),
        "short_setups": result.get("short_setups", 0),
        "tp1_hit_pct": result.get("tp1_hit_rate"),
        "tp2_hit_pct": result.get("tp2_hit_rate"),
        "tp3_hit_pct": result.get("tp3_hit_rate"),
        "sl_hit_pct": result.get("sl_rate"),
        "average_R": result.get("average_R"),
        "median_R": result.get("median_R"),
        "expectancy_R": result.get("expectancy_R"),
        "profit_factor": result.get("profit_factor"),
        "max_drawdown_R": result.get("max_drawdown_R"),
        "average_MAE_R": result.get("average_MAE_R"),
        "average_MFE_R": result.get("average_MFE_R"),
        "average_holding_time": result.get("average_holding_time"),
        "ambiguous_trades": result.get("ambiguous_count", result.get("ambiguous_trades")),
    }


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _num(v: Any, d: int = 3) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
        if x == float("inf"):
            return "inf"
        return f"{x:.{d}f}"
    except (TypeError, ValueError):
        return "—"


def print_block(title: str, row: dict[str, Any]) -> None:
    print(f"\n=== {title} ===")
    print(f"  sample_size / total_setups : {row['sample_size']}")
    print(f"  LONG setups                : {row['long_setups']}")
    print(f"  SHORT setups               : {row['short_setups']}")
    print(f"  TP1 hit %                  : {_pct(row['tp1_hit_pct'])}")
    print(f"  TP2 hit %                  : {_pct(row['tp2_hit_pct'])}")
    print(f"  TP3 hit %                  : {_pct(row['tp3_hit_pct'])}")
    print(f"  SL hit %                   : {_pct(row['sl_hit_pct'])}")
    print(f"  average R                  : {_num(row['average_R'])}")
    print(f"  median R                   : {_num(row['median_R'])}")
    print(f"  expectancy R               : {_num(row['expectancy_R'])}")
    print(f"  profit factor              : {_num(row['profit_factor'])}")
    print(f"  max drawdown R             : {_num(row['max_drawdown_R'])}")
    print(f"  average MAE_R              : {_num(row['average_MAE_R'])}")
    print(f"  average MFE_R              : {_num(row['average_MFE_R'])}")
    print(f"  average holding time       : {_num(row['average_holding_time'], 2)} bars")
    print(f"  ambiguous trades           : {row['ambiguous_trades']}")


async def load_candles() -> dict[tuple[str, str], list[dict[str, Any]]]:
    settings = get_settings()
    await db_manager.connect(settings)
    if not db_manager.enabled:
        raise RuntimeError("DATABASE not available — cannot load persisted OHLCV")
    loaded = await ohlcv_store.load_from_db(
        symbols=SYMBOLS,
        timeframes=TIMEFRAMES,
        limit_per_series=5000,
    )
    print(f"Hydrated {loaded} candles from Postgres")
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            raw = ohlcv_store.get_candles_for_engine(sym, tf, include_open=False)
            candles = list(raw) if raw else []
            out[(sym, tf)] = candles
            if candles:
                t0 = candles[0].get("time") or candles[0].get("timestamp")
                t1 = candles[-1].get("time") or candles[-1].get("timestamp")
                print(f"  {sym} {tf}: n={len(candles)}  {t0} -> {t1}")
            else:
                print(f"  {sym} {tf}: INSUFFICIENT_DATA (0 candles)")
    return out


def main() -> None:
    series = asyncio.run(load_candles())
    scfg = SignalConfig()
    # Persist coverage is ~200 bars; keep min_bars modest but honest
    rcfg = ResearchConfig(min_bars=40, min_coverage_ratio=0.80)

    a_trades: list[dict[str, Any]] = []
    b_trades: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}

    print("\n" + "=" * 72)
    print("RESEARCH COMPARISON — CANDLE_1_BOS→CANDLE_2_BREAK vs EXISTING_PULLBACK_RETEST")
    print("Label: RESEARCH_COMPARISON — not a ranking, not a profitability claim")
    print("Live signal engine / thresholds: UNCHANGED")
    print("=" * 72)

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            candles = series.get((sym, tf)) or []
            coverage[f"{sym}:{tf}"] = {
                "n": len(candles),
                "status": "OK" if len(candles) >= rcfg.min_bars else "INSUFFICIENT_DATA",
            }
            if len(candles) < rcfg.min_bars:
                print(f"SKIP {sym} {tf}: n={len(candles)} < min_bars={rcfg.min_bars}")
                continue

            a = run_hypothesis_a(
                sym, tf, candles, signal_config=scfg, research_config=rcfg
            )
            b = run_hypothesis_b(
                sym, tf, candles, signal_config=scfg, research_config=rcfg
            )
            for t in a.get("trades") or []:
                if t.get("outcome") != "OPEN":
                    a_trades.append({**t, "symbol": sym, "timeframe": tf})
            for t in b.get("trades") or []:
                if t.get("outcome") != "OPEN":
                    b_trades.append({**t, "symbol": sym, "timeframe": tf})
            for ex in a.get("examples") or []:
                examples.append(ex)
            print(
                f"{sym} {tf}: A sample={a.get('result', {}).get('sample_size')} "
                f"B sample={b.get('result', {}).get('sample_size')} "
                f"A_elapsed={a.get('elapsed_seconds', 0):.2f}s "
                f"B_elapsed={b.get('elapsed_seconds', 0):.2f}s"
            )

    a_all = aggregate_trade_metrics(
        a_trades,
        combination_id=HYPOTHESIS_A,
        description="Candle-1 BOS → Candle-2 break",
    )
    b_all = aggregate_trade_metrics(
        b_trades,
        combination_id=HYPOTHESIS_B,
        description="Existing pullback/retest entry",
    )
    print_block("A) CANDLE_1_BOS → CANDLE_2_BREAK  (ALL)", _metric_row(a_all))
    print_block("B) EXISTING_PULLBACK_RETEST_ENTRY  (ALL)", _metric_row(b_all))

    def subset(trades: list[dict[str, Any]], pred) -> list[dict[str, Any]]:
        return [t for t in trades if pred(t)]

    breakdowns = [
        ("A BTC/ETH", a_trades, lambda t: t["symbol"] in BTC_ETH, HYPOTHESIS_A),
        ("B BTC/ETH", b_trades, lambda t: t["symbol"] in BTC_ETH, HYPOTHESIS_B),
        ("A other symbols", a_trades, lambda t: t["symbol"] not in BTC_ETH, HYPOTHESIS_A),
        ("B other symbols", b_trades, lambda t: t["symbol"] not in BTC_ETH, HYPOTHESIS_B),
        ("A 5m", a_trades, lambda t: t["timeframe"] == "5m", HYPOTHESIS_A),
        ("B 5m", b_trades, lambda t: t["timeframe"] == "5m", HYPOTHESIS_B),
        ("A 15m", a_trades, lambda t: t["timeframe"] == "15m", HYPOTHESIS_A),
        ("B 15m", b_trades, lambda t: t["timeframe"] == "15m", HYPOTHESIS_B),
        ("A 1h", a_trades, lambda t: t["timeframe"] == "1h", HYPOTHESIS_A),
        ("B 1h", b_trades, lambda t: t["timeframe"] == "1h", HYPOTHESIS_B),
        ("A LONG", a_trades, lambda t: t["direction"] == "LONG", HYPOTHESIS_A),
        ("B LONG", b_trades, lambda t: t["direction"] == "LONG", HYPOTHESIS_B),
        ("A SHORT", a_trades, lambda t: t["direction"] == "SHORT", HYPOTHESIS_A),
        ("B SHORT", b_trades, lambda t: t["direction"] == "SHORT", HYPOTHESIS_B),
    ]
    for title, trades, pred, hid in breakdowns:
        m = aggregate_trade_metrics(
            subset(trades, pred), combination_id=hid, description=title
        )
        print_block(title, _metric_row(m))

    # 10 historical examples with manual verification fields
    print("\n" + "=" * 72)
    print("10 HISTORICAL EXAMPLES — Hypothesis A (Candle-1 → Candle-2)")
    print("Verify: Candle 2 index = Candle 1 index + 1; setup as_of = Candle 1 only")
    print("=" * 72)
    shown = examples[:10]
    if not shown:
        print("No Hypothesis A examples found in the available persisted window.")
    for i, ex in enumerate(shown, 1):
        v = ex.get("verification") or {}
        print(f"\n--- Example {i} ---")
        print(f"  symbol              : {ex.get('symbol')}")
        print(f"  timeframe           : {ex.get('timeframe')}")
        print(f"  Candle 1 timestamp  : {ex.get('candle1_timestamp')}")
        print(f"  Candle 1 OHLC       : {ex.get('candle1_ohlc')}")
        print(f"  BOS level           : {ex.get('bos_level')}")
        print(f"  Candle 2 timestamp  : {ex.get('candle2_timestamp')}")
        print(f"  Candle 2 OHLC       : {ex.get('candle2_ohlc')}")
        print(f"  entry               : {ex.get('entry')}")
        print(f"  SL                  : {ex.get('stop')}")
        print(f"  TP1                 : {ex.get('tp1')}")
        print(f"  TP2                 : {ex.get('tp2')}")
        print(f"  R:R                 : {ex.get('rr')}")
        print(f"  direction           : {ex.get('direction')}")
        print(f"  outcome             : {ex.get('outcome')}")
        print(f"  r_multiple          : {ex.get('r_multiple')}")
        print(f"  C1 index / C2 index : {ex.get('candle1_index')} / {ex.get('candle2_index')}")
        print(f"  entry_not_before_C2 : {v.get('entry_not_before_candle2')}")
        print(f"  C1_closed_before_C2 : {v.get('candle1_closed_before_candle2')}")
        print(f"  as_of_for_setup     : {v.get('as_of_for_setup')}")
        print(f"  break_rule          : {v.get('break_rule')}")
        # Manual invariant checks
        assert ex.get("candle2_index") == ex.get("candle1_index") + 1
        assert v.get("as_of_for_setup") == ex.get("candle1_index")
        assert v.get("entry_not_before_candle2") is True

    out_path = ROOT / "scripts" / "candle12_research_result.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": "RESEARCH_COMPARISON",
        "disclaimer": (
            "Single research test of Candle-1/Candle-2 hypothesis. "
            "Not a ranking. Not a profitability claim. Live engine unchanged."
        ),
        "coverage": coverage,
        "A_all": _metric_row(a_all),
        "B_all": _metric_row(b_all),
        "examples": shown,
        "a_trade_count": len(a_trades),
        "b_trade_count": len(b_trades),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")
    asyncio.run(db_manager.close())


if __name__ == "__main__":
    main()
