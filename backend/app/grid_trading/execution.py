"""Live execution boundary. Submitting an order always fails."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.rate_limiter import RateLimiter
from app.grid_trading.config import GridConfigError

logger = get_logger("grid_trading.execution")


class GridExecution:
    def __init__(self) -> None:
        self.limiter = RateLimiter(
            name="grid_trading",
            capacity=5,
            refill_per_second=1,
            max_concurrency=1,
        )

    async def submit_live(self, order: dict[str, Any]) -> None:
        await self.limiter.acquire()
        logger.warning("live_grid_rejected", symbol=str(order.get("symbol") or ""))
        raise GridConfigError("live_disabled", "live grid execution is disabled")
