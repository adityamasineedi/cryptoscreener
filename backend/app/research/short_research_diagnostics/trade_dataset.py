"""Build trade-level SHORT diagnostic rows from research trades + candles."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.engines.mtf.indicators import atr as calc_atr
from app.engines.mtf.indicators import atr_series as calc_atr_series
from app.research.short_research_diagnostics.classifiers import (
    classify_entry_quality,
    classify_market_regimes,
    classify_stop_placement,
    support_distance_atr,
)
from app.research.short_research_diagnostics.metrics import (
    atr_normalized_distance,
    candle_ohlc,
    entry_delay_bars,
    entry_extension_atr,
    initial_risk,
    parse_ts,
    price_at_offset,
    reward_to_risk,
)
from app.research.trade_fees import enrich_trade_execution
from app.signals._candle_utils import candle_time, series_ohlcv
from app.signals.signal_engine import SignalEngine
from app.signals.config import SignalConfig
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend


def _find_entry_index(
    candles: Sequence[Mapping[str, Any]],
    entry_time: Any,
    fallback: int | None = None,
) -> int | None:
    if fallback is not None:
        return int(fallback)
    target = parse_ts(entry_time)
    if target is None or not candles:
        return None
    best = None
    for i, c in enumerate(candles):
        t = candle_time(c)
        if t is None:
            continue
        if t.tzinfo is None:
            from datetime import timezone

            t = t.replace(tzinfo=timezone.utc)
        if t <= target:
            best = i
        else:
            break
    return best


def _atr_series_cached(
    candles: Sequence[Mapping[str, Any]],
    *,
    period: int = 14,
    cache: dict[int, list[float | None]] | None = None,
) -> list[float | None]:
    key = id(candles)
    if cache is not None and key in cache:
        return cache[key]
    _, highs, lows, closes, _ = series_ohlcv(list(candles))
    try:
        series = calc_atr_series(highs, lows, closes, period)
    except Exception:
        series = [None] * len(candles)
    if cache is not None:
        cache[key] = series
    return series


def _atr_at(
    candles: Sequence[Mapping[str, Any]],
    idx: int,
    period: int = 14,
    *,
    atr_cache: dict[int, list[float | None]] | None = None,
) -> float | None:
    if idx < 0 or idx >= len(candles):
        return None
    series = _atr_series_cached(candles, period=period, cache=atr_cache)
    val = series[idx] if idx < len(series) else None
    return float(val) if val is not None else None


def _atr_percentile(
    candles: Sequence[Mapping[str, Any]],
    idx: int,
    atr_now: float | None,
    lookback: int = 200,
    period: int = 14,
    *,
    atr_cache: dict[int, list[float | None]] | None = None,
) -> float | None:
    if atr_now is None or idx < period + 5:
        return None
    series = _atr_series_cached(candles, period=period, cache=atr_cache)
    start = max(period, idx - lookback)
    window = [float(x) for x in series[start : idx + 1] if x is not None]
    if len(window) < 10:
        return None
    below = sum(1 for x in window if x <= atr_now)
    return below / len(window)


def _recent_swing_levels(
    candles: Sequence[Mapping[str, Any]],
    idx: int,
    *,
    timeframe: str = "1h",
    lookback: int = 80,
    use_swing_detector: bool = False,
) -> dict[str, Any]:
    """Lightweight structural context — local window only (no full-history swing scan)."""
    if idx < 0 or not candles:
        return {
            "swings": [],
            "recent_swing_high": None,
            "recent_swing_low": None,
            "trend_state": "",
            "lh_ll_timestamps": [],
        }
    start = max(0, idx - lookback)
    window = list(candles[start : idx + 1])
    hs = []
    ls = []
    closes = []
    for c in window:
        o, h, l, cl = candle_ohlc(c)
        hs.append(h)
        ls.append(l)
        closes.append(cl)
    recent_high = max(hs[-40:]) if hs else None
    recent_low = min(ls[-40:]) if ls else None
    trend_state = ""
    lh_ll: list[str] = []
    swings: list[Any] = []
    if use_swing_detector:
        cfg = SignalConfig()
        swings = swings_for_timeframe(window, cfg, timeframe, symbol="DIAG")
        sh = [s for s in swings if getattr(s, "swing_type", None) == "HIGH"]
        sl = [s for s in swings if getattr(s, "swing_type", None) == "LOW"]
        trend = infer_trend(swings)
        trend_state = str(trend.get("trend") or "")
        if sh:
            recent_high = float(sh[-1].price)
        if sl:
            recent_low = float(sl[-1].price)
        lh_ll = [
            s.timestamp.isoformat() if hasattr(s.timestamp, "isoformat") else str(s.timestamp)
            for s in swings
            if getattr(s, "label", None) in ("LH", "LL")
        ]
    else:
        # Slope proxy from closes — research diagnostic only, not a production trend engine.
        if len(closes) >= 20:
            early = sum(closes[:10]) / 10.0
            late = sum(closes[-10:]) / 10.0
            if late < early * 0.995:
                trend_state = "BEARISH"
            elif late > early * 1.005:
                trend_state = "BULLISH"
            else:
                trend_state = "NEUTRAL"
    return {
        "swings": swings,
        "recent_swing_high": recent_high,
        "recent_swing_low": recent_low,
        "trend_state": trend_state,
        "lh_ll_timestamps": lh_ll,
    }


def _signal_context_at_bar(
    symbol: str,
    candles: Sequence[Mapping[str, Any]],
    idx: int,
    *,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Rebuild setup context at entry bar (as_of) — no post-entry data."""
    cfg = SignalConfig()
    engine = SignalEngine(cfg)
    tf = engine.analyze_timeframe(
        symbol,
        "1h",
        list(candles[: idx + 1]),
        as_of_index=idx,
    )
    trend = tf.get("trend") or {}
    bos = tf.get("bos") or {}
    retest = tf.get("retest") or {}
    o, h, l, c = candle_ohlc(candles[idx])
    atr = _atr_at(candles, idx)

    # HTF trends from provided series truncated to entry time
    htf_1h = None
    htf_4h = None
    entry_ts = candle_time(candles[idx])
    for series, key in ((candles_1h or candles, "1h"), (candles_4h, "4h")):
        if not series:
            continue
        truncated = []
        for bar in series:
            t = candle_time(bar)
            if t is None or entry_ts is None:
                continue
            if t.tzinfo is None:
                from datetime import timezone

                t = t.replace(tzinfo=timezone.utc)
            et = entry_ts if entry_ts.tzinfo else entry_ts.replace(tzinfo=__import__("datetime").timezone.utc)
            if t <= et:
                truncated.append(bar)
        if len(truncated) < 30:
            continue
        ht = engine.analyze_timeframe(symbol, key, truncated, as_of_index=len(truncated) - 1)
        lab = str((ht.get("trend") or {}).get("trend") or "")
        if key == "1h":
            htf_1h = lab
        else:
            htf_4h = lab

    bos_level = bos.get("broken_level")
    if bos_level is None:
        bos_level = bos.get("level")
    bos_ts = bos.get("timestamp") or bos.get("bos_time") or bos.get("confirmation_time")
    ref_low = bos.get("reference_swing_low") or bos.get("swing_low")
    swings_meta = _recent_swing_levels(candles, idx)
    support = swings_meta.get("recent_swing_low")
    bos_range = (h - l) if h and l else None
    bos_atr_mult = (bos_range / atr) if bos_range is not None and atr else None
    bos_close_dist = None
    if bos_level is not None:
        bos_close_dist = float(c) - float(bos_level)

    return {
        "trend_state": str(trend.get("trend") or swings_meta.get("trend_state") or ""),
        "lh_ll_timestamps": swings_meta.get("lh_ll_timestamps") or [],
        "bos_timestamp": bos_ts,
        "bos_level": float(bos_level) if bos_level is not None else None,
        "bos_close": float(c),
        "bos_close_distance_below_swing_low": (
            float(ref_low) - float(c)
            if ref_low is not None
            else (-bos_close_dist if bos_close_dist is not None else None)
        ),
        "bos_candle_range": bos_range,
        "bos_candle_atr_multiple": bos_atr_mult,
        "retest": bool(retest.get("retest")),
        "htf_1h_state": htf_1h or str((tf.get("htf") or {}).get("trend_1h") or ""),
        "htf_4h_state": htf_4h or str((tf.get("htf") or {}).get("trend_4h") or ""),
        "recent_swing_high": swings_meta.get("recent_swing_high"),
        "recent_swing_low": support,
        "atr_at_signal": atr,
        "support_level": support,
    }


