from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping, Sequence

from .catalog import registration
from .features import FeatureSeries, build_features
from .models import ScalpCandidate


@dataclass
class _Zone:
    direction: str
    low: float
    high: float
    activated_at: Any
    source_index: int
    touched: bool = False


class ScalpingStrategyEngine:
    """Pure point-in-time engine used by replay and screener evaluation.

    Input timestamps are candle *open* times in UTC.  Inputs must contain only
    fully closed bars.  Equality does not constitute a buffered structural
    break; touches and stop/target fills are inclusive.
    """

    def evaluate(
        self,
        *,
        symbol: str,
        candles_1m: Sequence[Mapping[str, Any]],
        candles_5m: Sequence[Mapping[str, Any]],
        candles_15m: Sequence[Mapping[str, Any]],
        include_pending_entry: bool = False,
    ) -> list[ScalpCandidate]:
        one = build_features(candles_1m, "1m")
        five = build_features(candles_5m, "5m")
        fifteen = build_features(candles_15m, "15m")
        out = [
            *self._bos_retest(symbol, one, fifteen, include_pending_entry),
            *self._sweep_reclaim(symbol, one, five, fifteen, include_pending_entry),
            *self._vwap_pullback(symbol, one, fifteen, include_pending_entry),
        ]
        return sorted(out, key=lambda c: (c.signal_time, c.strategy_id, c.variant))

    @staticmethod
    def _trend_at(fifteen: FeatureSeries, close_at: Any) -> str:
        idx = fifteen.closed_index_at(close_at)
        return fifteen.trend(idx) if idx is not None else "INSUFFICIENT_DATA"

    @staticmethod
    def _candidate(
        *,
        strategy_id: str,
        variant: str,
        symbol: str,
        direction: str,
        one: FeatureSeries,
        signal_index: int,
        stop: float,
        frozen_atr: float,
        hold: int,
        evidence: dict[str, Any],
        include_pending: bool,
    ) -> ScalpCandidate | None:
        registration(strategy_id, variant)
        entry_index = signal_index + 1
        if entry_index < len(one.candles):
            entry = one.opens[entry_index]
            entry_time = one.times[entry_index]
        elif include_pending and entry_index == len(one.candles):
            entry = None
            entry_time = None
        else:
            return None
        if entry is not None:
            risk = entry - stop if direction == "LONG" else stop - entry
            if risk <= 0:
                return None
            target = entry + 1.5 * risk if direction == "LONG" else entry - 1.5 * risk
        else:
            target = None
        return ScalpCandidate(
            strategy_id=strategy_id,
            variant=variant,
            symbol=symbol.upper(),
            direction=direction,
            signal_index=signal_index,
            signal_time=one.closes_at[signal_index],
            entry_index=entry_index,
            entry_time=entry_time,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            max_holding_minutes=hold,
            frozen_atr=frozen_atr,
            evidence={**evidence, "entry_rule": "NEXT_1M_OPEN", "target_r": 1.5},
        )

    def _bos_retest(
        self,
        symbol: str,
        one: FeatureSeries,
        fifteen: FeatureSeries,
        include_pending: bool,
    ) -> list[ScalpCandidate]:
        out: list[ScalpCandidate] = []
        broken: set[tuple[str, int]] = set()
        for i in range(20, len(one.candles)):
            atr = one.atr[i - 1]
            if atr is None or atr <= 0:
                continue
            trend = self._trend_at(fifteen, one.closes_at[i])
            sides = (("LONG", "HIGH", "BULLISH"), ("SHORT", "LOW", "BEARISH"))
            for direction, swing_side, needed_trend in sides:
                swings = one.confirmed_swings(i - 1, swing_side)
                swings = [s for s in swings if (swing_side, s.bar_index) not in broken]
                if not swings:
                    continue
                swing = swings[-1]
                level = float(swing.price)
                is_break = (
                    one.closes[i] > level + 0.1 * atr
                    if direction == "LONG"
                    else one.closes[i] < level - 0.1 * atr
                )
                if not is_break or trend != needed_trend:
                    continue
                broken.add((swing_side, swing.bar_index))
                body = abs(one.closes[i] - one.opens[i])
                if body < 0.8 * atr or (one.rvol[i] or 0.0) < 1.5:
                    continue
                # A missing bar expires this sequence; elapsed candle counts are
                # not silently compressed.
                for j in range(i + 1, min(i + 6, len(one.candles))):
                    if one.segment[j] != one.segment[i]:
                        break
                    close_invalid = (
                        one.closes[j] < level - 0.2 * atr
                        if direction == "LONG"
                        else one.closes[j] > level + 0.2 * atr
                    )
                    if close_invalid:
                        break
                    touches = one.lows[j] <= level + 0.15 * atr and one.highs[j] >= level - 0.15 * atr
                    if not touches:
                        continue
                    passes = (
                        one.closes[j] > level and one.closes[j] > one.opens[j]
                        if direction == "LONG"
                        else one.closes[j] < level and one.closes[j] < one.opens[j]
                    )
                    if passes:
                        anchor = min(one.lows[j], level) if direction == "LONG" else max(one.highs[j], level)
                        stop = anchor - 0.2 * atr if direction == "LONG" else anchor + 0.2 * atr
                        candidate = self._candidate(
                            strategy_id="scalp_bos_retest_v1",
                            variant="default",
                            symbol=symbol,
                            direction=direction,
                            one=one,
                            signal_index=j,
                            stop=stop,
                            frozen_atr=atr,
                            hold=30,
                            evidence={
                                "broken_level": level,
                                "breakout_index": i,
                                "retest_index": j,
                                "breakout_rvol": one.rvol[i],
                            },
                            include_pending=include_pending,
                        )
                        if candidate:
                            out.append(candidate)
                    # First touch consumes the setup whether it passes or fails.
                    break
        return out

    @staticmethod
    def _zones(five: FeatureSeries) -> list[_Zone]:
        zones: list[_Zone] = []
        broken: set[tuple[str, int]] = set()
        for i in range(1, len(five.candles)):
            atr = five.atr[i - 1]
            if atr is None or atr <= 0:
                continue
            trend = five.trend(i - 1)
            for direction, side, needed in (("LONG", "HIGH", "BULLISH"), ("SHORT", "LOW", "BEARISH")):
                available = [s for s in five.confirmed_swings(i - 1, side) if (side, s.bar_index) not in broken]
                if not available or trend != needed:
                    continue
                swing = available[-1]
                level = float(swing.price)
                crossed = five.closes[i] > level + 0.1 * atr if direction == "LONG" else five.closes[i] < level - 0.1 * atr
                if not crossed:
                    continue
                broken.add((side, swing.bar_index))
                if abs(five.closes[i] - five.opens[i]) < atr:
                    continue
                prior = range(max(0, i - 5), i)
                opposite = [
                    j for j in prior
                    if (five.closes[j] < five.opens[j] if direction == "LONG" else five.closes[j] > five.opens[j])
                ]
                if not opposite:
                    continue
                source = opposite[-1]
                zones.append(
                    _Zone(
                        direction=direction,
                        low=five.lows[source],
                        high=five.highs[source],
                        activated_at=five.closes_at[i],
                        source_index=i,
                    )
                )
        return zones

    def _sweep_reclaim(
        self,
        symbol: str,
        one: FeatureSeries,
        five: FeatureSeries,
        fifteen: FeatureSeries,
        include_pending: bool,
    ) -> list[ScalpCandidate]:
        out: list[ScalpCandidate] = []
        zones = self._zones(five)
        for i in range(20, len(one.candles)):
            atr = one.atr[i - 1]
            if atr is None or atr <= 0:
                continue
            now = one.closes_at[i]
            active: list[_Zone] = []
            for zone in zones:
                if zone.touched or zone.activated_at >= now or now - zone.activated_at > timedelta(hours=24):
                    continue
                five_idx = five.closed_index_at(now)
                if five_idx is None:
                    continue
                since = zone.source_index + 1
                invalid = any(
                    (five.closes[k] < zone.low if zone.direction == "LONG" else five.closes[k] > zone.high)
                    for k in range(since, five_idx + 1)
                )
                if invalid:
                    continue
                if one.lows[i] <= zone.high and one.highs[i] >= zone.low:
                    active.append(zone)
            if not active:
                continue
            # Newest qualifying zone owns an overlapping touch.  All touched
            # overlapping zones are consumed so an old zone cannot fire later.
            active.sort(key=lambda z: z.activated_at)
            zone = active[-1]
            for touched in active:
                touched.touched = True
            direction = zone.direction
            needed_15m = "BULLISH" if direction == "LONG" else "BEARISH"
            needed_pre = "BEARISH" if direction == "LONG" else "BULLISH"
            if self._trend_at(fifteen, now) != needed_15m or one.trend(i - 1) != needed_pre:
                continue
            side = "LOW" if direction == "LONG" else "HIGH"
            swings = one.confirmed_swings(i - 1, side)
            opposing = one.confirmed_swings(i, "HIGH" if direction == "LONG" else "LOW")
            if not swings or not opposing or (one.rvol[i] or 0.0) < 1.5:
                continue
            swept = swings[-1]
            level = float(swept.price)
            reclaim = (
                one.lows[i] <= level - 0.1 * atr and one.closes[i] > level
                if direction == "LONG"
                else one.highs[i] >= level + 0.1 * atr and one.closes[i] < level
            )
            if not reclaim:
                continue
            sweep_extreme = one.lows[i] if direction == "LONG" else one.highs[i]
            stop = sweep_extreme - 0.2 * atr if direction == "LONG" else sweep_extreme + 0.2 * atr
            common = {
                "zone_low": zone.low,
                "zone_high": zone.high,
                "swept_level": level,
                "sweep_index": i,
            }
            reclaim_only = self._candidate(
                strategy_id="scalp_sweep_reclaim_v1",
                variant="reclaim_only",
                symbol=symbol,
                direction=direction,
                one=one,
                signal_index=i,
                stop=stop,
                frozen_atr=atr,
                hold=20,
                evidence=common,
                include_pending=include_pending,
            )
            if reclaim_only:
                out.append(reclaim_only)
            opposing_level = float(opposing[-1].price)
            for j in range(i + 1, min(i + 4, len(one.candles))):
                if one.segment[j] != one.segment[i]:
                    break
                breached = one.lows[j] < sweep_extreme if direction == "LONG" else one.highs[j] > sweep_extreme
                if breached:
                    break
                confirmed = (
                    one.closes[j] > opposing_level + 0.1 * atr
                    if direction == "LONG"
                    else one.closes[j] < opposing_level - 0.1 * atr
                )
                if confirmed:
                    candidate = self._candidate(
                        strategy_id="scalp_sweep_reclaim_v1",
                        variant="reclaim_choch",
                        symbol=symbol,
                        direction=direction,
                        one=one,
                        signal_index=j,
                        stop=stop,
                        frozen_atr=atr,
                        hold=20,
                        evidence={**common, "opposing_swing": opposing_level, "choch_index": j},
                        include_pending=include_pending,
                    )
                    if candidate:
                        out.append(candidate)
                    break
        return out

    def _vwap_pullback(
        self,
        symbol: str,
        one: FeatureSeries,
        fifteen: FeatureSeries,
        include_pending: bool,
    ) -> list[ScalpCandidate]:
        out: list[ScalpCandidate] = []
        i = 20
        while i < len(one.candles):
            atr = one.atr[i]
            vwap = one.vwap[i]
            minute = one.times[i].hour * 60 + one.times[i].minute
            if atr is None or atr <= 0 or vwap is None or minute < 30 or i < 10:
                i += 1
                continue
            trend = self._trend_at(fifteen, one.closes_at[i])
            for direction, needed in (("LONG", "BULLISH"), ("SHORT", "BEARISH")):
                if trend != needed or one.segment[i - 10] != one.segment[i]:
                    continue
                old_vwap = one.vwap[i - 10]
                if old_vwap is None:
                    continue
                slope_ok = vwap > old_vwap if direction == "LONG" else vwap < old_vwap
                prior_ok = all(
                    one.vwap[k] is not None
                    and (one.closes[k] > one.vwap[k] if direction == "LONG" else one.closes[k] < one.vwap[k])
                    for k in range(i - 3, i)
                )
                touch = one.lows[i] <= vwap if direction == "LONG" else one.highs[i] >= vwap
                close_ok = one.closes[i] > vwap if direction == "LONG" else one.closes[i] < vwap
                if not (slope_ok and prior_ok and touch and close_ok):
                    continue
                pull_high, pull_low = one.highs[i], one.lows[i]
                for j in range(i + 1, min(i + 4, len(one.candles))):
                    if one.segment[j] != one.segment[i]:
                        break
                    invalid = one.closes[j] < pull_low if direction == "LONG" else one.closes[j] > pull_high
                    if invalid:
                        break
                    confirmed = one.closes[j] > pull_high if direction == "LONG" else one.closes[j] < pull_low
                    if confirmed and (one.rvol[j] or 0.0) >= 1.2:
                        extreme = min(one.lows[i : j + 1]) if direction == "LONG" else max(one.highs[i : j + 1])
                        stop = extreme - 0.2 * atr if direction == "LONG" else extreme + 0.2 * atr
                        candidate = self._candidate(
                            strategy_id="scalp_vwap_pullback_v1",
                            variant="default",
                            symbol=symbol,
                            direction=direction,
                            one=one,
                            signal_index=j,
                            stop=stop,
                            frozen_atr=atr,
                            hold=30,
                            evidence={
                                "pullback_index": i,
                                "confirmation_index": j,
                                "pullback_vwap": vwap,
                                "confirmation_rvol": one.rvol[j],
                            },
                            include_pending=include_pending,
                        )
                        if candidate:
                            out.append(candidate)
                        break
            i += 1
        return out
