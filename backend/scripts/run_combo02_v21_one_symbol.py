#!/usr/bin/env python3
"""Run one COMBO_02 V2.1 symbol backtest and checkpoint JSON (memory-friendly)."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json

from scripts.run_combo02_v21_variant_compare import (
    DATA_END,
    DATA_START,
    OUT_DIR,
    _install_validation_15m_speed_patch,
    _install_validation_context_patch,
    _install_validation_htf_speed_patch,
    _install_validation_ohlcv_cache_patch,
    _run_combo,
)


async def _prewarm_1h_feature_cache(symbol: str) -> None:
    """Build/cache 1h feature table before 15m series is loaded (peak-RAM)."""
    from app.research.combo02_v2.context import Combo02V2Context
    from app.research.service import _load_research_candles

    c1h, _, _ = await _load_research_candles(
        symbol,
        "1h",
        start_date=DATA_START,
        end_date=DATA_END,
        use_research_cache=True,
        warmup_bars=100,
    )
    print(f"prewarm: building/caching 1h table with only 1h loaded ({len(c1h)} bars)...", flush=True)
    Combo02V2Context.build(
        symbol=symbol,
        setup_timeframe="1h",
        setup_candles=c1h,
        candles_15m=None,
    )
    del c1h
    gc.collect()
    print("prewarm: 1h cache ready; loading remaining series for backtest...", flush=True)


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--combo-id", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--symbol", required=True)
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{args.label}_{args.symbol}.json"
    if out_path.exists():
        print(f"skip existing {out_path}", flush=True)
        return 0

    from app.config import get_settings
    from app.services.database import db_manager

    await db_manager.connect(get_settings())
    _install_validation_context_patch()
    _install_validation_15m_speed_patch()
    _install_validation_htf_speed_patch()
    _install_validation_ohlcv_cache_patch()

    await _prewarm_1h_feature_cache(args.symbol)

    one = await _run_combo(args.combo_id, args.symbol)
    out_path.write_text(json.dumps(one, indent=2, default=str), encoding="utf-8")
    print(
        f"wrote {out_path} trades={one['metrics']['trades']} "
        f"total_R={one['metrics']['total_R']:.3f} elapsed={one['elapsed_seconds']}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
