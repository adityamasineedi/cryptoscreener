"""GET /signals/{symbol} is read-only; destructive paper POSTs need loopback or a token.

Everything the routes touch is stubbed: an in-memory paper engine, a stub setup
service, a stub persistence call and no orchestrator. Nothing is written and the
real ledger is never loaded.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.services import paper_trade as paper_mod
from app.services import setup_signals as setup_mod
from app.services.persistence import persistence

REMOTE = ("203.0.113.5", 50000)
LOOPBACK_V4 = ("127.0.0.1", 50000)
DEFAULT_PEER = "starlette-default"  # TestClient's own host, "testclient"
TOKEN = "s3cret-admin-token"

PROTECTED = (
    "/api/paper/reset",
    "/api/paper/disable",
    "/api/paper/close-legacy?confirm=true",
)

ENTRY_PAYLOAD = {
    "symbol": "BTCUSDT",
    "status": "LONG_ENTRY_CANDIDATE",
    "entry": 100.0,
    "stop": 98.0,
    "tp1": 104.0,
    "quantity": 10.0,
}


class _LedgerEngine:
    """In-memory paper book. on_setup_signal opens on an entry candidate."""

    def __init__(self) -> None:
        self.enabled = True
        self.open: list[dict[str, Any]] = [{"symbol": "ETHUSDT"}]
        self.closed: list[dict[str, Any]] = [{"symbol": "SOLUSDT", "pnl": 1.5}]
        self.setup_calls: list[str] = []
        self.calls: list[str] = []

    def on_setup_signal(self, symbol: str, payload: dict[str, Any]) -> Any:
        self.setup_calls.append(symbol)
        if payload.get("status") == "LONG_ENTRY_CANDIDATE":
            pos = {"symbol": symbol}
            self.open.append(pos)
            return pos
        return None

    def reset(self) -> None:
        self.calls.append("reset")
        self.open.clear()
        self.closed.clear()

    def disable(self) -> None:
        self.calls.append("disable")
        self.enabled = False

    def close_legacy_paper_positions(self, *, confirm: bool, reason: str) -> dict[str, Any]:
        self.calls.append("close-legacy")
        closed = len(self.open) if confirm else 0
        if confirm:
            self.open.clear()
        return {"ok": bool(confirm), "closed": closed, "reason": reason}

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "open_count": len(self.open),
            "closed_count": len(self.closed),
        }


@pytest.fixture()
def harness(monkeypatch):
    engine = _LedgerEngine()
    settings = SimpleNamespace(paper_admin_token=None)
    db_clears: list[str] = []
    analyzed: list[str] = []

    def _analyze(symbol: str, **_kw: Any) -> dict[str, Any]:
        analyzed.append(symbol)
        return dict(ENTRY_PAYLOAD)

    async def _clear() -> None:
        db_clears.append("clear")

    async def _flush(_eng: Any) -> None:
        return None

    monkeypatch.setattr(paper_mod, "get_paper_trade_engine", lambda: engine)
    monkeypatch.setattr(paper_mod, "flush_paper_trade_persists", _flush)
    monkeypatch.setattr(persistence, "clear_paper_trades", _clear)
    monkeypatch.setattr(
        setup_mod,
        "get_setup_signal_service",
        lambda: SimpleNamespace(config=SimpleNamespace(), analyze_symbol=_analyze),
    )
    monkeypatch.setattr(routes, "get_orchestrator", lambda: None)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "_enrich_paper_status", lambda status: status)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")

    def _client(peer: Any) -> TestClient:
        if peer == DEFAULT_PEER:
            return TestClient(app)

        async def _with_peer(scope: dict[str, Any], receive: Any, send: Any) -> None:
            # The ASGI server fills scope["client"] from the TCP peer; set it here.
            await app({**scope, "client": peer}, receive, send)

        return TestClient(_with_peer)

    return SimpleNamespace(
        engine=engine,
        settings=settings,
        db_clears=db_clears,
        analyzed=analyzed,
        client=_client,
    )


# ── GET /signals/{symbol} ────────────────────────────────────────────────────


def test_stub_engine_would_open_on_this_payload():
    """Guard: the payload served below is one the engine acts on."""
    engine = _LedgerEngine()
    assert engine.on_setup_signal("BTCUSDT", dict(ENTRY_PAYLOAD)) is not None
    assert len(engine.open) == 2


@pytest.mark.parametrize("peer", [LOOPBACK_V4, REMOTE])
def test_get_signal_returns_analysis_and_opens_nothing(harness, peer):
    before = len(harness.engine.open)

    res = harness.client(peer).get("/api/signals/BTCUSDT")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "LONG_ENTRY_CANDIDATE"
    assert body["entry"] == 100.0
    assert "timestamp" in body
    assert harness.analyzed == ["BTCUSDT"]
    assert harness.engine.setup_calls == []
    assert len(harness.engine.open) == before
    assert harness.engine.calls == []


# ── POST /paper/reset, /paper/disable, /paper/close-legacy ───────────────────


def test_reset_from_non_loopback_without_header_is_403_and_keeps_ledger(harness):
    res = harness.client(REMOTE).post("/api/paper/reset")

    assert res.status_code == 403
    assert harness.engine.calls == []
    assert harness.engine.open == [{"symbol": "ETHUSDT"}]
    assert harness.engine.closed == [{"symbol": "SOLUSDT", "pnl": 1.5}]
    assert harness.db_clears == []


@pytest.mark.parametrize("path", PROTECTED)
@pytest.mark.parametrize("token", [None, "", TOKEN])
def test_protected_routes_403_for_non_loopback_without_header(harness, path, token):
    harness.settings.paper_admin_token = token

    res = harness.client(REMOTE).post(path)

    assert res.status_code == 403
    assert harness.engine.calls == []
    assert harness.engine.enabled is True
    assert len(harness.engine.open) == 1
    assert harness.db_clears == []


@pytest.mark.parametrize("path", PROTECTED)
@pytest.mark.parametrize("sent", ["wrong", TOKEN + "x", TOKEN[:-1], "", "tökén"])
def test_protected_routes_403_for_non_loopback_with_wrong_header(harness, path, sent):
    harness.settings.paper_admin_token = TOKEN

    res = harness.client(REMOTE).post(path, headers={"X-Paper-Admin": sent.encode("utf-8")})

    assert res.status_code == 403
    assert harness.engine.calls == []
    assert harness.db_clears == []


@pytest.mark.parametrize("path", PROTECTED)
@pytest.mark.parametrize("token", [None, ""])
@pytest.mark.parametrize("sent", ["", "anything", "None"])
def test_unset_token_never_matches_any_header(harness, path, token, sent):
    harness.settings.paper_admin_token = token

    res = harness.client(REMOTE).post(path, headers={"X-Paper-Admin": sent})

    assert res.status_code == 403
    assert harness.engine.calls == []


@pytest.mark.parametrize(
    ("path", "call"),
    [
        ("/api/paper/reset", "reset"),
        ("/api/paper/disable", "disable"),
        ("/api/paper/close-legacy?confirm=true", "close-legacy"),
    ],
)
def test_non_loopback_with_correct_header_is_allowed(harness, path, call):
    harness.settings.paper_admin_token = TOKEN

    res = harness.client(REMOTE).post(path, headers={"X-Paper-Admin": TOKEN})

    assert res.status_code == 200
    assert harness.engine.calls == [call]


@pytest.mark.parametrize("peer", [LOOPBACK_V4, ("::1", 50000), ("::ffff:127.0.0.1", 50000)])
@pytest.mark.parametrize(
    ("path", "call"),
    [
        ("/api/paper/reset", "reset"),
        ("/api/paper/disable", "disable"),
        ("/api/paper/close-legacy?confirm=true", "close-legacy"),
    ],
)
def test_loopback_without_token_is_allowed(harness, peer, path, call):
    assert harness.settings.paper_admin_token is None

    res = harness.client(peer).post(path)

    assert res.status_code == 200
    assert harness.engine.calls == [call]


def test_loopback_reset_clears_ledger_and_db(harness):
    res = harness.client(LOOPBACK_V4).post("/api/paper/reset")

    assert res.status_code == 200
    assert res.json() == {"enabled": True, "open_count": 0, "closed_count": 0}
    assert harness.db_clears == ["clear"]


@pytest.mark.parametrize(
    "peer",
    [
        DEFAULT_PEER,  # host "testclient" — not an address
        None,  # server gave no peer at all
        ("localhost", 50000),
        ("", 50000),
        ("127.0.0.2", 50000),
        ("10.0.0.7", 50000),
        ("172.18.0.1", 50000),
        ("::ffff:203.0.113.5", 50000),
        ("::", 50000),
    ],
)
def test_unknown_or_non_loopback_peers_are_refused(harness, peer):
    res = harness.client(peer).post("/api/paper/reset")

    assert res.status_code == 403
    assert harness.engine.calls == []


def test_forwarded_headers_do_not_make_a_remote_peer_loopback(harness):
    res = harness.client(REMOTE).post(
        "/api/paper/reset",
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "Forwarded": "for=127.0.0.1",
            "Host": "127.0.0.1:8000",
        },
    )

    assert res.status_code == 403
    assert harness.engine.calls == []


def test_missing_client_scope_is_refused(harness):
    request = SimpleNamespace(client=None, headers={})

    assert routes._peer_is_loopback(request) is False
    with pytest.raises(routes.HTTPException) as exc:
        routes._require_paper_admin(request)
    assert exc.value.status_code == 403
    assert TOKEN not in str(exc.value.detail)


def test_other_paper_routes_stay_open_to_non_loopback(harness):
    harness.engine.enable = lambda: harness.engine.calls.append("enable")

    res = harness.client(REMOTE).post("/api/paper/enable")

    assert res.status_code == 200
    assert harness.engine.calls == ["enable"]
