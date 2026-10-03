"""Run research-only failure-regime decomposition on full PostgreSQL OHLCV.

Uses existing COMBO_02 Path A backtest. Does not modify live engines or thresholds.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.config import ResearchConfig
from app.research.failure_regime_decomposition import (
    build_report,
    diagnose_bundle_trades,
    write_outputs,
)
from app.research.postgres_ohlcv import load_ohlcv_series
from app.research.trend_regime_diagnostic import collect_combo02_trades
from app.services.database import db_manager
from app.signals.config import SignalConfig

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TFS = ["15m", "1h"]  # 5m reported as NO DATA if not run
# LONG-only cut: SHORT skipped for runtime. Documented in meta / report.
DIRECTIONS = ("LONG",)
OUT_JSON = Path(__file__).resolve().parent / "failure_regime_decomposition.json"
OUT_MD = Path(__file__).resolve().parent / "failure_regime_decomposition.md"
OUT_ROWS = Path(__file__).resolve().parent / "failure_regime_trade_rows.json"


async def main() -> None:
    def log(msg: str) -> None:
        print(msg, flush=True)

    await db_manager.connect(get_settings())
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    bundles: list[dict] = []
    bars_loaded: dict[str, int] = {}
    t_all = time.perf_counter()
    try:
        h1_cache: dict[str, list] = {}
        h4_cache: dict[str, list] = {}
        for sym in SYMBOLS:
            log(f"loading full HTF history {sym}...")
            h1_cache[sym] = await load_ohlcv_series(sym, "1h")
            h4_cache[sym] = await load_ohlcv_series(sym, "4h")
            bars_loaded[f"{sym}:1h"] = len(h1_cache[sym])
            bars_loaded[f"{sym}:4h"] = len(h4_cache[sym])
            log(
                f"  {sym} 1h={len(h1_cache[sym])} 4h={len(h4_cache[sym])}"
            )

        for tf in TFS:
            for sym in SYMBOLS:
                log(f"loading full setup series {sym} {tf}...")
                candles = await load_ohlcv_series(sym, tf)
                bars_loaded[f"{sym}:{tf}"] = len(candles)
                log(f"  bars={len(candles)}")
                if len(candles) < 120:
                    log("  skip shallow")
                    continue
                trades: list[dict] = []
                for direction in DIRECTIONS:
                    log(f"  COMBO_02 backtest {direction}...")
                    t0 = time.perf_counter()
                    tlist = collect_combo02_trades(
                        symbol=sym,
                        timeframe=tf,
                        candles=candles,
                        signal_config=scfg,
                        research_config=rcfg,
                        direction=direction,
                        candles_1h=h1_cache[sym],
                        candles_4h=h4_cache[sym],
                    )
                    closed = [
                        t for t in tlist if t.get("outcome") not in (None, "OPEN")
                    ]
                    log(
                        f"  {direction} closed={len(closed)} "
                        f"elapsed={time.perf_counter() - t0:.1f}s"
                    )
                    trades.extend(closed)
                if "SHORT" not in DIRECTIONS:
                    log("  SHORT skipped (this run is LONG-only)")
                bundles.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "setup_candles": candles,
                        "h1_candles": h1_cache[sym],
                        "h4_candles": h4_cache[sym],
                        "trades": trades,
                    }
                )
    finally:
        await db_manager.close()

    n_raw = sum(len(b["trades"]) for b in bundles)
    log(f"diagnosing {n_raw} closed trades...")
    rows = diagnose_bundle_trades(bundles, signal_config=scfg)
    report = build_report(
        rows,
        bars_loaded=bars_loaded,
        meta_extra={
            "ohlcv_mode": "full_series_load_ohlcv_series",
            "directions_run": list(DIRECTIONS),
            "short_status": (
                "SKIPPED_THIS_RUN"
                if "SHORT" not in DIRECTIONS
                else "INCLUDED"
            ),
            "elapsed_seconds_total": round(time.perf_counter() - t_all, 1),
        },
    )
    write_outputs(report, out_json=OUT_JSON, out_md=OUT_MD)
    OUT_ROWS.write_text(
        json.dumps([r.to_dict() for r in rows], indent=2, default=str),
        encoding="utf-8",
    )
    log(f"WROTE {OUT_JSON}")
    log(f"WROTE {OUT_MD}")
    log(f"WROTE {OUT_ROWS}")
    log(f"n_trades={report['dataset']['n_trades']}")
    log(f"regime_counts={report['dataset']['regime_counts']}")
    log(f"LONG={report['by_direction']['LONG']['overall']}")
    log(f"SHORT={report['by_direction']['SHORT']['overall']}")


if __name__ == "__main__":
    asyncio.run(main())
