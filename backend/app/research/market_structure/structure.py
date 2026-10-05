"""Per-timeframe structure snapshots (point-in-time safe)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr_series
from app.research.bos_strategy_comparison.htf import timeframe_seconds
from app.research.market_structure.config import MarketStructureFeatureConfig
from app.research.market_structure.indicators_ext import (
    adx_di_series,
    atr_percentile_series,
    atr_relative_series,
    choppiness_index_series,
    directional_alternation_count,
    efficiency_ratio_series,
    ema_series,
    realized_vol_series,
    roc_series,
    rolling_high_low,
    rolling_slope,
    rsi_series,
)
from app.research.market_structure.regime import RegimeClassification, classify_market_regime
from app.signals._candle_utils import candle_time, series_ohlcv
from app.signals.bos_engine import detect_bos
from app.signals.swing_detector import detect_swings, extend_swings
from app.signals.trend_engine import infer_trend


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def candle_close_time(
    candle: Mapping[str, Any], timeframe: str
) -> datetime | None:
    open_ts = _aware(candle_time(candle))
    if open_ts is None:
        return None
    return open_ts + timedelta(seconds=timeframe_seconds(timeframe))


@dataclass
class StructureSnapshot:
    timeframe: str
    decision_time: str | None
    last_closed_candle_time: str | None
    trend_state: str
    market_regime: str
    direction: str
    structure_state: str
    swing_high: float | None
    swing_low: float | None
    last_higher_high: float | None
    last_higher_low: float | None
    last_lower_high: float | None
    last_lower_low: float | None
    bos_state: str
    bos_direction: str | None
    bos_time: str | None
    choch_state: str
    choch_direction: str | None
    pullback_state: str
    range_state: str
    volatility_state: str
    momentum_state: str
    confidence_score: float
    data_quality_state: str
    reason_codes: list[str] = field(default_factory=list)
    # Diagnostic extras (not required in table but useful for classifier)
    adx: float | None = None
    di_plus: float | None = None
    di_minus: float | None = None
    efficiency_ratio: float | None = None
    choppiness_index: float | None = None
    atr: float | None = None
    atr_percentile: float | None = None
    atr_relative: float | None = None
    ema_alignment: str | None = None
    direction_changes: int | None = None
    rsi: float | None = None
    roc: float | None = None
    feature_timestamp: str | None = None
    data_source: str = "ohlcv"
    uses_future_candle: bool = False
    uses_unconfirmed_htf_candle: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _missing_snapshot(
    timeframe: str,
    decision_time: str | None,
    *,
    quality: str,
    reason: str,
) -> StructureSnapshot:
    return StructureSnapshot(
        timeframe=timeframe,
        decision_time=decision_time,
        last_closed_candle_time=None,
        trend_state=quality
        if quality
        in {"UNKNOWN_DATA_MISSING", "UNKNOWN_INSUFFICIENT_HISTORY"}
        else "UNKNOWN_DATA_MISSING",
        market_regime="UNKNOWN",
        direction="UNKNOWN",
        structure_state="UNKNOWN",
        swing_high=None,
        swing_low=None,
        last_higher_high=None,
        last_higher_low=None,
        last_lower_high=None,
        last_lower_low=None,
        bos_state="UNKNOWN",
        bos_direction=None,
        bos_time=None,
        choch_state="UNKNOWN",
        choch_direction=None,
        pullback_state="PULLBACK_UNKNOWN",
        range_state="UNKNOWN",
        volatility_state="UNKNOWN",
        momentum_state="UNKNOWN",
        confidence_score=0.0,
        data_quality_state=quality,
        reason_codes=[reason],
        feature_timestamp=decision_time,
    )


@dataclass
class TimeframeFeatureTable:
    """Precomputed causal features for one timeframe (indexed by candle)."""

    timeframe: str
    candles: list[Mapping[str, Any]]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float]
    open_times: list[datetime | None]
    close_times: list[datetime | None]
    ema20: list[float | None]
    ema50: list[float | None]
    ema200: list[float | None]
    atr: list[float | None]
    atr_pct: list[float | None]
    atr_rel: list[float | None]
    adx: list[float | None]
    di_plus: list[float | None]
    di_minus: list[float | None]
    efficiency: list[float | None]
    choppiness: list[float | None]
    slope: list[float | None]
    roc: list[float | None]
    rsi: list[float | None]
    rvol: list[float | None]
    alternations: list[int | None]
    roll_high: list[float | None]
    roll_low: list[float | None]
    # Per-bar structure labels (confirmed swings only)
    trend_state: list[str]
    structure_state: list[str]
    direction: list[str]
    swing_high: list[float | None]
    swing_low: list[float | None]
    last_hh: list[float | None]
    last_hl: list[float | None]
    last_lh: list[float | None]
    last_ll: list[float | None]
    bos_state: list[str]
    bos_direction: list[str | None]
    bos_time: list[str | None]
    choch_state: list[str]
    choch_direction: list[str | None]
    pullback_state: list[str]
    range_state: list[str]
    volatility_state: list[str]
    momentum_state: list[str]
    ema_alignment: list[str]
    config: MarketStructureFeatureConfig


def build_timeframe_feature_table(
    candles: Sequence[Mapping[str, Any]],
    timeframe: str,
    *,
    symbol: str = "",
    config: MarketStructureFeatureConfig | None = None,
) -> TimeframeFeatureTable | None:
    """Vectorized / incremental feature build. Returns None if no candles."""
    cfg = config or MarketStructureFeatureConfig()
    series = list(candles)
    if not series:
        return None
    opens, highs, lows, closes, vols = series_ohlcv(series)
    n = len(series)
    open_times = [_aware(candle_time(c)) for c in series]
    close_times = [
        (ot + timedelta(seconds=timeframe_seconds(timeframe)))
        if ot is not None
        else None
        for ot in open_times
    ]

    ema20 = ema_series(closes, cfg.ema_fast)
    ema50 = ema_series(closes, cfg.ema_mid)
    ema200 = ema_series(closes, cfg.ema_slow)
    atr = atr_series(highs, lows, closes, cfg.atr_period)
    atr_pct = atr_percentile_series(atr, cfg.atr_percentile_lookback)
    atr_rel = atr_relative_series(atr, cfg.atr_percentile_lookback)
    adx, di_p, di_m = adx_di_series(highs, lows, closes, cfg.adx_period)
    efficiency = efficiency_ratio_series(closes, cfg.efficiency_lookback)
    choppiness = choppiness_index_series(highs, lows, closes, cfg.choppiness_lookback)
    slope = rolling_slope(closes, cfg.slope_lookback)
    roc = roc_series(closes, cfg.momentum_lookback)
    rsi = rsi_series(closes, cfg.rsi_period)
    rvol = realized_vol_series(closes, cfg.momentum_lookback)
    alternations = directional_alternation_count(closes, cfg.direction_change_lookback)
    roll_high, roll_low = rolling_high_low(highs, lows, cfg.range_lookback)

    trend_state = ["UNKNOWN_INSUFFICIENT_HISTORY"] * n
    structure_state = ["UNKNOWN"] * n
    direction = ["UNKNOWN"] * n
    swing_high: list[float | None] = [None] * n
    swing_low: list[float | None] = [None] * n
    last_hh: list[float | None] = [None] * n
    last_hl: list[float | None] = [None] * n
    last_lh: list[float | None] = [None] * n
    last_ll: list[float | None] = [None] * n
    bos_state = ["UNKNOWN"] * n
    bos_direction: list[str | None] = [None] * n
    bos_time: list[str | None] = [None] * n
    choch_state = ["UNKNOWN"] * n
    choch_direction: list[str | None] = [None] * n
    pullback_state = ["PULLBACK_UNKNOWN"] * n
    range_state = ["UNKNOWN"] * n
    volatility_state = ["UNKNOWN"] * n
    momentum_state = ["UNKNOWN"] * n
    ema_alignment = ["UNKNOWN"] * n

    # Incremental confirmed swings — confirmation delay explicitly modeled via
    # as_of_index / extend_swings (right-bar confirmation, no look-ahead).
    min_i = max(cfg.swing_left + cfg.swing_right, cfg.min_bars_basic - 1)
    swings: list = []
    prev_trend = "NEUTRAL"
    for i in range(n):
        if i < min_i:
            continue
        if not swings:
            swings = detect_swings(
                series,
                left=cfg.swing_left,
                right=cfg.swing_right,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=cfg.atr_period,
                as_of_index=i,
                highs=highs,
                lows=lows,
                closes=closes,
            )
        else:
            swings = extend_swings(
                swings,
                series,
                left=cfg.swing_left,
                right=cfg.swing_right,
                symbol=symbol,
                timeframe=timeframe,
                atr_period=cfg.atr_period,
                as_of_index=i,
                highs=highs,
                lows=lows,
                closes=closes,
            )
        trend = infer_trend(swings)
        tlabel = str(trend.get("trend") or "NEUTRAL").upper()
        if tlabel == "INSUFFICIENT_DATA":
            trend_state[i] = "UNKNOWN_INSUFFICIENT_HISTORY"
            structure_state[i] = "UNKNOWN"
            direction[i] = "UNKNOWN"
        elif tlabel == "BULLISH":
            trend_state[i] = "BULLISH"
            structure_state[i] = "UPTREND_HH_HL"
            direction[i] = "BULLISH"
        elif tlabel == "BEARISH":
            trend_state[i] = "BEARISH"
            structure_state[i] = "DOWNTREND_LH_LL"
            direction[i] = "BEARISH"
        else:
            # Neutral — check if transitioning from prior directional trend
            if prev_trend in {"BULLISH", "BEARISH"}:
                trend_state[i] = "TRANSITION"
                structure_state[i] = "TRANSITION_STRUCTURE"
            else:
                trend_state[i] = "NEUTRAL"
                structure_state[i] = "RANGE_STRUCTURE"
            direction[i] = "NEUTRAL"
        if tlabel in {"BULLISH", "BEARISH", "NEUTRAL"}:
            prev_trend = tlabel

        highs_sw = [s for s in swings if s.swing_type == "HIGH"]
        lows_sw = [s for s in swings if s.swing_type == "LOW"]
        swing_high[i] = highs_sw[-1].price if highs_sw else None
        swing_low[i] = lows_sw[-1].price if lows_sw else None
        for s in reversed(highs_sw):
            if s.label == "HH" and last_hh[i] is None:
                last_hh[i] = s.price
            elif s.label == "LH" and last_lh[i] is None:
                last_lh[i] = s.price
            if last_hh[i] is not None and last_lh[i] is not None:
                break
        for s in reversed(lows_sw):
            if s.label == "HL" and last_hl[i] is None:
                last_hl[i] = s.price
            elif s.label == "LL" and last_ll[i] is None:
                last_ll[i] = s.price
            if last_hl[i] is not None and last_ll[i] is not None:
                break

        # Mixed structure override
        seq = list(trend.get("structure_sequence") or [])
        if len(seq) >= 4:
            labels = {str(x) for x in seq[-4:]}
            if {"HH", "HL", "LH", "LL"}.issubset(labels) or (
                "HH" in labels and "LL" in labels
            ):
                structure_state[i] = "MIXED_STRUCTURE"

        bos = detect_bos(
            series,
            swings,
            trend,
            symbol=symbol,
            timeframe=timeframe,
            atr_period=cfg.atr_period,
            as_of_index=i,
            atr_value=atr[i],
        )
        if bos and bos.get("state") == "CONFIRMED" and bos.get("direction"):
            bos_state[i] = str(bos["direction"])
            bos_direction[i] = str(bos["direction"])
            bt = bos.get("break_timestamp")
            bos_time[i] = bt.isoformat() if hasattr(bt, "isoformat") else (str(bt) if bt else None)
        else:
            bos_state[i] = "NO_CONFIRMED_BOS"
            bos_direction[i] = None
            bos_time[i] = None

        # Simple CHoCH: trend flipped vs prior confirmed BOS direction recently
        choch_state[i] = "NO_CONFIRMED_CHOCH"
        choch_direction[i] = None
        if i > 0 and trend_state[i] in {"BULLISH", "BEARISH"}:
            # Look back for opposite BOS while current trend is new side
            look = max(0, i - cfg.bos_lookback_bars)
            for j in range(i, look - 1, -1):
                bd = bos_direction[j]
                if not bd:
                    continue
                if trend_state[i] == "BULLISH" and bd == "BEARISH_BOS":
                    # Bullish CHoCH when structure turns bull after bear BOS context
                    if structure_state[i] in {"UPTREND_HH_HL", "BULLISH_STRUCTURE"}:
                        choch_state[i] = "BULLISH_CHOCH"
                        choch_direction[i] = "BULLISH_CHOCH"
                    break
                if trend_state[i] == "BEARISH" and bd == "BULLISH_BOS":
                    if structure_state[i] in {"DOWNTREND_LH_LL", "BEARISH_STRUCTURE"}:
                        choch_state[i] = "BEARISH_CHOCH"
                        choch_direction[i] = "BEARISH_CHOCH"
                    break
                break

        # Pullback vs last swing / ATR
        atr_i = atr[i]
        close_i = closes[i]
        if atr_i and atr_i > 0 and swing_high[i] is not None and swing_low[i] is not None:
            if direction[i] == "BULLISH":
                depth = (swing_high[i] - close_i) / atr_i
                if depth < 0:
                    pullback_state[i] = "NO_PULLBACK"
                elif depth >= cfg.pullback_atr_deep:
                    pullback_state[i] = "DEEP_PULLBACK"
                elif depth >= cfg.pullback_atr_shallow:
                    pullback_state[i] = "BULLISH_PULLBACK"
                else:
                    pullback_state[i] = "NO_PULLBACK"
            elif direction[i] == "BEARISH":
                depth = (close_i - swing_low[i]) / atr_i
                if depth < 0:
                    pullback_state[i] = "NO_PULLBACK"
                elif depth >= cfg.pullback_atr_deep:
                    pullback_state[i] = "DEEP_PULLBACK"
                elif depth >= cfg.pullback_atr_shallow:
                    pullback_state[i] = "BEARISH_PULLBACK"
                else:
                    pullback_state[i] = "NO_PULLBACK"
            else:
                pullback_state[i] = "NO_PULLBACK"
        else:
            pullback_state[i] = "PULLBACK_UNKNOWN"

        # Range state
        rh, rl = roll_high[i], roll_low[i]
        if rh is not None and rl is not None and atr_i and atr_i > 0:
            width = (rh - rl) / atr_i
            if rl <= close_i <= rh:
                if width <= 2.0:
                    range_state[i] = "RANGE_COMPRESSION"
                elif width >= 6.0:
                    range_state[i] = "RANGE_EXPANSION"
                else:
                    range_state[i] = "INSIDE_RANGE"
            else:
                range_state[i] = "RANGE_EXPANSION"
        else:
            range_state[i] = "UNKNOWN"

        # Volatility
        ap = atr_pct[i]
        if ap is None:
            volatility_state[i] = (
                "INSUFFICIENT_HISTORY" if i < cfg.atr_percentile_lookback else "UNKNOWN"
            )
        elif ap >= cfg.atr_high_percentile:
            volatility_state[i] = "HIGH"
        elif ap <= cfg.atr_low_percentile:
            volatility_state[i] = "LOW"
        else:
            volatility_state[i] = "NORMAL"

        # Momentum
        r = roc[i]
        s = slope[i]
        if r is None:
            momentum_state[i] = "UNKNOWN"
        elif r > 0 and (s is None or s >= 0):
            momentum_state[i] = "BULLISH"
        elif r < 0 and (s is None or s <= 0):
            momentum_state[i] = "BEARISH"
        else:
            momentum_state[i] = "NEUTRAL"
        if i >= cfg.momentum_lookback and roc[i] is not None and roc[i - 1] is not None:
            if abs(roc[i]) > abs(roc[i - 1]) * 1.15:
                momentum_state[i] = "ACCELERATING"
            elif abs(roc[i]) < abs(roc[i - 1]) * 0.85:
                momentum_state[i] = "DECELERATING"

        # EMA alignment
        e20, e50, e200 = ema20[i], ema50[i], ema200[i]
        if e20 is not None and e50 is not None:
            if e20 > e50 and (e200 is None or e50 > e200):
                ema_alignment[i] = "BULLISH"
            elif e20 < e50 and (e200 is None or e50 < e200):
                ema_alignment[i] = "BEARISH"
            else:
                ema_alignment[i] = "MIXED"
        else:
            ema_alignment[i] = "UNKNOWN"

    return TimeframeFeatureTable(
        timeframe=timeframe,
        candles=series,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=vols,
        open_times=open_times,
        close_times=close_times,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        atr=atr,
        atr_pct=atr_pct,
        atr_rel=atr_rel,
        adx=adx,
        di_plus=di_p,
        di_minus=di_m,
        efficiency=efficiency,
        choppiness=choppiness,
        slope=slope,
        roc=roc,
        rsi=rsi,
        rvol=rvol,
        alternations=alternations,
        roll_high=roll_high,
        roll_low=roll_low,
        trend_state=trend_state,
        structure_state=structure_state,
        direction=direction,
        swing_high=swing_high,
        swing_low=swing_low,
        last_hh=last_hh,
        last_hl=last_hl,
        last_lh=last_lh,
        last_ll=last_ll,
        bos_state=bos_state,
        bos_direction=bos_direction,
        bos_time=bos_time,
        choch_state=choch_state,
        choch_direction=choch_direction,
        pullback_state=pullback_state,
        range_state=range_state,
        volatility_state=volatility_state,
        momentum_state=momentum_state,
        ema_alignment=ema_alignment,
        config=cfg,
    )


def snapshot_at_index(
    table: TimeframeFeatureTable,
    index: int,
    *,
    decision_time: datetime | None,
) -> StructureSnapshot:
    """Build snapshot for a closed candle index (features at that index only)."""
    tf = table.timeframe
    dec = decision_time.isoformat() if decision_time else None
    if index < 0 or index >= len(table.candles):
        return _missing_snapshot(tf, dec, quality="UNKNOWN_DATA_MISSING", reason="INDEX_OOB")

    feat_close = table.close_times[index]
    feat_ts = feat_close.isoformat() if feat_close else (
        table.open_times[index].isoformat() if table.open_times[index] else None
    )
    # PIT assertions — raise if violated
    uses_future = False
    uses_unconfirmed = False
    if decision_time is not None and feat_close is not None:
        if feat_close > decision_time:
            uses_future = True
            uses_unconfirmed = True

    if uses_future or uses_unconfirmed:
        raise AssertionError(
            f"PIT violation tf={tf} index={index}: "
            f"feature_close={feat_close} decision={decision_time} "
            f"uses_future_candle={uses_future} uses_unconfirmed_htf_candle={uses_unconfirmed}"
        )
    if decision_time is not None and feat_close is not None:
        assert feat_close <= decision_time
    assert not uses_future
    assert not uses_unconfirmed

    raw = {
        "trend_state": table.trend_state[index],
        "structure_state": table.structure_state[index],
        "direction": table.direction[index],
        "bos_state": table.bos_state[index],
        "choch_state": table.choch_state[index],
        "volatility_state": table.volatility_state[index],
        "momentum_state": table.momentum_state[index],
        "range_state": table.range_state[index],
        "adx": table.adx[index],
        "di_plus": table.di_plus[index],
        "di_minus": table.di_minus[index],
        "efficiency_ratio": table.efficiency[index],
        "choppiness_index": table.choppiness[index],
        "atr_percentile": table.atr_pct[index],
        "ema_alignment": table.ema_alignment[index],
        "direction_changes": table.alternations[index],
        "data_quality_state": (
            "OK"
            if index >= table.config.min_bars_basic
            else "UNKNOWN_INSUFFICIENT_HISTORY"
        ),
    }
    regime: RegimeClassification = classify_market_regime(raw, table.config)

    return StructureSnapshot(
        timeframe=tf,
        decision_time=dec,
        last_closed_candle_time=feat_ts,
        trend_state=table.trend_state[index],
        market_regime=regime.market_regime,
        direction=table.direction[index],
        structure_state=table.structure_state[index],
        swing_high=table.swing_high[index],
        swing_low=table.swing_low[index],
        last_higher_high=table.last_hh[index],
        last_higher_low=table.last_hl[index],
        last_lower_high=table.last_lh[index],
        last_lower_low=table.last_ll[index],
        bos_state=table.bos_state[index],
        bos_direction=table.bos_direction[index],
        bos_time=table.bos_time[index],
        choch_state=table.choch_state[index],
        choch_direction=table.choch_direction[index],
        pullback_state=table.pullback_state[index],
        range_state=table.range_state[index],
        volatility_state=table.volatility_state[index],
        momentum_state=table.momentum_state[index],
        confidence_score=regime.confidence_score,
        data_quality_state=raw["data_quality_state"],
        reason_codes=list(regime.reason_codes),
        adx=table.adx[index],
        di_plus=table.di_plus[index],
        di_minus=table.di_minus[index],
        efficiency_ratio=table.efficiency[index],
        choppiness_index=table.choppiness[index],
        atr=table.atr[index],
        atr_percentile=table.atr_pct[index],
        atr_relative=table.atr_rel[index],
        ema_alignment=table.ema_alignment[index],
        direction_changes=table.alternations[index],
        rsi=table.rsi[index],
        roc=table.roc[index],
        feature_timestamp=feat_ts,
        data_source="ohlcv",
        uses_future_candle=False,
        uses_unconfirmed_htf_candle=False,
    )
