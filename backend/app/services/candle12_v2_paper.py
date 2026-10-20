"""Virtual paper for the frozen CANDLE12_V2 BTCUSDT 1h LONG book.

The signal, stop, target, and shared long/short lock come from
``run_candle12_v2`` unchanged. This module records LONG trades only.
Shorts stay inside the engine so they can still block a long, and they
are not paper positions. No exchange order is sent. The COMBO paper
ledger is not used.

Each trade is booked once under its key ``(strategy_id, symbol, side,
signal-bar close)``. The signal bar is Candle 1, so its close is the engine's
``signal_time`` (Candle 2's open). The engine's ``entry_index`` is a position
in the rolling candle window it was given and moves every hour, so it is never
used as an identity.

State version 1 keyed trades by ``entry_index`` and booked the same trade again
on every new bar. Loading a version 1 file marks every record in it INVALID
(kept under ``invalid``) and restarts the book from the bar it is loaded on.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from app.core.logging import get_logger
from app.services import decision_journal
from app.research.candle12_v2 import Candle12V2Config, _exit_fill, run_candle12_v2
from app.research.trade_fees import enrich_trade_execution

logger = get_logger("candle12_v2_paper")

STRATEGY_ID = "CANDLE12_V2"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
DIRECTION = "LONG"
RISK_USD = 20.0
EQUITY = 1000.0
LEVERAGE = 2.0
TAKER_FEE = 0.0004
MAKER_FEE = 0.0002

STATE_VERSION = 2
INVALID_REASON = "v1 state keyed trades by rolling window index and double-booked them"

STATE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "candle12_v2_paper" / "btcusdt_long.json"
)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        ts = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(value or "")


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _after(left: Any, right: Any) -> bool:
    a, b = _utc(left), _utc(right)
    if a is None:
        return False
    return b is None or a > b


def trade_key(trade: Mapping[str, Any]) -> str | None:
    """(strategy_id, symbol, side, signal-bar close) as one string. None without a time."""
    ts = _utc(trade.get("signal_time"))
    if ts is None:
        return None
    return f"{STRATEGY_ID}|{SYMBOL}|{DIRECTION}|{ts.astimezone(timezone.utc).isoformat()}"


def fresh_state(watermark: str) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "strategy_id": STRATEGY_ID,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": DIRECTION,
        "mode": "VIRTUAL_PAPER",
        "live_orders": False,
        "risk_usd": RISK_USD,
        "account_equity": EQUITY,
        "leverage": LEVERAGE,
        "taker_fee": TAKER_FEE,
        "watermark": watermark,
        "booked_keys": [],
        "open": None,
        "closed": [],
        "invalid": [],
    }


def migrate_state(state: dict[str, Any], *, last_bar_time: str, now: str | None = None) -> dict[str, Any]:
    """Version 1 -> 2: every old record becomes INVALID and the book restarts at ``last_bar_time``."""
    if int(state.get("version") or 1) >= STATE_VERSION:
        return state
    at = now or datetime.now(timezone.utc).isoformat()
    invalid = []
    for row in list(state.get("closed") or []) + ([state["open"]] if state.get("open") else []):
        invalid.append({**row, "status": "INVALID", "invalid_reason": INVALID_REASON, "invalidated_at": at})
    out = fresh_state(last_bar_time)
    out["invalid"] = invalid
    out["migrated_from"] = {"version": 1, "watermark": state.get("watermark"), "at": at}
    logger.warning("candle12_v2_state_migrated", invalid=len(invalid), restart=last_bar_time)
    return out


def _dollars(trade: Mapping[str, Any], v2: Candle12V2Config) -> dict[str, Any]:
    raw = trade.get("exit_price")
    fill = None if raw is None else _exit_fill(DIRECTION, float(raw), v2)
    row = enrich_trade_execution(
        {
            "direction": DIRECTION,
            "entry_price": trade["entry_price"],
            "stop_price": trade["stop_price"],
            "exit_price": fill,
            "entry_time": trade.get("signal_time"),
            "exit_time": trade.get("exit_time"),
            "entry_index": trade["entry_index"],
            "exit_index": trade.get("exit_index"),
            "outcome": trade.get("outcome"),
            "tp1": trade.get("tp1"),
            "holding_bars": trade.get("holding_bars"),
            "condition_snapshot": {"entry_type": "MARKET"},
        },
        risk_usd=RISK_USD,
        taker_fee=v2.trading_fee,
        maker_fee=MAKER_FEE,
        leverage=LEVERAGE,
        account_equity=EQUITY,
    )
    return {
        "key": trade_key(trade),
        "strategy_id": STRATEGY_ID,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": DIRECTION,
        "mode": "VIRTUAL_PAPER",
        "live_orders": False,
        "entry_index": int(trade["entry_index"]),
        "entry_time": _iso(trade.get("signal_time")),
        "entry_price": float(trade["entry_price"]),
        "stop_price": float(trade["stop_price"]),
        "tp1": trade.get("tp1"),
        "exit_index": trade.get("exit_index"),
        "exit_time": _iso(trade.get("exit_time")) if trade.get("exit_time") else None,
        "outcome": trade.get("outcome"),
        "raw_exit_price": raw,
        "exit_price": fill,
        "qty": row.get("qty"),
        "net_pnl_usd": row.get("net_pnl_usd"),
        "fee_total_usd": row.get("fee_total_usd"),
    }


def paper_step(
    state: dict[str, Any] | None,
    trades: Sequence[Mapping[str, Any]],
    *,
    last_bar_time: str,
    v2: Candle12V2Config | None = None,
) -> dict[str, Any]:
    """Apply one engine read. Historical closes before the watermark are not booked.

    A trade is booked once per key; reading the same bars again, a shifted
    window, or a restart books nothing new. The open trade closes when the
    engine reports the same key closed.
    """
    cfg = v2 or Candle12V2Config()
    longs = [
        t
        for t in trades
        if str(t.get("direction") or "").upper() == DIRECTION and trade_key(t) is not None
    ]
    alerts: list[dict[str, Any]] = []
    if state is None:
        booked = fresh_state(last_bar_time)
        opens = [t for t in longs if str(t.get("outcome") or "") == "OPEN"]
        if opens:
            current = opens[-1]
            booked["open"] = _dollars(current, cfg)
            booked["booked_keys"].append(trade_key(current))
            alerts.append({"kind": "entry", "row": booked["open"]})
        booked["_alerts"] = alerts
        return booked

    state = migrate_state(state, last_bar_time=last_bar_time)
    keys = set(state.get("booked_keys") or [])
    watermark = state.get("watermark")
    for trade in longs:
        key = trade_key(trade)
        outcome = str(trade.get("outcome") or "")
        open_row = state.get("open") or {}
        if open_row.get("key") == key:
            if outcome not in ("", "OPEN"):
                closed = _dollars(trade, cfg)
                state["closed"].append(closed)
                state["open"] = None
                alerts.append({"kind": "exit", "row": closed})
            continue
        if key in keys or not _after(trade.get("signal_time"), watermark):
            continue
        row = _dollars(trade, cfg)
        if outcome in ("", "OPEN"):
            if state.get("open"):
                # The engine allows one trade at a time; a second open means the
                # stored one left the window unresolved. Keep the first booking.
                logger.warning("candle12_v2_second_open_ignored", key=key, held=open_row.get("key"))
                decision_journal.write("CANDLE12_V2", "skip", reason="second_open_ignored", key=key, held=open_row.get("key"))
                continue
            state["open"] = row
            alerts.append({"kind": "entry", "row": row})
        else:
            state["closed"].append(row)
            alerts.append({"kind": "exit", "row": row})
        keys.add(key)
    state["booked_keys"] = sorted(keys)
    state["live_orders"] = False
    state["_alerts"] = alerts
    return state


def format_signal(alert: Mapping[str, Any]) -> str:
    """Plain Telegram text. Virtual paper only."""
    row = alert.get("row") if isinstance(alert.get("row"), Mapping) else {}
    kind = str(alert.get("kind") or "")
    if kind == "entry":
        return (
            "CANDLE12_V2 PAPER LONG BTCUSDT 1h\n"
            f"Entry {row.get('entry_time')}\n"
            f"Price {row.get('entry_price')}\n"
            f"Stop {row.get('stop_price')}\n"
            f"TP1 {row.get('tp1')}\n"
            "Risk $20. Virtual fill. No exchange order."
        )
    net = row.get("net_pnl_usd")
    net_s = "n/a" if net is None else f"{float(net):+.2f}"
    return (
        "CANDLE12_V2 PAPER EXIT BTCUSDT 1h\n"
        f"Outcome {row.get('outcome')}\n"
        f"Exit {row.get('exit_time')}\n"
        f"Price {row.get('exit_price')}\n"
        f"Net {net_s} USD\n"
        "Virtual fill. No exchange order."
    )


async def send_signals(alerts: Sequence[Mapping[str, Any]]) -> int:
    """Post this book's new entry and exit messages. Returns how many were sent."""
    from app.config import get_settings

    settings = get_settings()
    if not bool(getattr(settings, "paper_candle12_v2_telegram_enabled", False)):
        return 0
    token = str(getattr(settings, "telegram_bot_token", "") or "").strip()
    chat_id = str(getattr(settings, "telegram_chat_id", "") or "").strip()
    if not token or not chat_id or not alerts:
        if alerts and (not token or not chat_id):
            logger.warning("candle12_v2_telegram_missing_credentials")
        return 0
    sent = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for alert in alerts:
            text = format_signal(alert)
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("candle12_v2_telegram_failed", error=str(exc))
                continue
            if resp.status_code >= 400:
                logger.warning("candle12_v2_telegram_failed", status=resp.status_code)
                continue
            sent += 1
    if sent:
        logger.info("candle12_v2_telegram_sent", count=sent)
    return sent


