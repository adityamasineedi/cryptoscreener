from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.schemas import DataStatus, FreshValue
from app.services.metric_dependencies import (
    SCREENER_TF,
    annotate_indicator_waiting,
    annotate_structure_waiting,
    annotate_volume_waiting,
    annotate_zone_waiting,
    with_requires,
)


class EngineStore:
    """In-memory results from calculation engines keyed by symbol."""

    def __init__(self) -> None:
        self.indicators: dict[str, dict[str, dict[str, FreshValue]]] = {}
        # symbol -> timeframe -> indicator -> FreshValue
        self.structure: dict[str, dict[str, Any]] = {}
        self.zones: dict[str, list[dict[str, Any]]] = {}
        self.volume: dict[str, dict[str, Any]] = {}
        self.fundamentals: dict[str, dict[str, FreshValue]] = {}
        self.signals: dict[str, dict[str, Any]] = {}
        self.setup_signals: dict[str, dict[str, Any]] = {}
        self._dirty: set[str] = set()

    def mark_dirty(self, symbol: str) -> None:
        self._dirty.add(symbol.upper())

    def pop_dirty(self) -> list[str]:
        out = sorted(self._dirty)
        self._dirty.clear()
        return out

    def set_indicators(
        self, symbol: str, timeframe: str, values: dict[str, FreshValue]
    ) -> None:
        sym = symbol.upper()
        self.indicators.setdefault(sym, {})[timeframe] = values
        self.mark_dirty(sym)

    def set_structure(self, symbol: str, timeframe: str, payload: dict[str, Any]) -> None:
        sym = symbol.upper()
        self.structure.setdefault(sym, {})[timeframe] = payload
        self.mark_dirty(sym)

    def set_zones(self, symbol: str, zones: list[dict[str, Any]]) -> None:
        sym = symbol.upper()
        self.zones[sym] = zones
        self.mark_dirty(sym)

    def set_volume(self, symbol: str, timeframe: str, payload: dict[str, Any]) -> None:
        sym = symbol.upper()
        self.volume.setdefault(sym, {})[timeframe] = payload
        self.mark_dirty(sym)

    def set_fundamentals(self, symbol: str, values: dict[str, FreshValue]) -> None:
        sym = symbol.upper()
        self.fundamentals[sym] = values
        self.mark_dirty(sym)

    def set_signals(self, symbol: str, payload: dict[str, Any]) -> None:
        sym = symbol.upper()
        self.signals[sym] = payload
        self.mark_dirty(sym)

    def set_setup_signal(self, symbol: str, payload: dict[str, Any]) -> None:
        sym = symbol.upper()
        self.setup_signals[sym] = payload
        # Merge into signals for coin detail consumers
        existing = dict(self.signals.get(sym) or {})
        existing["setup"] = payload
        self.signals[sym] = existing
        self.mark_dirty(sym)

    def get_setup_signal(self, symbol: str) -> dict[str, Any] | None:
        return self.setup_signals.get(symbol.upper())

    def get_rvol(self, symbol: str, timeframe: str = "15m") -> FreshValue[float]:
        vol = self.volume.get(symbol.upper(), {}).get(timeframe) or {}
        fresh = vol.get("fresh") or {}
        fv = fresh.get("relative_volume") or fresh.get("rvol")
        if isinstance(fv, FreshValue):
            if fv.value is None and fv.status == DataStatus.WAITING:
                return with_requires(fv, f"{timeframe.upper()} OHLCV", detail="RVOL")
            return fv
        raw = (vol.get("raw") or {}).get("relative_volume")
        if raw is None:
            raw = (vol.get("raw") or {}).get("rvol")
        if raw is None:
            return annotate_volume_waiting(symbol, timeframe)
        return FreshValue(
            value=float(raw),
            timestamp=datetime.now(timezone.utc),
            source="volume_engine",
            status=DataStatus.LIVE,
        )

    def get_volatility(self, symbol: str, timeframe: str = "15m") -> FreshValue[float]:
        ind = self.indicators.get(symbol.upper(), {}).get(timeframe) or {}
        fv = ind.get("volatility")
        if isinstance(fv, FreshValue):
            if fv.value is None and fv.status == DataStatus.WAITING:
                return with_requires(fv, f"{timeframe.upper()} OHLCV", detail="volatility")
            return fv
        return annotate_indicator_waiting(
            symbol, "mtf_engine", timeframe=timeframe, name="volatility"
        )

    def get_structure_label(self, symbol: str, timeframe: str = "15m") -> FreshValue[str]:
        payload = self.structure.get(symbol.upper(), {}).get(timeframe)
        if not payload:
            return annotate_structure_waiting(symbol, timeframe)
        trend = payload.get("trend")
        if trend is None:
            return annotate_structure_waiting(symbol, timeframe)
        return FreshValue(
            value=str(trend),
            timestamp=datetime.now(timezone.utc),
            source="market_structure",
            status=DataStatus.LIVE,
        )

    def get_bos_choch(self, symbol: str, timeframe: str = "15m") -> tuple[FreshValue, FreshValue]:
        payload = self.structure.get(symbol.upper(), {}).get(timeframe) or {}
        if not payload:
            waiting = annotate_structure_waiting(symbol, timeframe)
            return waiting, waiting
        events = payload.get("events") or []
        bos = None
        choch = None
        for ev in reversed(events):
            et = str(ev.get("event_type", ""))
            et_up = et.upper()
            if bos is None and et_up.startswith("BOS"):
                bos = et_up
            if choch is None and et_up.startswith("CHOCH"):
                choch = et_up
            if bos and choch:
                break
        ts = datetime.now(timezone.utc)
        bos_fv = (
            FreshValue(value=bos, timestamp=ts, source="market_structure", status=DataStatus.LIVE)
            if bos
            else FreshValue.waiting(
                "market_structure",
                methodology="Structure computed — no BOS event yet",
            )
        )
        choch_fv = (
            FreshValue(value=choch, timestamp=ts, source="market_structure", status=DataStatus.LIVE)
            if choch
            else FreshValue.waiting(
                "market_structure",
                methodology="Structure computed — no CHOCH event yet",
            )
        )
        return bos_fv, choch_fv

    def nearest_zones(self, symbol: str, price: float | None) -> tuple[FreshValue, FreshValue]:
        zones = self.zones.get(symbol.upper()) or []
        if not zones or price is None:
            waiting = annotate_zone_waiting(symbol, SCREENER_TF)
            return waiting, waiting
        supply = [
            z
            for z in zones
            if "SUPPLY" in str(z.get("zone_type", "")).upper()
        ]
        demand = [
            z
            for z in zones
            if "DEMAND" in str(z.get("zone_type", "")).upper()
        ]
        ts = datetime.now(timezone.utc)

        def nearest(cands: list[dict[str, Any]]) -> FreshValue[str]:
            if not cands:
                return FreshValue.waiting(
                    "supply_demand",
                    methodology="Zones computed — no matching supply/demand candidate",
                )
            best = min(
                cands,
                key=lambda z: abs(((float(z.get("high", 0)) + float(z.get("low", 0))) / 2) - price),
            )
            mid = (float(best.get("high", 0)) + float(best.get("low", 0))) / 2
            label = f"{best.get('zone_type')}:{mid:.6g}:{best.get('status')}"
            return FreshValue(
                value=label,
                timestamp=ts,
                source="supply_demand",
                status=DataStatus.LIVE,
            )

        return nearest(supply), nearest(demand)

    def get_fundamental(self, symbol: str, field: str) -> FreshValue:
        vals = self.fundamentals.get(symbol.upper()) or {}
        fv = vals.get(field)
        if isinstance(fv, FreshValue):
            return fv
        return FreshValue.waiting(field)


engine_store = EngineStore()
