"""Research paper-entry simulator — never pretends paper_entry == backtest_entry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.research.live_backtest_parity.constants import (
    PRODUCTION_APPROVED,
    SOURCE,
    STRATEGY_ID,
    TELEGRAM_ELIGIBLE,
)
from app.research.live_backtest_parity.entry_price import (
    build_entry_triad,
    check_entry_price,
    compute_entry_deviation,
    entry_triad_snippet_fields,
    resolve_live_price,
)
from app.research.live_backtest_parity.models import ParityEvent


def simulate_paper_entry(
    event: ParityEvent,
    *,
    live_result: Mapping[str, Any],
    injected_live_price: float | None = None,
    injected_price_source: str | None = None,
    open_on_stale: bool = False,
    paper_entry_at: datetime | None = None,
) -> dict[str, Any]:
    """Record paper entry with distinct triad values.

    paper_entry uses the resolved live signal price (actual observation).
    backtest_entry remains the strategy AS-OF entry. Never copies silently.
    """
    backtest_entry = event.backtest_entry
    if backtest_entry is None:
        backtest_entry = _f(live_result.get("entry_price"))

    close_fallback = _f(live_result.get("entry_price"))
    # Prefer last close from tip if present in extra.
    if event.extra.get("setup_close") is not None:
        close_fallback = _f(event.extra.get("setup_close"))

    live_px, source = resolve_live_price(
        event.symbol,
        fallback_close=close_fallback,
        injected=injected_live_price,
        injected_source=injected_price_source,
    )

    # Paper fill = observed live price when available; else unavailable.
    paper_px = live_px
    triad = build_entry_triad(
        backtest_entry=backtest_entry,
        live_signal_price=live_px,
        paper_entry=paper_px,
        price_source=source,
    )
    event.entry_triad = triad
    dev = compute_entry_deviation(triad)
    event.absolute_difference = dev["absolute_difference"]
    event.entry_deviation_pct = dev["difference_pct"]
    event.live_vs_backtest_bps = dev["live_vs_backtest_bps"]

    check = check_entry_price(triad)
    event.entry_price_status = str(check["entry_price_status"])
    event.entry_price_label = check.get("entry_price_label")

    event.strategy_id = event.strategy_id or STRATEGY_ID
    event.source = event.source or SOURCE
    event.production_approved = PRODUCTION_APPROVED
    event.telegram_eligible = TELEGRAM_ELIGIBLE

    paper_time = paper_entry_at or datetime.now(timezone.utc)
    event.timeline.paper_entry_at = paper_time

    opened = False
    skip_reason = None
    if triad.paper_entry is None or triad.backtest_entry is None:
        skip_reason = "unavailable_entry"
    elif event.entry_price_status == "STALE" and not open_on_stale:
        skip_reason = "STALE_ENTRY"
    else:
        opened = True
        event.paper_opened = True

    snippet = {
        "strategy_id": STRATEGY_ID,
        "source": SOURCE,
        "production_approved": False,
        "telegram_eligible": False,
        "signal_time": event.timeline.signal_detected_at.isoformat()
        if event.timeline.signal_detected_at
        else None,
        "paper_entry_time": paper_time.isoformat(),
        **entry_triad_snippet_fields(triad, check),
        # Explicit honesty: equality only when numerically true.
        "paper_equals_backtest": (
            triad.paper_entry is not None
            and triad.backtest_entry is not None
            and abs(float(triad.paper_entry) - float(triad.backtest_entry)) < 1e-12
        ),
    }
    return {
        "opened": opened,
        "skip_reason": skip_reason,
        "snippet": snippet,
        "backtest_entry": triad.backtest_entry,
        "live_signal_price": triad.live_signal_price,
        "paper_entry": triad.paper_entry,
        "entry_deviation_pct": event.entry_deviation_pct,
        "signal_time": snippet["signal_time"],
        "paper_entry_time": snippet["paper_entry_time"],
        "entry_price_status": event.entry_price_status,
        "price_source": triad.price_source,
    }


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
