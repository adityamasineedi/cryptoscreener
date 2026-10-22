"""Ranked strategy board — read-only view of strategy_rank/ranked.json."""

import json

from app.research.strategy_rank_board import (
    RANK_FILE,
    build_strategy_rank_board,
    fill_label,
)


def _raw():
    return json.loads(RANK_FILE.read_text(encoding="utf-8"))


def test_board_keeps_rollup_order_and_sums():
    raw = _raw()
    board = build_strategy_rank_board()
    assert board["status"] == "OK"
    assert [g["id"] for g in board["groups"]] == ["day", "scalp", "swing", "grid"]
    for group in board["groups"]:
        # Duplicates and losing / no-trade / grid-research rows are left off the board.
        rollup = [
            r
            for r in raw["strategy_rollup"][group["id"]]
            if r["net_pnl_usd"] > 0
            and r["trades"] > 0
            and r["strategy_id"] != "COMBO_02_LOCAL"
            and not r["strategy_id"].startswith("GRID-")
        ]
        assert len(group["rows"]) == len(rollup)
        hidden = group["hidden"]
        assert hidden["duplicates"] + hidden["failures"] == len(raw["strategy_rollup"][group["id"]]) - len(rollup)
        for rank, (row, roll) in enumerate(zip(group["rows"], rollup), 1):
            assert row["rank"] == rank
            assert row["strategy_id"] == roll["strategy_id"]
            assert row["direction"] == roll["direction"]
            assert row["timeframe"] == roll["timeframe"]
            assert row["coins_passing"] == roll["coins_passing"]
            assert row["passing_symbols"] == roll["passing_symbols"]
            assert row["net_pnl_usd"] == roll["net_pnl_usd"]
            assert row["trades"] == roll["trades"]
            # Pass flags come from the file, one cell per coin.
            assert len(row["cells"]) == roll["coins"]
            assert sum(1 for c in row["cells"] if c["passes"]) == roll["coins_passing"]


def test_board_window_and_descriptions():
    board = build_strategy_rank_board()
    window = board["window"]
    assert (window["start"], window["end"]) == ("2026-02-14", "2026-10-12")
    assert window["coins"] == ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK"]
    assert window["account"]["risk_usd"] == 20.0
    assert window["account"]["leverage"] == 2.0
    assert window["in_sample"] is True

    day = next(g for g in board["groups"] if g["id"] == "day")
    rows = {(r["strategy_id"], r["direction"]): r for r in day["rows"]}

    combo02 = rows[("COMBO_02", "LONG")]
    assert combo02["catalog_id"] == "COMBO_02_V1"
    assert combo02["description_source"] == "catalog"
    assert combo02["fill_label"] == "Signal close, no slippage"
    assert [a["id"] for a in combo02["aliases"]] == ["COMBO_02_CLOSED_HTF"]
    # Losing sides and the duplicate id are off the board; the twin keeps the note.
    assert ("COMBO_02", "SHORT") not in rows
    assert ("COMBO_02_LOCAL", "LONG") not in rows
    assert "COMBO_02_LOCAL" in [a["id"] for a in rows[("COMBO_01", "LONG")]["aliases"]]

    # Backtest-menu ids now have RESEARCH catalog cards (strategies screen audit
    # 2026-10-17), so their rows take the catalog text.
    assert rows[("COMBO_01", "LONG")]["catalog_id"] == "COMBO_01"
    assert rows[("COMBO_01", "LONG")]["description_source"] == "catalog"
    candle = rows[("CANDLE12_V2", "LONG")]
    assert candle["in_catalog"] is True
    assert candle["catalog_id"] == "CANDLE12_V2"
    assert candle["description_source"] == "catalog"
    assert candle["fill_label"] == "0.02% slippage included"

    scalp = next(g for g in board["groups"] if g["id"] == "scalp")
    intraday = next(r for r in scalp["rows"] if r["strategy_id"] == "INTRADAY_BOS_SD_V1.C")
    assert intraday["catalog_id"] == "INTRADAY_BOS_SD_V1"
    assert intraday["variant"] == "C"
    assert intraday["sizing_note"] == "2.50 USD risk, 1x"


def test_grid_research_engine_is_never_a_pass():
    board = build_strategy_rank_board()
    grid = next(g for g in board["groups"] if g["id"] == "grid")
    assert not [r for r in grid["rows"] if r["strategy_id"].startswith("GRID-")]
    assert grid["hidden"]["failures"] >= 4
    assert all(r["net_pnl_usd"] > 0 for g in board["groups"] for r in g["rows"])


def test_missing_empty_and_invalid_file(tmp_path):
    missing = build_strategy_rank_board(path=tmp_path / "ranked.json")
    assert missing["status"] == "MISSING"
    assert missing["groups"] == []
    assert "not found" in missing["error"]

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert build_strategy_rank_board(path=empty)["status"] == "EMPTY"

    no_rows = tmp_path / "no_rows.json"
    no_rows.write_text(json.dumps({"strategy_rollup": {}, "cells_ranked": {}}), encoding="utf-8")
    assert build_strategy_rank_board(path=no_rows)["status"] == "EMPTY"

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert build_strategy_rank_board(path=broken)["status"] == "INVALID"


def test_fill_label_reads_the_execution_model():
    assert fill_label("LEGACY_SIGNAL_CLOSE") == "Signal close, no slippage"
    assert fill_label("CAUSAL_NEXT_OPEN") == "Next bar open, 0.05% slippage included"
    assert fill_label("LEGACY_SIGNAL_CLOSE", "COMBO_SD_A") == "Signal close, 0.02% slippage included"
    assert fill_label("LEGACY_SIGNAL_CLOSE", "COMBO_02") == "Signal close, no slippage"
    assert fill_label("entry next bar open with 0.02% adverse slippage") == "0.02% slippage included"
    assert fill_label("taker both sides; no slippage") == "No slippage"
