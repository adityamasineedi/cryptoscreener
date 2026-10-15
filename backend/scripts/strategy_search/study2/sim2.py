"""Study-2 account simulator. Copy of scripts/sleeve_study/sim.py (unchanged original) extended per PROTOCOL.md §5-§7.
Every behavioural difference from sim.py is listed in SIM_CHANGES.md and covered by test_sim2.py.

Clock: 15m steps when any 15m-exec signal is present, else 1h. Marks on the 1h grid.
Signals (dicts): sid, coin, tf, side 'LONG'/'SHORT', signal_close (epoch s; entry at the exec bar opening then),
  stop (price), and optionally: target (price) | target_r (R multiple of |fill-stop|), trail_k + atr (chandelier from
  best close), partial_r + partial_frac (+ be_after_partial), max_hold_s, regime (label), regime_match (bool),
  priority (int, lower first), pyramid (bool).
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
TF_RANK = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
H1, M15, FUND_SEC, DAY, WEEK = 3600, 900, 8 * 3600, 86400, 7 * 86400
MONDAY_OFFSET = 4 * DAY  # epoch 0 was a Thursday; Monday 00:00 UTC = t where (t - 4d) % 7d == 0


@dataclass
class Costs:
    taker: float = 0.0004
    slip: float = 0.0002
    funding_8h: float = 0.0001   # hypothetical, both sides pay, per 00/08/16 UTC boundary
    scale: float = 1.0           # 1.5 = stress column (fees, execution and funding)

    @property
    def fee(self) -> float:
        return self.taker * self.scale

    @property
    def slp(self) -> float:
        return self.slip * self.scale

    @property
    def fund(self) -> float:
        return self.funding_8h * self.scale


@dataclass
class Config:
    sleeve_base: float = 1000.0
    risk_frac: float = 0.02
    leverage: float = 2.0
    risk_cap_abs: float | None = 200.0
    risk_cap_frac: float = 0.02
    daily_pause: float | None = 0.02
    weekly_pause: float | None = 0.05
    dd_stop: float | None = 0.20
    withdraw: bool = True
    max_adds: int = 2
    costs: Costs = field(default_factory=Costs)
    lot_step: dict[str, float] = field(default_factory=dict)
    min_notional: float = 5.0


class Bars:
    """Per (coin, exec tf): open-time indexed OHLC arrays (epoch seconds)."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], dict[str, Any]] = {}

    def put(self, coin: str, tf: str, candles: list[dict]) -> None:
        t = [int(c["time"].timestamp()) for c in candles]
        self.data[(coin, tf)] = {"t": t, "idx": {x: i for i, x in enumerate(t)},
                                 "o": [c["open"] for c in candles], "h": [c["high"] for c in candles],
                                 "l": [c["low"] for c in candles], "c": [c["close"] for c in candles]}

    def bar(self, coin: str, tf: str, t: int):
        d = self.data.get((coin, tf))
        if d is None:
            return None
        i = d["idx"].get(t)
        return None if i is None else (d["o"][i], d["h"][i], d["l"][i], d["c"][i])

    def last_close(self, coin: str, tf: str, t: int) -> float | None:
        """Close of the last bar of this tf that ended at or before t."""
        d = self.data.get((coin, tf))
        if d is None:
            return None
        sec = M15 if tf == "15m" else H1
        k = bisect_right(d["t"], t - sec) - 1
        return d["c"][k] if k >= 0 else None


def exec_tf_for(tf: str) -> str:
    return "15m" if tf == "15m" else "1h"


