"""VectorBT parity check for the COMBO family (counts and parity only).

Each id is resolved the way ``BacktestJobService.start`` resolves a screen
request (``engine_strategy_id`` check, then ``validate_backtest_request`` with
``allow_short=True``, then ``cell_risk_lookup`` for the cell's risk), and run
through the same code the job runs:

- ``real``: ``ResearchService.strategy_matrix`` (DB, BTCUSDT 1h,
  2024-01-01 -> 2024-06-30, ``use_research_cache=False``, include_trades=True),
  exactly the call ``backtest_job._run`` makes, with ``job_id=None``.
- ``fixture``: ``strategy_matrix`` loads candles from the DB itself, so this
  calls the two layers it runs per cell, with the arguments it builds:
  ``combination_backtest.run_combination_backtest`` (the function the CPU
  worker runs; same signal config ``service._signal_config()``, research config
  ``ResearchConfig()`` or ``for_new_combo02_v2_run()`` for COMBO_02_V2,
  ``service._market_cap``, direction filter, ``index_start=FIXTURE_WARMUP``)
  and then ``backtest_cpu_worker.build_matrix_row_postprocess`` (the same fee
  enrichment and row build, ``write_artifacts=False``). HTF 4h candles are
  aggregated from the fixture's 1h bars (complete UTC 4h buckets) for combos
  that require HTF alignment. Ids whose screen path also loads 15m/5m strategy
  candles (COMBO_02_V2 family, COMBO_SD_*) are SKIP on the fixture, because
  those series cannot be derived from 1h bars.

Then ``combo_replay_orders`` -> ``check_parity``. Writes
backend/reports/vectorbt_parity/combo_parity.json as
{"cells": [...], "v2_1a_alias_check": {...}}.

Serial; no process pool is added here (strategy_matrix spawns its own worker
subprocess per cell on Windows). Research only.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.research.vectorbt_orders_combo import (  # noqa: E402
    ReplayUnsupported,
    combo_replay_orders,
    is_closed,
)
from app.research.vectorbt_replay import ParityResult, check_parity  # noqa: E402

OUT = BACKEND / "reports" / "vectorbt_parity" / "combo_parity.json"

SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
REAL_START = "2024-01-01"
REAL_END = "2024-06-30"
RISK_USD = 20.0
PRINCIPAL = 1000.0
LEVERAGE = 2.0
TAKER_PCT = 0.04  # screen units (percent); strategy_matrix gets /100
MAKER_PCT = 0.02
LIMIT = 1200  # BacktestJobService default; dated runs load by range
MAX_DETAILS = 10

COMBO_IDS = (
    "COMBO_01", "COMBO_02", "COMBO_02_LOCAL", "COMBO_02_CLOSED_HTF", "COMBO_02_V1",
    "COMBO_02_V2", "COMBO_02_V2_1A", "COMBO_02_V2_1_A", "COMBO_02_V2_1B", "COMBO_03",
    "COMBO_03_TRANSITION", "COMBO_03_TRANSITION_B", "COMBO_03_TRANSITION_C", "COMBO_04",
    "COMBO_04_HTF", "COMBO_05", "COMBO_06", "COMBO_07", "COMBO_08",
    "COMBO_SD_A", "COMBO_SD_B", "COMBO_SD_C", "COMBO_SD_C12", "COMBO_SD_C20",
    "COMBO_SD_C_FRESH", "COMBO_SD_D", "COMBO_SD_E", "COMBO_SD_F", "COMBO_SD_LIQ",
)
DIRECTIONS = ("LONG", "SHORT")

# What the Backtest screen sends (frontend/src/backtest/v1Config.ts +
# BacktestPanel.tsx ``run``). Ids not on the dropdown are sent as an API
# request with combination_id = strategy_id = id and no combo_version.
_UI_DIRECT = {
    "COMBO_02_V2": "v2-combo02-adaptive-3-regime",
    "COMBO_02_V2_1B": "v2.1-b-choppy-15m",
    "COMBO_02": "v1",
    "COMBO_02_CLOSED_HTF": "v1-combo02-long-htf-closed",
    "COMBO_03_TRANSITION": "combo03-transition",
    "COMBO_03_TRANSITION_B": "combo03-transition-b",
    "COMBO_01": "combo01-bos",
    "COMBO_02_LOCAL": "combo02-local",
}


def screen_request(sid: str) -> dict[str, Any]:
    if sid == "COMBO_02_V1":
        return dict(combination_id="COMBO_02", strategy_id="COMBO_02_V1", combo_version="v1",
                    risk_mode="V1_PRODUCTION_PROFILE", research_risk_override=False,
                    on_dropdown=True)
    if sid == "COMBO_02_V2_1_A":
        return dict(combination_id=sid, strategy_id=sid, combo_version="v2.1-a-choppy-wait",
                    risk_mode="RESEARCH_OVERRIDE", research_risk_override=True, on_dropdown=True)
    if sid in _UI_DIRECT:
        return dict(combination_id=sid, strategy_id=sid, combo_version=_UI_DIRECT[sid],
                    risk_mode="RESEARCH_OVERRIDE", research_risk_override=True, on_dropdown=True)
    if sid.startswith("COMBO_SD_"):
        return dict(combination_id=sid, strategy_id=sid, combo_version="sd-structure",
                    risk_mode="RESEARCH_OVERRIDE", research_risk_override=True, on_dropdown=True)
    return dict(combination_id=sid, strategy_id=sid, combo_version=None,
                risk_mode="RESEARCH_OVERRIDE", research_risk_override=True, on_dropdown=False)


def resolve(sid: str, direction: str) -> dict[str, Any]:
    """Mirror BacktestJobService.start up to the strategy_matrix call."""
    from app.research.backtest_ui_config import cell_risk_lookup, validate_backtest_request
    from app.research.engine_backtest import engine_strategy_id

    req = screen_request(sid)
    if engine_strategy_id(req["combination_id"]) or engine_strategy_id(req["strategy_id"]):
        raise RuntimeError(f"{sid} resolves to an engine-family id, not COMBO")
    resolved = validate_backtest_request(
        symbols=[SYMBOL], timeframes=[TIMEFRAME], direction=direction,
        combination_id=req["combination_id"], strategy_id=req["strategy_id"],
        combo_version=req["combo_version"], setup_timeframe=TIMEFRAME,
        risk_mode=req["risk_mode"], research_risk_override=req["research_risk_override"],
        risk_usd=RISK_USD, risk_percent=None, principal_usd=PRINCIPAL, leverage=LEVERAGE,
        taker_fee_pct=TAKER_PCT, maker_fee_pct=MAKER_PCT,
        start_date=REAL_START, end_date=REAL_END, allow_short=True,
    )
    cell = cell_risk_lookup(resolved, SYMBOL, TIMEFRAME)
    return {
        "request": req,
        "combination_id": str(resolved.combination_id).upper().strip(),
        "combo_version": resolved.combo_version,
        "direction": resolved.direction,
        "cell_risk_usd": float(cell.effective_risk_amount) if cell else RISK_USD,
        "risk_source": cell.risk_source if cell else None,
    }


def _agg_4h(candles: list[dict]) -> list[dict]:
    """Complete UTC 4h buckets from 1h bars."""
    out: list[dict] = []
    bucket: list[dict] = []
    for c in candles:
        t = c["time"]
        if t.hour % 4 == 0 and bucket:
            bucket = []
        bucket.append(c)
        if t.hour % 4 == 3 and len(bucket) == 4:
            out.append({
                "time": bucket[0]["time"],
                "open": bucket[0]["open"],
                "high": max(b["high"] for b in bucket),
                "low": min(b["low"] for b in bucket),
                "close": bucket[-1]["close"],
                "volume": sum(b["volume"] for b in bucket),
            })
            bucket = []
    return out


def run_fixture_row(combination_id: str, direction: str, cell_risk: float) -> dict[str, Any]:
    """The two layers strategy_matrix runs per cell, on fixture candles.

    Candle inputs mirror what strategy_matrix loads for a 1h setup:
    candles_1h=None (setup is 1h); candles_4h when the combo requires HTF
    alignment or is COMBO_02_V2 family or COMBO_SD_*; candles_15m for the V2
    family and SD; candles_5m and candles_1d for SD. Ids needing 15m/5m/1d use
    the multi-TF fixture (one 5m walk aggregated exactly); all others use
    ``fixture_candles()`` with 4h aggregated from it.
    """
    from app.config import get_settings
    from app.research.backtest_cpu_worker import build_matrix_row_postprocess
    from app.research.bos_combinations import get_combination
    from app.research.combination_backtest import run_combination_backtest
    from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
    from app.research.config import ResearchConfig
    from app.research.market_structure import resolve_analytics_flags
    from app.research.sd_structure.variants import SD_STRUCTURE_FAMILY
    from app.research.service import _market_cap, _signal_config
    from scripts.vectorbt_parity.fixture import (
        FIXTURE_WARMUP,
        MTF_FIXTURE_WARMUP_1H,
        fixture_candles,
        fixture_candles_tf,
    )

    combo = get_combination(combination_id)
    if combo is None:
        return {"status": "NOT_FOUND", "reason": f"Unknown combination {combination_id}"}
    cid = str(combo.combination_id)
    rcfg = ResearchConfig.for_new_combo02_v2_run() if cid == "COMBO_02_V2" else ResearchConfig()
    is_v2 = cid in COMBO_V2_FAMILY
    is_sd = cid in SD_STRUCTURE_FAMILY
    mtf = is_v2 or is_sd
    c15m = c5m = c1d = None
    if mtf:
        candles = fixture_candles_tf("1h")
        idx0 = MTF_FIXTURE_WARMUP_1H
        c4h = fixture_candles_tf("4h")
        c15m = fixture_candles_tf("15m")
        if is_sd:
            c5m = fixture_candles_tf("5m")
            c1d = fixture_candles_tf("1d")
        source = "fixture (multi-TF fixture: fixture_candles_tf, 5m walk aggregated)"
    else:
        candles = fixture_candles()
        idx0 = FIXTURE_WARMUP
        c4h = _agg_4h(candles) if bool(combo.require_htf_alignment) else None
        source = "fixture (fixture_candles, 4h aggregated from 1h)"
    out = run_combination_backtest(
        SYMBOL, TIMEFRAME, list(candles), cid,
        signal_config=_signal_config(), research_config=rcfg,
        market_cap=_market_cap(SYMBOL),
        direction_filter=None if is_v2 else direction,
        index_start=idx0,
        candles_1h=None, candles_4h=c4h, candles_15m=c15m, candles_5m=c5m, candles_1d=c1d,
    )
    analytics_on, _ = resolve_analytics_flags(get_settings())
    row = build_matrix_row_postprocess({
        "backtest_out": out, "risk_usd": cell_risk,
        "taker_fee": TAKER_PCT / 100.0, "maker_fee": MAKER_PCT / 100.0,
        "leverage": LEVERAGE, "principal": PRINCIPAL, "include_trades": True,
        "direction": direction, "combination_id": combination_id,
        "combination_name": combo.name,
        "load_meta": {"symbol": SYMBOL, "timeframe": TIMEFRAME, "candle_source": source},
        "candles": candles, "candles_1h": None, "candles_4h": c4h, "candles_15m": c15m,
        "index_start": idx0, "job_id": None, "analytics_on": analytics_on,
        "write_artifacts": False,
    })
    return {"status": "OK", "rows": [row], "rows_loaded": len(candles), "multi_tf": mtf}


async def run_real_row(svc: Any, combination_id: str, direction: str, cell_risk: float) -> dict[str, Any]:
    return await svc.strategy_matrix(
        combination_id=combination_id, symbols=[SYMBOL], timeframes=[TIMEFRAME],
        direction=direction, limit=LIMIT, risk_usd=cell_risk, principal_usd=PRINCIPAL,
        start_date=REAL_START, end_date=REAL_END,
        taker_fee=TAKER_PCT / 100.0, maker_fee=MAKER_PCT / 100.0, leverage=LEVERAGE,
        include_trades=True, job_id=None, use_research_cache=False,
    )


def _example(res: ParityResult, orders: list) -> dict[str, Any] | None:
    if not res.details:
        return None
    key = res.first_mismatch_trade
    o = next((x for x in orders if x.trade_key == key), None)
    v = next((x for x in res.replayed if x.trade_key == key), None)
    if o is None and v is None:
        return None
    ex: dict[str, Any] = {"trade_key": key}
    if o is not None:
        ex["bar_loop"] = {
            "direction": o.direction, "entry_index": o.entry_index, "exit_index": o.exit_index,
            "entry_fill": o.entry_price, "exit_fill": o.exit_price, "qty": o.qty,
            "entry_fee_rate": o.entry_fee_rate, "exit_fee_rate": o.exit_fee_rate,
            "risk_usd": o.risk_usd, "stop": o.stop, "target": o.target, "outcome": o.outcome,
            "net_r": o.net_r,
        }
    if v is not None:
        ex["vectorbt"] = {
            "entry_index": v.entry_index, "exit_index": v.exit_index,
            "entry_price": v.entry_price, "exit_price": v.exit_price, "size": v.size,
            "fees_usd": v.fees_usd, "net_r": v.net_r,
        }
    return ex


def _trade_notes(trades: list[dict]) -> dict[str, Any]:
    closed = [t for t in trades if is_closed(t)]
    return {
        "closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "ambiguous_trades": sum(1 for t in closed if t.get("ambiguous") is True),
        "outcomes": dict(Counter(str(t.get("outcome")) for t in closed)),
        "trade_directions": dict(Counter(str(t.get("direction")) for t in closed)),
        "entry_types": dict(Counter(str(t.get("entry_type")) for t in closed)),
        "execution_models": dict(Counter(str(t.get("execution_model") or "LEGACY") for t in closed)),
        "slippage_rates": sorted({float(t.get("slippage_rate") or 0.0) for t in closed}),
        "same_bar_exits": sum(1 for t in closed if t.get("exit_index") == (
            t.get("fill_index") if t.get("execution_model") == "CAUSAL_NEXT_OPEN" else t.get("entry_index"))),
        "leverage_capped": sum(1 for t in closed if t.get("capped_by_leverage")),
        **_overlaps(closed),
    }


def _entry_bar(t: dict) -> int:
    return int(t["fill_index"] if t.get("execution_model") == "CAUSAL_NEXT_OPEN" else t["entry_index"])


def _overlaps(closed: list[dict]) -> dict[str, Any]:
    """Trades whose entry bar falls before an earlier trade's exit bar (two positions open)."""
    try:
        ordered = sorted(closed, key=lambda t: (_entry_bar(t), int(t["exit_index"])))
    except (KeyError, TypeError, ValueError):
        return {"overlapping_positions": None}
    n, first, last_exit, last_key = 0, None, -1, None
    for t in ordered:
        e = _entry_bar(t)
        if e < last_exit:
            n += 1
            if first is None:
                first = {"earlier_trade": last_key, "earlier_exit_bar": last_exit,
                         "trade": t.get("trade_no"), "entry_bar": e,
                         "signal_index": t.get("entry_index"), "exit_bar": t.get("exit_index")}
        if int(t["exit_index"]) >= last_exit:
            last_exit, last_key = int(t["exit_index"]), t.get("trade_no")
    return {"overlapping_positions": n, "first_overlap": first}


