"""Screener-only fix for the impulse/pullback gates that can never both pass (owner D193(b), D220).

The shared ``SignalEngine.analyze_timeframe`` measures the impulse on the evaluated bar itself
(``detect_impulse`` with ``as_of_index = end``, so ``bar_index == end``) and then asks
``detect_pullback`` for bars *after* that impulse. Those bars do not exist yet, so the pullback is
always WAITING ("Waiting for bars after impulse", ``signals/pullback_engine.py``), and
``evaluate_entry`` keeps every row at WAITING or CONFLICT. No screener row can reach
ENTRY_CANDIDATE. This is the defect research fixed as "+PBFIX"
(``research/audit_harness/pullback_fix.py``; DESIGN_FLAWS item 1; GATES E4).

``ScreenerSignalEngine`` applies the same rule on the setup timeframe only:

- the BOS episode is the run of bars back from ``end`` on which ``detect_bos`` gives the same
  direction and broken level as at ``end``;
- the impulse is the latest bar ``j < end`` of that episode where ``detect_impulse`` (with that
  bar's BOS) reports ``is_impulse``;
- ``detect_pullback`` and ``detect_retest`` are then evaluated at ``end`` against that impulse, and
  the setup's impulse gate reads that impulse.

If no such bar exists, the shared engine's result is kept unchanged (WAITING). Only unfrozen code
is used: the frozen engines (bos, impulse, pullback, swings, trend) are called, never edited.

Paper is not affected: ``SetupSignalService`` keeps the shared engine's payload as is (paper,
Path A validation, trade chances and alerts read those fields) and stores this engine's view under
``payload["screener_setup"]``, which only the screener columns and the SCREENER journal row read.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.signals.bos_engine import detect_bos
from app.signals.impulse_engine import detect_impulse
from app.signals.pullback_engine import detect_pullback
from app.signals.retest_engine import detect_retest
from app.signals.signal_engine import SignalEngine
from app.signals.swing_detector import swings_for_timeframe
from app.signals.trend_engine import infer_trend

SCREENER_PULLBACK_RULE = "episode_impulse_v1"


def episode_impulse(
    candles: Sequence[Mapping[str, Any]],
    bos: Mapping[str, Any],
    config: Any,
    end: int,
    *,
    timeframe: str,
    symbol: str,
) -> dict[str, Any] | None:
    """Latest impulse at a bar before ``end`` in the same BOS episode, or None.

    Same rule as research ``pullback_fix._episode_impulse``: walk back from ``end - 1`` while the
    bar's own BOS is CONFIRMED with the same direction and broken level; return the first bar
    (latest in time) where the frozen ``detect_impulse`` reports ``is_impulse``.
    Each bar is evaluated as of that bar only (no look-ahead).
    """
    direction, level = bos.get("direction"), bos.get("broken_level")
    for j in range(end - 1, -1, -1):
        swings_j = swings_for_timeframe(candles, config, timeframe, symbol=symbol, as_of_index=j)
        trend_j = infer_trend(
            swings_j, close=float(candles[j]["close"]), mode=getattr(config, "trend_mode", None)
        )
        bos_j = detect_bos(
            candles,
            swings_j,
            trend_j,
            symbol=symbol,
            timeframe=timeframe,
            atr_period=config.atr_period,
            as_of_index=j,
        )
        if (
            not bos_j
            or bos_j.get("state") != "CONFIRMED"
            or bos_j.get("direction") != direction
            or bos_j.get("broken_level") != level
        ):
            return None
        imp = detect_impulse(candles, bos_j, config, as_of_index=j)
        if imp and imp.get("is_impulse"):
            return imp
    return None


class ScreenerSignalEngine(SignalEngine):
    """``SignalEngine`` whose setup-timeframe impulse/pullback use the BOS episode's impulse."""

    def analyze_timeframe(  # type: ignore[override]
        self,
        symbol: str,
        timeframe: str,
        candles: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        out = super().analyze_timeframe(symbol, timeframe, candles, **kwargs)
        if timeframe != self.config.mtf_setup or out.get("data_status") != "LIVE":
            return out
        bos = out.get("bos")
        if not bos or bos.get("state") != "CONFIRMED":
            return out
        as_of_index = kwargs.get("as_of_index")
        end = len(candles) - 1 if as_of_index is None else min(int(as_of_index), len(candles) - 1)
        impulse = out.get("impulse") or {}
        if impulse.get("is_impulse") and int(impulse.get("bar_index", end)) < end:
            return out  # already an earlier impulse: the shared path is correct here
        earlier = episode_impulse(candles, bos, self.config, end, timeframe=timeframe, symbol=symbol)
        if earlier is None:
            return out
        earlier = {**earlier, "pullback_rule": SCREENER_PULLBACK_RULE}
        pullback = detect_pullback(
            candles,
            bos,
            earlier,
            self.config,
            demand_zone=kwargs.get("demand_zone"),
            supply_zone=kwargs.get("supply_zone"),
            vwap=kwargs.get("vwap"),
            ema=kwargs.get("ema"),
            as_of_index=as_of_index,
        )
        pullback = {**pullback, "impulse_bar": earlier.get("bar_index"), "pullback_rule": SCREENER_PULLBACK_RULE}
        direction = "LONG" if bos.get("direction") == "BULLISH_BOS" else "SHORT"
        retest = detect_retest(
            candles,
            bos,
            pullback,
            self.config,
            direction=direction,
            as_of_index=as_of_index,
            atr_value=kwargs.get("atr_value"),
        )
        return {**out, "impulse": earlier, "pullback": pullback, "retest": retest}


_VIEW_KEYS = (
    "status",
    "direction",
    "impulse",
    "pullback",
    "retest",
    "entry",
    "stop",
    "targets",
    "risk_reward",
    "conditions",
    "invalidation_reason",
    "explanation",
)


def screener_view(analysis: Mapping[str, Any], *, fixed: bool) -> dict[str, Any]:
    """The setup fields the screener shows, from one ``SetupAnalysis.to_dict()``."""
    view = {k: analysis.get(k) for k in _VIEW_KEYS}
    view["pullback_rule"] = SCREENER_PULLBACK_RULE
    view["episode_impulse_used"] = bool(fixed)
    return view
