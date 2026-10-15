"""Regime labels for the strategy search (research copy; classifiers unchanged).

Same calls as sleeve_study/a2_regimes.py (build_timeframe_feature_table -> snapshot_at_index ->
classify_market_regime), extended to 2026-10-08, with HIGH_VOLATILITY_TREND split by its own reason code:
  UPTREND   = BULL_TREND, HIGH_VOLATILITY_TREND with CLASSIFIED_HIGH_VOL_BULL_TREND
  DOWNTREND = BEAR_TREND, HIGH_VOLATILITY_TREND with CLASSIFIED_HIGH_VOL_BEAR_TREND
  RANGE     = RANGE, HIGH_VOLATILITY_RANGE, LOW_VOLATILITY_COMPRESSION
  NO_TRADE  = CHOPPY, TRANSITION, UNKNOWN
Usage: python -B regimes_ext.py discovery|holdout
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import timedelta

from ss_common import OUT, universe, candles, UTC
from a2_regimes import _RAW_FIELDS
from app.research.market_structure.regime import classify_market_regime
from app.research.market_structure.structure import build_timeframe_feature_table, snapshot_at_index
from app.research.passed_regime_backtest import RegimeClock
from app.signals.config import SignalConfig

RDIR = OUT / "regimes"
DATA_FROM_DAYS = 900


def bucket(regime: str, hv_side: str | None) -> str:
    if regime == "BULL_TREND" or (regime == "HIGH_VOLATILITY_TREND" and hv_side == "BULL"):
        return "UPTREND"
    if regime == "BEAR_TREND" or (regime == "HIGH_VOLATILITY_TREND" and hv_side == "BEAR"):
        return "DOWNTREND"
    if regime in {"RANGE", "HIGH_VOLATILITY_RANGE", "LOW_VOLATILITY_COMPRESSION"}:
        return "RANGE"
    return "NO_TRADE"


def label(coin: str, tf: str, start, end) -> list[dict]:
    cs = candles(coin, tf, start - timedelta(days=DATA_FROM_DAYS), end)
    table = build_timeframe_feature_table(cs, tf, symbol=coin)
    out = []
    for i in range(len(cs)):
        ct = table.close_times[i]
        reg = snapshot_at_index(table, i, decision_time=ct).market_regime
        hv = None
        if reg == "HIGH_VOLATILITY_TREND":
            raw = {k: getattr(table, a)[i] for k, a in _RAW_FIELDS}
            raw["data_quality_state"] = "OK"
            rc = classify_market_regime(raw, table.config)
            hv = "BULL" if "CLASSIFIED_HIGH_VOL_BULL_TREND" in rc.reason_codes else "BEAR"
        out.append({"close": int(ct.timestamp()), "regime": reg, "hv": hv, "bucket": bucket(reg, hv)})
    return out


def main(name: str) -> None:
    coins, (start, end) = universe(name)
    RDIR.mkdir(parents=True, exist_ok=True)
    days = {}
    for c in coins:
        for tf in ("4h", "1d"):
            rows = label(c, tf, start, end)
            (RDIR / f"{c}_{tf}.json").write_text(json.dumps(rows), encoding="utf-8")
            cnt = defaultdict(float)
            for r in rows:
                if start.timestamp() < r["close"] <= end.timestamp():
                    cnt[r["bucket"]] += 1.0 if tf == "1d" else 4 / 24
            days[f"{c}|{tf}"] = {k: round(v, 1) for k, v in cnt.items()}
            print(c, tf, days[f"{c}|{tf}"], flush=True)
    if name == "discovery":
        btc = candles("BTCUSDT", "1d", start - timedelta(days=DATA_FROM_DAYS), end)
        clock = RegimeClock(btc, SignalConfig())
        rows, d = [], start - timedelta(days=30)
        while d < end:
            rows.append({"day": d.date().isoformat(), "trend": clock.at(d)["trend"]})
            d += timedelta(days=1)
        (RDIR / "BTC_daily_trend.json").write_text(json.dumps(rows), encoding="utf-8")
    (OUT / f"regime_days_{name}.json").write_text(json.dumps(days, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
