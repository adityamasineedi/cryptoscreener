import json
import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "research" / "cap_book_monitor.py"
_SPEC = importlib.util.spec_from_file_location("cap_book_monitor_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
read_cap_book_agents = _MODULE.read_cap_book_agents


def test_cap_book_monitor_isolates_missing_and_mid_write_shards(tmp_path):
    (tmp_path / "shard_a.json").write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "market_direction": "NEUTRAL",
                "principal_usd": 1000,
                "elapsed_seconds": 12.5,
                "rows": [
                    {
                        "combination_id": "COMBO_01",
                        "symbol": "BTCUSDT",
                        "timeframe": "1h",
                        "direction": "LONG",
                        "sample_size": 7,
                        "pnl_usd_net": 15.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "shard_b.json").write_text('{"status":"RUNNING","rows":[', encoding="utf-8")
    (tmp_path / "shard_d.json").write_text(
        json.dumps({"status": "OK", "elapsed_seconds": 44, "rows": []}),
        encoding="utf-8",
    )

    result = read_cap_book_agents(tmp_path)

    assert [shard["shard"] for shard in result["shards"]] == ["a", "b", "c", "d"]
    assert result["shards"][0]["status"] == "RUNNING"
    assert result["shards"][0]["cells_finished"] == 1
    assert result["shards"][0]["rows"][0]["symbol"] == "BTCUSDT"
    assert result["shards"][1]["status"] == "waiting"
    assert result["shards"][1]["rows"] == []
    assert result["shards"][2]["status"] == "waiting"
    assert result["shards"][3]["status"] == "OK"


def test_cap_book_monitor_reads_disk_again_on_each_call(tmp_path):
    target = tmp_path / "shard_c.json"
    first = read_cap_book_agents(tmp_path)
    assert first["shards"][2]["cells_finished"] == 0

    target.write_text(
        json.dumps({"status": "RUNNING", "elapsed_seconds": 3, "rows": [{"symbol": "NEARUSDT"}]}),
        encoding="utf-8",
    )
    second = read_cap_book_agents(tmp_path)
    assert second["shards"][2]["cells_finished"] == 1
    assert second["shards"][2]["rows"][0]["symbol"] == "NEARUSDT"
