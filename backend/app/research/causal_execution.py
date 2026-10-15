"""Causal execution ledger beside the legacy signal-close fill.

Signal prices, stops, and targets are inputs. This module does not recompute
them. Legacy trades stay intact; causal fills are a separate book.

Model ``CAUSAL_NEXT_OPEN`` (version ``causal_next_open_v1``):
- the signal is known only after the signal candle closes
- the MARKET fill is the next candle's open
- entry and exit slippage are adverse and configurable
- a stop and a target on the same bar resolve to the stop
- a gap through the stop is labeled ``GAP_THROUGH_STOP`` and slippage is
  applied once
- the working target is the trade's TP1 (the audited book)

Dollar P&L, fees, and the leverage cap go through ``enrich_trade_execution``
with slippage already applied to the prices, so the fee helper does not
apply a second slip.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.research.config import RESEARCH_ENGINE_VERSION, SIGNAL_ENGINE_VERSION, ResearchConfig
from app.research.metrics import expectancy_r, profit_factor
from app.research.trade_fees import enrich_trade_execution
from app.signals._candle_utils import candle_time

EXECUTION_MODEL_VERSION = "causal_next_open_v1"
LEGACY_SIGNAL_CLOSE = "LEGACY_SIGNAL_CLOSE"
CAUSAL_NEXT_OPEN = "CAUSAL_NEXT_OPEN"
OPTIMISTIC_COMPARISON_LABEL = (
    "Optimistic comparison — LEGACY_SIGNAL_CLOSE fills at the signal close "
    "and does not apply next-open slippage."
)
PROTECTED_RESEARCH_RUNS = frozenset({"9f1709ac13d4"})
DUAL_LEGACY_AND_CAUSAL = "DUAL_LEGACY_AND_CAUSAL"
OUTCOME_GAP_THROUGH_STOP = "GAP_THROUGH_STOP"
# Copied from the legacy trade by ``dict(trade)``; they describe the legacy walk.
LEGACY_CLOCK_KEYS = (
    "exit_time",
    "holding_bars",
    "time_to_tp1",
    "time_to_tp2",
    "time_to_tp3",
    "time_to_sl",
    "mae",
    "mfe",
    "mae_r",
    "mfe_r",
    "rr",
    "ambiguous",
)

_ISOLATED_RUNS: dict[str, dict[str, Any]] = {}


def decision_view(
    candles: Sequence[Mapping[str, Any]],
    signal_index: int,
) -> list[Mapping[str, Any]]:
    """Candles visible when the signal candle has just closed.

    The next candle is not included. Fill and path logic must not use this
    slice as a price source for the entry.
    """
    end = int(signal_index) + 1
    if end < 0:
        return []
    return list(candles[:end])


def _num(candle: Mapping[str, Any], *keys: str) -> float:
    for key in keys:
        raw = candle.get(key)
        if raw is not None and raw != "":
            return float(raw)
    return 0.0


def _adverse(price: float, side: str, *, is_entry: bool, rate: float) -> float:
    slip = max(0.0, float(rate or 0.0))
    if slip == 0 or price <= 0:
        return price
    if side == "LONG":
        return price * (1.0 + slip) if is_entry else price * (1.0 - slip)
    return price * (1.0 - slip) if is_entry else price * (1.0 + slip)


def _walk_exit(
    candles: Sequence[Mapping[str, Any]],
    start: int,
    side: str,
    stop: float,
    target: float | None,
) -> tuple[int, float, str]:
    """Walk from the fill bar. Stop wins when both levels print. Gaps at the open."""
    last_i = start
    last_close = _num(candles[start], "close", "c") if start < len(candles) else 0.0
    for i in range(start, len(candles)):
        candle = candles[i]
        opened = _num(candle, "open", "o")
        high = _num(candle, "high", "h")
        low = _num(candle, "low", "l")
        last_i = i
        last_close = _num(candle, "close", "c")
        if side == "LONG":
            if opened <= stop:
                return i, opened, OUTCOME_GAP_THROUGH_STOP
            if target is not None and opened >= target:
                return i, opened, "TP1"
            stop_hit = low <= stop
            target_hit = target is not None and high >= target
        else:
            if opened >= stop:
                return i, opened, OUTCOME_GAP_THROUGH_STOP
            if target is not None and opened <= target:
                return i, opened, "TP1"
            stop_hit = high >= stop
            target_hit = target is not None and low <= target
        if stop_hit and target_hit:
            return i, stop, "SL"
        if stop_hit:
            return i, stop, "SL"
        if target_hit:
            return i, target if target is not None else opened, "TP1"
    return last_i, last_close, "OPEN"


def _through_stop(side: str, fill: float, stop: float) -> bool:
    if side == "LONG":
        return fill <= stop
    return fill >= stop


def _bar_time(candle: Mapping[str, Any]) -> str | None:
    """Candle open time, stamped the way the legacy walk stamps ``exit_time``."""
    ts = candle_time(candle)
    return ts.isoformat() if ts else None


def _excursions(
    side: str,
    entry: float,
    stop: float,
    bars: Sequence[Mapping[str, Any]],
) -> tuple[float, float, float | None, float | None]:
    """Same arithmetic as ``combination_backtest._excursions``, kept local."""
    highs = [_num(bar, "high", "h") for bar in bars]
    lows = [_num(bar, "low", "l") for bar in bars]
    risk = abs(entry - stop)
    if side == "LONG":
        mae = max(0.0, entry - (min(lows) if lows else entry))
        mfe = max(0.0, (max(highs) if highs else entry) - entry)
    else:
        mae = max(0.0, (max(highs) if highs else entry) - entry)
        mfe = max(0.0, entry - (min(lows) if lows else entry))
    return mae, mfe, (mae / risk) if risk > 0 else None, (mfe / risk) if risk > 0 else None


def _stamp_causal_clock(row: dict[str, Any], candles: Sequence[Mapping[str, Any]]) -> None:
    """Replace the legacy clock and excursion fields with the causal ones.

    Display only: prices, indexes and the outcome on ``row`` are not changed.
    Bars are counted from the fill bar, so an exit on the fill bar holds 0.
    """
    for key in LEGACY_CLOCK_KEYS:
        row[key] = None
    row["fill_time"] = None
    entry = row.get("entry_price")
    if entry is None:
        # No fill bar yet: nothing from the legacy walk applies.
        row["r_multiple"] = None
        return
    side = str(row["direction"])
    stop = float(row["stop_price"])
    target = row.get("tp1")
    fill_index = int(row["fill_index"])
    exit_index = int(row["exit_index"])
    outcome = row.get("outcome")
    risk = abs(float(entry) - stop)
    row["fill_time"] = _bar_time(candles[fill_index])
    if target is not None and risk > 0:
        row["rr"] = abs(float(target) - float(entry)) / risk
    # The fill is at the bar's open, so the fill bar's own range counts.
    # An OPEN trade is measured through the last bar, like the legacy walk.
    bars = candles[fill_index : exit_index + 1]
    if outcome == OUTCOME_GAP_THROUGH_STOP and exit_index == fill_index:
        # Filled and closed at the same open: no time in the market.
        bars = []
    row["mae"], row["mfe"], row["mae_r"], row["mfe_r"] = _excursions(
        side, float(entry), stop, bars
    )
    if outcome == "OPEN":
        row["r_multiple"] = None
        return
    row["exit_time"] = _bar_time(candles[exit_index])
    row["holding_bars"] = exit_index - fill_index
    # GAP_THROUGH_STOP is neither label; its bar count is ``holding_bars``.
    if outcome == "SL":
        row["time_to_sl"] = row["holding_bars"]
    elif outcome == "TP1":
        row["time_to_tp1"] = row["holding_bars"]


def execute_causal_trade(
    trade: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    entry_slippage_rate: float = 0.0005,
    exit_slippage_rate: float = 0.0005,
) -> dict[str, Any]:
    """Return a new fill row. Does not mutate ``trade``."""
    row = _fill_causal_trade(
        trade,
        candles,
        entry_slippage_rate=entry_slippage_rate,
        exit_slippage_rate=exit_slippage_rate,
    )
    _stamp_causal_clock(row, candles)
    return row


def _fill_causal_trade(
    trade: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    *,
    entry_slippage_rate: float,
    exit_slippage_rate: float,
) -> dict[str, Any]:
    side = str(trade.get("direction") or trade.get("side") or "LONG").upper()
    signal_index = int(trade.get("entry_index") or 0)
    fill_index = signal_index + 1
    stop = float(trade.get("stop_price") or trade.get("stop") or 0.0)
    target_raw = trade.get("tp1")
    target = float(target_raw) if target_raw not in (None, "") else None
    row = dict(trade)
    row["execution_model"] = CAUSAL_NEXT_OPEN
    row["execution_model_version"] = EXECUTION_MODEL_VERSION
    row["signal_index"] = signal_index
    row["fill_index"] = fill_index
    row["direction"] = side
    row["stop_price"] = stop
    row["tp1"] = target
    if fill_index >= len(candles) or fill_index < 0:
        row["outcome"] = "OPEN"
        row["entry_price"] = None
        row["exit_price"] = None
        return row

    raw_open = _num(candles[fill_index], "open", "o")
    fill = _adverse(raw_open, side, is_entry=True, rate=entry_slippage_rate)
    row["next_open"] = raw_open
    row["entry_price"] = fill
    row["entry_type"] = "MARKET"

    if _through_stop(side, fill, stop):
        row["exit_index"] = fill_index
        row["exit_price"] = fill
        row["outcome"] = OUTCOME_GAP_THROUGH_STOP
        row["slippage_applied_once"] = True
        return row

    exit_index, raw_exit, outcome = _walk_exit(candles, fill_index, side, stop, target)
    row["exit_index"] = exit_index
    row["outcome"] = outcome
    if outcome == "OPEN":
        row["exit_price"] = None
        return row
    if outcome == OUTCOME_GAP_THROUGH_STOP and exit_index == fill_index:
        row["exit_price"] = fill
        row["slippage_applied_once"] = True
        return row
    row["exit_price"] = _adverse(raw_exit, side, is_entry=False, rate=exit_slippage_rate)
    row["slippage_applied_once"] = False
    return row


def _dollarize(
    trade: Mapping[str, Any],
    *,
    risk_usd: float,
    taker_fee: float,
    maker_fee: float,
    leverage: float,
    account_equity: float | None,
) -> dict[str, Any]:
    if trade.get("entry_price") in (None, "") or trade.get("outcome") == "OPEN":
        return dict(trade)
    enriched = enrich_trade_execution(
        {
            **dict(trade),
            "entry_type": trade.get("entry_type") or "MARKET",
            "direction": trade.get("direction") or trade.get("side"),
        },
        risk_usd=risk_usd,
        taker_fee=taker_fee,
        maker_fee=maker_fee,
        leverage=leverage,
        account_equity=account_equity,
        slippage_rate=0.0,
    )
    return enriched


def _stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("net_pnl_usd") is not None or r.get("net_pnl") is not None]
    nets: list[float] = []
    gross_rs: list[float] = []
    net_rs: list[float] = []
    fees = 0.0
    gross_pnl = 0.0
    capped = 0
    wins = 0
    for row in closed:
        net = row.get("net_pnl_usd")
        if net is None:
            net = row.get("net_pnl")
        if net is None:
            continue
        nets.append(float(net))
        if float(row.get("r_gross") or row.get("r_multiple") or 0) > 0:
            wins += 1
        if row.get("r_gross") is not None:
            gross_rs.append(float(row["r_gross"]))
        elif row.get("gross_r") is not None:
            gross_rs.append(float(row["gross_r"]))
        if row.get("r_net") is not None:
            net_rs.append(float(row["r_net"]))
        fees += float(row.get("fee_total_usd") or row.get("fees") or 0.0)
        if row.get("gross_pnl_usd") is not None:
            gross_pnl += float(row["gross_pnl_usd"])
        elif row.get("gross_pnl") is not None:
            gross_pnl += float(row["gross_pnl"])
        if row.get("capped_by_leverage"):
            capped += 1
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    r_equity = 0.0
    r_peak = 0.0
    max_dd_r = 0.0
    for net, r_net in zip(nets, net_rs or [0.0] * len(nets)):
        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        r_equity += r_net
        r_peak = max(r_peak, r_equity)
        max_dd_r = max(max_dd_r, r_peak - r_equity)
    return {
        "trades": len(nets),
        "winners": wins,
        "net_pnl": round(sum(nets), 6) if nets else 0.0,
        "gross_pnl": round(gross_pnl, 6) if nets else 0.0,
        "win_rate": round(wins / len(nets), 6) if nets else None,
        "gross_r": round(sum(gross_rs), 6) if gross_rs else 0.0,
        "net_r": round(sum(net_rs), 6) if net_rs else 0.0,
        "fees": round(fees, 6),
        "margin_capped_trades": capped,
        "max_drawdown_usd": round(max_dd, 6),
        "max_drawdown_r": round(max_dd_r, 6),
    }


def price_r(side: str, entry: float, exit_px: float, stop: float) -> float | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if side == "SHORT":
        return (entry - exit_px) / risk
    return (exit_px - entry) / risk


def compare_execution_models(
    trades: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    config: ResearchConfig | None = None,
    *,
    account_equity: float | None = None,
) -> dict[str, Any]:
    """Legacy dollar book and causal book. Neither input trade is mutated."""
    cfg = config or ResearchConfig()
    legacy_rows: list[dict[str, Any]] = []
    causal_rows: list[dict[str, Any]] = []
    for trade in trades:
        frozen = copy.deepcopy(dict(trade))
        legacy = _dollarize(
            frozen,
            risk_usd=cfg.risk_usd,
            taker_fee=cfg.taker_fee_rate,
            maker_fee=cfg.maker_fee_rate,
            leverage=cfg.leverage,
            account_equity=account_equity,
        )
        legacy["execution_model"] = LEGACY_SIGNAL_CLOSE
        if legacy.get("r_multiple") is not None and legacy.get("r_gross") is None:
            legacy["r_gross"] = legacy.get("r_multiple")
        legacy_rows.append(legacy)

        causal = execute_causal_trade(
            frozen,
            candles,
            entry_slippage_rate=cfg.entry_slippage_rate,
            exit_slippage_rate=cfg.exit_slippage_rate,
        )
        if causal.get("entry_price") not in (None, "") and causal.get("exit_price") not in (None, ""):
            gross = price_r(
                str(causal.get("direction") or "LONG"),
                float(causal["entry_price"]),
                float(causal["exit_price"]),
                float(causal.get("stop_price") or 0),
            )
            causal["r_multiple"] = gross
            causal["r_gross"] = gross
        causal = _dollarize(
            causal,
            risk_usd=cfg.risk_usd,
            taker_fee=cfg.taker_fee_rate,
            maker_fee=cfg.maker_fee_rate,
            leverage=cfg.leverage,
            account_equity=account_equity,
        )
        causal_rows.append(causal)
        if frozen != dict(trade):
            raise RuntimeError("execution comparison mutated the source trade")

    legacy_stats = _stats(legacy_rows)
    causal_stats = _stats(causal_rows)
    return {
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "legacy_model": LEGACY_SIGNAL_CLOSE,
        "causal_model": CAUSAL_NEXT_OPEN,
        "legacy": {"summary": legacy_stats, "trades": legacy_rows},
        "causal": {"summary": causal_stats, "trades": causal_rows},
        "reconciliation": {
            "legacy": legacy_stats,
            "causal": causal_stats,
            "delta_net_pnl": round(causal_stats["net_pnl"] - legacy_stats["net_pnl"], 6),
            "delta_gross_r": round(causal_stats["gross_r"] - legacy_stats["gross_r"], 6),
            "delta_net_r": round(causal_stats["net_r"] - legacy_stats["net_r"], 6),
            "delta_fees": round(causal_stats["fees"] - legacy_stats["fees"], 6),
            "winner_count_unchanged": legacy_stats["winners"] == causal_stats["winners"],
        },
    }


def dataset_fingerprint(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    timeframe: str,
) -> str:
    """Hash the actual OHLCV rows, not only the last close."""
    hasher = hashlib.sha256()
    hasher.update(f"{symbol.upper()}|{timeframe}|{len(candles)}|".encode("utf-8"))
    for candle in candles:
        stamp = candle.get("time") or candle.get("open_time") or ""
        hasher.update(
            (
                f"{stamp}|{_num(candle, 'open', 'o'):.8f}|{_num(candle, 'high', 'h'):.8f}|"
                f"{_num(candle, 'low', 'l'):.8f}|{_num(candle, 'close', 'c'):.8f}\n"
            ).encode("utf-8")
        )
    return hasher.hexdigest()[:16]


def build_execution_run_record(
    *,
    run_id: str,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    config: ResearchConfig | None = None,
    comparison: Mapping[str, Any] | None = None,
    account_equity: float | None = None,
) -> dict[str, Any]:
    cfg = config or ResearchConfig.for_new_combo02_v2_run()
    book = (
        dict(comparison)
        if comparison is not None
        else compare_execution_models(trades, candles, cfg, account_equity=account_equity)
    )
    return {
        "run_id": run_id,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "dataset_fingerprint": dataset_fingerprint(candles, symbol=symbol, timeframe=timeframe),
        "configuration_hash": cfg.configuration_hash(),
        "execution_configuration_hash": cfg.execution_configuration_hash(),
        "signal_engine_version": SIGNAL_ENGINE_VERSION,
        "research_engine_version": RESEARCH_ENGINE_VERSION,
        "execution_model_version": EXECUTION_MODEL_VERSION,
        "execution_model": cfg.execution_model,
        "htf_fully_closed": cfg.htf_fully_closed,
        "legacy_summary": book["reconciliation"]["legacy"],
        "causal_summary": book["reconciliation"]["causal"],
        "reconciliation": book["reconciliation"],
        "legacy_trades": book["legacy"]["trades"],
        "causal_trades": book["causal"]["trades"],
    }


def persist_execution_run_isolated(record: Mapping[str, Any]) -> str:
    """Store one run in process memory.

    Refuses to replace an existing id. Does not open Postgres and does not
    run schema DDL.
    """
    run_id = str(record.get("run_id") or "")
    if not run_id:
        raise ValueError("run_id is required")
    if run_id in _ISOLATED_RUNS:
        raise FileExistsError(f"refusing to overwrite execution run {run_id}")
    _ISOLATED_RUNS[run_id] = copy.deepcopy(dict(record))
    return run_id


def get_isolated_execution_run(run_id: str) -> dict[str, Any] | None:
    row = _ISOLATED_RUNS.get(run_id)
    return copy.deepcopy(row) if row is not None else None


def clear_isolated_execution_runs() -> None:
    _ISOLATED_RUNS.clear()


def causal_r_metrics(causal_trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Gross-R row metrics from the causal closed trades.

    Same definitions as ``metrics.compute_metrics``: rates are over every
    closed trade and ``profit_factor`` is the shared helper. ``sl_hits`` and
    ``sl_rate`` count SL-labelled exits only; a gap through the stop is
    reported in ``gap_through_stop_hits``.
    """
    closed = [t for t in causal_trades if t.get("outcome") not in (None, "OPEN")]
    rs = [float(t["r_multiple"]) for t in closed if t.get("r_multiple") is not None]
    # No losing trade: report no ratio, as profit_factor_net does. An infinite
    # value is not valid JSON for the job response.
    pf = profit_factor(rs)
    if pf is not None and not math.isfinite(pf):
        pf = None
    sample = len(closed)
    tp1 = sum(1 for t in closed if t.get("outcome") == "TP1")
    sl = sum(1 for t in closed if t.get("outcome") == "SL")
    gaps = sum(1 for t in closed if t.get("outcome") == OUTCOME_GAP_THROUGH_STOP)
    equity: list[float] = []
    if rs:
        equity = [0.0]
        for r in rs:
            equity.append(equity[-1] + r)
    return {
        "expectancy_R": expectancy_r(rs),
        "profit_factor": pf,
        "tp1_hit_rate": (tp1 / sample) if sample else None,
        "sl_rate": (sl / sample) if sample else None,
        "tp1_hits": tp1,
        "sl_hits": sl,
        "gap_through_stop_hits": gaps,
        "r_values": rs,
        "equity_curve_r": equity,
    }


