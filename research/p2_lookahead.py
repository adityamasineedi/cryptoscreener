"""Phase 2 look-ahead (truncation) test for every structure detector and every hypothesis setup generator.

A detector passes when, for every truncation point T, its outputs computed on bars[0..T] equal its outputs
computed on the full series at indices 0..T (NaN-aware, exact). A setup generator passes when the setups it
emits from data truncated at time tau (only bars CLOSED by tau, every timeframe) equal the full-data setups with
signal time <= tau. A deliberately leaky fractal (pivot usable at the pivot bar) is run as a control and must fail.

Run from repo root, one process per coin (dev segment only):
    backend/.venv/Scripts/python.exe research/p2_lookahead.py BTCUSDT
Writes research/out/p2/lookahead_<coin>.json; research/p2_summarize.py builds the report table.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hypotheses as H  # noqa: E402
import structure as S  # noqa: E402
from common import DEV, OUT, TF_SEC, bars, epoch_s  # noqa: E402

RNG = np.random.default_rng(20261015)
WIN = 600          # exhaustive truncation window (bars)
W0 = 400           # window start (after indicator warm-up)
N_RANDOM = 20      # random truncation points across the whole dev series
N_EVENT = 15       # sampled setups per hypothesis for boundary truncations


def leaky_fractal(h, l, k=3):
    """CONTROL: same pivots as fractal_swings but treated as known at the pivot bar (uses k future bars)."""
    m = len(h)
    ev = []
    for i in range(k, m - k):
        if h[i] > h[i - k:i].max() and h[i] >= h[i + 1:i + k + 1].max():
            ev.append((i, i, float(h[i]), 1))
        if l[i] < l[i - k:i].min() and l[i] <= l[i + 1:i + k + 1].min():
            ev.append((i, i, float(l[i]), -1))
    return S._swing_state(m, ev)


def detectors(x: dict) -> dict[str, dict[str, np.ndarray]]:
    h, l, c, o, v = x["h"], x["l"], x["c"], x["o"], x["v"]
    a = S.atr(h, l, c, 14)
    fr = S.fractal_swings(h, l, 3)
    dc = S.dc_swings(h, l, 2.5 * a)
    zz = S.dc_swings(h, l, np.where(np.isnan(a), np.nan, 0.03 * c))
    dh, dl = S.donchian_prior(h, l, 24)
    return {
        "atr14": {"v": a},
        "atr100": {"v": S.atr(h, l, c, 100)},
        "fractal_k3": fr,
        "dc_2.5atr": dc,
        "zigzag_3pct": zz,
        "breaks_dc": S.breaks(c, dc),
        "breaks_fractal": S.breaks(c, fr),
        "sweeps_fractal": S.sweeps(h, l, c, fr, a),
        "compression": S.compression_breakout(h, l, c, S.atr(h, l, c, 100)),
        "failed_breakout": S.failed_breakouts(h, l, c, a),
        "sd_zones": S.sd_zones(o, h, l, c, a),
        "rel_volume": {"v": S.rel_volume(v, 168)},
        "donchian24": {"hi": dh, "lo": dl},
        "CONTROL_leaky_fractal": leaky_fractal(h, l, 3),
    }


def _eq(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.array_equal(a, b, equal_nan=True))


def arrays(df: pd.DataFrame) -> dict[str, np.ndarray]:
    return {"o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float), "l": df["low"].to_numpy(float),
            "c": df["close"].to_numpy(float), "v": df["volume"].to_numpy(float)}


def detector_test(x: dict, points: list[int]) -> dict[str, dict]:
    full = detectors(x)
    res = {k: {"tested": 0, "fail": 0, "first_fail_T": None} for k in full}
    for T in points:
        tr = detectors({k: v[:T + 1] for k, v in x.items()})
        for name, outs in tr.items():
            ok = all(_eq(outs[key], full[name][key][:T + 1]) for key in outs)
            res[name]["tested"] += 1
            if not ok:
                res[name]["fail"] += 1
                res[name]["first_fail_T"] = res[name]["first_fail_T"] or int(T)
    return res


def daily_z_test(d1: pd.DataFrame, points: list[int]) -> dict:
    c = d1["close"].to_numpy(float)
    full = S.daily_z(c)
    fail = sum(not _eq(S.daily_z(c[:T + 1]), full[:T + 1]) for T in points)
    return {"tested": len(points), "fail": fail}


def asof_test(b: dict, taus: list[int]) -> dict:
    """4h ATR mapped to 1h bars: truncating both series at tau must not change any mapped value <= tau."""
    t1, t4 = epoch_s(b["1h"]["time"]), epoch_s(b["4h"]["time"])
    a4 = S.atr(*(b["4h"][k].to_numpy(float) for k in ("high", "low", "close")))
    full = S.asof_map(t1, 3600, t4, 14400, a4)
    fail = 0
    for tau in taus:
        k1 = t1 + 3600 <= tau
        k4 = t4 + 14400 <= tau
        a4t = S.atr(*(b["4h"][k].to_numpy(float)[k4] for k in ("high", "low", "close")))
        tr = S.asof_map(t1[k1], 3600, t4[k4], 14400, a4t)
        fail += not _eq(tr, full[k1])
    return {"tested": len(taus), "fail": fail}


def truncate(b: dict, tau: int) -> dict:
    return {tf: df[epoch_s(df["time"]) + TF_SEC[tf] <= tau].reset_index(drop=True) for tf, df in b.items()}


def leaky_h1(b: dict) -> pd.DataFrame:
    """CONTROL: H1 built on the leaky fractal."""
    orig = S.fractal_swings
    S.fractal_swings = leaky_fractal
    try:
        return H.h1(b)
    finally:
        S.fractal_swings = orig


def control_h1_at_open(b: dict) -> pd.DataFrame:
    """CONTROL: H1 setups stamped at the signal bar's OPEN although they use its close."""
    d = H.h1(b)
    return d.assign(sig_t=d["sig_t"] - 14400)


