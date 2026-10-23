"""E34 universe: the 26 cap-book USDT perps other than BTC (D108).

Hard-coded so a later edit to the cap book does not move this forward test.
"""

from __future__ import annotations

# Alphabetical, as listed in GATES E34.
UNIVERSE: tuple[str, ...] = (
    "AAVEUSDT",
    "ADAUSDT",
    "APTUSDT",
    "ARBUSDT",
    "ATOMUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "DOTUSDT",
    "ETCUSDT",
    "ETHUSDT",
    "FILUSDT",
    "ICPUSDT",
    "INJUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "NEARUSDT",
    "OPUSDT",
    "RENDERUSDT",
    "SOLUSDT",
    "SUIUSDT",
    "TONUSDT",
    "TRXUSDT",
    "UNIUSDT",
    "XRPUSDT",
)

assert len(UNIVERSE) == 26
assert "BTCUSDT" not in UNIVERSE
