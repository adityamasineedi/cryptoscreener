"""VectorBT parity for the engine-family strategies (CANDLE12, multicap, OSE).

For every engine id x allowed direction x dataset, runs ``run_engine_cell`` with
the payload shape ``engine_strategy_matrix`` builds, maps the closed trades to
explicit orders (``vectorbt_orders_engine``) and replays them through VectorBT
(``vectorbt_replay.check_parity``). Writes
backend/reports/vectorbt_parity/engine_parity.json. Counts and parity only;
no performance numbers are read or written.

Datasets (BTCUSDT 1h):
  fixture  seeded synthetic candles (scripts/vectorbt_parity/fixture.py), index_start
           FIXTURE_WARMUP, market_cap None
  real     2024-01-01 -> 2024-06-30 via service._load_research_candles, exactly as
           engine_strategy_matrix loads it (read-only SELECT; no schema DDL)

Run from backend/:  PYTHONPATH=. .venv/Scripts/python.exe scripts/vectorbt_parity/run_engine_parity.py
Serial on purpose (no process pool on Windows).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.research.engine_backtest import (  # noqa: E402
    ENGINE_STRATEGIES,
    engine_request_error,
    run_engine_cell,
)
from app.research.vectorbt_orders_engine import ReplayUnsupported, engine_replay_orders  # noqa: E402
from app.research.vectorbt_replay import check_parity  # noqa: E402
from scripts.vectorbt_parity.fixture import FIXTURE_WARMUP, fixture_candles  # noqa: E402

OUT = BACKEND / "reports" / "vectorbt_parity" / "engine_parity.json"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
REAL_START = "2024-01-01"
REAL_END = "2024-06-30"
# Same request defaults as the Backtest screen run this mirrors.
RISK_USD = 20.0
TAKER_FEE = 0.0004
MAKER_FEE = 0.0002
LEVERAGE = 2.0
PRINCIPAL = 1000.0
MAX_DETAILS = 10


async def _load_real(warmup_bars: int) -> tuple[list[dict[str, Any]], int, dict[str, Any], float | None]:
    from app.research.service import _load_research_candles, _market_cap

    candles, eval_start, meta = await _load_research_candles(
        SYMBOL,
        TIMEFRAME,
        limit=20000,
        start_date=REAL_START,
        end_date=REAL_END,
        warmup_bars=int(warmup_bars),
        use_research_cache=False,
        tail_warmup=True,
    )
    sym = str(meta.get("symbol") or SYMBOL)
    return list(candles), int(eval_start or 0), meta, _market_cap(sym)


async def _real_datasets(warmups: set[int]) -> tuple[dict[int, Any], str | None]:
    from app.config import get_settings
    from app.services.database import db_manager

    try:
        # Research CLI: no schema DDL against a database a live API may share.
        await db_manager.connect(get_settings(), ensure_schema=False)
    except Exception as exc:  # noqa: BLE001
        return {}, f"database connect failed: {type(exc).__name__}: {exc}"
    if not db_manager.enabled or db_manager.engine is None:
        return {}, f"database unavailable (status={db_manager.status})"
    out: dict[int, Any] = {}
    try:
        for w in sorted(warmups):
            candles, eval_start, meta, mcap = await _load_real(w)
            src = str(meta.get("candle_source") or "")
            if not candles or not src.startswith("postgresql"):
                return {}, f"real slice not loaded from PostgreSQL (source={src!r}, bars={len(candles)})"
            out[w] = (candles, eval_start, meta, mcap)
    finally:
        await db_manager.engine.dispose()
    return out, None


def _payload(sid: str, direction: str, candles: list[dict], index_start: int, market_cap: float | None) -> dict:
    return {
        "strategy_id": sid,
        "direction": direction,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "candles": [dict(c) for c in candles],
        "index_start": int(index_start),
        "risk_usd": RISK_USD,
        "taker_fee": TAKER_FEE,
        "maker_fee": MAKER_FEE,
        "leverage": LEVERAGE,
        "principal": PRINCIPAL,
        "market_cap": market_cap,
    }


def _cell(sid: str, dataset: str, direction: str) -> dict[str, Any]:
    return {
        "strategy_id": sid,
        "family": ENGINE_STRATEGIES[sid]["family"],
        "dataset": dataset,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": direction,
        "status": None,
        "bar_loop_trades": None,
        "vectorbt_trades": None,
        "mismatches": None,
        "first_mismatch_field": None,
        "first_mismatch_trade": None,
        "skip_reason": None,
        "error": None,
        "details": [],
    }


def run_cell(sid: str, dataset: str, direction: str, data: tuple | None, data_error: str | None) -> dict[str, Any]:
    cell = _cell(sid, dataset, direction)
    why = engine_request_error(sid, direction, [TIMEFRAME])
    if why:
        cell.update(status="SKIP", skip_reason=f"engine_request_error: {why}")
        return cell
    if data is None:
        cell.update(status="SKIP", skip_reason=f"dataset unavailable: {data_error}")
        return cell
    candles, index_start, market_cap = data
    t0 = time.perf_counter()
    try:
        out = run_engine_cell(_payload(sid, direction, candles, index_start, market_cap))
    except Exception as exc:  # noqa: BLE001
        cell.update(
            status="SKIP",
            skip_reason="engine rejected the cell",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc()[-1500:],
        )
        return cell
    cell["engine_seconds"] = round(time.perf_counter() - t0, 3)
    trades = list(out.get("trades") or [])
    cell["open_trades_at_end"] = int(out.get("open_trades_at_end") or 0)
    cell["bar_loop_trades"] = len(trades)
    if not trades:
        cell.update(status="EMPTY", vectorbt_trades=0, mismatches=0)
        return cell
    t1 = time.perf_counter()
    try:
        orders = engine_replay_orders(sid, trades, taker_fee=TAKER_FEE)
    except ReplayUnsupported as exc:
        cell.update(
            status="FAIL",
            vectorbt_trades=0,
            mismatches=len(trades),
            first_mismatch_field="replay_unsupported",
            error=f"ReplayUnsupported: {exc}",
        )
        return cell
    res = check_parity(orders)
    cell["replay_seconds"] = round(time.perf_counter() - t1, 3)
    # Path coverage: how many trades exercised the intra-bar ordering rules.
    cell["same_bar_exits"] = sum(1 for o in orders if o.exit_index == o.entry_index)
    by_entry = sorted(orders, key=lambda o: (o.entry_index, o.exit_index))
    cell["entry_on_prior_exit_bar"] = sum(
        1 for a, b in zip(by_entry, by_entry[1:]) if b.entry_index == a.exit_index
    )
    d = res.to_dict(max_details=MAX_DETAILS)
    cell.update(
        status="PASS" if res.passed else "FAIL",
        vectorbt_trades=res.vectorbt_trades,
        mismatches=res.mismatches,
        first_mismatch_field=res.first_mismatch_field,
        first_mismatch_trade=res.first_mismatch_trade,
        error=res.error,
        details=d["details"],
    )
    return cell


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--datasets", nargs="+", default=["fixture", "real"], choices=["fixture", "real"])
    ap.add_argument("--ids", nargs="*", default=None, help="engine ids (default: all)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    ids = [s for s in ENGINE_STRATEGIES if not args.ids or s in args.ids]
    datasets: dict[str, dict[str, Any]] = {}
    meta_out: dict[str, Any] = {}
    if "fixture" in args.datasets:
        fx = fixture_candles()
        datasets["fixture"] = {"error": None, "by_warmup": None, "data": (fx, FIXTURE_WARMUP, None)}
        meta_out["fixture"] = {"bars": len(fx), "index_start": FIXTURE_WARMUP}
    if "real" in args.datasets:
        warmups = {int(ENGINE_STRATEGIES[s]["warmup_bars"]) for s in ids}
        loaded, err = asyncio.run(_real_datasets(warmups))
        datasets["real"] = {"error": err, "by_warmup": loaded, "data": None}
        meta_out["real"] = {
            "start_date": REAL_START,
            "end_date": REAL_END,
            "error": err,
            "by_warmup": {
                str(w): {
                    "bars": len(v[0]),
                    "index_start": v[1],
                    "first_bar": str(v[0][0]["time"]),
                    "first_eval_bar": str(v[0][v[1]]["time"]),
                    "last_bar": str(v[0][-1]["time"]),
                    "candle_source": v[2].get("candle_source"),
                    "market_cap": v[3],
                }
                for w, v in loaded.items()
            },
        }

    cells: list[dict[str, Any]] = []
    family_seconds: dict[str, float] = {}
    for sid in ids:
        spec = ENGINE_STRATEGIES[sid]
        for ds_name in args.datasets:
            ds = datasets[ds_name]
            if ds["by_warmup"] is not None:
                v = ds["by_warmup"].get(int(spec["warmup_bars"]))
                data = (v[0], v[1], v[3]) if v else None
            else:
                data = ds["data"]
            for direction in ("LONG", "SHORT"):
                t0 = time.perf_counter()
                cell = run_cell(sid, ds_name, direction, data, ds["error"])
                dt = time.perf_counter() - t0
                cell["cell_seconds"] = round(dt, 3)
                family_seconds[spec["family"]] = family_seconds.get(spec["family"], 0.0) + dt
                cells.append(cell)
                print(
                    f"{sid:28s} {ds_name:7s} {direction:5s} {cell['status']:5s} "
                    f"bar={cell['bar_loop_trades']} vbt={cell['vectorbt_trades']} "
                    f"mm={cell['mismatches']} first={cell['first_mismatch_field']} "
                    f"{(cell['skip_reason'] or cell['error'] or '')[:80]} ({dt:.1f}s)",
                    flush=True,
                )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cells, indent=2, default=str), encoding="utf-8")
    meta_path = out.with_name(out.stem + "_meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "datasets": meta_out,
                "request": {
                    "symbol": SYMBOL,
                    "timeframe": TIMEFRAME,
                    "risk_usd": RISK_USD,
                    "taker_fee": TAKER_FEE,
                    "maker_fee": MAKER_FEE,
                    "leverage": LEVERAGE,
                    "principal": PRINCIPAL,
                },
                "family_seconds": {k: round(v, 2) for k, v in family_seconds.items()},
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(cells)} cells); family seconds {family_seconds}")
    return 1 if any(c["status"] == "FAIL" for c in cells) else 0


if __name__ == "__main__":
    raise SystemExit(main())
