"""Causal regime labels for study 2 (existing classifiers, unchanged; research mapping only).

Calls (same as sleeve_study/a2_regimes.py and strategy_search/regimes_ext.py):
  app.research.market_structure.structure.build_timeframe_feature_table(candles, tf, symbol)
  -> snapshot_at_index(table, i, decision_time=close_time).market_regime        (PIT-asserted)
  HIGH_VOLATILITY_TREND side read from app.research.market_structure.regime.classify_market_regime(...)
  reason codes CLASSIFIED_HIGH_VOL_BULL_TREND / CLASSIFIED_HIGH_VOL_BEAR_TREND (regime.py lines 229-243).
BTC daily trend: app.research.passed_regime_backtest.RegimeClock(btc_1d, SignalConfig()).by_count(n)
  -> btc_daily_regime.daily_trend over the first n closed daily bars.

Stored per bar: close (epoch s), raw regime, hv side. Mappings are applied in signals2.py:
  MAP_BASELINE  (sleeve-study map, flawed): UPTREND = BULL_TREND + every HIGH_VOLATILITY_TREND; DOWNTREND = BEAR_TREND
  MAP_CORRECTED (study 2):                  UPTREND = BULL_TREND + HVT(bull reason); DOWNTREND = BEAR_TREND + HVT(bear reason)
  both: RANGE = RANGE, HIGH_VOLATILITY_RANGE, LOW_VOLATILITY_COMPRESSION; CHOPPY/TRANSITION/UNKNOWN = no trade.
Usage: python -B regimes2.py --shard i/n      (idempotent per coin/tf)
"""
from __future__ import annotations

import argparse
import json

from common2 import OUT, UNIVERSE, candles, COMMON
from app.research.market_structure.regime import classify_market_regime
from app.research.market_structure.structure import build_timeframe_feature_table, snapshot_at_index
from app.research.passed_regime_backtest import RegimeClock
from app.signals.config import SignalConfig

RDIR = OUT / "regimes"
RAW_FIELDS = (("trend_state", "trend_state"), ("structure_state", "structure_state"), ("direction", "direction"),
              ("bos_state", "bos_state"), ("choch_state", "choch_state"), ("volatility_state", "volatility_state"),
              ("momentum_state", "momentum_state"), ("range_state", "range_state"), ("adx", "adx"),
              ("di_plus", "di_plus"), ("di_minus", "di_minus"), ("efficiency_ratio", "efficiency"),
              ("choppiness_index", "choppiness"), ("atr_percentile", "atr_pct"), ("ema_alignment", "ema_alignment"),
              ("direction_changes", "alternations"))


def label(coin: str, tf: str) -> list[list]:
    cs = candles(coin, tf)
    table = build_timeframe_feature_table(cs, tf, symbol=coin)
    rows = []
    for i in range(len(cs)):
        ct = table.close_times[i]
        reg = snapshot_at_index(table, i, decision_time=ct).market_regime
        hv = ""
        if reg == "HIGH_VOLATILITY_TREND":
            raw = {k: getattr(table, a)[i] for k, a in RAW_FIELDS}
            raw["data_quality_state"] = "OK"
            rc = classify_market_regime(raw, table.config)
            assert rc.market_regime == reg, (coin, tf, i)
            if "CLASSIFIED_HIGH_VOL_BULL_TREND" in rc.reason_codes:
                hv = "BULL"
            elif "CLASSIFIED_HIGH_VOL_BEAR_TREND" in rc.reason_codes:
                hv = "BEAR"
            else:
                hv = "?"  # ambiguous -> no trade under the corrected map
        rows.append([int(ct.timestamp()), reg, hv])
    return rows


def btc_daily() -> None:
    f = RDIR / "BTC_daily_trend.json"
    if f.exists():
        return
    btc = candles("BTCUSDT", "1d")
    clock = RegimeClock(btc, SignalConfig())
    rows = []
    for n in range(1, len(btc) + 1):  # trend after the n-th daily bar has closed
        close = int(btc[n - 1]["time"].timestamp()) + 86400
        rows.append([close, clock.by_count(n)["trend"]])
    f.write_text(json.dumps(rows), encoding="utf-8")
    print("BTC daily", len(rows), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1")
    a = ap.parse_args()
    i, n = (int(x) for x in a.shard.split("/"))
    RDIR.mkdir(parents=True, exist_ok=True)
    if i == 0:
        btc_daily()
    for coin in UNIVERSE[i::n]:
        for tf in ("4h", "1d"):
            f = RDIR / f"{coin}_{tf}.json"
            if f.exists():
                continue
            rows = label(coin, tf)
            f.write_text(json.dumps(rows), encoding="utf-8")
            print(coin, tf, len(rows), rows[-1][0] if rows else None, flush=True)


if __name__ == "__main__":
    main()
