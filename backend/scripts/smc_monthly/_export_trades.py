import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from smc_long_strategy import Params, read_ohlcv, run_backtest

HERE = Path(__file__).parent
df = read_ohlcv(HERE / "btc_ohlcv.csv")
df = df.loc["2020-01-01":"2026-12-31"]
trades, _, _ = run_backtest(df, Params())
out = HERE / "trades.csv"
trades.to_csv(out, index=False)
print(len(trades), round(trades["R"].sum(), 2), out)
