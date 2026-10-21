"""OPT-IN Binance Futures TESTNET integration test (D128c, D131). Never runs by default, in CI or in the suites.

It runs only when ALL of these are set in the process environment by the owner (the test never reads .env):
    RUN_TESTNET_INTEGRATION=1
    EXECUTION_TESTNET_ENABLED=true
    BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET  (testnet keys; never printed)
and the constitution in force is v2.5 or later. The client refuses any non-testnet URL.

It places one tiny BTCUSDT bracket on the testnet, checks protection and reconciliation, then closes it.
"""

from __future__ import annotations

import os
import time

import pytest

RUN = (
    os.environ.get("RUN_TESTNET_INTEGRATION") == "1"
    and os.environ.get("EXECUTION_TESTNET_ENABLED", "").lower() == "true"
    and bool(os.environ.get("BINANCE_TESTNET_API_KEY"))
    and bool(os.environ.get("BINANCE_TESTNET_API_SECRET"))
    and not os.environ.get("CI")
)

pytestmark = pytest.mark.skipif(not RUN, reason="opt-in testnet integration (RUN_TESTNET_INTEGRATION=1 + testnet keys)")


def test_tiny_bracket_round_trip_on_testnet():
    from app.execution.adapter import ExecutionAdapter
    from app.execution.models import STOP_MARKET
    from app.execution.testnet_client import DEFAULT_CONSTITUTION_PATH, BinanceFuturesTestnetClient

    client = BinanceFuturesTestnetClient(
        enabled=True,
        base_url=os.environ.get("BINANCE_TESTNET_FUTURES_REST", "https://testnet.binancefuture.com"),
        api_key=os.environ["BINANCE_TESTNET_API_KEY"],
        api_secret=os.environ["BINANCE_TESTNET_API_SECRET"],
        constitution_path=os.environ.get("EXECUTION_CONSTITUTION_PATH", DEFAULT_CONSTITUTION_PATH),
    )
    seen: list[dict] = []
    ad = ExecutionAdapter(client, mismatch_hook=seen.append)
    signal = f"TESTNET|BTCUSDT|{int(time.time())}"
    b = ad.open_bracket(strategy="D128C_IT", signal_key=signal, symbol="BTCUSDT", side="LONG", quantity=0.002,
                        stop_price=1000.0, tp_price=None)
    try:
        assert b.entry.executed_qty > 0
        stops = [o for o in client.open_orders("BTCUSDT") if o.type == STOP_MARKET and o.reduce_only]
        assert stops and abs(sum(o.quantity for o in stops) - b.entry.executed_qty) < 1e-9
        again = ad.open_bracket(strategy="D128C_IT", signal_key=signal, symbol="BTCUSDT", side="LONG", quantity=0.002,
                                stop_price=1000.0, tp_price=None)
        assert again.entry.order_id == b.entry.order_id  # idempotent
    finally:
        ad.close_bracket(b, reason="integration_test_cleanup")
