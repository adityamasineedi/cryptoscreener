"""Bounded rolling lookback structure analysis for Candle-12 research V2.

LOOKBACK CONTRACT (research-only; does not change live SignalConfig)
-------------------------------------------------------------------
- default lookback: 300 candles (DEFAULT_LOOKBACK_BARS)
- minimum warmup:   50 candles (DEFAULT_MIN_WARMUP_BARS) — need ATR + swings
- maximum lookback: 2000 candles (MAX_LOOKBACK_BARS) — hard cap; never silent

Why 300 is sufficient for existing BOS / impulse / pullback logic
----------------------------------------------------------------
Live engines confirm swings with left/right ~3 bars, ATR period 14, and
BOS/impulse/pullback only consume the *latest* confirmed swings plus the
setup bar itself. A 300-bar window is >> those horizons and matches the
spirit of the in-memory store depth without scanning full multi-year
history. Research never silently changes lookback based on outcomes.

Complexity
----------
Incremental swing updates are O(1) per bar (plus O(swings) drop/relabel).
Full-history growing prefixes (analyze all bars from 0..i each step) are
forbidden - that is O(n^2).

Semantics
---------
At evaluation index N, only candles in
    [max(0, N - lookback + 1), N]
may influence swings / trend / BOS / impulse / pullback. as_of = N.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Mapping

from app.signals._candle_utils import candle_time, ohlc
from app.signals.bos_engine import detect_bos
from app.signals.config import SignalConfig
from app.signals.impulse_engine import detect_impulse
from app.signals.pullback_engine import detect_pullback
from app.signals.schemas import EventState, SwingRecord, TrendState
from app.signals.swing_detector import _label_swings, swings_for_timeframe
from app.signals.trend_engine import infer_trend

DEFAULT_LOOKBACK_BARS = 300
DEFAULT_MIN_WARMUP_BARS = 50
MAX_LOOKBACK_BARS = 2000


class CandleWindow(Sequence):
    """Zero-copy chronological window over a candle list (inclusive end)."""

    def __init__(
        self, candles: Sequence[Mapping[str, Any]], start: int, end: int
    ) -> None:
        if start < 0 or end < start or end >= len(candles):
            raise IndexError("invalid CandleWindow bounds")
        self._candles = candles
        self.start = start
        self.end = end

    def __len__(self) -> int:
        return self.end - self.start + 1

    def __getitem__(self, idx: int | slice):  # type: ignore[override]
        if isinstance(idx, slice):
            length = len(self)
            start, stop, step = idx.indices(length)
            if step != 1:
                return [self._candles[self.start + i] for i in range(start, stop, step)]
            if start >= stop:
                return []
            return CandleWindow(
                self._candles, self.start + start, self.start + stop - 1
            )
        if idx < 0:
            idx += len(self)
        if idx < 0 or idx >= len(self):
            raise IndexError(idx)
        return self._candles[self.start + idx]


def clamp_lookback(lookback: int) -> int:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if lookback > MAX_LOOKBACK_BARS:
        raise ValueError(
            f"lookback {lookback} exceeds MAX_LOOKBACK_BARS={MAX_LOOKBACK_BARS}"
        )
    return lookback


def lookback_documentation() -> dict[str, Any]:
    return {
        "default_lookback": DEFAULT_LOOKBACK_BARS,
        "minimum_warmup": DEFAULT_MIN_WARMUP_BARS,
        "maximum_lookback": MAX_LOOKBACK_BARS,
        "rationale": (
            "BOS/impulse/pullback consume latest confirmed swings (left/right~3) "
            "and ATR(14). A fixed 300-bar rolling window bounds work to O(n*L) "
            "with incremental swings ~O(n), never O(n^2) full-history scans. "
            "Lookback is configuration-fixed - never outcome-dependent."
        ),
    }


def _precompute_arrays(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[list[float], list[float], list[float], list[float], list[Any]]:
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    times: list[Any] = []
    for c in candles:
        o, h, l, cl = ohlc([c], 0)
        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(cl)
        times.append(candle_time(c))
    return opens, highs, lows, closes, times


class RollingSwingState:
    """Incrementally maintain fractal swings inside a sliding lookback window.

    Equivalent to re-running detect_swings on candles[window] each bar when
    minimum_swing_distance_atr == 0 (the SignalConfig default).
    """

    def __init__(
        self,
        *,
        left: int,
        right: int,
        symbol: str = "",
        timeframe: str = "",
    ) -> None:
        if left < 1 or right < 1:
            raise ValueError("left/right must be >= 1")
        self.left = left
        self.right = right
        self.symbol = symbol
        self.timeframe = timeframe
        self._swings: list[SwingRecord] = []
        self._last_end: int | None = None
        self._dirty = True
        self._highs: list[float] | None = None
        self._lows: list[float] | None = None
        self._closes: list[float] | None = None
        self._times: list[Any] | None = None
        self._n = 0

    def bind(self, candles: Sequence[Mapping[str, Any]]) -> None:
        """Precompute OHLCV arrays once per series for O(1) swing updates."""
        _, highs, lows, closes, times = _precompute_arrays(candles)
        self._highs = highs
        self._lows = lows
        self._closes = closes
        self._times = times
        self._n = len(candles)
        self._swings = []
        self._last_end = None
        self._dirty = True

    def update(
        self,
        candles: Sequence[Mapping[str, Any]],
        *,
        end: int,
        lookback: int,
    ) -> list[SwingRecord]:
        """Advance to as-of index `end` (inclusive). Returns absolute-index swings."""
        if self._highs is None or self._n != len(candles):
            self.bind(candles)
        assert self._highs is not None and self._lows is not None
        assert self._times is not None

        start = max(0, end - lookback + 1)
        min_swing_idx = start + self.left
        changed = False

        if self._swings:
            kept = [s for s in self._swings if s.bar_index >= min_swing_idx]
            if len(kept) != len(self._swings):
                self._swings = kept
                changed = True

        prev_end = self._last_end
        if prev_end is None or end < prev_end:
            self._swings = []
            self._scan_range(start, end)
            changed = True
        else:
            prev_last = prev_end - self.right
            new_last = end - self.right
            first_new = max(min_swing_idx, prev_last + 1)
            before = len(self._swings)
            for i in range(first_new, new_last + 1):
                self._try_confirm_arr(i)
            if len(self._swings) != before:
                changed = True

        self._last_end = end
        if changed or self._dirty:
            self._swings = _label_swings(
                sorted(self._swings, key=lambda s: s.bar_index)
            )
            self._dirty = False
        return self._swings

    def _scan_range(self, start: int, end: int) -> None:
        last_confirmable = end - self.right
        first = start + self.left
        for i in range(first, last_confirmable + 1):
            self._try_confirm_arr(i)

    def _try_confirm_arr(self, i: int) -> None:
        highs = self._highs
        lows = self._lows
        times = self._times
        assert highs is not None and lows is not None and times is not None
        left, right = self.left, self.right
        if i - left < 0 or i + right >= self._n:
            return
        h = highs[i]
        l = lows[i]
        left_h_max = max(highs[j] for j in range(i - left, i))
        right_h_max = max(highs[j] for j in range(i + 1, i + right + 1))
        left_l_min = min(lows[j] for j in range(i - left, i))
        right_l_min = min(lows[j] for j in range(i + 1, i + right + 1))
        confirmed_at = times[i + right]
        ts = times[i]
        if h > left_h_max and h > right_h_max:
            self._swings.append(
                SwingRecord(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    swing_type="HIGH",
                    price=h,
                    timestamp=ts if isinstance(ts, datetime) else ts,
                    bar_index=i,
                    strength=1.0,
                    confirmed_at=confirmed_at
                    if isinstance(confirmed_at, datetime)
                    else confirmed_at,
                )
            )
        if l < left_l_min and l < right_l_min:
            self._swings.append(
                SwingRecord(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    swing_type="LOW",
                    price=l,
                    timestamp=ts if isinstance(ts, datetime) else ts,
                    bar_index=i,
                    strength=1.0,
                    confirmed_at=confirmed_at
                    if isinstance(confirmed_at, datetime)
                    else confirmed_at,
                )
            )


def remap_swings_to_window(
    swings: Sequence[SwingRecord], offset: int
) -> list[SwingRecord]:
    out: list[SwingRecord] = []
    for s in swings:
        out.append(
            SwingRecord(
                symbol=s.symbol,
                timeframe=s.timeframe,
                swing_type=s.swing_type,
                price=s.price,
                timestamp=s.timestamp,
                bar_index=s.bar_index - offset,
                strength=s.strength,
                confirmed_at=s.confirmed_at,
                label=s.label,
            )
        )
    return out


def _bos_prefilter(
    *,
    closes: list[float],
    as_of_index: int,
    swings: Sequence[SwingRecord],
    trend: Mapping[str, Any],
) -> bool:
    """Cheap gate: only bars that can produce CONFIRMED BOS need detect_bos."""
    trend_state = str(trend.get("trend") or "")
    if trend_state not in (TrendState.BULLISH.value, TrendState.BEARISH.value):
        return False
    highs_sw = [s for s in swings if s.swing_type == "HIGH" and s.bar_index < as_of_index]
    lows_sw = [s for s in swings if s.swing_type == "LOW" and s.bar_index < as_of_index]
    if not highs_sw or not lows_sw:
        return False
    c = closes[as_of_index]
    if trend_state == TrendState.BULLISH.value:
        return c > highs_sw[-1].price
    return c < lows_sw[-1].price


def analyze_structure_at_bar(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[Mapping[str, Any]],
    as_of_index: int,
    lookback: int,
    signal_config: SignalConfig,
    swing_state: RollingSwingState,
    require_impulse: bool = True,
) -> dict[str, Any] | None:
    """Structure snapshot at as_of_index using bounded lookback. No look-ahead.

    Returns None when there is no CONFIRMED BOS whose confirmation bar is
    exactly as_of_index (Candle-1 definition).
    """
    lookback = clamp_lookback(lookback)
    if as_of_index < 1 or as_of_index >= len(candles):
        return None

    offset = max(0, as_of_index - lookback + 1)
    local_as_of = as_of_index - offset

    sc = signal_config.swing_for(timeframe)
    if float(sc.minimum_swing_distance_atr or 0) > 0:
        window = CandleWindow(candles, offset, as_of_index)
        local_swings = swings_for_timeframe(
            window,
            signal_config,
            timeframe,
            symbol=symbol,
            as_of_index=local_as_of,
        )
        swing_state.update(candles, end=as_of_index, lookback=lookback)
        abs_swings = local_swings
        use_abs_for_prefilter = False
    else:
        abs_swings = swing_state.update(
            candles, end=as_of_index, lookback=lookback
        )
        use_abs_for_prefilter = True
        local_swings = None  # type: ignore[assignment]

    # Trend + cheap BOS gate before expensive detect_bos / series_ohlcv
    if use_abs_for_prefilter:
        # Trend uses labels; indices absolute — infer_trend only needs order/labels/prices
        trend = infer_trend(list(abs_swings))
        closes = swing_state._closes or []
        if not _bos_prefilter(
            closes=closes,
            as_of_index=as_of_index,
            swings=abs_swings,
            trend=trend,
        ):
            return None
        local_swings = remap_swings_to_window(abs_swings, offset)
    else:
        trend = infer_trend(local_swings)
        closes_local = [float(ohlc(candles, offset + i)[3]) for i in range(local_as_of + 1)]
        if not _bos_prefilter(
            closes=closes_local,
            as_of_index=local_as_of,
            swings=local_swings,
            trend=trend,
        ):
            return None

    window = CandleWindow(candles, offset, as_of_index)
    bos = detect_bos(
        window,
        local_swings,
        trend,
        symbol=symbol,
        timeframe=timeframe,
        atr_period=signal_config.atr_period,
        as_of_index=local_as_of,
    ) or {}

    if bos.get("state") != EventState.CONFIRMED.value:
        return None
    conf = bos.get("confirmation_candle")
    if conf is None or int(conf) + offset != as_of_index:
        return None

    impulse = detect_impulse(
        window, bos, signal_config, as_of_index=local_as_of
    )
    if require_impulse and not impulse.get("is_impulse"):
        return None
    pullback = detect_pullback(
        window, bos, impulse, signal_config, as_of_index=local_as_of
    )

    assert as_of_index >= offset
    assert local_as_of == as_of_index - offset

    return {
        "bos": bos,
        "impulse": impulse,
        "pullback": pullback,
        "swings": local_swings,
        "trend": trend,
        "as_of_index": as_of_index,
        "structure_offset": offset,
        "structure_lookback": lookback,
        "local_as_of": local_as_of,
    }
