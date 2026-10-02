from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.models.schemas import DataStatus, FreshValue


class LiquidationProvider(ABC):
    """Abstraction for legitimate liquidation data sources. Never synthetic."""

    name: str = "unknown"

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def get_summary(self, symbol: str) -> FreshValue[str]: ...

    @abstractmethod
    def aggregates(self, symbol: str) -> dict[str, dict[str, float]]: ...

    @abstractmethod
    def status(self) -> dict[str, Any]: ...

    @abstractmethod
    def liquidation_status(self) -> DataStatus: ...


class BinanceForceOrderProvider(LiquidationProvider):
    """Wraps LiquidationIngestion (!forceOrder@arr on /market/ws)."""

    name = "binance_force_order"

    def __init__(self, ingestion) -> None:
        self._ingestion = ingestion

    async def start(self) -> None:
        await self._ingestion.start()

    async def stop(self) -> None:
        await self._ingestion.stop()

    def get_summary(self, symbol: str) -> FreshValue[str]:
        return self._ingestion.get_summary(symbol)

    def aggregates(self, symbol: str) -> dict[str, dict[str, float]]:
        return self._ingestion.aggregates(symbol)

    def liquidation_status(self) -> DataStatus:
        if hasattr(self._ingestion, "compute_status"):
            status = self._ingestion.compute_status()
            try:
                return DataStatus(status)
            except ValueError:
                return DataStatus.WAITING
        base = self._ingestion.status()
        events = int(base.get("total_events") or 0)
        if events > 0:
            return DataStatus.LIVE
        return DataStatus.WAITING

    def status(self) -> dict[str, Any]:
        base = dict(self._ingestion.status())
        status = self.liquidation_status()
        base["provider"] = self.name
        base["liquidation_status"] = status.value
        if status == DataStatus.WAITING:
            base["note"] = (
                "WAITING until real !forceOrder events arrive — never fabricated."
            )
        elif status == DataStatus.LIVE:
            base["note"] = "Receiving real forceOrder events."
        elif status == DataStatus.STALE:
            base["note"] = "Prior real events exist but outside freshness window."
        else:
            base["note"] = "Liquidation provider/connection unavailable."
        base["checked_at"] = datetime.now(timezone.utc).isoformat()
        return base

    def diagnostic(self) -> dict[str, Any]:
        if hasattr(self._ingestion, "diagnostic"):
            return self._ingestion.diagnostic()
        return self.status()
