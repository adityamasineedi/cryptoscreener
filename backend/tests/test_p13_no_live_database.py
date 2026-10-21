"""P13 (owner ruling D50): the test suite never reaches the live database or Redis by default."""

from __future__ import annotations

import os

import conftest
from app.config import Settings, get_settings


def test_tests_run_without_a_database_by_default():
    if os.environ.get("TEST_DATABASE_URL"):
        return  # an explicit isolated test database was given
    settings = get_settings()
    assert settings.database_enabled is False
    assert settings.database_url == conftest._NO_DB_URL
    assert settings.redis_enabled is False


def test_the_live_database_url_is_recognised_and_would_be_refused():
    live = conftest._live_database_urls()
    default = str(Settings.model_fields["database_url"].default).replace("postgresql+asyncpg://", "postgresql://")
    assert default in live
    assert conftest._NO_DB_URL.replace("postgresql+asyncpg://", "postgresql://") not in live