def load_state(path: Path | None = None) -> dict[str, Any] | None:
    file = path or STATE_PATH
    if not file.exists():
        return None
    return json.loads(file.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    file = path or STATE_PATH
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, file)


def sync_candles(
    candles: Sequence[Mapping[str, Any]],
    *,
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
    path: Path | None = None,
) -> dict[str, Any]:
    """Run the frozen engine and update the virtual ledger. Refuses any other book."""
    if str(symbol).upper() != SYMBOL or str(timeframe) != TIMEFRAME:
        raise ValueError(f"virtual paper is {SYMBOL} {TIMEFRAME} only, got {symbol} {timeframe}")
    if len(candles) < 50:
        return {"status": "WAITING_FOR_BARS", "bars": len(candles), "live_orders": False}
    result = run_candle12_v2(SYMBOL, TIMEFRAME, list(candles), progress_every=0)
    if result.get("status") != "OK":
        return {"status": result.get("status") or "ENGINE_ERROR", "live_orders": False}
    last = _iso(candles[-1].get("time"))
    v2_raw = result.get("v2_config") or {}
    v2 = Candle12V2Config(
        trading_fee=float(v2_raw.get("trading_fee", TAKER_FEE)),
        entry_slippage=float(v2_raw.get("entry_slippage", 0.0002)),
        exit_slippage=float(v2_raw.get("exit_slippage", 0.0002)),
    )
    state = paper_step(load_state(path), result.get("trades") or [], last_bar_time=last, v2=v2)
    alerts = list(state.pop("_alerts", []))
    decision_journal.write("CANDLE12_V2", "tip_evaluated", bar=last, trades_in_window=len(result.get("trades") or []))
    for alert in alerts:
        row = alert.get("row") if isinstance(alert.get("row"), Mapping) else {}
        decision_journal.write("CANDLE12_V2", str(alert.get("kind") or "alert"), key=row.get("key"),
                               entry_time=row.get("entry_time"), entry_price=row.get("entry_price"),
                               stop_price=row.get("stop_price"), exit_time=row.get("exit_time"), outcome=row.get("outcome"))
    save_state(state, path)
    return {
        "status": "OK",
        "live_orders": False,
        "open": state.get("open") is not None,
        "closed": len(state.get("closed") or []),
        "watermark": state.get("watermark"),
        "alerts": alerts,
    }
