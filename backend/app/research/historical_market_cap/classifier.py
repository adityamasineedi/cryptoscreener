"""Time-aware market-cap classification (research only).

Uses the same numeric thresholds as ResearchConfig / classify_asset_group.
Does NOT invent values. Missing as-of observation → CAP_GROUP_UNAVAILABLE.

BTC/ETH special labels from classify_asset_group are preserved for taxonomy
compatibility; they do NOT map to LARGE_CAP/MID_CAP/SMALL_CAP strategy groups.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.research.config import ResearchConfig, classify_asset_group
from app.research.historical_market_cap.schemas import CapLookupResult
from app.research.multi_cap_strategies.config import MultiCapResearchConfig

CLASSIFICATION_RULE_VERSION = "research_hist_mcap_v1"

# Strategy groups used by multi_cap strategies
STRATEGY_GROUPS = frozenset({"LARGE_CAP", "MID_CAP", "SMALL_CAP"})


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def classify_strategy_cap_group(
    symbol: str,
    market_cap: float | None,
    *,
    config: ResearchConfig | MultiCapResearchConfig | None = None,
) -> str:
    """Map to strategy bucket: LARGE_CAP|MID_CAP|SMALL_CAP|UNAVAILABLE.

    Reuses classify_asset_group thresholds/labels. BTC/ETH/UNKNOWN → UNAVAILABLE
    for strategy purposes (strategies require LARGE/MID/SMALL only).
    """
    if isinstance(config, MultiCapResearchConfig):
        rcfg = config.to_research_config()
    else:
        rcfg = config or ResearchConfig()
    if market_cap is None or market_cap <= 0:
        return "UNAVAILABLE"
    label = classify_asset_group(symbol, market_cap, config=rcfg)
    if label == "large-cap":
        return "LARGE_CAP"
    if label == "mid-cap":
        return "MID_CAP"
    if label == "small-cap":
        return "SMALL_CAP"
    # BTC, ETH, UNKNOWN — not a multi-cap strategy group
    return "UNAVAILABLE"


def taxonomy_label(
    symbol: str,
    market_cap: float | None,
    *,
    config: ResearchConfig | MultiCapResearchConfig | None = None,
) -> str:
    if isinstance(config, MultiCapResearchConfig):
        rcfg = config.to_research_config()
    else:
        rcfg = config or ResearchConfig()
    return classify_asset_group(symbol, market_cap, config=rcfg)


def cap_group_at(
    symbol: str,
    as_of: datetime,
    observations: Sequence[Mapping[str, Any]] | None = None,
    *,
    config: ResearchConfig | MultiCapResearchConfig | None = None,
) -> CapLookupResult:
    """Latest observation with effective_time <= as_of.

    Never uses observations after as_of. Empty/missing → CAP_GROUP_UNAVAILABLE.
    """
    as_of_u = _ensure_utc(as_of)
    sym = symbol.upper()
    best: Mapping[str, Any] | None = None
    best_t: datetime | None = None
    for row in observations or []:
        et = row.get("effective_time")
        if et is None:
            continue
        if isinstance(et, str):
            et_dt = datetime.fromisoformat(et.replace("Z", "+00:00"))
        else:
            et_dt = et
        et_dt = _ensure_utc(et_dt)
        if et_dt > as_of_u:
            continue
        mcap = row.get("market_cap")
        if mcap is None:
            continue
        try:
            mcap_f = float(mcap)
        except (TypeError, ValueError):
            continue
        if mcap_f <= 0:
            continue
        if best_t is None or et_dt > best_t:
            best = row
            best_t = et_dt

    if best is None or best_t is None:
        return CapLookupResult(
            symbol=sym,
            as_of=as_of_u,
            market_cap=None,
            effective_time=None,
            source=None,
            currency=None,
            cap_group="UNAVAILABLE",
            strategy_cap_group="UNAVAILABLE",
            classification_rule_version=CLASSIFICATION_RULE_VERSION,
            status="CAP_GROUP_UNAVAILABLE",
            reason="CAP_GROUP_UNAVAILABLE",
        )

    mcap_f = float(best["market_cap"])
    tax = taxonomy_label(sym, mcap_f, config=config)
    strat = classify_strategy_cap_group(sym, mcap_f, config=config)
    return CapLookupResult(
        symbol=sym,
        as_of=as_of_u,
        market_cap=mcap_f,
        effective_time=best_t,
        source=str(best.get("source") or "unknown"),
        currency=str(best.get("currency") or "usd"),
        cap_group=tax.upper() if tax in ("BTC", "ETH") else tax,
        strategy_cap_group=strat,
        classification_rule_version=CLASSIFICATION_RULE_VERSION,
        status="OK",
        reason="OK",
    )


def filter_candidates_by_historical_cap(
    candidates: Sequence[Any],
    *,
    required_group: str,
    observations_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    config: ResearchConfig | MultiCapResearchConfig | None = None,
) -> tuple[list[Any], list[Any], dict[str, int]]:
    """Split candidates into cap-eligible vs cross-cap using signal_time as-of.

    Returns (eligible, cross_cap, stats).
    """
    want = required_group.upper().strip()
    eligible: list[Any] = []
    cross: list[Any] = []
    stats = {
        "signals_detected": len(candidates),
        "cap_eligible_signals": 0,
        "cross_cap_signal_count": 0,
        "excluded_by_cap": 0,
        "excluded_by_missing_cap": 0,
        "excluded_by_missing_time": 0,
    }
    for c in candidates:
        sym = str(getattr(c, "symbol", "") or "").upper()
        sig_time = getattr(c, "signal_time", None)
        if not sig_time:
            stats["excluded_by_missing_time"] += 1
            cross.append(c)
            continue
        if isinstance(sig_time, str):
            as_of = datetime.fromisoformat(sig_time.replace("Z", "+00:00"))
        else:
            as_of = sig_time
        obs = observations_by_symbol.get(sym) or []
        lookup = cap_group_at(sym, as_of, obs, config=config)
        if lookup.status != "OK" or lookup.strategy_cap_group == "UNAVAILABLE":
            stats["excluded_by_missing_cap"] += 1
            stats["excluded_by_cap"] += 1
            cross.append(c)
            continue
        if lookup.strategy_cap_group != want:
            stats["cross_cap_signal_count"] += 1
            stats["excluded_by_cap"] += 1
            cross.append(c)
            continue
        eligible.append(c)
        stats["cap_eligible_signals"] += 1
    return eligible, cross, stats
