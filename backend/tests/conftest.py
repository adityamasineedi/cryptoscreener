import os
from pathlib import Path

import pytest

# Owner ruling D50 (P13): tests never reach the live database. app.config's default DATABASE_URL (and the
# repo's .env) is the live Postgres. Environment variables outrank .env in pydantic-settings, and this runs
# before any test imports the app, so by default every test sees no database and no Redis. A test run that
# needs a database sets TEST_DATABASE_URL to an isolated test database; the live one is refused.
_NO_DB_URL = "postgresql+asyncpg://tests-no-database@127.0.0.1:1/none"


def _live_database_urls() -> set[str]:
    urls = set()
    for env in (Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[1] / ".env"):
        try:
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    urls.add(line.split("=", 1)[1].strip())
        except OSError:
            pass
    try:
        from app.config import Settings

        urls.add(str(Settings.model_fields["database_url"].default))
    except Exception:  # noqa: BLE001
        pass
    return {u.replace("postgresql+asyncpg://", "postgresql://") for u in urls if u}


_test_db = os.environ.get("TEST_DATABASE_URL", "").strip()
if _test_db:
    if _test_db.replace("postgresql+asyncpg://", "postgresql://") in _live_database_urls():
        raise pytest.UsageError("TEST_DATABASE_URL points at the live database; use an isolated test database (P13)")
    os.environ["DATABASE_URL"] = _test_db
    os.environ["DATABASE_ENABLED"] = "true"
else:
    os.environ["DATABASE_URL"] = _NO_DB_URL
    os.environ["DATABASE_ENABLED"] = "false"
os.environ["REDIS_ENABLED"] = os.environ.get("TEST_REDIS_ENABLED", "false")

pytest_plugins = []


@pytest.fixture(autouse=True)
def _echo_closed_path_a_eval(monkeypatch):
    """Unit tests supply their own payload. Production re-reads closed candles.

    The NEAR regression replaces this with the closed-candle fixture.
    """
    import app.services.path_a_paper_validation as validation

    monkeypatch.setattr(
        validation,
        "analyze_closed_path_a",
        lambda _symbol, payload: dict(payload or {}),
    )

@pytest.fixture(autouse=True)
def _empty_passed_symbol_allowlist():
    """Tests run with no per-coin allowlist entries unless they set their own.

    Keeps passed-book tests independent of the committed
    passed_symbol_allowlist.json (its BTC/ETH entries are data, not test inputs).
    """
    from app.research import passed_symbol_allowlist as allowlist

    allowlist.set_allowlist_for_tests(allowlist.SymbolAllowlist(entries={}, source="tests"))
    yield
    allowlist.set_allowlist_for_tests(None)


@pytest.fixture
def passed_book_open(monkeypatch):
    """Passed-regime book open, matching production (REGIME_BOOK_OPEN = True)."""
    import app.services.passed_paper_watcher as watcher

    monkeypatch.setattr(watcher, "REGIME_BOOK_OPEN", True)


@pytest.fixture
def passed_book_closed(monkeypatch):
    """Close the passed-regime book to observe the refuse-to-open path."""
    import app.services.passed_paper_watcher as watcher

    monkeypatch.setattr(watcher, "REGIME_BOOK_OPEN", False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _decision_journal_switch():
    """Behavior-identity check for the decision journal (owner ruling D19).

    Off by default. With DECISION_JOURNAL_TEST_PATH set, every test runs with the journal on, writing
    to that file; the suite must pass exactly as it does with the journal off.
    """
    import os

    from app.services import decision_journal

    path = os.environ.get("DECISION_JOURNAL_TEST_PATH")
    # P14: DECISION_JOURNAL_TEST_SIGNAL_DETAIL=1 also turns signal detail on for the whole suite.
    detail = os.environ.get("DECISION_JOURNAL_TEST_SIGNAL_DETAIL", "") not in ("", "0", "false", "False")
    decision_journal.configure(path or None, signal_detail=detail)
    yield
    decision_journal.configure(None)
