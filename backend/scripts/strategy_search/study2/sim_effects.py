"""Effect of each sim2 change on one reference variant (research window, 16 coins, 2% risk) -> out/sim_effects.json.
The original scripts/sleeve_study/sim.py is imported read-only for the baseline row."""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace

from common2 import HERE, OUT, RESEARCH, UNIVERSE, ts
import search2 as S
import signals2 as SG
from metrics2 import metrics
from sim2 import Config, Costs, simulate

REF = "R1-06|s1.5|TRAIL3|btc0"


def old_sim():
    p = HERE.parents[1] / "sleeve_study" / "sim.py"
    spec = importlib.util.spec_from_file_location("sleeve_sim", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["sleeve_sim"] = m
    spec.loader.exec_module(m)
    return m


def main() -> None:
    _, vs = S.load_round(1)
    v = {x["vid"]: x for x in vs}[REF]
    sigs = SG.build(v, UNIVERSE, ts(RESEARCH[0]), ts(RESEARCH[1]))
    b = S.bars()
    rows = {}
    # original sim.py: initial-risk cap $320, 2x, slippage+taker, no funding, no lot, no pauses / dd stop
    om = old_sim()
    ob = om.Bars()
    for c in UNIVERSE:
        d = b.data[(c, "1h")]
        ob.data[(c, "1h")] = d
    osigs = [dict(s, target=None, trail_atr=s.get("trail_k"), regime_bucket=s["regime"]) for s in sigs]
    ores = om.simulate(osigs, ob, UNIVERSE, RESEARCH[0], RESEARCH[1],
                       om.Config(risk_frac=0.02, leverage=2.0, account_risk_cap=320.0, withdraw=True,
                                 costs=om.Costs()))
    net = sum(t["net"] for t in ores["trades"])
    days = (RESEARCH[1] - RESEARCH[0]).days
    rows["sleeve_study_sim.py"] = {"trades": len(ores["trades"]), "net": round(net, 2),
                                   "usd30_10k": round(net * 10 / 16 / days * 30, 2)}
    full = Config(risk_frac=0.02, risk_cap_abs=320.0, lot_step=S.LOT, costs=Costs())
    variants = {
        "sim2_full": full,
        "no_hypothetical_funding": replace(full, costs=Costs(funding_8h=0.0)),
        "no_lot_rounding": replace(full, lot_step={}),
        "no_loss_pauses": replace(full, daily_pause=None, weekly_pause=None),
        "no_dd_stop": replace(full, dd_stop=None),
        "no_account_risk_cap": replace(full, risk_cap_abs=None, risk_cap_frac=1.0),
        "all_new_limits_off": replace(full, costs=Costs(funding_8h=0.0), lot_step={}, daily_pause=None,
                                      weekly_pause=None, dd_stop=None),
    }
    for name, cfg in variants.items():
        res = simulate(sigs, b, UNIVERSE, RESEARCH[0], RESEARCH[1], cfg)
        m = metrics(res)
        rows[name] = {"trades": m["trades"], "net": m["net"], "usd30_10k": m["usd30_10k"],
                      "funding_hyp": m["funding_hyp"], "max_dd_pct": m["max_dd_pct"], "skips": m["skips"]}
        print(name, rows[name], flush=True)
    print("sleeve_study_sim.py", rows["sleeve_study_sim.py"])
    (OUT / "sim_effects.json").write_text(json.dumps({"reference_variant": REF, "rows": rows}, indent=1),
                                          encoding="utf-8")


if __name__ == "__main__":
    main()
