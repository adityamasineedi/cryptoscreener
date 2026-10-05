"""Shared active screen/compute universe — V1 paper + top volume.

Screener discovery can still load all Binance USDT perps into market_store.
UI screens, coverage %, and REST backfill/compute use this capped set so
health/nav/screener stay in sync. Does not alter strategy entry/exit logic.
"""

from __future__ import annotations

from typing import Sequence

from app.config import get_settings
from app.engines.orchestrator import get_orchestrator
from app.services.market_store import market_store


def list_discovered_symbols(*, market_type: str = "futures_perp") -> list[str]:
    return [s.symbol for s in market_store.list_symbols(market_type=market_type)]


def _paper_must_symbols() -> set[str]:
    try:
        from app.research.v1_production import V1_SYMBOLS

        return {s.upper() for s in V1_SYMBOLS}
    except Exception:  # noqa: BLE001
        return {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


def _active_cap() -> int:
    settings = get_settings()
    cfg = (settings.market_config or {}).get("backfill") or {}
    try:
        return max(
            1,
            int(cfg.get("active_universe_count") or cfg.get("tier2_count") or 80),
        )
    except (TypeError, ValueError):
        return 80


def _fallback_select(candidates: Sequence[str]) -> list[str]:
    """Used before orchestrator/backfill is up — same shape as backfill selector."""
    cand = [s.upper() for s in candidates if s]
    cand_set = set(cand)
    must = sorted(s for s in _paper_must_symbols() if s in cand_set)
    ranked = sorted(
        [s for s in cand if s not in set(must)],
        key=lambda s: float(
            getattr(market_store.tickers.get(s), "quote_volume_24h", 0) or 0
        ),
        reverse=True,
    )
    cap = _active_cap()
    if len(must) >= cap:
        return list(dict.fromkeys(must))
    return list(dict.fromkeys([*must, *ranked]))[:cap]


def list_active_symbols(
    candidates: Sequence[str] | None = None,
    *,
    market_type: str = "futures_perp",
) -> list[str]:
    """Active universe for screener / coverage / health (not full discovery)."""
    discovered = (
        [s.upper() for s in candidates if s]
        if candidates is not None
        else list_discovered_symbols(market_type=market_type)
    )
    orch = get_orchestrator()
    backfill = getattr(orch, "backfill", None) if orch is not None else None
    if backfill is not None and hasattr(backfill, "select_active_universe"):
        return backfill.select_active_universe(discovered)
    return _fallback_select(discovered)


def active_universe_meta(
    candidates: Sequence[str] | None = None,
    *,
    market_type: str = "futures_perp",
) -> dict[str, int]:
    discovered = (
        [s.upper() for s in candidates if s]
        if candidates is not None
        else list_discovered_symbols(market_type=market_type)
    )
    active = list_active_symbols(discovered, market_type=market_type)
    return {
        "discovered_universe": len(discovered),
        "active_universe": len(active),
        "active_universe_cap": _active_cap(),
    }
