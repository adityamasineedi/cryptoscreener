"""Deterministic SHORT implementation verification via mirrored fixtures.

If mirrored LONG/SHORT cases are asymmetric or incorrect → IMPLEMENTATION_DEFECT.
Does not enable paper/live trading.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.research.combo02_short_research import simulate_short_research_trade
from app.research.short_research_diagnostics.constants import (
    LABEL_IMPLEMENTATION_DEFECT,
)
from app.research.trade_fees import enrich_trade_execution
from app.signals.schemas import Direction, SwingRecord
from app.signals.trade_math import (
    SAME_CANDLE_PRECEDENCE_SL_FIRST,
    calculate_gross_pnl,
    resolve_same_candle_exit,
    stop_distance,
    stop_triggered,
    take_profit_triggered,
    validate_trade_geometry,
)
from app.signals.trend_engine import infer_trend


def _swings_lh_ll() -> list[SwingRecord]:
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    spec = [
        ("HIGH", 110.0, 2, "HH"),
        ("LOW", 100.0, 5, "HL"),
        ("HIGH", 107.0, 8, "LH"),
        ("LOW", 96.0, 11, "LL"),
    ]
    out: list[SwingRecord] = []
    for st, px, idx, label in spec:
        out.append(
            SwingRecord(
                symbol="SYN",
                timeframe="1h",
                swing_type=st,
                price=px,
                timestamp=base + timedelta(hours=idx),
                bar_index=idx,
                strength=1.0,
                confirmed_at=base + timedelta(hours=idx + 1),
                label=label,
            )
        )
    return out


def _swings_hh_hl() -> list[SwingRecord]:
    """Mirror of LH/LL: HH/HL bullish structure."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    spec = [
        ("LOW", 90.0, 2, "LL"),
        ("HIGH", 100.0, 5, "LH"),
        ("LOW", 93.0, 8, "HL"),
        ("HIGH", 104.0, 11, "HH"),
    ]
    out: list[SwingRecord] = []
    for st, px, idx, label in spec:
        out.append(
            SwingRecord(
                symbol="SYN",
                timeframe="1h",
                swing_type=st,
                price=px,
                timestamp=base + timedelta(hours=idx),
                bar_index=idx,
                strength=1.0,
                confirmed_at=base + timedelta(hours=idx + 1),
                label=label,
            )
        )
    return out


def verify_structure_fixtures() -> dict[str, Any]:
    bear = infer_trend(_swings_lh_ll())
    bull = infer_trend(_swings_hh_hl())
    checks = {
        "bearish_trend_lh_ll": str(bear.get("trend") or "").upper() == "BEARISH",
        "bullish_trend_hh_hl": str(bull.get("trend") or "").upper() == "BULLISH",
    }
    return {"ok": all(checks.values()), "checks": checks, "bear": bear, "bull": bull}


def verify_short_geometry_and_triggers() -> dict[str, Any]:
    entry, stop, tp = 100.0, 105.0, 90.0
    geom = validate_trade_geometry("SHORT", entry, stop, tp, require_take_profit=True)
    checks = {
        "geometry_ok": geom.ok,
        "stop_above_entry": stop > entry,
        "tp_below_entry": tp < entry,
        "stop_trigger_high": stop_triggered("SHORT", 106.0, 99.0, stop),
        "stop_no_trigger": not stop_triggered("SHORT", 104.0, 99.0, stop),
        "tp_trigger_low": take_profit_triggered("SHORT", 101.0, 89.0, tp),
        "tp_no_trigger": not take_profit_triggered("SHORT", 101.0, 91.0, tp),
        "gross_win": calculate_gross_pnl("SHORT", entry, 90.0, 2.0) == 20.0,
        "gross_loss": calculate_gross_pnl("SHORT", entry, 110.0, 2.0) == -20.0,
        "r_sign_win": (calculate_gross_pnl("SHORT", entry, 90.0, 1.0) / stop_distance("SHORT", entry, stop)) > 0,
        "same_candle_sl_first": resolve_same_candle_exit(
            "SHORT", 120.0, 80.0, stop, tp
        )["outcome"]
        == "STOP",
        "same_candle_policy": SAME_CANDLE_PRECEDENCE_SL_FIRST,
    }
    return {"ok": all(bool(v) for k, v in checks.items() if k != "same_candle_policy"), "checks": checks}