def build_diagnostic_trade_row(
    trade: Mapping[str, Any],
    *,
    candles: Sequence[Mapping[str, Any]],
    risk_usd: float = 20.0,
    candles_1h: Sequence[Mapping[str, Any]] | None = None,
    candles_4h: Sequence[Mapping[str, Any]] | None = None,
    btc_trend: str | None = None,
    enrich_context: bool = True,
    atr_cache: dict[int, list[float | None]] | None = None,
) -> dict[str, Any]:
    """Persist one historical SHORT trade diagnostic record."""
    enriched = enrich_trade_execution(trade, risk_usd=risk_usd)
    entry = float(enriched.get("entry_price") or trade.get("entry_price") or 0)
    stop = float(enriched.get("stop_price") or trade.get("stop_price") or 0)
    tp1 = trade.get("tp1") or trade.get("take_profit_price")
    tp2 = trade.get("tp2")
    tp3 = trade.get("tp3")
    entry_idx = _find_entry_index(
        candles,
        trade.get("entry_time") or trade.get("signal_time"),
        fallback=trade.get("entry_index"),
    )
    snap = trade.get("condition_snapshot") or {}
    entry_type = str(
        enriched.get("entry_type") or trade.get("entry_type") or snap.get("entry_type") or "MARKET"
    )

    ctx: dict[str, Any] = {}
    if enrich_context and entry_idx is not None:
        ctx = _signal_context_at_bar(
            str(trade.get("symbol") or "UNKNOWN"),
            candles,
            entry_idx,
            candles_1h=candles_1h,
            candles_4h=candles_4h,
        )
    else:
        swings_meta = (
            _recent_swing_levels(candles, entry_idx)
            if entry_idx is not None and candles
            else {}
        )
        atr_fast = (
            _atr_at(candles, entry_idx, atr_cache=atr_cache)
            if entry_idx is not None and candles
            else trade.get("atr_at_signal")
        )
        ctx = {
            "trend_state": snap.get("trend")
            or swings_meta.get("trend_state")
            or "",
            "htf_1h_state": snap.get("trend_1h") or "",
            "htf_4h_state": snap.get("trend_4h") or "",
            "bos_level": trade.get("bos_level") or snap.get("bos_level"),
            "bos_timestamp": trade.get("bos_timestamp")
            or trade.get("bos_candle_timestamp")
            or trade.get("signal_time"),
            "atr_at_signal": atr_fast,
            "recent_swing_high": trade.get("recent_swing_high")
            or swings_meta.get("recent_swing_high"),
            "recent_swing_low": trade.get("recent_swing_low")
            or swings_meta.get("recent_swing_low"),
            "retest": entry_type == "LIMIT_RETEST",
            "lh_ll_timestamps": trade.get("lh_ll_timestamps")
            or swings_meta.get("lh_ll_timestamps")
            or [],
            "support_level": trade.get("support_level")
            or swings_meta.get("recent_swing_low"),
            "bos_candle_range": None,
            "bos_candle_atr_multiple": None,
            "bos_close_distance_below_swing_low": None,
        }

    atr = ctx.get("atr_at_signal")
    bos_level = ctx.get("bos_level")
    # Market-entry SHORT often fills at/near BOS close; use recent swing low as proxy BOS level.
    if bos_level is None and ctx.get("recent_swing_low") is not None:
        bos_level = ctx.get("recent_swing_low")
        ctx["bos_level"] = bos_level
    delay = entry_delay_bars(
        trade.get("entry_time") or trade.get("signal_time"),
        ctx.get("bos_timestamp") or trade.get("signal_time"),
    )
    ext = entry_extension_atr(entry, bos_level, atr)
    stop_atr = atr_normalized_distance(entry, stop, atr)
    tp_atr = atr_normalized_distance(entry, float(tp1), atr) if tp1 is not None else None
    rr = reward_to_risk(entry, stop, float(tp1) if tp1 is not None else None, direction="SHORT")
    dist_support = support_distance_atr(entry, ctx.get("support_level"), atr)
    atr_pct = (
        _atr_percentile(candles, entry_idx, atr, atr_cache=atr_cache)
        if entry_idx is not None
        else trade.get("atr_percentile")
    )

    # Path prices after signal
    price_signal = price_at_offset(candles, entry_idx, 0) if entry_idx is not None else entry
    forward = {
        f"price_{n}_bars_after": (
            price_at_offset(candles, entry_idx, n) if entry_idx is not None else None
        )
        for n in (1, 3, 6, 12)
    }

    mfe = trade.get("mfe")
    mae = trade.get("mae")
    mfe_r = trade.get("mfe_r")
    mae_r = trade.get("mae_r")
    if entry_idx is not None and (mfe is None or mae is None):
        exit_idx = trade.get("exit_index")
        end = int(exit_idx) if exit_idx is not None else min(len(candles) - 1, entry_idx + int(trade.get("holding_bars") or 0))
        highs = []
        lows = []
        for i in range(entry_idx + 1, max(entry_idx + 1, end + 1)):
            if i >= len(candles):
                break
            _, h, l, _ = candle_ohlc(candles[i])
            highs.append(h)
            lows.append(l)
        from app.research.short_research_diagnostics.metrics import compute_mfe_mae_short

        exc = compute_mfe_mae_short(
            entry_price=entry, stop_price=stop, highs=highs, lows=lows
        )
        mfe = exc["mfe"] if mfe is None else mfe
        mae = exc["mae"] if mae is None else mae
        mfe_r = exc["mfe_r"] if mfe_r is None else mfe_r
        mae_r = exc["mae_r"] if mae_r is None else mae_r

    entry_class = classify_entry_quality(
        entry_type=entry_type,
        entry_extension_atr_value=ext,
        bos_level=bos_level,
        entry_price=entry,
        atr=atr,
        candles=candles,
        entry_index=entry_idx,
        retest_flag=bool(ctx.get("retest")),
    )
    stop_class = classify_stop_placement(
        direction="SHORT",
        entry_price=entry,
        stop_price=stop,
        atr=atr,
        recent_swing_high=ctx.get("recent_swing_high"),
        outcome=str(trade.get("outcome") or ""),
        mfe_r=float(mfe_r) if mfe_r is not None else None,
        mae_r=float(mae_r) if mae_r is not None else None,
    )
    regimes = classify_market_regimes(
        symbol_trend=str(ctx.get("trend_state") or ""),
        htf_1h=str(ctx.get("htf_1h_state") or snap.get("trend_1h") or ""),
        htf_4h=str(ctx.get("htf_4h_state") or snap.get("trend_4h") or ""),
        btc_trend=btc_trend,
        atr_percentile=float(atr_pct) if atr_pct is not None else None,
        distance_to_support_atr=dist_support,
    )

    risk = initial_risk(entry, stop)
    r_net = enriched.get("r_net")
    if r_net is None and enriched.get("net_pnl") is not None and risk_usd:
        r_net = float(enriched["net_pnl"]) / float(risk_usd)

    # Prefer blotter-accurate PnL already attached by the research backtest.
    gross_pnl = trade.get("gross_pnl")
    if gross_pnl is None:
        gross_pnl = trade.get("gross_pnl_usd")
    if gross_pnl is None:
        gross_pnl = enriched.get("gross_pnl")
    net_pnl = trade.get("net_pnl")
    if net_pnl is None:
        net_pnl = trade.get("net_pnl_usd")
    if net_pnl is None:
        net_pnl = enriched.get("net_pnl")
    fees = trade.get("fees")
    if fees is None:
        fees = trade.get("fee_total_usd")
    if fees is None:
        fees = trade.get("total_fee")
    if fees is None:
        fees = enriched.get("total_fee")
    r_final = trade.get("R")
    if r_final is None:
        r_final = trade.get("r_net")
    if r_final is None:
        r_final = r_net if r_net is not None else trade.get("r_multiple")

    return {
        "symbol": str(trade.get("symbol") or "").upper(),
        "timeframe": str(trade.get("timeframe") or "1h"),
        "direction": "SHORT",
        "entry_time": trade.get("entry_time") or trade.get("signal_time"),
        "exit_time": trade.get("exit_time"),
        "outcome": trade.get("outcome"),
        "entry_type": entry_type,
        "entry_price": entry,
        "stop_price": stop,
        "TP1": float(tp1) if tp1 is not None else None,
        "TP2": float(tp2) if tp2 is not None else None,
        "TP3": float(tp3) if tp3 is not None else None,
        "initial_risk": risk,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "entry_fee": trade.get("entry_fee") or enriched.get("entry_fee"),
        "exit_fee": trade.get("exit_fee") or enriched.get("exit_fee"),
        "net_pnl": net_pnl,
        "R": r_final,
        "hold_bars": trade.get("holding_bars"),
        "MFE": mfe,
        "MAE": mae,
        "MFE_R": mfe_r,
        "MAE_R": mae_r,
        "price_at_signal": price_signal,
        **forward,
        "trend_state": ctx.get("trend_state"),
        "lh_ll_timestamps": ctx.get("lh_ll_timestamps") or [],
        "BOS_timestamp": ctx.get("bos_timestamp"),
        "BOS_close_distance_below_swing_low": ctx.get("bos_close_distance_below_swing_low"),
        "BOS_candle_range": ctx.get("bos_candle_range"),
        "BOS_candle_ATR_multiple": ctx.get("bos_candle_atr_multiple"),
        "distance_entry_to_BOS_level": (
            abs(entry - float(bos_level)) if bos_level is not None else None
        ),
        "distance_entry_to_recent_swing_high": (
            abs(entry - float(ctx["recent_swing_high"]))
            if ctx.get("recent_swing_high") is not None
            else None
        ),
        "stop_distance_atr": stop_atr,
        "TP_distance_atr": tp_atr,
        "reward_to_risk_ratio": rr,
        "HTF_1h_state": ctx.get("htf_1h_state"),
        "HTF_4h_state": ctx.get("htf_4h_state"),
        "entry_delay_bars": delay,
        "entry_extension_atr": ext,
        "entry_quality": entry_class,
        "stop_class": stop_class,
        "regimes": regimes,
        "atr_at_signal": atr,
        "atr_percentile": atr_pct,
        "distance_to_support_atr": dist_support,
        "primary_regime": regimes[0] if regimes else None,
        "entry_index": entry_idx,
        "exit_index": trade.get("exit_index"),
        "maker_taker": enriched.get("fee_type"),
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
    }


def build_diagnostic_dataset(
    trades: Sequence[Mapping[str, Any]],
    *,
    candles_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    candles_4h_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    risk_usd: float = 20.0,
    btc_trend_by_time: Mapping[str, str] | None = None,
    enrich_context: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    c4h_map = candles_4h_by_symbol or {}
    atr_cache: dict[int, list[float | None]] = {}
    for t in trades:
        if str(t.get("direction") or "SHORT").upper() != "SHORT":
            continue
        outcome = str(t.get("outcome") or "")
        if outcome in ("", "OPEN", "None"):
            continue
        sym = str(t.get("symbol") or "").upper()
        candles = candles_by_symbol.get(sym) or []
        btc_trend = None
        if btc_trend_by_time:
            key = str(t.get("entry_time") or t.get("signal_time") or "")[:13]
            btc_trend = btc_trend_by_time.get(key)
        rows.append(
            build_diagnostic_trade_row(
                t,
                candles=candles,
                risk_usd=risk_usd,
                candles_1h=candles,
                candles_4h=c4h_map.get(sym),
                btc_trend=btc_trend,
                enrich_context=enrich_context and bool(candles),
                atr_cache=atr_cache,
            )
        )
    return rows
