"""Postgres persistence for research historical market-cap observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text

from app.core.logging import get_logger
from app.services.database import db_manager

logger = get_logger("historical_market_cap_repository")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS research_market_cap_observations (
        symbol                      TEXT NOT NULL,
        effective_time              TIMESTAMPTZ NOT NULL,
        market_cap                  DOUBLE PRECISION NOT NULL,
        source                      TEXT NOT NULL,
        currency                    TEXT NOT NULL DEFAULT 'usd',
        cap_group                   TEXT NOT NULL,
        classification_rule_version TEXT NOT NULL,
        provider_coin_id            TEXT,
        ingested_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (symbol, effective_time, source)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_research_mcap_sym_time
        ON research_market_cap_observations (symbol, effective_time DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS research_market_cap_symbol_map (
        symbol           TEXT PRIMARY KEY,
        provider         TEXT NOT NULL,
        provider_coin_id TEXT NOT NULL,
        base_asset       TEXT,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


async def ensure_schema() -> bool:
    if not db_manager.enabled or db_manager.engine is None:
        return False
    try:
        async with db_manager.engine.begin() as conn:
            for stmt in SCHEMA:
                await conn.execute(text(stmt))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("hist_mcap_schema_failed", error=str(exc))
        return False


async def upsert_observations(rows: Sequence[dict[str, Any]]) -> int:
    if not rows:
        return 0
    if not await ensure_schema():
        return 0
    assert db_manager.engine is not None
    n = 0
    async with db_manager.engine.begin() as conn:
        for row in rows:
            await conn.execute(
                text(
                    """
                    INSERT INTO research_market_cap_observations (
                        symbol, effective_time, market_cap, source, currency,
                        cap_group, classification_rule_version, provider_coin_id
                    ) VALUES (
                        :symbol, :effective_time, :market_cap, :source, :currency,
                        :cap_group, :classification_rule_version, :provider_coin_id
                    )
                    ON CONFLICT (symbol, effective_time, source) DO UPDATE SET
                        market_cap = EXCLUDED.market_cap,
                        currency = EXCLUDED.currency,
                        cap_group = EXCLUDED.cap_group,
                        classification_rule_version = EXCLUDED.classification_rule_version,
                        provider_coin_id = EXCLUDED.provider_coin_id,
                        ingested_at = NOW()
                    """
                ),
                {
                    "symbol": str(row["symbol"]).upper(),
                    "effective_time": row["effective_time"],
                    "market_cap": float(row["market_cap"]),
                    "source": str(row["source"]),
                    "currency": str(row.get("currency") or "usd"),
                    "cap_group": str(row["cap_group"]),
                    "classification_rule_version": str(
                        row["classification_rule_version"]
                    ),
                    "provider_coin_id": row.get("provider_coin_id"),
                },
            )
            n += 1
    return n


async def upsert_symbol_map(
    symbol: str, *, provider: str, provider_coin_id: str, base_asset: str | None
) -> None:
    if not await ensure_schema():
        return
    assert db_manager.engine is not None
    async with db_manager.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO research_market_cap_symbol_map (
                    symbol, provider, provider_coin_id, base_asset, updated_at
                ) VALUES (:symbol, :provider, :provider_coin_id, :base_asset, NOW())
                ON CONFLICT (symbol) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    provider_coin_id = EXCLUDED.provider_coin_id,
                    base_asset = EXCLUDED.base_asset,
                    updated_at = NOW()
                """
            ),
            {
                "symbol": symbol.upper(),
                "provider": provider,
                "provider_coin_id": provider_coin_id,
                "base_asset": base_asset,
            },
        )


async def load_observations(symbol: str) -> list[dict[str, Any]]:
    if not db_manager.enabled or db_manager.engine is None:
        return []
    await ensure_schema()
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT symbol, effective_time, market_cap, source, currency,
                       cap_group, classification_rule_version, provider_coin_id
                FROM research_market_cap_observations
                WHERE symbol = :symbol
                ORDER BY effective_time ASC
                """
            ),
            {"symbol": symbol.upper()},
        )
        rows = []
        for row in r.fetchall():
            rows.append(
                {
                    "symbol": row[0],
                    "effective_time": row[1],
                    "market_cap": float(row[2]),
                    "source": row[3],
                    "currency": row[4],
                    "cap_group": row[5],
                    "classification_rule_version": row[6],
                    "provider_coin_id": row[7],
                }
            )
        return rows


async def load_observations_many(
    symbols: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {s.upper(): [] for s in symbols}
    if not symbols or not db_manager.enabled or db_manager.engine is None:
        return out
    await ensure_schema()
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT symbol, effective_time, market_cap, source, currency,
                       cap_group, classification_rule_version, provider_coin_id
                FROM research_market_cap_observations
                WHERE symbol = ANY(:symbols)
                ORDER BY symbol ASC, effective_time ASC
                """
            ),
            {"symbols": [s.upper() for s in symbols]},
        )
        for row in r.fetchall():
            sym = str(row[0]).upper()
            out.setdefault(sym, []).append(
                {
                    "symbol": sym,
                    "effective_time": row[1],
                    "market_cap": float(row[2]),
                    "source": row[3],
                    "currency": row[4],
                    "cap_group": row[5],
                    "classification_rule_version": row[6],
                    "provider_coin_id": row[7],
                }
            )
    return out


async def coverage_summary() -> list[dict[str, Any]]:
    if not db_manager.enabled or db_manager.engine is None:
        return []
    await ensure_schema()
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT symbol,
                       min(effective_time) AS first_cap_timestamp,
                       max(effective_time) AS last_cap_timestamp,
                       count(*) AS observation_count
                FROM research_market_cap_observations
                GROUP BY symbol
                ORDER BY symbol
                """
            )
        )
        out = []
        for row in r.fetchall():
            out.append(
                {
                    "symbol": row[0],
                    "first_cap_timestamp": row[1].isoformat() if row[1] else None,
                    "last_cap_timestamp": row[2].isoformat() if row[2] else None,
                    "observation_count": int(row[3]),
                }
            )
        return out


async def get_coin_id(symbol: str) -> str | None:
    if not db_manager.enabled or db_manager.engine is None:
        return None
    await ensure_schema()
    async with db_manager.engine.begin() as conn:
        r = await conn.execute(
            text(
                """
                SELECT provider_coin_id
                FROM research_market_cap_symbol_map
                WHERE symbol = :symbol
                """
            ),
            {"symbol": symbol.upper()},
        )
        row = r.fetchone()
        return str(row[0]) if row else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
