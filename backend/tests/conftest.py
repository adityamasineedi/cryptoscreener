import pytest

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
    decision_journal.configure(path or None)
    yield
    decision_journal.configure(None)
