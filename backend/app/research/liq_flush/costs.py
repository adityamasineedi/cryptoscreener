"""D144 costs and the E34 sizing constants (GATES E32 and E34).

Taker 0.05% per side. Slippage 2 bp per side, 5 bp on the thin-book coins.
No fill is zero-slippage. Planned risk is $20 on a virtual $1,000, and
notional is capped at 2× ($2,000). R is net / $20, including capped trades.
"""

from __future__ import annotations

TAKER = 0.0005
SLIP_BASE = 0.0002
SLIP_THIN = 0.0005
THIN = frozenset((
    "AAVEUSDT",
    "APTUSDT",
    "ARBUSDT",
    "DOTUSDT",
    "ETCUSDT",
    "OPUSDT",
    "FILUSDT",
))

RISK_USD = 20.0
PRINCIPAL_USD = 1000.0
LEVERAGE = 2.0
NOTIONAL_CAP_USD = PRINCIPAL_USD * LEVERAGE
TARGET_R = 2.0
TIME_BARS = 48
ATR_PERIOD = 14
ATR_BUFFER = 0.2
P99_HOURS = 720
P99_MIN_PRESENT = 648
WICK_MIN = 0.5
READ_TRADES = 30
READ_MIN_PF = 1.10
INCONCLUSIVE_MONTHS = 12
# Wilder ATR is recursive from the first bar. The origin is fixed on the
# first run so later bars do not re-seed it. 40 days is warmup only.
PRICE_WARMUP = 40 * 24
SEAL_GRACE_SECONDS = 60
HOUR_MS = 3_600_000

# Public market-data endpoints. No API key. Settings/.env are not read.
REST_BASE = "https://fapi.binance.com"
WS_BASE = "wss://fstream.binance.com"


def slippage(symbol: str) -> float:
    return SLIP_THIN if str(symbol).upper() in THIN else SLIP_BASE


def size_qty(fill: float, stop: float) -> tuple[float, bool]:
    """Return (qty, capped). Qty is the min of $20 / stop-distance and $2,000 / fill."""
    dist = abs(fill - stop)
    if dist <= 0 or fill <= 0:
        return 0.0, False
    q_risk = RISK_USD / dist
    q_cap = NOTIONAL_CAP_USD / fill
    return min(q_risk, q_cap), q_cap < q_risk
