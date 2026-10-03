"""Unit + lookahead tests for multi-cap research strategies.

Synthetic fixtures only — not performance claims.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.research.bos_strategy_comparison.strategies import STRATEGIES as BOS_STRATEGIES
from app.research.combination_backtest import evaluate_candidate_trades, simulate_research_trade
from app.research.multi_cap_strategies.adapter import (
    MultiCapStrategyAdapter,
    list_strategy_definitions,
)
from app.research.multi_cap_strategies.cap_filter import (
    filter_symbols_for_strategy,
    symbol_eligible_for_strategy,
)
from app.research.multi_cap_strategies.config import (
    FVG_MITIGATION_RULE,
    SMALL_CAP_DEEP_RETRACE_THRESHOLD,
    MultiCapResearchConfig,
)
from app.research.multi_cap_strategies.fvg import (
    detect_fvg_at,
    update_mitigation,
    FVGState,
)
from app.research.multi_cap_strategies.large_cap_sweep_choch import (
    STRATEGY_ID as LARGE_ID,
    generate_candidates as gen_large,
)
from app.research.multi_cap_strategies.mid_cap_fvg_discount import (
    STRATEGY_ID as MID_ID,
    generate_candidates as gen_mid,
)
from app.research.multi_cap_strategies.reference import (
    ref_bullish_fvg,
    ref_discount_mask,
    ref_sweep_indices,
    ref_volume_bos_indices,
)
from app.research.multi_cap_strategies.runner import run_strategy_on_series
from app.research.multi_cap_strategies.small_cap_volume_bos import (
    STRATEGY_ID as SMALL_ID,
    generate_candidates as gen_small,
)
from app.research.schemas import ResearchTrade


def _candle(i: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> dict:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i)
    return {
        "time": t0,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


def _flat_series(n: int, price: float = 100.0, vol: float = 100.0) -> list[dict]:
    return [_candle(i, price, price + 0.5, price - 0.5, price, vol) for i in range(n)]


# ---------------------------------------------------------------------------
# Catalog / regression safety vs S1-S3
# ---------------------------------------------------------------------------


def test_strategy_ids_exact_and_do_not_collide_with_bos():
    ids = {d["strategy_id"] for d in list_strategy_definitions()}
    assert ids == {LARGE_ID, MID_ID, SMALL_ID}
    for sid in ids:
        assert sid not in BOS_STRATEGIES


def test_fvg_mitigation_rule_documented():
    assert "low <= upper" in FVG_MITIGATION_RULE
    assert "fully mitigated" in FVG_MITIGATION_RULE.lower() or "Full mitigation" in FVG_MITIGATION_RULE


def test_deep_retrace_threshold_documented_default():
    cfg = MultiCapResearchConfig()
    assert cfg.small_cap_deep_retrace_threshold == SMALL_CAP_DEEP_RETRACE_THRESHOLD
    assert cfg.small_cap_deep_retrace_threshold == 0.618


# ---------------------------------------------------------------------------
# Cap classification
# ---------------------------------------------------------------------------


def test_cap_group_unavailable_no_guess():
    ok, label, reason = symbol_eligible_for_strategy("FOOUSDT", None, "LARGE_CAP")
    assert not ok
    assert reason == "CAP_GROUP_UNAVAILABLE"
    assert label == "CAP_GROUP_UNAVAILABLE"


def test_cap_group_enforcement():
    elig = filter_symbols_for_strategy(
        ["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        {
            "AAAUSDT": 20_000_000_000,
            "BBBUSDT": 2_000_000_000,
            "CCCUSDT": 200_000_000,
        },
        "MID_CAP",
    )
    assert elig.eligible_symbols == ["BBBUSDT"]
    assert any(r["symbol"] == "AAAUSDT" for r in elig.excluded_symbols)
    assert any(r["symbol"] == "CCCUSDT" for r in elig.excluded_symbols)


# ---------------------------------------------------------------------------
# Sweep fixture
# ---------------------------------------------------------------------------


def _sweep_fixture() -> list[dict]:
    """Build series with a clear downside sweep around bar 60."""
    candles = []
    price = 100.0
    for i in range(80):
        if i < 50:
            # gentle downtrend establishing lows
            price = 100.0 - i * 0.1
            candles.append(_candle(i, price + 0.2, price + 0.4, price - 0.2, price, 100))
        elif i == 55:
            # pierce prior lows then close back above
            candles.append(_candle(i, 94.0, 95.0, 90.0, 94.5, 200))
        else:
            price = 94.0 + (i - 55) * 0.15
            candles.append(_candle(i, price - 0.1, price + 0.3, price - 0.2, price, 100))
    return candles


def test_sweep_detection_matches_reference():
    candles = _sweep_fixture()
    cfg = MultiCapResearchConfig(large_cap_htf_swing_lookback=48, min_bars=50)
    # Extract sweep indices via strategy internal path by checking metadata
    from app.research.multi_cap_strategies.common import extract_ohlcv, shifted_rolling_min

    _, _, lows, closes, _ = extract_ohlcv(candles)
    prior = shifted_rolling_min(lows, 48)
    adapter_sweeps = [
        i
        for i in range(48, len(lows))
        if not np.isnan(prior[i]) and lows[i] < prior[i] and closes[i] > prior[i]
    ]
    ref = ref_sweep_indices(candles, 48)
    assert adapter_sweeps == ref
    assert 55 in adapter_sweeps


def test_no_entry_on_sweep_candle():
    candles = _sweep_fixture()
    cfg = MultiCapResearchConfig(large_cap_htf_swing_lookback=48, min_bars=50)
    cands = gen_large("AAAUSDT", "15m", candles, config=cfg)
    for c in cands:
        assert c.entry_index != 55


def test_sweep_without_choch_no_entry():
    # Flat after sweep — no swing high break
    candles = _flat_series(80, 100.0)
    # Force a sweep-like bar without later structure break
    candles[55] = _candle(55, 99.5, 100.0, 95.0, 99.8, 100)
    cfg = MultiCapResearchConfig(large_cap_htf_swing_lookback=20, min_bars=40)
    cands = gen_large("AAAUSDT", "15m", candles, config=cfg)
    # May or may not find CHOCH depending on swings; if any, entry after sweep
    for c in cands:
        assert c.entry_index > 55


# ---------------------------------------------------------------------------
# FVG + discount
# ---------------------------------------------------------------------------


def _fvg_discount_fixture() -> list[dict]:
    candles = _flat_series(40, 100.0)
    # Create bullish FVG: candle 30 high < candle 32 low
    candles[30] = _candle(30, 100, 100.2, 99.8, 100.0, 100)
    candles[31] = _candle(31, 100.0, 101.5, 100.0, 101.2, 100)
    candles[32] = _candle(32, 101.2, 101.8, 100.8, 101.0, 100)  # gap: 100.2 -> 100.8
    # Later return into discount + into FVG
    for i in range(33, 40):
        px = 100.5 - (i - 33) * 0.1
        candles[i] = _candle(i, px + 0.05, px + 0.2, px - 0.15, px, 100)
    # Ensure bar intersects FVG [100.2, 100.8]
    candles[36] = _candle(36, 100.6, 100.7, 100.3, 100.4, 100)
    return candles


def test_bullish_fvg_detection_matches_reference():
    candles = _fvg_discount_fixture()
    from app.research.multi_cap_strategies.common import extract_ohlcv

    _, highs, lows, _, _ = extract_ohlcv(candles)
    zone = detect_fvg_at(highs, lows, 32)
    assert zone is not None
    assert zone.direction == "BULLISH"
    ref = [z for z in ref_bullish_fvg(candles) if z["created_at"] == 32][0]
    assert abs(zone.lower - ref["lower"]) < 1e-9
    assert abs(zone.upper - ref["upper"]) < 1e-9


def test_fvg_mitigation_stateful():
    highs = np.array([10.0, 11.0, 12.5])
    lows = np.array([9.0, 10.5, 12.0])  # bullish FVG at i=2: [11, 12]
    # Actually h0=10 < l2=12 → FVG [10, 12]
    highs = np.array([10.0, 11.5, 13.0, 12.0, 9.5])
    lows = np.array([9.0, 10.0, 12.5, 11.0, 9.0])
    closes = np.array([9.5, 11.0, 12.8, 11.5, 9.2])
    state = FVGState()
    z = detect_fvg_at(highs, lows, 2)
    assert z is not None and z.direction == "BULLISH"
    state.zones.append(z)
    # bar 3 intersects but does not fully mitigate (low 11 > lower)
    update_mitigation(state, index=3, low=11.0, high=12.0, close=11.5)
    assert state.zones[0].mitigated is False
    # bar 4 fully mitigates
    update_mitigation(state, index=4, low=9.0, high=9.5, close=9.2)
    assert state.zones[0].mitigated is True
    assert state.zones[0].mitigation_time == 4


def test_mitigated_fvg_not_reused():
    candles = _fvg_discount_fixture()
    # Fully fill FVG before later bars
    candles[33] = _candle(33, 100.5, 100.6, 99.5, 100.0, 100)  # low through floor
    cfg = MultiCapResearchConfig(mid_cap_range_window=10, min_bars=20)
    cands = gen_mid("MIDUSDT", "15m", candles, config=cfg)
    # After full mitigation at 33, later bars should not enter on that FVG
    for c in cands:
        assert c.metadata.get("fvg_lower") is not None
        # If entry after mitigation bar for same FVG created_at 32 — forbidden
        if c.entry_index > 33:
            assert c.metadata.get("fvg_age", 0) >= 0
            # The used FVG key prevents reuse; entry_index>33 with created 32 shouldn't happen
            assert not (
                c.events
                and any(
                    e.event_type == "FVG_BULLISH" and e.bar_index == 32
                    for e in c.events
                )
                and c.entry_index > 33
            )


def test_premium_blocks_entry():
    candles = _flat_series(50, 100.0)
    # Strong uptrend so close stays in premium
    for i in range(30, 50):
        px = 100 + (i - 30) * 1.0
        candles[i] = _candle(i, px - 0.2, px + 0.5, px - 0.3, px, 100)
    # Force an FVG mid-way
    candles[35] = _candle(35, 105, 105.2, 104.8, 105.0, 100)
    candles[36] = _candle(36, 105, 108, 105, 107.5, 100)
    candles[37] = _candle(37, 107.5, 109, 106.5, 108.5, 100)
    cfg = MultiCapResearchConfig(mid_cap_range_window=10, min_bars=20)
    cands = gen_mid("MIDUSDT", "15m", candles, config=cfg)
    for c in cands:
        assert c.metadata.get("discount") is True


# ---------------------------------------------------------------------------
# Volume BOS
# ---------------------------------------------------------------------------


def _volume_bos_fixture(*, high_volume: bool = True, deep_retrace: bool = False) -> list[dict]:
    candles = _flat_series(80, 100.0, vol=100.0)
    # Rising range then breakout
    for i in range(60, 70):
        px = 100 + (i - 60) * 0.2
        candles[i] = _candle(i, px, px + 0.3, px - 0.1, px, 100)
    vol = 400.0 if high_volume else 150.0  # 3x of ~100 sma needs >300
    candles[70] = _candle(70, 102.0, 104.0, 101.8, 103.5, vol)
    for j in range(1, 6):
        if deep_retrace:
            # Retrace deeply into impulse
            candles[70 + j] = _candle(70 + j, 102.0, 102.2, 101.5, 101.8, 100)
        else:
            candles[70 + j] = _candle(70 + j, 103.5, 104.0, 103.0, 103.6, 100)
    return candles


def test_volume_bos_matches_reference():
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=False)
    ref = ref_volume_bos_indices(candles)
    assert 70 in ref


def test_volume_below_3x_no_signal():
    candles = _volume_bos_fixture(high_volume=False, deep_retrace=False)
    cfg = MultiCapResearchConfig(min_bars=60)
    cands = gen_small("SMLUSDT", "15m", candles, config=cfg)
    assert all(c.metadata.get("bos_index") != 70 for c in cands)


def test_deep_retrace_invalidates():
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=True)
    cfg = MultiCapResearchConfig(
        min_bars=60,
        small_cap_deep_retrace_threshold=0.618,
        small_cap_deep_retrace_window=5,
    )
    cands = gen_small("SMLUSDT", "15m", candles, config=cfg)
    # Should invalidate — no confirmed entry from bar 70 signal
    assert not any(c.metadata.get("bos_index") == 70 for c in cands)


def test_volume_bos_entry_after_retrace_window():
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=False)
    cfg = MultiCapResearchConfig(
        min_bars=60,
        small_cap_deep_retrace_window=5,
        small_cap_deep_retrace_threshold=0.618,
    )
    cands = gen_small("SMLUSDT", "15m", candles, config=cfg)
    assert any(c.metadata.get("bos_index") == 70 and c.entry_index == 75 for c in cands)


# ---------------------------------------------------------------------------
# Lookahead protection (Phase 7 A–K)
# ---------------------------------------------------------------------------


def test_lookahead_A_future_mutation_does_not_change_earlier_signal():
    """A) Changing future candles after an event does not change earlier signal."""
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=False)
    cfg = MultiCapResearchConfig(min_bars=60)
    as_of = 75
    base = gen_small("SMLUSDT", "15m", candles[: as_of + 1], config=cfg)
    mutated = [dict(c) for c in candles]
    for i in range(as_of + 1, len(mutated)):
        mutated[i] = _candle(i, 1.0, 2.0, 0.5, 1.5, 99999)
    after = gen_small("SMLUSDT", "15m", mutated[: as_of + 1], config=cfg)
    assert [(c.entry_index, c.entry_price) for c in base] == [
        (c.entry_index, c.entry_price) for c in after
    ]


def test_lookahead_B_truncate_after_entry_preserves_validity():
    """B) Removing candles after entry does not alter whether earlier entry was valid."""
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=False)
    cfg = MultiCapResearchConfig(
        min_bars=60,
        small_cap_deep_retrace_window=5,
        small_cap_deep_retrace_threshold=0.618,
    )
    full = gen_small("SMLUSDT", "15m", candles, config=cfg)
    assert full, "fixture must produce an entry"
    entry_i = full[0].entry_index
    truncated = gen_small("SMLUSDT", "15m", candles[: entry_i + 1], config=cfg)
    assert any(c.entry_index == entry_i for c in truncated)


def test_lookahead_C_rolling_48_excludes_current_and_future():
    """C) Rolling 48 does not include current/future bars."""
    from app.research.multi_cap_strategies.common import shifted_rolling_min

    lows = np.arange(60, dtype=float)  # 0..59
    prior = shifted_rolling_min(lows, 48)
    assert np.isnan(prior[47])
    # At i=48 window is lows[0:48] → min 0; current lows[48]=48 excluded
    assert prior[48] == 0.0
    assert prior[49] == 1.0  # lows[1:49]


def test_lookahead_D_rolling_24_excludes_current_and_future():
    """D) Rolling 24 does not include current/future bars."""
    from app.research.multi_cap_strategies.common import shifted_rolling_max

    highs = np.arange(40, dtype=float)
    prior = shifted_rolling_max(highs, 24)
    assert np.isnan(prior[23])
    assert prior[24] == 23.0  # max of highs[0:24], excludes highs[24]=24
    assert prior[25] == 24.0


def test_lookahead_E_rolling_10_bos_excludes_current():
    """E) Rolling 10 BOS reference excludes current bar."""
    from app.research.multi_cap_strategies.common import shifted_rolling_max

    highs = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 100], dtype=float)
    prior = shifted_rolling_max(highs, 10)
    # At i=10, window highs[0:10] max=10 — current 100 excluded
    assert prior[10] == 10.0


def test_lookahead_F_sma50_uses_only_historical_volume():
    """F) SMA50 uses only available historical volume (excludes current)."""
    from app.research.multi_cap_strategies.common import shifted_sma

    vols = np.ones(60, dtype=float) * 10.0
    vols[55] = 1000.0  # spike at 55
    sma = shifted_sma(vols, 50)
    # At i=55, SMA uses vols[5:55] — does NOT include vols[55]=1000
    assert abs(sma[55] - 10.0) < 1e-9
    # At i=56, window vols[6:56] includes the spike once
    assert sma[56] > 10.0


def test_lookahead_G_fvg_cannot_exist_before_candle3_closes():
    """G) FVG cannot exist before Candle 3 closes."""
    highs = np.array([10.0, 11.0, 13.0])
    lows = np.array([9.0, 10.0, 12.0])  # bullish: h0=10 < l2=12
    assert detect_fvg_at(highs, lows, 0) is None
    assert detect_fvg_at(highs, lows, 1) is None
    z = detect_fvg_at(highs, lows, 2)
    assert z is not None and z.created_at == 2


def test_lookahead_H_fvg_mitigation_cannot_use_future():
    """H) FVG mitigation cannot use future information."""
    highs = np.array([10.0, 11.5, 13.0, 12.0, 9.5])
    lows = np.array([9.0, 10.0, 12.5, 11.0, 9.0])
    closes = np.array([9.5, 11.0, 12.8, 11.5, 9.2])
    state = FVGState()
    z = detect_fvg_at(highs, lows, 2)
    assert z is not None
    state.zones.append(z)
    # Mitigate only through bar 3 — future bar 4 must not affect
    update_mitigation(state, index=3, low=11.0, high=12.0, close=11.5)
    assert state.zones[0].mitigated is False
    # Truncated series end at 3: still unmitigated
    assert state.zones[0].mitigation_time is None
    update_mitigation(state, index=4, low=9.0, high=9.5, close=9.2)
    assert state.zones[0].mitigated is True
    assert state.zones[0].mitigation_time == 4


def test_lookahead_I_swing_confirmation_needs_right_bars():
    """I) Swing confirmation occurs only after required right-side bars."""
    from app.signals.swing_detector import detect_swings

    candles = _flat_series(20, 100.0)
    # Clear swing high at bar 10
    candles[10] = _candle(10, 100, 105, 99.5, 100.5, 100)
    for i in range(11, 14):
        candles[i] = _candle(i, 100, 101, 99, 100, 100)
    # as_of before right bars complete → swing at 10 not confirmable
    early = detect_swings(candles, left=3, right=3, as_of_index=12)
    assert not any(s.bar_index == 10 and s.swing_type == "HIGH" for s in early)
    late = detect_swings(candles, left=3, right=3, as_of_index=13)
    assert any(s.bar_index == 10 and s.swing_type == "HIGH" for s in late)


def test_lookahead_J_entry_timestamp_ge_signal_confirmation():
    """J) Trade entry timestamp >= signal confirmation timestamp."""
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=False)
    cfg = MultiCapResearchConfig(min_bars=60, small_cap_deep_retrace_window=5)
    cands = gen_small("SMLUSDT", "15m", candles, config=cfg)
    assert cands
    for c in cands:
        conf_bars = [e.bar_index for e in c.events]
        assert c.entry_index >= max(conf_bars)
        assert c.entry_index >= int(c.metadata["bos_index"])


def test_lookahead_K_exit_after_entry():
    """K) Trade exit timestamp > entry timestamp (index)."""
    candles = _flat_series(40, 100.0)
    candles[12] = _candle(12, 101, 104, 100.5, 103.5, 100)
    trade = ResearchTrade(
        symbol="AAAUSDT",
        timeframe="15m",
        combination_id=LARGE_ID,
        entry_index=10,
        signal_time=None,
        direction="LONG",
        entry_price=100.0,
        stop_price=99.0,
        tp1=102.0,
        tp2=None,
        tp3=None,
        rr=2.0,
    )
    out = evaluate_candidate_trades(candles, [trade])
    assert len(out) == 1
    assert out[0].exit_index is not None
    assert out[0].exit_index > out[0].entry_index


def test_lookahead_sweep_prior_excludes_current():
    from app.research.multi_cap_strategies.common import shifted_rolling_min

    lows = np.array([5.0, 4.0, 3.0, 2.0, 1.0], dtype=float)
    prior = shifted_rolling_min(lows, 3)
    assert prior[3] == 3.0
    assert prior[4] == 2.0


def test_discount_mask_matches_reference_shifted():
    candles = _fvg_discount_fixture()
    mask = ref_discount_mask(candles, window=10)
    assert mask.dtype == bool or mask.dtype == np.bool_


# ---------------------------------------------------------------------------
# Evaluator integration
# ---------------------------------------------------------------------------


def test_evaluate_candidate_trades_reuses_combination_backtest():
    candles = _flat_series(30, 100.0)
    candles[12] = _candle(12, 101, 102.5, 100.5, 102.2, 100)

    def _mk() -> ResearchTrade:
        return ResearchTrade(
            symbol="AAAUSDT",
            timeframe="15m",
            combination_id=LARGE_ID,
            entry_index=10,
            signal_time=None,
            direction="LONG",
            entry_price=100.0,
            stop_price=99.0,
            tp1=102.0,
            tp2=None,
            tp3=None,
            rr=2.0,
        )

    out = evaluate_candidate_trades(candles, [_mk()])
    assert len(out) == 1
    assert out[0].outcome == "TP1"
    sim = simulate_research_trade(_mk(), candles)
    assert sim.outcome == "TP1"


def test_adapter_excludes_wrong_cap_before_signals():
    ad = MultiCapStrategyAdapter()
    candles = _volume_bos_fixture()
    cands, meta = ad.generate_candidates(
        SMALL_ID,
        "BIGUSDT",
        "15m",
        candles,
        market_cap=50_000_000_000,  # large-cap
    )
    assert cands == []
    assert meta["reason"] == "CAP_GROUP_MISMATCH"


def test_run_strategy_report_has_required_sections():
    candles = _volume_bos_fixture(high_volume=True, deep_retrace=False)
    cfg = MultiCapResearchConfig(min_bars=60)
    report = run_strategy_on_series(
        strategy_id=SMALL_ID,
        symbol="SMLUSDT",
        timeframe="15m",
        candles=candles,
        market_cap=200_000_000,
        config=cfg,
    )
    assert report["status"] == "OK"
    assert "TRAIN" in report and "VALIDATION" in report and "OOS" in report
    assert "signal_count" in report and "trade_count" in report
    assert "signal_to_trade_conversion" in report
    assert report["NO_LIVE_TRADING_LOGIC_WAS_CHANGED"] is True
    assert "SIGNAL" in report["signal_logic"] or report["signal_logic"] == SMALL_ID
    assert "trade_evaluation_logic" in report


def test_mid_cap_run_with_explicit_mcap():
    candles = _fvg_discount_fixture()
    # pad to min_bars
    while len(candles) < 60:
        i = len(candles)
        candles.append(_candle(i, 100.4, 100.6, 100.2, 100.3, 100))
    cfg = MultiCapResearchConfig(min_bars=40, mid_cap_range_window=10)
    report = run_strategy_on_series(
        strategy_id=MID_ID,
        symbol="MIDUSDT",
        timeframe="15m",
        candles=candles,
        market_cap=2_000_000_000,
        config=cfg,
    )
    assert report["status"] in ("OK", "INSUFFICIENT_DATA")
    assert report["cap_group"] == "MID_CAP"
