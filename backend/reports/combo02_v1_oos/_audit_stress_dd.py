"""Diagnostic-only: OOS ledger stress + portfolio DD. Does not modify COMBO_02."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

TRADES_PATH = Path(__file__).with_name("oos_trades_20261005T054508Z.json")


def rnet(t: dict) -> float:
    return float(t.get("r_net") if t.get("r_net") is not None else t.get("r_multiple") or 0)


def metrics(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for r in rs if r > 0)
    curve = [0.0]
    for r in rs:
        curve.append(curve[-1] + r)
    peak = 0.0
    dd = 0.0
    for x in curve:
        peak = max(peak, x)
        dd = max(dd, peak - x)
    pos = sum(r for r in rs if r > 0)
    neg = abs(sum(r for r in rs if r < 0))
    pf = (pos / neg) if neg > 0 else None
    streak = 0
    maxs = 0
    for r in rs:
        if r <= 0:
            streak += 1
            maxs = max(maxs, streak)
        else:
            streak = 0
    return {
        "n": n,
        "net": round(sum(rs), 4),
        "avg": round(sum(rs) / n, 4),
        "wr": round(wins / n, 4),
        "pf": round(pf, 4) if pf is not None else None,
        "dd": round(dd, 4),
        "streak": maxs,
    }


def stress_r(
    t: dict,
    *,
    slip_rt: float = 0.0,
    funding_per_8h: float = 0.0,
    tp_slip_r: float = 0.0,
) -> float:
    r = rnet(t)
    risk = float(t.get("risk_usd") or 20)
    notional_e = float(t.get("notional_entry_usd") or t.get("notional") or 0)
    notional_x = float(t.get("notional_exit_usd") or notional_e)
    avg_n = (notional_e + notional_x) / 2.0
    r -= (slip_rt * avg_n) / risk
    hold = float(t.get("holding_bars") or 0)
    periods = hold / 8.0
    r -= (periods * funding_per_8h * avg_n) / risk
    if tp_slip_r and float(t.get("r_multiple") or 0) > 0:
        r -= tp_slip_r
    return r


def main() -> None:
    trades = json.loads(TRADES_PATH.read_text(encoding="utf-8"))["trades"]
    chrono = sorted(
        trades, key=lambda t: (t.get("exit_time") or "", t.get("symbol") or "")
    )
    scenarios = {
        "baseline_file_order": [rnet(t) for t in trades],
        "baseline_chrono_exit": [rnet(t) for t in chrono],
        "conservative_slip_4bps_rt": [
            stress_r(t, slip_rt=0.0004) for t in chrono
        ],
        "conservative_slip_10bps_rt": [
            stress_r(t, slip_rt=0.0010) for t in chrono
        ],
        "funding_1bp_per_8h": [
            stress_r(t, funding_per_8h=0.0001) for t in chrono
        ],
        "funding_3bp_per_8h": [
            stress_r(t, funding_per_8h=0.0003) for t in chrono
        ],
        "tp_haircut_0.05R": [stress_r(t, tp_slip_r=0.05) for t in chrono],
        "combined_slip4_fund1_tp005": [
            stress_r(t, slip_rt=0.0004, funding_per_8h=0.0001, tp_slip_r=0.05)
            for t in chrono
        ],
        "combined_slip10_fund3_tp01": [
            stress_r(t, slip_rt=0.0010, funding_per_8h=0.0003, tp_slip_r=0.10)
            for t in chrono
        ],
    }
    out = {k: metrics(v) for k, v in scenarios.items()}

    # DD trough reconstruction (chrono)
    rs = [rnet(t) for t in chrono]
    curve = [0.0]
    for r in rs:
        curve.append(curve[-1] + r)
    peak = 0.0
    max_dd = 0.0
    trough_i = 0
    for i, x in enumerate(curve):
        peak = max(peak, x)
        if peak - x >= max_dd:
            max_dd = peak - x
            trough_i = i
    peak_i = max(range(0, trough_i + 1), key=lambda i: curve[i])
    seg = chrono[peak_i:trough_i]  # trades after peak equity point
    seg_sym = Counter(t["symbol"] for t in seg)
    multi_loss_days = 0
    multi_loss_r = 0.0
    by_day: dict[str, list] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("exit_time"))[:10]].append(t)
    for _d, ts in by_day.items():
        losses = [t for t in ts if rnet(t) <= 0]
        if len({t["symbol"] for t in losses}) >= 2:
            multi_loss_days += 1
            multi_loss_r += sum(rnet(t) for t in losses)

    report = {
        "scenarios": out,
        "portfolio_chrono_max_dd": round(max_dd, 4),
        "dd_peak_equity_r": round(curve[peak_i], 4),
        "dd_trough_equity_r": round(curve[trough_i], 4),
        "dd_peak_exit": chrono[peak_i - 1]["exit_time"] if peak_i > 0 else None,
        "dd_trough_exit": chrono[trough_i - 1]["exit_time"] if trough_i > 0 else None,
        "dd_segment_n": len(seg),
        "dd_segment_symbols": dict(seg_sym),
        "dd_segment_net_r": round(sum(rnet(t) for t in seg), 4),
        "days_multi_symbol_losses": multi_loss_days,
        "multi_symbol_loss_days_sum_r": round(multi_loss_r, 4),
        "ambiguous_count": sum(1 for t in trades if t.get("ambiguous")),
        "entry_types": dict(Counter(t.get("entry_type") for t in trades)),
        "note": (
            "baseline_file_order matches OOS script (symbol-concatenated). "
            "baseline_chrono_exit is true calendar portfolio order. "
            "Funding/slippage stresses are ledger overlays [Inference], not engine re-runs."
        ),
    }
    out_path = Path(__file__).with_name("audit_stress_dd_latest.json")
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
