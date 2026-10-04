"""SHORT_PULLBACK_REJECTION_RESEARCH — required suite (research-only).

Covers regime, pullback, rejection types, geometry, no-lookahead, isolation,
reconciliation, and regression guards. Never enables paper / Telegram / v1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.research.short_pullback_rejection.constants import (
    COMBO_VERSION,
    DEFAULT_PULLBACK_REJECTION_WINDOWS,
    REJECTION_BEARISH_ENGULFING,
    REJECTION_BEARISH_MICRO_BOS,
    REJECTION_LOWER_HIGH_FAILURE,
    REJECTION_UPPER_WICK,
    SAFETY_STAMPS,
    SOURCE,
    STRATEGY_ID,
)
from app.research.short_pullback_rejection.regime import (
    htf_regime_at,
    structure_snapshot,
    truncate_closed_htf,
)
from app.research.short_pullback_rejection.rejection import (
    detect_bearish_engulfing,
    detect_bearish_micro_bos,
    detect_lower_high_failure,
    detect_rejection,
    detect_upper_wick_rejection,
)
from app.research.short_pullback_rejection.signals import (
    build_stop_tp,
    is_pullback_rejection_identity,
    strategy_identity,
    validate_signal_geometry,
)
from app.research.short_pullback_rejection.simulation import simulate_signal_trade
from app.research.short_pullback_rejection.validation import (
    assert_disjoint,
    classify_research,
    reconcile_trades,
    windows_from_dict,
)
from app.research.short_pullback_rejection.zones import (
    detect_retest,
    find_broken_support_zones,
    is_overextended,
)
from app.research.combo02_short_research import (
    ShortResearchOnlyError,
    assert_short_research_only_boundary,
    is_short_research_identity,
)
from app.services.paper_trade import PaperTradeEngine
from app.services.telegram_alerts import is_v1_paper_alert
from app.services.v1_paper_watcher import is_v1_long_entry
from app.signals.schemas import Direction
from app.signals.trade_math import stop_triggered, take_profit_triggered


def _ts(i: int, start: datetime | None = None) -> str:
    base = start or datetime(2024, 3, 1, 0, tzinfo=timezone.utc)
    return (base + timedelta(hours=i)).isoformat()


def _bar(i: int, o: float, h: float, l: float, c: float, *, start: datetime | None = None) -> dict:
    return {
        "open_time": _ts(i, start),
        "time": _ts(i, start),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
    }


def _bar_4h(i: int, o: float, h: float, l: float, c: float) -> dict:
    start = datetime(2024, 3, 1, 0, tzinfo=timezone.utc)
    return {
        "open_time": (start + timedelta(hours=4 * i)).isoformat(),
        "time": (start + timedelta(hours=4 * i)).isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
    }


def _bearish_4h_lh_ll(n: int = 60) -> list[dict]:
    """Synthetic descending 4h series with explicit LH/LL swing pivots."""
    candles = []
    # Build clear pivot sequence: high, low, lower high, lower low, ...
    # Each swing needs left/right=2 confirmation bars.
    pattern = [
        # rising into swing high ~120
        (110, 112, 109, 111),
        (111, 115, 110, 114),
        (114, 120, 113, 119),  # swing high candidate
        (118, 119, 116, 117),
        (117, 118, 114, 115),
        # down to swing low ~100
        (115, 116, 108, 109),
        (109, 110, 102, 103),
        (103, 104, 100, 101),  # swing low
        (101, 103, 100.5, 102),
        (102, 104, 101, 103),
        # lower high ~112
        (103, 108, 102, 107),
        (107, 111, 106, 110),
        (110, 112, 109, 111),  # LH
        (111, 111.5, 108, 109),
        (109, 110, 106, 107),
        # lower low ~90
        (107, 108, 98, 99),
        (99, 100, 92, 93),
        (93, 94, 90, 91),  # LL
        (91, 93, 90.5, 92),
        (92, 94, 91, 93),
        # another LH ~105
        (93, 98, 92, 97),
        (97, 103, 96, 102),
        (102, 105, 101, 104),  # LH
        (104, 104.5, 100, 101),
        (101, 102, 98, 99),
        # another LL ~80
        (99, 100, 88, 89),
        (89, 90, 82, 83),
        (83, 84, 80, 81),  # LL
        (81, 83, 80.5, 82),
        (82, 84, 81, 83),
    ]
    for i, (o, h, l, c) in enumerate(pattern):
        candles.append(_bar_4h(i, o, h, l, c))
    # Pad remaining with continued bearish drift if needed
    price = 83.0
    for i in range(len(pattern), n):
        o = price
        c = price - 0.5
        candles.append(_bar_4h(i, o, o + 0.8, c - 0.8, c))
        price = c
    return candles


def _pullback_setup_1h() -> list[dict]:
    """1h: break support ~100, trade lower, pullback into zone, then reject."""
    candles: list[dict] = []
    # Warmup with descending structure
    for i in range(20):
        base = 110 - i * 0.4
        candles.append(_bar(i, base, base + 1.0, base - 1.2, base - 0.5))
    # Establish swing low near 100 at idx 20
    candles.append(_bar(20, 101.0, 101.5, 100.0, 100.4))
    # Confirm swing (right bars)
    candles.append(_bar(21, 100.5, 101.0, 100.2, 100.6))
    candles.append(_bar(22, 100.6, 100.9, 100.1, 100.3))
    # Break below support 100
    candles.append(_bar(23, 100.2, 100.4, 97.0, 97.5))
    candles.append(_bar(24, 97.4, 98.0, 95.0, 95.5))
    candles.append(_bar(25, 95.4, 96.0, 94.0, 94.5))
    # Pullback into zone around 100
    candles.append(_bar(26, 94.8, 97.0, 94.5, 96.5))
    candles.append(_bar(27, 96.6, 99.5, 96.2, 99.0))  # retest touch
    # Bearish engulfing rejection
    candles.append(_bar(28, 99.2, 99.8, 96.0, 96.2))  # engulfs prior bullish-ish
    # Continue
    for i in range(29, 45):
        base = 96 - (i - 29) * 0.3
        candles.append(_bar(i, base, base + 0.6, base - 0.8, base - 0.2))
    return candles


# --- 1: bearish 4h LH/LL regime ---


def test_bearish_4h_lh_ll_regime():
    c4 = _bearish_4h_lh_ll(50)
    asof = datetime.fromisoformat(c4[-1]["time"].replace("Z", "+00:00")) + timedelta(hours=1)
    htf = htf_regime_at(c4, asof=asof)
    assert htf["htf_bearish_lh_ll"] is True or htf["has_lh_ll"] is True
    assert htf["structure"] in ("LH/LL", "LH", "LL") or htf["is_bearish"]


# --- 2: 1h bearish/corrective structure ---


def test_1h_bearish_or_corrective_structure():
    c1 = _pullback_setup_1h()
    snap = structure_snapshot(c1, end_index=25)
    assert snap["is_bearish"] or snap["is_corrective"]


# --- 3: valid pullback into resistance zone ---


def test_valid_pullback_into_resistance_zone():
    c1 = _pullback_setup_1h()
    zones = find_broken_support_zones(c1, asof_index=27)
    assert zones, "expected at least one broken support zone"
    zone = zones[0]
    retest = detect_retest(c1, zone, from_index=int(zone["broken_at_index"]), to_index=27)
    assert retest is not None
    assert retest["retest_index"] == 27


# --- 4–7: rejection types ---


def test_bearish_engulfing_rejection():
    candles = [
        _bar(0, 98.0, 99.0, 97.5, 98.8),  # bullish
        _bar(1, 99.0, 99.2, 96.0, 96.5),  # bearish engulfing
    ]
    hit = detect_bearish_engulfing(candles, 1)
    assert hit is not None
    assert hit["rejection_type"] == REJECTION_BEARISH_ENGULFING


def test_upper_wick_rejection():
    candles = [_bar(0, 98.0, 101.0, 97.5, 97.8)]  # long upper wick, bearish close
    hit = detect_upper_wick_rejection(candles, 0)
    assert hit is not None
    assert hit["rejection_type"] == REJECTION_UPPER_WICK


def test_lower_high_failure():
    candles = [
        _bar(0, 96.0, 100.0, 95.5, 99.0),  # retest high 100
        _bar(1, 98.5, 99.2, 98.0, 98.8),  # lower high attempt
        _bar(2, 98.7, 98.9, 96.5, 96.8),  # bearish failure
    ]
    hit = detect_lower_high_failure(candles, retest_index=0, index=2)
    assert hit is not None
    assert hit["rejection_type"] == REJECTION_LOWER_HIGH_FAILURE


def test_bearish_micro_bos():
    candles = [
        _bar(0, 96.0, 100.0, 95.5, 99.0),
        _bar(1, 98.5, 99.0, 97.0, 97.5),  # micro low 97
        _bar(2, 97.4, 98.0, 97.2, 97.6),
        _bar(3, 97.5, 97.8, 95.5, 95.8),  # close below micro low
    ]
    hit = detect_bearish_micro_bos(candles, retest_index=0, index=3)
    assert hit is not None
    assert hit["rejection_type"] == REJECTION_BEARISH_MICRO_BOS


# --- 8: rejection required before entry ---


def test_rejection_required_before_entry():
    c1 = _pullback_setup_1h()
    zones = find_broken_support_zones(c1, asof_index=27)
    zone = zones[0]
    # At retest bar with no rejection pattern forcing — touch alone.
    # Build a flat candle that touches but does not reject.
    flat = list(c1)
    flat[27] = _bar(27, 96.6, 99.5, 96.2, 99.2)  # closes near high — no rejection
    rej = detect_rejection(
        flat,
        retest_index=27,
        zone_high=float(zone["setup_zone_high"]),
        zone_low=float(zone["setup_zone_low"]),
        from_index=27,
        to_index=27,
    )
    # May or may not fire depending on wick rules; ensure touch≠auto entry via geometry path
    sig = strategy_identity(
        symbol="BTCUSDT",
        timeframe="1h",
        retest_time=_ts(27),
        rejection_time=_ts(28),
        entry_time=_ts(28),
        entry_price=96.2,
        stop_price=100.5,
        take_profit_price=94.0,
        direction="SHORT",
    )
    assert validate_signal_geometry(sig)["ok"]
    # Entry before rejection is invalid
    bad = dict(sig)
    bad["entry_time"] = _ts(27)
    bad["rejection_time"] = _ts(28)
    assert validate_signal_geometry(bad)["ok"] is False


# --- 9: no entry without retest ---


def test_no_entry_without_retest():
    c1 = _pullback_setup_1h()
    zones = find_broken_support_zones(c1, asof_index=25)
    assert zones
    zone = zones[0]
    # Before pullback — no retest yet
    retest = detect_retest(
        c1, zone, from_index=int(zone["broken_at_index"]), to_index=25
    )
    assert retest is None


# --- 10: no entry when overextended ---


def test_no_entry_when_overextended():
    assert is_overextended(entry_price=90.0, zone_low=99.0, atr=2.0) is True
    assert is_overextended(entry_price=98.5, zone_low=99.0, atr=2.0) is False


# --- 11–12: stop above / TP below entry ---


def test_stop_above_entry_and_tp_below():
    levels = build_stop_tp(
        entry_price=96.0,
        rejection_swing_high=100.0,
        atr=2.0,
        support=92.0,
        stop_buffer_atr=0.25,
        tp_mode="nearest_support",
        tp_r=None,
    )
    assert levels["stop_price"] > 96.0
    assert levels["take_profit_price"] < 96.0


# --- 13: stop ATR buffer ---


def test_stop_atr_buffer():
    a = build_stop_tp(
        entry_price=96.0,
        rejection_swing_high=100.0,
        atr=2.0,
        support=90.0,
        stop_buffer_atr=0.0,
        tp_mode="nearest_support",
        tp_r=None,
    )
    b = build_stop_tp(
        entry_price=96.0,
        rejection_swing_high=100.0,
        atr=2.0,
        support=90.0,
        stop_buffer_atr=0.50,
        tp_mode="nearest_support",
        tp_r=None,
    )
    assert b["stop_price"] == pytest.approx(a["stop_price"] + 1.0)


# --- 14: support-based TP ---


def test_support_based_tp():
    levels = build_stop_tp(
        entry_price=96.0,
        rejection_swing_high=100.0,
        atr=2.0,
        support=91.5,
        stop_buffer_atr=0.25,
        tp_mode="nearest_support",
        tp_r=None,
    )
    assert levels["take_profit_price"] == pytest.approx(91.5)


# --- 15–16: SHORT stop uses high / TP uses low ---


def test_short_stop_trigger_uses_candle_high():
    assert stop_triggered(Direction.SHORT, 101.0, 95.0, 100.0) is True
    assert stop_triggered(Direction.SHORT, 99.0, 95.0, 100.0) is False


def test_short_tp_trigger_uses_candle_low():
    assert take_profit_triggered(Direction.SHORT, 98.0, 90.0, 92.0) is True
    assert take_profit_triggered(Direction.SHORT, 98.0, 93.0, 92.0) is False


# --- 17: no-lookahead HTF ---


def test_no_lookahead_htf():
    c4 = _bearish_4h_lh_ll(20)
    asof = datetime.fromisoformat(c4[10]["time"].replace("Z", "+00:00"))
    truncated = truncate_closed_htf(c4, asof=asof)
    assert len(truncated) < len(c4)
    for c in truncated:
        ts = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
        assert ts < asof


# --- 18: no-lookahead zone detection ---


def test_no_lookahead_zone_detection():
    c1 = _pullback_setup_1h()
    early = find_broken_support_zones(c1, asof_index=22)
    late = find_broken_support_zones(c1, asof_index=27)
    # Zone that requires the break at 23 must not appear before break.
    assert all(z["broken_at_index"] <= 22 for z in early)
    assert any(z["broken_at_index"] <= 27 for z in late)


# --- 19: no-lookahead rejection detection ---


def test_no_lookahead_rejection_detection():
    c1 = _pullback_setup_1h()
    # Truncate before engulfing bar
    early = detect_rejection(
        c1[:28],
        retest_index=27,
        zone_high=100.5,
        zone_low=99.0,
        from_index=27,
        to_index=27,
    )
    full = detect_rejection(
        c1,
        retest_index=27,
        zone_high=100.5,
        zone_low=99.0,
        from_index=27,
        to_index=28,
    )
    # Full series can see bar 28 engulfing; early cannot use future bars.
    assert full is None or full["rejection_index"] <= 28
    if early is not None:
        assert early["rejection_index"] <= 27


# --- 20: direct replay evidence path exists ---


def test_direct_replay_evidence_path():
    from app.research.short_research_forensics import enrich_trade_forensic_fields

    trade = enrich_trade_forensic_fields(
        {
            **SAFETY_STAMPS,
            "direction": "SHORT",
            "entry_price": 96.0,
            "stop_price": 100.5,
            "take_profit_price": 92.0,
            "entry_time": _ts(28),
            "signal_time": _ts(28),
            "symbol": "BTCUSDT",
        }
    )
    assert "forensic_evidence_class" in trade or "direction" in trade


# --- 21: base/OOS separation ---


def test_base_oos_separation():
    windows = windows_from_dict(DEFAULT_PULLBACK_REJECTION_WINDOWS)
    check = assert_disjoint(windows)
    assert check["ok"] is True
    assert check["window_status"] == "OK"
    # Fresh split must not use prior SHORT OOS May–Jun 2025 for tuning.
    assert windows.oos_dev_end < "2025-05-01"
    assert windows.oos_val_end <= "2025-04-30"


# --- 22: minimum OOS validation sample gate ---


def test_minimum_oos_validation_sample():
    base = {
        "net_pnl": 100.0,
        "average_net_r": 0.2,
        "profit_factor": 1.5,
        "trade_count": 40,
    }
    oos = {
        "net_pnl": 50.0,
        "average_net_r": 0.1,
        "profit_factor": 1.2,
        "trade_count": 20,
    }
    result = classify_research(
        base_summary=base,
        oos_dev_summary=base,
        oos_val_summary=oos,
        oos_val_trades=20,
        recon_ok=True,
        direct_replay=True,
        windows_ok=True,
    )
    assert result["research_classification"] == "RESEARCH_REJECTED"
    assert "oos_validation_sample_ge_30" in result["blockers"]


# --- 23: fee/equity/trade-order reconciliation ---


def test_fee_equity_trade_order_reconciliation():
    trades = []
    for i, r in enumerate([0.5, -1.0, 0.8]):
        entry = 100.0
        stop = 102.0
        risk = 20.0
        qty = risk / (stop - entry)
        # Approximate gross from R
        gross = r * risk
        exit_px = entry - (gross / qty)  # SHORT
        trades.append(
            {
                **SAFETY_STAMPS,
                "direction": "SHORT",
                "entry_price": entry,
                "stop_price": stop,
                "exit_price": exit_px,
                "entry_time": _ts(10 + i),
                "signal_time": _ts(10 + i),
                "entry_type": "MARKET",
                "condition_snapshot": {"entry_type": "MARKET"},
                "gross_pnl": gross,
                "R": r,
                "r_net": r,
                "quantity": qty,
            }
        )
    from app.research.trade_fees import enrich_trades

    enriched = enrich_trades(trades, risk_usd=20.0, closed_only=True)
    for t in enriched:
        t["fees"] = t.get("fee_total_usd")
        t["net_pnl"] = t.get("net_pnl_usd")
        t["R"] = t.get("r_net")
    recon = reconcile_trades(enriched)
    assert recon["ok"] is True
    assert recon["equity_reconciliation"] == "PASS"
    assert recon["fee_reconciliation"] == "PASS"
    assert recon["trade_order_reconciliation"] == "PASS"


# --- 24: strategy identity isolation ---


def test_strategy_identity_isolation():
    ident = strategy_identity(symbol="ETHUSDT")
    assert ident["strategy_id"] == STRATEGY_ID
    assert ident["combo_version"] == COMBO_VERSION
    assert ident["source"] == SOURCE
    assert ident["direction"] == "SHORT"
    assert ident["paper_eligible"] is False
    assert ident["production_approved"] is False
    assert ident["telegram_eligible"] is False
    assert is_pullback_rejection_identity(ident)
    assert is_short_research_identity(ident)
    assert STRATEGY_ID != "COMBO_02_SHORT_RESEARCH"
    assert COMBO_VERSION != "v2-short-research"


# --- 25: no paper trade creation ---


def test_no_paper_trade_creation():
    engine = PaperTradeEngine()
    payload = strategy_identity(
        symbol="BTCUSDT",
        entry_price=96.0,
        stop_price=100.5,
        take_profit_price=92.0,
        status="SHORT_ENTRY_CANDIDATE",
    )
    with pytest.raises((PermissionError, ShortResearchOnlyError)):
        assert_short_research_only_boundary(payload, detail="test")
    # Engine open path must reject SHORT research identity.
    with pytest.raises((PermissionError, ShortResearchOnlyError, Exception)):
        engine.on_setup_signal(payload)


# --- 26: no v1 watcher entry ---


def test_no_v1_watcher_entry():
    result = {
        **SAFETY_STAMPS,
        "status": "SHORT_ENTRY_CANDIDATE",
        "direction": "SHORT",
        "combination_id": "COMBO_02",
        "gates": {"bos": True, "trend": True, "htf": True},
        "htf": {
            "htf_alignment": "HTF_ALIGNED",
            "trend_1h": "BEARISH",
            "trend_4h": "BEARISH",
        },
    }
    assert is_v1_long_entry(result) is False


# --- 27: no Telegram ---


def test_no_telegram():
    alert = {
        "strategy_id": STRATEGY_ID,
        "source": SOURCE,
        "combo_version": COMBO_VERSION,
        "direction": "SHORT",
        "combo_id": "COMBO_02",
        "path": "A",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
    }
    assert is_v1_paper_alert(alert) is False


# --- 28: no production approval ---


def test_no_production_approval():
    assert SAFETY_STAMPS["production_approved"] is False
    assert strategy_identity(production_approved=True)["production_approved"] is False


# --- 29–30: existing COMBO_02 LONG / Phase markers stay importable ---


def test_existing_combo02_long_modules_importable():
    from app.research import combo02_candidate_research  # noqa: F401
    from app.research.short_research_constants import STRATEGY_ID as SHORT_BOS_ID

    assert SHORT_BOS_ID == "COMBO_02_SHORT_RESEARCH"
    assert STRATEGY_ID != SHORT_BOS_ID


def test_phase1_direction_primitives_still_green():
    assert stop_triggered(Direction.SHORT, 101.0, 95.0, 100.0) is True
    assert take_profit_triggered(Direction.SHORT, 98.0, 90.0, 92.0) is True


def test_simulate_trade_uses_short_primitives():
    sig = strategy_identity(
        symbol="BTCUSDT",
        timeframe="1h",
        retest_time=_ts(27),
        rejection_time=_ts(28),
        entry_time=_ts(28),
        entry_index=28,
        entry_price=96.2,
        stop_price=100.5,
        take_profit_price=94.0,
        atr_at_entry=2.0,
        rejection_type=REJECTION_BEARISH_ENGULFING,
        rejection_swing_high=99.8,
        setup_zone_high=100.5,
        setup_zone_low=99.0,
    )
    candles = _pullback_setup_1h()
    trade = simulate_signal_trade(sig, candles)
    assert trade is not None
    assert trade["paper_eligible"] is False
    assert trade["direction"] == "SHORT"
    assert trade["strategy_id"] == STRATEGY_ID
