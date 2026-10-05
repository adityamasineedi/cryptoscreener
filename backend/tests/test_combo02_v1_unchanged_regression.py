"""Regression: COMBO_02_V1 signal identity must remain unchanged by v1.1 work."""

from __future__ import annotations

from app.research.bos_combinations import COMBINATIONS, get_combination
from app.research.bos_strategy_comparison.htf import (
    HTF_ALIGNED,
    HTF_CONFLICT,
    HTF_NEUTRAL_UNAVAILABLE,
    classify_htf_alignment,
)
from app.research.v1_production import (
    COMBO_ID,
    COMBO_VERSION,
    FROZEN_V1_SYMBOLS,
    V1_PAPER_RISK_BY_SYMBOL,
)


def test_v1_combo_definition_unchanged():
    c = get_combination("COMBO_02")
    assert c is not None
    assert c.combination_id == "COMBO_02"
    assert c.require_bos is True
    assert c.require_trend is True
    assert c.require_htf_alignment is True
    assert c.require_pullback is False
    assert COMBO_ID == "COMBO_02"
    assert COMBO_VERSION == "v1-combo02-long-htf"


def test_v1_freeze_symbols_unchanged():
    assert FROZEN_V1_SYMBOLS == frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})


def test_v1_paper_risk_books_not_mutated_by_v1_1():
    # V1 path remains flat 2% in code books; v1.1 carries differentiated risk separately.
    assert V1_PAPER_RISK_BY_SYMBOL["BTCUSDT"] == 0.02
    assert V1_PAPER_RISK_BY_SYMBOL["ETHUSDT"] == 0.02
    assert V1_PAPER_RISK_BY_SYMBOL["SOLUSDT"] == 0.02


def test_htf_classifier_semantics_unchanged():
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS", trend_4h="BULLISH", trend_1h="BULLISH"
        )
        == HTF_ALIGNED
    )
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS", trend_4h="BEARISH", trend_1h="BEARISH"
        )
        == HTF_CONFLICT
    )
    assert (
        classify_htf_alignment(
            bos_direction="BULLISH_BOS", trend_4h="BEARISH", trend_1h="BULLISH"
        )
        == HTF_NEUTRAL_UNAVAILABLE
    )


def test_combo02_local_still_distinct():
    local = COMBINATIONS["COMBO_02_LOCAL"]
    v1 = COMBINATIONS["COMBO_02"]
    assert local.require_htf_alignment is False
    assert v1.require_htf_alignment is True
