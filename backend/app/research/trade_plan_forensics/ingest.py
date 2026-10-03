"""Load historical trades from paper DB, backtest job, or JSON artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.research.trade_plan_forensics.schemas import UNAVAILABLE, UNKNOWN, NormalizedTrade
from app.services.database import db_manager

DEFAULT_JSON_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "scripts" / "trend_regime_trade_rows.json",
)


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    s = str(v).strip()
    return s or None


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize_paper_row(row: dict[str, Any]) -> NormalizedTrade:
    snip = row.get("signal_snippet") if isinstance(row.get("signal_snippet"), dict) else {}
    opened = _iso(row.get("opened_at"))
    closed = _iso(row.get("closed_at"))
    bars = None
    duration = UNAVAILABLE
    return NormalizedTrade(
        trade_id=str(row.get("id") or UNKNOWN),
        symbol=str(row.get("symbol") or UNKNOWN).upper(),
        direction=str(row.get("side") or UNKNOWN).upper(),
        entry_time=opened,
        exit_time=closed,
        entry_price=_f(row.get("entry_price")),
        exit_price=_f(row.get("exit_price")),
        stop_loss=_f(row.get("stop_price")),
        take_profit=_f(row.get("tp1_price")),
        exit_reason=str(row.get("exit_reason") or UNKNOWN),
        bars_held=bars,
        duration=duration,
        entry_type=str(snip.get("entry_type") or UNKNOWN),
        fees=None,  # not stored on paper_trades
        gross_pnl=_f(row.get("pnl_usd")),
        net_pnl=None,
        R=_f(row.get("r_multiple")),
        balance=UNAVAILABLE,
        timeframe=str(row.get("timeframe") or UNKNOWN),
        strategy=str(snip.get("strategy") or snip.get("combination_id") or UNKNOWN),
        setup=str(snip.get("setup") or UNKNOWN),
        market_signal=str(snip.get("market_signal") or UNKNOWN),
        trade_plan_state=str(row.get("status") or UNKNOWN),
        source="paper_trades",
        raw=dict(row),
    )


def normalize_backtest_trade(row: dict[str, Any], *, cell: dict[str, Any] | None = None) -> NormalizedTrade:
    cell = cell or {}
    tid = (
        row.get("trade_id")
        or f"{row.get('symbol')}|{row.get('timeframe')}|{row.get('signal_time')}|{row.get('entry_index')}"
    )
    return NormalizedTrade(
        trade_id=str(tid),
        symbol=str(row.get("symbol") or cell.get("symbol") or UNKNOWN).upper(),
        direction=str(row.get("direction") or UNKNOWN).upper(),
        entry_time=_iso(row.get("signal_time") or row.get("entry_time")),
        exit_time=_iso(row.get("exit_time")),
        entry_price=_f(row.get("entry_price")),
        exit_price=_f(row.get("exit_price")),
        stop_loss=_f(row.get("stop_price") or row.get("stop_loss")),
        take_profit=_f(row.get("tp1") or row.get("take_profit")),
        exit_reason=str(row.get("outcome") or row.get("exit_reason") or UNKNOWN),
        bars_held=_i(row.get("holding_bars") or row.get("bars_held")),
        duration=UNAVAILABLE,
        entry_type=str(row.get("entry_type") or (row.get("condition_snapshot") or {}).get("entry_type") or UNKNOWN),
        fees=_f(row.get("fee_total_usd") or row.get("fees")),
        gross_pnl=_f(row.get("gross_pnl_usd") or row.get("gross_pnl")),
        net_pnl=_f(row.get("net_pnl_usd") or row.get("net_pnl")),
        R=_f(row.get("r_multiple") or row.get("R") or row.get("r_gross")),
        balance=UNAVAILABLE,
        timeframe=str(row.get("timeframe") or cell.get("timeframe") or UNKNOWN),
        strategy=str(row.get("combination_id") or cell.get("combination_id") or "COMBO_02"),
        setup=str(row.get("combination_id") or "COMBO_02"),
        market_signal=UNKNOWN,
        trade_plan_state=str(row.get("outcome") or UNKNOWN),
        source="backtest_job",
        mae=_f(row.get("mae")),
        mfe=_f(row.get("mfe")),
        mae_r=_f(row.get("mae_r")),
        mfe_r=_f(row.get("mfe_r")),
        entry_index=_i(row.get("entry_index")),
        raw=dict(row),
    )


def normalize_research_json_row(row: dict[str, Any]) -> NormalizedTrade:
    tid = (
        row.get("trade_id")
        or f"{row.get('symbol')}|{row.get('timeframe')}|{row.get('entry_time')}|{row.get('entry_index')}"
    )
    return NormalizedTrade(
        trade_id=str(tid),
        symbol=str(row.get("symbol") or UNKNOWN).upper(),
        direction=str(row.get("direction") or UNKNOWN).upper(),
        entry_time=_iso(row.get("entry_time")),
        exit_time=_iso(row.get("exit_time")),
        entry_price=_f(row.get("entry_price")),
        exit_price=_f(row.get("exit_price")),
        stop_loss=_f(row.get("stop_loss") or row.get("stop_price")),
        take_profit=_f(row.get("take_profit") or row.get("tp1")),
        exit_reason=str(row.get("result") or row.get("exit_reason") or UNKNOWN),
        bars_held=_i(row.get("bars_held") or row.get("holding_bars")),
        duration=UNAVAILABLE,
        entry_type=str(row.get("entry_type") or UNKNOWN),
        fees=_f(row.get("fees")),
        gross_pnl=_f(row.get("gross_pnl")),
        net_pnl=_f(row.get("net_pnl")),
        R=_f(row.get("R") or row.get("r_multiple")),
        balance=UNAVAILABLE,
        timeframe=str(row.get("timeframe") or UNKNOWN),
        strategy=str(row.get("strategy") or row.get("combination_id") or "COMBO_02"),
        setup=str(row.get("setup") or row.get("regime_category") or UNKNOWN),
        market_signal=str(row.get("market_signal") or UNKNOWN),
        trade_plan_state=str(row.get("result") or UNKNOWN),
        source="research_json",
        mae=_f(row.get("MAE") or row.get("mae")),
        mfe=_f(row.get("MFE") or row.get("mfe")),
        mae_r=_f(row.get("MAE_R") or row.get("mae_r")),
        mfe_r=_f(row.get("MFE_R") or row.get("mfe_r")),
        entry_index=_i(row.get("entry_index")),
        raw=dict(row),
    )


async def load_paper_trades(*, closed_only: bool = True, limit: int = 5000) -> list[NormalizedTrade]:
    if db_manager.engine is None:
        return []
    clause = "WHERE status IN ('CLOSED', 'CANCELLED')" if closed_only else ""
    sql = text(
        f"""
        SELECT id, symbol, side, status, entry_price, stop_price, tp1_price,
               quantity, risk_usd, opened_at, closed_at, exit_price, exit_reason,
               pnl_usd, r_multiple, source_candle_ts, timeframe, signal_snippet
        FROM paper_trades
        {clause}
        ORDER BY COALESCE(closed_at, opened_at) DESC
        LIMIT :lim
        """
    )
    async with db_manager.engine.begin() as conn:
        rows = (await conn.execute(sql, {"lim": int(limit)})).mappings().all()
    out: list[NormalizedTrade] = []
    for r in rows:
        d = dict(r)
        snip = d.get("signal_snippet")
        if isinstance(snip, str):
            try:
                snip = json.loads(snip)
            except Exception:  # noqa: BLE001
                snip = {}
        d["signal_snippet"] = snip if isinstance(snip, dict) else {}
        out.append(normalize_paper_row(d))
    return out


def load_backtest_job_trades() -> list[NormalizedTrade]:
    try:
        from app.research.backtest_job import backtest_job_service
    except Exception:  # noqa: BLE001
        return []
    st = backtest_job_service.status()
    rows = st.get("rows") or []
    out: list[NormalizedTrade] = []
    for cell in rows:
        for t in cell.get("trades") or []:
            if not isinstance(t, dict):
                continue
            out.append(normalize_backtest_trade(t, cell=cell))
    return out


def load_json_trades(path: str | Path | None = None) -> list[NormalizedTrade]:
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    candidates.extend(DEFAULT_JSON_CANDIDATES)
    for p in candidates:
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                continue
            return [normalize_research_json_row(dict(r)) for r in raw if isinstance(r, dict)]
    return []


async def ingest_trades(
    *,
    source: str = "auto",
    json_path: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    start: str | None = None,
    end: str | None = None,
    strategy: str | None = None,
) -> tuple[list[NormalizedTrade], dict[str, Any]]:
    """Load and filter trades. ``source``: auto|paper|backtest_job|json."""
    meta: dict[str, Any] = {"requested_source": source, "sources_tried": []}
    trades: list[NormalizedTrade] = []

    async def _paper() -> list[NormalizedTrade]:
        meta["sources_tried"].append("paper_trades")
        return await load_paper_trades()

    def _job() -> list[NormalizedTrade]:
        meta["sources_tried"].append("backtest_job")
        return load_backtest_job_trades()

    def _json() -> list[NormalizedTrade]:
        meta["sources_tried"].append("research_json")
        return load_json_trades(json_path)

    src = (source or "auto").lower().strip()
    if src == "paper":
        trades = await _paper()
    elif src == "backtest_job":
        trades = _job()
    elif src == "json":
        trades = _json()
    else:
        trades = await _paper()
        if trades:
            meta["resolved_source"] = "paper_trades"
        else:
            trades = _job()
            if trades:
                meta["resolved_source"] = "backtest_job"
            else:
                trades = _json()
                meta["resolved_source"] = "research_json" if trades else "none"

    if src != "auto":
        meta["resolved_source"] = src

    def _in_window(t: NormalizedTrade) -> bool:
        if symbol and t.symbol.upper() != symbol.upper():
            return False
        if timeframe and str(t.timeframe or "").lower() != timeframe.lower():
            return False
        if strategy and str(t.strategy or "").upper() != strategy.upper():
            return False
        et = t.entry_time or ""
        if start and et and et[:10] < start[:10]:
            return False
        if end and et and et[:10] > end[:10]:
            return False
        return True

    filtered = [t for t in trades if _in_window(t)]
    meta["raw_count"] = len(trades)
    meta["filtered_count"] = len(filtered)
    meta["missing_fields_policy"] = "UNKNOWN/UNAVAILABLE — no inferred fills"
    return filtered, meta
