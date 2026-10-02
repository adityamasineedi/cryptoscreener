from __future__ import annotations

import asyncio

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
