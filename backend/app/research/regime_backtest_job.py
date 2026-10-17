"""Background regime-book backtest for the Backtest tab.

Uses ``run_regime_book``. Entry, stop, target, and fees stay in that module.
This job only loads candles and keeps one run at a time.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.research.passed_cap_book import cap_symbols
from app.research.passed_regime_backtest import run_regime_book

_MIN_BARS = 80
_lock = asyncio.Lock()
_task: asyncio.Task[None] | None = None
_state: dict[str, Any] = {
    "status": "idle",
    "phase": "",
    "current": "",
    "symbols": [],
    "limit": 0,
    "summary": None,
    "trades": [],
    "skipped_symbols": [],
    "error": None,
}


def status() -> dict[str, Any]:
    return dict(_state)


def slim_result(result: dict[str, Any], *, trade_limit: int = 40) -> dict[str, Any]:
    """Summary plus the latest closed trades. Drops the full signal list."""
    summary = dict(result.get("summary") or {})
    by_strategy = summary.get("by_strategy") or {}
    summary["by_strategy"] = sorted(
        by_strategy.values(),
        key=lambda row: float(row.get("pnl_usd_net") or 0.0),
        reverse=True,
    )
    trades = [
        {
            "symbol": t.get("symbol"),
            "strategy_id": t.get("strategy_id"),
            "direction": t.get("direction"),
            "trend_at_entry": t.get("trend_at_entry"),
            "signal_time": t.get("signal_time"),
            "exit_time": t.get("exit_time"),
            "outcome": t.get("outcome"),
            "net_pnl_usd": t.get("net_pnl_usd"),
            "r_net": t.get("r_net"),
        }
        for t in (result.get("trades") or [])
        if t.get("net_pnl_usd") is not None
    ]
    trades.sort(key=lambda row: str(row.get("exit_time") or ""))
    return {"summary": summary, "trades": trades[-trade_limit:]}


async def _load_book(symbol: str, limit: int) -> dict[str, Any] | None:
    from app.research.config import ResearchConfig
    from app.research.service import _load_htf_candles_for_combo, _load_research_candles

    warmup = max(int(ResearchConfig().min_bars), 80)
    candles, _, meta = await _load_research_candles(
        symbol, "1h", limit=limit, warmup_bars=warmup, use_research_cache=False, tail_warmup=True
    )
    if len(candles) < _MIN_BARS:
        return None
    c1h, c4h = await _load_htf_candles_for_combo(
        symbol,
        require_htf=True,
        setup_timeframe="1h",
        limit=limit,
        start_date=None,
        end_date=None,
        warmup_bars=warmup,
        use_research_cache=False,
    )
    c15, _, _ = await _load_research_candles(
        symbol, "15m", limit=max(limit * 4, 400), warmup_bars=80, use_research_cache=False, tail_warmup=True
    )
    c1d, _, _ = await _load_research_candles(
        symbol, "1d", limit=max(limit // 24, 120), warmup_bars=20, use_research_cache=False, tail_warmup=True
    )
    return {"candles": candles, "meta": meta, "c1h": c1h, "c4h": c4h, "c15": c15, "c1d": c1d}


async def _run(symbols: list[str], limit: int) -> None:
    global _state
    skipped: list[str] = []
    books: dict[str, Any] = {}
    try:
        for index, symbol in enumerate(symbols, start=1):
            _state["phase"] = "loading"
            _state["current"] = f"{symbol} {index}/{len(symbols)}"
            book = await _load_book(symbol, limit)
            if book is None:
                skipped.append(symbol)
                continue
            books[symbol] = book
        if not books:
            _state["status"] = "error"
            _state["error"] = "No symbol had enough 1h candles."
            _state["skipped_symbols"] = skipped
            return
        _state["phase"] = "running"
        _state["current"] = "regime book"
        from app.research.service import _load_research_candles

        btc_daily, _, _ = await _load_research_candles(
            "BTCUSDT", "1d", limit=500, warmup_bars=40, use_research_cache=False, tail_warmup=True
        )
        result = await asyncio.to_thread(run_regime_book, books, btc_daily)
        slim = slim_result(result)
        _state["summary"] = slim["summary"]
        _state["trades"] = slim["trades"]
        _state["skipped_symbols"] = skipped
        _state["status"] = "done" if result.get("status") != "CANCELLED" else "cancelled"
        _state["phase"] = "done"
        _state["error"] = None
    except Exception as exc:  # noqa: BLE001
        _state["status"] = "error"
        _state["error"] = str(exc)
        _state["skipped_symbols"] = skipped


async def start(symbols: list[str], limit: int) -> dict[str, Any]:
    """Start one regime-book run. A run already in progress is left alone."""
    global _task, _state
    allowed = set(cap_symbols())
    chosen = [s for s in (str(sym).upper() for sym in symbols) if s in allowed]
    if not chosen:
        return {"status": "REJECTED", "error": "No large-cap or mid-cap symbols."}
    async with _lock:
        if _state["status"] == "running":
            return {"status": "ALREADY_RUNNING", "job": status()}
        _state = {
            "status": "running",
            "phase": "starting",
            "current": "",
            "symbols": chosen,
            "limit": int(limit),
            "summary": None,
            "trades": [],
            "skipped_symbols": [],
            "error": None,
        }
        _task = asyncio.create_task(_run(chosen, max(80, min(int(limit), 5000))))
    return {"status": "STARTED", "job": status()}
