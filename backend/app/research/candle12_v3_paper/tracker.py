"""Bar-by-bar Candle-1 detection and entry planning for the V3 paper adapter.

Mirrors the walk in ``run_candle12_v3``: the same detector, the same
once_per_level bookkeeping, the same stop/target/sizing functions. It only ever
sees closed bars: ``observe`` takes the closed series and the index of its last
bar, and refuses anything else.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research import candle12_v3 as v3
from app.research.candle12_v2 import Candle12V2Config
from app.research.rolling_structure import RollingSwingState, _precompute_arrays
from app.signals.config import SignalConfig


class _GrowingSwingState(RollingSwingState):
    """Extends its price arrays as closed bars arrive instead of rebinding.

    Swings still confirm only ``right`` bars back from the as-of bar, so the state
    equals the engine's (which binds the whole series once).
    """

    def update(self, candles, *, end, lookback):  # type: ignore[override]
        if self._highs is not None and len(candles) > self._n:
            _, highs, lows, closes, times = _precompute_arrays(list(candles[self._n:]))
            self._highs.extend(highs)
            self._lows.extend(lows)
            assert self._closes is not None and self._times is not None
            self._closes.extend(closes)
            self._times.extend(times)
            self._n = len(candles)
        return super().update(candles, end=end, lookback=lookback)


def entry_prices(direction: str, c1: Mapping[str, float], cfg: v3.Candle12V3Config) -> dict[str, float]:
    """trigger T, engine stop-order price P, and the order's stop price F.

    F = P adjusted by the entry slippage: C1 high x 1.0002 (LONG) or C1 low x
    0.9998 (SHORT) in the inclusive mode. The engine fills a non-gap entry at F
    only when Candle-2 trades F, so a stop order resting at F triggers on
    exactly the engine's fills.
    """
    trigger = float(c1["high"] if direction == "LONG" else c1["low"])
    order = trigger
    if cfg.entry_slippage_mode == "additional":
        order = v3._adverse(direction, trigger, cfg.entry_slippage, entry=True)
    return {
        "trigger": trigger,
        "stop_order": order,
        "entry_stop_price": v3._adverse(direction, order, cfg.entry_slippage, entry=True),
    }


def is_gap(direction: str, price: float, stop_order: float) -> bool:
    """The engine's gap rule: Candle-2 opens beyond the stop-order price."""
    return price > stop_order if direction == "LONG" else price < stop_order


def plan_entry(
    setup: Mapping[str, Any],
    *,
    fill: float,
    cfg: v3.Candle12V3Config,
    scfg: SignalConfig,
    filters: tuple[float | None, float | None, float | None],
) -> dict[str, Any] | None:
    """Stop, targets and size for an expected fill. Same steps as ``v3._plan``."""
    direction = str(setup["direction"])
    c1 = setup["c1_ohlc"]
    stop = v3._structural_stop(
        direction=direction, entry=fill, c1_high=float(c1["high"]), c1_low=float(c1["low"]),
        pullback=setup.get("pullback"), impulse=setup.get("impulse"),
        atr=float(setup["atr"]) if setup.get("atr") is not None else None, signal_config=scfg,
    )
    final = stop.get("final_stop")
    if final is None or not v3._beyond(direction, fill, float(final), favorable=True):
        return None
    targets = v3.compute_targets(
        direction=direction, entry_price=fill, stop=stop, swings=setup.get("swings") or [], config=scfg
    )
    levels = [float(t["target_price"]) for t in targets if t.get("target_price") is not None]
    if not levels:
        return None
    step, min_qty, min_notional = filters
    sizing = v3.size_position(
        entry=fill, stop=float(final), cfg=cfg, step=step, min_qty=min_qty, min_notional=min_notional
    )
    nearest = min(levels) if direction == "LONG" else max(levels)
    return {
        "expected_fill": fill,
        "stop": float(final),
        "targets": levels,
        "target": nearest,
        "target_slot": f"TP{levels.index(nearest) + 1}",
        "sizing": sizing,
    }


class SignalTracker:
    """Feeds closed bars, in order, to the frozen detector."""

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        cfg: v3.Candle12V3Config,
        scfg: SignalConfig | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.cfg = cfg
        self.scfg = scfg or SignalConfig()
        sc = self.scfg.swing_for(timeframe)
        self.swing_state = _GrowingSwingState(
            left=sc.swing_left_bars, right=sc.swing_right_bars, symbol=self.symbol, timeframe=timeframe
        )
        self.setup_cfg = Candle12V2Config(
            structure_lookback_bars=cfg.structure_lookback_bars,
            min_bars=cfg.min_bars,
            require_impulse=cfg.require_impulse,
            require_close_in_bos_direction=cfg.require_close_in_bos_direction,
        )
        self.consumed: dict[tuple[str, int, float], int] = {}
        self.last_index: int | None = None
        self.refire_suppressed = 0
        self.filters = cfg.exchange_filter(symbol)[:3]

    def observe(self, closed: Sequence[Mapping[str, Any]], i: int, *, first_trade_index: int) -> dict[str, Any] | None:
        """Detect a Candle-1 at closed bar ``i`` (the last bar of ``closed``).

        Returns the setup when it is tradable for this book (after re-arm and
        side filtering), else None. The caller applies the position lock.
        """
        if len(closed) != i + 1:
            raise ValueError("observe() takes the closed series ending at bar i; no unclosed bar may be passed")
        if self.last_index is not None and i != self.last_index + 1:
            raise ValueError(f"bars must arrive in order: got {i} after {self.last_index}")
        self.last_index = i
        if i < self.cfg.min_bars:
            return None
        setup = v3.detect_c1_v2(
            symbol=self.symbol, timeframe=self.timeframe, candles=closed, candle1_index=i,
            signal_config=self.scfg, v2=self.setup_cfg, swing_state=self.swing_state,
        )
        if not setup:
            return None
        direction = str(setup["direction"])
        key = v3._broken_swing_key(setup, i)
        if self.cfg.bos_refire_policy == "once_per_level" and key is not None:
            prior = self.consumed.get(key)
            if prior is not None and not v3._opposing_swing_after(setup, prior):
                if i >= first_trade_index and direction in self.cfg.sides():
                    self.refire_suppressed += 1
                return None
            self.consumed[key] = i
        if i < first_trade_index or direction not in self.cfg.sides():
            return None
        return {**setup, "broken_swing_key": key}
