"""Coin-page context checklist (owner D205). Display only.

One row per category, from closed bars and data the app already has. Nothing here is a
signal, an alert source, or a paper input. The banner says so. Order words are not used.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

BANNER = "Context only; no combination of these is a tested signal (FINAL_VERDICT)."
ALIGN_LONG = "aligned with a long"
ALIGN_SHORT = "aligned with a short"
ALIGN_NEUTRAL = "mixed/neutral"
NO_OI = "no OI history"
NO_LIQ = "no 30-day liquidation history yet"
NO_FUNDING = "funding: no data"
NO_VOLUME = "no volume average"
NO_ZONE = "no zone"
NO_STRUCTURE = "no structure"
HOURS_30D = 30 * 24

CATEGORIES = (
    "Structure",
    "Supply/demand",
    "Volume",
    "Futures",
    "Open interest",
    "Liquidations",
    "Higher timeframe",
)


def _row(category: str, state: str, alignment: str) -> dict[str, str]:
    if alignment not in (ALIGN_LONG, ALIGN_SHORT, ALIGN_NEUTRAL):
        raise ValueError(f"unknown alignment {alignment}")
    return {"category": category, "state": state, "alignment": alignment}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def population_z(values: Sequence[float]) -> float | None:
    """(latest - mean) / population stdev. None when fewer than 3 points or the scale is 0."""
    series = [float(v) for v in values]
    if len(series) < 3:
        return None
    mean = sum(series) / len(series)
    var = sum((v - mean) ** 2 for v in series) / len(series)
    if var <= 0:
        return 0.0
    return (series[-1] - mean) / math.sqrt(var)


def rvol_versus_prior_20(volumes: Sequence[float]) -> float | None:
    """Last closed bar's volume divided by the mean of the 20 closed bars before it."""
    series = []
    for raw in volumes:
        vol = _num(raw)
        if vol is None:
            return None
        series.append(vol)
    if len(series) < 21:
        return None
    window = series[-21:-1]
    avg = sum(window) / 20.0
    if avg <= 0:
        return None
    return series[-1] / avg


def _trend_side(raw: Any) -> str | None:
    if isinstance(raw, Mapping):
        raw = raw.get("trend") or raw.get("state")
    text = str(raw or "").upper()
    if not text or text in ("NONE", "N/A", "NULL"):
        return None
    if "BULL" in text or text in ("UP", "LONG"):
        return "up"
    if "BEAR" in text or text in ("DOWN", "SHORT"):
        return "down"
    if text == "FLAT":
        return "flat"
    return None


