"""Paper-trade risk gates — large/mid preferred; no fabricated mcap/liq data.

All gates are fail-closed when required LIVE inputs are missing (except
BTC/ETH symbol identity, which does not need CoinGecko mcap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.engines.liquidations.engine import liquidation_spike
from app.models.schemas import DataStatus
from app.research.config import classify_asset_group

logger = get_logger("paper_risk")

# Groups allowed for auto paper opens by default
DEFAULT_ALLOWED_GROUPS = frozenset({"BTC", "ETH", "large-cap", "mid-cap"})


@dataclass
class PaperRiskPolicy:
    """Conservative defaults for meme / thin-name protection."""

    enabled: bool = True
    allowed_groups: frozenset[str] = field(default_factory=lambda: DEFAULT_ALLOWED_GROUPS)
    # When mcap is WAITING, still allow if 24h quote volume is LIVE and >= this
    min_quote_volume_24h: float = 5_000_000.0
    # Stricter volume if we only have volume (no mcap) as liquidity proxy
    min_quote_volume_no_mcap: float = 20_000_000.0
    risk_pct_btc_eth: float = 0.02
    risk_pct_large: float = 0.02
    risk_pct_mid: float = 0.01
    risk_pct_small: float = 0.005
    risk_pct_unknown: float = 0.005
    max_open_positions: int = 5
    # Max sum(open risk_usd) as fraction of equity
    max_open_risk_pct: float = 0.10
    liq_gate_enabled: bool = True
    # Only pause on meaningful long-liq pressure (not micro force-orders)
    liq_min_long_notional_5m: float = 25_000.0
    liq_spike_multiplier: float = 3.0

    def risk_percent_for_group(self, group: str) -> float:
        g = (group or "UNKNOWN").upper() if group in {"BTC", "ETH"} else (group or "UNKNOWN")
        # classify returns mixed case for *-cap
        if group in ("BTC", "ETH"):
            return float(self.risk_pct_btc_eth)
        if group == "large-cap":
            return float(self.risk_pct_large)
        if group == "mid-cap":
            return float(self.risk_pct_mid)
        if group == "small-cap":
            return float(self.risk_pct_small)
        return float(self.risk_pct_unknown)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed_groups": sorted(self.allowed_groups),
            "min_quote_volume_24h": self.min_quote_volume_24h,
            "min_quote_volume_no_mcap": self.min_quote_volume_no_mcap,
            "risk_pct_btc_eth": self.risk_pct_btc_eth,
            "risk_pct_large": self.risk_pct_large,
            "risk_pct_mid": self.risk_pct_mid,
            "risk_pct_small": self.risk_pct_small,
            "max_open_positions": self.max_open_positions,
            "max_open_risk_pct": self.max_open_risk_pct,
            "liq_gate_enabled": self.liq_gate_enabled,
            "liq_min_long_notional_5m": self.liq_min_long_notional_5m,
        }


def policy_from_settings(settings: Any | None = None) -> PaperRiskPolicy:
    if settings is None:
        try:
            from app.config import get_settings

            settings = get_settings()
        except Exception:  # noqa: BLE001
            return PaperRiskPolicy()

    groups_raw = str(
        getattr(settings, "paper_allowed_groups", "BTC,ETH,large-cap,mid-cap") or ""
    )
    groups = frozenset(g.strip() for g in groups_raw.split(",") if g.strip())
    return PaperRiskPolicy(
        enabled=bool(getattr(settings, "paper_risk_gates_enabled", True)),
        allowed_groups=groups or DEFAULT_ALLOWED_GROUPS,
        min_quote_volume_24h=float(
            getattr(settings, "paper_min_quote_volume_24h", 5_000_000.0) or 5_000_000.0
        ),
        min_quote_volume_no_mcap=float(
            getattr(settings, "paper_min_quote_volume_no_mcap", 20_000_000.0)
            or 20_000_000.0
        ),
        risk_pct_btc_eth=float(getattr(settings, "paper_risk_pct_btc_eth", 0.02) or 0.02),
        risk_pct_large=float(getattr(settings, "paper_risk_pct_large", 0.02) or 0.02),
        risk_pct_mid=float(getattr(settings, "paper_risk_pct_mid", 0.01) or 0.01),
        risk_pct_small=float(getattr(settings, "paper_risk_pct_small", 0.005) or 0.005),
        risk_pct_unknown=float(getattr(settings, "paper_risk_pct_unknown", 0.005) or 0.005),
        max_open_positions=int(getattr(settings, "paper_max_open_positions", 5) or 5),
        max_open_risk_pct=float(getattr(settings, "paper_max_open_risk_pct", 0.10) or 0.10),
        liq_gate_enabled=bool(getattr(settings, "paper_liq_gate_enabled", True)),
        liq_min_long_notional_5m=float(
            getattr(settings, "paper_liq_min_long_notional_5m", 25_000.0) or 25_000.0
        ),
    )


def _mcap_and_group(symbol: str) -> tuple[str, float | None, str]:
    """Return (group, mcap_value_or_None, mcap_status)."""
    from app.services.engine_store import engine_store

    fv = engine_store.get_fundamental(symbol, "market_cap")
    raw_st = getattr(fv, "status", DataStatus.WAITING)
    if hasattr(raw_st, "value"):
        status = str(raw_st.value).upper()
    else:
        status = str(raw_st or "WAITING").upper()
        if status.startswith("DATASTATUS."):
            status = status.split(".", 1)[-1]
    val = getattr(fv, "value", None)
    mcap = float(val) if val is not None else None
    if mcap is not None and mcap <= 0:
        mcap = None
    group = classify_asset_group(symbol, mcap)
    return group, mcap, status


def _quote_volume_24h(symbol: str) -> tuple[float | None, str]:
    try:
        from app.services.market_store import market_store

        tick = market_store.get_ticker(symbol)
        if tick is None:
            return None, "WAITING"
        qv = getattr(tick, "quote_volume_24h", None)
        if qv is None:
            return None, "WAITING"
        st = getattr(tick, "status", DataStatus.LIVE)
        status = str(st.value if hasattr(st, "value") else st).upper()
        return float(qv), status
    except Exception:  # noqa: BLE001
        return None, "UNAVAILABLE"


def _long_liq_spike_blocks(symbol: str, policy: PaperRiskPolicy) -> tuple[bool, str]:
    """True = block open. Only when liquidation feed is LIVE for real events."""
    if not policy.liq_gate_enabled:
        return False, ""
    try:
        from app.engines.orchestrator import get_orchestrator

        orch = get_orchestrator()
        if orch is None or getattr(orch, "liquidations", None) is None:
            return False, ""
        liq = orch.liquidations
        raw_status = liq.status()
        if isinstance(raw_status, dict):
            st = str(
                raw_status.get("liquidation_status") or raw_status.get("status") or ""
            ).upper()
        else:
            st = str(raw_status or "").upper()
        if st != "LIVE":
            # Honest: no force-order stream yet — do not invent a spike
            return False, ""
        # Prefer per-symbol freshness when available
        try:
            summary = liq.get_summary(symbol)
            sym_st = str(getattr(summary, "status", "") or "")
            if hasattr(summary.status, "value"):
                sym_st = str(summary.status.value)
            sym_st = sym_st.upper()
            if sym_st in ("WAITING", "UNAVAILABLE"):
                return False, ""
        except Exception:  # noqa: BLE001
            pass
        aggs = liq.aggregates(symbol) or {}
        w5 = aggs.get("5m") or {}
        w15 = aggs.get("15m") or {}
        long_5 = float(w5.get("long_liq_notional") or 0)
        total_5 = float(w5.get("total_notional") or 0)
        baseline = (float(w15.get("total_notional") or 0)) / 3.0
        if long_5 < policy.liq_min_long_notional_5m:
            return False, ""
        # Require a real baseline so quiet books with one event aren't "spikes"
        if baseline <= 0:
            return False, ""
        if not liquidation_spike(
            total_5, baseline, multiplier=policy.liq_spike_multiplier
        ):
            return False, ""
        # Long paper: pause when long liquidations dominate the spike
        if total_5 > 0 and long_5 < total_5 * 0.45:
            return False, ""
        return True, f"long_liq_spike_5m={long_5:.0f}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("paper_liq_gate_error", symbol=symbol, error=str(exc))
        return False, ""


@dataclass
class RiskGateResult:
    ok: bool
    reason: str = ""
    group: str = "UNKNOWN"
    risk_percent: float = 0.02
    mcap: float | None = None
    quote_volume_24h: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "group": self.group,
            "risk_percent": self.risk_percent,
            "mcap": self.mcap,
            "quote_volume_24h": self.quote_volume_24h,
        }


def evaluate_paper_entry_risk(
    symbol: str,
    *,
    policy: PaperRiskPolicy,
    open_count: int,
    open_risk_usd: float,
    equity: float,
    planned_risk_usd: float | None = None,
) -> RiskGateResult:
    """Return whether a new paper long may open under the risk policy.

    Fail-closed for non-majors when market_cap is not LIVE/CACHED/STALE —
    we never invent a cap tier for memes.
    """
    sym = symbol.upper()
    group, mcap, mcap_status = _mcap_and_group(sym)
    qv, qv_status = _quote_volume_24h(sym)
    risk_pct = policy.risk_percent_for_group(group)

    if not policy.enabled:
        return RiskGateResult(
            ok=True,
            reason="",
            group=group,
            risk_percent=risk_pct,
            mcap=mcap,
            quote_volume_24h=qv,
        )

    def _fail(reason: str, *, rp: float | None = None) -> RiskGateResult:
        return RiskGateResult(
            ok=False,
            reason=reason,
            group=group,
            risk_percent=float(rp if rp is not None else risk_pct),
            mcap=mcap,
            quote_volume_24h=qv,
        )

    # Book limits
    if open_count >= int(policy.max_open_positions):
        return _fail(f"max_open_positions={policy.max_open_positions}")
    max_book = float(equity) * float(policy.max_open_risk_pct)
    if open_risk_usd >= max_book - 1e-9:
        return _fail(f"max_open_risk_usd={max_book:.2f}")
    if planned_risk_usd is not None and (open_risk_usd + planned_risk_usd) > max_book + 1e-9:
        return _fail(f"open_risk_would_exceed={max_book:.2f}")

    mcap_known = mcap_status in ("LIVE", "CACHED", "STALE") and mcap is not None
    is_major = group in ("BTC", "ETH")

    # Cap / identity gate
    if is_major:
        if group not in policy.allowed_groups:
            return _fail(f"group_not_allowed={group}")
    elif mcap_known:
        if group not in policy.allowed_groups:
            return _fail(f"group_not_allowed={group}")
    else:
        # Non-major without usable mcap — do not guess (blocks most memes early)
        return _fail(f"mcap_unavailable_status={mcap_status or 'WAITING'}")

    # Liquidity floor (majors may pass with slightly lower bar still applied)
    if qv_status in ("LIVE", "CACHED", "STALE") and qv is not None:
        if qv < policy.min_quote_volume_24h:
            return _fail(f"quote_volume_low={qv:.0f}")
    elif not is_major:
        return _fail("quote_volume_unavailable")

    blocked, liq_reason = _long_liq_spike_blocks(sym, policy)
    if blocked:
        return _fail(liq_reason or "liquidation_spike")

    return RiskGateResult(
        ok=True,
        reason="",
        group=group,
        risk_percent=risk_pct,
        mcap=mcap,
        quote_volume_24h=qv,
    )
