from __future__ import annotations

from datetime import datetime, timezone

from app.engines.liquidations.engine import (
    aggregate_windows,
    imbalance_ratio,
    parse_force_order,
)


SAMPLE = {
    "e": "forceOrder",
    "E": 1700000000000,
    "o": {
        "s": "BTCUSDT",
        "S": "SELL",
        "o": "LIMIT",
        "f": "IOC",
        "q": "0.01",
        "p": "65000",
        "ap": "64950",
        "X": "FILLED",
        "l": "0.01",
        "z": "0.01",
        "T": 1700000000000,
    },
}


def test_parse_force_order_wrapped():
    ev = parse_force_order(SAMPLE)
    assert ev is not None
    assert ev.symbol == "BTCUSDT"
    assert ev.side == "SELL"
    assert ev.order_status == "FILLED"
    assert ev.order_type == "LIMIT"
    assert ev.average_price == 64950.0
    assert ev.source == "binance_force_order"
    assert ev.event_key
    assert ev.notional == 64950.0 * 0.01


def test_parse_force_order_rejects_invalid():
    assert parse_force_order({}) is None
    assert parse_force_order({"o": {"s": "X", "p": "0", "q": "1"}}) is None
    assert parse_force_order({"e": "forceOrder", "o": {"s": "X", "p": "1", "q": "0"}}) is None


def test_parse_malformed_message():
    assert parse_force_order({"e": "kline", "k": {}}) is None
    assert parse_force_order({"result": None, "id": 1}) is None


def test_aggregate_and_imbalance():
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


def test_no_fabricated_event_from_empty():
    assert parse_force_order({"e": "forceOrder"}) is None
