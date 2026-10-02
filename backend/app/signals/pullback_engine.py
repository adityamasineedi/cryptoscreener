"""Pullback / retracement detection after BOS + impulse."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.signals._candle_utils import ohlc
from app.signals.config import SignalConfig
from app.signals.schemas import PullbackState


def detect_pullback(
    candles: Sequence[Mapping[str, Any]],
    bos: dict[str, Any] | None,
    impulse: dict[str, Any] | None,
    config: SignalConfig,
    *,
    demand_zone: tuple[float, float] | None = None,
    supply_zone: tuple[float, float] | None = None,
    vwap: float | None = None,
    ema: float | None = None,
    as_of_index: int | None = None,
) -> dict[str, Any]:
    if not bos or bos.get("state") != "CONFIRMED":
        return {
            "pullback_state": PullbackState.WAITING.value,
            "reason": "Waiting for confirmed BOS",
        }
    if not impulse or not impulse.get("is_impulse"):
        return {
            "pullback_state": PullbackState.WAITING.value,
            "reason": "Waiting for valid impulse after BOS",
        }

    end = len(candles) - 1 if as_of_index is None else min(as_of_index, len(candles) - 1)
    origin = impulse.get("impulse_origin")
    imp_end = impulse.get("impulse_end")
    direction = impulse.get("direction")
    broken = bos.get("broken_level")
    if origin is None or imp_end is None or broken is None:
        return {
            "pullback_state": PullbackState.WAITING.value,
            "reason": "Missing impulse/BOS levels",
        }

    impulse_range = abs(float(imp_end) - float(origin))
    if impulse_range <= 0:
        return {
            "pullback_state": PullbackState.INVALIDATED.value,
            "reason": "Zero impulse range",
        }

    start = int(impulse.get("bar_index") or end)
    if start >= end:
        return {
            "pullback_state": PullbackState.WAITING.value,
            "reason": "Waiting for bars after impulse",
            "impulse_origin": origin,
            "impulse_end": imp_end,
        }

    # Zone confluence (optional — not all required)
    zones: list[tuple[str, float, float]] = [("structure", float(broken), float(broken))]
    if demand_zone and direction == "BULLISH":
        zones.append(("demand", float(demand_zone[0]), float(demand_zone[1])))
    if supply_zone and direction == "BEARISH":
        zones.append(("supply", float(supply_zone[0]), float(supply_zone[1])))
    if vwap is not None:
        zones.append(("vwap", float(vwap), float(vwap)))
    if ema is not None:
        zones.append(("ema", float(ema), float(ema)))

    retrace_extreme = None
    structure_intact = True
    for i in range(start + 1, end + 1):
        _, h, l, c = ohlc(candles, i)
        if direction == "BULLISH":
            retrace_extreme = l if retrace_extreme is None else min(retrace_extreme, l)
            if c < float(origin):
                structure_intact = False
        else:
            retrace_extreme = h if retrace_extreme is None else max(retrace_extreme, h)
            if c > float(origin):
                structure_intact = False

    if retrace_extreme is None:
        return {
            "pullback_state": PullbackState.WAITING.value,
            "reason": "No pullback bars yet",
            "impulse_origin": origin,
            "impulse_end": imp_end,
        }

    if direction == "BULLISH":
        retracement = (float(imp_end) - float(retrace_extreme)) / impulse_range
    else:
        retracement = (float(retrace_extreme) - float(imp_end)) / impulse_range

    if not structure_intact or retracement >= config.pullback_invalidation_retracement:
        return {
            "pullback_state": PullbackState.INVALIDATED.value,
            "impulse_origin": origin,
            "impulse_end": imp_end,
            "retracement_low": retrace_extreme if direction == "BULLISH" else None,
            "retracement_high": retrace_extreme if direction == "BEARISH" else None,
            "retracement_percentage": retracement * 100.0,
            "pullback_zone": zones,
            "structure_intact": False,
            "reason": "Pullback exceeded invalidation / broke impulse origin",
        }

    in_zone = False
    zone_hit = None
    _, h, l, c = ohlc(candles, end)
    for name, zlo, zhi in zones:
        lo, hi = (min(zlo, zhi), max(zlo, zhi))
        # widen tiny single-price levels slightly via retracement band
        if direction == "BULLISH" and l <= hi and c >= lo * 0.999:
            in_zone = True
            zone_hit = name
            break
        if direction == "BEARISH" and h >= lo and c <= hi * 1.001:
            in_zone = True
            zone_hit = name
            break

    if retracement < config.pullback_min_retracement:
        state = PullbackState.WAITING.value
        reason = f"Retracement {retracement:.1%} below minimum {config.pullback_min_retracement:.0%}"
    elif retracement > config.pullback_max_retracement:
        state = PullbackState.FAILED.value
        reason = f"Retracement {retracement:.1%} above maximum {config.pullback_max_retracement:.0%}"
    elif in_zone:
        state = PullbackState.CONFIRMED.value
        reason = f"Pullback into {zone_hit or 'zone'} at {retracement:.1%} retracement"
    else:
        state = PullbackState.ACTIVE.value
        reason = f"Active pullback {retracement:.1%} — awaiting zone interaction"

    return {
        "pullback_state": state,
        "impulse_origin": origin,
        "impulse_end": imp_end,
        "retracement_low": retrace_extreme if direction == "BULLISH" else None,
        "retracement_high": retrace_extreme if direction == "BEARISH" else None,
        "retracement_percentage": retracement * 100.0,
        "pullback_zone": [{"name": n, "low": a, "high": b} for n, a, b in zones],
        "structure_intact": structure_intact,
        "zone_hit": zone_hit,
        "reason": reason,
    }
