"""Repository integrity checks for the COMBO_02 SHORT research pipeline.

Distinguishes:
- SHORT operational isolation (paper/Telegram/production blocked)
- SHORT research pipeline completeness (full module, not a boundary stub)
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.research import combo02_short_research as short_mod
from app.research.combo02_short_research import (
    REQUIRED_PUBLIC_API,
    SHORT_RESEARCH_MODULE_INCOMPLETE_CODE,
    SHORT_RESEARCH_MODULE_KIND,
    SHORT_RESEARCH_PIPELINE_COMPLETE,
    ShortResearchOnlyError,
    assert_short_research_only_boundary,
    assert_short_research_pipeline_complete,
    assert_short_signal_geometry,
    classify_short_oos,
    short_research_identity,
    short_research_pipeline_status,
    simulate_short_research_trade,
    validate_short_research_signal,
)
from app.research.short_research_constants import (
    COMBO_VERSION,
    DIRECTION,
    PAPER_ELIGIBLE,
    PRODUCTION_APPROVED,
    SOURCE,
    STRATEGY_ID,
    TELEGRAM_ELIGIBLE,
)


MODULE_PATH = Path(short_mod.__file__).resolve()


def test_full_short_research_module_imports():
    assert MODULE_PATH.name == "combo02_short_research.py"
    # Boundary stubs were ~100 lines; full pipeline is several hundred.
    line_count = len(MODULE_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count >= 600, f"module too small to be full pipeline: {line_count}"


def test_expected_public_functions_exist():
    for name in REQUIRED_PUBLIC_API:
        assert hasattr(short_mod, name), f"missing public API: {name}"
        assert callable(getattr(short_mod, name)) or inspect.isclass(
            getattr(short_mod, name)
        )


def test_expected_strategy_identity_returned():
    ident = short_research_identity(symbol="BTCUSDT")
    assert ident["strategy_id"] == "COMBO_02_SHORT_RESEARCH"
    assert ident["combo_version"] == "v2-short-research"
    assert ident["source"] == "SHORT_RESEARCH_PIPELINE"
    assert ident["direction"] == "SHORT"
    assert ident["paper_eligible"] is False
    assert ident["production_approved"] is False
    assert ident["telegram_eligible"] is False
    # Constants must match identity contract.
    assert STRATEGY_ID == "COMBO_02_SHORT_RESEARCH"
    assert COMBO_VERSION == "v2-short-research"
    assert SOURCE == "SHORT_RESEARCH_PIPELINE"
    assert DIRECTION == "SHORT"
    assert PAPER_ELIGIBLE is False
    assert PRODUCTION_APPROVED is False
    assert TELEGRAM_ELIGIBLE is False


def test_classification_and_oos_helpers_work():
    # classify_short_oos is a thin wrapper around classify_oos with SHORT identity.
    assert callable(classify_short_oos)
    assert callable(validate_short_research_signal)
    status = short_research_pipeline_status()
    assert status["short_isolation_available"] is True
    assert status["short_research_pipeline_available"] is True
    assert status["module_kind"] == "FULL_PIPELINE"


def test_simulate_short_research_trade_deterministic():
    # One-bar SHORT simulator: stop hit when high reaches stop above entry.
    trade = simulate_short_research_trade(
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
        risk_usd=20.0,
        candle_high=106.0,
        candle_low=99.0,
    )
    assert trade["direction"] == "SHORT"
    assert trade["strategy_id"] == STRATEGY_ID
    assert trade.get("paper_eligible") is False
    assert trade.get("telegram_eligible") is False
    assert trade.get("production_approved") is False
    assert trade["hit_stop"] is True
    assert trade["outcome"] in {"STOP", "SL", "stop", "STOPPED"}


def test_forensic_module_available():
    from app.research import short_research_forensics as forensics

    assert hasattr(forensics, "enrich_trade_forensic_fields")
    src = Path(forensics.__file__).read_text(encoding="utf-8")
    assert len(src.splitlines()) >= 200


def test_missing_module_cannot_silently_degrade_to_stub():
    assert SHORT_RESEARCH_PIPELINE_COMPLETE is True
    assert SHORT_RESEARCH_MODULE_KIND == "FULL_PIPELINE"
    assert_short_research_pipeline_complete()  # must not raise


def test_incomplete_module_returns_short_research_module_incomplete(monkeypatch):
    monkeypatch.setattr(short_mod, "SHORT_RESEARCH_PIPELINE_COMPLETE", False)
    with pytest.raises(RuntimeError) as ei:
        short_mod.assert_short_research_pipeline_complete()
    assert SHORT_RESEARCH_MODULE_INCOMPLETE_CODE in str(ei.value)
    status = short_mod.short_research_pipeline_status()
    assert status["short_research_pipeline_available"] is False
    assert status["short_isolation_available"] is True


def test_operational_boundaries_remain_blocked():
    with pytest.raises(ShortResearchOnlyError):
        assert_short_research_only_boundary({"direction": "SHORT"}, detail="test")
    with pytest.raises(ShortResearchOnlyError):
        assert_short_research_only_boundary(
            {"strategy_id": STRATEGY_ID, "direction": "LONG"}, detail="identity"
        )
    # Geometry helper accepts valid SHORT geometry only.
    assert_short_signal_geometry(
        direction="SHORT",
        entry_price=100.0,
        stop_price=105.0,
        take_profit_price=90.0,
    )


def test_module_source_is_not_boundary_stub_ast():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    fn_names = {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for required in (
        "classify_short_oos",
        "simulate_short_research_trade",
        "finalize_short_candidate",
        "ShortResearchRegistry",
    ):
        assert required in fn_names


def test_historical_short_research_artifacts_present():
    reports = Path(__file__).resolve().parents[1] / "reports" / "short_research"
    assert reports.is_dir()
    # Known artifacts from prior research runs — do not modify them.
    entry = reports / "short_entry_research_20261004T095422Z-24f02156.json"
    pullback = reports / "short_pullback_rejection_20261004T112702Z-305889de.json"
    assert entry.is_file(), "missing historical short_entry_research artifact"
    assert pullback.is_file(), "missing historical short_pullback_rejection artifact"
