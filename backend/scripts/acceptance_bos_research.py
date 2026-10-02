"""Controlled acceptance sample for BOS research.

Pads the known HH/HL structure fixture so outcomes can resolve.
Production research API reads real Binance candles from ohlcv_store only —
this script never feeds synthetic candles into live stores.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_setup_signals import _ts, make_hh_hl_series  # noqa: E402

from app.research.bos_combinations import COMBINATIONS  # noqa: E402
from app.research.combination_backtest import (  # noqa: E402
    compare_combinations,
    run_oos_split_backtest,
)
from app.research.config import ResearchConfig  # noqa: E402
from app.signals.config import SignalConfig  # noqa: E402


def make_controlled_series(n_pad: int = 80) -> list[dict]:
    s = make_hh_hl_series()
    last = float(s[-1]["close"])
    for i in range(len(s), len(s) + n_pad):
        o = last
        c = last + 0.2
        h = c + 1.0
        l = o - 2.0
        s.append(
            {
                "time": _ts(i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1000.0,
            }
        )
        last = c
    return s


def main() -> None:
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    timeframes = ["15m", "1h"]
    cfg = ResearchConfig(min_bars=20)
    sc = SignalConfig()
    print("ACCEPTANCE — research comparison (no ranking / no profitability claim)")
    print("Note: controlled structural fixture for correctness; live API uses real OHLCV.")
    for sym in symbols:
        for tf in timeframes:
            candles = make_controlled_series()
            cmp = compare_combinations(
                sym,
                tf,
                candles,
                list(COMBINATIONS),
                signal_config=sc,
                research_config=cfg,
            )
            print(
                f"{sym} {tf}: combinations={cmp['combinations_tested']} "
                f"label={cmp['label']}"
            )
            for row in cmp["rows"]:
                print(
                    f"  {row['combination_id']} setups={row['sample_size']} "
                    f"avgR={row['average_R']} pf={row['profit_factor']}"
                )
            oos = run_oos_split_backtest(
                sym, tf, candles, "COMBO_01", signal_config=sc, research_config=cfg
            )
            print("  OOS periods:", list(oos["periods"].keys()))
            for label, payload in oos["periods"].items():
                print(f"    {label} sample_size={payload.get('sample_size')}")
    print("Done. Live signal state untouched. No combination declared best.")


if __name__ == "__main__":
    main()
