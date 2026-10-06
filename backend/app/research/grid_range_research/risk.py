"""Research-only grid risk wrapper — does not modify production risk engines."""

from __future__ import annotations

from dataclasses import dataclass

from app.research.grid_range_research.config import (
    RESEARCH_ACCOUNT_EQUITY,
    RESEARCH_LEVERAGE,
    RESEARCH_RISK_USD,
)
from app.research.trade_fees import (
    DEFAULT_LEVERAGE,
    DEFAULT_MAKER_FEE,
    DEFAULT_TAKER_FEE,
    enrich_trade_execution,
)


@dataclass(frozen=True)
class GridRiskCaps:
    risk_usd_per_position: float = RESEARCH_RISK_USD
    max_simultaneous: int = 3
    account_equity: float = RESEARCH_ACCOUNT_EQUITY
    leverage: float = RESEARCH_LEVERAGE
    taker_fee: float = DEFAULT_TAKER_FEE
    maker_fee: float = DEFAULT_MAKER_FEE

    @property
    def max_simultaneous_risk_usd(self) -> float:
        return self.risk_usd_per_position * float(self.max_simultaneous)

    @property
    def max_total_exposure_notional(self) -> float:
        """Hard notional cap: equity * leverage (same paper convention)."""
        return float(self.account_equity) * float(self.leverage or DEFAULT_LEVERAGE)


def apply_fees_and_sizing(
    *,
    direction: str,
    entry_price: float,
    stop_price: float,
    exit_price: float,
    r_gross: float,
    entry_type: str = "MARKET",
    caps: GridRiskCaps | None = None,
) -> dict:
    """Size one grid leg and apply canonical research fees.

    ``r_net`` is fee-adjusted on the *requested* risk unit ($20) so leverage
    notional caps cannot explode R denominators (research-comparable).
    """
    c = caps or GridRiskCaps()
    trade = {
        "direction": direction,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "exit_price": exit_price,
        "r_multiple": r_gross,
        "entry_type": entry_type,
    }
    priced = enrich_trade_execution(
        trade,
        risk_usd=c.risk_usd_per_position,
        taker_fee=c.taker_fee,
        maker_fee=c.maker_fee,
        leverage=c.leverage,
        account_equity=c.account_equity,
    )
    fee = float(priced.get("total_fee") or 0.0)
    req = float(c.risk_usd_per_position)
    # fee is NEGATIVE_COST; converting to R units on requested risk
    r_net = float(r_gross) + (fee / req if req > 0 else 0.0)
    priced["r_gross"] = float(r_gross)
    priced["r_net"] = r_net
    priced["r_net_basis"] = "requested_risk_usd"
    return priced


def assert_no_martingale(sizes: list[float], *, tol: float = 1e-9) -> bool:
    """True iff all position notionals/risk units are equal (no size escalation)."""
    if not sizes:
        return True
    base = float(sizes[0])
    return all(abs(float(s) - base) <= tol for s in sizes)
