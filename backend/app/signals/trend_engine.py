"""Trend from confirmed market structure only (never candle color).

Modes
-----
``legacy`` (default)
    Last two swing highs + last two swing lows only. Preserves COMBO_02 v1
    fingerprints and freeze behavior.

``choch_persistent``
    Walk confirmed swings in time order. Once BULLISH, stay BULLISH until a
    lower-low **breaks the active bullish HL** (CHOCH). Once BEARISH, stay
    BEARISH until a higher-high **breaks the active bearish LH**. Optional
    ``close`` applies the same CHOCH rule on the current closed bar so a
    reclaim above the last LH is not stuck BEARISH while a new HH is still
    unconfirmed (no lookahead — close must already be on the as-of bar).
"""

from __future__ import annotations

from typing import Any, Literal

from app.signals.schemas import SwingRecord, TrendState

TrendMode = Literal["legacy", "choch_persistent"]

TREND_MODE_LEGACY: TrendMode = "legacy"
TREND_MODE_CHOCH_PERSISTENT: TrendMode = "choch_persistent"
DEFAULT_TREND_MODE: TrendMode = TREND_MODE_LEGACY


def _base_fields(swings: list[SwingRecord]) -> dict[str, Any]:
    highs = [s for s in swings if s.swing_type == "HIGH"]
    lows = [s for s in swings if s.swing_type == "LOW"]
    return {
        "last_swing_high": highs[-1].price if highs else None,
        "last_swing_low": lows[-1].price if lows else None,
        "structure_sequence": [s.label for s in swings[-8:] if s.label],
        "trend_mode": DEFAULT_TREND_MODE,
    }


def _legacy_from_last_pair(swings: list[SwingRecord]) -> dict[str, Any]:
    """Original last-two-high / last-two-low classifier."""
    highs = [s for s in swings if s.swing_type == "HIGH"]
    lows = [s for s in swings if s.swing_type == "LOW"]
    base = _base_fields(swings)
    base["trend_mode"] = TREND_MODE_LEGACY

    if len(highs) < 2 or len(lows) < 2:
        return {
            **base,
            "trend": TrendState.INSUFFICIENT_DATA.value,
            "trend_strength": 0.0,
            "reason": "Not enough confirmed swings for HH/HL or LH/LL sequence",
        }

    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]

    if h2.label == "HH" and l2.label == "HL" and h2.price > h1.price and l2.price > l1.price:
        strength = min(
            1.0,
            ((h2.price - h1.price) + (l2.price - l1.price))
            / max(abs(h1.price), 1e-12)
            * 50,
        )
        return {
            **base,
            "trend": TrendState.BULLISH.value,
            "trend_strength": float(max(0.3, strength)),
            "reason": "Higher High + Higher Low",
        }

    if h2.label == "LH" and l2.label == "LL" and h2.price < h1.price and l2.price < l1.price:
        strength = min(
            1.0,
            ((h1.price - h2.price) + (l1.price - l2.price))
            / max(abs(h1.price), 1e-12)
            * 50,
        )
        return {
            **base,
            "trend": TrendState.BEARISH.value,
            "trend_strength": float(max(0.3, strength)),
            "reason": "Lower High + Lower Low",
        }

    return {
        **base,
        "trend": TrendState.NEUTRAL.value,
        "trend_strength": 0.1,
        "reason": "No sufficiently clear HH+HL or LH+LL structure",
    }


def _pair_bullish(h1: SwingRecord, h2: SwingRecord, l1: SwingRecord, l2: SwingRecord) -> bool:
    return (
        h2.label == "HH"
        and l2.label == "HL"
        and h2.price > h1.price
        and l2.price > l1.price
    )


def _pair_bearish(h1: SwingRecord, h2: SwingRecord, l1: SwingRecord, l2: SwingRecord) -> bool:
    return (
        h2.label == "LH"
        and l2.label == "LL"
        and h2.price < h1.price
        and l2.price < l1.price
    )


