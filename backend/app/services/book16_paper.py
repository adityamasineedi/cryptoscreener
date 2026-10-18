"""Virtual paper for 4h book 16 (252-bar closing-high breakout) on BTCUSDT and ETHUSDT, inside the live app.

Mirror of the frozen forward test BOOK16_4H_FWD-1 (docs/book16_4h_fwd1_contract.md on branch
research/book16-fwd1, its own collector and ledger are the official record). Same rules, same start, same
accounting:

- book 16 from app.research.daily_trend.books with the pinned screen ledger (simulate_ledger of
  scripts/daily_direction_shortlist_mtf_prereg.py, sha256 263ecc52), funding via daily_trend.funding;
- first forward bar 2026-10-19 00:00 UTC; the 1,000 bars before it are indicator warm-up only (inside the
  sealed holdout 2026-07-15 -> 2026-10-19: no signal, trade or PnL is computed for them); the ledger starts
  one bar after START, so only forward-bar closes can schedule an entry;
- one virtual $10,000 account per coin, 0.5% compounding risk, fee 0.04%, slippage 0.05%, Binance funding.

4h bars come from Postgres ``ohlcv`` (the live ingestion); funding from Binance public fapi (fundingRate,
fundingInfo). No order is sent; the COMBO paper ledger is not used. Closed trades are written once and never
changed (a difference stops the book with status INTEGRITY); a closed trade waits until the first funding
settlement after its exit exists (its funding coverage needs it).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger("book16_paper")

STRATEGY_ID = "MTF_4H_16_BREAKOUT252"
MIRRORS = "BOOK16_4H_FWD-1"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
TIMEFRAME = "4h"
BAR = pd.Timedelta(hours=4)
START = pd.Timestamp("2026-10-19 00:00", tz="UTC")
WARMUP_BARS = 1000
FAPI = "https://fapi.binance.com"
BACKEND = Path(__file__).resolve().parents[2]
STATE_PATH = BACKEND / "data" / "book16_paper" / "state.json"
SCREEN_RUNNER = BACKEND / "scripts" / "daily_direction_shortlist_mtf_prereg.py"
SCREEN_RUNNER_SHA256 = "263ecc52d2cfb9d875d3de698c01a7b415b39334e933c97342de984f8db0eba4"


@lru_cache(maxsize=1)
def _screen():
    import hashlib

    got = hashlib.sha256(SCREEN_RUNNER.read_bytes()).hexdigest()
    if got != SCREEN_RUNNER_SHA256:
        raise RuntimeError(f"RULES_DRIFT: screen runner sha256 {got}")
    spec = importlib.util.spec_from_file_location("mtf_screen_prereg_paper", SCREEN_RUNNER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fresh_state() -> dict[str, Any]:
    return {
        "strategy_id": STRATEGY_ID,
        "mirrors": MIRRORS,
        "symbols": list(SYMBOLS),
        "timeframe": TIMEFRAME,
        "direction": "LONG",
        "mode": "VIRTUAL_PAPER",
        "live_orders": False,
        "start": START.isoformat(),
        "account_equity_start": 10_000.0,
        "risk_fraction": 0.005,
        "status": "WAITING_FOR_FIRST_FORWARD_BAR",
        "books": {s: {"closed": [], "pending_closed": [], "open": None, "equity": 10_000.0,
                      "last_bar": None, "forward_bars": 0, "skipped_lev_cap": 0} for s in SYMBOLS},
        "updated_utc": None,
        "error": None,
    }


def load_state(path: Path | None = None) -> dict[str, Any] | None:
    file = path or STATE_PATH
    if not file.exists():
        return None
    return json.loads(file.read_text(encoding="utf-8"))


def save_state(state: Mapping[str, Any], path: Path | None = None) -> None:
    file = path or STATE_PATH
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    tmp.replace(file)


def _frame(candles: Sequence[Mapping[str, Any]], now: pd.Timestamp) -> pd.DataFrame:
    d = pd.DataFrame(
        {k: [float(c[k]) for c in candles] for k in ("open", "high", "low", "close")},
        index=pd.DatetimeIndex(pd.to_datetime([c["time"] for c in candles], utc=True)).as_unit("ns"),
    ).sort_index()
    d = d[~d.index.duplicated(keep="last")]
    d = d[d.index + BAR <= now]                       # closed bars only
    gaps = d.index[1:][(d.index[1:] - d.index[:-1]) != BAR]
    if len(gaps):
        raise RuntimeError(f"DATA_GAP: missing 4h bar(s) before {gaps[0].isoformat()}")
    return d


def evaluate(symbol: str, d: pd.DataFrame, settlements: pd.DataFrame | None) -> dict[str, Any]:
    """Closed trades, the open position and equity from START + 1 bar to the last closed bar."""
    from app.research.daily_trend.books import build_books
    from app.research.daily_trend.funding import FundingSchedule

    m = _screen()
    i_start = int(d.index.searchsorted(START))
    out: dict[str, Any] = {"closed": [], "open": None, "equity": m.START_EQUITY, "skipped_lev_cap": 0,
                           "last_bar": d.index[-1].isoformat() if len(d) else None,
                           "forward_bars": max(0, len(d) - i_start)}
    if i_start >= len(d) or d.index[i_start] != START:
        return out
    if i_start < WARMUP_BARS:
        raise RuntimeError(f"{symbol}: warm-up short ({i_start} of {WARMUP_BARS} bars before START)")
    d = d.iloc[i_start - WARMUP_BARS:]
    i0, i1 = WARMUP_BARS + 1, len(d) - 1
    if i1 < i0:
        return out
    b16 = build_books(d)[16]
    fs = FundingSchedule(settlements, d.index, d["open"].to_numpy())
    trades, rows, skipped = m.simulate_ledger(
        symbol, d, BAR, dict(state=b16["state"], trig=b16["trig"], kind="event"),
        np.full(len(d), m.RISK), i0, i1, fs, eval_id=f"{STRATEGY_ID}|{symbol}", fold=0)
    for t in trades:
        rec = {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in t.items()
               if k not in ("evaluation_id", "fold")}
        rec["trade_id"] = f"{symbol}|{t['entry_time']}"
        if pd.Timestamp(t["entry_time"]) < START + BAR:
            raise RuntimeError(f"{symbol}: entry before the first forward signal ({t['entry_time']})")
        if t["exit_reason"] == "window_end":
            # Open at the last closed bar: marked to that close, never booked as closed.
            rec["mark_price"] = float(d["close"].iloc[-1])
            rec["unrealized_usd"] = rec.pop("net_pnl")
            for k in ("exit_time", "exit_price", "exit_reason"):
                rec.pop(k, None)
            out["open"] = rec
        else:
            out["closed"].append(rec)
    out["equity"] = float(rows[-1]["equity"]) if rows else m.START_EQUITY
    out["skipped_lev_cap"] = int(skipped)
    return out


def apply(state: dict[str, Any], symbol: str, ev: Mapping[str, Any], last_settlement: pd.Timestamp | None) -> None:
    """Book newly closed trades once; a booked trade that would change stops the book (INTEGRITY)."""
    book = state["books"][symbol]
    booked = {t["trade_id"]: t for t in book["closed"]}
    pending = []
    for t in ev["closed"]:
        if last_settlement is None or last_settlement <= pd.Timestamp(t["exit_time"]):
            pending.append(t)
            continue
        if t["trade_id"] in booked:
            if json.dumps(booked[t["trade_id"]], sort_keys=True, default=str) != json.dumps(t, sort_keys=True,
                                                                                           default=str):
                raise RuntimeError(f"INTEGRITY: booked trade {t['trade_id']} would change")
            continue
        book["closed"].append(t)
    if not set(booked) <= {t["trade_id"] for t in ev["closed"]} - {t["trade_id"] for t in pending}:
        raise RuntimeError(f"INTEGRITY: {symbol} booked trades disappeared")
    book.update(pending_closed=pending, open=ev["open"], equity=ev["equity"], last_bar=ev["last_bar"],
                forward_bars=ev["forward_bars"], skipped_lev_cap=ev["skipped_lev_cap"])


async def _fapi(client, path: str, params: dict) -> Any:
    r = await client.get(f"{FAPI}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


async def fetch_funding(client, symbol: str, now: pd.Timestamp, interval_hours: float | None) -> pd.DataFrame:
    rows, t = [], START
    while True:
        page = await _fapi(client, "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": int(t.value // 10**6),
                                                          "endTime": int(now.value // 10**6), "limit": 1000})
        rows += page
        if len(page) < 1000:
            break
        t = pd.Timestamp(int(page[-1]["fundingTime"]) + 1, unit="ms", tz="UTC")
    if not rows:
        return pd.DataFrame(columns=["rate", "documented_interval_hours"])
    idx = pd.DatetimeIndex(pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True)).as_unit("ns")
    f = pd.DataFrame({"rate": [float(r["fundingRate"]) for r in rows],
                      "documented_interval_hours": float(interval_hours) if interval_hours else np.nan}, index=idx)
    return f[~f.index.duplicated(keep="first")].sort_index()


async def sync(now: datetime | None = None, path: Path | None = None) -> dict[str, Any]:
    """One pass: load 4h bars (Postgres) and funding (Binance), rebuild, book new closes, save."""
    import asyncio

    import httpx

    from app.research.postgres_ohlcv import load_ohlcv_series_range

    now_ts = pd.Timestamp(now or datetime.now(timezone.utc))
    state = load_state(path) or fresh_state()
    if state.get("status") in ("INTEGRITY", "RULES_DRIFT"):
        return {"status": state["status"], "error": state.get("error")}      # stopped until investigated
    if now_ts < START + BAR:
        state.update(status="WAITING_FOR_FIRST_FORWARD_BAR", updated_utc=now_ts.isoformat())
        save_state(state, path)
        return {"status": state["status"]}
    try:
        async with httpx.AsyncClient() as client:
            info = {r["symbol"]: r for r in await _fapi(client, "/fapi/v1/fundingInfo", {})}
            for s in SYMBOLS:
                candles = await load_ohlcv_series_range(
                    s, TIMEFRAME, start=(START - WARMUP_BARS * BAR).to_pydatetime(), end_exclusive=None)
                d = _frame(candles, now_ts)
                iv = info.get(s, {}).get("fundingIntervalHours")
                f = await fetch_funding(client, s, now_ts, float(iv) if iv else None)
                ev = await asyncio.to_thread(evaluate, s, d, f if len(f) else None)
                apply(state, s, ev, f.index.max() if len(f) else None)
        state.update(status="COLLECTING", error=None, updated_utc=now_ts.isoformat())
    except Exception as exc:  # noqa: BLE001 - a network or data error is retried next pass
        msg = f"{exc}" if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
        kind = msg.split(":", 1)[0]
        state.update(status=kind if kind in ("INTEGRITY", "RULES_DRIFT") else "ERROR", error=msg,
                     updated_utc=now_ts.isoformat())
        logger.warning("book16_paper_sync_failed", error=msg)
    save_state(state, path)
    return {"status": state["status"], "error": state.get("error"),
            "closed": {s: len(state["books"][s]["closed"]) for s in SYMBOLS}}


def view(path: Path | None = None) -> dict[str, Any]:
    """Read-only view for the Paper tab."""
    state = load_state(path) or fresh_state()
    return {**state, "timestamp": datetime.now(timezone.utc).isoformat()}