def base_cell(sid: str, dataset: str, direction: str) -> dict[str, Any]:
    return {
        "strategy_id": sid, "dataset": dataset, "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "direction": direction, "status": None, "bar_loop_trades": None,
        "vectorbt_trades": None, "mismatches": None, "first_mismatch_field": None,
        "first_mismatch_trade": None, "skip_reason": None, "error": None, "details": [],
    }


def evaluate_row(cell: dict[str, Any], payload: dict[str, Any], combination_id: str) -> list[dict]:
    """Fill a cell from a strategy_matrix-shaped payload; return the row's trades."""
    status = payload.get("status")
    if status in ("NOT_FOUND", "ERROR", "CANCELLED"):
        cell["status"] = "SKIP" if status == "NOT_FOUND" else "FAIL"
        reason = payload.get("reason") or f"strategy_matrix status {status}"
        if status == "NOT_FOUND":
            cell["skip_reason"] = reason
        else:
            cell["error"] = reason
        return []
    rows = payload.get("rows") or []
    if len(rows) != 1:
        cell["status"] = "FAIL"
        cell["error"] = f"expected 1 row, got {len(rows)}"
        return []
    row = rows[0]
    trades = list(row.get("trades") or [])
    notes = _trade_notes(trades)
    notes.update({
        "row_status": row.get("status"),
        "candle_source": row.get("candle_source"),
        "bars_loaded": row.get("bars_loaded"),
        "warmup_bars": row.get("warmup_bars"),
        "row_execution_model": row.get("execution_model"),
        "pnl_model": row.get("pnl_model"),
    })
    cell["notes"].update(notes)
    if str(row.get("status") or "") not in ("OK", "SUCCESS_EMPTY"):
        cell["status"] = "FAIL"
        cell["error"] = f"row status {row.get('status')}: {row.get('reason') or ''}".strip()
        return trades
    try:
        orders = combo_replay_orders(combination_id, trades)
    except ReplayUnsupported as exc:
        cell["status"] = "FAIL"
        cell["error"] = f"ReplayUnsupported: {exc}"
        cell["bar_loop_trades"] = notes["closed_trades"]
        return trades
    if not orders:
        cell.update(status="EMPTY", bar_loop_trades=0, vectorbt_trades=0, mismatches=0)
        return trades
    res = check_parity(orders)
    cell.update(
        status="PASS" if res.passed else "FAIL",
        bar_loop_trades=res.bar_loop_trades, vectorbt_trades=res.vectorbt_trades,
        mismatches=res.mismatches, first_mismatch_field=res.first_mismatch_field,
        first_mismatch_trade=res.first_mismatch_trade, error=res.error,
        details=res.details[:MAX_DETAILS],
    )
    if not res.passed:
        cell["first_mismatch_example"] = _example(res, orders)
        cell["mismatch_fields"] = dict(Counter(d["field"] for d in res.details))
    return trades


