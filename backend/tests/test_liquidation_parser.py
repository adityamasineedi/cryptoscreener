from __future__ import annotations

from app.engines.liquidations.engine import (
    aggregate_windows,
    imbalance_ratio,
    parse_force_order,
)


def test_parse_force_order_wrapped():
    payload = {
        "e": "forceOrder",
        "E": 1700000000000,
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "p": "65000",
            "q": "0.01",
            "T": 1700000000000,
        },
    }
    ev = parse_force_order(payload)
    assert ev is not None
    assert ev.symbol == "BTCUSDT"
    assert ev.side == "SELL"
    assert ev.notional == 650.0


def test_parse_force_order_rejects_invalid():
    assert parse_force_order({}) is None
    assert parse_force_order({"o": {"s": "X", "p": "0", "q": "1"}}) is None


def test_aggregate_and_imbalance():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ev = parse_force_order(
        {
            "e": "forceOrder",
            "o": {
                "s": "ETHUSDT",
                "S": "BUY",
                "p": "2000",
                "q": "2",
                "T": int(now.timestamp() * 1000),
            },
        }
    )
    assert ev is not None
    aggs = aggregate_windows([ev], now=now)
    assert aggs["5m"]["short_liq_notional"] == 4000.0
    imb = imbalance_ratio(50, 100)
    assert imb is not None
    assert imb < 0
