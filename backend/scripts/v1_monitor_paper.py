#!/usr/bin/env python3
"""Weekly COMBO_02 v1 paper review — compare closed trades to backtest ranges.

Usage:
  python scripts/v1_monitor_paper.py
  python scripts/v1_monitor_paper.py --json /path/to/closed.json

Reads closed paper trades from Postgres when DATABASE_ENABLED, else from --json.
Only COMBO_02_V1 / V1_PAPER_WATCHER / 1h Path A rows are scored. Legacy 15m
research trades are excluded even when the symbol is BTC/ETH/SOL.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.research.v1_production import (  # noqa: E402
    V1_MONITOR_THRESHOLDS,
    classify_tier,
    profile_summary,
)
from app.services.paper_classification import (  # noqa: E402
    PATH_B,
    STRATEGY_EXPERIMENTAL_PATH_B,
    TIMEFRAME_V1,
    classification_fields_from_position,
    is_legacy_research,
    is_v1_classified,
)


def _r(trade: dict[str, Any]) -> float | None:
    for k in ("r_multiple", "r_net", "r_gross"):
        v = trade.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _tf(trade: dict[str, Any]) -> str:
    tf = trade.get("timeframe") or (trade.get("signal_snippet") or {}).get("timeframe")
    return str(tf or "1h").lower()


def max_losing_streak(rs: list[float]) -> int:
    best = cur = 0
    for r in rs:
        if r < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_drawdown_r(rs: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def partition_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Split closed rows into v1-eligible vs exclusion buckets (diagnostics)."""
    v1: list[dict[str, Any]] = []
    excl_legacy = 0
    excl_path_b = 0
    excl_wrong_tf = 0
    excl_other = 0

    for t in trades:
        status = str(t.get("status") or "").upper()
        if status and status not in ("CLOSED",):
            continue
        fields = classification_fields_from_position(t)
        path = str(fields.get("path") or "").upper().replace("PATH_", "")
        tf = str(fields.get("timeframe") or _tf(t)).lower()

        if is_v1_classified(fields) and tf == TIMEFRAME_V1:
            v1.append(t)
            continue

        # Mutually exclusive buckets (priority: Path B → wrong TF → legacy → other)
        if path == PATH_B or str(fields.get("strategy_id") or "") == STRATEGY_EXPERIMENTAL_PATH_B:
            excl_path_b += 1
        elif tf != TIMEFRAME_V1:
            excl_wrong_tf += 1
        elif is_legacy_research(fields):
            excl_legacy += 1
        else:
            excl_other += 1

    return {
        "v1_eligible": v1,
        "excluded_legacy_research": excl_legacy,
        "excluded_path_b": excl_path_b,
        "excluded_wrong_timeframe": excl_wrong_tf,
        "excluded_other": excl_other,
    }


