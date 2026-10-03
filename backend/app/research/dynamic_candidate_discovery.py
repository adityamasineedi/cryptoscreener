"""Daily/manual dynamic candidate discovery from the broad screener universe.

Never creates paper trades, Telegram alerts, or v1 promotions.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import structlog

from app.research.combo02_candidate_selector import select_candidates
from app.research.combo02_candidate_thresholds import CandidateSelectorConfig
from app.research.dynamic_candidate_constants import (
    DISCLAIMER,
    SELECTOR_VERSION,
    STRATEGY_ID,
)
from app.research.strategy_candidate_registry import strategy_candidate_registry
from app.services.database import db_manager

logger = structlog.get_logger(__name__)


async def run_dynamic_discovery(
    *,
    top_n: int = 30,
    min_avg_24h_quote_volume_usd: float | None = None,
    config: CandidateSelectorConfig | None = None,
) -> dict[str, Any]:
    """Select Top-N liquid futures and upsert DISCOVERED registry rows."""
    # CLI / jobs: ensure DB is connected when available (market_store may be empty).
    if not db_manager.enabled:
        try:
            from app.config import get_settings

            await db_manager.connect(get_settings())
        except Exception:  # noqa: BLE001
            pass

    await strategy_candidate_registry.ensure_schema()
    base = config or CandidateSelectorConfig(
        top_n=int(top_n),
        selector_version=SELECTOR_VERSION,
    )
    if min_avg_24h_quote_volume_usd is not None:
        cfg = CandidateSelectorConfig(
            **{
                **base.to_dict(),
                "top_n": int(top_n),
                "min_avg_24h_quote_volume_usd": float(min_avg_24h_quote_volume_usd),
                "selector_version": SELECTOR_VERSION,
            }
        )
    else:
        cfg = CandidateSelectorConfig(
            **{**base.to_dict(), "top_n": int(top_n), "selector_version": SELECTOR_VERSION}
        )

    manifest = await select_candidates(config=cfg)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_id = f"dynamic_discovery_{stamp}"

    selected = manifest.get("symbols") or manifest.get("candidates") or []
    excluded = manifest.get("excluded") or []
    reason_counts = Counter(str(e.get("reason") or "unknown") for e in excluded)

    new_count = 0
    already = 0
    for row in selected:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        _, is_new = await strategy_candidate_registry.upsert_discovered(
            symbol=sym,
            selector_version=str(cfg.selector_version),
            manifest_id=manifest_id,
            rank=int(row.get("rank") or 0) or None,
            volume_usd=(
                float(row["avg_24h_quote_volume_usd"])
                if row.get("avg_24h_quote_volume_usd") is not None
                else None
            ),
            discovery_reason=str(row.get("inclusion_reason") or "top_liquidity"),
            market_type="futures_perp",
            quote_asset="USDT",
        )
        if is_new:
            new_count += 1
        else:
            already += 1

    summary = {
        "status": "OK",
        "strategy_id": STRATEGY_ID,
        "manifest_id": manifest_id,
        "selected": len(selected),
        "new": new_count,
        "already_registered": already,
        "excluded": len(excluded),
        "reasons": dict(reason_counts),
        "selector_version": cfg.selector_version,
        "disclaimer": DISCLAIMER,
        "telegram_eligible": False,
        "paper_trades_created": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        "dynamic_discovery_completed",
        selected=summary["selected"],
        new=summary["new"],
        already_registered=summary["already_registered"],
        excluded=summary["excluded"],
        reasons=summary["reasons"],
    )
    return summary
