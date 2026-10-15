"""Market context at each backtest trade's decision bar. Display only.

Adds one ``market_context`` dict per trade: trend, whether that trend agrees
with the trade direction, relative volume, and where the close sat in the
recent range. Reads closed bars up to the decision bar only. Never changes a
signal, a fill, a stop or a result.

Definitions are the repo's own:
- trend: confirmed-swing trend, same call the COMBO HTF gate uses
  (``bos_strategy_comparison.htf.precompute_htf_trend_cache``)
- volume: ``engines.volume.engine.volume_regime`` on volume / 20-bar average,
  threshold 1.5 (the volume engine default)
- zone: below or above the middle of the prior 24-bar high-low range
  (``multi_cap_strategies.reference.ref_discount_mask``)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

VOLUME_SMA_BARS = 20
RVOL_THRESHOLD = 1.5
ZONE_WINDOW_BARS = 24

_TREND_TEXT = {"BULLISH": "Uptrend", "BEARISH": "Downtrend", "NEUTRAL": "No trend"}
_VOLUME_TEXT = {"contraction": "Low volume", "expansion": "High volume", "normal": "Normal volume"}


def _utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def structure_verdict(direction: str, trend: str | None) -> str:
    """CORRECT when the trend backs the trade side, WRONG when it opposes it."""
    side = str(direction or "").upper()
    if trend not in ("BULLISH", "BEARISH") or side not in ("LONG", "SHORT"):
        return "NEUTRAL"
    return "CORRECT" if (side == "LONG") == (trend == "BULLISH") else "WRONG"


def attach_trade_context(
    trades: Sequence[dict[str, Any]],
    candles: Sequence[Mapping[str, Any]],
    *,
    timeframe: str,
    symbol: str,
    decision_offset: int = 0,
) -> int:
    """Set ``market_context`` on each trade. Returns how many were labelled.

    The decision bar is the trade's ``signal_time`` bar, shifted by
    ``decision_offset`` (-1 when the stamp is the entry bar's open, so the
    bar before it is the last closed one).
    """
    if not trades or not candles:
        return 0
    from app.engines.mtf.indicators import relative_volume
    from app.engines.volume.engine import volume_regime
    from app.research.bos_strategy_comparison.htf import precompute_htf_trend_cache

    index: dict[datetime, int] = {}
    for i, c in enumerate(candles):
        ts = _utc(c.get("time"))
        if ts is not None:
            index[ts] = i
    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]
    closes = [float(c.get("close") or 0) for c in candles]
    volumes = [float(c.get("volume") or 0) for c in candles]
    tf = str(timeframe).lower()
    trend_cache = precompute_htf_trend_cache(list(candles), timeframe=tf, symbol=str(symbol))

    done = 0
    for trade in trades:
        at = index.get(_utc(trade.get("signal_time") or trade.get("entry_time")))
        if at is None:
            continue
        i = at + int(decision_offset)
        if i < 0 or i >= len(candles):
            continue
        trend = trend_cache.get((tf, i))
        trend = trend if trend in _TREND_TEXT else None
        verdict = structure_verdict(str(trade.get("direction") or ""), trend)

        rvol = regime = None
        if i >= VOLUME_SMA_BARS:
            base = sum(volumes[i - VOLUME_SMA_BARS : i]) / VOLUME_SMA_BARS
            rvol = relative_volume(volumes[i], base)
            regime = volume_regime(rvol, RVOL_THRESHOLD)

        zone = position = None
        if i >= ZONE_WINDOW_BARS:
            top = max(highs[i - ZONE_WINDOW_BARS : i])
            bottom = min(lows[i - ZONE_WINDOW_BARS : i])
            if top > bottom:
                position = (closes[i] - bottom) / (top - bottom)
                zone = "LOW" if closes[i] < bottom + 0.5 * (top - bottom) else "HIGH"

        parts = [
            {"CORRECT": "Structure correct", "WRONG": "Structure wrong"}.get(verdict, "Structure neutral"),
            _TREND_TEXT.get(trend or "", "Trend unknown"),
            _VOLUME_TEXT.get(regime or "", "Volume unknown"),
            {"LOW": "Low zone", "HIGH": "High zone"}.get(zone or "", "Zone unknown"),
        ]
        trade["market_context"] = {
            "structure": verdict,
            "trend": trend,
            "volume": regime,
            "relative_volume": round(rvol, 3) if rvol is not None else None,
            "zone": zone,
            "range_position": round(position, 3) if position is not None else None,
            "decision_bar": _utc(candles[i].get("time")).isoformat(),
            "label": " · ".join(parts),
        }
        done += 1
    return done
