"""Pure alert maths and text: entry zone, targets, leverage, R:R, tick rounding, cost filter, messages.

No I/O. Every number comes from ``AlertConfig``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from app.alerts.config import AlertConfig, timeframe_minutes

# Skip codes a plan can return.
NO_TICK_SIZE = "NO_TICK_SIZE"
NO_MARKET_DATA = "NO_MARKET_DATA"
NO_ENGINE_STOP = "NO_ENGINE_STOP"
INVALID_STOP = "INVALID_STOP"
NO_TESTED_TARGET = "NO_TESTED_TARGET"
INVALID_TARGET = "INVALID_TARGET"


def utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def base_asset(symbol: str) -> str:
    from app.services.telegram_alerts import _base_asset

    return _base_asset(symbol)


@dataclass
class Signal:
    """One closed-bar signal from a paper book or forward test, before any alert rule is applied."""

    source: str
    strategy_id: str
    symbol: str
    side: str
    timeframe: str
    signal_bar: datetime
    decided_at: datetime
    stop: float | None = None
    tested_target: float | None = None
    signal_close: float | None = None
    atr: float | None = None
    journal_ref: dict[str, Any] | None = None
    position_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # False when the strategy's target has no backtest behind it (the screener): T2 shows "(untested)".
    target_tested: bool = True

    def __post_init__(self) -> None:
        self.symbol = str(self.symbol).upper()
        self.side = str(self.side).upper()
        self.signal_bar = utc(self.signal_bar)
        self.decided_at = utc(self.decided_at)

    @property
    def bar_close(self) -> datetime:
        return self.signal_bar + timedelta(minutes=timeframe_minutes(self.timeframe))

    @property
    def dedup_key(self) -> str:
        return f"{self.strategy_id}|{self.symbol}|{self.side}|{self.signal_bar.isoformat()}"

    @property
    def alert_id(self) -> str:
        return hashlib.sha1(self.dedup_key.encode("utf-8")).hexdigest()[:12]


# --- prices -----------------------------------------------------------------------------------
def tick_decimals(tick: float) -> int:
    exp = Decimal(str(tick)).normalize().as_tuple().exponent
    return max(0, -int(exp))


def round_to_tick(price: float, tick: float) -> float:
    t = Decimal(str(tick))
    steps = (Decimal(str(price)) / t).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(steps * t)


def fmt_price(price: float, tick: float) -> str:
    return f"{round_to_tick(price, tick):.{tick_decimals(tick)}f}"


def fmt_num(value: float, decimals: int) -> str:
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


# --- plan -------------------------------------------------------------------------------------
def build_plan(sig: Signal, cfg: AlertConfig) -> tuple[dict[str, Any] | None, str | None]:
    """The alert plan for a signal, or (None, skip code). The cost filter is a flag on the plan."""
    tick = cfg.tick_for(sig.symbol)
    if tick is None:
        return None, NO_TICK_SIZE
    if sig.stop is None:
        return None, NO_ENGINE_STOP
    if sig.signal_close is None or sig.atr is None or sig.atr <= 0:
        return None, NO_MARKET_DATA
    long = sig.side == "LONG"
    sgn = 1.0 if long else -1.0
    close = float(sig.signal_close)
    stop = float(sig.stop)
    dist = (close - stop) * sgn
    if dist <= 0:
        return None, INVALID_STOP

    ez = cfg.section("entry_zone")
    far = close - sgn * float(ez["atr_multiple"]) * float(sig.atr)
    # Never closer to the SL than this fraction of the stop distance.
    limit = stop + sgn * float(ez["min_sl_gap_fraction_of_stop"]) * dist
    capped = (far < limit) if long else (far > limit)
    if capped:
        far = limit
    mid = (close + far) / 2.0
    r_unit = (mid - stop) * sgn

    tg = cfg.section("targets")
    tested = True
    if sig.tested_target is None:
        nt = cfg.section("no_tested_target")
        if str(nt.get("action")) != "fallback_r":
            return None, NO_TESTED_TARGET
        t2 = mid + sgn * float(nt["fallback_t2_r"]) * r_unit
        tested = False
    else:
        t2 = float(sig.tested_target)
        tested = bool(sig.target_tested)
    if (t2 - mid) * sgn <= 0:
        return None, INVALID_TARGET
    t1 = mid + sgn * float(tg["t1_r"]) * r_unit
    t3 = t2 + sgn * float(tg["t3_r_beyond_t2"]) * r_unit
    t4 = t2 + sgn * float(tg["t4_r_beyond_t2"]) * r_unit
    rr_raw = (t2 - mid) * sgn / r_unit
    rr = round(rr_raw, int(cfg.raw["rr_decimals"]))

    rk = cfg.section("risk")
    risk_pct = float(rk["risk_pct"])
    stop_pct = r_unit / mid * 100.0
    needed = risk_pct / stop_pct
    lev_raw = math.ceil(needed - 1e-9)
    lev = int(min(max(lev_raw, int(rk["min_leverage"])), int(rk["max_leverage"])))
    cap_bound = lev_raw > int(rk["max_leverage"])
    # Sized by risk. When the cap binds, the full account at the cap risks less than risk_pct: show that.
    real_risk = lev * stop_pct if cap_bound else risk_pct

    rt = cfg.round_trip_cost()
    cost_floor = float(cfg.raw["cost"]["multiple"]) * rt
    stop_frac = r_unit / mid

    zone_lo, zone_hi = (far, close) if long else (close, far)
    plan = {
        "tick": tick,
        "signal_close": close,
        "atr": float(sig.atr),
        "zone_capped_by_sl": bool(capped),
        "zone_lo": zone_lo,
        "zone_hi": zone_hi,
        "entry_mid": mid,
        "stop": stop,
        "r_unit": r_unit,
        "targets": [t1, t2, t3, t4],
        "t2_tested": tested,
        "rr_tested": rr,
        "rr_raw": rr_raw,
        "t2_below_min_r": rr_raw + 1e-9 < float(tg["min_tested_t2_r"]),
        "stop_pct": stop_pct,
        "leverage": lev,
        "leverage_needed": needed,
        "leverage_cap_bound": cap_bound,
        "risk_pct_shown": real_risk,
        "round_trip_cost": rt,
        "stop_frac": stop_frac,
        "cost_floor_frac": cost_floor,
        "cost_skip": stop_frac < cost_floor,
        "rounded": {
            "zone_a": fmt_price(close, tick),
            "zone_b": fmt_price(far, tick),
            "zone_lo": fmt_price(zone_lo, tick),
            "zone_hi": fmt_price(zone_hi, tick),
            "sl": fmt_price(stop, tick),
            "t": [fmt_price(x, tick) for x in (t1, t2, t3, t4)],
        },
    }
    return plan, None


# --- text -------------------------------------------------------------------------------------
def render_alert(
    sig: Signal, plan: Mapping[str, Any], status: str, cfg: AlertConfig, *, sent_at: datetime | None = None
) -> str:
    rd = plan["rounded"]
    if sig.side == "LONG":
        a, b = rd["zone_a"], rd["zone_b"]  # signal close down to the far edge
    else:
        a, b = rd["zone_lo"], rd["zone_hi"]  # low to high
    t1, t2, t3, t4 = rd["t"]
    mark = "(tested)" if plan["t2_tested"] else "(untested)"
    rr = f"{plan['rr_tested']:.{int(cfg.raw['rr_decimals'])}f}"
    risk = fmt_num(float(plan["risk_pct_shown"]), int(cfg.raw["risk"]["display_decimals"]))
    lines = [
            f"✅ {cfg.label_for(sig.timeframe)} TRADE - {base_asset(sig.symbol)}",
            f"🏮 TYPE - {sig.side}",
            "",
            f"👉 ENTRY - ${a} - ${b}",
            f"👉 TARGET - ${t1}, ${t2} {mark}, ${t3} & ${t4}+",
            f"👉 SL - ${rd['sl']}",
            "",
            f"🚨 LEVERAGE - {plan['leverage']}x",
            f"📊 R:R - 1:{rr} | Risk {risk}% of account",
            f"🧪 {status} · {sig.strategy_id} · {sig.timeframe}",
            f"⏱ Valid {int(cfg.raw['validity_bars'])} bars · Not financial advice",
    ]
    if sent_at is not None and cfg.is_forward_runner(sig.source):
        # Owner ruling D51b: forward-runner records land after the bar close; show how long after.
        lines.append(f"⏱ signal +{int((utc(sent_at) - sig.bar_close).total_seconds() // 60)}m")
    return "\n".join(lines)


def render_followup(event: Mapping[str, Any], tracker: Mapping[str, Any]) -> str:
    coin = base_asset(str(tracker["symbol"]))
    side = str(tracker["side"])
    tick = float(tracker["tick"])
    kind = str(event["kind"])
    price = event.get("price")
    p = fmt_price(float(price), tick) if price is not None else ""
    r = event.get("r")
    rs = f"{float(r):+.1f}R" if r is not None else ""
    if kind == "fill":
        return f"✅ ENTRY FILLED - {coin} {side} @ ${p}"
    if kind == "target":
        return f"🎯 T{int(event['target'])} HIT - {coin} @ ${p} ({rs})"
    if kind == "sl":
        return f"🛑 SL HIT - {coin} @ ${p} ({rs})"
    if kind == "expired":
        return "❌ NOT FILLED - expired"
    if kind == "final":
        return f"🏁 RESULT - {coin} {side} {rs} (tested exit: {event['exit']})"
    raise ValueError(f"unknown follow-up kind {kind!r}")


def render_summary(day: str, groups: Mapping[str, Mapping[str, Any]], *, title: str = "DAILY SUMMARY") -> str:
    def tot(key: str) -> float:
        return sum(float(g.get(key) or 0) for g in groups.values())

    lines = [
        f"📅 {title} - {day} UTC",
        f"Alerts sent: {int(tot('sent'))} | Filled: {int(tot('filled'))}",
        f"Wins: {int(tot('wins'))} | Losses: {int(tot('losses'))} | Total R: {tot('total_r'):+.1f}",
    ]
    for name in sorted(groups):
        g = groups[name]
        lines.append(
            f"{name}: sent {int(g.get('sent') or 0)}, filled {int(g.get('filled') or 0)}, "
            f"W/L {int(g.get('wins') or 0)}/{int(g.get('losses') or 0)}, R {float(g.get('total_r') or 0):+.1f}"
            f", not sent {int(g.get('not_sent') or 0)}"
        )
    return "\n".join(lines)


def atr_at(bars: Sequence[Mapping[str, Any]], period: int) -> float | None:
    """Wilder ATR(period) at the last bar, with the app's own ATR."""
    from app.engines.mtf.indicators import atr

    if len(bars) < period + 1:
        return None
    return atr(
        [float(b["high"]) for b in bars],
        [float(b["low"]) for b in bars],
        [float(b["close"]) for b in bars],
        period,
    )
