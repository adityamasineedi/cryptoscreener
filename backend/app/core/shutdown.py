"""Bound shutdown waits so a stuck socket cannot wedge the API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable

from app.core.logging import get_logger

logger = get_logger("shutdown")

# Websocket close during reload must not wait on a peer that never answers.
WS_CLOSE_TIMEOUT_S = 2.0


async def cancel_tasks(
    tasks: Iterable[asyncio.Task | None],
    *,
    timeout: float = 8.0,
    label: str = "shutdown",
) -> None:
    live = [t for t in tasks if t is not None and not t.done()]
    for task in live:
        task.cancel()
    if not live:
        return
    _done, pending = await asyncio.wait(live, timeout=timeout)
    if pending:
        logger.warning(
            "shutdown_tasks_pending",
            label=label,
            timeout=timeout,
            tasks=[t.get_name() for t in pending],
        )


async def finish_within(
    awaitable: Awaitable[object],
    *,
    timeout: float = 8.0,
    label: str = "shutdown",
) -> None:
    try:
        await asyncio.wait_for(awaitable, timeout=timeout)
    except TimeoutError:
        logger.warning("shutdown_step_timeout", label=label, timeout=timeout)
    except asyncio.CancelledError:
        raise
