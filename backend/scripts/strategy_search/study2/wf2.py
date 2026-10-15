"""Anchored walk-forward inside research (reported, not a gate; PROTOCOL.md section 2).

For each pooled candidate: neighbourhood = every registered screen variant of its base (stop x exit x BTC filter).
For k in F2, F3, F4: pick the variant with the highest summed net over folds before k; record its fold-k net.
Fold nets come from the one continuous research simulation per variant (trades attributed by entry time).
-> out/walkforward.json
"""
from __future__ import annotations

import json

import search2 as S
from common2 import OUT

FOLDS = ["F1", "F2", "F3", "F4"]


def main() -> None:
    pool = json.load(open(OUT / "pool.json", encoding="utf-8"))["pool"]
    scr = S.logged("screen")
    out = []
    for c in pool:
        nb = [r for r in scr if r["vid"].split("|")[0] == c["base"]]
        rows, oos = [], 0.0
        for k in range(1, 4):
            pick = max(nb, key=lambda r: sum(float(r[f]) for f in FOLDS[:k]))
            val = float(pick[FOLDS[k]])
            oos += val
            rows.append({"test_fold": FOLDS[k], "chosen": pick["vid"], "in_sample": round(sum(float(pick[f])
                                                                                                for f in FOLDS[:k]), 2),
                         "oos_net": round(val, 2)})
        own = [r for r in nb if r["vid"] == c["vid"]][0]
        out.append({"candidate": c["vid"], "steps": rows, "wf_oos_net_F2_F4": round(oos, 2),
                    "candidate_net_F2_F4": round(sum(float(own[f]) for f in FOLDS[1:]), 2),
                    "neighbourhood_size": len(nb)})
        print(c["vid"], "WF OOS", round(oos, 2), "candidate", out[-1]["candidate_net_F2_F4"])
    (OUT / "walkforward.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