ALIAS_FIELDS = (
    "entry_index", "exit_index", "direction", "signal_time", "exit_time", "entry_type",
    "entry_price", "exit_price", "stop_price", "tp1", "tp2", "tp3", "outcome", "ambiguous",
    "r_multiple", "qty", "risk_usd", "fee_entry_rate", "fee_exit_rate", "entry_fee",
    "exit_fee", "gross_pnl_usd", "net_pnl_usd", "r_net",
)


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    return a == b


def compare_trade_lists(a: list[dict], b: list[dict]) -> dict[str, Any]:
    """Field-by-field over ALIAS_FIELDS, in order; first difference wins."""
    out: dict[str, Any] = {"trades_a": len(a), "trades_b": len(b), "result": "MATCH",
                           "first_differing_field": None, "first_differing_position": None}
    for i, (x, y) in enumerate(zip(a, b)):
        f = next((f for f in ALIAS_FIELDS if not _eq(x.get(f), y.get(f))), None)
        if f is not None:
            out.update(result="DIFFER", first_differing_field=f, first_differing_position=i,
                       a_value=x.get(f), b_value=y.get(f))
            break
    if out["result"] == "MATCH" and len(a) != len(b):
        out.update(result="DIFFER", first_differing_field="trade_count",
                   first_differing_position=min(len(a), len(b)))
    keys = set()
    for x, y in zip(a, b):
        for k in set(x) | set(y):
            if k not in ALIAS_FIELDS and not _eq(x.get(k), y.get(k)):
                keys.add(k)
    out["other_keys_differing"] = sorted(keys)
    return out


