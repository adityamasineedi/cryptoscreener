"""Bar evaluator for supply/demand location plus staged structure confirmation.

Research hypothesis only. Existing COMBO evaluators are not used and are not
modified. OHLCV volume measures participation, not aggressive buy/sell delta.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Sequence

from app.research.bos_combinations import CombinationDefinition
from app.research.combination_engine import _clone_signal_config
from app.research.config import ResearchConfig
from app.research.bos_strategy_comparison.htf import timeframe_seconds
from app.research.sd_structure.liquidity import completed_equal_sweep
from app.research.sd_structure.sequence import (
    bos_after_choch,
    choch_at,
    director_allows,
    plain_bos,
    structure_swings,
    preceding_rvol,
    slot_rvol,
    range_expansion,
    retest_holds,
    structural_invalidation_without_bos,
    volume_divergence,
)
from app.research.sd_structure.variants import (
    RVOL_WINDOW,
    SEQUENCE_LOOKBACK,
    VOLUME_DIVERGENCE,
    VOLUME_EXPANSION,
    VOLUME_NONE,
    VOLUME_RVOL,
    SdVariant,
    variant_for,
)
from app.research.sd_structure.zones import (
    CausalZone,
    causal_zones,
    most_recent_touched,
    nearest_ahead,
)
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv
from app.signals.config import SignalConfig
from app.signals.risk_engine import risk_reward
from app.signals.schemas import SwingRecord
from app.signals.stop_engine import compute_stop
from app.signals.swing_detector import detect_swings, extend_swings
from app.signals.target_engine import compute_targets
from app.signals.trend_engine import infer_trend


def _no(combination_id: str, as_of_index: int, signal_time: str | None, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "NO_SETUP",
        "combination_id": combination_id,
        "as_of_index": as_of_index,
        "signal_time": signal_time,
        "reason": reason,
        "direction": extra.pop("direction", None),
        "gates": extra.pop("gates", {}),
        "required": [],
        "playbook": "SD_STRUCTURE",
        "sd_structure": extra or None,
        "note": "RESEARCH setup — not a live signal, not a trade command",
    }


def _signal_time(candles: Sequence[Mapping[str, Any]], index: int) -> str | None:
    if index < 0 or index >= len(candles):
        return None
    stamp = candle_time(candles[index])
    return stamp.isoformat() if stamp else None


# Raw swings for one candle list, advanced with extend_swings. The filtered
# structure view is rebuilt each call so a new inside bar or range still drops.
def clear_structure_cache() -> None:
    """Drop swing and timestamp caches. Call once per backtest series."""
    _SWING_STATE.clear()
    _OPEN_TIMES.clear()


_SWING_STATE: dict[
    tuple[int, str, int, int],
    tuple[int, list[SwingRecord], list[float], list[float], list[float]],
] = {}


_OPEN_TIMES: dict[int, list] = {}


def _open_times(candles: Sequence[Mapping[str, Any]]) -> list:
    key = id(candles)
    cached = _OPEN_TIMES.get(key)
    if cached is None or len(cached) != len(candles):
        cached = [candle_time(candle) for candle in candles]
        if len(_OPEN_TIMES) > 8:
            _OPEN_TIMES.clear()
        _OPEN_TIMES[key] = cached
    return cached


def _closed_end_index(
    setup_candles: Sequence[Mapping[str, Any]],
    setup_index: int,
    setup_timeframe: str,
    htf_candles: Sequence[Mapping[str, Any]],
    htf_timeframe: str,
) -> int | None:
    """Last HTF bar whose close is at or before the setup bar close."""
    if setup_index < 0 or setup_index >= len(setup_candles) or not htf_candles:
        return None
    open_ts = candle_time(setup_candles[setup_index])
    if open_ts is None:
        return None
    try:
        decision = open_ts + timedelta(seconds=timeframe_seconds(setup_timeframe))
        htf_seconds = timeframe_seconds(htf_timeframe)
    except (KeyError, ValueError):
        return None
    times = _open_times(htf_candles)
    lo = 0
    hi = len(times) - 1
    found = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        opened = times[mid]
        if opened is None:
            return None
        if opened + timedelta(seconds=htf_seconds) <= decision:
            found = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return found if found >= 0 else None


def _swings_as_of(
    candles: Sequence[Mapping[str, Any]],
    index: int,
    *,
    symbol: str,
    timeframe: str,
    signal_config: SignalConfig,
    supplied: Sequence[SwingRecord] | None,
) -> list[SwingRecord]:
    swing_cfg = signal_config.swing_for(timeframe)
    if supplied is not None:
        visible = [
            swing
            for swing in supplied
            if swing.bar_index + swing_cfg.swing_right_bars <= index
        ]
        if visible:
            return structure_swings(visible, candles)
    key = (id(candles), timeframe, swing_cfg.swing_left_bars, swing_cfg.swing_right_bars)
    state = _SWING_STATE.get(key)
    if state is not None and state[0] == index:
        detected = state[1]
        highs, lows, closes = state[2], state[3], state[4]
    elif state is not None and state[0] >= 0 and index > state[0]:
        detected = state[1]
        highs, lows, closes = state[2], state[3], state[4]
        for step in range(state[0] + 1, index + 1):
            detected = extend_swings(
                detected,
                candles,
                left=swing_cfg.swing_left_bars,
                right=swing_cfg.swing_right_bars,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=signal_config.atr_period,
                minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
                as_of_index=step,
                highs=highs,
                lows=lows,
                closes=closes,
            )
    else:
        _, highs, lows, closes, _ = series_ohlcv(list(candles))
        detected = detect_swings(
            candles,
            left=swing_cfg.swing_left_bars,
            right=swing_cfg.swing_right_bars,
            symbol=symbol,
            timeframe=timeframe,
            atr_period=signal_config.atr_period,
            minimum_swing_distance_atr=swing_cfg.minimum_swing_distance_atr,
            as_of_index=index,
            highs=highs,
            lows=lows,
            closes=closes,
        )
    if len(_SWING_STATE) > 6 and key not in _SWING_STATE:
        _SWING_STATE.clear()
    _SWING_STATE[key] = (index, detected, highs, lows, closes)
    return structure_swings(detected, candles)


def _closed_prefix(
    setup_candles: Sequence[Mapping[str, Any]],
    setup_index: int,
    setup_timeframe: str,
    htf_candles: Sequence[Mapping[str, Any]],
    htf_timeframe: str,
) -> list[Mapping[str, Any]] | None:
    """HTF bars whose close is at or before the setup bar close. No timestamps → None."""
    if setup_index < 0 or setup_index >= len(setup_candles) or not htf_candles:
        return None
    open_ts = candle_time(setup_candles[setup_index])
    if open_ts is None:
        return None
    try:
        decision = open_ts + timedelta(seconds=timeframe_seconds(setup_timeframe))
        htf_seconds = timeframe_seconds(htf_timeframe)
    except (KeyError, ValueError):
        return None
    kept: list[Mapping[str, Any]] = []
    for candle in htf_candles:
        opened = candle_time(candle)
        if opened is None:
            return None
        if opened + timedelta(seconds=htf_seconds) <= decision:
            kept.append(candle)
        else:
            break
    return kept


def last_band_touch(
    candles: Sequence[Mapping[str, Any]],
    *,
    low: float,
    high: float,
    start: int,
    end: int,
) -> int:
    last = -1
    for index in range(max(0, start), min(end, len(candles) - 1) + 1):
        _, bar_high, bar_low, _ = ohlc(candles, index)
        if bar_low <= high and bar_high >= low:
            last = index
    return last


def _location_zone(
    *,
    zone_type: str,
    candles: Sequence[Mapping[str, Any]],
    signal_index: int,
    timeframe: str,
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_4h: Sequence[Mapping[str, Any]] | None,
    candles_1d: Sequence[Mapping[str, Any]] | None,
    atr_period: int,
) -> tuple[CausalZone | None, str]:
    """Major zone on a closed 4h bar, else a closed 1d bar.

    5m, 15m, and 1h setup bars are not a location source. A 4h or 1d setup
    series is its own location. Higher-timeframe zones use only bars already
    closed at the signal.
    """
    touch_start = max(0, signal_index - SEQUENCE_LOOKBACK)
    touch_end = max(0, signal_index - 1)
    setup_tf = timeframe.lower()
    sources: list[tuple[str, Sequence[Mapping[str, Any]] | None, str]] = []
    if setup_tf != "4h":
        sources.append(("4h", candles_4h, "4h"))
    if setup_tf != "1d":
        sources.append(("1d", candles_1d, "1d"))
    for source, series, htf_tf in sources:
        if not series:
            continue
        end = _closed_end_index(candles, signal_index, timeframe, series, htf_tf)
        if end is None or end < 29:
            continue
        zones = causal_zones(series, end, atr_period=atr_period)
        htf_seconds = timeframe_seconds(htf_tf)
        candidates: list[CausalZone] = []
        for zone in zones:
            if zone.zone_type != zone_type or zone.broken:
                continue
            opened = candle_time(series[zone.departure_index])
            if opened is None:
                continue
            available = opened + timedelta(seconds=htf_seconds)
            touch_from = touch_start
            for index in range(touch_start, touch_end + 1):
                bar_open = candle_time(candles[index])
                if bar_open is not None and bar_open >= available:
                    touch_from = index
                    break
            else:
                continue
            if last_band_touch(
                candles,
                low=zone.low,
                high=zone.high,
                start=touch_from,
                end=touch_end,
            ) >= 0:
                candidates.append(zone)
        if candidates:
            return candidates[-1], source
    if setup_tf not in {"4h", "1d"}:
        return None, ""
    zones = causal_zones(candles, signal_index, atr_period=atr_period)
    zone = most_recent_touched(
        zones,
        candles,
        zone_type=zone_type,
        touch_end=touch_end,
        touch_start=touch_start,
    )
    return zone, "setup"


def _htf_trend_label(
    setup_candles: Sequence[Mapping[str, Any]],
    setup_index: int,
    setup_timeframe: str,
    htf_candles: Sequence[Mapping[str, Any]] | None,
    *,
    symbol: str,
    htf_timeframe: str,
    signal_config: SignalConfig,
) -> str | None:
    """Closed higher-timeframe trend after inside-bar and range filters."""
    if not htf_candles:
        return None
    end = _closed_end_index(
        setup_candles, setup_index, setup_timeframe, htf_candles, htf_timeframe
    )
    if end is None or end < 29:
        return None
    swings = _swings_as_of(
        htf_candles,
        end,
        symbol=symbol,
        timeframe=htf_timeframe,
        signal_config=signal_config,
        supplied=None,
    )
    if not swings:
        return None
    return str(infer_trend(swings).get("trend") or "") or None


def _volume_ok(
    variant: SdVariant,
    candles: Sequence[Mapping[str, Any]],
    swings: Sequence[SwingRecord],
    *,
    direction: str,
    volume_index: int,
) -> tuple[bool, float | None]:
    if variant.volume_mode in {VOLUME_RVOL, VOLUME_EXPANSION}:
        rvol = slot_rvol(candles, volume_index)
    else:
        rvol = preceding_rvol(candles, volume_index, window=RVOL_WINDOW)
    if variant.volume_mode == VOLUME_NONE:
        return True, rvol
    if variant.volume_mode == VOLUME_RVOL:
        return rvol is not None and rvol + 1e-12 >= float(variant.rvol_min or 0), rvol
    if variant.volume_mode == VOLUME_EXPANSION:
        rvol_ok = rvol is not None and rvol + 1e-12 >= float(variant.rvol_min or 0)
        return rvol_ok and range_expansion(candles, volume_index, window=RVOL_WINDOW), rvol
    if variant.volume_mode == VOLUME_DIVERGENCE:
        return volume_divergence(swings, candles, direction=direction, as_of_index=volume_index), rvol
    return False, rvol


def _find_setup(
    *,
    variant: SdVariant,
    candles: Sequence[Mapping[str, Any]],
    signal_index: int,
    swings: Sequence[SwingRecord],
    zone: CausalZone,
    zone_source: str,
    symbol: str,
    timeframe: str,
    right_bars: int,
) -> dict[str, Any] | None:
    direction = "LONG" if zone.zone_type == "demand" else "SHORT"
    want = "CHOCH_BULLISH" if direction == "LONG" else "CHOCH_BEARISH"
    earliest = max(1, signal_index - SEQUENCE_LOOKBACK)
    touch_at = last_band_touch(
        candles,
        low=zone.low,
        high=zone.high,
        start=earliest if zone_source != "setup" else max(zone.departure_index + 1, earliest),
        end=max(0, signal_index - 1),
    )
    if touch_at < 0:
        return None

    def choch_between(start: int, end: int) -> dict[str, Any] | None:
        for index in range(end, start - 1, -1):
            event = choch_at(
                candles, swings, index, symbol=symbol, timeframe=timeframe, right_bars=right_bars
            )
            if event and event.get("direction") == want and int(event["confirmation_candle"]) > touch_at:
                return event
        return None

    if variant.require_retest:
        if not variant.require_bos:
            return None
        for bos_index in range(signal_index - 1, earliest - 1, -1):
            event = choch_between(earliest, bos_index - 1) if variant.require_choch else {"confirmation_candle": earliest, "level": zone.high if direction == "LONG" else zone.low}
            if variant.require_choch and event is None:
                continue
            assert event is not None
            bos = bos_after_choch(
                candles,
                swings,
                choch_index=int(event["confirmation_candle"]),
                choch_level=float(event["level"]),
                direction=direction,
                right_bars=right_bars,
                bos_index=bos_index,
            )
            if bos is None:
                continue
            if retest_holds(candles, direction=direction, level=float(bos["level"]), index=signal_index):
                return {
                    "direction": direction,
                    "choch": event if variant.require_choch else None,
                    "bos": bos,
                    "volume_index": bos_index,
                    "invalidation": float(bos["invalidation"]),
                }
        return None

    if variant.require_bos:
        event = choch_between(earliest, signal_index - 1) if variant.require_choch else None
        if variant.require_choch and event is None:
            return None
        if event is None:
            bos = plain_bos(
                candles,
                swings,
                direction=direction,
                right_bars=right_bars,
                bos_index=signal_index,
                beyond=zone.high if direction == "LONG" else zone.low,
            )
        else:
            choch_index = int(event["confirmation_candle"])
            choch_level = float(event["level"])
            bos = bos_after_choch(
                candles,
                swings,
                choch_index=choch_index,
                choch_level=choch_level,
                direction=direction,
                right_bars=right_bars,
                bos_index=signal_index,
            )
        if bos is None:
            return None
        return {
            "direction": direction,
            "choch": event,
            "bos": bos,
            "volume_index": signal_index,
            "invalidation": float(bos["invalidation"]),
        }

    event = choch_at(
        candles, swings, signal_index, symbol=symbol, timeframe=timeframe, right_bars=right_bars
    )
    if event is None or event.get("direction") != want:
        return None
    if int(event["confirmation_candle"]) <= touch_at:
        return None
    invalidation = structural_invalidation_without_bos(
        swings, direction=direction, choch_index=signal_index
    )
    if invalidation is None:
        return None
    return {
        "direction": direction,
        "choch": event,
        "bos": None,
        "volume_index": signal_index,
        "invalidation": invalidation,
    }


def _room_to_opposing_zone(
    *,
    direction: str,
    entry: float,
    stop: float,
    opposing: CausalZone | None,
    min_rr: float,
) -> bool:
    if opposing is None:
        return True
    risk = abs(entry - stop)
    if risk <= 0:
        return False
    if direction == "LONG":
        reward = opposing.low - entry
    else:
        reward = entry - opposing.high
    if reward <= 0:
        return False
    return (reward / risk) + 1e-12 >= min_rr


def _resolve_trigger(
    candles: Sequence[Mapping[str, Any]],
    signal_index: int,
    timeframe: str,
    candles_15m: Sequence[Mapping[str, Any]] | None,
    candles_5m: Sequence[Mapping[str, Any]] | None,
) -> tuple[Sequence[Mapping[str, Any]] | None, str, int]:
    """CHoCH and the protective swing live on 15m, else 5m.

    A 15m or 5m setup series is already that trigger.
    """
    setup_tf = timeframe.lower()
    if setup_tf in {"5m", "15m"}:
        return candles, setup_tf, signal_index
    for name, series in (("15m", candles_15m), ("5m", candles_5m)):
        if not series:
            continue
        end = _closed_end_index(candles, signal_index, timeframe, series, name)
        if end is not None and end >= 29:
            return series, name, end
    return None, "", -1


def _director_trend(
    candles: Sequence[Mapping[str, Any]],
    signal_index: int,
    timeframe: str,
    *,
    symbol: str,
    signal_config: SignalConfig,
    candles_4h: Sequence[Mapping[str, Any]] | None,
    candles_1d: Sequence[Mapping[str, Any]] | None,
) -> str | None:
    """4h trend, or the 1d trend when 4h is the setup or 4h is unavailable."""
    setup_tf = timeframe.lower()
    if setup_tf == "1d":
        return _htf_trend_label(
            candles, signal_index, timeframe, candles, symbol=symbol,
            htf_timeframe="1d", signal_config=signal_config,
        )
    if setup_tf == "4h":
        return _htf_trend_label(
            candles, signal_index, timeframe, candles, symbol=symbol,
            htf_timeframe="4h", signal_config=signal_config,
        )
    trend = None
    if candles_4h:
        trend = _htf_trend_label(
            candles, signal_index, timeframe, candles_4h, symbol=symbol,
            htf_timeframe="4h", signal_config=signal_config,
        )
    if trend:
        return trend
    if candles_1d:
        return _htf_trend_label(
            candles, signal_index, timeframe, candles_1d, symbol=symbol,
            htf_timeframe="1d", signal_config=signal_config,
        )
    return None


def evaluate_sd_structure_at_bar(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    combination: CombinationDefinition,
    signal_config: SignalConfig,
    research_config: ResearchConfig,
    swings: Sequence[Any] | None = None,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    candles_15m: Sequence[Mapping[str, Any]] | None = None,
    candles_5m: Sequence[Mapping[str, Any]] | None = None,
    candles_1d: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Confirm on the prior bar and price the entry at this bar's open.

    The decision does not read this bar's high, low, or close.
    """
    variant = variant_for(combination.combination_id)
    local_cfg = _clone_signal_config(signal_config, research_config, require_htf_alignment=False)
    entry_index = min(as_of_index, len(candles) - 1)
    signal_index = entry_index - 1
    signal_time = _signal_time(candles, signal_index)
    if signal_index < 1:
        return _no(combination.combination_id, as_of_index, signal_time, "NOT_ENOUGH_BARS")

    entry_open = ohlc(candles, entry_index)[0]
    trigger, trigger_tf, trigger_index = _resolve_trigger(
        candles, signal_index, timeframe, candles_15m, candles_5m
    )
    if trigger is None or trigger_index < 1:
        return _no(combination.combination_id, as_of_index, signal_time, "LTF_TRIGGER_UNAVAILABLE")
    director = _director_trend(
        candles,
        signal_index,
        timeframe,
        symbol=symbol,
        signal_config=local_cfg,
        candles_4h=candles_4h,
        candles_1d=candles_1d,
    )
    if not director_allows("LONG", director) and not director_allows("SHORT", director):
        return _no(combination.combination_id, as_of_index, signal_time, "HTF_UNAVAILABLE")
    swing_cfg = local_cfg.swing_for(trigger_tf)
    swing_list = _swings_as_of(
        trigger,
        trigger_index,
        symbol=symbol,
        timeframe=trigger_tf,
        signal_config=local_cfg,
        supplied=list(swings) if swings is not None and trigger is candles else None,
    )

    attempts: list[tuple[str, str]] = [("demand", "LONG"), ("supply", "SHORT")]
    rejection = "NO_ZONE_OR_SEQUENCE"
    for zone_type, _direction in attempts:
        zone, zone_source = _location_zone(
            zone_type=zone_type,
            candles=candles,
            signal_index=signal_index,
            timeframe=timeframe,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
            candles_1d=candles_1d,
            atr_period=local_cfg.atr_period,
        )
        if zone is None:
            continue
        if variant.fresh_zone_only and not zone.fresh:
            rejection = "ZONE_NOT_FRESH"
            continue
        matched = _find_setup(
            variant=variant,
            candles=trigger,
            signal_index=trigger_index,
            swings=swing_list,
            zone=zone,
            zone_source=zone_source,
            symbol=symbol,
            timeframe=trigger_tf,
            right_bars=swing_cfg.swing_right_bars,
        )
        if matched is None:
            rejection = "SEQUENCE_INCOMPLETE"
            continue
        direction = str(matched["direction"])
        volume_ok, rvol = _volume_ok(
            variant,
            trigger,
            swing_list,
            direction=direction,
            volume_index=int(matched["volume_index"]),
        )
        if not volume_ok:
            rejection = "VOLUME_FILTER"
            continue
        liquidity = None
        if variant.require_liquidity_sweep:
            choch = matched.get("choch")
            before = (
                int(choch["confirmation_candle"])
                if choch is not None
                else int(matched["volume_index"])
            )
            liquidity = completed_equal_sweep(
                trigger,
                swing_list,
                direction=direction,
                before_index=before,
                right_bars=swing_cfg.swing_right_bars,
            )
            if liquidity is None:
                rejection = "LIQUIDITY_SWEEP_MISSING"
                continue
        if not director_allows(direction, director):
            rejection = "HTF_OPPOSES"
            continue

        setup_zones = causal_zones(candles, signal_index, atr_period=local_cfg.atr_period)
        opposing = nearest_ahead(setup_zones, direction=direction, entry=float(entry_open))
        pullback: dict[str, Any]
        if direction == "LONG":
            pullback = {"retracement_low": float(matched["invalidation"])}
        else:
            pullback = {"retracement_high": float(matched["invalidation"])}
        # Zone location is not passed into the stop. The example stop sits
        # under the post-CHoCH higher low, which can be inside or above demand.
        atr = None
        from app.engines.mtf.indicators import atr as calc_atr

        highs = [float(c.get("high") or c.get("h") or 0) for c in trigger[: trigger_index + 1]]
        lows = [float(c.get("low") or c.get("l") or 0) for c in trigger[: trigger_index + 1]]
        closes = [float(c.get("close") or c.get("c") or 0) for c in trigger[: trigger_index + 1]]
        atr = calc_atr(highs, lows, closes, local_cfg.atr_period)
        stop = compute_stop(
            direction=direction,
            entry_price=float(entry_open),
            pullback=pullback,
            demand_zone=None,
            supply_zone=None,
            atr=float(atr) if atr else None,
            sl_buffer_atr=local_cfg.sl_buffer_atr,
        )
        if stop.get("final_stop") is None:
            rejection = "STOP_UNAVAILABLE"
            continue
        final_stop = float(stop["final_stop"])
        if direction == "LONG" and final_stop >= float(entry_open):
            rejection = "STOP_INVALID"
            continue
        if direction == "SHORT" and final_stop <= float(entry_open):
            rejection = "STOP_INVALID"
            continue
        if not _room_to_opposing_zone(
            direction=direction,
            entry=float(entry_open),
            stop=final_stop,
            opposing=opposing,
            min_rr=float(research_config.min_rr),
        ):
            rejection = "OPPOSING_ZONE_TOO_CLOSE"
            continue

        targets = compute_targets(
            direction=direction,
            entry_price=float(entry_open),
            stop=stop,
            swings=swing_list,
            supply_zone=opposing.band() if opposing and direction == "LONG" else None,
            demand_zone=opposing.band() if opposing and direction == "SHORT" else None,
            config=local_cfg,
        )
        if not targets:
            rejection = "NO_TARGET"
            continue
        rr = risk_reward(
            float(entry_open),
            final_stop,
            targets,
            min_rr=research_config.min_rr,
        )
        tp1_r = rr.get("TP1_R") or rr.get("first_target_R")
        if tp1_r is None or float(tp1_r) + 1e-12 < float(research_config.min_rr):
            rejection = "RR_BELOW_MINIMUM"
            continue
        tps = [float(item["target_price"]) for item in targets if item.get("target_price") is not None]
        while len(tps) < 3:
            tps.append(None)  # type: ignore[arg-type]
        status = "LONG_ENTRY_CANDIDATE" if direction == "LONG" else "SHORT_ENTRY_CANDIDATE"
        diagnostics = {
            "variant": variant.variant_id,
            "zone_source": zone_source,
            "zone_type": zone.zone_type,
            "zone_low": zone.low,
            "zone_high": zone.high,
            "zone_touch_count": zone.touch_count,
            "zone_age_bars": zone.age_bars,
            "zone_fresh": zone.fresh,
            "zone_distance": abs(((zone.low + zone.high) / 2.0) - float(entry_open)),
            "rvol": rvol,
            "volume_mode": variant.volume_mode,
            "choch_level": None if matched["choch"] is None else matched["choch"].get("level"),
            "bos_level": None if matched["bos"] is None else matched["bos"].get("level"),
            "invalidation": matched["invalidation"],
            "entry_type": "NEXT_OPEN",
            "trigger_timeframe": trigger_tf,
            "director_trend": director,
            "market_regime": (
                "trend" if director in {"BULLISH", "BEARISH"} else "range"
                if director == "NEUTRAL"
                else "unknown"
            ),
            "setup_class": (
                "continuation_retest"
                if variant.require_retest
                else "continuation_breakout"
                if variant.require_bos and not variant.require_choch
                else "reversal"
            ),
            "market_type": "perpetual",
            "session": "24h",
            "contract_roll": "none",
            "max_hold_bars": 48,
            "trend_1h": _htf_trend_label(
                candles,
                signal_index,
                timeframe,
                candles_1h,
                symbol=symbol,
                htf_timeframe="1h",
                signal_config=local_cfg,
            ),
            "trend_4h": _htf_trend_label(
                candles,
                signal_index,
                timeframe,
                candles_4h,
                symbol=symbol,
                htf_timeframe="4h",
                signal_config=local_cfg,
            ),
            **(
                {
                    "liquidity_event": liquidity.get("event"),
                    "liquidity_level": liquidity.get("level"),
                    "liquidity_sweep_index": liquidity.get("sweep_index"),
                }
                if liquidity is not None
                else {}
            ),
            "volume_note": (
                "RVOL and expansion use the same UTC clock slot, at least 8 prior "
                "occurrences. Range expansion still compares the candle range with "
                "the previous 20 bars. OHLCV volume is participation, not delta."
            ),
            "risk_note": (
                "Per-trade equity risk, leverage, funding, and portfolio correlation "
                "are account constraints and are not applied inside this bar backtest."
            ),
        }
        return {
            "status": status,
            "direction": direction,
            "entry_price": float(entry_open),
            "entry_type": "NEXT_OPEN",
            "stop_price": final_stop,
            "stop": stop,
            "targets": targets,
            "tp1": tps[0],
            "tp2": tps[1] if len(tps) > 1 else None,
            "tp3": tps[2] if len(tps) > 2 else None,
            "rr": rr.get("best_R"),
            "risk_reward": rr,
            "gates": {
                "zone": True,
                "choch": matched["choch"] is not None,
                "bos": matched["bos"] is not None,
                "retest": variant.require_retest,
                "volume": volume_ok,
                **(
                    {"liquidity_sweep": True}
                    if variant.require_liquidity_sweep
                    else {}
                ),
                "rr": True,
            },
            "combination_id": combination.combination_id,
            "signal_time": signal_time,
            "as_of_index": as_of_index,
            "playbook": "SD_STRUCTURE",
            "sd_structure": diagnostics,
            "condition_definition": combination.to_dict(),
            "note": "RESEARCH setup — not a live signal, not a trade command",
        }

    return _no(
        combination.combination_id,
        as_of_index,
        signal_time,
        rejection,
    )