def month_key(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m")


@dataclass
class Pos:
    sig: dict
    coin: str
    side: int
    qty: float
    avg: float          # average fill incl. execution allowance
    avg_raw: float      # average raw price (gross P&L reference)
    stop: float
    target: float
    init_rpu: float     # |fill - stop| at entry (price units, for R levels)
    risk_alloc: float   # executed risk at entry (USD) = the position's risk allowance
    entry_t: int
    exec_tf: str
    best: float
    trail: float = 0.0
    partial_px: float | None = None
    partial_frac: float = 0.0
    be_after_partial: bool = True
    partial_done: bool = False
    max_hold_s: int = 0
    adds: int = 0
    pending: str | None = None      # 'time' | 'forced' | 'reduce'
    reduce_frac: float = 0.0
    gross: float = 0.0
    fees: float = 0.0
    execc: float = 0.0
    funding: float = 0.0
    fills: list = field(default_factory=list)


def simulate(signals: list[dict], bars: Bars, sleeves: list[str], start: datetime, end: datetime, cfg: Config) -> dict:
    """sleeves: one $sleeve_base sleeve per name; names without bars/signals are cash sleeves."""
    t0, t1 = int(start.timestamp()), int(end.timestamp())
    c = cfg.costs
    by_t: dict[int, list[dict]] = defaultdict(list)
    for s in signals:
        if t0 <= s["signal_close"] < t1 and s["coin"] in sleeves:
            by_t[s["signal_close"]].append(s)
    base = cfg.sleeve_base * len(sleeves)
    cash = {s: cfg.sleeve_base for s in sleeves}
    pos: dict[str, Pos] = {}
    busy_until: dict[str, int] = defaultdict(int)
    trades, skips, curve, wlog, events = [], [], [], [], []
    months: dict[str, dict] = {}
    st = {"withdrawn": 0.0, "stopped": False, "stop_t": None, "peak": base, "max_dd": 0.0, "max_dd_pct": 0.0,
          "day_start": base, "week_start": base, "pause_day": False, "pause_week": False,
          "cur_month": month_key(t0), "month_start_eq": base, "month_withdraw_due": 0.0, "pause_hours": 0}

    def px_mark(p: Pos, t: int) -> float:
        return bars.last_close(p.coin, p.exec_tf, t) or p.avg_raw

    def unreal(p: Pos, t: int) -> float:
        # entry execution cost is already out of cash, so mark against the raw entry price
        return p.side * p.qty * (px_mark(p, t) - p.avg_raw)

    def sleeve_eq(s: str, t: int) -> float:
        p = pos.get(s)
        return cash[s] + (unreal(p, t) if p else 0.0)

    def account_eq(t: int) -> float:
        return sum(sleeve_eq(s, t) for s in sleeves)

    def open_risk_of(p: Pos) -> float:
        return max(0.0, p.qty * p.side * (p.avg - p.stop) + p.qty * p.stop * (c.fee + c.slp))

    def open_risk() -> float:
        return sum(open_risk_of(p) for p in pos.values())

    def risk_cap(t: int) -> float:
        cap = cfg.risk_cap_frac * account_eq(t)
        return min(cfg.risk_cap_abs, cap) if cfg.risk_cap_abs is not None else cap

    def lot(coin: str, q: float) -> float:
        step = cfg.lot_step.get(coin)
        if not step:
            return q
        return math.floor(q / step + 1e-9) * step

    def fill_out(p: Pos, q: float, raw: float, t: int, why: str) -> None:
        """Reduce position by q at raw price (taker + execution allowance)."""
        px = raw * (1 - p.side * c.slp)
        g = p.side * q * (raw - p.avg_raw)
        ex = q * raw * c.slp
        fee = q * px * c.fee
        p.gross += g; p.execc += ex; p.fees += fee
        cash[p.coin] += g - ex - fee
        # entry-side execution cost was charged at entry; keep avg_raw for remaining qty
        p.qty -= q
        p.fills.append((t, -q, raw, why))

    def close(p: Pos, t_bar: int, sec: int, raw: float, why: str) -> None:
        fill_out(p, p.qty, raw, t_bar, why)
        pos.pop(p.coin, None)
        busy_until[p.coin] = t_bar + sec
        net = p.gross - p.fees - p.execc - p.funding
        trades.append({"sid": p.sig["sid"], "coin": p.coin, "tf": p.sig["tf"], "side": "LONG" if p.side > 0 else "SHORT",
                       "signal_close": p.sig["signal_close"], "entry_t": p.entry_t, "exit_t": t_bar + sec,
                       "fill": p.avg, "stop0": p.sig["stop"], "risk_usd": p.risk_alloc, "adds": p.adds,
                       "partial": p.partial_done, "gross": p.gross, "fees": p.fees, "exec": p.execc,
                       "funding": p.funding, "net": net, "r": net / p.risk_alloc if p.risk_alloc else 0.0,
                       "reason": why, "regime": p.sig.get("regime"), "hold_h": (t_bar + sec - p.entry_t) / 3600})

    def mark(t: int) -> None:
        eq = account_eq(t)
        cf = eq + st["withdrawn"]
        st["peak"] = max(st["peak"], cf)
        dd = st["peak"] - cf
        if dd > st["max_dd"]:
            st["max_dd"] = dd
        st["max_dd_pct"] = max(st["max_dd_pct"], dd / st["peak"] if st["peak"] > 0 else 0.0)
        curve.append((t, eq, cf))
        if cfg.dd_stop is not None and not st["stopped"] and dd >= cfg.dd_stop * st["peak"]:
            st["stopped"], st["stop_t"] = True, t
            events.append({"t": t, "event": "dd_stop", "dd": dd, "peak": st["peak"]})
            for p in pos.values():
                p.pending = "forced"
        if cfg.daily_pause is not None and not st["pause_day"] and eq <= st["day_start"] * (1 - cfg.daily_pause):
            st["pause_day"] = True
            events.append({"t": t, "event": "daily_pause", "eq": eq, "day_start": st["day_start"]})
        if cfg.weekly_pause is not None and not st["pause_week"] and eq <= st["week_start"] * (1 - cfg.weekly_pause):
            st["pause_week"] = True
            events.append({"t": t, "event": "weekly_pause", "eq": eq, "week_start": st["week_start"]})

    def month_end(t: int) -> None:
        eq = account_eq(t)
        m = st["cur_month"]
        due = eq - base if (cfg.withdraw and eq > base + 1e-9) else 0.0
        paid = 0.0
        if due > 0:
            meq = {s: max(0.0, sleeve_eq(s, t)) for s in sleeves}
            tot = sum(meq.values())
            for s in sleeves:
                d = due * meq[s] / tot if tot > 0 else 0.0
                if d <= 0:
                    continue
                p = pos.get(s)
                if p:
                    u = unreal(p, t)
                    free = cash[s] - p.qty * px_mark(p, t) / cfg.leverage + min(0.0, u)
                else:
                    free = cash[s]
                pay = min(d, max(0.0, free))
                cash[s] -= pay
                paid += pay
                if d - pay > 1e-6 and p:
                    notional = p.qty * px_mark(p, t)
                    p.pending = p.pending or "reduce"
                    p.reduce_frac = min(1.0, (d - pay) * cfg.leverage / notional) if notional > 0 else 0.0
                    p.reduce_due = d - pay  # type: ignore[attr-defined]
                wlog.append({"t": t, "sleeve": s, "due": d, "paid": pay})
        st["withdrawn"] += paid
        months[m] = {"start_equity": st["month_start_eq"], "end_equity": eq, "net": eq - st["month_start_eq"],
                     "withdrawn": paid, "deferred": max(0.0, due - paid)}
        st["month_start_eq"] = eq - paid
        st["last_month"] = m
        st["cur_month"] = month_key(t)

    def pay_after_reduce(p_coin: str, due: float, t: int) -> None:
        p = pos.get(p_coin)
        if p:
            free = cash[p_coin] - p.qty * px_mark(p, t) / cfg.leverage + min(0.0, unreal(p, t))
        else:
            free = cash[p_coin]
        pay = min(due, max(0.0, free))
        cash[p_coin] -= pay
        st["withdrawn"] += pay
        mk = st.get("last_month")
        if mk in months:
            months[mk]["withdrawn"] += pay
            months[mk]["deferred"] = max(0.0, months[mk]["deferred"] - pay)
        # a cash flow, not a trading loss: move every reference level with it
        st["month_start_eq"] -= pay
        st["day_start"] -= pay
        st["week_start"] -= pay
        wlog.append({"t": t, "sleeve": p_coin, "due": due, "paid": pay, "after_reduction": True})

    def try_entry(s: dict, t: int) -> None:
        coin = s["coin"]
        side = 1 if s["side"] == "LONG" else -1
        etf = exec_tf_for(s["tf"])
        b = bars.bar(coin, etf, t)
        if b is None:
            skips.append((s["sid"], coin, t, "no_entry_bar")); return
        raw = b[0]
        fill = raw * (1 + side * c.slp)
        stop = float(s["stop"])
        dist = side * (fill - stop)
        if dist <= 0:
            skips.append((s["sid"], coin, t, "fill_through_stop")); return
        if s.get("target_r"):
            target = fill + side * s["target_r"] * dist
        elif s.get("target") is not None:
            target = float(s["target"])
            if side * (target - fill) <= 0:
                skips.append((s["sid"], coin, t, "fill_through_target")); return
        else:
            target = side * math.inf
        rpu = dist + c.fee * fill + (c.fee + c.slp) * stop
        eq = cash[coin]
        budget = cfg.risk_frac * eq
        qty = budget / rpu
        qty = min(qty, cfg.leverage * eq / fill)
        room = risk_cap(t) - open_risk()
        if room <= 0:
            skips.append((s["sid"], coin, t, "account_risk_cap")); return
        qty = min(qty, room / rpu)
        qty = lot(coin, qty)
        if qty <= 0 or qty * fill < cfg.min_notional:
            skips.append((s["sid"], coin, t, "below_min_size")); return
        fee = qty * fill * c.fee
        ex = qty * raw * c.slp
        cash[coin] -= fee + ex
        p = Pos(sig=s, coin=coin, side=side, qty=qty, avg=fill, avg_raw=raw, stop=stop, target=target, init_rpu=dist,
                risk_alloc=qty * rpu, entry_t=t, exec_tf=etf, best=raw,
                trail=float(s.get("trail_k") or 0.0) * float(s.get("atr") or 0.0),
                max_hold_s=int(s.get("max_hold_s") or 0), be_after_partial=bool(s.get("be_after_partial", True)))
        p.fees, p.execc = fee, ex
        if s.get("partial_r"):
            p.partial_px = fill + side * float(s["partial_r"]) * dist
            p.partial_frac = float(s.get("partial_frac") or 0.5)
        p.fills.append((t, qty, raw, "entry"))
        pos[coin] = p

    def try_add(p: Pos, s: dict, t: int) -> None:
        b = bars.bar(p.coin, p.exec_tf, t)
        if b is None:
            skips.append((s["sid"], p.coin, t, "add_no_bar")); return
        raw = b[0]
        fill = raw * (1 + p.side * c.slp)
        if p.side * (fill - p.avg) < p.init_rpu:
            skips.append((s["sid"], p.coin, t, "add_not_1R")); return
        new_stop = max(p.stop, float(s["stop"])) if p.side > 0 else min(p.stop, float(s["stop"]))
        if p.side * (fill - new_stop) <= 0:
            skips.append((s["sid"], p.coin, t, "add_fill_through_stop")); return
        rpu = p.side * (fill - new_stop) + c.fee * fill + (c.fee + c.slp) * new_stop
        old_stop = p.stop
        p.stop = new_stop
        existing = open_risk_of(p)
        allow = p.risk_alloc - existing
        room = risk_cap(t) - open_risk()
        q = min(allow, room) / rpu if min(allow, room) > 0 else 0.0
        eq = sleeve_eq(p.coin, t)
        q = min(q, max(0.0, cfg.leverage * eq / fill - p.qty))
        q = lot(p.coin, q)
        if q <= 0 or q * fill < cfg.min_notional:
            p.stop = old_stop
            skips.append((s["sid"], p.coin, t, "add_no_room")); return
        fee = q * fill * c.fee
        ex = q * raw * c.slp
        cash[p.coin] -= fee + ex
        p.fees += fee; p.execc += ex
        p.avg = (p.avg * p.qty + fill * q) / (p.qty + q)
        p.avg_raw = (p.avg_raw * p.qty + raw * q) / (p.qty + q)
        p.qty += q
        p.adds += 1
        p.fills.append((t, q, raw, "add"))

    has15 = any(exec_tf_for(s["tf"]) == "15m" for s in signals)
    step = M15 if has15 else H1
    t = t0
    while t < t1:
        # --- calendar boundaries (before anything trades at t)
        if month_key(t) != st["cur_month"]:
            month_end(t)
        if t % DAY == 0:
            st["day_start"] = account_eq(t)
            st["pause_day"] = False
            if (t - MONDAY_OFFSET) % WEEK == 0:
                st["week_start"] = st["day_start"]
                st["pause_week"] = False
        # --- hypothetical funding at 00/08/16 UTC for positions opened before t
        if t % FUND_SEC == 0 and c.fund:
            for p in pos.values():
                if p.entry_t < t:
                    f = p.qty * px_mark(p, t) * c.fund
                    p.funding += f
                    cash[p.coin] -= f
        # --- entries / additions for signals whose bar closed at t
        cands = by_t.get(t)
        if cands:
            if st["stopped"] or st["pause_day"] or st["pause_week"]:
                for s in cands:
                    skips.append((s["sid"], s["coin"], t, "stopped" if st["stopped"] else "loss_pause"))
            else:
                per: dict[str, list[dict]] = defaultdict(list)
                for s in cands:
                    per[s["coin"]].append(s)
                for coin, lst in per.items():
                    p = pos.get(coin)
                    if p is not None:
                        added = False
                        for s in lst:
                            if (not added and s.get("pyramid") and s["sid"] == p.sig["sid"] and p.adds < cfg.max_adds
                                    and (1 if s["side"] == "LONG" else -1) == p.side and not p.pending):
                                try_add(p, s, t); added = True
                            else:
                                skips.append((s["sid"], coin, t, "coin_busy"))
                        continue
                    if busy_until[coin] > t:
                        for s in lst:
                            skips.append((s["sid"], coin, t, "coin_busy"))
                        continue
                    top_rank = max(TF_RANK[s["tf"]] for s in lst)
                    top = [s for s in lst if TF_RANK[s["tf"]] == top_rank]
                    sides = {s["side"] for s in top}
                    if len(sides) > 1:
                        compat = {s["side"] for s in top if s.get("regime_match")}
                        if len(compat) != 1:
                            for s in lst:
                                skips.append((s["sid"], coin, t, "side_conflict"))
                            continue
                        top = [s for s in top if s["side"] in compat]
                    top.sort(key=lambda s: s.get("priority", 99))
                    pick = top[0]
                    for s in lst:
                        if s is not pick:
                            skips.append((s["sid"], coin, t, "lost_conflict"))
                    try_entry(pick, t)
        # --- exec bars opening at t
        for coin in list(pos.keys()):
            p = pos[coin]
            sec = M15 if p.exec_tf == "15m" else H1
            if t % sec:
                continue
            b = bars.bar(coin, p.exec_tf, t)
            if b is None:
                continue
            o, h, l, cl = b
            first = t == p.entry_t
            if p.pending in ("forced", "time"):
                close(p, t, sec, o, p.pending); continue
            if p.pending == "reduce":
                q = lot(coin, p.qty * p.reduce_frac)
                due = getattr(p, "reduce_due", 0.0)
                p.pending = None
                if q >= p.qty - 1e-12:
                    close(p, t, sec, o, "withdraw_reduce")
                    pay_after_reduce(coin, due, t); continue
                if q > 0:
                    fill_out(p, q, o, t, "withdraw_reduce")
                pay_after_reduce(coin, due, t)
            s_ = p.side
            if not first and s_ * (o - p.stop) <= 0:
                close(p, t, sec, o, "stop_gap"); continue
            if s_ * (l if s_ > 0 else h) <= s_ * p.stop:
                close(p, t, sec, p.stop, "stop"); continue
            if p.partial_px is not None and not p.partial_done:
                hit_open = not first and s_ * (o - p.partial_px) >= 0
                if hit_open or s_ * ((h if s_ > 0 else l) - p.partial_px) >= 0:
                    q = lot(coin, p.qty * p.partial_frac)
                    if 0 < q < p.qty:
                        fill_out(p, q, o if hit_open else p.partial_px, t, "partial")
                    p.partial_done = True
            if not first and s_ * (o - p.target) >= 0:
                close(p, t, sec, o, "target_gap"); continue
            if s_ * ((h if s_ > 0 else l) - p.target) >= 0:
                close(p, t, sec, p.target, "target"); continue
            # completed-bar updates, effective from the next bar
            if p.partial_done and p.be_after_partial:
                p.stop = max(p.stop, p.avg) if s_ > 0 else min(p.stop, p.avg)
            if p.trail > 0:
                p.best = max(p.best, cl) if s_ > 0 else min(p.best, cl)
                p.stop = max(p.stop, p.best - p.trail) if s_ > 0 else min(p.stop, p.best + p.trail)
            if p.max_hold_s and t + sec - p.entry_t >= p.max_hold_s:
                p.pending = "time"
        t += step
        if t % H1 == 0:
            mark(t)
            if st["pause_day"] or st["pause_week"]:
                st["pause_hours"] += 1
    for coin in list(pos.keys()):
        p = pos[coin]
        sec = M15 if p.exec_tf == "15m" else H1
        close(p, t1 - sec, sec, px_mark(p, t1), "window_end")
    month_end(t1)
    return {"trades": trades, "skips": skips, "curve": curve, "months": months, "withdrawals": wlog,
            "withdrawn": st["withdrawn"], "final_equity": sum(cash.values()), "base": base, "events": events,
            "max_dd": st["max_dd"], "max_dd_pct": st["max_dd_pct"], "stopped": st["stopped"], "stop_t": st["stop_t"],
            "pause_hours": st["pause_hours"], "start": t0, "end": t1, "sleeves": list(sleeves)}
