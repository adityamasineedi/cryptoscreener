"""Regime-correction comparison (PROTOCOL.md section 3), research window, 2% risk, 16-coin account.

For TPB 4h (anchor 20) and BRKA 4h (n 20), every registered stop/exit/btc tag:
  A  baseline map, longs only           (R1-49 / R1-50)
  B  corrected map, longs only          (R1-03 / R1-09)
  C  corrected map, longs + shorts      (R1-03+R1-04 / R1-09+R1-10, one shared account)
B-A isolates the direction correction; C-B isolates adding shorts. -> out/regime_cmp.json
"""
from __future__ import annotations

import json

import search2 as S
from select2 import run_combo
from common2 import OUT, RESEARCH

PAIRS = {"TPB_4h_a20": ("R1-49", "R1-03", "R1-04"), "BRKA_4h_n20": ("R1-50", "R1-09", "R1-10")}


def main() -> None:
    _, vs = S.load_round(1)
    vm = {v["vid"]: v for v in vs}
    scr = {r["vid"]: r for r in S.logged("screen")}
    out = {}
    for name, (a, b, s) in PAIRS.items():
        rows = []
        for vid in [x for x in vm if x.startswith(b + "|")]:
            tag = vid[len(b):]
            va, vb, vsh = vm[a + tag], vm[b + tag], vm[s + tag]
            _, mc = run_combo([vb, vsh], RESEARCH)
            S.log(dict(vb, vid=f"CMP|{b}+{s}{tag}"), mc, "regime_cmp", "research", 0.02, "base", None, 0.0,
                  "variants_regime_cmp.csv")
            ra, rb = scr[a + tag], scr[b + tag]
            rows.append({"tag": tag.strip("|"), "A_baseline_long": float(ra["usd30_10k"]),
                         "B_corrected_long": float(rb["usd30_10k"]), "C_corrected_long_short": mc["usd30_10k"],
                         "A_trades": int(ra["trades"]), "B_trades": int(rb["trades"]), "C_trades": mc["trades"],
                         "C_net_long": mc["net_long"], "C_net_short": mc["net_short"], "C_F4": mc["folds"]["F4"]})
        n = len(rows)
        out[name] = {"rows": rows, "mean_usd30": {k: round(sum(r[k] for r in rows) / n, 2) for k in
                                                  ("A_baseline_long", "B_corrected_long", "C_corrected_long_short")},
                     "direction_correction_effect": round(sum(r["B_corrected_long"] - r["A_baseline_long"]
                                                              for r in rows) / n, 2),
                     "adding_shorts_effect": round(sum(r["C_corrected_long_short"] - r["B_corrected_long"]
                                                       for r in rows) / n, 2)}
        print(name, out[name]["mean_usd30"], "dir", out[name]["direction_correction_effect"], "shorts",
              out[name]["adding_shorts_effect"])
    (OUT / "regime_cmp.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
