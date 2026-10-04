"""Research data_cache unit tests (no live trading paths)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.research.data_cache.cache_key import make_feature_cache_key, make_ohlcv_cache_key
from app.research.data_cache.checkpoint import CheckpointStore, RunCheckpoint
from app.research.data_cache.config import ResearchCacheConfig
from app.research.data_cache.dataset import ResearchDataset
from app.research.data_cache.feature_cache import FeatureCache
from app.research.data_cache.manifest import OhlcvCacheManifest, read_manifest, write_manifest
from app.research.data_cache.metrics import ResearchCacheMetrics
from app.research.data_cache.parquet_store import (
    candles_to_frame,
    file_sha256,
    frame_to_candles,
    read_ohlcv_parquet,
    write_ohlcv_parquet,
)
from app.research.data_cache.validation import (
    CACHE_INVALID,
    CACHE_PASS,
    manifests_compatible,
    validate_ohlcv_candles,
)


def _candles(n: int = 120, *, start: datetime | None = None, step_s: int = 900) -> list[dict]:
    t0 = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    px = 100.0
    for i in range(n):
        ts = t0 + timedelta(seconds=step_s * i)
        o = px
        c = px + (0.1 if i % 2 == 0 else -0.05)
        h = max(o, c) + 0.2
        lo = min(o, c) - 0.2
        out.append(
            {
                "time": ts,
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 10.0 + i,
            }
        )
        px = c
    return out


def test_cache_key_uniqueness():
    a = make_ohlcv_cache_key(
        symbol="btcusdt",
        timeframe="15m",
        start_time="2024-01-01",
        end_time="2025-01-01",
        dataset_version="ohlcv_v1",
    )
    b = make_ohlcv_cache_key(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2024-01-01",
        end_time="2025-01-02",
        dataset_version="ohlcv_v1",
    )
    assert a != b
    assert "BTCUSDT" in a and "15m" in a and "ohlcv_v1" in a


def test_feature_cache_key_includes_feature_version():
    k = make_feature_cache_key(
        symbol="ETHUSDT",
        timeframe="1h",
        start_time="2024-01-01",
        end_time="2024-06-01",
        dataset_version="ohlcv_v1",
        feature_version="research_features_cache_v1",
    )
    assert "research_features_cache_v1" in k


def test_parquet_roundtrip_and_sha(tmp_path: Path):
    candles = _candles(80)
    path = tmp_path / "x.parquet"
    digest = write_ohlcv_parquet(path, candles)
    assert digest == file_sha256(path)
    df = read_ohlcv_parquet(path)
    back = frame_to_candles(df)
    assert len(back) == 80
    assert back[0]["open"] == candles[0]["open"]
    assert back[-1]["close"] == candles[-1]["close"]


def test_validation_pass_and_duplicate_fail():
    good = _candles(100)
    ok = validate_ohlcv_candles(good, symbol="BTCUSDT", timeframe="15m")
    assert ok["validation_status"] == CACHE_PASS
    bad = list(good)
    bad.append(dict(good[-1]))  # duplicate timestamp
    bad_rep = validate_ohlcv_candles(bad, symbol="BTCUSDT", timeframe="15m")
    assert bad_rep["validation_status"] == CACHE_INVALID
    assert any("duplicate" in p for p in bad_rep["problems"])


def test_manifest_compatible_exact_range(tmp_path: Path):
    man = OhlcvCacheManifest(
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2024-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
        row_count=10,
        first_timestamp="2024-01-01T00:00:00+00:00",
        last_timestamp="2024-01-01T02:00:00+00:00",
        sha256="abc",
        validation_status=CACHE_PASS,
    )
    path = tmp_path / "m.json"
    write_manifest(path, man)
    loaded = read_manifest(path)
    assert loaded is not None
    ok, reason = manifests_compatible(
        manifest=loaded.to_dict(),
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2024-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
    )
    assert ok and reason == "ok"
    bad, why = manifests_compatible(
        manifest=loaded.to_dict(),
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2023-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
    )
    assert not bad and why == "range_mismatch"


def test_dataset_no_lookahead_slice():
    candles = _candles(50)
    ds = ResearchDataset.from_candles(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=candles,
        start_time="2024-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
    )
    as_of = candles[20]["time"]
    clipped = ds.slice_as_of(as_of)
    assert clipped.row_count == 21
    # Mutating "future" source list must not affect clipped frame already taken
    future = ds.slice_as_of(candles[10]["time"])
    assert future.row_count == 11


def test_feature_cache_reuse(tmp_path: Path):
    cfg = ResearchCacheConfig(enabled=True, cache_root=tmp_path)
    ds = ResearchDataset.from_candles(
        symbol="BTCUSDT",
        timeframe="15m",
        candles=_candles(80),
        start_time="2024-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
        cache_key="testkey",
    )
    metrics = ResearchCacheMetrics()
    fc = FeatureCache(cfg, metrics)
    a = fc.ensure_features(ds)
    b = fc.ensure_features(ds)
    assert a["status"] == "CACHE_WRITTEN"
    assert b["status"] == "CACHE_HIT"
    assert metrics.feature_cache_hits == 1
    assert metrics.feature_cache_misses == 1
    frame = fc.load_feature_frame(ds)
    assert "atr_14" in frame.columns


def test_checkpoint_resume(tmp_path: Path):
    cfg = ResearchCacheConfig(enabled=True, cache_root=tmp_path)
    store = CheckpointStore(cfg)
    cp = store.create(
        symbols=["BTCUSDT", "ETHUSDT"],
        timeframes=["15m"],
        start_time="2024-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
        strategy_version="test",
    )
    store.mark(cp, "BTCUSDT", "15m", "COMPLETED", result_summary={"ok": True})
    loaded = store.load(cp.run_id)
    assert loaded is not None
    pending = loaded.pending_cells()
    assert len(pending) == 1
    assert pending[0].symbol == "ETHUSDT"


def test_worker_limit_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RESEARCH_CPU_WORKERS", "8")
    monkeypatch.setenv("RESEARCH_DB_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("RESEARCH_MEMORY_LIMIT_MB", "512")
    monkeypatch.setenv("RESEARCH_CACHE_PATH", str(tmp_path))
    cfg = ResearchCacheConfig()
    assert cfg.cpu_workers == 8
    assert cfg.db_max_concurrency == 2
    assert cfg.memory_limit_mb == 512
    assert cfg.cache_root == tmp_path.resolve()


def test_corrupted_cache_manifest_rejected(tmp_path: Path):
    man = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "start_time": "2024-01-01",
        "end_time": "2024-02-01",
        "dataset_version": "ohlcv_v1",
        "validation_status": "PASS",
        "fabricated": True,
    }
    ok, why = manifests_compatible(
        manifest=man,
        symbol="BTCUSDT",
        timeframe="15m",
        start_time="2024-01-01",
        end_time="2024-02-01",
        dataset_version="ohlcv_v1",
    )
    assert not ok and why == "synthetic_data_flag"


def test_candles_to_frame_sorted():
    candles = list(reversed(_candles(10)))
    df = candles_to_frame(candles)
    ts = df["timestamp"].to_list()
    assert ts == sorted(ts)
