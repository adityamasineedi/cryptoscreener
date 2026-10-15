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
    """Reopen the passed-regime book for a test that observes the open path.

    Production keeps it closed (REGIME_BOOK_OPEN = False). Gate tests use an open
    as their observable; the gates themselves are unchanged.
    """
    import app.services.passed_paper_watcher as watcher

    monkeypatch.setattr(watcher, "REGIME_BOOK_OPEN", True)


@pytest.fixture
def anyio_backend():
    return "asyncio"
