"""Portfolio backtest: shared equity, one position per symbol, costs on every fill.

Bar order at each timestamp, across all symbols:
  1. exits signalled at the previous close fill at this open
  2. entries signalled at the previous close fill at this open (caps checked here)
  3. ATR stops fill intrabar (at the open if price gapped through), like an
     exchange-side stop-market order that always fills
  4. exit and entry rules are evaluated on this close
  5. equity is marked to market; daily loss limit and kill switch are checked
Sizing: qty = equity * risk_pct * size multipliers / (stop_atr_mult * ATR at the
signal bar), then capped by heat, leverage and volume. Equity for sizing is
realized cash. Loss limits are checked on bar closes, so a loss can overshoot them.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from app.research.regime_lab.config import (
    BARS_PER_YEAR,
    BTC_SYMBOL,
    DONCHIAN,
    DOWNTREND,
    LONG,
    REGIMES,
    ROUTING,
    SHORT,
    UPTREND,
    WARMUP,
    RunConfig,
    corr_bars,
)
from app.research.regime_lab.strategies import (
    MEAN_REVERSION,
    Position,
    SymbolData,
    dip_confirmed,
    entry_side,
    exit_reason,
    retest_confirmed,
    retest_reference,
)

MIN_NOTIONAL = 10.0
MAX_CURVE_POINTS = 1500
VOLUME_LOOKBACK_BARS = 20
TF_DELTA = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "1d": timedelta(days=1)}
MONTE_CARLO_RUNS = 500
QUICK_STOP_BARS = 2
TOP_WINNERS_REMOVED = 5


@dataclass
class Trade:
    symbol: str
    strategy: str
    side: str
    regime: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    notional: float
    gross_pnl: float
    fees: float
    slippage_cost: float
    net_pnl: float
    r_multiple: float
    bars_held: int
    exit_reason: str


@dataclass
class _Pending:
    strategy: str
    side: str
    regime: str
    atr: float
    size_mult: float


@dataclass
class _Armed:
    """A signal waiting for confirmation: a dip turn (ref None) or a retest of `ref`."""

    strategy: str
    side: str
    expires: int
    ref: float | None = None


def _iso(t: datetime) -> str:
    return t.isoformat()


def run_backtest(
    datas: dict[str, SymbolData],
    cfg: RunConfig,
    start: datetime,
    end: datetime,
    *,
    btc: SymbolData | None = None,
    initial_equity: float | None = None,
    include_trades: bool = True,
    with_monte_carlo: bool = True,
    peak_equity: float | None = None,
    halted: bool = False,
) -> dict[str, Any]:
    """`btc` feeds the BTC gate (when cfg.btc_gate), the crash filter and correlation."""
    p = cfg.params
    sg = cfg.safeguards
    fee_rate = cfg.fee_rate
    stop_mult = cfg.eff_stop_mult
    risk_pct = cfg.eff_risk_pct
    strategies = cfg.eff_strategies
    allow_short = cfg.eff_allow_short
    start_equity = cfg.initial_equity if initial_equity is None else initial_equity
    bar_delta = TF_DELTA[cfg.timeframe]
    n_corr = corr_bars(cfg.timeframe)
    rank_thr = cfg.regime.atr_rank_threshold
    btc = btc if btc is not None else datas.get(BTC_SYMBOL)

    timeline = sorted({t for d in datas.values() for t in d.times if start <= t < end})
    last_index: dict[str, int] = {}
    for sym, d in datas.items():
        idx = [d.index[t] for t in d.times if start <= t < end]
        if idx:
            last_index[sym] = idx[-1]

    cash = start_equity
    positions: dict[str, Position] = {}
    pending_entry: dict[str, _Pending] = {}
    pending_exit: dict[str, str] = {}
    armed: dict[str, _Armed] = {}
    trades: list[Trade] = []
    curve: list[tuple[datetime, float]] = []
    last_close: dict[str, float] = {}
    last_i: dict[str, int] = {}
    counters: dict[str, int] = defaultdict(int)
    regime_bars: dict[str, dict[str, int]] = {s: defaultdict(int) for s in datas}
    exposed_bars = 0

    stop_streak: dict[str, int] = defaultdict(int)
    cooldown_until: dict[str, int] = {}
    sym_pause_until: dict[str, int] = {}
    global_pause_until: datetime | None = None
    strat_r: dict[str, deque[float]] = {s: deque(maxlen=max(sg.perf_window, 1)) for s in strategies}
    strat_streak: dict[str, int] = defaultdict(int)
    current_day = None
    day_start_equity = start_equity
    day_halted = False
    day_entries = 0
    kill_at: str | None = None
    peak = max(start_equity, peak_equity or 0.0)
    mtm = start_equity

    def perf_mult(strategy: str) -> float:
        if sg.perf_cut <= 0:
            return 1.0
        hist = strat_r[strategy]
        cold = strat_streak[strategy] >= sg.perf_loss_streak or (
            len(hist) == hist.maxlen and sum(hist) / len(hist) < 0
        )
        return sg.perf_cut if cold else 1.0

    def close_position(sym: str, raw_price: float, i: int, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(sym)
        pending_exit.pop(sym, None)
        d = datas[sym]
        slip = cfg.slippage_for(sym)
        fill = raw_price * (1 - slip) if pos.side == LONG else raw_price * (1 + slip)
        gross = (fill - pos.entry_price) * pos.qty * pos.direction
        exit_fee = abs(fill * pos.qty) * fee_rate
        cash += gross - exit_fee
        net = gross - exit_fee - pos.entry_fee
        r_mult = net / pos.risk_amount if pos.risk_amount else 0.0
        trades.append(
            Trade(
                symbol=sym,
                strategy=pos.strategy,
                side=pos.side,
                regime=pos.regime,
                entry_time=_iso(pos.entry_time),
                exit_time=_iso(d.times[i]),
                entry_price=pos.entry_price,
                exit_price=fill,
                qty=pos.qty,
                notional=pos.entry_price * pos.qty,
                gross_pnl=gross,
                fees=pos.entry_fee + exit_fee,
                slippage_cost=pos.entry_slippage + abs(raw_price - fill) * pos.qty,
                net_pnl=net,
                r_multiple=r_mult,
                bars_held=i - pos.entry_index + 1,
                exit_reason=reason,
            )
        )
        if pos.strategy in strat_r:
            strat_r[pos.strategy].append(r_mult)
            strat_streak[pos.strategy] = strat_streak[pos.strategy] + 1 if net <= 0 else 0
        if reason == "stop":
            stop_streak[sym] += 1
            if sg.cooldown_stops and stop_streak[sym] >= sg.cooldown_stops:
                cooldown_until[sym] = i + sg.cooldown_bars
                stop_streak[sym] = 0
                counters["cooldowns_started"] += 1
        else:
            stop_streak[sym] = 0

    def flatten(reason: str) -> None:
        for sym in list(positions):
            close_position(sym, last_close[sym], last_i[sym], reason)
        pending_entry.clear()
        armed.clear()

    def is_correlated(sym: str, at: int) -> bool:
        if btc is None:
            return False
        c = datas[sym].corr_to(btc, n_corr)[at] if sym != btc.symbol else 1.0
        return c is not None and c >= sg.corr_threshold

    def allowed_sides(sym: str, strategy: str, route: str, btc_route: str | None) -> tuple[str, ...]:
        sides = ROUTING[strategy].get(route, ())
        if not allow_short:
            sides = tuple(s for s in sides if s != SHORT)
        if btc_route is not None and sym != BTC_SYMBOL:
            sides = tuple(
                s
                for s in sides
                if not (s == LONG and btc_route == DOWNTREND) and not (s == SHORT and btc_route == UPTREND)
            )
        return sides

    btc_ptr = -1
    for t in timeline:
        if t.date() != current_day:
            current_day = t.date()
            day_start_equity = mtm if sg.daily_loss_basis == "equity" else cash
            day_halted = False
            day_entries = 0
        if btc is not None:
            while btc_ptr + 1 < len(btc.times) and btc.times[btc_ptr + 1] <= t:
                btc_ptr += 1
        btc_route = btc.route[btc_ptr] if cfg.btc_gate and btc is not None and btc_ptr >= 0 else None

        active = [(sym, d, d.index[t]) for sym, d in datas.items() if t in d.index]

        # 1. Exits signalled at the previous close.
        for sym, d, i in active:
            if sym in pending_exit and sym in positions:
                close_position(sym, d.opens[i], i, pending_exit[sym])
            pending_exit.pop(sym, None)

        # 2. Entries signalled at the previous close.
        for sym, d, i in active:
            sig = pending_entry.pop(sym, None)
            if sig is None or sym in positions:
                continue
            if sg.max_entries_per_day and day_entries >= sg.max_entries_per_day:
                counters["skipped_daily_trade_cap"] += 1
                continue
            if sg.max_positions and len(positions) >= sg.max_positions:
                counters["skipped_max_positions"] += 1
                continue
            if sg.corr_threshold > 0 and is_correlated(sym, i - 1):
                same = sum(
                    1 for s, pos in positions.items() if pos.side == sig.side and is_correlated(s, last_i[s])
                )
                if same >= sg.max_correlated:
                    counters["skipped_correlated_cluster"] += 1
                    continue
            raw = d.opens[i]
            slip = cfg.slippage_for(sym)
            fill = raw * (1 + slip) if sig.side == LONG else raw * (1 - slip)
            stop_dist = stop_mult * sig.atr
            cut = perf_mult(sig.strategy)
            if cut < 1.0:
                counters["entries_cut_by_performance"] += 1
            mult = sig.size_mult * cut
            size_dist = stop_dist + (2 * slip + 2 * fee_rate) * fill if sg.slippage_buffer else stop_dist
            qty = cash * risk_pct * mult / size_dist
            if sg.heat_cap > 0:
                room_risk = sg.heat_cap * cash - sum(pos.risk_amount for pos in positions.values())
                if qty * stop_dist > room_risk:
                    counters["entries_capped_by_heat"] += 1
                    qty = max(room_risk, 0.0) / stop_dist
            room = cash * cfg.max_leverage - sum(pos.entry_price * pos.qty for pos in positions.values())
            if qty * fill > room:
                counters["entries_capped_by_leverage"] += 1
                qty = max(room, 0.0) / fill
            if sg.max_volume_participation > 0:
                adv = d.avg_dollar_volume(VOLUME_LOOKBACK_BARS)[i]
                if adv is not None:
                    sym_hours = (d.bar_delta or bar_delta).total_seconds() / 3600
                    cap = sg.max_volume_participation * adv / sym_hours
                    if qty * fill > cap:
                        counters["entries_capped_by_volume"] += 1
                        qty = cap / fill
            if qty * fill < MIN_NOTIONAL:
                counters["entries_skipped_no_room"] += 1
                continue
            entry_fee = qty * fill * fee_rate
            cash -= entry_fee
            day_entries += 1
            positions[sym] = Position(
                symbol=sym,
                strategy=sig.strategy,
                side=sig.side,
                regime=sig.regime,
                entry_index=i,
                entry_time=t,
                entry_price=fill,
                qty=qty,
                stop=fill - stop_dist if sig.side == LONG else fill + stop_dist,
                risk_amount=qty * stop_dist,
                entry_fee=entry_fee,
                entry_slippage=abs(fill - raw) * qty,
            )

        # 3. Intrabar ATR stops.
        for sym, d, i in active:
            pos = positions.get(sym)
            if pos is None:
                continue
            if pos.side == LONG and d.lows[i] <= pos.stop:
                close_position(sym, min(d.opens[i], pos.stop), i, "stop")
            elif pos.side == SHORT and d.highs[i] >= pos.stop:
                close_position(sym, max(d.opens[i], pos.stop), i, "stop")

        # A BTC bar that dumps from its open pauses every coin.
        if sg.crash_filter and btc is not None and t in btc.index:
            j = btc.index[t]
            if btc.lows[j] / btc.opens[j] - 1 <= -sg.crash_btc_drop:
                global_pause_until = t + bar_delta * sg.crash_pause_bars
                counters["btc_crash_pauses"] += 1

        # 4. Rules on the close.
        for sym, d, i in active:
            last_close[sym] = d.closes[i]
            last_i[sym] = i
            regime_bars[sym][d.regime[i]] += 1
            is_last = i >= last_index[sym]
            pos = positions.get(sym)
            if pos is not None:
                if is_last:
                    close_position(sym, d.closes[i], i, "end_of_test")
                else:
                    reason = exit_reason(pos, d, i, p)
                    if reason:
                        pending_exit[sym] = reason
                continue
            route = d.route[i]
            if sg.crash_filter and i > 0 and d.atr[i - 1] and d.highs[i] - d.lows[i] > sg.crash_range_atr * d.atr[i - 1]:
                sym_pause_until[sym] = i + sg.crash_pause_bars
                counters["coin_crash_pauses"] += 1
            if is_last or route == WARMUP:
                armed.pop(sym, None)
                continue
            atr_now = d.atr[i]
            if not atr_now or atr_now <= 0:
                continue
            block = None
            if halted:
                block = "blocked_kill_switch"
            elif day_halted:
                block = "blocked_daily_loss_limit"
            elif global_pause_until is not None and t <= global_pause_until:
                block = "blocked_btc_crash_pause"
            elif i <= sym_pause_until.get(sym, -1):
                block = "blocked_coin_crash_pause"
            elif i <= cooldown_until.get(sym, -1):
                block = "blocked_cooldown"
            elif sg.chop_filter:
                er = d.er(sg.er_period)[i]
                rank = d.atr_rank[i]
                if er is not None and rank is not None and er < sg.er_min and rank >= rank_thr:
                    block = "blocked_chop_filter"
            if block:
                counters[block] += 1
                armed.pop(sym, None)
                continue

            a = armed.get(sym)
            if a is not None:
                ok = retest_confirmed(d, i, a.side, a.ref) if a.ref is not None else dip_confirmed(d, i, a.side)
                if i > a.expires or a.side not in allowed_sides(sym, a.strategy, route, btc_route):
                    armed.pop(sym)
                    counters["retest_entries_expired" if a.ref is not None else "dip_entries_expired"] += 1
                elif ok:
                    armed.pop(sym)
                    pending_entry[sym] = _Pending(a.strategy, a.side, d.regime[i], atr_now, d.size[i])
                    continue
                else:
                    continue

            for strategy in strategies:
                base_sides = ROUTING[strategy].get(route, ())
                if not allow_short:
                    base_sides = tuple(s for s in base_sides if s != SHORT)
                sides = allowed_sides(sym, strategy, route, btc_route)
                if sides != base_sides:
                    raw_side = entry_side(strategy, d, i, base_sides, p)
                    if raw_side is not None and raw_side not in sides:
                        counters["entries_blocked_by_btc_gate"] += 1
                if not sides:
                    continue
                side = entry_side(strategy, d, i, sides, p)
                if not side:
                    continue
                ref = retest_reference(d, i, side, p) if strategy == DONCHIAN else None
                if ref is not None:
                    armed[sym] = _Armed(strategy, side, i + p.retest_bars, ref)
                elif sg.dip_confirm and strategy in MEAN_REVERSION and not dip_confirmed(d, i, side):
                    armed[sym] = _Armed(strategy, side, i + sg.dip_confirm_bars)
                else:
                    pending_entry[sym] = _Pending(strategy, side, d.regime[i], atr_now, d.size[i])
                break

        # 5. Mark to market, then the account-level stops.
        if positions:
            exposed_bars += 1
        mtm = cash + sum(
            (last_close.get(s, pos.entry_price) - pos.entry_price) * pos.qty * pos.direction
            for s, pos in positions.items()
        )
        day_value = mtm
        if sg.daily_loss_basis == "realized":
            day_value = cash + sum(
                min(0.0, (last_close.get(s, pos.entry_price) - pos.entry_price) * pos.qty * pos.direction)
                for s, pos in positions.items()
            )
        if sg.daily_loss_limit > 0 and not day_halted and day_value <= day_start_equity * (1 - sg.daily_loss_limit):
            flatten("daily_loss_limit")
            day_halted = True
            counters["daily_loss_limit_hits"] += 1
            mtm = cash
        peak = max(peak, mtm)
        if sg.kill_drawdown > 0 and not halted and mtm <= peak * (1 - sg.kill_drawdown):
            flatten("kill_switch")
            halted = True
            kill_at = _iso(t)
            mtm = cash
        curve.append((t, mtm))

    return {
        "summary": summarize(trades, curve, start_equity, cfg.timeframe, exposed_bars, len(timeline)),
        "counters": dict(counters),
        "kill_switch_at": kill_at,
        "breakdowns": breakdowns(trades),
        "regime_share": {
            sym: {r: round(bars.get(r, 0) / max(sum(bars.values()), 1), 4) for r in (*REGIMES, WARMUP)}
            for sym, bars in regime_bars.items()
        },
        "monte_carlo": monte_carlo(trades, start_equity) if with_monte_carlo and include_trades else None,
        "equity_curve": downsample(curve),
        "trades": [asdict(tr) for tr in trades] if include_trades else [],
        "trade_count": len(trades),
        "end_equity": curve[-1][1] if curve else start_equity,
        "peak_equity": peak,
        "halted": halted,
        "_curve": curve,
    }


def max_drawdown(values: list[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def _percentile(sorted_vals: list[float], q: float) -> float:
    k = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def monte_carlo(trades: list[Trade], start_equity: float, runs: int = MONTE_CARLO_RUNS, seed: int = 7) -> dict[str, Any] | None:
    """Reshuffle trade order (drawdown spread) and resample trades (return spread).

    Each trade is replayed as its return on the equity it was opened with, in exit order.
    """
    if len(trades) < 20:
        return None
    ordered = sorted(trades, key=lambda t: t.exit_time)
    equity = start_equity
    rets: list[float] = []
    for t in ordered:
        rets.append(t.net_pnl / equity if equity > 0 else 0.0)
        equity += t.net_pnl
    rng = random.Random(seed)

    def path(seq: list[float]) -> tuple[float, float]:
        e = 1.0
        pk = 1.0
        dd = 0.0
        for r in seq:
            e *= 1 + r
            pk = max(pk, e)
            dd = max(dd, (pk - e) / pk if pk > 0 else 0.0)
        return dd, e - 1

    shuffled_dd = []
    boot_ret = []
    boot_dd = []
    for _ in range(runs):
        seq = rets[:]
        rng.shuffle(seq)
        shuffled_dd.append(path(seq)[0])
        dd, ret = path([rng.choice(rets) for _ in rets])
        boot_dd.append(dd)
        boot_ret.append(ret)
    shuffled_dd.sort()
    boot_ret.sort()
    boot_dd.sort()
    return {
        "runs": runs,
        "trades": len(rets),
        "shuffle_max_dd_pct": {q: _percentile(shuffled_dd, v) * 100 for q, v in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))},
        "bootstrap_return_pct": {q: _percentile(boot_ret, v) * 100 for q, v in (("p5", 0.05), ("p50", 0.5), ("p95", 0.95))},
        "bootstrap_max_dd_pct": {q: _percentile(boot_dd, v) * 100 for q, v in (("p50", 0.5), ("p95", 0.95))},
        "prob_loss": sum(1 for r in boot_ret if r < 0) / runs,
    }


def summarize(
    trades: list[Trade],
    curve: list[tuple[datetime, float]],
    start_equity: float,
    timeframe: str,
    exposed_bars: int,
    total_bars: int,
) -> dict[str, Any]:
    end_equity = curve[-1][1] if curve else start_equity
    nets = [t.net_pnl for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    values = [v for _, v in curve]
    rets = [values[k] / values[k - 1] - 1 for k in range(1, len(values)) if values[k - 1] > 0]
    sharpe = None
    if len(rets) > 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        if var > 0:
            sharpe = mean / math.sqrt(var) * math.sqrt(BARS_PER_YEAR.get(timeframe, 2190))
    days = (curve[-1][0] - curve[0][0]).total_seconds() / 86400 if len(curve) > 1 else 0
    total_return = end_equity / start_equity - 1 if start_equity else 0.0
    cagr = None
    if days >= 30 and end_equity > 0:
        cagr = (end_equity / start_equity) ** (365 / days) - 1
    mdd = max_drawdown(values) * 100
    peak_v = -math.inf
    mdd_usd = 0.0
    for v in values:
        peak_v = max(peak_v, v)
        mdd_usd = max(mdd_usd, peak_v - v)
    streak = worst_streak = 0
    for t in sorted(trades, key=lambda t: t.exit_time):
        streak = streak + 1 if t.net_pnl <= 0 else 0
        worst_streak = max(worst_streak, streak)
    top = sorted(nets, reverse=True)[:TOP_WINNERS_REMOVED]
    return {
        "start_equity": start_equity,
        "end_equity": end_equity,
        "net_pnl": end_equity - start_equity,
        "return_pct": total_return * 100,
        "cagr_pct": None if cagr is None else cagr * 100,
        "max_drawdown_pct": mdd,
        "max_drawdown_usd": mdd_usd,
        "max_losing_streak": worst_streak,
        "quick_stops": sum(1 for t in trades if t.exit_reason == "stop" and t.bars_held <= QUICK_STOP_BARS),
        "pnl_ex_top5": sum(nets) - sum(x for x in top if x > 0),
        # Live drawdown beyond this means the live bot is not behaving like the backtest.
        "live_pause_drawdown_pct": mdd * 1.5,
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
        "avg_r": sum(t.r_multiple for t in trades) / len(trades) if trades else None,
        "fees": sum(t.fees for t in trades),
        "slippage": sum(t.slippage_cost for t in trades),
        "exposure_pct": exposed_bars / total_bars * 100 if total_bars else 0.0,
        "days": days,
        "pnl_per_30d": (end_equity - start_equity) / days * 30 if days > 0 else None,
    }


def _group(trades: list[Trade], key) -> list[dict[str, Any]]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    out = []
    for name, ts in sorted(groups.items()):
        nets = [t.net_pnl for t in ts]
        gains = sum(x for x in nets if x > 0)
        pains = -sum(x for x in nets if x <= 0)
        out.append(
            {
                "key": name,
                "trades": len(ts),
                "net_pnl": sum(nets),
                "win_rate": sum(1 for x in nets if x > 0) / len(ts),
                "avg_r": sum(t.r_multiple for t in ts) / len(ts),
                "profit_factor": gains / pains if pains > 0 else None,
                "fees": sum(t.fees for t in ts),
            }
        )
    return out


def breakdowns(trades: list[Trade]) -> dict[str, list[dict[str, Any]]]:
    return {
        "strategy": _group(trades, lambda t: t.strategy),
        "regime": _group(trades, lambda t: t.regime),
        "strategy_regime_side": _group(trades, lambda t: f"{t.strategy} · {t.regime} · {t.side}"),
        "symbol": _group(trades, lambda t: t.symbol),
        "exit_reason": _group(trades, lambda t: t.exit_reason),
        "year": _group(trades, lambda t: t.exit_time[:4]),
    }


def downsample(curve: list[tuple[datetime, float]]) -> list[dict[str, Any]]:
    if not curve:
        return []
    step = max(1, math.ceil(len(curve) / MAX_CURVE_POINTS))
    pts = curve[::step]
    if pts[-1] is not curve[-1]:
        pts.append(curve[-1])
    return [{"time": int(t.timestamp()), "equity": round(v, 2)} for t, v in pts]