class ChochPersistentCursor:
    """Fold confirmed swings once. Appending swings matches a full replay.

    ``build_timeframe_feature_table`` used to call ``infer_trend`` on the whole
    swing list every time one swing confirmed. That replay is quadratic and is
    what holds COMBO_02 V2 in CONTEXT_BUILD. Labels, strength, and CHOCH levels
    stay the same as ``_choch_persistent``.
    """

    def __init__(self) -> None:
        self.highs: list[SwingRecord] = []
        self.lows: list[SwingRecord] = []
        self.consumed = 0
        self._anchor: SwingRecord | None = None
        self.trend = TrendState.NEUTRAL.value
        self.reason = "No sufficiently clear HH+HL or LH+LL structure"
        self.strength = 0.1
        self.bull_hl: float | None = None
        self.bear_lh: float | None = None

    def reset(self) -> None:
        self.__init__()

    def sync(self, swings: list[SwingRecord]) -> None:
        """Fold ``swings`` that were appended since the last sync.

        ``swings`` must already be in ``(bar_index, HIGH before LOW)`` order,
        which is how the swing detector returns them. A replaced list is
        refolded from the start.
        """
        if self.consumed > len(swings) or (
            self.consumed > 0
            and (self._anchor is None or swings[self.consumed - 1] is not self._anchor)
        ):
            self.reset()
        for sp in swings[self.consumed :]:
            self._step(sp)
            self.consumed += 1
            self._anchor = sp

    def _step(self, sp: SwingRecord) -> None:
        if sp.swing_type == "HIGH":
            self.highs.append(sp)
        else:
            self.lows.append(sp)
        if len(self.highs) < 2 or len(self.lows) < 2:
            return

        h1, h2 = self.highs[-2], self.highs[-1]
        l1, l2 = self.lows[-2], self.lows[-1]
        trend = self.trend
        bull_hl = self.bull_hl
        bear_lh = self.bear_lh

        if _pair_bullish(h1, h2, l1, l2):
            if trend == TrendState.BEARISH.value and bear_lh is not None:
                if h2.price <= bear_lh:
                    return
            self.trend = TrendState.BULLISH.value
            if l2.bar_index < h2.bar_index:
                self.bull_hl = float(l2.price)
            elif l1.bar_index < h2.bar_index:
                self.bull_hl = float(l1.price)
            else:
                self.bull_hl = float(l2.price)
            self.bear_lh = None
            strength = min(
                1.0,
                ((h2.price - h1.price) + (l2.price - l1.price))
                / max(abs(h1.price), 1e-12)
                * 50,
            )
            self.strength = float(max(0.3, strength))
            self.reason = "Higher High + Higher Low (persistent)"
            return

        if _pair_bearish(h1, h2, l1, l2):
            if trend == TrendState.BULLISH.value and bull_hl is not None:
                if float(l2.price) >= float(bull_hl):
                    return
            if trend == TrendState.BULLISH.value and bull_hl is not None:
                self.reason = (
                    f"CHOCH vs bull HL {bull_hl}: LH+LL with LL {l2.price} < HL"
                )
            else:
                self.reason = "Lower High + Lower Low (persistent)"
            self.trend = TrendState.BEARISH.value
            if h2.bar_index < l2.bar_index:
                self.bear_lh = float(h2.price)
            elif h1.bar_index < l2.bar_index:
                self.bear_lh = float(h1.price)
            else:
                self.bear_lh = float(h2.price)
            self.bull_hl = None
            strength = min(
                1.0,
                ((h1.price - h2.price) + (l1.price - l2.price))
                / max(abs(h1.price), 1e-12)
                * 50,
            )
            self.strength = float(max(0.3, strength))

    def structural(self, swings: list[SwingRecord]) -> dict[str, Any]:
        """Swing-only state. Does not apply the closed-bar CHOCH close rule."""
        base = _base_fields(swings)
        base["trend_mode"] = TREND_MODE_CHOCH_PERSISTENT
        if len(self.highs) < 2 or len(self.lows) < 2:
            return {
                **base,
                "trend": TrendState.INSUFFICIENT_DATA.value,
                "trend_strength": 0.0,
                "reason": "Not enough confirmed swings for HH/HL or LH/LL sequence",
            }
        return {
            **base,
            "trend": self.trend,
            "trend_strength": float(self.strength),
            "reason": self.reason,
            "active_bull_hl": self.bull_hl,
            "active_bear_lh": self.bear_lh,
        }


