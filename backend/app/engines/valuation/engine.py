"""Deterministic valuation / derived fundamental ratios. Never invent inputs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.schemas import DataStatus, FreshValue

METHODS = {
    "volume_mcap": "quote_volume_24h / market_cap",
    "mcap_fdv": "market_cap / fdv",
    "circ_max_ratio": "circulating_supply / max_supply",
    "circ_total_ratio": "circulating_supply / total_supply",
    "nvt_proxy": "market_cap / quote_volume_24h (EXPERIMENTAL proxy; not on-chain NVT)",
    "velocity_proxy": "quote_volume_24h / market_cap (EXPERIMENTAL; not on-chain velocity)",
    "nvt": "market_cap / on_chain_transaction_volume (requires on-chain provider)",
    "velocity": "on_chain_transaction_volume / market_cap (requires on-chain provider)",
    "fdv_from_max_supply": "max_supply × price (provider FDV missing or ≈ market_cap despite supply gap)",
}

_STATUS_RANK = {
    DataStatus.UNAVAILABLE: 0,
    DataStatus.STALE: 1,
    DataStatus.WAITING: 2,
    DataStatus.CACHED: 3,
    DataStatus.HISTORICAL: 4,
    DataStatus.LIVE: 5,
}


def _worst_status(*fvs: FreshValue | None) -> DataStatus:
    worst = DataStatus.LIVE
    worst_rank = _STATUS_RANK[DataStatus.LIVE]
    for fv in fvs:
        if fv is None or fv.value is None:
            continue
        rank = _STATUS_RANK.get(fv.status, 2)
        if rank < worst_rank:
            worst = fv.status
            worst_rank = rank
    return worst


def _num(fv: FreshValue | None | float) -> float | None:
    if fv is None:
        return None
    if isinstance(fv, (int, float)):
        return float(fv)
    if fv.value is None:
        return None
    try:
        return float(fv.value)
    except (TypeError, ValueError):
        return None


def _ts(*fvs: FreshValue | None) -> datetime | None:
    times = [f.timestamp for f in fvs if f is not None and f.timestamp is not None]
    return max(times) if times else datetime.now(timezone.utc)


def _ratio(
    num: float | None,
    den: float | None,
    *,
    source: str,
    methodology: str,
    ts: datetime | None,
    status: DataStatus = DataStatus.LIVE,
) -> FreshValue[float]:
    if num is None or den is None:
        return FreshValue.waiting(source, methodology=methodology)
    if den == 0:
        return FreshValue.unavailable(source, methodology=methodology)
    return FreshValue(
        value=num / den,
        timestamp=ts or datetime.now(timezone.utc),
        source=source,
        status=status,
        methodology=methodology,
    )


def resolve_fdv(
    *,
    fdv: FreshValue | None,
    max_supply: FreshValue | None,
    price: FreshValue | None,
    market_cap: FreshValue | None,
    circulating_supply: FreshValue | None,
) -> FreshValue | None:
    """Prefer max_supply×price when provider FDV is missing or equals mcap despite unlock gap."""
    mx = _num(max_supply)
    px = _num(price)
    mc = _num(market_cap)
    fd = _num(fdv)
    circ = _num(circulating_supply)
    if mx is None or px is None or mx <= 0 or px <= 0:
        return fdv
    computed = mx * px
    supply_gap = circ is not None and mx > circ * 1.001
    fdv_eq_mcap = (
        fd is not None and mc is not None and mc > 0 and abs(fd - mc) / mc < 0.005
    )
    if fd is None or (supply_gap and fdv_eq_mcap):
        st = _worst_status(
            max_supply if isinstance(max_supply, FreshValue) else None,
            price if isinstance(price, FreshValue) else None,
        )
        return FreshValue(
            value=computed,
            timestamp=_ts(
                max_supply if isinstance(max_supply, FreshValue) else None,
                price if isinstance(price, FreshValue) else None,
            ),
            source="calc",
            status=st if st != DataStatus.WAITING else DataStatus.LIVE,
            methodology=METHODS["fdv_from_max_supply"],
        )
    return fdv


class ValuationEngine:
    """Compute valuation metrics only when real inputs exist."""

    SOURCE = "valuation_engine"

    def compute(
        self,
        *,
        market_cap: FreshValue | None = None,
        fdv: FreshValue | None = None,
        quote_volume_24h: FreshValue | None = None,
        circulating_supply: FreshValue | None = None,
        total_supply: FreshValue | None = None,
        max_supply: FreshValue | None = None,
        on_chain_tx_volume: FreshValue | None = None,
        price: FreshValue | None = None,
    ) -> dict[str, FreshValue[Any]]:
        fdv_resolved = resolve_fdv(
            fdv=fdv,
            max_supply=max_supply,
            price=price,
            market_cap=market_cap,
            circulating_supply=circulating_supply,
        )
        mc = _num(market_cap)
        fd = _num(fdv_resolved)
        qv = _num(quote_volume_24h)
        circ = _num(circulating_supply)
        total = _num(total_supply)
        mx = _num(max_supply)
        octx = _num(on_chain_tx_volume)
        ts = _ts(
            market_cap if isinstance(market_cap, FreshValue) else None,
            fdv_resolved if isinstance(fdv_resolved, FreshValue) else None,
            quote_volume_24h if isinstance(quote_volume_24h, FreshValue) else None,
            price if isinstance(price, FreshValue) else None,
        )
        vol_status = _worst_status(
            quote_volume_24h if isinstance(quote_volume_24h, FreshValue) else None,
            market_cap if isinstance(market_cap, FreshValue) else None,
        )
        fdv_status = _worst_status(
            market_cap if isinstance(market_cap, FreshValue) else None,
            fdv_resolved if isinstance(fdv_resolved, FreshValue) else None,
        )

        out: dict[str, FreshValue[Any]] = {
            "fdv": fdv_resolved
            if isinstance(fdv_resolved, FreshValue)
            else FreshValue.waiting(self.SOURCE),
            "volume_mcap": _ratio(
                qv,
                mc,
                source=self.SOURCE,
                methodology=METHODS["volume_mcap"],
                ts=ts,
                status=vol_status,
            ),
            "mcap_fdv": _ratio(
                mc,
                fd,
                source=self.SOURCE,
                methodology=METHODS["mcap_fdv"],
                ts=ts,
                status=fdv_status,
            ),
            "circ_max_ratio": _ratio(
                circ, mx, source=self.SOURCE, methodology=METHODS["circ_max_ratio"], ts=ts
            ),
            "circ_total_ratio": _ratio(
                circ, total, source=self.SOURCE, methodology=METHODS["circ_total_ratio"], ts=ts
            ),
        }

        # True NVT/velocity require on-chain tx volume — never silently substitute exchange volume
        if octx is not None:
            out["nvt"] = _ratio(
                mc, octx, source=self.SOURCE, methodology=METHODS["nvt"], ts=ts
            )
            out["velocity"] = _ratio(
                octx, mc, source=self.SOURCE, methodology=METHODS["velocity"], ts=ts
            )
        else:
            out["nvt"] = FreshValue.waiting(self.SOURCE, methodology=METHODS["nvt"])
            out["velocity"] = FreshValue.waiting(
                self.SOURCE, methodology=METHODS["velocity"]
            )
            # Explicit experimental proxies — labeled so UI never confuses them with true NVT
            out["nvt_proxy"] = _ratio(
                mc,
                qv,
                source=self.SOURCE,
                methodology=METHODS["nvt_proxy"],
                ts=ts,
            )
            out["velocity_proxy"] = _ratio(
                qv,
                mc,
                source=self.SOURCE,
                methodology=METHODS["velocity_proxy"],
                ts=ts,
            )

        return out
