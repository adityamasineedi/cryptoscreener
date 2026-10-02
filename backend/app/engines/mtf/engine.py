"""MTF indicator engine — recalculates on closed candles only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.engines.mtf import indicators as ind
from app.models.schemas import DataStatus, FreshValue


def _candle_field(candle: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in candle:
            return candle[key]
    return None


def _is_closed(candle: Mapping[str, Any]) -> bool:
    closed = _candle_field(candle, "closed", "is_closed")
    if closed is not None:
        return bool(closed)
    return True


def _series(candles: list[Mapping[str, Any]], field: str, *alt: str) -> list[float]:
    out: list[float] = []
    for c in candles:
        val = _candle_field(c, field, *alt)
        if val is None:
            continue
        out.append(float(val))
    return out


def _candle_time(candle: Mapping[str, Any]) -> datetime | None:
    raw = _candle_field(candle, "time", "timestamp", "open_time", "t")
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


def _live(value: Any, source: str, ts: datetime | None) -> FreshValue[Any]:
    return FreshValue(
        value=value,
        timestamp=ts or datetime.now(timezone.utc),
        source=source,
        status=DataStatus.LIVE,
    )


class MTFEngine:
    """Computes configured MTF indicators when a candle closes."""

    SOURCE = "mtf_engine"

    def __init__(self, indicators_config: Mapping[str, Any] | None = None) -> None:
        cfg = dict(indicators_config or {})
        mtf = dict(cfg.get("mtf") or cfg)
        self._ema_periods: list[int] = list(mtf.get("ema_periods") or [])
        self._sma_periods: list[int] = list(mtf.get("sma_periods") or [])
        self._rsi_period: int = int(mtf.get("rsi_period", 14))
        self._atr_period: int = int(mtf.get("atr_period", 14))
        self._volume_sma_period: int = int(mtf.get("volume_sma_period", 20))
        self._vwap_enabled: bool = bool(mtf.get("vwap_enabled", True))
        self._volatility_period: int = int(mtf.get("volatility_period", 20))
        self._williams_period: int = int(mtf.get("williams_r_period", 14))

    def on_candle_close(
        self,
        symbol: str,
        timeframe: str,
        candles: list[Mapping[str, Any]],
    ) -> dict[str, FreshValue[Any]]:
        _ = symbol
        _ = timeframe
        if not candles:
            return self._all_waiting()

        last = candles[-1]
        if not _is_closed(last):
            return self._all_waiting()

        ts = _candle_time(last)
        closes = _series(candles, "close", "c")
        highs = _series(candles, "high", "h")
        lows = _series(candles, "low", "l")
        volumes = _series(candles, "volume", "v")
        opens = _series(candles, "open", "o")

        if len(closes) < 2 or not highs or not lows:
            return self._all_waiting()

        out: dict[str, FreshValue[Any]] = {}

        for period in self._ema_periods:
            key = f"ema_{period}"
            val = ind.ema(closes, int(period))
            out[key] = (
                _live(val, self.SOURCE, ts)
                if val is not None
                else FreshValue.waiting(self.SOURCE)
            )

        for period in self._sma_periods:
            key = f"sma_{period}"
            val = ind.sma(closes, int(period))
            out[key] = (
                _live(val, self.SOURCE, ts)
                if val is not None
                else FreshValue.waiting(self.SOURCE)
            )

        rsi_val = ind.rsi(closes, self._rsi_period)
        out["rsi"] = (
            _live(rsi_val, self.SOURCE, ts)
            if rsi_val is not None
            else FreshValue.waiting(self.SOURCE)
        )

        atr_val = ind.atr(highs, lows, closes, self._atr_period)
        out["atr"] = (
            _live(atr_val, self.SOURCE, ts)
            if atr_val is not None
            else FreshValue.waiting(self.SOURCE)
        )

        vol_sma = ind.volume_sma(volumes, self._volume_sma_period)
        out["volume_sma"] = (
            _live(vol_sma, self.SOURCE, ts)
            if vol_sma is not None
            else FreshValue.waiting(self.SOURCE)
        )

        if volumes and vol_sma is not None:
            rvol = ind.relative_volume(volumes[-1], vol_sma)
            out["relative_volume"] = (
                _live(rvol, self.SOURCE, ts)
                if rvol is not None
                else FreshValue.waiting(self.SOURCE)
            )
        else:
            out["relative_volume"] = FreshValue.waiting(self.SOURCE)

        if self._vwap_enabled and volumes:
            vw = ind.vwap(highs, lows, closes, volumes)
            out["vwap"] = (
                _live(vw, self.SOURCE, ts)
                if vw is not None
                else FreshValue.waiting(self.SOURCE)
            )
        elif self._vwap_enabled:
            out["vwap"] = FreshValue.waiting(self.SOURCE)

        volty = ind.volatility(closes, self._volatility_period)
        out["volatility"] = (
            _live(volty, self.SOURCE, ts)
            if volty is not None
            else FreshValue.waiting(self.SOURCE)
        )

        wr = ind.williams_r(highs, lows, closes, self._williams_period)
        out["williams_r"] = (
            FreshValue(
                value=wr,
                timestamp=ts,
                source=self.SOURCE,
                status=DataStatus.LIVE,
                methodology="(HH_n - Close) / (HH_n - LL_n) * -100",
            )
            if wr is not None
            else FreshValue.waiting(
                self.SOURCE,
                methodology="(HH_n - Close) / (HH_n - LL_n) * -100",
            )
        )

        if opens and highs and lows and closes:
            br = ind.candle_body_ratio(opens[-1], highs[-1], lows[-1], closes[-1])
            out["body_ratio"] = _live(br, self.SOURCE, ts)

        return out

    def _all_waiting(self) -> dict[str, FreshValue[Any]]:
        keys = [f"ema_{p}" for p in self._ema_periods]
        keys += [f"sma_{p}" for p in self._sma_periods]
        keys += [
            "rsi",
            "atr",
            "volume_sma",
            "relative_volume",
            "volatility",
            "williams_r",
            "body_ratio",
        ]
        if self._vwap_enabled:
            keys.append("vwap")
        return {k: FreshValue.waiting(self.SOURCE) for k in keys}
