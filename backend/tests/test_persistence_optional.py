from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.config import Settings
from app.services.database import DatabaseManager
from app.services.persistence import PersistenceService
from app.services.redis_manager import RedisManager


def test_database_disabled_is_noop():
    settings = Settings(DATABASE_ENABLED=False)
    db = DatabaseManager()
    asyncio.run(db.connect(settings))
    assert db.enabled is False
    assert db.status == "disabled"
    health = asyncio.run(db.health())
    assert health["enabled"] is False


def test_redis_disabled_is_noop():
    settings = Settings(REDIS_ENABLED=False)
    rm = RedisManager()
    asyncio.run(rm.connect(settings))
    assert rm.enabled is False
    assert rm.status == "disabled"


def test_persistence_inactive_when_db_disabled():
    settings = Settings(DATABASE_ENABLED=False)
    # Ensure global db_manager path: PersistenceService.active uses db_manager
    from app.services import database as dbmod

    asyncio.run(dbmod.db_manager.connect(settings))
    p = PersistenceService()
    assert p.active is False
    n = asyncio.run(p.persist_ohlcv_batch([]))
    assert n == 0


def test_as_utc_dt_parses_iso_strings_for_asyncpg():
    """Paper persist must bind datetime objects, not ISO strings."""
    p = PersistenceService()
    dt = p._as_utc_dt("2026-10-04T17:00:20.356921+00:00")
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.month == 10 and dt.day == 4
    assert p._as_utc_dt(None) is None
    naive = p._as_utc_dt("2026-10-04T17:00:20")
    assert naive is not None and naive.tzinfo == timezone.utc
