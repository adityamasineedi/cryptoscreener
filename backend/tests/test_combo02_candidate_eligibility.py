"""Tests for COMBO_02 candidate eligibility research pipeline.

Research-only: asserts frozen v1 boundaries are not mutated by this code path.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from app.research.bos_combinations import get_combination
from app.research.combo02_candidate_eligibility import (
    classify_eligibility,
    classify_oos,
    entry_overlap_pct,
    incremental_portfolio_stats,
    max_drawdown_r,
    max_losing_streak,
    max_winning_streak,
    peak_concurrent_positions,
    TradeInterval,
)
from app.research.combo02_candidate_research import (
    compute_trade_metrics,
    map_run_status,
    strategy_fingerprint,
)
from app.research.combo02_candidate_selector import exclusion_reason
from app.research.combo02_candidate_thresholds import (
    DEFAULT_WINDOW,
    CandidateSelectorConfig,
    DEFAULT_THRESHOLDS,
)
from app.research.v1_production import V1_SYMBOLS


# ---------------------------------------------------------------------------
# Selector exclusions
# ---------------------------------------------------------------------------


def test_selector_excludes_stablecoins():
    assert exclusion_reason("USDCUSDT") == "stablecoin"
    assert exclusion_reason("DAIUSDT") == "stablecoin"


def test_selector_excludes_leveraged_tokens():
    assert exclusion_reason("BTCUPUSDT") == "leveraged_token"
    assert exclusion_reason("ETHDOWNUSDT") == "leveraged_token"
    assert exclusion_reason("BTC3LUSDT") == "leveraged_token"


def test_selector_excludes_inactive():
    assert (
        exclusion_reason("ABCUSDT", status="BREAK") == "inactive"
        or exclusion_reason("ABCUSDT", status="DELISTED") == "inactive"
    )


def test_selector_excludes_nonstandard():
    # base "BTC-" / "FOO$" contain non-alnum → excluded
    assert exclusion_reason("BTC-USDT") == "nonstandard_symbol"
    assert exclusion_reason("FOO$USDT") == "nonstandard_symbol"
    # pure alphanumeric bases are allowed by this gate
    assert exclusion_reason("BNBUSDT") is None


def test_selector_excludes_v1_by_default():
    assert exclusion_reason("BTCUSDT") == "v1_universe_reserved"
    assert exclusion_reason("ETHUSDT") == "v1_universe_reserved"
    assert exclusion_reason("SOLUSDT") == "v1_universe_reserved"


def test_selector_allows_liquid_perp_when_active():
    assert (
        exclusion_reason(
            "BNBUSDT",
            status="TRADING",
            contract_type="PERPETUAL",
            market_type="futures_perp",
        )
        is None
    )


def test_selector_config_defaults():
    cfg = CandidateSelectorConfig()
    assert cfg.top_n == 30
    assert cfg.min_history_days == 540
    assert cfg.min_ohlcv_completeness == 0.99
    assert cfg.min_avg_24h_quote_volume_usd == 5_000_000.0
    assert cfg.exclude_stablecoins is True
    assert cfg.exclude_leveraged_tokens is True


# ---------------------------------------------------------------------------
# Tier classifier
# ---------------------------------------------------------------------------


def test_insufficient_data():
    tier, reasons, conds = classify_eligibility(
        trade_count=3,
        net_avg_r=1.0,
        net_pnl=10.0,
        profit_factor=2.0,
        max_dd_r=1.0,
        max_lose_streak=1,
        fee_share=0.1,
    )
    assert tier == "INSUFFICIENT_DATA"
    assert conds  # still emits promising condition matrix


def test_reject_negative_expectancy():
    tier, _, _ = classify_eligibility(
        trade_count=25,
        net_avg_r=-0.1,
        net_pnl=-5.0,
        profit_factor=0.8,
        max_dd_r=2.0,
        max_lose_streak=2,
        fee_share=0.2,
    )
    assert tier == "REJECT"


def test_reject_severe_dd():
    tier, reasons, conds = classify_eligibility(
        trade_count=25,
        net_avg_r=0.5,
        net_pnl=50.0,
        profit_factor=1.5,
        max_dd_r=12.0,
        max_lose_streak=2,
        fee_share=0.2,
    )
    assert tier == "REJECT"
    assert any("max_dd" in r for r in reasons)
    dd_rule = next(c for c in conds if c["rule"] == "max_drawdown_le_10R")
    assert dd_rule["passed"] is False
    assert dd_rule["actual"] == 12.0
    assert dd_rule["required"] == "<= 10.0"


def test_eligibility_report_structured_and_deterministic():
    from app.research.combo02_candidate_eligibility import eligibility_report

    a = eligibility_report(
        trade_count=25,
        net_avg_r=-0.21,
        net_pnl=-5.0,
        profit_factor=0.73,
        max_dd_r=13.69,
        max_lose_streak=2,
        fee_share=0.2,
    )
    b = eligibility_report(
        trade_count=25,
        net_avg_r=-0.21,
        net_pnl=-5.0,
        profit_factor=0.73,
        max_dd_r=13.69,
        max_lose_streak=2,
        fee_share=0.2,
    )
    assert a == b
    assert a["tier"] == "RESEARCH_REJECTED"
    assert a["passed"] is False
    by_rule = {r["rule"]: r for r in a["reasons"]}
    assert by_rule["net_avg_r_gt_0"]["passed"] is False
    assert by_rule["net_avg_r_gt_0"]["actual"] == -0.21
    assert by_rule["profit_factor_gt_1"]["passed"] is False
    assert by_rule["max_drawdown_le_10R"]["passed"] is False


def test_promising():
    tier, reasons, conds = classify_eligibility(
        trade_count=25,
        net_avg_r=0.4,
        net_pnl=50.0,
        profit_factor=1.5,
        max_dd_r=3.0,
        max_lose_streak=3,
        fee_share=0.2,
    )
    assert tier == "PROMISING"
    assert "meets_promising_thresholds" in reasons
    assert all(c["passed"] for c in conds)


def test_watchlist_thin():
    tier, reasons, conds = classify_eligibility(
        trade_count=12,
        net_avg_r=0.1,
        net_pnl=5.0,
        profit_factor=1.1,
        max_dd_r=2.0,
        max_lose_streak=2,
        fee_share=0.2,
    )
    assert tier == "WATCHLIST"
    assert any(not c["passed"] for c in conds)


def test_streak_and_drawdown_from_r_series():
    rs = [1.0, -1.0, -1.0, -1.0, 0.5, -1.0, -1.0]
    assert max_losing_streak(rs) == 3
    assert max_winning_streak([1, 1, -1, 1]) == 2
    assert max_drawdown_r([1, 1, -3, 1]) == 3.0


# ---------------------------------------------------------------------------
# OOS + portfolio deterministic helpers
# ---------------------------------------------------------------------------


def test_oos_v2_paper_candidate():
    label, reasons, conds = classify_oos(
        base_tier="PROMISING",
        oos_trade_count=12,
        oos_net_avg_r=0.2,
        oos_net_pnl=10.0,
        oos_profit_factor=1.3,
        oos_max_dd_r=3.0,
        oos_max_lose_streak=3,
        oos_usable=True,
    )
    assert label == "V2_PAPER_CANDIDATE"
    assert all(c["passed"] for c in conds)


def test_oos_insufficient_data():
    label, _, _ = classify_oos(
        base_tier="PROMISING",
        oos_trade_count=0,
        oos_net_avg_r=None,
        oos_net_pnl=None,
        oos_profit_factor=None,
        oos_max_dd_r=None,
        oos_max_lose_streak=None,
        oos_usable=False,
    )
    assert label == "INSUFFICIENT_OOS_DATA"


def test_oos_needs_more_evidence():
    label, _, _ = classify_oos(
        base_tier="PROMISING",
        oos_trade_count=12,
        oos_net_avg_r=-0.1,
        oos_net_pnl=-2.0,
        oos_profit_factor=0.8,
        oos_max_dd_r=2.0,
        oos_max_lose_streak=2,
        oos_usable=True,
    )
    assert label == "PROMISING_NEEDS_MORE_EVIDENCE"


def test_portfolio_overlap_and_concurrency_deterministic():
    t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2025, 1, 1, 2, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 1, 4, 0, tzinfo=timezone.utc)
    t3 = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)
    cand = [
        TradeInterval("BNBUSDT", t0, t2, 1.0),
        TradeInterval("BNBUSDT", t1, t3, -0.5),
    ]
    v1 = [
        TradeInterval("BTCUSDT", t0, t1, 0.5),
        TradeInterval("ETHUSDT", t2, t3, 0.2),
    ]
    assert peak_concurrent_positions(cand + v1) >= 2
    ov = entry_overlap_pct([t0, t1], [t0, t2], tolerance_hours=1.0)
    assert ov == 0.5
    stats = incremental_portfolio_stats(cand, v1)
    assert "combined_max_dd_r" in stats
    assert stats["peak_concurrent_with_v1"] >= 2


# ---------------------------------------------------------------------------
# Trade metrics field mapping (enrich_trades names)
# ---------------------------------------------------------------------------


def test_compute_trade_metrics_uses_enrich_trade_fields():
    trades = [
        {
            "status": "CLOSED",
            "r_net": 0.5,
            "r_gross": 0.6,
            "r_multiple": 0.6,
            "fee_total_usd": 1.0,
            "gross_pnl_usd": 12.0,
            "net_pnl_usd": 11.0,
            "holding_bars": 5,
            "entry_time": "2025-01-01T00:00:00+00:00",
            "exit_time": "2025-01-01T05:00:00+00:00",
            "exit_price": 100.0,
        },
        {
            "status": "CLOSED",
            "r_net": -1.0,
            "r_gross": -0.9,
            "fee_total_usd": 1.0,
            "gross_pnl_usd": -18.0,
            "net_pnl_usd": -19.0,
            "holding_bars": 10,
            "entry_time": "2025-01-02T00:00:00+00:00",
            "exit_time": "2025-01-02T10:00:00+00:00",
            "exit_price": 90.0,
        },
    ]
    m = compute_trade_metrics(trades)
    assert m["trade_count"] == 2
    assert m["total_fees"] == 2.0
    assert m["gross_pnl"] == -6.0
    assert m["net_pnl"] == -8.0
    assert m["max_losing_streak"] == 1
    assert map_run_status(engine_status="OK", metrics=m) == "COMPLETED"


def test_map_run_status_no_trades():
    m = compute_trade_metrics([])
    assert map_run_status(engine_status="OK", metrics=m) == "NO_TRADES"
    assert map_run_status(engine_status="INSUFFICIENT_DATA", metrics=m) == "INSUFFICIENT_OHLCV"


# ---------------------------------------------------------------------------
# Frozen runner settings + v1 boundary AST checks
# ---------------------------------------------------------------------------


def test_fingerprint_frozen_combo02_1h_long_htf():
    fp = strategy_fingerprint(DEFAULT_WINDOW)
    assert fp["combination_id"] == "COMBO_02"
    assert fp["setup_timeframe"] == "1h"
    assert fp["direction"] == "LONG"
    assert fp["combo_require_htf"] is True
    assert fp["no_path_b"] is True
    assert fp["no_combo_02_local"] is True
    assert fp["no_15m"] is True
    assert fp["settings_identical_across_candidates"] is True
    assert fp["window_start"] == "2025-01-01"
    assert fp["window_end"] == "2026-01-31"
    combo = get_combination("COMBO_02")
    assert combo is not None
    assert combo.require_htf_alignment is True


def _module_assigns_v1_symbols(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "V1_SYMBOLS":
                    return True
                if isinstance(t, ast.Attribute) and t.attr == "V1_SYMBOLS":
                    return True
        if isinstance(node, ast.AugAssign):
            t = node.target
            if isinstance(t, ast.Name) and t.id == "V1_SYMBOLS":
                return True
    return False


def test_candidate_modules_do_not_mutate_v1_symbols():
    root = Path(__file__).resolve().parents[1] / "app" / "research"
    for name in (
        "combo02_candidate_eligibility.py",
        "combo02_candidate_selector.py",
        "combo02_candidate_research.py",
        "combo02_candidate_thresholds.py",
    ):
        assert not _module_assigns_v1_symbols(root / name), name


def test_v1_universe_still_btc_eth_sol_only():
    assert V1_SYMBOLS == frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})


def test_thresholds_match_spec():
    t = DEFAULT_THRESHOLDS
    assert t.promising_min_trades == 20
    assert t.promising_min_net_avg_r == 0.25
    assert t.promising_min_profit_factor == 1.25
    assert t.promising_max_dd_r == 6.0
    assert t.promising_max_losing_streak == 6
    assert t.promising_max_fee_pct_of_gross == 0.70
    assert t.oos_min_trades == 10
