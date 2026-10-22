"""BTC context for the screener (owner D188 part 1c). Read-only.

- Regime: the live BTC daily switch, ``btc_daily_regime.daily_trend`` through ``RegimeClock``, the same
  clock the passed-book watcher reads, on the closed BTCUSDT 1d bars in the live store.
- Funding: the D175 monitor, the 72 h mean of BTCUSDT 8 h funding settlements against the 0.01%/8 h base
  rate, from the app's own settlement table (``grid_funding_settlements``, read via the grid reader).
  The request path never fetches from the exchange: without fresh settlements it says "funding: no data".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

BTC = "BTCUSDT"
BASE_RATE = 0.0001  # 0.01% per 8 h settlement
WINDOW = timedelta(hours=72)
SETTLEMENT_INTERVAL = timedelta(hours=8)
STALE_GRACE = timedelta(minutes=30)
MIN_SETTLEMENTS = 9  # 72 h / 8 h
REGIME_SOURCE = "btc_daily_regime.daily_trend via RegimeClock (the live passed-book daily switch)"
FUNDING_SOURCE = "grid_funding_settlements (app DB, read-only)"
NO_DATA_TEXT = "funding: no data"


def _utc(value: Any) -> datetime | None:
    from app.screener_tool.htf import _utc as parse

    return parse(value)


def btc_regime(now: datetime | None = None, *, candles: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """The BTC daily trend at ``now`` from closed daily bars (RegimeClock counts a bar from open + 24 h)."""
    now = _utc(now) or datetime.now(timezone.utc)
    out: dict[str, Any] = {"trend": None, "as_of_bar": None, "bars": 0, "short_history": None,
                           "status": "OK", "source": REGIME_SOURCE}
    try:
        from app.research.passed_regime_backtest import RegimeClock
        from app.signals.config import SignalConfig

        if candles is None:
            from app.services.ohlcv_store import ohlcv_store

            candles = list(ohlcv_store.get_candles_for_engine(BTC, "1d", include_open=False) or [])
        reading = RegimeClock(candles, SignalConfig()).at(now)
    except Exception as exc:  # noqa: BLE001 - display only
        out.update(status="ERROR", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        return out
    out.update(
        trend=reading.get("trend"),
        as_of_bar=reading.get("as_of_bar") or None,
        bars=int(reading.get("bars") or 0),
        short_history=bool(reading.get("short_history")),
    )
    if not out["as_of_bar"]:
        out["status"] = "NO_DATA"
    return out


def funding_state(rows: Sequence[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    """D175 reading from settlement rows (``time``, ``rate``). Pure."""
    now = _utc(now) or datetime.now(timezone.utc)
    points = sorted(
        ((t, float(r["rate"])) for r in rows or [] if (t := _utc(r.get("time"))) is not None and t <= now),
        key=lambda p: p[0],
    )
    out: dict[str, Any] = {"status": "NO_DATA", "text": NO_DATA_TEXT, "source": FUNDING_SOURCE,
                           "base_rate": BASE_RATE, "mean_72h": None, "state": None, "latest_settlement": None,
                           "settlements": 0, "share_above_base": None, "reason": None}
    if not points:
        out["reason"] = "no settlements stored for the last 72 h"
        return out
    latest = points[-1][0]
    out["latest_settlement"] = latest.isoformat()
    if now - latest > SETTLEMENT_INTERVAL + STALE_GRACE:
        out["reason"] = f"latest stored settlement {latest.isoformat()} is older than 8 h"
        return out
    window = [rate for t, rate in points if latest - WINDOW < t <= latest]
    out["settlements"] = len(window)
    if len(window) < MIN_SETTLEMENTS:
        out["reason"] = f"{len(window)} of {MIN_SETTLEMENTS} settlements stored in the 72 h window"
        return out
    mean = sum(window) / len(window)
    if mean < 0:
        state = "NEGATIVE"
    elif mean > BASE_RATE + 1e-12:
        state = "ABOVE_BASE"
    elif mean < BASE_RATE - 1e-12:
        state = "BELOW_BASE"
    else:
        state = "AT_BASE"
    out.update(
        status="OK",
        mean_72h=mean,
        state=state,
        share_above_base=sum(1 for r in window if r > BASE_RATE + 1e-12) / len(window),
        text=f"BTC funding 72 h mean {mean * 100:.4f}%/8h vs 0.0100% base: {state.replace('_', ' ').lower()}",
        reason=None,
    )
    return out


Loader = Callable[[str, datetime, datetime], Awaitable[Mapping[str, Any]]]


async def btc_funding(now: datetime | None = None, *, loader: Loader | None = None) -> dict[str, Any]:
    now = _utc(now) or datetime.now(timezone.utc)
    if loader is None:
        from app.grid_trading.settlements import load_grid_settlements

        loader = load_grid_settlements
    try:
        got = await loader(BTC, now - WINDOW - SETTLEMENT_INTERVAL, now)
    except Exception as exc:  # noqa: BLE001 - display only
        out = funding_state([], now)
        out["reason"] = f"settlement read failed: {type(exc).__name__}"
        return out
    out = funding_state(list(got.get("rows") or []), now)
    if out["status"] != "OK" and got.get("status") in ("unavailable", "never_fetched"):
        out["reason"] = str(got.get("reason") or got.get("status"))
    return out