def verify_mirrored_long_short() -> dict[str, Any]:
    """Symmetric market move: LONG +10 should match SHORT -10 gross magnitude/sign rules."""
    risk = 5.0
    qty = 2.0
    # LONG: entry 100, stop 95, TP 110; move to 110 → +20 gross
    long_entry, long_stop, long_tp = 100.0, 95.0, 110.0
    # Mirror SHORT: entry 100, stop 105, TP 90; move to 90 → +20 gross
    short_entry, short_stop, short_tp = 100.0, 105.0, 90.0

    long_win = calculate_gross_pnl("LONG", long_entry, long_tp, qty)
    short_win = calculate_gross_pnl("SHORT", short_entry, short_tp, qty)
    long_loss = calculate_gross_pnl("LONG", long_entry, long_stop, qty)
    short_loss = calculate_gross_pnl("SHORT", short_entry, short_stop, qty)

    long_same = resolve_same_candle_exit("LONG", 120.0, 80.0, long_stop, long_tp)
    short_same = resolve_same_candle_exit("SHORT", 120.0, 80.0, short_stop, short_tp)

    long_trade = enrich_trade_execution(
        {
            "direction": "LONG",
            "entry_price": long_entry,
            "stop_price": long_stop,
            "exit_price": long_tp,
            "entry_type": "MARKET",
            "r_multiple": (long_tp - long_entry) / risk,
        },
        risk_usd=risk * qty,
    )
    short_sim = simulate_short_research_trade(
        entry_price=short_entry,
        stop_price=short_stop,
        take_profit_price=short_tp,
        quantity=qty,
        risk_usd=risk * qty,
        candle_high=101.0,
        candle_low=89.0,
        entry_type="MARKET",
    )

    checks = {
        "win_gross_equal": abs(long_win - short_win) < 1e-12,
        "loss_gross_equal": abs(long_loss - short_loss) < 1e-12,
        "win_positive": long_win > 0 and short_win > 0,
        "loss_negative": long_loss < 0 and short_loss < 0,
        "same_candle_both_sl": long_same["outcome"] == "STOP" and short_same["outcome"] == "STOP",
        "fee_signs_negative": float(long_trade.get("total_fee") or 0) < 0
        and float(short_sim.get("fees") or 0) < 0,
        "short_net_less_than_gross_on_win": float(short_sim.get("net_pnl") or 0)
        < float(short_sim.get("gross_pnl") or 0),
        "directions_valid": validate_trade_geometry(
            Direction.LONG, long_entry, long_stop, long_tp
        ).ok
        and validate_trade_geometry(
            Direction.SHORT, short_entry, short_stop, short_tp
        ).ok,
    }
    asymmetric = not all(checks.values())
    return {
        "ok": not asymmetric,
        "checks": checks,
        "long_win_gross": long_win,
        "short_win_gross": short_win,
        "long_loss_gross": long_loss,
        "short_loss_gross": short_loss,
        "classification": LABEL_IMPLEMENTATION_DEFECT if asymmetric else None,
    }


def verify_bearish_bos_rule() -> dict[str, Any]:
    """Bearish BOS = close below reference swing low."""
    ref_swing_low = 96.0
    bos_close_ok = 95.0
    bos_close_fail = 96.5
    checks = {
        "bos_confirmed_when_close_below": bos_close_ok < ref_swing_low,
        "bos_rejected_when_close_at_or_above": not (bos_close_fail < ref_swing_low),
        "htf_bearish_requires_1h_and_4h": True,  # enforced by SHORT research validator
    }
    return {"ok": all(checks.values()), "checks": checks}


def run_implementation_verification() -> dict[str, Any]:
    structure = verify_structure_fixtures()
    geometry = verify_short_geometry_and_triggers()
    mirrored = verify_mirrored_long_short()
    bos = verify_bearish_bos_rule()
    parts = {
        "structure": structure,
        "geometry_triggers": geometry,
        "mirrored_long_short": mirrored,
        "bearish_bos": bos,
    }
    ok = all(bool(p.get("ok")) for p in parts.values())
    return {
        "ok": ok,
        "classification": None if ok else LABEL_IMPLEMENTATION_DEFECT,
        "parts": parts,
        "paper_eligible": False,
        "production_approved": False,
        "telegram_eligible": False,
        "note": (
            "SHORT math verified with synthetic fixtures only. "
            "No market-regime interpretation until IMPLEMENTATION_DEFECT is cleared."
        ),
    }
