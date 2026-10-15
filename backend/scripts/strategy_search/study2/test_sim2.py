"""Synthetic checks for sim2.py (and the causal regime lookup in signals2.py).

Run: python -B test_sim2.py      (plain asserts; prints one line per case)
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from sim2 import Bars, Config, Costs, simulate

UTC = timezone.utc
T0 = datetime(2024, 1, 1, tzinfo=UTC)  # Monday 00:00
H = 3600
ZERO = Costs(taker=0.0, slip=0.0, funding_8h=0.0)


def mk_bars(coin: str, rows: list[tuple], start: datetime = T0, tf: str = "1h") -> Bars:
    """rows: (o, h, l, c) per hour from start; flat 100 bars if a row is a number."""
    b = Bars()
    cs = []
    for i, r in enumerate(rows):
        o, h, l, c = (r, r, r, r) if isinstance(r, (int, float)) else r
        cs.append({"time": start + timedelta(hours=i), "open": o, "high": h, "low": l, "close": c})
    b.put(coin, tf, cs)
    return b


def sig(coin="X", t_close=T0 + timedelta(hours=1), side="LONG", stop=95.0, **kw) -> dict:
    d = {"sid": kw.pop("sid", "A"), "coin": coin, "tf": kw.pop("tf", "1h"), "side": side,
         "signal_close": int(t_close.timestamp()), "stop": stop}
    d.update(kw)
    return d


def run(bars, sigs, n_hours=10, sleeves=("X",), **cfg):
    costs = cfg.pop("costs", ZERO)
    c = Config(costs=costs, **cfg)
    return simulate(sigs, bars, list(sleeves), T0, T0 + timedelta(hours=n_hours), c)


def case(name):
    def deco(f):
        f(); print("ok", name)
        return f
    return deco


@case("next-open execution with execution allowance and fee")
def _():
    b = mk_bars("X", [100, (101, 102, 100.5, 101.5), 101.5, 101.5])
    r = run(b, [sig(stop=96.0, target_r=10)], n_hours=4, costs=Costs(taker=0.0004, slip=0.0002, funding_8h=0.0))
    tr = r["trades"][0]
    assert abs(tr["fill"] - 101 * 1.0002) < 1e-9, tr["fill"]   # bar opening at signal close, not the signal close
    assert tr["entry_t"] == int((T0 + timedelta(hours=1)).timestamp())
    assert tr["reason"] == "window_end"
    assert tr["fees"] > 0 and tr["exec"] > 0


@case("risk sizing includes stop-exit costs; 2% of sleeve equity")
def _():
    b = mk_bars("X", [100, 100, 100, 100])
    cs = Costs(taker=0.0004, slip=0.0002, funding_8h=0.0)
    r = run(b, [sig(stop=95.0, target_r=10)], n_hours=3, costs=cs)
    tr = r["trades"][0]
    assert abs(tr["risk_usd"] - 20.0) < 1e-6, tr["risk_usd"]   # $1,000 x 2% = $20, not $200
    fill = 100 * 1.0002
    rpu = (fill - 95) + 0.0004 * fill + 0.0006 * 95
    assert abs(tr["risk_usd"] / rpu * fill - 20 / rpu * fill) < 1e-6


@case("stop then target in the same bar resolves to the stop")
def _():
    b = mk_bars("X", [100, 100, (100, 120, 90, 110), 110])
    r = run(b, [sig(stop=95.0, target_r=1.0)], n_hours=4)
    tr = r["trades"][0]
    assert tr["reason"] == "stop" and tr["net"] < 0
    assert abs(tr["r"] + 1.0) < 1e-9


@case("gap through stop fills at the adverse open")
def _():
    b = mk_bars("X", [100, 100, (90, 91, 88, 89), 89])
    r = run(b, [sig(stop=95.0)], n_hours=4)
    tr = r["trades"][0]
    assert tr["reason"] == "stop_gap"
    assert tr["r"] < -1.9  # (90-100)/(100-95) = -2R


@case("entry fill through the stop is skipped, stop not moved")
def _():
    b = mk_bars("X", [100, (94, 95, 93, 94), 94])
    r = run(b, [sig(stop=95.0)], n_hours=3)
    assert not r["trades"] and r["skips"][0][3] == "fill_through_stop"


@case("one position per coin; sleeve equity compounds into the next size")
def _():
    b = mk_bars("X", [100, 100, (100, 100, 94, 95), 100, 100, 100, 100, 100])
    s1 = sig(stop=95.0, target_r=5)
    s2 = sig(t_close=T0 + timedelta(hours=2), stop=90.0, sid="B")   # coin busy at that open
    s3 = sig(t_close=T0 + timedelta(hours=4), stop=95.0, sid="C")
    r = run(b, [s1, s2, s3], n_hours=8, sleeves=("X", "CASH"))  # $2,000 account: a $20 loss is not a 2% day
    assert [t["sid"] for t in r["trades"]] == ["A", "C"], (r["trades"], r["skips"])
    assert any(k[0] == "B" and k[3] == "coin_busy" for k in r["skips"])
    assert abs(r["trades"][1]["risk_usd"] - 0.02 * 980.0) < 1e-6  # $20 lost, next risk 2% of $980


@case("2x leverage cap binds and the full risk budget is not assumed")
def _():
    b = mk_bars("X", [100, 100, 100, 100])
    r = run(b, [sig(stop=99.9)], n_hours=3)
    tr = r["trades"][0]
    assert tr["risk_usd"] < 20.0 and abs(tr["risk_usd"] - 20 * 0.1) < 1e-6  # qty capped at 20 units = $2,000


@case("account open-risk cap min($200, 2% equity) reduces / skips entries")
def _():
    rows = [100] * 6
    b = Bars()
    coins = [f"C{i}" for i in range(12)]
    for cn in coins:
        b.data.update(mk_bars(cn, rows).data)
    sigs = [sig(coin=cn, stop=95.0, sid=cn) for cn in coins]
    r = run(b, sigs, n_hours=5, sleeves=coins, risk_frac=0.05)
    # 12 sleeves = $12,000, cap = min(200, 240) = 200 -> four $50 positions, then nothing
    risks = [t["risk_usd"] for t in r["trades"]]
    assert abs(sum(risks) - 200.0) < 1e-6 and len(risks) == 4, risks
    assert sum(1 for k in r["skips"] if k[3] == "account_risk_cap") == 8


@case("lot step rounds quantity down")
def _():
    b = mk_bars("X", [100, 100, 100])
    r = run(b, [sig(stop=95.0)], n_hours=2, lot_step={"X": 1.0})
    tr = r["trades"][0]
    assert abs(tr["risk_usd"] - 20.0) < 1e-9  # 4 units x $5
    r = run(b, [sig(stop=93.0)], n_hours=2, lot_step={"X": 1.0})
    assert abs(r["trades"][0]["risk_usd"] - 14.0) < 1e-9  # 20/7 = 2.86 -> 2 units x $7


@case("partial exit at 1R, stop to breakeven from the next bar")
def _():
    b = mk_bars("X", [100, 100, (100, 105.5, 99, 104), (104, 104, 99.5, 100), 100])
    r = run(b, [sig(stop=95.0, partial_r=1.0, partial_frac=0.5)], n_hours=5)
    tr = r["trades"][0]
    assert tr["partial"] and tr["reason"] == "stop"
    assert abs(tr["net"] - 10.0) < 1e-9, tr["net"]  # half (2 units) +$5 each, rest out at breakeven


@case("addition only after +1R, shares the original risk allowance")
def _():
    rows = [100, 100, (100, 106, 99, 106), (106, 107, 105, 106), (106, 106, 106, 106), 106, 106]
    b = mk_bars("X", rows)
    s1 = sig(stop=95.0, target_r=20, pyramid=True)
    s2 = sig(t_close=T0 + timedelta(hours=2), stop=101.0, pyramid=True)   # before +1R at open 100 -> refused
    s3 = sig(t_close=T0 + timedelta(hours=3), stop=101.0, pyramid=True)   # open 106 >= 1R -> added
    r = run(b, [s1, s2, s3], n_hours=7)
    tr = r["trades"][0]
    assert tr["adds"] == 1, r["skips"]
    assert any(k[3] == "add_not_1R" for k in r["skips"])


@case("hypothetical funding at 00/08/16 UTC on notional, both sides")
def _():
    rows = [100] * 30
    for side, stop in (("LONG", 95.0), ("SHORT", 105.0)):
        b = mk_bars("X", rows)
        r = run(b, [sig(side=side, stop=stop)], n_hours=26,
                costs=Costs(taker=0.0, slip=0.0, funding_8h=0.0001))
        tr = r["trades"][0]
        # entered 01:00, held through 08:00, 16:00, 00:00 -> 3 charges of 0.01% x $400
        assert abs(tr["funding"] - 3 * 0.0001 * 400) < 1e-9, (side, tr["funding"])


@case("daily loss pause blocks new entries until 00:00 UTC")
def _():
    b = Bars()
    b.data.update(mk_bars("X", [100, 100, (100, 100, 85, 85)] + [85] * 30).data)  # -$60 = 3% of $2,000: daily, not weekly
    b.data.update(mk_bars("Y", [100] * 33).data)
    s1 = sig(coin="X", stop=50.0, risk_frac=None)
    s2 = sig(coin="Y", t_close=T0 + timedelta(hours=5), stop=95.0, sid="B")
    s3 = sig(coin="Y", t_close=T0 + timedelta(hours=25), stop=95.0, sid="C")
    r = run(b, [s1, s2, s3], n_hours=30, sleeves=("X", "Y"), risk_frac=0.2, risk_cap_abs=None, risk_cap_frac=1.0)
    assert any(e["event"] == "daily_pause" for e in r["events"])
    assert any(k[0] == "B" and k[3] == "loss_pause" for k in r["skips"])
    assert any(t["sid"] == "C" for t in r["trades"])


@case("20% drawdown stop exits at the next open and ends the experiment")
def _():
    b = mk_bars("X", [100, 100, (100, 100, 70, 70), (70, 71, 69, 70)] + [70] * 6)
    s1 = sig(stop=50.0)
    s2 = sig(t_close=T0 + timedelta(hours=6), stop=60.0, sid="B")
    r = run(b, [s1, s2], n_hours=10, risk_frac=0.5, leverage=2.0, risk_cap_abs=None, risk_cap_frac=1.0,
            daily_pause=None, weekly_pause=None)
    assert r["stopped"]
    tr = r["trades"][0]
    assert tr["reason"] == "forced" and tr["exit_t"] == int((T0 + timedelta(hours=4)).timestamp())
    assert any(k[0] == "B" and k[3] == "stopped" for k in r["skips"])


@case("month-end withdrawal: excess over base paid from free collateral, losses carry")
def _():
    start = datetime(2024, 1, 31, 20, tzinfo=UTC)
    rows = [100, 100, (100, 111, 100, 111)] + [111] * 30
    b = mk_bars("X", rows, start=start)
    b.data.update(mk_bars("Y", [100] * 33, start=start).data)
    s1 = {"sid": "A", "coin": "X", "tf": "1h", "side": "LONG", "signal_close": int((start + timedelta(hours=1)).timestamp()),
          "stop": 95.0, "target_r": 2.0}
    c = Config(costs=ZERO)
    r = simulate([s1], b, ["X", "Y"], start, start + timedelta(hours=30), c)
    jan = r["months"]["2024-01"]
    assert abs(jan["net"] - 40.0) < 1e-9 and abs(jan["withdrawn"] - 40.0) < 1e-6, jan
    assert abs(r["final_equity"] - 2000.0) < 1e-6
    # a losing month withdraws nothing and is not topped up
    b2 = mk_bars("X", [100, 100, (100, 100, 94, 95)] + [95] * 30, start=start)
    r2 = simulate([s1], b2, ["X"], start, start + timedelta(hours=30), c)
    assert r2["withdrawn"] == 0 and abs(r2["final_equity"] - 980.0) < 1e-6


@case("withdrawal never pays unrealised profit; reduces the position at the next open")
def _():
    start = datetime(2024, 1, 31, 20, tzinfo=UTC)
    rows = [100, 100, (100, 101, 99.9, 101)] + [(101 + 0.5 * i, 101 + 0.5 * (i + 1), 100.9 + 0.5 * i, 101 + 0.5 * (i + 1))
                                              for i in range(4)] + [103] * 30
    b = mk_bars("X", rows, start=start)
    s1 = {"sid": "A", "coin": "X", "tf": "1h", "side": "LONG", "signal_close": int((start + timedelta(hours=1)).timestamp()),
          "stop": 99.0, "target_r": 50.0}
    r = simulate([s1], b, ["X"], start, start + timedelta(hours=20), Config(costs=ZERO, risk_frac=0.05))
    # 20 units (2x cap = $2,000 notional): collateral $1,000 = all cash -> nothing free at month end
    w0 = [w for w in r["withdrawals"] if not w.get("after_reduction")][0]
    assert w0["paid"] == 0 and w0["due"] > 0, w0
    red = [w for w in r["withdrawals"] if w.get("after_reduction")]
    assert red and red[0]["paid"] > 0, r["withdrawals"]
    assert r["trades"][0]["reason"] in ("window_end",)


@case("same-timestamp conflict: higher timeframe wins; equal tf needs a unique regime-compatible side")
def _():
    b = mk_bars("X", [100] * 6)
    t1 = T0 + timedelta(hours=4)
    a = sig(t_close=t1, stop=95.0, sid="L1h", tf="1h", regime_match=True)
    s = sig(t_close=t1, side="SHORT", stop=105.0, sid="S4h", tf="4h", regime_match=False)
    r = run(b, [a, s], n_hours=6)
    assert r["trades"][0]["sid"] == "S4h"
    a2 = dict(a, sid="L", regime_match=False)
    s2 = dict(s, sid="S", tf="1h", regime_match=False)
    r = run(b, [a2, s2], n_hours=6)
    assert not r["trades"] and any(k[3] == "side_conflict" for k in r["skips"])
    a3 = dict(a2, regime_match=True)
    r = run(b, [a3, s2], n_hours=6)
    assert r["trades"][0]["sid"] == "L"


@case("trailing stop updates on completed bars only")
def _():
    rows = [100, 100, (100, 110, 99, 110), (110, 110, 104, 105), 105]
    b = mk_bars("X", rows)
    r = run(b, [sig(stop=95.0, trail_k=5.0, atr=1.0)], n_hours=5)
    tr = r["trades"][0]
    # after bar 2 closes at 110 the stop moves to 105; bar 3 low 104 hits it at 105
    assert tr["reason"] == "stop" and abs(tr["net"] - 4 * 5) < 1e-9, tr


@case("time exit fills at the next open")
def _():
    rows = [100, 100, 101, 102, (103, 103, 103, 103), 104]
    b = mk_bars("X", rows)
    r = run(b, [sig(stop=95.0, max_hold_s=2 * H)], n_hours=6)
    tr = r["trades"][0]
    # entered at the 01:00 open; 2h held at the 02:00 bar close; exits at the 03:00 open (102), bar ends 04:00
    assert tr["reason"] == "time" and tr["exit_t"] == int((T0 + timedelta(hours=4)).timestamp()), tr
    assert abs(tr["net"] - 4 * 2.0) < 1e-9


if __name__ == "__main__":
    try:
        from signals2 import label_before
        closes = [100, 200, 300]
        labels = ["A", "B", "C"]
        assert label_before(closes, labels, 200) == "A"      # bar closing exactly at the signal close is not used
        assert label_before(closes, labels, 201) == "B"
        assert label_before(closes, labels, 100) == "NO_TRADE"
        print("ok regime label strictly before the signal close")
    except ImportError:
        print("signals2 not present yet")
