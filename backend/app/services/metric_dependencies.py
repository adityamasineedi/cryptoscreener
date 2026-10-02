"""Metric dependency chains for truthful WAITING explanations.

OHLCV (screener primary TF = 15m)
 ├── Structure / BOS / CHOCH
 ├── Supply/Demand zones
 ├── RVOL / volatility / Williams / RSI
 ├── Technical Rating (aggregates components)
 └── Entry/Exit

OI
 └── OI component of Technical Rating / Entry-Exit

Liquidation stream
 └── Liquidation component of Technical Rating

Never invent values when a dependency is missing — only explain the block.
"""

from __future__ import annotations

from typing import Any

from app.models.schemas import DataStatus, FreshValue
from app.services.ohlcv_store import ohlcv_store

# Screener engines read 15m-keyed store results (see ScreenerService.build_row).
SCREENER_TF = "15m"
MIN_STRUCTURE_BARS = 5
MIN_VOLUME_BARS = 5
MIN_ZONE_BARS = 20


def candle_count(symbol: str, timeframe: str) -> int:
    return len(ohlcv_store.get_closed(symbol, timeframe) or [])


def has_ohlcv(symbol: str, timeframe: str, *, min_bars: int = 1) -> bool:
    return candle_count(symbol, timeframe) >= min_bars


def blocking_ohlcv(
    symbol: str,
    timeframe: str = SCREENER_TF,
    *,
    min_bars: int = 1,
) -> str | None:
    """Return human dependency label if candles are insufficient, else None."""
    if has_ohlcv(symbol, timeframe, min_bars=min_bars):
        return None
    return f"{timeframe.upper()} OHLCV"


def with_requires(
    fv: FreshValue,
    requires: str,
    *,
    detail: str | None = None,
) -> FreshValue:
    """Attach a compact dependency explanation without changing status/value."""
    if fv.status != DataStatus.WAITING or fv.value is not None:
        return fv
    method = f"Requires {requires}"
    if detail:
        method = f"{method} — {detail}"
    return FreshValue(
        value=None,
        timestamp=fv.timestamp,
        source=fv.source,
        status=DataStatus.WAITING,
        methodology=method,
    )


def annotate_structure_waiting(symbol: str, timeframe: str = SCREENER_TF) -> FreshValue[str]:
    block = blocking_ohlcv(symbol, timeframe, min_bars=MIN_STRUCTURE_BARS)
    if block:
        return FreshValue.waiting(
            "market_structure",
            methodology=f"Requires {block}",
        )
    return FreshValue.waiting(
        "market_structure",
        methodology=f"Requires {timeframe.upper()} OHLCV compute — structure not ready",
    )


def annotate_volume_waiting(symbol: str, timeframe: str = SCREENER_TF) -> FreshValue[float]:
    block = blocking_ohlcv(symbol, timeframe, min_bars=MIN_VOLUME_BARS)
    if block:
        return FreshValue.waiting("volume_engine", methodology=f"Requires {block}")
    return FreshValue.waiting(
        "volume_engine",
        methodology=f"Requires {timeframe.upper()} OHLCV — RVOL not computed",
    )


def annotate_zone_waiting(symbol: str, timeframe: str = SCREENER_TF) -> FreshValue[str]:
    block = blocking_ohlcv(symbol, timeframe, min_bars=MIN_ZONE_BARS)
    if block:
        return FreshValue.waiting("supply_demand", methodology=f"Requires {block}")
    return FreshValue.waiting(
        "supply_demand",
        methodology=f"Requires {timeframe.upper()} OHLCV — zones not detected",
    )


def annotate_indicator_waiting(
    symbol: str,
    source: str,
    *,
    timeframe: str = SCREENER_TF,
    name: str = "indicator",
) -> FreshValue:
    block = blocking_ohlcv(symbol, timeframe, min_bars=MIN_VOLUME_BARS)
    if block:
        return FreshValue.waiting(source, methodology=f"Requires {block}")
    return FreshValue.waiting(
        source,
        methodology=f"Requires {timeframe.upper()} OHLCV — {name} not computed",
    )


def tech_rating_requires(missing: list[str]) -> str:
    """Compact dependency string for INSUFFICIENT_DATA / WAITING rating."""
    pretty = {
        "structure": "Structure",
        "volume": "Volume",
        "momentum": "Momentum",
        "supply_demand": "Zones",
        "open_interest": "OI",
        "liquidation": "Liquidations",
    }
    parts = [pretty.get(m, m) for m in missing]
    if not parts:
        return "Requires Structure + Volume + Momentum"
    return "Requires " + " + ".join(parts)


