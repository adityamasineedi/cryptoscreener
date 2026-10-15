"""Hand check of 3 trades per engine: net = gross + fees, fee = rate x executed notional.
Arithmetic here is independent of the engine code. Writes _sanity.json next to this file."""
import json, sys
from pathlib import Path
OUT = Path(__file__).resolve().parent
RATE = 0.0004
def check(path, picks, engine):
    trades = json.loads((OUT / path).read_text(encoding="utf-8"))
    res = []
    for i in picks:
        if i >= len(trades):
            continue
        t = trades[i]
        q, e, x = t["quantity"], t["entry_price"], t["exit_price"]
        sign = 1 if t["direction"] == "LONG" else -1
        gross = sign * (x - e) * q
        fee = -(abs(e * q) + abs(x * q)) * RATE
        risk = abs(e - t["stop_price"]) * q
        row = {
            "file": path, "index": i, "direction": t["direction"], "entry_time": t["entry_time"],
            "entry": e, "exit": x, "qty": q, "exit_kind": t.get("exit_reason") or t.get("outcome"),
            "gross_recomputed": gross, "gross_recorded": t["gross_pnl_usd"],
            "fee_recomputed": fee, "fee_recorded": t["fees_usd"],
            "net_recorded": t["net_pnl_usd"], "gross_plus_fees": t["gross_pnl_usd"] + t["fees_usd"],
            "r_recomputed": (gross + fee) / risk if risk else None, "r_recorded": t["r_net"],
            "executed_notional": abs(e * q) + abs(x * q),
            "fee_over_notional": -t["fees_usd"] / (abs(e * q) + abs(x * q)),
        }
        row["ok_gross"] = abs(gross - t["gross_pnl_usd"]) < 1e-6
        row["ok_fee"] = abs(fee - t["fees_usd"]) < 1e-6
        row["ok_net"] = abs(row["gross_plus_fees"] - t["net_pnl_usd"]) < 1e-6
        row["ok_r"] = row["r_recomputed"] is not None and abs(row["r_recomputed"] - t["r_net"]) < 1e-6
        if engine == "intraday":
            raw = t["entry_bar_open_raw"]
            row["entry_over_raw_open"] = e / raw
            row["ok_entry_slippage_5bps"] = abs(e / raw - (1 + sign * 0.0005)) < 1e-9
        res.append(row)
    return res
out = {
    "intraday": check(sys.argv[1], [0, 7, 100], "intraday"),
    "scalp": check(sys.argv[2], [0, 1, 2], "scalp"),
}
out["all_ok"] = all(v for part in ("intraday", "scalp") for r in out[part] for k, v in r.items() if k.startswith("ok_"))
out["counts"] = {k: len(out[k]) for k in ("intraday", "scalp")}
(OUT / "_sanity.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
for part in ("intraday", "scalp"):
    for r in out[part]:
        print(part, r["index"], r["direction"], r["exit_kind"], "gross", round(r["gross_recorded"], 6), "fee", round(r["fee_recorded"], 6),
              "net", round(r["net_recorded"], 6), "fee/notional", round(r["fee_over_notional"], 8),
              {k: v for k, v in r.items() if k.startswith("ok_")})
print("all_ok", out["all_ok"], out["counts"])
