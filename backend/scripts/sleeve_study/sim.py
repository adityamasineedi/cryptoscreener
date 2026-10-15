"""Sleeve-study portfolio simulator (research accounting; does not touch frozen fill code).

One chronological account. 15m master clock. Rules (fixed in the brief):
  * Signal known at its bar close; entry at the next bar's open (= the exec bar opening at signal close),
    plus adverse slippage. If that open is already through the stop the trade is skipped.
  * One open position per coin (sleeve). A coin is free again only after the exec bar that closed it ends.
  * Same-bar conflicts for a flat coin: higher timeframe wins; same timeframe -> side matching the regime,
    then the book's declared priority order.
  * Exits on exec bars (1h for 1h/4h/1d setups, 15m for 15m setups): bar opens through stop -> fill at open;
    low/high through stop -> stop; through target -> target; both in the same bar -> stop. All exits taker.
  * Risk = risk_frac x sleeve equity at entry, notional <= leverage x sleeve equity, and total open initial
    risk across the account <= account_risk_cap (USD). Below the exchange minimum notional -> skip.
  * Equity marked at every hour end; drawdown from marked account equity.
  * Month-end withdrawal (account level): excess of marked equity over the base is withdrawn, taken from the
    sleeves above their own base in proportion to their excess. Losses carry.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
TF_RANK = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
H1 = 3600
M15 = 900
FUND_SEC = 8 * 3600


@dataclass
class Costs:
    taker: float = 0.0004
    maker: float = 0.0002
    slip: float = 0.0002
    funding_flat_8h: float = 0.0  # sensitivity column: cost per 8h of notional held
    scale: float = 1.0           # 1.5 for the stress column (fees and slippage)

    @property
    def fee(self) -> float:
        return self.taker * self.scale

    @property
    def slp(self) -> float:
        return self.slip * self.scale


@dataclass
class Config:
    sleeve_base: float = 1000.0
    risk_frac: float = 0.02
    leverage: float = 2.0
    account_risk_cap: float | None = 200.0
    withdraw: bool = True
    costs: Costs = field(default_factory=Costs)
    min_notional: dict[str, float] = field(default_factory=dict)
    default_min_notional: float = 5.0


@dataclass
class Position:
    sig: dict
    coin: str
    side: int  # +1 long / -1 short
    qty: float
    entry_t: int
    fill: float
    stop: float
    target: float
    risk_usd: float
    exec_tf: str
    entry_fee: float
    funding: float = 0.0
    last_mark: float = 0.0
    trail: float = 0.0       # optional: trailing distance in price (k x ATR at signal); 0 = off
    best: float = 0.0        # best exec-bar close since entry (trail anchor)
    max_hold_s: int = 0      # optional: time stop in seconds; 0 = off


class Bars:
    """Per coin, per exec tf: open-time indexed OHLC arrays (epoch seconds)."""

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
        if i is None:
            return None
        return d["o"][i], d["h"][i], d["l"][i], d["c"][i]

    def last_close(self, coin: str, tf: str, t: int) -> float | None:
        """Close of the last exec bar that ended at or before t."""
        d = self.data.get((coin, tf))
        if d is None:
            return None
        sec = M15 if tf == "15m" else H1
        k = bisect_right(d["t"], t - sec) - 1
        return d["c"][k] if k >= 0 else None


def exec_tf_for(tf: str) -> str:
    return "15m" if tf == "15m" else "1h"


def _month_key(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m")


def simulate(signals: list[dict], bars: Bars, coins: list[str], start: datetime, end: datetime, cfg: Config,
             funding: dict[str, dict[int, float]] | None = None, priority: dict[str, int] | None = None) -> dict:
    """signals: dicts with sid, coin, tf, side ('LONG'/'SHORT'), signal_close (epoch s), stop, target_fn
    (callable fill->target price) or target (price), regime_bucket (str). Returns ledger + equity curve."""
    t0, t1 = int(start.timestamp()), int(end.timestamp())
    costs = cfg.costs
    priority = priority or {}
    by_t: dict[int, list[dict]] = defaultdict(list)
    for s in signals:
        if t0 <= s["signal_close"] < t1:
            by_t[s["signal_close"]].append(s)
    sleeve = {c: cfg.sleeve_base for c in coins}
    pos: dict[str, Position] = {}
    free_from: dict[str, int] = defaultdict(int)
    trades: list[dict] = []
    skips: list[dict] = []
    curve: list[tuple[int, float]] = []
    months: dict[str, dict[str, float]] = {}
    withdrawn_total = 0.0
    month_start_eq = sum(sleeve.values())
    cur_month = _month_key(t0)

    def mark_equity(t: int) -> float:
        eq = 0.0
        for c in coins:
            eq += sleeve[c]
            p = pos.get(c)
            if p is not None:
                px = bars.last_close(c, p.exec_tf, t)
                if px is None:
                    px = p.last_mark or p.fill
                p.last_mark = px
                eq += p.side * p.qty * (px - p.fill) - p.entry_fee + p.funding
        return eq

    def sleeve_mark(c: str, t: int) -> float:
        p = pos.get(c)
        if p is None:
            return sleeve[c]
        px = bars.last_close(c, p.exec_tf, t) or p.last_mark or p.fill
        return sleeve[c] + p.side * p.qty * (px - p.fill) - p.entry_fee + p.funding

    def close_pos(c: str, t_bar: int, bar_sec: int, px_raw: float, reason: str) -> None:
        p = pos.pop(c)
        px = px_raw * (1 - p.side * costs.slp)  # adverse slippage on the exit
        gross = p.side * p.qty * (px - p.fill)
        exit_fee = abs(p.qty * px) * costs.fee
        net = gross - p.entry_fee - exit_fee + p.funding
        sleeve[c] += net
        free_from[c] = t_bar + bar_sec
        trades.append({"sid": p.sig["sid"], "coin": c, "tf": p.sig["tf"], "side": "LONG" if p.side > 0 else "SHORT",
                       "signal_close": p.sig["signal_close"], "entry_t": p.entry_t, "exit_t": t_bar + bar_sec,
                       "fill": p.fill, "stop": p.stop, "target": p.target, "exit_px": px, "qty": p.qty,
                       "risk_usd": p.risk_usd, "gross": gross, "fees": p.entry_fee + exit_fee, "funding": p.funding,
                       "net": net, "r": net / p.risk_usd if p.risk_usd else 0.0, "reason": reason,
                       "regime": p.sig.get("regime_bucket")})

    def month_end(t: int) -> None:
        nonlocal withdrawn_total, month_start_eq, cur_month
        eq = mark_equity(t)
        w = 0.0
        if cfg.withdraw and eq > cfg.sleeve_base * len(coins) + 1e-9:
            w = eq - cfg.sleeve_base * len(coins)
            ex = {c: sleeve_mark(c, t) - cfg.sleeve_base for c in coins}
            pos_ex = {c: v for c, v in ex.items() if v > 0}
            tot = sum(pos_ex.values())
            for c, v in pos_ex.items():
                sleeve[c] -= w * v / tot
            withdrawn_total += w
        months[cur_month] = {"start_equity": month_start_eq, "end_equity_marked": eq, "net": eq - month_start_eq,
                             "withdrawn": w}
        month_start_eq = eq - w
        cur_month = _month_key(t)

    open_risk = lambda: sum(p.risk_usd for p in pos.values())  # noqa: E731

    # 15m clock only when a 15m setup is present; otherwise the 1h clock gives identical results faster
    step = M15 if any(exec_tf_for(s["tf"]) == "15m" for s in signals) else H1
    t = t0
    while t < t1:
        if _month_key(t) != cur_month:
            month_end(t)
        # ---- entries at t (signals whose bar closed at t) ----
        cands = by_t.get(t, [])
        if cands:
            per_coin: dict[str, list[dict]] = defaultdict(list)
            for s in cands:
                per_coin[s["coin"]].append(s)
            for c, lst in per_coin.items():
                if c not in sleeve:
                    continue
                if c in pos or free_from[c] > t:
                    for s in lst:
                        skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "coin_busy"})
                    continue
                lst.sort(key=lambda s: (-TF_RANK[s["tf"]],
                                        0 if s.get("regime_match", True) else 1,
                                        priority.get(s["sid"], 99)))
                top = lst[0]
                same = [s for s in lst if TF_RANK[s["tf"]] == TF_RANK[top["tf"]]]
                sides = {s["side"] for s in same}
                if len(sides) > 1:
                    matching = [s for s in same if s.get("regime_match", True)]
                    msides = {s["side"] for s in matching}
                    if len(msides) != 1:
                        for s in lst:
                            skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "side_conflict"})
                        continue
                    top = matching[0]
                for s in lst:
                    if s is not top:
                        skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "lost_conflict"})
                s = top
                etf = exec_tf_for(s["tf"])
                b = bars.bar(c, etf, t)
                if b is None:
                    skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "no_entry_bar"})
                    continue
                side = 1 if s["side"] == "LONG" else -1
                fill = b[0] * (1 + side * costs.slp)
                stop = float(s["stop"])
                rpu = side * (fill - stop)
                if rpu <= 0:
                    skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "open_through_stop"})
                    continue
                target = s["target_fn"](fill, stop) if "target_fn" in s else s.get("target")
                target = side * math.inf if target is None else float(target)  # None = no fixed target
                if side * (target - fill) <= 0:
                    skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "open_through_target"})
                    continue
                eq = sleeve[c]
                risk = cfg.risk_frac * eq
                if cfg.account_risk_cap is not None:
                    risk = min(risk, max(0.0, cfg.account_risk_cap - open_risk()))
                qty = risk / rpu
                qty = min(qty, cfg.leverage * eq / fill)
                notional = qty * fill
                if notional < cfg.min_notional.get(c, cfg.default_min_notional) or qty <= 0:
                    skips.append({"sid": s["sid"], "coin": c, "t": t, "why": "below_min_size"})
                    continue
                pos[c] = Position(sig=s, coin=c, side=side, qty=qty, entry_t=t, fill=fill, stop=stop, target=target,
                                  risk_usd=qty * rpu, exec_tf=etf, entry_fee=notional * costs.fee, last_mark=fill,
                                  trail=float(s.get("trail_atr") or 0.0) * float(s.get("atr") or 0.0), best=fill,
                                  max_hold_s=int(s.get("max_hold_s") or 0))
        # ---- exec bars starting at t ----
        for c in list(pos.keys()):
            p = pos[c]
            sec = M15 if p.exec_tf == "15m" else H1
            if t % sec:
                continue
            b = bars.bar(c, p.exec_tf, t)
            if b is None:
                continue
            o, h, l, cl = b
            first = t == p.entry_t
            # funding settlements at 00/08/16 UTC falling inside (t, t+sec] for held positions
            fs = ((t // FUND_SEC) + 1) * FUND_SEC
            while fs <= t + sec:
                if fs > p.entry_t:
                    notional = p.qty * cl
                    if funding is not None and c in funding:
                        rate = funding[c].get(fs)
                        if rate is not None:
                            p.funding -= p.side * notional * rate
                    if costs.funding_flat_8h:
                        p.funding -= abs(notional) * costs.funding_flat_8h
                fs += FUND_SEC
            if p.side > 0:
                if not first and o <= p.stop:
                    close_pos(c, t, sec, o, "stop_gap"); continue
                if l <= p.stop:
                    close_pos(c, t, sec, p.stop, "stop"); continue
                if not first and o >= p.target:
                    close_pos(c, t, sec, o, "target_gap"); continue
                if h >= p.target:
                    close_pos(c, t, sec, p.target, "target"); continue
            else:
                if not first and o >= p.stop:
                    close_pos(c, t, sec, o, "stop_gap"); continue
                if h >= p.stop:
                    close_pos(c, t, sec, p.stop, "stop"); continue
                if not first and o <= p.target:
                    close_pos(c, t, sec, o, "target_gap"); continue
                if l <= p.target:
                    close_pos(c, t, sec, p.target, "target"); continue
            p.last_mark = cl
            # optional time stop: exit at this exec bar's close once the hold limit is reached
            if p.max_hold_s and t + sec - p.entry_t >= p.max_hold_s:
                close_pos(c, t, sec, cl, "time"); continue
            # optional trail: ratchet the stop from the best close; applies from the next exec bar
            if p.trail > 0:
                if p.side > 0:
                    p.best = max(p.best, cl)
                    p.stop = max(p.stop, p.best - p.trail)
                else:
                    p.best = min(p.best, cl)
                    p.stop = min(p.stop, p.best + p.trail)
        t += step
        if t % H1 == 0:
            curve.append((t, mark_equity(t) + withdrawn_total))
    # window end: close what is open at the last mark
    for c in list(pos.keys()):
        p = pos[c]
        px = bars.last_close(c, p.exec_tf, t1) or p.last_mark
        sec = M15 if p.exec_tf == "15m" else H1
        close_pos(c, t1 - sec, sec, px, "window_end")
    month_end(t1)
    return {"trades": trades, "skips": skips, "curve": curve, "months": months, "withdrawn": withdrawn_total,
            "final_equity": sum(sleeve.values())}


def max_drawdown(curve: list[tuple[int, float]]) -> float:
    """Peak-to-trough of marked equity; the curve already adds back cumulative withdrawals."""
    peak = -math.inf
    mdd = 0.0
    for _, e in curve:
        peak = max(peak, e)
        mdd = max(mdd, peak - e)
    return mdd
