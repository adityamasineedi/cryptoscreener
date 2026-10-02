"""Tests for opt-in research gates (default OFF)."""

from __future__ import annotations

from app.signals.config import SignalConfig
from app.signals.research_gate import apply_research_gate
from app.signals.schemas import MTFAlignment, SignalStatus


def test_research_gate_default_off_passthrough():
    cfg = SignalConfig()
    assert cfg.research_gate_enabled is False
    out = apply_research_gate(
        status=SignalStatus.SHORT_ENTRY_CANDIDATE.value,
        direction="SHORT",
        mtf={"MTF_ALIGNMENT": MTFAlignment.STRONG_SHORT.value},
        config=cfg,
    )
    assert out["status"] == SignalStatus.SHORT_ENTRY_CANDIDATE.value
    assert out["direction"] == "SHORT"
    assert out["research_gate_applied"] is False


def test_research_gate_block_shorts():
    cfg = SignalConfig(
        research_gate_enabled=True,
        research_gate_block_shorts=True,
    )
    out = apply_research_gate(
        status=SignalStatus.SHORT_ENTRY_CANDIDATE.value,
        direction="SHORT",
        mtf={"MTF_ALIGNMENT": MTFAlignment.STRONG_SHORT.value},
        config=cfg,
    )
    assert out["status"] == SignalStatus.NO_SETUP.value
    assert out["direction"] is None
    assert out["research_gate_applied"] is True
    assert out["research_gate_reason"] == "research_gate_block_shorts"

    long_out = apply_research_gate(
        status=SignalStatus.LONG_ENTRY_CANDIDATE.value,
        direction="LONG",
        mtf={"MTF_ALIGNMENT": MTFAlignment.STRONG_LONG.value},
        config=cfg,
    )
    assert long_out["status"] == SignalStatus.LONG_ENTRY_CANDIDATE.value
    assert long_out["research_gate_applied"] is False


def test_research_gate_block_htf_conflict():
    cfg = SignalConfig(
        research_gate_enabled=True,
        research_gate_block_htf_conflict=True,
    )
    conflict = apply_research_gate(
        status=SignalStatus.LONG_ENTRY_CANDIDATE.value,
        direction="LONG",
        mtf={"MTF_ALIGNMENT": MTFAlignment.CONFLICT.value},
        config=cfg,
    )
    assert conflict["status"] == SignalStatus.CONFLICT.value
    assert conflict["research_gate_applied"] is True

    mixed = apply_research_gate(
        status=SignalStatus.LONG_ENTRY_CANDIDATE.value,
        direction="LONG",
        mtf={"MTF_ALIGNMENT": MTFAlignment.MIXED.value},
        config=cfg,
    )
    assert mixed["status"] == SignalStatus.NO_SETUP.value
    assert mixed["research_gate_reason"] == "research_gate_require_strong_long_mtf"

    strong = apply_research_gate(
        status=SignalStatus.LONG_ENTRY_CANDIDATE.value,
        direction="LONG",
        mtf={"MTF_ALIGNMENT": MTFAlignment.STRONG_LONG.value},
        config=cfg,
    )
    assert strong["status"] == SignalStatus.LONG_ENTRY_CANDIDATE.value
    assert strong["research_gate_applied"] is False
