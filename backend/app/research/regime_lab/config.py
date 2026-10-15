"""Regime Lab settings: regimes, tiers, strategy defaults, walk-forward grid.

Every number here is a starting value, not a tuned one. Tune per tier, not per coin.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.research.trade_fees import DEFAULT_TAKER_FEE

UPTREND = "UPTREND"
DOWNTREND = "DOWNTREND"
SIDEWAYS = "SIDEWAYS"
CHOPPY = "CHOPPY"
TRANSITION = "TRANSITION"  # vote method only: trend strength but directional signals disagree
WARMUP = "WARMUP"
REGIMES = (UPTREND, DOWNTREND, SIDEWAYS, CHOPPY, TRANSITION)

REGIME_METHODS = ("threshold", "hysteresis", "vote")

LONG = "LONG"
SHORT = "SHORT"

DONCHIAN = "donchian"
RSI2 = "rsi2"
BOLLINGER = "bollinger"
STRATEGIES = (DONCHIAN, RSI2, BOLLINGER)

# Sides each strategy may open in each regime. CHOPPY is reserved for pairs,
# wide VWAP and squeeze breakouts, none of which exist yet, so nothing trades there.
ROUTING: dict[str, dict[str, tuple[str, ...]]] = {
    DONCHIAN: {UPTREND: (LONG,), DOWNTREND: (SHORT,)},
    RSI2: {UPTREND: (LONG,), DOWNTREND: (SHORT,), SIDEWAYS: (LONG, SHORT)},
    BOLLINGER: {SIDEWAYS: (LONG, SHORT)},
}

STRATEGY_LABELS = {
    DONCHIAN: "Donchian breakout (trend)",
    RSI2: "RSI(2) pullback / reversion",
    BOLLINGER: "Bollinger band reversion",
}

TIMEFRAMES = ("1h", "4h", "1d")
BARS_PER_YEAR = {"1h": 24 * 365, "4h": 6 * 365, "1d": 365}

# Bars from this date on are kept out of every run unless a request opts in, so
# one untouched window stays available for a final check after walk-forward.
HOLDOUT_START = datetime(2026, 7, 15, tzinfo=timezone.utc)

BTC_SYMBOL = "BTCUSDT"
LIQUIDITY_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class RegimeParams:
    sma_period: int = 200
    adx_period: int = 14
    adx_threshold: float = 20.0
    atr_period: int = 14
    atr_rank_window: int = 360
    atr_rank_threshold: float = 0.70


@dataclass(frozen=True)
class StrategyParams:
    donchian_entry: int = 20
    donchian_exit: int = 10
    rsi_period: int = 2
    rsi_entry: float = 10.0  # long below this, short above 100 - this
    rsi_exit_sma: int = 5
    rsi_max_bars: int = 10
    bb_period: int = 20
    bb_std: float = 2.0
    bb_max_bars: int = 20
    # Mean-reversion trades close when the regime no longer allows their side.
    exit_mr_on_regime_change: bool = True
    # RSI(2) exit: "sma" closes back across the 5-bar SMA; "rsi" waits for RSI(2)
    # above rsi_exit_level (long) / below 100 - rsi_exit_level (short).
    rsi_exit_mode: str = "sma"
    rsi_exit_level: float = 70.0
    # Donchian shorts. With the filter on, a short also needs a close below the
    # prior short_filter_channel-bar low, ADX above short_filter_adx, and close below the 200 SMA.
    donchian_shorts: bool = True
    donchian_short_filter: bool = False
    short_filter_channel: int = 55
    short_filter_adx: float = 25.0
    # Donchian entry: "breakout" fills at the next open after the breakout close.
    # "retest_level" waits for a bar that dips to the breakout level and closes back beyond it;
    # "retest_mid" does the same with the breakout bar's midpoint. Either waits retest_bars at most.
    donchian_entry_mode: str = "breakout"
    retest_bars: int = 6


@dataclass(frozen=True)
class TierConfig:
    name: str
    label: str
    stop_atr_mult: float
    slippage: float  # fraction of price, applied against us on every fill
    risk_pct: float  # fraction of equity risked per trade
    strategies: tuple[str, ...]
    allow_short: bool
    stop_grid: tuple[float, ...]
    rank_min: int  # liquidity rank range, 1-based inclusive
    rank_max: int


TIERS: dict[str, TierConfig] = {
    "large": TierConfig(
        name="large",
        label="Large caps",
        stop_atr_mult=1.75,
        slippage=0.0005,
        risk_pct=0.005,
        strategies=(DONCHIAN, RSI2, BOLLINGER),
        allow_short=True,
        stop_grid=(1.5, 2.0),
        rank_min=1,
        rank_max=5,
    ),
    "mid": TierConfig(
        name="mid",
        label="Mid caps",
        stop_atr_mult=2.75,
        slippage=0.0015,
        risk_pct=0.005,
        strategies=(DONCHIAN,),
        allow_short=True,
        stop_grid=(2.5, 3.0),
        rank_min=6,
        rank_max=20,
    ),
    "small": TierConfig(
        name="small",
        label="Small caps",
        stop_atr_mult=3.5,
        slippage=0.003,
        risk_pct=0.0025,
        strategies=(DONCHIAN,),
        allow_short=False,
        stop_grid=(3.0, 4.0),
        rank_min=21,
        rank_max=40,
    ),
}


@dataclass(frozen=True)
class Safeguards:
    """Optional protections. Defaults are all off, so a plain run is the original plan.

    For counts and fractions, 0 means off.
    """

    # Regime lag
    regime_method: str = "threshold"  # threshold | hysteresis | vote
    adx_enter: float = 25.0  # hysteresis: enter a trend above this
    adx_exit: float = 18.0  # hysteresis: leave a trend only below this
    confirm_bars: int = 1  # a new regime label must hold this many bars (1 = immediate)
    transition_size: float = 0.0  # vote: size in TRANSITION along the vote's lean (0 = flat)
    daily_regime: bool = False  # regime from closed daily bars, entries on the run timeframe
    perf_cut: float = 0.0  # size multiplier while a strategy is cold (0 = off)
    perf_loss_streak: int = 5
    perf_window: int = 20  # rolling expectancy window, in trades
    # Chop
    chop_filter: bool = False  # no entries when ER < er_min and ATR percentile >= threshold
    er_period: int = 20
    er_min: float = 0.3
    cooldown_stops: int = 0  # consecutive stop-outs on a coin that start a cooldown
    cooldown_bars: int = 12
    max_entries_per_day: int = 0
    # Crashes
    crash_filter: bool = False
    crash_range_atr: float = 3.0  # a bar's range above this many ATRs pauses that coin
    crash_btc_drop: float = 0.05  # a BTC bar falling this much from its open pauses everything
    crash_pause_bars: int = 6
    dip_confirm: bool = False  # mean-reversion entries wait for close past the prior extreme or EMA(5) turn
    dip_confirm_bars: int = 3
    slippage_buffer: bool = False  # size on stop distance + round-trip slippage and fees
    daily_loss_limit: float = 0.0  # flatten and stop for the UTC day at this loss
    # "equity": loss vs marked-to-market equity at the day's start, so giving back open
    # profit counts. "realized": cash at the day's start vs cash plus open losses only.
    daily_loss_basis: str = "equity"
    kill_drawdown: float = 0.0  # flatten and stop for good at this drawdown from peak
    # Correlation and heat
    heat_cap: float = 0.0  # total open risk as a fraction of equity
    max_positions: int = 0
    corr_threshold: float = 0.0  # 30-day return correlation to BTC that counts as "the same trade"
    max_correlated: int = 2  # same-side positions allowed above corr_threshold
    # Liquidity
    max_volume_participation: float = 0.0  # order notional cap, fraction of average hourly $ volume
    # Walk-forward
    wf_selection: str = "best"  # best | plateau (average of the grid point and its neighbours)


RECOMMENDED_SAFEGUARDS = Safeguards(
    regime_method="vote",
    adx_enter=25.0,
    adx_exit=18.0,
    confirm_bars=2,
    transition_size=0.5,
    perf_cut=0.5,
    chop_filter=True,
    cooldown_stops=2,
    max_entries_per_day=6,
    crash_filter=True,
    dip_confirm=True,
    slippage_buffer=True,
    daily_loss_limit=0.025,
    kill_drawdown=0.25,
    heat_cap=0.04,
    max_positions=6,
    corr_threshold=0.8,
    max_correlated=2,
    max_volume_participation=0.01,
    wf_selection="plateau",
)


@dataclass(frozen=True)
class RunConfig:
    tier: str = "large"
    timeframe: str = "4h"
    strategies: tuple[str, ...] = (DONCHIAN, RSI2, BOLLINGER)
    initial_equity: float = 10_000.0
    risk_pct: float | None = None  # None: tier default
    stop_atr_mult: float | None = None  # None: tier default
    slippage: float | None = None  # None: tier default
    fee_rate: float = DEFAULT_TAKER_FEE
    max_leverage: float = 2.0  # cap on total open notional / equity
    btc_gate: bool = True
    allow_short: bool | None = None  # None: tier default
    regime: RegimeParams = field(default_factory=RegimeParams)
    params: StrategyParams = field(default_factory=StrategyParams)
    safeguards: Safeguards = field(default_factory=Safeguards)
    # Per-symbol slippage overrides (fraction per fill), e.g. {"BTCUSDT": 0.0005}.
    symbol_slippage: dict[str, float] = field(default_factory=dict)
    # Per-symbol bar timeframe overrides, e.g. {"BTCUSDT": "1d"}; others use `timeframe`.
    symbol_timeframes: dict[str, str] = field(default_factory=dict)

    def slippage_for(self, symbol: str) -> float:
        return self.symbol_slippage.get(symbol, self.eff_slippage)

    @property
    def tier_config(self) -> TierConfig:
        return TIERS[self.tier]

    @property
    def eff_risk_pct(self) -> float:
        return self.risk_pct if self.risk_pct is not None else self.tier_config.risk_pct

    @property
    def eff_stop_mult(self) -> float:
        return self.stop_atr_mult if self.stop_atr_mult is not None else self.tier_config.stop_atr_mult

    @property
    def eff_slippage(self) -> float:
        return self.slippage if self.slippage is not None else self.tier_config.slippage

    @property
    def eff_allow_short(self) -> bool:
        return self.allow_short if self.allow_short is not None else self.tier_config.allow_short

    @property
    def eff_strategies(self) -> tuple[str, ...]:
        allowed = set(self.tier_config.strategies)
        return tuple(s for s in self.strategies if s in allowed)

    def with_overrides(self, **kw: Any) -> "RunConfig":
        params_kw = {k: v for k, v in kw.items() if k in StrategyParams.__dataclass_fields__}
        run_kw = {k: v for k, v in kw.items() if k in RunConfig.__dataclass_fields__}
        out = replace(self, **run_kw)
        if params_kw:
            out = replace(out, params=replace(out.params, **params_kw))
        return out


def walk_forward_grid(cfg: RunConfig) -> list[dict[str, Any]]:
    """Small joint grid; walk-forward picks one point per fold on train data only.

    A run with an explicit stop_atr_mult keeps it fixed instead of searching stops.
    """
    grid: list[dict[str, Any]] = []
    stops = cfg.tier_config.stop_grid if cfg.stop_atr_mult is None else (cfg.stop_atr_mult,)
    for stop in stops:
        for dc in (20, 55):
            for rsi in (5.0, 10.0):
                for bb in (2.0, 2.5):
                    grid.append(
                        {"stop_atr_mult": stop, "donchian_entry": dc, "rsi_entry": rsi, "bb_std": bb}
                    )
    return grid


def warmup_bars(cfg: RunConfig) -> int:
    r = cfg.regime
    return max(r.sma_period, 2 * r.adx_period, r.atr_period + r.atr_rank_window, 60) + 10


# Daily bars cover a year per 365 bars, so the ATR percentile window is shorter there.
DAILY_REGIME_PARAMS_OVERRIDES = {"atr_rank_window": 120}


def corr_bars(timeframe: str) -> int:
    """30 days of bars on this timeframe."""
    return max(20, BARS_PER_YEAR[timeframe] * 30 // 365)


def config_document() -> dict[str, Any]:
    default = RunConfig()
    return {
        "regimes": {
            UPTREND: "close > 200 SMA and ADX > 20",
            DOWNTREND: "close <= 200 SMA and ADX > 20",
            SIDEWAYS: "ADX <= 20 and ATR% percentile < 70",
            CHOPPY: "ADX <= 20 and ATR% percentile >= 70",
            TRANSITION: "vote method: ADX says trend but EMA50/EMA200/slope/Donchian votes disagree",
        },
        "regime_methods": {
            "threshold": "single ADX threshold (the original plan)",
            "hysteresis": "enter trend above adx_enter, leave below adx_exit",
            "vote": "4 direction votes + ADX strength; disagreement is TRANSITION",
        },
        "safeguards": {"off": asdict(Safeguards()), "recommended": asdict(RECOMMENDED_SAFEGUARDS)},
        "routing": {s: {r: list(sides) for r, sides in m.items()} for s, m in ROUTING.items()},
        "strategies": [{"id": s, "label": STRATEGY_LABELS[s]} for s in STRATEGIES],
        "tiers": {k: asdict(v) for k, v in TIERS.items()},
        "timeframes": list(TIMEFRAMES),
        "defaults": {
            "tier": default.tier,
            "timeframe": default.timeframe,
            "initial_equity": default.initial_equity,
            "fee_rate": default.fee_rate,
            "max_leverage": default.max_leverage,
            "btc_gate": default.btc_gate,
            "regime": asdict(default.regime),
            "params": asdict(default.params),
            "train_months": 12,
            "test_months": 3,
        },
        "holdout_start": HOLDOUT_START.isoformat(),
        "walk_forward_grid_size": len(walk_forward_grid(default)),
        "not_modeled": [
            "perpetual funding payments (only BTCUSDT has settlement history stored)",
            "order book depth, spread checks and partial fills",
            "post-only limit entries and TWAP splitting",
            "BTC perp beta hedge",
            "CHOPPY-regime strategies (pairs, wide VWAP, squeeze breakout)",
            "exchange-side stops: modeled as stops that always fill, at the open on a gap",
        ],
    }
