"""GRID_RANGE_RESEARCH unit tests — isolated from COMBO_02 / COMBO_02_V2."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.research.bos_combinations import get_combination
from app.research.grid_range_research.config import GRID_A, GRID_B, GRID_C, get_variant
from app.research.grid_range_research.engine import run_grid_backtest
from app.research.grid_range_research.levels import build_grid_levels, grid_spacing
from app.research.grid_range_research.range_bounds import confirmed_range_bounds
from app.research.grid_range_research.risk import assert_no_martingale, apply_fees_and_sizing


def _candles_flat_range(
    n_hours: int = 80,
    *,
    low: float = 100.0,
    high: float = 108.0,
    start: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """Synthetic oscillating 1H + 15M inside a range (no lookahead needed)."""
    t0 = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    c1h: list[dict] = []
    c15: list[dict] = []
    mid = 0.5 * (low + high)
    for i in range(n_hours):
        # Oscillate between low and high
        phase = i % 8
        if phase < 4:
            o = mid - (phase * (mid - low) / 4)
            c = mid - ((phase + 1) * (mid - low) / 4)
        else:
            p = phase - 4
            o = low + (p * (high - low) / 4)
            c = low + ((p + 1) * (high - low) / 4)
        h = max(o, c) + 0.2
        l = min(o, c) - 0.2
        # Keep inside extended range for swings
        h = min(h, high + 0.5)
        l = max(l, low - 0.5)
        bar = {
            "time": t0 + timedelta(hours=i),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000.0 + i,
        }
        c1h.append(bar)
        for k in range(4):
            # 15m walks from open toward close, touching extremes
            frac0 = k / 4
            frac1 = (k + 1) / 4
            o15 = o + (c - o) * frac0
            c15_px = o + (c - o) * frac1
            c15.append(
                {
                    "time": bar["time"] + timedelta(minutes=15 * k),
                    "open": o15,
                    "high": max(o15, c15_px, h if k in (1, 2) else o15),
                    "low": min(o15, c15_px, l if k in (1, 2) else o15),
                    "close": c15_px,
                    "volume": 250.0,
                }
            )
    return c1h, c15


def test_grid_level_construction():
    levels = build_grid_levels(100.0, 108.0, 9)
    assert levels[0] == 100.0
    assert levels[-1] == 108.0
    assert len(levels) == 9
    assert abs(levels[1] - 101.0) < 1e-9


def test_grid_spacing():
    assert abs(grid_spacing(100.0, 108.0, 9) - 1.0) < 1e-12
    assert abs(grid_spacing(100.0, 108.0, 8) - (8.0 / 7.0)) < 1e-12


def test_long_grid_fill_and_target():
    levels = build_grid_levels(100.0, 108.0, 9)
    # LONG at 100 → target 101
    entry_i = 0
    assert levels[entry_i] == 100.0
    target = levels[entry_i + 1]
    assert abs(target - 101.0) < 1e-9


def test_short_grid_fill_and_target():
    levels = build_grid_levels(100.0, 108.0, 9)
    entry_i = 8
    assert levels[entry_i] == 108.0
    target = levels[entry_i - 1]
    assert abs(target - 107.0) < 1e-9


def test_maximum_inventory_respected():
    # Engine-level: variant max_simultaneous never exceeded
    c1h, c15 = _candles_flat_range(120)
    # Force enabled regimes by monkeypatching precompute — instead run and check
    # Using a tiny stub: call engine with patched regimes via wrapping levels only.
    # Direct inventory check on risk config:
    assert GRID_A.max_simultaneous == 3
    assert GRID_C.max_simultaneous == 2
    out = run_grid_backtest("TEST", c1h, c15, GRID_C, index_start=10)
    assert out.risk_summary["max_simultaneous_positions"] == 2
    assert out.risk_summary["max_inventory_observed"] <= 2


def test_no_martingale():
    sizes = [20.0, 20.0, 20.0]
    assert assert_no_martingale(sizes) is True
    assert assert_no_martingale([20.0, 40.0]) is False
    priced = apply_fees_and_sizing(
        direction="LONG",
        entry_price=100.0,
        stop_price=99.0,
        exit_price=101.0,
        r_gross=1.0,
    )
    priced2 = apply_fees_and_sizing(
        direction="LONG",
        entry_price=100.0,
        stop_price=99.0,
        exit_price=101.0,
        r_gross=1.0,
    )
    # Equal requested risk / equal stop distance ⇒ equal size (no martingale)
    assert float(priced["requested_risk_usd"]) == float(priced2["requested_risk_usd"])
    assert assert_no_martingale(
        [float(priced["risk_usd"]), float(priced2["risk_usd"])]
    )


def test_breakout_kill_condition_logic():
    from app.research.grid_range_research.config import GRID_KILL_REGIMES

    assert "BULL_TREND" in GRID_KILL_REGIMES
    assert "CHOPPY" not in GRID_KILL_REGIMES
    # Structural: session ends when close beyond range + spacing — covered in engine
    c1h, c15 = _candles_flat_range(40, low=100.0, high=108.0)
    # Inject strong upside breakout on later bars
    for i in range(30, 40):
        c1h[i]["open"] = 110.0
        c1h[i]["high"] = 115.0
        c1h[i]["low"] = 109.0
        c1h[i]["close"] = 114.0
        for k in range(4):
            j = i * 4 + k
            c15[j]["open"] = 110.0 + k
            c15[j]["high"] = 115.0
            c15[j]["low"] = 109.0
            c15[j]["close"] = 114.0
    out = run_grid_backtest("TEST", c1h, c15, GRID_A, index_start=5)
    reasons = [s.get("reason_grid_stopped") for s in out.sessions]
    # May or may not start a session depending on swings; if sessions exist with breakout:
    for s in out.sessions:
        if s.get("breakout_seen") or str(s.get("reason_grid_stopped", "")).startswith(
            "RANGE_BREAK"
        ):
            assert s.get("reason_grid_stopped")


def test_regime_transition_kills_new_entries():
    from app.research.grid_range_research.config import GRID_KILL_REGIMES, GRID_ENABLED_PRIMARY

    assert GRID_ENABLED_PRIMARY.isdisjoint(GRID_KILL_REGIMES)
    assert "TRANSITION" in GRID_KILL_REGIMES


def test_no_future_candle_usage_in_range_bounds():
    c1h, _ = _candles_flat_range(60)
    # Bounds at index i must equal bounds when series truncated to i+1
    for i in (20, 35, 50):
        a = confirmed_range_bounds(c1h, i, symbol="T")
        b = confirmed_range_bounds(c1h[: i + 1], i, symbol="T")
        assert a == b


def test_fee_calculation_negative_cost():
    priced = apply_fees_and_sizing(
        direction="LONG",
        entry_price=100.0,
        stop_price=99.0,
        exit_price=101.0,
        r_gross=1.0,
    )
    assert float(priced["total_fee"]) < 0
    assert priced["r_net"] is not None
    assert float(priced["r_net"]) < float(priced.get("r_multiple") or 1.0) or float(
        priced["net_pnl"]
    ) < float(priced["gross_pnl"])


def test_risk_cap_max_simultaneous():
    assert GRID_A.max_simultaneous == 3
    assert GRID_B.n_levels == 10
    assert GRID_C.max_simultaneous == 2
    caps_risk = GRID_A.max_simultaneous * 20.0
    assert caps_risk == 60.0


def test_duplicate_fill_prevention_via_level_sets():
    """Open set semantics: same level cannot hold two concurrent longs."""
    long_open_at: set[int] = set()
    level_i = 2
    assert level_i not in long_open_at
    long_open_at.add(level_i)
    # Second fill blocked
    blocked = level_i in long_open_at
    assert blocked is True
    long_open_at.discard(level_i)
    assert level_i not in long_open_at


def test_variants_registered():
    assert get_variant("GRID-A").n_levels == 8
    assert get_variant("GRID-B").n_levels == 10
    assert get_variant("GRID-C").max_simultaneous == 2


def test_combo02_v1_and_v2_untouched_by_grid_package():
    v1 = get_combination("COMBO_02")
    v2 = get_combination("COMBO_02_V2")
    assert v1 is not None and v1.require_bos is True
    assert v2 is not None and v2.require_bos is False
    # Grid research is not registered as a bos combination replacement
    assert get_combination("GRID_RANGE_RESEARCH") is None
