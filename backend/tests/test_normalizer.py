from __future__ import annotations

from app.ingestion.normalizer import (
    normalize_futures_ticker_array,
    normalize_mark_price_array,
)


def test_normalize_ticker_array_real_shape():
    payload = [
        {
            "e": "24hrTicker",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "c": "42000.5",
            "P": "1.25",
            "h": "43000",
            "l": "41000",
            "v": "1000.5",
            "q": "42000000",
            "n": 12345,
        },
        {
            "e": "24hrTicker",
            "E": 1700000000000,
            "s": "ETHUSDT",
            "c": "2200.1",
            "P": "-0.5",
            "h": "2300",
            "l": "2100",
            "v": "5000",
            "q": "11000000",
            "n": 555,
        },
    ]
    ticks = normalize_futures_ticker_array(payload)
    assert len(ticks) == 2
    assert ticks[0].symbol == "BTCUSDT"
    assert ticks[0].price == 42000.5
    assert ticks[0].price_change_pct_24h == 1.25
    assert ticks[0].source == "binance_ws"
    assert ticks[1].symbol == "ETHUSDT"


def test_normalize_mark_price_array():
    payload = [
        {
            "e": "markPriceUpdate",
            "E": 1700000000000,
            "s": "BTCUSDT",
            "p": "42001",
            "i": "42000",
            "r": "0.0001",
            "T": 1700003600000,
        }
    ]
    marks = normalize_mark_price_array(payload)
    assert len(marks) == 1
    assert marks[0].symbol == "BTCUSDT"
    assert marks[0].mark_price == 42001.0
    assert marks[0].funding_rate == 0.0001


def test_normalize_rejects_malformed():
    assert normalize_futures_ticker_array([{"s": "BTCUSDT"}]) == []
    assert normalize_mark_price_array([{"s": "BTCUSDT"}]) == []


def test_normalize_rest_ticker_and_premium():
    from app.ingestion.normalizer import (
        normalize_rest_premium_index,
        normalize_rest_ticker_24hr,
    )

    tick = normalize_rest_ticker_24hr(
        {
            "symbol": "BTCUSDT",
            "lastPrice": "65000.12",
            "priceChangePercent": "1.5",
            "highPrice": "66000",
            "lowPrice": "64000",
            "volume": "100",
            "quoteVolume": "6500000",
            "count": 10,
            "closeTime": 1700000000000,
        }
    )
    assert tick is not None
    assert tick.price == 65000.12
    assert tick.source == "binance_rest"
    # Receive-time stamp (not stale exchange closeTime) so REST stays LIVE/CACHED
    from datetime import datetime, timezone

    assert abs((datetime.now(timezone.utc) - tick.timestamp).total_seconds()) < 5

    mark = normalize_rest_premium_index(
        {
            "symbol": "BTCUSDT",
            "markPrice": "65001",
            "indexPrice": "65000",
            "lastFundingRate": "0.0001",
            "nextFundingTime": 1700003600000,
            "time": 1700000000000,
        }
    )
    assert mark is not None
    assert mark.funding_rate == 0.0001
    assert abs((datetime.now(timezone.utc) - mark.timestamp).total_seconds()) < 5
