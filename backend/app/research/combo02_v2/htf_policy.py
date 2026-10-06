"""COMBO_02 v2 HTF policy — softer than v1 hard HTF_ALIGNED gate.

Distinguishes true NEUTRAL from INSUFFICIENT_DATA / missing series.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.bos_strategy_comparison.htf import htf_trends_for_setup_bar
from app.signals.config import SignalConfig

_DIRECTIONAL = frozenset({"BULLISH", "BEARISH"})
_INSUFFICIENT = frozenset({"INSUFFICIENT_DATA", "WAITING", "", "NONE", "UNKNOWN"})


def _norm(label: str | None) -> str:
    return str(label or "").upper().strip()


def fetch_htf_trends(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    signal_config: SignalConfig,
    candles_1h: Sequence[Mapping[str, Any]] | None,
    candles_4h: Sequence[Mapping[str, Any]] | None,
    htf_trend_cache: dict[tuple[str, int], str] | None = None,
    htf_idx_1h_map: Sequence[int | None] | None = None,
    htf_idx_4h_map: Sequence[int | None] | None = None,
) -> dict[str, Any]:
    tf = (timeframe or "").lower()
    series_1h = candles_1h if candles_1h is not None else (candles if tf == "1h" else None)
    series_4h = candles_4h if candles_4h is not None else (candles if tf == "4h" else None)
    meta: dict[str, Any] = {
        "trend_4h": None,
        "trend_1h": None,
        "data_ok": False,
        "reason": None,
    }
    if not series_1h or not series_4h:
        meta["reason"] = "HTF candles missing — fail closed"
        return meta
    htf = htf_trends_for_setup_bar(
        symbol=symbol,
        setup_candles=candles,
        as_of_index=as_of_index,
        candles_4h=series_4h,
        candles_1h=series_1h,
        config=signal_config,
        trend_cache=htf_trend_cache,
        idx_1h_map=htf_idx_1h_map,
        idx_4h_map=htf_idx_4h_map,
        setup_timeframe=tf or "1h",
        htf_require_fully_closed=False,
    )
    t4 = _norm(htf.get("trend_4h"))
    t1 = _norm(htf.get("trend_1h"))
    meta["trend_4h"] = t4
    meta["trend_1h"] = t1
    if t4 in _INSUFFICIENT or t1 in _INSUFFICIENT:
        meta["reason"] = f"HTF insufficient history (4h={t4}, 1h={t1})"
        meta["data_ok"] = False
        return meta
    meta["data_ok"] = True
    meta["reason"] = "HTF_OK"
    return meta


def trend_htf_allows(direction: str, htf: Mapping[str, Any]) -> tuple[bool, str]:
    """Trend continuation: allow aligned or neutral 4H; reject clear opposition.

    Insufficient data must already be rejected by fetch_htf_trends (data_ok).
    """
    if not htf.get("data_ok"):
        return False, str(htf.get("reason") or "HTF data unavailable")
    t4 = _norm(htf.get("trend_4h"))
    t1 = _norm(htf.get("trend_1h"))
    want_long = direction == "LONG"
    # Clear opposition on 4H
    if want_long and t4 == "BEARISH":
        return False, f"HTF_4H_OPPOSITION (4h={t4}, 1h={t1})"
    if not want_long and t4 == "BULLISH":
        return False, f"HTF_4H_OPPOSITION (4h={t4}, 1h={t1})"
    # Aligned 4H
    if want_long and t4 == "BULLISH":
        return True, f"HTF_4H_ALIGNED (4h={t4}, 1h={t1})"
    if not want_long and t4 == "BEARISH":
        return True, f"HTF_4H_ALIGNED (4h={t4}, 1h={t1})"
    # Neutral 4H — require 1H directional agreement with trade
    if t4 == "NEUTRAL":
        if want_long and t1 == "BULLISH":
            return True, f"HTF_4H_NEUTRAL_1H_ALIGNED (4h={t4}, 1h={t1})"
        if not want_long and t1 == "BEARISH":
            return True, f"HTF_4H_NEUTRAL_1H_ALIGNED (4h={t4}, 1h={t1})"
        return False, f"HTF_4H_NEUTRAL_1H_NOT_ALIGNED (4h={t4}, 1h={t1})"
    return False, f"HTF_TREND_REJECT (4h={t4}, 1h={t1})"
