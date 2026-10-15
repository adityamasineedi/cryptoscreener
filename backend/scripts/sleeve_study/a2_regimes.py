"""Agent 2 - regimes. Reuses existing classifiers only (no new labeler).

Per coin, 4h and 1d: app.research.market_structure.structure.build_timeframe_feature_table
 -> snapshot_at_index (PIT-asserted) -> .market_regime  (classify_market_regime inside).
BTC 1d: app.research.passed_regime_backtest.RegimeClock -> btc_daily_regime.daily_trend.
Fixed map (common.bucket). Writes out/regimes/<SYM>_<tf>.json and out/a2_regime_days.json.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

from common import DATA_FROM, OUT, STUDY_END, STUDY_START, STUDY_SYMBOLS, bucket
from _db import load_candles
from app.research.market_structure.regime import classify_market_regime
from app.research.market_structure.structure import build_timeframe_feature_table, snapshot_at_index
from app.research.passed_regime_backtest import RegimeClock
from app.signals.config import SignalConfig

RDIR = OUT / "regimes"
RDIR.mkdir(parents=True, exist_ok=True)

_RAW_FIELDS = (("trend_state", "trend_state"), ("structure_state", "structure_state"), ("direction", "direction"),
               ("bos_state", "bos_state"), ("choch_state", "choch_state"), ("volatility_state", "volatility_state"),
               ("momentum_state", "momentum_state"), ("range_state", "range_state"), ("adx", "adx"),
               ("di_plus", "di_plus"), ("di_minus", "di_minus"), ("efficiency_ratio", "efficiency"),
               ("choppiness_index", "choppiness"), ("atr_percentile", "atr_pct"), ("ema_alignment", "ema_alignment"),
               ("direction_changes", "alternations"))


def label_series(sym: str, tf: str) -> list[dict]:
    candles = load_candles(sym, tf, DATA_FROM, STUDY_END)
    table = build_timeframe_feature_table(candles, tf, symbol=sym)
    out = []
    for i in range(len(candles)):
        close_t = table.close_times[i]
        reg = snapshot_at_index(table, i, decision_time=close_t).market_regime
        hv_side = None
        if reg == "HIGH_VOLATILITY_TREND":
            # The classifier emits HVT for both sides; read the side from its own reason code (diagnostic only).
            raw = {k: getattr(table, a)[i] for k, a in _RAW_FIELDS}
            raw["data_quality_state"] = "OK"
            rc = classify_market_regime(raw, table.config)
            assert rc.market_regime == reg
            hv_side = "BULL" if "CLASSIFIED_HIGH_VOL_BULL_TREND" in rc.reason_codes else "BEAR"
        out.append({"open": table.open_times[i].isoformat(), "close": close_t.isoformat(), "regime": reg,
                    "bucket": bucket(reg), "hv_side": hv_side})
    return out


def main() -> None:
    days, hv = {}, {}
    for sym in STUDY_SYMBOLS:
        for tf in ("4h", "1d"):
            rows = label_series(sym, tf)
            (RDIR / f"{sym}_{tf}.json").write_text(json.dumps(rows), encoding="utf-8")
            cnt: dict = defaultdict(lambda: defaultdict(float))
            hvc: dict = defaultdict(lambda: defaultdict(float))
            per_bar_days = 1.0 if tf == "1d" else 4 / 24
            for r in rows:
                if not (STUDY_START.isoformat() <= r["open"] < STUDY_END.isoformat()):
                    continue
                y = r["open"][:4]
                cnt[y][r["bucket"]] += per_bar_days
                cnt[y]["raw:" + r["regime"]] += per_bar_days
                if r["hv_side"]:
                    hvc[y][r["hv_side"]] += per_bar_days
            days[f"{sym}|{tf}"] = {y: {k: round(v, 2) for k, v in d.items()} for y, d in cnt.items()}
            hv[f"{sym}|{tf}"] = {y: {k: round(v, 2) for k, v in d.items()} for y, d in hvc.items()}
            print(sym, tf, {y: {k: round(v) for k, v in d.items() if not k.startswith("raw:")} for y, d in cnt.items()},
                  "HVT days by side", {y: {k: round(v) for k, v in d.items()} for y, d in hvc.items()}, flush=True)
    btc = load_candles("BTCUSDT", "1d", DATA_FROM, STUDY_END)
    clock = RegimeClock(btc, SignalConfig())
    btc_rows = []
    d = STUDY_START - timedelta(days=400)
    while d < STUDY_END:
        info = clock.at(d)  # trend over daily bars fully closed at 00:00 UTC of day d
        btc_rows.append({"day": d.date().isoformat(), "trend": info["trend"], "as_of_bar": info["as_of_bar"]})
        d += timedelta(days=1)
    (RDIR / "BTC_daily_trend.json").write_text(json.dumps(btc_rows), encoding="utf-8")
    bcnt: dict = defaultdict(lambda: defaultdict(int))
    for r in btc_rows:
        if r["day"] >= STUDY_START.date().isoformat():
            bcnt[r["day"][:4]][r["trend"]] += 1
    (OUT / "a2_regime_days.json").write_text(json.dumps({
        "functions_called": [
            "app.research.market_structure.structure.build_timeframe_feature_table",
            "app.research.market_structure.structure.snapshot_at_index",
            "app.research.market_structure.regime.classify_market_regime (inside snapshot_at_index; re-called only on HVT bars to read the side reason code)",
            "app.research.passed_regime_backtest.RegimeClock.at",
            "app.research.btc_daily_regime.daily_trend (inside RegimeClock)",
        ],
        "map": {"UPTREND": ["BULL_TREND", "HIGH_VOLATILITY_TREND"], "DOWNTREND": ["BEAR_TREND"],
                "RANGE": ["RANGE", "HIGH_VOLATILITY_RANGE", "LOW_VOLATILITY_COMPRESSION"],
                "NO_TRADE": ["CHOPPY", "TRANSITION", "UNKNOWN"]},
        "days_per_coin_tf_year": days, "hvt_days_by_side": hv,
        "btc_daily_trend_days_per_year": {y: dict(v) for y, v in bcnt.items()},
    }, indent=1), encoding="utf-8")
    print("BTC", {y: dict(v) for y, v in bcnt.items()})


if __name__ == "__main__":
    main()
