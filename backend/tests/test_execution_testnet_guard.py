"""D128c / D131: the Binance Futures TESTNET client refuses unless every C1 v2.5 guard passes.

No network: no test here sends a request. Dummy key strings only; real keys are never read (the tests never call
get_settings and never open the root .env).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.execution.testnet_client import (
    TESTNET_HOSTS,
    BinanceFuturesTestnetClient,
    TestnetRefused,
    check_testnet_url,
    constitution_version,
    make_testnet_client,
)

DUMMY_KEY = "dummy-key-for-tests"
DUMMY_SECRET = "dummy-secret-for-tests"


def _constitution(tmp_path: Path, version: str) -> Path:
    p = tmp_path / f"CONSTITUTION_v{version}.md"
    p.write_text(f"=== CONSTITUTION v{version} (copy verbatim into every prompt you generate) ===\nC1. ...\n", encoding="utf-8")
    return p


def _build(tmp_path: Path, **kw):
    args = dict(enabled=True, base_url="https://testnet.binancefuture.com", api_key=DUMMY_KEY, api_secret=DUMMY_SECRET,
                constitution_path=_constitution(tmp_path, "2.5"))
    args.update(kw)
    return BinanceFuturesTestnetClient(**args)


def test_refuses_by_default_flag_off(tmp_path):
    from app.config import Settings

    assert Settings.model_fields["execution_testnet_enabled"].default is False
    with pytest.raises(TestnetRefused, match="EXECUTION_TESTNET_ENABLED"):
        make_testnet_client(SimpleNamespace())
    with pytest.raises(TestnetRefused, match="EXECUTION_TESTNET_ENABLED"):
        _build(tmp_path, enabled=False)
    with pytest.raises(TestnetRefused):
        _build(tmp_path, enabled="true")  # only the boolean True enables it


@pytest.mark.parametrize("version", ["2.4", "1.9", "2.0"])
def test_refuses_a_constitution_older_than_v2_5(tmp_path, version):
    with pytest.raises(TestnetRefused, match="v2.5 or later"):
        _build(tmp_path, constitution_path=_constitution(tmp_path, version))


def test_refuses_a_missing_or_unreadable_constitution(tmp_path):
    with pytest.raises(TestnetRefused, match="unreadable"):
        _build(tmp_path, constitution_path=tmp_path / "missing.md")
    bad = tmp_path / "bad.md"
    bad.write_text("no version line\n", encoding="utf-8")
    with pytest.raises(TestnetRefused):
        _build(tmp_path, constitution_path=bad)


@pytest.mark.parametrize("url", [
    "https://fapi.binance.com",
    "https://api.binance.com",
    "https://dapi.binance.com",
    "http://testnet.binancefuture.com",
    "https://testnet.binancefuture.com.evil.example",
    "https://evil.example/testnet.binancefuture.com",
    "https://user:pass@testnet.binancefuture.com",
    "https://testnet.binancefuture.com:8443",
    "wss://fstream.binance.com",
    "",
])
def test_refuses_every_non_testnet_base_url(tmp_path, url):
    with pytest.raises(TestnetRefused):
        check_testnet_url(url)
    with pytest.raises(TestnetRefused):
        _build(tmp_path, base_url=url)


def test_mainnet_url_raises_even_with_everything_else_valid(tmp_path):
    with pytest.raises(TestnetRefused, match="non-testnet"):
        _build(tmp_path, base_url="https://fapi.binance.com")


def test_refuses_missing_keys(tmp_path):
    with pytest.raises(TestnetRefused, match="missing"):
        _build(tmp_path, api_key="")
    with pytest.raises(TestnetRefused, match="missing"):
        _build(tmp_path, api_secret="")


def test_builds_for_testnet_hosts_and_never_shows_the_keys(tmp_path):
    for host in sorted(TESTNET_HOSTS):
        client = _build(tmp_path, base_url=f"https://{host}/")
        text = repr(client) + str(client)
        assert DUMMY_KEY not in text and DUMMY_SECRET not in text and "***" in text
    assert constitution_version(_constitution(tmp_path, "2.5")) == (2, 5)
    assert constitution_version(_constitution(tmp_path, "3.0")) == (3, 0)


def test_signature_is_binance_hmac_sha256(tmp_path):
    import hashlib
    import hmac

    client = _build(tmp_path)
    signed = client.sign({"symbol": "BTCUSDT", "side": "BUY", "timestamp": 1})
    query = "symbol=BTCUSDT&side=BUY&timestamp=1"
    assert signed == f"{query}&signature={hmac.new(DUMMY_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()}"


def test_every_request_re_checks_the_url_and_sends_nothing_to_mainnet(tmp_path):
    sent: list[str] = []

    class _Http:
        def request(self, method, url, headers=None):
            sent.append(url)
            raise AssertionError("must not be reached")

    client = _build(tmp_path, http=_Http())
    client._base = "https://fapi.binance.com"  # noqa: SLF001 - simulate a later tampering
    with pytest.raises(TestnetRefused):
        client.get_order("BTCUSDT", "cs-EN-x")
    assert sent == []


def test_settings_keys_are_secret_strings():
    from app.config import Settings

    s = Settings(BINANCE_TESTNET_API_KEY=DUMMY_KEY, BINANCE_TESTNET_API_SECRET=DUMMY_SECRET)
    text = repr(s) + str(s.model_dump())
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
