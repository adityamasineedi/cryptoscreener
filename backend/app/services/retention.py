from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import Settings
from app.core.logging import get_logger
from app.services.database import db_manager

logger = get_logger("retention")


def retention_days_map(settings: Settings) -> dict[str, int]:
    cfg = settings.market_config.get("retention") or {}
    ohlcv = dict(cfg.get("ohlcv") or {})
    # defaults if yaml missing keys
    defaults = {
        "1m": 60,
        "5m": 270,
        "15m": 545,
        "1h": 1095,
        "4h": 1825,
        "1d": 3650,
    }
    out = {**defaults, **{str(k).lower(): int(v) for k, v in ohlcv.items()}}
    return out


async def apply_retention(settings: Settings) -> dict[str, Any]:
    """Delete aged rows when DATABASE_ENABLED. No-op when disabled."""
    if not db_manager.enabled or db_manager.engine is None:
        return {"status": "skipped", "reason": "database_disabled"}

    cfg = settings.market_config.get("retention") or {}
    days_map = retention_days_map(settings)
    now = datetime.now(timezone.utc)
    deleted: dict[str, int] = {}

    statements: list[tuple[str, dict[str, Any]]] = []
    for tf, days in days_map.items():
        cutoff = now - timedelta(days=days)
        statements.append(
            (
                "DELETE FROM ohlcv WHERE timeframe = :tf AND time < :cutoff",
                {"tf": tf, "cutoff": cutoff},
            )
        )
    if cfg.get("liquidations_hours"):
        statements.append(
            (
                "DELETE FROM liquidations WHERE time < :cutoff",
                {"cutoff": now - timedelta(hours=int(cfg["liquidations_hours"]))},
            )
        )
    if cfg.get("open_interest_days"):
        statements.append(
            (
                "DELETE FROM open_interest WHERE time < :cutoff",
                {"cutoff": now - timedelta(days=int(cfg["open_interest_days"]))},
            )
        )
    if cfg.get("funding_days"):
        statements.append(
            (
                "DELETE FROM funding_rates WHERE time < :cutoff",
                {"cutoff": now - timedelta(days=int(cfg["funding_days"]))},
            )
        )
    if cfg.get("signals_days"):
        statements.append(
            (
                "DELETE FROM signals WHERE time < :cutoff",
                {"cutoff": now - timedelta(days=int(cfg["signals_days"]))},
            )
        )
    if cfg.get("structure_events_days"):
        statements.append(
            (
                "DELETE FROM structure_events WHERE time < :cutoff",
                {"cutoff": now - timedelta(days=int(cfg["structure_events_days"]))},
            )
        )
    if cfg.get("supply_demand_zones_days"):
        statements.append(
            (
                "DELETE FROM supply_demand_zones WHERE updated_at < :cutoff",
                {
                    "cutoff": now
                    - timedelta(days=int(cfg["supply_demand_zones_days"]))
                },
            )
        )
    # Mirror OHLCV retention for derived volume/price series (use 1d retention as floor)
    ohlcv_days = retention_days_map(settings)
    vol_days = max(ohlcv_days.values()) if ohlcv_days else 365
    statements.append(
        (
            "DELETE FROM volume_history WHERE time < :cutoff",
            {"cutoff": now - timedelta(days=int(vol_days))},
        )
    )
    if cfg.get("ticks_hours"):
        statements.append(
            (
                "DELETE FROM price_snapshots WHERE time < :cutoff",
                {"cutoff": now - timedelta(hours=int(cfg["ticks_hours"]))},
            )
        )

    try:
        async with db_manager.engine.begin() as conn:
            for sql, params in statements:
                result = await conn.execute(text(sql), params)
                deleted[sql.split()[2]] = deleted.get(sql.split()[2], 0) + (
                    result.rowcount or 0
                )
        logger.info("retention_applied", deleted=deleted)
        return {"status": "ok", "deleted": deleted}
    except Exception as exc:  # noqa: BLE001
        logger.warning("retention_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}
