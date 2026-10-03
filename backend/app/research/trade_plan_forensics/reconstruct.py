"""No-lookahead market-state reconstruction at trade entry (research-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.config import get_settings
from app.engines.mtf.indicators import atr as calc_atr, rsi as calc_rsi
from app.engines.supply_demand.engine import SupplyDemandEngine
from app.research.bos_strategy_comparison.htf import as_of_index_at_or_before
from app.research.postgres_ohlcv import load_ohlcv_series_tail
from app.research.trade_plan_forensics.schemas import UNAVAILABLE, UNKNOWN, NormalizedTrade
from app.research.trade_plan_forensics.thresholds import (
    ENTRY_AFTER_BOS_ATR_MAX,
    ENTRY_AT_BOS_ATR_MAX,
    ENTRY_TOO_FAR_BOS_ATR,
    HTF_TFS,
    REGIME_ATR_PCT_CONTRACTION,
    REGIME_ATR_PCT_EXPANSION,
    REGIME_BOS_FREQ_BREAKOUT_MIN,
    REGIME_DIR_CHANGES_CHOP_MIN,
    REGIME_RANGE_ATR_SIDEWAYS_MAX,
    REGIME_TREND_STRENGTH_MIN,
    SWEEP_DEEP_ATR_MIN,
    SWEEP_WEAK_ATR_MAX,
    TIMING_EARLY_MAX,
    TIMING_LATE_MAX,
    TIMING_TIMELY_MAX,
)
from app.signals._candle_utils import candle_time, ohlc, series_ohlcv, volume_at
from app.signals.config import SignalConfig
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend


def _parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def entry_candle_metrics(
    candles: Sequence[Mapping[str, Any]], idx: int, *, atr_period: int = 14
) -> dict[str, Any]:
    if idx < 0 or idx >= len(candles):
        return {"status": UNAVAILABLE}
    o, h, l, c = ohlc(candles, idx)
    vol = volume_at(candles, idx)
    rng = h - l
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    prev = None
    if idx > 0:
        po, ph, pl, pc = ohlc(candles, idx - 1)
        prev = {
            "open": po,
            "high": ph,
            "low": pl,
            "close": pc,
            "volume": volume_at(candles, idx - 1),
        }
    _, highs, lows, closes, vols = series_ohlcv(list(candles[: idx + 1]))
    atr_v = calc_atr(highs, lows, closes, atr_period)
    atr_pct = (atr_v / c * 100.0) if atr_v and c else None
    rvol = None
    if len(vols) >= 21:
        avg = sum(vols[-21:-1]) / 20.0
        rvol = (vols[-1] / avg) if avg > 0 else None
    vol_sma = (sum(vols[-20:]) / min(20, len(vols))) if vols else None
    rsi_v = calc_rsi(closes, 14)
    # volatility percentile of ATR% over lookback
    vol_pctile = None
    if atr_pct is not None and len(closes) >= 50:
        atr_pcts: list[float] = []
        for j in range(14, idx + 1):
            a = calc_atr(highs[: j + 1], lows[: j + 1], closes[: j + 1], atr_period)
            if a and closes[j]:
                atr_pcts.append(a / closes[j] * 100.0)
        if atr_pcts:
            less = sum(1 for x in atr_pcts if x <= atr_pct)
            vol_pctile = less / len(atr_pcts)
    ts = candle_time(candles[idx])
    return {
        "status": "OK",
        "entry_candle_timestamp": ts.isoformat() if ts else UNKNOWN,
        "entry_ohlcv": {"open": o, "high": h, "low": l, "close": c, "volume": vol},
        "previous_ohlcv": prev if prev is not None else UNAVAILABLE,
        "atr": atr_v,
        "atr_percent": atr_pct,
        "candle_range_over_atr": (rng / atr_v) if atr_v and atr_v > 0 else None,
        "body_over_range": (body / rng) if rng > 0 else None,
        "upper_wick_pct": (upper / rng * 100.0) if rng > 0 else None,
        "lower_wick_pct": (lower / rng * 100.0) if rng > 0 else None,
        "volume": vol,
        "volume_vs_sma20": (vol / vol_sma) if vol_sma and vol_sma > 0 else None,
        "rvol": rvol,
        "rsi_14": rsi_v,
        "volatility_percentile": vol_pctile,
    }


def _direction_changes(labels: Sequence[str]) -> int:
    def fam(lab: str) -> str | None:
        if lab in {"HH", "HL"}:
            return "BULL"
        if lab in {"LH", "LL"}:
            return "BEAR"
        return None

    changes = 0
    prev: str | None = None
    for lab in labels:
        f = fam(str(lab))
        if f is None:
            continue
        if prev is not None and f != prev:
            changes += 1
        prev = f
    return changes


def classify_entry_vs_bos(
    *,
    bars_since_bos: int | None,
    bos_distance_atr: float | None,
    bos_confirmed: bool,
) -> str:
    """Research labels using documented ATR/bar thresholds (not production rules)."""
    if not bos_confirmed:
        return "ENTRY_BEFORE_CONFIRMATION"
    if bos_distance_atr is not None and bos_distance_atr <= ENTRY_AT_BOS_ATR_MAX:
        if bars_since_bos is not None and bars_since_bos <= TIMING_EARLY_MAX:
            return "ENTRY_AT_BOS"
    if bos_distance_atr is not None and bos_distance_atr > ENTRY_TOO_FAR_BOS_ATR:
        return "ENTRY_TOO_FAR_AFTER_BOS"
    if bars_since_bos is not None and bars_since_bos >= 1:
        return "ENTRY_AFTER_BOS"
    if bos_distance_atr is not None and bos_distance_atr <= ENTRY_AFTER_BOS_ATR_MAX:
        return "ENTRY_AFTER_BOS"
    return "ENTRY_AFTER_BOS"


def classify_timing(bars_since_bos: int | None) -> str:
    if bars_since_bos is None:
        return UNAVAILABLE
    if bars_since_bos <= TIMING_EARLY_MAX:
        return "EARLY"
    if bars_since_bos <= TIMING_TIMELY_MAX:
        return "TIMELY"
    if bars_since_bos <= TIMING_LATE_MAX:
        return "LATE"
    return "VERY_LATE"


def classify_htf_state(
    *,
    direction: str,
    trends: Mapping[str, str],
) -> str:
    """HTF_ALIGNED / HTF_CONFLICT / HTF_NEUTRAL / HTF_UNAVAILABLE — never collapse conflict."""
    wanted = "BULLISH" if direction.upper() == "LONG" else "BEARISH"
    opposed = "BEARISH" if wanted == "BULLISH" else "BULLISH"
    # Prefer 4h + 1h when present
    keys = [k for k in ("4h", "1h", "1d") if k in trends]
    if not keys:
        keys = [k for k in trends if trends[k] not in (UNAVAILABLE, UNKNOWN, "")]
    if not keys:
        return "HTF_UNAVAILABLE"
    vals = [trends[k] for k in keys]
    if any(v == opposed for v in vals):
        return "HTF_CONFLICT"
    directional = [v for v in vals if v in ("BULLISH", "BEARISH")]
    if not directional:
        return "HTF_NEUTRAL"
    if all(v == wanted for v in directional):
        return "HTF_ALIGNED"
    if any(v == opposed for v in directional):
        return "HTF_CONFLICT"
    return "HTF_NEUTRAL"


def classify_regime(
    *,
    trend: str,
    trend_strength: float | None,
    atr_percent: float | None,
    recent_range_atr: float | None,
    direction_changes: int | None,
    bos_freq: float | None,
    dist_to_range_edge_atr: float | None,
) -> dict[str, Any]:
    """Research-only multi-label regime. Measurements stored alongside labels."""
    labels: list[str] = []
    if atr_percent is not None:
        if atr_percent >= REGIME_ATR_PCT_EXPANSION:
            labels.append("VOLATILITY_EXPANSION")
        elif atr_percent <= REGIME_ATR_PCT_CONTRACTION:
            labels.append("VOLATILITY_CONTRACTION")
    if trend == "BULLISH" and (trend_strength or 0) >= REGIME_TREND_STRENGTH_MIN:
        labels.append("TRENDING_UP")
    elif trend == "BEARISH" and (trend_strength or 0) >= REGIME_TREND_STRENGTH_MIN:
        labels.append("TRENDING_DOWN")
    if (
        recent_range_atr is not None
        and recent_range_atr <= REGIME_RANGE_ATR_SIDEWAYS_MAX
        and (direction_changes or 0) >= 2
        and "TRENDING_UP" not in labels
        and "TRENDING_DOWN" not in labels
    ):
        labels.append("SIDEWAYS")
    if (
        (direction_changes or 0) >= REGIME_DIR_CHANGES_CHOP_MIN
        and recent_range_atr is not None
        and recent_range_atr <= REGIME_RANGE_ATR_SIDEWAYS_MAX
    ):
        labels.append("CHOP")
    if bos_freq is not None and bos_freq >= REGIME_BOS_FREQ_BREAKOUT_MIN:
        labels.append("BREAKOUT")
    if dist_to_range_edge_atr is not None:
        if dist_to_range_edge_atr <= 0.35:
            labels.append("RANGE_EDGE")
        elif dist_to_range_edge_atr >= 1.0:
            labels.append("MID_RANGE")
    if not labels:
        labels.append("UNKNOWN")
    primary = labels[0]
    return {
        "primary": primary,
        "labels": labels,
        "measurements": {
            "trend": trend,
            "trend_strength": trend_strength,
            "atr_percent": atr_percent,
            "recent_range_atr": recent_range_atr,
            "direction_changes": direction_changes,
            "bos_frequency": bos_freq,
            "dist_to_range_edge_atr": dist_to_range_edge_atr,
            "thresholds_doc": {
                "REGIME_ATR_PCT_EXPANSION": REGIME_ATR_PCT_EXPANSION,
                "REGIME_ATR_PCT_CONTRACTION": REGIME_ATR_PCT_CONTRACTION,
                "REGIME_RANGE_ATR_SIDEWAYS_MAX": REGIME_RANGE_ATR_SIDEWAYS_MAX,
                "REGIME_DIR_CHANGES_CHOP_MIN": REGIME_DIR_CHANGES_CHOP_MIN,
                "REGIME_TREND_STRENGTH_MIN": REGIME_TREND_STRENGTH_MIN,
            },
        },
    }


def classify_sweep(
    *,
    direction: str,
    candles: Sequence[Mapping[str, Any]],
    entry_idx: int,
    swings: Sequence[Any],
    atr_v: float | None,
) -> dict[str, Any]:
    if not swings or atr_v is None or atr_v <= 0 or entry_idx < 1:
        return {"state": "SWEEP_DATA_UNAVAILABLE", "detail": {}}
    end = entry_idx
    o, h, l, c = ohlc(candles, end)
    highs_sw = [s for s in swings if s.swing_type == "HIGH" and s.bar_index < end]
    lows_sw = [s for s in swings if s.swing_type == "LOW" and s.bar_index < end]
    if direction.upper() == "LONG":
        if not lows_sw:
            return {"state": "NO_SWEEP", "detail": {}}
        level = float(lows_sw[-1].price)
        depth = max(0.0, level - l)
        depth_atr = depth / atr_v
        close_back = c >= level
        prior_liq = level
    else:
        if not highs_sw:
            return {"state": "NO_SWEEP", "detail": {}}
        level = float(highs_sw[-1].price)
        depth = max(0.0, h - level)
        depth_atr = depth / atr_v
        close_back = c <= level
        prior_liq = level
    if depth_atr <= 0:
        state = "NO_SWEEP"
    elif depth_atr < SWEEP_WEAK_ATR_MAX:
        state = "WEAK_SWEEP"
    elif depth_atr >= SWEEP_DEEP_ATR_MIN:
        state = "DEEP_SWEEP"
    elif close_back:
        state = "VALID_SWEEP"
    else:
        state = "WEAK_SWEEP"
    return {
        "state": state,
        "detail": {
            "prior_liquidity_level": prior_liq,
            "sweep_depth": depth,
            "sweep_depth_atr": depth_atr,
            "close_back_inside": close_back,
            "note": "Research heuristic on last swing; not production sweep rules.",
        },
    }


def path_excursions(
    *,
    direction: str,
    entry: float,
    stop: float | None,
    tp: float | None,
    candles: Sequence[Mapping[str, Any]],
    entry_idx: int,
    exit_idx: int | None,
) -> dict[str, Any]:
    """Post-entry path stats (MAE/MFE). Uses candles after entry by design."""
    if entry_idx >= len(candles) - 1:
        return {"status": UNAVAILABLE}
    risk = abs(entry - stop) if stop is not None else None
    end = exit_idx if exit_idx is not None else len(candles) - 1
    end = min(end, len(candles) - 1)
    start = entry_idx + 1
    if start > end:
        return {"status": UNAVAILABLE}
    mae = 0.0
    mfe = 0.0
    first_side: str | None = None
    hit_0_5 = hit_1 = hit_2 = False
    near_tp_before_sl = False
    touched_sl = False
    touched_tp = False
    for i in range(start, end + 1):
        _, h, l, _ = ohlc(candles, i)
        if direction.upper() == "LONG":
            adverse = max(0.0, entry - l)
            fav = max(0.0, h - entry)
            if first_side is None:
                if l < entry and h <= entry:
                    first_side = "AGAINST"
                elif h > entry and l >= entry:
                    first_side = "FAVOR"
                elif l < entry:
                    first_side = "AGAINST"
                elif h > entry:
                    first_side = "FAVOR"
            if stop is not None and l <= stop:
                touched_sl = True
            if tp is not None and h >= tp:
                touched_tp = True
        else:
            adverse = max(0.0, h - entry)
            fav = max(0.0, entry - l)
            if first_side is None:
                if h > entry and l >= entry:
                    first_side = "AGAINST"
                elif l < entry and h <= entry:
                    first_side = "FAVOR"
                elif h > entry:
                    first_side = "AGAINST"
                elif l < entry:
                    first_side = "FAVOR"
            if stop is not None and h >= stop:
                touched_sl = True
            if tp is not None and l <= tp:
                touched_tp = True
        mae = max(mae, adverse)
        mfe = max(mfe, fav)
        if risk and risk > 0:
            r_now = fav / risk
            if r_now >= 0.5:
                hit_0_5 = True
            if r_now >= 1.0:
                hit_1 = True
            if r_now >= 2.0:
                hit_2 = True
            if tp is not None and fav >= abs(tp - entry) * 0.9 and not touched_sl:
                near_tp_before_sl = True
    return {
        "status": "OK",
        "mae": mae,
        "mfe": mfe,
        "mae_r": (mae / risk) if risk and risk > 0 else None,
        "mfe_r": (mfe / risk) if risk and risk > 0 else None,
        "immediate_move": first_side or UNKNOWN,
        "reached_0_5R": hit_0_5,
        "reached_1R": hit_1,
        "reached_2R": hit_2,
        "touched_sl": touched_sl,
        "touched_tp": touched_tp,
        "near_tp_before_sl": near_tp_before_sl,
        "sl_distance": risk,
        "sl_distance_pct": (risk / entry * 100.0) if risk and entry else None,
    }


class CandleCache:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def get(self, symbol: str, timeframe: str) -> list[dict[str, Any]]:
        key = (symbol.upper(), timeframe.lower())
        if key in self._data:
            return self._data[key]
        # Prefer full available history for as-of reconstruction
        try:
            candles = await load_ohlcv_series_tail(symbol, timeframe, limit=20000)
        except Exception:  # noqa: BLE001
            candles = []
        self._data[key] = candles
        return candles


async def reconstruct_trade(
    trade: NormalizedTrade,
    *,
    cache: CandleCache | None = None,
    signal_config: SignalConfig | None = None,
) -> dict[str, Any]:
    """Full forensic reconstruction for one trade. No lookahead on entry features."""
    cache = cache or CandleCache()
    scfg = signal_config or SignalConfig()
    tf = str(trade.timeframe or "").lower()
    if not tf or tf in (UNKNOWN.lower(), UNAVAILABLE.lower()):
        return {
            "trade_id": trade.trade_id,
            "status": "STOP",
            "reason": "ENTRY_TIMEFRAME_UNAVAILABLE",
            "trade": trade.to_dict(),
        }
    entry_ts = _parse_ts(trade.entry_time)
    if entry_ts is None:
        return {
            "trade_id": trade.trade_id,
            "status": "STOP",
            "reason": "ENTRY_TIME_UNAVAILABLE",
            "trade": trade.to_dict(),
        }

    candles = await cache.get(trade.symbol, tf)
    if not candles:
        return {
            "trade_id": trade.trade_id,
            "status": "STOP",
            "reason": "OHLCV_UNAVAILABLE",
            "trade": trade.to_dict(),
            "symbol": trade.symbol,
            "timeframe": tf,
        }

    idx = trade.entry_index
    if idx is None:
        idx = as_of_index_at_or_before(candles, entry_ts)
    if idx is None or idx < 0:
        return {
            "trade_id": trade.trade_id,
            "status": "STOP",
            "reason": "ENTRY_INDEX_UNAVAILABLE",
            "trade": trade.to_dict(),
        }
    # Enforce no-lookahead: clamp to last candle at/before entry time
    asof = as_of_index_at_or_before(candles, entry_ts)
    if asof is not None:
        idx = min(idx, asof)

    engine = SignalEngine(scfg)
    analysis = engine.analyze_timeframe(
        trade.symbol, tf, candles, as_of_index=idx
    )
    swings = analysis.get("_swings_objs") or swings_for_timeframe(
        candles, scfg, tf, symbol=trade.symbol, as_of_index=idx
    )
    trend_obj = analysis.get("trend") or {}
    bos = analysis.get("bos")
    choch = analysis.get("choch")
    impulse = analysis.get("impulse") or {}
    pullback = analysis.get("pullback") or {}
    retest = analysis.get("retest") or {}

    ctx = entry_candle_metrics(candles, idx, atr_period=scfg.atr_period)
    atr_v = ctx.get("atr")
    labels = [getattr(s, "label", None) for s in swings if getattr(s, "label", None)]
    dchg = _direction_changes([str(x) for x in labels if x])

    # recent range / ATR
    look = min(40, idx + 1)
    window = candles[idx + 1 - look : idx + 1]
    hh = max(ohlc(window, i)[1] for i in range(len(window))) if window else None
    ll = min(ohlc(window, i)[2] for i in range(len(window))) if window else None
    recent_range_atr = None
    dist_edge = None
    if hh is not None and ll is not None and atr_v and atr_v > 0:
        recent_range_atr = (hh - ll) / atr_v
        close = ohlc(candles, idx)[3]
        dist_edge = min(abs(hh - close), abs(close - ll)) / atr_v

    bos_idx = None
    if isinstance(bos, dict):
        bos_idx = bos.get("confirmation_candle")
        if bos_idx is None:
            bos_idx = bos.get("break_index")
    bars_since_bos = (idx - int(bos_idx)) if isinstance(bos_idx, int) else None
    bars_since_bos_source = "engine_as_of"
    # If live as-of BOS is sticky/absent, prefer precomputed research field when present.
    if bars_since_bos is None and trade.raw.get("bars_since_BOS") is not None:
        try:
            bars_since_bos = int(trade.raw["bars_since_BOS"])
            bars_since_bos_source = "research_row_precomputed"
        except (TypeError, ValueError):
            pass
    bos_level = float(bos["broken_level"]) if isinstance(bos, dict) and bos.get("broken_level") is not None else None
    if bos_level is None and trade.raw.get("BOS_ATR_distance") is not None and atr_v:
        # distance only — level remains unknown
        pass
    close = ohlc(candles, idx)[3]
    bos_dist_atr = (
        abs(close - bos_level) / atr_v if bos_level is not None and atr_v and atr_v > 0 else None
    )
    if bos_dist_atr is None and trade.raw.get("BOS_ATR_distance") is not None:
        try:
            bos_dist_atr = float(trade.raw["BOS_ATR_distance"])
            bars_since_bos_source = bars_since_bos_source + "+bos_atr_from_row"
        except (TypeError, ValueError):
            pass
    bos_confirmed = bool(isinstance(bos, dict) and bos.get("state") == "CONFIRMED")
    if not bos_confirmed and trade.raw.get("bos_direction"):
        bos_confirmed = True  # research row had a BOS at entry; engine sticky state may differ

    highs_sw = [s for s in swings if s.swing_type == "HIGH" and s.bar_index <= idx]
    lows_sw = [s for s in swings if s.swing_type == "LOW" and s.bar_index <= idx]
    last_sh = highs_sw[-1] if highs_sw else None
    last_sl = lows_sw[-1] if lows_sw else None

    entry_vs_bos = classify_entry_vs_bos(
        bars_since_bos=bars_since_bos,
        bos_distance_atr=bos_dist_atr,
        bos_confirmed=bos_confirmed,
    )
    timing = classify_timing(bars_since_bos)

    # MTF trends (as-of entry time only)
    mtf: dict[str, Any] = {}
    trend_map: dict[str, str] = {}
    for htf in HTF_TFS:
        series = await cache.get(trade.symbol, htf)
        if not series:
            mtf[htf] = {"trend": UNAVAILABLE, "status": UNAVAILABLE}
            trend_map[htf] = UNAVAILABLE
            continue
        h_idx = as_of_index_at_or_before(series, entry_ts)
        if h_idx is None:
            mtf[htf] = {"trend": UNAVAILABLE, "status": UNAVAILABLE}
            trend_map[htf] = UNAVAILABLE
            continue
        h_sw = swings_for_timeframe(
            series, scfg, htf, symbol=trade.symbol, as_of_index=h_idx
        )
        h_tr = infer_trend(h_sw)
        tlab = str(h_tr.get("trend") or UNAVAILABLE)
        h_an = engine.analyze_timeframe(
            trade.symbol, htf, series, as_of_index=h_idx
        )
        _, hhights, hlows, hcloses, _ = series_ohlcv(list(series[: h_idx + 1]))
        h_atr = calc_atr(hhights, hlows, hcloses, scfg.atr_period)
        h_close = ohlc(series, h_idx)[3]
        mtf[htf] = {
            "trend": tlab,
            "structure_labels": [s.label for s in h_sw[-6:] if getattr(s, "label", None)],
            "bos": (h_an.get("bos") or {}).get("direction"),
            "choch": (h_an.get("choch") or {}).get("direction") if h_an.get("choch") else None,
            "atr_percent": (h_atr / h_close * 100.0) if h_atr and h_close else None,
            "as_of_index": h_idx,
            "status": "OK",
        }
        trend_map[htf] = tlab

    htf_state = classify_htf_state(direction=trade.direction, trends=trend_map)

    # Cheap proxy: unique swing breaks in lookback / bars (not a full BOS rescan).
    look_n = min(80, idx + 1)
    swing_breaks = sum(1 for s in swings if idx - look_n < s.bar_index <= idx)
    bos_freq = (swing_breaks / look_n) if look_n else None

    regime = classify_regime(
        trend=str(trend_obj.get("trend") or UNKNOWN),
        trend_strength=float(trend_obj["trend_strength"])
        if trend_obj.get("trend_strength") is not None
        else None,
        atr_percent=ctx.get("atr_percent") if isinstance(ctx.get("atr_percent"), (int, float)) else None,
        recent_range_atr=recent_range_atr,
        direction_changes=dchg,
        bos_freq=bos_freq,
        dist_to_range_edge_atr=dist_edge,
    )

    sweep = classify_sweep(
        direction=trade.direction,
        candles=candles,
        entry_idx=idx,
        swings=swings,
        atr_v=float(atr_v) if atr_v else None,
    )

    # S/D zones (as-of truncated) — FVG unavailable in codebase
    sd_state: dict[str, Any]
    try:
        sd_eng = SupplyDemandEngine(get_settings().indicators_config)
        truncated = list(candles[: idx + 1])
        zones = sd_eng.detect_zones(trade.symbol, tf, truncated) if len(truncated) >= 30 else []
        demand = [z for z in zones if z.zone_type.value == "demand"]
        supply = [z for z in zones if z.zone_type.value == "supply"]
        chosen = None
        if trade.direction.upper() == "LONG" and demand:
            chosen = min(demand, key=lambda z: abs((z.high + z.low) / 2 - close))
        elif trade.direction.upper() == "SHORT" and supply:
            chosen = min(supply, key=lambda z: abs((z.high + z.low) / 2 - close))
        if chosen is None:
            sd_state = {"state": "NO_ZONE", "zones_found": len(zones)}
        else:
            mid = (chosen.high + chosen.low) / 2
            dist = abs(close - mid)
            sd_state = {
                "state": "NEAR_ZONE" if atr_v and dist <= float(atr_v) else "FAR_FROM_ZONE",
                "zone_type": chosen.zone_type.value,
                "zone_timeframe": tf,
                "distance": dist,
                "distance_atr": (dist / atr_v) if atr_v and atr_v > 0 else None,
                "status": chosen.status.value,
                "zones_found": len(zones),
            }
    except Exception as exc:  # noqa: BLE001
        sd_state = {"state": UNAVAILABLE, "error": str(exc)}

    fvg_state = {
        "state": UNAVAILABLE,
        "reason": "No FVG engine exists in this codebase — not fabricated.",
    }

    # exit index for path
    exit_ts = _parse_ts(trade.exit_time)
    exit_idx = as_of_index_at_or_before(candles, exit_ts) if exit_ts else None
    path = path_excursions(
        direction=trade.direction,
        entry=float(trade.entry_price or close),
        stop=trade.stop_loss,
        tp=trade.take_profit,
        candles=candles,
        entry_idx=idx,
        exit_idx=exit_idx,
    )
    if trade.mae_r is not None and path.get("mae_r") is None:
        path["mae_r"] = trade.mae_r
    if trade.mfe_r is not None and path.get("mfe_r") is None:
        path["mfe_r"] = trade.mfe_r

    sl_atr = None
    if trade.stop_loss is not None and atr_v and atr_v > 0 and trade.entry_price:
        sl_atr = abs(float(trade.entry_price) - float(trade.stop_loss)) / atr_v

    # Lifecycle bar distances (best-effort from engines)
    bars_impulse = None
    if impulse.get("bar_index") is not None:
        try:
            bars_impulse = max(0, idx - int(impulse["bar_index"]))
        except (TypeError, ValueError):
            bars_impulse = None
    bars_pullback = UNAVAILABLE  # engine does not expose pullback start index
    bars_retest = UNAVAILABLE

    session_hour = entry_ts.astimezone(timezone.utc).hour
    session_bin = UNKNOWN
    from app.research.trade_plan_forensics.thresholds import SESSION_BINS, SESSION_REGION_RULES

    for name, rng in SESSION_BINS:
        if session_hour in rng:
            session_bin = name
            break
    regions = [name for name, rng in SESSION_REGION_RULES if session_hour in rng]

    is_win = trade.R is not None and float(trade.R) > 0
    is_loss = trade.R is not None and float(trade.R) <= 0

    return {
        "trade_id": trade.trade_id,
        "status": "OK",
        "trade": trade.to_dict(),
        "entry_context": ctx,
        "local_structure": {
            "trend": trend_obj.get("trend"),
            "trend_strength": trend_obj.get("trend_strength"),
            "structure_sequence": trend_obj.get("structure_sequence"),
            "hh_hl_lh_ll": labels[-8:],
            "bos": bos,
            "choch": choch,
            "last_swing_high": last_sh.price if last_sh else None,
            "last_swing_low": last_sl.price if last_sl else None,
            "bars_since_swing_high": (idx - last_sh.bar_index) if last_sh else None,
            "bars_since_swing_low": (idx - last_sl.bar_index) if last_sl else None,
            "bars_since_bos": bars_since_bos,
            "bars_since_bos_source": bars_since_bos_source,
            "bos_price": bos_level,
            "entry_distance_from_bos": abs(close - bos_level) if bos_level is not None else None,
            "bos_distance_atr": bos_dist_atr,
            "impulse": {
                "is_impulse": impulse.get("is_impulse"),
                "quality": impulse.get("quality"),
                "atr_multiple": impulse.get("atr_multiple"),
                "state": impulse.get("state") or impulse.get("impulse_state"),
            },
            "pullback": {
                "state": pullback.get("pullback_state"),
                "retracement_pct": pullback.get("retracement_percentage"),
                "structure_intact": pullback.get("structure_intact"),
            },
            "retest": {
                "retest": retest.get("retest"),
                "state": retest.get("state"),
            },
            "entry_vs_bos_class": entry_vs_bos,
        },
        "mtf": mtf,
        "htf_state": htf_state,
        "regime": regime,
        "entry_timing": {
            "class": timing,
            "bars_from_BOS_to_entry": bars_since_bos,
            "bars_from_impulse_to_entry": bars_impulse,
            "bars_from_pullback_to_entry": bars_pullback,
            "bars_from_retest_to_entry": bars_retest,
            "entry_price_distance_from_BOS": abs(close - bos_level) if bos_level is not None else None,
            "distance_in_ATR": bos_dist_atr,
            "distance_from_FVG": UNAVAILABLE,
            "distance_from_SD_zone": sd_state.get("distance"),
            "distance_from_swing": (
                abs(close - float(last_sl.price))
                if trade.direction.upper() == "LONG" and last_sl
                else abs(close - float(last_sh.price))
                if last_sh
                else None
            ),
            "thresholds_doc": {
                "EARLY_max_bars": TIMING_EARLY_MAX,
                "TIMELY_max_bars": TIMING_TIMELY_MAX,
                "LATE_max_bars": TIMING_LATE_MAX,
            },
        },
        "liquidity": sweep,
        "fvg": fvg_state,
        "supply_demand": sd_state,
        "stop_forensics": {
            "sl_distance": path.get("sl_distance"),
            "sl_distance_pct": path.get("sl_distance_pct"),
            "sl_distance_atr": sl_atr,
            "path": path,
        },
        "tp_forensics": {
            "mfe_r": path.get("mfe_r"),
            "mae_r": path.get("mae_r"),
            "reached_0_5R": path.get("reached_0_5R"),
            "reached_1R": path.get("reached_1R"),
            "reached_2R": path.get("reached_2R"),
            "touched_tp": path.get("touched_tp"),
            "near_tp_before_sl": path.get("near_tp_before_sl"),
        },
        "session": {
            "utc_hour": session_hour,
            "bin": session_bin,
            "regions_utc_approx": regions,
        },
        "outcome_class": "WIN" if is_win else "LOSS" if is_loss else UNKNOWN,
        "data_quality": {
            "candles_available": len(candles),
            "entry_index": idx,
            "no_lookahead": True,
            "fvg": UNAVAILABLE,
            "balance": trade.balance,
            "fees": trade.fees if trade.fees is not None else UNAVAILABLE,
        },
    }
