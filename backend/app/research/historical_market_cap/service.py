"""Orchestration helpers for historical market-cap research."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import text

from app.core.logging import get_logger
from app.research.config import ResearchConfig
from app.research.historical_market_cap.classifier import (
    CLASSIFICATION_RULE_VERSION,
    cap_group_at,
    classify_strategy_cap_group,
)
from app.research.historical_market_cap.provider import CoinGeckoHistoricalMarketCapClient
from app.research.historical_market_cap import repository as repo
from app.services.database import db_manager

logger = get_logger("historical_market_cap_service")

COVERAGE_BUCKETS_DAYS = {
    ">=1m": 30,
    ">=3m": 90,
    ">=6m": 180,
    ">=1y": 365,
    ">=2y": 730,
    ">=3y": 1095,
    ">=5y": 1825,
}


async def list_usdt_perp_symbols() -> list[str]:
    if db_manager.engine is None:
        return []
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT symbol FROM symbols
                WHERE quote_asset = 'USDT'
                  AND market_type = 'futures_perp'
                  AND status = 'TRADING'
                ORDER BY symbol ASC
                """
            )
        )
        return [str(row[0]).upper() for row in r.fetchall()]


async def ohlcv_span(symbol: str, timeframe: str) -> dict[str, Any]:
    if db_manager.engine is None:
        return {"bars": 0, "data_start": None, "data_end": None}
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT min(time), max(time), count(*)
                FROM ohlcv
                WHERE symbol = :symbol AND timeframe = :tf
                """
            ),
            {"symbol": symbol.upper(), "tf": timeframe},
        )
        row = r.fetchone()
        if not row or not row[2]:
            return {"bars": 0, "data_start": None, "data_end": None}
        return {
            "bars": int(row[2]),
            "data_start": row[0].isoformat() if row[0] else None,
            "data_end": row[1].isoformat() if row[1] else None,
            "data_start_dt": row[0],
            "data_end_dt": row[1],
        }


def _base(symbol: str) -> str:
    return symbol.upper().replace("USDT", "").replace("USDC", "")


async def ingest_symbol_map_from_markets(
    client: CoinGeckoHistoricalMarketCapClient,
    symbols: Sequence[str],
) -> dict[str, str]:
    """Map Binance symbols → CoinGecko coin ids via /coins/markets."""
    markets = await client.fetch_markets_pages(pages=4)
    by_base: dict[str, dict[str, Any]] = {}
    for row in markets:
        base = str(row.get("symbol") or "").upper()
        cid = row.get("id")
        if base and cid and base not in by_base:
            by_base[base] = row
    mapped: dict[str, str] = {}
    for sym in symbols:
        base = _base(sym)
        row = by_base.get(base)
        if not row:
            continue
        cid = str(row["id"])
        mapped[sym.upper()] = cid
        await repo.upsert_symbol_map(
            sym, provider="coingecko", provider_coin_id=cid, base_asset=base
        )
    return mapped


async def ingest_historical_for_symbols(
    symbols: Sequence[str],
    *,
    days: str = "365",
    max_symbols: int | None = None,
) -> dict[str, Any]:
    """Fetch + persist CoinGecko market_chart for symbols (rate-limited)."""
    await repo.ensure_schema()
    targets = [s.upper() for s in symbols]
    if max_symbols is not None:
        targets = targets[: int(max_symbols)]
    stats = {
        "requested": len(targets),
        "mapped": 0,
        "fetched_ok": 0,
        "fetched_empty": 0,
        "rows_upserted": 0,
        "unmapped": [],
    }
    async with CoinGeckoHistoricalMarketCapClient() as client:
        mapped = await ingest_symbol_map_from_markets(client, targets)
        stats["mapped"] = len(mapped)
        for sym in targets:
            cid = mapped.get(sym) or await repo.get_coin_id(sym)
            if not cid:
                stats["unmapped"].append(sym)
                continue
            series = await client.fetch_market_chart(cid, days=days, interval="daily")
            if not series:
                stats["fetched_empty"] += 1
                continue
            rows = client.observation_rows(sym, cid, series)
            n = await repo.upsert_observations(rows)
            stats["rows_upserted"] += n
            stats["fetched_ok"] += 1
            logger.info(
                "hist_mcap_ingested",
                symbol=sym,
                coin_id=cid,
                points=len(series),
            )
    return stats


def span_days(first: datetime | None, last: datetime | None) -> float:
    if first is None or last is None:
        return 0.0
    return max(0.0, (last - first).total_seconds() / 86400.0)


async def build_coverage_report(
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Coverage across all USDT perps using persisted historical observations."""
    await repo.ensure_schema()
    symbols = await list_usdt_perp_symbols()
    cov_rows = await repo.coverage_summary()
    by_sym = {r["symbol"]: r for r in cov_rows}
    as_of = as_of or datetime.now(timezone.utc)
    obs_all = await repo.load_observations_many(list(by_sym.keys()))

    hist_buckets = {k: 0 for k in COVERAGE_BUCKETS_DAYS}
    with_hist = 0
    with_current_obs = 0  # has any observation
    no_cap = 0
    partial = 0
    group_counts = {
        "LARGE_CAP": 0,
        "MID_CAP": 0,
        "SMALL_CAP": 0,
        "UNAVAILABLE": 0,
        "BTC": 0,
        "ETH": 0,
        "UNKNOWN": 0,
    }
    detail: list[dict[str, Any]] = []
    rcfg = ResearchConfig()

    for sym in symbols:
        meta = by_sym.get(sym)
        if not meta:
            no_cap += 1
            group_counts["UNAVAILABLE"] += 1
            detail.append(
                {
                    "symbol": sym,
                    "first_cap_timestamp": None,
                    "last_cap_timestamp": None,
                    "observation_count": 0,
                    "strategy_cap_group_as_of": "UNAVAILABLE",
                    "has_historical_cap": False,
                }
            )
            continue
        with_current_obs += 1
        with_hist += 1
        first = datetime.fromisoformat(meta["first_cap_timestamp"]) if meta["first_cap_timestamp"] else None
        last = datetime.fromisoformat(meta["last_cap_timestamp"]) if meta["last_cap_timestamp"] else None
        days = span_days(first, last)
        for label, need in COVERAGE_BUCKETS_DAYS.items():
            if days >= need:
                hist_buckets[label] += 1
        # partial: has some history but < 30d span
        if 0 < days < 30:
            partial += 1
        lookup = cap_group_at(sym, as_of, obs_all.get(sym) or [], config=rcfg)
        tax = str(lookup.cap_group or "")
        strat = lookup.strategy_cap_group
        # Prefer taxonomy BTC/ETH labels when present; else strategy bucket.
        if tax in ("BTC", "ETH", "btc", "eth"):
            group_counts[tax.upper()] += 1
        elif strat in group_counts:
            group_counts[strat] += 1
        else:
            group_counts["UNKNOWN"] += 1
        detail.append(
            {
                "symbol": sym,
                "first_cap_timestamp": meta["first_cap_timestamp"],
                "last_cap_timestamp": meta["last_cap_timestamp"],
                "observation_count": meta["observation_count"],
                "history_days": round(days, 2),
                "strategy_cap_group_as_of": lookup.strategy_cap_group,
                "taxonomy_label_as_of": lookup.cap_group,
                "has_historical_cap": True,
            }
        )

    return {
        "source": {
            "provider": "coingecko",
            "endpoint": "/coins/{id}/market_chart",
            "observation_source_label": "coingecko_market_chart",
            "currency": "usd",
            "units": "USD market capitalization (CoinGecko market_caps)",
            "classification_rule_version": CLASSIFICATION_RULE_VERSION,
            "thresholds": {
                "large_cap_min": rcfg.large_cap_min,
                "mid_cap_min": rcfg.mid_cap_min,
                "mid_cap_max": rcfg.mid_cap_max,
                "small_cap_min": rcfg.small_cap_min,
                "small_cap_max": rcfg.small_cap_max,
            },
            "timestamp_semantics": (
                "effective_time = CoinGecko chart point time (UTC). "
                "cap_group_at(T) uses latest observation with effective_time <= T. "
                "Never uses future observations."
            ),
            "missing_data_behavior": "CAP_GROUP_UNAVAILABLE — no fabrication / no silent substitute",
            "fallback_behavior": "None — no price/volume/rank/FDV/name inference",
            "current_vs_historical": (
                "CURRENT_CAP = live CoinGecko /coins/markets via engine_store. "
                "HISTORICAL_CAP = research_market_cap_observations. "
                "Primary research uses HISTORICAL_CAP only."
            ),
            "btc_eth_taxonomy": (
                "classify_asset_group still labels BTC/ETH as BTC/ETH; "
                "they do not map to LARGE/MID/SMALL strategy groups."
            ),
        },
        "as_of": as_of.isoformat(),
        "total_binance_usdt_perp_symbols": len(symbols),
        "symbols_with_historical_cap": with_hist,
        "symbols_with_any_observation": with_current_obs,
        "symbols_with_no_cap": no_cap,
        "symbols_with_partial_history_lt_30d": partial,
        "historical_coverage_counts": hist_buckets,
        "cap_group_counts_as_of": group_counts,
        "symbols": detail,
    }


