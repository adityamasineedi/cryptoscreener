"""COMBO_02 v1 production profile — evidence-based risk & book classification.

Frozen entry logic remains tag ``v1-combo02-long-htf`` (see docs/v1_freeze.md).
This module only encodes *which* books are live/paper-ready and at what risk.
It does not alter HTF gates, BOS, or trend engines.

Evidence window (user-validated): 2025-01-01 → 2026-01-31, COMBO_02 LONG.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Tier = Literal["core", "secondary", "research"]

COMBO_VERSION = "v1-combo02-long-htf"
COMBO_ID = "COMBO_02"
DEFAULT_PRINCIPAL_USD = 1000.0

# Path A paper / research claim universe (Binance USDT-M).
V1_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
CORE_SYMBOLS = frozenset({"BTCUSDT"})
SECONDARY_SYMBOLS = frozenset({"ETHUSDT", "SOLUSDT"})

# Production books (setup TF). 15m is research-only.
CORE_TIMEFRAMES = frozenset({"1h"})
SECONDARY_TIMEFRAMES = frozenset({"4h"})  # optional swing book
RESEARCH_TIMEFRAMES = frozenset({"15m", "5m", "1m"})


@dataclass(frozen=True)
class V1Book:
    """One asset × timeframe production slot."""

    symbol: str
    timeframe: str
    tier: Tier
    risk_percent: float  # fraction of equity per trade (0.015 = 1.5%)
    enabled_by_default: bool
    notes: str = ""


# Concrete v1 books — conservative vs backtest evidence.
V1_BOOKS: tuple[V1Book, ...] = (
    V1Book(
        "BTCUSDT",
        "1h",
        "core",
        0.015,
        True,
        "Best risk-adjusted: ~56% WR, avgR~+0.66, maxDD~2.5R, streak≤2",
    ),
    V1Book(
        "ETHUSDT",
        "1h",
        "secondary",
        0.005,
        True,
        "Positive but noisy: ~38% WR, avgR~+0.12, maxDD~10–11R, streak~10",
    ),
    V1Book(
        "SOLUSDT",
        "1h",
        "secondary",
        0.005,
        True,
        "Thin sample / modest edge — optional secondary at half-core risk",
    ),
    V1Book(
        "BTCUSDT",
        "4h",
        "secondary",
        0.010,
        False,
        "Swing book: solid avgR, low n — enable explicitly",
    ),
    V1Book(
        "ETHUSDT",
        "4h",
        "secondary",
        0.005,
        False,
        "Swing book: positive but higher DD — enable explicitly",
    ),
    V1Book(
        "BTCUSDT",
        "15m",
        "research",
        0.0,
        False,
        "Fee-dominated / negative expectancy — paper research only",
    ),
    V1Book(
        "ETHUSDT",
        "15m",
        "research",
        0.0,
        False,
        "Not production-ready",
    ),
    V1Book(
        "SOLUSDT",
        "15m",
        "research",
        0.0,
        False,
        "Not production-ready",
    ),
)

_BOOK_INDEX: dict[tuple[str, str], V1Book] = {
    (b.symbol, b.timeframe): b for b in V1_BOOKS
}

# Symbol-level Path A paper risk when live setup TF ≠ research TF (mtf_setup).
# Maps research claim risk onto paper sizing by symbol only.
V1_PAPER_RISK_BY_SYMBOL: dict[str, float] = {
    "BTCUSDT": 0.015,  # core
    "ETHUSDT": 0.005,  # secondary
    "SOLUSDT": 0.005,  # secondary optional
}

# Weekly review thresholds (manual or script) vs backtest ranges.
V1_MONITOR_THRESHOLDS: dict[str, Any] = {
    "min_trades_for_review": 10,
    "win_rate_floor_vs_backtest": 0.70,  # flag if realized WR < 70% of BT WR
    "max_dd_multiple_vs_backtest": 1.5,  # pause/reduce if DD > 1.5× BT max DD
    "max_losing_streak_multiple": 1.5,
    "backtest_reference": {
        ("BTCUSDT", "1h"): {
            "win_rate": 0.56,
            "avg_R": 0.66,
            "max_dd_R": 2.5,
            "max_losing_streak": 2,
            "n": 27,
        },
        ("ETHUSDT", "1h"): {
            "win_rate": 0.38,
            "avg_R": 0.12,
            "max_dd_R": 10.5,
            "max_losing_streak": 10,
            "n": 40,
        },
        ("SOLUSDT", "1h"): {
            "win_rate": 0.50,
            "avg_R": 0.52,
            "max_dd_R": 1.0,
            "max_losing_streak": 2,
            "n": 4,
            "note": "Thin sample — treat thresholds as soft",
        },
        ("BTCUSDT", "4h"): {
            "win_rate": 0.55,
            "avg_R": 0.68,
            "max_dd_R": 2.1,
            "max_losing_streak": 2,
            "n": 11,
        },
        ("ETHUSDT", "4h"): {
            "win_rate": 0.40,
            "avg_R": 0.47,
            "max_dd_R": 5.3,
            "max_losing_streak": 4,
            "n": 15,
        },
    },
}

# Minimal regime rules (HTF already enforced in COMBO_02 / Path A).
V1_REGIME_RULES: tuple[str, ...] = (
    "Path A / COMBO_02 LONG only when 4h and 1h are both BULLISH (HTF_ALIGNED); fail closed.",
    "No additional cross-asset filters in v1 (e.g. SOL gated on BTC 4h) — keep HTF local to the asset.",
    "15m and sub-hour setups are research-only; do not size as live production.",
)


def enabled_v1_books(
    *,
    secondary_enabled: bool = True,
    timeframes: frozenset[str] | set[str] | None = None,
    include_disabled_defaults: bool = False,
) -> list[V1Book]:
    """Return production books eligible for the v1 paper watcher.

    Default: enabled-by-default 1h books (BTC core, ETH/SOL secondary).
    15m research and off-by-default 4h swing books are excluded unless
    ``include_disabled_defaults`` is True and they pass the timeframe filter.
    """
    want_tf = {
        normalize_timeframe(t)
        for t in (timeframes if timeframes is not None else CORE_TIMEFRAMES)
    }
    out: list[V1Book] = []
    for book in V1_BOOKS:
        if normalize_timeframe(book.timeframe) not in want_tf:
            continue
        if book.tier == "research" or book.risk_percent <= 0:
            continue
        if not include_disabled_defaults and not book.enabled_by_default:
            continue
        if book.tier == "secondary" and not secondary_enabled:
            continue
        out.append(book)
    return out


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").upper().strip()


def normalize_timeframe(tf: str) -> str:
    return (tf or "").lower().strip()


def lookup_book(symbol: str, timeframe: str) -> V1Book | None:
    return _BOOK_INDEX.get((normalize_symbol(symbol), normalize_timeframe(timeframe)))


def classify_tier(symbol: str, timeframe: str) -> Tier | None:
    book = lookup_book(symbol, timeframe)
    if book is not None:
        return book.tier
    tf = normalize_timeframe(timeframe)
    if tf in RESEARCH_TIMEFRAMES:
        return "research"
    if tf in CORE_TIMEFRAMES and normalize_symbol(symbol) in CORE_SYMBOLS:
        return "core"
    if tf in CORE_TIMEFRAMES | SECONDARY_TIMEFRAMES and normalize_symbol(
        symbol
    ) in SECONDARY_SYMBOLS | CORE_SYMBOLS:
        return "secondary"
    return None


def risk_percent_for(symbol: str, timeframe: str | None = None) -> float | None:
    """Return configured risk fraction, or None if unknown/research (0%)."""
    if timeframe:
        book = lookup_book(symbol, timeframe)
        if book is not None:
            return float(book.risk_percent)
    sym = normalize_symbol(symbol)
    if sym in V1_PAPER_RISK_BY_SYMBOL:
        return float(V1_PAPER_RISK_BY_SYMBOL[sym])
    return None


def recommended_risk_usd(
    symbol: str,
    timeframe: str,
    *,
    principal_usd: float = DEFAULT_PRINCIPAL_USD,
    fallback_risk_usd: float | None = None,
) -> float:
    """Dollar risk (1R) for backtests. Research books keep ``fallback_risk_usd``."""
    book = lookup_book(symbol, timeframe)
    principal = max(1.0, float(principal_usd))
    if book is None:
        return float(fallback_risk_usd if fallback_risk_usd is not None else 20.0)
    if book.tier == "research" or book.risk_percent <= 0:
        return float(fallback_risk_usd if fallback_risk_usd is not None else 20.0)
    return max(1.0, principal * float(book.risk_percent))


def paper_symbol_allowed(
    symbol: str,
    *,
    secondary_enabled: bool = True,
    universe_only: bool = True,
) -> tuple[bool, str]:
    """Whether Path A may open this symbol under the v1 production profile."""
    sym = normalize_symbol(symbol)
    if not universe_only:
        return True, ""
    if sym in CORE_SYMBOLS:
        return True, "core"
    if sym in SECONDARY_SYMBOLS:
        if secondary_enabled:
            return True, "secondary"
        return False, "secondary_disabled"
    return False, "outside_v1_universe"


def paper_risk_percent(
    symbol: str,
    *,
    secondary_enabled: bool = True,
    fallback: float = 0.02,
) -> float:
    """Effective Path A risk % for a symbol under the v1 profile."""
    ok, reason = paper_symbol_allowed(
        symbol, secondary_enabled=secondary_enabled, universe_only=True
    )
    if not ok:
        return 0.0
    pct = risk_percent_for(symbol)
    if pct is None:
        return float(fallback)
    if reason == "secondary" and not secondary_enabled:
        return 0.0
    return float(pct)


def profile_summary() -> dict[str, Any]:
    """JSON-serializable summary for API / UI / status endpoints."""
    return {
        "combo_id": COMBO_ID,
        "combo_version": COMBO_VERSION,
        "principal_usd_default": DEFAULT_PRINCIPAL_USD,
        "regime_rules": list(V1_REGIME_RULES),
        "books": [
            {
                "symbol": b.symbol,
                "timeframe": b.timeframe,
                "tier": b.tier,
                "risk_percent": b.risk_percent,
                "risk_pct_display": f"{b.risk_percent * 100:g}%",
                "risk_usd_at_1k": round(b.risk_percent * DEFAULT_PRINCIPAL_USD, 2)
                if b.risk_percent > 0
                else 0.0,
                "enabled_by_default": b.enabled_by_default,
                "notes": b.notes,
            }
            for b in V1_BOOKS
        ],
        "paper_risk_by_symbol": dict(V1_PAPER_RISK_BY_SYMBOL),
        "monitor_thresholds": {
            "min_trades_for_review": V1_MONITOR_THRESHOLDS["min_trades_for_review"],
            "win_rate_floor_vs_backtest": V1_MONITOR_THRESHOLDS[
                "win_rate_floor_vs_backtest"
            ],
            "max_dd_multiple_vs_backtest": V1_MONITOR_THRESHOLDS[
                "max_dd_multiple_vs_backtest"
            ],
            "max_losing_streak_multiple": V1_MONITOR_THRESHOLDS[
                "max_losing_streak_multiple"
            ],
        },
        "do_not": [
            "Weaken HTF or promote COMBO_02_LOCAL / Path B as v1",
            "Add 15m (or sub-hour) as a live production timeframe",
            "Curve-fit ETH/SOL parameters to chase more trades",
            "Raise ETH 1h risk toward BTC levels without a new evidence window",
        ],
    }
