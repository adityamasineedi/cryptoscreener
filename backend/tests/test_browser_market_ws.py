"""Browser /ws/market forwarding: one subscriber, concurrent sends."""

from __future__ import annotations

import asyncio

from starlette.websockets import WebSocketDisconnect

from app.api import routes
from app.services.market_store import market_store


class _BrowserSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self._inbound: asyncio.Queue[str | None] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)

    async def receive_text(self) -> str:
        item = await self._inbound.get()
        if item is None:
            raise WebSocketDisconnect()
        return item


def _forwarder_count() -> int:
    return sum(1 for sub in market_store._subscribers if sub is routes._forward_market_events)


async def test_two_market_sockets_do_not_duplicate_broadcasts():
    market_store.unsubscribe(routes._forward_market_events)
    assert _forwarder_count() == 0

    first = _BrowserSocket()
    second = _BrowserSocket()
    tasks = [
        asyncio.create_task(routes.ws_market(first)),  # type: ignore[arg-type]
        asyncio.create_task(routes.ws_market(second)),  # type: ignore[arg-type]
    ]
    try:
        for _ in range(50):
            if first.accepted and second.accepted and first.sent and second.sent:
                break
            await asyncio.sleep(0.01)
        assert first.accepted and second.accepted
        assert _forwarder_count() == 1
        assert first in routes.ws_connections.active
        assert second in routes.ws_connections.active

        await routes._forward_market_events(
            {"type": "market_batch", "updates": [{"symbol": "BTCUSDT"}]}
        )

        def batches(sock: _BrowserSocket) -> list[dict]:
            return [msg for msg in sock.sent if msg.get("type") == "market_batch"]

        assert len(batches(first)) == 1
        assert len(batches(second)) == 1

        await first._inbound.put(None)
        await second._inbound.put(None)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert first not in routes.ws_connections.active
        assert second not in routes.ws_connections.active
        assert _forwarder_count() == 1
    finally:
        for sock in (first, second):
            if sock in routes.ws_connections.active:
                await routes.ws_connections.disconnect(sock)  # type: ignore[arg-type]
            if not sock._inbound.empty():
                continue
            await sock._inbound.put(None)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        market_store.unsubscribe(routes._forward_market_events)


async def test_failed_client_does_not_block_other_broadcasts():
    manager = routes.ConnectionManager()
    release = asyncio.Event()
    started = asyncio.Event()
    order: list[str] = []

    class Slow:
        async def send_json(self, _message: dict) -> None:
            order.append("slow-start")
            started.set()
            await release.wait()
            order.append("slow-end")

    class Bad:
        async def send_json(self, _message: dict) -> None:
            order.append("bad")
            raise RuntimeError("send failed")

    class Good:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            order.append("good")
            self.messages.append(message)

    slow, bad, good = Slow(), Bad(), Good()
    manager.active.add(slow)  # type: ignore[arg-type]
    manager.active.add(bad)  # type: ignore[arg-type]
    manager.active.add(good)  # type: ignore[arg-type]

    broadcast = asyncio.create_task(manager.broadcast({"type": "market_batch"}))
    await asyncio.wait_for(started.wait(), timeout=1)
    for _ in range(50):
        if "good" in order and "bad" in order:
            break
        await asyncio.sleep(0.01)

    assert "slow-end" not in order
    assert "good" in order
    assert "bad" in order
    assert good.messages == [{"type": "market_batch"}]
    release.set()
    await asyncio.wait_for(broadcast, timeout=1)
    assert bad not in manager.active
    assert good in manager.active
    assert slow in manager.active
