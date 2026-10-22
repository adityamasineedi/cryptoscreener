"""Research badge per screener setup type (owner D188 part 1d). Never a buy or sell signal.

The wording comes from research/FINAL_VERDICT.md (final 2026-10-22, D189/D190): no strategy passed
VALIDATION. Structure/BOS entries, pullbacks and breakouts on 1h-4h bars showed no edge after real costs;
the 15m screener setups themselves were never tested as a strategy.
"""

from __future__ import annotations

from typing import Any, Mapping

BADGE_TEXT = "Pattern untested / no edge found (FINAL_VERDICT)"
NOT_A_SIGNAL = "Research display only: not a buy or sell signal."

SETUP_TYPES: dict[str, dict[str, str]] = {
    "TREND_BOS_PULLBACK": {
        "label": "Trend + BOS + pullback/retest",
        "verdict": "NO_EDGE_FOUND",
        "detail": "BOS, structure and pullback entries showed no edge after costs on 1h-4h (FINAL_VERDICT: "
                  "gross about +0.02 R/trade, costs about 0.07 R/trade). This 15m version is untested.",
    },
    "TREND_BOS": {
        "label": "Trend + BOS",
        "verdict": "NO_EDGE_FOUND",
        "detail": "Structure and BOS entries showed no edge after costs on 1h-4h (FINAL_VERDICT). "
                  "This 15m version is untested.",
    },
    "STRUCTURE_FORMING": {
        "label": "Structure forming",
        "verdict": "UNTESTED",
        "detail": "A partial setup: never tested as a strategy (FINAL_VERDICT lists no test of it).",
    },
    "NO_SETUP": {
        "label": "No setup",
        "verdict": "UNTESTED",
        "detail": "No setup on this row.",
    },
}


def setup_type(row: Mapping[str, Any]) -> str:
    tier = str(row.get("tier") or "").upper()
    if tier in ("WAITING", "BLOCKED"):
        return "NO_SETUP"
    bos = str(row.get("bos") or "").upper()
    trend = str(row.get("setup_trend") or "").upper()
    pullback = str(row.get("pullback") or "").upper()
    retest = str(row.get("retest") or "").upper()
    has_bos = bos.endswith("_BOS")
    has_trend = trend in ("BULLISH", "BEARISH")
    if has_bos and has_trend and (pullback in ("ACTIVE", "CONFIRMED") or retest in ("YES", "CONFIRMED")):
        return "TREND_BOS_PULLBACK"
    if has_bos and has_trend:
        return "TREND_BOS"
    if has_bos or has_trend:
        return "STRUCTURE_FORMING"
    return "NO_SETUP"


def badge_for(row: Mapping[str, Any]) -> dict[str, Any]:
    kind = setup_type(row)
    info = SETUP_TYPES[kind]
    return {
        "setup_type": kind,
        "setup_label": info["label"],
        "verdict": info["verdict"],
        "text": BADGE_TEXT,
        "detail": info["detail"],
        "not_a_signal": NOT_A_SIGNAL,
        "source": "research/FINAL_VERDICT.md (2026-10-22)",
    }
