"""CANDLE12_V3_EXEC_FIX OOS contract runner and the non-acceptance smoke test.

The engine is replaced by canned results in the gate tests, so no reserved bar
is needed and nothing here reads the OOS window.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.research import candle12_v3 as v3
from app.research.candle12_v3_paper import contract as C
from app.research.candle12_v3_paper import oos
from app.research.candle12_v3_paper.oos import OOSRefused, evaluate_oos
from app.research.candle12_v3_paper.smoke import run_smoke
from app.research.candle12_v3_paper.store import PaperStore

UTC = timezone.utc
EARLIEST = C.earliest_evaluation()


def quiet_candles(after_start_hours: int = 48):
    start = C.OOS_START - timedelta(hours=C.WARMUP_BARS)
    n = C.WARMUP_BARS + after_start_hours
    return [{"time": start + timedelta(hours=i), "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
             "volume": 1.0} for i in range(n)]


def trade(i: int, net: float, *, funding: float = 0.0, coverage: str | None = "FULL") -> dict:
    t = C.OOS_START + timedelta(days=5 * i)
    return {"outcome": "TP1" if net > 0 else "SL", "entry_time": t.isoformat(),
            "exit_time": (t + timedelta(hours=10)).isoformat(), "net_pnl_usd": net, "funding_usd": funding,
            "funding_coverage": coverage}


@pytest.fixture
def engine(monkeypatch):
    """Canned engine: {'primary': [...], 'stress': [...]} picked by slippage."""
    book = {"primary": [], "stress": [], "calls": 0}

    def fake_run(symbol, timeframe, candles, *, config, index_start, funding=None):
        book["calls"] += 1
        key = "stress" if config.entry_slippage == C.STRESS_SLIPPAGE else "primary"
        return {"trades": book[key], "rejected": [], "counts": {}}

    monkeypatch.setattr(oos.v3, "run_candle12_v3", fake_run)
    return book


@pytest.fixture
def lock():
    # Acceptance reads need a freeze-oos lock (oos.lock_integrity); build one for the current tree.
    from app.research.candle12_v3_paper.freeze import build_frozen_lock

    return build_frozen_lock(C.REPO_ROOT, now=datetime(2026, 10, 15, 20, tzinfo=UTC), commit="test",
                             branch="test", tag="test", supersedes={}, replaced=None)


def test_an_unfrozen_or_tampered_lock_never_passes(engine):
    engine["primary"] = engine["stress"] = [trade(i, 30.0 if i % 3 else -20.0) for i in range(30)]
    read = evaluate(C.build_lock(), EARLIEST + timedelta(days=1))
    assert read["classification"] == "CONFIG_DRIFT" and "lock_not_frozen" in read["config_drift"]


def evaluate(lock, now, *, exceptions=(), mode="VIRTUAL_PAPER_MAINNET", diagnostic=False, read_log=None):
    return evaluate_oos(candles=quiet_candles(), funding_rows=None, now=now, lock=lock,
                        open_exceptions=list(exceptions), store_mode=mode, diagnostic=diagnostic, read_log=read_log)


def winners(n=30, net=30.0):
    return [trade(i, net if i % 3 else -20.0) for i in range(n)]


def test_earliest_evaluation_needs_both_the_date_and_twelve_months():
    assert C.OOS_START == datetime(2026, 10, 16, tzinfo=UTC)
    assert C.EARLIEST_EVALUATION == datetime(2027, 10, 15, tzinfo=UTC)
    assert EARLIEST == datetime(2027, 10, 16, tzinfo=UTC)  # 12 months after the start is the later of the two


# 14
def test_runner_refuses_early_evaluation(engine, lock):
    engine["primary"] = engine["stress"] = winners(40)
    with pytest.raises(OOSRefused) as exc:
        evaluate(lock, EARLIEST - timedelta(hours=1))
    assert exc.value.reason == "TOO_EARLY" and engine["calls"] == 0
    with pytest.raises(OOSRefused):
        evaluate(lock, datetime(2027, 10, 15, 12, tzinfo=UTC))


# 15
def test_runner_refuses_below_thirty_trades(engine, lock):
    engine["primary"] = engine["stress"] = winners(29)
    with pytest.raises(OOSRefused) as exc:
        evaluate(lock, EARLIEST + timedelta(days=1))
    assert exc.value.reason == "BELOW_MIN_TRADES" and exc.value.detail["closed_trades"] == 29


# 16
def test_runner_accepts_only_after_both_time_and_trades(engine, lock, tmp_path):
    engine["primary"] = engine["stress"] = winners(30)
    log = tmp_path / "reads.jsonl"
    read = evaluate(lock, EARLIEST + timedelta(days=1), read_log=log)
    assert read["classification"] == "OOS_PASS" and read["acceptance"] is True
    assert read["primary"]["closed_trades"] == 30
    stored = json.loads(log.read_text().splitlines()[0])
    for key in ("code", "config", "data"):
        assert stored["fingerprints"][key]
    assert stored["evaluated_at"] and stored["as_of"]
    early = evaluate(lock, EARLIEST - timedelta(days=30), diagnostic=True, read_log=log)
    assert early["acceptance"] is False and early["label"] == "NON_ACCEPTANCE_DIAGNOSTIC" and early["too_early"]
    assert len(log.read_text().splitlines()) == 2  # diagnostic reads are logged too


def test_classification_rules_and_precedence(engine, lock):
    after = EARLIEST + timedelta(days=1)
    engine["primary"] = engine["stress"] = [trade(i, 10.0 if i % 2 else -9.5) for i in range(30)]  # PF 1.05
    assert evaluate(lock, after)["classification"] == "OOS_FAIL_EDGE"
    engine["primary"] = winners(30)
    engine["stress"] = [trade(i, -1.0) for i in range(30)]
    assert evaluate(lock, after)["classification"] == "OOS_FAIL_ROBUSTNESS"
    engine["primary"] = engine["stress"] = [trade(i, -20.0) for i in range(18)] + [trade(18 + i, 60.0) for i in range(12)]
    read = evaluate(lock, after)  # 18 losses in a row = 18R drawdown, PF 2.0, net +360
    assert read["primary"]["max_drawdown_r"] == pytest.approx(18.0)
    assert read["classification"] == "OOS_FAIL_RISK"
    engine["primary"] = engine["stress"] = winners(30)
    read = evaluate(lock, after, exceptions=[{"id": "x", "category": "PARTIAL_FILL"}])
    assert read["classification"] == "EXECUTION_MISMATCH" and read["acceptance"] is False


def test_funding_counts_only_on_fully_covered_trades(engine, lock):
    engine["primary"] = engine["stress"] = [trade(i, 30.0, funding=-5.0, coverage="FULL" if i < 10 else "PARTIAL")
                                            for i in range(30)]
    read = evaluate(lock, EARLIEST + timedelta(days=1))
    assert read["primary"]["funding_included_usd"] == pytest.approx(-50.0)
    assert read["primary"]["funding_excluded_trades"] == 20
    assert read["primary"]["net_pnl_usd"] == pytest.approx(10 * 30.0 + 20 * 35.0)


# 17
def test_config_drift_is_detected(engine, lock, monkeypatch):
    assert C.drift(lock) == []
    engine["primary"] = engine["stress"] = winners(30)
    tampered = json.loads(json.dumps(lock))
    tampered["code_fingerprint"]["files"]["backend/app/research/candle12_v3.py"] = "0" * 64
    assert C.drift(tampered) == ["code:backend/app/research/candle12_v3.py"]
    assert evaluate(tampered, EARLIEST + timedelta(days=1))["classification"] == "CONFIG_DRIFT"
    monkeypatch.setattr(C, "contract_config", lambda slippage=C.PRIMARY_SLIPPAGE: v3.Candle12V3Config(
        book_side="LONG", risk_tolerance_pct=0.2, funding_enabled=True, entry_slippage=slippage, exit_slippage=slippage))
    assert "config_fingerprint" in C.drift(lock)


def test_contract_book_is_the_frozen_book():
    cfg = C.contract_config()
    assert (cfg.book_side, cfg.portfolio_lock_mode, cfg.bos_refire_policy, cfg.target_policy) == (
        "LONG", "per_side", "once_per_level", "nearest_target_full_exit")
    assert (cfg.entry_slippage_mode, cfg.entry_slippage, cfg.taker_fee, cfg.risk_tolerance_pct) == (
        "inclusive", 0.0002, 0.0004, 0.10)
    assert cfg.apply_exchange_filters and cfg.exchange_filter("BTCUSDT")[2] == 50.0 and cfg.funding_enabled
    assert C.contract_config(C.STRESS_SLIPPAGE).exit_slippage == 0.0005
    assert (C.MIN_CLOSED_TRADES, C.MIN_PROFIT_FACTOR, C.MAX_DRAWDOWN_R) == (30, 1.30, 17.0)


# 18
def test_smoke_is_explicitly_non_acceptance(tmp_path, lock):
    from test_candle12_v3 import _synthetic

    candles = _synthetic()
    rows = [{"time": candles[0]["time"] + timedelta(hours=8 * j), "rate": 0.0001, "mark_price": None}
            for j in range(len(candles) // 8)]
    cfg = v3.Candle12V3Config(apply_exchange_filters=False, funding_enabled=True)
    result = run_smoke(candles, first_trade_index=300, store_root=tmp_path, funding_rows=rows, symbol="SYN", cfg=cfg)
    assert result["result"] == "SMOKE_PASS", result["checks"]
    assert result["label"] == "NON_ACCEPTANCE_SMOKE_ONLY"
    assert result["counts_toward_oos"] is False and result["strategy_validation"] is False
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["mode"] == "SMOKE" and manifest["non_acceptance"] is True
    with pytest.raises(OOSRefused) as exc:
        evaluate(lock, EARLIEST + timedelta(days=1), mode="SMOKE")
    assert exc.value.reason == "NOT_A_PAPER_STORE"
    # Legacy PAPER stores (mainnet candles + testnet fills) and testnet broker checks never count either.
    for other in ("PAPER", "TESTNET_BROKER_CHECK", "DRY_RUN"):
        with pytest.raises(OOSRefused) as exc:
            evaluate(lock, EARLIEST + timedelta(days=1), mode=other)
        assert exc.value.reason == "NOT_A_PAPER_STORE"
    with pytest.raises(RuntimeError):  # the smoke run refuses reserved bars
        run_smoke(quiet_candles(), first_trade_index=C.WARMUP_BARS, store_root=tmp_path / "x", funding_rows=rows)


def test_smoke_fails_when_a_stage_is_missing(tmp_path):
    from test_candle12_v3 import _synthetic

    cfg = v3.Candle12V3Config(apply_exchange_filters=False, funding_enabled=True)
    result = run_smoke(_synthetic(), first_trade_index=300, store_root=tmp_path, funding_rows=None, symbol="SYN",
                       cfg=cfg)
    assert result["result"] == "SMOKE_FAIL" and result["checks"]["funding_captured"] is None
    assert PaperStore(tmp_path, mode="SMOKE").manifest["smoke_result"] == "SMOKE_FAIL"
