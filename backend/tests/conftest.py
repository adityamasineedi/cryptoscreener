import pytest

pytest_plugins = []

@pytest.fixture
def anyio_backend():
    return "asyncio"
