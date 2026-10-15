"""Entry and exit rules. All checks use bar i's close; fills happen at bar i+1's open.

Donchian  entry: close breaks the prior N-bar high (long) / low (short).
          exit:  close breaks the prior M-bar low (long) / high (short).
RSI(2)    entry: RSI below rsi_entry (long) / above 100 - rsi_entry (short).
          exit:  close back above (long) / below (short) the 5-bar SMA, or time stop.
Bollinger entry: close below the lower band (long) / above the upper band (short).
          exit:  close back to the middle band, or time stop.
Every trade also carries a fixed ATR stop set at entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

from app.research.regime_lab import indicators as ind
from app.research.regime_lab.config import (
    BOLLINGER,
    DONCHIAN,
    LONG,
    ROUTING,
    RSI2,
    SHORT,
    WARMUP,
    RegimeParams,
    Safeguards,
    StrategyParams,
)
from app.research.regime_lab.regime import map_higher_timeframe, regime_series

MEAN_REVERSION = (RSI2, BOLLINGER)
DIP_CONFIRM_EMA = 5


@dataclass
class SymbolData:
    symbol: str
    times: list[datetime]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float]
    regime_params: RegimeParams
    safeguards: Safeguards = field(default_factory=Safeguards)
    regime: list[str] = field(init=False)  # label, for reporting and the BTC gate
    route: list[str] = field(init=False)  # regime used to pick strategies
    size: list[float] = field(init=False)  # regime size multiplier
    atr: list[float | None] = field(init=False)
    atr_rank: list[float | None] = field(init=False)
    trend_sma: list[float | None] = field(init=False)  # the regime's 200 SMA
    adx: list[float | None] = field(init=False)
    bar_delta: timedelta | None = None  # set when this symbol runs on its own timeframe
    _cache: dict[tuple, Any] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        rs = regime_series(self.highs, self.lows, self.closes, self.regime_params, self.safeguards)
        self.regime = rs["regime"]
        self.route = rs["route"]
        self.size = rs["size"]
        self.atr = rs["atr"]
        self.atr_rank = rs["atr_rank"]
        self.trend_sma = rs["sma"]
        self.adx = rs["adx"]
        self.index = {t: i for i, t in enumerate(self.times)}

    @classmethod
    def from_candles(
        cls,
        symbol: str,
        candles: Sequence[dict[str, Any]],
        regime_params: RegimeParams,
        safeguards: Safeguards | None = None,
    ) -> "SymbolData":
        return cls(
            symbol=symbol,
            times=[c["time"] for c in candles],
            opens=[float(c["open"]) for c in candles],
            highs=[float(c["high"]) for c in candles],
            lows=[float(c["low"]) for c in candles],
            closes=[float(c["close"]) for c in candles],
            volumes=[float(c.get("volume") or 0.0) for c in candles],
            regime_params=regime_params,
            safeguards=safeguards or Safeguards(),
        )

    def use_higher_timeframe_regime(self, htf: "SymbolData", bar_delta: timedelta, htf_delta: timedelta) -> None:
        """Replace regime/route/size with the last closed higher-timeframe bar's values."""
        args = (self.times, bar_delta, htf.times, htf_delta)
        self.regime = map_higher_timeframe(*args, htf.regime, WARMUP)
        self.route = map_higher_timeframe(*args, htf.route, WARMUP)
        self.size = map_higher_timeframe(*args, htf.size, 1.0)

    def ema(self, n: int):
        return self._get(("ema", n), lambda: ind.ema(self.closes, n))

    def er(self, n: int):
        return self._get(("er", n), lambda: ind.efficiency_ratio(self.closes, n))

    def returns(self):
        return self._get(
            ("ret",),
            lambda: [None] + [self.closes[i] / self.closes[i - 1] - 1 for i in range(1, len(self.closes))],
        )

    def corr_to(self, other: "SymbolData", n: int):
        """Rolling return correlation with `other`, aligned by bar time."""
        def build():
            if other is self:
                return [1.0] * len(self.times)
            other_ret = other.returns()
            ys = [other_ret[other.index[t]] if t in other.index else None for t in self.times]
            return ind.rolling_corr(self.returns(), ys, n)

        return self._get(("corr", other.symbol, n), build)

    def avg_dollar_volume(self, n: int):
        """Mean close*volume over the n bars before i."""
        def build():
            dv = [c * v for c, v in zip(self.closes, self.volumes)]
            m = ind.sma(dv, n)
            return [None] + m[:-1]

        return self._get(("adv", n), build)

    def _get(self, key: tuple, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def chan_high(self, n: int):
        return self._get(("hi", n), lambda: ind.prior_high(self.highs, n))

    def chan_low(self, n: int):
        return self._get(("lo", n), lambda: ind.prior_low(self.lows, n))

    def sma(self, n: int):
        return self._get(("sma", n), lambda: ind.sma(self.closes, n))

    def rsi(self, n: int):
        return self._get(("rsi", n), lambda: ind.rsi(self.closes, n))

    def std(self, n: int):
        return self._get(("std", n), lambda: ind.rolling_std(self.closes, n))


@dataclass
class Position:
    symbol: str
    strategy: str
    side: str
    regime: str
    entry_index: int
    entry_time: datetime
    entry_price: float
    qty: float
    stop: float
    risk_amount: float
    entry_fee: float
    entry_slippage: float

    @property
    def direction(self) -> int:
        return 1 if self.side == LONG else -1


def _bb(d: SymbolData, p: StrategyParams, i: int) -> tuple[float, float, float] | None:
    mid = d.sma(p.bb_period)[i]
    sd = d.std(p.bb_period)[i]
    if mid is None or sd is None:
        return None
    return mid, mid + p.bb_std * sd, mid - p.bb_std * sd


def _short_filter_ok(d: SymbolData, i: int, p: StrategyParams) -> bool:
    """Stricter short: close below the prior long-channel low, strong ADX, below the 200 SMA."""
    low = d.chan_low(p.short_filter_channel)[i]
    adx = d.adx[i]
    sma = d.trend_sma[i]
    c = d.closes[i]
    return (
        low is not None and c < low
        and adx is not None and adx > p.short_filter_adx
        and sma is not None and c < sma
    )


def entry_side(strategy: str, d: SymbolData, i: int, sides: tuple[str, ...], p: StrategyParams) -> str | None:
    """The side this strategy opens at bar i's close, limited to `sides`."""
    c = d.closes[i]
    if strategy == DONCHIAN:
        hi = d.chan_high(p.donchian_entry)[i]
        lo = d.chan_low(p.donchian_entry)[i]
        if LONG in sides and hi is not None and c > hi:
            return LONG
        if SHORT in sides and p.donchian_shorts and lo is not None and c < lo:
            if p.donchian_short_filter and not _short_filter_ok(d, i, p):
                return None
            return SHORT
        return None
    if strategy == RSI2:
        r = d.rsi(p.rsi_period)[i]
        if r is None:
            return None
        if LONG in sides and r < p.rsi_entry:
            return LONG
        if SHORT in sides and r > 100.0 - p.rsi_entry:
            return SHORT
        return None
    if strategy == BOLLINGER:
        bands = _bb(d, p, i)
        if bands is None:
            return None
        _, upper, lower = bands
        if LONG in sides and c < lower:
            return LONG
        if SHORT in sides and c > upper:
            return SHORT
        return None
    raise ValueError(f"unknown strategy {strategy}")


def exit_reason(pos: Position, d: SymbolData, i: int, p: StrategyParams) -> str | None:
    """Rule-based exit at bar i's close (the ATR stop is handled intrabar by the engine)."""
    c = d.closes[i]
    long = pos.side == LONG
    held = i - pos.entry_index + 1
    if pos.strategy == DONCHIAN:
        lvl = (d.chan_low if long else d.chan_high)(p.donchian_exit)[i]
        if lvl is not None and (c < lvl if long else c > lvl):
            return "channel_exit"
        return None
    if p.exit_mr_on_regime_change and pos.side not in ROUTING[pos.strategy].get(d.route[i], ()):
        return "regime_change"
    if pos.strategy == RSI2:
        if p.rsi_exit_mode == "rsi":
            r = d.rsi(p.rsi_period)[i]
            if r is not None and (r > p.rsi_exit_level if long else r < 100.0 - p.rsi_exit_level):
                return "rsi_exit"
        else:
            m = d.sma(p.rsi_exit_sma)[i]
            if m is not None and (c > m if long else c < m):
                return "sma_exit"
        if held >= p.rsi_max_bars:
            return "time_stop"
        return None
    if pos.strategy == BOLLINGER:
        bands = _bb(d, p, i)
        if bands is not None and (c >= bands[0] if long else c <= bands[0]):
            return "middle_band"
        if held >= p.bb_max_bars:
            return "time_stop"
        return None
    raise ValueError(f"unknown strategy {pos.strategy}")


def dip_confirmed(d: SymbolData, i: int, side: str) -> bool:
    """A mean-reversion entry stops falling (long) or rising (short): close past the
    prior bar's extreme, or EMA(5) turning in the trade's direction."""
    if i < 1:
        return False
    e = d.ema(DIP_CONFIRM_EMA)
    turned = e[i] is not None and e[i - 1] is not None and (e[i] > e[i - 1] if side == LONG else e[i] < e[i - 1])
    if side == LONG:
        return d.closes[i] > d.highs[i - 1] or turned
    return d.closes[i] < d.lows[i - 1] or turned


def retest_reference(d: SymbolData, i: int, side: str, p: StrategyParams) -> float | None:
    """Price a retest entry must dip to and close back beyond, fixed at the breakout bar i."""
    if p.donchian_entry_mode == "retest_level":
        return (d.chan_high if side == LONG else d.chan_low)(p.donchian_entry)[i]
    if p.donchian_entry_mode == "retest_mid":
        return (d.highs[i] + d.lows[i]) / 2
    return None


def retest_confirmed(d: SymbolData, i: int, side: str, ref: float) -> bool:
    """Bar i pulled back to `ref` and closed back on the breakout side of it."""
    if side == LONG:
        return d.lows[i] <= ref < d.closes[i]
    return d.highs[i] >= ref > d.closes[i]
