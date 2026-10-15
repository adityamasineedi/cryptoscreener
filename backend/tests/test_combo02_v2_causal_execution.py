"""COMBO_02_V2 causal execution and the SOLUSDT 73-trade audit ledger."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.research.causal_execution import (
    CAUSAL_NEXT_OPEN,
    EXECUTION_MODEL_VERSION,
    OUTCOME_GAP_THROUGH_STOP,
    build_execution_run_record,
    clear_isolated_execution_runs,
    compare_execution_models,
    decision_view,
    execute_causal_trade,
    get_isolated_execution_run,
    persist_execution_run_isolated,
    write_execution_run_artifact,
)
from app.research.config import ResearchConfig
from app.research.combo02_v2.htf_policy import fetch_htf_trends

AUDIT_CSV = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "9f1709ac13d4"
    / "deep_historical_trade_audit"
    / "audit_trades.csv"
)

# Published read-only replay of job 9f1709ac13d4. Tolerances cover float rounding.
LEGACY_NET = 88.5447
CAUSAL_NET = -16.0321
CAUSAL_NET_R = -5.0627
LEGACY_GROSS_R = 4.9324
LEGACY_NET_R = -0.0761


def _bar(open_: float, high: float, low: float, close: float, hour: int) -> dict:
    return {
        "time": datetime(2026, 2, 14, hour, tzinfo=timezone.utc).isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _load_audit_rows() -> list[dict[str, str]]:
    with AUDIT_CSV.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_default_config_keeps_legacy_and_forming_htf():
    cfg = ResearchConfig()
    assert cfg.execution_model == "LEGACY_SIGNAL_CLOSE"
    assert cfg.htf_fully_closed is False
    fresh = ResearchConfig.for_new_combo02_v2_run()
    assert fresh.htf_fully_closed is True
    assert fresh.execution_model == "DUAL_LEGACY_AND_CAUSAL"
    assert cfg.configuration_hash() == ResearchConfig().configuration_hash()
    assert cfg.configuration_hash() == fresh.configuration_hash()
    assert cfg.execution_configuration_hash() != fresh.execution_configuration_hash()


def test_fetch_htf_trends_forwards_closed_flag(monkeypatch):
    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {"trend_4h": "NEUTRAL", "trend_1h": "BULLISH"}

    monkeypatch.setattr(
        "app.research.combo02_v2.htf_policy.htf_trends_for_setup_bar",
        _fake,
    )
    fetch_htf_trends(
        symbol="SOLUSDT",
        timeframe="1h",
        candles=[{"open": 1, "high": 1, "low": 1, "close": 1}],
        as_of_index=0,
        signal_config=object(),
        candles_1h=[{"open": 1}],
        candles_4h=[{"open": 1}],
        htf_require_fully_closed=True,
    )
    assert captured["htf_require_fully_closed"] is True


def test_causal_fill_is_next_open_and_ignores_the_signal_bar():
    candles = [
        _bar(10, 11, 9, 10.5, 0),
        _bar(20, 21, 19, 20.5, 1),
        _bar(30, 40, 29, 35, 2),
    ]
    trade = {
        "trade_id": 1,
        "direction": "LONG",
        "entry_index": 1,
        "entry_price": 20.5,
        "stop_price": 19.0,
        "tp1": 24.0,
    }
    assert len(decision_view(candles, 1)) == 2
    assert decision_view(candles, 1)[-1]["close"] == 20.5
    first = execute_causal_trade(trade, candles, entry_slippage_rate=0.0005, exit_slippage_rate=0)
    assert first["fill_index"] == 2
    assert first["entry_price"] == pytest.approx(30 * 1.0005)
    assert first["outcome"] == "TP1"
    candles[2] = _bar(31, 40, 29, 35, 2)
    second = execute_causal_trade(trade, candles, entry_slippage_rate=0.0005, exit_slippage_rate=0)
    assert second["entry_price"] == pytest.approx(31 * 1.0005)
    assert first["entry_price"] != second["entry_price"]


def test_gap_through_stop_is_explicit_and_slips_once():
    candles = [
        _bar(100, 101, 99, 100, 0),
        _bar(90, 91, 89, 90, 1),
    ]
    trade = {
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 100,
        "stop_price": 95,
        "tp1": 110,
    }
    row = execute_causal_trade(trade, candles, entry_slippage_rate=0.0005, exit_slippage_rate=0.0005)
    assert row["outcome"] == OUTCOME_GAP_THROUGH_STOP
    assert row["entry_price"] == pytest.approx(90 * 1.0005)
    assert row["exit_price"] == row["entry_price"]
    assert row["slippage_applied_once"] is True


def test_same_bar_stop_and_target_resolves_to_stop():
    candles = [
        _bar(100, 101, 99, 100, 0),
        _bar(100, 120, 90, 110, 1),
    ]
    trade = {
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 100,
        "stop_price": 95,
        "tp1": 110,
    }
    row = execute_causal_trade(trade, candles, entry_slippage_rate=0, exit_slippage_rate=0)
    assert row["outcome"] == "SL"
    assert row["exit_price"] == pytest.approx(95)


def test_replay_is_deterministic_and_does_not_mutate_the_source():
    candles = [
        _bar(100, 101, 99, 100, 0),
        _bar(101, 102, 98, 99, 1),
        _bar(99, 100, 90, 92, 2),
    ]
    trade = {
        "trade_id": "a",
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 100.0,
        "stop_price": 95.0,
        "tp1": 110.0,
        "r_multiple": -1.0,
    }
    cfg = ResearchConfig.for_new_combo02_v2_run()
    first = compare_execution_models([trade], candles, cfg)
    second = compare_execution_models([trade], candles, cfg)
    assert first["causal"]["trades"][0]["entry_price"] == second["causal"]["trades"][0]["entry_price"]
    assert first["causal"]["trades"][0]["exit_price"] == second["causal"]["trades"][0]["exit_price"]
    assert trade["entry_price"] == 100.0
    assert first["legacy"]["trades"][0]["entry_price"] == 100.0
    assert first["causal"]["trades"][0]["execution_model"] == CAUSAL_NEXT_OPEN


def test_isolated_store_refuses_overwrite_and_skips_the_database():
    clear_isolated_execution_runs()
    candles = [_bar(1, 1, 1, 1, 0), _bar(1, 1, 1, 1, 1)]
    trade = {
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 1.0,
        "stop_price": 0.9,
        "tp1": 1.2,
        "r_multiple": 1.0,
        "exit_price": 1.2,
        "outcome": "TP1",
    }
    record = build_execution_run_record(
        run_id="unit-run",
        symbol="SOLUSDT",
        timeframe="1h",
        candles=candles,
        trades=[trade],
    )
    assert record["execution_model_version"] == EXECUTION_MODEL_VERSION
    assert record["dataset_fingerprint"]
    assert record["signal_engine_version"]
    assert record["research_engine_version"]
    persist_execution_run_isolated(record)
    with pytest.raises(FileExistsError):
        persist_execution_run_isolated(record)
    stored = get_isolated_execution_run("unit-run")
    assert stored is not None
    assert stored["legacy_trades"]
    assert stored["causal_trades"]
    clear_isolated_execution_runs()


def test_execution_artifact_is_not_overwritten(tmp_path):
    from app.research.causal_execution import write_execution_run_artifact

    row = {
        "execution_run": {"run_id": "job:SOLUSDT:1h", "execution_model_version": EXECUTION_MODEL_VERSION},
        "causal_trades": [{"trade_id": 1}],
        "trades": [{"trade_id": 1, "entry_price": 1}],
    }
    first = write_execution_run_artifact("job", row, reports_root=tmp_path)
    assert first is not None
    row["causal_trades"] = [{"trade_id": 99}]
    second = write_execution_run_artifact("job", row, reports_root=tmp_path)
    assert second is None
    saved = Path(first).read_text(encoding="utf-8")
    assert "99" not in saved


def test_sol_audit_ledger_is_73_trades_and_24_winners():
    rows = _load_audit_rows()
    assert len(rows) == 73
    wins = sum(1 for row in rows if float(row["r_gross"]) > 0)
    assert wins == 24
    net = sum(float(row["net_pnl"]) for row in rows)
    gross_r = sum(float(row["r_gross"]) for row in rows)
    net_r = sum(float(row["r_net"]) for row in rows)
    assert net == pytest.approx(LEGACY_NET, abs=0.02)
    assert gross_r == pytest.approx(LEGACY_GROSS_R, abs=0.01)
    assert net_r == pytest.approx(LEGACY_NET_R, abs=0.01)
    label_diffs = [
        row["trade_id"]
        for row in rows
        if row["forming_4h_trend"] != row["closed_4h_trend"]
    ]
    assert label_diffs == ["61"]
    assert all(row["lookahead_dependent"] == "False" for row in rows)


@pytest.mark.asyncio
async def test_sol_audit_causal_replay_matches_published_book():
    from app.config import get_settings
    from app.research.postgres_ohlcv import load_ohlcv_series_range
    from app.services.database import db_manager

    settings = get_settings()
    if not getattr(settings, "database_enabled", True):
        pytest.skip("database disabled")
    await db_manager.connect(settings, ensure_schema=False)
    candles = await load_ohlcv_series_range(
        "SOLUSDT",
        "1h",
        start=datetime(2026, 2, 1, tzinfo=timezone.utc),
        end_exclusive=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )
    if len(candles) < 1000:
        pytest.skip("SOLUSDT 1h history is not loaded")

    index_by_time = {}
    for i, candle in enumerate(candles):
        ts = candle["time"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            key = ts.isoformat()
        else:
            key = str(ts)
        index_by_time[key] = i

    trades = []
    for row in _load_audit_rows():
        signal_time = row["entry_utc_recorded"]
        if signal_time not in index_by_time:
            signal_time = signal_time.replace("+00:00", "+00:00")
        assert signal_time in index_by_time
        trades.append(
            {
                "trade_id": int(row["trade_id"]),
                "direction": row["side"],
                "entry_index": index_by_time[signal_time],
                "entry_price": float(row["entry_price"]),
                "stop_price": float(row["stop"]),
                "tp1": float(row["tp1"]),
                "exit_price": float(row["exit_price"]),
                "r_multiple": float(row["r_gross"]),
                "outcome": "TP1" if float(row["r_gross"]) > 0 else "SL",
                "entry_type": "MARKET",
            }
        )

    cfg = ResearchConfig.for_new_combo02_v2_run()
    book = compare_execution_models(trades, candles, cfg, account_equity=1000.0)
    legacy = book["reconciliation"]["legacy"]
    causal = book["reconciliation"]["causal"]
    assert legacy["trades"] == 73
    assert causal["trades"] == 73
    assert legacy["winners"] == 24
    assert causal["winners"] == 24
    assert legacy["net_pnl"] == pytest.approx(LEGACY_NET, abs=0.05)
    assert causal["net_pnl"] == pytest.approx(CAUSAL_NET, abs=0.05)
    assert causal["net_r"] == pytest.approx(CAUSAL_NET_R, abs=0.05)
    assert book["reconciliation"]["winner_count_unchanged"] is True
    for source, causal_row in zip(trades, book["causal"]["trades"]):
        assert causal_row["fill_index"] == source["entry_index"] + 1
        assert causal_row["entry_price"] != source["entry_price"] or source["entry_price"] == causal_row["next_open"]
    flips = [
        source["trade_id"]
        for source, causal_row in zip(trades, book["causal"]["trades"])
        if (source["r_multiple"] > 0) != (float(causal_row.get("r_gross") or 0) > 0)
    ]
    assert flips == []


def test_slippage_fees_and_leverage_cap():
    candles = [
        _bar(100, 101, 99, 100, 0),
        _bar(100, 101, 94, 96, 1),
    ]
    trade = {
        "trade_id": 1,
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 100.0,
        "stop_price": 99.9,
        "tp1": 110.0,
        "r_multiple": -1.0,
        "exit_price": 99.9,
        "outcome": "SL",
        "entry_type": "MARKET",
    }
    cfg = ResearchConfig.for_new_combo02_v2_run()
    cfg.entry_slippage_rate = 0.0005
    cfg.exit_slippage_rate = 0.0005
    book = compare_execution_models([trade], candles, cfg, account_equity=1000.0)
    causal = book["causal"]["trades"][0]
    assert causal["entry_price"] == pytest.approx(100 * 1.0005)
    assert causal["outcome"] == "SL"
    assert causal["exit_price"] == pytest.approx(99.9 * 0.9995)
    assert causal["capped_by_leverage"] is True
    assert causal["notional_entry_usd"] <= 1000.0 * 2.0 + 1e-6
    assert float(causal["fees"]) < 0
    assert float(causal["net_pnl"]) == pytest.approx(
        float(causal["gross_pnl"]) + float(causal["fees"])
    )


def test_zero_trade_book_stays_empty():
    from app.research.causal_execution import apply_causal_primary_display

    cfg = ResearchConfig.for_new_combo02_v2_run()
    book = compare_execution_models([], [_bar(1, 1, 1, 1, 0)], cfg)
    assert book["causal"]["summary"]["trades"] == 0
    assert book["causal"]["summary"]["net_pnl"] == 0
    assert book["legacy"]["summary"]["winners"] == 0
    row: dict = {"pnl_usd_net": 99, "sample_size": 4, "trades": [{"entry_price": 1}]}
    record = {
        "causal_summary": book["causal"]["summary"],
        "legacy_summary": book["legacy"]["summary"],
        "causal_trades": [],
        "legacy_trades": [],
    }
    apply_causal_primary_display(row, record)
    assert row["sample_size"] == 0
    assert row["pnl_usd_net"] == 0
    assert row["trades"] == []
    assert row["pnl_model"] == "CAUSAL_NEXT_OPEN"
    assert row["optimistic_comparison"]["model"] == "LEGACY_SIGNAL_CLOSE"


def test_dataset_fingerprint_tracks_ohlcv_and_hashes_stay_stable():
    from app.research.causal_execution import dataset_fingerprint

    candles = [_bar(10, 11, 9, 10.5, 0), _bar(10.5, 12, 10, 11, 1)]
    first = dataset_fingerprint(candles, symbol="SOLUSDT", timeframe="1h")
    second = dataset_fingerprint(list(candles), symbol="SOLUSDT", timeframe="1h")
    assert first == second
    changed = [dict(candles[0]), dict(candles[1])]
    changed[1]["high"] = 12.5
    assert dataset_fingerprint(changed, symbol="SOLUSDT", timeframe="1h") != first
    default = ResearchConfig()
    new_run = ResearchConfig.for_new_combo02_v2_run()
    assert default.configuration_hash() == "095aec279e492c61"
    assert new_run.configuration_hash() == "095aec279e492c61"
    assert default.execution_configuration_hash() == "0551908fd83d87d8"
    assert new_run.execution_configuration_hash() == "15f8476acb04cb8d"


def test_closed_4h_and_causal_15m_context_exclude_forming_bars():
    from app.research.bos_strategy_comparison.htf import (
        build_htf_as_of_index_map_fully_closed,
        htf_trends_for_setup_bar,
    )
    from app.research.combo02_v2.context import Combo02V2Context

    setup = []
    for hour in range(4):
        setup.append(
            {
                "time": datetime(2026, 2, 14, hour, tzinfo=timezone.utc),
                "open": 100 + hour,
                "high": 101 + hour,
                "low": 99 + hour,
                "close": 100.5 + hour,
            }
        )
    bars_4h = [
        {
            "time": datetime(2026, 2, 13, 20, tzinfo=timezone.utc),
            "open": 90, "high": 95, "low": 89, "close": 94,
        },
        {
            "time": datetime(2026, 2, 14, 0, tzinfo=timezone.utc),
            "open": 100, "high": 110, "low": 99, "close": 108,
        },
    ]
    bars_1h = setup
    closed = htf_trends_for_setup_bar(
        symbol="SOLUSDT",
        setup_candles=setup,
        as_of_index=2,
        candles_4h=bars_4h,
        candles_1h=bars_1h,
        setup_timeframe="1h",
        htf_require_fully_closed=True,
    )
    forming = htf_trends_for_setup_bar(
        symbol="SOLUSDT",
        setup_candles=setup,
        as_of_index=2,
        candles_4h=bars_4h,
        candles_1h=bars_1h,
        setup_timeframe="1h",
        htf_require_fully_closed=False,
    )
    assert closed["idx_4h"] == 0
    assert forming["idx_4h"] == 1
    assert closed["idx_1h"] == 2
    assert closed["decision_ts"] is not None

    bars_15m = []
    for step in range(0, 60 * 4, 15):
        hour, minute = divmod(step, 60)
        bars_15m.append(
            {
                "time": datetime(2026, 2, 14, hour, minute, tzinfo=timezone.utc),
                "open": 1, "high": 1, "low": 1, "close": 1,
            }
        )
    ctx = Combo02V2Context.build(
        symbol="SOLUSDT",
        setup_timeframe="1h",
        setup_candles=setup,
        candles_15m=bars_15m,
    )
    mapped = build_htf_as_of_index_map_fully_closed(
        setup, bars_15m, setup_timeframe="1h", htf_timeframe="15m"
    )
    assert mapped[2] == ctx.idx_15m_map[2]
    chosen = bars_15m[int(ctx.idx_15m_map[2])]
    assert chosen["time"] == datetime(2026, 2, 14, 2, 45, tzinfo=timezone.utc)


def test_historical_run_files_are_immutable():
    import hashlib

    from app.research.bos_combinations import get_combination
    from app.research.causal_execution import (
        PROTECTED_RESEARCH_RUNS,
        write_execution_run_artifact,
    )

    combo = get_combination("COMBO_02_V2")
    assert combo is not None
    assert combo.htf_require_fully_closed is False
    assert combo.require_htf_alignment is False
    assert "9f1709ac13d4" in PROTECTED_RESEARCH_RUNS
    root = AUDIT_CSV.parents[1]
    expected = {
        "deep_historical_trade_audit/audit_trades.csv": "500e6ec3af2c0ac80f917ffe62ea4da7d3918d9224102f08645787335228458a",
        "deep_historical_trade_audit/summary.json": "742dab5ef0b5ab6dce6ffe53443b1e626956192a620a8efbaea7e342c06b0b03",
        "deep_historical_trade_audit/trade_evidence.json": "d66ac0b78a7d1f6bc6b098e7f88d4e8534441b411cb1c9f0807d4d5fdf82e61c",
    }
    for relative, digest in expected.items():
        payload = (root / relative).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == digest
    assert not list(root.glob("execution_run_*.json"))
    with pytest.raises(PermissionError):
        write_execution_run_artifact(
            "9f1709ac13d4",
            {"execution_run": {"run_id": "9f1709ac13d4"}},
            reports_root=root.parent,
        )


def test_execution_artifact_reloads_after_memory_is_cleared(tmp_path):
    from app.research.causal_execution import (
        dataset_fingerprint,
        load_execution_run_artifact,
    )

    clear_isolated_execution_runs()
    candles = [_bar(100, 101, 99, 100, 0), _bar(101, 110, 100, 108, 1)]
    trade = {
        "trade_id": 7,
        "direction": "LONG",
        "entry_index": 0,
        "entry_price": 100.0,
        "stop_price": 95.0,
        "tp1": 104.0,
        "exit_price": 104.0,
        "outcome": "TP1",
        "r_multiple": 0.8,
        "entry_type": "MARKET",
    }
    record = build_execution_run_record(
        run_id="reload-job:SOLUSDT:1h",
        symbol="SOLUSDT",
        timeframe="1h",
        candles=candles,
        trades=[trade],
    )
    row = {
        "execution_run": {
            key: record[key]
            for key in (
                "run_id",
                "dataset_fingerprint",
                "configuration_hash",
                "execution_configuration_hash",
                "signal_engine_version",
                "research_engine_version",
                "execution_model_version",
            )
        },
        "causal_trades": record["causal_trades"],
        "legacy_trades": record["legacy_trades"],
    }
    written = persist_execution_run_isolated(record)
    path = write_execution_run_artifact("reload-job", row, reports_root=tmp_path)
    assert written == record["run_id"]
    assert path is not None
    clear_isolated_execution_runs()
    assert get_isolated_execution_run(record["run_id"]) is None
    loaded = load_execution_run_artifact(Path(path))
    assert loaded["dataset_fingerprint"] == dataset_fingerprint(
        candles, symbol="SOLUSDT", timeframe="1h"
    )
    assert loaded["configuration_hash"] == record["configuration_hash"]
    assert loaded["execution_configuration_hash"] == record["execution_configuration_hash"]
    assert loaded["execution_model_version"] == EXECUTION_MODEL_VERSION
    assert loaded["causal_trades"][0]["trade_id"] == 7
    assert loaded["causal_trades"][0]["entry_price"] == record["causal_trades"][0]["entry_price"]
    assert loaded["legacy_trades"][0]["entry_price"] == 100.0
    clear_isolated_execution_runs()
