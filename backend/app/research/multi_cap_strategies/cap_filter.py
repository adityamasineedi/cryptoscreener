"""Asset-group enforcement via existing classify_asset_group.

Does NOT guess market cap. Unknown / unavailable → CAP_GROUP_UNAVAILABLE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.research.config import classify_asset_group
from app.research.multi_cap_strategies.config import MultiCapResearchConfig
from app.research.multi_cap_strategies.schemas import CapEligibility

# Strategy asset_group → classify_asset_group label
_GROUP_MAP = {
    "LARGE_CAP": "large-cap",
    "MID_CAP": "mid-cap",
    "SMALL_CAP": "small-cap",
}


def resolve_cap_label(
    symbol: str,
    market_cap: float | None,
    *,
    config: MultiCapResearchConfig | None = None,
) -> str:
    rcfg = (config or MultiCapResearchConfig()).to_research_config()
    return classify_asset_group(symbol, market_cap, config=rcfg)


def symbol_eligible_for_strategy(
    symbol: str,
    market_cap: float | None,
    required_group: str,
    *,
    config: MultiCapResearchConfig | None = None,
) -> tuple[bool, str, str]:
    """Return (ok, resolved_group, reason).

    reason is OK | CAP_GROUP_UNAVAILABLE | CAP_GROUP_MISMATCH.
    """
    label = resolve_cap_label(symbol, market_cap, config=config)
    want = _GROUP_MAP.get(required_group.upper(), required_group.lower())
    if market_cap is None or market_cap <= 0:
        # BTC/ETH get special labels even without mcap — still not LARGE/MID/SMALL
        if label in ("BTC", "ETH"):
            return False, label, "CAP_GROUP_MISMATCH"
        return False, "CAP_GROUP_UNAVAILABLE", "CAP_GROUP_UNAVAILABLE"
    if label == "UNKNOWN":
        return False, "CAP_GROUP_UNAVAILABLE", "CAP_GROUP_UNAVAILABLE"
    if label != want:
        return False, label, "CAP_GROUP_MISMATCH"
    return True, label, "OK"


def filter_symbols_for_strategy(
    symbols: Sequence[str],
    market_caps: Mapping[str, float | None],
    required_group: str,
    *,
    config: MultiCapResearchConfig | None = None,
) -> CapEligibility:
    elig = CapEligibility()
    for sym in symbols:
        key = sym.upper()
        mcap = market_caps.get(key)
        if mcap is None:
            # try raw key
            mcap = market_caps.get(sym)
        ok, label, reason = symbol_eligible_for_strategy(
            key, mcap, required_group, config=config
        )
        row = {
            "symbol": key,
            "resolved_group": label,
            "required_group": required_group,
            "reason": reason,
            "market_cap": mcap,
        }
        if ok:
            elig.eligible_symbols.append(key)
        elif reason == "CAP_GROUP_UNAVAILABLE":
            elig.unknown_symbols.append(row)
            elig.excluded_symbols.append(row)
        else:
            elig.excluded_symbols.append(row)
    return elig


def market_cap_from_store(symbol: str) -> float | None:
    """Best-effort mcap from engine store; None if unavailable (no guessing)."""
    try:
        from app.services.engine_store import engine_store

        fv = engine_store.get_fundamental(symbol.upper(), "market_cap")
        if fv is None:
            return None
        val = getattr(fv, "value", None)
        if val is None and isinstance(fv, dict):
            val = fv.get("value")
        if val is None:
            return None
        f = float(val)
        return f if f > 0 else None
    except Exception:  # noqa: BLE001
        return None
