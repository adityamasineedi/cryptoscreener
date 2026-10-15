"""Non-acceptance smoke test for the V3 paper adapter.

Runs the adapter on a short window of already-seen bars with the simulated
broker and checks the plumbing end to end. The result is labelled
NON_ACCEPTANCE_SMOKE_ONLY, is written to a SMOKE store that the OOS runner
refuses, and says nothing about the strategy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper.adapter import PaperAdapter
from app.research.candle12_v3_paper.broker import SimFaults, SimulatedBroker
from app.research.candle12_v3_paper.contract import contract_config
from app.research.candle12_v3_paper.reconcile import reconcile
from app.research.candle12_v3_paper.store import NON_ACCEPTANCE_SMOKE_ONLY, PaperStore
from app.signals._candle_utils import candle_time

SMOKE_PASS, SMOKE_FAIL = "SMOKE_PASS", "SMOKE_FAIL"


def run_smoke(
    candles: Sequence[Mapping[str, Any]],
    *,
    first_trade_index: int,
    store_root: Path,
    funding_rows: Sequence[Mapping[str, Any]] | None = None,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    cfg: v3.Candle12V3Config | None = None,
    faults: SimFaults | None = None,
) -> dict[str, Any]:
    v3.check_oos_guard(candles)  # a smoke run never touches the reserved window
    funding_rows = [{**r, "time": v3._as_utc(r["time"])} for r in (funding_rows or [])]
    cfg = cfg or contract_config()
    if not funding_rows:
        cfg = v3.Candle12V3Config(**{**{k: v for k, v in cfg.to_dict().items()
                                         if k not in ("strategy_id", "engine_version")}, "funding_enabled": False})
    step, _, min_notional, _ = cfg.exchange_filter(symbol)
    store = PaperStore(store_root, mode="SMOKE", manifest={
        "window_start": candle_time(candles[first_trade_index]).isoformat(),
        "window_end": candle_time(candles[-1]).isoformat(),
        "counts_toward_oos": False,
    })
    broker = SimulatedBroker(slippage=cfg.entry_slippage, fee_rate=cfg.taker_fee, step=step,
                             min_notional=min_notional, equity=cfg.account_equity, leverage=cfg.leverage,
                             faults=faults)
    adapter = PaperAdapter(broker=broker, store=store, cfg=cfg, symbol=symbol, timeframe=timeframe,
                           funding_rows=funding_rows)
    adapter.replay(candles, first_trade_index)

    reloaded = PaperStore(store_root, mode="SMOKE")
    events = reloaded.read("events")
    trades = reloaded.read("trades")
    signals = reloaded.read("signals")
    kinds = [e["event"] for e in events]
    rec = reconcile(paper_trades=trades, paper_signals=signals, candles=candles,
                    first_trade_index=first_trade_index, cfg=cfg, symbol=symbol, timeframe=timeframe,
                    funding_rows=funding_rows)
    for e in rec["exceptions"]:
        reloaded.exception(e["id"], e["category"], **{k: v for k, v in e.items() if k not in ("id", "category")})

    funding_expected = bool(funding_rows) and any(
        candle_time(candles[first_trade_index]) <= r["time"] <= candle_time(candles[-1]) for r in funding_rows)
    checks = {
        "candles_ingested": len(adapter.closed) == len(candles) and "DATA_GAP" not in kinds,
        "signal_detected": "SIGNAL_DETECTED" in kinds,
        "order_placed": "ORDER_ACCEPTED" in kinds,
        "order_canceled_after_candle2": any(e["event"] == "ORDER_CANCELED" and e.get("reason") == "CANDLE2_CLOSED"
                                            for e in events),
        "fill_captured": "ORDER_FILLED" in kinds,
        "exit_orders_placed": "EXIT_ORDERS_PLACED" in kinds,
        "stop_or_target_handled": "POSITION_CLOSED" in kinds,
        "fees_captured": bool(trades) and all(t["fees_reported_usd"] > 0 for t in trades),
        "funding_captured": (any(t["funding_events"] > 0 for t in trades) if funding_expected else None),
        "persistence_round_trip": len(events) == store._seq and len(trades) == kinds.count("POSITION_CLOSED")
        and reloaded.load_state() is not None,
        "reconciliation_clean": rec["status"] == "RECONCILED",
    }
    passed = all(v is not False for v in checks.values()) and checks["funding_captured"] is not None
    result = {
        "result": SMOKE_PASS if passed else SMOKE_FAIL,
        "label": NON_ACCEPTANCE_SMOKE_ONLY,
        "counts_toward_oos": False,
        "strategy_validation": False,
        "checks": checks,
        "window": {"first_trade_bar": candle_time(candles[first_trade_index]).isoformat(),
                   "last_bar": candle_time(candles[-1]).isoformat()},
        "counts": {k: kinds.count(k) for k in sorted(set(kinds))},
        "trades": len(trades),
        "reconciliation": {"status": rec["status"], "exceptions": rec["exceptions"],
                           "paper_trades": rec["paper_trades"], "engine_trades": rec["engine_trades"]},
        "store": str(store_root),
    }
    reloaded.update_manifest(smoke_result=result["result"], label=NON_ACCEPTANCE_SMOKE_ONLY)
    return result
