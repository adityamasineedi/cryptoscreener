"""Follow an alert on closed bars of its own timeframe: fill, targets, SL, expiry, result in R.

Rules (gross, no fees):
- Valid for ``validity_bars`` bars after the signal bar closes. A bar whose range touches the entry zone
  fills at its open clipped into the zone (a limit at the zone edge when the open is outside it).
- On the fill bar only the SL is checked (the order of moves inside that bar is unknown).
- SL and a target on the same bar count as the SL.
- The result in R is the tested exit: T2 or the SL, whichever comes first, from the fill, in units of the
  alert's R (entry mid to SL). T3 and T4 hits after T2 are reported but do not change it.
- Tracking ends at the SL, at T4, or after ``max_track_bars`` bars (result marked at that bar's close).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from app.alerts.config import AlertConfig, timeframe_minutes
from app.alerts.plan import Signal, utc

PENDING = "PENDING"
FILLED = "FILLED"
DONE = "DONE"


def new_tracker(sig: Signal, plan: Mapping[str, Any], cfg: AlertConfig, *, sent: bool) -> dict[str, Any]:
    tf_min = timeframe_minutes(sig.timeframe)
    close = sig.bar_close
    return {
        "alert_id": sig.alert_id,
        "symbol": sig.symbol,
        "side": sig.side,
        "timeframe": sig.timeframe,
        "status": None,
        "sent": bool(sent),
        "tick": plan["tick"],
        "zone_lo": plan["zone_lo"],
        "zone_hi": plan["zone_hi"],
        "stop": plan["stop"],
        "r_unit": plan["r_unit"],
        "targets": list(plan["targets"]),
        "bar_close": close.isoformat(),
        "valid_until": (close + timedelta(minutes=tf_min * int(cfg.raw["validity_bars"]))).isoformat(),
        "max_track_bars": int(cfg.raw["tracking"]["max_track_bars"]),
        "state": PENDING,
        "next_bar": close.isoformat(),
        "fill_price": None,
        "fill_bar": None,
        "hits": [],
        "tested_done": False,
        "final_r": None,
        "bars_tracked": 0,
    }


def _r(tr: Mapping[str, Any], price: float) -> float:
    sgn = 1.0 if tr["side"] == "LONG" else -1.0
    return (price - float(tr["fill_price"])) * sgn / float(tr["r_unit"])


def step(tr: dict[str, Any], bar: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Apply one closed bar (open time >= tr['next_bar']). Returns follow-up events."""
    if tr["state"] == DONE:
        return []
    t = utc(bar["time"])
    if t < utc(tr["next_bar"]):
        return []
    tf = timedelta(minutes=timeframe_minutes(tr["timeframe"]))
    tr["next_bar"] = (t + tf).isoformat()
    o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    long = tr["side"] == "LONG"
    stop = float(tr["stop"])
    bar_iso = t.isoformat()
    close_iso = (t + tf).isoformat()
    events: list[dict[str, Any]] = []

    if tr["state"] == PENDING:
        if t >= utc(tr["valid_until"]):
            tr["state"] = DONE
            return [{"kind": "expired", "at": tr["valid_until"]}]
        lo, hi = float(tr["zone_lo"]), float(tr["zone_hi"])
        if l <= hi and h >= lo:
            fill = min(max(o, lo), hi)
            tr.update(state=FILLED, fill_price=fill, fill_bar=bar_iso)
            events.append({"kind": "fill", "price": fill, "at": close_iso, "bar": bar_iso})
            if (l <= stop) if long else (h >= stop):
                events.extend(_stop(tr, close_iso, bar_iso))
            return events
        if t + tf >= utc(tr["valid_until"]):
            tr["state"] = DONE
            events.append({"kind": "expired", "at": tr["valid_until"]})
        return events

    # FILLED
    tr["bars_tracked"] = int(tr["bars_tracked"]) + 1
    if (l <= stop) if long else (h >= stop):
        return _stop(tr, close_iso, bar_iso)
    targets = [float(x) for x in tr["targets"]]
    for k, px in enumerate(targets, start=1):
        if k in tr["hits"]:
            continue
        if (h >= px) if long else (l <= px):
            tr["hits"].append(k)
            events.append({"kind": "target", "target": k, "price": px, "r": _r(tr, px), "at": close_iso, "bar": bar_iso})
            if k == 2 and not tr["tested_done"]:
                tr["tested_done"] = True
                tr["final_r"] = _r(tr, px)
                events.append({"kind": "final", "exit": "T2", "price": px, "r": tr["final_r"], "at": close_iso})
        else:
            break  # targets are ordered; a further one cannot be hit before a nearer one
    if 4 in tr["hits"]:
        tr["state"] = DONE
    elif int(tr["bars_tracked"]) >= int(tr["max_track_bars"]):
        tr["state"] = DONE
        if not tr["tested_done"]:
            tr["tested_done"] = True
            tr["final_r"] = _r(tr, c)
            events.append({"kind": "final", "exit": "timeout", "price": c, "r": tr["final_r"], "at": close_iso})
    return events


def _stop(tr: dict[str, Any], close_iso: str, bar_iso: str) -> list[dict[str, Any]]:
    stop = float(tr["stop"])
    tr["state"] = DONE
    out = [{"kind": "sl", "price": stop, "r": _r(tr, stop), "at": close_iso, "bar": bar_iso}]
    if not tr["tested_done"]:
        tr["tested_done"] = True
        tr["final_r"] = _r(tr, stop)
        out.append({"kind": "final", "exit": "SL", "price": stop, "r": tr["final_r"], "at": close_iso})
    return out


def expire_if_due(tr: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """A pending alert whose window has closed with no bar touching the zone (bars missing included)."""
    if tr["state"] != PENDING or now < utc(tr["valid_until"]):
        return []
    grace = timedelta(minutes=timeframe_minutes(tr["timeframe"]))
    if utc(tr["next_bar"]) >= utc(tr["valid_until"]) or now >= utc(tr["valid_until"]) + grace:
        tr["state"] = DONE
        return [{"kind": "expired", "at": tr["valid_until"]}]
    return []
