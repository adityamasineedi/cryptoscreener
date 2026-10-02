"""Run research-only trend/regime diagnostic on COMBO_02 trades.

Does not modify live signals or production thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.research.config import ResearchConfig
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.research.trend_regime_diagnostic import (
    build_report_payload,
    collect_combo02_trades,
    render_markdown,
    run_diagnostics_on_trades,
)
from app.services.database import db_manager
from app.signals.config import SignalConfig

OUT_JSON = Path(__file__).resolve().parent / "trend_regime_diagnostic.json"
OUT_ROWS = Path(__file__).resolve().parent / "trend_regime_trade_rows.json"
OUT_MD = Path(__file__).resolve().parent / "trend_regime_diagnostic.md"


async def main() -> None:
    p = argparse.ArgumentParser(description="COMBO_02 trend/regime diagnostic (research)")
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--tfs", default="15m,1h")
    p.add_argument(
        "--limit",
        type=int,
        default=3600,
        help="Setup bars per symbol/TF (default 3600 ≈ 37d on 15m / 150d on 1h)",
    )
    p.add_argument(
        "--htf-limit",
        type=int,
        default=5000,
        help="HTF 1h/4h bars loaded for regime labels",
    )
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    limit = max(120, int(args.limit))
    htf_limit = max(limit, int(args.htf_limit))

    def log(msg: str) -> None:
        print(msg, flush=True)

    await db_manager.connect(get_settings())
    scfg = SignalConfig()
    rcfg = ResearchConfig()
    bundles: list[dict] = []
    try:
        h1_cache: dict[str, list] = {}
        h4_cache: dict[str, list] = {}
        for sym in symbols:
            log(f"loading HTF {sym}…")
            h1_cache[sym] = await load_ohlcv_series_tail(sym, "1h", limit=htf_limit)
            h4_cache[sym] = await load_ohlcv_series_tail(sym, "4h", limit=htf_limit)
            log(f"{sym} HTF loaded 1h={len(h1_cache[sym])} 4h={len(h4_cache[sym])}")

        for tf in tfs:
            for sym in symbols:
                log(f"loading {sym} {tf}…")
                candles = await load_ohlcv_series_tail(sym, tf, limit=limit)
                log(f"{sym} {tf} setup bars={len(candles)}")
                if len(candles) < 120:
                    log("  skip shallow")
                    continue
                trades: list[dict] = []
                for direction in ("LONG", "SHORT"):
                    log(f"  backtest {direction}…")
                    tlist = collect_combo02_trades(
                        symbol=sym,
                        timeframe=tf,
                        candles=candles,
                        signal_config=scfg,
                        research_config=rcfg,
                        direction=direction,
                    )
                    closed = [t for t in tlist if t.get("outcome") not in (None, "OPEN")]
                    log(f"  {direction} closed={len(closed)}")
                    trades.extend(closed)
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

    log(f"diagnosing {sum(len(b['trades']) for b in bundles)} trades…")
    rows = run_diagnostics_on_trades(bundles, signal_config=scfg)
    payload = build_report_payload(rows)
    payload["run_params"] = {
        "symbols": symbols,
        "tfs": tfs,
        "limit": limit,
        "htf_limit": htf_limit,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    OUT_ROWS.write_text(
        json.dumps([r.to_dict() for r in rows], indent=2, default=str),
        encoding="utf-8",
    )
    OUT_MD.write_text(render_markdown(payload, rows), encoding="utf-8")
    log(f"WROTE {OUT_JSON}")
    log(f"WROTE {OUT_ROWS}")
    log(f"WROTE {OUT_MD}")
    log(f"n_trades {payload['n_trades']} regime_counts {payload['regime_counts']}")


if __name__ == "__main__":
    asyncio.run(main())
