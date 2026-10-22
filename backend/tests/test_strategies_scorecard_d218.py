"""Owner D191 (DECISIONS D218): Strategies page, display only.

- The rank board's fill labels and caveats state what the old ranking did (synthetic rank file;
  ranked.json itself is not read or rewritten here).
- The 37 CANDLE12 V3 OOS-2 frozen files match their lock.
- Paper, backtest and ranking code are byte-identical to the branch base 1251e57, and no D218
  commit touches them.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.research.strategy_rank_board import BOARD_CAVEATS, build_strategy_rank_board, fill_label

REPO = Path(__file__).resolve().parents[2]
BASE = "1251e57"

# The only paths a D218 commit may touch.
D218_ALLOWED = {
    "backend/app/research/strategy_rank_board.py",
    "backend/tests/test_strategy_rank_board.py",
    "backend/tests/test_strategies_scorecard_d218.py",
    "frontend/src/components/StrategyRankBoard.tsx",
    "frontend/src/components/StrategyRankBoard.test.tsx",
    "frontend/src/components/ResearchScorecard.tsx",
    "frontend/src/components/StrategiesPanel.test.tsx",
    "frontend/src/research/scorecardSnapshot.ts",
    "frontend/src/research/scorecardSnapshot.test.ts",
}

PAPER_ENGINE_FILES = (
    "backend/app/services/paper_trade.py",
    "backend/app/services/paper_sizing.py",
    "backend/app/services/paper_risk.py",
    "backend/app/services/paper_classification.py",
    "backend/app/services/paper_ledger_view.py",
    "backend/app/services/passed_paper_watcher.py",
    "backend/app/services/v1_paper_watcher.py",
    "backend/app/services/v21a_paper_watcher.py",
    "backend/app/services/v21a_paper_validation.py",
    "backend/app/services/v2_candidate_paper_watcher.py",
    "backend/app/services/path_a_paper_validation.py",
    "backend/app/services/candle12_v2_paper.py",
    "backend/app/services/book16_paper.py",
    "backend/app/research/combo02_v21a_paper_constants.py",
)
BACKTEST_FILES = (
    "backend/app/research/backtest_cpu_pool.py",
    "backend/app/research/backtest_cpu_worker.py",
    "backend/app/research/backtest_job.py",
    "backend/app/research/backtest_timing.py",
    "backend/app/research/backtest_ui_config.py",
    "backend/app/research/causal_execution.py",
    "backend/app/research/combination_backtest.py",
    "backend/app/research/combination_engine.py",
    "backend/app/research/engine_backtest.py",
    "backend/app/research/passed_regime_backtest.py",
    "backend/app/research/regime_backtest_job.py",
    "backend/app/research/trade_fees.py",
    "backend/app/research/scalping/backtest.py",
    "backend/app/services/seal.py",
)
# Whole trees: the V3 paper runner, live/backtest parity, the ranking scripts, the Backtest tab.
GUARDED_PREFIXES = (
    "backend/app/research/candle12_v3_paper/",
    "backend/app/research/live_backtest_parity/",
    "backend/scripts/strategy_rank/",
    "frontend/src/backtest/",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60)


def _need_base() -> None:
    if shutil.which("git") is None or _git("cat-file", "-e", f"{BASE}^{{commit}}").returncode != 0:
        pytest.skip(f"git or the D218 base commit {BASE} is not available")


def _guarded(path: str) -> bool:
    return path in PAPER_ENGINE_FILES or path in BACKTEST_FILES or path.startswith(GUARDED_PREFIXES)


def _lock_files() -> dict[str, str]:
    lock = json.loads((REPO / "docs" / "candle12_v3_oos_contract.lock.json").read_text(encoding="utf-8"))
    return lock["code_fingerprint"]["files"]


# ---- display text --------------------------------------------------------------------------


def _cell(sid: str, model: str, style: str = "day") -> dict:
    return {
        "family": "combo", "strategy_id": sid, "direction": "LONG", "timeframe": "1h", "style": style,
        "symbol": "BTCUSDT", "net_pnl_usd": 1.0, "trades_closed": 1, "passes": False, "fail_reasons": [],
        "execution_model": model, "risk_usd": 20.0, "leverage": 2.0, "principal_usd": 1000.0,
        "taker_fee_pct": 0.04, "maker_fee_pct": 0.02,
        "window_start_utc": "2026-02-14T00:00:00Z", "window_end_utc": "2026-10-12T00:00:00Z",
    }


def _roll(sid: str, model: str) -> dict:
    return {
        "family": "combo", "strategy_id": sid, "direction": "LONG", "timeframe": "1h", "coins": 1,
        "coins_passing": 0, "coins_net_positive": 1, "passing_symbols": [], "trades": 1,
        "net_pnl_usd": 1.0, "fees_usd": -0.1, "worst_cell_net_usd": 1.0, "best_cell_net_usd": 1.0,
        "execution_models": [model], "risk_usd": [20.0], "usd_comparable": True,
    }


def test_board_fill_labels_and_caveats_state_the_old_ranking(tmp_path):
    rows = [("COMBO_02", "LEGACY_SIGNAL_CLOSE"), ("COMBO_SD_A", "LEGACY_SIGNAL_CLOSE"),
            ("COMBO_02_V2", "CAUSAL_NEXT_OPEN")]
    rank = tmp_path / "ranked.json"
    rank.write_text(json.dumps({
        "summary": {"per_style": {"day": {"cells": 3, "cells_net_positive": 3, "cells_passing": 0}}},
        "strategy_rollup": {"day": [_roll(s, m) for s, m in rows]},
        "cells_ranked": {"day": [_cell(s, m) for s, m in rows]},
    }), encoding="utf-8")
    board = build_strategy_rank_board(catalog={"strategies": [], "all_combinations": []}, path=rank)
    assert board["status"] == "OK"
    labels = {r["strategy_id"]: r["fill_label"] for r in board["groups"][0]["rows"]}
    assert labels == {
        "COMBO_02": "Signal close, no slippage",
        "COMBO_SD_A": "Signal close, 0.02% slippage included",
        "COMBO_02_V2": "Next bar open, 0.05% slippage included",
    }
    assert board["window"]["fees"] == {"taker_pct": 0.04, "maker_pct": 0.02}
    text = " ".join(board["caveats"])
    assert "COMBO strategies fill at the signal close with no slippage" not in text
    assert "COMBO_02_V2 fills at the next bar's open with 0.05%" in text
    assert "COMBO_SD_* also fill at the signal close, with 0.02%" in text
    assert "0.04% taker, 0.02% maker" in text
    assert list(board["caveats"]) == list(BOARD_CAVEATS)


def test_fill_label_keeps_the_strategy_id_optional():
    assert fill_label("LEGACY_SIGNAL_CLOSE") == "Signal close, no slippage"
    assert fill_label("CAUSAL_NEXT_OPEN", "COMBO_02_V2") == "Next bar open, 0.05% slippage included"


# ---- frozen and engine files ----------------------------------------------------------------


def test_frozen_oos2_files_match_their_lock():
    files = _lock_files()
    assert len(files) == 37
    for rel, sha in files.items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == sha, rel


def test_frozen_paper_and_backtest_files_are_byte_identical_to_the_base():
    _need_base()
    subjects = _git("log", "--format=%s", f"{BASE}..HEAD").stdout.splitlines()
    if any("D218" not in s for s in subjects):
        pytest.skip(f"commits other than D218 are on top of {BASE}; the per-commit guard below still runs")
    paths = [*_lock_files(), *PAPER_ENGINE_FILES, *BACKTEST_FILES]
    tracked = _git("ls-files", "--", *GUARDED_PREFIXES).stdout.splitlines()
    assert tracked, "guarded trees not found"
    for rel in [*paths, *tracked]:
        base = _git("rev-parse", f"{BASE}:{rel}")
        assert base.returncode == 0, rel
        now = _git("hash-object", "--", rel)
        assert now.stdout.strip() == base.stdout.strip(), rel


def test_no_d218_commit_touches_paper_backtest_or_frozen_files():
    _need_base()
    frozen = set(_lock_files())
    shas = _git("log", "--format=%H", "--grep=D218", f"{BASE}..HEAD").stdout.split()
    for sha in shas:
        touched = [p for p in _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.splitlines() if p]
        assert not [p for p in touched if p in frozen or _guarded(p)], (sha, touched)
        assert not [p for p in touched if p not in D218_ALLOWED], (sha, touched)