def centered_rel_volume(v, n=168):
    m = len(v)
    out = np.full(m, np.nan)
    for i in range(n // 2, m - n // 2):
        w = np.r_[v[i - n // 2:i], v[i + 1:i + 1 + n // 2]]
        med = np.median(w)
        out[i] = v[i] / med if med > 0 else np.nan
    return out


def control_h8_centered(b: dict) -> pd.DataFrame:
    """CONTROL: H8 with relative volume measured on a window centred on the bar (uses 84 future bars)."""
    orig = S.rel_volume
    S.rel_volume = centered_rel_volume
    try:
        return H.h8(b)
    finally:
        S.rel_volume = orig


def setup_test(b: dict, gens: dict, taus_common: list[int]) -> dict[str, dict]:
    out = {}
    for name, g in gens.items():
        full = g(b)
        taus = list(taus_common)
        if len(full):
            pick = full.sample(min(N_EVENT, len(full)), random_state=1)["sig_t"].tolist()
            for s in pick:
                taus += [s - 3600, s, s + 3600, s - 14400]
        fail, first = 0, None
        for tau in taus:
            tr = g(truncate(b, tau)).reset_index(drop=True)
            ref = full[full["sig_t"] <= tau].reset_index(drop=True)
            ok = len(tr) == len(ref) and (len(tr) == 0 or tr.equals(ref) or _frames_close(tr, ref))
            if not ok:
                fail += 1
                first = first or int(tau)
        out[name] = {"setups_full": int(len(full)), "tested": len(taus), "fail": fail, "first_fail_tau": first}
    return out


def _frames_close(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    for col in a.columns:
        x, y = a[col].to_numpy(), b[col].to_numpy()
        if x.dtype.kind in "fc":
            if not np.array_equal(x, y, equal_nan=True):
                return False
        elif not (x == y).all():
            return False
    return True


def main(coin: str) -> None:
    t_start = time.time()
    b = bars(coin, DEV[0], DEV[1])
    res = {"coin": coin, "detectors": {}, "setups": {}}
    for tf in ("1h", "4h"):
        x = arrays(b[tf])
        n = len(x["c"])
        pts = list(range(W0, min(n, W0 + WIN))) + sorted(RNG.integers(W0 + WIN, n, N_RANDOM).tolist())
        res["detectors"][tf] = detector_test(x, pts)
        print(coin, tf, "detectors done", round(time.time() - t_start), "s", flush=True)
    d = b["1d"]
    res["detectors"]["1d"] = {"daily_z": daily_z_test(d, list(range(100, len(d))))}
    t1 = epoch_s(b["1h"]["time"])
    taus = sorted(RNG.choice(t1[500:] + 3600, N_RANDOM, replace=False).tolist())
    res["detectors"]["1h<-4h"] = {"asof_map_atr4": asof_test(b, taus)}
    gens = dict(H.ALL)
    gens["H2_fractal"] = lambda bb: H.h2(bb, "fractal")
    gens["H2_zigzag"] = lambda bb: H.h2(bb, "zz")
    gens["CONTROL_leaky_H1"] = leaky_h1   # renamed INFO_ by --controls (see controls_only)
    # exhaustive: every 1h close over one week in the middle of dev, plus random points
    mid = int(t1[len(t1) // 2] // 3600 * 3600)
    taus_common = [mid + i * 3600 for i in range(168)] + taus
    res["setups"] = setup_test(b, gens, taus_common)
    res["seconds"] = round(time.time() - t_start)
    (OUT / "p2").mkdir(parents=True, exist_ok=True)
    (OUT / "p2" / f"lookahead_{coin}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


def controls_only(coin: str) -> None:
    """Run the two setup-level controls and merge them into an existing lookahead_<coin>.json.
    The leaky-fractal H1 control is kept as INFO: a fractal low at i requires bars i+1..i+k to stay at or
    above it, so knowing it k bars early cannot change any sweep or break event (verified: identical setups)."""
    t_start = time.time()
    f = OUT / "p2" / f"lookahead_{coin}.json"
    res = json.loads(f.read_text(encoding="utf-8"))
    b = bars(coin, DEV[0], DEV[1])
    t1 = epoch_s(b["1h"]["time"])
    taus = sorted(RNG.choice(t1[500:] + 3600, N_RANDOM, replace=False).tolist())
    mid = int(t1[len(t1) // 2] // 3600 * 3600)
    taus_common = [mid + i * 3600 for i in range(168)] + taus
    gens = {"CONTROL_H1_stamped_at_open": control_h1_at_open, "CONTROL_H8_centered_relvol": control_h8_centered}
    res["setups"].update(setup_test(b, gens, taus_common))
    if "CONTROL_leaky_H1" in res["setups"]:
        res["setups"]["INFO_leaky_fractal_H1"] = res["setups"].pop("CONTROL_leaky_H1")
    res["seconds_controls"] = round(time.time() - t_start)
    f.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res["setups"].items() if not k.startswith("H")}, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--controls":
        controls_only(sys.argv[1])
    else:
        main(sys.argv[1])