def _event(block: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(block, Mapping) or not block:
        return None
    direction = str(block.get("direction") or "")
    side = _trend_side(direction)
    return {
        "name": name,
        "side": side,
        "confirmed": str(block.get("state") or "").upper() == "CONFIRMED",
        "time": str(block.get("break_timestamp") or ""),
    }


def structure_from_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read trend and the later of BOS / CHoCH off a screener display payload."""
    body = payload if isinstance(payload, Mapping) else {}
    view = body.get("screener_setup") if isinstance(body.get("screener_setup"), Mapping) else body
    bos = _event(view.get("bos"), "BOS")
    choch = _event(view.get("choch"), "CHoCH")
    chosen = None
    if bos and choch:
        chosen = bos if bos["time"] >= choch["time"] else choch
    else:
        chosen = bos or choch
    return {
        "trend": _trend_side(view.get("trend") if view.get("trend") is not None else body.get("trend")),
        "event": None if chosen is None else chosen["name"],
        "event_side": None if chosen is None else chosen["side"],
        "confirmed": None if chosen is None else bool(chosen["confirmed"]),
    }


def structure_row(snapshot: Mapping[str, Any] | None) -> dict[str, str]:
    snap = snapshot or {}
    trend = snap.get("trend")
    event = snap.get("event")
    side = snap.get("event_side")
    confirmed = snap.get("confirmed")
    if trend is None and event is None:
        return _row("Structure", NO_STRUCTURE, ALIGN_NEUTRAL)
    confirm_text = "unconfirmed" if confirmed is False else ("confirmed" if confirmed else "unconfirmed")
    event_text = event if event else "none"
    state = f"trend {trend or 'unknown'}; last {event_text}; {confirm_text}"
    if confirmed and trend in ("up", "down") and side == trend:
        alignment = ALIGN_LONG if trend == "up" else ALIGN_SHORT
    else:
        alignment = ALIGN_NEUTRAL
    return _row("Structure", state, alignment)


def zone_row(snapshot: Mapping[str, Any] | None) -> dict[str, str]:
    snap = snapshot or {}
    kind = str(snap.get("kind") or "").lower()
    distance = _num(snap.get("distance_pct"))
    if kind not in ("supply", "demand") or distance is None:
        return _row("Supply/demand", NO_ZONE, ALIGN_NEUTRAL)
    alignment = ALIGN_LONG if kind == "demand" else ALIGN_SHORT
    return _row("Supply/demand", f"nearest {kind} zone, {distance:.2f}% away", alignment)


def volume_row(rvol: float | None) -> dict[str, str]:
    value = _num(rvol)
    if value is None:
        return _row("Volume", NO_VOLUME, ALIGN_NEUTRAL)
    return _row("Volume", f"RVOL {_fmt(value)} vs the 20-bar average", ALIGN_NEUTRAL)


def futures_row(rate: float | None, z_7d: float | None) -> dict[str, str]:
    rate_n, z_n = _num(rate), _num(z_7d)
    if rate_n is None or z_n is None:
        return _row("Futures", NO_FUNDING, ALIGN_NEUTRAL)
    if z_n >= 1:
        alignment = ALIGN_SHORT
    elif z_n <= -1:
        alignment = ALIGN_LONG
    else:
        alignment = ALIGN_NEUTRAL
    return _row("Futures", f"funding {_fmt(rate_n * 100, 4)}%; 7-day z-score {_fmt(z_n)}", alignment)


def oi_row(has_history: bool, change_24h_pct: float | None, z: float | None) -> dict[str, str]:
    if not has_history:
        return _row("Open interest", NO_OI, ALIGN_NEUTRAL)
    change = _num(change_24h_pct)
    score = _num(z)
    change_text = "n/a" if change is None else f"{change:.2f}%"
    score_text = "n/a" if score is None else f"{score:.2f}"
    return _row("Open interest", f"24h change {change_text}; z-score {score_text}", ALIGN_NEUTRAL)


def percentile(values: Sequence[float], pct: float) -> float | None:
    series = sorted(float(v) for v in values)
    if not series:
        return None
    if len(series) == 1:
        return series[0]
    rank = (len(series) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return series[low]
    weight = rank - low
    return series[low] * (1 - weight) + series[high] * weight


def liquidation_facts(hourly: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Hourly long/short notional. Fewer than 30 days is not history. The 60-minute window is not accepted."""
    rows = [r for r in (hourly or []) if isinstance(r, Mapping)]
    if len(rows) < HOURS_30D:
        return {"has_30d": False}
    ordered = rows[-HOURS_30D:]
    last_day = ordered[-24:]
    return {
        "has_30d": True,
        "long_24h": sum(float(r.get("long") or 0) for r in last_day),
        "short_24h": sum(float(r.get("short") or 0) for r in last_day),
        "p95_long": percentile([float(r.get("long") or 0) for r in ordered], 0.95),
        "p95_short": percentile([float(r.get("short") or 0) for r in ordered], 0.95),
    }


def liquidation_row(facts: Mapping[str, Any] | None) -> dict[str, str]:
    snap = facts or {}
    if not snap.get("has_30d"):
        return _row("Liquidations", NO_LIQ, ALIGN_NEUTRAL)
    long_usd = _num(snap.get("long_24h"))
    short_usd = _num(snap.get("short_24h"))
    p95_long = _num(snap.get("p95_long"))
    p95_short = _num(snap.get("p95_short"))
    if None in (long_usd, short_usd, p95_long, p95_short):
        return _row("Liquidations", NO_LIQ, ALIGN_NEUTRAL)
    long_hot = long_usd >= p95_long
    short_hot = short_usd >= p95_short
    if long_hot and not short_hot:
        alignment = ALIGN_SHORT
    elif short_hot and not long_hot:
        alignment = ALIGN_LONG
    else:
        alignment = ALIGN_NEUTRAL
    state = (
        f"24h long {long_usd:.0f} USD vs 30-day p95 {p95_long:.0f}; "
        f"24h short {short_usd:.0f} USD vs 30-day p95 {p95_short:.0f}"
    )
    return _row("Liquidations", state, alignment)


def htf_row(states: Mapping[str, Mapping[str, Any]] | None) -> dict[str, str]:
    states = states or {}
    parts = []
    trends = []
    usable = True
    for tf in ("4h", "1d"):
        st = states.get(tf) or {}
        if st.get("status") != "OK" or not st.get("trend"):
            usable = False
            parts.append(f"{tf} no closed-bar EMA")
            continue
        trends.append(st.get("trend"))
        parts.append(
            f"{tf} close vs EMA50 {st.get('close_vs_ema50')}, EMA20 vs EMA50 {st.get('ema20_vs_ema50')}"
        )
    if not usable:
        return _row("Higher timeframe", "; ".join(parts), ALIGN_NEUTRAL)
    if trends == ["UP", "UP"]:
        alignment = ALIGN_LONG
    elif trends == ["DOWN", "DOWN"]:
        alignment = ALIGN_SHORT
    else:
        alignment = ALIGN_NEUTRAL
    return _row("Higher timeframe", "; ".join(parts), alignment)


def build_checklist(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Rows, banner, and counts from an already-normalized snapshot. No I/O."""
    rows = [
        structure_row(snapshot.get("structure")),
        zone_row(snapshot.get("zone")),
        volume_row(_num(snapshot.get("rvol"))),
        futures_row(_num(snapshot.get("funding_rate")), _num(snapshot.get("funding_z"))),
        oi_row(
            bool(snapshot.get("oi_has_history")),
            _num(snapshot.get("oi_change_24h_pct")),
            _num(snapshot.get("oi_z")),
        ),
        liquidation_row(snapshot.get("liquidations")),
        htf_row(snapshot.get("htf")),
    ]
    if [r["category"] for r in rows] != list(CATEGORIES):
        raise RuntimeError("checklist categories drifted")
    counts = {ALIGN_LONG: 0, ALIGN_SHORT: 0, ALIGN_NEUTRAL: 0}
    for row in rows:
        counts[row["alignment"]] += 1
    return {
        "banner": BANNER,
        "timeframe": snapshot.get("timeframe"),
        "symbol": snapshot.get("symbol"),
        "rows": rows,
        "counts": counts,
        "read_only": True,
    }


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def oi_facts(samples: Sequence[tuple[datetime, float]] | None, now: datetime) -> dict[str, Any]:
    points = sorted((t, float(v)) for t, v in (samples or []))
    if not points:
        return {"has_history": False, "change_24h_pct": None, "z": None}
    last_t, last_v = points[-1]
    cutoff = last_t - timedelta(hours=24)
    prior = [v for t, v in points if t <= cutoff]
    change = None
    if prior and prior[-1] != 0:
        change = (last_v - prior[-1]) / prior[-1] * 100.0
    window = [v for t, v in points if t >= now - timedelta(days=7)]
    return {"has_history": True, "change_24h_pct": change, "z": population_z(window)}


def nearest_zone(zones: Sequence[Mapping[str, Any]] | None, price: float | None) -> dict[str, Any]:
    if price is None or price <= 0:
        return {"kind": None, "distance_pct": None}
    best: tuple[float, str] | None = None
    for zone in zones or []:
        label = str(zone.get("zone_type") or "").upper()
        if "DEMAND" in label:
            kind = "demand"
        elif "SUPPLY" in label:
            kind = "supply"
        else:
            continue
        try:
            mid = (float(zone.get("high")) + float(zone.get("low"))) / 2.0
        except (TypeError, ValueError):
            continue
        distance = abs(mid - price) / price * 100.0
        if best is None or distance < best[0]:
            best = (distance, kind)
    if best is None:
        return {"kind": None, "distance_pct": None}
    return {"kind": best[1], "distance_pct": best[0]}


def hourly_liquidation_history(symbol: str) -> list[dict[str, Any]] | None:
    """The C9 hourly recorder is the only accepted source.

    This build does not read the 60-minute in-memory window. Until a later branch
    supplies 30 days of hourly points, the checklist says history is missing.
    """
    del symbol
    return None


async def load_checklist(symbol: str, timeframe: str = "1h", now: datetime | None = None) -> dict[str, Any]:
    """Read-only gather. A missing source becomes that row's missing-data text. No writes."""
    now = _utc(now)
    sym = str(symbol or "").upper()
    tf = str(timeframe or "1h")
    snapshot: dict[str, Any] = {"symbol": sym, "timeframe": tf, "liquidations": {"has_30d": False}}
    try:
        from app.services.engine_store import engine_store
        from app.services.screener_display import display_payload
        from app.services.setup_signals import get_setup_signal_service

        stored = engine_store.get_setup_signal(sym)
        raw = stored if stored else get_setup_signal_service().analyze_symbol(sym)
        snapshot["structure"] = structure_from_payload(display_payload(raw, symbol=sym))
    except Exception:
        snapshot["structure"] = {}
    try:
        from app.services.ohlcv_store import ohlcv_store

        bars = list(ohlcv_store.get_candles_for_engine(sym, tf, include_open=False) or [])
        volumes = []
        price = None
        for bar in bars:
            if isinstance(bar, Mapping):
                if bar.get("volume") is not None:
                    volumes.append(float(bar["volume"]))
                if bar.get("close") is not None:
                    price = float(bar["close"])
        snapshot["rvol"] = rvol_versus_prior_20(volumes)
        from app.services.engine_store import engine_store

        snapshot["zone"] = nearest_zone(engine_store.zones.get(sym), price)
        from app.screener_tool.htf import htf_state

        snapshot["htf"] = {
            "4h": htf_state(list(ohlcv_store.get_candles_for_engine(sym, "4h", include_open=False) or []), "4h", now),
            "1d": htf_state(list(ohlcv_store.get_candles_for_engine(sym, "1d", include_open=False) or []), "1d", now),
        }
    except Exception:
        snapshot.setdefault("rvol", None)
        snapshot.setdefault("zone", {})
        snapshot.setdefault("htf", {})
    try:
        from app.grid_trading.settlements import load_grid_settlements

        got = await load_grid_settlements(sym, now - timedelta(days=7), now)
        points = []
        for row in got.get("rows") or []:
            if not isinstance(row, Mapping):
                continue
            rate = _num(row.get("rate"))
            stamp = row.get("time")
            if rate is None or stamp is None:
                continue
            points.append((str(stamp), rate))
        points.sort()
        rates = [rate for _, rate in points]
        if rates:
            snapshot["funding_rate"] = rates[-1]
            snapshot["funding_z"] = population_z(rates)
    except Exception:
        pass
    try:
        from app.services.persistence import persistence

        if persistence.active:
            history = await persistence.load_open_interest_history([sym], limit_per_symbol=500)
            facts = oi_facts(history.get(sym), now)
            snapshot["oi_has_history"] = facts["has_history"]
            snapshot["oi_change_24h_pct"] = facts["change_24h_pct"]
            snapshot["oi_z"] = facts["z"]
    except Exception:
        pass
    try:
        facts = liquidation_facts(hourly_liquidation_history(sym))
        snapshot["liquidations"] = facts
    except Exception:
        snapshot["liquidations"] = {"has_30d": False}
    return build_checklist(snapshot)
