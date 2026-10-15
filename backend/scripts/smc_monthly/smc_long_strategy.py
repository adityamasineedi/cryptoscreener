"""Long-only CHoCH/BOS + demand-zone retest + volume backtest.

Research script for monthly_report.py. Does not change live or paper rules.

Rules (1h bars, causal):
- Swing high/low: fractal, 2 bars left and 2 bars right. A swing is known
  only after the right-hand bar has closed.
- Bullish break: bar closes above the latest confirmed swing high that has
  not already been broken. Bearish break: close below the latest swing low.
  A break after bearish structure is labeled CHoCH; a break after bullish
  structure is labeled BOS. Both are eligible.
- Volume: break-bar volume is at least the mean of the prior 20 bars.
- Demand zone: the confirmed swing-low candle that the rally left behind.
  Zone top is that candle's high, zone bottom is its low.
- Entry: limit at the zone top, from the next bar through 48 bars. Filled
  when a bar trades down to the zone top and has not opened through the
  zone bottom. One position at a time; a new break replaces a pending setup.
- Stop: zone bottom. Target: 2R. If stop and target are both inside one
  bar, the stop is used. Round-trip taker fee is 0.04% of notional per side.
- Risk: 1% of equity at entry. R is net PnL divided by that risk amount.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    start_equity: float = 10_000.0
    risk_frac: float = 0.01
    swing_left: int = 2
    swing_right: int = 2
    volume_window: int = 20
    retest_bars: int = 48
    reward_r: float = 2.0
    taker_fee: float = 0.0004


def read_ohlcv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    stamp = None
    for name in ("timestamp", "time", "date", "open_time", "datetime"):
        if name in df.columns:
            stamp = name
            break
    if stamp is None:
        stamp = df.columns[0]
    df[stamp] = pd.to_datetime(df[stamp], utc=True).dt.tz_convert(None)
    df = df.set_index(stamp).sort_index()
    df = df.rename(columns={c: c.lower() for c in df.columns})
    need = ["open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise SystemExit(f"CSV missing columns: {missing}")
    return df[need].astype(float)


def demo_data(n: int = 500) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    steps = rng.normal(0.0002, 0.01, n)
    close = 100 * np.exp(np.cumsum(steps))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.random(n) * 0.004)
    low = np.minimum(open_, close) * (1 - rng.random(n) * 0.004)
    volume = rng.random(n) * 1000 + 100
    idx = pd.date_range("2020-01-01", periods=n, freq="h")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _swings(high: np.ndarray, low: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(high)
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window_h = high[i - left : i + right + 1]
        window_l = low[i - left : i + right + 1]
        if high[i] == window_h.max() and np.sum(window_h == high[i]) == 1:
            sh[i] = True
        if low[i] == window_l.min() and np.sum(window_l == low[i]) == 1:
            sl[i] = True
    return sh, sl


def _trade_row(
    *,
    entry_time,
    exit_time,
    entry_i: int,
    exit_i: int,
    setup: str,
    entry: float,
    exit_px: float,
    stop: float,
    target: float,
    reason: str,
    r_mult: float,
    zone_high: float,
    zone_low: float,
) -> dict:
    return {
        "entry_time": entry_time,
        "exit_time": exit_time,
        "setup": setup,
        "entry": entry,
        "exit": exit_px,
        "stop": stop,
        "stop0": stop,
        "target": target,
        "zone_high": zone_high,
        "zone_low": zone_low,
        "bars": exit_i - entry_i,
        "reason": reason,
        "R": r_mult,
    }


def run_backtest(df: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, pd.Series, dict]:
    data = df.sort_index()
    o = data["open"].to_numpy(float)
    h = data["high"].to_numpy(float)
    l = data["low"].to_numpy(float)
    c = data["close"].to_numpy(float)
    v = data["volume"].to_numpy(float)
    idx = data.index
    n = len(data)
    sh, sl = _swings(h, l, p.swing_left, p.swing_right)
    vol_mean = pd.Series(v).rolling(p.volume_window, min_periods=p.volume_window).mean().shift(1).to_numpy()

    equity = np.full(n, p.start_equity)
    cash = p.start_equity
    trades: list[dict] = []

    swing_high = np.nan
    swing_high_i = -1
    swing_low = np.nan
    swing_low_i = -1
    high_broken = True
    low_broken = True
    bias = 0  # 1 bullish after an up break, -1 after a down break

    pending = None
    position = None

    for i in range(n):
        # Swings that become confirmed on this bar (pivot is `right` bars back).
        piv = i - p.swing_right
        if piv >= p.swing_left:
            if sh[piv]:
                swing_high = h[piv]
                swing_high_i = piv
                high_broken = False
            if sl[piv]:
                swing_low = l[piv]
                swing_low_i = piv
                low_broken = False

        if position is not None:
            stop = position["stop"]
            target = position["target"]
            hit_stop = l[i] <= stop
            hit_target = h[i] >= target
            exit_px = None
            reason = None
            if hit_stop and hit_target:
                exit_px, reason = stop, "stop"
            elif hit_stop:
                exit_px, reason = stop, "stop"
            elif hit_target:
                exit_px, reason = target, "target"
            elif i == n - 1:
                exit_px, reason = c[i], "eod"
            if exit_px is not None:
                qty = position["qty"]
                gross = (exit_px - position["entry"]) * qty
                exit_fee = exit_px * qty * p.taker_fee
                net = gross - exit_fee
                cash += net
                risk = position["risk_dollars"]
                r_mult = net / risk if risk else 0.0
                trades.append(
                    _trade_row(
                        entry_time=idx[position["entry_i"]],
                        exit_time=idx[i],
                        entry_i=position["entry_i"],
                        exit_i=i,
                        setup=position["setup"],
                        entry=position["entry"],
                        exit_px=exit_px,
                        stop=stop,
                        target=target,
                        reason=reason,
                        r_mult=r_mult,
                        zone_high=position["zone_high"],
                        zone_low=position["zone_low"],
                    )
                )
                position = None

        if position is None and pending is not None and i > pending["break_i"]:
            if i > pending["expire_i"] or o[i] <= pending["zone_low"]:
                pending = None
            elif l[i] <= pending["entry"]:
                entry = pending["entry"]
                stop = pending["zone_low"]
                risk_per_unit = entry - stop
                if risk_per_unit > 0:
                    risk_dollars = cash * p.risk_frac
                    qty = risk_dollars / risk_per_unit
                    entry_fee = entry * qty * p.taker_fee
                    cash -= entry_fee
                    target = entry + p.reward_r * risk_per_unit
                    # Same bar can trade the limit and the stop. Stop wins if both print.
                    if l[i] <= stop:
                        exit_fee = stop * qty * p.taker_fee
                        net = (stop - entry) * qty - exit_fee
                        cash += net
                        trades.append(
                            _trade_row(
                                entry_time=idx[i],
                                exit_time=idx[i],
                                entry_i=i,
                                exit_i=i,
                                setup=pending["setup"],
                                entry=entry,
                                exit_px=stop,
                                stop=stop,
                                target=target,
                                reason="stop",
                                r_mult=net / risk_dollars,
                                zone_high=pending["entry"],
                                zone_low=pending["zone_low"],
                            )
                        )
                    elif h[i] >= target:
                        exit_fee = target * qty * p.taker_fee
                        net = (target - entry) * qty - exit_fee
                        cash += net
                        trades.append(
                            _trade_row(
                                entry_time=idx[i],
                                exit_time=idx[i],
                                entry_i=i,
                                exit_i=i,
                                setup=pending["setup"],
                                entry=entry,
                                exit_px=target,
                                stop=stop,
                                target=target,
                                reason="target",
                                r_mult=net / risk_dollars,
                                zone_high=pending["entry"],
                                zone_low=pending["zone_low"],
                            )
                        )
                    else:
                        position = {
                            "entry_i": i,
                            "entry": entry,
                            "stop": stop,
                            "target": target,
                            "qty": qty,
                            "risk_dollars": risk_dollars,
                            "setup": pending["setup"],
                            "zone_high": pending["entry"],
                            "zone_low": pending["zone_low"],
                        }
                pending = None

        if (
            position is None
            and pending is None
            and not high_broken
            and np.isfinite(swing_high)
            and c[i] > swing_high
            and np.isfinite(vol_mean[i])
            and v[i] >= vol_mean[i]
            and swing_low_i >= 0
            and swing_low_i < i
        ):
            zone_low = l[swing_low_i]
            zone_high = h[swing_low_i]
            if zone_high > zone_low and zone_high < c[i]:
                pending = {
                    "break_i": i,
                    "expire_i": i + p.retest_bars,
                    "entry": zone_high,
                    "zone_low": zone_low,
                    "setup": "CHoCH" if bias < 0 else "BOS",
                }
            high_broken = True
            bias = 1

        if not low_broken and np.isfinite(swing_low) and c[i] < swing_low:
            low_broken = True
            bias = -1
            if position is None:
                pending = None

        equity[i] = cash
        if position is not None:
            qty = position["qty"]
            mtm = (c[i] - position["entry"]) * qty
            exit_fee = c[i] * qty * p.taker_fee
            equity[i] = cash + mtm - exit_fee

    trades_df = pd.DataFrame(trades)
    eq = pd.Series(equity, index=idx, name="equity")
    return trades_df, eq, {"trades": len(trades_df)}
