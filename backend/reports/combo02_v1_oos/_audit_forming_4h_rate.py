"""Count forming-4h HTF usage across all OOS trades. Diagnostic only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

TRADES = Path(r"e:\cryptoscreener\backend\reports\combo02_v1_oos\oos_trades_20261005T054508Z.json")


def parse_ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)


def forming_4h_open(setup_open: datetime) -> datetime:
    hour = setup_open.hour
    base = hour - (hour % 4)
    return setup_open.replace(hour=base, minute=0, second=0, microsecond=0)


def last_fully_closed_4h_open(setup_open: datetime) -> datetime:
    decision = setup_open + timedelta(hours=1)
    candidate = forming_4h_open(decision - timedelta(seconds=1))
    while candidate + timedelta(hours=4) > decision:
        candidate -= timedelta(hours=4)
    return candidate


def main() -> None:
    trades = json.loads(TRADES.read_text(encoding="utf-8"))["trades"]
    forming = 0
    closed = 0
    by_sym = {"forming": {}, "closed": {}}
    for t in trades:
        sig = parse_ts(t["signal_time"])
        f = forming_4h_open(sig)
        c = last_fully_closed_4h_open(sig)
        sym = t["symbol"]
        if f == c:
            closed += 1
            by_sym["closed"][sym] = by_sym["closed"].get(sym, 0) + 1
        else:
            forming += 1
            by_sym["forming"][sym] = by_sym["forming"].get(sym, 0) + 1
    # hour-of-4h distribution for signal opens
    hod = {}
    for t in trades:
        h = parse_ts(t["signal_time"]).hour % 4
        hod[h] = hod.get(h, 0) + 1
    out = {
        "n": len(trades),
        "forming_4h_at_decision": forming,
        "fully_closed_4h_at_decision": closed,
        "forming_pct": round(100 * forming / len(trades), 2),
        "by_symbol": by_sym,
        "signal_hour_mod4": hod,
        "note": (
            "hour%4==3 means 1h is last hour of 4h so forming==fully closed at 1h close. "
            "Other hours use unfinished 4h final OHLC from history = lookahead."
        ),
    }
    path = TRADES.with_name("audit_forming_4h_rate.json")
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
