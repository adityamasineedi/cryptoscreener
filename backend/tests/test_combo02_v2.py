"""COMBO_02 v2 adaptive playbook — does not mutate COMBO_02 v1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.bos_combinations import get_combination
from app.research.combination_backtest import run_combination_backtest
from app.research.combination_engine import evaluate_combination_at_bar
from app.research.combo02_v2.htf_policy import trend_htf_allows
from app.research.combo02_v2.playbooks import detect_liquidity_sweep
from app.research.combo02_v2.router import (
    PLAYBOOK_RANGE,
    PLAYBOOK_REVERSAL,
    PLAYBOOK_TREND,
    PLAYBOOK_WAIT,
    route_playbook,
)
from app.research.config import ResearchConfig
from app.signals.config import SignalConfig


def _candles(n: int, *, drift: float = 0.2, base: float = 100.0) -> list[dict]:
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    px = base
    for i in range(n):
        o = px
        c = px + drift
        out.append(
            {
                "time": t0 + timedelta(hours=i),
                "open": o,
                "high": max(o, c) + 0.8,
                "low": min(o, c) - 0.8,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
        px = c
    return out


def _down(c1h: list[dict], every: int, tf: int) -> list[dict]:
    out = []
    for i in range(0, len(c1h), every):
        chunk = c1h[i : i + every]
        if not chunk:
            continue
        out.append(
            {
                "time": c1h[0]["time"] + timedelta(seconds=tf * len(out)),
                "open": chunk[0]["open"],
                "high": max(x["high"] for x in chunk),
                "low": min(x["low"] for x in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(x["volume"] for x in chunk),
            }
        )
    return out


def _expand_15m(c1h: list[dict]) -> list[dict]:
    out = []
    for c in c1h:
        for k in range(4):
            out.append(
                {
                    "time": c["time"] + timedelta(minutes=15 * k),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"] / 4,
                }
            )
    return out


def test_v1_combo_definition_unchanged():
    c = get_combination("COMBO_02")
    assert c is not None
    assert c.require_bos is True
    assert c.require_trend is True
    assert c.require_htf_alignment is True


def test_v2_combo_registered():
    c = get_combination("COMBO_02_V2")
    assert c is not None
    assert c.require_bos is False
    assert c.require_htf_alignment is False


def test_v21_research_combos_registered_not_default():
    a = get_combination("COMBO_02_V2_1A")
    a_ui = get_combination("COMBO_02_V2_1_A")
    b = get_combination("COMBO_02_V2_1B")
    assert a is not None and b is not None and a_ui is not None
    assert a.require_bos is False and b.require_bos is False
    # Frozen default id remains COMBO_02_V2
    assert get_combination("COMBO_02_V2").combination_id == "COMBO_02_V2"


def test_router_mapping():
    assert route_playbook("BULL_TREND") == PLAYBOOK_TREND
    assert route_playbook("BEAR_TREND") == PLAYBOOK_TREND
    assert route_playbook("HIGH_VOLATILITY_TREND") == PLAYBOOK_TREND
    assert route_playbook("CHOPPY") == PLAYBOOK_RANGE
    assert route_playbook("RANGE") == PLAYBOOK_RANGE
    assert route_playbook("HIGH_VOLATILITY_RANGE") == PLAYBOOK_RANGE
    assert route_playbook("LOW_VOLATILITY_COMPRESSION") == PLAYBOOK_RANGE
    assert route_playbook("TRANSITION") == PLAYBOOK_REVERSAL
    assert route_playbook("UNKNOWN") == PLAYBOOK_WAIT


def test_v21a_choppy_waits_other_range_unchanged():
    from app.research.combo02_v2.research_variants import (
        VARIANT_A,
        route_playbook_variant,
    )

    assert route_playbook_variant("CHOPPY", variant=VARIANT_A) == PLAYBOOK_WAIT
    assert route_playbook_variant("RANGE", variant=VARIANT_A) == PLAYBOOK_RANGE
    assert route_playbook_variant("HIGH_VOLATILITY_RANGE", variant=VARIANT_A) == PLAYBOOK_RANGE
    assert route_playbook_variant("LOW_VOLATILITY_COMPRESSION", variant=VARIANT_A) == PLAYBOOK_RANGE
    assert route_playbook_variant("BULL_TREND", variant=VARIANT_A) == PLAYBOOK_TREND
    assert route_playbook_variant("TRANSITION", variant=VARIANT_A) == PLAYBOOK_REVERSAL
    # No variant == frozen router
    assert route_playbook_variant("CHOPPY", variant=None) == PLAYBOOK_RANGE


def test_v21b_rejects_optional_15m_pass():
    from app.research.combo02_v2.research_variants import choppy_range_15m_required_ok

    ok, reason = choppy_range_15m_required_ok(
        direction="LONG",
        snap_15m=None,
        analysis_15m=None,
        confirmation="15M_UNAVAILABLE_OPTIONAL_PASS",
    )
    assert ok is False
    assert "REQUIRED" in reason


def test_htf_policy_allows_neutral_not_opposition():
    ok, reason = trend_htf_allows(
        "LONG", {"data_ok": True, "trend_4h": "NEUTRAL", "trend_1h": "BULLISH"}
    )
    assert ok is True
    assert "NEUTRAL" in reason
    bad, _ = trend_htf_allows(
        "LONG", {"data_ok": True, "trend_4h": "BEARISH", "trend_1h": "BULLISH"}
    )
    assert bad is False
    insuff, reason2 = trend_htf_allows(
        "LONG", {"data_ok": False, "reason": "HTF insufficient history"}
    )
    assert insuff is False
    assert "insufficient" in reason2.lower() or "unavailable" in reason2.lower()


def test_sweep_detection_failed_breakdown():
    # Build flat then spike low and recover
    c = _candles(40, drift=0.0, base=100.0)
    # bar 35: sweep below then close back
    c[35]["low"] = 95.0
    c[35]["close"] = 100.2
    c[35]["high"] = 100.5
    c[35]["open"] = 100.0
    sweep = detect_liquidity_sweep(c, 35, lookback=20)
    assert sweep is not None
    assert sweep["event"] == "LOW_SWEEP"
    assert sweep["direction"] == "LONG"


def test_v2_backtest_does_not_change_v1_trades():
    c1h = _candles(160, drift=0.3)
    c4h = _down(c1h, 4, 14400)
    v1_a = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        "COMBO_02",
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=50,
    )
    v1_b = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        "COMBO_02",
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=50,
    )
    assert v1_a["trades"] == v1_b["trades"]
    # Running v2 must not alter v1 definition path results
    _ = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        "COMBO_02_V2",
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        candles_15m=_expand_15m(c1h),
        index_start=50,
    )
    v1_c = run_combination_backtest(
        "BTCUSDT",
        "1h",
        c1h,
        "COMBO_02",
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        direction_filter="LONG",
        index_start=50,
    )
    assert v1_a["trades"] == v1_c["trades"]


def test_v2_supports_long_and_short_statuses():
    """Evaluator returns standard candidate statuses only."""
    c1h = _candles(120, drift=0.25)
    c4h = _down(c1h, 4, 14400)
    combo = get_combination("COMBO_02_V2")
    assert combo is not None
    setup = evaluate_combination_at_bar(
        symbol="BTCUSDT",
        timeframe="1h",
        candles=c1h,
        as_of_index=80,
        combination=combo,
        signal_config=SignalConfig(),
        research_config=ResearchConfig(),
        candles_1h=c1h,
        candles_4h=c4h,
        candles_15m=_expand_15m(c1h),
    )
    assert setup["status"] in {
        "NO_SETUP",
        "LONG_ENTRY_CANDIDATE",
        "SHORT_ENTRY_CANDIDATE",
    }
    assert "playbook" in setup or setup["status"] == "NO_SETUP"
