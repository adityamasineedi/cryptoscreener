#!/usr/bin/env python3
"""COMBO_02 BTCUSDT golden baseline for performance follow-up (analytics off)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path


def _trade_digest(trades: list[dict]) -> list[dict]:
    out = []
    for t in trades:
        out.append(
            {
                "trade_id": t.get("trade_id") or t.get("id"),
                "entry_ts": t.get("entry_time") or t.get("entry_ts") or t.get("opened_at"),
                "exit_ts": t.get("exit_time") or t.get("exit_ts") or t.get("closed_at"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "sl": t.get("stop_loss") or t.get("sl") or t.get("stop_price"),
                "tp": t.get("take_profit") or t.get("tp") or t.get("target_price"),
                "exit_reason": t.get("exit_reason") or t.get("reason"),
                "gross_pnl": t.get("gross_pnl") or t.get("pnl_gross") or t.get("gross_pnl_usd"),
                "fees": t.get("fees") or t.get("fees_usd"),
                "net_pnl": t.get("net_pnl") or t.get("pnl_net") or t.get("net_pnl_usd"),
                "r_multiple": t.get("r_multiple") or t.get("r") or t.get("r_net"),
            }
        )
    return out


async def main() -> None:
    from app.config import get_settings
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.config import ResearchConfig
    from app.research.service import _load_research_candles
    from app.services.database import db_manager

    settings = get_settings()
    await db_manager.connect(settings)

    combo = get_combination("COMBO_02")
    assert combo is not None

    start, end = "2025-07-01", "2026-09-30"
    symbol = "BTCUSDT"

    t0 = time.perf_counter()
    c1h, _, meta1 = await _load_research_candles(
        symbol, "1h", start_date=start, end_date=end, use_research_cache=True
    )
    c4h, _, meta4 = await _load_research_candles(
        symbol, "4h", start_date=start, end_date=end, use_research_cache=True
    )
    load_s = time.perf_counter() - t0

    # dataset fingerprint from candle open times + closes
    h = hashlib.sha256()
    for c in c1h:
        h.update(str(c.get("open_time") or c.get("time")).encode())
        h.update(str(c.get("close")).encode())
    dataset_fp = h.hexdigest()[:32]

    cfg = ResearchConfig()
    t1 = time.perf_counter()
    out = run_combination_backtest(
        symbol=symbol,
        timeframe="1h",
        candles=c1h,
        combination=combo,
        research_config=cfg,
        candles_4h=c4h,
    )
    bt_s = time.perf_counter() - t1

    trades = list(out.get("trades") or [])
    result = out.get("result") or {}
    digest = _trade_digest(trades)

    payload = {
        "label": "COMBO_02_BTCUSDT_15m_window_baseline",
        "symbol": symbol,
        "timeframe": "1h",
        "start": start,
        "end": end,
        "bars_1h": len(c1h),
        "bars_4h": len(c4h),
        "src_1h": meta1.get("source"),
        "src_4h": meta4.get("source"),
        "dataset_fingerprint": dataset_fp,
        "configuration_fingerprint": (
            result.get("configuration_fingerprint")
            or out.get("configuration_fingerprint")
            or getattr(combo, "fingerprint", None)
            or str(getattr(combo, "id", "COMBO_02"))
        ),
        "trade_count": len(trades),
        "trades": digest,
        "equity_curve": result.get("equity_curve") or out.get("equity_curve"),
        "max_drawdown": result.get("max_drawdown") or result.get("max_dd"),
        "final_balance": result.get("final_balance") or result.get("ending_equity"),
        "metrics": result,
        "load_s": round(load_s, 3),
        "runtime_s": round(bt_s, 3),
        "analytics": "off",
    }

    out_path = Path("reports/full_system_performance_followup/backtest_golden_equality.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Store baseline under nested key; optimized fill later
    wrapper = {"baseline": payload, "optimized": None, "equality": None}
    out_path.write_text(json.dumps(wrapper, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in (
        "bars_1h", "trade_count", "dataset_fingerprint", "configuration_fingerprint",
        "load_s", "runtime_s", "max_drawdown", "final_balance"
    )}, indent=2))
    print("wrote", out_path)
    await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