def build_metric_dependency_diagnostics(
    symbols: list[str],
    *,
    orch: Any | None,
) -> list[dict[str, Any]]:
    """Coverage payload: per-metric dependency blocks (honest counts only)."""
    total = len(symbols) or 1
    tf = SCREENER_TF

    ohlcv_missing = sum(1 for s in symbols if not has_ohlcv(s, tf, min_bars=1))
    ohlcv_ready = total - ohlcv_missing

    from app.services.engine_store import engine_store

    struct_ready = 0
    struct_blocked_ohlcv = 0
    rvol_ready = 0
    rvol_blocked_ohlcv = 0
    zone_ready = 0
    zone_blocked_ohlcv = 0
    for s in symbols:
        has_bars = has_ohlcv(s, tf, min_bars=MIN_STRUCTURE_BARS)
        payload = (engine_store.structure.get(s.upper()) or {}).get(tf)
        if payload and payload.get("trend") is not None:
            struct_ready += 1
        elif not has_bars:
            struct_blocked_ohlcv += 1

        vol = (engine_store.volume.get(s.upper()) or {}).get(tf) or {}
        fresh = vol.get("fresh") or {}
        rvol = fresh.get("relative_volume") or fresh.get("rvol")
        raw = (vol.get("raw") or {}).get("relative_volume") or (vol.get("raw") or {}).get("rvol")
        if (isinstance(rvol, FreshValue) and rvol.value is not None) or raw is not None:
            rvol_ready += 1
        elif not has_ohlcv(s, tf, min_bars=MIN_VOLUME_BARS):
            rvol_blocked_ohlcv += 1

        zones = engine_store.zones.get(s.upper()) or []
        if zones:
            zone_ready += 1
        elif not has_ohlcv(s, tf, min_bars=MIN_ZONE_BARS):
            zone_blocked_ohlcv += 1

    oi_waiting = 0
    oi_live = 0
    if orch is not None:
        for s in symbols:
            st = orch.oi.get_state(s)
            if st is None or st.open_interest.value is None:
                oi_waiting += 1
            else:
                oi_live += 1
    else:
        oi_waiting = total

    liq_live = 0
    liq_waiting = total
    if orch is not None:
        # Liquidations are stream-wide; per-symbol summaries may still be WAITING
        liq_waiting = 0
        for s in symbols:
            summary = orch.liquidations.get_summary(s)
            if summary and summary.status == DataStatus.LIVE and summary.value is not None:
                liq_live += 1
            else:
                liq_waiting += 1

    def row(
        metric: str,
        coverage_pct: float,
        status: str,
        blocking: str | None,
        missing: int,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        out = {
            "metric": metric,
            "coverage": f"{coverage_pct:.2f}%",
            "status": status,
            "blocking_dependency": blocking,
            "missing_count": missing,
        }
        if extra:
            out.update(extra)
        return out

    diagnostics = [
        row(
            "ohlcv_15m",
            100.0 * ohlcv_ready / total,
            "READY" if ohlcv_missing == 0 else "WAITING",
            None if ohlcv_missing == 0 else "Binance klines backfill",
            ohlcv_missing,
            {"note": "Primary TF for screener Structure/RVOL/Zones/Rating"},
        ),
        row(
            "structure",
            100.0 * struct_ready / total,
            "READY" if struct_ready == total else "WAITING",
            "15m OHLCV" if struct_blocked_ohlcv else ("structure engine" if struct_ready < total else None),
            total - struct_ready,
            {"blocked_by_ohlcv": struct_blocked_ohlcv},
        ),
        row(
            "rvol",
            100.0 * rvol_ready / total,
            "READY" if rvol_ready == total else "WAITING",
            "15m OHLCV" if rvol_blocked_ohlcv else ("volume engine" if rvol_ready < total else None),
            total - rvol_ready,
            {"blocked_by_ohlcv": rvol_blocked_ohlcv},
        ),
        row(
            "zones",
            100.0 * zone_ready / total,
            "READY" if zone_ready == total else "WAITING",
            "15m OHLCV" if zone_blocked_ohlcv else ("supply/demand engine" if zone_ready < total else None),
            total - zone_ready,
            {"blocked_by_ohlcv": zone_blocked_ohlcv},
        ),
        row(
            "open_interest",
            100.0 * oi_live / total,
            "READY" if oi_waiting == 0 else "WAITING",
            "OI REST scheduler" if oi_waiting else None,
            oi_waiting,
        ),
        row(
            "liquidations",
            100.0 * liq_live / total,
            "READY" if liq_waiting == 0 else "WAITING",
            "forceOrder stream events" if liq_waiting else None,
            liq_waiting,
            {
                "note": (
                    "Liquidation metrics stay WAITING until real force-order events "
                    "arrive — never zero-filled"
                )
            },
        ),
        row(
            "tech_rating",
            # Approximate: ready when structure+rvol exist (core deps)
            100.0 * min(struct_ready, rvol_ready) / total,
            "WAITING",
            "Structure + Volume (+ Momentum/Zones/OI when present)",
            total - min(struct_ready, rvol_ready),
            {
                "note": (
                    "INSUFFICIENT_DATA when too many components missing; "
                    "computes immediately when available deps exist"
                )
            },
        ),
        row(
            "entry_exit",
            100.0 * min(struct_ready, rvol_ready) / total,
            "WAITING",
            "Structure + Volume + Zones",
            total - min(struct_ready, rvol_ready),
        ),
    ]
    return diagnostics
