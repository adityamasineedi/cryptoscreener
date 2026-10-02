"""Volume analytics — SMA, RVOL, z-score, taker flow, expansion/contraction."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any, Mapping

from app.engines.mtf.indicators import relative_volume, volume_sma
from app.models.schemas import DataStatus, FreshValue


def _series(candles: list[Mapping[str, Any]], field: str, *alt: str) -> list[float]:
    out: list[float] = []
    for c in candles:
        val = c.get(field)
        if val is None:
            for a in alt:
                if a in c:
                    val = c[a]
                    break
        if val is not None:
            out.append(float(val))
    return out


def volume_change_pct(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def volume_zscore(volumes: list[float], period: int) -> float | None:
    if period < 2 or len(volumes) < period:
        return None
    window = volumes[-period:]
    mu = mean(window)
    if mu == 0:
        return None
    sigma = pstdev(window)
    if sigma == 0:
        return 0.0
    return (window[-1] - mu) / sigma


def taker_imbalance(
    taker_buy: float | None, volume: float | None
) -> float | None:
    """Buy share minus sell share in [-1, 1]."""
    if taker_buy is None or volume is None or volume <= 0:
        return None
    buy_share = taker_buy / volume
    return 2.0 * buy_share - 1.0


def volume_regime(rvol: float | None, threshold: float) -> str | None:
    if rvol is None:
        return None
    if rvol >= threshold:
        return "expansion"
    if rvol < 1.0 / threshold:
        return "contraction"
    return "normal"


class VolumeEngine:
    """Volume metrics with raw and normalized output dicts."""

    SOURCE = "volume_engine"

    def __init__(self, indicators_config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(indicators_config or {})
        vol = dict(cfg.get("volume") or cfg)
        self.sma_period: int = int(vol.get("sma_period", 20))
        self.zscore_period: int = int(vol.get("zscore_period", 20))
        self.rvol_threshold: float = float(vol.get("rvol_threshold", 1.5))

    def compute(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        _ = symbol
        _ = timeframe
        volumes = _series(candles, "volume", "v")
        if not volumes:
            return {"raw": {}, "normalized": {}, "fresh": {}}

        ts = datetime.now(timezone.utc)
        sma = volume_sma(volumes, self.sma_period)
        cur = volumes[-1]
        prev = volumes[-2] if len(volumes) >= 2 else None
        rvol = relative_volume(cur, sma)
        chg = volume_change_pct(cur, prev)
        z = volume_zscore(volumes, self.zscore_period)

        taker_buy_series = _series(candles, "taker_buy_volume", "taker_buy_base")
        taker_buy = taker_buy_series[-1] if taker_buy_series else None
        imb = taker_imbalance(taker_buy, cur)
        taker_sell = (cur - taker_buy) if taker_buy is not None else None

        regime = volume_regime(rvol, self.rvol_threshold)

        raw = {
            "volume": cur,
            "volume_sma": sma,
            "relative_volume": rvol,
            "volume_change_pct": chg,
            "volume_zscore": z,
            "taker_buy_volume": taker_buy,
            "taker_sell_volume": taker_sell,
            "taker_imbalance": imb,
            "regime": regime,
        }

        normalized: dict[str, float | None] = {}
        if rvol is not None:
            normalized["relative_volume"] = min(rvol / self.rvol_threshold, 3.0)
        if z is not None:
            normalized["volume_zscore"] = max(-3.0, min(3.0, z / 3.0))
        if imb is not None:
            normalized["taker_imbalance"] = imb
        if chg is not None:
            normalized["volume_change_pct"] = max(-100.0, min(100.0, chg)) / 100.0

        fresh: dict[str, FreshValue[Any]] = {}
        for key, val in raw.items():
            if val is None:
                fresh[key] = FreshValue.waiting(self.SOURCE)
            else:
                fresh[key] = FreshValue(
                    value=val,
                    timestamp=ts,
                    source=self.SOURCE,
                    status=DataStatus.LIVE,
                )

        return {"raw": raw, "normalized": normalized, "fresh": fresh}
