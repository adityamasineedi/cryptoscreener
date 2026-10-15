"""Reconcile V3 paper trades against the frozen engine on the same closed bars.

Every field gets a row: engine expected, paper actual, difference, within
tolerance (None = reported only). A row outside tolerance is a reconciliation
exception. Open exceptions block OOS acceptance until resolved with a note.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research import candle12_v3 as v3

# Relative tolerances are fractions of the engine value.
TOLERANCES: dict[str, float] = {
    "entry_fill_rel": 0.0005,  # 0.05% of the expected fill
    "stop_fill_rel": 0.0005,  # 0.05% of the expected stop exit fill
    "target_fill_rel": 0.0005,  # 0.05% of the expected target exit fill
    "qty_abs": 1e-12,  # exact after step rounding
    "fee_abs": 1e-6,  # computed vs exchange-reported, USD
    "funding_abs": 1e-9,  # computed vs exchange-reported, USD ("exact")
    "price_rel": 1e-9,  # order prices and candle data: exact
}


def _row(field: str, engine: Any, paper: Any, *, rel: float | None = None, absolute: float | None = None,
         exact: bool = False) -> dict[str, Any]:
    diff = None
    within: bool | None = None
    if isinstance(engine, (int, float)) and isinstance(paper, (int, float)) and engine is not None:
        diff = float(paper) - float(engine)
        if rel is not None:
            within = abs(diff) <= rel * max(abs(float(engine)), 1e-12)
        elif absolute is not None:
            within = abs(diff) <= absolute
    elif exact:
        within = engine == paper
    if exact and within is None:
        within = engine == paper
    return {"field": field, "engine": engine, "paper": paper, "difference": diff, "within_tolerance": within}


def _ohlc(c: Mapping[str, Any]) -> dict[str, float]:
    return {k: float(c[k]) for k in ("open", "high", "low", "close")}


def engine_expectation(
    candles: Sequence[Mapping[str, Any]],
    *,
    first_trade_index: int,
    cfg: v3.Candle12V3Config,
    symbol: str,
    timeframe: str,
    funding_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    funding = list(funding_rows or []) if cfg.funding_enabled else None
    if cfg.funding_enabled and not funding:
        cfg = v3.Candle12V3Config(**{**{k: v for k, v in cfg.to_dict().items()
                                         if k not in ("strategy_id", "engine_version")}, "funding_enabled": False})
        funding = None
    return v3.run_candle12_v3(symbol, timeframe, candles, config=cfg, index_start=first_trade_index, funding=funding)


def compare_trade(engine: Mapping[str, Any], paper: Mapping[str, Any], candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    t = TOLERANCES
    e_target = min(x for x in (engine["tp1"], engine["tp2"], engine["tp3"]) if x is not None) \
        if engine["direction"] == "LONG" else max(x for x in (engine["tp1"], engine["tp2"], engine["tp3"]) if x is not None)
    exit_rel = t["stop_fill_rel"] if engine["outcome"] == "SL" else t["target_fill_rel"]
    e_fees = -float(engine["fee_total_usd"])
    rows = [
        _row("signal_timestamp", engine["candle1_time"], paper["candle1_time"], exact=True),
        _row("candle1_ohlc", _ohlc(candles[engine["candle1_index"]]), paper["c1"], exact=True),
        _row("candle2_ohlc", _ohlc(candles[engine["entry_index"]]), paper["c2"], exact=True),
        _row("trigger_price", engine["trigger_level"], paper["trigger"], rel=t["price_rel"]),
        _row("entry_fill", engine["entry_price"], paper["entry_fill"], rel=t["entry_fill_rel"]),
        _row("expected_fill_used_by_paper", engine["entry_price"], paper["expected_fill"], rel=t["price_rel"]),
        _row("entry_slippage_vs_trigger", engine["entry_price"] / engine["trigger_level"] - 1.0,
             paper["entry_slippage_vs_trigger"]),
        _row("stop_price", engine["stop_price"], paper["stop_price"], rel=t["price_rel"]),
        _row("target_price", e_target, paper["target_price"], rel=t["price_rel"]),
        _row("quantity", engine["qty"], paper["qty"], absolute=t["qty_abs"]),
        _row("notional_usd", engine["notional_entry_usd"], paper["notional_usd"]),
        _row("intended_risk_usd", engine["intended_risk_usd"], paper["intended_risk_usd"], absolute=1e-9),
        _row("actual_risk_usd", engine["risk_usd"], paper["actual_risk_usd"]),
        _row("fees_usd_engine_vs_paper", e_fees, paper["fees_reported_usd"]),
        _row("fees_usd_computed_vs_exchange", paper["fees_computed_usd"], paper["fees_reported_usd"], absolute=t["fee_abs"]),
    ]
    if paper.get("funding_reported_usd") is not None:
        rows.append(_row("funding_usd_computed_vs_exchange", paper["funding_computed_usd"],
                         paper["funding_reported_usd"], absolute=t["funding_abs"]))
    rows.append(_row("funding_usd_engine_vs_paper", engine.get("funding_usd"), paper.get("funding_computed_usd")))
    rows += [
        _row("gross_pnl_usd", engine["gross_pnl_usd"], paper["gross_pnl_usd"]),
        _row("net_pnl_usd", engine["net_pnl_usd"], paper["net_pnl_usd"]),
        _row("r_net_actual_risk", engine["r_net_actual_risk"], paper["r_net_actual_risk"]),
        _row("r_net_intended_risk", engine["r_net_intended_risk"], paper["r_net_intended_risk"]),
        _row("exit_reason", engine["outcome"], paper["exit_reason"], exact=True),
        _row("exit_timestamp", engine["exit_time"], paper["exit_time"], exact=True),
        _row("exit_fill", engine["exit_price"], paper["exit_fill"], rel=exit_rel),
        _row("rejection_tags", [], paper.get("rejection_tags", []), exact=True),
    ]
    return rows


_CATEGORY = {
    "entry_fill": "ENTRY_FILL_OUTSIDE_TOLERANCE",
    "exit_fill": "EXIT_FILL_OUTSIDE_TOLERANCE",
    "quantity": "QUANTITY_MISMATCH",
    "fees_usd_computed_vs_exchange": "FEE_MISMATCH",
    "funding_usd_computed_vs_exchange": "FUNDING_MISMATCH",
    "exit_reason": "EXIT_MISMATCH",
    "exit_timestamp": "EXIT_MISMATCH",
    "signal_timestamp": "DATA_MISMATCH",
    "candle1_ohlc": "DATA_MISMATCH",
    "candle2_ohlc": "DATA_MISMATCH",
}


def reconcile(
    *,
    paper_trades: Sequence[Mapping[str, Any]],
    paper_signals: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    first_trade_index: int,
    cfg: v3.Candle12V3Config,
    symbol: str,
    timeframe: str,
    funding_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    engine = engine_expectation(candles, first_trade_index=first_trade_index, cfg=cfg, symbol=symbol,
                                timeframe=timeframe, funding_rows=funding_rows)
    e_trades = {t["candle1_time"]: t for t in engine["trades"] if t["outcome"] != "OPEN"}
    e_open = {t["candle1_time"] for t in engine["trades"] if t["outcome"] == "OPEN"}
    e_rejected = {r["candle1_time"]: r for r in engine["rejected"]}
    p_trades = {t["candle1_time"]: t for t in paper_trades}
    p_signals = {s["candle1_time"]: s for s in paper_signals}
    reports: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []

    def exc(key: str, category: str, **detail: Any) -> None:
        exceptions.append({"id": f"{key}:{category}:{detail.get('field', '')}", "category": category,
                           "candle1_time": key, **detail})

    for key in sorted(set(e_trades) | set(p_trades)):
        e, p = e_trades.get(key), p_trades.get(key)
        if e and p:
            rows = compare_trade(e, p, candles)
            reports.append({"candle1_time": key, "trade_id": p["trade_id"], "rows": rows})
            for r in rows:
                if r["within_tolerance"] is False:
                    note = ("gap or market impact: explain before resolving"
                            if r["field"] in ("entry_fill", "exit_fill") else "")
                    exc(key, _CATEGORY.get(r["field"], "FIELD_MISMATCH"), field=r["field"], engine=r["engine"],
                        paper=r["paper"], difference=r["difference"], note=note)
        elif e:
            exc(key, "MISSING_PAPER_TRADE", paper_outcome=p_signals.get(key, {}).get("outcome"))
        elif p and key not in e_open:
            exc(key, "UNEXPECTED_PAPER_TRADE")

    for key, r in e_rejected.items():
        s = p_signals.get(key)
        tags = sorted({v3.rejection_outcome(x) for x in r["reject_reasons"]})
        if not s or s.get("outcome") != "SIGNAL_REJECTED" or sorted(set(s.get("tags", []))) != tags:
            exc(key, "REJECTION_MISMATCH", engine_tags=tags, paper=s)
    for key, s in p_signals.items():
        if s.get("outcome") != "SIGNAL_REJECTED" or key in e_rejected:
            continue
        # The paper book checks size before Candle-2; the engine only sizes fills.
        # A rejection is consistent when Candle-2 never traded the entry price.
        if key in e_trades:
            exc(key, "REJECTION_MISMATCH", detail="paper rejected a trade the engine took")
        else:
            reports.append({"candle1_time": key, "trade_id": s["trade_id"], "rows": [
                _row("rejection_tags", None, s.get("tags"), exact=False),
            ], "note": "rejected before Candle-2; the engine records no fill for this setup"})

    return {
        "engine_counts": engine["counts"],
        "paper_trades": len(paper_trades),
        "engine_trades": len(e_trades),
        "reports": reports,
        "exceptions": exceptions,
        "tolerances": TOLERANCES,
        "status": "RECONCILED" if not exceptions else "EXCEPTIONS",
    }