def _choch_persistent(
    swings: list[SwingRecord],
    *,
    close: float | None,
) -> dict[str, Any]:
    """Trend persists until opposite CHOCH vs the active structural level."""
    highs = [s for s in swings if s.swing_type == "HIGH"]
    lows = [s for s in swings if s.swing_type == "LOW"]
    ordered = sorted(swings, key=lambda s: (s.bar_index, 0 if s.swing_type == "HIGH" else 1))
    cursor = ChochPersistentCursor()
    cursor.sync(ordered)
    structural = cursor.structural(swings)
    if len(highs) < 2 or len(lows) < 2:
        return structural
    return apply_persistent_close(
        structural,
        close=close,
        last_swing_high=highs[-1],
        last_swing_low=lows[-1],
    )


def apply_persistent_close(
    structural: dict[str, Any],
    *,
    close: float | None,
    last_swing_high: SwingRecord | None,
    last_swing_low: SwingRecord | None,
) -> dict[str, Any]:
    """Apply the current closed-bar CHOCH rule to cached swing-only state."""
    out = dict(structural)
    if close is None or last_swing_high is None or last_swing_low is None:
        return out

    trend = str(out.get("trend") or TrendState.NEUTRAL.value)
    strength = float(out.get("trend_strength") or 0.0)
    reason = str(out.get("reason") or "")
    bull_hl = out.get("active_bull_hl")
    bear_lh = out.get("active_bear_lh")
    last_sh = last_swing_high
    last_sl = last_swing_low
    if close is not None:
        c = float(close)
        reclaim_level = bear_lh if bear_lh is not None else (
            float(last_sh.price) if last_sh.label == "LH" else None
        )
        if trend == TrendState.BEARISH.value and reclaim_level is not None and c > float(
            reclaim_level
        ):
            trend = TrendState.BULLISH.value
            if last_sl.label == "HL" and last_sl.bar_index < last_sh.bar_index:
                bull_hl = float(last_sl.price)
            bear_lh = None
            strength = max(strength, 0.3)
            reason = (
                f"Bullish CHOCH: close {c} above LH {reclaim_level} "
                "(persistent reclaim)"
            )
        elif (
            trend == TrendState.BULLISH.value
            and bull_hl is not None
            and c < float(bull_hl)
        ):
            broken = bull_hl
            trend = TrendState.BEARISH.value
            bear_lh = float(last_sh.price)
            bull_hl = None
            strength = max(strength, 0.3)
            reason = (
                f"Bearish CHOCH: close {c} below active HL {broken} "
                "(persistent break)"
            )
        elif (
            trend == TrendState.NEUTRAL.value
            and last_sh.label == "LH"
            and c > float(last_sh.price)
            and last_sl.label == "HL"
        ):
            trend = TrendState.BULLISH.value
            if last_sl.bar_index < last_sh.bar_index:
                bull_hl = float(last_sl.price)
            strength = max(strength, 0.3)
            reason = (
                f"Bullish reclaim: close {c} above LH {last_sh.price} "
                "(persistent)"
            )

    out.update(
        {
            "trend": trend,
            "trend_strength": float(strength),
            "reason": reason,
            "active_bull_hl": bull_hl,
            "active_bear_lh": bear_lh,
        }
    )
    return out


def infer_trend(
    swings: list[SwingRecord],
    *,
    close: float | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Determine BULLISH / BEARISH / NEUTRAL / INSUFFICIENT_DATA from swings.

    Parameters
    ----------
    swings:
        Confirmed swings only (already as-of truncated by the swing detector).
    close:
        Optional last closed bar close for CHOCH reclaim/break in
        ``choch_persistent`` mode. Ignored in ``legacy`` mode.
    mode:
        ``legacy`` (default) or ``choch_persistent``. Unknown values fall back
        to ``legacy`` so callers cannot accidentally change v1 behavior.
    """
    resolved: TrendMode
    raw = (mode or DEFAULT_TREND_MODE).strip().lower()
    if raw == TREND_MODE_CHOCH_PERSISTENT:
        resolved = TREND_MODE_CHOCH_PERSISTENT
    else:
        resolved = TREND_MODE_LEGACY

    if resolved == TREND_MODE_CHOCH_PERSISTENT:
        return _choch_persistent(swings, close=close)
    return _legacy_from_last_pair(swings)
