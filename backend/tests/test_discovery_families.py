"""Isolation and registry checks for research-only discovery families."""

from __future__ import annotations

from pathlib import Path

from app.research.bos_combinations import COMBINATIONS
from app.research.market_structure_discovery_v1 import families


def test_only_existing_combinations_are_mapped() -> None:
    expected = {
        "bos_continuation": "COMBO_01",
        "bos_pullback": "COMBO_03",
        "sweep_plus_structure_shift": "COMBO_03_TRANSITION",
    }

    assert dict(families.MAPPED_COMBINATIONS) == expected
    assert set(expected.values()).issubset(COMBINATIONS)


def test_unmapped_families_are_not_registered_combinations() -> None:
    unmapped = {
        "choch_reversal_only",
        "choch_then_confirming_bos",
        "bos_fvg_retest",
    }

    assert set(families.FAMILIES) == set(families.FAMILY_COMBINATION_IDS)
    assert all(families.FAMILY_COMBINATION_IDS[name] is None for name in unmapped)
    assert unmapped.isdisjoint(COMBINATIONS)


def test_family_taxonomy_remains_outside_execution_and_notification_code() -> None:
    backend = Path(__file__).resolve().parents[1]
    app_root = backend / "app"
    prohibited_terms = ("signals", "paper", "live", "telegram")
    import_markers = (
        "market_structure_discovery_v1.families",
        "market_structure_discovery_v1 import families",
    )

    for path in app_root.rglob("*.py"):
        relative_name = path.relative_to(app_root).as_posix().lower()
        if not any(term in relative_name for term in prohibited_terms):
            continue
        source = path.read_text(encoding="utf-8")
        assert not any(marker in source for marker in import_markers), path

    family_source = Path(families.__file__).read_text(encoding="utf-8")
    assert "get_combination" not in family_source


def test_approval_flags_are_false() -> None:
    assert families.RESEARCH_ONLY is True
    assert families.OPERATOR_APPROVED is False
    assert families.PRODUCTION_APPROVED is False
    assert families.PAPER_ELIGIBLE is False
    assert families.LIVE_ELIGIBLE is False
    assert families.TELEGRAM_ELIGIBLE is False
