"""Compact independent numpy reference implementations.

Cross-check only — NOT a second production engine.
Uses numpy only (no new production dependency).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np


def candles_to_arrays(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(candles)
    o = np.empty(n)
    h = np.empty(n)
    l = np.empty(n)
    c = np.empty(n)
    v = np.empty(n)
    for i, row in enumerate(candles):
        o[i] = float(row.get("open") or row.get("o") or 0)
        h[i] = float(row.get("high") or row.get("h") or 0)
        l[i] = float(row.get("low") or row.get("l") or 0)
        c[i] = float(row.get("close") or row.get("c") or 0)
        v[i] = float(row.get("volume") or row.get("v") or 0)
    return o, h, l, c, v


def _shifted_rolling_min(values: np.ndarray, window: int) -> np.ndarray:
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(window, n):
        out[i] = float(np.min(values[i - window : i]))
    return out


def _shifted_rolling_max(values: np.ndarray, window: int) -> np.ndarray:
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(window, n):
        out[i] = float(np.max(values[i - window : i]))
    return out


def _shifted_sma(values: np.ndarray, window: int) -> np.ndarray:
    n = len(values)
    out = np.full(n, np.nan)
    csum = np.cumsum(values)
    for i in range(window, n):
        total = csum[i - 1] - (csum[i - window - 1] if i - window - 1 >= 0 else 0.0)
        out[i] = float(total / window)
    return out


def ref_sweep_indices(candles: Sequence[Mapping[str, Any]], lookback: int = 48) -> list[int]:
    """Sweep: low < prior rolling min low AND close > that level. Shifted window."""
    _, _, lows, closes, _ = candles_to_arrays(candles)
    prior = _shifted_rolling_min(lows, lookback)
    out: list[int] = []
    for i in range(lookback, len(lows)):
        if np.isnan(prior[i]):
            continue
        if lows[i] < prior[i] and closes[i] > prior[i]:
            out.append(i)
    return out


def ref_bullish_fvg(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Three-candle bullish FVG known at candle 3 index."""
    _, highs, lows, _, _ = candles_to_arrays(candles)
    out: list[dict[str, Any]] = []
    for i in range(2, len(highs)):
        if highs[i - 2] < lows[i]:
            out.append(
                {
                    "created_at": i,
                    "lower": float(highs[i - 2]),
                    "upper": float(lows[i]),
                    "direction": "BULLISH",
                }
            )
    return out


def ref_discount_mask(candles: Sequence[Mapping[str, Any]], window: int = 24) -> np.ndarray:
    _, highs, lows, closes, _ = candles_to_arrays(candles)
    sh = _shifted_rolling_max(highs, window)
    sl = _shifted_rolling_min(lows, window)
    eq = sl + 0.50 * (sh - sl)
    return (closes < eq) & ~np.isnan(eq)


def ref_volume_bos_indices(
    candles: Sequence[Mapping[str, Any]],
    *,
    bos_lookback: int = 10,
    vol_sma: int = 50,
    vol_ratio: float = 3.0,
) -> list[int]:
    _, highs, _, closes, volumes = candles_to_arrays(candles)
    prior_max = _shifted_rolling_max(highs, bos_lookback)
    sma = _shifted_sma(volumes, vol_sma)
    out: list[int] = []
    for i in range(len(closes)):
        if np.isnan(prior_max[i]) or np.isnan(sma[i]) or sma[i] <= 0:
            continue
        if closes[i] > prior_max[i] and volumes[i] > vol_ratio * sma[i]:
            out.append(i)
    return out


def ref_fvg_full_mitigation_bullish(
    zones: Sequence[Mapping[str, Any]],
    candles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Mark bullish FVG fully mitigated when a later bar's low <= lower."""
    _, _, lows, closes, _ = candles_to_arrays(candles)
    out = []
    for z in zones:
        created = int(z["created_at"])
        lower = float(z["lower"])
        mitigated = False
        mit_i = None
        for i in range(created + 1, len(lows)):
            if lows[i] <= lower:
                mitigated = True
                mit_i = i
                break
        out.append(
            {
                **dict(z),
                "mitigated": mitigated,
                "mitigation_time": mit_i,
                "mitigation_price": float(closes[mit_i]) if mit_i is not None else None,
            }
        )
    return out


# Back-compat alias used by tests
def candles_to_df(candles: Sequence[Mapping[str, Any]]) -> Sequence[Mapping[str, Any]]:
    return candles