def summarize(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in trades:
        if str(t.get("status") or "").upper() not in ("CLOSED", "", "closed"):
            if t.get("status") and str(t.get("status")).upper() not in ("CLOSED",):
                continue
        sym = str(t.get("symbol") or "").upper()
        if not sym:
            continue
        r = _r(t)
        if r is None:
            continue
        buckets[(sym, _tf(t))].append(r)

    refs = V1_MONITOR_THRESHOLDS["backtest_reference"]
    min_n = int(V1_MONITOR_THRESHOLDS["min_trades_for_review"])
    wr_floor = float(V1_MONITOR_THRESHOLDS["win_rate_floor_vs_backtest"])
    dd_mult = float(V1_MONITOR_THRESHOLDS["max_dd_multiple_vs_backtest"])
    streak_mult = float(V1_MONITOR_THRESHOLDS["max_losing_streak_multiple"])

    rows: list[dict[str, Any]] = []
    for (sym, tf), rs in sorted(buckets.items()):
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        wr = wins / n if n else 0.0
        avg_r = sum(rs) / n if n else 0.0
        dd = max_drawdown_r(rs)
        streak = max_losing_streak(rs)
        ref = refs.get((sym, tf)) or {}
        flags: list[str] = []
        if n >= min_n and ref:
            bt_wr = float(ref.get("win_rate") or 0)
            bt_dd = float(ref.get("max_dd_R") or 0)
            bt_streak = float(ref.get("max_losing_streak") or 0)
            if bt_wr > 0 and wr < bt_wr * wr_floor:
                flags.append(
                    f"WR {wr:.0%} < {wr_floor:.0%} of BT {bt_wr:.0%} → review"
                )
            if bt_dd > 0 and dd > bt_dd * dd_mult:
                flags.append(
                    f"maxDD {dd:.2f}R > {dd_mult:g}× BT {bt_dd:g}R → pause/reduce"
                )
            if bt_streak > 0 and streak > bt_streak * streak_mult:
                flags.append(
                    f"lose streak {streak} > {streak_mult:g}× BT {bt_streak:g} → pause/reduce"
                )
        elif n < min_n:
            flags.append(f"n={n} < {min_n} (observe only)")

        rows.append(
            {
                "symbol": sym,
                "timeframe": tf,
                "tier": classify_tier(sym, tf),
                "n": n,
                "win_rate": round(wr, 4),
                "avg_R": round(avg_r, 4),
                "net_R": round(sum(rs), 4),
                "max_dd_R": round(dd, 4),
                "max_losing_streak": streak,
                "backtest_ref": ref or None,
                "flags": flags,
            }
        )
    return rows


def load_json(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "closed" in raw:
        return list(raw["closed"] or [])
    if isinstance(raw, list):
        return raw
    raise SystemExit(f"Unrecognized JSON shape in {path}")


def load_db() -> list[dict[str, Any]]:
    """Load CLOSED paper trades from Postgres via PersistenceService."""
    try:
        import asyncio

        from app.config import get_settings
        from app.services.database import db_manager
        from app.services.persistence import persistence

        settings = get_settings()
        if not getattr(settings, "database_enabled", False):
            print("# DATABASE_ENABLED=false — skipping DB load", file=sys.stderr)
            return []

        async def _load() -> list[dict[str, Any]]:
            await db_manager.connect(settings)
            try:
                rows = await persistence.load_paper_trades(closed_limit=500)
            finally:
                await db_manager.close()
            return [r for r in rows if str(r.get("status") or "").upper() == "CLOSED"]

        return asyncio.run(_load())
    except Exception as exc:  # noqa: BLE001
        print(f"# DB load skipped: {exc}", file=sys.stderr)
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, help="Closed trades JSON export")
    ap.add_argument("--profile", action="store_true", help="Print v1 profile summary")
    args = ap.parse_args()

    if args.profile:
        print(json.dumps(profile_summary(), indent=2))
        return 0

    trades: list[dict[str, Any]] = []
    if args.json:
        trades = load_json(args.json)
    else:
        trades = load_db()
        if not trades:
            print(
                "No CLOSED paper trades in DB yet — v1 watcher book is empty "
                "(or only OPEN/CANCELLED). Re-run after closes, or pass --json.",
                file=sys.stderr,
            )
            print(
                json.dumps(
                    {
                        "trade_count": 0,
                        "v1_eligible_closed_trades": 0,
                        "excluded_legacy_research_trades": 0,
                        "excluded_path_b_trades": 0,
                        "excluded_wrong_timeframe_trades": 0,
                        "rows": [],
                        "note": "empty_closed_book",
                    },
                    indent=2,
                )
            )
            return 0

    part = partition_trades(trades)
    v1_trades = part["v1_eligible"]
    rows = summarize(v1_trades)

    print(
        f"V1 eligible closed trades: {len(v1_trades)}",
        file=sys.stderr,
    )
    print(
        f"Excluded legacy/research trades: {part['excluded_legacy_research']}",
        file=sys.stderr,
    )
    print(
        f"Excluded Path B trades: {part['excluded_path_b']}",
        file=sys.stderr,
    )
    print(
        f"Excluded wrong-timeframe trades: {part['excluded_wrong_timeframe']}",
        file=sys.stderr,
    )

    print(
        json.dumps(
            {
                "trade_count_raw": len(trades),
                "v1_eligible_closed_trades": len(v1_trades),
                "excluded_legacy_research_trades": part["excluded_legacy_research"],
                "excluded_path_b_trades": part["excluded_path_b"],
                "excluded_wrong_timeframe_trades": part["excluded_wrong_timeframe"],
                "excluded_other_trades": part["excluded_other"],
                "rows": rows,
            },
            indent=2,
        )
    )
    flagged = [r for r in rows if any("pause" in f or "review" in f for f in r["flags"])]
    if flagged:
        print("\n# ACTION FLAGS", file=sys.stderr)
        for r in flagged:
            print(f"# {r['symbol']} {r['timeframe']}: {'; '.join(r['flags'])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