def apply_causal_primary_display(
    row: dict[str, Any],
    record: Mapping[str, Any],
    *,
    include_trades: bool = True,
) -> None:
    """Point the displayed P&L at the causal book. Legacy stays beside it."""
    causal = dict(record["causal_summary"])
    legacy = dict(record["legacy_summary"])
    count = int(causal.get("trades") or 0)
    legacy_count = int(legacy.get("trades") or 0)
    row["pnl_model"] = CAUSAL_NEXT_OPEN
    row["pnl_usd"] = causal.get("gross_pnl")
    row["pnl_usd_net"] = causal.get("net_pnl")
    row["fees_usd"] = causal.get("fees")
    gross_r = causal.get("gross_r")
    # Gross R per trade from the same book as the count beside it.
    row["average_R"] = (float(gross_r) / count) if count and gross_r is not None else None
    row["average_R_net"] = (float(causal["net_r"]) / count) if count else None
    row["max_drawdown_R"] = causal.get("max_drawdown_r")
    row["max_drawdown_usd"] = causal.get("max_drawdown_usd")
    row["win_rate"] = causal.get("win_rate")
    row["sample_size"] = count
    # Hit rates, expectancy, profit factor and the R curve from the same book.
    row.update(causal_r_metrics(record.get("causal_trades") or []))
    row["legacy_trades"] = list(record.get("legacy_trades") or [])
    if include_trades:
        row["trades"] = list(record.get("causal_trades") or [])
    row["optimistic_comparison"] = {
        "model": LEGACY_SIGNAL_CLOSE,
        "label": OPTIMISTIC_COMPARISON_LABEL,
        "net_pnl": legacy.get("net_pnl"),
        "gross_pnl": legacy.get("gross_pnl"),
        "fees": legacy.get("fees"),
        "net_r": legacy.get("net_r"),
        "gross_r": legacy.get("gross_r"),
        "winners": legacy.get("winners"),
        "trades": legacy_count,
        "win_rate": legacy.get("win_rate"),
        "max_drawdown_usd": legacy.get("max_drawdown_usd"),
        "max_drawdown_r": legacy.get("max_drawdown_r"),
    }


def write_execution_run_artifact(job_id: str, row: Mapping[str, Any], *, reports_root: Path) -> str | None:
    """Write one run file if it is not already there.

    Does not replace an existing file and does not touch the database.
    Historical run 9f1709ac13d4 is refused.
    """
    if str(job_id) in PROTECTED_RESEARCH_RUNS:
        raise PermissionError(f"refusing to modify historical research run {job_id}")
    execution = row.get("execution_run")
    if not job_id or not isinstance(execution, Mapping):
        return None
    run_id = str(execution.get("run_id") or job_id)
    safe = run_id.replace(":", "_").replace("/", "_")
    path = reports_root / str(job_id) / f"execution_run_{safe}.json"
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **dict(execution),
        "causal_trades": row.get("causal_trades") or [],
        "legacy_trades": row.get("legacy_trades") or [],
    }
    path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    return str(path)


def load_execution_run_artifact(path: Path) -> dict[str, Any]:
    """Reload a run file. Does not use the in-memory store."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
