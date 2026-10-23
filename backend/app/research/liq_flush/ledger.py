"""Append-only virtual ledger and the E34 read rule.

The official read is withheld until 30 closed trades. Before that, status
reports counts only. At 30 trades, PASS means net > 0 and PF > 1.10 on the
first 30 closes. If 30 trades are not reached 12 months after the start,
the read is INCONCLUSIVE.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.research.liq_flush.costs import INCONCLUSIVE_MONTHS, READ_MIN_PF, READ_TRADES

UTC = timezone.utc
PRODUCTION_STORE = Path(r"D:\cryptoscreener\data\liq_flush")
LIVE_DATA = Path(r"D:\cryptoscreener\data")


def resolve_store(store: Path, *, production: bool) -> Path:
    """Tests pass a temp directory. The live data tree is refused unless this is the owner process."""
    path = Path(store).resolve()
    live = LIVE_DATA.resolve()
    prod = PRODUCTION_STORE.resolve()
    under_live = path == live or live in path.parents
    if under_live and path != prod:
        raise RuntimeError("refusing to write the live data tree")
    if path == prod and not production:
        raise RuntimeError("refusing to write the live liq_flush store")
    if production and path != prod:
        raise RuntimeError("production mode writes only D:\\cryptoscreener\\data\\liq_flush")
    return path


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read append-only JSON lines. A torn final line is ignored and rewritten past."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    out = []
    for index, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
    return out


def add_months(when: datetime, months: int) -> datetime:
    month0 = when.month - 1 + months
    year = when.year + month0 // 12
    month = month0 % 12 + 1
    if month == 12:
        following = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        following = datetime(year, month + 1, 1, tzinfo=UTC)
    last_day = (following - timedelta(days=1)).day
    return when.replace(year=year, month=month, day=min(when.day, last_day))


def profit_factor(nets: Iterable[float]) -> tuple[float | None, bool]:
    """Return (pf, infinite). Infinite when there are wins and no losses."""
    wins = 0.0
    losses = 0.0
    for net in nets:
        if net > 0:
            wins += net
        elif net < 0:
            losses += net
    if losses < 0:
        return wins / abs(losses), False
    if wins > 0:
        return None, True
    return None, False


def judge(closes: list[dict[str, Any]]) -> dict[str, Any]:
    """PASS / FAIL on the first 30 closes. Caller guarantees len >= 30."""
    ordered = sorted(closes, key=lambda row: (row["exit_hour"], row["symbol"], row["side"], row["trade_id"]))
    sample = ordered[:READ_TRADES]
    nets = [float(row["net"]) for row in sample]
    total = sum(nets)
    pf, infinite = profit_factor(nets)
    passed = total > 0 and (infinite or (pf is not None and pf > READ_MIN_PF))
    return {
        "capped_trades": sum(1 for row in sample if row.get("capped")),
        "n": READ_TRADES,
        "net": total,
        "pf": pf,
        "pf_infinite": infinite,
        "type": "read",
        "verdict": "PASS" if passed else "FAIL",
    }


def status_from_events(events: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Counts always. Net and PF only after the official read."""
    now = now.astimezone(UTC)
    start = next((row for row in events if row.get("type") == "start"), None)
    closes = [row for row in events if row.get("type") == "close"]
    open_ids = {row["trade_id"] for row in events if row.get("type") == "open"}
    closed_ids = {row["trade_id"] for row in closes}
    halt = next((row for row in events if row.get("type") == "halt"), None)
    read = next((row for row in events if row.get("type") == "read"), None)
    out: dict[str, Any] = {
        "closed_trades": len(closes),
        "halted": halt.get("code") if halt else None,
        "open_trades": len(open_ids - closed_ids),
        "read": "NOT_YET",
        "started_at": start.get("at") if start else None,
    }
    if read:
        out["read"] = read["verdict"]
        out["net"] = read["net"]
        out["pf"] = read["pf"]
        out["pf_infinite"] = bool(read.get("pf_infinite"))
        out["capped_trades"] = read.get("capped_trades")
        return out
    if start and len(closes) < READ_TRADES:
        started = datetime.fromisoformat(start["at"])
        if now >= add_months(started, INCONCLUSIVE_MONTHS):
            out["read"] = "INCONCLUSIVE"
    return out
