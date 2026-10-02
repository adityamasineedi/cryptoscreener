from __future__ import annotations

import math

from app.ingestion.klines import (
    detect_gaps,
    generate_kline_streams,
    normalize_rest_kline,
    normalize_ws_kline,
    required_connections,
    shard_streams,
    stream_name,
)
from app.models.ohlcv import Candle
from datetime import datetime, timedelta, timezone


TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
MAX_STREAMS = 900


def test_stream_name_format():
    assert stream_name("BTCUSDT", "1m") == "btcusdt@kline_1m"
    assert stream_name("ETHUSDT", "1D") == "ethusdt@kline_1d"


def test_sharding_527_symbols_6_tfs():
    symbols = [f"COIN{i}USDT" for i in range(527)]
    streams = generate_kline_streams(symbols, TIMEFRAMES)
    assert len(streams) == 527 * 6
    # no duplicates
    assert len(streams) == len(set(streams))

    n_conn = required_connections(len(streams), MAX_STREAMS)
    assert n_conn == math.ceil(3162 / 900)
    assert n_conn == 4

    shards = shard_streams(streams, max_streams_per_connection=MAX_STREAMS)
    assert len(shards) == 4
    assert sum(s.stream_count for s in shards) == 3162
    assert all(s.stream_count <= MAX_STREAMS for s in shards)
    # Last shard holds remainder
    assert shards[-1].stream_count == 3162 - 3 * 900


def test_sharding_dynamic_not_hardcoded():
    streams = generate_kline_streams(["BTCUSDT", "ETHUSDT"], ["1m", "5m"])
    shards = shard_streams(streams, max_streams_per_connection=3)
    assert len(shards) == math.ceil(4 / 3)
    assert shards[0].stream_count == 3
    assert shards[1].stream_count == 1


def test_normalize_ws_kline():
    payload = {
        "e": "kline",
        "E": 1_700_000_000_000,
        "s": "BTCUSDT",
        "k": {
            "t": 1_700_000_000_000,
            "T": 1_700_000_059_999,
            "s": "BTCUSDT",
            "i": "1m",
            "o": "100",
            "h": "110",
            "l": "90",
            "c": "105",
            "v": "12.5",
            "q": "1250",
            "n": 42,
            "V": "6",
            "Q": "600",
            "x": True,
        },
    }
    c = normalize_ws_kline(payload)
    assert c is not None
    assert c.symbol == "BTCUSDT"
    assert c.timeframe == "1m"
    assert c.is_closed is True
    assert c.open == 100
    assert c.close == 105
    assert c.trade_count == 42


def test_normalize_rest_kline():
    row = [
        1_700_000_000_000,
        "1",
        "2",
        "0.5",
        "1.5",
        "100",
        1_700_000_059_999,
        "150",
        10,
        "40",
        "60",
        "0",
    ]
    c = normalize_rest_kline("ETHUSDT", "15m", row)
    assert c is not None
    assert c.symbol == "ETHUSDT"
    assert c.timeframe == "15m"
    assert c.is_closed is True
    assert c.volume == 100


def test_gap_detection():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in (0, 1, 3, 4):  # missing i=2
        ot = base + timedelta(minutes=i)
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe="1m",
                open_time=ot,
                close_time=ot + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=1,
                high=2,
                low=0.5,
                close=1.5,
                volume=10,
                is_closed=True,
                timestamp=ot,
                source="test",
            )
        )
    gaps = detect_gaps(candles, "1m")
    assert len(gaps) == 1
    assert gaps[0].expected_open_time == base + timedelta(minutes=2)


def test_malformed_kline_ignored():
    assert normalize_ws_kline({"foo": 1}) is None
    assert normalize_rest_kline("BTCUSDT", "1m", []) is None