def combination_definition_diff(a_id: str, b_id: str) -> dict[str, Any]:
    from app.research.bos_combinations import get_combination

    a = get_combination(a_id).to_dict()
    b = get_combination(b_id).to_dict()
    diff = {k: {"a": a.get(k), "b": b.get(k)} for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
    return {"a": a_id, "b": b_id, "differing_keys": diff}


MERGE: dict[str, Any] | None = None  # existing report when run with --merge


def _cell_key(c: dict[str, Any]) -> tuple[str, str, str]:
    return (str(c.get("strategy_id")), str(c.get("dataset")), str(c.get("direction")))


def _merge_alias(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = dict(old)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and "comparisons" in v:
            merged = dict(out[k])
            merged.update({kk: vv for kk, vv in v.items() if kk != "comparisons"})
            merged["comparisons"] = {**(out[k].get("comparisons") or {}), **v["comparisons"]}
            out[k] = merged
        else:
            out[k] = v
    cmps = [c for k in ("screen_path", "direct_combination")
            for c in ((out.get(k) or {}).get("comparisons") or {}).values()]
    out["result"] = ("MATCH" if cmps and all(c["result"] == "MATCH" for c in cmps)
                     else ("DIFFER" if cmps else "NOT_RUN"))
    return out


def _write(cells: list[dict], alias: dict[str, Any], meta: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if MERGE is not None:
        new_keys = {_cell_key(c) for c in cells}
        kept = [c for c in MERGE.get("cells") or [] if _cell_key(c) not in new_keys]
        order = {sid: i for i, sid in enumerate(COMBO_IDS)}
        cells = sorted(kept + cells, key=lambda c: (
            order.get(str(c.get("strategy_id")), 999), DIRECTIONS.index(c.get("direction"))
            if c.get("direction") in DIRECTIONS else 9, str(c.get("dataset"))))
        alias = _merge_alias(MERGE.get("v2_1a_alias_check") or {}, alias)
        meta = {**(MERGE.get("meta") or {}), "merged_update": meta}
    else:
        alias = _merge_alias({}, alias) if alias else alias
    payload = {"meta": meta, "cells": cells, "v2_1a_alias_check": alias}
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


async def main(ids: tuple[str, ...] = COMBO_IDS, datasets: tuple[str, ...] = ("fixture", "real")) -> int:
    t_all = time.perf_counter()
    from app.config import get_settings
    from app.research.bos_combinations import get_combination
    from app.research.combo02_v2.research_variants import COMBO_V2_FAMILY
    from app.research.sd_structure.variants import SD_STRUCTURE_FAMILY
    from app.research.service import get_bos_research_service
    from app.services.database import db_manager

    meta: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "real_window": [REAL_START, REAL_END],
        "risk_usd": RISK_USD, "principal_usd": PRINCIPAL, "leverage": LEVERAGE,
        "taker_fee": TAKER_PCT / 100.0, "maker_fee": MAKER_PCT / 100.0,
        "fixture": "scripts/vectorbt_parity/fixture.py fixture_candles(), index_start=FIXTURE_WARMUP",
    }
    db_status = None
    if "real" in datasets:
        await db_manager.connect(get_settings(), ensure_schema=False)
        db_status = db_manager.status
        meta["db_status"] = db_status
    svc = get_bos_research_service()
    cells: list[dict[str, Any]] = []
    alias_trades: dict[tuple[str, str, str], list[dict]] = {}
    alias: dict[str, Any] = {}
    try:
        for sid in ids:
            for direction in DIRECTIONS:
                try:
                    res = resolve(sid, direction)
                    rej = None
                except Exception as exc:  # noqa: BLE001
                    res, rej = None, f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
                for dataset in datasets:
                    cell = base_cell(sid, dataset, direction)
                    cell["notes"] = {}
                    t0 = time.perf_counter()
                    if rej is not None:
                        cell.update(status="SKIP", skip_reason=f"screen rejected request: {rej}")
                        cells.append(cell)
                        continue
                    cid = res["combination_id"]
                    cell["notes"].update({
                        "resolved_combination_id": cid,
                        "resolved_combo_version": res["combo_version"],
                        "screen_request": res["request"],
                        "cell_risk_usd": res["cell_risk_usd"],
                        "risk_source": res["risk_source"],
                        "direction_filter_applied": cid not in COMBO_V2_FAMILY,
                    })
                    combo = get_combination(cid)
                    try:
                        if dataset == "fixture":
                            payload = run_fixture_row(cid, res["direction"], res["cell_risk_usd"])
                            if payload.get("multi_tf"):
                                cell["note"] = "multi-TF fixture"
                        else:
                            if db_status != "ok":
                                cell.update(status="SKIP", skip_reason=f"database unavailable: {db_status}")
                                cells.append(cell)
                                continue
                            payload = await run_real_row(svc, cid, res["direction"], res["cell_risk_usd"])
                        trades = evaluate_row(cell, payload, cid)
                        if sid in ("COMBO_02_V2_1A", "COMBO_02_V2_1_A"):
                            alias_trades[(sid, dataset, direction)] = trades
                    except Exception as exc:  # noqa: BLE001
                        cell.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
                        cell["notes"]["traceback"] = traceback.format_exc()[-1500:]
                    cell["notes"]["elapsed_seconds"] = round(time.perf_counter() - t0, 2)
                    cells.append(cell)
                    print(f"{sid:24s} {dataset:7s} {direction:5s} {cell['status']:5s} "
                          f"bl={cell['bar_loop_trades']} vb={cell['vectorbt_trades']} "
                          f"mm={cell['mismatches']} ff={cell['first_mismatch_field']} "
                          f"err={(cell['error'] or '')[:80]} ({cell['notes'].get('elapsed_seconds')}s)",
                          flush=True)
                    _write(cells, alias, meta)

        # COMBO_02_V2_1A vs COMBO_02_V2_1_A.
        screen = {}
        for dataset in datasets:
            for direction in DIRECTIONS:
                a = alias_trades.get(("COMBO_02_V2_1A", dataset, direction))
                b = alias_trades.get(("COMBO_02_V2_1_A", dataset, direction))
                if a is None or b is None:
                    continue
                screen[f"{dataset}/{direction}"] = compare_trade_lists(a, b)
        alias["screen_path"] = {
            "note": ("validate_backtest_request rewrites COMBO_02_V2_1A to combination "
                     "COMBO_02_V2_1_A, so through the screen path both ids run the same "
                     "combination object."),
            "comparisons": screen,
        }
        alias["combination_definitions"] = combination_definition_diff("COMBO_02_V2_1A", "COMBO_02_V2_1_A")
        if "COMBO_02_V2_1A" in ids:
            # Bypass the screen alias: run the raw combination COMBO_02_V2_1A.
            direct = {}
            for dataset in datasets:
                if dataset == "real" and db_status != "ok":
                    continue
                for direction in DIRECTIONS:
                    if dataset == "real":
                        raw = await run_real_row(svc, "COMBO_02_V2_1A", direction, RISK_USD)
                    else:
                        raw = run_fixture_row("COMBO_02_V2_1A", direction, RISK_USD)
                    rows = raw.get("rows") or []
                    raw_trades = list((rows[0] if rows else {}).get("trades") or [])
                    b = alias_trades.get(("COMBO_02_V2_1_A", dataset, direction))
                    if b is not None:
                        direct[f"{dataset}/{direction}"] = compare_trade_lists(raw_trades, b)
            alias["direct_combination"] = {
                "note": ("raw combination COMBO_02_V2_1A run directly (strategy_matrix for real, "
                         "the fixture layers for fixture; not reachable from the screen) versus "
                         "the screen's COMBO_02_V2_1_A cell."),
                "comparisons": direct,
            }
        meta["elapsed_seconds"] = round(time.perf_counter() - t_all, 1)
        _write(cells, alias, meta)
    finally:
        if "real" in datasets:
            await db_manager.close()
    print(f"wrote {OUT} in {meta.get('elapsed_seconds')}s; "
          + json.dumps(dict(Counter(c['status'] for c in cells))))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    ids = tuple(a for a in args if a.startswith("COMBO_")) or COMBO_IDS
    ds = tuple(a for a in args if a in ("fixture", "real")) or ("fixture", "real")
    for a in args:
        if a.startswith("--out="):  # partial/debug runs; default is the report path
            OUT = Path(a.split("=", 1)[1]).resolve()
    if "--merge" in args and OUT.exists():  # replace only the cells this run produces
        MERGE = json.loads(OUT.read_text(encoding="utf-8"))
    src = next((a.split("=", 1)[1] for a in args if a.startswith("--merge-from=")), None)
    if src:  # fold a separately written partial report into OUT; no run
        MERGE = json.loads(OUT.read_text(encoding="utf-8"))
        part = json.loads(Path(src).read_text(encoding="utf-8"))
        _write(part["cells"], part.get("v2_1a_alias_check") or {}, part.get("meta") or {})
        print(f"merged {len(part['cells'])} cells from {src} into {OUT}")
        sys.exit(0)
    sys.exit(asyncio.run(main(ids, ds)))
