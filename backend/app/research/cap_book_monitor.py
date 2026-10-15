"""Read-only snapshots for the cap/MTF research shard monitor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CAP_BOOK_DIRECTORY = (
    Path(__file__).resolve().parents[3]
    / "research_sandbox"
    / "cap_mtf_risk_20261010"
)

SHARD_SPECS = (
    ("a", "Agent A", "large", ("BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA")),
    ("b", "Agent B", "large", ("TRX", "AVAX", "LINK", "DOT", "BCH", "LTC", "TON")),
    ("c", "Agent C", "mid", ("NEAR", "ATOM", "APT", "ARB", "OP", "SUI", "INJ")),
    ("d", "Agent D", "mid", ("AAVE", "UNI", "FIL", "ETC", "ICP", "RENDER")),
)


def read_cap_book_agents(directory: Path | None = None) -> dict[str, Any]:
    """Read every shard independently so one partial write cannot hide the rest."""
    base = directory or CAP_BOOK_DIRECTORY
    shards: list[dict[str, Any]] = []

    for shard_id, agent_name, cap_group, coins in SHARD_SPECS:
        path = base / f"shard_{shard_id}.json"
        shard: dict[str, Any] = {
            "shard": shard_id,
            "agent_name": agent_name,
            "cap_group": cap_group,
            "coins": list(coins),
            "status": "waiting",
            "market_direction": None,
            "principal_usd": 1000.0,
            "elapsed_seconds": 0.0,
            "cells_finished": 0,
            "rows": [],
        }
        try:
            # The workers replace/grow these files. A decode failure is expected
            # during a non-atomic write and is isolated to this shard.
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
                raise ValueError("invalid shard payload")
            rows = [row for row in payload["rows"] if isinstance(row, dict)]
            shard.update(
                status=str(payload.get("status") or "waiting"),
                market_direction=payload.get("market_direction"),
                principal_usd=payload.get("principal_usd", 1000.0),
                elapsed_seconds=payload.get("elapsed_seconds", 0.0),
                cells_finished=len(rows),
                rows=rows,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            # Missing and mid-write files have the same UI contract. The next
            # three-second poll will try the disk again.
            pass
        shards.append(shard)

    return {"status": "OK", "poll_interval_seconds": 3, "shards": shards}
