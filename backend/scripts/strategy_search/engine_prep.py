"""Existing-engine signal sources for the search (COMBO_01, OSE_A/B/D), via sleeve_study/a34_signals.py.

The engines' own structural stop is kept as a distance from the signal close (scaled for the +/-20% gate);
their fills and exits are not used. Output: out/raw/<universe>_ENG_<coin>.pkl keyed like strategies raw signals.
Usage: python -B engine_prep.py discovery|holdout --shard i/n
"""
from __future__ import annotations

import argparse
import pickle
import sys
from datetime import timedelta

from ss_common import OUT, TF_SEC, candles, universe, WARMUP_DAYS
import a34_signals as A
import strategies as S

ENGINE_TASKS = [("combo", "COMBO_01", "1h"), ("combo", "COMBO_01", "4h"), ("combo", "COMBO_01", "1d"),
                ("ose", "OSE_A_corrected_baseline", "1h"), ("ose", "OSE_A_corrected_baseline", "4h"),
                ("ose", "OSE_B_acceptance", "1h"), ("ose", "OSE_B_acceptance", "4h"),
                ("ose", "OSE_D_faster_confirmation", "1h"), ("ose", "OSE_D_faster_confirmation", "4h")]
FAM = {"COMBO_01": "ENG_C01", "OSE_A_corrected_baseline": "ENG_OSEA", "OSE_B_acceptance": "ENG_OSEB",
       "OSE_D_faster_confirmation": "ENG_OSED"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--shard", default="0/1")
    a = ap.parse_args()
    coins, (start, end) = universe(a.name)
    i, n = (int(x) for x in a.shard.split("/"))
    phase = "SEARCH_" + a.name.upper()
    A.PHASE_END[phase] = (end - timedelta(days=1)).date().isoformat()
    A.STUDY_START = start  # engine windows start where the universe window starts
    A.SDIR = OUT / "engine_signals"
    A._init()
    from search import regime_at, btc_at
    for coin in coins[i::n]:
        f = OUT / "raw" / f"{a.name}_ENG_{coin}.pkl"
        if f.exists():
            continue
        out = {}
        for src, sid, tf in ENGINE_TASKS:
            task, st, cnt, sec = A.run_task((src, sid, coin, tf), phase)
            if not st.startswith("OK"):
                print(coin, sid, tf, st[:300], file=sys.stderr, flush=True)
                continue
            import json
            sigs = json.load(open(A._path((src, sid, coin, tf), phase), encoding="utf-8"))["signals"]
            cs = candles(coin, tf, start - timedelta(days=WARMUP_DAYS[tf]), end)
            from datetime import datetime
            opens = [int(datetime.fromisoformat(s["signal_open"]).timestamp()) for s in sigs]
            atrs = S.atr_at(cs, opens)
            rows = []
            for s, ot in zip(sigs, opens):
                side = 1 if s["side"] == "LONG" else -1
                dist = side * (s["ref"] - s["stop"])
                if ot not in atrs or dist <= 0 or not (start.timestamp() <= s["signal_close"] < end.timestamp()):
                    continue
                rows.append({"open_ts": ot, "close_ts": s["signal_close"], "side": side, "ref": s["ref"],
                             "atr": atrs[ot], "stop_dist": dist, "bucket": regime_at(coin, tf, ot), "btc": btc_at(ot)})
            out[(FAM[sid], 0, tf)] = rows
            print(coin, sid, tf, len(rows), round(sec), file=sys.stderr, flush=True)
        f.write_bytes(pickle.dumps(out))


if __name__ == "__main__":
    main()
