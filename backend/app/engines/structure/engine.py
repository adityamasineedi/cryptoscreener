"""Market structure detection — swings, BOS, CHOCH, sweeps, breakouts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from app.engines.mtf.indicators import atr as calc_atr


class TrendBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGE = "range"


class SwingLabel(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"


@dataclass
class SwingPoint:
    index: int
    kind: str  # "high" | "low"
    price: float
    candle_time: datetime | None
    label: SwingLabel | None = None


@dataclass
class StructureEvent:
    symbol: str
    timeframe: str
    event_type: str
    price: float
    candle_time: datetime | None
    strength: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "event_type": self.event_type,
            "price": self.price,
            "candle_time": self.candle_time,
            "strength": self.strength,
            "evidence": self.evidence,
        }


def _candle_time(candle: Mapping[str, Any]) -> datetime | None:
    raw = candle.get("time") or candle.get("timestamp") or candle.get("open_time")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _ohlc(candles: list[Mapping[str, Any]], i: int) -> tuple[float, float, float, float]:
    c = candles[i]
    o = float(c.get("open") or c.get("o") or 0)
    h = float(c.get("high") or c.get("h") or 0)
    l = float(c.get("low") or c.get("l") or 0)
    cl = float(c.get("close") or c.get("c") or 0)
    return o, h, l, cl


def detect_swings(
    candles: list[Mapping[str, Any]],
    swing_left: int,
    swing_right: int,
) -> list[SwingPoint]:
    """Swing high/low when extremum is strict vs neighbors in left/right windows."""
    n = len(candles)
    if n == 0 or swing_left < 1 or swing_right < 1:
        return []
    swings: list[SwingPoint] = []
    for i in range(swing_left, n - swing_right):
        _, h, l, _ = _ohlc(candles, i)
        left_h = [_ohlc(candles, j)[1] for j in range(i - swing_left, i)]
        right_h = [_ohlc(candles, j)[1] for j in range(i + 1, i + swing_right + 1)]
        left_l = [_ohlc(candles, j)[2] for j in range(i - swing_left, i)]
        right_l = [_ohlc(candles, j)[2] for j in range(i + 1, i + swing_right + 1)]
        if h > max(left_h + right_h):
            swings.append(
                SwingPoint(
                    index=i,
                    kind="high",
                    price=h,
                    candle_time=_candle_time(candles[i]),
                )
            )
        if l < min(left_l + right_l):
            swings.append(
                SwingPoint(
                    index=i,
                    kind="low",
                    price=l,
                    candle_time=_candle_time(candles[i]),
                )
            )
    swings.sort(key=lambda s: s.index)
    return swings


def label_swings(swings: list[SwingPoint]) -> list[SwingPoint]:
    last_high: float | None = None
    last_low: float | None = None
    labeled: list[SwingPoint] = []
    for sp in swings:
        if sp.kind == "high":
            if last_high is None:
                sp.label = SwingLabel.HH
            elif sp.price > last_high:
                sp.label = SwingLabel.HH
            else:
                sp.label = SwingLabel.LH
            last_high = sp.price
        else:
            if last_low is None:
                sp.label = SwingLabel.HL
            elif sp.price > last_low:
                sp.label = SwingLabel.HL
            else:
                sp.label = SwingLabel.LL
            last_low = sp.price
        labeled.append(sp)
    return labeled


def infer_trend(swings: list[SwingPoint]) -> TrendBias:
    highs = [s for s in swings if s.kind == "high" and s.label]
    lows = [s for s in swings if s.kind == "low" and s.label]
    if len(highs) < 2 or len(lows) < 2:
        return TrendBias.RANGE
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    if h2.label == SwingLabel.HH and l2.label == SwingLabel.HL:
        return TrendBias.BULLISH
    if h2.label == SwingLabel.LH and l2.label == SwingLabel.LL:
        return TrendBias.BEARISH
    if h1.price == h2.price and l1.price == l2.price:
        return TrendBias.RANGE
    return TrendBias.RANGE


def _min_break_distance(price: float, pct: float) -> float:
    return abs(price) * (pct / 100.0)


class StructureEngine:
    """Deterministic market-structure events from OHLCV candles."""

    SKIP_TIMEFRAMES = {"1m", "1min", "1MIN"}

    def __init__(self, indicators_config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(indicators_config or {})
        ms = dict(cfg.get("market_structure") or cfg)
        self.swing_left: int = int(ms.get("swing_left", ms.get("swing_lookback", 2)))
        self.swing_right: int = int(ms.get("swing_right", ms.get("swing_lookback", 2)))
        self.break_confirmation: int = int(
            ms.get("break_confirmation", ms.get("confirmation_candles", 1))
        )
        self.minimum_break_distance_pct: float = float(
            ms.get("minimum_break_distance_pct", 0.05)
        )
        self.atr_tolerance_mult: float = float(ms.get("atr_tolerance_mult", 0.1))
        self.timeframes: list[str] = [
            str(t).lower() for t in (ms.get("timeframes") or ["5m", "15m", "1h", "4h", "1d"])
        ]

    def supports_timeframe(self, timeframe: str) -> bool:
        tf = timeframe.lower()
        if tf in self.SKIP_TIMEFRAMES:
            return False
        allowed = {t.lower() for t in self.timeframes}
        return tf in allowed

    def analyze(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not self.supports_timeframe(timeframe):
            return {"events": [], "trend": TrendBias.RANGE.value, "swings": []}
        if len(candles) < self.swing_left + self.swing_right + 3:
            return {"events": [], "trend": TrendBias.RANGE.value, "swings": []}

        swings = label_swings(
            detect_swings(candles, self.swing_left, self.swing_right)
        )
        trend = infer_trend(swings)
        events = self._detect_events(symbol, timeframe, candles, swings, trend)
        return {
            "events": [e.to_dict() for e in events],
            "trend": trend.value,
            "swings": [
                {
                    "index": s.index,
                    "kind": s.kind,
                    "price": s.price,
                    "label": s.label.value if s.label else None,
                    "candle_time": s.candle_time,
                }
                for s in swings
            ],
        }

    def _detect_events(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
        swings: list[SwingPoint],
        trend: TrendBias,
    ) -> list[StructureEvent]:
        events: list[StructureEvent] = []
        n = len(candles)
        highs = [float(c.get("high") or c.get("h") or 0) for c in candles]
        lows = [float(c.get("low") or c.get("l") or 0) for c in candles]
        closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
        atr_val = calc_atr(highs, lows, closes, 14)
        tol = (atr_val or 0.0) * self.atr_tolerance_mult

        swing_highs = [s for s in swings if s.kind == "high"]
        swing_lows = [s for s in swings if s.kind == "low"]
        if not swing_highs or not swing_lows:
            return events

        last_idx = n - 1
        _, lh, ll, lc = _ohlc(candles, last_idx)
        last_time = _candle_time(candles[last_idx])
        prev_sh = swing_highs[-1]
        prev_sl = swing_lows[-1]
        min_dist_h = _min_break_distance(prev_sh.price, self.minimum_break_distance_pct)
        min_dist_l = _min_break_distance(prev_sl.price, self.minimum_break_distance_pct)

        events.append(
            StructureEvent(
                symbol=symbol,
                timeframe=timeframe,
                event_type="trend_state",
                price=lc,
                candle_time=last_time,
                strength=1.0,
                evidence={"trend": trend.value},
            )
        )

        for sp in swings[-4:]:
            if sp.label:
                events.append(
                    StructureEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type=f"swing_{sp.label.value}",
                        price=sp.price,
                        candle_time=sp.candle_time,
                        strength=0.5,
                        evidence={"kind": sp.kind, "index": sp.index},
                    )
                )

        if lc > prev_sh.price + min_dist_h + tol:
            et = "bos_bullish" if trend == TrendBias.BULLISH else "choch_bullish"
            events.append(
                StructureEvent(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=et,
                    price=prev_sh.price,
                    candle_time=last_time,
                    strength=min(1.0, (lc - prev_sh.price) / (min_dist_h + tol + 1e-9)),
                    evidence={"close": lc, "level": prev_sh.price},
                )
            )
        if lc < prev_sl.price - min_dist_l - tol:
            et = "bos_bearish" if trend == TrendBias.BEARISH else "choch_bearish"
            events.append(
                StructureEvent(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type=et,
                    price=prev_sl.price,
                    candle_time=last_time,
                    strength=min(1.0, (prev_sl.price - lc) / (min_dist_l + tol + 1e-9)),
                    evidence={"close": lc, "level": prev_sl.price},
                )
            )

        events.extend(
            self._liquidity_sweeps(
                symbol, timeframe, candles, last_idx, swing_highs, swing_lows, tol
            )
        )
        events.extend(
            self._breakouts(
                symbol,
                timeframe,
                candles,
                closes,
                swing_highs,
                swing_lows,
                min_dist_h,
                min_dist_l,
                tol,
            )
        )
        return events

    def _liquidity_sweeps(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
        idx: int,
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
        tol: float,
    ) -> list[StructureEvent]:
        out: list[StructureEvent] = []
        if not swing_highs and not swing_lows:
            return out
        _, h, l, c = _ohlc(candles, idx)
        t = _candle_time(candles[idx])
        if swing_highs:
            level = swing_highs[-1].price
            if h > level + tol and c < level:
                out.append(
                    StructureEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type="liquidity_sweep_high",
                        price=level,
                        candle_time=t,
                        strength=min(1.0, (h - level) / (tol + 1e-9)),
                        evidence={"high": h, "close": c},
                    )
                )
        if swing_lows:
            level = swing_lows[-1].price
            if l < level - tol and c > level:
                out.append(
                    StructureEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type="liquidity_sweep_low",
                        price=level,
                        candle_time=t,
                        strength=min(1.0, (level - l) / (tol + 1e-9)),
                        evidence={"low": l, "close": c},
                    )
                )
        return out

    def _breakouts(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
        closes: list[float],
        swing_highs: list[SwingPoint],
        swing_lows: list[SwingPoint],
        min_dist_h: float,
        min_dist_l: float,
        tol: float,
    ) -> list[StructureEvent]:
        out: list[StructureEvent] = []
        if len(closes) < self.break_confirmation + 1:
            return out
        level_h = swing_highs[-1].price
        level_l = swing_lows[-1].price
        confirm = self.break_confirmation
        recent = closes[-confirm:]
        last_time = _candle_time(candles[-1])

        if all(c > level_h + min_dist_h + tol for c in recent):
            out.append(
                StructureEvent(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type="breakout_bullish",
                    price=level_h,
                    candle_time=last_time,
                    strength=0.8,
                    evidence={"closes": recent, "level": level_h},
                )
            )
        elif len(closes) >= confirm + 2:
            prev = closes[-confirm - 1]
            if prev > level_h + min_dist_h and recent[-1] < level_h:
                out.append(
                    StructureEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type="failed_breakout_bullish",
                        price=level_h,
                        candle_time=last_time,
                        strength=0.7,
                        evidence={"prior_close": prev, "close": recent[-1]},
                    )
                )

        if all(c < level_l - min_dist_l - tol for c in recent):
            out.append(
                StructureEvent(
                    symbol=symbol,
                    timeframe=timeframe,
                    event_type="breakout_bearish",
                    price=level_l,
                    candle_time=last_time,
                    strength=0.8,
                    evidence={"closes": recent, "level": level_l},
                )
            )
        elif len(closes) >= confirm + 2:
            prev = closes[-confirm - 1]
            if prev < level_l - min_dist_l and recent[-1] > level_l:
                out.append(
                    StructureEvent(
                        symbol=symbol,
                        timeframe=timeframe,
                        event_type="failed_breakout_bearish",
                        price=level_l,
                        candle_time=last_time,
                        strength=0.7,
                        evidence={"prior_close": prev, "close": recent[-1]},
                    )
                )
        return out
