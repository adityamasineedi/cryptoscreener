"""Performance-path correctness tests (no strategy-rule changes)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research.bos_strategy_comparison.htf import (
    as_of_index_at_or_before,
    build_htf_as_of_index_map,
    precompute_htf_trend_cache,
    trend_at_as_of,
)
from app.research.data_cache.cache_key import make_ohlcv_cache_key
from app.research.data_cache.golden_equality import (
    compare_ohlcv_rows,
    compare_trades,
    trade_digest,
)
from app.research.data_cache.manifest import OhlcvCacheManifest, write_manifest
from app.research.data_cache.parquet_store import file_sha256, write_ohlcv_parquet
from app.research.data_cache.validation import manifests_compatible
from app.signals.config import SignalConfig


def _candles(n: int, *, minutes: int = 15) -> list[dict]:
    t0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    out = []
    px = 100.0
    for i in range(n):
        ts = t0 + timedelta(minutes=minutes * i)
        o = px
        c = px + 0.1
        out.append(
            {
                "time": ts,
                "open": o,
                "high": max(o, c) + 0.2,
                "low": min(o, c) - 0.2,
                "close": c,
                "volume": 10.0 + i,
            }
        )
        px = c
    return out


def test_cache_key_includes_dataset_version():
    k = make_ohlcv_cache_key(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2025-01-01",
        end_time="2025-02-01",
        dataset_version="ohlcv_v1",
    )
    assert "ohlcv_v1" in k
    assert "BTCUSDT" in k
    assert "15m" in k


def test_manifest_incompatible_on_version_mismatch(tmp_path: Path):
    man = OhlcvCacheManifest(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2025-01-01",
        end_time="2025-02-01",
        dataset_version="ohlcv_v1",
        row_count=10,
        first_timestamp="2025-01-01T00:00:00+00:00",
        last_timestamp="2025-01-01T02:15:00+00:00",
        source="postgresql",
        sha256="abc",
        parquet_relpath="x.parquet",
        validation_status="PASS",
    )
    ok, reason = manifests_compatible(
        manifest=man.to_dict(),
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2025-01-01",
        end_time="2025-02-01",
        dataset_version="ohlcv_v2",
    )
    assert ok is False
    assert "version" in reason or "dataset" in reason.lower() or reason


def test_parquet_round_trip_equality(tmp_path: Path):
    from app.research.data_cache.parquet_store import frame_to_candles, read_ohlcv_parquet

    candles = _candles(80)
    path = tmp_path / "t.parquet"
    write_ohlcv_parquet(path, candles)
    back = frame_to_candles(read_ohlcv_parquet(path))
    assert compare_ohlcv_rows(candles, back)["status"] == "PASS"
    assert file_sha256(path)


def test_trade_digest_and_compare_trades():
    t1 = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "LONG",
        "entry_index": 10,
        "exit_index": 20,
        "signal_time": "2025-01-01T00:00:00+00:00",
        "exit_time": "2025-01-01T02:30:00+00:00",
        "entry_price": 100.0,
        "stop_price": 99.0,
        "tp1": 102.0,
        "outcome": "TP1",
        "r_multiple": 2.0,
    }
    assert trade_digest(t1)["entry_price"] == 100.0
    assert compare_trades([t1], [dict(t1)])["status"] == "PASS"
    bad = dict(t1, entry_price=100.5)
    assert compare_trades([t1], [bad])["status"] == "FIRST_MISMATCH"


def test_htf_precompute_no_lookahead_and_equality():
    h1 = _candles(120, minutes=60)
    setup = _candles(480, minutes=15)
    cfg = SignalConfig()
    cache: dict = {}
    precompute_htf_trend_cache(
        h1, timeframe="1h", symbol="BTCUSDT", config=cfg, cache=cache
    )
    for i in (20, 50, 90, 119):
        assert cache[("1h", i)] == trend_at_as_of(
            h1, i, timeframe="1h", symbol="BTCUSDT", config=cfg, cache=None
        )
    mapped = build_htf_as_of_index_map(setup, h1)
    for i, idx in enumerate(mapped):
        assert idx == as_of_index_at_or_before(h1, setup[i]["time"])
        if idx is not None:
            assert h1[idx]["time"] <= setup[i]["time"]


def test_future_candle_mutation_does_not_change_past_htf():
    """No-lookahead: mutating candles after T must not change trend at T."""
    h1 = _candles(80, minutes=60)
    cfg = SignalConfig()
    t = 40
    before = trend_at_as_of(h1, t, timeframe="1h", symbol="BTCUSDT", config=cfg)
    mutated = list(h1)
    # Mutate future bars only
    for j in range(t + 1, len(mutated)):
        c = dict(mutated[j])
        c["high"] = float(c["high"]) + 999.0
        c["close"] = float(c["close"]) + 999.0
        mutated[j] = c
    after = trend_at_as_of(mutated, t, timeframe="1h", symbol="BTCUSDT", config=cfg)
    assert before == after


@pytest.mark.asyncio
async def test_cache_hit_skips_second_materializing_read(tmp_path: Path, monkeypatch):
    """On CACHE_HIT, ensure() must not read parquet twice before load()."""
    from app.research.data_cache.cache_manager import ResearchCacheManager
    from app.research.data_cache.config import ResearchCacheConfig
    import app.research.data_cache.cache_manager as cm

    candles = _candles(40)
    cfg = ResearchCacheConfig(enabled=True, cache_root=tmp_path)
    mgr = ResearchCacheManager(cfg)
    # Seed cache via force write path using monkeypatched PG loader
    async def fake_bulk(**kwargs):
        return candles

    monkeypatch.setattr(cm, "bulk_load_ohlcv_from_postgres", fake_bulk)
    await mgr.load_ohlcv_to_cache(
        "BTCUSDT", "15m", "2025-01-01", "2025-01-02", force_refresh=True
    )

    reads = {"n": 0}
    real_read = cm.read_ohlcv_parquet

    def counting_read(path):
        reads["n"] += 1
        return real_read(path)

    monkeypatch.setattr(cm, "read_ohlcv_parquet", counting_read)
    meta = await mgr.load_ohlcv_to_cache(
        "BTCUSDT", "15m", "2025-01-01", "2025-01-02", force_refresh=False
    )
    assert meta["status"] == "CACHE_HIT"
    # Hit path should not materialize parquet inside ensure (sha256 only)
    assert reads["n"] == 0
