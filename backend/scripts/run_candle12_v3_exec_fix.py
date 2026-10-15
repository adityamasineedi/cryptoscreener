"""CANDLE12_V3_EXEC_FIX vs the frozen CANDLE12_V2, BTCUSDT 1h. Read-only database.

Every window this script reads ends on or before 2026-10-15 and has been seen
before: results are IN-SAMPLE. Nothing here tunes a parameter.

Usage: python scripts/run_candle12_v3_exec_fix.py [--out results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "strategy_rank" / "multicap"))

from _db import _connect, load_candles  # noqa: E402
from app.research.candle12_v3 import Candle12V3Config, check_oos_guard, run_candle12_v3  # noqa: E402
from app.research.engine_backtest import run_engine_cell  # noqa: E402

UTC = timezone.utc
SYMBOL = "BTCUSDT"
START = datetime(2023, 1, 1, tzinfo=UTC)
END_EXCLUSIVE = datetime(2026, 10, 16, tzinfo=UTC)  # last bar 2026-10-15
WARMUP = 1000
RISK, EQUITY, LEVERAGE, TAKER = 20.0, 1000.0, 2.0, 0.0004
LABEL = "IN-SAMPLE (2023-01-01 to 2026-10-15; seen before)"


def _candles() -> tuple[list[dict[str, Any]], int]:
    raw = load_candles(SYMBOL, "1h", START - timedelta(hours=WARMUP + 600), END_EXCLUSIVE)
    check_oos_guard(raw)
    first = next(i for i, b in enumerate(raw) if b["time"] >= START)
    return raw[first - WARMUP:], WARMUP


async def _funding_rows() -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        rows = await conn.fetch(
            "SELECT funding_time, funding_rate, mark_price FROM grid_funding_settlements "
            "WHERE symbol = $1 AND funding_time >= $2 AND funding_time < $3 ORDER BY funding_time",
            SYMBOL, START, END_EXCLUSIVE,
        )
    except Exception:  # noqa: BLE001 - table missing means no funding history
        return []
    finally:
        await conn.close()
    return [{"time": r[0], "rate": float(r[1]), "mark_price": r[2]} for r in rows]


def _stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    trades = sorted(trades, key=lambda t: (str(t["exit_time"]), str(t["entry_time"])))
    nets = [float(t["net_pnl_usd"]) for t in trades]
    eq = peak = EQUITY
    dd = 0.0
    for p in nets:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    wins = sum(p for p in nets if p > 0)
    losses = -sum(p for p in nets if p < 0)
    n = len(trades)
    r_act = [float(t.get("r_net_actual_risk", t.get("r_net"))) for t in trades]
    return {
        "trades": n,
        "net_pnl_usd": round(sum(nets), 2),
        "win_rate": round(sum(p > 0 for p in nets) / n, 4) if n else None,
        "avg_r_net_actual_risk": round(sum(r_act) / n, 4) if n else None,
        "avg_r_net_intended_risk": round(sum(nets) / n / RISK, 4) if n else None,
        "profit_factor": round(wins / losses, 3) if losses else None,
        "max_drawdown_usd": round(dd, 2),
        "fees_usd": round(-sum(float(t.get("fee_total_usd") or 0) for t in trades), 2),
        "funding_usd": round(sum(float(t.get("funding_usd") or 0) for t in trades), 2),
        "capped": sum(bool(t.get("capped_by_leverage")) for t in trades),
    }


def _v3(candles, first, *, funding=None, **kw) -> dict[str, Any]:
    cfg = Candle12V3Config(risk_usd=RISK, account_equity=EQUITY, leverage=LEVERAGE, taker_fee=TAKER, **kw)
    res = run_candle12_v3(SYMBOL, "1h", candles, config=cfg, index_start=first, funding=funding)
    closed = [t for t in res["trades"] if t["outcome"] != "OPEN"]
    out = {"config": {k: kw[k] for k in sorted(kw)}, **_stats(closed), **{
        k: res["counts"][k] for k in (
            "gap_entry_fills", "gap_stop_exits", "gap_target_exits", "size_rejected",
            "min_notional_rejected", "risk_tolerance_rejected",
            "refire_suppressed", "blocked_by_opposite", "trades_open_at_end",
        )
    }}
    out["rejections"] = [
        {
            "entry_time": r["entry_time"], "direction": r["direction"], "outcome": r["outcome"],
            "reasons": r["reject_reasons"], "intended_risk_usd": r["intended_risk_usd"],
            "actual_risk_usd": round(r["actual_risk_usd"], 4), "risk_diff_usd": round(r["risk_diff_usd"], 4),
            "risk_diff_pct": round(r["risk_diff_pct"], 4), "qty": r["qty"],
            "notional_usd": round(r["notional_usd"], 2), "capped_by_leverage": r["capped_by_leverage"],
        }
        for r in res["rejected"]
    ]
    out["funding"] = res["funding"]
    for side in ("LONG", "SHORT"):
        part = [t for t in closed if t["direction"] == side]
        if part and kw.get("book_side") == "BOTH":
            out[side] = _stats(part)
    out["_trades"] = closed
    return out


def _v2_baseline(candles, first) -> dict[str, Any]:
    cell = {"strategy_id": "CANDLE12_V2", "symbol": SYMBOL, "timeframe": "1h", "candles": candles,
            "index_start": first, "market_cap": None, "risk_usd": RISK, "taker_fee": TAKER,
            "maker_fee": 0.0002, "leverage": LEVERAGE, "principal": EQUITY}
    longs = run_engine_cell({**cell, "direction": "LONG"})["trades"]
    shorts = run_engine_cell({**cell, "direction": "SHORT"})["trades"]
    gap_entry = sum(
        1 for t in longs if candles[t["entry_index"]]["open"] >= t["entry_price"]
    )
    gap_stop = sum(
        1 for t in longs
        if t["outcome"] == "SL" and candles[t["exit_index"]]["open"] < t["stop_price"]
        and t["exit_index"] != t["entry_index"]
    )
    # V2's hidden-short lock: the same setups walked LONG-only (V3 every_close, no qty
    # rounding, per-side lock reproduces V2 exactly). A LONG missing from the
    # shared walk whose Candle-1 sat inside an open SHORT was blocked by it.
    solo = _v3(candles, first, book_side="LONG", bos_refire_policy="every_close",
               apply_exchange_filters=False, risk_tolerance_pct=None)["_trades"]
    have = {t["entry_index"] for t in longs}
    blocked = sum(
        1 for t in solo
        if t["entry_index"] not in have
        and any(s["entry_index"] <= t["candle1_index"] <= s["exit_index"] for s in shorts)
    )
    return {**_stats(longs), "gap_entry_fills": gap_entry, "gap_stop_exits": gap_stop,
            "blocked_by_opposite": blocked, "_trades": longs, "_shorts": shorts}


def _parity(candles, first, v2_long, v2_short) -> dict[str, Any]:
    """V3 with V2's rules (shared lock, every_close, no rounding) against V2."""
    r = _v3(candles, first, book_side="BOTH", portfolio_lock_mode="shared",
            bos_refire_policy="every_close", apply_exchange_filters=False, risk_tolerance_pct=None)
    a = {t["entry_index"]: t for t in v2_long + v2_short}
    b = {t["entry_index"]: t for t in r["_trades"]}
    diff = [
        k for k in set(a) | set(b)
        if k not in a or k not in b
        or a[k]["outcome"] != b[k]["outcome"] or a[k]["exit_index"] != b[k]["exit_index"]
        or abs(a[k]["net_pnl_usd"] - b[k]["net_pnl_usd"]) > 1e-6
    ]
    return {"v2_trades": len(a), "v3_trades": len(b), "mismatches": len(diff)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    candles, first = _candles()
    base = dict(book_side="LONG", portfolio_lock_mode="per_side")
    runs: dict[str, Any] = {}
    v2 = _v2_baseline(candles, first)
    runs["V2_frozen_LONG_shared_lock"] = v2
    runs["parity_v3_as_v2"] = _parity(candles, first, v2["_trades"], v2["_shorts"])
    runs["1_long_once_002"] = _v3(candles, first, **base, bos_refire_policy="once_per_level")
    runs["2_long_every_002"] = _v3(candles, first, **base, bos_refire_policy="every_close")
    runs["3_long_once_005"] = _v3(candles, first, **base, bos_refire_policy="once_per_level",
                                  entry_slippage=0.0005, exit_slippage=0.0005)
    runs["4_short_once_002"] = _v3(candles, first, book_side="SHORT", bos_refire_policy="once_per_level")
    runs["5_combined_shared_once_002"] = _v3(candles, first, book_side="BOTH", portfolio_lock_mode="shared",
                                             bos_refire_policy="once_per_level")
    runs["x_long_once_tp1_runner"] = _v3(candles, first, **base, bos_refire_policy="once_per_level",
                                         target_policy="tp1_runner")
    runs["x_long_once_risk_tolerance_off"] = _v3(candles, first, **base, bos_refire_policy="once_per_level",
                                                 risk_tolerance_pct=None)
    # Funding is a sensitivity only: the stored settlement history covers part of
    # the window, so it never enters the full-period acceptance metrics.
    sensitivity: dict[str, Any] = {}
    funding = asyncio.run(_funding_rows())
    if funding:
        run = _v3(candles, first, **base, bos_refire_policy="once_per_level",
                  funding_enabled=True, funding=funding)
        sensitivity["funding"] = {
            "label": run["funding"]["label"],
            "coverage": {
                "first_settlement": run["funding"]["first_settlement"],
                "last_settlement": run["funding"]["last_settlement"],
                "rows": run["funding"]["rows"],
                "trades_full": run["funding"]["trades_full"],
                "trades_partial": run["funding"]["trades_partial"],
                "trades_none": run["funding"]["trades_none"],
            },
            "funding_usd_on_covered_trades": round(sum(
                t["funding_usd"] for t in run["_trades"] if t["funding_coverage"] == "FULL"), 2),
            "net_pnl_usd_with_partial_funding": run["net_pnl_usd"],
        }

    report = {
        "label": LABEL,
        "symbol": SYMBOL,
        "timeframe": "1h",
        "first_bar": candles[first]["time"].isoformat(),
        "last_bar": candles[-1]["time"].isoformat(),
        "sizing": {"risk_usd": RISK, "equity": EQUITY, "leverage": LEVERAGE, "taker_fee": TAKER},
        "acceptance_metrics": {
            k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in runs.items()
        },
        "sensitivity": sensitivity,
    }
    text = json.dumps(report, indent=1, default=str)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