async def build_eligibility_rows(
    *,
    timeframes: Sequence[str] = ("5m", "15m", "1h"),
    min_ohlcv_bars: int = 60,
    min_cap_history_days: float = 30.0,
) -> list[dict[str, Any]]:
    symbols = await list_usdt_perp_symbols()
    obs = await repo.load_observations_many(symbols)
    cov = {r["symbol"]: r for r in await repo.coverage_summary()}
    rows: list[dict[str, Any]] = []
    rcfg = ResearchConfig()
    for sym in symbols:
        meta = cov.get(sym)
        mcap_start = meta["first_cap_timestamp"] if meta else None
        mcap_end = meta["last_cap_timestamp"] if meta else None
        first = datetime.fromisoformat(mcap_start) if mcap_start else None
        last = datetime.fromisoformat(mcap_end) if mcap_end else None
        hist_days = span_days(first, last)
        for tf in timeframes:
            oh = await ohlcv_span(sym, tf)
            exclusion = None
            eligible = True
            # Cap group as-of OHLCV end (or now)
            as_of = oh.get("data_end_dt") or datetime.now(timezone.utc)
            lookup = cap_group_at(sym, as_of, obs.get(sym) or [], config=rcfg)
            strat = lookup.strategy_cap_group
            if oh["bars"] <= 0:
                eligible = False
                exclusion = "TIMEFRAME_UNAVAILABLE"
            elif oh["bars"] < min_ohlcv_bars:
                eligible = False
                exclusion = "OHLCV_HISTORY_TOO_SHORT"
            elif not meta or hist_days <= 0:
                eligible = False
                exclusion = "CAP_GROUP_UNAVAILABLE"
            elif hist_days < min_cap_history_days:
                eligible = False
                exclusion = "CAP_HISTORY_TOO_SHORT"
            elif strat == "UNAVAILABLE":
                eligible = False
                # BTC/ETH or below small_cap_min / unknown
                exclusion = "CAP_GROUP_UNAVAILABLE"
            # Ensure as-of at data_start exists for historical research
            start_dt = oh.get("data_start_dt")
            if eligible and start_dt is not None:
                start_lookup = cap_group_at(
                    sym, start_dt, obs.get(sym) or [], config=rcfg
                )
                if start_lookup.status != "OK":
                    eligible = False
                    exclusion = "CAP_GROUP_UNAVAILABLE"
            rows.append(
                {
                    "symbol": sym,
                    "timeframe": tf,
                    "data_start": oh["data_start"],
                    "data_end": oh["data_end"],
                    "bars": oh["bars"],
                    "market_cap_start": mcap_start,
                    "market_cap_end": mcap_end,
                    "cap_group": strat,
                    "taxonomy_label": lookup.cap_group,
                    "eligible": eligible,
                    "exclusion_reason": exclusion,
                    "cap_history_days": round(hist_days, 2),
                }
            )
    return rows


def select_controlled_universe(
    eligibility: Sequence[dict[str, Any]],
    *,
    per_group: int = 3,
    timeframe: str = "15m",
) -> dict[str, list[str]]:
    """Deterministic: alphabetical among eligible symbols with highest bar coverage."""
    by_group: dict[str, list[dict[str, Any]]] = {
        "LARGE_CAP": [],
        "MID_CAP": [],
        "SMALL_CAP": [],
    }
    for row in eligibility:
        if row.get("timeframe") != timeframe:
            continue
        if not row.get("eligible"):
            continue
        g = row.get("cap_group")
        if g in by_group:
            by_group[g].append(row)
    selected: dict[str, list[str]] = {}
    for g, rows in by_group.items():
        rows_sorted = sorted(
            rows,
            key=lambda r: (-int(r.get("bars") or 0), r["symbol"]),
        )
        # unique symbols preserving order
        seen: list[str] = []
        for r in rows_sorted:
            if r["symbol"] not in seen:
                seen.append(r["symbol"])
            if len(seen) >= per_group:
                break
        selected[g] = seen
    return selected
