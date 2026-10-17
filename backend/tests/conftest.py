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
