"""Causal universe membership.

Historical market-cap ranks are used only when at least 100 non-stable
as-of observations exist. Otherwise tiers are liquidity tiers and are labeled
LIQUIDITY_TIER_NOT_MARKET_CAP. Base-asset volume is never ranked across symbols.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.intraday_bos_sd.candles import Series
from app.research.intraday_bos_sd.spec import (
    LARGE_RANK_MAX,
    LIQUIDITY_LOOKBACK_DAYS,
    MID_RANK_MAX,
    MIN_LISTING_AGE_DAYS,
    MIN_MEDIAN_DAILY_QUOTE_VOLUME_LARGE,
    MIN_MEDIAN_DAILY_QUOTE_VOLUME_MID,
    MIN_SYMBOLS_FOR_MARKET_CAP_RANK,
    STABLE_BASES,
    WRAPPED_BASES,
)


def base_asset(symbol: str) -> str:
    sym = symbol.upper()
    for quote in ("USDT", "USDC", "FDUSD"):
        if sym.endswith(quote) and len(sym) > len(quote):
            return sym[: -len(quote)]
    return sym


def excluded_symbol(symbol: str) -> str | None:
    base = base_asset(symbol)
    if base in STABLE_BASES:
        return "stablecoin"
    if base in WRAPPED_BASES:
        return "wrapped_duplicate"
    return None


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def daily_quote_volumes(series: Series) -> dict:
    """Days with any missing quote volume are excluded. Base volume is not summed."""
    days: dict[datetime, float] = {}
    tainted: set[datetime] = set()
    for bar in series.bars:
        day = datetime(bar.open_time.year, bar.open_time.month, bar.open_time.day, tzinfo=timezone.utc)
        if bar.quote_volume is None:
            tainted.add(day)
            continue
        days[day] = days.get(day, 0.0) + float(bar.quote_volume)
    for day in tainted:
        days.pop(day, None)
    return days


def trailing_median_quote_volume(series: Series, as_of: datetime) -> float | None:
    days = daily_quote_volumes(series)
    if not days:
        return None
    start = as_of.astimezone(timezone.utc) - timedelta(days=LIQUIDITY_LOOKBACK_DAYS)
    window = [v for day, v in days.items() if start <= day < as_of]
    return median(window)


def classify_membership(
    *,
    symbol: str,
    listed_at: datetime | None,
    as_of: datetime,
    median_quote_volume: float | None,
    market_cap: float | None,
    caps_available: int,
    liquidity_rank: int | None,
) -> dict:
    reason = excluded_symbol(symbol)
    if reason:
        return {"symbol": symbol, "eligible": False, "tier": None, "tier_kind": None, "reason": reason}
    if listed_at is None or as_of - listed_at < timedelta(days=MIN_LISTING_AGE_DAYS):
        return {"symbol": symbol, "eligible": False, "tier": None, "tier_kind": None, "reason": "listing_age"}
    if median_quote_volume is None:
        return {
            "symbol": symbol,
            "eligible": False,
            "tier": None,
            "tier_kind": None,
            "reason": "quote_volume_unavailable_base_volume_not_compared",
        }
    if caps_available >= MIN_SYMBOLS_FOR_MARKET_CAP_RANK and market_cap is not None and market_cap > 0:
        return {
            "symbol": symbol,
            "eligible": False,
            "tier": None,
            "tier_kind": "MARKET_CAP",
            "reason": "rank_assigned_by_caller",
            "market_cap": market_cap,
        }
    if liquidity_rank is None:
        return {"symbol": symbol, "eligible": False, "tier": None, "tier_kind": None, "reason": "rank_unavailable"}
    kind = "LIQUIDITY_TIER_NOT_MARKET_CAP"
    if liquidity_rank <= LARGE_RANK_MAX and median_quote_volume >= MIN_MEDIAN_DAILY_QUOTE_VOLUME_LARGE:
        return {"symbol": symbol, "eligible": True, "tier": "LARGE_LIQUIDITY", "tier_kind": kind, "reason": "ok"}
    if liquidity_rank <= MID_RANK_MAX and median_quote_volume >= MIN_MEDIAN_DAILY_QUOTE_VOLUME_MID:
        return {"symbol": symbol, "eligible": True, "tier": "MID_LIQUIDITY", "tier_kind": kind, "reason": "ok"}
    return {"symbol": symbol, "eligible": False, "tier": None, "tier_kind": kind, "reason": "outside_liquidity_ranks_or_floor"}


def assign_market_cap_tiers(rows: list[dict]) -> list[dict]:
    """rows need symbol and market_cap. Rank 1 is the largest cap."""
    ranked = sorted(rows, key=lambda r: (-float(r["market_cap"]), r["symbol"]))
    out = []
    for index, row in enumerate(ranked, start=1):
        tier = None
        if index <= LARGE_RANK_MAX:
            tier = "LARGE_CAP"
        elif index <= MID_RANK_MAX:
            tier = "MID_CAP"
        item = dict(row)
        item["rank"] = index
        item["tier"] = tier
        item["tier_kind"] = "MARKET_CAP"
        item["eligible"] = tier is not None
        out.append(item)
    return out
